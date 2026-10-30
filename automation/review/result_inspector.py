"""Read-only inspection of persisted reviewer result sets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence

from automation.contracts import (
    BenchmarkModel,
    CompleteReviewResult,
    HistoryProposalOutput,
    MainReviewOutput,
    ProfileProposalOutput,
    Reviewer,
)

from .review_storage import (
    resolve_recoverable_unconfirmed_attempt,
    resolve_confirmed_review_set,
)


class ReviewerStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    NOT_STARTED = "NOT_STARTED"
    UNVERIFIED = "UNVERIFIED"


class RecoveryAction(str, Enum):
    KEEP = "KEEP"
    REPEAT_FULL_REVIEW = "REPEAT_FULL_REVIEW"
    START_FULL_REVIEW = "START_FULL_REVIEW"
    BLOCK_FOR_PROVENANCE = "BLOCK_FOR_PROVENANCE"


class IssueDomain(str, Enum):
    STRUCTURE = "structure"
    PROVENANCE = "provenance"


@dataclass(frozen=True)
class InspectionIssue:
    domain: IssueDomain
    code: str
    message: str
    path: Optional[Path] = None


@dataclass(frozen=True)
class ProvisionalArtifactFiles:
    """Explicit paths supplied by the caller; no filename convention is inferred."""

    histories: Sequence[Path]
    profile_proposals: Sequence[Path]

    def __post_init__(self) -> None:
        object.__setattr__(self, "histories", tuple(Path(path) for path in self.histories))
        object.__setattr__(
            self,
            "profile_proposals",
            tuple(Path(path) for path in self.profile_proposals),
        )


@dataclass(frozen=True)
class ReviewerInspection:
    reviewer: Reviewer
    status: ReviewerStatus
    recovery_action: RecoveryAction
    structural_issues: Sequence[InspectionIssue] = field(default_factory=tuple)
    provenance_issues: Sequence[InspectionIssue] = field(default_factory=tuple)
    complete_result: Optional[CompleteReviewResult] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "structural_issues", tuple(self.structural_issues))
        object.__setattr__(self, "provenance_issues", tuple(self.provenance_issues))


@dataclass(frozen=True)
class StatementReviewInspection:
    statement_index: int
    statement_path: Path
    historical_adjudication_present: bool
    reviewers: Mapping[Reviewer, ReviewerInspection]

    def __post_init__(self) -> None:
        object.__setattr__(self, "reviewers", MappingProxyType(dict(self.reviewers)))

    @property
    def adjudication_enabled(self) -> bool:
        return (
            set(self.reviewers) == set(Reviewer)
            and all(
                result.status is ReviewerStatus.COMPLETE
                for result in self.reviewers.values()
            )
        )


_INSPECTION_ATTEMPT = "__structural_validation_only__"
_PROVENANCE_FIELDS = ("task_id", "attempt_id", "statement_index", "reviewer")


def _issue(
    target: list[InspectionIssue],
    domain: IssueDomain,
    code: str,
    message: str,
    path: Optional[Path] = None,
) -> None:
    target.append(InspectionIssue(domain, code, message, path))


def _read_object(path: Path, issues: list[InspectionIssue]) -> Optional[dict]:
    if not path.is_file():
        _issue(
            issues,
            IssueDomain.STRUCTURE,
            "artifact_missing",
            f"required artifact is missing: {path}",
            path,
        )
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (json.JSONDecodeError, OSError, UnicodeError) as error:
        _issue(
            issues,
            IssueDomain.STRUCTURE,
            "artifact_json_invalid",
            f"could not read valid JSON: {error}",
            path,
        )
        return None
    if not isinstance(value, dict):
        _issue(
            issues,
            IssueDomain.STRUCTURE,
            "artifact_not_object",
            "artifact must contain one JSON object",
            path,
        )
        return None
    return value


def _reviewer_matches(value: object, reviewer: Reviewer) -> bool:
    return isinstance(value, str) and value.casefold() == reviewer.value.casefold()


def _validate_optional_identity(
    record: Mapping[str, object],
    *,
    reviewer: Reviewer,
    statement_index: int,
    path: Path,
    structural_issues: list[InspectionIssue],
) -> None:
    if "reviewer" in record and not _reviewer_matches(record.get("reviewer"), reviewer):
        _issue(
            structural_issues,
            IssueDomain.STRUCTURE,
            "reviewer_mismatch",
            f"artifact reviewer does not identify {reviewer.value}",
            path,
        )
    if "statement_index" in record and record.get("statement_index") != statement_index:
        _issue(
            structural_issues,
            IssueDomain.STRUCTURE,
            "statement_index_mismatch",
            "artifact statement_index does not match the inspected statement",
            path,
        )


def _validate_main_review(
    record: dict,
    *,
    reviewer: Reviewer,
    statement_index: int,
    path: Path,
    issues: list[InspectionIssue],
) -> Optional[MainReviewOutput]:
    if not _reviewer_matches(record.get("reviewer"), reviewer):
        _issue(
            issues,
            IssueDomain.STRUCTURE,
            "reviewer_mismatch",
            f"main review does not identify {reviewer.value}",
            path,
        )
        return None
    _validate_optional_identity(
        record,
        reviewer=reviewer,
        statement_index=statement_index,
        path=path,
        structural_issues=issues,
    )
    normalized = dict(record)
    normalized["reviewer"] = reviewer.value
    attempt_id = record.get("attempt_id")
    validation_attempt = attempt_id if isinstance(attempt_id, str) and attempt_id else _INSPECTION_ATTEMPT
    try:
        return MainReviewOutput(
            statement_index=statement_index,
            reviewer=reviewer,
            attempt_id=validation_attempt,
            output=normalized,
        )
    except ValueError as error:
        _issue(
            issues,
            IssueDomain.STRUCTURE,
            "main_review_invalid",
            str(error),
            path,
        )
        return None


def _validate_histories(
    records: Sequence[tuple[Path, dict]],
    *,
    reviewer: Reviewer,
    statement_index: int,
    issues: list[InspectionIssue],
) -> tuple[HistoryProposalOutput, ...]:
    results: list[HistoryProposalOutput] = []
    for path, record in records:
        _validate_optional_identity(
            record,
            reviewer=reviewer,
            statement_index=statement_index,
            path=path,
            structural_issues=issues,
        )
        try:
            model = BenchmarkModel(record.get("model"))
        except (TypeError, ValueError):
            _issue(
                issues,
                IssueDomain.STRUCTURE,
                "history_model_invalid",
                "history model must identify gemma, mistral, or llama",
                path,
            )
            continue
        attempt_id = record.get("attempt_id")
        validation_attempt = attempt_id if isinstance(attempt_id, str) and attempt_id else _INSPECTION_ATTEMPT
        try:
            results.append(
                HistoryProposalOutput(
                    statement_index=statement_index,
                    reviewer=reviewer,
                    attempt_id=validation_attempt,
                    model=model,
                    output=record,
                )
            )
        except ValueError as error:
            _issue(
                issues,
                IssueDomain.STRUCTURE,
                "history_invalid",
                str(error),
                path,
            )
    return tuple(results)


def _validate_profiles(
    records: Sequence[tuple[Path, dict]],
    *,
    reviewer: Reviewer,
    statement_index: int,
    issues: list[InspectionIssue],
) -> tuple[ProfileProposalOutput, ...]:
    results: list[ProfileProposalOutput] = []
    for path, record in records:
        _validate_optional_identity(
            record,
            reviewer=reviewer,
            statement_index=statement_index,
            path=path,
            structural_issues=issues,
        )
        try:
            model = BenchmarkModel(record.get("model_name"))
        except (TypeError, ValueError):
            _issue(
                issues,
                IssueDomain.STRUCTURE,
                "profile_model_invalid",
                "profile model_name must identify gemma, mistral, or llama",
                path,
            )
            continue
        attempt_id = record.get("attempt_id")
        validation_attempt = attempt_id if isinstance(attempt_id, str) and attempt_id else _INSPECTION_ATTEMPT
        try:
            results.append(
                ProfileProposalOutput(
                    statement_index=statement_index,
                    reviewer=reviewer,
                    attempt_id=validation_attempt,
                    model=model,
                    output=record,
                )
            )
        except ValueError as error:
            _issue(
                issues,
                IssueDomain.STRUCTURE,
                "profile_proposal_invalid",
                str(error),
                path,
            )
    return tuple(results)


def _check_provenance(
    artifacts: Sequence[tuple[Path, dict]],
    *,
    reviewer: Reviewer,
    statement_index: int,
) -> tuple[Optional[tuple[str, str]], tuple[InspectionIssue, ...]]:
    issues: list[InspectionIssue] = []
    task_ids: set[str] = set()
    attempt_ids: set[str] = set()
    for path, record in artifacts:
        missing = [field for field in _PROVENANCE_FIELDS if field not in record]
        if missing:
            _issue(
                issues,
                IssueDomain.PROVENANCE,
                "provenance_missing",
                f"artifact lacks provenance fields: {', '.join(missing)}",
                path,
            )
            continue
        if record.get("statement_index") != statement_index:
            _issue(
                issues,
                IssueDomain.PROVENANCE,
                "provenance_statement_mismatch",
                "provenance statement_index is inconsistent",
                path,
            )
        if not _reviewer_matches(record.get("reviewer"), reviewer):
            _issue(
                issues,
                IssueDomain.PROVENANCE,
                "provenance_reviewer_mismatch",
                "provenance reviewer is inconsistent",
                path,
            )
        for field_name in ("task_id", "attempt_id"):
            value = record.get(field_name)
            if not isinstance(value, str) or not value.strip():
                _issue(
                    issues,
                    IssueDomain.PROVENANCE,
                    "provenance_identifier_invalid",
                    f"{field_name} must be a non-empty string",
                    path,
                )
            elif field_name == "task_id":
                task_ids.add(value)
            else:
                attempt_ids.add(value)

    if len(task_ids) > 1 or len(attempt_ids) > 1:
        _issue(
            issues,
            IssueDomain.PROVENANCE,
            "provenance_mixed_execution",
            "reviewer artifacts mix task_id or attempt_id values",
        )
        return None, tuple(issues)
    if issues or len(task_ids) != 1 or len(attempt_ids) != 1:
        return None, tuple(issues)
    return (task_ids.pop(), attempt_ids.pop()), ()


def _recovery_for(status: ReviewerStatus) -> RecoveryAction:
    if status is ReviewerStatus.COMPLETE:
        return RecoveryAction.KEEP
    if status in {ReviewerStatus.INCOMPLETE, ReviewerStatus.INVALID}:
        return RecoveryAction.REPEAT_FULL_REVIEW
    if status is ReviewerStatus.NOT_STARTED:
        return RecoveryAction.START_FULL_REVIEW
    return RecoveryAction.BLOCK_FOR_PROVENANCE


def _inspect_one_reviewer(
    *,
    statement_path: Path,
    statement_index: int,
    reviewer: Reviewer,
    files: Optional[ProvisionalArtifactFiles],
    historical: bool,
    main_path: Optional[Path] = None,
) -> ReviewerInspection:
    main_path = (
        statement_path / f"{reviewer.value}_review.json"
        if main_path is None
        else Path(main_path)
    )
    configured_paths = () if files is None else (*files.histories, *files.profile_proposals)
    any_artifact_exists = main_path.is_file() or any(path.is_file() for path in configured_paths)
    if not any_artifact_exists:
        status = ReviewerStatus.NOT_STARTED
        return ReviewerInspection(reviewer, status, _recovery_for(status))

    structural_issues: list[InspectionIssue] = []
    provenance_issues: list[InspectionIssue] = []
    main_record = _read_object(main_path, structural_issues)
    main_result = None
    if main_record is not None:
        main_result = _validate_main_review(
            main_record,
            reviewer=reviewer,
            statement_index=statement_index,
            path=main_path,
            issues=structural_issues,
        )

    if files is None:
        if historical:
            message = "historical adjudication exists, but removed provisional artifacts do not prove execution integrity"
        else:
            message = "provisional artifact paths are not configured"
        _issue(
            provenance_issues,
            IssueDomain.PROVENANCE,
            "provisional_artifacts_unavailable",
            message,
            statement_path,
        )
        status = ReviewerStatus.INVALID if structural_issues else ReviewerStatus.UNVERIFIED
        return ReviewerInspection(
            reviewer,
            status,
            _recovery_for(status),
            structural_issues,
            provenance_issues,
        )

    if len(files.histories) < len(BenchmarkModel):
        _issue(
            structural_issues,
            IssueDomain.STRUCTURE,
            "artifact_missing",
            "three explicit provisional history paths are required",
            statement_path,
        )
    elif len(files.histories) > len(BenchmarkModel):
        _issue(
            structural_issues,
            IssueDomain.STRUCTURE,
            "artifact_set_invalid",
            "exactly three provisional history paths are allowed",
            statement_path,
        )
    if len(files.profile_proposals) < len(BenchmarkModel):
        _issue(
            structural_issues,
            IssueDomain.STRUCTURE,
            "artifact_missing",
            "three explicit provisional profile paths are required",
            statement_path,
        )
    elif len(files.profile_proposals) > len(BenchmarkModel):
        _issue(
            structural_issues,
            IssueDomain.STRUCTURE,
            "artifact_set_invalid",
            "exactly three provisional profile paths are allowed",
            statement_path,
        )

    history_records: list[tuple[Path, dict]] = []
    for path in files.histories:
        record = _read_object(path, structural_issues)
        if record is not None:
            history_records.append((path, record))
    profile_records: list[tuple[Path, dict]] = []
    for path in files.profile_proposals:
        record = _read_object(path, structural_issues)
        if record is not None:
            profile_records.append((path, record))

    histories = _validate_histories(
        history_records,
        reviewer=reviewer,
        statement_index=statement_index,
        issues=structural_issues,
    )
    profiles = _validate_profiles(
        profile_records,
        reviewer=reviewer,
        statement_index=statement_index,
        issues=structural_issues,
    )

    missing_required = any(issue.code == "artifact_missing" for issue in structural_issues)
    invalid_structure = any(issue.code != "artifact_missing" for issue in structural_issues)
    if missing_required and not invalid_structure:
        status = ReviewerStatus.INCOMPLETE
        return ReviewerInspection(
            reviewer, status, _recovery_for(status), structural_issues
        )
    if invalid_structure or main_result is None:
        status = ReviewerStatus.INVALID
        return ReviewerInspection(
            reviewer, status, _recovery_for(status), structural_issues
        )

    all_records = [(main_path, main_record), *history_records, *profile_records]
    provenance, provenance_result_issues = _check_provenance(
        all_records,
        reviewer=reviewer,
        statement_index=statement_index,
    )
    provenance_issues.extend(provenance_result_issues)
    if provenance is None:
        mixed = any(
            issue.code == "provenance_mixed_execution" for issue in provenance_issues
        )
        status = ReviewerStatus.INVALID if mixed else ReviewerStatus.UNVERIFIED
        return ReviewerInspection(
            reviewer,
            status,
            _recovery_for(status),
            structural_issues,
            provenance_issues,
        )

    task_id, attempt_id = provenance
    try:
        complete = CompleteReviewResult(
            task_id=task_id,
            attempt_id=attempt_id,
            statement_index=statement_index,
            reviewer=reviewer,
            main_review=main_result,
            histories=histories,
            profile_proposals=profiles,
            created_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
    except ValueError as error:
        _issue(
            structural_issues,
            IssueDomain.STRUCTURE,
            "complete_review_invalid",
            str(error),
            statement_path,
        )
        status = ReviewerStatus.INVALID
        return ReviewerInspection(
            reviewer, status, _recovery_for(status), structural_issues
        )

    status = ReviewerStatus.COMPLETE
    return ReviewerInspection(
        reviewer,
        status,
        _recovery_for(status),
        complete_result=complete,
    )


def inspect_reviewer_results(
    *,
    statement_index: int,
    review_cases_root: Path,
    provisional_files: Optional[Mapping[Reviewer, ProvisionalArtifactFiles]] = None,
) -> StatementReviewInspection:
    """Inspect all reviewers for one statement without writing or recovering."""

    if not isinstance(statement_index, int) or isinstance(statement_index, bool) or statement_index < 0:
        raise ValueError("statement_index must be a non-negative integer")
    root = Path(review_cases_root)
    statement_path = root / f"statement_{statement_index:03d}"
    historical = (statement_path / "adjudication.json").is_file()
    configured = {} if provisional_files is None else dict(provisional_files)
    results = {}
    for reviewer in Reviewer:
        files = configured.get(reviewer)
        main_path = None
        confirmed, storage_error = resolve_confirmed_review_set(
            statement_path=statement_path,
            statement_index=statement_index,
            reviewer=reviewer,
        )
        if storage_error is not None:
            issue = InspectionIssue(
                IssueDomain.STRUCTURE,
                "confirmed_set_invalid",
                storage_error,
                statement_path,
            )
            status = ReviewerStatus.INVALID
            results[reviewer] = ReviewerInspection(
                reviewer,
                status,
                _recovery_for(status),
                structural_issues=(issue,),
            )
            continue
        if confirmed is not None:
            main_path = confirmed.artifacts.main_review
            files = ProvisionalArtifactFiles(
                histories=tuple(
                    confirmed.artifacts.histories[model]
                    for model in BenchmarkModel
                ),
                profile_proposals=tuple(
                    confirmed.artifacts.profile_proposals[model]
                    for model in BenchmarkModel
                ),
            )
        elif files is None:
            recovered, recovery_error = resolve_recoverable_unconfirmed_attempt(
                statement_path=statement_path,
                statement_index=statement_index,
                reviewer=reviewer,
            )
            if recovered is not None:
                issue = InspectionIssue(
                    IssueDomain.STRUCTURE,
                    "unconfirmed_projection",
                    "a complete immutable set was projected but never activated",
                    statement_path / f"{reviewer.value}_review.json",
                )
                status = ReviewerStatus.INCOMPLETE
                results[reviewer] = ReviewerInspection(
                    reviewer,
                    status,
                    _recovery_for(status),
                    structural_issues=(issue,),
                )
                continue
            if recovery_error is not None:
                issue = InspectionIssue(
                    IssueDomain.PROVENANCE,
                    "retry_identity_unverified",
                    recovery_error,
                    statement_path / f"{reviewer.value}_review.json",
                )
                status = ReviewerStatus.UNVERIFIED
                results[reviewer] = ReviewerInspection(
                    reviewer,
                    status,
                    _recovery_for(status),
                    provenance_issues=(issue,),
                )
                continue
        results[reviewer] = _inspect_one_reviewer(
            statement_path=statement_path,
            statement_index=statement_index,
            reviewer=reviewer,
            files=files,
            historical=historical,
            main_path=main_path,
        )
    return StatementReviewInspection(
        statement_index=statement_index,
        statement_path=statement_path,
        historical_adjudication_present=historical,
        reviewers=results,
    )
