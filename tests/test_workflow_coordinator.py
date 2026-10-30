from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import automation.workflow_coordinator as coordinator_module
from automation.contracts import BenchmarkModel
from automation.orchestrator import (
    OrchestratorConfig,
    PlanAction,
    StatementPlan,
    plan_next_statement,
)
from automation.workflow_coordinator import (
    CoordinationExpectation,
    CoordinationStatus,
    WorkflowCoordinatorConfig,
    coordinate_workflow_cycle,
)
from automation.workflow_lease import WorkflowLease, WorkflowLeaseInUseError


RAW_MODELS = {
    BenchmarkModel.GEMMA: "gemma_2b",
    BenchmarkModel.MISTRAL: "mistral",
    BenchmarkModel.LLAMA: "llama_3_2",
}


def workflow_config(root: Path) -> OrchestratorConfig:
    operational = root / "operational"
    return OrchestratorConfig(
        statements_path=operational / "statements.jsonl",
        runs_root=operational / "runs",
        review_cases_root=operational / "review_cases",
        feedback_root=operational / "feedback",
        profiles_root=operational / "profiles",
    )


def coordinator_config(root: Path, owner: str = "coordinator") -> WorkflowCoordinatorConfig:
    return WorkflowCoordinatorConfig(
        workflow_config(root),
        root / "runtime" / "workflow.lock",
        owner,
        lease_location_confirmed=True,
    )


def plan(action: PlanAction = PlanAction.RUN_MISSING_REVIEWER) -> StatementPlan:
    return StatementPlan(action, 10, Path("statement_010"), action.value)


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def add_real_initial_inputs(config: OrchestratorConfig, index: int = 10) -> None:
    (config.review_cases_root / "statement_009").mkdir(parents=True)
    config.statements_path.parent.mkdir(parents=True, exist_ok=True)
    config.statements_path.write_text(
        json.dumps(
            {"statement_index": index, "statement": {"report": "synthetic statement"}}
        )
        + "\n",
        encoding="utf-8",
    )
    run_id = "run-a"
    metadata = config.runs_root / run_id / "metadata.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    for model, raw_name in RAW_MODELS.items():
        output = (
            config.runs_root
            / run_id
            / raw_name
            / "generation"
            / "draft_reports.jsonl"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "model_name": raw_name,
                    "statement_index": index,
                    "draft_report": f"synthetic {model.value}",
                }
            )
            + "\n",
            encoding="utf-8",
        )


def precondition_fingerprint(config: OrchestratorConfig) -> str:
    return coordinator_module._precondition_fingerprint(config, None)


def test_requires_explicit_safe_lease_location(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit operator confirmation"):
        WorkflowCoordinatorConfig(
            workflow_config(tmp_path), tmp_path / "runtime" / "lock", "owner"
        )


def test_rejects_known_synchronized_and_operational_locations(tmp_path: Path) -> None:
    workflow = OrchestratorConfig(
        statements_path=tmp_path / "data" / "statements" / "statements.jsonl",
        runs_root=tmp_path / "data" / "generated" / "runs",
        review_cases_root=tmp_path / "data" / "review_cases",
        feedback_root=tmp_path / "config" / "feedback",
        profiles_root=tmp_path / "config" / "profiles",
    )
    with pytest.raises(ValueError, match="outside data and config"):
        WorkflowCoordinatorConfig(
            workflow,
            tmp_path / "data" / "runtime" / "lock",
            "owner",
            lease_location_confirmed=True,
        )
    with pytest.raises(ValueError, match="synchronized"):
        WorkflowCoordinatorConfig(
            workflow_config(tmp_path),
            tmp_path / "OneDrive" / "runtime" / "lock",
            "owner",
            lease_location_confirmed=True,
        )


def test_rejects_symlinked_location_resolving_into_operational_root(tmp_path: Path) -> None:
    workflow = OrchestratorConfig(
        statements_path=tmp_path / "data" / "statements" / "statements.jsonl",
        runs_root=tmp_path / "data" / "runs",
        review_cases_root=tmp_path / "data" / "review_cases",
        feedback_root=tmp_path / "config" / "feedback",
        profiles_root=tmp_path / "config" / "profiles",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "runtime-link").symlink_to(tmp_path / "data", target_is_directory=True)

    with pytest.raises(ValueError, match="outside data and config"):
        WorkflowCoordinatorConfig(
            workflow,
            tmp_path / "runtime-link" / "workflow.lock",
            "owner",
            lease_location_confirmed=True,
        )


def test_lease_is_acquired_before_planning_and_valid_plan_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    calls = 0

    def inspected(_: OrchestratorConfig) -> StatementPlan:
        nonlocal calls
        calls += 1
        with pytest.raises(WorkflowLeaseInUseError):
            WorkflowLease(cfg.lease_path, "contender").acquire()
        return plan()

    monkeypatch.setattr(coordinator_module, "plan_next_statement", inspected)
    result = coordinate_workflow_cycle(cfg)

    assert calls == 2
    assert result.status is CoordinationStatus.ACTION_REQUIRED
    assert result.authorization_required
    assert result.precondition_sha256 is not None
    with WorkflowLease(cfg.lease_path, "after-cycle"):
        pass


def test_external_stale_plan_is_rejected_after_fresh_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())
    expected = CoordinationExpectation(plan(PlanAction.RUN_ADJUDICATIONS))

    result = coordinate_workflow_cycle(coordinator_config(tmp_path), expected=expected)

    assert result.status is CoordinationStatus.STALE_PLAN
    assert result.plan == plan()
    assert not result.authorization_required


@pytest.mark.parametrize("changed_name", ["manifest.json", "active.json"])
def test_storage_change_during_revalidation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_name: str
) -> None:
    cfg = coordinator_config(tmp_path)
    statement = cfg.workflow.review_cases_root / "statement_010"
    target = statement / changed_name
    target.parent.mkdir(parents=True)
    target.write_text('{"identity":"first"}', encoding="utf-8")
    calls = 0

    def changing(_: OrchestratorConfig) -> StatementPlan:
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_text('{"identity":"second"}', encoding="utf-8")
        return plan(
            PlanAction.RECOVER_APPLICATION
            if changed_name == "manifest.json"
            else PlanAction.RETRY_REVIEWER
        )

    monkeypatch.setattr(coordinator_module, "plan_next_statement", changing)
    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "changed during protected revalidation" in result.reason


def test_prior_fingerprint_detects_same_plan_with_changed_preconditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())
    first = coordinate_workflow_cycle(cfg)
    marker = cfg.workflow.review_cases_root / "statement_010" / "active.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"attempt_id":"changed"}', encoding="utf-8")

    second = coordinate_workflow_cycle(cfg, expected=first.expectation())

    assert second.status is CoordinationStatus.STALE_PLAN
    assert second.plan == first.plan


def test_planning_exception_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)

    def fail(_: OrchestratorConfig) -> StatementPlan:
        raise OSError("synthetic inspection failure")

    monkeypatch.setattr(coordinator_module, "plan_next_statement", fail)
    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    with WorkflowLease(cfg.lease_path, "after-error"):
        pass


def test_audit_statements_symlink_scenario_is_rejected_before_unsafe_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    target = tmp_path / "external" / "statements.jsonl"
    target.parent.mkdir()
    cfg.workflow.statements_path.parent.mkdir(parents=True)
    cfg.workflow.statements_path.symlink_to(target)
    add_real_initial_inputs(cfg.workflow)

    # This is the original O02-QA-04 precondition: unprotected O-01 follows the
    # link and proposes an actionable review for statement 010.
    original_plan = plan_next_statement(cfg.workflow)
    assert original_plan.action is PlanAction.RUN_MISSING_REVIEWER
    assert original_plan.statement_index == 10
    target.write_text(
        json.dumps(
            {"statement_index": 10, "statement": {"report": "changed target"}}
        )
        + "\n",
        encoding="utf-8",
    )

    planning_calls = 0

    def track_planning(config: OrchestratorConfig) -> StatementPlan:
        nonlocal planning_calls
        planning_calls += 1
        return plan_next_statement(config)

    monkeypatch.setattr(coordinator_module, "plan_next_statement", track_planning)
    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "symbolic dependency rejected for statements" in result.reason
    assert not result.authorization_required
    assert result.plan is None
    assert planning_calls == 0


def test_statements_path_replaced_by_symlink_after_second_real_plan_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    add_real_initial_inputs(cfg.workflow)
    target = tmp_path / "external-statements.jsonl"
    target.write_text(
        json.dumps(
            {"statement_index": 10, "statement": {"report": "changed target"}}
        )
        + "\n",
        encoding="utf-8",
    )
    original = cfg.workflow.statements_path.with_suffix(".original")
    calls = 0

    def plan_then_replace(config: OrchestratorConfig) -> StatementPlan:
        nonlocal calls
        inspected = plan_next_statement(config)
        calls += 1
        if calls == 2:
            cfg.workflow.statements_path.rename(original)
            cfg.workflow.statements_path.symlink_to(target)
        return inspected

    monkeypatch.setattr(coordinator_module, "plan_next_statement", plan_then_replace)

    result = coordinate_workflow_cycle(cfg)

    assert calls == 2
    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "symbolic dependency rejected for statements" in result.reason
    assert not result.authorization_required
    assert result.plan is None


@pytest.mark.parametrize(
    ("dependency", "relative_path"),
    [
        ("review_cases", Path("marker.json")),
        ("feedback", Path("gemma_history.jsonl")),
        ("profiles", Path("gemma_profile.json")),
    ],
)
def test_symbolic_file_inside_dependency_tree_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency: str,
    relative_path: Path,
) -> None:
    cfg = coordinator_config(tmp_path)
    root = {
        "review_cases": cfg.workflow.review_cases_root,
        "feedback": cfg.workflow.feedback_root,
        "profiles": cfg.workflow.profiles_root,
    }[dependency]
    root.mkdir(parents=True)
    target = tmp_path / "external" / f"{dependency}.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text('{}', encoding="utf-8")
    (root / relative_path).symlink_to(target)
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())

    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert f"symbolic dependency rejected for {dependency}" in result.reason
    assert not result.authorization_required
    assert result.plan is None


def test_symbolic_review_cases_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    target = tmp_path / "external-review-cases"
    target.mkdir()
    cfg.workflow.review_cases_root.parent.mkdir(parents=True)
    cfg.workflow.review_cases_root.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())

    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "symbolic dependency rejected for review_cases" in result.reason
    assert result.plan is None


def test_symbolic_intermediate_directory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external-operational"
    external.mkdir()
    intermediate = tmp_path / "operational-link"
    intermediate.symlink_to(external, target_is_directory=True)
    workflow = OrchestratorConfig(
        statements_path=intermediate / "statements.jsonl",
        runs_root=intermediate / "runs",
        review_cases_root=intermediate / "review_cases",
        feedback_root=intermediate / "feedback",
        profiles_root=intermediate / "profiles",
    )
    cfg = WorkflowCoordinatorConfig(
        workflow,
        tmp_path / "runtime" / "workflow.lock",
        "coordinator",
        lease_location_confirmed=True,
    )
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())

    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "symbolic dependency rejected" in result.reason
    assert str(intermediate) in result.reason


def test_selected_run_through_symbolic_directory_is_rejected(tmp_path: Path) -> None:
    cfg = coordinator_config(tmp_path)
    add_real_initial_inputs(cfg.workflow)
    run = cfg.workflow.runs_root / "run-a"
    target = tmp_path / "external-run"
    run.rename(target)
    run.symlink_to(target, target_is_directory=True)

    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "symbolic dependency rejected for runs" in result.reason
    assert not result.authorization_required
    assert result.plan is None


@pytest.mark.parametrize("kind", ["broken", "circular"])
def test_broken_or_circular_symlink_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    cfg = coordinator_config(tmp_path)
    cfg.workflow.feedback_root.mkdir(parents=True)
    link = cfg.workflow.feedback_root / "gemma_history.jsonl"
    link.symlink_to(
        cfg.workflow.feedback_root / ("missing.jsonl" if kind == "broken" else link.name)
    )
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())

    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "symbolic dependency rejected for feedback" in result.reason
    assert not result.authorization_required


def test_file_replaced_by_symlink_during_planning_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    history = cfg.workflow.feedback_root / "gemma_history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text('{"state":"ordinary"}\n', encoding="utf-8")
    target = tmp_path / "external-history.jsonl"
    target.write_text('{"state":"external"}\n', encoding="utf-8")
    calls = 0

    def replace_during_first_inspection(_: OrchestratorConfig) -> StatementPlan:
        nonlocal calls
        calls += 1
        if calls == 1:
            history.unlink()
            history.symlink_to(target)
        return plan()

    monkeypatch.setattr(
        coordinator_module, "plan_next_statement", replace_during_first_inspection
    )

    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert "symbolic dependency rejected for feedback" in result.reason
    assert not result.authorization_required
    assert result.plan is None


def test_identical_ordinary_states_produce_the_same_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())
    results = []
    for name in ("first", "second"):
        cfg = coordinator_config(tmp_path / name)
        marker = cfg.workflow.feedback_root / "gemma_history.jsonl"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"same":true}\n', encoding="utf-8")
        results.append(coordinate_workflow_cycle(cfg))

    assert all(result.status is CoordinationStatus.ACTION_REQUIRED for result in results)
    assert results[0].precondition_sha256 == results[1].precondition_sha256


def test_structural_collision_states_have_distinct_fingerprints(tmp_path: Path) -> None:
    config = workflow_config(tmp_path)
    auxiliary = config.review_cases_root / "statement_009" / "aux"
    auxiliary.mkdir(parents=True)
    first = auxiliary / "a"
    second = auxiliary / "b"
    imitated_header = b"statement_009/aux/b\0file\0"

    first.write_bytes(b"X" + imitated_header + b"Y")
    state_a = precondition_fingerprint(config)

    first.write_bytes(b"X")
    second.write_bytes(b"Y")
    state_b = precondition_fingerprint(config)

    assert state_a != state_b


def test_structural_collision_change_after_second_real_plan_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    add_real_initial_inputs(cfg.workflow)
    auxiliary = cfg.workflow.review_cases_root / "statement_009" / "aux"
    auxiliary.mkdir(parents=True)
    first = auxiliary / "a"
    second = auxiliary / "b"
    first.write_bytes(b"Xstatement_009/aux/b\0file\0Y")
    calls = 0

    def replace_after_second_real_plan(config: OrchestratorConfig) -> StatementPlan:
        nonlocal calls
        inspected = plan_next_statement(config)
        calls += 1
        if calls == 2:
            first.write_bytes(b"X")
            second.write_bytes(b"Y")
        return inspected

    monkeypatch.setattr(
        coordinator_module, "plan_next_statement", replace_after_second_real_plan
    )

    result = coordinate_workflow_cycle(cfg)

    assert calls == 2
    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert not result.authorization_required
    assert result.plan is None


@pytest.mark.parametrize(
    ("left_name", "left_content", "right_name", "right_content"),
    [
        ("nul.bin", b"A\0B", "nul.bin", b"A\0C"),
        (
            "header.bin",
            b"Xstatement_009/aux/b\0file\0Y",
            "header.bin",
            b"Xstatement_009/aux/b\0directory\0Y",
        ),
        ("a", b"same", "a-much-longer-name", b"same"),
    ],
)
def test_fingerprint_frames_arbitrary_content_and_paths(
    tmp_path: Path,
    left_name: str,
    left_content: bytes,
    right_name: str,
    right_content: bytes,
) -> None:
    left = workflow_config(tmp_path / "left")
    right = workflow_config(tmp_path / "right")
    left_file = left.review_cases_root / left_name
    right_file = right.review_cases_root / right_name
    left_file.parent.mkdir(parents=True)
    right_file.parent.mkdir(parents=True)
    left_file.write_bytes(left_content)
    right_file.write_bytes(right_content)

    assert precondition_fingerprint(left) != precondition_fingerprint(right)


def test_empty_file_addition_and_deletion_change_fingerprint(tmp_path: Path) -> None:
    config = workflow_config(tmp_path)
    config.review_cases_root.mkdir(parents=True)
    without_file = precondition_fingerprint(config)
    empty = config.review_cases_root / "empty"
    empty.write_bytes(b"")
    with_file = precondition_fingerprint(config)
    empty.unlink()

    assert with_file != without_file
    assert precondition_fingerprint(config) == without_file


def test_directory_creation_order_does_not_change_fingerprint(tmp_path: Path) -> None:
    digests = []
    for root_name, names in (("forward", ("a", "b")), ("reverse", ("b", "a"))):
        config = workflow_config(tmp_path / root_name)
        config.review_cases_root.mkdir(parents=True)
        for name in names:
            (config.review_cases_root / name).write_bytes(name.encode("ascii"))
        digests.append(precondition_fingerprint(config))

    assert digests[0] == digests[1]


def test_large_ordinary_file_content_is_framed(tmp_path: Path) -> None:
    config = workflow_config(tmp_path)
    large = config.review_cases_root / "large.bin"
    large.parent.mkdir(parents=True)
    large.write_bytes(b"A" * (1024 * 1024))
    before = precondition_fingerprint(config)
    large.write_bytes(b"A" * (1024 * 1024 - 1) + b"B")

    assert precondition_fingerprint(config) != before


def test_anchor_replacement_blocks_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    replaced = False

    def replace_anchor(_: OrchestratorConfig) -> StatementPlan:
        nonlocal replaced
        if not replaced:
            replaced = True
            cfg.lease_path.parent.rename(tmp_path / "moved-runtime")
            cfg.lease_path.parent.mkdir(mode=0o700)
        return plan()

    monkeypatch.setattr(coordinator_module, "plan_next_statement", replace_anchor)
    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.VALIDATION_FAILED
    assert not result.authorization_required


@pytest.mark.parametrize("action", [PlanAction.BLOCKED, PlanAction.WAIT_OPERATOR])
def test_inspector_block_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: PlanAction
) -> None:
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan(action))

    result = coordinate_workflow_cycle(coordinator_config(tmp_path))

    assert result.status is CoordinationStatus.BLOCKED
    assert result.plan is not None and result.plan.action is action


def test_recovery_action_remains_prioritized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery = plan(PlanAction.RECOVER_APPLICATION)
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: recovery)

    result = coordinate_workflow_cycle(coordinator_config(tmp_path))

    assert result.status is CoordinationStatus.ACTION_REQUIRED
    assert result.plan == recovery


def test_real_planner_reconstructs_next_statement_without_reprocessing_history(
    tmp_path: Path,
) -> None:
    cfg = coordinator_config(tmp_path)
    add_real_initial_inputs(cfg.workflow)

    result = coordinate_workflow_cycle(cfg)

    assert result.status is CoordinationStatus.ACTION_REQUIRED
    assert result.plan is not None
    assert result.plan.statement_index == 10
    assert result.plan.action is PlanAction.RUN_MISSING_REVIEWER
    assert result.plan.run_id == "run-a"
    assert result.plan.statement_index != 9


def test_real_process_contention_returns_explicit_result(tmp_path: Path) -> None:
    helper = Path(__file__).parent / "helpers" / "workflow_coordinator_holder.py"
    environment = os.environ.copy()
    repository = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = repository
    process = subprocess.Popen(
        [sys.executable, str(helper), str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdout is not None and process.stdin is not None
    assert process.stdout.readline().strip() == "planning"
    try:
        result = coordinate_workflow_cycle(coordinator_config(tmp_path, "contender"))
        assert result.status is CoordinationStatus.CONTENDED
        assert result.plan is None
    finally:
        process.stdin.write("continue\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == CoordinationStatus.CURRENT.value
        assert process.wait(timeout=5) == 0


def test_coordination_does_not_modify_operational_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = coordinator_config(tmp_path)
    files = {
        cfg.workflow.statements_path: [{"statement_index": 10}],
        cfg.workflow.feedback_root / "gemma_history.jsonl": [{"sentinel": "history"}],
        cfg.workflow.profiles_root / "gemma_profile.json": {"sentinel": "profile"},
    }
    for path, value in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    before = snapshot(cfg.workflow.statements_path.parent)
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: plan())

    result = coordinate_workflow_cycle(cfg)
    after = snapshot(cfg.workflow.statements_path.parent)

    assert result.status is CoordinationStatus.ACTION_REQUIRED
    assert after == before
