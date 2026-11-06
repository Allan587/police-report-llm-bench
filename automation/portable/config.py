"""Versioned local configuration; paths are anchored at the config file."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PortableConfigError(ValueError):
    """A portable environment cannot be used safely."""


def _keys(value: Any, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise PortableConfigError(f"{label} requires exactly: {', '.join(sorted(expected))}")
    return value


def _path(raw: Any, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise PortableConfigError(f"{label} must be a non-empty path")
    candidate = Path(raw).expanduser()
    return (candidate if candidate.is_absolute() else base / candidate).absolute()


def _no_symlink(path: Path, label: str) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise PortableConfigError(f"{label} contains a symbolic link")


def _under(path: Path, roots: tuple[Path, ...], label: str) -> None:
    if not any(path.resolve().is_relative_to(root.resolve()) for root in roots):
        raise PortableConfigError(f"{label} is outside allowed input roots")


@dataclass(frozen=True)
class BrowserRuntimeConfig:
    engine: str
    headless: bool
    locale: str
    timeout_ms: int
    profile_root: Path


@dataclass(frozen=True)
class PortableConfig:
    config_path: Path
    input_root: Path
    allowed_input_roots: tuple[Path, ...]
    output_root: Path
    browser: BrowserRuntimeConfig

    @property
    def statements_path(self) -> Path:
        return self.input_root / "statements.jsonl"

    @property
    def runs_root(self) -> Path:
        return self.input_root / "runs"


def load_config(path: Path) -> PortableConfig:
    path = Path(path).expanduser().absolute()
    _no_symlink(path, "configuration")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PortableConfigError("configuration is missing, unreadable or invalid JSON") from error
    _keys(raw, {"schema_version", "inputs", "outputs", "browser"}, "configuration")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise PortableConfigError("schema_version must be 1")
    inputs = _keys(raw["inputs"], {"root", "allowed_roots"}, "inputs")
    outputs = _keys(raw["outputs"], {"root"}, "outputs")
    browser = _keys(raw["browser"], {"engine", "headless", "locale", "timeout_ms", "profile_root"}, "browser")
    base = path.parent
    root = _path(inputs["root"], base, "inputs.root")
    allowed = inputs["allowed_roots"]
    if not isinstance(allowed, list) or not allowed:
        raise PortableConfigError("inputs.allowed_roots must be a non-empty list")
    allowed_roots = tuple(_path(item, base, "inputs.allowed_roots") for item in allowed)
    output = _path(outputs["root"], base, "outputs.root")
    profile = _path(browser["profile_root"], base, "browser.profile_root")
    for label, item in (("inputs.root", root), ("outputs.root", output), ("browser.profile_root", profile)):
        _no_symlink(item, label)
    _under(root, allowed_roots, "inputs.root")
    if any(output.resolve().is_relative_to(item.resolve()) or item.resolve().is_relative_to(output.resolve()) for item in (root, *allowed_roots)):
        raise PortableConfigError("input and output roots overlap")
    if profile.resolve().is_relative_to(output.resolve()) or output.resolve().is_relative_to(profile.resolve()):
        raise PortableConfigError("browser profiles and outputs overlap")
    if profile.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(profile.resolve()):
        raise PortableConfigError("browser profiles and inputs overlap")
    if browser["engine"] != "chromium":
        raise PortableConfigError("only the tested chromium engine is supported")
    if type(browser["headless"]) is not bool:
        raise PortableConfigError("browser.headless must be boolean")
    if not isinstance(browser["locale"], str) or not browser["locale"].strip():
        raise PortableConfigError("browser.locale must be non-empty")
    if type(browser["timeout_ms"]) is not int or not 1000 <= browser["timeout_ms"] <= 120000:
        raise PortableConfigError("browser.timeout_ms must be 1000..120000")
    folded = tuple(part.casefold() for part in profile.parts)
    if any(part == "cloudstorage" or part == "mobile documents" or
           part.startswith(("onedrive", "dropbox", "google drive", "icloud drive"))
           for part in folded):
        raise PortableConfigError("browser profiles must be outside synchronized folders")
    if any((parent / ".git").exists() for parent in (profile, *profile.parents)):
        raise PortableConfigError("browser profiles must be outside a Git checkout")
    return PortableConfig(path, root, allowed_roots, output,
                          BrowserRuntimeConfig("chromium", browser["headless"], browser["locale"], browser["timeout_ms"], profile))
