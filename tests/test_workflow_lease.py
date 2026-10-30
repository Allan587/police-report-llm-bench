from __future__ import annotations

import gc
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from automation.workflow_lease import (
    WorkflowLease,
    WorkflowLeaseAcquisitionError,
    WorkflowLeaseIntegrityError,
    WorkflowLeaseInUseError,
    WorkflowLeaseOwnershipError,
    WorkflowLeaseReleaseError,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{_REPOSITORY_ROOT}{os.pathsep}{existing}" if existing else str(_REPOSITORY_ROOT)
    )
    return environment


def _holder(lock_path: Path, owner_id: str = "child-owner") -> subprocess.Popen[str]:
    helper = Path(__file__).parent / "helpers" / "workflow_lease_holder.py"
    process = subprocess.Popen(
        [sys.executable, str(helper), str(lock_path), owner_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_subprocess_environment(),
    )
    assert process.stdout is not None
    ready = process.stdout.readline().strip()
    if not ready.startswith("acquired:"):
        stderr = process.stderr.read() if process.stderr is not None else ""
        process.kill()
        process.wait()
        pytest.fail(f"lease helper failed: {ready!r} {stderr}")
    return process


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    assert process.stdin is not None
    process.stdin.write("release\n")
    process.stdin.flush()
    process.wait(timeout=5)


def _sleep_in_spawned_process(seconds: float) -> None:
    time.sleep(seconds)


def test_acquire_release_and_diagnostic_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / "workflow.lock"
    lease = WorkflowLease(lock_path, "run-001")

    owner = lease.acquire()

    assert lease.is_acquired
    assert lease.owner == owner
    assert owner.owner_id == "run-001"
    assert owner.pid == os.getpid()
    assert json.loads(lock_path.read_text(encoding="utf-8"))["token"] == owner.token

    lease.release()
    assert not lease.is_acquired
    assert lease.owner is None
    assert lock_path.exists()  # Presence is not ownership.


def test_real_process_exclusion_and_nonblocking_failure(tmp_path: Path) -> None:
    lock_path = tmp_path / "workflow.lock"
    process = _holder(lock_path)
    try:
        contender = WorkflowLease(lock_path, "parent-contender")
        with pytest.raises(WorkflowLeaseInUseError) as caught:
            contender.acquire(blocking=False)

        assert caught.value.owner is not None
        assert caught.value.owner.owner_id == "child-owner"
        assert not contender.is_acquired
    finally:
        _stop(process)

    replacement = WorkflowLease(lock_path, "parent-after-release")
    assert replacement.acquire().owner_id == "parent-after-release"
    replacement.release()


def test_simultaneous_independent_process_attempts_have_one_winner(tmp_path: Path) -> None:
    lock_path = tmp_path / "workflow.lock"
    helper = Path(__file__).parent / "helpers" / "workflow_lease_holder.py"

    def start(owner_id: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(helper), str(lock_path), owner_id],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_environment(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        processes = list(pool.map(start, ("simultaneous-a", "simultaneous-b")))

    lines = []
    for process in processes:
        assert process.stdout is not None
        lines.append(process.stdout.readline().strip())

    acquired = [process for process, line in zip(processes, lines) if line.startswith("acquired:")]
    failed = [process for process, line in zip(processes, lines) if not line.startswith("acquired:")]
    assert len(acquired) == 1
    assert len(failed) == 1
    assert failed[0].wait(timeout=5) != 0
    _stop(acquired[0])


def test_owner_process_termination_releases_kernel_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "workflow.lock"
    process = _holder(lock_path, "terminated-owner")
    process.kill()
    process.wait(timeout=5)

    replacement = WorkflowLease(lock_path, "replacement-owner")
    replacement.acquire()
    replacement.release()


def test_context_manager_releases_after_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "workflow.lock"
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with WorkflowLease(lock_path, "exception-owner"):
            raise RuntimeError("synthetic failure")

    with WorkflowLease(lock_path, "next-owner") as next_lease:
        assert next_lease.is_acquired


def test_non_owner_cannot_release_active_lease(tmp_path: Path) -> None:
    lock_path = tmp_path / "workflow.lock"
    owner = WorkflowLease(lock_path, "actual-owner")
    outsider = WorkflowLease(lock_path, "outsider")
    owner.acquire()
    try:
        with pytest.raises(WorkflowLeaseOwnershipError):
            outsider.release()
        with pytest.raises(WorkflowLeaseInUseError):
            outsider.acquire()
        assert owner.is_acquired
    finally:
        owner.release()


def test_same_owner_id_does_not_share_descriptor_ownership(tmp_path: Path) -> None:
    lock_path = tmp_path / "private-runtime" / "workflow.lock"
    first = WorkflowLease(lock_path, "same-owner")
    second = WorkflowLease(lock_path, "same-owner")
    first.acquire()
    try:
        with pytest.raises(WorkflowLeaseInUseError):
            second.acquire()
        with pytest.raises(WorkflowLeaseOwnershipError):
            second.release()
    finally:
        first.release()


@pytest.mark.parametrize("operation", ["unlink", "replace"])
def test_metadata_path_change_does_not_create_second_process_owner(
    tmp_path: Path, operation: str
) -> None:
    lock_path = tmp_path / "private-runtime" / "workflow.lock"
    process = _holder(lock_path, "original-owner")
    try:
        if operation == "unlink":
            lock_path.unlink()
        else:
            replacement = tmp_path / "replacement.lock"
            replacement.write_text("replacement", encoding="utf-8")
            os.replace(replacement, lock_path)

        contender = WorkflowLease(lock_path, "contender")
        with pytest.raises(WorkflowLeaseInUseError):
            contender.acquire(blocking=False)
    finally:
        _stop(process)

    with WorkflowLease(lock_path, "after-release"):
        pass


def test_lock_directory_is_private(tmp_path: Path) -> None:
    lock_path = tmp_path / "private-runtime" / "workflow.lock"
    with WorkflowLease(lock_path, "private-directory"):
        mode = stat.S_IMODE(lock_path.parent.stat().st_mode)
        assert mode & 0o077 == 0


def test_non_private_existing_lock_directory_is_rejected(tmp_path: Path) -> None:
    shared = tmp_path / "shared-runtime"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)

    with pytest.raises(WorkflowLeaseAcquisitionError, match="private"):
        WorkflowLease(shared / "workflow.lock", "unsafe-directory").acquire()


def test_replaced_anchor_directory_invalidates_lease_state(tmp_path: Path) -> None:
    runtime = tmp_path / "private-runtime"
    lock_path = runtime / "workflow.lock"
    lease = WorkflowLease(lock_path, "anchor-owner")
    lease.acquire()

    moved = tmp_path / "moved-runtime"
    runtime.rename(moved)
    runtime.mkdir(mode=0o700)

    assert not lease.is_acquired
    with pytest.raises(WorkflowLeaseIntegrityError, match="directory was replaced"):
        lease.validate()
    with pytest.raises(WorkflowLeaseReleaseError, match="directory was replaced"):
        lease.release()
    assert lease.owner is None
    assert not lease.is_acquired


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is unavailable")
def test_fork_child_does_not_inherit_lease_ownership(tmp_path: Path) -> None:
    lock_path = tmp_path / "private-runtime" / "workflow.lock"
    helper = Path(__file__).parent / "helpers" / "workflow_lease_holder.py"
    process = subprocess.Popen(
        [
            sys.executable,
            str(helper),
            str(lock_path),
            "fork-parent",
            "fork-parent-exits",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_subprocess_environment(),
    )
    assert process.stdout is not None
    lines = {process.stdout.readline().strip(), process.stdout.readline().strip()}
    parent_line = next(line for line in lines if line.startswith("fork-parent:"))
    child_line = next(line for line in lines if line.startswith("fork-child:"))
    child_pid = int(parent_line.split(":", 1)[1])
    assert child_line == f"fork-child:{child_pid}:False"
    assert process.wait(timeout=5) == 0

    try:
        with WorkflowLease(lock_path, "replacement-while-child-alive"):
            os.kill(child_pid, 0)
    finally:
        assert process.stdin is not None
        process.stdin.write("exit\n")
        process.stdin.flush()


def test_spawn_and_exec_children_do_not_extend_parent_lease(tmp_path: Path) -> None:
    lock_path = tmp_path / "private-runtime" / "workflow.lock"
    owner = WorkflowLease(lock_path, "parent")
    owner.acquire()

    spawn_context = multiprocessing.get_context("spawn")
    spawned = spawn_context.Process(target=_sleep_in_spawned_process, args=(1.0,))
    spawned.start()
    executed = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    owner.release()
    try:
        with WorkflowLease(lock_path, "replacement"):
            assert spawned.is_alive()
            assert executed.poll() is None
    finally:
        spawned.join(timeout=5)
        executed.wait(timeout=5)


def test_closed_lock_descriptor_clears_state_and_allows_recovery(tmp_path: Path) -> None:
    lock_path = tmp_path / "private-runtime" / "workflow.lock"
    lease = WorkflowLease(lock_path, "closed-descriptor")
    lease.acquire()
    os.close(lease._lock_fd)

    with pytest.raises(WorkflowLeaseReleaseError, match="descriptor"):
        lease.release()

    assert not lease.is_acquired
    assert lease.owner is None
    with WorkflowLease(lock_path, "replacement"):
        pass


def test_many_cycles_do_not_leak_descriptors(tmp_path: Path) -> None:
    gc.collect()
    before = len(os.listdir("/dev/fd"))
    lease = WorkflowLease(tmp_path / "private-runtime" / "workflow.lock", "cycles")
    for _ in range(1_000):
        lease.acquire()
        lease.release()
    gc.collect()
    assert len(os.listdir("/dev/fd")) <= before


def test_release_backend_error_is_reported_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = WorkflowLease(tmp_path / "workflow.lock", "release-error-owner")
    lease.acquire()

    def fail_unlock(lock_file: object) -> None:
        raise OSError("synthetic unlock failure")

    monkeypatch.setattr(lease, "_unlock_file", fail_unlock)
    with pytest.raises(WorkflowLeaseReleaseError, match="could not release"):
        lease.release()
    assert not lease.is_acquired


def test_browser_profile_locks_are_independent(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    from automation.browser.manager import BrowserManager

    managers = [
        BrowserManager(tmp_path / "profiles", provider)
        for provider in ("chatgpt", "gemini", "grok")
    ]
    for manager in managers:
        manager._acquire_profile_lock()
    try:
        with WorkflowLease(tmp_path / "runtime" / "workflow.lock", "workflow-owner"):
            assert all(manager._lock_file is not None for manager in managers)
    finally:
        for manager in managers:
            manager._release_profile_lock()


def test_repeated_acquire_release_cycles(tmp_path: Path) -> None:
    lease = WorkflowLease(tmp_path / "workflow.lock", "repeat-owner")
    for _ in range(5):
        lease.acquire()
        assert lease.is_acquired
        lease.release()
        assert not lease.is_acquired


def test_invalid_lock_path_is_reported_explicitly(tmp_path: Path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("sentinel", encoding="utf-8")
    lease = WorkflowLease(parent_file / "workflow.lock", "invalid-path")

    with pytest.raises(WorkflowLeaseAcquisitionError, match="could not acquire"):
        lease.acquire()


def test_symbolic_link_lock_path_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.lock"
    target.write_text("sentinel", encoding="utf-8")
    link = tmp_path / "workflow.lock"
    link.symlink_to(target)

    with pytest.raises(WorkflowLeaseAcquisitionError, match="symbolic link"):
        WorkflowLease(link, "symlink-test").acquire()

    assert target.read_text(encoding="utf-8") == "sentinel"


def test_permission_error_is_reported_explicitly(tmp_path: Path) -> None:
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    forbidden.chmod(0)
    try:
        lease = WorkflowLease(forbidden / "workflow.lock", "permission-test")
        with pytest.raises(WorkflowLeaseAcquisitionError, match="could not acquire"):
            lease.acquire()
    finally:
        forbidden.chmod(0o700)


def test_lease_does_not_modify_operational_data(tmp_path: Path) -> None:
    operational = tmp_path / "operational"
    files = {
        operational / "data" / "evidence.json": b'{"stable":true}\n',
        operational / "config" / "profiles" / "gemma.json": b'{"version":1}\n',
        operational / "config" / "feedback" / "gemma.jsonl": b'{"output":1}\n',
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    with WorkflowLease(tmp_path / "runtime" / "workflow.lock", "isolated-owner"):
        pass

    assert {path: path.read_bytes() for path in files} == files


def test_owner_id_is_sanitized(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="owner_id"):
        WorkflowLease(tmp_path / "workflow.lock", "unsafe owner/id")
