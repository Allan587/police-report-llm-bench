"""Hold a BrowserManager profile lock for the parent integration test."""

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from automation.browser.manager import BrowserManager


def main() -> None:
    manager = BrowserManager(Path(sys.argv[1]), sys.argv[2], headless=True)
    manager._acquire_profile_lock()
    try:
        print("LOCKED", flush=True)
        sys.stdin.readline()
    finally:
        manager._release_profile_lock()


if __name__ == "__main__":
    main()
