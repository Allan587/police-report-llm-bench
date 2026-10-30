"""Deterministic, read-only verification of statement-level completion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence

from automation.contracts import BenchmarkModel, JSONValue, Reviewer

from .input_loader import ReviewInputValidation, inspect_review_inputs
from .result_inspector import (
    ProvisionalArtifactFiles,
    ReviewerStatus,
    StatementReviewInspection,
    inspect_reviewer_results,
)
from .statement_selector import (
    CompletionState,
    SelectionAction,
    StatementSelection,
    discover_statements,
    select_statement,
)
from .validators import (
    validate_adjudication as validate_adjudication_schema,
    validate_history as validate_history_schema,
    validate_profile as validate_profile_schema,
    validate_profile_transition,
)


HISTORICAL_STATEMENT_RANGE = range(0, 10)


class StatementCompletionStatus(str, Enum):
    HISTORICAL_COMPLETE = "HISTORICAL_COMPLETE"
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNVERIFIED = "UNVERIFIED"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CompletionIssueKind(str, Enum):
    MISSING = "missing"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class CompletionIssue:
    kind: CompletionIssueKind
    code: str
    message: str
    path: Optional[Path] = None


@dataclass(frozen=True)
class ApplicationEvidenceInspection:
    status: CheckStatus
    path: Optional[Path]
    evidence: Optional[Mapping[str, JSONValue]] = None
    issues: Sequence[CompletionIssue] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.evidence is not None:
            object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        object.__setattr__(self, "issues", tuple(self.issues))


@dataclass(frozen=True)
class StatementCompletionInspection:
    statement_index: int
    status: StatementCompletionStatus
    checks: Mapping[str, CheckStatus]
    input_validation: Optional[ReviewInputValidation]
    review_inspection: Optional[StatementReviewInspection]
    application_evidence: ApplicationEvidenceInspection
    missing_dependencies: Sequence[str] = field(default_factory=tuple)
    issues: Sequence[CompletionIssue] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))
        object.__setattr__(self, "missing_dependencies", tuple(self.missing_dependencies))
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def can_advance(self) -> bool:
        return self.status in {
            StatementCompletionStatus.HISTORICAL_COMPLETE,
            StatementCompletionStatus.COMPLETE,
        }

    @property
    def selector_completion(self) -> CompletionState:
        if self.can_advance:
            return CompletionState.COMPLETE
        if self.status is StatementCompletionStatus.INCOMPLETE:
            return CompletionState.INCOMPLETE
        return CompletionState.UNKNOWN


_QUALITY_FIELDS = frozenset(
    {
        "factual_consistency",
        "unsupported_information",
        "relevant_information_preserved",
        "temporal_consistency",
        "spatial_consistency",
        "third_person",
        "institutional_language",
        "classification_supported",
        "narrative_classification_consistency",
        "format_valid",
    }
)
_SELECTED_FIELDS = frozenset(
    {"factuality", "narrative_structure", "classification", "discarded_elements"}
)
_DECISION_STATUSES = frozenset({"approved", "needs_human_review", "rejected"})
_CONSENSUS_STATUSES = frozenset(
    {"unanimous", "majority", "disagreement", "indeterminate"}
)


def _valid_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _add_issue(
    issues: list[CompletionIssue],
    kind: CompletionIssueKind,
    code: str,
    message: str,
    path: Optional[Path] = None,
) -> None:
    issues.append(CompletionIssue(kind, code, message, path))


def _read_json_object(
    path: Path,
    issues: list[CompletionIssue],
    *,
    missing_code: str,
    invalid_code: str,
) -> Optional[dict]:
    if not path.is_file():
        _add_issue(
            issues,
            CompletionIssueKind.MISSING,
            missing_code,
            f"required file is missing: {path}",
            path,
        )
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (json.JSONDecodeError, OSError, UnicodeError) as error:
        _add_issue(
            issues,
            CompletionIssueKind.INVALID,
            invalid_code,
            f"file is not readable valid JSON: {error}",
            path,
        )
        return None
    if not isinstance(value, dict):
        _add_issue(
            issues,
            CompletionIssueKind.INVALID,
            invalid_code,
            "file must contain one JSON object",
            path,
        )
        return None
    return value


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_adjudication(
    *, statement_index: int, statement_path: Path
) -> tuple[CheckStatus, Optional[dict], tuple[CompletionIssue, ...]]:
    path = statement_path / "adjudication.json"
    issues: list[CompletionIssue] = []
    value = _read_json_object(
        path,
        issues,
        missing_code="adjudication_missing",
        invalid_code="adjudication_json_invalid",
    )
    if value is None:
        return CheckStatus.FAIL, None, tuple(issues)

    def invalid(code: str, message: str) -> None:
        _add_issue(issues, CompletionIssueKind.INVALID, code, message, path)

    if value.get("statement_index") != statement_index:
        invalid("adjudication_statement_mismatch", "statement_index does not match")
    reviewers = value.get("reviewers")
    if (
        not isinstance(reviewers, list)
        or any(not isinstance(reviewer, str) for reviewer in reviewers)
        or len(reviewers) != len(set(reviewers))
        or set(reviewers) != {reviewer.value for reviewer in Reviewer}
    ):
        invalid(
            "adjudication_reviewers_invalid",
            "reviewers must identify chatgpt, gemini, and grok exactly once",
        )

    consensus = value.get("classification_consensus")
    if not isinstance(consensus, dict):
        invalid("adjudication_consensus_invalid", "classification_consensus must be an object")
    else:
        classifications = consensus.get("reviewer_classifications")
        if not isinstance(classifications, dict) or set(classifications) != {
            reviewer.value for reviewer in Reviewer
        } or any(not _nonempty_text(item) for item in classifications.values()):
            invalid(
                "adjudication_reviewer_classifications_invalid",
                "reviewer_classifications must contain three non-empty reviewer values",
            )
        if not _nonempty_text(consensus.get("final_category")):
            invalid("adjudication_final_category_invalid", "final_category must be non-empty")
        if consensus.get("consensus") not in _CONSENSUS_STATUSES:
            invalid("adjudication_consensus_value_invalid", "consensus value is invalid")
        if not _nonempty_text(consensus.get("final_justification")):
            invalid(
                "adjudication_final_justification_invalid",
                "final_justification must be non-empty",
            )

    selected = value.get("selected_elements")
    if not isinstance(selected, dict) or any(
        not _string_list(selected.get(name)) for name in _SELECTED_FIELDS
    ):
        invalid(
            "adjudication_selected_elements_invalid",
            "selected_elements must contain the four required string lists",
        )

    report = value.get("final_report")
    if not isinstance(report, dict):
        invalid("adjudication_final_report_invalid", "final_report must be an object")
    else:
        classification = report.get("crime_classification")
        if (
            not _nonempty_text(report.get("narrative"))
            or not isinstance(report.get("closing"), str)
            or not isinstance(classification, dict)
            or not _nonempty_text(classification.get("category"))
            or not _nonempty_text(classification.get("justification"))
        ):
            invalid(
                "adjudication_final_report_invalid",
                "final_report does not satisfy the required structure",
            )

    quality = value.get("quality_control")
    if not isinstance(quality, dict) or any(
        quality.get(name) not in {"pass", "fail"} for name in _QUALITY_FIELDS
    ):
        invalid(
            "adjudication_quality_control_invalid",
            "quality_control must contain every required pass/fail field",
        )

    decision = value.get("decision")
    if (
        not isinstance(decision, dict)
        or decision.get("status") not in _DECISION_STATUSES
        or not _nonempty_text(decision.get("reason"))
    ):
        invalid(
            "adjudication_decision_invalid",
            "decision requires a valid status and non-empty reason",
        )
    try:
        validate_adjudication_schema(value, statement_index)
    except ValueError as error:
        if not issues:
            invalid("adjudication_schema_invalid", str(error))
    return (CheckStatus.FAIL if issues else CheckStatus.PASS), value, tuple(issues)


def _inspect_histories(
    *, statement_index: int, feedback_root: Path
) -> tuple[CheckStatus, Mapping[BenchmarkModel, dict], tuple[CompletionIssue, ...]]:
    issues: list[CompletionIssue] = []
    records_by_model: dict[BenchmarkModel, dict] = {}
    for model in BenchmarkModel:
        path = feedback_root / f"{model.value}_history.jsonl"
        if not path.is_file():
            _add_issue(
                issues,
                CompletionIssueKind.MISSING,
                "history_file_missing",
                f"history file for {model.value} is missing",
                path,
            )
            continue
        records: list[dict] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError(f"line {line_number} is not an object")
                    records.append(value)
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as error:
            _add_issue(
                issues,
                CompletionIssueKind.INVALID,
                "history_file_invalid",
                f"history file for {model.value} is invalid: {error}",
                path,
            )
            continue
        matches = [record for record in records if record.get("output") == statement_index]
        if not matches:
            _add_issue(
                issues,
                CompletionIssueKind.MISSING,
                "history_record_missing",
                f"history for {model.value} and statement_index={statement_index} is missing",
                path,
            )
            continue
        if len(matches) > 1:
            _add_issue(
                issues,
                CompletionIssueKind.INVALID,
                "history_record_duplicate",
                f"history for {model.value} and statement_index={statement_index} is duplicated",
                path,
            )
            continue
        record = matches[0]
        try:
            validate_history_schema(record, model, statement_index)
        except ValueError:
            _add_issue(
                issues,
                CompletionIssueKind.INVALID,
                "history_record_invalid",
                f"history for {model.value} does not satisfy the strict new-record schema",
                path,
            )
            continue
        records_by_model[model] = record
    return (
        CheckStatus.FAIL if issues else CheckStatus.PASS,
        MappingProxyType(records_by_model),
        tuple(issues),
    )


def _valid_profile(value: object, model: BenchmarkModel) -> bool:
    try:
        validate_profile_schema(value, model)
    except ValueError:
        return False
    return True


def _inspect_profiles(
    *, profiles_root: Path
) -> tuple[CheckStatus, Mapping[BenchmarkModel, dict], tuple[CompletionIssue, ...]]:
    issues: list[CompletionIssue] = []
    profiles: dict[BenchmarkModel, dict] = {}
    for model in BenchmarkModel:
        path = profiles_root / f"{model.value}_profile.json"
        value = _read_json_object(
            path,
            issues,
            missing_code="profile_missing",
            invalid_code="profile_json_invalid",
        )
        if value is None:
            continue
        if not _valid_profile(value, model):
            _add_issue(
                issues,
                CompletionIssueKind.INVALID,
                "profile_invalid",
                f"profile for {model.value} does not satisfy its accumulated schema",
                path,
            )
            continue
        profiles[model] = value
    return (
        CheckStatus.FAIL if issues else CheckStatus.PASS,
        MappingProxyType(profiles),
        tuple(issues),
    )


def _inspect_application_evidence(
    *,
    path: Optional[Path],
    statement_index: int,
    run_id: Optional[str],
    reviews: StatementReviewInspection,
    adjudication: Optional[dict],
    histories: Mapping[BenchmarkModel, dict],
    profiles: Mapping[BenchmarkModel, dict],
) -> ApplicationEvidenceInspection:
    if path is None:
        issue = CompletionIssue(
            CompletionIssueKind.UNCERTAIN,
            "application_evidence_not_configured",
            "no application evidence path was supplied",
        )
        return ApplicationEvidenceInspection(CheckStatus.UNVERIFIED, None, issues=(issue,))
    evidence_path = Path(path)
    issues: list[CompletionIssue] = []
    evidence = _read_json_object(
        evidence_path,
        issues,
        missing_code="application_evidence_missing",
        invalid_code="application_evidence_json_invalid",
    )
    if evidence is None:
        if issues and issues[0].kind is CompletionIssueKind.MISSING:
            issue = issues[0]
            issues[0] = CompletionIssue(
                CompletionIssueKind.UNCERTAIN, issue.code, issue.message, issue.path
            )
            return ApplicationEvidenceInspection(
                CheckStatus.UNVERIFIED, evidence_path, issues=issues
            )
        return ApplicationEvidenceInspection(CheckStatus.FAIL, evidence_path, issues=issues)

    def invalid(code: str, message: str) -> None:
        _add_issue(issues, CompletionIssueKind.INVALID, code, message, evidence_path)

    evidence_version = evidence.get("schema_version")
    if evidence_version not in {1, 2}:
        invalid("application_evidence_version_invalid", "schema_version must be 1 or 2")
    if evidence.get("statement_index") != statement_index:
        invalid("application_evidence_statement_mismatch", "statement_index does not match")
    if evidence.get("run_id") != run_id or not _nonempty_text(run_id):
        invalid("application_evidence_run_mismatch", "run_id does not match validated inputs")

    attempts = evidence.get("review_attempts")
    if not isinstance(attempts, dict) or set(attempts) != {
        reviewer.value for reviewer in Reviewer
    }:
        invalid("application_evidence_reviews_invalid", "review_attempts must identify all reviewers")
    else:
        for reviewer in Reviewer:
            complete = reviews.reviewers[reviewer].complete_result
            recorded = attempts.get(reviewer.value)
            if (
                complete is None
                or not isinstance(recorded, dict)
                or recorded.get("task_id") != complete.task_id
                or recorded.get("attempt_id") != complete.attempt_id
            ):
                invalid(
                    "application_evidence_review_mismatch",
                    f"review attempt evidence does not match {reviewer.value}",
                )

    if adjudication is None or evidence.get("review_adjudication_sha256") != _canonical_sha256(
        adjudication
    ):
        invalid(
            "application_evidence_adjudication_mismatch",
            "review adjudication digest does not match adjudication.json",
        )

    history_application = evidence.get("history_application")
    if not isinstance(history_application, dict) or not _nonempty_text(
        history_application.get("adjudication_id")
    ):
        invalid(
            "history_application_evidence_invalid",
            "history application requires a non-empty adjudication_id",
        )
    else:
        recorded = history_application.get("records")
        if not isinstance(recorded, dict) or set(recorded) != {
            model.value for model in BenchmarkModel
        }:
            invalid(
                "history_application_evidence_invalid",
                "history evidence must identify all benchmark models",
            )
        else:
            for model in BenchmarkModel:
                if model not in histories or recorded.get(model.value) != _canonical_sha256(
                    histories[model]
                ):
                    invalid(
                        "history_application_evidence_mismatch",
                        f"history evidence does not match {model.value}",
                    )

    profile_application = evidence.get("profile_application")
    if not isinstance(profile_application, dict) or not _nonempty_text(
        profile_application.get("adjudication_id")
    ):
        invalid(
            "profile_application_evidence_invalid",
            "profile application requires a non-empty adjudication_id",
        )
    else:
        recorded = profile_application.get("profiles")
        if not isinstance(recorded, dict) or set(recorded) != {
            model.value for model in BenchmarkModel
        }:
            invalid(
                "profile_application_evidence_invalid",
                "profile evidence must identify all benchmark models",
            )
        else:
            for model in BenchmarkModel:
                current = profiles.get(model)
                item = recorded.get(model.value)
                metadata = None if current is None else current.get("profile_metadata")
                exact_current = (
                    current is not None
                    and isinstance(item, dict)
                    and item.get("sha256") == _canonical_sha256(current)
                    and isinstance(metadata, dict)
                    and item.get("outputs_reviewed") == metadata.get("outputs_reviewed")
                    and item.get("version") == metadata.get("version")
                )
                profile_matches = exact_current
                if evidence_version == 2 and isinstance(item, dict):
                    profile_matches = False
                    applied = item.get("applied_profile")
                    try:
                        if (
                            isinstance(applied, dict)
                            and item.get("sha256") == _canonical_sha256(applied)
                            and item.get("outputs_reviewed")
                            == applied.get("profile_metadata", {}).get("outputs_reviewed")
                            and item.get("version")
                            == applied.get("profile_metadata", {}).get("version")
                            and current is not None
                        ):
                            validate_profile_transition(applied, current, model)
                            profile_matches = True
                    except (AttributeError, TypeError, ValueError):
                        profile_matches = False
                if not profile_matches:
                    invalid(
                        "profile_application_evidence_mismatch",
                        f"profile evidence does not match {model.value}",
                    )

    if evidence.get("temporary_artifacts_cleaned") is not True:
        invalid(
            "temporary_cleanup_unconfirmed",
            "temporary_artifacts_cleaned must be true",
        )
    return ApplicationEvidenceInspection(
        CheckStatus.FAIL if issues else CheckStatus.PASS,
        evidence_path,
        evidence=evidence,
        issues=issues,
    )


def inspect_statement_completeness(
    *,
    statement_index: int,
    statements_path: Path,
    runs_root: Path,
    review_cases_root: Path,
    feedback_root: Path,
    profiles_root: Path,
    run: Optional[str | Path] = None,
    provisional_files: Optional[Mapping[Reviewer, ProvisionalArtifactFiles]] = None,
    application_evidence_path: Optional[Path] = None,
) -> StatementCompletionInspection:
    """Inspect one statement without writing, recovering, or fabricating evidence."""

    if not _valid_index(statement_index):
        raise ValueError("statement_index must be a non-negative integer")
    if statement_index in HISTORICAL_STATEMENT_RANGE:
        evidence = ApplicationEvidenceInspection(CheckStatus.NOT_APPLICABLE, None)
        return StatementCompletionInspection(
            statement_index=statement_index,
            status=StatementCompletionStatus.HISTORICAL_COMPLETE,
            checks={
                "historical_policy": CheckStatus.PASS,
                "inputs": CheckStatus.NOT_APPLICABLE,
                "reviews": CheckStatus.NOT_APPLICABLE,
                "adjudication": CheckStatus.NOT_APPLICABLE,
                "histories": CheckStatus.NOT_APPLICABLE,
                "profiles": CheckStatus.NOT_APPLICABLE,
                "application_evidence": CheckStatus.NOT_APPLICABLE,
            },
            input_validation=None,
            review_inspection=None,
            application_evidence=evidence,
        )

    inputs = inspect_review_inputs(
        statement_index=statement_index,
        statements_path=Path(statements_path),
        runs_root=Path(runs_root),
        run=run,
    )
    reviews = inspect_reviewer_results(
        statement_index=statement_index,
        review_cases_root=Path(review_cases_root),
        provisional_files=provisional_files,
    )
    statement_path = Path(review_cases_root) / f"statement_{statement_index:03d}"
    adjudication_status, adjudication, adjudication_issues = _validate_adjudication(
        statement_index=statement_index, statement_path=statement_path
    )
    history_status, histories, history_issues = _inspect_histories(
        statement_index=statement_index, feedback_root=Path(feedback_root)
    )
    profile_status, profiles, profile_issues = _inspect_profiles(
        profiles_root=Path(profiles_root)
    )

    review_status = CheckStatus.PASS
    review_issues: list[CompletionIssue] = []
    if any(
        item.status in {ReviewerStatus.INCOMPLETE, ReviewerStatus.INVALID, ReviewerStatus.NOT_STARTED}
        for item in reviews.reviewers.values()
    ):
        review_status = CheckStatus.FAIL
    elif any(
        item.status is ReviewerStatus.UNVERIFIED for item in reviews.reviewers.values()
    ):
        review_status = CheckStatus.UNVERIFIED
    for reviewer, item in reviews.reviewers.items():
        kind = (
            CompletionIssueKind.UNCERTAIN
            if item.status is ReviewerStatus.UNVERIFIED
            else CompletionIssueKind.INVALID
        )
        for issue in (*item.structural_issues, *item.provenance_issues):
            _add_issue(
                review_issues,
                kind,
                f"review_{reviewer.value}_{issue.code}",
                issue.message,
                issue.path,
            )
        if item.status is ReviewerStatus.NOT_STARTED:
            _add_issue(
                review_issues,
                CompletionIssueKind.MISSING,
                f"review_{reviewer.value}_not_started",
                f"{reviewer.value} review has not started",
                statement_path,
            )

    evidence = _inspect_application_evidence(
        path=application_evidence_path,
        statement_index=statement_index,
        run_id=inputs.selected_run_id,
        reviews=reviews,
        adjudication=adjudication,
        histories=histories,
        profiles=profiles,
    )
    applications_root = statement_path / ".adjudication_applications"
    # Version 1 was an evidence-only read contract. Version 2 is backed by
    # stored preimages, prepared targets, and an application manifest; its
    # evidence file alone cannot prove that the six destinations were applied.
    requires_stored_application = (
        evidence.evidence is not None
        and evidence.evidence.get("schema_version") == 2
    )
    if requires_stored_application and not applications_root.is_dir():
        evidence = ApplicationEvidenceInspection(
            CheckStatus.FAIL, evidence.path, evidence=evidence.evidence,
            issues=(*evidence.issues, CompletionIssue(
                CompletionIssueKind.MISSING, "application_storage_missing",
                "v2 application storage is required to verify the six destinations",
                applications_root,
            )),
        )
    elif evidence.status is CheckStatus.UNVERIFIED and applications_root.exists():
        evidence = ApplicationEvidenceInspection(
            CheckStatus.FAIL, evidence.path, evidence=evidence.evidence,
            issues=(*evidence.issues, CompletionIssue(
                CompletionIssueKind.MISSING, "application_evidence_required",
                "stored application requires verifiable final evidence",
                evidence.path,
            )),
        )
    if evidence.status is CheckStatus.PASS and applications_root.exists():
        # Preserve version 1 evidence-only reads. Stored material, when present,
        # must corroborate the evidence and the accumulated destinations.
        from .adjudication_storage import ApplicationPhase, inspect_adjudication_application

        try:
            application_id = evidence.evidence["history_application"]["adjudication_id"]
            if application_id != evidence.evidence["profile_application"]["adjudication_id"]:
                raise ValueError("history and profile application identities disagree")
            candidates = list(applications_root.iterdir())
            if len(candidates) != 1 or candidates[0].name != application_id:
                raise ValueError("application evidence does not identify unique stored material")
            application = inspect_adjudication_application(
                application_path=candidates[0], statement_path=statement_path,
                feedback_root=Path(feedback_root), profiles_root=Path(profiles_root),
                application_evidence_path=evidence.path,
            )
            if (
                application.phase is not ApplicationPhase.APPLIED
                or application.statement_index != statement_index
                or application.run_id != inputs.selected_run_id
            ):
                raise ValueError("stored application or accumulated destinations are invalid")
        except (KeyError, TypeError, ValueError, OSError) as error:
            evidence = ApplicationEvidenceInspection(
                CheckStatus.FAIL, evidence.path, evidence=evidence.evidence,
                issues=(*evidence.issues, CompletionIssue(
                    CompletionIssueKind.INVALID, "application_storage_evidence_mismatch",
                    str(error), applications_root,
                )),
            )
    input_status = CheckStatus.PASS if inputs.inputs_complete else CheckStatus.FAIL
    checks = {
        "historical_policy": CheckStatus.NOT_APPLICABLE,
        "inputs": input_status,
        "reviews": review_status,
        "adjudication": adjudication_status,
        "histories": history_status,
        "profiles": profile_status,
        "application_evidence": evidence.status,
    }

    input_issues = tuple(
        CompletionIssue(
            CompletionIssueKind.MISSING
            if issue.category.value == "missing"
            else CompletionIssueKind.INVALID,
            f"input_{issue.code}",
            issue.message,
            issue.path,
        )
        for issue in inputs.issues
    )
    issues = (
        *input_issues,
        *review_issues,
        *adjudication_issues,
        *history_issues,
        *profile_issues,
        *evidence.issues,
    )
    if CheckStatus.FAIL in checks.values():
        status = StatementCompletionStatus.INCOMPLETE
    elif CheckStatus.UNVERIFIED in checks.values():
        status = StatementCompletionStatus.UNVERIFIED
    else:
        status = StatementCompletionStatus.COMPLETE
    missing_dependencies = tuple(
        dict.fromkeys(
            issue.code
            for issue in issues
            if issue.kind in {CompletionIssueKind.MISSING, CompletionIssueKind.UNCERTAIN}
        )
    )
    return StatementCompletionInspection(
        statement_index=statement_index,
        status=status,
        checks=checks,
        input_validation=inputs,
        review_inspection=reviews,
        application_evidence=evidence,
        missing_dependencies=missing_dependencies,
        issues=issues,
    )


def select_statement_from_verification(
    review_cases_root: Path,
    verification: StatementCompletionInspection,
    *,
    earlier_verifications: Optional[Mapping[int, StatementCompletionInspection]] = None,
) -> StatementSelection:
    """Adapt ordered global verifications to the existing selector contract.

    Active predecessors are never inferred complete from their directories or
    artifacts.  The caller must supply the completion inspection that was
    obtained for every predecessor before processing the latest statement.
    """

    if not isinstance(verification, StatementCompletionInspection):
        raise ValueError("verification must be a StatementCompletionInspection")
    if earlier_verifications is None:
        earlier_verifications = {}
    if not isinstance(earlier_verifications, Mapping):
        raise ValueError("earlier_verifications must be a mapping")
    normalized_earlier: dict[int, StatementCompletionInspection] = {}
    for index, earlier in earlier_verifications.items():
        if not isinstance(index, int) or isinstance(index, bool) or index < 10:
            raise ValueError("earlier verification indices must be active statement indices")
        if not isinstance(earlier, StatementCompletionInspection):
            raise ValueError("earlier verifications must contain StatementCompletionInspection values")
        if earlier.statement_index != index:
            raise ValueError("earlier verification key does not match its statement_index")
        normalized_earlier[index] = earlier
    discovery = discover_statements(Path(review_cases_root))
    if discovery.error is None and not discovery.duplicate_indices and discovery.latest is not None:
        matching = next(
            (
                item
                for item in discovery.statements
                if item.statement_index == verification.statement_index
            ),
            None,
        )
        if matching is None:
            return StatementSelection(
                action=SelectionAction.PENDING,
                statement_index=None,
                statement_path=None,
                latest_existing_index=discovery.latest.statement_index,
                reason="verification does not correspond to an existing statement",
            )
        if matching.statement_index != discovery.latest.statement_index:
            if verification.selector_completion is CompletionState.INCOMPLETE:
                return StatementSelection(
                    action=SelectionAction.RESUME,
                    statement_index=matching.statement_index,
                    statement_path=matching.path,
                    latest_existing_index=discovery.latest.statement_index,
                    reason="an earlier statement is confirmed incomplete",
                )
            return StatementSelection(
                action=SelectionAction.PENDING,
                statement_index=None,
                statement_path=None,
                latest_existing_index=discovery.latest.statement_index,
                reason="later statement directories exist and require ordered verification",
            )
        earlier_new = [
            item for item in discovery.statements
            if item.statement_index >= 10 and item.statement_index < matching.statement_index
        ]
        expected_earlier = {item.statement_index for item in earlier_new}
        unexpected = set(normalized_earlier) - expected_earlier
        if unexpected:
            unexpected_text = ", ".join(str(index) for index in sorted(unexpected))
            raise ValueError(
                f"earlier verifications do not precede the active statement: {unexpected_text}"
            )
        for earlier in earlier_new:
            earlier_verification = normalized_earlier.get(earlier.statement_index)
            if earlier_verification is None:
                return StatementSelection(
                    action=SelectionAction.PENDING,
                    statement_index=None,
                    statement_path=None,
                    latest_existing_index=discovery.latest.statement_index,
                    reason=(
                        f"statement_{earlier.statement_index:03d} has not been verified; "
                        "ordered completion cannot be established"
                    ),
                )
            if earlier_verification.status is StatementCompletionStatus.INCOMPLETE:
                return StatementSelection(
                    action=SelectionAction.RESUME,
                    statement_index=earlier.statement_index,
                    statement_path=earlier.path,
                    latest_existing_index=discovery.latest.statement_index,
                    reason=f"statement_{earlier.statement_index:03d} is confirmed incomplete",
                )
            if earlier_verification.status is not StatementCompletionStatus.COMPLETE:
                return StatementSelection(
                    action=SelectionAction.PENDING,
                    statement_index=None,
                    statement_path=None,
                    latest_existing_index=discovery.latest.statement_index,
                    reason=(
                        f"statement_{earlier.statement_index:03d} is not verified complete"
                    ),
                )
    return select_statement(
        Path(review_cases_root), latest_completion=verification.selector_completion
    )
