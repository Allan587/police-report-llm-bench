from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from automation.browser.local_runtime import LocalBrowserRuntime, LocalBrowserTimeouts
from automation.browser.web_interaction import (
    FakePageInteraction, WebInteractionError, WebResponseState,
)
from automation.contracts import Reviewer
from automation.review.fake_site_adapter import FakeSiteReviewerAdapter
from automation.review.vertical_flow import (
    DeterministicFakeReviewerAdapter, VerticalReviewStatus, run_synthetic_reviewer_once,
)
from tests.helpers.fake_reviewer_sites import FakeSiteServer
from tests.test_synthetic_review_flow import ready_fixture, snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("reviewer", list(Reviewer))
async def test_each_fake_navigates_accepts_input_and_returns_complete(tmp_path, reviewer):
    with FakeSiteServer() as site:
        site.artifacts[(reviewer.value, "main")] = {"fixture": reviewer.value}
        async with LocalBrowserRuntime(tmp_path / "profiles") as runtime:
            browser = await runtime.open(reviewer)
            interaction = FakePageInteraction(browser, site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            assert interaction.state is WebResponseState.PREPARED
            assert await browser.page.locator('[data-role="prompt"]').input_value() == "synthetic prompt"
            assert await browser.page.locator('[data-fake]').get_attribute("data-fake") == reviewer.value
            await interaction.submit()
            result = await interaction.complete()
            assert result.state is WebResponseState.COMPLETE
            assert json.loads(result.content) == {"fixture": reviewer.value}
            assert result.request_sha256 == hashlib.sha256(b"synthetic prompt").hexdigest()
            assert result.content_sha256 == hashlib.sha256(result.content.encode()).hexdigest()
            assert result.evidence == "data-state:complete"
            assert await browser.page.locator('[data-role="submit"]').get_attribute("data-clicks") == "1"
        assert site.visits == [(reviewer.value, "main")]


@pytest.mark.asyncio
async def test_streaming_is_partial_until_complete(tmp_path):
    with FakeSiteServer() as site:
        site.scenarios[("gemini", "main")] = "streaming"
        site.artifacts[("gemini", "main")] = {"fixture": "full synthetic content"}
        async with LocalBrowserRuntime(tmp_path / "profiles") as runtime:
            browser = await runtime.open(Reviewer.GEMINI)
            interaction = FakePageInteraction(browser, site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.submit()
            await browser.page.wait_for_selector('[data-state="partial"]')
            partial = await interaction.observe()
            assert partial.state is WebResponseState.PARTIAL
            assert partial.content != json.dumps(site.artifacts[("gemini", "main")])
            complete = await interaction.complete()
            assert complete.state is WebResponseState.COMPLETE
            assert json.loads(complete.content) == site.artifacts[("gemini", "main")]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,state", [
    ("error", WebResponseState.UI_ERROR),
    ("expired", WebResponseState.SESSION_EXPIRED),
    ("auth", WebResponseState.AUTH_REQUIRED),
])
async def test_terminal_ui_states_reject_content(tmp_path, scenario, state):
    with FakeSiteServer() as site:
        site.scenarios[("grok", "main")] = scenario
        async with LocalBrowserRuntime(tmp_path / "profiles") as runtime:
            interaction = FakePageInteraction(await runtime.open(Reviewer.GROK), site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.submit()
            with pytest.raises(WebInteractionError) as raised:
                await interaction.complete()
            assert raised.value.state is state
            assert site.visits == [("grok", "main")]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["partial", "timeout"])
async def test_partial_and_timeout_never_become_complete(tmp_path, scenario):
    with FakeSiteServer() as site:
        site.scenarios[("chatgpt", "main")] = scenario
        site.artifacts[("chatgpt", "main")] = {"fixture": "unusable"}
        async with LocalBrowserRuntime(
            tmp_path / "profiles", timeouts=LocalBrowserTimeouts(1000, 70)
        ) as runtime:
            interaction = FakePageInteraction(await runtime.open(Reviewer.CHATGPT), site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.submit()
            with pytest.raises(WebInteractionError) as raised:
                await interaction.complete()
            assert raised.value.state is WebResponseState.TIMEOUT
            assert interaction.browser.state.value == "RESPONSE_UNCERTAIN"
            with pytest.raises(WebInteractionError):
                await interaction.submit()
        assert site.visits == [("chatgpt", "main")]


@pytest.mark.asyncio
async def test_cancel_and_browser_loss_after_send_are_uncertain(tmp_path):
    with FakeSiteServer() as site:
        site.scenarios[("chatgpt", "main")] = "uncertain"
        async with LocalBrowserRuntime(tmp_path / "profiles") as runtime:
            interaction = FakePageInteraction(await runtime.open(Reviewer.CHATGPT), site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.submit()
            await interaction.cancel()
            assert interaction.state is WebResponseState.UNCERTAIN
            assert interaction.browser.page.is_closed()
            with pytest.raises(WebInteractionError):
                await interaction.submit()
        assert site.visits == [("chatgpt", "main")]
    with FakeSiteServer() as site:
        site.scenarios[("grok", "main")] = "uncertain"
        async with LocalBrowserRuntime(tmp_path / "other-profiles") as runtime:
            interaction = FakePageInteraction(await runtime.open(Reviewer.GROK), site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.submit()
            await interaction.browser.context.close()
            observed = await interaction.observe()
            assert observed.state is WebResponseState.UNCERTAIN
        assert site.visits == [("grok", "main")]


def load_seven_fixture(site, *, omit=None, wrong_identity=False):
    def loader(request):
        fixture = DeterministicFakeReviewerAdapter().execute(request)
        artifacts = {"main": dict(fixture.main_review.output)}
        artifacts.update({f"history_{item.model.value}": dict(item.output) for item in fixture.histories})
        artifacts.update({f"profile_{item.model.value}": dict(item.output) for item in fixture.profile_proposals})
        if wrong_identity:
            artifacts["history_gemma"]["task_id"] = "wrong-task"
        for slot, value in artifacts.items():
            if slot != omit:
                site.artifacts[(request.task.reviewer.value, slot)] = value
    return loader


def test_seven_explicit_interactions_publish_via_existing_storage(tmp_path):
    (tmp_path / "fixture").mkdir()
    config = ready_fixture(tmp_path / "fixture")
    feedback_before = snapshot(config.coordinator.workflow.feedback_root)
    profiles_before = snapshot(config.coordinator.workflow.profiles_root)
    with FakeSiteServer() as site:
        adapter = FakeSiteReviewerAdapter(
            site.base_url, tmp_path / "browser-profiles", prompt="synthetic prepared review",
            prepare_fixture=load_seven_fixture(site),
        )
        outcome = run_synthetic_reviewer_once(config, adapter)
        assert outcome.status is VerticalReviewStatus.COMPLETED
        assert outcome.publication is not None
        assert len(adapter.observations) == 7
        assert len(site.visits) == 7
        assert {slot for _, slot in site.visits} == {
            "main", "history_gemma", "history_mistral", "history_llama",
            "profile_gemma", "profile_mistral", "profile_llama",
        }
        assert all(reply.state is WebResponseState.COMPLETE for reply in adapter.observations)
        assert len({reply.request_sha256 for reply in adapter.observations}) == 7
        assert outcome.publication.confirmed.task_id == outcome.task.task_id
        assert outcome.publication.confirmed.attempt_id == outcome.attempt.attempt_id
        assert outcome.publication.confirmed.attempt_metadata.run_id == "run-a"
        assert outcome.publication.confirmed.statement_index == 10
        assert outcome.publication.confirmed.reviewer is Reviewer.CHATGPT
        assert len(list(outcome.publication.confirmed.set_path.rglob("*.json"))) == 8  # manifest + seven
    assert snapshot(config.coordinator.workflow.feedback_root) == feedback_before
    assert snapshot(config.coordinator.workflow.profiles_root) == profiles_before


@pytest.mark.parametrize("failure", ["missing", "wrong_identity"])
def test_missing_or_wrong_artifact_blocks_publication(tmp_path, failure):
    (tmp_path / "fixture").mkdir()
    config = ready_fixture(tmp_path / "fixture")
    with FakeSiteServer() as site:
        adapter = FakeSiteReviewerAdapter(
            site.base_url, tmp_path / "browser-profiles", prompt="synthetic prepared review",
            prepare_fixture=load_seven_fixture(site, omit="profile_llama" if failure == "missing" else None,
                                                 wrong_identity=failure == "wrong_identity"),
        )
        outcome = run_synthetic_reviewer_once(config, adapter)
        assert outcome.status is VerticalReviewStatus.ADAPTER_FAILED
        assert outcome.publication is None
        assert not (config.coordinator.workflow.review_cases_root / "statement_010" / ".review_sets").exists()


@pytest.mark.asyncio
async def test_cancel_during_wait_does_not_resubmit(tmp_path):
    with FakeSiteServer() as site:
        site.scenarios[("gemini", "main")] = "partial"
        async with LocalBrowserRuntime(tmp_path / "profiles") as runtime:
            interaction = FakePageInteraction(await runtime.open(Reviewer.GEMINI), site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.submit()
            pending = asyncio.create_task(interaction.complete())
            await interaction.browser.page.wait_for_selector('[data-state="partial"]', state='attached')
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert interaction.state is WebResponseState.UNCERTAIN
            assert interaction.browser.page.is_closed()
        assert site.visits == [("gemini", "main")]


@pytest.mark.asyncio
async def test_cancel_before_send_is_distinct(tmp_path):
    with FakeSiteServer() as site:
        async with LocalBrowserRuntime(tmp_path / "profiles") as runtime:
            interaction = FakePageInteraction(await runtime.open(Reviewer.GROK), site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.cancel()
            assert interaction.state is WebResponseState.CANCELLED
            assert site.visits == [("grok", "main")]
