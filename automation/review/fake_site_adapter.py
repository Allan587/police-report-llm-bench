"""Test-only bridge from seven local page replies to the existing reviewer port."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Callable

from automation.browser.local_runtime import LocalBrowserError, LocalBrowserRuntime
from automation.browser.manager import BrowserManagerError
from automation.portable.config import PortableConfigError
from playwright.async_api import Error as PlaywrightError
from automation.portable.browser import BrowserSessionFactory
from automation.portable.fake_session import LocalFakeBrowserSession
from automation.browser.web_interaction import (
    FAKE_REVIEW_SLOTS, FakePageInteraction, ObservedResponse, WebInteractionError,
)
from automation.contracts import CompleteReviewResult
from .capture_protocol import CaptureStatus, capture_separate, capture_structured, result_from_capture
from .vertical_flow import ReviewerAdapterError, ReviewerExecutionRequest


class FakeSiteReviewerAdapter:
    """Test-only ReviewerAdapter bridge for separate or structured fake replies."""

    def __init__(self, base_url: str, profile_root: Path, *, prompt: str,
                 prepare_fixture: Callable[[ReviewerExecutionRequest], None] | None = None,
                 session_factory: BrowserSessionFactory | None = None, operator: str | None = None,
                 capture_pattern: str = "separate"):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty")
        self.base_url = base_url
        self.profile_root = profile_root
        self.prompt = prompt
        if capture_pattern not in {"separate", "structured"}:
            raise ValueError("unknown synthetic capture pattern")
        self.capture_pattern = capture_pattern
        self.prepare_fixture = prepare_fixture
        if (session_factory is None) != (operator is None):
            raise ValueError("session_factory and operator must be supplied together")
        self.session_factory = session_factory
        self.operator = operator
        self.observations: tuple[ObservedResponse, ...] = ()
        self.capture_status = CaptureStatus.PARTIAL

    def execute(self, request: ReviewerExecutionRequest) -> CompleteReviewResult:
        if not isinstance(request, ReviewerExecutionRequest):
            raise TypeError("request must be ReviewerExecutionRequest")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._execute(request))
        raise RuntimeError("synchronous ReviewerAdapter must run outside an event loop")

    async def _execute(self, request: ReviewerExecutionRequest) -> CompleteReviewResult:
        self.observations = ()
        self.capture_status = CaptureStatus.PARTIAL
        if self.prepare_fixture is not None:
            self.prepare_fixture(request)
        raw: dict[str, ObservedResponse] = {}
        observed: list[ObservedResponse] = []
        try:
            if self.session_factory is None:
                async with LocalBrowserRuntime(self.profile_root) as runtime:
                    browser = await runtime.open(request.task.reviewer)
                    await self._collect(request, browser, raw, observed)
            else:
                async with LocalFakeBrowserSession(
                    self.session_factory,
                    f"{self.operator[:48]}-{hashlib.sha256(request.attempt.attempt_id.encode()).hexdigest()[:12]}",
                    request.task.reviewer, self.base_url
                ) as browser:
                    await self._collect(request, browser, raw, observed)
        except ReviewerAdapterError:
            self.observations = tuple(observed)
            raise
        except (LocalBrowserError, BrowserManagerError, PortableConfigError, PlaywrightError):
            self.observations = tuple(observed)
            self.capture_status = CaptureStatus.UNCERTAIN
            raise ReviewerAdapterError("Synthetic browser session failed safely.") from None
        self.observations = tuple(observed)
        components = (capture_separate(request, raw) if self.capture_pattern == "separate"
                      else capture_structured(request, raw["bundle"]))
        result = result_from_capture(request, components)
        self.capture_status = CaptureStatus.COMPLETE
        return result

    async def _collect(self, request, browser, raw, observed):
        for slot in (FAKE_REVIEW_SLOTS if self.capture_pattern == "separate" else ("bundle",)):
            interaction = FakePageInteraction(browser, self.base_url)
            prepared_prompt = (
                f"{self.prompt}\nslot:{slot}\nreviewer_id:{request.task.reviewer.value}"
                f"\nstatement_index:{request.inputs.statement_index}"
                f"\nrun_id:{request.inputs.selected_run_id}"
                f"\ntask_id:{request.task.task_id}\nattempt_id:{request.attempt.attempt_id}"
            )
            await interaction.prepare(slot, prepared_prompt)
            try:
                await interaction.submit()
                reply = await interaction.complete()
            except WebInteractionError as error:
                self.capture_status = (CaptureStatus.UNCERTAIN if error.state.value in
                                       {"TIMEOUT", "UNCERTAIN"} else CaptureStatus.PARTIAL)
                raise ReviewerAdapterError(str(error)) from None
            raw[slot] = reply
            observed.append(reply)
