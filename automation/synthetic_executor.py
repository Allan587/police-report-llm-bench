"""Minimal synthetic effect executed inside the global workflow lease.

This module is deliberately not an operational writer.  It creates one new
file below an explicitly confirmed temporary root so the lease/revalidation
protocol can be exercised without touching review, feedback or profile data.

``COMPLETED`` proves that the bytes observed through the created file
descriptor matched the requested payload while the lease was held.  It does
not promise durability after power loss or integrity after the lease is
released.  An error after creation may leave a partial file and deliberately
does not emit successful evidence.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from automation.orchestrator import StatementPlan
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


class SyntheticExecutionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    CONTENDED = "CONTENDED"
    STALE_PLAN = "STALE_PLAN"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    OPERATION_REJECTED = "OPERATION_REJECTED"
    EXECUTION_ERROR = "EXECUTION_ERROR"


@dataclass(frozen=True)
class SyntheticExecutorConfig:
    coordinator: WorkflowCoordinatorConfig
    temporary_base: Path
    synthetic_root: Path
    temporary_location_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.coordinator, WorkflowCoordinatorConfig):
            raise TypeError("coordinator must be a WorkflowCoordinatorConfig")
        if not self.temporary_location_confirmed:
            raise ValueError("synthetic execution requires explicit temporary-root confirmation")
        temporary_base = _absolute(Path(self.temporary_base))
        synthetic_root = _absolute(Path(self.synthetic_root))
        _require_plain_directory(temporary_base, "temporary_base")
        _require_plain_directory(synthetic_root, "synthetic_root")
        if not _is_relative_to(synthetic_root, temporary_base):
            raise ValueError("synthetic_root must be confined to temporary_base")
        workflow_container = _workflow_container(self.coordinator)
        if _paths_overlap(synthetic_root, workflow_container):
            raise ValueError("synthetic_root must be outside the workflow repository container")
        for operational in _operational_paths(self.coordinator):
            if _paths_overlap(synthetic_root, operational):
                raise ValueError("synthetic_root must not overlap operational workflow paths")
        object.__setattr__(self, "temporary_base", temporary_base)
        object.__setattr__(self, "synthetic_root", synthetic_root)


@dataclass(frozen=True)
class SyntheticWriteOperation:
    root: Path
    relative_path: Path
    payload: bytes
    root_identity: tuple[int, int, int]
    parent_identity: tuple[int, int, int]
    expected_preimage_sha256: Optional[str] = None


@dataclass(frozen=True)
class SyntheticExecutionEvidence:
    target: Path
    plan: StatementPlan
    workflow_precondition_sha256: str
    expected_preimage_sha256: Optional[str]
    result_sha256: str
    bytes_written: int
    lease_owner: LeaseOwner


@dataclass(frozen=True)
class SyntheticExecutionResult:
    status: SyntheticExecutionStatus
    reason: str
    plan: Optional[StatementPlan] = None
    workflow_precondition_sha256: Optional[str] = None
    evidence: Optional[SyntheticExecutionEvidence] = None
    lease_owner: Optional[LeaseOwner] = None


class SyntheticOperationRejected(ValueError):
    """The requested effect no longer satisfies its safe preconditions."""


def prepare_synthetic_write(
    config: SyntheticExecutorConfig,
    relative_path: Path,
    payload: bytes,
) -> SyntheticWriteOperation:
    """Capture the identity and absent-file preimage for one synthetic create."""

    if not isinstance(config, SyntheticExecutorConfig):
        raise TypeError("config must be a SyntheticExecutorConfig")
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise SyntheticOperationRejected("synthetic destination must be a confined relative path")
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")

    root = config.synthetic_root
    parent = root.joinpath(*relative.parts[:-1]) if len(relative.parts) > 1 else root
    target = root / relative
    _require_plain_directory(root, "synthetic_root")
    _require_plain_directory(parent, "synthetic destination parent")
    if not _is_relative_to(parent, root):
        raise SyntheticOperationRejected("synthetic destination escapes its authorized root")
    if target.is_symlink() or target.exists():
        raise SyntheticOperationRejected("synthetic destination preimage must be absent")
    return SyntheticWriteOperation(
        root=root,
        relative_path=relative,
        payload=bytes(payload),
        root_identity=_identity(root.lstat()),
        parent_identity=_identity(parent.lstat()),
    )


def execute_synthetic_write(
    config: SyntheticExecutorConfig,
    operation: SyntheticWriteOperation,
    *,
    expected: Optional[CoordinationExpectation] = None,
) -> SyntheticExecutionResult:
    """Create one synthetic file while the same workflow lease remains held."""

    if not isinstance(config, SyntheticExecutorConfig):
        raise TypeError("config must be a SyntheticExecutorConfig")
    if not isinstance(operation, SyntheticWriteOperation):
        raise TypeError("operation must be a SyntheticWriteOperation")
    try:
        _validate_operation_shape(config, operation)
    except (TypeError, SyntheticOperationRejected) as error:
        return SyntheticExecutionResult(
            SyntheticExecutionStatus.OPERATION_REJECTED,
            str(error),
        )

    lease = WorkflowLease(
        config.coordinator.lease_path,
        config.coordinator.owner_id,
    )
    try:
        owner = lease.acquire(blocking=False)
    except WorkflowLeaseInUseError as error:
        return SyntheticExecutionResult(
            SyntheticExecutionStatus.CONTENDED,
            str(error),
            lease_owner=error.owner,
        )
    except WorkflowLeaseError as error:
        return SyntheticExecutionResult(
            SyntheticExecutionStatus.VALIDATION_FAILED,
            str(error),
        )

    result: Optional[SyntheticExecutionResult] = None
    try:
        coordinated = coordinate_workflow_under_lease(
            config.coordinator,
            lease,
            expected=expected,
        )
        if coordinated.status is CoordinationStatus.STALE_PLAN:
            result = _from_coordination(SyntheticExecutionStatus.STALE_PLAN, coordinated)
        elif coordinated.status is CoordinationStatus.VALIDATION_FAILED:
            result = _from_coordination(
                SyntheticExecutionStatus.VALIDATION_FAILED, coordinated
            )
        elif coordinated.status is not CoordinationStatus.ACTION_REQUIRED:
            result = _from_coordination(
                SyntheticExecutionStatus.OPERATION_REJECTED, coordinated
            )
        else:
            protected_expectation = coordinated.expectation()
            immediate = confirm_workflow_expectation_under_lease(
                config.coordinator,
                lease,
                protected_expectation,
            )
            if immediate.status is CoordinationStatus.STALE_PLAN:
                result = _from_coordination(
                    SyntheticExecutionStatus.STALE_PLAN, immediate
                )
            elif immediate.status is not CoordinationStatus.ACTION_REQUIRED:
                result = _from_coordination(
                    SyntheticExecutionStatus.VALIDATION_FAILED, immediate
                )
            else:
                lease.validate()
                _validate_operation_precondition(config, operation)
                target, result_sha256 = _write_synthetic_file(operation)
                lease.validate()
                result = SyntheticExecutionResult(
                    SyntheticExecutionStatus.COMPLETED,
                    "synthetic operation completed inside the protected lease",
                    plan=immediate.plan,
                    workflow_precondition_sha256=immediate.precondition_sha256,
                    evidence=SyntheticExecutionEvidence(
                        target=target,
                        plan=immediate.plan,
                        workflow_precondition_sha256=immediate.precondition_sha256,
                        expected_preimage_sha256=operation.expected_preimage_sha256,
                        result_sha256=result_sha256,
                        bytes_written=len(operation.payload),
                        lease_owner=owner,
                    ),
                    lease_owner=owner,
                )
    except SyntheticOperationRejected as error:
        result = SyntheticExecutionResult(
            SyntheticExecutionStatus.OPERATION_REJECTED,
            str(error),
            lease_owner=owner,
        )
    except (OSError, WorkflowLeaseError) as error:
        result = SyntheticExecutionResult(
            SyntheticExecutionStatus.EXECUTION_ERROR,
            "synthetic execution failed; the target may have been created or "
            f"partially written: {error}",
            lease_owner=owner,
        )
    finally:
        try:
            lease.release()
        except WorkflowLeaseError as error:
            result = SyntheticExecutionResult(
                SyntheticExecutionStatus.EXECUTION_ERROR,
                f"synthetic execution could not release the workflow lease safely: {error}",
                plan=None if result is None else result.plan,
                workflow_precondition_sha256=(
                    None if result is None else result.workflow_precondition_sha256
                ),
                evidence=None if result is None else result.evidence,
                lease_owner=owner,
            )
    assert result is not None
    return result


def _from_coordination(status: SyntheticExecutionStatus, coordinated) -> SyntheticExecutionResult:
    return SyntheticExecutionResult(
        status,
        coordinated.reason,
        plan=coordinated.plan,
        workflow_precondition_sha256=coordinated.precondition_sha256,
        lease_owner=coordinated.lease_owner,
    )


def _validate_operation_precondition(
    config: SyntheticExecutorConfig,
    operation: SyntheticWriteOperation,
) -> None:
    _validate_operation_shape(config, operation)
    root = config.synthetic_root
    parent = root.joinpath(*operation.relative_path.parts[:-1]) if len(
        operation.relative_path.parts
    ) > 1 else root
    target = root / operation.relative_path
    _require_plain_directory(root, "synthetic_root")
    _require_plain_directory(parent, "synthetic destination parent")
    if _identity(root.lstat()) != operation.root_identity:
        raise SyntheticOperationRejected("synthetic root identity changed")
    if _identity(parent.lstat()) != operation.parent_identity:
        raise SyntheticOperationRejected("synthetic destination parent identity changed")
    if target.is_symlink() or target.exists():
        raise SyntheticOperationRejected("synthetic destination preimage changed")


def _validate_operation_shape(
    config: SyntheticExecutorConfig,
    operation: SyntheticWriteOperation,
) -> None:
    if not isinstance(operation.root, Path):
        raise TypeError("synthetic operation root must be a Path")
    if not isinstance(operation.relative_path, Path):
        raise TypeError("synthetic relative_path must be a Path")
    if operation.root != config.synthetic_root:
        raise SyntheticOperationRejected(
            "operation root does not match the authorized synthetic root"
        )
    relative = Path(operation.relative_path)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise SyntheticOperationRejected("synthetic destination must be a confined relative path")
    if not isinstance(operation.payload, bytes):
        raise TypeError("synthetic payload must be bytes")
    _validate_identity_shape(operation.root_identity, "synthetic root_identity")
    _validate_identity_shape(operation.parent_identity, "synthetic parent_identity")
    if operation.expected_preimage_sha256 is not None:
        raise SyntheticOperationRejected(
            "synthetic create operation requires an absent preimage and no preimage hash"
        )
    parent = config.synthetic_root.joinpath(*relative.parts[:-1]) if len(
        relative.parts
    ) > 1 else config.synthetic_root
    if not _is_relative_to(parent, config.synthetic_root):
        raise SyntheticOperationRejected("synthetic destination escapes its authorized root")


def _write_synthetic_file(operation: SyntheticWriteOperation) -> tuple[Path, str]:
    root = operation.root
    parent = root.joinpath(*operation.relative_path.parts[:-1]) if len(
        operation.relative_path.parts
    ) > 1 else root
    target = root / operation.relative_path
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(parent, directory_flags)
    descriptor: Optional[int] = None
    try:
        if _identity(os.fstat(parent_fd)) != operation.parent_identity:
            raise SyntheticOperationRejected("synthetic destination parent identity changed")
        file_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        file_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(operation.relative_path.name, file_flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as error:
            raise SyntheticOperationRejected("synthetic destination preimage changed") from error
        _write_all(descriptor, operation.payload)
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != len(operation.payload):
            raise OSError("synthetic result is not the expected regular file")
        observed_sha256, observed_size = _read_descriptor_digest(
            descriptor, len(operation.payload)
        )
        verified = os.fstat(descriptor)
        if _identity(verified) != _identity(opened) or verified.st_size != observed_size:
            raise OSError("synthetic result identity or size changed during verification")
        current = target.lstat()
        if _identity(current) != _identity(opened):
            raise OSError("synthetic result identity changed after creation")
        if _identity(root.lstat()) != operation.root_identity:
            raise OSError("synthetic root identity changed during execution")
        if _identity(parent.lstat()) != operation.parent_identity:
            raise OSError("synthetic destination parent identity changed during execution")
        requested_sha256 = hashlib.sha256(operation.payload).hexdigest()
        if observed_sha256 != requested_sha256:
            raise OSError("synthetic result content differs from the requested payload")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)

    return target, observed_sha256


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    view = memoryview(payload)
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("synthetic write made no progress")
        written += count


def _read_descriptor_digest(descriptor: int, expected_size: int) -> tuple[str, int]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    observed_size = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        observed_size += len(chunk)
    if observed_size != expected_size:
        raise OSError(
            "synthetic result verification was incomplete: "
            f"expected {expected_size} bytes, observed {observed_size}"
        )
    return digest.hexdigest(), observed_size


def _validate_identity_shape(value: object, label: str) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or any(
            not isinstance(component, int) or isinstance(component, bool) or component < 0
            for component in value
        )
    ):
        raise TypeError(f"{label} must be a three-integer identity tuple")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


def _require_plain_directory(path: Path, label: str) -> None:
    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            inspected = current.lstat()
        except FileNotFoundError as error:
            raise SyntheticOperationRejected(f"{label} does not exist: {path}") from error
        if stat.S_ISLNK(inspected.st_mode):
            raise SyntheticOperationRejected(f"{label} must not contain symlinks: {current}")
    if not stat.S_ISDIR(absolute.lstat().st_mode):
        raise SyntheticOperationRejected(f"{label} must be a directory: {path}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_relative_to(left, right) or _is_relative_to(right, left)


def _operational_paths(config: WorkflowCoordinatorConfig) -> tuple[Path, ...]:
    workflow = config.workflow
    return tuple(
        _absolute(path)
        for path in (
            workflow.statements_path,
            workflow.runs_root,
            workflow.review_cases_root,
            workflow.feedback_root,
            workflow.profiles_root,
        )
    )


def _workflow_container(config: WorkflowCoordinatorConfig) -> Path:
    workflow = config.workflow
    paths = (
        _absolute(workflow.statements_path).parent,
        _absolute(workflow.runs_root),
        _absolute(workflow.review_cases_root),
        _absolute(workflow.feedback_root),
        _absolute(workflow.profiles_root),
    )
    return Path(os.path.commonpath(tuple(os.fspath(path) for path in paths)))


__all__ = [
    "SyntheticExecutionEvidence",
    "SyntheticExecutionResult",
    "SyntheticExecutionStatus",
    "SyntheticExecutorConfig",
    "SyntheticOperationRejected",
    "SyntheticWriteOperation",
    "execute_synthetic_write",
    "prepare_synthetic_write",
]
