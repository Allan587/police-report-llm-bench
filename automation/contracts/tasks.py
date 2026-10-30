"""Typed task and execution-attempt contracts for the orchestration layer."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional, TypeAlias, Union
from uuid import uuid4


JSONScalar: TypeAlias = Union[None, bool, int, float, str]
JSONValue: TypeAlias = Union[JSONScalar, list["JSONValue"], dict[str, "JSONValue"]]


class TaskType(str, Enum):
    GENERATION = "generation"
    EVALUATION = "evaluation"
    FEEDBACK = "feedback"
    PROFILE_OPTIMIZATION = "profile_optimization"
    DATASET_PREPARATION = "dataset_preparation"


class ReviewOperation(str, Enum):
    """Review operations whose logical identity is part of a task."""

    MAIN_REVIEW = "main_review"


class Reviewer(str, Enum):
    """Independent reviewer identities used by the current workflow."""

    CHATGPT = "chatgpt"
    GEMINI = "gemini"
    GROK = "grok"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_PENDING = "RETRY_PENDING"
    WAITING_REVIEW = "WAITING_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AttemptStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _optional_text(value: Optional[str], field_name: str) -> None:
    if value is not None:
        _required_text(value, field_name)


def _aware_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")


def _validate_json(value: object, path: str = "metadata") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain non-finite numbers")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            _validate_json(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} must contain only JSON-compatible values")


def immutable_json_mapping(
    value: Mapping[str, JSONValue], field_name: str = "metadata"
) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    copied = dict(value)
    _validate_json(copied, field_name)
    return MappingProxyType(copied)


@dataclass(frozen=True)
class TaskError:
    """Sanitized, structured failure information safe for persistence."""

    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_text(self.code, "code")
        _required_text(self.message, "message")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be a boolean")
        object.__setattr__(
            self, "details", immutable_json_mapping(self.details, "details")
        )


_REQUIRED_FIELDS = {
    TaskType.GENERATION: ("case_id", "provider", "model", "prompt_version"),
    TaskType.EVALUATION: ("case_id",),
    TaskType.FEEDBACK: ("case_id",),
    TaskType.PROFILE_OPTIMIZATION: ("provider", "model", "profile_version"),
    TaskType.DATASET_PREPARATION: (),
}


@dataclass(frozen=True)
class Task:
    """A logical unit of work; retries keep its identity and repetition value."""

    task_id: str
    experiment_id: str
    case_id: Optional[str]
    task_type: TaskType
    provider: Optional[str]
    model: Optional[str]
    prompt_version: Optional[str]
    profile_version: Optional[str]
    iteration: int
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    experimental_repetition: int = 0
    statement_index: Optional[int] = None
    reviewer: Optional[Reviewer] = None
    review_operation: Optional[ReviewOperation] = None

    def __post_init__(self) -> None:
        _required_text(self.task_id, "task_id")
        _required_text(self.experiment_id, "experiment_id")
        if not isinstance(self.task_type, TaskType):
            raise ValueError("task_type must be a TaskType")
        if not isinstance(self.status, TaskStatus):
            raise ValueError("status must be a TaskStatus")

        for field_name in (
            "case_id",
            "provider",
            "model",
            "prompt_version",
            "profile_version",
        ):
            _optional_text(getattr(self, field_name), field_name)

        if not isinstance(self.iteration, int) or isinstance(self.iteration, bool):
            raise ValueError("iteration must be an integer")
        if self.iteration < 0:
            raise ValueError("iteration must be greater than or equal to zero")
        if not isinstance(self.experimental_repetition, int) or isinstance(
            self.experimental_repetition, bool
        ):
            raise ValueError("experimental_repetition must be an integer")
        if self.experimental_repetition < 0:
            raise ValueError(
                "experimental_repetition must be greater than or equal to zero"
            )
        if self.statement_index is not None:
            if not isinstance(self.statement_index, int) or isinstance(
                self.statement_index, bool
            ):
                raise ValueError("statement_index must be an integer")
            if self.statement_index < 0:
                raise ValueError("statement_index must be greater than or equal to zero")
        if self.reviewer is not None and not isinstance(self.reviewer, Reviewer):
            raise ValueError("reviewer must be a Reviewer")
        if self.review_operation is not None and not isinstance(
            self.review_operation, ReviewOperation
        ):
            raise ValueError("review_operation must be a ReviewOperation")

        review_identity = (
            self.statement_index,
            self.reviewer,
            self.review_operation,
        )
        if any(value is not None for value in review_identity):
            if not all(value is not None for value in review_identity):
                raise ValueError(
                    "review tasks require statement_index, reviewer, and review_operation"
                )
            if self.task_type is not TaskType.EVALUATION:
                raise ValueError("review tasks must use the evaluation task type")

        _aware_datetime(self.created_at, "created_at")
        _aware_datetime(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")

        missing = [
            name for name in _REQUIRED_FIELDS[self.task_type] if getattr(self, name) is None
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(
                f"{self.task_type.value} tasks require the following fields: {joined}"
            )

        object.__setattr__(self, "metadata", immutable_json_mapping(self.metadata))

    @classmethod
    def create(
        cls,
        *,
        experiment_id: str,
        task_type: TaskType,
        case_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        prompt_version: Optional[str] = None,
        profile_version: Optional[str] = None,
        iteration: int = 0,
        experimental_repetition: int = 0,
        statement_index: Optional[int] = None,
        reviewer: Optional[Reviewer] = None,
        review_operation: Optional[ReviewOperation] = None,
        metadata: Optional[Mapping[str, JSONValue]] = None,
        now: Optional[datetime] = None,
    ) -> "Task":
        timestamp = now or utc_now()
        return cls(
            task_id=str(uuid4()),
            experiment_id=experiment_id,
            case_id=case_id,
            task_type=task_type,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            profile_version=profile_version,
            iteration=iteration,
            status=TaskStatus.PENDING,
            created_at=timestamp,
            updated_at=timestamp,
            metadata={} if metadata is None else metadata,
            experimental_repetition=experimental_repetition,
            statement_index=statement_index,
            reviewer=reviewer,
            review_operation=review_operation,
        )

    @property
    def deduplication_key(self) -> str:
        """Stable logical identity; task_id, attempts and metadata are excluded."""
        identity = {
            "case_id": self.case_id,
            "experiment_id": self.experiment_id,
            "experimental_repetition": self.experimental_repetition,
            "iteration": self.iteration,
            "model": self.model,
            "profile_version": self.profile_version,
            "prompt_version": self.prompt_version,
            "provider": self.provider,
            "review_operation": (
                None if self.review_operation is None else self.review_operation.value
            ),
            "reviewer": None if self.reviewer is None else self.reviewer.value,
            "statement_index": self.statement_index,
            "task_type": self.task_type.value,
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExecutionAttempt:
    """One execution attempt for a task; retries increment attempt_number."""

    attempt_id: str
    task_id: str
    attempt_number: int
    status: AttemptStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    error: Optional[TaskError] = None

    def __post_init__(self) -> None:
        _required_text(self.attempt_id, "attempt_id")
        _required_text(self.task_id, "task_id")
        if not isinstance(self.attempt_number, int) or isinstance(
            self.attempt_number, bool
        ):
            raise ValueError("attempt_number must be an integer")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be greater than zero")
        if not isinstance(self.status, AttemptStatus):
            raise ValueError("status must be an AttemptStatus")
        if self.error is not None and not isinstance(self.error, TaskError):
            raise ValueError("error must be a TaskError")
        _aware_datetime(self.started_at, "started_at")
        if self.finished_at is not None:
            _aware_datetime(self.finished_at, "finished_at")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at must not be earlier than started_at")

        if self.status is AttemptStatus.RUNNING:
            if self.finished_at is not None or self.error is not None:
                raise ValueError("a running attempt cannot be finished or have an error")
        elif self.finished_at is None:
            raise ValueError("a terminal attempt requires finished_at")

        if self.status is AttemptStatus.SUCCEEDED and self.error is not None:
            raise ValueError("a succeeded attempt cannot have an error")
        if self.status is AttemptStatus.FAILED and self.error is None:
            raise ValueError("a failed attempt requires an error")

    @classmethod
    def start(
        cls, task_id: str, attempt_number: int, *, now: Optional[datetime] = None
    ) -> "ExecutionAttempt":
        return cls(
            attempt_id=str(uuid4()),
            task_id=task_id,
            attempt_number=attempt_number,
            status=AttemptStatus.RUNNING,
            started_at=now or utc_now(),
        )
