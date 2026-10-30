"""Sequential statement discovery without inferring global completeness."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


_STATEMENT_DIRECTORY = re.compile(r"^statement_(?P<index>\d{3,})$")


class CompletionState(str, Enum):
    """Result supplied by the future global completeness verifier."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class SelectionAction(str, Enum):
    RESUME = "resume"
    ADVANCE = "advance"
    PENDING = "pending"


@dataclass(frozen=True)
class StatementDirectory:
    statement_index: int
    path: Path


@dataclass(frozen=True)
class StatementDiscovery:
    root: Path
    statements: tuple[StatementDirectory, ...]
    duplicate_indices: tuple[int, ...] = ()
    error: Optional[str] = None

    @property
    def latest(self) -> Optional[StatementDirectory]:
        return self.statements[-1] if self.statements else None


@dataclass(frozen=True)
class StatementSelection:
    action: SelectionAction
    statement_index: Optional[int]
    statement_path: Optional[Path]
    latest_existing_index: Optional[int]
    reason: str

    @property
    def decided(self) -> bool:
        return self.action is not SelectionAction.PENDING


def discover_statements(review_cases_root: Path) -> StatementDiscovery:
    """Return recognized statement directories ordered by numeric index.

    Inspection is read-only. Names with fewer than three digits or non-numeric
    suffixes are not statement directories and are ignored.
    """

    root = Path(review_cases_root)
    if not root.exists():
        return StatementDiscovery(root=root, statements=(), error="review cases root is missing")
    if not root.is_dir():
        return StatementDiscovery(
            root=root, statements=(), error="review cases root is not a directory"
        )

    by_index: dict[int, list[Path]] = {}
    for child in root.iterdir():
        if not child.is_dir():
            continue
        match = _STATEMENT_DIRECTORY.fullmatch(child.name)
        if match is None:
            continue
        index = int(match.group("index"))
        by_index.setdefault(index, []).append(child)

    duplicates = tuple(sorted(index for index, paths in by_index.items() if len(paths) > 1))
    statements = tuple(
        StatementDirectory(statement_index=index, path=sorted(paths)[0])
        for index, paths in sorted(by_index.items())
    )
    return StatementDiscovery(
        root=root,
        statements=statements,
        duplicate_indices=duplicates,
    )


def select_statement(
    review_cases_root: Path,
    *,
    latest_completion: CompletionState,
) -> StatementSelection:
    """Resume or advance using an externally supplied completeness decision."""

    if not isinstance(latest_completion, CompletionState):
        raise ValueError("latest_completion must be a CompletionState")

    discovery = discover_statements(review_cases_root)
    latest = discovery.latest
    latest_index = None if latest is None else latest.statement_index

    if discovery.error is not None:
        return StatementSelection(
            action=SelectionAction.PENDING,
            statement_index=None,
            statement_path=None,
            latest_existing_index=None,
            reason=discovery.error,
        )
    if discovery.duplicate_indices:
        duplicate_text = ", ".join(str(index) for index in discovery.duplicate_indices)
        return StatementSelection(
            action=SelectionAction.PENDING,
            statement_index=None,
            statement_path=None,
            latest_existing_index=latest_index,
            reason=f"duplicate statement directory indices: {duplicate_text}",
        )
    if latest is None:
        return StatementSelection(
            action=SelectionAction.PENDING,
            statement_index=None,
            statement_path=None,
            latest_existing_index=None,
            reason="no existing statement establishes a safe sequential starting point",
        )
    active_indices = {
        statement.statement_index
        for statement in discovery.statements
        if statement.statement_index >= 10
    }
    if active_indices:
        first_missing = next(
            (
                index
                for index in range(10, latest.statement_index)
                if index not in active_indices
            ),
            None,
        )
        if first_missing is not None:
            return StatementSelection(
                action=SelectionAction.PENDING,
                statement_index=None,
                statement_path=None,
                latest_existing_index=latest.statement_index,
                reason=(
                    f"statement_{first_missing:03d} is missing from the active sequence; "
                    "its original inputs must be validated before it can be selected"
                ),
            )
    if latest_completion is CompletionState.UNKNOWN:
        return StatementSelection(
            action=SelectionAction.PENDING,
            statement_index=None,
            statement_path=None,
            latest_existing_index=latest.statement_index,
            reason="global completeness of the latest statement is unknown",
        )
    if latest_completion is CompletionState.INCOMPLETE:
        return StatementSelection(
            action=SelectionAction.RESUME,
            statement_index=latest.statement_index,
            statement_path=latest.path,
            latest_existing_index=latest.statement_index,
            reason="latest statement is confirmed incomplete",
        )

    next_index = latest.statement_index + 1
    return StatementSelection(
        action=SelectionAction.ADVANCE,
        statement_index=next_index,
        statement_path=Path(review_cases_root) / f"statement_{next_index:03d}",
        latest_existing_index=latest.statement_index,
        reason="latest statement is confirmed complete",
    )
