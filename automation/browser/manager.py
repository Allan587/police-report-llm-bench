"""Async Playwright lifecycle management for one persistent browser profile.

This module only manages Chromium and local profile ownership. It does not log
in, submit prompts, read responses, or interact with third-party services.
"""

from __future__ import annotations

import asyncio
import errno
import re
from pathlib import Path
from typing import IO, Optional

from playwright.async_api import BrowserContext, Playwright, async_playwright

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX only
    msvcrt = None


_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class BrowserManagerError(RuntimeError):
    """Base error for browser lifecycle failures."""


class ProfileInUseError(BrowserManagerError):
    """Raised when another manager owns the requested profile directory."""


class BrowserLaunchError(BrowserManagerError):
    """Raised when Playwright cannot launch a persistent Chromium context."""


class BrowserManager:
    """Own one persistent Chromium context and its exclusive profile lock."""

    def __init__(
        self,
        profile_root: Path,
        profile_name: str,
        *,
        headless: bool = False,
        launch_timeout_ms: int = 30_000,
    ) -> None:
        if not _PROFILE_NAME.fullmatch(profile_name):
            raise ValueError(
                "profile_name must contain only letters, numbers, '_' or '-', "
                "start with a letter or number, and be at most 64 characters"
            )
        if launch_timeout_ms <= 0:
            raise ValueError("launch_timeout_ms must be greater than zero")

        self.profile_root = Path(profile_root).expanduser().resolve()
        self.profile_name = profile_name
        self.headless = headless
        self.launch_timeout_ms = launch_timeout_ms
        self.profile_dir = self.profile_root / profile_name

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._lock_file: Optional[IO[str]] = None
        self._unexpected_cleanup_task: Optional[asyncio.Task[None]] = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def context(self) -> BrowserContext:
        """Return the active context or fail with an actionable message."""
        if self._context is None:
            raise BrowserManagerError(
                f"Browser profile '{self.profile_name}' is not open. "
                "Call await manager.open() first."
            )
        return self._context

    @property
    def is_open(self) -> bool:
        return self._context is not None

    async def open(self) -> BrowserContext:
        """Launch Chromium with the persistent profile, once."""
        async with self._lifecycle_lock:
            if self.is_open:
                return self.context

            await self._wait_for_unexpected_cleanup()
            self._acquire_profile_lock()
            self.profile_dir.mkdir(parents=True, exist_ok=True)

            try:
                self._playwright = await async_playwright().start()
                self._context = (
                    await self._playwright.chromium.launch_persistent_context(
                        user_data_dir=self.profile_dir,
                        headless=self.headless,
                        timeout=self.launch_timeout_ms,
                    )
                )
                self._context.on("close", self._handle_unexpected_context_close)
            except Exception as error:
                await self._cleanup_after_failed_launch()
                raise BrowserLaunchError(
                    f"Could not launch Chromium for profile '{self.profile_name}'. "
                    "Verify that Playwright is installed and run "
                    "'python -m playwright install chromium'."
                ) from error

            return self._context

    async def close(self) -> None:
        """Close browser resources and release the profile lock idempotently."""
        async with self._lifecycle_lock:
            await self._wait_for_unexpected_cleanup()
            context, self._context = self._context, None
            playwright, self._playwright = self._playwright, None

            try:
                if context is not None:
                    await context.close()
            finally:
                try:
                    if playwright is not None:
                        await playwright.stop()
                finally:
                    self._release_profile_lock()

    async def __aenter__(self) -> "BrowserManager":
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    def _acquire_profile_lock(self) -> None:
        lock_dir = self.profile_root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{self.profile_name}.lock"
        lock_file = lock_path.open("a+", encoding="utf-8")

        try:
            self._lock_file_handle(lock_file)
        except OSError as error:
            lock_file.close()
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise BrowserManagerError(
                    f"Could not lock browser profile '{self.profile_name}'."
                ) from error
            raise ProfileInUseError(
                f"Browser profile '{self.profile_name}' is already in use. "
                "Close the other process before retrying."
            ) from error
        except Exception:
            lock_file.close()
            raise

        self._lock_file = lock_file

    def _release_profile_lock(self) -> None:
        if self._lock_file is None:
            return

        try:
            self._unlock_file_handle(self._lock_file)
        finally:
            self._lock_file.close()
            self._lock_file = None

    @staticmethod
    def _lock_file_handle(lock_file: IO[str]) -> None:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        if msvcrt is not None:  # pragma: no cover - exercised on Windows only
            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.write("0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        raise BrowserManagerError("No supported file-locking backend is available.")

    @staticmethod
    def _unlock_file_handle(lock_file: IO[str]) -> None:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return
        if msvcrt is not None:  # pragma: no cover - exercised on Windows only
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return

    async def _cleanup_after_failed_launch(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None
        self._context = None
        self._release_profile_lock()

    def _handle_unexpected_context_close(self, closed_context: BrowserContext) -> None:
        """Release ownership if Chromium closes outside ``close``."""
        if closed_context is not self._context:
            return

        self._context = None
        playwright, self._playwright = self._playwright, None
        self._release_profile_lock()

        if playwright is not None:
            self._unexpected_cleanup_task = asyncio.create_task(
                self._stop_after_unexpected_close(playwright)
            )

    async def _stop_after_unexpected_close(self, playwright: Playwright) -> None:
        # Playwright emits ``close`` before the API call that initiated it has
        # necessarily received its response. Allow that protocol response to
        # settle before stopping the shared driver connection.
        await asyncio.sleep(0.1)
        try:
            await playwright.stop()
        except Exception:
            # Chromium is already gone. Cleanup must not produce an unhandled
            # task error or prevent the persistent profile from being reopened.
            return

    async def _wait_for_unexpected_cleanup(self) -> None:
        task, self._unexpected_cleanup_task = self._unexpected_cleanup_task, None
        if task is not None:
            await task
