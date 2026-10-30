"""Sequential synthetic range runner, composing the existing protected stages.

There is no persisted cursor: each stage is selected again by O-01/O-02 from
JSON/JSONL. Each executor acquires the same WorkflowLease and revalidates the
observed expectation before any effect. This module owns no lock or writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from automation.orchestrator import PlanAction, StatementPlan
from automation.workflow_coordinator import (
    CoordinationExpectation, CoordinationStatus, WorkflowCoordinatorConfig,
    coordinate_workflow_cycle,
)
from .path_safety import confined_path
from .synthetic_adjudication import (
    BundleAdjudicatorAdapter, SyntheticAdjudicationConfig,
    SyntheticAdjudicationStatus, run_synthetic_adjudication_and_persist_once,
)
from .synthetic_application import (
    SyntheticApplicationConfig, SyntheticApplicationStatus,
    run_synthetic_application_once, validate_synthetic_fixture,
)
from .vertical_flow import (
    ReviewerAdapter, VerticalReviewConfig, VerticalReviewStatus,
    run_synthetic_reviewer_once,
)


class SyntheticRangeStatus(str, Enum):
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    CONTENDED = "CONTENDED"
    STALE_PLAN = "STALE_PLAN"
    VALIDATION_FAILED = "VALIDATION_FAILED"


@dataclass(frozen=True)
class SyntheticRangeConfig:
    coordinator: WorkflowCoordinatorConfig
    fixture_root: Path
    prompt_path: Path
    fixture_location_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.coordinator, WorkflowCoordinatorConfig):
            raise TypeError("coordinator must be a WorkflowCoordinatorConfig")
        w = self.coordinator.workflow
        w.__post_init__()
        if w.start_statement is None:
            raise ValueError("synthetic range execution requires explicit inclusive bounds")
        root = validate_synthetic_fixture(
            self.coordinator, self.fixture_root, self.fixture_location_confirmed
        )
        prompt = confined_path(self.prompt_path, root, label="synthetic adjudication prompt")
        if not prompt.is_file() or not prompt.read_text(encoding="utf-8").strip():
            raise ValueError("synthetic adjudication prompt must be a non-empty file")
        object.__setattr__(self, "fixture_root", root)
        object.__setattr__(self, "prompt_path", prompt)


@dataclass(frozen=True)
class SyntheticRangeStep:
    statement_index: int
    action: PlanAction
    status: str
    reviewer: Optional[str] = None


@dataclass(frozen=True)
class SyntheticRangeResult:
    status: SyntheticRangeStatus
    reason: str
    plan: Optional[StatementPlan]
    steps: tuple[SyntheticRangeStep, ...] = ()


def run_synthetic_range(
    config: SyntheticRangeConfig,
    reviewer_adapter: ReviewerAdapter,
    adjudicator_adapter: BundleAdjudicatorAdapter,
    *,
    expected: Optional[CoordinationExpectation] = None,
    failure_injector: Optional[Callable[[int, str], None]] = None,
) -> SyntheticRangeResult:
    """Finish the inclusive range or stop at its first concrete blocker.

    Bounds live only in ``config.coordinator.workflow.start_statement`` and
    ``end_statement``. The per-statement application interface is unchanged.
    Hooks are in-memory fault injection for temporary synthetic fixtures only.
    """

    if not isinstance(config, SyntheticRangeConfig):
        raise TypeError("config must be a SyntheticRangeConfig")
    config.__post_init__()
    steps: list[SyntheticRangeStep] = []
    previous_fingerprint = None
    while True:
        coordinated = coordinate_workflow_cycle(config.coordinator, expected=expected)
        expected = None
        plan = coordinated.plan
        if coordinated.status is CoordinationStatus.CURRENT:
            return SyntheticRangeResult(SyntheticRangeStatus.COMPLETE, coordinated.reason, plan, tuple(steps))
        if coordinated.status is not CoordinationStatus.ACTION_REQUIRED:
            return SyntheticRangeResult(
                SyntheticRangeStatus(coordinated.status.value), coordinated.reason, plan, tuple(steps)
            )
        if coordinated.precondition_sha256 == previous_fingerprint:
            return SyntheticRangeResult(
                SyntheticRangeStatus.VALIDATION_FAILED, "stage made no verifiable progress", plan, tuple(steps)
            )
        previous_fingerprint = coordinated.precondition_sha256
        assert plan is not None and plan.statement_index is not None
        index = plan.statement_index

        def checkpoint(stage: str) -> None:
            if failure_injector is not None:
                failure_injector(index, stage)

        checkpoint(f"before_stage:{plan.action.value}")
        review = VerticalReviewConfig(
            config.coordinator, config.fixture_root, statement_index=index,
            fixture_location_confirmed=True,
        )
        expectation = coordinated.expectation()
        reviewer = None
        if plan.action in {PlanAction.RUN_MISSING_REVIEWER, PlanAction.RETRY_REVIEWER}:
            result = run_synthetic_reviewer_once(
                review, reviewer_adapter, expected=expectation,
                publication_failure_injector=checkpoint,
            )
            success = result.status is VerticalReviewStatus.COMPLETED
            reviewer = None if result.task is None else result.task.reviewer.value
        elif plan.action is PlanAction.RUN_ADJUDICATIONS or (
            plan.action is PlanAction.RECOVER_APPLICATION
            and not (plan.statement_path / "adjudication.json").is_file()
        ):
            result = run_synthetic_adjudication_and_persist_once(
                SyntheticAdjudicationConfig(review, config.prompt_path),
                adjudicator_adapter, expected=expectation, failure_injector=checkpoint,
            )
            success = result.status in {
                SyntheticAdjudicationStatus.PERSISTED, SyntheticAdjudicationStatus.UNCHANGED,
            }
        elif plan.action in {
            PlanAction.ADJUDICATION_COMMITTED, PlanAction.RECOVER_APPLICATION, PlanAction.FINALIZE_APPLICATION,
        } and plan.application_path is not None:
            result = run_synthetic_application_once(
                SyntheticApplicationConfig(
                    config.coordinator, config.fixture_root, index, plan.application_path.name,
                    fixture_location_confirmed=True,
                ),
                expected=expectation, failure_injector=checkpoint,
            )
            success = result.status in {SyntheticApplicationStatus.APPLIED, SyntheticApplicationStatus.UNCHANGED}
        else:
            return SyntheticRangeResult(
                SyntheticRangeStatus.BLOCKED, "protected plan has no supported synthetic stage", plan, tuple(steps)
            )
        steps.append(SyntheticRangeStep(index, plan.action, result.status.value, reviewer))
        if not success:
            status = (
                SyntheticRangeStatus(result.status.value)
                if result.status.value in SyntheticRangeStatus._value2member_map_
                else SyntheticRangeStatus.BLOCKED
            )
            return SyntheticRangeResult(status, result.reason, plan, tuple(steps))
        checkpoint(f"after_stage:{plan.action.value}")
        # Never increment a cursor. O-01 reinspects global completion and keeps
        # this same index until application, cleanup and v2 evidence all pass.
