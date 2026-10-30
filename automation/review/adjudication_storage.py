"""Safe, resumable-by-identity application of already adjudicated file results.

This module does not run adjudication and does not declare a statement complete.
It prepares immutable application material, replaces each destination safely, and
records whether this seven-file operation is prepared, applying, or applied.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Optional, Sequence

from automation.contracts import BenchmarkModel, Reviewer

from .path_safety import confined_path, review_statement_path
from .result_inspector import inspect_reviewer_results
from .review_storage import resolve_confirmed_review_set
from .validators import (
    nonempty_text as _shared_nonempty_text,
    valid_index as _shared_valid_index,
    validate_adjudication as _shared_validate_adjudication,
    validate_history as _shared_validate_history,
    validate_profile as _shared_validate_profile,
    validate_profile_adjudication_transition as _shared_validate_profile_adjudication_transition,
    validate_profile_transition as _shared_validate_profile_transition,
)


APPLICATION_SCHEMA_VERSION = 2
HISTORICAL_STATEMENT_RANGE = range(0, 10)


class AdjudicationApplyStatus(str, Enum):
    APPLIED = "APPLIED"
    UNCHANGED = "UNCHANGED"


class ApplicationPhase(str, Enum):
    PREPARED = "PREPARED"
    APPLYING = "APPLYING"
    ADJUDICATION_COMMITTED = "ADJUDICATION_COMMITTED"
    APPLIED = "APPLIED"
    INTERRUPTED = "INTERRUPTED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class AdjudicationApplication:
    statement_index: int
    application_id: str
    application_path: Path
    manifest_path: Path
    status_path: Path
    phase: ApplicationPhase
    adjudication_sha256: str
    history_sha256: Mapping[BenchmarkModel, str]
    profile_sha256: Mapping[BenchmarkModel, str]
    run_id: Optional[str]
    review_attempts: Mapping[Reviewer, Mapping[str, str]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "history_sha256", MappingProxyType(dict(self.history_sha256)))
        object.__setattr__(self, "profile_sha256", MappingProxyType(dict(self.profile_sha256)))
        object.__setattr__(
            self,
            "review_attempts",
            MappingProxyType(
                {
                    reviewer: MappingProxyType(dict(identity))
                    for reviewer, identity in self.review_attempts.items()
                }
            ),
        )


@dataclass(frozen=True)
class AdjudicationApplyResult:
    status: AdjudicationApplyStatus
    application: AdjudicationApplication


class AdjudicationCommitStatus(str, Enum):
    COMMITTED = "COMMITTED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class AdjudicationCommitResult:
    status: AdjudicationCommitStatus
    application: AdjudicationApplication


FailureInjector = Callable[[str], None]

def _valid_index(value: object) -> bool:
    return _shared_valid_index(value)


def _nonempty_text(value: object) -> bool:
    return _shared_nonempty_text(value)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_clone(value: Mapping[str, object], label: str) -> dict:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a JSON object")
    clone = json.loads(json.dumps(dict(value), ensure_ascii=False))
    if not isinstance(clone, dict):
        raise TypeError(f"{label} must be a JSON object")
    return clone


def _read_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path} line {line_number} must be one JSON object")
            records.append(value)
    return records


def _jsonl_bytes(records: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_bytes(record) for record in records)


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


def _write_prepared(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
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


def _validate_adjudication(value: dict, statement_index: int) -> None:
    _shared_validate_adjudication(value, statement_index)


def _normalize_model_records(
    values: Sequence[Mapping[str, object]], *, label: str
) -> dict[BenchmarkModel, dict]:
    normalized: dict[BenchmarkModel, dict] = {}
    for raw in values:
        value = _json_clone(raw, label)
        identifier = value.get("model") if label == "history" else value.get("model_name")
        try:
            model = BenchmarkModel(identifier)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} has an invalid model identifier") from error
        if model in normalized:
            raise ValueError(f"{label} contains duplicate model {model.value}")
        normalized[model] = value
    if set(normalized) != set(BenchmarkModel):
        raise ValueError(f"exactly one {label} is required for every benchmark model")
    return normalized


def _validate_history(value: dict, model: BenchmarkModel, statement_index: int) -> None:
    _shared_validate_history(value, model, statement_index)


def _validate_profile(value: dict, model: BenchmarkModel) -> None:
    _shared_validate_profile(value, model)


def _normalize_review_attempts(
    statement_path: Path,
    statement_index: int,
    supplied: Optional[Mapping[Reviewer | str, Mapping[str, str]]],
) -> dict[Reviewer, dict[str, str]]:
    provided: dict[Reviewer, dict[str, str]] = {}
    if supplied is not None:
        for key, identity in supplied.items():
            try:
                reviewer = key if isinstance(key, Reviewer) else Reviewer(key)
            except ValueError as error:
                raise ValueError("review_attempts contains an invalid reviewer") from error
            if (
                not isinstance(identity, Mapping)
                or not _nonempty_text(identity.get("task_id"))
                or not _nonempty_text(identity.get("attempt_id"))
            ):
                raise ValueError("review_attempts identities require task_id and attempt_id")
            provided[reviewer] = {
                "task_id": str(identity["task_id"]),
                "attempt_id": str(identity["attempt_id"]),
            }
        if set(provided) != set(Reviewer):
            raise ValueError("review_attempts must identify all three reviewers")

    inspection = inspect_reviewer_results(
        statement_index=statement_index,
        review_cases_root=statement_path.parent,
    )
    if not inspection.adjudication_enabled:
        raise ValueError("three complete active reviewer sets are required before adjudication")
    active = {
        reviewer: {
            "task_id": inspection.reviewers[reviewer].complete_result.task_id,
            "attempt_id": inspection.reviewers[reviewer].complete_result.attempt_id,
        }
        for reviewer in Reviewer
    }
    if active and provided and active != provided:
        raise ValueError("review_attempts do not match the active reviewer sets")
    return active


def _validate_active_review_run(
    statement_path: Path,
    statement_index: int,
    run_id: Optional[str],
    attempts: Mapping[Reviewer, Mapping[str, str]],
) -> None:
    if not _nonempty_text(run_id):
        raise ValueError("run_id is required to commit an adjudication")
    for reviewer in Reviewer:
        confirmed, error = resolve_confirmed_review_set(
            statement_path=statement_path,
            statement_index=statement_index,
            reviewer=reviewer,
        )
        if confirmed is None:
            raise ValueError(
                f"reviewer {reviewer.value} has no verifiable active set: {error or 'missing'}"
            )
        if (
            confirmed.task_id != attempts[reviewer]["task_id"]
            or confirmed.attempt_id != attempts[reviewer]["attempt_id"]
        ):
            raise ValueError("active reviewer attempt changed before adjudication")
        metadata = confirmed.attempt_metadata
        if metadata is None or not _nonempty_text(metadata.run_id):
            raise ValueError(f"reviewer {reviewer.value} has no verifiable run_id")
        if metadata.run_id != run_id:
            raise ValueError(f"reviewer {reviewer.value} run_id contradicts requested run_id")


def _validate_profile_transition(
    current: dict, candidate: dict, model: BenchmarkModel
) -> None:
    _shared_validate_profile_transition(current, candidate, model)


def _validate_profile_destinations(
    profiles: Mapping[BenchmarkModel, dict], profiles_root: Path
) -> None:
    for model in BenchmarkModel:
        path = profiles_root / f"{model.value}_profile.json"
        if path.exists():
            current = _read_object(path)
            _validate_profile_transition(current, profiles[model], model)


def _validate_profile_adjudications(
    *,
    profiles: Mapping[BenchmarkModel, dict],
    profiles_root: Path,
    statement_index: int,
    adjudication: Mapping[str, object],
) -> None:
    """Apply the mono-statement semantic gate required before bundle effects."""

    for model in BenchmarkModel:
        path = profiles_root / f"{model.value}_profile.json"
        if not path.is_file():
            raise ValueError(f"profile preimage is missing for {model.value}")
        current = _read_object(path)
        _shared_validate_profile_adjudication_transition(
            current,
            profiles[model],
            model,
            statement_index=statement_index,
            adjudication=adjudication,
        )


def _application_root(statement_path: Path) -> Path:
    return statement_path / ".adjudication_applications"


def _application_id(
    statement_index: int,
    adjudication: dict,
    histories: Mapping[BenchmarkModel, dict],
    profiles: Mapping[BenchmarkModel, dict],
    run_id: Optional[str],
    review_attempts: Mapping[Reviewer, Mapping[str, str]],
    source_hashes: Mapping[str, Optional[str]],
) -> str:
    identity = {
        "statement_index": statement_index,
        "adjudication": adjudication,
        "histories": {model.value: histories[model] for model in BenchmarkModel},
        "profiles": {model.value: profiles[model] for model in BenchmarkModel},
        "run_id": run_id,
        "review_attempts": {
            reviewer.value: dict(review_attempts[reviewer]) for reviewer in review_attempts
        },
        "source_hashes": dict(sorted(source_hashes.items())),
    }
    return _canonical_sha256(identity)


def _artifact_destinations(
    statement_path: Path, feedback_root: Path, profiles_root: Path
) -> dict[str, Path]:
    destinations = {"adjudication": statement_path / "adjudication.json"}
    destinations.update(
        {
            f"history:{model.value}": feedback_root / f"{model.value}_history.jsonl"
            for model in BenchmarkModel
        }
    )
    destinations.update(
        {
            f"profile:{model.value}": profiles_root / f"{model.value}_profile.json"
            for model in BenchmarkModel
        }
    )
    return destinations


def _prepared_path(application_path: Path, key: str) -> Path:
    kind, _, model = key.partition(":")
    if kind == "adjudication":
        return application_path / "prepared" / "adjudication.json"
    suffix = "history.jsonl" if kind == "history" else "profile.json"
    return application_path / "prepared" / kind / f"{model}_{suffix}"


def _application_internal_path(root: Path, path: Path, *, label: str) -> Path:
    """Guard one application file immediately before it is accessed."""

    return confined_path(path, root, label=label)


def _state(path: Path, phase: ApplicationPhase) -> None:
    _atomic_write(path, _canonical_bytes({"phase": phase.value}))


def _manifest_payload(
    *,
    statement_index: int,
    application_id: str,
    run_id: Optional[str],
    review_attempts: Mapping[Reviewer, Mapping[str, str]],
    adjudication: dict,
    histories: Mapping[BenchmarkModel, dict],
    profiles: Mapping[BenchmarkModel, dict],
    source_hashes: Mapping[str, Optional[str]],
    target_bytes: Mapping[str, bytes],
) -> dict:
    return {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "statement_index": statement_index,
        "application_id": application_id,
        "run_id": run_id,
        "review_attempts": {
            reviewer.value: dict(review_attempts[reviewer]) for reviewer in review_attempts
        },
        "review_adjudication_sha256": _canonical_sha256(adjudication),
        "history_application": {
            "adjudication_id": application_id,
            "records": {
                model.value: _canonical_sha256(histories[model])
                for model in BenchmarkModel
            },
        },
        "profile_application": {
            "adjudication_id": application_id,
            "profiles": {
                model.value: {
                    "sha256": _canonical_sha256(profiles[model]),
                    "outputs_reviewed": profiles[model]["profile_metadata"]["outputs_reviewed"],
                    "version": profiles[model]["profile_metadata"]["version"],
                }
                for model in BenchmarkModel
            },
        },
        "files": {
            key: {
                "source_sha256": source_hashes[key],
                "target_sha256": hashlib.sha256(target_bytes[key]).hexdigest(),
                "prepared_path": str(_prepared_path(Path("."), key).relative_to(".")),
            }
            for key in target_bytes
        },
    }


def _manifest_matches_request(
    manifest: Mapping[str, object],
    *,
    statement_index: int,
    run_id: Optional[str],
    review_attempts: Mapping[Reviewer, Mapping[str, str]],
    adjudication: dict,
    histories: Mapping[BenchmarkModel, dict],
    profiles: Mapping[BenchmarkModel, dict],
    target_bytes: Mapping[str, bytes],
) -> bool:
    """Compare requested results without rebinding the original preimages."""

    try:
        return (
            manifest.get("schema_version") == APPLICATION_SCHEMA_VERSION
            and manifest.get("statement_index") == statement_index
            and manifest.get("run_id") == run_id
            and manifest.get("review_attempts")
            == {
                reviewer.value: dict(review_attempts[reviewer])
                for reviewer in review_attempts
            }
            and manifest.get("review_adjudication_sha256")
            == _canonical_sha256(adjudication)
            and manifest["history_application"]["records"]
            == {
                model.value: _canonical_sha256(histories[model])
                for model in BenchmarkModel
            }
            and manifest["profile_application"]["profiles"]
            == {
                model.value: {
                    "sha256": _canonical_sha256(profiles[model]),
                    "outputs_reviewed": profiles[model]["profile_metadata"][
                        "outputs_reviewed"
                    ],
                    "version": profiles[model]["profile_metadata"]["version"],
                }
                for model in BenchmarkModel
            }
            and {
                key: value["target_sha256"]
                for key, value in manifest["files"].items()
            }
            == {
                key: hashlib.sha256(content).hexdigest()
                for key, content in target_bytes.items()
            }
        )
    except (KeyError, TypeError, AttributeError):
        return False


def _validate_existing_histories(
    *,
    histories: Mapping[BenchmarkModel, dict],
    feedback_root: Path,
    statement_index: int,
) -> dict[BenchmarkModel, list[dict]]:
    result = {}
    for model in BenchmarkModel:
        records = _read_jsonl(feedback_root / f"{model.value}_history.jsonl")
        matches = [record for record in records if record.get("output") == statement_index]
        if len(matches) > 1:
            raise ValueError(f"duplicate history already exists for {model.value}/{statement_index}")
        if matches and matches[0] != histories[model]:
            raise ValueError(f"conflicting history already exists for {model.value}/{statement_index}")
        result[model] = records
    return result


def _prepare_application(
    *,
    application_path: Path,
    manifest: dict,
    target_bytes: Mapping[str, bytes],
) -> None:
    root = application_path.parent
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        for key, content in target_bytes.items():
            _write_prepared(_prepared_path(staging, key), content)
        _write_prepared(staging / "manifest.json", _canonical_bytes(manifest))
        _write_prepared(staging / "status.json", _canonical_bytes({"phase": "PREPARED"}))
        os.rename(staging, application_path)
        _fsync_directory(root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _application_from_manifest(
    application_path: Path, manifest: dict, phase: ApplicationPhase
) -> AdjudicationApplication:
    histories = {
        model: manifest["history_application"]["records"][model.value]
        for model in BenchmarkModel
    }
    profiles = {
        model: manifest["profile_application"]["profiles"][model.value]["sha256"]
        for model in BenchmarkModel
    }
    attempts = {
        Reviewer(name): identity
        for name, identity in manifest.get("review_attempts", {}).items()
    }
    return AdjudicationApplication(
        statement_index=manifest["statement_index"],
        application_id=manifest["application_id"],
        application_path=application_path,
        manifest_path=application_path / "manifest.json",
        status_path=application_path / "status.json",
        phase=phase,
        adjudication_sha256=manifest["review_adjudication_sha256"],
        history_sha256=histories,
        profile_sha256=profiles,
        run_id=manifest.get("run_id"),
        review_attempts=attempts,
    )


def inspect_adjudication_application(
    *,
    application_path: Path,
    statement_path: Path,
    feedback_root: Path,
    profiles_root: Path,
    application_evidence_path: Optional[Path] = None,
) -> AdjudicationApplication:
    """Validate prepared material and report actual cross-file application state."""

    destination_statement = review_statement_path(
        Path(statement_path),
        int(Path(statement_path).name.removeprefix("statement_")),
    )
    feedback = confined_path(Path(feedback_root), Path(feedback_root), label="feedback_root")
    profiles_destination = confined_path(
        Path(profiles_root), Path(profiles_root), label="profiles_root"
    )
    applications_root = confined_path(
        _application_root(destination_statement),
        destination_statement,
        label="adjudication application storage",
    )
    root = confined_path(
        Path(application_path), applications_root, label="application_path"
    )
    try:
        manifest_path = _application_internal_path(
            root, root / "manifest.json", label="application manifest"
        )
        status_path = _application_internal_path(
            root, root / "status.json", label="application status"
        )
        manifest = _read_object(manifest_path)
        status = _read_object(status_path)
        if (
            manifest.get("schema_version") != APPLICATION_SCHEMA_VERSION
            or manifest.get("application_id") != root.name
            or not _valid_index(manifest.get("statement_index"))
        ):
            raise ValueError("application manifest identity is invalid")
        destinations = _artifact_destinations(
            destination_statement, feedback, profiles_destination
        )
        for key, destination in destinations.items():
            boundary = (
                destination_statement
                if key == "adjudication"
                else feedback
                if key.startswith("history:")
                else profiles_destination
            )
            confined_path(destination, boundary, label=f"destination {key}")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(destinations):
            raise ValueError("application manifest files are invalid")
        target_bytes = {}
        for key, destination in destinations.items():
            item = files[key]
            prepared = _application_internal_path(
                root,
                _prepared_path(root, key),
                label=f"prepared application artifact {key}",
            )
            if (
                not isinstance(item, dict)
                or not prepared.is_file()
                or _file_sha256(prepared) != item.get("target_sha256")
            ):
                raise ValueError(f"prepared application artifact is invalid: {key}")
            prepared = _application_internal_path(
                root, prepared, label=f"prepared application artifact {key}"
            )
            target_bytes[key] = prepared.read_bytes()

        statement_index = manifest["statement_index"]
        adjudication = _read_object(
            _application_internal_path(
                root,
                _prepared_path(root, "adjudication"),
                label="prepared adjudication",
            )
        )
        _validate_adjudication(adjudication, statement_index)
        histories = {}
        profiles = {}
        for model in BenchmarkModel:
            records = _read_jsonl(
                _application_internal_path(
                    root,
                    _prepared_path(root, f"history:{model.value}"),
                    label=f"prepared history {model.value}",
                )
            )
            matching = [record for record in records if record.get("output") == statement_index]
            if len(matching) != 1:
                raise ValueError("prepared history does not contain exactly one target record")
            _validate_history(matching[0], model, statement_index)
            histories[model] = matching[0]
            profile = _read_object(
                _application_internal_path(
                    root,
                    _prepared_path(root, f"profile:{model.value}"),
                    label=f"prepared profile {model.value}",
                )
            )
            _validate_profile(profile, model)
            profiles[model] = profile
        raw_attempts = manifest.get("review_attempts")
        if not isinstance(raw_attempts, dict):
            raise ValueError("application review_attempts are invalid")
        attempts = {Reviewer(name): dict(identity) for name, identity in raw_attempts.items()}
        if set(attempts) not in (set(), set(Reviewer)) or any(
            not _nonempty_text(identity.get("task_id"))
            or not _nonempty_text(identity.get("attempt_id"))
            for identity in attempts.values()
        ):
            raise ValueError("application review_attempts are invalid")
        run_id = manifest.get("run_id")
        if run_id is not None and not _nonempty_text(run_id):
            raise ValueError("application run_id is invalid")
        source_hashes = {key: item.get("source_sha256") for key, item in files.items()}
        application_id = _application_id(
            statement_index,
            adjudication,
            histories,
            profiles,
            run_id,
            attempts,
            source_hashes,
        )
        if application_id != root.name:
            raise ValueError("application content does not match its identity")
        expected_manifest = _manifest_payload(
            statement_index=statement_index,
            application_id=application_id,
            run_id=run_id,
            review_attempts=attempts,
            adjudication=adjudication,
            histories=histories,
            profiles=profiles,
            source_hashes=source_hashes,
            target_bytes=target_bytes,
        )
        if manifest != expected_manifest:
            raise ValueError("application manifest does not match its prepared results")

        target_matches = []
        changed_target_matches = []
        current_hashes = {}
        destination_conflict = False
        for key, destination in destinations.items():
            boundary = (
                destination_statement
                if key == "adjudication"
                else feedback
                if key.startswith("history:")
                else profiles_destination
            )
            destination = confined_path(
                destination, boundary, label=f"destination {key}"
            )
            current_hash = _file_sha256(destination) if destination.is_file() else None
            current_hashes[key] = current_hash
            source_hash = files[key]["source_sha256"]
            target_hash = files[key]["target_sha256"]
            target_matches.append(current_hash == target_hash)
            if source_hash != target_hash:
                changed_target_matches.append(current_hash == target_hash)
                if current_hash not in {source_hash, target_hash}:
                    destination_conflict = True
        declared = ApplicationPhase(status.get("phase"))
        if (
            declared is ApplicationPhase.APPLIED
            and application_evidence_path is not None
            and Path(application_evidence_path).exists()
        ):
            from .finalization import verify_application_evidence

            verify_application_evidence(
                application=_application_from_manifest(root, manifest, declared),
                statement_path=destination_statement,
                evidence_path=application_evidence_path,
            )
        adjudication_committed = (
            current_hashes["adjudication"]
            == files["adjudication"]["target_sha256"]
            and all(
                current_hashes[key] == files[key]["source_sha256"]
                for key in destinations
                if key != "adjudication"
            )
        )
        # A finalized older application can be inspected against accumulated
        # successors, but this never authorizes recovery writes to those files.
        successor_verified = False
        if (
            destination_conflict
            and declared is ApplicationPhase.APPLIED
            and application_evidence_path is not None
        ):
            from .finalization import verify_application_evidence

            application = _application_from_manifest(root, manifest, declared)
            verify_application_evidence(
                application=application,
                statement_path=destination_statement,
                evidence_path=application_evidence_path,
            )
            if current_hashes["adjudication"] != files["adjudication"]["target_sha256"]:
                raise ValueError("final adjudication changed after application")
            # Verify a contiguous successor application, including a partial
            # one. Comparing only the global accumulated files would reject a
            # legitimate cut halfway through the next six-file update.
            next_index = statement_index + 1
            next_statement = destination_statement.parent / f"statement_{next_index:03d}"
            next_root = confined_path(
                _application_root(next_statement), destination_statement.parent,
                label="successor application storage",
            )
            candidates = list(next_root.iterdir()) if next_root.is_dir() else []
            if len(candidates) != 1 or not candidates[0].is_dir():
                raise ValueError("accumulated destinations have no unique successor application")
            next_application = inspect_adjudication_application(
                application_path=candidates[0], statement_path=next_statement,
                feedback_root=feedback, profiles_root=profiles_destination,
                application_evidence_path=(next_statement / Path(application_evidence_path).name),
            )
            if (
                next_application.statement_index != next_index
                or next_application.phase not in {
                    ApplicationPhase.ADJUDICATION_COMMITTED,
                    ApplicationPhase.INTERRUPTED, ApplicationPhase.APPLIED,
                }
            ):
                raise ValueError("successor application is not verifiable")
            next_manifest = _read_object(next_application.manifest_path)
            next_adjudication = _read_object(_prepared_path(next_application.application_path, "adjudication"))
            legacy_chain = True
            for index, statement in ((statement_index, destination_statement), (next_index, next_statement)):
                for reviewer in Reviewer:
                    confirmed, error = resolve_confirmed_review_set(
                        statement_path=statement, statement_index=index, reviewer=reviewer,
                    )
                    if confirmed is None:
                        raise ValueError(f"successor review provenance is invalid: {error}")
                    if confirmed.attempt_metadata is not None:
                        legacy_chain = False
            if not legacy_chain:
                _validate_active_review_run(
                    next_statement, next_index, next_application.run_id,
                    next_application.review_attempts,
                )
            for model in BenchmarkModel:
                for kind in ("history", "profile"):
                    key = f"{kind}:{model.value}"
                    if next_manifest["files"][key]["source_sha256"] != files[key]["target_sha256"]:
                        raise ValueError("successor preimage does not match the previous application")
                previous_records = _read_jsonl(_prepared_path(root, f"history:{model.value}"))
                next_records = _read_jsonl(_prepared_path(next_application.application_path, f"history:{model.value}"))
                if next_records[:-1] != previous_records or next_records[-1]["output"] != next_index:
                    raise ValueError("successor history must append exactly its own statement")
                next_profile = _read_object(_prepared_path(next_application.application_path, f"profile:{model.value}"))
                if legacy_chain:
                    # Earlier applications predate OLBM-31's semantic gate.
                    # Preserve their approved read-only non-regression contract;
                    # new application execution still requires verified v2 runs.
                    _validate_profile_transition(profiles[model], next_profile, model)
                else:
                    _shared_validate_profile_adjudication_transition(
                        profiles[model], next_profile, model,
                        statement_index=next_index, adjudication=next_adjudication,
                    )
            successor_verified = True
        if successor_verified:
            phase = ApplicationPhase.APPLIED
        elif destination_conflict:
            phase = ApplicationPhase.INVALID
        elif all(target_matches) and declared is ApplicationPhase.APPLIED:
            phase = ApplicationPhase.APPLIED
        elif adjudication_committed and declared in {
            ApplicationPhase.APPLYING,
            ApplicationPhase.ADJUDICATION_COMMITTED,
        }:
            phase = ApplicationPhase.ADJUDICATION_COMMITTED
        elif declared in {
            ApplicationPhase.PREPARED,
            ApplicationPhase.APPLYING,
        } and not any(changed_target_matches):
            phase = ApplicationPhase.PREPARED
        elif declared in {ApplicationPhase.PREPARED, ApplicationPhase.APPLYING}:
            phase = ApplicationPhase.INTERRUPTED
        else:
            phase = ApplicationPhase.INVALID
        return _application_from_manifest(root, manifest, phase)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError, UnicodeError):
        return AdjudicationApplication(
            statement_index=-1,
            application_id=root.name,
            application_path=root,
            manifest_path=root / "manifest.json",
            status_path=root / "status.json",
            phase=ApplicationPhase.INVALID,
            adjudication_sha256="",
            history_sha256={},
            profile_sha256={},
            run_id=None,
            review_attempts={},
        )


def resume_adjudication_application(
    *,
    application_path: Path,
    statement_path: Path,
    feedback_root: Path,
    profiles_root: Path,
    failure_injector: Optional[FailureInjector] = None,
) -> AdjudicationApplyResult:
    """Continue one prepared/interrupted application from its verified files."""

    try:
        destination_index = int(Path(statement_path).name.removeprefix("statement_"))
    except ValueError as error:
        raise ValueError("statement_path must end with statement_NNN") from error
    destination_statement = review_statement_path(Path(statement_path), destination_index)
    inspected = inspect_adjudication_application(
        application_path=application_path,
        statement_path=destination_statement,
        feedback_root=feedback_root,
        profiles_root=profiles_root,
    )
    if inspected.statement_index in HISTORICAL_STATEMENT_RANGE:
        raise PermissionError("historical statements 000-009 are read-only")
    if inspected.phase is ApplicationPhase.INVALID:
        raise ValueError("adjudication application is invalid and cannot be recovered")
    if destination_statement.name != f"statement_{inspected.statement_index:03d}":
        raise ValueError("statement_path does not match the application statement_index")
    if inspected.phase is ApplicationPhase.APPLIED:
        return AdjudicationApplyResult(AdjudicationApplyStatus.UNCHANGED, inspected)
    if inspected.phase not in {
        ApplicationPhase.PREPARED,
        ApplicationPhase.INTERRUPTED,
        ApplicationPhase.ADJUDICATION_COMMITTED,
    }:
        raise ValueError("adjudication application is not recoverable")

    application_root = inspected.application_path
    manifest_path = _application_internal_path(
        application_root,
        inspected.manifest_path,
        label="application manifest",
    )
    manifest = _read_object(manifest_path)
    destinations = _artifact_destinations(
        destination_statement, Path(feedback_root), Path(profiles_root)
    )
    status_path = _application_internal_path(
        application_root,
        inspected.status_path,
        label="application status",
    )
    _state(status_path, ApplicationPhase.APPLYING)
    for position, (key, destination) in enumerate(destinations.items(), start=1):
        boundary = (
            destination_statement
            if key == "adjudication"
            else Path(feedback_root)
            if key.startswith("history:")
            else Path(profiles_root)
        )
        destination = confined_path(destination, boundary, label=f"destination {key}")
        item = manifest["files"][key]
        current_hash = _file_sha256(destination) if destination.is_file() else None
        if current_hash == item["target_sha256"]:
            continue
        if current_hash != item["source_sha256"]:
            raise ValueError(
                f"destination changed after preparation; recovery blocked: {key}"
            )
        prepared = _application_internal_path(
            application_root,
            _prepared_path(application_root, key),
            label=f"prepared application artifact {key}",
        )
        _atomic_write(destination, prepared.read_bytes())
        if _file_sha256(destination) != item["target_sha256"]:
            raise RuntimeError(f"destination verification failed: {key}")
        if failure_injector is not None:
            failure_injector(f"after_replace:{position}:{key}")

    status_path = _application_internal_path(
        application_root,
        inspected.status_path,
        label="application status",
    )
    _state(status_path, ApplicationPhase.APPLIED)
    verified = inspect_adjudication_application(
        application_path=application_path,
        statement_path=destination_statement,
        feedback_root=feedback_root,
        profiles_root=profiles_root,
    )
    if verified.phase is not ApplicationPhase.APPLIED:
        raise RuntimeError("recovered adjudication application failed verification")
    if failure_injector is not None:
        failure_injector("after_application_commit")
    return AdjudicationApplyResult(AdjudicationApplyStatus.APPLIED, verified)


def _commit_prepared_adjudication(
    *,
    application_path: Path,
    statement_path: Path,
    feedback_root: Path,
    profiles_root: Path,
    failure_injector: Optional[FailureInjector],
) -> AdjudicationCommitResult:
    inspected = inspect_adjudication_application(
        application_path=application_path,
        statement_path=statement_path,
        feedback_root=feedback_root,
        profiles_root=profiles_root,
    )
    if inspected.phase is ApplicationPhase.ADJUDICATION_COMMITTED:
        return AdjudicationCommitResult(AdjudicationCommitStatus.UNCHANGED, inspected)
    if inspected.phase is ApplicationPhase.APPLIED:
        return AdjudicationCommitResult(AdjudicationCommitStatus.UNCHANGED, inspected)
    if inspected.phase is not ApplicationPhase.PREPARED:
        raise ValueError("adjudication application is not ready for final-result commit")

    root = inspected.application_path
    manifest = _read_object(
        _application_internal_path(root, inspected.manifest_path, label="application manifest")
    )
    status_path = _application_internal_path(
        root, inspected.status_path, label="application status"
    )
    destination = confined_path(
        Path(statement_path) / "adjudication.json",
        Path(statement_path),
        label="destination adjudication",
    )
    item = manifest["files"]["adjudication"]
    current_hash = _file_sha256(destination) if destination.is_file() else None
    if current_hash != item["target_sha256"]:
        if current_hash != item["source_sha256"]:
            raise ValueError("destination changed during adjudication commit")
        _state(status_path, ApplicationPhase.APPLYING)
        if failure_injector is not None:
            failure_injector("before_adjudication_commit")
        prepared = _application_internal_path(
            root,
            _prepared_path(root, "adjudication"),
            label="prepared adjudication",
        )
        _atomic_write(destination, prepared.read_bytes())
        if _file_sha256(destination) != item["target_sha256"]:
            raise RuntimeError("destination verification failed: adjudication")
        if failure_injector is not None:
            failure_injector("after_replace:1:adjudication")
    _state(status_path, ApplicationPhase.ADJUDICATION_COMMITTED)
    verified = inspect_adjudication_application(
        application_path=root,
        statement_path=statement_path,
        feedback_root=feedback_root,
        profiles_root=profiles_root,
    )
    if verified.phase is not ApplicationPhase.ADJUDICATION_COMMITTED:
        raise RuntimeError("committed adjudication failed verification")
    if failure_injector is not None:
        failure_injector("after_adjudication_commit")
    return AdjudicationCommitResult(AdjudicationCommitStatus.COMMITTED, verified)


def commit_adjudication_bundle(
    *,
    statement_index: int,
    statement_path: Path,
    feedback_root: Path,
    profiles_root: Path,
    adjudication: Mapping[str, object],
    histories: Sequence[Mapping[str, object]],
    profiles: Sequence[Mapping[str, object]],
    run_id: Optional[str] = None,
    review_attempts: Optional[Mapping[Reviewer | str, Mapping[str, str]]] = None,
    failure_injector: Optional[FailureInjector] = None,
) -> AdjudicationCommitResult:
    """Prepare the existing 1+3+3 application and commit only adjudication.json.

    Histories and profiles remain immutable prepared material.  Their operational
    destinations are deliberately untouched until a later incremental-application
    step resumes this same application.
    """

    if not _valid_index(statement_index):
        raise ValueError("statement_index must be a non-negative integer")
    if statement_index in HISTORICAL_STATEMENT_RANGE:
        raise PermissionError("historical statements 000-009 are read-only")
    destination_statement = review_statement_path(Path(statement_path), statement_index)
    if run_id is not None and not _nonempty_text(run_id):
        raise ValueError("run_id must be a non-empty string when supplied")

    adjudication_value = _json_clone(adjudication, "adjudication")
    _validate_adjudication(adjudication_value, statement_index)
    history_values = _normalize_model_records(histories, label="history")
    profile_values = _normalize_model_records(profiles, label="profile")
    for model in BenchmarkModel:
        _validate_history(history_values[model], model, statement_index)
        _validate_profile(profile_values[model], model)
    attempts = _normalize_review_attempts(
        destination_statement, statement_index, review_attempts
    )
    _validate_active_review_run(destination_statement, statement_index, run_id, attempts)

    feedback = confined_path(Path(feedback_root), Path(feedback_root), label="feedback_root")
    profile_root = confined_path(
        Path(profiles_root), Path(profiles_root), label="profiles_root"
    )
    confined_path(
        _application_root(destination_statement),
        destination_statement,
        label="adjudication application storage",
    )
    destinations = _artifact_destinations(destination_statement, feedback, profile_root)
    for key, destination in destinations.items():
        boundary = (
            destination_statement
            if key == "adjudication"
            else feedback
            if key.startswith("history:")
            else profile_root
        )
        confined_path(destination, boundary, label=f"destination {key}")

    _validate_profile_adjudications(
        profiles=profile_values,
        profiles_root=profile_root,
        statement_index=statement_index,
        adjudication=adjudication_value,
    )
    prior_histories = _validate_existing_histories(
        histories=history_values,
        feedback_root=feedback,
        statement_index=statement_index,
    )
    adjudication_path = destination_statement / "adjudication.json"
    if adjudication_path.exists() and _read_object(adjudication_path) != adjudication_value:
        raise ValueError("a different adjudication.json already exists for this statement")

    target_bytes: dict[str, bytes] = {"adjudication": _canonical_bytes(adjudication_value)}
    for model in BenchmarkModel:
        records = prior_histories[model]
        if not any(record.get("output") == statement_index for record in records):
            records = [*records, history_values[model]]
        target_bytes[f"history:{model.value}"] = _jsonl_bytes(records)
        target_bytes[f"profile:{model.value}"] = _canonical_bytes(profile_values[model])
    source_hashes = {
        key: _file_sha256(path) if path.is_file() else None
        for key, path in destinations.items()
    }
    application_id = _application_id(
        statement_index,
        adjudication_value,
        history_values,
        profile_values,
        run_id,
        attempts,
        source_hashes,
    )
    applications_root = _application_root(destination_statement)
    application_path = applications_root / application_id
    if applications_root.is_dir():
        for other in applications_root.iterdir():
            if other.is_symlink():
                raise ValueError("adjudication application storage contains a symbolic link")
            if not other.is_dir() or other.name.startswith(".staging-"):
                continue
            other_manifest = _read_object(
                _application_internal_path(
                    other, other / "manifest.json", label="application manifest"
                )
            )
            if other_manifest.get("statement_index") != statement_index:
                continue
            existing = inspect_adjudication_application(
                application_path=other,
                statement_path=destination_statement,
                feedback_root=feedback,
                profiles_root=profile_root,
            )
            if existing.phase is ApplicationPhase.INVALID:
                raise ValueError("existing adjudication application is invalid")
            if not _manifest_matches_request(
                other_manifest,
                statement_index=statement_index,
                run_id=run_id,
                review_attempts=attempts,
                adjudication=adjudication_value,
                histories=history_values,
                profiles=profile_values,
                target_bytes=target_bytes,
            ):
                raise ValueError(
                    "a different adjudication application already exists for this statement"
                )
            return _commit_prepared_adjudication(
                application_path=other,
                statement_path=destination_statement,
                feedback_root=feedback,
                profiles_root=profile_root,
                failure_injector=failure_injector,
            )

    manifest = _manifest_payload(
        statement_index=statement_index,
        application_id=application_id,
        run_id=run_id,
        review_attempts=attempts,
        adjudication=adjudication_value,
        histories=history_values,
        profiles=profile_values,
        source_hashes=source_hashes,
        target_bytes=target_bytes,
    )
    _prepare_application(
        application_path=application_path,
        manifest=manifest,
        target_bytes=target_bytes,
    )
    if failure_injector is not None:
        failure_injector("after_prepare")
    return _commit_prepared_adjudication(
        application_path=application_path,
        statement_path=destination_statement,
        feedback_root=feedback,
        profiles_root=profile_root,
        failure_injector=failure_injector,
    )


def apply_adjudicated_results(
    *,
    statement_index: int,
    statement_path: Path,
    feedback_root: Path,
    profiles_root: Path,
    adjudication: Mapping[str, object],
    histories: Sequence[Mapping[str, object]],
    profiles: Sequence[Mapping[str, object]],
    run_id: Optional[str] = None,
    review_attempts: Optional[Mapping[Reviewer | str, Mapping[str, str]]] = None,
    failure_injector: Optional[FailureInjector] = None,
) -> AdjudicationApplyResult:
    """Validate, prepare, apply, and verify seven adjudicated destination files."""

    if not _valid_index(statement_index):
        raise ValueError("statement_index must be a non-negative integer")
    if statement_index in HISTORICAL_STATEMENT_RANGE:
        raise PermissionError("historical statements 000-009 are read-only")
    destination_statement = review_statement_path(Path(statement_path), statement_index)
    if run_id is not None and not _nonempty_text(run_id):
        raise ValueError("run_id must be a non-empty string when supplied")

    adjudication_value = _json_clone(adjudication, "adjudication")
    _validate_adjudication(adjudication_value, statement_index)
    history_values = _normalize_model_records(histories, label="history")
    profile_values = _normalize_model_records(profiles, label="profile")
    for model in BenchmarkModel:
        _validate_history(history_values[model], model, statement_index)
        _validate_profile(profile_values[model], model)
    attempts = _normalize_review_attempts(
        destination_statement, statement_index, review_attempts
    )

    feedback = confined_path(Path(feedback_root), Path(feedback_root), label="feedback_root")
    profile_root = confined_path(Path(profiles_root), Path(profiles_root), label="profiles_root")
    confined_path(
        _application_root(destination_statement),
        destination_statement,
        label="adjudication application storage",
    )
    guarded_destinations = _artifact_destinations(
        destination_statement, feedback, profile_root
    )
    for key, destination in guarded_destinations.items():
        boundary = (
            destination_statement
            if key == "adjudication"
            else feedback
            if key.startswith("history:")
            else profile_root
        )
        confined_path(destination, boundary, label=f"destination {key}")
    _validate_profile_destinations(profile_values, profile_root)
    prior_histories = _validate_existing_histories(
        histories=history_values,
        feedback_root=feedback,
        statement_index=statement_index,
    )
    adjudication_path = destination_statement / "adjudication.json"
    if adjudication_path.exists() and _read_object(adjudication_path) != adjudication_value:
        raise ValueError("a different adjudication.json already exists for this statement")

    destinations = _artifact_destinations(destination_statement, feedback, profile_root)
    target_bytes: dict[str, bytes] = {"adjudication": _canonical_bytes(adjudication_value)}
    for model in BenchmarkModel:
        records = prior_histories[model]
        if not any(record.get("output") == statement_index for record in records):
            records = [*records, history_values[model]]
        target_bytes[f"history:{model.value}"] = _jsonl_bytes(records)
        target_bytes[f"profile:{model.value}"] = _canonical_bytes(profile_values[model])
    source_hashes = {
        key: _file_sha256(path) if path.is_file() else None
        for key, path in destinations.items()
    }
    application_id = _application_id(
        statement_index,
        adjudication_value,
        history_values,
        profile_values,
        run_id,
        attempts,
        source_hashes,
    )
    applications_root = _application_root(destination_statement)
    application_path = applications_root / application_id
    if applications_root.is_dir():
        for other in applications_root.iterdir():
            if other.is_symlink():
                raise ValueError("adjudication application storage contains a symbolic link")
            if not other.is_dir() or other.name.startswith(".staging-"):
                continue
            try:
                other_manifest = _read_object(
                    _application_internal_path(
                        other, other / "manifest.json", label="application manifest"
                    )
                )
            except (ValueError, json.JSONDecodeError, OSError, UnicodeError) as error:
                raise ValueError("an unreadable adjudication application already exists") from error
            if other_manifest.get("statement_index") == statement_index:
                existing = inspect_adjudication_application(
                    application_path=other,
                    statement_path=destination_statement,
                    feedback_root=feedback,
                    profiles_root=profile_root,
                )
                if existing.phase is ApplicationPhase.INVALID:
                    raise ValueError("existing adjudication application is invalid")
                if _manifest_matches_request(
                    other_manifest,
                    statement_index=statement_index,
                    run_id=run_id,
                    review_attempts=attempts,
                    adjudication=adjudication_value,
                    histories=history_values,
                    profiles=profile_values,
                    target_bytes=target_bytes,
                ):
                    return resume_adjudication_application(
                        application_path=other,
                        statement_path=destination_statement,
                        feedback_root=feedback,
                        profiles_root=profile_root,
                        failure_injector=failure_injector,
                    )
                raise ValueError(
                    "a different adjudication application already exists for this statement"
                )
    manifest = _manifest_payload(
        statement_index=statement_index,
        application_id=application_id,
        run_id=run_id,
        review_attempts=attempts,
        adjudication=adjudication_value,
        histories=history_values,
        profiles=profile_values,
        source_hashes=source_hashes,
        target_bytes=target_bytes,
    )
    _prepare_application(
        application_path=application_path,
        manifest=manifest,
        target_bytes=target_bytes,
    )
    if failure_injector is not None:
        failure_injector("after_prepare")

    files = manifest["files"]
    already_applied = all(
        destination.is_file()
        and _file_sha256(destination) == files[key]["target_sha256"]
        for key, destination in destinations.items()
    )
    if already_applied:
        _state(
            _application_internal_path(
                application_path,
                application_path / "status.json",
                label="application status",
            ),
            ApplicationPhase.APPLIED,
        )
        inspected = inspect_adjudication_application(
            application_path=application_path,
            statement_path=destination_statement,
            feedback_root=feedback,
            profiles_root=profile_root,
        )
        if inspected.phase is not ApplicationPhase.APPLIED:
            raise RuntimeError("stored adjudication application failed verification")
        return AdjudicationApplyResult(AdjudicationApplyStatus.UNCHANGED, inspected)

    _state(
        _application_internal_path(
            application_path,
            application_path / "status.json",
            label="application status",
        ),
        ApplicationPhase.APPLYING,
    )
    for position, (key, destination) in enumerate(destinations.items(), start=1):
        item = files[key]
        current_hash = _file_sha256(destination) if destination.is_file() else None
        if current_hash == item["target_sha256"]:
            continue
        if current_hash != item["source_sha256"]:
            raise ValueError(f"destination changed during application: {key}")
        prepared = _application_internal_path(
            application_path,
            _prepared_path(application_path, key),
            label=f"prepared application artifact {key}",
        )
        _atomic_write(destination, prepared.read_bytes())
        if _file_sha256(destination) != item["target_sha256"]:
            raise RuntimeError(f"destination verification failed: {key}")
        if failure_injector is not None:
            failure_injector(f"after_replace:{position}:{key}")

    _state(
        _application_internal_path(
            application_path,
            application_path / "status.json",
            label="application status",
        ),
        ApplicationPhase.APPLIED,
    )
    inspected = inspect_adjudication_application(
        application_path=application_path,
        statement_path=destination_statement,
        feedback_root=feedback,
        profiles_root=profile_root,
    )
    if inspected.phase is not ApplicationPhase.APPLIED:
        raise RuntimeError("stored adjudication application failed verification")
    return AdjudicationApplyResult(AdjudicationApplyStatus.APPLIED, inspected)
