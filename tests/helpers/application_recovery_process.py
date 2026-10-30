"""OLBM-33 independent process: reconstruct config and optionally crash at a checkpoint."""

import json
import os
import sys
from pathlib import Path

from automation.orchestrator import OrchestratorConfig
from automation.review.synthetic_application import SyntheticApplicationConfig, run_synthetic_application_once
from automation.workflow_coordinator import WorkflowCoordinatorConfig


def main():
    root = Path(sys.argv[1])
    operational = root / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl", operational / "runs", operational / "review_cases",
        operational / "feedback", operational / "profiles",
    )
    config = SyntheticApplicationConfig(
        WorkflowCoordinatorConfig(workflow, root / "runtime/workflow.lock", "application-child", lease_location_confirmed=True),
        root, int(sys.argv[2]), sys.argv[3], fixture_location_confirmed=True,
    )

    def crash(stage):
        if stage == sys.argv[4]:
            os._exit(53)

    result = run_synthetic_application_once(config, failure_injector=crash)
    print(json.dumps({"status": result.status.value, "reason": result.reason}))


if __name__ == "__main__":
    main()
