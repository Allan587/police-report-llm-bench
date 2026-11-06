"""Operator-facing adjudication and application through the existing CLI."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from automation.contracts import Reviewer
from automation.orchestrator import plan_next_statement
from automation.review.synthetic_adjudication import (
    DeterministicFakeAdjudicatorAdapter, _build_bundle_request, _build_request,
)
from tests.helpers.manual_review_package import synthetic_package
from tests.test_synthetic_adjudication_flow import ready_fixture, snapshot


def call(root: Path, inbox: Path, *args):
    return subprocess.run([sys.executable, "-m", "automation.review.manual_cli",
                           "--root", str(root), "--inbox", str(inbox), *map(str, args)],
                          text=True, capture_output=True, check=False)


def output(process):
    return json.loads(process.stdout or process.stderr)


def setup(tmp_path):
    config = ready_fixture(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return config, inbox


def publish_one(root, inbox):
    prepared = call(root, inbox, "prepare")
    assert prepared.returncode == 0, prepared.stderr
    request = output(prepared)["request"]
    package = synthetic_package(task=request["task_id"], attempt=request["attempt_id"],
                                reviewer=Reviewer(request["reviewer"]), run=request["run_id"])
    path = inbox / f'{request["reviewer"]}.json'
    path.write_text(json.dumps(package), encoding="utf-8")
    imported = call(root, inbox, "import", path, "--reviewer", request["reviewer"])
    assert imported.returncode == 0, imported.stderr
    return request


def bundle_package(config, inbox, requests):
    plan = plan_next_statement(config.review.coordinator.workflow)
    bundle_request = _build_bundle_request(config, _build_request(config, plan))
    bundle = DeterministicFakeAdjudicatorAdapter().execute_bundle(bundle_request)
    bundle.pop("ADJUDICATION_NOTES")
    package = {"schema_version": 1, "statement_index": 10,
               "run_id": requests[0]["run_id"], "review_attempts": {
                   request["reviewer"]: {"task_id": request["task_id"],
                                         "attempt_id": request["attempt_id"]}
                   for request in requests}, "bundle": bundle}
    path = inbox / "adjudication.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    return path, package


def test_cli_full_flow_replay_and_contradiction(tmp_path):
    config, inbox = setup(tmp_path)
    w = config.review.coordinator.workflow
    before_feedback, before_profiles = snapshot(w.feedback_root), snapshot(w.profiles_root)
    requests = []
    for count in range(1, 4):
        requests.append(publish_one(tmp_path, inbox))
        status = output(call(tmp_path, inbox, "status"))
        assert status["valid_reviewers"] == count
        assert status["adjudication"]["ready"] is (count == 3)
        if count < 3:
            denied = call(tmp_path, inbox, "adjudicate", inbox / "missing.json",
                          "--statement-index", 10, "--run-id", "run-a")
            assert denied.returncode == 2
            assert output(denied)["error"] == "run_missing_reviewer"
    assert snapshot(w.feedback_root) == before_feedback
    assert snapshot(w.profiles_root) == before_profiles
    path, package = bundle_package(config, inbox, requests)
    first = call(tmp_path, inbox, "adjudicate", path,
                 "--statement-index", 10, "--run-id", "run-a")
    assert first.returncode == 0, first.stderr or first.stdout
    result = output(first)
    assert result["status"] == "APPLIED"
    assert result["adjudication"]["bundle"] == "COMMITTED"
    assert result["adjudication"]["statement_completion"] == "COMPLETE"
    assert set(result["adjudication"]["incremental"].values()) == {"APPLIED"}
    assert len(result["adjudication"]["incremental"]) == 6
    after = (snapshot(w.review_cases_root), snapshot(w.feedback_root), snapshot(w.profiles_root))
    replay = call(tmp_path, inbox, "adjudicate", path,
                  "--statement-index", 10, "--run-id", "run-a")
    assert replay.returncode == 0, replay.stderr or replay.stdout
    assert output(replay)["status"] == "UNCHANGED"
    assert after == (snapshot(w.review_cases_root), snapshot(w.feedback_root), snapshot(w.profiles_root))
    package["bundle"]["adjudication"]["decision"]["reason"] = "contradiction"
    path.write_text(json.dumps(package), encoding="utf-8")
    conflict = call(tmp_path, inbox, "adjudicate", path,
                    "--statement-index", 10, "--run-id", "run-a")
    assert conflict.returncode == 2
    assert output(conflict)["error"] == "contradictory_adjudication_bundle"
    assert after == (snapshot(w.review_cases_root), snapshot(w.feedback_root), snapshot(w.profiles_root))


def test_cli_requires_complete_bundle_and_exact_identity(tmp_path):
    config, inbox = setup(tmp_path)
    requests = [publish_one(tmp_path, inbox) for _ in range(3)]
    path, package = bundle_package(config, inbox, requests)
    package["run_id"] = "other-run"
    path.write_text(json.dumps(package), encoding="utf-8")
    wrong = call(tmp_path, inbox, "adjudicate", path,
                 "--statement-index", 10, "--run-id", "run-a")
    assert wrong.returncode == 2
    assert output(wrong)["error"] == "adjudication_identity_mismatch"
    package["run_id"] = "run-a"
    package["bundle"]["histories"].pop()
    path.write_text(json.dumps(package), encoding="utf-8")
    missing = call(tmp_path, inbox, "adjudicate", path,
                   "--statement-index", 10, "--run-id", "run-a")
    assert missing.returncode == 2
    assert output(missing)["error"] == "three_histories_required"
    assert not list((config.review.coordinator.workflow.review_cases_root /
                     "statement_010").glob("adjudication.json"))


def test_cli_resumes_interrupted_incremental_application(tmp_path):
    from automation.review.manual_adjudication import OperatorBundleAdapter
    from automation.review.synthetic_adjudication import (
        SyntheticAdjudicationStatus, run_synthetic_adjudication_and_persist_once,
    )
    config, inbox = setup(tmp_path)
    requests = [publish_one(tmp_path, inbox) for _ in range(3)]
    path, package = bundle_package(config, inbox, requests)
    identity = {key: package[key] for key in (
        "schema_version", "statement_index", "run_id", "review_attempts")}
    committed = run_synthetic_adjudication_and_persist_once(
        config, OperatorBundleAdapter(path, inbox, identity))
    assert committed.status is SyntheticAdjudicationStatus.PERSISTED
    application_id = committed.application.application_id
    status = output(call(tmp_path, inbox, "status"))["adjudication"]
    assert status["bundle"] == "COMMITTED"
    assert set(status["incremental"].values()) == {"PENDING"}
    crashed = subprocess.run([
        sys.executable, "-m", "tests.helpers.application_recovery_process",
        str(tmp_path), "10", application_id, "after_replace:5:profile:gemma",
    ], capture_output=True, text=True, check=False)
    assert crashed.returncode == 53
    intermediate = output(call(tmp_path, inbox, "status"))["adjudication"]
    assert intermediate["application_phase"] == "INTERRUPTED"
    assert set(intermediate["incremental"].values()) == {"PENDING", "APPLIED"}
    resumed = call(tmp_path, inbox, "adjudicate", path,
                   "--statement-index", 10, "--run-id", "run-a")
    assert resumed.returncode == 0, resumed.stderr or resumed.stdout
    assert output(resumed)["status"] == "APPLIED"
    assert set(output(resumed)["adjudication"]["incremental"].values()) == {"APPLIED"}


def test_cli_rejects_missing_review_artifact_and_wrong_run(tmp_path):
    config, inbox = setup(tmp_path)
    requests = [publish_one(tmp_path, inbox) for _ in range(3)]
    path, _ = bundle_package(config, inbox, requests)
    wrong = call(tmp_path, inbox, "adjudicate", path,
                 "--statement-index", 10, "--run-id", "other-run")
    assert wrong.returncode == 2
    assert output(wrong)["error"] == "run_mismatch"
    statement = config.review.coordinator.workflow.review_cases_root / "statement_010"
    candidates = list(statement.rglob("histories/gemma_history.json"))
    assert candidates
    candidates[0].unlink()
    denied = call(tmp_path, inbox, "adjudicate", path,
                  "--statement-index", 10, "--run-id", "run-a")
    assert denied.returncode == 2
    assert not (statement / "adjudication.json").exists()


def test_cli_respects_workflow_lease(tmp_path):
    from automation.workflow_lease import WorkflowLease
    config, inbox = setup(tmp_path)
    requests = [publish_one(tmp_path, inbox) for _ in range(3)]
    path, _ = bundle_package(config, inbox, requests)
    lease = WorkflowLease(config.review.coordinator.lease_path, "test-holder")
    lease.acquire(blocking=False)
    try:
        denied = call(tmp_path, inbox, "adjudicate", path,
                      "--statement-index", 10, "--run-id", "run-a")
        assert denied.returncode == 2
        assert output(denied)["status"] == "CONTENDED"
        assert not (config.review.coordinator.workflow.review_cases_root /
                    "statement_010/adjudication.json").exists()
    finally:
        lease.release()
    accepted = call(tmp_path, inbox, "adjudicate", path,
                    "--statement-index", 10, "--run-id", "run-a")
    assert accepted.returncode == 0, accepted.stderr or accepted.stdout
    assert output(accepted)["status"] == "APPLIED"
