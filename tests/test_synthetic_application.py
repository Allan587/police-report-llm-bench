"""OLBM-33: confirmed, incremental, recoverable application on temporary fixtures."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from automation.contracts import BenchmarkModel, Reviewer
from automation.orchestrator import PlanAction, plan_next_statement
from automation.review import (
    ApplicationPhase, ReviewAttemptMetadata, StatementCompletionStatus,
    commit_adjudication_bundle, inspect_adjudication_application, publish_complete_review,
)
from automation.review.finalization import verify_application_evidence
from automation.review.synthetic_application import (
    SyntheticApplicationConfig, SyntheticApplicationStatus as Status,
    _completion, run_synthetic_application_once,
)
from automation.workflow_lease import WorkflowLease, WorkflowLeaseInUseError
from tests.test_review_finalization import adjudication, complete_review, history, read_jsonl
from tests.test_synthetic_adjudication_flow import RAW_MODELS, ready_fixture, snapshot, write_json, write_jsonl


ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "tests/helpers/application_recovery_process.py"


def fixture(tmp_path):
    config = ready_fixture(tmp_path)
    w = config.review.coordinator.workflow
    for index in range(10):
        historical = w.review_cases_root / f"statement_{index:03d}"
        historical.mkdir(exist_ok=True)
        (historical / "sentinel.json").write_text('{"historical":true}', encoding="utf-8")
    for model in BenchmarkModel:
        write_jsonl(w.feedback_root / f"{model.value}_history.jsonl", [history(model, i) for i in range(10)])
    return config


def prepare(config, index=10, *, bad_profile=False, cut=None):
    w = config.review.coordinator.workflow
    # Single-statement applications are explicit; this is fixture construction,
    # not an executor/driver for a configurable range.
    statements = read_jsonl(w.statements_path)
    if index not in [s["statement_index"] for s in statements]:
        write_jsonl(w.statements_path, [*statements, {"statement_index": index, "statement": {"report": f"synthetic {index}"}}])
        for raw in RAW_MODELS:
            path = w.runs_root / "run-a" / raw / "generation/draft_reports.jsonl"
            write_jsonl(path, [*read_jsonl(path), {"run_id": "run-a", "model_name": raw, "statement_index": index, "draft_report": f"synthetic {index}/{raw}"}])
    statement = w.review_cases_root / f"statement_{index:03d}"
    for reviewer in Reviewer:
        publish_complete_review(
            complete_review(reviewer, index), statement_path=statement,
            attempt_metadata=ReviewAttemptMetadata(1, "run-a", hashlib.sha256(f"{index}/{reviewer.value}".encode()).hexdigest()),
        )
    profiles = []
    for model in BenchmarkModel:
        profile = json.loads((w.profiles_root / f"{model.value}_profile.json").read_text())
        profile["profile_metadata"]["version"] += 1
        profile["profile_metadata"]["outputs_reviewed"] += 1
        for pattern in profile["strengths"]:
            pattern["evidence_count"] += 1
            pattern["last_observed_output"] = index
        profiles.append(profile)
    if bad_profile:
        profiles[0]["profile_metadata"]["outputs_reviewed"] = 999
        profiles[0]["strengths"][0].update(evidence_count=999, last_observed_output=999)
    args = dict(
        statement_index=index, statement_path=statement, feedback_root=w.feedback_root,
        profiles_root=w.profiles_root, adjudication=adjudication(index),
        histories=[history(model, index) for model in BenchmarkModel], profiles=profiles, run_id="run-a",
    )
    if bad_profile:
        before = snapshot(config.review.fixture_root / "operational")
        with pytest.raises(ValueError):
            commit_adjudication_bundle(**args)
        assert snapshot(config.review.fixture_root / "operational") == before
        return

    def interrupt(stage):
        if stage == cut:
            raise RuntimeError("controlled cut")

    if cut:
        with pytest.raises(RuntimeError, match="controlled cut"):
            commit_adjudication_bundle(**args, failure_injector=interrupt)
        app_id = next((statement / ".adjudication_applications").iterdir()).name
    else:
        app_id = commit_adjudication_bundle(**args).application.application_id
    return SyntheticApplicationConfig(config.review.coordinator, config.review.fixture_root, index, app_id, fixture_location_confirmed=True)


def inspect(config, *, successors=False):
    w = config.coordinator.workflow
    statement = w.review_cases_root / f"statement_{config.statement_index:03d}"
    return inspect_adjudication_application(
        application_path=statement / ".adjudication_applications" / config.application_id,
        statement_path=statement, feedback_root=w.feedback_root, profiles_root=w.profiles_root,
        application_evidence_path=statement / "application_evidence.json" if successors else None,
    )


def child(config, stage="resume"):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(CHILD), str(config.fixture_root), str(config.statement_index), config.application_id, stage],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
    )


def applied(config):
    result = run_synthetic_application_once(config)
    assert result.status is Status.APPLIED, result.reason
    assert result.completion.status is StatementCompletionStatus.COMPLETE
    return result


def test_qa33_01_v2_requires_stored_application_after_profile_tampering(tmp_path):
    config = prepare(fixture(tmp_path))
    applied(config)
    w = config.coordinator.workflow
    statement = w.review_cases_root / "statement_010"
    assert _completion(config).status is StatementCompletionStatus.COMPLETE
    assert plan_next_statement(w).statement_index == 11

    path = w.profiles_root / "gemma_profile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["profile_metadata"]["outputs_reviewed"] = 999
    profile["strengths"][0].update(evidence_count=999, last_observed_output=999)
    write_json(path, profile)
    assert _completion(config).status is StatementCompletionStatus.INCOMPLETE
    plan = plan_next_statement(w)
    assert plan.action is PlanAction.WAIT_OPERATOR
    assert plan.statement_index == 10

    shutil.rmtree(statement / ".adjudication_applications")
    completion = _completion(config)
    assert completion.status is StatementCompletionStatus.INCOMPLETE
    assert "application_storage_missing" in {issue.code for issue in completion.issues}
    plan = plan_next_statement(w)
    assert plan.action is PlanAction.WAIT_OPERATOR
    assert plan.statement_index == 10


@pytest.mark.parametrize("damage", ["empty_storage", "missing_manifest", "corrupt_manifest", "ambiguous_storage", "missing_evidence"])
def test_qa33_01_v2_missing_or_ambiguous_material_fails_closed(tmp_path, damage):
    config = prepare(fixture(tmp_path))
    applied(config)
    w = config.coordinator.workflow
    statement = w.review_cases_root / "statement_010"
    storage = statement / ".adjudication_applications"
    application = storage / config.application_id
    if damage == "empty_storage":
        shutil.rmtree(application)
    elif damage == "missing_manifest":
        (application / "manifest.json").unlink()
    elif damage == "corrupt_manifest":
        (application / "manifest.json").write_text("{", encoding="utf-8")
    elif damage == "ambiguous_storage":
        (storage / "another-application").mkdir()
    else:
        (statement / "application_evidence.json").unlink()
    completion = _completion(config)
    assert completion.status is StatementCompletionStatus.INCOMPLETE
    plan = plan_next_statement(w)
    assert plan.statement_index == 10
    assert plan.action is (PlanAction.FINALIZE_APPLICATION if damage == "missing_evidence" else PlanAction.WAIT_OPERATOR)


def test_qa33_01_committed_adjudication_without_application_remains_pending(tmp_path):
    config = prepare(fixture(tmp_path), cut="after_adjudication_commit")
    assert _completion(config).status is not StatementCompletionStatus.COMPLETE
    plan = plan_next_statement(config.coordinator.workflow)
    assert plan.statement_index == 10
    assert plan.action is not PlanAction.COMPLETE


def test_six_destinations_v2_and_exact_replay(tmp_path):
    config = prepare(fixture(tmp_path))
    w = config.coordinator.workflow
    before = snapshot(tmp_path / "operational")
    result = applied(config)
    after = snapshot(tmp_path / "operational")
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {
        *(f"feedback/{m.value}_history.jsonl" for m in BenchmarkModel),
        *(f"profiles/{m.value}_profile.json" for m in BenchmarkModel),
        f"review_cases/statement_010/.adjudication_applications/{config.application_id}/status.json",
    }
    assert all(not name.endswith("adjudication.json") for name in changed)
    application_dir = f"review_cases/statement_010/.adjudication_applications/{config.application_id}"
    assert set(after) - set(before) == {
        "review_cases/statement_010/application_evidence.json",
        f"{application_dir}/cleanup_manifest.json",
        f"{application_dir}/cleanup_status.json",
    }
    evidence_path = w.review_cases_root / "statement_010/application_evidence.json"
    evidence = json.loads(evidence_path.read_text())
    assert evidence["schema_version"] == 2
    assert evidence["history_application"]["adjudication_id"] == config.application_id
    assert evidence["profile_application"]["adjudication_id"] == config.application_id
    verify_application_evidence(application=result.application, statement_path=evidence_path.parent, evidence_path=evidence_path)
    for model in BenchmarkModel:
        assert [r["output"] for r in read_jsonl(w.feedback_root / f"{model.value}_history.jsonl")] == list(range(11))
        p = json.loads((w.profiles_root / f"{model.value}_profile.json").read_text())
        assert p["profile_metadata"]["outputs_reviewed"] == 11
        assert p["strengths"][0]["evidence_count"] == 2
    replay = run_synthetic_application_once(config)
    assert replay.status is Status.UNCHANGED, replay.reason
    assert snapshot(tmp_path / "operational") == after


@pytest.mark.parametrize("stage,phase,complete", [
    ("before_application", ApplicationPhase.ADJUDICATION_COMMITTED, False),
    ("after_replace:2:history:gemma", ApplicationPhase.INTERRUPTED, False),
    ("after_replace:4:history:llama", ApplicationPhase.INTERRUPTED, False),
    ("after_replace:5:profile:gemma", ApplicationPhase.INTERRUPTED, False),
    ("after_replace:6:profile:mistral", ApplicationPhase.INTERRUPTED, False),
    ("after_replace:7:profile:llama", ApplicationPhase.INTERRUPTED, False),
    ("after_application_commit", ApplicationPhase.APPLIED, False),
    ("after_recovery", ApplicationPhase.APPLIED, False),
    ("after_cleanup_complete", ApplicationPhase.APPLIED, False),
    ("after_evidence", ApplicationPhase.APPLIED, True),
])
def test_process_crashes_recover_only_pending_files(tmp_path, stage, phase, complete):
    config = prepare(fixture(tmp_path))
    before = snapshot(tmp_path / "operational")
    stopped = child(config, stage)
    assert stopped.returncode == 53, stopped.stderr + stopped.stdout
    app = inspect(config)
    assert app.phase is phase
    assert (_completion(config).status is StatementCompletionStatus.COMPLETE) is complete
    w = config.coordinator.workflow
    destinations = [*(w.feedback_root / f"{m.value}_history.jsonl" for m in BenchmarkModel), *(w.profiles_root / f"{m.value}_profile.json" for m in BenchmarkModel)]
    committed = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in destinations if snapshot(tmp_path / "operational")[p.relative_to(tmp_path / "operational").as_posix()] != before[p.relative_to(tmp_path / "operational").as_posix()]}
    restarted = child(config)
    assert restarted.returncode == 0, restarted.stderr
    result = json.loads(restarted.stdout)
    assert result["status"] == ("UNCHANGED" if complete else "APPLIED"), result
    for p, (content, mtime) in committed.items():
        assert p.read_bytes() == content
        assert p.stat().st_mtime_ns == mtime
    assert inspect(config).phase is ApplicationPhase.APPLIED
    assert _completion(config).status is StatementCompletionStatus.COMPLETE
    after = snapshot(tmp_path / "operational")
    assert json.loads(child(config).stdout)["status"] == "UNCHANGED"
    assert snapshot(tmp_path / "operational") == after


@pytest.mark.parametrize("missing", ["statement", *RAW_MODELS, *[r.value for r in Reviewer], "adjudication"])
def test_missing_precondition_rejects_before_application_effects(tmp_path, missing):
    config = prepare(fixture(tmp_path))
    w = config.coordinator.workflow
    statement = w.review_cases_root / "statement_010"
    if missing == "statement":
        w.statements_path.unlink()
    elif missing in RAW_MODELS:
        (w.runs_root / "run-a" / missing / "generation/draft_reports.jsonl").unlink()
    elif missing == "adjudication":
        (statement / "adjudication.json").unlink()
    else:
        (statement / ".review_sets" / missing / "active.json").unlink()
    before = snapshot(tmp_path / "operational")
    result = run_synthetic_application_once(config)
    assert result.status in {Status.BLOCKED, Status.VALIDATION_FAILED}, result
    assert snapshot(tmp_path / "operational") == before


def test_unconfirmed_preparation_is_not_applied(tmp_path):
    config = prepare(fixture(tmp_path), cut="after_prepare")
    assert inspect(config).phase is ApplicationPhase.PREPARED
    before = snapshot(tmp_path / "operational")
    assert run_synthetic_application_once(config).status is Status.VALIDATION_FAILED
    assert snapshot(tmp_path / "operational") == before


def test_adversarial_999_rejected_before_any_bundle_effect(tmp_path):
    prepare(fixture(tmp_path), bad_profile=True)


@pytest.mark.parametrize("target", ["preimage", "prepared", "source_hash", "run", "attempt", "evidence", "destination", "cleanup"])
def test_corruption_and_identity_contradictions_fail_closed(tmp_path, target):
    config = prepare(fixture(tmp_path))
    w = config.coordinator.workflow
    app = inspect(config)
    if target in {"evidence", "destination", "cleanup"}:
        applied(config)
    if target in {"preimage", "destination"}:
        path = w.profiles_root / "mistral_profile.json"
        value = json.loads(path.read_text())
        value["profile_metadata"]["version"] += 1
    elif target == "prepared":
        path = app.application_path / "prepared/profile/gemma_profile.json"
        value = json.loads(path.read_text())
        value["profile_metadata"]["outputs_reviewed"] = 999
    elif target in {"source_hash", "run", "attempt"}:
        path = app.manifest_path
        value = json.loads(path.read_text())
        if target == "source_hash":
            value["files"]["profile:gemma"]["source_sha256"] = "0" * 64
        elif target == "run":
            value["run_id"] = "foreign"
        else:
            value["review_attempts"]["grok"]["attempt_id"] = "foreign"
    elif target == "cleanup":
        path = app.application_path / "cleanup_status.json"
        value = {"phase": "PREPARED"}
    else:
        path = w.review_cases_root / "statement_010/application_evidence.json"
        value = json.loads(path.read_text())
        value["history_application"]["adjudication_id"] = "foreign"
    write_json(path, value)
    before = snapshot(tmp_path / "operational")
    result = run_synthetic_application_once(config)
    assert result.status in {Status.BLOCKED, Status.VALIDATION_FAILED}, result
    assert snapshot(tmp_path / "operational") == before
    if target in {"evidence", "destination", "cleanup"}:
        assert _completion(config).status is not StatementCompletionStatus.COMPLETE


def test_incremental_010_011_012_and_late_011_replay(tmp_path):
    source = fixture(tmp_path)
    configs = []
    for index in (10, 11, 12):
        config = prepare(source, index)
        configs.append(config)
        old = {c.statement_index: snapshot(c.coordinator.workflow.review_cases_root / f"statement_{c.statement_index:03d}") for c in configs[:-1]}
        applied(config)
        w = config.coordinator.workflow
        for c in configs:
            assert _completion(c).status is StatementCompletionStatus.COMPLETE
            assert inspect(c, successors=True).phase is ApplicationPhase.APPLIED
        for previous, previous_snapshot in old.items():
            assert snapshot(w.review_cases_root / f"statement_{previous:03d}") == previous_snapshot
        for model in BenchmarkModel:
            assert [r["output"] for r in read_jsonl(w.feedback_root / f"{model.value}_history.jsonl")] == list(range(index + 1))
            p = json.loads((w.profiles_root / f"{model.value}_profile.json").read_text())
            assert p["profile_metadata"]["outputs_reviewed"] == index + 1
            assert p["profile_metadata"]["version"] == index - 8
            assert p["strengths"][0]["evidence_count"] == index - 8
            assert p["strengths"][0]["last_observed_output"] == index
    before = snapshot(tmp_path / "operational")
    restarted = child(configs[1])
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["status"] == "UNCHANGED", restarted.stdout
    assert snapshot(tmp_path / "operational") == before
    assert plan_next_statement(w).statement_index == 13


def test_contradictory_replay_id_does_not_overwrite(tmp_path):
    config = prepare(fixture(tmp_path))
    applied(config)
    before = snapshot(tmp_path / "operational")
    result = run_synthetic_application_once(replace(config, application_id="0" * 64))
    assert result.status is Status.VALIDATION_FAILED
    assert snapshot(tmp_path / "operational") == before


@pytest.mark.parametrize("index", range(10))
def test_historical_statements_are_protected(tmp_path, index):
    source = fixture(tmp_path)
    before = snapshot(tmp_path / "operational")
    with pytest.raises(PermissionError):
        SyntheticApplicationConfig(source.review.coordinator, tmp_path, index, "0" * 64, fixture_location_confirmed=True)
    assert snapshot(tmp_path / "operational") == before


def test_confinement_operational_integrity_and_lease(tmp_path):
    roots = [ROOT / name for name in ("data", "config/profiles", "config/feedback")]
    before = [snapshot(root) for root in roots]
    config = prepare(fixture(tmp_path))
    with pytest.raises(ValueError, match="temporary directory"):
        replace(config, fixture_root=ROOT)
    with pytest.raises(ValueError, match="authorized root"):
        replace(config, coordinator=replace(config.coordinator, workflow=replace(config.coordinator.workflow, profiles_root=ROOT / "config/profiles")))

    def check_lease(stage):
        with pytest.raises(WorkflowLeaseInUseError):
            WorkflowLease(config.coordinator.lease_path, "contender").acquire(blocking=False)
    result = run_synthetic_application_once(config, failure_injector=check_lease)
    assert result.status is Status.APPLIED, result.reason
    assert [snapshot(root) for root in roots] == before


@pytest.mark.parametrize("target", ["history", "profile", "prepared", "evidence", "adjudication"])
def test_partial_process_recovery_blocks_corruption(tmp_path, target):
    config = prepare(fixture(tmp_path))
    assert child(config, "after_replace:5:profile:gemma").returncode == 53
    w = config.coordinator.workflow
    statement = w.review_cases_root / "statement_010"
    if target == "history":
        path = w.feedback_root / "gemma_history.jsonl"
        records = read_jsonl(path)
        records[0]["observations"] = ["corrupt historical prefix"]
        write_jsonl(path, records)
    elif target == "profile":
        path = w.profiles_root / "llama_profile.json"
        value = json.loads(path.read_text())
        value["profile_metadata"]["version"] = 999
        write_json(path, value)
    elif target == "prepared":
        app = inspect(config)
        path = app.application_path / "prepared/profile/llama_profile.json"
        path.write_text('{}', encoding="utf-8")
    elif target == "adjudication":
        (statement / "adjudication.json").unlink()
    else:
        write_json(statement / "application_evidence.json", {"schema_version": 2, "corrupt": True})
    before = snapshot(tmp_path / "operational")
    restarted = child(config)
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["status"] in {"BLOCKED", "VALIDATION_FAILED"}
    assert snapshot(tmp_path / "operational") == before
    assert _completion(config).status is not StatementCompletionStatus.COMPLETE


@pytest.mark.parametrize("target", ["profile", "history", "old_prepared", "old_evidence"])
def test_late_replay_blocks_corruption_instead_of_accepting_arbitrary_successor(tmp_path, target):
    source = fixture(tmp_path)
    first = prepare(source, 10)
    applied(first)
    second = prepare(source, 11)
    applied(second)
    w = first.coordinator.workflow
    if target == "profile":
        path = w.profiles_root / "gemma_profile.json"
        value = json.loads(path.read_text())
        value["strengths"][0]["evidence_count"] = 999
        write_json(path, value)
    elif target == "history":
        path = w.feedback_root / "gemma_history.jsonl"
        write_jsonl(path, [*read_jsonl(path), history(BenchmarkModel.GEMMA, 999)])
    elif target == "old_prepared":
        app = inspect(first, successors=True)
        (app.application_path / "prepared/adjudication.json").write_text('{}', encoding="utf-8")
    else:
        write_json(w.review_cases_root / "statement_010/application_evidence.json", {"schema_version": 2})
    before = snapshot(tmp_path / "operational")
    result = run_synthetic_application_once(first)
    assert result.status in {Status.BLOCKED, Status.VALIDATION_FAILED}, result
    assert snapshot(tmp_path / "operational") == before
    assert _completion(first).status is not StatementCompletionStatus.COMPLETE


def test_genuine_active_run_contradiction_is_rechecked(tmp_path):
    from tests.test_adjudication_run_identity import change_active_manifest

    config = prepare(fixture(tmp_path))
    w = config.coordinator.workflow
    change_active_manifest(
        {"statement_path": w.review_cases_root / "statement_010"}, Reviewer.GROK,
        lambda manifest, reference: manifest["execution"].update(run_id="foreign-run"),
    )
    before = snapshot(tmp_path / "operational")
    result = run_synthetic_application_once(config)
    assert result.status is Status.VALIDATION_FAILED, result
    assert "run_id contradicts" in result.reason
    assert snapshot(tmp_path / "operational") == before


def test_precondition_mutation_immediately_before_effect_is_rejected(tmp_path):
    config = prepare(fixture(tmp_path))
    w = config.coordinator.workflow
    before = snapshot(w.profiles_root)

    def change_input(stage):
        if stage == "before_application":
            records = read_jsonl(w.statements_path)
            records[0]["statement"]["report"] = "changed synthetic input"
            write_jsonl(w.statements_path, records)

    result = run_synthetic_application_once(config, failure_injector=change_input)
    assert result.status is Status.STALE_PLAN, result
    assert snapshot(w.profiles_root) == before
    assert inspect(config).phase is ApplicationPhase.ADJUDICATION_COMMITTED


def test_process_contention_and_release(tmp_path):
    import time

    config = prepare(fixture(tmp_path))
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    holder = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/helpers/adjudication_recovery_process.py"), str(tmp_path), "hold"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "holder_ready").exists():
            assert holder.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        before = snapshot(tmp_path / "operational")
        other = child(config)
        assert other.returncode == 0, other.stderr
        assert json.loads(other.stdout)["status"] == "CONTENDED"
        assert snapshot(tmp_path / "operational") == before
    finally:
        holder.terminate()
        holder.communicate(timeout=10)
    assert json.loads(child(config).stdout)["status"] == "APPLIED"


@pytest.mark.parametrize("index", [11, 12])
@pytest.mark.parametrize("stage", ["after_replace:2:history:gemma", "after_replace:5:profile:gemma", "after_replace:7:profile:llama", "after_application_commit"])
def test_accumulated_successor_process_recovery_preserves_complete_predecessors(tmp_path, index, stage):
    source = fixture(tmp_path)
    predecessors = []
    for previous in range(10, index):
        config = prepare(source, previous)
        applied(config)
        predecessors.append(config)
    current = prepare(source, index)
    stopped = child(current, stage)
    assert stopped.returncode == 53, stopped.stderr + stopped.stdout
    for previous in predecessors:
        assert inspect(previous, successors=True).phase is ApplicationPhase.APPLIED
        assert _completion(previous).status is StatementCompletionStatus.COMPLETE
    plan = plan_next_statement(current.coordinator.workflow)
    assert plan.statement_index == index
    assert plan.action in {PlanAction.RECOVER_APPLICATION, PlanAction.FINALIZE_APPLICATION}
    restarted = child(current)
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["status"] == "APPLIED", restarted.stdout
    for previous in [*predecessors, current]:
        assert _completion(previous).status is StatementCompletionStatus.COMPLETE


@pytest.mark.parametrize("kind", ["duplicate", "future"])
def test_invalid_accumulated_history_is_rejected_before_application(tmp_path, kind):
    source = fixture(tmp_path)
    w = source.review.coordinator.workflow
    path = w.feedback_root / "gemma_history.jsonl"
    write_jsonl(path, [*read_jsonl(path), history(BenchmarkModel.GEMMA, 0 if kind == "duplicate" else 999)])
    config = prepare(source)
    before = snapshot(tmp_path / "operational")
    result = run_synthetic_application_once(config)
    assert result.status is Status.VALIDATION_FAILED, result
    assert snapshot(tmp_path / "operational") == before


def test_legacy_model_less_history_preimage_is_preserved(tmp_path):
    source = fixture(tmp_path)
    w = source.review.coordinator.workflow
    for model in BenchmarkModel:
        path = w.feedback_root / f"{model.value}_history.jsonl"
        records = read_jsonl(path)
        for record in records:
            del record["model"]
        write_jsonl(path, records)
    config = prepare(source)
    applied(config)
    for model in BenchmarkModel:
        records = read_jsonl(w.feedback_root / f"{model.value}_history.jsonl")
        assert all("model" not in record for record in records[:-1])
        assert records[-1]["model"] == model.value
