"""Apply one confirmed adjudication in a temporary synthetic workflow only.

No reviewer/adjudicator is called, no range is driven, and all persistence is
owned by the existing application writer and v2 finalizer.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from automation.contracts import BenchmarkModel
from automation.orchestrator import PlanAction
from automation.workflow_coordinator import (
    CoordinationExpectation,
    CoordinationStatus,
    WorkflowCoordinatorConfig,
    confirm_workflow_expectation_under_lease,
    coordinate_workflow_under_lease,
)
from automation.workflow_lease import WorkflowLease, WorkflowLeaseError, WorkflowLeaseInUseError

from .adjudication_storage import (
    AdjudicationApplication,
    ApplicationPhase,
    _validate_active_review_run,
    _normalize_review_attempts,
    inspect_adjudication_application,
)
from .completeness import StatementCompletionInspection, StatementCompletionStatus, inspect_statement_completeness
from .finalization import finalize_adjudication_application, verify_application_evidence
from .path_safety import confined_path
from .validators import valid_index, validate_history, validate_profile_adjudication_transition


class SyntheticApplicationStatus(str, Enum):
    APPLIED = "APPLIED"
    UNCHANGED = "UNCHANGED"
    BLOCKED = "BLOCKED"
    CONTENDED = "CONTENDED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    STALE_PLAN = "STALE_PLAN"


@dataclass(frozen=True)
class SyntheticApplicationConfig:
    coordinator: WorkflowCoordinatorConfig
    fixture_root: Path
    statement_index: int
    application_id: str
    fixture_location_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.coordinator, WorkflowCoordinatorConfig):
            raise TypeError("coordinator must be a WorkflowCoordinatorConfig")
        if not valid_index(self.statement_index):
            raise ValueError("statement_index must be a non-negative integer")
        if self.statement_index < 10:
            raise PermissionError("historical statements 000-009 are read-only")
        workflow = self.coordinator.workflow
        if workflow.start_statement is not None and not (
            workflow.start_statement <= self.statement_index <= workflow.end_statement
        ):
            raise ValueError("statement_index is outside the configured range")
        if (
            not isinstance(self.application_id, str)
            or len(self.application_id) != 64
            or any(c not in "0123456789abcdef" for c in self.application_id)
        ):
            raise ValueError("application_id must be the confirmed application SHA-256")
        object.__setattr__(self, "fixture_root", validate_synthetic_fixture(
            self.coordinator, self.fixture_root, self.fixture_location_confirmed
        ))


def validate_synthetic_fixture(
    coordinator: WorkflowCoordinatorConfig, fixture_root: Path, confirmed: bool
) -> Path:
    """Shared confinement gate for synthetic application and range execution."""

    if not confirmed:
        raise ValueError("synthetic application requires temporary-fixture confirmation")
    root = Path(fixture_root).absolute()
    base = Path(tempfile.gettempdir()).resolve()
    if root.resolve() == base or not root.resolve().is_relative_to(base):
        raise ValueError("fixture_root must be a dedicated directory under the system temporary directory")
    repository = Path(__file__).resolve().parents[2]
    if root.resolve().is_relative_to(repository) or repository.is_relative_to(root.resolve()):
        raise ValueError("synthetic fixtures must not overlap the source repository")
    confined_path(root, base, label="synthetic fixture")
    if not root.is_dir():
        raise ValueError("fixture_root must already exist")
    workflow = coordinator.workflow
    for path in (
        workflow.statements_path, workflow.runs_root, workflow.review_cases_root,
        workflow.feedback_root, workflow.profiles_root, coordinator.lease_path,
    ):
        confined_path(path, root, label="synthetic workflow path")
    if workflow.run is not None:
        requested = Path(workflow.run)
        confined_path(
            requested if requested.is_absolute() else workflow.runs_root / requested,
            root, label="synthetic selected run",
        )
    return root


@dataclass(frozen=True)
class SyntheticApplicationResult:
    status: SyntheticApplicationStatus
    reason: str
    application: Optional[AdjudicationApplication] = None
    completion: Optional[StatementCompletionInspection] = None


def _completion(config: SyntheticApplicationConfig) -> StatementCompletionInspection:
    w = config.coordinator.workflow
    return inspect_statement_completeness(
        statement_index=config.statement_index,
        statements_path=w.statements_path, runs_root=w.runs_root,
        review_cases_root=w.review_cases_root, feedback_root=w.feedback_root,
        profiles_root=w.profiles_root, run=w.run,
        application_evidence_path=w.review_cases_root / f"statement_{config.statement_index:03d}" / w.application_evidence_filename,
    )


def _validate_application(config: SyntheticApplicationConfig) -> AdjudicationApplication:
    w = config.coordinator.workflow
    statement = w.review_cases_root / f"statement_{config.statement_index:03d}"
    evidence = statement / w.application_evidence_filename
    application = inspect_adjudication_application(
        application_path=statement / ".adjudication_applications" / config.application_id,
        statement_path=statement, feedback_root=w.feedback_root, profiles_root=w.profiles_root,
        application_evidence_path=evidence,
    )
    if application.phase not in {
        ApplicationPhase.ADJUDICATION_COMMITTED, ApplicationPhase.INTERRUPTED, ApplicationPhase.APPLIED,
    } or application.statement_index != config.statement_index:
        raise ValueError("a valid confirmed adjudication application is required")
    completion = _completion(config)
    inputs = completion.input_validation
    if inputs is None or not inputs.inputs_complete or inputs.selected_run_id != application.run_id:
        raise ValueError("application run contradicts complete aligned inputs")
    attempts = _normalize_review_attempts(statement, config.statement_index, application.review_attempts)
    _validate_active_review_run(statement, config.statement_index, application.run_id, attempts)
    manifest = json.loads(application.manifest_path.read_text(encoding="utf-8"))
    if hashlib.sha256((statement / "adjudication.json").read_bytes()).hexdigest() != manifest["files"]["adjudication"]["target_sha256"]:
        raise ValueError("final adjudication is not confirmed")
    prepared = application.application_path / "prepared"
    adjudication = json.loads((prepared / "adjudication.json").read_text(encoding="utf-8"))
    # The inspector verified all seven prepared hashes and their application
    # identity. Revalidate semantic transitions for every still-pending profile;
    # already replaced profiles must match the committed target exactly.
    for model in BenchmarkModel:
        history_item = manifest["files"][f"history:{model.value}"]
        records = [
            json.loads(line) for line in
            (application.application_path / history_item["prepared_path"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        indices = [record.get("output") for record in records]
        if (
            any(not valid_index(index) or index > config.statement_index for index in indices)
            or len(set(indices)) != len(indices)
            or indices[-1] != config.statement_index
        ):
            raise ValueError("prepared history has duplicate, future or misaligned records")
        for record in records:
            # Read-only compatibility with model-less historical JSONL; never
            # rewrite the legacy record or fabricate persisted provenance.
            validate_history({"model": model.value, **record}, model, record["output"])
        history_bytes = (w.feedback_root / f"{model.value}_history.jsonl").read_bytes()
        if hashlib.sha256(history_bytes).hexdigest() == history_item["source_sha256"]:
            previous_records = [json.loads(line) for line in history_bytes.splitlines() if line.strip()]
            if records[:-1] != previous_records:
                raise ValueError("prepared history does not preserve its accumulated preimage")
        key = f"profile:{model.value}"
        current_path = w.profiles_root / f"{model.value}_profile.json"
        current_bytes = current_path.read_bytes()
        item = manifest["files"][key]
        if hashlib.sha256(current_bytes).hexdigest() == item["source_sha256"]:
            candidate = json.loads((application.application_path / item["prepared_path"]).read_text(encoding="utf-8"))
            validate_profile_adjudication_transition(
                json.loads(current_bytes), candidate, model,
                statement_index=config.statement_index, adjudication=adjudication,
            )
    if evidence.exists():
        verify_application_evidence(application=application, statement_path=statement, evidence_path=evidence)
    elif application.phase is ApplicationPhase.APPLIED and completion.status is StatementCompletionStatus.COMPLETE:
        raise ValueError("complete application has no evidence")
    return application


def run_synthetic_application_once(
    config: SyntheticApplicationConfig,
    *,
    expected: Optional[CoordinationExpectation] = None,
    failure_injector: Optional[Callable[[str], None]] = None,
) -> SyntheticApplicationResult:
    """Apply/finalize only this statement, or verify its exact identity on replay."""

    if not isinstance(config, SyntheticApplicationConfig):
        raise TypeError("config must be a SyntheticApplicationConfig")
    # Recheck confinement on every call, including reuse of a config object.
    config.__post_init__()
    coordinator = config.coordinator
    lease = WorkflowLease(coordinator.lease_path, coordinator.owner_id)
    try:
        lease.acquire(blocking=False)
    except WorkflowLeaseInUseError as error:
        return SyntheticApplicationResult(SyntheticApplicationStatus.CONTENDED, str(error))
    except WorkflowLeaseError as error:
        return SyntheticApplicationResult(SyntheticApplicationStatus.VALIDATION_FAILED, str(error))
    try:
        coordinated = coordinate_workflow_under_lease(coordinator, lease, expected=expected)
        if coordinated.status is CoordinationStatus.STALE_PLAN:
            return SyntheticApplicationResult(SyntheticApplicationStatus.STALE_PLAN, coordinated.reason)
        if coordinated.plan is None or coordinated.plan.action is PlanAction.WAIT_OPERATOR:
            return SyntheticApplicationResult(SyntheticApplicationStatus.BLOCKED, coordinated.reason)
        application = _validate_application(config)
        completion = _completion(config)
        replay = completion.status is StatementCompletionStatus.COMPLETE
        if not replay and (
            coordinated.status is not CoordinationStatus.ACTION_REQUIRED
            or coordinated.plan.statement_index != config.statement_index
            or coordinated.plan.application_path != application.application_path
            or coordinated.plan.action not in {
                PlanAction.ADJUDICATION_COMMITTED, PlanAction.RECOVER_APPLICATION, PlanAction.FINALIZE_APPLICATION,
            }
        ):
            return SyntheticApplicationResult(SyntheticApplicationStatus.BLOCKED, "O-01 has not authorized this application")
        if replay and application.phase is not ApplicationPhase.APPLIED:
            raise ValueError("complete evidence contradicts application phase")
        if failure_injector is not None:
            failure_injector("before_application")
        immediate = confirm_workflow_expectation_under_lease(coordinator, lease, coordinated.expectation())
        if (
            immediate.status is not CoordinationStatus.ACTION_REQUIRED
            or immediate.plan != coordinated.plan
            or immediate.precondition_sha256 != coordinated.precondition_sha256
        ):
            return SyntheticApplicationResult(SyntheticApplicationStatus.STALE_PLAN, "workflow changed before application")
        lease.validate()
        if replay:
            return SyntheticApplicationResult(SyntheticApplicationStatus.UNCHANGED, "application and v2 evidence already verified", application, completion)
        w = coordinator.workflow
        statement = w.review_cases_root / f"statement_{config.statement_index:03d}"

        def checkpoint(stage: str) -> None:
            lease.validate()
            if failure_injector is not None:
                failure_injector(stage)
            lease.validate()

        final = finalize_adjudication_application(
            application_path=application.application_path, statement_path=statement,
            feedback_root=w.feedback_root, profiles_root=w.profiles_root,
            evidence_path=statement / w.application_evidence_filename,
            failure_injector=checkpoint,
        )
        verify_application_evidence(application=final.application, statement_path=statement, evidence_path=final.evidence_path)
        completion = _completion(config)
        if completion.status is not StatementCompletionStatus.COMPLETE:
            raise ValueError("application has not reached verified statement completion")
        return SyntheticApplicationResult(SyntheticApplicationStatus.APPLIED, "six accumulated destinations and v2 evidence verified", final.application, completion)
    except (OSError, ValueError, TypeError, WorkflowLeaseError) as error:
        return SyntheticApplicationResult(SyntheticApplicationStatus.VALIDATION_FAILED, str(error))
    finally:
        lease.release()
