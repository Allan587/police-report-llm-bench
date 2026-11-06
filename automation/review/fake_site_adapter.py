"""Test-only bridge from seven local page replies to the existing reviewer port."""
from __future__ import annotations

import asyncio
import hashlib
import json
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
from automation.contracts import (
    BenchmarkModel, CompleteReviewResult, HistoryProposalOutput, MainReviewOutput,
    ProfileProposalOutput, utc_now,
)
from .vertical_flow import ReviewerAdapterError, ReviewerExecutionRequest

def _raw_artifact(content: str, slot: str) -> dict:
    try:
        raw = json.loads(content)
    except (TypeError, ValueError):
        raise ReviewerAdapterError(f"Fake response for {slot} is invalid JSON.") from None
    if not isinstance(raw, dict):
        raise ReviewerAdapterError(f"Fake response for {slot} is not an object.")
    return raw


class FakeSiteReviewerAdapter:
    """Test-only synchronous ReviewerAdapter bridge using seven explicit page replies."""

    def __init__(self, base_url: str, profile_root: Path, *, prompt: str,
                 prepare_fixture: Callable[[ReviewerExecutionRequest], None] | None = None,
                 session_factory: BrowserSessionFactory | None = None, operator: str | None = None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty")
        self.base_url = base_url
        self.profile_root = profile_root
        self.prompt = prompt
        self.prepare_fixture = prepare_fixture
        if (session_factory is None) != (operator is None):
            raise ValueError("session_factory and operator must be supplied together")
        self.session_factory = session_factory
        self.operator = operator
        self.observations: tuple[ObservedResponse, ...] = ()

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
        if self.prepare_fixture is not None:
            self.prepare_fixture(request)
        raw: dict[str, dict] = {}
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
        except (LocalBrowserError, BrowserManagerError, PortableConfigError, PlaywrightError):
            raise ReviewerAdapterError("Synthetic browser session failed safely.") from None
        self.observations = tuple(observed)
        return self._validated_result(request, raw)

    async def _collect(self, request, browser, raw, observed):
        for slot in FAKE_REVIEW_SLOTS:
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
                raise ReviewerAdapterError(str(error)) from None
            raw[slot] = _raw_artifact(reply.content or "", slot)
            observed.append(reply)

    def _validated_result(self, request, raw):
        identity = {
            "task_id": request.task.task_id,
            "attempt_id": request.attempt.attempt_id,
            "statement_index": request.inputs.statement_index,
            "reviewer": request.task.reviewer.value,
        }
        if request.plan.run_id != request.inputs.selected_run_id:
            raise ReviewerAdapterError("Synthetic request run identity is inconsistent.")
        for slot, artifact in raw.items():
            if (any(artifact.get(key) != value for key, value in identity.items())
                    or ("run_id" in artifact and artifact["run_id"] != request.inputs.selected_run_id)):
                raise ReviewerAdapterError(f"Fake response for {slot} has wrong identity.")
        reviewer = request.task.reviewer
        index = request.inputs.statement_index
        attempt = request.attempt.attempt_id
        try:
            main = MainReviewOutput(index, reviewer, attempt, raw["main"])
            histories = tuple(HistoryProposalOutput(index, reviewer, attempt, model,
                raw[f"history_{model.value}"]) for model in BenchmarkModel)
            proposals = tuple(ProfileProposalOutput(index, reviewer, attempt, model,
                raw[f"profile_{model.value}"]) for model in BenchmarkModel)
            return CompleteReviewResult(request.task.task_id, attempt, index, reviewer,
                                        main, histories, proposals, utc_now())
        except (KeyError, ValueError):
            raise ReviewerAdapterError("Fake response set failed review validation.") from None
