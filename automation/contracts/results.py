"""Typed result contract produced by a single execution attempt."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Optional, Sequence
from uuid import uuid4

from .tasks import (
    JSONValue,
    Reviewer,
    TaskError,
    _aware_datetime,
    _required_text,
    immutable_json_mapping,
    utc_now,
)


class BenchmarkModel(str, Enum):
    GEMMA = "gemma"
    MISTRAL = "mistral"
    LLAMA = "llama"


_EXPECTED_MODELS = frozenset(BenchmarkModel)


def _statement_index(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("statement_index must be a non-negative integer")


def _review_provenance(
    *, statement_index: int, reviewer: Reviewer, attempt_id: str
) -> None:
    _statement_index(statement_index)
    if not isinstance(reviewer, Reviewer):
        raise ValueError("reviewer must be a Reviewer")
    _required_text(attempt_id, "attempt_id")


def _string_list(value: object, field_name: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")


def _mapping(value: object, field_name: str) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _string(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")


def _quality_section(value: object, field_name: str) -> None:
    section = _mapping(value, field_name)
    if section.get("status") not in {"pass", "minor_issues", "fail"}:
        raise ValueError(f"{field_name}.status is invalid")
    _string_list(section.get("findings"), f"{field_name}.findings")


def _model_review(value: Mapping[str, JSONValue], field_name: str) -> None:
    _quality_section(value.get("factuality"), f"{field_name}.factuality")
    _string_list(value.get("hallucinations"), f"{field_name}.hallucinations")
    _string_list(value.get("omissions"), f"{field_name}.omissions")
    _quality_section(
        value.get("narrative_quality"), f"{field_name}.narrative_quality"
    )
    classification = _mapping(
        value.get("crime_classification"), f"{field_name}.crime_classification"
    )
    _string(
        classification.get("model_category"),
        f"{field_name}.crime_classification.model_category",
    )
    if classification.get("assessment") not in {
        "correct",
        "partially_correct",
        "incorrect",
        "indeterminate",
    }:
        raise ValueError(f"{field_name}.crime_classification.assessment is invalid")
    _string(
        classification.get("proposed_category_cr"),
        f"{field_name}.crime_classification.proposed_category_cr",
    )
    _string(
        classification.get("justification"),
        f"{field_name}.crime_classification.justification",
    )
    _string_list(value.get("useful_elements"), f"{field_name}.useful_elements")


def _model_set(artifacts: Sequence[object], field_name: str) -> None:
    models = [getattr(artifact, "model", None) for artifact in artifacts]
    if len(models) != len(set(models)):
        raise ValueError(f"{field_name} contains duplicate models")
    if set(models) != _EXPECTED_MODELS:
        raise ValueError(f"{field_name} must contain gemma, mistral, and llama")


@dataclass(frozen=True)
class MainReviewOutput:
    statement_index: int
    reviewer: Reviewer
    attempt_id: str
    output: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        _review_provenance(
            statement_index=self.statement_index,
            reviewer=self.reviewer,
            attempt_id=self.attempt_id,
        )
        output = immutable_json_mapping(self.output, "main_review.output")
        object.__setattr__(self, "output", output)
        if output.get("reviewer") != self.reviewer.value:
            raise ValueError("main review reviewer does not match its provenance")
        model_reviews = output.get("model_reviews")
        if not isinstance(model_reviews, list):
            raise ValueError("main review model_reviews must be a list")
        names = []
        for item in model_reviews:
            if not isinstance(item, Mapping):
                raise ValueError("each main review entry must be a mapping")
            names.append(item.get("model_name"))
            _model_review(item, f"main_review.{item.get('model_name', 'unknown')}")
        if len(names) != len(set(names)):
            raise ValueError("main review contains duplicate model identifiers")
        if set(names) != {model.value for model in _EXPECTED_MODELS}:
            raise ValueError("main review must evaluate gemma, mistral, and llama")
        ideal_report = _mapping(
            output.get("proposed_ideal_report"), "main_review.proposed_ideal_report"
        )
        _string(
            ideal_report.get("narrative"),
            "main_review.proposed_ideal_report.narrative",
        )
        _string(ideal_report.get("closing"), "main_review.proposed_ideal_report.closing")
        ideal_classification = _mapping(
            ideal_report.get("crime_classification"),
            "main_review.proposed_ideal_report.crime_classification",
        )
        _string(
            ideal_classification.get("category"),
            "main_review.proposed_ideal_report.crime_classification.category",
        )
        _string(
            ideal_classification.get("justification"),
            "main_review.proposed_ideal_report.crime_classification.justification",
        )


@dataclass(frozen=True)
class HistoryProposalOutput:
    statement_index: int
    reviewer: Reviewer
    attempt_id: str
    model: BenchmarkModel
    output: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        _review_provenance(
            statement_index=self.statement_index,
            reviewer=self.reviewer,
            attempt_id=self.attempt_id,
        )
        if not isinstance(self.model, BenchmarkModel):
            raise ValueError("model must be a BenchmarkModel")
        output = immutable_json_mapping(self.output, "history.output")
        object.__setattr__(self, "output", output)
        if output.get("model") != self.model.value:
            raise ValueError("history model does not match its provenance")
        if output.get("output") != self.statement_index:
            raise ValueError("history output does not match statement_index")
        _required_text(output.get("date"), "history.date")
        _string_list(output.get("observations"), "history.observations")


_PROFILE_LIST_FIELDS = (
    "new_strengths",
    "new_weaknesses",
    "new_hallucination_patterns",
    "new_classification_patterns",
    "new_narrative_patterns",
    "new_factuality_patterns",
    "new_finetuning_priorities",
)


@dataclass(frozen=True)
class ProfileProposalOutput:
    statement_index: int
    reviewer: Reviewer
    attempt_id: str
    model: BenchmarkModel
    output: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        _review_provenance(
            statement_index=self.statement_index,
            reviewer=self.reviewer,
            attempt_id=self.attempt_id,
        )
        if not isinstance(self.model, BenchmarkModel):
            raise ValueError("model must be a BenchmarkModel")
        output = immutable_json_mapping(self.output, "profile_proposal.output")
        object.__setattr__(self, "output", output)
        if output.get("model_name") != self.model.value:
            raise ValueError("profile proposal model_name does not match its provenance")
        for field_name in _PROFILE_LIST_FIELDS:
            _string_list(output.get(field_name), f"profile_proposal.{field_name}")


@dataclass(frozen=True)
class CompleteReviewResult:
    """Validated seven-output result produced by one reviewer attempt."""

    task_id: str
    attempt_id: str
    statement_index: int
    reviewer: Reviewer
    main_review: MainReviewOutput
    histories: Sequence[HistoryProposalOutput]
    profile_proposals: Sequence[ProfileProposalOutput]
    created_at: datetime

    def __post_init__(self) -> None:
        _required_text(self.task_id, "task_id")
        _review_provenance(
            statement_index=self.statement_index,
            reviewer=self.reviewer,
            attempt_id=self.attempt_id,
        )
        _aware_datetime(self.created_at, "created_at")
        if not isinstance(self.main_review, MainReviewOutput):
            raise ValueError("main_review must be a MainReviewOutput")
        histories = tuple(self.histories)
        proposals = tuple(self.profile_proposals)
        if any(not isinstance(item, HistoryProposalOutput) for item in histories):
            raise ValueError("histories must contain HistoryProposalOutput values")
        if any(not isinstance(item, ProfileProposalOutput) for item in proposals):
            raise ValueError(
                "profile_proposals must contain ProfileProposalOutput values"
            )
        _model_set(histories, "histories")
        _model_set(proposals, "profile_proposals")
        object.__setattr__(self, "histories", histories)
        object.__setattr__(self, "profile_proposals", proposals)

        artifacts = (self.main_review, *histories, *proposals)
        for artifact in artifacts:
            if artifact.statement_index != self.statement_index:
                raise ValueError("review result mixes statement_index values")
            if artifact.reviewer is not self.reviewer:
                raise ValueError("review result mixes reviewers")
            if artifact.attempt_id != self.attempt_id:
                raise ValueError("review result mixes execution attempts")
            serialized = artifact.output
            expected_identity = {
                "task_id": self.task_id,
                "attempt_id": self.attempt_id,
                "statement_index": self.statement_index,
                "reviewer": self.reviewer.value,
            }
            if any(
                field in serialized and serialized.get(field) != expected
                for field, expected in expected_identity.items()
            ):
                raise ValueError(
                    "review artifact serialized identity contradicts CompleteReviewResult"
                )

    def as_task_result(self) -> "TaskResult":
        """Convert only a fully validated review into a successful generic result."""

        return TaskResult.success(
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            now=self.created_at,
            output={
                "statement_index": self.statement_index,
                "reviewer": self.reviewer.value,
                "main_review": dict(self.main_review.output),
                "histories": [dict(item.output) for item in self.histories],
                "profile_proposals": [
                    dict(item.output) for item in self.profile_proposals
                ],
            },
        )


@dataclass(frozen=True)
class TaskResult:
    result_id: str
    task_id: str
    attempt_id: str
    succeeded: bool
    created_at: datetime
    output: Mapping[str, JSONValue] = field(default_factory=dict)
    error: Optional[TaskError] = None

    def __post_init__(self) -> None:
        _required_text(self.result_id, "result_id")
        _required_text(self.task_id, "task_id")
        _required_text(self.attempt_id, "attempt_id")
        if not isinstance(self.succeeded, bool):
            raise ValueError("succeeded must be a boolean")
        if self.error is not None and not isinstance(self.error, TaskError):
            raise ValueError("error must be a TaskError")
        _aware_datetime(self.created_at, "created_at")
        object.__setattr__(self, "output", immutable_json_mapping(self.output, "output"))

        if self.succeeded and self.error is not None:
            raise ValueError("a successful result cannot have an error")
        if not self.succeeded and self.error is None:
            raise ValueError("a failed result requires an error")

    @classmethod
    def success(
        cls,
        *,
        task_id: str,
        attempt_id: str,
        output: Optional[Mapping[str, JSONValue]] = None,
        now: Optional[datetime] = None,
    ) -> "TaskResult":
        return cls(
            result_id=str(uuid4()),
            task_id=task_id,
            attempt_id=attempt_id,
            succeeded=True,
            created_at=now or utc_now(),
            output={} if output is None else output,
        )

    @classmethod
    def failure(
        cls,
        *,
        task_id: str,
        attempt_id: str,
        error: TaskError,
        now: Optional[datetime] = None,
    ) -> "TaskResult":
        return cls(
            result_id=str(uuid4()),
            task_id=task_id,
            attempt_id=attempt_id,
            succeeded=False,
            created_at=now or utc_now(),
            error=error,
        )
