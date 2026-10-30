"""First lease-protected vertical review flow using a replaceable reviewer port.

Each call handles one synthetic statement and one reviewer attempt. Without
explicit protected bounds the original statement_010 restriction remains.
It reuses O-01, O-02, the existing seven-artifact contracts and the
immutable review-set writer.  It does not adjudicate or touch accumulated
profiles and histories.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

from automation.contracts import (
    AttemptStatus,
    BenchmarkModel,
    CompleteReviewResult,
    ExecutionAttempt,
    HistoryProposalOutput,
    MainReviewOutput,
    ProfileProposalOutput,
    ReviewOperation,
    Reviewer,
    Task,
    TaskError,
    TaskStatus,
    TaskType,
    utc_now,
)
from automation.orchestrator import PlanAction, StatementPlan, plan_next_statement
from automation.state_machine import transition
from automation.workflow_coordinator import (
    CoordinationExpectation,
    CoordinationStatus,
    WorkflowCoordinatorConfig,
    confirm_workflow_expectation_under_lease,
    coordinate_workflow_under_lease,
)
from automation.workflow_lease import (
    LeaseOwner,
    WorkflowLease,
    WorkflowLeaseError,
    WorkflowLeaseInUseError,
)

from .input_loader import ReviewInputValidation, inspect_review_inputs
from .result_inspector import ReviewerStatus, StatementReviewInspection, inspect_reviewer_results
from .review_storage import (
    FailureInjector,
    RecoveredReviewAttempt,
    ReviewAttemptMetadata,
    ReviewPublishResult,
    ReviewPublishStatus,
    publish_complete_review,
    resolve_confirmed_review_set,
    resolve_recoverable_unconfirmed_attempt,
)


class VerticalReviewStatus(str, Enum):
    COMPLETED = "COMPLETED"
    UNCHANGED = "UNCHANGED"
    CONTENDED = "CONTENDED"
    STALE_PLAN = "STALE_PLAN"
    BLOCKED = "BLOCKED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    ADAPTER_FAILED = "ADAPTER_FAILED"
    OUTPUT_REJECTED = "OUTPUT_REJECTED"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"


class ReviewerAdapterError(RuntimeError):
    """Expected, sanitized failure from a reviewer adapter."""


@dataclass(frozen=True)
class VerticalReviewConfig:
    coordinator: WorkflowCoordinatorConfig
    fixture_root: Path
    experiment_id: str = "synthetic-vertical-review"
    prompt_version: str = "synthetic-main-review-v1"
    statement_index: int = 10
    fixture_location_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.coordinator, WorkflowCoordinatorConfig):
            raise TypeError("coordinator must be a WorkflowCoordinatorConfig")
        workflow = self.coordinator.workflow
        if (
            not isinstance(self.statement_index, int)
            or isinstance(self.statement_index, bool)
            or self.statement_index < 10
        ):
            raise ValueError("statement_index must be an integer >= 10")
        if workflow.start_statement is None:
            if self.statement_index != 10:
                raise ValueError("review beyond statement_010 requires explicit protected bounds")
        elif not workflow.start_statement <= self.statement_index <= workflow.end_statement:
            raise ValueError("statement_index is outside the configured range")
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError("experiment_id must be a non-empty string")
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise ValueError("prompt_version must be a non-empty string")
        if not self.fixture_location_confirmed:
            raise ValueError("synthetic fixture location requires explicit confirmation")
        root = _absolute(Path(self.fixture_root))
        _require_plain_directory(root, "fixture_root")
        workflow = self.coordinator.workflow
        scoped_paths = (
            workflow.statements_path,
            workflow.runs_root,
            workflow.review_cases_root,
            workflow.feedback_root,
            workflow.profiles_root,
            self.coordinator.lease_path,
        )
        if any(not _is_relative_to(_absolute(Path(path)), root) for path in scoped_paths):
            raise ValueError("all synthetic workflow and lease paths must be inside fixture_root")
        object.__setattr__(self, "fixture_root", root)


@dataclass(frozen=True)
class ReviewerExecutionRequest:
    plan: StatementPlan
    inputs: ReviewInputValidation
    task: Task
    attempt: ExecutionAttempt

    def __post_init__(self) -> None:
        if self.plan.action not in {
            PlanAction.RUN_MISSING_REVIEWER,
            PlanAction.RETRY_REVIEWER,
        }:
            raise ValueError("review execution requires a reviewer plan")
        if not self.inputs.inputs_complete:
            raise ValueError("review execution requires complete aligned inputs")
        if self.task.status is not TaskStatus.RUNNING:
            raise ValueError("review task must be RUNNING")
        if self.attempt.status is not AttemptStatus.RUNNING:
            raise ValueError("review attempt must be RUNNING")
        if self.attempt.task_id != self.task.task_id:
            raise ValueError("review attempt does not belong to the task")
        if (
            self.task.statement_index != self.inputs.statement_index
            or self.plan.statement_index != self.inputs.statement_index
        ):
            raise ValueError("review request mixes statement identities")
        if self.task.reviewer not in self.plan.reviewers:
            raise ValueError("reviewer is not pending in the protected plan")


class ReviewerAdapter(Protocol):
    def execute(self, request: ReviewerExecutionRequest) -> CompleteReviewResult:
        """Return all seven outputs for exactly one reviewer attempt."""


class DeterministicFakeReviewerAdapter:
    """Offline adapter used only by synthetic tests and local demonstrations."""

    def __init__(self, *, marker: str = "synthetic") -> None:
        if not isinstance(marker, str) or not marker:
            raise ValueError("marker must be a non-empty string")
        self.marker = marker

    def execute(self, request: ReviewerExecutionRequest) -> CompleteReviewResult:
        identity = {
            "task_id": request.task.task_id,
            "attempt_id": request.attempt.attempt_id,
            "statement_index": request.inputs.statement_index,
            "reviewer": request.task.reviewer.value,
        }
        main = MainReviewOutput(
            request.inputs.statement_index,
            request.task.reviewer,
            request.attempt.attempt_id,
            {
                **identity,
                "model_reviews": [
                    {
                        "model_name": model.value,
                        "factuality": {"status": "pass", "findings": [self.marker]},
                        "hallucinations": [],
                        "omissions": [],
                        "narrative_quality": {"status": "pass", "findings": []},
                        "crime_classification": {
                            "model_category": "synthetic",
                            "assessment": "correct",
                            "proposed_category_cr": "synthetic",
                            "justification": "synthetic fixture",
                        },
                        "useful_elements": [],
                    }
                    for model in BenchmarkModel
                ],
                "proposed_ideal_report": {
                    "narrative": self.marker,
                    "closing": "synthetic closing",
                    "crime_classification": {
                        "category": "synthetic",
                        "justification": "synthetic fixture",
                    },
                },
            },
        )
        histories = tuple(
            HistoryProposalOutput(
                request.inputs.statement_index,
                request.task.reviewer,
                request.attempt.attempt_id,
                model,
                {
                    **identity,
                    "model": model.value,
                    "output": request.inputs.statement_index,
                    "date": "21/10/2026",
                    "observations": [self.marker],
                },
            )
            for model in BenchmarkModel
        )
        profiles = tuple(
            ProfileProposalOutput(
                request.inputs.statement_index,
                request.task.reviewer,
                request.attempt.attempt_id,
                model,
                {
                    **identity,
                    "model_name": model.value,
                    "new_strengths": [self.marker],
                    "new_weaknesses": [],
                    "new_hallucination_patterns": [],
                    "new_classification_patterns": [],
                    "new_narrative_patterns": [],
                    "new_factuality_patterns": [],
                    "new_finetuning_priorities": [],
                },
            )
            for model in BenchmarkModel
        )
        return CompleteReviewResult(
            task_id=request.task.task_id,
            attempt_id=request.attempt.attempt_id,
            statement_index=request.inputs.statement_index,
            reviewer=request.task.reviewer,
            main_review=main,
            histories=histories,
            profile_proposals=profiles,
            created_at=utc_now(),
        )


@dataclass(frozen=True)
class VerticalReviewResult:
    status: VerticalReviewStatus
    reason: str
    before_plan: Optional[StatementPlan] = None
    after_plan: Optional[StatementPlan] = None
    task: Optional[Task] = None
    attempt: Optional[ExecutionAttempt] = None
    inputs: Optional[ReviewInputValidation] = None
    publication: Optional[ReviewPublishResult] = None
    inspection: Optional[StatementReviewInspection] = None
    lease_owner: Optional[LeaseOwner] = None


def run_synthetic_reviewer_once(
    config: VerticalReviewConfig,
    adapter: ReviewerAdapter,
    *,
    expected: Optional[CoordinationExpectation] = None,
    task: Optional[Task] = None,
    previous_attempt: Optional[ExecutionAttempt] = None,
    publication_failure_injector: Optional[FailureInjector] = None,
) -> VerticalReviewResult:
    """Execute and publish one complete synthetic reviewer set under one lease."""

    if not isinstance(config, VerticalReviewConfig):
        raise TypeError("config must be a VerticalReviewConfig")
    if not callable(getattr(adapter, "execute", None)):
        raise TypeError("adapter must implement execute(request)")
    lease = WorkflowLease(config.coordinator.lease_path, config.coordinator.owner_id)
    try:
        owner = lease.acquire(blocking=False)
    except WorkflowLeaseInUseError as error:
        return VerticalReviewResult(
            VerticalReviewStatus.CONTENDED, str(error), lease_owner=error.owner
        )
    except WorkflowLeaseError as error:
        return VerticalReviewResult(VerticalReviewStatus.VALIDATION_FAILED, str(error))

    result: Optional[VerticalReviewResult] = None
    try:
        coordinated = coordinate_workflow_under_lease(
            config.coordinator, lease, expected=expected
        )
        if coordinated.status is CoordinationStatus.STALE_PLAN:
            result = _coordination_result(
                VerticalReviewStatus.STALE_PLAN, coordinated.reason, coordinated.plan, owner
            )
        elif coordinated.status is CoordinationStatus.BLOCKED:
            result = _coordination_result(
                VerticalReviewStatus.BLOCKED, coordinated.reason, coordinated.plan, owner
            )
        elif coordinated.status is not CoordinationStatus.ACTION_REQUIRED:
            result = _coordination_result(
                VerticalReviewStatus.VALIDATION_FAILED,
                coordinated.reason,
                coordinated.plan,
                owner,
            )
        elif task is not None and task.status is TaskStatus.COMPLETED:
            result = _completed_replay(config, task, previous_attempt, coordinated.plan, owner)
        else:
            result = _execute_current_plan(
                config,
                adapter,
                lease,
                owner,
                coordinated.plan,
                coordinated.expectation(),
                task,
                previous_attempt,
                publication_failure_injector,
            )
    finally:
        try:
            lease.release()
        except WorkflowLeaseError as error:
            result = VerticalReviewResult(
                VerticalReviewStatus.VALIDATION_FAILED,
                f"workflow lease could not be released safely: {error}",
                before_plan=None if result is None else result.before_plan,
                after_plan=None if result is None else result.after_plan,
                task=None if result is None else result.task,
                attempt=None if result is None else result.attempt,
                inputs=None if result is None else result.inputs,
                publication=None if result is None else result.publication,
                inspection=None if result is None else result.inspection,
                lease_owner=owner,
            )
    assert result is not None
    return result


def _execute_current_plan(
    config: VerticalReviewConfig,
    adapter: ReviewerAdapter,
    lease: WorkflowLease,
    owner: LeaseOwner,
    plan: Optional[StatementPlan],
    expectation: CoordinationExpectation,
    task: Optional[Task],
    previous_attempt: Optional[ExecutionAttempt],
    failure_injector: Optional[FailureInjector],
) -> VerticalReviewResult:
    if (
        plan is None
        or plan.action not in {PlanAction.RUN_MISSING_REVIEWER, PlanAction.RETRY_REVIEWER}
        or plan.statement_index != config.statement_index
        or plan.statement_path is None
        or not plan.reviewers
    ):
        return VerticalReviewResult(
            VerticalReviewStatus.BLOCKED,
            "protected plan does not authorize this statement reviewer",
            before_plan=plan,
            lease_owner=owner,
        )
    reviewer = task.reviewer if task is not None else plan.reviewers[0]
    if reviewer not in plan.reviewers:
        return VerticalReviewResult(
            VerticalReviewStatus.STALE_PLAN,
            "requested reviewer is no longer pending",
            before_plan=plan,
            task=task,
            attempt=previous_attempt,
            lease_owner=owner,
        )
    inputs = inspect_review_inputs(
        statement_index=config.statement_index,
        statements_path=config.coordinator.workflow.statements_path,
        runs_root=config.coordinator.workflow.runs_root,
        run=config.coordinator.workflow.run,
    )
    if not inputs.inputs_complete or inputs.selected_run_id != plan.run_id:
        return VerticalReviewResult(
            VerticalReviewStatus.VALIDATION_FAILED,
            "review inputs are incomplete or no longer match the protected run",
            before_plan=plan,
            task=task,
            attempt=previous_attempt,
            inputs=inputs,
            lease_owner=owner,
        )
    recovered_attempt = None
    if plan.action is PlanAction.RETRY_REVIEWER:
        recovered_attempt, recovery_error = resolve_recoverable_unconfirmed_attempt(
            statement_path=plan.statement_path,
            statement_index=config.statement_index,
            reviewer=reviewer,
        )
        if recovered_attempt is None:
            return VerticalReviewResult(
                VerticalReviewStatus.BLOCKED,
                recovery_error
                or "retry plan has no unambiguous persistent attempt identity",
                before_plan=plan,
                inputs=inputs,
                lease_owner=owner,
            )
        expected_task = task if task is not None else _new_task(config, reviewer)
        if (
            recovered_attempt.run_id != inputs.selected_run_id
            or recovered_attempt.task_deduplication_key
            != expected_task.deduplication_key
        ):
            return VerticalReviewResult(
                VerticalReviewStatus.BLOCKED,
                "persistent retry identity contradicts the selected run or logical task",
                before_plan=plan,
                inputs=inputs,
                lease_owner=owner,
            )
        if task is not None:
            if (
                previous_attempt is None
                or recovered_attempt.task_id != task.task_id
                or recovered_attempt.attempt_id != previous_attempt.attempt_id
                or recovered_attempt.attempt_number != previous_attempt.attempt_number
            ):
                return VerticalReviewResult(
                    VerticalReviewStatus.BLOCKED,
                    "in-memory retry identity contradicts the persisted attempt",
                    before_plan=plan,
                    task=task,
                    attempt=previous_attempt,
                    inputs=inputs,
                    lease_owner=owner,
                )
    logical_task, attempt = _start_attempt(
        config,
        reviewer,
        task,
        previous_attempt,
        recovered_attempt if task is None else None,
    )
    request = ReviewerExecutionRequest(plan, inputs, logical_task, attempt)
    try:
        candidate = adapter.execute(request)
    except ReviewerAdapterError:
        return _failed_attempt(
            VerticalReviewStatus.ADAPTER_FAILED,
            "synthetic reviewer adapter failed before producing a complete result",
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_adapter_failed",
        )
    try:
        validated = _validate_candidate(candidate, request)
    except (TypeError, ValueError) as error:
        return _failed_attempt(
            VerticalReviewStatus.OUTPUT_REJECTED,
            f"reviewer output was rejected: {error}",
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_output_rejected",
        )

    immediate = confirm_workflow_expectation_under_lease(
        config.coordinator, lease, expectation
    )
    if immediate.status is CoordinationStatus.STALE_PLAN:
        return _failed_attempt(
            VerticalReviewStatus.STALE_PLAN,
            immediate.reason,
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_plan_stale",
        )
    if immediate.status is not CoordinationStatus.ACTION_REQUIRED:
        return _failed_attempt(
            VerticalReviewStatus.VALIDATION_FAILED,
            immediate.reason,
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_revalidation_failed",
        )
    if (
        immediate.plan != plan
        or reviewer not in immediate.plan.reviewers
        or immediate.plan.run_id != inputs.selected_run_id
    ):
        return _failed_attempt(
            VerticalReviewStatus.STALE_PLAN,
            "review plan changed immediately before publication",
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_plan_stale",
        )

    try:
        publication = publish_complete_review(
            validated,
            statement_path=plan.statement_path,
            attempt_metadata=ReviewAttemptMetadata(
                attempt_number=attempt.attempt_number,
                run_id=inputs.selected_run_id,
                task_deduplication_key=logical_task.deduplication_key,
            ),
            failure_injector=failure_injector,
        )
    except (OSError, TypeError, ValueError) as error:
        return _failed_attempt(
            VerticalReviewStatus.PUBLICATION_FAILED,
            f"review publication failed: {error}",
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_publication_failed",
        )
    lease.validate()
    inspection = inspect_reviewer_results(
        statement_index=config.statement_index,
        review_cases_root=config.coordinator.workflow.review_cases_root,
    )
    confirmed = inspection.reviewers[reviewer]
    if (
        confirmed.status is not ReviewerStatus.COMPLETE
        or confirmed.complete_result is None
        or confirmed.complete_result.task_id != logical_task.task_id
        or confirmed.complete_result.attempt_id != attempt.attempt_id
    ):
        return _failed_attempt(
            VerticalReviewStatus.PUBLICATION_FAILED,
            "published reviewer set did not pass independent reinspection",
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_reinspection_failed",
            publication=publication,
            inspection=inspection,
        )
    after_plan = plan_next_statement(config.coordinator.workflow)
    remaining_reviewers = (
        after_plan.action in {PlanAction.RUN_MISSING_REVIEWER, PlanAction.RETRY_REVIEWER}
        and after_plan.statement_index == config.statement_index
        and reviewer not in after_plan.reviewers
        and bool(after_plan.reviewers)
        and not inspection.adjudication_enabled
    )
    adjudication_barrier_reached = (
        after_plan.action is PlanAction.RUN_ADJUDICATIONS
        and after_plan.statement_index == config.statement_index
        and not after_plan.reviewers
        and inspection.adjudication_enabled
        and set(inspection.reviewers) == set(Reviewer)
        and all(
            item.status is ReviewerStatus.COMPLETE
            and item.complete_result is not None
            for item in inspection.reviewers.values()
        )
    )
    if not (remaining_reviewers or adjudication_barrier_reached):
        return _failed_attempt(
            VerticalReviewStatus.PUBLICATION_FAILED,
            "O-01 did not recognize exactly one completed reviewer",
            plan,
            logical_task,
            attempt,
            inputs,
            owner,
            "review_replan_failed",
            publication=publication,
            inspection=inspection,
            after_plan=after_plan,
        )
    finished_at = utc_now()
    succeeded_attempt = replace(
        attempt, status=AttemptStatus.SUCCEEDED, finished_at=finished_at
    )
    completed_task = transition(
        logical_task, TaskStatus.COMPLETED, occurred_at=finished_at
    ).task
    return VerticalReviewResult(
        VerticalReviewStatus.COMPLETED,
        (
            "one complete reviewer set was published and all three reviewers are ready for adjudication"
            if adjudication_barrier_reached
            else "one complete reviewer set was published and O-01 requests the remaining reviewers"
        ),
        before_plan=plan,
        after_plan=after_plan,
        task=completed_task,
        attempt=succeeded_attempt,
        inputs=inputs,
        publication=publication,
        inspection=inspection,
        lease_owner=owner,
    )


def _start_attempt(
    config: VerticalReviewConfig,
    reviewer: Reviewer,
    task: Optional[Task],
    previous_attempt: Optional[ExecutionAttempt],
    recovered_attempt: Optional[RecoveredReviewAttempt] = None,
) -> tuple[Task, ExecutionAttempt]:
    if task is None:
        if previous_attempt is not None:
            raise ValueError("previous_attempt requires an existing task")
        task = _new_task(config, reviewer)
        if recovered_attempt is None:
            attempt_number = 1
        else:
            if (
                recovered_attempt.statement_index != config.statement_index
                or recovered_attempt.reviewer is not reviewer
                or recovered_attempt.task_deduplication_key
                != task.deduplication_key
            ):
                raise ValueError("recovered attempt identity contradicts the logical task")
            task = replace(
                task,
                task_id=recovered_attempt.task_id,
                status=TaskStatus.RETRY_PENDING,
            )
            attempt_number = recovered_attempt.attempt_number + 1
    else:
        if recovered_attempt is not None:
            raise ValueError("persistent recovery cannot be combined with in-memory retry")
        _validate_task(config, reviewer, task)
        if task.status is not TaskStatus.RETRY_PENDING:
            raise ValueError("an existing unfinished task must be RETRY_PENDING")
        if (
            previous_attempt is None
            or previous_attempt.task_id != task.task_id
            or previous_attempt.status is not AttemptStatus.FAILED
        ):
            raise ValueError("retry requires the prior failed attempt")
        attempt_number = previous_attempt.attempt_number + 1
    running = transition(task, TaskStatus.RUNNING).task
    return running, ExecutionAttempt.start(running.task_id, attempt_number)


def _new_task(config: VerticalReviewConfig, reviewer: Reviewer) -> Task:
    return Task.create(
        experiment_id=config.experiment_id,
        task_type=TaskType.EVALUATION,
        case_id=f"statement_{config.statement_index:03d}",
        provider="synthetic",
        model=reviewer.value,
        prompt_version=config.prompt_version,
        statement_index=config.statement_index,
        reviewer=reviewer,
        review_operation=ReviewOperation.MAIN_REVIEW,
    )


def _validate_task(config: VerticalReviewConfig, reviewer: Reviewer, task: Task) -> None:
    if not isinstance(task, Task):
        raise TypeError("task must be a Task")
    if (
        task.statement_index != config.statement_index
        or task.reviewer is not reviewer
        or task.review_operation is not ReviewOperation.MAIN_REVIEW
        or task.task_type is not TaskType.EVALUATION
        or task.experiment_id != config.experiment_id
        or task.prompt_version != config.prompt_version
    ):
        raise ValueError("task identity does not match the selected synthetic review")


def _validate_candidate(
    candidate: object, request: ReviewerExecutionRequest
) -> CompleteReviewResult:
    if not isinstance(candidate, CompleteReviewResult):
        raise TypeError("adapter must return CompleteReviewResult")
    validated = CompleteReviewResult(
        task_id=candidate.task_id,
        attempt_id=candidate.attempt_id,
        statement_index=candidate.statement_index,
        reviewer=candidate.reviewer,
        main_review=candidate.main_review,
        histories=candidate.histories,
        profile_proposals=candidate.profile_proposals,
        created_at=candidate.created_at,
    )
    if (
        validated.task_id != request.task.task_id
        or validated.attempt_id != request.attempt.attempt_id
        or validated.statement_index != request.inputs.statement_index
        or validated.reviewer is not request.task.reviewer
    ):
        raise ValueError("adapter result identity does not match its request")
    validated.as_task_result()
    return validated


def _failed_attempt(
    status: VerticalReviewStatus,
    reason: str,
    plan: StatementPlan,
    task: Task,
    attempt: ExecutionAttempt,
    inputs: ReviewInputValidation,
    owner: LeaseOwner,
    error_code: str,
    *,
    publication: Optional[ReviewPublishResult] = None,
    inspection: Optional[StatementReviewInspection] = None,
    after_plan: Optional[StatementPlan] = None,
) -> VerticalReviewResult:
    finished_at = utc_now()
    error = TaskError(error_code, reason, retryable=True)
    failed_attempt = replace(
        attempt,
        status=AttemptStatus.FAILED,
        finished_at=finished_at,
        error=error,
    )
    retry_task = transition(task, TaskStatus.RETRY_PENDING, occurred_at=finished_at).task
    return VerticalReviewResult(
        status,
        reason,
        before_plan=plan,
        after_plan=after_plan,
        task=retry_task,
        attempt=failed_attempt,
        inputs=inputs,
        publication=publication,
        inspection=inspection,
        lease_owner=owner,
    )


def _completed_replay(
    config: VerticalReviewConfig,
    task: Task,
    attempt: Optional[ExecutionAttempt],
    plan: Optional[StatementPlan],
    owner: LeaseOwner,
) -> VerticalReviewResult:
    if (
        attempt is None
        or attempt.task_id != task.task_id
        or attempt.status is not AttemptStatus.SUCCEEDED
        or task.statement_index != config.statement_index
        or task.reviewer is None
    ):
        return VerticalReviewResult(
            VerticalReviewStatus.VALIDATION_FAILED,
            "completed replay requires its successful task and attempt",
            before_plan=plan,
            task=task,
            attempt=attempt,
            lease_owner=owner,
        )
    confirmed, error = resolve_confirmed_review_set(
        statement_path=config.coordinator.workflow.review_cases_root
        / f"statement_{config.statement_index:03d}",
        statement_index=config.statement_index,
        reviewer=task.reviewer,
    )
    if (
        confirmed is None
        or confirmed.task_id != task.task_id
        or confirmed.attempt_id != attempt.attempt_id
    ):
        return VerticalReviewResult(
            VerticalReviewStatus.VALIDATION_FAILED,
            f"completed replay has no matching confirmed set: {error or 'identity mismatch'}",
            before_plan=plan,
            task=task,
            attempt=attempt,
            lease_owner=owner,
        )
    inspection = inspect_reviewer_results(
        statement_index=config.statement_index,
        review_cases_root=config.coordinator.workflow.review_cases_root,
    )
    return VerticalReviewResult(
        VerticalReviewStatus.UNCHANGED,
        "the completed attempt is already the active immutable reviewer set",
        before_plan=plan,
        after_plan=plan,
        task=task,
        attempt=attempt,
        publication=ReviewPublishResult(
            ReviewPublishStatus.UNCHANGED,
            confirmed,
            config.coordinator.workflow.review_cases_root
            / f"statement_{config.statement_index:03d}"
            / f"{task.reviewer.value}_review.json",
        ),
        inspection=inspection,
        lease_owner=owner,
    )


def _coordination_result(
    status: VerticalReviewStatus,
    reason: str,
    plan: Optional[StatementPlan],
    owner: LeaseOwner,
) -> VerticalReviewResult:
    return VerticalReviewResult(status, reason, before_plan=plan, lease_owner=owner)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_plain_directory(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        inspected = current.lstat()
        if stat.S_ISLNK(inspected.st_mode):
            raise ValueError(f"{label} must not contain symbolic links: {current}")
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f"{label} must be a directory")


__all__ = [
    "DeterministicFakeReviewerAdapter",
    "ReviewerAdapter",
    "ReviewerAdapterError",
    "ReviewerExecutionRequest",
    "VerticalReviewConfig",
    "VerticalReviewResult",
    "VerticalReviewStatus",
    "run_synthetic_reviewer_once",
]
