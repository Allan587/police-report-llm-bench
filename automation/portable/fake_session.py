"""Strictly local fake-site session built on the portable Chromium factory."""
from __future__ import annotations

from urllib.parse import urlsplit

from automation.contracts import Reviewer
from automation.browser.local_runtime import LocalBrowserTimeouts, LocalReviewBrowser
from automation.browser.web_interaction import FAKE_CAPTURE_ROUTES
from .browser import BrowserSessionFactory
from .config import PortableConfigError


class LocalFakeBrowserSession:
    """One fresh factory-owned profile confined to one synthetic loopback site.

    This class accepts only the seven fake routes or the synthetic bundle route. It is not a provider
    connector, and intentionally refuses existing profiles/authentication state.
    """

    def __init__(self, factory: BrowserSessionFactory, operator: str,
                 reviewer: Reviewer, base_url: str):
        if not isinstance(reviewer, Reviewer):
            raise PortableConfigError("fake session reviewer is invalid")
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except (TypeError, ValueError):
            raise PortableConfigError("fake site origin is invalid") from None
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or port is None or parsed.username is not None
                or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise PortableConfigError("fake site must be an explicit 127.0.0.1 HTTP origin")
        self.origin = f"http://127.0.0.1:{port}"
        self.reviewer = reviewer
        self.manager = factory.create(operator, reviewer.value)
        self.timeouts = LocalBrowserTimeouts(factory.config.timeout_ms, factory.config.timeout_ms)
        self.browser: LocalReviewBrowser | None = None

    async def __aenter__(self) -> LocalReviewBrowser:
        # A fresh profile prevents importing an authenticated browser state.
        if self.manager.profile_dir.exists():
            raise PortableConfigError("fake session requires a new browser profile")
        try:
            context = await self.manager.open()
            await context.route("**/*", self._route)
            await context.route_web_socket(
                "**/*", lambda websocket: websocket.close(code=1008, reason="Blocked"))
            context.set_default_navigation_timeout(self.timeouts.navigation_ms)
            context.set_default_timeout(self.timeouts.wait_ms)
            page = await context.new_page()
            self.browser = LocalReviewBrowser(self.reviewer, context, page, self.timeouts)
            return self.browser
        except BaseException:
            await self.manager.close()
            raise

    async def _route(self, route) -> None:
        request = route.request
        parsed = urlsplit(request.url)
        allowed = {f"/{self.reviewer.value}/{slot}" for slot in FAKE_CAPTURE_ROUTES}
        if (request.method == "GET" and
                f"{parsed.scheme}://{parsed.netloc}" == self.origin and
                parsed.path in allowed and not parsed.query and not parsed.fragment):
            await route.continue_()
        else:
            await route.abort()

    async def __aexit__(self, _type, _value, _traceback) -> None:
        await self.manager.close()
