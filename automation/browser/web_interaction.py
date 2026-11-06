"""Small observable web interaction contract for loopback reviewer simulations.

The browser layer observes a page; it does not validate or publish reviews.
No provider DOM or external capability is asserted by this module.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from automation.contracts import utc_now
from .local_runtime import BrowserOperationState, LocalBrowserError, LocalReviewBrowser


class WebResponseState(str, Enum):
    PREPARED = "PREPARED"
    WAITING = "WAITING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    UI_ERROR = "UI_ERROR"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    UNCERTAIN = "UNCERTAIN"


class WebInteractionError(RuntimeError):
    def __init__(self, state: WebResponseState):
        self.state = state
        super().__init__(f"Simulated web interaction ended in {state.value}.")


@dataclass(frozen=True)
class ObservedResponse:
    state: WebResponseState
    content: str | None
    observed_at: datetime
    request_sha256: str
    content_sha256: str | None
    evidence: str


class WebInteraction(Protocol):
    async def prepare(self, slot: str, prompt: str) -> None: ...
    async def submit(self) -> None: ...
    async def observe(self) -> ObservedResponse: ...
    async def complete(self) -> ObservedResponse: ...
    async def cancel(self) -> None: ...


FAKE_REVIEW_SLOTS = ("main", "history_gemma", "history_mistral", "history_llama",
          "profile_gemma", "profile_mistral", "profile_llama")
FAKE_CAPTURE_ROUTES = (*FAKE_REVIEW_SLOTS, "bundle")
_PAGE_STATES = {
    "partial": WebResponseState.PARTIAL,
    "complete": WebResponseState.COMPLETE,
    "error": WebResponseState.UI_ERROR,
    "auth": WebResponseState.AUTH_REQUIRED,
    "expired": WebResponseState.SESSION_EXPIRED,
}


class FakePageInteraction:
    """DOM contract shared by three deliberately synthetic local pages."""

    def __init__(self, browser: LocalReviewBrowser, base_url: str):
        self.browser = browser
        self.base_url = base_url.rstrip("/")
        self.state = WebResponseState.PREPARED
        self._fingerprint: str | None = None
        self._sent = False

    async def prepare(self, slot: str, prompt: str) -> None:
        if self._sent or self.browser.state is not BrowserOperationState.READY:
            raise WebInteractionError(WebResponseState.UNCERTAIN)
        if slot not in FAKE_CAPTURE_ROUTES or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("slot and prompt must be valid")
        target = f"{self.base_url}/{self.browser.reviewer.value}/{slot}"
        await self.browser.navigate(target)
        if (self.browser.page.url != target or
                await self.browser.page.locator("[data-fake]").get_attribute("data-fake") != self.browser.reviewer.value):
            raise LocalBrowserError("Unexpected synthetic page identity.")
        await self.browser.page.locator('[data-role="prompt"]').fill(prompt)
        self._fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self.state = WebResponseState.PREPARED

    async def submit(self) -> None:
        if self._fingerprint is None or self._sent:
            raise WebInteractionError(WebResponseState.UNCERTAIN)
        # Mark possible submission before clicking. An interrupted click is uncertain.
        self._sent = True
        self.browser.begin_response_wait()
        self.state = WebResponseState.WAITING
        try:
            await self.browser.page.locator('[data-role="submit"]').click()
        except asyncio.CancelledError:
            self.state = WebResponseState.UNCERTAIN
            await self.browser.cancel()
            raise
        except Exception:
            self.state = WebResponseState.UNCERTAIN
            await self.browser.cancel()
            raise WebInteractionError(self.state) from None

    async def observe(self) -> ObservedResponse:
        if not self._sent:
            raise WebInteractionError(WebResponseState.PREPARED)
        if self.browser.state is BrowserOperationState.RESPONSE_UNCERTAIN:
            self.state = WebResponseState.UNCERTAIN
            return self._observation(None, "browser_lost")
        try:
            marker = await self.browser.page.locator('[data-role="response"]').get_attribute("data-state")
            mapped = _PAGE_STATES.get(marker)
            self.state = mapped or WebResponseState.WAITING
            content = await self.browser.page.locator('[data-role="response"]').text_content()
        except Exception:
            self.state = WebResponseState.UNCERTAIN
            return self._observation(None, "dom_unavailable")
        return self._observation(content if mapped in {WebResponseState.PARTIAL, WebResponseState.COMPLETE} else None,
                                 f"data-state:{marker or 'waiting'}")

    def _observation(self, content: str | None, evidence: str) -> ObservedResponse:
        return ObservedResponse(
            self.state, content, utc_now(), self._fingerprint or "",
            hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None,
            evidence,
        )

    async def complete(self) -> ObservedResponse:
        if not self._sent:
            raise WebInteractionError(WebResponseState.PREPARED)
        if self.state is WebResponseState.UNCERTAIN:
            raise WebInteractionError(self.state)
        try:
            await self.browser.wait_for_local_selector(
                '[data-role="response"][data-state="complete"],'
                '[data-role="response"][data-state="error"],'
                '[data-role="response"][data-state="auth"],'
                '[data-role="response"][data-state="expired"]'
            )
        except asyncio.CancelledError:
            self.state = WebResponseState.UNCERTAIN
            raise
        except LocalBrowserError:
            self.state = WebResponseState.TIMEOUT if not self.browser.page.is_closed() else WebResponseState.UNCERTAIN
            raise WebInteractionError(self.state) from None
        observed = await self.observe()
        if observed.state is not WebResponseState.COMPLETE or not observed.content:
            raise WebInteractionError(observed.state)
        return observed

    async def cancel(self) -> None:
        await self.browser.cancel()
        self.state = (WebResponseState.UNCERTAIN if self._sent else WebResponseState.CANCELLED)
