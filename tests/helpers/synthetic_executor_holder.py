from __future__ import annotations

import sys
from pathlib import Path

import automation.synthetic_executor as executor_module
import automation.workflow_coordinator as coordinator_module
from automation.orchestrator import OrchestratorConfig, PlanAction, StatementPlan
from automation.synthetic_executor import (
    SyntheticExecutorConfig,
    execute_synthetic_write,
    prepare_synthetic_write,
)
from automation.workflow_coordinator import WorkflowCoordinatorConfig


def main(root: Path) -> int:
    operational = root / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl",
        operational / "runs",
        operational / "review_cases",
        operational / "feedback",
        operational / "profiles",
    )
    coordinator = WorkflowCoordinatorConfig(
        workflow,
        root / "runtime" / "workflow.lock",
        "synthetic-holder",
        lease_location_confirmed=True,
    )
    temporary_base = root / "temporary"
    synthetic_root = temporary_base / "effects"
    synthetic_root.mkdir(parents=True)
    config = SyntheticExecutorConfig(
        coordinator,
        temporary_base,
        synthetic_root,
        temporary_location_confirmed=True,
    )
    operation = prepare_synthetic_write(config, Path("holder.bin"), b"holder")
    stable = StatementPlan(
        PlanAction.RUN_MISSING_REVIEWER,
        10,
        Path("statement_010"),
        "synthetic holder",
    )
    coordinator_module.plan_next_statement = lambda _: stable
    original = executor_module._write_synthetic_file

    def blocking_write(value):
        print("effect", flush=True)
        if sys.stdin.readline().strip() != "continue":
            raise RuntimeError("missing continuation")
        return original(value)

    executor_module._write_synthetic_file = blocking_write
    result = execute_synthetic_write(config, operation)
    print(result.status.value, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
