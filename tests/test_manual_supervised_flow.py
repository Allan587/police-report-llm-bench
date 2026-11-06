"""Synthetic operator handoff, recovery, and full approved downstream flow."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from automation.contracts import Reviewer
from automation.orchestrator import PlanAction, plan_next_statement
from automation.review.manual_supervisor import (
    ManualSupervisorError, import_manual_package, manual_status,
    prepare_manual_request, template_for,
)
from automation.review.manual_import_adapter import ManualReviewImportError
from automation.review.synthetic_adjudication import (
    DeterministicFakeAdjudicatorAdapter, SyntheticAdjudicationStatus,
    run_synthetic_adjudication_and_persist_once,
)
from automation.review.synthetic_application import (
    SyntheticApplicationConfig, SyntheticApplicationStatus,
    run_synthetic_application_once,
)
from automation.review.vertical_flow import VerticalReviewStatus
from tests.helpers.manual_review_package import digest, synthetic_package
from tests.test_synthetic_adjudication_flow import ready_fixture
from tests.test_synthetic_review_flow import snapshot


def package_for(request, inbox: Path):
    package = synthetic_package(task=request.task_id, attempt=request.attempt_id,
                                reviewer=request.reviewer, run=request.run_id)
    path = inbox / f"{request.reviewer.value}.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    return path, package


def import_for(config, inbox, request, path):
    return import_manual_package(config.review, path, allowed_root=inbox,
                                 reviewer=request.reviewer)


def test_three_operator_packages_then_adjudication_and_incremental_application(tmp_path):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    workflow = config.review.coordinator.workflow
    feedback_before = snapshot(workflow.feedback_root)
    profiles_before = snapshot(workflow.profiles_root)
    for count, reviewer in enumerate(Reviewer, 1):
        request = prepare_manual_request(config.review)
        assert request.reviewer is reviewer
        assert prepare_manual_request(config.review) == request
        assert all(item["output"] is None for item in template_for(request)["components"])
        assert manual_status(config.review)["reviewers"][reviewer.value]["state"] == "WAIT_OPERATOR"
        path, _ = package_for(request, inbox)
        assert manual_status(config.review, package_path=path, allowed_root=inbox)[
            "reviewers"][reviewer.value]["state"] == "VALID"
        outcome = import_for(config, inbox, request, path)
        assert outcome.status is VerticalReviewStatus.COMPLETED
        status = manual_status(config.review)
        assert status["valid_reviewers"] == count
        assert status["missing_reviewers"] == 3 - count
        assert status["adjudication_enabled"] is (count == 3)
        assert status["reviewers"][reviewer.value]["state"] == "PUBLISHED"
        assert snapshot(workflow.feedback_root) == feedback_before
        assert snapshot(workflow.profiles_root) == profiles_before
        if count < 3:
            assert plan_next_statement(workflow).action is PlanAction.RUN_MISSING_REVIEWER
            assert run_synthetic_adjudication_and_persist_once(
                config, DeterministicFakeAdjudicatorAdapter()
            ).status is SyntheticAdjudicationStatus.BLOCKED
    assert plan_next_statement(workflow).action is PlanAction.RUN_ADJUDICATIONS
    adjudication = run_synthetic_adjudication_and_persist_once(
        config, DeterministicFakeAdjudicatorAdapter())
    assert adjudication.status is SyntheticAdjudicationStatus.PERSISTED
    assert adjudication.application is not None
    application = SyntheticApplicationConfig(
        config.review.coordinator, config.review.fixture_root, 10,
        adjudication.application.application_id, fixture_location_confirmed=True)
    applied = run_synthetic_application_once(application)
    assert applied.status is SyntheticApplicationStatus.APPLIED
    assert snapshot(workflow.feedback_root) != feedback_before
    assert snapshot(workflow.profiles_root) != profiles_before
    assert run_synthetic_application_once(application).status is SyntheticApplicationStatus.UNCHANGED
    assert import_for(config, inbox, request, path).status is VerticalReviewStatus.UNCHANGED


def test_restart_replay_and_incompatible_identity(tmp_path):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    request = prepare_manual_request(config.review)
    path, package = package_for(request, inbox)
    assert prepare_manual_request(config.review) == request  # process state is not needed
    assert import_for(config, inbox, request, path).status is VerticalReviewStatus.COMPLETED
    before = snapshot(config.review.coordinator.workflow.review_cases_root)
    assert import_for(config, inbox, request, path).status is VerticalReviewStatus.UNCHANGED
    assert snapshot(config.review.coordinator.workflow.review_cases_root) == before
    package["components"][0]["output"]["proposed_ideal_report"]["narrative"] = "changed"
    package["components"][0]["content_sha256"] = digest(package["components"][0]["output"])
    path.write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(ManualSupervisorError, match="incompatible_replay"):
        import_for(config, inbox, request, path)
    assert snapshot(config.review.coordinator.workflow.review_cases_root) == before


@pytest.mark.parametrize("mutation,state", [
    (lambda p: p["components"].pop(), "CAPTURE_INCOMPLETE"),
    (lambda p: p["components"][0].update(state="UNCERTAIN"), "CAPTURE_UNCERTAIN"),
    (lambda p: p["components"][0].update(reviewer="grok"), "REJECTED"),
    (lambda p: p.update(run_id="other-run"), "REJECTED"),
    (lambda p: p.update(attempt_id="other-attempt"), "REJECTED"),
])
def test_invalid_capture_never_publishes(tmp_path, mutation, state):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    request = prepare_manual_request(config.review)
    path, package = package_for(request, inbox)
    mutation(package)
    path.write_text(json.dumps(package), encoding="utf-8")
    assert manual_status(config.review, package_path=path, allowed_root=inbox)[
        "reviewers"][request.reviewer.value]["state"] == state
    with pytest.raises(ManualReviewImportError):
        import_for(config, inbox, request, path)
    assert manual_status(config.review)["reviewers"][request.reviewer.value]["state"] == "REJECTED"
    assert manual_status(config.review)["valid_reviewers"] == 0
    assert not list(tmp_path.rglob("active.json"))
    assert not list(tmp_path.rglob("adjudication.json"))


def test_cli_recovers_pending_request_across_processes(tmp_path):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    command = [sys.executable, "-m", "automation.review.manual_cli",
               "--root", str(tmp_path), "--inbox", str(inbox)]
    first = json.loads(subprocess.check_output([*command, "prepare"], text=True))
    second = json.loads(subprocess.check_output([*command, "prepare"], text=True))
    assert first == second
    pending = json.loads(subprocess.check_output([*command, "pending"], text=True))
    assert pending["missing_reviewers"] == 3
    assert pending["pending"]["chatgpt"]["request"]["task_id"] == first["request"]["task_id"]
    assert Path(first["template"]).is_file()
    request = prepare_manual_request(config.review)
    path, _ = package_for(request, inbox)
    published = json.loads(subprocess.check_output(
        [*command, "import", str(path), "--reviewer", request.reviewer.value], text=True))
    assert published["status"] == "COMPLETED"
    resumed = json.loads(subprocess.check_output([*command, "status"], text=True))
    assert resumed["valid_reviewers"] == 1
    replay = json.loads(subprocess.check_output(
        [*command, "import", str(path), "--reviewer", request.reviewer.value], text=True))
    assert replay["status"] == "UNCHANGED"
    next_request = prepare_manual_request(config.review)
    invalid_path, invalid = package_for(next_request, inbox)
    invalid["components"][0]["content_sha256"] = "0" * 64
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    rejected = subprocess.run(
        [*command, "import", str(invalid_path), "--reviewer", next_request.reviewer.value],
        capture_output=True, text=True, check=False)
    assert rejected.returncode == 2
    assert json.loads(rejected.stderr)["error"] == "content_hash_mismatch"
    after_restart = json.loads(subprocess.check_output([*command, "status"], text=True))
    assert after_restart["reviewers"]["gemini"]["state"] == "REJECTED"


def test_unconfirmed_attempt_requires_new_prepared_identity(tmp_path):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    first = prepare_manual_request(config.review)
    path, _ = package_for(first, inbox)

    def interrupt(stage):
        if stage == "before_activation":
            raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        import_manual_package(config.review, path, allowed_root=inbox,
                              reviewer=first.reviewer,
                              publication_failure_injector=interrupt)
    assert manual_status(config.review)["valid_reviewers"] == 0
    assert plan_next_statement(config.review.coordinator.workflow).action is PlanAction.RETRY_REVIEWER
    next_request = prepare_manual_request(config.review)
    assert next_request.task_id == first.task_id
    assert next_request.attempt_number == first.attempt_number + 1
    assert next_request.attempt_id != first.attempt_id
    assert prepare_manual_request(config.review) == next_request
    next_path, _ = package_for(next_request, inbox)
    assert import_for(config, inbox, next_request, next_path).status is VerticalReviewStatus.COMPLETED
    assert manual_status(config.review)["valid_reviewers"] == 1
    assert import_for(config, inbox, next_request, next_path).status is VerticalReviewStatus.UNCHANGED


def test_two_of_three_resume_third_without_republishing(tmp_path):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for _ in range(2):
        request = prepare_manual_request(config.review)
        path, _ = package_for(request, inbox)
        assert import_for(config, inbox, request, path).status is VerticalReviewStatus.COMPLETED
    root = config.review.coordinator.workflow.review_cases_root
    before = snapshot(root)
    assert manual_status(config.review)["valid_reviewers"] == 2
    assert plan_next_statement(config.review.coordinator.workflow).action is PlanAction.RUN_MISSING_REVIEWER
    request = prepare_manual_request(config.review)
    assert request.reviewer is Reviewer.GROK
    assert snapshot(root) != before  # only the durable request was added
    path, _ = package_for(request, inbox)
    assert import_for(config, inbox, request, path).status is VerticalReviewStatus.COMPLETED
    assert manual_status(config.review)["adjudication_enabled"]
    assert len(list(root.rglob("active.json"))) == 3
