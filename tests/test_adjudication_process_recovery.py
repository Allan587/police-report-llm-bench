"""OLBM-32 recovery using independent Python processes and temporary fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from automation.contracts import BenchmarkModel, Reviewer
from automation.orchestrator import PlanAction, plan_next_statement
from automation.review import ApplicationPhase, inspect_adjudication_application
from automation.review.synthetic_adjudication import SyntheticAdjudicationStatus
from tests.test_synthetic_adjudication_flow import (
    publish_reviewers,
    ready_fixture,
    snapshot,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "tests/helpers/adjudication_recovery_process.py"


def child(root: Path, mode: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(CHILD), str(root), mode],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def result(root: Path) -> dict:
    return json.loads((root / "child_result.json").read_text(encoding="utf-8"))


def application(config):
    workflow = config.review.coordinator.workflow
    statement = workflow.review_cases_root / "statement_010"
    paths = list((statement / ".adjudication_applications").glob("[!.]*"))
    assert len(paths) == 1
    return inspect_adjudication_application(
        application_path=paths[0],
        statement_path=statement,
        feedback_root=workflow.feedback_root,
        profiles_root=workflow.profiles_root,
    )


@pytest.mark.parametrize("cut,exit_code", [("before_commit", 43), ("after_replace", 44)])
def test_restart_before_commit_reuses_verified_preparation(
    tmp_path: Path, cut: str, exit_code: int
) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    workflow = config.review.coordinator.workflow
    feedback_before = snapshot(workflow.feedback_root)
    profiles_before = snapshot(workflow.profiles_root)

    assert child(tmp_path, cut).returncode == exit_code
    final = workflow.review_cases_root / "statement_010/adjudication.json"
    assert final.exists() is (cut == "after_replace")
    assert application(config).phase is (
        ApplicationPhase.PREPARED if cut == "before_commit"
        else ApplicationPhase.ADJUDICATION_COMMITTED
    )
    assert child(tmp_path, "resume").returncode == 0
    resumed = result(tmp_path)
    assert resumed["statement_index"] == 10
    assert resumed["reviewer_calls"] == 0
    assert resumed["adjudicator_calls"] == (1 if cut == "before_commit" else 0)
    assert resumed["status"] == (
        SyntheticAdjudicationStatus.PERSISTED.value
        if cut == "before_commit" else SyntheticAdjudicationStatus.UNCHANGED.value
    )
    assert resumed["plan"] == PlanAction.ADJUDICATION_COMMITTED.value
    assert application(config).phase is ApplicationPhase.ADJUDICATION_COMMITTED
    assert snapshot(workflow.feedback_root) == feedback_before
    assert snapshot(workflow.profiles_root) == profiles_before


def test_restart_after_commit_preserves_exact_bytes_and_does_not_readjudicate(
    tmp_path: Path,
) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    assert child(tmp_path, "after_commit").returncode == 45
    workflow = config.review.coordinator.workflow
    statement = workflow.review_cases_root / "statement_010"
    final = statement / "adjudication.json"
    original = final.read_bytes()
    before = snapshot(statement)

    assert child(tmp_path, "resume").returncode == 0
    assert result(tmp_path)["status"] == "UNCHANGED"
    assert result(tmp_path)["adjudicator_calls"] == 0
    assert final.read_bytes() == original
    assert snapshot(statement) == before
    assert hashlib.sha256(final.read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest()
    assert child(tmp_path, "resume").returncode == 0
    assert result(tmp_path)["status"] == "UNCHANGED"
    assert snapshot(statement) == before
    assert plan_next_statement(workflow).action is PlanAction.ADJUDICATION_COMMITTED


def test_restart_executes_only_missing_reviewer(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    assert child(tmp_path, "partial_reviews").returncode == 46
    workflow = config.review.coordinator.workflow
    statement = workflow.review_cases_root / "statement_010"
    before = {
        reviewer: snapshot(statement / ".review_sets" / reviewer.value)
        for reviewer in (Reviewer.CHATGPT, Reviewer.GEMINI)
    }
    assert child(tmp_path, "resume").returncode == 0
    resumed = result(tmp_path)
    assert resumed["reviewer_calls"] == 1
    assert resumed["adjudicator_calls"] == 1
    assert resumed["status"] == "PERSISTED"
    for reviewer, files in before.items():
        assert snapshot(statement / ".review_sets" / reviewer.value) == files
    assert application(config).phase is ApplicationPhase.ADJUDICATION_COMMITTED


def test_missing_input_after_restart_blocks_without_effects(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    assert child(tmp_path, "before_commit").returncode == 43
    workflow = config.review.coordinator.workflow
    missing = workflow.runs_root / "run-a/llama_3_2/generation/draft_reports.jsonl"
    missing.unlink()
    before = snapshot(workflow.review_cases_root)
    assert child(tmp_path, "resume").returncode == 0
    assert result(tmp_path)["status"] == "VALIDATION_FAILED"
    assert result(tmp_path)["adjudicator_calls"] == 0
    assert snapshot(workflow.review_cases_root) == before


@pytest.mark.parametrize("corruption", ["manifest", "prepared", "run"])
def test_corrupt_or_contradictory_preparation_fails_closed(
    tmp_path: Path, corruption: str
) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    assert child(tmp_path, "before_commit").returncode == 43
    app = application(config)
    if corruption == "manifest":
        path = app.manifest_path
        value = json.loads(path.read_text(encoding="utf-8"))
        value["run_id"] = "foreign-run"
        write_json(path, value)
    elif corruption == "prepared":
        (app.application_path / "prepared/history/gemma_history.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
    else:
        # A valid manifest with the wrong current run must not be accepted.
        workflow = config.review.coordinator.workflow
        metadata = workflow.runs_root / "run-a/metadata.json"
        write_json(metadata, {"run_id": "foreign-run"})
    before = snapshot(config.review.coordinator.workflow.review_cases_root)
    assert child(tmp_path, "resume").returncode == 0
    assert result(tmp_path)["status"] in {"BLOCKED", "VALIDATION_FAILED"}
    assert result(tmp_path)["adjudicator_calls"] == 0
    assert snapshot(config.review.coordinator.workflow.review_cases_root) == before
    assert not (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/adjudication.json"
    ).exists()


def test_ambiguous_orphan_preparation_blocks_without_commit(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    assert child(tmp_path, "before_commit").returncode == 43
    statement = config.review.coordinator.workflow.review_cases_root / "statement_010"
    orphan = statement / ".adjudication_applications/.staging-orphan"
    orphan.mkdir()
    before = snapshot(statement)
    assert child(tmp_path, "resume").returncode == 0
    assert result(tmp_path)["status"] == "BLOCKED"
    assert result(tmp_path)["adjudicator_calls"] == 0
    assert snapshot(statement) == before
    assert not (statement / "adjudication.json").exists()


def test_lease_excludes_second_process_and_releases_after_termination(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    holder = subprocess.Popen(
        [sys.executable, str(CHILD), str(tmp_path), "hold"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "holder_ready").exists() and time.monotonic() < deadline:
            assert holder.poll() is None
            time.sleep(0.02)
        assert (tmp_path / "holder_ready").exists()
        before = snapshot(config.review.coordinator.workflow.review_cases_root)
        assert child(tmp_path, "resume").returncode == 0
        assert result(tmp_path)["status"] == "CONTENDED"
        assert snapshot(config.review.coordinator.workflow.review_cases_root) == before
    finally:
        holder.kill()
        holder.communicate(timeout=10)
    assert child(tmp_path, "resume").returncode == 0
    assert result(tmp_path)["status"] == "PERSISTED"


def test_committed_application_with_changed_active_attempt_fails_closed(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    assert child(tmp_path, "after_commit").returncode == 45
    workflow = config.review.coordinator.workflow
    statement = workflow.review_cases_root / "statement_010"
    reference = statement / ".review_sets/grok/active.json"
    value = json.loads(reference.read_text(encoding="utf-8"))
    value["attempt_id"] = "foreign-attempt"
    write_json(reference, value)
    before = snapshot(statement)
    assert child(tmp_path, "resume").returncode == 0
    assert result(tmp_path)["status"] in {"BLOCKED", "VALIDATION_FAILED"}
    assert result(tmp_path)["adjudicator_calls"] == 0
    assert snapshot(statement) == before
