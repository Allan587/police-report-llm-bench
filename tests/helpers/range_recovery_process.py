"""OLBM-34 independent Python process; no parent objects or persisted cursor."""

import json
import os
from pathlib import Path
import sys

from automation.orchestrator import OrchestratorConfig
from automation.review.synthetic_range import SyntheticRangeConfig, run_synthetic_range
from automation.review.synthetic_adjudication import DeterministicFakeAdjudicatorAdapter
from automation.review.vertical_flow import DeterministicFakeReviewerAdapter
from automation.workflow_coordinator import WorkflowCoordinatorConfig


def main():
    root = Path(sys.argv[1])
    operational = root / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl", operational / "runs", operational / "review_cases",
        operational / "feedback", operational / "profiles",
        start_statement=int(sys.argv[2]), end_statement=int(sys.argv[3]),
    )
    config = SyntheticRangeConfig(
        WorkflowCoordinatorConfig(workflow, root / "runtime/workflow.lock", "range-child", lease_location_confirmed=True),
        root, operational / "prompts/review_adjudication.md", fixture_location_confirmed=True,
    )
    reviews, adjudications = [], []

    class Reviewer(DeterministicFakeReviewerAdapter):
        def execute(self, request):
            reviews.append([request.inputs.statement_index, request.task.reviewer.value])
            return super().execute(request)

    class Adjudicator(DeterministicFakeAdjudicatorAdapter):
        def execute_bundle(self, request):
            adjudications.append(request.statement_index)
            return super().execute_bundle(request)

    def crash(index, stage):
        target = sys.argv[5]
        matches = stage == target or (
            target == "adjudication_prepared" and stage == "after_prepare" and index in adjudications
        )
        if index == int(sys.argv[4]) and matches:
            print(json.dumps({"reviewers": reviews, "adjudications": adjudications}), flush=True)
            os._exit(63)

    result = run_synthetic_range(config, Reviewer(), Adjudicator(), failure_injector=crash)
    print(json.dumps({
        "status": result.status.value, "reason": result.reason,
        "reviewers": reviews, "adjudications": adjudications,
        "steps": [[s.statement_index, s.action.value, s.status] for s in result.steps],
    }))


if __name__ == "__main__":
    main()
