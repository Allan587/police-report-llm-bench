"""Shared strict validators for persisted adjudication artifacts.

These functions deliberately cover only the schemas consumed by both the
adjudication writer and the global completeness inspector.
"""

from __future__ import annotations

from typing import Mapping

from automation.contracts import BenchmarkModel, Reviewer


QUALITY_FIELDS = frozenset(
    {
        "factual_consistency",
        "unsupported_information",
        "relevant_information_preserved",
        "temporal_consistency",
        "spatial_consistency",
        "third_person",
        "institutional_language",
        "classification_supported",
        "narrative_classification_consistency",
        "format_valid",
    }
)
SELECTED_FIELDS = frozenset(
    {"factuality", "narrative_structure", "classification", "discarded_elements"}
)
DECISION_STATUSES = frozenset({"approved", "needs_human_review", "rejected"})
CONSENSUS_STATUSES = frozenset(
    {"unanimous", "majority", "disagreement", "indeterminate"}
)
PROFILE_TOP_LEVEL = frozenset(
    {
        "model_name",
        "strengths",
        "weaknesses",
        "common_hallucinations",
        "classification_behavior",
        "narrative_behavior",
        "factuality_behavior",
        "fine_tuning_priorities",
        "profile_metadata",
    }
)
PROFILE_PATTERN_PATHS = (
    ("strengths",),
    ("weaknesses",),
    ("common_hallucinations",),
    ("classification_behavior", "strengths"),
    ("classification_behavior", "weaknesses"),
    ("narrative_behavior", "strengths"),
    ("narrative_behavior", "weaknesses"),
    ("factuality_behavior", "strengths"),
    ("factuality_behavior", "weaknesses"),
)


def valid_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_adjudication(value: object, statement_index: int) -> None:
    if not isinstance(value, dict) or set(value) != {
        "statement_index",
        "reviewers",
        "classification_consensus",
        "selected_elements",
        "final_report",
        "quality_control",
        "decision",
    }:
        raise ValueError("adjudication has an invalid top-level structure")
    if value.get("statement_index") != statement_index:
        raise ValueError("adjudication statement_index does not match")
    reviewers = value.get("reviewers")
    if (
        not isinstance(reviewers, list)
        or any(not isinstance(item, str) for item in reviewers)
        or len(reviewers) != len(set(reviewers))
        or set(reviewers) != {reviewer.value for reviewer in Reviewer}
    ):
        raise ValueError("adjudication must identify all three reviewers exactly once")
    consensus = value.get("classification_consensus")
    if not isinstance(consensus, dict) or set(consensus) != {
        "reviewer_classifications",
        "final_category",
        "consensus",
        "final_justification",
    }:
        raise ValueError("classification_consensus must be an object")
    classifications = consensus.get("reviewer_classifications")
    if (
        not isinstance(classifications, dict)
        or set(classifications) != {reviewer.value for reviewer in Reviewer}
        or any(not nonempty_text(item) for item in classifications.values())
        or not nonempty_text(consensus.get("final_category"))
        or consensus.get("consensus") not in CONSENSUS_STATUSES
        or not nonempty_text(consensus.get("final_justification"))
    ):
        raise ValueError("classification_consensus is invalid")
    selected = value.get("selected_elements")
    if (
        not isinstance(selected, dict)
        or set(selected) != SELECTED_FIELDS
        or any(not string_list(selected.get(field)) for field in SELECTED_FIELDS)
    ):
        raise ValueError("selected_elements is invalid")
    report = value.get("final_report")
    classification = None if not isinstance(report, dict) else report.get("crime_classification")
    if (
        not isinstance(report, dict)
        or set(report) != {"narrative", "closing", "crime_classification"}
        or not nonempty_text(report.get("narrative"))
        or not isinstance(report.get("closing"), str)
        or not isinstance(classification, dict)
        or set(classification) != {"category", "justification"}
        or not nonempty_text(classification.get("category"))
        or not nonempty_text(classification.get("justification"))
    ):
        raise ValueError("final_report is invalid")
    quality = value.get("quality_control")
    if (
        not isinstance(quality, dict)
        or set(quality) != QUALITY_FIELDS
        or any(quality.get(field) not in {"pass", "fail"} for field in QUALITY_FIELDS)
    ):
        raise ValueError("quality_control is invalid")
    decision = value.get("decision")
    if (
        not isinstance(decision, dict)
        or set(decision) != {"status", "reason"}
        or decision.get("status") not in DECISION_STATUSES
        or not nonempty_text(decision.get("reason"))
    ):
        raise ValueError("decision is invalid")


def validate_history(value: object, model: BenchmarkModel, statement_index: int) -> None:
    if not isinstance(value, dict) or set(value) != {
        "model",
        "output",
        "date",
        "observations",
    }:
        raise ValueError("history must contain exactly model, output, date, and observations")
    if (
        value.get("model") != model.value
        or value.get("output") != statement_index
        or not nonempty_text(value.get("date"))
        or not string_list(value.get("observations"))
    ):
        raise ValueError(f"history for {model.value} is invalid")


def _valid_pattern_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and set(item) == {"pattern", "evidence_count", "last_observed_output"}
        and nonempty_text(item.get("pattern"))
        and valid_index(item.get("evidence_count"))
        and valid_index(item.get("last_observed_output"))
        for item in value
    )


def validate_profile(value: object, model: BenchmarkModel) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != PROFILE_TOP_LEVEL
        or value.get("model_name") != model.value
    ):
        raise ValueError(f"profile for {model.value} has an invalid top-level structure")
    for field in ("strengths", "weaknesses", "common_hallucinations"):
        if not _valid_pattern_list(value.get(field)):
            raise ValueError(f"profile field {field} is invalid")
    for field in ("classification_behavior", "narrative_behavior", "factuality_behavior"):
        section = value.get(field)
        if (
            not isinstance(section, dict)
            or set(section) != {"strengths", "weaknesses"}
            or not _valid_pattern_list(section.get("strengths"))
            or not _valid_pattern_list(section.get("weaknesses"))
        ):
            raise ValueError(f"profile section {field} is invalid")
    priorities = value.get("fine_tuning_priorities")
    if not isinstance(priorities, list) or any(
        not isinstance(item, dict)
        or set(item) != {"priority", "priority_level"}
        or not nonempty_text(item.get("priority"))
        or item.get("priority_level") not in {"high", "medium", "low"}
        for item in priorities
    ):
        raise ValueError("fine_tuning_priorities is invalid")
    metadata = value.get("profile_metadata")
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"outputs_reviewed", "last_updated", "version"}
        or not valid_index(metadata.get("outputs_reviewed"))
        or not nonempty_text(metadata.get("last_updated"))
        or not valid_index(metadata.get("version"))
    ):
        raise ValueError("profile_metadata is invalid")


def pattern_lists(profile: Mapping[str, object]) -> tuple[list[dict], ...]:
    lists = []
    for path in PROFILE_PATTERN_PATHS:
        value: object = profile
        for component in path:
            value = value[component]  # type: ignore[index]
        lists.append(value)  # type: ignore[arg-type]
    return tuple(lists)


def _patterns_by_identity(
    profile: Mapping[str, object],
) -> dict[tuple[tuple[str, ...], str], dict]:
    patterns = {}
    for category, values in zip(PROFILE_PATTERN_PATHS, pattern_lists(profile)):
        for item in values:
            patterns[(category, item["pattern"])] = item
    return patterns


def validate_profile_transition(
    current: dict, candidate: dict, model: BenchmarkModel
) -> None:
    """Reject measurable rollback while allowing adjudicated removals."""

    validate_profile(current, model)
    validate_profile(candidate, model)
    current_metadata = current["profile_metadata"]
    candidate_metadata = candidate["profile_metadata"]
    current_version = current_metadata["version"]
    candidate_version = candidate_metadata["version"]
    if candidate_version < current_version:
        raise ValueError(f"profile for {model.value} regresses version")
    if candidate_metadata["outputs_reviewed"] < current_metadata["outputs_reviewed"]:
        raise ValueError(f"profile for {model.value} regresses outputs_reviewed")
    if candidate_version == current_version and candidate != current:
        raise ValueError(
            f"profile for {model.value} changes content without incrementing version"
        )

    candidate_patterns = _patterns_by_identity(candidate)
    for identity, previous in _patterns_by_identity(current).items():
        category, pattern = identity
        updated = candidate_patterns.get(identity)
        if updated is None:
            # The adjudication contract permits a deliberate removal. The
            # validator cannot infer intent, so retained patterns are monotonic.
            continue
        if updated["evidence_count"] < previous["evidence_count"]:
            raise ValueError(
                f"profile for {model.value} regresses evidence_count for "
                f"{'.'.join(category)}/{pattern!r}"
            )
        if updated["last_observed_output"] < previous["last_observed_output"]:
            raise ValueError(
                f"profile for {model.value} regresses last_observed_output for "
                f"{'.'.join(category)}/{pattern!r}"
            )


def validate_profile_adjudication_transition(
    current: dict,
    candidate: dict,
    model: BenchmarkModel,
    *,
    statement_index: int,
    adjudication: Mapping[str, object],
) -> None:
    """Validate one mono-statement profile proposal before any bundle effect.

    The profile prompt defines ``outputs_reviewed`` as distinct outputs actually
    incorporated, ``evidence_count`` as distinct outputs supporting a pattern,
    and ``last_observed_output`` as the most recent supporting index.  In the
    sequential workflow, the preimage for statement N therefore covers exactly
    N outputs (0..N-1), and the candidate covers N+1.  A retained pattern may
    gain at most one unit of evidence in this single-statement adjudication.
    """

    if not valid_index(statement_index):
        raise ValueError("statement_index must be a non-negative integer")
    validate_adjudication(adjudication, statement_index)
    validate_profile_transition(current, candidate, model)

    current_metadata = current["profile_metadata"]
    candidate_metadata = candidate["profile_metadata"]
    if current_metadata["outputs_reviewed"] != statement_index:
        raise ValueError(
            f"profile preimage for {model.value} is not aligned with statement "
            f"{statement_index}"
        )
    if candidate_metadata["outputs_reviewed"] != statement_index + 1:
        raise ValueError(
            f"profile for {model.value} has incompatible outputs_reviewed"
        )
    if candidate_metadata["version"] != current_metadata["version"] + 1:
        raise ValueError(
            f"profile for {model.value} must advance exactly one version"
        )

    previous_patterns = _patterns_by_identity(current)
    candidate_patterns = _patterns_by_identity(candidate)
    for identity, updated in candidate_patterns.items():
        category, pattern = identity
        if updated["last_observed_output"] > statement_index:
            raise ValueError(
                f"profile for {model.value} has future last_observed_output for "
                f"{'.'.join(category)}/{pattern!r}"
            )
        if updated["evidence_count"] > candidate_metadata["outputs_reviewed"]:
            raise ValueError(
                f"profile for {model.value} has impossible evidence_count for "
                f"{'.'.join(category)}/{pattern!r}"
            )
        previous = previous_patterns.get(identity)
        if previous is None:
            if (
                updated["evidence_count"] != 1
                or updated["last_observed_output"] != statement_index
            ):
                raise ValueError(
                    f"new profile pattern for {model.value} is incompatible with "
                    f"statement {statement_index}: {'.'.join(category)}/{pattern!r}"
                )
            continue

        evidence_delta = updated["evidence_count"] - previous["evidence_count"]
        if evidence_delta not in {0, 1}:
            raise ValueError(
                f"profile for {model.value} has incompatible evidence_count for "
                f"{'.'.join(category)}/{pattern!r}"
            )
        expected_last = (
            statement_index
            if evidence_delta == 1
            else previous["last_observed_output"]
        )
        if updated["last_observed_output"] != expected_last:
            raise ValueError(
                f"profile for {model.value} has incompatible last_observed_output "
                f"for {'.'.join(category)}/{pattern!r}"
            )
