"""Recovery, evidence publication, and safe cleanup for one adjudication application."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

from automation.contracts import BenchmarkModel, Reviewer

from .adjudication_storage import (
    AdjudicationApplication,
    ApplicationPhase,
    inspect_adjudication_application,
    resume_adjudication_application,
)
from .path_safety import confined_path, review_statement_path
from .review_storage import resolve_confirmed_review_set, reviewer_storage_root


EVIDENCE_SCHEMA_VERSION = 2


class CleanupPhase(str, Enum):
    PREPARED = "PREPARED"
    APPLYING = "APPLYING"
    CLEANED = "CLEANED"
    INVALID = "INVALID"


class FinalizationStatus(str, Enum):
    FINALIZED = "FINALIZED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class CleanupResult:
    phase: CleanupPhase
    manifest_path: Path
    status_path: Path
    removed_count: int
    planned_count: int


@dataclass(frozen=True)
class FinalizationResult:
    status: FinalizationStatus
    application: AdjudicationApplication
    cleanup: CleanupResult
    evidence_path: Path
    evidence: dict


FailureInjector = Callable[[str], None]


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _read_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validated_cleanup_paths(
    *,
    statement_path: Path,
    application_path: Path,
    evidence_path: Path,
    temporary_paths: Sequence[Path],
) -> tuple[Path, ...]:
    statement = statement_path.resolve()
    application_root = (statement_path / ".adjudication_applications").resolve()
    evidence = evidence_path.resolve()
    protected = {
        (statement_path / "adjudication.json").resolve(),
        evidence,
        *((statement_path / f"{reviewer.value}_review.json").resolve() for reviewer in Reviewer),
    }
    normalized = []
    seen = set()
    for raw in temporary_paths:
        path = Path(raw)
        if path.is_symlink():
            raise ValueError("cleanup paths must not be symbolic links")
        resolved = path.resolve()
        if not _is_relative_to(resolved, statement):
            raise ValueError("cleanup paths must stay inside the statement directory")
        if resolved in protected:
            raise ValueError("cleanup path identifies a permanent artifact")
        if _is_relative_to(resolved, application_root):
            raise ValueError("adjudication application files must be preserved")
        if any(
            _is_relative_to(resolved, reviewer_storage_root(statement_path, reviewer).resolve())
            for reviewer in Reviewer
        ):
            raise ValueError("confirmed review sets must be preserved")
        if resolved in seen:
            raise ValueError("cleanup paths must not contain duplicates")
        seen.add(resolved)
        normalized.append(resolved)
    return tuple(sorted(normalized, key=lambda item: str(item)))


def _cleanup_files(
    *,
    application_path: Path,
    statement_path: Path,
    evidence_path: Path,
    temporary_paths: Sequence[Path],
    failure_injector: Optional[FailureInjector],
) -> CleanupResult:
    cleanup_manifest_path = confined_path(
        application_path / "cleanup_manifest.json",
        application_path,
        label="cleanup manifest",
    )
    cleanup_status_path = confined_path(
        application_path / "cleanup_status.json",
        application_path,
        label="cleanup status",
    )
    paths = _validated_cleanup_paths(
        statement_path=statement_path,
        application_path=application_path,
        evidence_path=evidence_path,
        temporary_paths=temporary_paths,
    )
    relative_names = [str(path.relative_to(statement_path.resolve())) for path in paths]

    if cleanup_manifest_path.exists():
        manifest = _read_object(cleanup_manifest_path)
        if (
            manifest.get("schema_version") != 1
            or manifest.get("paths") != relative_names
            or not isinstance(manifest.get("files"), dict)
            or set(manifest["files"]) != set(relative_names)
        ):
            raise ValueError("cleanup request conflicts with the prepared cleanup manifest")
    else:
        files = {}
        for path, relative in zip(paths, relative_names):
            if not path.is_file():
                raise ValueError(f"cleanup candidate is not an existing file: {relative}")
            files[relative] = {"sha256": _file_sha256(path)}
        manifest = {
            "schema_version": 1,
            "statement_index": int(statement_path.name.removeprefix("statement_")),
            "paths": relative_names,
            "files": files,
        }
        _atomic_write_json(cleanup_manifest_path, manifest)
        _atomic_write_json(cleanup_status_path, {"phase": CleanupPhase.PREPARED.value})

    removed = 0
    _atomic_write_json(cleanup_status_path, {"phase": CleanupPhase.APPLYING.value})
    for position, (path, relative) in enumerate(zip(paths, relative_names), start=1):
        if path.exists():
            if not path.is_file() or _file_sha256(path) != manifest["files"][relative]["sha256"]:
                raise ValueError(f"cleanup candidate changed after preparation: {relative}")
            path.unlink()
            _fsync_directory(path.parent)
            removed += 1
            if failure_injector is not None:
                failure_injector(f"after_cleanup:{position}:{relative}")
    if any(path.exists() for path in paths):
        raise RuntimeError("cleanup verification failed")
    _atomic_write_json(cleanup_status_path, {"phase": CleanupPhase.CLEANED.value})
    return CleanupResult(
        phase=CleanupPhase.CLEANED,
        manifest_path=cleanup_manifest_path,
        status_path=cleanup_status_path,
        removed_count=removed,
        planned_count=len(paths),
    )


def _verify_active_reviews(application: AdjudicationApplication, statement_path: Path) -> None:
    if set(application.review_attempts) != set(Reviewer):
        raise ValueError("final evidence requires identities for all three reviewers")
    for reviewer in Reviewer:
        confirmed, error = resolve_confirmed_review_set(
            statement_path=statement_path,
            statement_index=application.statement_index,
            reviewer=reviewer,
        )
        if error is not None or confirmed is None:
            raise ValueError(f"active review for {reviewer.value} is not valid")
        recorded = application.review_attempts[reviewer]
        if (
            recorded.get("task_id") != confirmed.task_id
            or recorded.get("attempt_id") != confirmed.attempt_id
        ):
            raise ValueError(f"active review for {reviewer.value} changed after adjudication")


def _evidence(application: AdjudicationApplication) -> dict:
    if not isinstance(application.run_id, str) or not application.run_id.strip():
        raise ValueError("final evidence requires a non-empty run_id")
    manifest = _read_object(
        confined_path(
            application.manifest_path,
            application.application_path,
            label="application manifest",
        )
    )
    profile_application = json.loads(json.dumps(manifest["profile_application"]))
    for model in BenchmarkModel:
        prepared = confined_path(
            application.application_path
            / manifest["files"][f"profile:{model.value}"]["prepared_path"],
            application.application_path,
            label=f"prepared profile evidence {model.value}",
        )
        profile_application["profiles"][model.value]["applied_profile"] = _read_object(
            prepared
        )
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "statement_index": application.statement_index,
        "run_id": application.run_id,
        "review_attempts": {
            reviewer.value: dict(application.review_attempts[reviewer])
            for reviewer in Reviewer
        },
        "review_adjudication_sha256": application.adjudication_sha256,
        "history_application": manifest["history_application"],
        "profile_application": profile_application,
        "temporary_artifacts_cleaned": True,
    }


def verify_application_evidence(
    *,
    application: AdjudicationApplication,
    statement_path: Path,
    evidence_path: Path,
) -> None:
    """Read-only verification against the existing v2 evidence and cleanup commit."""

    evidence = confined_path(evidence_path, statement_path, label="application evidence")
    if _read_object(evidence) != _evidence(application):
        raise ValueError("final evidence does not match the adjudication application")
    root = application.application_path
    cleanup = _read_object(confined_path(root / "cleanup_manifest.json", root, label="cleanup manifest"))
    status = _read_object(confined_path(root / "cleanup_status.json", root, label="cleanup status"))
    if (
        cleanup.get("schema_version") != 1
        or cleanup.get("statement_index") != application.statement_index
        or not isinstance(cleanup.get("paths"), list)
        or not isinstance(cleanup.get("files"), dict)
        or set(cleanup["paths"]) != set(cleanup["files"])
        or status != {"phase": CleanupPhase.CLEANED.value}
    ):
        raise ValueError("application cleanup is not confirmed")
    paths = _validated_cleanup_paths(
        statement_path=statement_path,
        application_path=root,
        evidence_path=evidence,
        temporary_paths=[statement_path / name for name in cleanup["paths"]],
    )
    if any(path.exists() for path in paths):
        raise ValueError("application cleanup is incomplete")


def finalize_adjudication_application(
    *,
    application_path: Path,
    statement_path: Path,
    feedback_root: Path,
    profiles_root: Path,
    evidence_path: Path,
    temporary_paths: Sequence[Path] = (),
    failure_injector: Optional[FailureInjector] = None,
) -> FinalizationResult:
    """Recover one application, clean explicit loose temporaries, and publish evidence."""

    statement = Path(statement_path)
    try:
        statement_index = int(statement.name.removeprefix("statement_"))
    except ValueError as error:
        raise ValueError("statement_path must end with statement_NNN") from error
    statement = review_statement_path(statement, statement_index)
    if statement_index < 10:
        raise PermissionError("historical statements 000-009 are read-only")
    applications_root = confined_path(
        statement / ".adjudication_applications",
        statement,
        label="adjudication application storage",
    )
    application_destination = confined_path(
        Path(application_path), applications_root, label="application_path"
    )
    if application_destination.parent != applications_root:
        raise ValueError("application_path must identify one application for this statement")
    evidence_destination = confined_path(
        Path(evidence_path), statement, label="evidence_path"
    )
    if not _is_relative_to(evidence_destination.resolve(), statement.resolve()):
        raise ValueError("evidence_path must stay inside the statement directory")
    if _is_relative_to(evidence_destination.resolve(), applications_root) or _is_relative_to(
        evidence_destination.resolve(), (statement / ".review_sets").resolve()
    ):
        raise ValueError("evidence_path must stay outside internal storage directories")

    recovered = resume_adjudication_application(
        application_path=application_path,
        statement_path=statement,
        feedback_root=feedback_root,
        profiles_root=profiles_root,
        failure_injector=failure_injector,
    )
    application = recovered.application
    if application.statement_index != statement_index:
        raise ValueError("application statement_index does not match statement_path")
    if application.phase is not ApplicationPhase.APPLIED:
        raise ValueError("adjudication application is not fully applied")
    _verify_active_reviews(application, statement)
    expected_evidence = _evidence(application)
    if evidence_destination.exists() and _read_object(evidence_destination) != expected_evidence:
        raise ValueError("a different final evidence file already exists")
    if failure_injector is not None:
        failure_injector("after_recovery")

    cleanup = _cleanup_files(
        application_path=Path(application_path),
        statement_path=statement,
        evidence_path=evidence_destination,
        temporary_paths=temporary_paths,
        failure_injector=failure_injector,
    )
    if failure_injector is not None:
        failure_injector("after_cleanup_complete")

    verified = inspect_adjudication_application(
        application_path=application_path,
        statement_path=statement,
        feedback_root=feedback_root,
        profiles_root=profiles_root,
    )
    if verified.phase is not ApplicationPhase.APPLIED:
        raise RuntimeError("application changed before final evidence publication")
    _verify_active_reviews(verified, statement)
    evidence = _evidence(verified)
    if evidence != expected_evidence:
        raise RuntimeError("application identity changed during finalization")

    unchanged = False
    if evidence_destination.exists():
        unchanged = True
    else:
        _atomic_write_json(evidence_destination, evidence)
    if _read_object(evidence_destination) != evidence:
        raise RuntimeError("final evidence failed verification")
    if failure_injector is not None:
        failure_injector("after_evidence")
    return FinalizationResult(
        status=(
            FinalizationStatus.UNCHANGED
            if unchanged and recovered.status.value == "UNCHANGED" and cleanup.removed_count == 0
            else FinalizationStatus.FINALIZED
        ),
        application=verified,
        cleanup=cleanup,
        evidence_path=evidence_destination,
        evidence=evidence,
    )
