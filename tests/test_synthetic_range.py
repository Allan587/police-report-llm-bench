"""OLBM-34: inclusive ranges through real inspectors and protected executors."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from automation.contracts import BenchmarkModel, Reviewer
from automation.orchestrator import PlanAction, plan_next_statement, resume_plan
from automation.review import (
    StatementCompletionStatus, inspect_statement_completeness, resolve_confirmed_review_set,
)
from automation.review.synthetic_adjudication import DeterministicFakeAdjudicatorAdapter
from automation.review.synthetic_range import (
    SyntheticRangeConfig, SyntheticRangeStatus, run_synthetic_range,
)
from automation.review.vertical_flow import (
    DeterministicFakeReviewerAdapter, VerticalReviewConfig, run_synthetic_reviewer_once,
)
from automation.workflow_coordinator import (
    CoordinationExpectation, CoordinationStatus, confirm_workflow_expectation_under_lease,
    coordinate_workflow_cycle,
)
from automation.workflow_lease import WorkflowLease
from tests.test_review_finalization import history, read_jsonl
from tests.test_synthetic_adjudication_flow import (
    RAW_MODELS, accumulated_profile, snapshot, write_json, write_jsonl,
)
from tests.test_synthetic_application import fixture


ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "tests/helpers/range_recovery_process.py"


def range_fixture(tmp_path, start=10, end=12):
    old = fixture(tmp_path)
    w = replace(old.review.coordinator.workflow, start_statement=start, end_statement=end)
    # Include outside inputs too; selection must be bounded even when they exist.
    indices = list(range(start, end + 2))
    write_jsonl(w.statements_path, [
        {"statement_index": i, "statement": {"report": f"synthetic {i}"}} for i in indices
    ])
    for raw in RAW_MODELS:
        write_jsonl(w.runs_root / "run-a" / raw / "generation/draft_reports.jsonl", [
            {"run_id": "run-a", "model_name": raw, "statement_index": i, "draft_report": f"synthetic {i}/{raw}"}
            for i in indices
        ])
    for model in BenchmarkModel:
        write_jsonl(w.feedback_root / f"{model.value}_history.jsonl", [history(model, i) for i in range(start)])
        write_json(w.profiles_root / f"{model.value}_profile.json", accumulated_profile(model, start - 1))
    return SyntheticRangeConfig(
        replace(old.review.coordinator, workflow=w), tmp_path, old.prompt_path,
        fixture_location_confirmed=True,
    )


def completion(config, index):
    w = config.coordinator.workflow
    return inspect_statement_completeness(
        statement_index=index, statements_path=w.statements_path, runs_root=w.runs_root,
        review_cases_root=w.review_cases_root, feedback_root=w.feedback_root,
        profiles_root=w.profiles_root, run=w.run,
        application_evidence_path=w.review_cases_root / f"statement_{index:03d}" / w.application_evidence_filename,
    )


class ReviewRecorder(DeterministicFakeReviewerAdapter):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.calls = []

    def execute(self, request):
        index = request.inputs.statement_index
        w = self.config.coordinator.workflow
        assert w.start_statement <= index <= w.end_statement
        for previous in range(w.start_statement, index):
            assert completion(self.config, previous).status is StatementCompletionStatus.COMPLETE
        self.calls.append((index, request.task.reviewer.value))
        return super().execute(request)


class AdjudicationRecorder(DeterministicFakeAdjudicatorAdapter):
    def __init__(self):
        super().__init__()
        self.calls = []

    def execute_bundle(self, request):
        assert request.adjudication.reviewer_artifact_count == 21
        assert set(request.adjudication.confirmed_sets) == set(Reviewer)
        self.calls.append(request.statement_index)
        return super().execute_bundle(request)


def run(config, **kwargs):
    reviews, adjudicator = ReviewRecorder(config), AdjudicationRecorder()
    result = run_synthetic_range(config, reviews, adjudicator, **kwargs)
    return result, reviews, adjudicator


def assert_accumulated(config, end):
    w = config.coordinator.workflow
    for model in BenchmarkModel:
        records = read_jsonl(w.feedback_root / f"{model.value}_history.jsonl")
        assert [r["output"] for r in records] == list(range(end + 1))
        profile = json.loads((w.profiles_root / f"{model.value}_profile.json").read_text())
        assert profile["profile_metadata"]["outputs_reviewed"] == end + 1
        assert profile["profile_metadata"]["version"] == end - w.start_statement + 2
    for index in range(w.start_statement, end + 1):
        assert completion(config, index).status is StatementCompletionStatus.COMPLETE
        statement = w.review_cases_root / f"statement_{index:03d}"
        evidence = json.loads((statement / w.application_evidence_filename).read_text())
        assert evidence["schema_version"] == 2
        assert len(list((statement / ".adjudication_applications").iterdir())) == 1
        for reviewer in Reviewer:
            confirmed, error = resolve_confirmed_review_set(
                statement_path=statement, statement_index=index, reviewer=reviewer,
            )
            assert confirmed is not None, error
            assert confirmed.attempt_metadata.run_id == "run-a"
        for path in statement.rglob("*"):
            if path.is_file():
                assert b"ADJUDICATION_NOTES" not in path.read_bytes()


@pytest.mark.parametrize("start,end", [(10, 12), (10, 15), (20, 21), (10, 10)])
def test_a_b_inclusive_sequential_and_empty_pending_range(tmp_path, start, end):
    config = range_fixture(tmp_path, start, end)
    w = config.coordinator.workflow
    historical = snapshot(w.review_cases_root / "statement_009")
    # An invalid application outside the range must never become the target.
    outside = w.review_cases_root / f"statement_{end + 1:03d}" / ".adjudication_applications/.staging-outside"
    outside.mkdir(parents=True)
    outside_before = snapshot(outside.parent.parent)
    result, reviews, adjudicator = run(config)
    assert result.status is SyntheticRangeStatus.COMPLETE, result.reason
    assert reviews.calls == [(i, r.value) for i in range(start, end + 1) for r in Reviewer]
    assert adjudicator.calls == list(range(start, end + 1))
    assert [s.statement_index for s in result.steps] == [i for i in range(start, end + 1) for _ in range(5)]
    assert_accumulated(config, end)
    assert snapshot(w.review_cases_root / "statement_009") == historical
    assert snapshot(outside.parent.parent) == outside_before
    before = snapshot(tmp_path / "operational")
    replay, reviews, adjudicator = run(config)
    assert replay.status is SyntheticRangeStatus.COMPLETE
    assert replay.steps == () and not reviews.calls and not adjudicator.calls
    assert snapshot(tmp_path / "operational") == before
    assert result.plan.statement_index is None
    assert set(result.plan.verifications) == set(range(start, end + 1))


@pytest.mark.parametrize("missing", ["statement", *RAW_MODELS])
def test_c_missing_input_stops_at_012_without_generating_or_advancing(tmp_path, missing):
    config = range_fixture(tmp_path, 10, 15)
    w = config.coordinator.workflow
    path = w.statements_path if missing == "statement" else w.runs_root / "run-a" / missing / "generation/draft_reports.jsonl"
    write_jsonl(path, [r for r in read_jsonl(path) if r["statement_index"] != 12])
    original_inputs = {str(p): p.read_bytes() for p in [w.statements_path, *w.runs_root.rglob("*.jsonl")]}
    result, reviews, adjudicator = run(config)
    assert result.status is SyntheticRangeStatus.BLOCKED
    assert result.plan.statement_index == 12
    assert path in result.plan.blocker.paths
    assert result.plan.missing_dependencies
    assert {i for i, _ in reviews.calls} == {10, 11}
    assert adjudicator.calls == [10, 11]
    assert_accumulated(config, 11)
    assert not (w.review_cases_root / "statement_012").exists()
    assert not any((w.review_cases_root / f"statement_{i:03d}").exists() for i in range(13, 16))
    assert all(p.read_bytes() == value for p, value in [(Path(k), v) for k, v in original_inputs.items()])


def test_d_only_pending_grok_runs_and_adjudication_waits_for_three(tmp_path):
    config = range_fixture(tmp_path, 10, 10)
    review = VerticalReviewConfig(config.coordinator, tmp_path, fixture_location_confirmed=True)
    for _ in range(2):
        assert run_synthetic_reviewer_once(review, DeterministicFakeReviewerAdapter()).status.value == "COMPLETED"
    statement = config.coordinator.workflow.review_cases_root / "statement_010"
    preserved = {r: snapshot(statement / ".review_sets" / r) for r in ("chatgpt", "gemini")}
    assert plan_next_statement(config.coordinator.workflow).reviewers == (Reviewer.GROK,)
    result, reviews, adjudicator = run(config)
    assert result.status is SyntheticRangeStatus.COMPLETE, result.reason
    assert reviews.calls == [(10, "grok")]
    assert adjudicator.calls == [10]
    assert all(snapshot(statement / ".review_sets" / r) == values for r, values in preserved.items())


def child(config, index=11, stage="resume"):
    w = config.coordinator.workflow
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(CHILD), str(config.fixture_root), str(w.start_statement), str(w.end_statement), str(index), stage],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=60,
    )


@pytest.mark.parametrize("stage", [
    "before_activation", "after_stage:RUN_MISSING_REVIEWER", "after_prepare",
    "adjudication_prepared",
    "after_replace:1:adjudication", "after_adjudication_commit",
    "after_replace:2:history:gemma", "after_replace:5:profile:gemma",
    "after_application_commit", "after_cleanup_complete", "after_evidence",
])
def test_e_real_process_restart_recovers_only_pending_work(tmp_path, stage):
    config = range_fixture(tmp_path)
    first = child(config, stage=stage)
    assert first.returncode == 63, (first.stdout, first.stderr)
    w = config.coordinator.workflow
    statement = w.review_cases_root / "statement_011"
    assert completion(config, 10).status is StatementCompletionStatus.COMPLETE
    assert not (w.review_cases_root / "statement_012").exists()
    plan = plan_next_statement(w)
    assert plan.statement_index == (12 if stage == "after_evidence" else 11)
    before = snapshot(statement)
    if stage == "adjudication_prepared":
        assert "adjudication.json" not in before
        assert any(name.endswith("/manifest.json") and name.startswith(".adjudication_applications/") for name in before)
    confirmed = []
    for reviewer in Reviewer:
        active, _ = resolve_confirmed_review_set(statement_path=statement, statement_index=11, reviewer=reviewer)
        if active:
            confirmed.append(reviewer.value)
    destinations = []
    for model in BenchmarkModel:
        for path in (w.feedback_root / f"{model.value}_history.jsonl", w.profiles_root / f"{model.value}_profile.json"):
            if (path.suffix == ".jsonl" and read_jsonl(path)[-1]["output"] == 11) or (
                path.suffix == ".json" and json.loads(path.read_text())["profile_metadata"]["outputs_reviewed"] == 12
            ):
                destinations.append((path, path.read_bytes(), path.stat().st_mtime_ns))
    # Stop before 012 in the second child to check that partial targets in 011
    # were skipped (bytes and mtime), before legitimate successor updates.
    second = child(config, index=12, stage="before_stage:RUN_MISSING_REVIEWER")
    assert second.returncode == 63, (second.stdout, second.stderr)
    recovered = json.loads(second.stdout)
    assert not any(i == 10 or (i == 11 and r in confirmed) for i, r in recovered["reviewers"])
    if "adjudication.json" in before:
        assert recovered["adjudications"] == []
    for reviewer in confirmed:
        prefix = f".review_sets/{reviewer}/"
        assert {k: v for k, v in snapshot(statement).items() if k.startswith(prefix)} == {
            k: v for k, v in before.items() if k.startswith(prefix)
        }
    assert completion(config, 11).status is StatementCompletionStatus.COMPLETE
    assert all(p.read_bytes() == data and p.stat().st_mtime_ns == mtime for p, data, mtime in destinations)
    after = snapshot(statement)
    for name, sha in before.items():
        if name == "adjudication.json" or any(name.startswith(f".review_sets/{r}/") for r in confirmed):
            assert after[name] == sha
    third = child(config)
    assert third.returncode == 0, (third.stdout, third.stderr)
    output = json.loads(third.stdout)
    assert output["status"] == "COMPLETE", output
    assert output["reviewers"] == [[12, r.value] for r in Reviewer]
    assert_accumulated(config, 12)
    fourth = child(config)
    assert json.loads(fourth.stdout)["reviewers"] == []
    assert json.loads(fourth.stdout)["adjudications"] == []


@pytest.mark.parametrize("damage", ["missing_store", "corrupt_manifest", "corrupt_evidence"])
def test_f_false_complete_blocks_without_fallback_or_effects(tmp_path, damage):
    config = range_fixture(tmp_path)
    assert child(config, index=10, stage="after_evidence").returncode == 63
    statement = config.coordinator.workflow.review_cases_root / "statement_010"
    storage = statement / ".adjudication_applications"
    if damage == "missing_store":
        shutil.rmtree(storage)
    elif damage == "corrupt_manifest":
        next(storage.glob("*/manifest.json")).write_text("{", encoding="utf-8")
    else:
        (statement / "application_evidence.json").write_text("{", encoding="utf-8")
    assert completion(config, 10).status is not StatementCompletionStatus.COMPLETE
    assert plan_next_statement(config.coordinator.workflow).statement_index == 10
    before = snapshot(tmp_path / "operational")
    result, reviews, adjudicator = run(config)
    assert result.status is SyntheticRangeStatus.BLOCKED, result.reason
    assert result.plan.statement_index == 10
    assert not reviews.calls and not adjudicator.calls
    assert snapshot(tmp_path / "operational") == before
    assert not (config.coordinator.workflow.review_cases_root / "statement_011").exists()


@pytest.mark.parametrize("bound", ["start_statement", "end_statement"])
def test_g_changed_bounds_invalidate_even_an_identical_next_action(tmp_path, bound):
    config = range_fixture(tmp_path, 10, 12)
    if bound == "start_statement":
        assert child(config, index=10, stage="after_evidence").returncode == 63
    coordinated = coordinate_workflow_cycle(config.coordinator)
    changed_w = replace(config.coordinator.workflow, **{bound: 11})
    changed = replace(config, coordinator=replace(config.coordinator, workflow=changed_w))
    current = coordinate_workflow_cycle(changed.coordinator)
    assert current.plan.statement_index == coordinated.plan.statement_index
    assert current.plan.action == coordinated.plan.action
    assert current.precondition_sha256 != coordinated.precondition_sha256
    for expectation in (coordinated.expectation(), CoordinationExpectation(coordinated.plan)):
        stale = coordinate_workflow_cycle(changed.coordinator, expected=expectation)
        assert stale.status is CoordinationStatus.STALE_PLAN
    with WorkflowLease(changed.coordinator.lease_path, "range-immediate") as lease:
        assert confirm_workflow_expectation_under_lease(
            changed.coordinator, lease, coordinated.expectation()
        ).status is CoordinationStatus.STALE_PLAN
    before = snapshot(tmp_path / "operational")
    result, reviews, adjudicator = run(changed, expected=coordinated.expectation())
    assert result.status is SyntheticRangeStatus.STALE_PLAN
    assert not reviews.calls and not adjudicator.calls
    assert snapshot(tmp_path / "operational") == before


@pytest.mark.parametrize("start,end", [(9, 12), (0, 9), (-1, 12), (13, 12), (True, 12), (10, False), (10.0, 12), (10, "12"), (None, 12), (10, None)])
def test_invalid_bounds_are_rejected_before_effects(tmp_path, start, end):
    config = range_fixture(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        replace(config.coordinator.workflow, start_statement=start, end_statement=end)
    assert snapshot(tmp_path) == before


def test_lower_incomplete_precedes_later_recovery_and_missing_directory(tmp_path):
    config = range_fixture(tmp_path)
    w = config.coordinator.workflow
    # Missing 010 directory is valid new work after input preflight; a later
    # unresolvable application cannot cause 010 or 011 to be skipped.
    (w.review_cases_root / "statement_012/.adjudication_applications/.staging-orphan").mkdir(parents=True)
    assert plan_next_statement(w).statement_index == 10
    result, reviews, adjudicator = run(config)
    assert result.status is SyntheticRangeStatus.BLOCKED
    assert result.plan.statement_index == 12
    assert {i for i, _ in reviews.calls} == {10, 11}
    assert adjudicator.calls == [10, 11]
    assert_accumulated(config, 11)
    with pytest.raises(ValueError, match="outside"):
        resume_plan(w, 13)


def test_range_requires_temporary_confirmed_confinement_and_explicit_bounds(tmp_path):
    config = range_fixture(tmp_path)
    with pytest.raises(ValueError, match="confirmation"):
        replace(config, fixture_location_confirmed=False)
    with pytest.raises(ValueError, match="explicit"):
        replace(config, coordinator=replace(config.coordinator, workflow=replace(
            config.coordinator.workflow, start_statement=None, end_statement=None
        )))
    with pytest.raises(ValueError, match="escapes"):
        replace(config, prompt_path=ROOT / "config/prompts/review_adjudication.md")
    with pytest.raises(ValueError, match="outside"):
        VerticalReviewConfig(config.coordinator, tmp_path, statement_index=13, fixture_location_confirmed=True)


def test_operational_files_unchanged_by_range_execution(tmp_path):
    roots = (ROOT / "data", ROOT / "config/feedback", ROOT / "config/profiles")
    before = {str(root): snapshot(root) for root in roots}
    result, _, _ = run(range_fixture(tmp_path, 10, 10))
    assert result.status is SyntheticRangeStatus.COMPLETE
    assert {str(root): snapshot(root) for root in roots} == before


@pytest.mark.parametrize("damage", ["manifest", "prepared", "destination", "ambiguous"])
def test_process_restart_blocks_corruption_without_writing_or_advancing(tmp_path, damage):
    config = range_fixture(tmp_path)
    assert child(config, stage="after_replace:2:history:gemma").returncode == 63
    w = config.coordinator.workflow
    storage = w.review_cases_root / "statement_011/.adjudication_applications"
    application = next(storage.iterdir())
    if damage == "manifest":
        (application / "manifest.json").write_text("{", encoding="utf-8")
    elif damage == "prepared":
        (application / "prepared/adjudication.json").write_text("{}", encoding="utf-8")
    elif damage == "destination":
        (w.feedback_root / "gemma_history.jsonl").write_text('{}\n', encoding="utf-8")
    else:
        (storage / ".staging-ambiguous").mkdir()
    before = snapshot(tmp_path / "operational")
    restarted = child(config)
    assert restarted.returncode == 0, restarted.stderr
    result = json.loads(restarted.stdout)
    assert result["status"] == "BLOCKED", result
    assert not result["reviewers"] and not result["adjudications"]
    assert snapshot(tmp_path / "operational") == before
    assert not (w.review_cases_root / "statement_012").exists()


def test_range_uses_existing_lease_and_resumes_after_holder_termination(tmp_path):
    config = range_fixture(tmp_path, 10, 10)
    holder = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/helpers/workflow_lease_holder.py"), str(config.coordinator.lease_path), "range-holder"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().startswith("acquired:")
        before = snapshot(tmp_path / "operational")
        other = child(config)
        assert other.returncode == 0, other.stderr
        result = json.loads(other.stdout)
        assert result["status"] == "CONTENDED", result
        assert not result["reviewers"] and not result["adjudications"]
        assert snapshot(tmp_path / "operational") == before
    finally:
        holder.terminate()
        holder.communicate(timeout=10)
    result = json.loads(child(config).stdout)
    assert result["status"] == "COMPLETE", result
