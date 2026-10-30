from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import automation.synthetic_executor as executor_module
import automation.workflow_coordinator as coordinator_module
from automation.contracts import BenchmarkModel
from automation.orchestrator import OrchestratorConfig, PlanAction, StatementPlan, plan_next_statement
from automation.synthetic_executor import (
    SyntheticExecutionStatus,
    SyntheticExecutorConfig,
    SyntheticOperationRejected,
    SyntheticWriteOperation,
    execute_synthetic_write,
    prepare_synthetic_write,
)
from automation.workflow_coordinator import CoordinationExpectation, WorkflowCoordinatorConfig
from automation.workflow_lease import WorkflowLease, WorkflowLeaseInUseError


RAW_MODELS = {
    BenchmarkModel.GEMMA: "gemma_2b",
    BenchmarkModel.MISTRAL: "mistral",
    BenchmarkModel.LLAMA: "llama_3_2",
}


def workflow_config(root: Path) -> OrchestratorConfig:
    operational = root / "operational"
    return OrchestratorConfig(
        operational / "statements.jsonl",
        operational / "runs",
        operational / "review_cases",
        operational / "feedback",
        operational / "profiles",
    )


def executor_config(root: Path, owner: str = "synthetic-test") -> SyntheticExecutorConfig:
    coordinator = WorkflowCoordinatorConfig(
        workflow_config(root),
        root / "runtime" / "workflow.lock",
        owner,
        lease_location_confirmed=True,
    )
    temporary_base = root / "temporary"
    synthetic_root = temporary_base / "effects"
    synthetic_root.mkdir(parents=True, exist_ok=True)
    return SyntheticExecutorConfig(
        coordinator,
        temporary_base,
        synthetic_root,
        temporary_location_confirmed=True,
    )


def stable_plan(action: PlanAction = PlanAction.RUN_MISSING_REVIEWER) -> StatementPlan:
    return StatementPlan(action, 10, Path("statement_010"), "synthetic plan")


def set_stable_planner(monkeypatch: pytest.MonkeyPatch, action=PlanAction.RUN_MISSING_REVIEWER):
    value = stable_plan(action)
    monkeypatch.setattr(coordinator_module, "plan_next_statement", lambda _: value)
    return value


def snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def add_real_inputs(config: OrchestratorConfig) -> None:
    historical = config.review_cases_root / "statement_009"
    historical.mkdir(parents=True)
    (historical / "sentinel.bin").write_bytes(b"historical")
    config.statements_path.parent.mkdir(parents=True, exist_ok=True)
    config.statements_path.write_text(
        json.dumps({"statement_index": 10, "statement": {"report": "synthetic"}}) + "\n",
        encoding="utf-8",
    )
    for model, raw_name in RAW_MODELS.items():
        output = config.runs_root / "run-a" / raw_name / "generation" / "draft_reports.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": "run-a",
                    "model_name": raw_name,
                    "statement_index": 10,
                    "draft_report": f"synthetic {model.value}",
                }
            ) + "\n",
            encoding="utf-8",
        )


def test_effect_runs_while_same_lease_is_acquired(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"payload")
    original = executor_module._write_synthetic_file
    observed = False

    def assert_locked(value):
        nonlocal observed
        observed = True
        with pytest.raises(WorkflowLeaseInUseError):
            WorkflowLease(config.coordinator.lease_path, "contender").acquire()
        return original(value)

    monkeypatch.setattr(executor_module, "_write_synthetic_file", assert_locked)
    result = execute_synthetic_write(config, operation)

    assert observed
    assert result.status is SyntheticExecutionStatus.COMPLETED
    assert result.evidence is not None
    assert result.evidence.result_sha256 == hashlib.sha256(b"payload").hexdigest()
    assert (config.synthetic_root / "result.bin").read_bytes() == b"payload"


def test_same_inode_and_size_content_change_cannot_complete(
    tmp_path: Path, monkeypatch
) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    payload = b"requested"
    altered = b"tampered!"
    operation = prepare_synthetic_write(config, Path("result.bin"), payload)
    original_fsync = executor_module.os.fsync
    descriptor_identities = []

    def alter_before_verification(descriptor: int) -> None:
        descriptor_identities.append(executor_module._identity(os.fstat(descriptor)))
        os.pwrite(descriptor, altered, 0)
        descriptor_identities.append(executor_module._identity(os.fstat(descriptor)))
        original_fsync(descriptor)

    monkeypatch.setattr(executor_module.os, "fsync", alter_before_verification)
    result = execute_synthetic_write(config, operation)
    target = config.synthetic_root / "result.bin"

    assert result.status is SyntheticExecutionStatus.EXECUTION_ERROR
    assert result.evidence is None
    assert target.read_bytes() == altered
    assert target.stat().st_size == len(payload)
    assert descriptor_identities[0] == descriptor_identities[1]
    assert "content differs" in result.reason


def test_requested_and_observed_digests_must_match(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    payload = b"requested"
    observed = b"different"
    operation = prepare_synthetic_write(config, Path("result.bin"), payload)

    def write_different_content(descriptor: int, ignored: bytes) -> None:
        executor_module.os.write(descriptor, observed)

    monkeypatch.setattr(executor_module, "_write_all", write_different_content)
    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.EXECUTION_ERROR
    assert result.evidence is None
    assert hashlib.sha256(observed).hexdigest() != hashlib.sha256(payload).hexdigest()
    assert (config.synthetic_root / "result.bin").read_bytes() == observed


def test_incomplete_descriptor_read_cannot_complete(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"payload")
    original_read = executor_module.os.read
    calls = 0

    def stop_after_first_byte(descriptor: int, size: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_read(descriptor, 1)
        return b""

    monkeypatch.setattr(executor_module.os, "read", stop_after_first_byte)
    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.EXECUTION_ERROR
    assert result.evidence is None
    assert "verification was incomplete" in result.reason
    assert (config.synthetic_root / "result.bin").exists()


def test_descriptor_error_during_verification_cannot_complete(
    tmp_path: Path, monkeypatch
) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"payload")

    def invalid_descriptor(*_args) -> int:
        raise OSError(errno.EBADF, "controlled invalid descriptor")

    monkeypatch.setattr(executor_module.os, "lseek", invalid_descriptor)
    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.EXECUTION_ERROR
    assert result.evidence is None
    assert "controlled invalid descriptor" in result.reason
    assert (config.synthetic_root / "result.bin").exists()


def test_manual_non_null_preimage_is_rejected_before_write(
    tmp_path: Path, monkeypatch
) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    valid = prepare_synthetic_write(config, Path("result.bin"), b"payload")
    contradictory = replace(valid, expected_preimage_sha256="f" * 64)

    result = execute_synthetic_write(config, contradictory)

    assert result.status is SyntheticExecutionStatus.OPERATION_REJECTED
    assert result.evidence is None
    assert "absent preimage" in result.reason
    assert not (config.synthetic_root / "result.bin").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"root": "not-a-path"},
        {"relative_path": "result.bin"},
        {"payload": bytearray(b"payload")},
        {"root_identity": (1, 2)},
        {"parent_identity": (1, True, 3)},
    ],
)
def test_manual_operation_rejects_invalid_types_and_identities(
    tmp_path: Path, monkeypatch, changes: dict[str, object]
) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    valid = prepare_synthetic_write(config, Path("result.bin"), b"payload")
    invalid = replace(valid, **changes)

    result = execute_synthetic_write(config, invalid)

    assert result.status is SyntheticExecutionStatus.OPERATION_REJECTED
    assert result.evidence is None
    assert not (config.synthetic_root / "result.bin").exists()


def test_two_processes_cannot_execute_effects_simultaneously(tmp_path: Path, monkeypatch) -> None:
    helper = Path(__file__).parent / "helpers" / "synthetic_executor_holder.py"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen(
        [sys.executable, str(helper), str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdout is not None and process.stdin is not None
    assert process.stdout.readline().strip() == "effect"
    try:
        config = executor_config(tmp_path, "contender")
        set_stable_planner(monkeypatch)
        operation = prepare_synthetic_write(config, Path("contender.bin"), b"no")
        result = execute_synthetic_write(config, operation)
        assert result.status is SyntheticExecutionStatus.CONTENDED
        assert not (config.synthetic_root / "contender.bin").exists()
    finally:
        process.stdin.write("continue\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == SyntheticExecutionStatus.COMPLETED.value
        assert process.wait(timeout=5) == 0


def test_stale_plan_prevents_write(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    current = set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("stale.bin"), b"no")
    expected = CoordinationExpectation(
        StatementPlan(PlanAction.RUN_ADJUDICATIONS, 10, Path("statement_010"), "old")
    )

    result = execute_synthetic_write(config, operation, expected=expected)

    assert current.action is PlanAction.RUN_MISSING_REVIEWER
    assert result.status is SyntheticExecutionStatus.STALE_PLAN
    assert not (config.synthetic_root / "stale.bin").exists()


def test_changed_parent_identity_prevents_write(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    parent = config.synthetic_root / "nested"
    parent.mkdir()
    operation = prepare_synthetic_write(config, Path("nested/result.bin"), b"no")
    parent.rename(config.synthetic_root / "old-nested")
    parent.mkdir()

    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.OPERATION_REJECTED
    assert "identity changed" in result.reason
    assert not (parent / "result.bin").exists()


def test_changed_absent_preimage_prevents_overwrite(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"authorized")
    target = config.synthetic_root / "result.bin"
    target.write_bytes(b"intruder")

    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.OPERATION_REJECTED
    assert target.read_bytes() == b"intruder"


@pytest.mark.parametrize("changed", ["active.json", "manifest.json"])
def test_review_or_application_change_during_immediate_check_is_rejected(
    tmp_path: Path, monkeypatch, changed: str
) -> None:
    config = executor_config(tmp_path)
    marker = config.coordinator.workflow.review_cases_root / "statement_010" / changed
    marker.parent.mkdir(parents=True)
    marker.write_text('{"value":1}', encoding="utf-8")
    value = stable_plan()
    calls = 0

    def changing(_):
        nonlocal calls
        calls += 1
        if calls == 3:
            marker.write_text('{"value":2}', encoding="utf-8")
        return value

    monkeypatch.setattr(coordinator_module, "plan_next_statement", changing)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"no")
    result = execute_synthetic_write(config, operation)

    assert calls == 3
    assert result.status is SyntheticExecutionStatus.VALIDATION_FAILED
    assert not (config.synthetic_root / "result.bin").exists()


def test_anchor_replacement_before_effect_is_rejected(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    value = stable_plan()
    calls = 0

    def changing(_):
        nonlocal calls
        calls += 1
        if calls == 3:
            config.coordinator.lease_path.parent.rename(tmp_path / "old-runtime")
            config.coordinator.lease_path.parent.mkdir(mode=0o700)
        return value

    monkeypatch.setattr(coordinator_module, "plan_next_statement", changing)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"no")
    result = execute_synthetic_write(config, operation)

    assert result.status in {
        SyntheticExecutionStatus.VALIDATION_FAILED,
        SyntheticExecutionStatus.EXECUTION_ERROR,
    }
    assert not (config.synthetic_root / "result.bin").exists()


def test_execution_exception_releases_lease_without_retry(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"no")
    calls = 0

    def fail(_):
        nonlocal calls
        calls += 1
        raise OSError("synthetic failure")

    monkeypatch.setattr(executor_module, "_write_synthetic_file", fail)
    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.EXECUTION_ERROR
    assert result.evidence is None
    assert calls == 1
    assert not (config.synthetic_root / "result.bin").exists()
    with WorkflowLease(config.coordinator.lease_path, "after-error"):
        pass


def test_partial_write_reports_possible_effect_without_success_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("partial.bin"), b"payload")

    def write_part_then_fail(descriptor: int, payload: bytes) -> None:
        executor_module.os.write(descriptor, payload[:3])
        raise OSError("controlled partial write")

    monkeypatch.setattr(executor_module, "_write_all", write_part_then_fail)
    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.EXECUTION_ERROR
    assert result.evidence is None
    assert "may have been created or partially written" in result.reason
    assert (config.synthetic_root / "partial.bin").read_bytes() == b"pay"


def test_unexpected_exception_propagates_but_releases_lease(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"no")
    monkeypatch.setattr(
        executor_module,
        "_write_synthetic_file",
        lambda _: (_ for _ in ()).throw(RuntimeError("programming failure")),
    )

    with pytest.raises(RuntimeError, match="programming failure"):
        execute_synthetic_write(config, operation)
    with WorkflowLease(config.coordinator.lease_path, "after-runtime-error"):
        pass


def test_external_and_symbolic_destinations_are_rejected(tmp_path: Path) -> None:
    config = executor_config(tmp_path)
    with pytest.raises(SyntheticOperationRejected, match="confined"):
        prepare_synthetic_write(config, Path("../outside.bin"), b"no")

    external = tmp_path / "external"
    external.mkdir()
    link = config.synthetic_root / "link"
    link.symlink_to(external, target_is_directory=True)
    with pytest.raises(SyntheticOperationRejected, match="symlink"):
        prepare_synthetic_write(config, Path("link/outside.bin"), b"no")

    root_identity = executor_module._identity(config.synthetic_root.lstat())
    forged = SyntheticWriteOperation(
        root=config.synthetic_root,
        relative_path=Path("../forged-outside.bin"),
        payload=b"no",
        root_identity=root_identity,
        parent_identity=root_identity,
    )
    result = execute_synthetic_write(config, forged)
    assert result.status is SyntheticExecutionStatus.OPERATION_REJECTED
    assert not (config.synthetic_root.parent / "forged-outside.bin").exists()


def test_synthetic_root_inside_workflow_container_is_rejected(tmp_path: Path) -> None:
    coordinator = WorkflowCoordinatorConfig(
        workflow_config(tmp_path),
        tmp_path / "runtime" / "workflow.lock",
        "unsafe-root",
        lease_location_confirmed=True,
    )
    unsafe_base = tmp_path / "operational" / "temporary"
    unsafe_root = unsafe_base / "effects"
    unsafe_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="outside the workflow repository container"):
        SyntheticExecutorConfig(
            coordinator,
            unsafe_base,
            unsafe_root,
            temporary_location_confirmed=True,
        )


def test_real_o01_history_and_operational_data_remain_unchanged(tmp_path: Path) -> None:
    config = executor_config(tmp_path)
    add_real_inputs(config.coordinator.workflow)
    before = snapshot(tmp_path / "operational")
    operation = prepare_synthetic_write(config, Path("result.bin"), b"synthetic")

    result = execute_synthetic_write(config, operation)
    after = snapshot(tmp_path / "operational")

    assert result.status is SyntheticExecutionStatus.COMPLETED
    assert result.plan is not None
    assert result.plan.statement_index == 10
    assert result.plan.action is PlanAction.RUN_MISSING_REVIEWER
    assert plan_next_statement(config.coordinator.workflow) == result.plan
    assert before == after
    assert (
        config.coordinator.workflow.review_cases_root
        / "statement_009"
        / "sentinel.bin"
    ).read_bytes() == b"historical"


def test_non_actionable_plan_rejects_operation(tmp_path: Path, monkeypatch) -> None:
    config = executor_config(tmp_path)
    set_stable_planner(monkeypatch, PlanAction.COMPLETE)
    operation = prepare_synthetic_write(config, Path("result.bin"), b"no")

    result = execute_synthetic_write(config, operation)

    assert result.status is SyntheticExecutionStatus.OPERATION_REJECTED
    assert not (config.synthetic_root / "result.bin").exists()
