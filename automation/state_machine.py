"""Explicit and auditable lifecycle transitions for orchestration tasks.

``FAILED`` is terminal. A failure that may be retried transitions from
``RUNNING`` to ``RETRY_PENDING`` instead; the next attempt returns the same task
to ``RUNNING`` and receives a new ``ExecutionAttempt.attempt_id`` and incremented
``attempt_number``. Experimental repetitions are separate tasks whose
``experimental_repetition`` participates in their deduplication key.

For review tasks, ``RETRY_PENDING`` means that deterministic validation rejected
the automatic result and the complete reviewer execution must be repeated.
``WAITING_REVIEW`` is reserved for human or external intervention. A data-level
adjudication value such as ``decision.status = needs_human_review`` does not by
itself change the task lifecycle status.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import FrozenSet, Mapping, Optional

from .contracts import Task, TaskStatus, utc_now


class InvalidStateTransition(ValueError):
    """Raised when a task lifecycle transition is not explicitly permitted."""

    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"invalid task transition: {current.value} -> {target.value}")


TERMINAL_STATUSES: FrozenSet[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

_ALLOWED_TRANSITIONS: Mapping[TaskStatus, FrozenSet[TaskStatus]] = MappingProxyType(
    {
        TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
        TaskStatus.RUNNING: frozenset(
            {
                TaskStatus.RETRY_PENDING,
                TaskStatus.WAITING_REVIEW,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }
        ),
        TaskStatus.RETRY_PENDING: frozenset(
            {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
        ),
        TaskStatus.WAITING_REVIEW: frozenset(
            {
                TaskStatus.RETRY_PENDING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }
        ),
        TaskStatus.COMPLETED: frozenset(),
        TaskStatus.FAILED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
    }
)


@dataclass(frozen=True)
class TaskTransition:
    task_id: str
    from_status: TaskStatus
    to_status: TaskStatus
    occurred_at: datetime


@dataclass(frozen=True)
class TransitionResult:
    task: Task
    event: TaskTransition


def allowed_transitions(status: TaskStatus) -> FrozenSet[TaskStatus]:
    if not isinstance(status, TaskStatus):
        raise ValueError("status must be a TaskStatus")
    return _ALLOWED_TRANSITIONS[status]


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    if not isinstance(target, TaskStatus):
        return False
    return target in allowed_transitions(current)


def transition(
    task: Task,
    target: TaskStatus,
    *,
    occurred_at: Optional[datetime] = None,
) -> TransitionResult:
    if not isinstance(task, Task):
        raise ValueError("task must be a Task")
    if not isinstance(target, TaskStatus):
        raise ValueError("target must be a TaskStatus")
    if not can_transition(task.status, target):
        raise InvalidStateTransition(task.status, target)

    timestamp = occurred_at or utc_now()
    if timestamp.tzinfo is None:
        raise ValueError("occurred_at must be a timezone-aware datetime")
    if timestamp < task.updated_at:
        raise ValueError("occurred_at must not be earlier than task.updated_at")

    updated_task = replace(task, status=target, updated_at=timestamp)
    event = TaskTransition(
        task_id=task.task_id,
        from_status=task.status,
        to_status=target,
        occurred_at=timestamp,
    )
    return TransitionResult(task=updated_task, event=event)
