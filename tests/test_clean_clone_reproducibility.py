"""End-to-end smoke intended to run inside an independently installed clone."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.orchestrator import PlanAction
from automation.portable.__main__ import doctor
from automation.portable.browser import BrowserSessionFactory
from automation.portable.config import PortableConfigError, load_config
from automation.portable.inputs import InputAdapter
from automation.review.fake_site_adapter import FakeSiteReviewerAdapter
from automation.review.manual_adjudication import adjudication_status
from automation.review.synthetic_adjudication import (
    DeterministicFakeAdjudicatorAdapter, SyntheticAdjudicationConfig,
    run_synthetic_adjudication_and_persist_once,
)
from automation.review.synthetic_application import SyntheticApplicationConfig, run_synthetic_application_once
from automation.review.vertical_flow import VerticalReviewConfig, VerticalReviewStatus, run_synthetic_reviewer_once
from automation.workflow_coordinator import WorkflowCoordinatorConfig
from tests.helpers.fake_reviewer_sites import FakeSiteServer
from tests.test_portable_environment import configuration, source_fixture
from tests.test_synthetic_adjudication_flow import snapshot
from tests.test_verifiable_capture import load_bundle


def _dataset(tmp_path: Path, label: str):
    source = source_fixture(tmp_path / f"{label}-source")
    statement = source.statements_path
    statement.write_text(json.dumps({
        "statement_index": 10,
        "statement": {"report": f"synthetic clean clone {label}"},
    }) + "\n", encoding="utf-8")
    return source


def _run(tmp_path: Path, label: str):
    source = _dataset(tmp_path, label)
    config = load_config(configuration(tmp_path, source.statements_path.parent, label))
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    diagnosed = subprocess.run(
        [sys.executable, "-m", "automation.portable", "doctor", "--config", str(config.config_path)],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, check=True,
    )
    assert json.loads(diagnosed.stdout)["chromium_local_page"] == "PASS"
    before = snapshot(config.input_root)
    inspected = InputAdapter(config).inspect()
    assert inspected.cases == ((10, "run-a"),)
    staged = subprocess.run(
        [sys.executable, "-m", "automation.portable", "prepare", "--config", str(config.config_path)],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, check=True,
    )
    assert json.loads(staged.stdout)["source_sha256"] == inspected.source_sha256
    prepared = InputAdapter(config).prepare()
    assert prepared.source_sha256 == inspected.source_sha256
    assert InputAdapter(config).prepare().source_sha256 == inspected.source_sha256
    assert snapshot(config.input_root) == before
    assert (config.output_root / "portable_manifest.json").is_file()
    fixture_root = config.output_root.parent
    coordinator = WorkflowCoordinatorConfig(
        prepared.workflow, fixture_root / "runtime/workflow.lock", f"clone-{label}",
        lease_location_confirmed=True,
    )
    review = VerticalReviewConfig(coordinator, fixture_root, fixture_location_confirmed=True)
    adjudication = SyntheticAdjudicationConfig(review, config.output_root / "prompts/review_adjudication.md")
    with FakeSiteServer() as site:
        adapter = FakeSiteReviewerAdapter(
            site.base_url, config.browser.profile_root,
            prompt="synthetic clean clone review", prepare_fixture=load_bundle(site),
            session_factory=BrowserSessionFactory(config.browser),
            operator=f"operator-{label}", capture_pattern="structured",
        )
        for expected in (PlanAction.RUN_MISSING_REVIEWER, PlanAction.RUN_MISSING_REVIEWER,
                         PlanAction.RUN_ADJUDICATIONS):
            result = run_synthetic_reviewer_once(review, adapter)
            assert result.status is VerticalReviewStatus.COMPLETED
            assert result.after_plan.action is expected
        assert site.visits == [(reviewer, "bundle") for reviewer in ("chatgpt", "gemini", "grok")]
    assert len(list((config.output_root / "review_cases/statement_010/.review_sets").iterdir())) == 3
    adjudicated = run_synthetic_adjudication_and_persist_once(
        adjudication, DeterministicFakeAdjudicatorAdapter())
    assert adjudicated.status.value == "PERSISTED"
    application = SyntheticApplicationConfig(
        coordinator, fixture_root, 10, adjudicated.application.application_id,
        fixture_location_confirmed=True,
    )
    assert run_synthetic_application_once(application).status.value == "APPLIED"
    state = adjudication_status(review)
    assert state["bundle"] == "COMMITTED"
    assert state["statement_completion"] == "COMPLETE"
    assert len(state["incremental"]) == 6
    assert set(state["incremental"].values()) == {"APPLIED"}
    for model in ("gemma", "mistral", "llama"):
        history = config.output_root / "feedback" / f"{model}_history.jsonl"
        profile = config.output_root / "profiles" / f"{model}_profile.json"
        assert json.loads(history.read_text().splitlines()[-1])["output"] == 10
        assert json.loads(profile.read_text())["profile_metadata"]["outputs_reviewed"] == 11
    assert snapshot(config.input_root) == before
    return config, application


def test_new_clone_two_datasets_and_process_restart(tmp_path):
    # This file must be run from the checkout under test, without PYTHONPATH.
    assert Path.cwd().resolve() == Path(__file__).resolve().parents[1]
    first, application = _run(tmp_path, "alpha")
    second, _ = _run(tmp_path, "beta")
    assert first.input_root != second.input_root
    assert first.output_root != second.output_root
    assert first.browser.profile_root != second.browser.profile_root
    assert (first.output_root / "portable_manifest.json").read_text() != (
        second.output_root / "portable_manifest.json").read_text()
    before = snapshot(first.output_root)
    script = """from pathlib import Path
from automation.orchestrator import OrchestratorConfig
from automation.workflow_coordinator import WorkflowCoordinatorConfig
from automation.review.synthetic_application import SyntheticApplicationConfig, run_synthetic_application_once
import sys
root = Path(sys.argv[1]); operational = root / 'operational'
workflow = OrchestratorConfig(operational/'statements.jsonl', operational/'runs', operational/'review_cases', operational/'feedback', operational/'profiles')
coordinator = WorkflowCoordinatorConfig(workflow, root/'runtime/workflow.lock', 'clone-restart', lease_location_confirmed=True)
result = run_synthetic_application_once(SyntheticApplicationConfig(coordinator, root, 10, sys.argv[2], fixture_location_confirmed=True))
print(result.status.value)
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    restarted = subprocess.run(
        [sys.executable, "-c", script, str(first.output_root.parent), application.application_id],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, check=True,
    )
    assert restarted.stdout.strip() == "UNCHANGED"
    assert snapshot(first.output_root) == before
    assert snapshot(second.output_root) != before


def test_missing_local_configuration_is_reported(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "automation.portable", "doctor", "--config", str(tmp_path / "missing.json")],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "FAIL"


def test_doctor_rejects_incompatible_playwright(tmp_path, monkeypatch):
    source = _dataset(tmp_path, "incompatible")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "incompatible"))
    monkeypatch.setattr("automation.portable.__main__.importlib.metadata.version", lambda _: "0.0.0")
    with pytest.raises(PortableConfigError, match="Playwright 1.60.0"):
        doctor(config)


def test_unreadable_input_and_unwritable_output(tmp_path):
    source = _dataset(tmp_path, "permissions")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "permissions"))
    statement = source.statements_path
    original_mode = statement.stat().st_mode
    try:
        statement.chmod(0)
        with pytest.raises(PortableConfigError):
            InputAdapter(config).inspect()
    finally:
        statement.chmod(original_mode)
    config.output_root.parent.mkdir(parents=True)
    parent = config.output_root.parent
    original_mode = parent.stat().st_mode
    try:
        parent.chmod(0o500)
        with pytest.raises(PortableConfigError, match="not writable"):
            doctor(config)
    finally:
        parent.chmod(original_mode)
    assert not config.output_root.exists()


def test_duplicate_statement_identity_is_rejected(tmp_path):
    source = _dataset(tmp_path, "duplicate")
    statement = source.statements_path
    statement.write_text(statement.read_text() * 2, encoding="utf-8")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "duplicate"))
    with pytest.raises(PortableConfigError, match="duplicate statement_index"):
        InputAdapter(config).inspect()
    assert not config.output_root.exists()
