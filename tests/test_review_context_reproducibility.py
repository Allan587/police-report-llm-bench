"""F3-19: inspect the actual local reviewer request across incremental application."""

import hashlib
import json

import pytest

from automation.workflow_coordinator import coordinate_workflow_cycle
from automation.contracts import BenchmarkModel, Reviewer
from automation.review import resolve_confirmed_review_set
from automation.review.input_loader import inspect_review_inputs
from automation.review.synthetic_adjudication import DeterministicFakeAdjudicatorAdapter
from automation.review.synthetic_range import SyntheticRangeStatus, run_synthetic_range
from automation.review.vertical_flow import DeterministicFakeReviewerAdapter
from tests.test_synthetic_range import range_fixture


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def request_bytes(*, index, run_id, reviewer, task_id, attempt_id, attempt_number, statement, outputs):
    # This is a documented canonical projection of ReviewerExecutionRequest,
    # not a claimed transport prompt or a historic provider payload.
    return canonical({
        "schema": "f3-19-request-projection-v1",
        "statement_index": index, "run_id": run_id, "reviewer": reviewer,
        "task_id": task_id, "attempt_id": attempt_id,
        "attempt_number": attempt_number, "statement": dict(statement),
        "outputs": {model.value: dict(outputs[model]) for model in BenchmarkModel},
    })


class CapturingReviewer(DeterministicFakeReviewerAdapter):
    def __init__(self):
        super().__init__()
        self.captures = {}

    def execute(self, request):
        assert request.inputs.selected_run_id == request.plan.run_id
        raw = request_bytes(
            index=request.inputs.statement_index, run_id=request.inputs.selected_run_id,
            reviewer=request.task.reviewer.value, task_id=request.task.task_id,
            attempt_id=request.attempt.attempt_id,
            attempt_number=request.attempt.attempt_number,
            statement=request.inputs.statement, outputs=request.inputs.outputs,
        )
        self.captures[request.task.reviewer] = (raw, hashlib.sha256(raw).hexdigest())
        return super().execute(request)


def test_request_projection_reconstructs_after_full_synthetic_application(tmp_path):
    config = range_fixture(tmp_path, 10, 10)
    w = config.coordinator.workflow
    preimages = {
        key: path.read_bytes()
        for model in BenchmarkModel
        for key, path in (
            (f"history:{model.value}", w.feedback_root / f"{model.value}_history.jsonl"),
            (f"profile:{model.value}", w.profiles_root / f"{model.value}_profile.json"),
        )
    }
    reviewer = CapturingReviewer()
    result = run_synthetic_range(config, reviewer, DeterministicFakeAdjudicatorAdapter())
    assert result.status is SyntheticRangeStatus.COMPLETE, result.reason
    assert len(reviewer.captures) == 3
    assert len(result.steps) == 5  # three reviewers, adjudication, application
    statement_path = w.review_cases_root / "statement_010"
    manifest = json.loads(next(statement_path.glob(".adjudication_applications/*/manifest.json")).read_text())
    inputs = inspect_review_inputs(
        statement_index=10, statements_path=w.statements_path,
        runs_root=w.runs_root, run=w.run,
    )
    assert inputs.inputs_complete and inputs.selected_run_id == manifest["run_id"] == "run-a"
    for identity in Reviewer:
        confirmed, error = resolve_confirmed_review_set(
            statement_path=statement_path, statement_index=10, reviewer=identity)
        assert confirmed is not None, error
        metadata = confirmed.attempt_metadata
        assert metadata is not None and metadata.run_id == "run-a"
        rebuilt = request_bytes(
            index=10, run_id=metadata.run_id, reviewer=identity.value,
            task_id=confirmed.task_id, attempt_id=confirmed.attempt_id,
            attempt_number=metadata.attempt_number,
            statement=inputs.statement, outputs=inputs.outputs,
        )
        captured, digest = reviewer.captures[identity]
        assert rebuilt == captured
        assert hashlib.sha256(rebuilt).hexdigest() == digest
        assert manifest["review_attempts"][identity.value] == {
            "task_id": confirmed.task_id, "attempt_id": confirmed.attempt_id,
        }
    for key, previous in preimages.items():
        assert manifest["files"][key]["source_sha256"] == hashlib.sha256(previous).hexdigest()
        target = next(statement_path.glob(".adjudication_applications/*")) / manifest["files"][key]["prepared_path"]
        assert target.read_bytes() != previous
        assert hashlib.sha256(target.read_bytes()).hexdigest() == manifest["files"][key]["target_sha256"]
    # Application storage keeps targets and source hashes, not profile source bytes.
    application_dir = next(statement_path.glob(".adjudication_applications/*"))
    persisted_hashes = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in application_dir.rglob("*") if path.is_file()
    }
    for model in BenchmarkModel:
        assert manifest["files"][f"profile:{model.value}"]["source_sha256"] not in persisted_hashes
    assert all(b"FUTURE_LEAK_MARKER" not in raw for raw, _ in reviewer.captures.values())
    replay = run_synthetic_range(config, CapturingReviewer(), DeterministicFakeAdjudicatorAdapter())
    assert replay.status is SyntheticRangeStatus.COMPLETE and replay.steps == ()


def test_profile_future_marker_is_absent_from_reviewer_request(tmp_path):
    config = range_fixture(tmp_path, 10, 10)
    w = config.coordinator.workflow
    profile_path = w.profiles_root / "gemma_profile.json"
    profile = json.loads(profile_path.read_text())
    profile["fine_tuning_priorities"] = ["FUTURE_LEAK_MARKER"]
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    reviewer = CapturingReviewer()
    from automation.review.vertical_flow import VerticalReviewConfig, run_synthetic_reviewer_once
    review_config = VerticalReviewConfig(config.coordinator, tmp_path, fixture_location_confirmed=True)
    outcome = run_synthetic_reviewer_once(review_config, reviewer)
    assert outcome.status.value == "COMPLETED", outcome.reason
    assert len(reviewer.captures) == 1
    assert all(b"FUTURE_LEAK_MARKER" not in raw for raw, _ in reviewer.captures.values())


@pytest.mark.parametrize("kind", ["profile", "history"])
def test_changed_accumulated_file_invalidates_prepared_plan(tmp_path, kind):
    config = range_fixture(tmp_path, 10, 10)
    w = config.coordinator.workflow
    prepared = coordinate_workflow_cycle(config.coordinator)
    assert prepared.expectation().precondition_sha256
    path = (w.profiles_root / "gemma_profile.json" if kind == "profile"
            else w.feedback_root / "gemma_history.jsonl")
    path.write_bytes(path.read_bytes() + b" ")
    from automation.review.vertical_flow import VerticalReviewConfig, run_synthetic_reviewer_once
    review_config = VerticalReviewConfig(config.coordinator, tmp_path, fixture_location_confirmed=True)
    adapter = CapturingReviewer()
    result = run_synthetic_reviewer_once(review_config, adapter, expected=prepared.expectation())
    assert result.status.value == "STALE_PLAN"
    assert adapter.captures == {}


@pytest.mark.parametrize("kind", ["profile", "history"])
def test_file_update_after_manual_task_preparation_does_not_enter_request(tmp_path, kind):
    from automation.review.input_loader import inspect_review_inputs
    from automation.review.manual_supervisor import _execution, prepare_manual_request
    from automation.review.vertical_flow import ReviewerExecutionRequest, VerticalReviewConfig

    config = range_fixture(tmp_path, 10, 10)
    w = config.coordinator.workflow
    review_config = VerticalReviewConfig(config.coordinator, tmp_path, fixture_location_confirmed=True)
    prepared = prepare_manual_request(review_config)
    task, attempt = _execution(review_config, prepared)
    plan = coordinate_workflow_cycle(config.coordinator).plan
    inputs = inspect_review_inputs(
        statement_index=10, statements_path=w.statements_path,
        runs_root=w.runs_root, run=w.run,
    )
    request = ReviewerExecutionRequest(plan, inputs, task, attempt)
    path = (w.profiles_root / "gemma_profile.json" if kind == "profile"
            else w.feedback_root / "gemma_history.jsonl")
    path.write_bytes(path.read_bytes() + b"FUTURE_LEAK_MARKER")
    reviewer = CapturingReviewer()
    reviewer.execute(request)
    assert reviewer.captures[prepared.reviewer][0].find(b"FUTURE_LEAK_MARKER") == -1
