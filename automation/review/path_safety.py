"""Lexical confinement and symlink guards for review persistence paths."""

from __future__ import annotations

import os
from pathlib import Path


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def confined_path(path: Path, root: Path, *, label: str) -> Path:
    """Return an absolute path after rejecting lexical escape and existing symlinks.

    This is a pre-operation guard. It intentionally does not claim to eliminate a
    race where another process replaces a checked component before the filesystem
    operation completes.
    """

    candidate = _absolute(Path(path))
    boundary = _absolute(Path(root))
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as error:
        raise ValueError(f"{label} escapes its authorized root") from error

    current = boundary
    if current.is_symlink():
        raise ValueError(f"{label} authorized root must not be a symbolic link")
    if current.exists() and not current.is_dir():
        raise ValueError(f"{label} authorized root must be a directory")
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} must not contain symbolic links")
    return candidate


def review_statement_path(statement_path: Path, statement_index: int) -> Path:
    path = Path(statement_path)
    expected = f"statement_{statement_index:03d}"
    if path.name != expected:
        raise ValueError(f"statement_path must end with {expected}")
    return confined_path(path, path.parent, label="statement_path")
