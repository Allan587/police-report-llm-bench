"""Durable operator handoff for the existing synthetic reviewer vertical.

The request JSON lives with its statement. Published v2 sets remain authoritative;
this file only preserves the identity needed before an operator supplies a package.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from automation.contracts import AttemptStatus, ExecutionAttempt, Reviewer, TaskStatus
from automation.orchestrator import PlanAction, StatementPlan, plan_next_statement
from automation.workflow_coordinator import (
    CoordinationStatus, coordinate_workflow_under_lease,
)
from automation.workflow_lease import WorkflowLease

from .input_loader import inspect_review_inputs
from .manual_import_adapter import (
    MANUAL_REVIEW_SLOTS, ManualImportReviewerAdapter, ManualReviewImportError,
)
from .path_safety import confined_path
from .result_inspector import ReviewerStatus, inspect_reviewer_results
from .review_storage import FailureInjector, resolve_recoverable_unconfirmed_attempt
from .vertical_flow import (
    VerticalReviewConfig, VerticalReviewResult, VerticalReviewStatus,
    _new_task, _start_attempt, run_synthetic_reviewer_once,
)


class ManualSupervisorError(ValueError):
    """Stable operator-facing error without package content."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ManualRequest:
    statement_index: int
    run_id: str
    reviewer: Reviewer
    task_id: str
    attempt_id: str
    attempt_number: int
    task_deduplication_key: str
    last_error: str | None = None

    def as_dict(self) -> dict:
        return {
            "schema_version": 1,
            "statement_index": self.statement_index,
            "run_id": self.run_id,
            "reviewer": self.reviewer.value,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "task_deduplication_key": self.task_deduplication_key,
            "last_error": self.last_error,
        }


def _request_path(config: VerticalReviewConfig, reviewer: Reviewer) -> Path:
    root = config.coordinator.workflow.review_cases_root
    return confined_path(
        root / f"statement_{config.statement_index:03d}"
        / f"manual_request_{reviewer.value}.json",
        config.fixture_root, label="manual request",
    )


def _read_request(config: VerticalReviewConfig, reviewer: Reviewer) -> ManualRequest | None:
    path = _request_path(config, reviewer)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ManualSupervisorError("unsafe_request")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version", "statement_index", "run_id", "reviewer", "task_id",
            "attempt_id", "attempt_number", "task_deduplication_key", "last_error",
        } or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ValueError
        request = ManualRequest(
            raw["statement_index"], raw["run_id"], Reviewer(raw["reviewer"]),
            raw["task_id"], raw["attempt_id"], raw["attempt_number"],
            raw["task_deduplication_key"], raw["last_error"],
        )
        task = _new_task(config, reviewer)
        if (request.statement_index != config.statement_index
                or request.reviewer is not reviewer
                or request.task_deduplication_key != task.deduplication_key
                or type(request.attempt_number) is not int
                or request.attempt_number < 1
                or (request.last_error is not None
                    and (not isinstance(request.last_error, str)
                         or not request.last_error.isascii()
                         or not request.last_error.replace("_", "").isalnum()
                         or len(request.last_error) > 80))
                or not all(isinstance(value, str) and value for value in (
                    request.run_id, request.task_id, request.attempt_id))):
            raise ValueError
        return request
    except (OSError, ValueError, TypeError, KeyError):
        raise ManualSupervisorError("invalid_request") from None


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ManualSupervisorError("unsafe_request")
    descriptor, temporary = tempfile.mkstemp(prefix=".manual-request-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_manual_request(config: VerticalReviewConfig) -> ManualRequest:
    """Persist exactly one pending identity for the next O-01 reviewer."""
    lease = WorkflowLease(config.coordinator.lease_path, config.coordinator.owner_id)
    lease.acquire(blocking=False)
    try:
        coordinated = coordinate_workflow_under_lease(config.coordinator, lease)
        plan = coordinated.plan
        if (coordinated.status is not CoordinationStatus.ACTION_REQUIRED
                or plan is None
                or plan.action not in {PlanAction.RUN_MISSING_REVIEWER, PlanAction.RETRY_REVIEWER}
                or plan.statement_index != config.statement_index
                or not plan.reviewers or not plan.run_id):
            raise ManualSupervisorError("review_not_pending")
        reviewer = plan.reviewers[0]
        existing = _read_request(config, reviewer)
        recovered = None
        if plan.action is PlanAction.RETRY_REVIEWER:
            recovered, error = resolve_recoverable_unconfirmed_attempt(
                statement_path=plan.statement_path,
                statement_index=config.statement_index, reviewer=reviewer)
            if recovered is None:
                raise ManualSupervisorError("retry_identity_unverified")
        if existing is not None:
            if existing.run_id != plan.run_id:
                raise ManualSupervisorError("request_run_mismatch")
            if recovered is None or (
                existing.task_id == recovered.task_id
                and existing.attempt_number == recovered.attempt_number + 1
            ):
                return existing
            if (existing.task_id != recovered.task_id
                    or existing.attempt_id != recovered.attempt_id
                    or existing.attempt_number != recovered.attempt_number):
                raise ManualSupervisorError("retry_identity_unverified")
        task, attempt = _start_attempt(config, reviewer, None, None, recovered)
        request = ManualRequest(config.statement_index, plan.run_id, reviewer,
                                task.task_id, attempt.attempt_id,
                                attempt.attempt_number, task.deduplication_key)
        lease.validate()
        _atomic_json(_request_path(config, reviewer), request.as_dict())
        return request
    finally:
        lease.release()


def template_for(request: ManualRequest) -> dict:
    """Structural template only. It deliberately contains no model response."""
    identity = {
        "statement_index": request.statement_index,
        "run_id": request.run_id,
        "reviewer": request.reviewer.value,
        "task_id": request.task_id,
        "attempt_id": request.attempt_id,
    }
    return {"schema_version": 1, **identity, "components": [
        {**identity, "slot": slot, "state": "PARTIAL", "observed_at": None,
         "declared_provenance": "", "output": None, "content_sha256": None}
        for slot in MANUAL_REVIEW_SLOTS
    ]}


def _execution(config: VerticalReviewConfig, request: ManualRequest):
    task = replace(_new_task(config, request.reviewer),
                   task_id=request.task_id, status=TaskStatus.RUNNING)
    attempt = replace(ExecutionAttempt.start(task.task_id, request.attempt_number),
                      attempt_id=request.attempt_id, status=AttemptStatus.RUNNING)
    return task, attempt


def _record_rejection(config: VerticalReviewConfig, request: ManualRequest, code: str) -> None:
    lease = WorkflowLease(config.coordinator.lease_path, config.coordinator.owner_id)
    lease.acquire(blocking=False)
    try:
        current = _read_request(config, request.reviewer)
        if current is None or current.task_id != request.task_id or current.attempt_id != request.attempt_id:
            raise ManualSupervisorError("stale_request")
        lease.validate()
        _atomic_json(_request_path(config, request.reviewer),
                     replace(current, last_error=code).as_dict())
    finally:
        lease.release()


def import_manual_package(config: VerticalReviewConfig, package_path: Path,
                          *, allowed_root: Path, reviewer: Reviewer,
                          publication_failure_injector: FailureInjector | None = None
                          ) -> VerticalReviewResult:
    plan = plan_next_statement(config.coordinator.workflow)
    inspection = inspect_reviewer_results(
        statement_index=config.statement_index,
        review_cases_root=config.coordinator.workflow.review_cases_root,
    )
    request = _read_request(config, reviewer)
    if request is None:
        raise ManualSupervisorError("request_missing")
    task, attempt = _execution(config, request)
    adapter = ManualImportReviewerAdapter(package_path, allowed_root=allowed_root)
    if inspection.reviewers[reviewer].status is ReviewerStatus.COMPLETE:
        # A crash may occur after active.json but before the caller receives success.
        # Validate against the original identity, then compare the complete result.
        synthetic_plan = StatementPlan(
            PlanAction.RUN_MISSING_REVIEWER, request.statement_index,
            _request_path(config, reviewer).parent, "replay validation",
            reviewers=(reviewer,), run_id=request.run_id,
        )
        inputs = inspect_review_inputs(
            statement_index=config.statement_index,
            statements_path=config.coordinator.workflow.statements_path,
            runs_root=config.coordinator.workflow.runs_root,
            run=config.coordinator.workflow.run,
        )
        from .vertical_flow import ReviewerExecutionRequest
        candidate = adapter.execute(ReviewerExecutionRequest(synthetic_plan, inputs, task, attempt))
        confirmed = inspection.reviewers[reviewer].complete_result
        if (confirmed is None or confirmed.task_id != request.task_id
                or confirmed.attempt_id != request.attempt_id
                or candidate.main_review.output != confirmed.main_review.output
                or any(a.output != b.output for a, b in zip(candidate.histories, confirmed.histories))
                or any(a.output != b.output for a, b in zip(
                    candidate.profile_proposals, confirmed.profile_proposals))):
            raise ManualSupervisorError("incompatible_replay")
        return VerticalReviewResult(VerticalReviewStatus.UNCHANGED,
                                    "published reviewer set matches the package",
                                    before_plan=plan, inspection=inspection)
    if (plan.action not in {PlanAction.RUN_MISSING_REVIEWER, PlanAction.RETRY_REVIEWER}
            or reviewer not in plan.reviewers):
        raise ManualSupervisorError("review_not_pending")
    if plan.run_id != request.run_id:
        raise ManualSupervisorError("request_run_mismatch")
    inputs = inspect_review_inputs(
        statement_index=config.statement_index,
        statements_path=config.coordinator.workflow.statements_path,
        runs_root=config.coordinator.workflow.runs_root,
        run=config.coordinator.workflow.run,
    )
    from .vertical_flow import ReviewerExecutionRequest
    try:
        adapter.execute(ReviewerExecutionRequest(plan, inputs, task, attempt))
    except ManualReviewImportError as error:
        _record_rejection(config, request, error.code)
        raise
    return run_synthetic_reviewer_once(
        config, adapter, task=task, previous_attempt=attempt,
        publication_failure_injector=publication_failure_injector)


def manual_status(config: VerticalReviewConfig, *, package_path: Path | None = None,
                  allowed_root: Path | None = None) -> dict:
    plan = plan_next_statement(config.coordinator.workflow)
    inspection = inspect_reviewer_results(
        statement_index=config.statement_index,
        review_cases_root=config.coordinator.workflow.review_cases_root,
    )
    reviewers = {}
    for reviewer in Reviewer:
        request = _read_request(config, reviewer)
        state = "PUBLISHED" if inspection.reviewers[reviewer].status is ReviewerStatus.COMPLETE else (
            "REJECTED" if request is not None and request.last_error else
            "WAIT_OPERATOR" if request is not None else "NOT_PREPARED")
        if request is not None and state in {"WAIT_OPERATOR", "REJECTED"} and package_path is not None:
            if allowed_root is None:
                raise ValueError("allowed_root is required with package_path")
            try:
                from .manual_import_adapter import _read_package
                raw = _read_package(package_path, allowed_root)
                states = [item.get("state") for item in raw.get("components", [])
                          if isinstance(item, dict)] if isinstance(raw.get("components"), list) else []
                if "UNCERTAIN" in states:
                    state = "CAPTURE_UNCERTAIN"
                elif "PARTIAL" in states or len(states) != 7:
                    state = "CAPTURE_INCOMPLETE"
                else:
                    task, attempt = _execution(config, request)
                    inputs = inspect_review_inputs(
                        statement_index=config.statement_index,
                        statements_path=config.coordinator.workflow.statements_path,
                        runs_root=config.coordinator.workflow.runs_root,
                        run=config.coordinator.workflow.run,
                    )
                    synthetic_plan = StatementPlan(
                        PlanAction.RUN_MISSING_REVIEWER, request.statement_index,
                        _request_path(config, reviewer).parent, "status validation",
                        reviewers=(reviewer,), run_id=request.run_id,
                    )
                    from .vertical_flow import ReviewerExecutionRequest
                    ManualImportReviewerAdapter(package_path, allowed_root=allowed_root).execute(
                        ReviewerExecutionRequest(synthetic_plan, inputs, task, attempt))
                    state = "VALID"
            except ManualReviewImportError:
                state = "REJECTED"
        reviewers[reviewer.value] = {
            "state": state,
            "request": None if request is None else request.as_dict(),
        }
    return {
        "statement_index": config.statement_index,
        "plan_action": plan.action.value,
        "valid_reviewers": sum(item.status is ReviewerStatus.COMPLETE
                               for item in inspection.reviewers.values()),
        "missing_reviewers": sum(item.status is not ReviewerStatus.COMPLETE
                                 for item in inspection.reviewers.values()),
        "adjudication_enabled": inspection.adjudication_enabled,
        "reviewers": reviewers,
    }
