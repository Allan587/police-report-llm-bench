from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from automation.contracts import (
    CompleteReviewResult, ExecutionAttempt, ReviewOperation, Reviewer,
    Task, TaskStatus, TaskType,
)
from automation.orchestrator import PlanAction, plan_next_statement
from automation.review.input_loader import inspect_review_inputs
from automation.review.manual_import_adapter import (
    MAX_PACKAGE_BYTES, ManualImportReviewerAdapter, ManualReviewImportError,
    manual_content_sha256,
)
from automation.review.result_inspector import ReviewerStatus, inspect_reviewer_results
from automation.review.review_storage import (
    ReviewAttemptMetadata, ReviewPublishStatus, publish_complete_review,
)
from automation.review.vertical_flow import (
    ReviewerAdapter, ReviewerExecutionRequest, VerticalReviewStatus,
    run_synthetic_reviewer_once,
)
from tests.helpers.manual_review_package import digest, synthetic_package
from tests.test_synthetic_review_flow import ready_fixture, snapshot


def request_for(config):
    workflow = config.coordinator.workflow
    task = Task.create(experiment_id=config.experiment_id, task_type=TaskType.EVALUATION,
                       case_id="statement_010", provider="synthetic", model="chatgpt",
                       prompt_version=config.prompt_version, statement_index=10,
                       reviewer=Reviewer.CHATGPT, review_operation=ReviewOperation.MAIN_REVIEW)
    task = replace(task, task_id="synthetic-task", status=TaskStatus.RUNNING)
    attempt = replace(ExecutionAttempt.start(task.task_id, 1), attempt_id="synthetic-attempt")
    return ReviewerExecutionRequest(plan_next_statement(workflow), inspect_review_inputs(
        statement_index=10, statements_path=workflow.statements_path,
        runs_root=workflow.runs_root), task, attempt)


def write_package(path, package):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package), encoding="utf-8")


class SyntheticOperator:
    """Test-only operator; actual importer never generates/rebinds identities."""
    def __init__(self, root, mutation=None):
        self.root, self.mutation = root, mutation
        self.request = self.result = None
        self.error = None

    def execute(self, request):
        self.request = request
        package = synthetic_package(task=request.task.task_id, attempt=request.attempt.attempt_id,
                                    reviewer=request.task.reviewer, run=request.inputs.selected_run_id)
        if self.mutation:
            self.mutation(package)
        path = self.root / "package.json"
        write_package(path, package)
        if self.mutation is malformed:
            path.write_text('{"private":"SENSITIVE_MARKER",')
        adapter: ReviewerAdapter = ManualImportReviewerAdapter(path, allowed_root=self.root)
        try:
            self.result = adapter.execute(request)
            return self.result
        except ManualReviewImportError as error:
            self.error = error
            raise


def missing_fourth(p): p["components"].pop(3)
def invalid_fourth(p):
    p["components"][3]["output"]["observations"] = "SENSITIVE_MARKER"
    p["components"][3]["content_sha256"] = digest(p["components"][3]["output"])
def wrong_statement(p): p["components"][3]["statement_index"] = 11
def wrong_attempt(p): p["components"][3]["attempt_id"] = "different-attempt"
def wrong_reviewer(p): p["components"][3]["reviewer"] = "gemini"
def wrong_run(p): p["components"][3]["run_id"] = "other-run"
def wrong_task(p): p["components"][3]["task_id"] = "other-task"
def wrong_hash(p): p["components"][3]["content_sha256"] = "0" * 64
def duplicate_slot(p): p["components"][3]["slot"] = p["components"][2]["slot"]
def malformed(p): pass
def uncertain(p): p["components"][3]["state"] = "UNCERTAIN"
def partial(p): p["components"][3]["state"] = "PARTIAL"
def output_identity(p):
    p["components"][3]["output"]["attempt_id"] = "different-attempt"
    p["components"][3]["content_sha256"] = digest(p["components"][3]["output"])
def file_reference(p):
    p["components"][3].pop("output")
    p["components"][3]["output_path"] = "nonexistent.json"


@pytest.mark.parametrize("mutation,code", [
    (missing_fourth, "seven_components_required"), (invalid_fourth, "invalid_review_content"),
    (wrong_statement, "component_identity_mismatch"), (wrong_attempt, "component_identity_mismatch"),
    (wrong_reviewer, "component_identity_mismatch"), (wrong_run, "component_identity_mismatch"),
    (wrong_task, "component_identity_mismatch"), (wrong_hash, "content_hash_mismatch"),
    (duplicate_slot, "duplicate_slot"), (malformed, "invalid_json"),
    (uncertain, "capture_not_complete"), (partial, "capture_not_complete"),
    (output_identity, "output_identity_mismatch"), (file_reference, "invalid_component_schema"),
], ids=lambda value: getattr(value, "__name__", value))
def test_adversarial_vertical_fails_before_any_publication(tmp_path, mutation, code):
    config = ready_fixture(tmp_path)
    before = snapshot(config.coordinator.workflow.review_cases_root)
    operator = SyntheticOperator(tmp_path / "inbox", mutation)
    outcome = run_synthetic_reviewer_once(config, operator)
    assert outcome.status is VerticalReviewStatus.ADAPTER_FAILED
    assert operator.result is None
    assert operator.error.code == code
    assert "SENSITIVE_MARKER" not in str(operator.error)
    assert outcome.publication is None
    assert snapshot(config.coordinator.workflow.review_cases_root) == before
    assert not inspect_reviewer_results(statement_index=10,
        review_cases_root=config.coordinator.workflow.review_cases_root).adjudication_enabled
    assert plan_next_statement(config.coordinator.workflow).action is PlanAction.RUN_MISSING_REVIEWER
    assert not list(tmp_path.rglob("manifest.json"))
    assert not list(tmp_path.rglob("adjudication.json"))


def test_three_manual_sets_publish_v2_and_only_then_open_barrier(tmp_path):
    config = ready_fixture(tmp_path)
    feedback = snapshot(config.coordinator.workflow.feedback_root)
    profiles = snapshot(config.coordinator.workflow.profiles_root)
    for index, reviewer in enumerate(Reviewer):
        operator = SyntheticOperator(tmp_path / "inbox")
        outcome = run_synthetic_reviewer_once(config, operator)
        assert outcome.status is VerticalReviewStatus.COMPLETED
        assert isinstance(operator.result, CompleteReviewResult)
        confirmed = outcome.publication.confirmed
        assert confirmed.reviewer is reviewer
        manifest = json.loads(confirmed.manifest_path.read_text())
        assert manifest["schema_version"] == 2
        assert manifest["execution"]["run_id"] == "run-a"
        assert manifest["execution"]["attempt_number"] == 1
        assert manifest["execution"]["task_deduplication_key"] == outcome.task.deduplication_key
        assert len(list(confirmed.set_path.rglob("*.json"))) == 8
        assert outcome.inspection.reviewers[reviewer].status is ReviewerStatus.COMPLETE
        assert outcome.inspection.adjudication_enabled is (index == 2)
        persisted = json.loads(confirmed.artifacts.main_review.read_text())
        metadata = persisted.pop("manual_import")
        assert metadata["provider_authenticity"] == "UNVERIFIED"
        assert metadata["integrity"] == "local_hash_validated"
        assert metadata["content_sha256"] == digest(persisted)
        assert "Synthetic fixture" in metadata["declared_provenance"]
        assert metadata["observed_at"] == "2026-11-06T10:00:00-06:00"
    assert outcome.after_plan.action is PlanAction.RUN_ADJUDICATIONS
    assert not list(tmp_path.rglob("adjudication.json"))
    assert snapshot(config.coordinator.workflow.feedback_root) == feedback
    assert snapshot(config.coordinator.workflow.profiles_root) == profiles


def test_checked_in_fixture_import_is_read_only_and_hash_matches(tmp_path):
    config = ready_fixture(tmp_path)
    fixture = Path(__file__).parent / "fixtures/manual_review/complete.json"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "package.json").write_bytes(fixture.read_bytes())
    before = snapshot(tmp_path)
    request = request_for(config)
    result = ManualImportReviewerAdapter(Path("package.json"), allowed_root=inbox).execute(request)
    assert isinstance(result, CompleteReviewResult)
    assert manual_content_sha256({"ñ": "á"}) == digest({"ñ": "á"})
    assert result.as_task_result().succeeded
    assert snapshot(tmp_path) == before


def test_identical_replay_and_incompatible_duplicate_use_existing_writer(tmp_path):
    config = ready_fixture(tmp_path)
    request = request_for(config)
    inbox = tmp_path / "inbox"
    path = inbox / "package.json"
    package = synthetic_package()
    write_package(path, package)
    adapter = ManualImportReviewerAdapter(path, allowed_root=inbox)
    metadata = ReviewAttemptMetadata(1, "run-a", request.task.deduplication_key)
    destination = config.coordinator.workflow.review_cases_root / "statement_010"
    def publish():
        return publish_complete_review(adapter.execute(request), statement_path=destination,
                                       attempt_metadata=metadata)
    assert publish().status is ReviewPublishStatus.CREATED
    before = snapshot(destination)
    assert publish().status is ReviewPublishStatus.UNCHANGED
    assert snapshot(destination) == before
    package["components"][3]["output"]["observations"] = ["changed synthetic text"]
    package["components"][3]["content_sha256"] = digest(package["components"][3]["output"])
    write_package(path, package)
    with pytest.raises(ValueError, match="same task_id/attempt_id"):
        publish()
    assert snapshot(destination) == before
    inspected = inspect_reviewer_results(statement_index=10,
        review_cases_root=config.coordinator.workflow.review_cases_root)
    assert inspected.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.COMPLETE
    assert not inspected.adjudication_enabled
    assert not list(destination.rglob("adjudication.json"))


@pytest.mark.parametrize("path_kind", ["outside", "traversal", "symlink", "symlink_parent",
                                        "missing", "directory", "fifo"])
def test_path_attacks_block_vertical_publication(tmp_path, path_kind):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("SENSITIVE_MARKER")
    if path_kind == "outside": path = outside
    elif path_kind == "traversal": path = Path("../outside.json")
    elif path_kind == "symlink":
        path = inbox / "link.json"
        path.symlink_to(outside)
    elif path_kind == "symlink_parent":
        (inbox / "link").symlink_to(tmp_path, target_is_directory=True)
        path = inbox / "link/outside.json"
    elif path_kind == "directory": path = inbox
    elif path_kind == "fifo":
        import os
        path = inbox / "pipe"
        os.mkfifo(path)
    else: path = inbox / "missing.json"
    adapter = ManualImportReviewerAdapter(path, allowed_root=inbox)
    with pytest.raises(ManualReviewImportError) as raised:
        adapter.execute(request_for(config))
    assert "SENSITIVE_MARKER" not in str(raised.value)
    outcome = run_synthetic_reviewer_once(config, adapter)
    assert outcome.status is VerticalReviewStatus.ADAPTER_FAILED
    assert outcome.publication is None
    assert not list(tmp_path.rglob("manifest.json"))
    assert not inspect_reviewer_results(statement_index=10,
        review_cases_root=config.coordinator.workflow.review_cases_root).adjudication_enabled


@pytest.mark.parametrize("kind", ["duplicate_key", "nan", "infinity", "overflow", "surrogate",
                                   "depth", "oversize", "invalid_utf8", "list"])
def test_hostile_json_is_sanitized_and_never_published(tmp_path, kind):
    config = ready_fixture(tmp_path)
    path = tmp_path / "package.json"
    texts = {"duplicate_key": '{"private":"SENSITIVE_MARKER","private":0}',
             "nan": '{"x":NaN}', "infinity": '{"x":Infinity}',
             "overflow": '{"x":1e999}', "surrogate": '{"x":"\\ud800"}',
             "depth": '[' * 1100 + '0' + ']' * 1100,
             "oversize": ' ' * (MAX_PACKAGE_BYTES + 1), "list": '[]'}
    path.write_bytes(b'\xff' if kind == "invalid_utf8" else texts[kind].encode())
    adapter = ManualImportReviewerAdapter(path, allowed_root=tmp_path)
    with pytest.raises(ManualReviewImportError) as error:
        adapter.execute(request_for(config))
    assert "SENSITIVE_MARKER" not in str(error.value)
    assert run_synthetic_reviewer_once(config, adapter).publication is None
    assert not list(tmp_path.rglob("manifest.json"))


@pytest.mark.parametrize("field,value", [("statement_index", True), ("statement_index", 10.0),
                                        ("run_id", "other-run"), ("task_id", "other-task"),
                                        ("attempt_id", "old-attempt"), ("reviewer", "grok"),
                                        ("schema_version", True), ("schema_version", 2)])
def test_package_identity_and_version_are_exact(tmp_path, field, value):
    config = ready_fixture(tmp_path)
    package = synthetic_package()
    package[field] = value
    path = tmp_path / "package.json"
    write_package(path, package)
    with pytest.raises(ManualReviewImportError):
        ManualImportReviewerAdapter(path, allowed_root=tmp_path).execute(request_for(config))


@pytest.mark.parametrize("field,value", [("observed_at", "yesterday"),
    ("observed_at", "2026-11-06T10:00:00"), ("declared_provenance", ""),
    ("declared_provenance", "x" * 2049), ("content_sha256", "xyz"),
    ("state", "UNVERIFIED"), ("slot", []), ("statement_index", 10.0)])
def test_component_metadata_must_be_explicit_and_valid(tmp_path, field, value):
    config = ready_fixture(tmp_path)
    package = synthetic_package()
    package["components"][3][field] = value
    path = tmp_path / "package.json"
    write_package(path, package)
    with pytest.raises(ManualReviewImportError):
        ManualImportReviewerAdapter(path, allowed_root=tmp_path).execute(request_for(config))


def test_new_attempt_cannot_silently_rebind_old_package(tmp_path):
    config = ready_fixture(tmp_path)
    request = request_for(config)
    path = tmp_path / "package.json"
    write_package(path, synthetic_package())
    changed = replace(request, attempt=replace(request.attempt, attempt_id="new-attempt"))
    with pytest.raises(ManualReviewImportError, match="package_identity_mismatch"):
        ManualImportReviewerAdapter(path, allowed_root=tmp_path).execute(changed)


@pytest.mark.parametrize("kind", ["overflow", "surrogate", "reserved", "wrong_output_statement",
                                   "wrong_model", "depth65", "unknown_field"])
def test_hostile_values_inside_valid_envelope_fail_closed(tmp_path, kind):
    config = ready_fixture(tmp_path)
    package = synthetic_package()
    component = package["components"][3]
    if kind == "overflow": component["output"]["extra"] = float("inf")
    elif kind == "surrogate": component["output"]["extra"] = "\ud800"
    elif kind == "reserved": component["output"]["manual_import"] = {"provider_authenticity": "VERIFIED"}
    elif kind == "wrong_output_statement": component["output"]["statement_index"] = 10.0
    elif kind == "wrong_model": component["output"]["model"] = "gemma"
    elif kind == "unknown_field": component["output_path"] = "../../private.json"
    else:
        extra = []
        component["output"]["extra"] = extra
        for _ in range(65):
            child = []
            extra.append(child)
            extra = child
    if kind not in {"overflow", "surrogate"}:
        component["content_sha256"] = digest(component["output"])
    path = tmp_path / "package.json"
    write_package(path, package)
    adapter = ManualImportReviewerAdapter(path, allowed_root=tmp_path)
    with pytest.raises(ManualReviewImportError):
        adapter.execute(request_for(config))
    assert run_synthetic_reviewer_once(config, adapter).status is VerticalReviewStatus.ADAPTER_FAILED
    assert not list(tmp_path.rglob("manifest.json"))


def test_failed_second_reviewer_preserves_valid_first_set(tmp_path):
    config = ready_fixture(tmp_path)
    first = run_synthetic_reviewer_once(config, SyntheticOperator(tmp_path / "inbox"))
    before = snapshot(first.publication.confirmed.set_path)
    second = run_synthetic_reviewer_once(config, SyntheticOperator(tmp_path / "inbox", invalid_fourth))
    assert second.status is VerticalReviewStatus.ADAPTER_FAILED
    assert snapshot(first.publication.confirmed.set_path) == before
    inspected = inspect_reviewer_results(statement_index=10,
        review_cases_root=config.coordinator.workflow.review_cases_root)
    assert inspected.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.COMPLETE
    assert inspected.reviewers[Reviewer.GEMINI].status is ReviewerStatus.NOT_STARTED
    assert not inspected.adjudication_enabled


def test_changing_declared_provenance_is_incompatible_duplicate(tmp_path):
    config = ready_fixture(tmp_path)
    request = request_for(config)
    path = tmp_path / "package.json"
    package = synthetic_package()
    write_package(path, package)
    adapter = ManualImportReviewerAdapter(path, allowed_root=tmp_path)
    metadata = ReviewAttemptMetadata(1, "run-a", request.task.deduplication_key)
    destination = config.coordinator.workflow.review_cases_root / "statement_010"
    publish_complete_review(adapter.execute(request), statement_path=destination, attempt_metadata=metadata)
    before = snapshot(destination)
    package["components"][3]["declared_provenance"] = "Different operator declaration"
    write_package(path, package)
    with pytest.raises(ValueError, match="same task_id/attempt_id"):
        publish_complete_review(adapter.execute(request), statement_path=destination, attempt_metadata=metadata)
    assert snapshot(destination) == before
