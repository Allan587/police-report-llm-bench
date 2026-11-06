"""Operator-scoped Playwright sessions for supported local browser engines."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from automation.browser.manager import BrowserManager
from .config import BrowserRuntimeConfig, PortableConfigError, _no_symlink


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class BrowserSessionFactory:
    """Builds existing BrowserManager contexts; callers own each close."""

    def __init__(self, config: BrowserRuntimeConfig, *,
                 manager_type: Callable[..., BrowserManager] | None = None,
                 profile_root: Path | None = None):
        if config.engine != "chromium":
            raise PortableConfigError("unsupported browser engine")
        self.config = config
        if manager_type is None:
            from automation.browser.manager import BrowserManager
            manager_type = BrowserManager
        self.manager_type = manager_type
        self.profile_root = profile_root or config.profile_root

    def create(self, operator: str, reviewer: str) -> BrowserManager:
        if not isinstance(operator, str) or not _NAME.fullmatch(operator):
            raise PortableConfigError("operator must be a safe single directory name")
        if reviewer not in {"chatgpt", "gemini", "grok"}:
            raise PortableConfigError("unknown reviewer")
        _no_symlink(self.profile_root / operator, "browser profile")
        return self.manager_type(
            self.profile_root / operator, reviewer,
            headless=self.config.headless,
            launch_timeout_ms=self.config.timeout_ms,
            locale=self.config.locale,
        )
