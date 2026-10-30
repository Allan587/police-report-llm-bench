"""Safe file persistence for one complete reviewer result set."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Optional

from automation.contracts import (
    BenchmarkModel,
    CompleteReviewResult,
    HistoryProposalOutput,
    MainReviewOutput,
    ProfileProposalOutput,
    Reviewer,
)
from .path_safety import confined_path, review_statement_path


LEGACY_STORAGE_SCHEMA_VERSION = 1
STORAGE_SCHEMA_VERSION = 2
_SUPPORTED_STORAGE_SCHEMA_VERSIONS = frozenset(
    {LEGACY_STORAGE_SCHEMA_VERSION, STORAGE_SCHEMA_VERSION}
)
_SET_ID = re.compile(r"^[0-9a-f]{64}$")
_DEDUPLICATION_KEY = re.compile(r"^[0-9a-f]{64}$")
_HISTORICAL_INDICES = range(0, 10)


class ReviewPublishStatus(str, Enum):
    CREATED = "CREATED"
    REPLACED = "REPLACED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class ReviewerArtifactPaths:
    main_review: Path
    histories: Mapping[BenchmarkModel, Path]
    profile_proposals: Mapping[BenchmarkModel, Path]


@dataclass(frozen=True)
class ConfirmedReviewSet:
    statement_index: int
    reviewer: Reviewer
    task_id: str
    attempt_id: str
    set_id: str
    set_path: Path
    manifest_path: Path
    active_reference_path: Path
    artifacts: ReviewerArtifactPaths
    attempt_metadata: Optional["ReviewAttemptMetadata"] = None


@dataclass(frozen=True)
class ReviewAttemptMetadata:
    """Minimal persistent identity needed to continue a logical review task."""

    attempt_number: int
    run_id: str
    task_deduplication_key: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.attempt_number, int)
            or isinstance(self.attempt_number, bool)
            or self.attempt_number < 1
        ):
            raise ValueError("attempt_number must be a positive integer")
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("run_id must be a non-empty string")
        if (
            not isinstance(self.task_deduplication_key, str)
            or _DEDUPLICATION_KEY.fullmatch(self.task_deduplication_key) is None
        ):
            raise ValueError("task_deduplication_key must be a SHA-256 hex digest")


@dataclass(frozen=True)
class RecoveredReviewAttempt:
    statement_index: int
    reviewer: Reviewer
    task_id: str
    attempt_id: str
    attempt_number: int
    run_id: str
    task_deduplication_key: str
    set_path: Path


@dataclass(frozen=True)
class ReviewPublishResult:
    status: ReviewPublishStatus
    confirmed: ConfirmedReviewSet
    permanent_review_path: Path


FailureInjector = Callable[[str], None]


def reviewer_storage_root(statement_path: Path, reviewer: Reviewer) -> Path:
    return Path(statement_path) / ".review_sets" / reviewer.value


def active_reference_path(statement_path: Path, reviewer: Reviewer) -> Path:
    return reviewer_storage_root(statement_path, reviewer) / "active.json"


def set_artifact_paths(set_path: Path, reviewer: Reviewer) -> ReviewerArtifactPaths:
    root = Path(set_path)
    return ReviewerArtifactPaths(
        main_review=root / f"{reviewer.value}_review.json",
        histories={
            model: root / "histories" / f"{model.value}_history.json"
            for model in BenchmarkModel
        },
        profile_proposals={
            model: root
            / "profile_proposals"
            / f"{model.value}_profile_proposal.json"
            for model in BenchmarkModel
        },
    )


def _set_id(task_id: str, attempt_id: str) -> str:
    identity = json.dumps(
        [task_id, attempt_id], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _read_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_prepared_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(_canonical_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _json_clone(value: Mapping[str, object]) -> dict:
    cloned = json.loads(json.dumps(dict(value), ensure_ascii=False))
    if not isinstance(cloned, dict):
        raise ValueError("review artifact must serialize to one JSON object")
    return cloned


def _validated_payloads(result: CompleteReviewResult) -> tuple[dict, dict, dict]:
    if not isinstance(result, CompleteReviewResult):
        raise TypeError("result must be a CompleteReviewResult")
    CompleteReviewResult(
        task_id=result.task_id,
        attempt_id=result.attempt_id,
        statement_index=result.statement_index,
        reviewer=result.reviewer,
        main_review=result.main_review,
        histories=result.histories,
        profile_proposals=result.profile_proposals,
        created_at=result.created_at,
    )
    main = _json_clone(result.main_review.output)
    histories = {item.model: _json_clone(item.output) for item in result.histories}
    proposals = {
        item.model: _json_clone(item.output) for item in result.profile_proposals
    }
    expected_identity = {
        "task_id": result.task_id,
        "attempt_id": result.attempt_id,
        "statement_index": result.statement_index,
        "reviewer": result.reviewer.value,
    }
    for label, payload in (
        ("main_review", main),
        *((f"history:{model.value}", histories[model]) for model in BenchmarkModel),
        *((f"profile:{model.value}", proposals[model]) for model in BenchmarkModel),
    ):
        if any(payload.get(field) != expected for field, expected in expected_identity.items()):
            raise ValueError(f"{label} serialized identity does not match CompleteReviewResult")
    main_contract = MainReviewOutput(
        result.statement_index, result.reviewer, result.attempt_id, main
    )
    history_contracts = tuple(
        HistoryProposalOutput(
            result.statement_index,
            result.reviewer,
            result.attempt_id,
            model,
            histories[model],
        )
        for model in BenchmarkModel
    )
    profile_contracts = tuple(
        ProfileProposalOutput(
            result.statement_index,
            result.reviewer,
            result.attempt_id,
            model,
            proposals[model],
        )
        for model in BenchmarkModel
    )
    CompleteReviewResult(
        task_id=result.task_id,
        attempt_id=result.attempt_id,
        statement_index=result.statement_index,
        reviewer=result.reviewer,
        main_review=main_contract,
        histories=history_contracts,
        profile_proposals=profile_contracts,
        created_at=result.created_at,
    )
    return main, histories, proposals


def _manifest(
    result: CompleteReviewResult,
    main: dict,
    histories: Mapping[BenchmarkModel, dict],
    proposals: Mapping[BenchmarkModel, dict],
    attempt_metadata: Optional[ReviewAttemptMetadata] = None,
) -> dict:
    manifest = {
        "schema_version": (
            LEGACY_STORAGE_SCHEMA_VERSION
            if attempt_metadata is None
            else STORAGE_SCHEMA_VERSION
        ),
        "statement_index": result.statement_index,
        "reviewer": result.reviewer.value,
        "task_id": result.task_id,
        "attempt_id": result.attempt_id,
        "artifacts": {
            "main_review": {
                "path": f"{result.reviewer.value}_review.json",
                "sha256": _canonical_sha256(main),
            },
            "histories": {
                model.value: {
                    "path": f"histories/{model.value}_history.json",
                    "sha256": _canonical_sha256(histories[model]),
                }
                for model in BenchmarkModel
            },
            "profile_proposals": {
                model.value: {
                    "path": f"profile_proposals/{model.value}_profile_proposal.json",
                    "sha256": _canonical_sha256(proposals[model]),
                }
                for model in BenchmarkModel
            },
        },
    }
    if attempt_metadata is not None:
        manifest["execution"] = {
            "attempt_number": attempt_metadata.attempt_number,
            "run_id": attempt_metadata.run_id,
            "task_deduplication_key": attempt_metadata.task_deduplication_key,
        }
    return manifest


def _active_reference(result: CompleteReviewResult, set_id: str, manifest: dict) -> dict:
    return {
        "schema_version": manifest["schema_version"],
        "statement_index": result.statement_index,
        "reviewer": result.reviewer.value,
        "task_id": result.task_id,
        "attempt_id": result.attempt_id,
        "set_id": set_id,
        "manifest_sha256": _canonical_sha256(manifest),
    }


def _validate_statement_path(statement_path: Path, statement_index: int) -> Path:
    return review_statement_path(Path(statement_path), statement_index)


def _validate_prepared_set(
    *,
    set_path: Path,
    reviewer: Reviewer,
    statement_index: int,
    task_id: str,
    attempt_id: str,
) -> dict:
    set_path = confined_path(set_path, set_path.parent, label="review attempt")
    manifest_path = confined_path(
        set_path / "manifest.json", set_path, label="review manifest"
    )
    manifest = _read_object(manifest_path)
    schema_version = manifest.get("schema_version")
    manifest_statement_index = manifest.get("statement_index")
    if (
        type(schema_version) is not int
        or schema_version not in _SUPPORTED_STORAGE_SCHEMA_VERSIONS
        or type(manifest_statement_index) is not int
        or manifest_statement_index != statement_index
        or manifest.get("reviewer") != reviewer.value
        or manifest.get("task_id") != task_id
        or manifest.get("attempt_id") != attempt_id
    ):
        raise ValueError("review set manifest identity is inconsistent")
    attempt_metadata = _attempt_metadata_from_manifest(manifest)
    artifacts = set_artifact_paths(set_path, reviewer)
    confined_path(artifacts.main_review, set_path, label="main review artifact")
    for artifact in (*artifacts.histories.values(), *artifacts.profile_proposals.values()):
        confined_path(artifact, set_path, label="review artifact")
    main = _read_object(artifacts.main_review)
    histories = {model: _read_object(path) for model, path in artifacts.histories.items()}
    proposals = {
        model: _read_object(path) for model, path in artifacts.profile_proposals.items()
    }
    expected_identity = {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "statement_index": statement_index,
        "reviewer": reviewer.value,
    }
    for payload in (main, *histories.values(), *proposals.values()):
        if any(payload.get(field) != expected for field, expected in expected_identity.items()):
            raise ValueError("review artifact identity contradicts its manifest")
    expected = _manifest(
        CompleteReviewResult(
            task_id=task_id,
            attempt_id=attempt_id,
            statement_index=statement_index,
            reviewer=reviewer,
            main_review=MainReviewOutput(statement_index, reviewer, attempt_id, main),
            histories=tuple(
                HistoryProposalOutput(
                    statement_index, reviewer, attempt_id, model, histories[model]
                )
                for model in BenchmarkModel
            ),
            profile_proposals=tuple(
                ProfileProposalOutput(
                    statement_index, reviewer, attempt_id, model, proposals[model]
                )
                for model in BenchmarkModel
            ),
            created_at=datetime.fromtimestamp(0, tz=timezone.utc),
        ),
        main,
        histories,
        proposals,
        attempt_metadata,
    )
    if manifest != expected:
        raise ValueError("review set manifest does not match its seven artifacts")
    return manifest


def _attempt_metadata_from_manifest(
    manifest: Mapping[str, object],
) -> Optional[ReviewAttemptMetadata]:
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("review manifest schema version must be an integer")
    if schema_version == LEGACY_STORAGE_SCHEMA_VERSION:
        if "execution" in manifest:
            raise ValueError("legacy review manifest must not contain execution identity")
        return None
    if schema_version != STORAGE_SCHEMA_VERSION:
        raise ValueError("review manifest schema version is unsupported")
    execution = manifest.get("execution")
    if not isinstance(execution, Mapping) or set(execution) != {
        "attempt_number",
        "run_id",
        "task_deduplication_key",
    }:
        raise ValueError("review manifest execution identity is incomplete")
    return ReviewAttemptMetadata(
        attempt_number=execution["attempt_number"],
        run_id=execution["run_id"],
        task_deduplication_key=execution["task_deduplication_key"],
    )


def resolve_confirmed_review_set(
    *, statement_path: Path, statement_index: int, reviewer: Reviewer
) -> tuple[Optional[ConfirmedReviewSet], Optional[str]]:
    """Resolve the one confirmed immutable set; never infer an in-progress set."""

    try:
        path = _validate_statement_path(Path(statement_path), statement_index)
        storage = confined_path(
            reviewer_storage_root(path, reviewer), path, label="reviewer storage"
        )
        reference_path = confined_path(
            active_reference_path(path, reviewer),
            storage,
            label="active review reference",
        )
    except ValueError as error:
        return None, str(error)
    if not reference_path.exists():
        return None, None
    try:
        reference = _read_object(reference_path)
        set_id = reference.get("set_id")
        reference_schema_version = reference.get("schema_version")
        reference_statement_index = reference.get("statement_index")
        if (
            type(reference_schema_version) is not int
            or reference_schema_version not in _SUPPORTED_STORAGE_SCHEMA_VERSIONS
            or type(reference_statement_index) is not int
            or reference_statement_index != statement_index
            or reference.get("reviewer") != reviewer.value
            or not isinstance(reference.get("task_id"), str)
            or not reference.get("task_id")
            or not isinstance(reference.get("attempt_id"), str)
            or not reference.get("attempt_id")
            or not isinstance(set_id, str)
            or _SET_ID.fullmatch(set_id) is None
        ):
            raise ValueError("active review reference is invalid")
        attempts_root = confined_path(
            storage / "attempts", storage, label="review attempts storage"
        )
        set_path = confined_path(
            attempts_root / set_id, attempts_root, label="review attempt"
        )
        manifest = _validate_prepared_set(
            set_path=set_path,
            reviewer=reviewer,
            statement_index=statement_index,
            task_id=reference["task_id"],
            attempt_id=reference["attempt_id"],
        )
        if reference.get("manifest_sha256") != _canonical_sha256(manifest):
            raise ValueError("active reference does not match the set manifest")
        if reference.get("schema_version") != manifest.get("schema_version"):
            raise ValueError("active reference schema does not match the set manifest")
        return (
            ConfirmedReviewSet(
                statement_index=statement_index,
                reviewer=reviewer,
                task_id=reference["task_id"],
                attempt_id=reference["attempt_id"],
                set_id=set_id,
                set_path=set_path,
                manifest_path=set_path / "manifest.json",
                active_reference_path=reference_path,
                artifacts=set_artifact_paths(set_path, reviewer),
                attempt_metadata=_attempt_metadata_from_manifest(manifest),
            ),
            None,
        )
    except (ValueError, KeyError, json.JSONDecodeError, OSError, UnicodeError) as error:
        return None, str(error)


def has_verifiable_unconfirmed_projection(
    *, statement_path: Path, statement_index: int, reviewer: Reviewer
) -> bool:
    """Return whether a permanent review is a projection of an unconfirmed set.

    This is deliberately weaker than confirmation: ``active.json`` remains the
    only commit point.  The result merely lets recovery distinguish a projection
    written by an interrupted publication from provenance-free legacy data.  No
    file is deleted, activated, or otherwise trusted as a completed review.
    """

    recovered, _ = resolve_recoverable_unconfirmed_attempt(
        statement_path=statement_path,
        statement_index=statement_index,
        reviewer=reviewer,
    )
    return recovered is not None


def resolve_recoverable_unconfirmed_attempt(
    *,
    statement_path: Path,
    statement_index: int,
    reviewer: Reviewer,
) -> tuple[Optional[RecoveredReviewAttempt], Optional[str]]:
    """Resolve unambiguous v2 retry lineage without activating any set."""

    try:
        path = _validate_statement_path(Path(statement_path), statement_index)
        storage = confined_path(
            reviewer_storage_root(path, reviewer), path, label="reviewer storage"
        )
        reference_path = confined_path(
            active_reference_path(path, reviewer), storage, label="active review reference"
        )
        if reference_path.exists():
            return None, None
        permanent_path = confined_path(
            path / f"{reviewer.value}_review.json", path, label="permanent review"
        )
        if not permanent_path.is_file():
            return None, None
        permanent = _read_object(permanent_path)
        attempts_root = confined_path(
            storage / "attempts", storage, label="review attempts storage"
        )
        if not attempts_root.exists() or not stat.S_ISDIR(
            attempts_root.lstat().st_mode
        ):
            return None, None
        candidates = tuple(attempts_root.iterdir())
    except (ValueError, json.JSONDecodeError, OSError, UnicodeError) as error:
        return None, str(error)

    verified: list[RecoveredReviewAttempt] = []
    projected: list[RecoveredReviewAttempt] = []
    legacy_projection = False
    legacy_attempt = False
    invalid_attempt = False
    for candidate in candidates:
        candidate_is_attempt = False
        try:
            candidate = confined_path(
                candidate, attempts_root, label="unconfirmed review attempt"
            )
            if (
                not stat.S_ISDIR(candidate.lstat().st_mode)
                or _SET_ID.fullmatch(candidate.name) is None
            ):
                continue
            candidate_is_attempt = True
            manifest_path = confined_path(
                candidate / "manifest.json", candidate, label="review manifest"
            )
            manifest = _read_object(manifest_path)
            task_id = manifest.get("task_id")
            attempt_id = manifest.get("attempt_id")
            if (
                not isinstance(task_id, str)
                or not task_id
                or not isinstance(attempt_id, str)
                or not attempt_id
                or candidate.name != _set_id(task_id, attempt_id)
            ):
                continue
            validated_manifest = _validate_prepared_set(
                set_path=candidate,
                reviewer=reviewer,
                statement_index=statement_index,
                task_id=task_id,
                attempt_id=attempt_id,
            )
            metadata = _attempt_metadata_from_manifest(validated_manifest)
            candidate_projection = _read_object(
                set_artifact_paths(candidate, reviewer).main_review
            )
            if metadata is None:
                legacy_attempt = True
                legacy_projection = legacy_projection or candidate_projection == permanent
                continue
            recovered = RecoveredReviewAttempt(
                statement_index=statement_index,
                reviewer=reviewer,
                task_id=task_id,
                attempt_id=attempt_id,
                attempt_number=metadata.attempt_number,
                run_id=metadata.run_id,
                task_deduplication_key=metadata.task_deduplication_key,
                set_path=candidate,
            )
            verified.append(recovered)
            if candidate_projection == permanent:
                projected.append(recovered)
        except (ValueError, KeyError, json.JSONDecodeError, OSError, UnicodeError):
            invalid_attempt = invalid_attempt or candidate_is_attempt
            continue
    if not projected:
        if legacy_projection:
            return None, "unconfirmed projection lacks persistent retry identity"
        return None, None
    if invalid_attempt or legacy_attempt:
        return None, "unconfirmed review attempts include unverifiable lineage"
    task_ids = {item.task_id for item in verified}
    run_ids = {item.run_id for item in verified}
    deduplication_keys = {item.task_deduplication_key for item in verified}
    attempt_numbers = [item.attempt_number for item in verified]
    attempt_ids = [item.attempt_id for item in verified]
    if len(task_ids) != 1 or len(run_ids) != 1 or len(deduplication_keys) != 1:
        return None, "unconfirmed review attempts have contradictory logical identity"
    if len(attempt_numbers) != len(set(attempt_numbers)) or len(attempt_ids) != len(
        set(attempt_ids)
    ):
        return None, "unconfirmed review attempts contain duplicate attempt identity"
    ordered = sorted(attempt_numbers)
    if ordered != list(range(1, ordered[-1] + 1)):
        return None, "unconfirmed review attempt sequence is not contiguous"
    latest = max(projected, key=lambda item: item.attempt_number)
    if latest.attempt_number != ordered[-1]:
        return None, "permanent projection does not identify the latest review attempt"
    if sum(item.attempt_number == latest.attempt_number for item in projected) != 1:
        return None, "unconfirmed review projection is ambiguous"
    return latest, None


def _prepare_set(
    staging_path: Path,
    result: CompleteReviewResult,
    main: dict,
    histories: Mapping[BenchmarkModel, dict],
    proposals: Mapping[BenchmarkModel, dict],
    attempt_metadata: Optional[ReviewAttemptMetadata],
) -> dict:
    artifacts = set_artifact_paths(staging_path, result.reviewer)
    _write_prepared_json(artifacts.main_review, main)
    for model in BenchmarkModel:
        _write_prepared_json(artifacts.histories[model], histories[model])
        _write_prepared_json(artifacts.profile_proposals[model], proposals[model])
    manifest = _manifest(result, main, histories, proposals, attempt_metadata)
    _write_prepared_json(staging_path / "manifest.json", manifest)
    _validate_prepared_set(
        set_path=staging_path,
        reviewer=result.reviewer,
        statement_index=result.statement_index,
        task_id=result.task_id,
        attempt_id=result.attempt_id,
    )
    return manifest


def _project_permanent_review(confirmed: ConfirmedReviewSet, statement_path: Path) -> Path:
    value = _read_object(confirmed.artifacts.main_review)
    target = confined_path(
        Path(statement_path) / f"{confirmed.reviewer.value}_review.json",
        Path(statement_path),
        label="permanent review",
    )
    if target.is_file():
        try:
            if _read_object(target) == value:
                return target
        except (ValueError, json.JSONDecodeError, OSError, UnicodeError):
            pass
    _atomic_write_json(target, value)
    return target


def publish_complete_review(
    result: CompleteReviewResult,
    *,
    statement_path: Path,
    attempt_metadata: Optional[ReviewAttemptMetadata] = None,
    failure_injector: Optional[FailureInjector] = None,
) -> ReviewPublishResult:
    """Prepare seven files, atomically confirm one immutable set, then project its main review.

    The atomic commit point is replacement of ``active.json``. Immutable sets that
    exist without that reference are ignored by the inspector.
    """

    main, histories, proposals = _validated_payloads(result)
    if result.statement_index in _HISTORICAL_INDICES:
        raise PermissionError("historical statements 000-009 are read-only")
    destination = _validate_statement_path(statement_path, result.statement_index)
    storage_root = confined_path(
        reviewer_storage_root(destination, result.reviewer),
        destination,
        label="reviewer storage",
    )
    attempts_root = confined_path(
        storage_root / "attempts", storage_root, label="review attempts storage"
    )
    attempts_root.mkdir(parents=True, exist_ok=True)
    set_id = _set_id(result.task_id, result.attempt_id)
    final_set_path = attempts_root / set_id
    if attempt_metadata is not None and not isinstance(
        attempt_metadata, ReviewAttemptMetadata
    ):
        raise TypeError("attempt_metadata must be ReviewAttemptMetadata")
    desired_manifest = _manifest(
        result, main, histories, proposals, attempt_metadata
    )

    current, current_error = resolve_confirmed_review_set(
        statement_path=destination,
        statement_index=result.statement_index,
        reviewer=result.reviewer,
    )
    if current is not None and current.task_id == result.task_id and current.attempt_id == result.attempt_id:
        if _read_object(current.manifest_path) != desired_manifest:
            raise ValueError("the same task_id/attempt_id cannot identify different output")
        permanent = _project_permanent_review(current, destination)
        return ReviewPublishResult(ReviewPublishStatus.UNCHANGED, current, permanent)

    staging_path: Optional[Path] = None
    if final_set_path.exists():
        existing_manifest = _validate_prepared_set(
            set_path=final_set_path,
            reviewer=result.reviewer,
            statement_index=result.statement_index,
            task_id=result.task_id,
            attempt_id=result.attempt_id,
        )
        if existing_manifest != desired_manifest:
            raise ValueError("an immutable review set already exists with different output")
    else:
        staging_path = Path(
            tempfile.mkdtemp(prefix=".staging-", dir=storage_root)
        )
        try:
            _prepare_set(
                staging_path,
                result,
                main,
                histories,
                proposals,
                attempt_metadata,
            )
            if failure_injector is not None:
                failure_injector("after_prepare")
            os.rename(staging_path, final_set_path)
            staging_path = None
            _fsync_directory(attempts_root)
        finally:
            if staging_path is not None and staging_path.exists():
                shutil.rmtree(staging_path)

    if failure_injector is not None:
        failure_injector("after_set_publish")
    candidate = ConfirmedReviewSet(
        statement_index=result.statement_index,
        reviewer=result.reviewer,
        task_id=result.task_id,
        attempt_id=result.attempt_id,
        set_id=set_id,
        set_path=final_set_path,
        manifest_path=final_set_path / "manifest.json",
        active_reference_path=active_reference_path(destination, result.reviewer),
        artifacts=set_artifact_paths(final_set_path, result.reviewer),
    )
    permanent = _project_permanent_review(candidate, destination)
    if failure_injector is not None:
        failure_injector("after_projection")
        failure_injector("before_activation")
    reference = _active_reference(result, set_id, desired_manifest)
    _atomic_write_json(active_reference_path(destination, result.reviewer), reference)
    if failure_injector is not None:
        failure_injector("after_activation")
    confirmed, error = resolve_confirmed_review_set(
        statement_path=destination,
        statement_index=result.statement_index,
        reviewer=result.reviewer,
    )
    if confirmed is None:
        raise RuntimeError(f"published review set failed verification: {error}")
    status = ReviewPublishStatus.CREATED if current is None and current_error is None else ReviewPublishStatus.REPLACED
    return ReviewPublishResult(status, confirmed, permanent)
