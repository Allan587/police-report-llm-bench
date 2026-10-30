"""Deterministic, read-only planning for the review/adjudication workflow.

This module intentionally does not coordinate or execute work.  It reconstructs
the next safe operation exclusively from the file-backed inspectors approved in
phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence

from automation.contracts import Reviewer
from automation.review import (
    ApplicationPhase,
    CheckStatus,
    InputIssueCategory,
    ReviewerStatus,
    SelectionAction,
    StatementCompletionInspection,
    StatementCompletionStatus,
    discover_statements,
    inspect_adjudication_application,
    inspect_statement_completeness,
    select_statement_from_verification,
)


class PlanAction(str, Enum):
    """Operations the read-only planner may recommend, but never execute."""

    RECOVER_APPLICATION = "RECOVER_APPLICATION"
    FINALIZE_APPLICATION = "FINALIZE_APPLICATION"
    RETRY_REVIEWER = "RETRY_REVIEWER"
    RUN_MISSING_REVIEWER = "RUN_MISSING_REVIEWER"
    RUN_ADJUDICATIONS = "RUN_ADJUDICATIONS"
    ADJUDICATION_COMMITTED = "ADJUDICATION_COMMITTED"
    COMPLETE = "COMPLETE"
    WAIT_OPERATOR = "WAIT_OPERATOR"
    BLOCKED = "BLOCKED"


class EvidenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"
    INFO = "INFO"


@dataclass(frozen=True)
class PlanningEvidence:
    code: str
    status: EvidenceStatus
    detail: str
    path: Optional[Path] = None


@dataclass(frozen=True)
class PlanningBlocker:
    code: str
    reason: str
    paths: Sequence[Path] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", tuple(Path(path) for path in self.paths))


@dataclass(frozen=True)
class OrchestratorConfig:
    """Explicit locations required to reconstruct workflow state."""

    statements_path: Path
    runs_root: Path
    review_cases_root: Path
    feedback_root: Path
    profiles_root: Path
    run: Optional[str | Path] = None
    application_evidence_filename: str = "application_evidence.json"
    start_statement: Optional[int] = None
    end_statement: Optional[int] = None

    def __post_init__(self) -> None:
        if (self.start_statement is None) != (self.end_statement is None):
            raise ValueError("start_statement and end_statement must be supplied together")
        if self.start_statement is not None:
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 10
                for value in (self.start_statement, self.end_statement)
            ):
                raise ValueError("statement bounds must be integers >= 10; 000-009 are protected")
            if self.start_statement > self.end_statement:
                raise ValueError("start_statement must be <= end_statement")
        for name in (
            "statements_path",
            "runs_root",
            "review_cases_root",
            "feedback_root",
            "profiles_root",
        ):
            object.__setattr__(self, name, Path(getattr(self, name)))
        if not self.application_evidence_filename or Path(
            self.application_evidence_filename
        ).name != self.application_evidence_filename:
            raise ValueError("application_evidence_filename must be one file name")


@dataclass(frozen=True)
class RecoveryPlan:
    action: PlanAction
    statement_index: int
    statement_path: Path
    reason: str
    evidence: Sequence[PlanningEvidence] = field(default_factory=tuple)
    blocker: Optional[PlanningBlocker] = None
    application_path: Optional[Path] = None
    reviewers: Sequence[Reviewer] = field(default_factory=tuple)
    run_id: Optional[str] = None
    missing_dependencies: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement_path", Path(self.statement_path))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "reviewers", tuple(self.reviewers))
        object.__setattr__(self, "missing_dependencies", tuple(self.missing_dependencies))
        if self.application_path is not None:
            object.__setattr__(self, "application_path", Path(self.application_path))


@dataclass(frozen=True)
class StatementPlan:
    action: PlanAction
    statement_index: Optional[int]
    statement_path: Optional[Path]
    reason: str
    evidence: Sequence[PlanningEvidence] = field(default_factory=tuple)
    verifications: Mapping[int, StatementCompletionStatus] = field(default_factory=dict)
    blocker: Optional[PlanningBlocker] = None
    application_path: Optional[Path] = None
    reviewers: Sequence[Reviewer] = field(default_factory=tuple)
    run_id: Optional[str] = None
    missing_dependencies: Sequence[str] = field(default_factory=tuple)
    start_statement: Optional[int] = None
    end_statement: Optional[int] = None

    def __post_init__(self) -> None:
        if self.statement_path is not None:
            object.__setattr__(self, "statement_path", Path(self.statement_path))
        if self.application_path is not None:
            object.__setattr__(self, "application_path", Path(self.application_path))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(
            self, "verifications", MappingProxyType(dict(self.verifications))
        )
        object.__setattr__(self, "reviewers", tuple(self.reviewers))
        object.__setattr__(self, "missing_dependencies", tuple(self.missing_dependencies))


def _statement_path(config: OrchestratorConfig, statement_index: int) -> Path:
    return config.review_cases_root / f"statement_{statement_index:03d}"


def _evidence_path(config: OrchestratorConfig, statement_index: int) -> Path:
    return _statement_path(config, statement_index) / config.application_evidence_filename


def _inspect_completion(
    config: OrchestratorConfig, statement_index: int
) -> StatementCompletionInspection:
    return inspect_statement_completeness(
        statement_index=statement_index,
        statements_path=config.statements_path,
        runs_root=config.runs_root,
        review_cases_root=config.review_cases_root,
        feedback_root=config.feedback_root,
        profiles_root=config.profiles_root,
        run=config.run,
        application_evidence_path=_evidence_path(config, statement_index),
    )


def _statement_inspection_failure(
    config: OrchestratorConfig,
    error: OSError | TypeError | ValueError,
    *,
    verifications: Optional[Mapping[int, StatementCompletionInspection]] = None,
) -> StatementPlan:
    """Return the common safe plan for contract-level inspection failures."""

    error_filename = getattr(error, "filename", None)
    error_path = (
        Path(error_filename)
        if isinstance(error_filename, (str, Path))
        else config.review_cases_root
    )
    blocker = PlanningBlocker(
        "statement_inspection_failed", str(error), (error_path,)
    )
    return StatementPlan(
        PlanAction.WAIT_OPERATOR,
        None,
        None,
        "a statement could not be inspected safely",
        (
            PlanningEvidence(
                "statement_inspection_failed",
                EvidenceStatus.FAIL,
                str(error),
                error_path,
            ),
        ),
        verifications={
            index: inspected.status
            for index, inspected in (verifications or {}).items()
        },
        blocker=blocker,
    )


def _application_recovery_plan(
    config: OrchestratorConfig, statement_index: int
) -> Optional[RecoveryPlan]:
    statement_path = _statement_path(config, statement_index)
    applications_root = statement_path / ".adjudication_applications"
    if not applications_root.exists():
        return None
    if not applications_root.is_dir():
        blocker = PlanningBlocker(
            "application_storage_invalid",
            "adjudication application storage is not a directory",
            (applications_root,),
        )
        return RecoveryPlan(
            PlanAction.WAIT_OPERATOR,
            statement_index,
            statement_path,
            blocker.reason,
            (PlanningEvidence(blocker.code, EvidenceStatus.FAIL, blocker.reason, applications_root),),
            blocker=blocker,
        )

    candidates: list[RecoveryPlan] = []
    try:
        application_paths = sorted(
            (path for path in applications_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
    except OSError as error:
        blocker = PlanningBlocker(
            "application_storage_unreadable", str(error), (applications_root,)
        )
        return RecoveryPlan(
            PlanAction.WAIT_OPERATOR,
            statement_index,
            statement_path,
            blocker.reason,
            (PlanningEvidence(blocker.code, EvidenceStatus.FAIL, blocker.reason, applications_root),),
            blocker=blocker,
        )

    for application_path in application_paths:
        if application_path.name.startswith(".staging-"):
            candidates.append(
                RecoveryPlan(
                    PlanAction.WAIT_OPERATOR,
                    statement_index,
                    statement_path,
                    "an orphaned application staging directory cannot be verified",
                    blocker=PlanningBlocker(
                        "application_staging_orphan",
                        "an orphaned application staging directory cannot be verified",
                        (application_path,),
                    ),
                    application_path=application_path,
                )
            )
            continue
        try:
            application = inspect_adjudication_application(
                application_path=application_path,
                statement_path=statement_path,
                feedback_root=config.feedback_root,
                profiles_root=config.profiles_root,
                application_evidence_path=_evidence_path(config, statement_index),
            )
        except (OSError, TypeError, ValueError) as error:
            candidates.append(
                RecoveryPlan(
                    PlanAction.WAIT_OPERATOR,
                    statement_index,
                    statement_path,
                    f"application cannot be verified: {error}",
                    (
                        PlanningEvidence(
                            "application_inspection_failed",
                            EvidenceStatus.FAIL,
                            str(error),
                            application_path,
                        ),
                    ),
                    blocker=PlanningBlocker(
                        "application_inspection_failed",
                        "an application cannot be verified safely",
                        (application_path,),
                    ),
                    application_path=application_path,
                )
            )
            continue
        if application.statement_index != statement_index:
            reason = (
                "application statement identity does not match its containing "
                "statement directory"
            )
            blocker = PlanningBlocker(
                "application_statement_mismatch",
                reason,
                (application_path, statement_path),
            )
            return RecoveryPlan(
                PlanAction.WAIT_OPERATOR,
                statement_index,
                statement_path,
                reason,
                (
                    PlanningEvidence(
                        "application_statement_mismatch",
                        EvidenceStatus.FAIL,
                        (
                            f"application identifies statement_{application.statement_index:03d} "
                            f"but is stored under statement_{statement_index:03d}"
                        ),
                        application_path,
                    ),
                ),
                blocker=blocker,
                application_path=application_path,
            )
        evidence = PlanningEvidence(
            "application_phase",
            EvidenceStatus.FAIL
            if application.phase is ApplicationPhase.INVALID
            else EvidenceStatus.PASS,
            f"application phase is {application.phase.value}",
            application_path,
        )
        if application.phase is ApplicationPhase.INVALID:
            candidates.append(
                RecoveryPlan(
                    PlanAction.WAIT_OPERATOR,
                    statement_index,
                    statement_path,
                    "application state is invalid or contradictory",
                    (evidence,),
                    blocker=PlanningBlocker(
                        "application_invalid",
                        "application state is invalid or contradictory",
                        (application_path,),
                    ),
                    application_path=application_path,
                )
            )
        elif application.phase in {
            ApplicationPhase.PREPARED,
            ApplicationPhase.INTERRUPTED,
        }:
            candidates.append(
                RecoveryPlan(
                    PlanAction.RECOVER_APPLICATION,
                    statement_index,
                    statement_path,
                    "a prepared or interrupted application must be recovered first",
                    (evidence,),
                    application_path=application_path,
                    run_id=application.run_id,
                )
            )
        elif application.phase is ApplicationPhase.ADJUDICATION_COMMITTED:
            candidates.append(
                RecoveryPlan(
                    PlanAction.ADJUDICATION_COMMITTED,
                    statement_index,
                    statement_path,
                    "the final adjudication is committed; incremental application remains pending",
                    (evidence,),
                    application_path=application_path,
                    run_id=application.run_id,
                )
            )
        elif (
            application.phase is ApplicationPhase.APPLIED
            and not _evidence_path(config, statement_index).is_file()
        ):
            candidates.append(
                RecoveryPlan(
                    PlanAction.FINALIZE_APPLICATION,
                    statement_index,
                    statement_path,
                    "application is applied but final evidence is absent",
                    (evidence,),
                    application_path=application_path,
                    run_id=application.run_id,
                )
            )

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    paths = tuple(
        plan.application_path
        for plan in candidates
        if plan.application_path is not None
    )
    blocker = PlanningBlocker(
        "multiple_application_candidates",
        "multiple applications require attention; selecting one would be ambiguous",
        paths,
    )
    return RecoveryPlan(
        PlanAction.WAIT_OPERATOR,
        statement_index,
        statement_path,
        blocker.reason,
        tuple(item for plan in candidates for item in plan.evidence),
        blocker=blocker,
    )


def resume_plan(
    config: OrchestratorConfig,
    statement_index: int,
    *,
    verification: Optional[StatementCompletionInspection] = None,
) -> RecoveryPlan:
    """Plan the next operation for one statement without executing it."""

    if not isinstance(config, OrchestratorConfig):
        raise ValueError("config must be an OrchestratorConfig")
    if not isinstance(statement_index, int) or isinstance(statement_index, bool) or statement_index < 0:
        raise ValueError("statement_index must be a non-negative integer")
    if config.start_statement is not None and not (
        config.start_statement <= statement_index <= config.end_statement
    ):
        raise ValueError("statement_index is outside the configured range")
    statement_path = _statement_path(config, statement_index)
    if statement_index < 10:
        return RecoveryPlan(
            PlanAction.COMPLETE,
            statement_index,
            statement_path,
            "historical statements 000-009 are closed and must not be reprocessed",
            (
                PlanningEvidence(
                    "historical_policy", EvidenceStatus.PASS, "historical closure applies", statement_path
                ),
            ),
        )

    application_plan = _application_recovery_plan(config, statement_index)
    if application_plan is not None:
        return application_plan
    inspected = verification or _inspect_completion(config, statement_index)
    if inspected.statement_index != statement_index:
        raise ValueError("verification statement_index does not match")
    evidence = [
        PlanningEvidence(
            "statement_completion",
            EvidenceStatus.PASS
            if inspected.status is StatementCompletionStatus.COMPLETE
            else EvidenceStatus.UNVERIFIED
            if inspected.status is StatementCompletionStatus.UNVERIFIED
            else EvidenceStatus.FAIL,
            f"statement status is {inspected.status.value}",
            statement_path,
        )
    ]
    evidence.extend(
        PlanningEvidence(
            f"completion_check_{name}",
            EvidenceStatus.PASS
            if check is CheckStatus.PASS
            else EvidenceStatus.UNVERIFIED
            if check is CheckStatus.UNVERIFIED
            else EvidenceStatus.INFO
            if check is CheckStatus.NOT_APPLICABLE
            else EvidenceStatus.FAIL,
            f"{name} check is {check.value}",
            statement_path,
        )
        for name, check in inspected.checks.items()
    )
    if inspected.status is StatementCompletionStatus.COMPLETE:
        return RecoveryPlan(
            PlanAction.COMPLETE,
            statement_index,
            statement_path,
            "statement is operationally complete",
            evidence,
            run_id=(
                None
                if inspected.input_validation is None
                else inspected.input_validation.selected_run_id
            ),
        )
    if inspected.status is StatementCompletionStatus.UNVERIFIED:
        blocker = PlanningBlocker(
            "statement_unverified",
            "statement contains evidence that cannot be verified automatically",
            tuple(issue.path for issue in inspected.issues if issue.path is not None),
        )
        return RecoveryPlan(
            PlanAction.WAIT_OPERATOR,
            statement_index,
            statement_path,
            blocker.reason,
            evidence,
            blocker=blocker,
            missing_dependencies=inspected.missing_dependencies,
        )

    inputs = inspected.input_validation
    if inputs is None or not inputs.inputs_complete:
        ambiguities = () if inputs is None else inputs.ambiguities
        action = PlanAction.WAIT_OPERATOR if ambiguities else PlanAction.BLOCKED
        reason = (
            "input run selection is ambiguous and requires an operator choice"
            if ambiguities
            else "required statement or generation inputs are missing or invalid"
        )
        issue_paths = () if inputs is None else tuple(
            issue.path for issue in inputs.issues if issue.path is not None
        )
        blocker = PlanningBlocker(
            "inputs_ambiguous" if ambiguities else "inputs_unavailable",
            reason,
            issue_paths,
        )
        evidence.extend(
            PlanningEvidence(
                f"input_{issue.code}",
                EvidenceStatus.UNVERIFIED
                if issue.category is InputIssueCategory.AMBIGUOUS
                else EvidenceStatus.FAIL,
                issue.message,
                issue.path,
            )
            for issue in (() if inputs is None else inputs.issues)
        )
        return RecoveryPlan(
            action,
            statement_index,
            statement_path,
            reason,
            evidence,
            blocker=blocker,
            run_id=None if inputs is None else inputs.selected_run_id,
            missing_dependencies=inspected.missing_dependencies,
        )

    reviews = inspected.review_inspection
    if reviews is None:
        blocker = PlanningBlocker(
            "review_inspection_missing", "review state was not inspected", (statement_path,)
        )
        return RecoveryPlan(
            PlanAction.WAIT_OPERATOR,
            statement_index,
            statement_path,
            blocker.reason,
            evidence,
            blocker=blocker,
            run_id=inputs.selected_run_id,
        )
    reviewer_keys = tuple(reviews.reviewers)
    if (
        any(not isinstance(reviewer, Reviewer) for reviewer in reviewer_keys)
        or set(reviewer_keys) != set(Reviewer)
    ):
        reason = "reviewer mapping keys must match the canonical reviewer set"
        blocker = PlanningBlocker(
            "reviewer_mapping_invalid", reason, (statement_path,)
        )
        return RecoveryPlan(
            PlanAction.WAIT_OPERATOR,
            statement_index,
            statement_path,
            reason,
            (
                *evidence,
                PlanningEvidence(
                    "reviewer_mapping_invalid",
                    EvidenceStatus.FAIL,
                    "expected exactly chatgpt, gemini, and grok reviewer keys",
                    statement_path,
                ),
            ),
            blocker=blocker,
            run_id=inputs.selected_run_id,
        )
    evidence.extend(
        PlanningEvidence(
            f"reviewer_{reviewer.value}",
            EvidenceStatus.PASS
            if item.status is ReviewerStatus.COMPLETE
            else EvidenceStatus.UNVERIFIED
            if item.status is ReviewerStatus.UNVERIFIED
            else EvidenceStatus.FAIL,
            f"{reviewer.value} reviewer set is {item.status.value}",
            statement_path,
        )
        for reviewer in Reviewer
        for item in (reviews.reviewers[reviewer],)
    )
    unverified = tuple(
        reviewer
        for reviewer in Reviewer
        for item in (reviews.reviewers[reviewer],)
        if item.status is ReviewerStatus.UNVERIFIED
    )
    if unverified:
        blocker = PlanningBlocker(
            "review_provenance_unverified",
            "one or more reviewer sets have unverified provenance",
            (statement_path,),
        )
        return RecoveryPlan(
            PlanAction.WAIT_OPERATOR,
            statement_index,
            statement_path,
            blocker.reason,
            evidence,
            blocker=blocker,
            reviewers=unverified,
            run_id=inputs.selected_run_id,
        )
    retry = tuple(
        reviewer
        for reviewer in Reviewer
        for item in (reviews.reviewers[reviewer],)
        if item.status in {ReviewerStatus.INCOMPLETE, ReviewerStatus.INVALID}
    )
    if retry:
        return RecoveryPlan(
            PlanAction.RETRY_REVIEWER,
            statement_index,
            statement_path,
            "each listed reviewer requires a new complete seven-artifact attempt",
            evidence,
            reviewers=retry,
            run_id=inputs.selected_run_id,
        )
    missing = tuple(
        reviewer
        for reviewer in Reviewer
        for item in (reviews.reviewers[reviewer],)
        if item.status is ReviewerStatus.NOT_STARTED
    )
    if missing:
        return RecoveryPlan(
            PlanAction.RUN_MISSING_REVIEWER,
            statement_index,
            statement_path,
            "one or more reviewers have not started",
            evidence,
            reviewers=missing,
            run_id=inputs.selected_run_id,
        )
    if reviews.adjudication_enabled:
        adjudication_path = statement_path / "adjudication.json"
        evidence_path = _evidence_path(config, statement_index)
        if adjudication_path.exists() or evidence_path.exists():
            blocker = PlanningBlocker(
                "untracked_or_contradictory_adjudication",
                "adjudication artifacts exist without a recoverable, complete application",
                tuple(path for path in (adjudication_path, evidence_path) if path.exists()),
            )
            return RecoveryPlan(
                PlanAction.WAIT_OPERATOR,
                statement_index,
                statement_path,
                blocker.reason,
                evidence,
                blocker=blocker,
                run_id=inputs.selected_run_id,
            )
        return RecoveryPlan(
            PlanAction.RUN_ADJUDICATIONS,
            statement_index,
            statement_path,
            "all three reviewer sets are complete; adjudication is the next stage",
            evidence,
            run_id=inputs.selected_run_id,
        )
    blocker = PlanningBlocker(
        "review_state_inconsistent",
        "review state does not map to a safe deterministic action",
        (statement_path,),
    )
    return RecoveryPlan(
        PlanAction.WAIT_OPERATOR,
        statement_index,
        statement_path,
        blocker.reason,
        evidence,
        blocker=blocker,
        run_id=inputs.selected_run_id,
    )


def _as_statement_plan(
    recovery: RecoveryPlan,
    *,
    verifications: Mapping[int, StatementCompletionStatus],
    prefix_evidence: Sequence[PlanningEvidence] = (),
) -> StatementPlan:
    return StatementPlan(
        action=recovery.action,
        statement_index=recovery.statement_index,
        statement_path=recovery.statement_path,
        reason=recovery.reason,
        evidence=(*prefix_evidence, *recovery.evidence),
        verifications=verifications,
        blocker=recovery.blocker,
        application_path=recovery.application_path,
        reviewers=recovery.reviewers,
        run_id=recovery.run_id,
        missing_dependencies=recovery.missing_dependencies,
    )


def plan_next_statement(config: OrchestratorConfig) -> StatementPlan:
    """Plan within explicit inclusive bounds, or preserve the unbounded API."""

    if not isinstance(config, OrchestratorConfig):
        raise ValueError("config must be an OrchestratorConfig")
    config.__post_init__()
    plan = (
        _plan_inclusive_range(config)
        if config.start_statement is not None
        else _plan_unbounded(config)
    )
    return replace(
        plan, start_statement=config.start_statement, end_statement=config.end_statement
    )


def _plan_inclusive_range(config: OrchestratorConfig) -> StatementPlan:
    # Inspect every numeric index, including directories not created yet. Never
    # compute a global recovery plan and then filter it: a later application
    # must not take precedence over the first incomplete statement in the range.
    discovery = discover_statements(config.review_cases_root)
    if discovery.error and config.review_cases_root.exists():
        reason = discovery.error
        return StatementPlan(
            PlanAction.BLOCKED, None, None, reason,
            blocker=PlanningBlocker("statement_discovery_invalid", reason, (config.review_cases_root,)),
        )
    statuses: dict[int, StatementCompletionStatus] = {}
    discovered_paths = {item.statement_index: item.path for item in discovery.statements}
    for index in range(config.start_statement, config.end_statement + 1):
        if index in discovery.duplicate_indices or (
            index in discovered_paths and discovered_paths[index] != _statement_path(config, index)
        ):
            reason = f"ambiguous or noncanonical directory for statement_{index:03d}"
            return StatementPlan(
                PlanAction.WAIT_OPERATOR, index, _statement_path(config, index), reason,
                verifications=statuses,
                blocker=PlanningBlocker("statement_discovery_invalid", reason, (config.review_cases_root,)),
            )
        try:
            inspected = _inspect_completion(config, index)
            statuses[index] = inspected.status
            if inspected.status is not StatementCompletionStatus.COMPLETE:
                return _as_statement_plan(
                    resume_plan(config, index, verification=inspected),
                    verifications=statuses,
                )
        except (OSError, TypeError, ValueError) as error:
            failure = _statement_inspection_failure(config, error)
            return replace(
                failure, statement_index=index, statement_path=_statement_path(config, index),
                verifications=statuses,
            )
    return StatementPlan(
        PlanAction.COMPLETE, None, None,
        "all statements in the inclusive range are verified COMPLETE",
        verifications=statuses,
    )


def _plan_unbounded(config: OrchestratorConfig) -> StatementPlan:
    """Return the next safe operational plan without modifying any file."""

    if not isinstance(config, OrchestratorConfig):
        raise ValueError("config must be an OrchestratorConfig")
    discovery = discover_statements(config.review_cases_root)
    discovery_evidence = PlanningEvidence(
        "statement_discovery",
        EvidenceStatus.FAIL
        if discovery.error or discovery.duplicate_indices
        else EvidenceStatus.PASS,
        discovery.error
        or (
            f"duplicate statement indices: {discovery.duplicate_indices}"
            if discovery.duplicate_indices
            else f"discovered {len(discovery.statements)} statement directories"
        ),
        config.review_cases_root,
    )
    if discovery.error or discovery.duplicate_indices:
        reason = discovery.error or "duplicate statement directory indices"
        blocker = PlanningBlocker("statement_discovery_invalid", reason, (config.review_cases_root,))
        return StatementPlan(
            PlanAction.BLOCKED,
            None,
            None,
            reason,
            (discovery_evidence,),
            blocker=blocker,
        )

    active = tuple(item for item in discovery.statements if item.statement_index >= 10)
    application_plans = tuple(
        plan
        for item in active
        if (plan := _application_recovery_plan(config, item.statement_index)) is not None
    )
    if len(application_plans) == 1:
        return _as_statement_plan(
            application_plans[0], verifications={}, prefix_evidence=(discovery_evidence,)
        )
    if len(application_plans) > 1:
        paths = tuple(
            plan.application_path
            for plan in application_plans
            if plan.application_path is not None
        )
        blocker = PlanningBlocker(
            "multiple_statements_require_application_recovery",
            "applications for multiple statements require attention",
            paths,
        )
        return StatementPlan(
            PlanAction.WAIT_OPERATOR,
            None,
            None,
            blocker.reason,
            (
                discovery_evidence,
                *(item for plan in application_plans for item in plan.evidence),
            ),
            blocker=blocker,
        )

    if not active:
        historical_indices = {item.statement_index for item in discovery.statements}
        if 9 not in historical_indices:
            reason = "statement_009 is absent; continuity to statement_010 cannot be established"
            blocker = PlanningBlocker("historical_boundary_missing", reason, (config.review_cases_root,))
            return StatementPlan(
                PlanAction.BLOCKED,
                None,
                None,
                reason,
                (discovery_evidence,),
                blocker=blocker,
            )
        target_index = 10
        try:
            verification = _inspect_completion(config, target_index)
        except (OSError, TypeError, ValueError) as error:
            failure = _statement_inspection_failure(config, error)
            return StatementPlan(
                action=failure.action,
                statement_index=failure.statement_index,
                statement_path=failure.statement_path,
                reason=failure.reason,
                evidence=(discovery_evidence, *failure.evidence),
                verifications=failure.verifications,
                blocker=failure.blocker,
            )
        recovery = resume_plan(config, target_index, verification=verification)
        return _as_statement_plan(
            recovery,
            verifications={target_index: verification.status},
            prefix_evidence=(discovery_evidence,),
        )

    verifications: dict[int, StatementCompletionInspection] = {}
    try:
        for item in active:
            verifications[item.statement_index] = _inspect_completion(
                config, item.statement_index
            )
    except (OSError, TypeError, ValueError) as error:
        failure = _statement_inspection_failure(
            config, error, verifications=verifications
        )
        return StatementPlan(
            action=failure.action,
            statement_index=failure.statement_index,
            statement_path=failure.statement_path,
            reason=failure.reason,
            evidence=(discovery_evidence, *failure.evidence),
            verifications=failure.verifications,
            blocker=failure.blocker,
        )
    latest = active[-1]
    selection = select_statement_from_verification(
        config.review_cases_root,
        verifications[latest.statement_index],
        earlier_verifications={
            index: inspected
            for index, inspected in verifications.items()
            if index < latest.statement_index
        },
    )
    statuses = {index: inspected.status for index, inspected in verifications.items()}
    selection_evidence = PlanningEvidence(
        "sequential_selection",
        EvidenceStatus.PASS
        if selection.action is not SelectionAction.PENDING
        else EvidenceStatus.FAIL,
        selection.reason,
        selection.statement_path or config.review_cases_root,
    )
    if selection.action is SelectionAction.PENDING or selection.statement_index is None:
        wait = "not verified" in selection.reason or "unknown" in selection.reason
        action = PlanAction.WAIT_OPERATOR if wait else PlanAction.BLOCKED
        blocker = PlanningBlocker("sequential_selection_blocked", selection.reason)
        return StatementPlan(
            action,
            None,
            None,
            selection.reason,
            (discovery_evidence, selection_evidence),
            verifications=statuses,
            blocker=blocker,
        )
    selected_verification = verifications.get(selection.statement_index)
    if selected_verification is None:
        try:
            selected_verification = _inspect_completion(
                config, selection.statement_index
            )
        except (OSError, TypeError, ValueError) as error:
            failure = _statement_inspection_failure(
                config, error, verifications=verifications
            )
            return StatementPlan(
                action=failure.action,
                statement_index=failure.statement_index,
                statement_path=failure.statement_path,
                reason=failure.reason,
                evidence=(
                    discovery_evidence,
                    selection_evidence,
                    *failure.evidence,
                ),
                verifications=failure.verifications,
                blocker=failure.blocker,
            )
        statuses[selection.statement_index] = selected_verification.status
    recovery = resume_plan(
        config, selection.statement_index, verification=selected_verification
    )
    return _as_statement_plan(
        recovery,
        verifications=statuses,
        prefix_evidence=(discovery_evidence, selection_evidence),
    )


__all__ = [
    "EvidenceStatus",
    "OrchestratorConfig",
    "PlanAction",
    "PlanningBlocker",
    "PlanningEvidence",
    "RecoveryPlan",
    "StatementPlan",
    "plan_next_statement",
    "resume_plan",
]
