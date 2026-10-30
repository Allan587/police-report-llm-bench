"""Process-scoped exclusive lease for persistent workflow mutations.

On POSIX systems the effective rendezvous point is the private, stable parent
directory, not the diagnostic JSON file inside it. Locking the directory inode
means deleting or replacing the metadata file cannot create a second cooperative
owner. A process able to replace the directory itself remains outside this
cooperative trust boundary and must be prevented operationally.
"""

from __future__ import annotations

import errno
import json
import os
import re
import socket
import stat
import threading
import weakref
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows-only branch
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX-only branch
    msvcrt = None


_OWNER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ACTIVE_INSTANCES: weakref.WeakSet[WorkflowLease] = weakref.WeakSet()


@dataclass(frozen=True)
class LeaseOwner:
    """Non-secret diagnostic identity written while a lease is held."""

    owner_id: str
    pid: int
    hostname: str
    acquired_at: str
    token: str


class WorkflowLeaseError(RuntimeError):
    """Base class for explicit workflow lease failures."""


class WorkflowLeaseInUseError(WorkflowLeaseError):
    """Raised when another process or lease instance owns the lock."""

    def __init__(self, lock_path: Path, owner: Optional[LeaseOwner]) -> None:
        self.lock_path = lock_path
        self.owner = owner
        detail = f" by owner '{owner.owner_id}' (pid {owner.pid})" if owner else ""
        super().__init__(f"workflow lease is already held{detail}: {lock_path}")


class WorkflowLeaseAcquisitionError(WorkflowLeaseError):
    """Raised when the lock cannot be opened, created, or acquired."""


class WorkflowLeaseIntegrityError(WorkflowLeaseError):
    """Raised when the locked rendezvous directory is no longer at its path."""


class WorkflowLeaseReleaseError(WorkflowLeaseError):
    """Raised when an acquired lock cannot be released safely."""


class WorkflowLeaseOwnershipError(WorkflowLeaseReleaseError):
    """Raised when a caller that does not own this lease tries to release it."""


class WorkflowLease:
    """Own one global workflow lock until explicit or context-manager release.

    The lock path must live directly inside a dedicated, stable directory owned
    by the current POSIX user and inaccessible to group/other users. Cooperative
    processes may replace the JSON metadata file without bypassing the directory
    lock. Replacing the directory itself is outside the trust model.
    """

    def __init__(self, lock_path: Path, owner_id: str) -> None:
        if not _OWNER_ID.fullmatch(owner_id):
            raise ValueError(
                "owner_id must start with a letter or number, contain only "
                "letters, numbers, '_', '-' or '.', and be at most 128 characters"
            )

        expanded_path = Path(lock_path).expanduser()
        self.lock_path = Path(os.path.abspath(os.fspath(expanded_path)))
        self.owner_id = owner_id
        self._file: Optional[IO[str]] = None
        self._lock_fd: Optional[int] = None
        self._lock_fd_is_separate = False
        self._anchor_path: Optional[Path] = None
        self._anchor_identity: Optional[tuple[int, int]] = None
        self._descriptor_identity: Optional[tuple[int, int]] = None
        self._owner: Optional[LeaseOwner] = None
        self._state_lock = threading.Lock()
        _ACTIVE_INSTANCES.add(self)

    @property
    def is_acquired(self) -> bool:
        """Whether this process still owns a valid lock at the stable path."""

        if self._lock_fd is None or self._owner is None:
            return False
        if self._owner.pid != os.getpid():
            return False
        return self._descriptor_and_anchor_are_valid()

    @property
    def owner(self) -> Optional[LeaseOwner]:
        """Return this instance's diagnostic owner metadata."""

        return self._owner

    def acquire(self, *, blocking: bool = False) -> LeaseOwner:
        """Acquire the lease atomically or raise an explicit acquisition error."""

        with self._state_lock:
            if self._lock_fd is not None:
                raise WorkflowLeaseInUseError(self.lock_path, self._owner)

            lock_fd: Optional[int] = None
            lock_fd_is_separate = False
            metadata_file: Optional[IO[str]] = None
            metadata_path: Optional[Path] = None
            locked = False
            lock_attempted = False
            completed = False
            try:
                anchor_path = self._prepare_anchor_directory()
                metadata_path = anchor_path / self.lock_path.name
                self._reject_metadata_symlink(metadata_path)

                if fcntl is not None:
                    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    flags |= getattr(os, "O_CLOEXEC", 0)
                    flags |= getattr(os, "O_NOFOLLOW", 0)
                    lock_fd = os.open(anchor_path, flags)
                    os.set_inheritable(lock_fd, False)
                    lock_fd_is_separate = True
                    lock_attempted = True
                    self._lock_file(lock_fd, blocking=blocking)
                    locked = True
                    anchor_identity = self._verify_anchor_identity(anchor_path, lock_fd)
                    descriptor_identity = anchor_identity
                    metadata_file = self._open_metadata_file(metadata_path)
                elif msvcrt is not None:  # pragma: no cover - Windows-only branch
                    metadata_file = self._open_metadata_file(metadata_path)
                    lock_fd = metadata_file.fileno()
                    lock_attempted = True
                    self._lock_file(lock_fd, blocking=blocking)
                    locked = True
                    anchor_stat = anchor_path.stat()
                    anchor_identity = (anchor_stat.st_dev, anchor_stat.st_ino)
                    descriptor_stat = os.fstat(lock_fd)
                    descriptor_identity = (
                        descriptor_stat.st_dev,
                        descriptor_stat.st_ino,
                    )
                else:
                    raise WorkflowLeaseAcquisitionError(
                        "no supported operating-system file-locking backend is available"
                    )

                owner = LeaseOwner(
                    owner_id=self.owner_id,
                    pid=os.getpid(),
                    hostname=socket.gethostname(),
                    acquired_at=datetime.now(timezone.utc).isoformat(),
                    token=uuid4().hex,
                )
                self._write_owner(metadata_file, owner)
                self._verify_metadata_identity(metadata_path, metadata_file)
                completed = True
            except WorkflowLeaseError:
                raise
            except OSError as error:
                if lock_attempted and not locked and self._is_contention(error):
                    observed = self._read_owner_path(metadata_path)
                    raise WorkflowLeaseInUseError(self.lock_path, observed) from error
                raise WorkflowLeaseAcquisitionError(
                    f"could not acquire workflow lease at {self.lock_path}: {error}"
                ) from error
            finally:
                if not completed:
                    if locked and lock_fd is not None:
                        try:
                            self._unlock_file(lock_fd)
                        except OSError:
                            pass
                    self._close_acquisition_handles(
                        metadata_file, lock_fd, lock_fd_is_separate
                    )

            self._file = metadata_file
            self._lock_fd = lock_fd
            self._lock_fd_is_separate = lock_fd_is_separate
            self._anchor_path = anchor_path
            self._anchor_identity = anchor_identity
            self._descriptor_identity = descriptor_identity
            self._owner = owner
            return owner

    def validate(self) -> LeaseOwner:
        """Confirm that this process owns the descriptor and stable anchor path."""

        with self._state_lock:
            if self._lock_fd is None or self._owner is None:
                raise WorkflowLeaseOwnershipError(
                    f"this lease instance does not own {self.lock_path}"
                )
            if self._owner.pid != os.getpid():
                raise WorkflowLeaseOwnershipError(
                    f"lease ownership belongs to another process: {self.lock_path}"
                )
            if not self._descriptor_is_valid():
                raise WorkflowLeaseIntegrityError(
                    f"workflow lease descriptor is invalid: {self.lock_path}"
                )
            if not self._anchor_is_current():
                raise WorkflowLeaseIntegrityError(
                    f"workflow lease directory was replaced: {self._anchor_path}"
                )
            return self._owner

    def release(self) -> None:
        """Release this instance's lease; reject calls without ownership."""

        with self._state_lock:
            if self._lock_fd is None or self._owner is None:
                raise WorkflowLeaseOwnershipError(
                    f"this lease instance does not own {self.lock_path}"
                )
            if self._owner.pid != os.getpid():
                raise WorkflowLeaseOwnershipError(
                    f"lease ownership belongs to another process: {self.lock_path}"
                )

            lock_fd = self._lock_fd
            metadata_file = self._file
            lock_fd_is_separate = self._lock_fd_is_separate
            descriptor_valid = self._descriptor_is_valid()
            anchor_current = descriptor_valid and self._anchor_is_current()
            error: Optional[BaseException] = None

            if descriptor_valid:
                try:
                    self._unlock_file(lock_fd)
                except OSError as caught:
                    error = caught
            else:
                error = OSError(errno.EBADF, "workflow lease descriptor is closed or invalid")

            if metadata_file is not None:
                try:
                    metadata_file.close()
                except OSError as caught:
                    error = error or caught
            if lock_fd_is_separate and descriptor_valid:
                try:
                    os.close(lock_fd)
                except OSError as caught:
                    error = error or caught

            self._clear_state()

            if not anchor_current and descriptor_valid:
                error = error or OSError(
                    errno.ESTALE, "workflow lease directory was replaced"
                )
            if error is not None:
                raise WorkflowLeaseReleaseError(
                    f"could not release workflow lease at {self.lock_path}: {error}"
                ) from error

    def __enter__(self) -> "WorkflowLease":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def _prepare_anchor_directory(self) -> Path:
        requested_parent = self.lock_path.parent
        requested_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if requested_parent.is_symlink():
            raise WorkflowLeaseAcquisitionError(
                f"workflow lock directory must not be a symbolic link: {requested_parent}"
            )
        anchor_path = requested_parent.resolve(strict=True)
        anchor_stat = anchor_path.stat()
        if not stat.S_ISDIR(anchor_stat.st_mode):
            raise WorkflowLeaseAcquisitionError(
                f"workflow lock parent is not a directory: {anchor_path}"
            )
        if fcntl is not None:
            if anchor_stat.st_uid != os.geteuid():
                raise WorkflowLeaseAcquisitionError(
                    f"workflow lock directory is not owned by the current user: {anchor_path}"
                )
            if stat.S_IMODE(anchor_stat.st_mode) & 0o077:
                raise WorkflowLeaseAcquisitionError(
                    f"workflow lock directory must be private (mode 0700 or stricter): {anchor_path}"
                )
        return anchor_path

    @staticmethod
    def _reject_metadata_symlink(metadata_path: Path) -> None:
        if metadata_path.is_symlink():
            raise WorkflowLeaseAcquisitionError(
                f"workflow lock path must not be a symbolic link: {metadata_path}"
            )

    @staticmethod
    def _open_metadata_file(metadata_path: Path) -> IO[str]:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(metadata_path, flags, 0o600)
        try:
            os.set_inheritable(descriptor, False)
            os.fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, "r+", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _verify_anchor_identity(anchor_path: Path, lock_fd: int) -> tuple[int, int]:
        descriptor_stat = os.fstat(lock_fd)
        path_stat = os.stat(anchor_path, follow_symlinks=False)
        descriptor_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
        if descriptor_identity != (path_stat.st_dev, path_stat.st_ino):
            raise WorkflowLeaseAcquisitionError(
                f"workflow lock directory changed during acquisition: {anchor_path}"
            )
        return descriptor_identity

    @staticmethod
    def _verify_metadata_identity(metadata_path: Path, metadata_file: IO[str]) -> None:
        descriptor_stat = os.fstat(metadata_file.fileno())
        path_stat = os.stat(metadata_path, follow_symlinks=False)
        if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
            path_stat.st_dev,
            path_stat.st_ino,
        ):
            raise WorkflowLeaseAcquisitionError(
                f"workflow lock metadata changed during acquisition: {metadata_path}"
            )

    def _descriptor_is_valid(self) -> bool:
        if self._lock_fd is None or self._descriptor_identity is None:
            return False
        try:
            descriptor_stat = os.fstat(self._lock_fd)
        except OSError:
            return False
        return (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        ) == self._descriptor_identity

    def _anchor_is_current(self) -> bool:
        if self._anchor_path is None or self._anchor_identity is None:
            return False
        try:
            path_stat = os.stat(self._anchor_path, follow_symlinks=False)
        except OSError:
            return False
        return (path_stat.st_dev, path_stat.st_ino) == self._anchor_identity

    def _descriptor_and_anchor_are_valid(self) -> bool:
        return self._descriptor_is_valid() and self._anchor_is_current()

    @staticmethod
    def _lock_file(lock_fd: int, *, blocking: bool) -> None:
        if fcntl is not None:
            operation = fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            fcntl.flock(lock_fd, operation)
            return
        if msvcrt is not None:  # pragma: no cover - Windows-only branch
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            os.lseek(lock_fd, 0, os.SEEK_SET)
            if os.fstat(lock_fd).st_size == 0:
                os.write(lock_fd, b"\n")
                os.lseek(lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(lock_fd, mode, 1)
            return
        raise WorkflowLeaseAcquisitionError(
            "no supported operating-system file-locking backend is available"
        )

    @staticmethod
    def _unlock_file(lock_fd: int) -> None:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            return
        if msvcrt is not None:  # pragma: no cover - Windows-only branch
            os.lseek(lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            return
        raise OSError(errno.ENOSYS, "no supported file-locking backend")

    @staticmethod
    def _write_owner(metadata_file: IO[str], owner: LeaseOwner) -> None:
        metadata_file.seek(0)
        json.dump(asdict(owner), metadata_file, sort_keys=True, separators=(",", ":"))
        metadata_file.write("\n")
        metadata_file.truncate()
        metadata_file.flush()
        os.fsync(metadata_file.fileno())

    @staticmethod
    def _read_owner(metadata_file: Optional[IO[str]]) -> Optional[LeaseOwner]:
        if metadata_file is None:
            return None
        try:
            metadata_file.seek(0)
            value = json.load(metadata_file)
            return LeaseOwner(
                owner_id=value["owner_id"],
                pid=value["pid"],
                hostname=value["hostname"],
                acquired_at=value["acquired_at"],
                token=value["token"],
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @classmethod
    def _read_owner_path(cls, metadata_path: Optional[Path]) -> Optional[LeaseOwner]:
        if metadata_path is None:
            return None
        try:
            with metadata_path.open("r", encoding="utf-8") as metadata_file:
                return cls._read_owner(metadata_file)
        except OSError:
            return None

    @staticmethod
    def _close_acquisition_handles(
        metadata_file: Optional[IO[str]],
        lock_fd: Optional[int],
        lock_fd_is_separate: bool,
    ) -> None:
        if metadata_file is not None:
            try:
                metadata_file.close()
            except OSError:
                pass
        if lock_fd_is_separate and lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass

    def _clear_state(self) -> None:
        self._file = None
        self._lock_fd = None
        self._lock_fd_is_separate = False
        self._anchor_path = None
        self._anchor_identity = None
        self._descriptor_identity = None
        self._owner = None

    def _after_fork_child(self) -> None:
        metadata_file = self._file
        lock_fd = self._lock_fd
        lock_fd_is_separate = self._lock_fd_is_separate
        if metadata_file is not None:
            try:
                metadata_file.close()
            except OSError:
                pass
        if lock_fd_is_separate and lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        self._clear_state()
        self._state_lock = threading.Lock()

    @staticmethod
    def _is_contention(error: OSError) -> bool:
        return error.errno in {errno.EACCES, errno.EAGAIN}


def _drop_inherited_leases_after_fork() -> None:
    for lease in tuple(_ACTIVE_INSTANCES):
        lease._after_fork_child()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_drop_inherited_leases_after_fork)


__all__ = [
    "LeaseOwner",
    "WorkflowLease",
    "WorkflowLeaseAcquisitionError",
    "WorkflowLeaseError",
    "WorkflowLeaseInUseError",
    "WorkflowLeaseIntegrityError",
    "WorkflowLeaseOwnershipError",
    "WorkflowLeaseReleaseError",
]
