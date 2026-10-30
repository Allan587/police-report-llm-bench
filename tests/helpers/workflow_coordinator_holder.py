"""Hold a real coordinator lease while a parent process tests contention."""

from __future__ import annotations

import sys
from pathlib import Path

import automation.workflow_coordinator as coordinator_module
from automation.orchestrator import OrchestratorConfig, PlanAction, StatementPlan
from automation.workflow_coordinator import (
    WorkflowCoordinatorConfig,
    coordinate_workflow_cycle,
)


def main() -> None:
    root = Path(sys.argv[1])
    calls = 0

    def controlled_plan(_: OrchestratorConfig) -> StatementPlan:
        nonlocal calls
        calls += 1
        if calls == 1:
            print("planning", flush=True)
            sys.stdin.readline()
        return StatementPlan(PlanAction.COMPLETE, 10, root / "statement_010", "complete")

    coordinator_module.plan_next_statement = controlled_plan
    workflow = OrchestratorConfig(
        statements_path=root / "operational" / "statements.jsonl",
        runs_root=root / "operational" / "runs",
        review_cases_root=root / "operational" / "review_cases",
        feedback_root=root / "operational" / "feedback",
        profiles_root=root / "operational" / "profiles",
    )
    result = coordinate_workflow_cycle(
        WorkflowCoordinatorConfig(
            workflow,
            root / "runtime" / "workflow.lock",
            "holder",
            lease_location_confirmed=True,
        )
    )
    print(result.status.value, flush=True)


if __name__ == "__main__":
    main()
