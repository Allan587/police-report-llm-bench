"""Acquire a WorkflowLease in a subprocess for multiprocess tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from automation.workflow_lease import WorkflowLease


def main() -> None:
    lease = WorkflowLease(Path(sys.argv[1]), sys.argv[2])
    lease.acquire()
    mode = sys.argv[3] if len(sys.argv) > 3 else "hold"
    if mode == "fork-parent-exits":
        child_pid = os.fork()
        if child_pid == 0:
            print(f"fork-child:{os.getpid()}:{lease.is_acquired}", flush=True)
            sys.stdin.readline()
            os._exit(0)
        print(f"fork-parent:{child_pid}", flush=True)
        os._exit(0)
    print(f"acquired:{os.getpid()}", flush=True)
    command = sys.stdin.readline().strip()
    if command == "release":
        lease.release()


if __name__ == "__main__":
    main()
