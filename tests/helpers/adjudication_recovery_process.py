"""Independent child process for synthetic adjudication recovery tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from automation.orchestrator import OrchestratorConfig, plan_next_statement
from automation.review.synthetic_adjudication import (
    DeterministicFakeAdjudicatorAdapter,
    SyntheticAdjudicationConfig,
    run_synthetic_adjudication_and_persist_once,
    run_synthetic_three_reviewers_and_persist,
)
from automation.review.vertical_flow import (
    DeterministicFakeReviewerAdapter,
    VerticalReviewConfig,
    run_synthetic_reviewer_once,
)
from automation.workflow_coordinator import WorkflowCoordinatorConfig
from automation.workflow_lease import WorkflowLease


class CountingReviewer(DeterministicFakeReviewerAdapter):
    calls = 0

    def execute(self, request):
        self.calls += 1
        return super().execute(request)


class CountingAdjudicator(DeterministicFakeAdjudicatorAdapter):
    calls = 0

    def execute_bundle(self, request):
        self.calls += 1
        return super().execute_bundle(request)


def main() -> None:
    fixture_root = Path(sys.argv[1])
    mode = sys.argv[2]
    operational = fixture_root / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl",
        operational / "runs",
        operational / "review_cases",
        operational / "feedback",
        operational / "profiles",
    )
    coordinator = WorkflowCoordinatorConfig(
        workflow,
        fixture_root / "runtime/workflow.lock",
        "adjudication-recovery-child",
        lease_location_confirmed=True,
    )
    config = SyntheticAdjudicationConfig(
        VerticalReviewConfig(coordinator, fixture_root, fixture_location_confirmed=True),
        operational / "prompts/review_adjudication.md",
    )
    if mode == "hold":
        lease = WorkflowLease(coordinator.lease_path, coordinator.owner_id)
        lease.acquire()
        (fixture_root / "holder_ready").write_text("ready", encoding="utf-8")
        while True:
            time.sleep(1)
    if mode == "partial_reviews":
        for _ in range(2):
            result = run_synthetic_reviewer_once(
                config.review, DeterministicFakeReviewerAdapter()
            )
            assert result.status.value == "COMPLETED"
        os._exit(46)

    def interrupt(stage: str) -> None:
        exit_codes = {
            "after_prepare": 43,
            "after_replace:1:adjudication": 44,
            "after_adjudication_commit": 45,
        }
        expected = {
            "before_commit": "after_prepare",
            "after_replace": "after_replace:1:adjudication",
            "after_commit": "after_adjudication_commit",
        }.get(mode)
        if stage == expected:
            os._exit(exit_codes[stage])

    reviewer = CountingReviewer()
    adjudicator = CountingAdjudicator()
    if mode in {"before_commit", "after_replace", "after_commit"}:
        result = run_synthetic_adjudication_and_persist_once(
            config, adjudicator, failure_injector=interrupt
        )
    else:
        flow = run_synthetic_three_reviewers_and_persist(config, reviewer, adjudicator)
        result = flow.adjudication
    plan = plan_next_statement(workflow)
    (fixture_root / "child_result.json").write_text(
        json.dumps(
            {
                "status": result.status.value,
                "reason": result.reason,
                "reviewer_calls": reviewer.calls,
                "adjudicator_calls": adjudicator.calls,
                "plan": plan.action.value,
                "statement_index": plan.statement_index,
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
