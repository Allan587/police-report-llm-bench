"""Local-only browser operations for future reviewer adapters.

This layer owns no workflow state or review output. It exposes browser outcomes
which a future ReviewerAdapter may translate into its existing contract.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from automation.contracts import Reviewer
from .sessions import SessionManager, UnsafeProfileRootError


class BrowserOperationState(str, Enum):
    READY = "READY"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    WAITING_FOR_RESPONSE = "WAITING_FOR_RESPONSE"
    RESPONSE_UNCERTAIN = "RESPONSE_UNCERTAIN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LocalBrowserError(RuntimeError):
    """Sanitized browser failure: no URL, response, or credentials are retained."""


class UnsafeNavigationError(LocalBrowserError):
    pass


class NavigationFailedError(LocalBrowserError):
    pass


@dataclass(frozen=True)
class LocalBrowserTimeouts:
    navigation_ms: int = 10_000
    wait_ms: int = 10_000

    def __post_init__(self) -> None:
        for value in (self.navigation_ms, self.wait_ms):
            if type(value) is not int or value <= 0:
                raise ValueError("browser timeouts must be positive integer milliseconds")


def _local_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
            and parsed.port is not None
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


class LocalReviewBrowser:
    """One page in one reviewer-scoped persistent context."""

    def __init__(self, reviewer: Reviewer, context: BrowserContext, page: Page, timeouts: LocalBrowserTimeouts):
        self.reviewer = reviewer
        self.context = context
        self.page = page
        self.timeouts = timeouts
        self.state = BrowserOperationState.READY
        self._closing = False
        context.on("close", self._on_context_close)
        page.on("close", self._on_page_close)

    def _on_context_close(self, _context: BrowserContext) -> None:
        self._lost()

    def _on_page_close(self, _page: Page) -> None:
        self._lost()

    def _lost(self) -> None:
        if self._closing or self.state is BrowserOperationState.CANCELLED:
            return
        if self.state is BrowserOperationState.WAITING_FOR_RESPONSE:
            self.state = BrowserOperationState.RESPONSE_UNCERTAIN
        elif self.state is not BrowserOperationState.RESPONSE_UNCERTAIN:
            self.state = BrowserOperationState.FAILED

    async def navigate(self, url: str) -> None:
        if self.state is not BrowserOperationState.READY:
            raise LocalBrowserError("Browser session is not ready.")
        if not _local_url(url):
            raise UnsafeNavigationError("Only explicit loopback HTTP pages are allowed.")
        try:
            await self.page.goto(url, timeout=self.timeouts.navigation_ms, wait_until="load")
            if self.state is BrowserOperationState.FAILED:
                raise NavigationFailedError("Browser closed during navigation.")
        except asyncio.CancelledError:
            await self.cancel()
            raise
        except (PlaywrightError, PlaywrightTimeoutError):
            self.state = BrowserOperationState.FAILED
            raise NavigationFailedError("Local navigation failed or timed out.") from None

    def require_user(self) -> None:
        if self.state is not BrowserOperationState.READY:
            raise LocalBrowserError("Browser session is not ready.")
        self.state = BrowserOperationState.WAITING_FOR_USER

    def user_resumed(self) -> None:
        if self.state is not BrowserOperationState.WAITING_FOR_USER or self.page.is_closed():
            raise LocalBrowserError("Manual session recovery is not available.")
        self.state = BrowserOperationState.READY

    def begin_response_wait(self) -> None:
        """Mark that an interaction may have been sent; retry requires reconciliation."""
        if self.state is not BrowserOperationState.READY:
            raise LocalBrowserError("Browser session is not ready.")
        self.state = BrowserOperationState.WAITING_FOR_RESPONSE

    async def wait_for_local_selector(self, selector: str) -> None:
        if self.state is not BrowserOperationState.WAITING_FOR_RESPONSE:
            raise LocalBrowserError("No response is pending.")
        pending = asyncio.create_task(
            self.page.wait_for_selector(selector, timeout=self.timeouts.wait_ms)
        )
        try:
            await asyncio.shield(pending)
            if self.state is BrowserOperationState.WAITING_FOR_RESPONSE:
                self.state = BrowserOperationState.READY
        except asyncio.CancelledError:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            self.state = BrowserOperationState.RESPONSE_UNCERTAIN
            await self.close()
            raise
        except (PlaywrightError, PlaywrightTimeoutError):
            self.state = BrowserOperationState.RESPONSE_UNCERTAIN
            raise LocalBrowserError("Response outcome is uncertain; reconcile before retry.") from None

    async def cancel(self) -> None:
        if self.state in {
            BrowserOperationState.WAITING_FOR_RESPONSE,
            BrowserOperationState.RESPONSE_UNCERTAIN,
        }:
            self.state = BrowserOperationState.RESPONSE_UNCERTAIN
        else:
            self.state = BrowserOperationState.CANCELLED
        await self.close()

    async def close(self) -> None:
        self._closing = True
        if not self.page.is_closed():
            await self.page.close()
        await self.context.close()


class LocalBrowserRuntime:
    """Own reviewer pages and close every underlying session on exit."""

    def __init__(
        self,
        profile_root: Path,
        *,
        headless: bool = True,
        launch_timeout_ms: int = 30_000,
        timeouts: Optional[LocalBrowserTimeouts] = None,
    ) -> None:
        self._sessions = SessionManager(
            profile_root, headless=headless, launch_timeout_ms=launch_timeout_ms
        )
        root = self._sessions.profile_root
        if any((parent / ".git").exists() for parent in (root, *root.parents)):
            raise UnsafeProfileRootError("Browser profiles must be outside a Git checkout.")
        self.timeouts = timeouts or LocalBrowserTimeouts()
        self._pages: dict[Reviewer, LocalReviewBrowser] = {}

    async def open(self, reviewer: Reviewer) -> LocalReviewBrowser:
        if not isinstance(reviewer, Reviewer):
            raise ValueError("reviewer must be a Reviewer")
        current = self._pages.get(reviewer)
        if current is not None and not current.page.is_closed():
            return current
        try:
            context = await self._sessions.open(reviewer.value)
            # The guard covers redirects and page subresources as well as direct goto.
            await context.route("**/*", self._allow_local_route)
            # HTTP routing does not intercept WebSocket handshakes. Close the
            # client-side route before connect(), including for loopback URLs.
            await context.route_web_socket(
                "**/*", lambda websocket: websocket.close(code=1008, reason="Blocked")
            )
            context.set_default_navigation_timeout(self.timeouts.navigation_ms)
            context.set_default_timeout(self.timeouts.wait_ms)
            page = await context.new_page()
        except asyncio.CancelledError:
            await self._sessions.close(reviewer.value)
            raise
        except Exception:
            await self._sessions.close(reviewer.value)
            raise LocalBrowserError("Local browser session could not open.") from None
        browser = LocalReviewBrowser(reviewer, context, page, self.timeouts)
        self._pages[reviewer] = browser
        return browser

    @staticmethod
    async def _allow_local_route(route) -> None:
        if _local_url(route.request.url):
            await route.continue_()
        else:
            await route.abort()

    async def close(self) -> None:
        for browser in self._pages.values():
            browser._closing = True
        try:
            await self._sessions.close_all()
        finally:
            self._pages.clear()

    async def __aenter__(self) -> "LocalBrowserRuntime":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
