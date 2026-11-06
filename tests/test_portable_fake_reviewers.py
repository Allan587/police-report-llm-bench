from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from automation.contracts import Reviewer
from automation.orchestrator import PlanAction
from automation.portable.browser import BrowserSessionFactory
from automation.portable.fake_session import LocalFakeBrowserSession
from automation.portable.config import PortableConfigError, load_config
from automation.portable.inputs import InputAdapter
from automation.review.fake_site_adapter import FakeSiteReviewerAdapter
from automation.review.synthetic_adjudication import (
    DeterministicFakeAdjudicatorAdapter, SyntheticAdjudicationConfig,
    run_synthetic_three_reviewers_and_persist,
)
from automation.review.synthetic_application import SyntheticApplicationConfig, run_synthetic_application_once
from automation.review.vertical_flow import VerticalReviewConfig, VerticalReviewStatus, run_synthetic_reviewer_once
from automation.workflow_coordinator import WorkflowCoordinatorConfig
from tests.helpers.fake_reviewer_sites import FakeSiteServer
from tests.test_portable_environment import configuration, source_fixture
from tests.test_synthetic_adjudication_flow import snapshot
from tests.test_web_interaction_fakes import load_seven_fixture


def prepared(tmp_path, name="one"):
    source = source_fixture(tmp_path / f"{name}-source")
    config = load_config(configuration(tmp_path, source.statements_path.parent, name))
    prepared_input = InputAdapter(config).prepare()
    fixture_root = config.output_root.parent
    coordinator = WorkflowCoordinatorConfig(
        prepared_input.workflow, fixture_root / "runtime/workflow.lock", "portable-web",
        lease_location_confirmed=True)
    review = VerticalReviewConfig(coordinator, fixture_root, fixture_location_confirmed=True)
    adjudication = SyntheticAdjudicationConfig(
        review, config.output_root / "prompts/review_adjudication.md")
    return config, adjudication


def adapter_for(site, config, *, operator="operator", loader=None):
    return FakeSiteReviewerAdapter(
        site.base_url, config.browser.profile_root, prompt="synthetic prepared review",
        prepare_fixture=loader or load_seven_fixture(site),
        session_factory=BrowserSessionFactory(config.browser), operator=operator,
    )


def test_portable_browser_to_publication_and_adjudication_twice(tmp_path):
    for name in ("alpha", "beta"):
        config, adjudication = prepared(tmp_path, name)
        before = snapshot(config.input_root)
        with FakeSiteServer() as site:
            adapter = adapter_for(site, config)
            result = run_synthetic_three_reviewers_and_persist(
                adjudication, adapter, DeterministicFakeAdjudicatorAdapter())
            assert [item.status for item in result.reviewer_results] == [VerticalReviewStatus.COMPLETED] * 3
            assert [item.after_plan.action for item in result.reviewer_results] == [
                PlanAction.RUN_MISSING_REVIEWER,
                PlanAction.RUN_MISSING_REVIEWER,
                PlanAction.RUN_ADJUDICATIONS,
            ]
            assert len(site.visits) == 21
            assert len(adapter.observations) == 7
            assert all(item.content_sha256 == hashlib.sha256(item.content.encode()).hexdigest()
                       for item in adapter.observations)
            assert result.adjudication.status.value == "PERSISTED"
            application = SyntheticApplicationConfig(
                adjudication.review.coordinator, config.output_root.parent, 10,
                result.adjudication.application.application_id,
                fixture_location_confirmed=True)
            assert run_synthetic_application_once(application).status.value == "APPLIED"
            assert len(list((config.output_root / "review_cases/statement_010/.review_sets").glob("*"))) == 3
            assert snapshot(config.input_root) == before
            for model in ("gemma", "mistral", "llama"):
                assert json.loads((config.output_root / f"profiles/{model}_profile.json").read_text())[
                    "profile_metadata"]["outputs_reviewed"] == 11
                assert json.loads((config.output_root / f"feedback/{model}_history.jsonl").read_text().splitlines()[-1])[
                    "output"] == 10


@pytest.mark.parametrize("bad_url", [
    "https://chatgpt.com", "http://127.0.0.1:9999/other", "http://localhost:9999",
    "http://127.0.0.1:9999?authorized=true", "http://user@127.0.0.1:9999",
])
def test_rejects_non_fake_origins_before_browser_open(tmp_path, bad_url):
    config, _ = prepared(tmp_path)
    with pytest.raises(PortableConfigError):
        LocalFakeBrowserSession(BrowserSessionFactory(config.browser), "operator", Reviewer.CHATGPT, bad_url)
    assert not config.browser.profile_root.exists()


@pytest.mark.asyncio
async def test_rejects_existing_profile_and_isolates_operators(tmp_path):
    config, _ = prepared(tmp_path)
    with FakeSiteServer() as site:
        factory = BrowserSessionFactory(config.browser)
        async with LocalFakeBrowserSession(factory, "alice", Reviewer.CHATGPT, site.base_url):
            pass
        with pytest.raises(PortableConfigError, match="new browser profile"):
            async with LocalFakeBrowserSession(factory, "alice", Reviewer.CHATGPT, site.base_url):
                pass
        async with LocalFakeBrowserSession(factory, "bob", Reviewer.CHATGPT, site.base_url):
            pass
        async with LocalFakeBrowserSession(factory, "alice", Reviewer.GEMINI, site.base_url):
            pass
        assert (config.browser.profile_root / "alice/chatgpt").is_dir()
        assert (config.browser.profile_root / "bob/chatgpt").is_dir()
        assert (config.browser.profile_root / "alice/gemini").is_dir()


@pytest.mark.parametrize("scenario", ["redirect_external", "redirect_local", "partial", "expired", "timeout"])
def test_invalid_page_never_publishes_complete_review(tmp_path, scenario):
    config, adjudication = prepared(tmp_path)
    if scenario == "timeout":
        from dataclasses import replace
        config = replace(config, browser=replace(config.browser, timeout_ms=1000))
    with FakeSiteServer() as site:
        site.scenarios[("chatgpt", "main")] = scenario
        outcome = run_synthetic_reviewer_once(adjudication.review, adapter_for(site, config))
        assert outcome.status is VerticalReviewStatus.ADAPTER_FAILED
        assert outcome.publication is None
        assert not (config.output_root / "review_cases/statement_010/.review_sets").exists()


@pytest.mark.parametrize("mutation", ["task", "run", "reviewer"])
def test_wrong_identity_never_publishes(tmp_path, mutation):
    config, adjudication = prepared(tmp_path)
    with FakeSiteServer() as site:
        def load(request):
            load_seven_fixture(site)(request)
            artifact = site.artifacts[("chatgpt", "history_gemma")]
            if mutation == "task":
                artifact["task_id"] = "wrong-task"
            elif mutation == "run":
                artifact["run_id"] = "wrong-run"
            else:
                artifact["reviewer"] = "gemini"
        adapter = adapter_for(site, config, loader=load)
        outcome = run_synthetic_reviewer_once(adjudication.review, adapter)
        assert outcome.status is VerticalReviewStatus.ADAPTER_FAILED
        assert outcome.publication is None


@pytest.mark.asyncio
async def test_unexpected_close_after_possible_send_is_uncertain(tmp_path):
    from automation.browser.web_interaction import FakePageInteraction, WebResponseState
    config, _ = prepared(tmp_path)
    with FakeSiteServer() as site:
        site.scenarios[("chatgpt", "main")] = "partial"
        async with LocalFakeBrowserSession(
            BrowserSessionFactory(config.browser), "operator", Reviewer.CHATGPT, site.base_url
        ) as browser:
            interaction = FakePageInteraction(browser, site.base_url)
            await interaction.prepare("main", "synthetic prompt")
            await interaction.submit()
            await browser.context.close()
            observation = await interaction.observe()
            assert observation.state is WebResponseState.UNCERTAIN
            assert site.visits == [("chatgpt", "main")]
