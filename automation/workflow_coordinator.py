"""Lease-protected, read-only coordination for one workflow planning cycle.

The coordinator deliberately executes no plan.  It acquires the global workflow
lease, reconstructs O-01 state twice through the approved inspectors, and emits
only a stable decision that a future, separately authorized executor may use.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from automation.orchestrator import (
    OrchestratorConfig,
    PlanAction,
    StatementPlan,
    plan_next_statement,
)
from automation.workflow_lease import (
    LeaseOwner,
    WorkflowLease,
    WorkflowLeaseError,
    WorkflowLeaseInUseError,
)


_SYNCHRONIZED_PATH_MARKERS = (
    "onedrive",
    "icloud",
    "dropbox",
    "google drive",
    "googledrive",
    "cloudstorage",
    "box sync",
    "boxsync",
)

_SNAPSHOT_DOMAIN = b"workflow-precondition-v2"
_SNAPSHOT_RECORD_PREFIX = b"\x00WPC2"
_SNAPSHOT_ROOT = b"R"
_SNAPSHOT_MISSING = b"M"
_SNAPSHOT_FILE = b"F"
_SNAPSHOT_DIRECTORY = b"D"
_SNAPSHOT_OTHER = b"O"
_MAX_FRAMED_LENGTH = (1 << 64) - 1


class CoordinationStatus(str, Enum):
    """Outcome of a protected planning and revalidation cycle."""

    ACTION_REQUIRED = "ACTION_REQUIRED"
    CURRENT = "CURRENT"
    STALE_PLAN = "STALE_PLAN"
    CONTENDED = "CONTENDED"
    BLOCKED = "BLOCKED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


@dataclass(frozen=True)
class WorkflowCoordinatorConfig:
    """Validated inputs for lease-protected coordination.

    ``lease_location_confirmed`` is an explicit operator assertion that the
    location is dedicated, stable and not synchronized.  Known synchronized or
    operational locations are rejected even when the assertion is true.
    """

    workflow: OrchestratorConfig
    lease_path: Path
    owner_id: str
    lease_location_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.workflow, OrchestratorConfig):
            raise TypeError("workflow must be an OrchestratorConfig")
        lease_path = Path(os.path.abspath(os.fspath(Path(self.lease_path).expanduser())))
        resolved_lease_path = lease_path.resolve(strict=False)
        object.__setattr__(self, "lease_path", lease_path)
        if not self.lease_location_confirmed:
            raise ValueError(
                "lease location requires explicit operator confirmation that it is "
                "private, stable and not synchronized"
            )
        if _has_synchronized_component(lease_path) or _has_synchronized_component(
            resolved_lease_path
        ):
            raise ValueError("workflow lease must not be stored in a synchronized path")
        for root in _operational_roots(self.workflow):
            if _is_relative_to(resolved_lease_path, root):
                raise ValueError("workflow lease must be outside data and config roots")


@dataclass(frozen=True)
class CoordinationExpectation:
    """Previously observed plan and optional storage-precondition fingerprint."""

    plan: StatementPlan
    precondition_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, StatementPlan):
            raise TypeError("plan must be a StatementPlan")
        if self.precondition_sha256 is not None and (
            len(self.precondition_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.precondition_sha256
            )
        ):
            raise ValueError("precondition_sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class CoordinationResult:
    status: CoordinationStatus
    reason: str
    plan: Optional[StatementPlan] = None
    precondition_sha256: Optional[str] = None
    authorization_required: bool = False
    lease_owner: Optional[LeaseOwner] = None

    def expectation(self) -> CoordinationExpectation:
        if self.plan is None or self.precondition_sha256 is None:
            raise ValueError("only a stable coordinated plan can become an expectation")
        return CoordinationExpectation(self.plan, self.precondition_sha256)


def coordinate_workflow_cycle(
    config: WorkflowCoordinatorConfig,
    *,
    expected: Optional[CoordinationExpectation] = None,
) -> CoordinationResult:
    """Return one protected, revalidated decision without executing its action."""

    if not isinstance(config, WorkflowCoordinatorConfig):
        raise TypeError("config must be a WorkflowCoordinatorConfig")
    if expected is not None and not isinstance(expected, CoordinationExpectation):
        raise TypeError("expected must be a CoordinationExpectation")

    lease = WorkflowLease(config.lease_path, config.owner_id)
    try:
        owner = lease.acquire(blocking=False)
    except WorkflowLeaseInUseError as error:
        return CoordinationResult(
            CoordinationStatus.CONTENDED,
            str(error),
            lease_owner=error.owner,
        )
    except WorkflowLeaseError as error:
        return CoordinationResult(CoordinationStatus.VALIDATION_FAILED, str(error))

    result: Optional[CoordinationResult] = None
    try:
        result = coordinate_workflow_under_lease(config, lease, expected=expected)
    finally:
        try:
            lease.release()
        except WorkflowLeaseError as error:
            if result is None or result.status is not CoordinationStatus.VALIDATION_FAILED:
                result = CoordinationResult(
                    CoordinationStatus.VALIDATION_FAILED,
                    f"workflow lease could not be released safely: {error}",
                    plan=None if result is None else result.plan,
                    precondition_sha256=(
                        None if result is None else result.precondition_sha256
                    ),
                    lease_owner=owner,
                )
    assert result is not None
    return result


def coordinate_workflow_under_lease(
    config: WorkflowCoordinatorConfig,
    lease: WorkflowLease,
    *,
    expected: Optional[CoordinationExpectation] = None,
) -> CoordinationResult:
    """Revalidate one decision while leaving an already-held lease acquired.

    This is the reusable protected section shared by the read-only coordinator
    and narrowly scoped executors.  Callers own acquisition and release; a
    successful result is not authorization after the lease has been released.
    """

    if not isinstance(config, WorkflowCoordinatorConfig):
        raise TypeError("config must be a WorkflowCoordinatorConfig")
    if not isinstance(lease, WorkflowLease):
        raise TypeError("lease must be a WorkflowLease")
    if expected is not None and not isinstance(expected, CoordinationExpectation):
        raise TypeError("expected must be a CoordinationExpectation")
    if lease.lock_path != config.lease_path:
        raise ValueError("lease path does not match coordinator configuration")

    owner: Optional[LeaseOwner] = lease.owner
    try:
        owner = lease.validate()
        _precondition_fingerprint(config.workflow, None)
        first_plan = plan_next_statement(config.workflow)
        lease.validate()
        first_fingerprint = _precondition_fingerprint(config.workflow, first_plan)

        # A second complete O-01 pass is intentional: fingerprints detect change,
        # but never replace the storage validators and identity checks.
        if _precondition_fingerprint(config.workflow, first_plan) != first_fingerprint:
            raise DependencyValidationError(
                "workflow dependencies changed before the second planning pass"
            )
        second_plan = plan_next_statement(config.workflow)
        lease.validate()
        second_fingerprint = _precondition_fingerprint(config.workflow, second_plan)

        if first_plan != second_plan or first_fingerprint != second_fingerprint:
            return CoordinationResult(
                CoordinationStatus.VALIDATION_FAILED,
                "workflow state changed during protected revalidation",
                plan=None,
                precondition_sha256=second_fingerprint,
                lease_owner=owner,
            )
        if expected is not None and (
            expected.plan != second_plan
            or (
                expected.precondition_sha256 is not None
                and expected.precondition_sha256 != second_fingerprint
            )
        ):
            return CoordinationResult(
                CoordinationStatus.STALE_PLAN,
                "the previously observed plan or its storage preconditions are stale",
                plan=second_plan,
                precondition_sha256=second_fingerprint,
                lease_owner=owner,
            )
        if second_plan.action in {PlanAction.BLOCKED, PlanAction.WAIT_OPERATOR}:
            return CoordinationResult(
                CoordinationStatus.BLOCKED,
                second_plan.reason,
                plan=second_plan,
                precondition_sha256=second_fingerprint,
                lease_owner=owner,
            )
        if second_plan.action is PlanAction.COMPLETE:
            return CoordinationResult(
                CoordinationStatus.CURRENT,
                second_plan.reason,
                plan=second_plan,
                precondition_sha256=second_fingerprint,
                lease_owner=owner,
            )
        return CoordinationResult(
            CoordinationStatus.ACTION_REQUIRED,
            "the protected plan is current; execution remains unauthorized",
            plan=second_plan,
            precondition_sha256=second_fingerprint,
            authorization_required=True,
            lease_owner=owner,
        )
    except (OSError, TypeError, UnicodeError, ValueError, WorkflowLeaseError) as error:
        return CoordinationResult(
            CoordinationStatus.VALIDATION_FAILED,
            f"protected planning or revalidation failed: {error}",
            lease_owner=owner,
        )


def confirm_workflow_expectation_under_lease(
    config: WorkflowCoordinatorConfig,
    lease: WorkflowLease,
    expectation: CoordinationExpectation,
) -> CoordinationResult:
    """Confirm a stable plan and fingerprint immediately before an effect."""

    if not isinstance(expectation, CoordinationExpectation):
        raise TypeError("expectation must be a CoordinationExpectation")
    if expectation.precondition_sha256 is None:
        raise ValueError("an immediate execution check requires a fingerprint")
    if lease.lock_path != config.lease_path:
        raise ValueError("lease path does not match coordinator configuration")

    owner = lease.owner
    try:
        owner = lease.validate()
        before = _precondition_fingerprint(config.workflow, expectation.plan)
        current_plan = plan_next_statement(config.workflow)
        lease.validate()
        after = _precondition_fingerprint(config.workflow, current_plan)
        if before != after:
            return CoordinationResult(
                CoordinationStatus.VALIDATION_FAILED,
                "workflow state changed during the immediate execution check",
                precondition_sha256=after,
                lease_owner=owner,
            )
        if current_plan != expectation.plan or after != expectation.precondition_sha256:
            return CoordinationResult(
                CoordinationStatus.STALE_PLAN,
                "the protected plan or its storage preconditions became stale",
                plan=current_plan,
                precondition_sha256=after,
                lease_owner=owner,
            )
        return CoordinationResult(
            CoordinationStatus.ACTION_REQUIRED,
            "the protected expectation remains current under the acquired lease",
            plan=current_plan,
            precondition_sha256=after,
            authorization_required=True,
            lease_owner=owner,
        )
    except (OSError, TypeError, UnicodeError, ValueError, WorkflowLeaseError) as error:
        return CoordinationResult(
            CoordinationStatus.VALIDATION_FAILED,
            f"immediate workflow confirmation failed: {error}",
            lease_owner=owner,
        )


def _has_synchronized_component(path: Path) -> bool:
    return any(
        marker in component.casefold()
        for component in path.parts
        for marker in _SYNCHRONIZED_PATH_MARKERS
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _named_root(path: Path) -> Optional[Path]:
    absolute = Path(os.path.abspath(os.fspath(path))).resolve(strict=False)
    candidates = [
        parent
        for parent in (absolute, *absolute.parents)
        if parent.name.casefold() in {"data", "config"}
    ]
    return candidates[0] if candidates else None


def _operational_roots(config: OrchestratorConfig) -> tuple[Path, ...]:
    roots = {
        root
        for path in (
            config.statements_path,
            config.runs_root,
            config.review_cases_root,
            config.feedback_root,
            config.profiles_root,
        )
        if (root := _named_root(path)) is not None
    }
    return tuple(sorted(roots, key=os.fspath))


class DependencyValidationError(ValueError):
    """An observed workflow dependency cannot be verified conservatively."""


def _precondition_fingerprint(
    config: OrchestratorConfig, plan: Optional[StatementPlan]
) -> str:
    digest = hashlib.sha256()
    digest.update(_SNAPSHOT_DOMAIN)
    # Frame configuration too: even when the next action is identical, changing
    # either bound invalidates the previously protected expectation.
    bounds = f"{config.start_statement}:{config.end_statement}".encode("ascii")
    _update_record_header(digest, _SNAPSHOT_FILE, b"statement_bounds", len(bounds))
    digest.update(bounds)
    paths: list[tuple[str, Path]] = [
        ("statements", config.statements_path),
        ("runs", config.runs_root),
        ("review_cases", config.review_cases_root),
        ("feedback", config.feedback_root),
        ("profiles", config.profiles_root),
    ]
    if plan is not None and plan.run_id is not None:
        paths.append(("selected_run", config.runs_root / plan.run_id))
    elif config.run is not None:
        requested = Path(config.run)
        paths.append(
            (
                "requested_run",
                requested if requested.is_absolute() else config.runs_root / requested,
            )
        )
    for label, path in paths:
        _update_path_digest(digest, label, Path(path))
    return digest.hexdigest()


def _update_path_digest(digest: Any, label: str, path: Path) -> None:
    _update_record_header(digest, _SNAPSHOT_ROOT, label.encode("utf-8"), 0)
    path = _absolute_without_resolution(path)
    _reject_symlink_components(path, label)
    try:
        root_stat = path.lstat()
    except FileNotFoundError:
        _update_record_header(digest, _SNAPSHOT_MISSING, b"", 0)
        return
    if stat.S_ISLNK(root_stat.st_mode):
        raise DependencyValidationError(
            f"symbolic dependency rejected for {label}: {path}"
        )
    if stat.S_ISREG(root_stat.st_mode):
        _update_file_bytes(digest, path, root_stat, label, b"")
        return
    if not stat.S_ISDIR(root_stat.st_mode):
        _update_other_record(digest, b"", root_stat.st_mode)
        return
    _update_record_header(digest, _SNAPSHOT_DIRECTORY, b"", 0)
    _update_directory_digest(digest, path, path, root_stat, label)


def _update_record_header(
    digest: Any, kind: bytes, relative: bytes, payload_length: int
) -> None:
    if len(kind) != 1:
        raise ValueError("snapshot record kind must be exactly one byte")
    if len(relative) > _MAX_FRAMED_LENGTH or not (
        0 <= payload_length <= _MAX_FRAMED_LENGTH
    ):
        raise DependencyValidationError("snapshot record exceeds framing limits")
    digest.update(_SNAPSHOT_RECORD_PREFIX)
    digest.update(kind)
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(payload_length.to_bytes(8, "big"))


def _update_other_record(digest: Any, relative: bytes, mode: int) -> None:
    payload = mode.to_bytes(8, "big", signed=False)
    _update_record_header(digest, _SNAPSHOT_OTHER, relative, len(payload))
    digest.update(payload)


def _absolute_without_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            inspected = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(inspected.st_mode):
            raise DependencyValidationError(
                f"symbolic dependency rejected for {label}: {current}"
            )


def _identity(inspected: os.stat_result) -> tuple[int, int, int]:
    return inspected.st_dev, inspected.st_ino, stat.S_IFMT(inspected.st_mode)


def _content_identity(inspected: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        *_identity(inspected),
        inspected.st_size,
        inspected.st_mtime_ns,
    )


def _update_directory_digest(
    digest: Any,
    root: Path,
    directory: Path,
    before: os.stat_result,
    label: str,
) -> None:
    try:
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
    except OSError as error:
        raise DependencyValidationError(
            f"dependency directory cannot be inspected for {label}: {directory}: {error}"
        ) from error
    for entry in children:
        child = directory / entry.name
        relative = child.relative_to(root).as_posix().encode("utf-8")
        child_stat = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(child_stat.st_mode):
            raise DependencyValidationError(
                f"symbolic dependency rejected for {label}: {child}"
            )
        if stat.S_ISREG(child_stat.st_mode):
            _update_file_bytes(digest, child, child_stat, label, relative)
        elif stat.S_ISDIR(child_stat.st_mode):
            _update_record_header(digest, _SNAPSHOT_DIRECTORY, relative, 0)
            _update_directory_digest(digest, root, child, child_stat, label)
        else:
            _update_other_record(digest, relative, child_stat.st_mode)
    after = directory.lstat()
    if _content_identity(before) != _content_identity(after):
        raise DependencyValidationError(
            f"dependency directory changed while inspected for {label}: {directory}"
        )


def _update_file_bytes(
    digest: Any,
    path: Path,
    before: os.stat_result,
    label: str,
    relative: bytes,
) -> None:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise DependencyValidationError(
            f"dependency file cannot be opened safely for {label}: {path}: {error}"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
            raise DependencyValidationError(
                f"dependency file identity changed while opened for {label}: {path}"
            )
        _update_record_header(digest, _SNAPSHOT_FILE, relative, opened.st_size)
        bytes_read = 0
        while chunk := os.read(descriptor, 65536):
            digest.update(chunk)
            bytes_read += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        current = path.lstat()
    except FileNotFoundError as error:
        raise DependencyValidationError(
            f"dependency file disappeared while inspected for {label}: {path}"
        ) from error
    if (
        bytes_read != opened.st_size
        or _content_identity(opened) != _content_identity(after)
        or _content_identity(after) != _content_identity(current)
    ):
        raise DependencyValidationError(
            f"dependency file changed while inspected for {label}: {path}"
        )


__all__ = [
    "CoordinationExpectation",
    "CoordinationResult",
    "CoordinationStatus",
    "WorkflowCoordinatorConfig",
    "confirm_workflow_expectation_under_lease",
    "coordinate_workflow_under_lease",
    "coordinate_workflow_cycle",
]
