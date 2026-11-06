from __future__ import annotations

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.portable.browser import BrowserSessionFactory
from automation.portable.config import PortableConfigError, load_config
from automation.portable.inputs import InputAdapter
from automation.portable.__main__ import doctor, _browser_smoke
from automation.review.synthetic_adjudication import (
    DeterministicFakeAdjudicatorAdapter, SyntheticAdjudicationConfig,
    run_synthetic_three_reviewers_and_persist,
)
from automation.review.synthetic_application import SyntheticApplicationConfig, run_synthetic_application_once
from automation.review.vertical_flow import DeterministicFakeReviewerAdapter, VerticalReviewConfig
from automation.workflow_coordinator import WorkflowCoordinatorConfig
from tests.test_synthetic_adjudication_flow import ready_fixture, snapshot


def source_fixture(path: Path):
    path.mkdir()
    return ready_fixture(path).review.coordinator.workflow


def configuration(tmp_path: Path, source: Path, name: str) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "inputs": {"root": str(source), "allowed_roots": [str(source.parent)]},
        "outputs": {"root": str(tmp_path / f"{name}-workspace" / "operational")},
        "browser": {"engine": "chromium", "headless": True,
                    "locale": "es-CR", "timeout_ms": 30000,
                    "profile_root": str(tmp_path / f"{name}-profiles")},
    }), encoding="utf-8")
    return path


def test_two_different_datasets_prepare_without_source_changes(tmp_path):
    first = source_fixture(tmp_path / "source-a")
    second = source_fixture(tmp_path / "source-b")
    second.statements_path.write_text(
        json.dumps({"statement_index": 10, "statement": {"report": "different synthetic statement"}}) + "\n",
        encoding="utf-8")
    configs = [load_config(configuration(tmp_path, workflow.statements_path.parent, name))
               for workflow, name in ((first, "a"), (second, "b"))]
    before = [snapshot(config.input_root) for config in configs]
    prepared = [InputAdapter(config).prepare() for config in configs]
    assert [item.cases for item in prepared] == [((10, "run-a"),)] * 2
    assert prepared[0].source_sha256 != prepared[1].source_sha256
    assert [snapshot(config.input_root) for config in configs] == before
    assert prepared[0].workflow.statements_path.read_bytes() != prepared[1].workflow.statements_path.read_bytes()
    assert InputAdapter(configs[0]).prepare().source_sha256 == prepared[0].source_sha256
    assert not (configs[0].output_root / "review_cases/statement_011").exists()
    for config, item in zip(configs, prepared):
        fixture_root = config.output_root.parent
        coordinator = WorkflowCoordinatorConfig(
            item.workflow, fixture_root / "runtime/workflow.lock", "portable-pair",
            lease_location_confirmed=True)
        review = VerticalReviewConfig(coordinator, fixture_root, fixture_location_confirmed=True)
        adjudication = SyntheticAdjudicationConfig(
            review, config.output_root / "prompts/review_adjudication.md")
        result = run_synthetic_three_reviewers_and_persist(
            adjudication, DeterministicFakeReviewerAdapter(),
            DeterministicFakeAdjudicatorAdapter())
        assert result.adjudication.status.value == "PERSISTED"
        application = SyntheticApplicationConfig(
            coordinator, fixture_root, 10, result.adjudication.application.application_id,
            fixture_location_confirmed=True)
        assert run_synthetic_application_once(application).status.value == "APPLIED"
    assert [snapshot(config.input_root) for config in configs] == before
    assert (configs[0].output_root / "review_cases/statement_010/adjudication.json").is_file()
    assert (configs[1].output_root / "review_cases/statement_010/adjudication.json").is_file()


def test_prepared_dataset_continues_to_existing_adjudication(tmp_path, capsys):
    source = source_fixture(tmp_path / "source")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "operator"))
    prepared = InputAdapter(config).prepare()
    fixture_root = config.output_root.parent
    inbox = fixture_root / "inbox"
    inbox.mkdir()
    from automation.review.manual_cli import main as manual_main
    assert manual_main(["--root", str(fixture_root), "--inbox", str(inbox), "pending"]) == 0
    assert json.loads(capsys.readouterr().out)["statement_index"] == 10
    coordinator = WorkflowCoordinatorConfig(
        prepared.workflow, fixture_root / "runtime/workflow.lock", "portable-test",
        lease_location_confirmed=True)
    review = VerticalReviewConfig(coordinator, fixture_root, fixture_location_confirmed=True)
    adjudication = SyntheticAdjudicationConfig(
        review, config.output_root / "prompts/review_adjudication.md")
    result = run_synthetic_three_reviewers_and_persist(
        adjudication, DeterministicFakeReviewerAdapter(),
        DeterministicFakeAdjudicatorAdapter())
    assert len(result.reviewer_results) == 3
    assert result.adjudication.status.value == "PERSISTED"
    assert (config.output_root / "review_cases/statement_010/adjudication.json").is_file()
    assert snapshot(config.input_root) == snapshot(source.statements_path.parent)


def test_path_and_schema_guards(tmp_path):
    source = source_fixture(tmp_path / "source")
    path = configuration(tmp_path, source.statements_path.parent, "bad")
    raw = json.loads(path.read_text())
    for mutation in (
        lambda value: value.update(schema_version=2),
        lambda value: value["inputs"].update(root="../outside"),
        lambda value: value["outputs"].update(root=value["inputs"]["root"]),
        lambda value: value["browser"].update(engine="firefox"),
        lambda value: value["browser"].update(timeout_ms=0),
    ):
        changed = json.loads(json.dumps(raw))
        mutation(changed)
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(PortableConfigError):
            load_config(path)
    link = tmp_path / "link"
    link.symlink_to(source.statements_path.parent, target_is_directory=True)
    raw["inputs"]["root"] = str(link)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PortableConfigError):
        load_config(path)


def test_changed_source_rejects_reuse(tmp_path):
    source = source_fixture(tmp_path / "source")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "one"))
    InputAdapter(config).prepare()
    source.statements_path.write_text(
        source.statements_path.read_text().replace("synthetic statement", "changed statement"),
        encoding="utf-8")
    with pytest.raises(PortableConfigError, match="different or changed"):
        InputAdapter(config).prepare()


def test_variable_case_count_starts_only_first_case(tmp_path):
    source = source_fixture(tmp_path / "source")
    statements = source.statements_path
    statements.write_text(statements.read_text() + json.dumps(
        {"statement_index": 11, "statement": {"report": "second synthetic statement"}}) + "\n",
        encoding="utf-8")
    for model in ("gemma_2b", "mistral", "llama_3_2"):
        path = source.runs_root / "run-a" / model / "generation/draft_reports.jsonl"
        path.write_text(path.read_text() + json.dumps({
            "run_id": "run-a", "model_name": model, "statement_index": 11,
            "draft_report": f"synthetic {model} second"}) + "\n", encoding="utf-8")
    config = load_config(configuration(tmp_path, statements.parent, "multi"))
    prepared = InputAdapter(config).prepare()
    assert prepared.cases == ((10, "run-a"), (11, "run-a"))
    assert (config.output_root / "review_cases/statement_010").is_dir()
    assert not (config.output_root / "review_cases/statement_011").exists()


def test_missing_and_invalid_inputs_fail_without_output(tmp_path):
    source = source_fixture(tmp_path / "source")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "bad-input"))
    source.statements_path.write_text("{invalid\n", encoding="utf-8")
    with pytest.raises(PortableConfigError, match="invalid"):
        InputAdapter(config).prepare()
    assert not config.output_root.exists()
    source.statements_path.unlink()
    with pytest.raises(PortableConfigError, match="unreadable"):
        InputAdapter(config).inspect()


def test_browser_factory_isolates_operators_and_validates_names(tmp_path):
    source = source_fixture(tmp_path / "source")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "operator"))
    calls = []

    def fake_manager(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    factory = BrowserSessionFactory(config.browser, manager_type=fake_manager)
    factory.create("alice", "chatgpt")
    factory.create("bob", "chatgpt")
    assert calls[0][0][0] != calls[1][0][0]
    assert calls[0][1]["locale"] == "es-CR"
    with pytest.raises(PortableConfigError):
        factory.create("../bob", "chatgpt")


def test_doctor_reports_missing_dependencies(tmp_path, monkeypatch):
    source = source_fixture(tmp_path / "source")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "doctor"))
    monkeypatch.setattr("automation.portable.__main__.importlib.util.find_spec", lambda name: None)
    with pytest.raises(PortableConfigError, match="Playwright missing"):
        doctor(config)


def test_doctor_reports_missing_browser_binary(tmp_path, monkeypatch):
    source = source_fixture(tmp_path / "source")
    config = load_config(configuration(tmp_path, source.statements_path.parent, "doctor"))

    class MissingBrowser:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(tmp_path / "absent")))

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: MissingBrowser())
    with pytest.raises(PortableConfigError, match="Chromium is missing"):
        asyncio.run(_browser_smoke(config))
