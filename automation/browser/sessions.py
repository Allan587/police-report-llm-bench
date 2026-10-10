"""Provider-scoped persistent browser sessions.

SessionManager selects and coordinates BrowserManager instances. It contains no
third-party navigation, authentication, prompt submission, or response logic.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple, Union

from playwright.async_api import BrowserContext

from .manager import BrowserManager


PROFILE_ROOT_ENV = "POLICE_BENCH_BROWSER_PROFILE_ROOT"
SUPPORTED_PROVIDERS = frozenset({"chatgpt", "gemini", "grok"})


class SessionManagerError(RuntimeError):
    """Base error for session configuration and lifecycle failures."""


class UnsafeProfileRootError(SessionManagerError):
    """Raised when browser state would be stored in a synchronized directory."""


class UnknownProviderError(SessionManagerError):
    """Raised when a provider has no declared session boundary."""


class SessionCloseError(SessionManagerError):
    """Raised after all sessions were attempted but one or more failed to close."""

    def __init__(self, providers: Iterable[str]) -> None:
        self.providers = tuple(sorted(providers))
        joined = ", ".join(self.providers)
        super().__init__(f"Could not close browser sessions for: {joined}")


def default_profile_root() -> Path:
    """Return a platform-appropriate, non-repository profile location."""
    if sys.platform == "darwin":
        base_dir = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":  # pragma: no cover - exercised on Windows only
        base_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base_dir = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))

    return base_dir / "police-report-llm-bench" / "browser-profiles"


def resolve_profile_root(profile_root: Optional[Union[Path, str]] = None) -> Path:
    """Resolve explicit/env/default profile storage and reject known sync roots."""
    configured = profile_root or os.environ.get(PROFILE_ROOT_ENV)
    resolved = (
        Path(configured).expanduser().resolve()
        if configured
        else default_profile_root().resolve()
    )
    _validate_profile_root(resolved)
    return resolved


def _validate_profile_root(profile_root: Path) -> None:
    folded_parts = tuple(part.casefold() for part in profile_root.parts)
    synchronized_part = next(
        (
            part
            for part in folded_parts
            if part == "cloudstorage"
            or part == "mobile documents"
            or part.startswith("onedrive")
            or part.startswith("dropbox")
            or part.startswith("google drive")
            or part.startswith("icloud drive")
        ),
        None,
    )

    if synchronized_part is not None:
        raise UnsafeProfileRootError(
            "Browser profiles must be stored outside OneDrive, iCloud, Dropbox, "
            "Google Drive, and other synchronized folders. Set "
            f"{PROFILE_ROOT_ENV} to a local-only directory."
        )


BrowserManagerFactory = Callable[..., BrowserManager]


class SessionManager:
    """Manage one reusable persistent BrowserManager per supported provider."""

    def __init__(
        self,
        profile_root: Optional[Union[Path, str]] = None,
        *,
        headless: bool = False,
        launch_timeout_ms: int = 30_000,
        browser_manager_factory: BrowserManagerFactory = BrowserManager,
    ) -> None:
        self.profile_root = resolve_profile_root(profile_root)
        self.headless = headless
        self.launch_timeout_ms = launch_timeout_ms
        self._browser_manager_factory = browser_manager_factory
        self._managers: Dict[str, BrowserManager] = {}

    @property
    def providers(self) -> Tuple[str, ...]:
        return tuple(sorted(self._managers))

    async def open(self, provider: str) -> BrowserContext:
        """Open or reuse the provider's independent persistent context."""
        provider_name = self._normalize_provider(provider)
        manager = self._managers.get(provider_name)

        if manager is None:
            manager = self._browser_manager_factory(
                self.profile_root,
                provider_name,
                headless=self.headless,
                launch_timeout_ms=self.launch_timeout_ms,
            )
            self._managers[provider_name] = manager

        return await manager.open()

    def context(self, provider: str) -> BrowserContext:
        """Return an open provider context with BrowserManager's diagnostics."""
        provider_name = self._normalize_provider(provider)
        manager = self._managers.get(provider_name)
        if manager is None:
            raise SessionManagerError(
                f"Browser session '{provider_name}' has not been opened."
            )
        return manager.context

    def is_open(self, provider: str) -> bool:
        provider_name = self._normalize_provider(provider)
        manager = self._managers.get(provider_name)
        return manager is not None and manager.is_open

    async def close(self, provider: str) -> None:
        provider_name = self._normalize_provider(provider)
        manager = self._managers.get(provider_name)
        if manager is not None:
            await manager.close()

    async def close_all(self) -> None:
        """Attempt every close so one provider cannot leak the others."""
        failed = []
        for provider_name, manager in tuple(self._managers.items()):
            try:
                await manager.close()
            except Exception:
                failed.append(provider_name)

        if failed:
            raise SessionCloseError(failed)

    async def __aenter__(self) -> "SessionManager":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close_all()

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        provider_name = provider.strip().casefold()
        if provider_name not in SUPPORTED_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise UnknownProviderError(
                f"Unsupported browser provider '{provider}'. Supported: {supported}."
            )
        return provider_name
