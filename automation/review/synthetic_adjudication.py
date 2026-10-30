"""Lease-protected preparation and scoped persistence of synthetic adjudication.

This module composes the approved vertical reviewer flow with O-01/O-02 and the
shared validators.  The original preparation entry point remains read-only.
The OLBM-31 entry point prepares the existing 1+3+3 application but confirms
only ``adjudication.json``; histories and profiles remain staged for OLBM-33.
``ADJUDICATION_NOTES`` is accepted only as raw adapter output and discarded.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Optional, Protocol, Sequence

from automation.contracts import BenchmarkModel, CompleteReviewResult, Reviewer
from automation.orchestrator import PlanAction, StatementPlan, plan_next_statement
from automation.workflow_coordinator import (
    CoordinationExpectation,
    CoordinationStatus,
    confirm_workflow_expectation_under_lease,
    coordinate_workflow_under_lease,
)
from automation.workflow_lease import (
    LeaseOwner,
    WorkflowLease,
    WorkflowLeaseError,
    WorkflowLeaseInUseError,
)

from .input_loader import ReviewInputValidation, inspect_review_inputs
from .adjudication_storage import (
    AdjudicationApplication,
    AdjudicationCommitStatus,
    ApplicationPhase,
    commit_adjudication_bundle,
    inspect_adjudication_application,
)
from .result_inspector import ReviewerStatus, inspect_reviewer_results
from .review_storage import ConfirmedReviewSet, resolve_confirmed_review_set
from .validators import validate_adjudication, validate_history, validate_profile
from .vertical_flow import (
    ReviewerAdapter,
    VerticalReviewConfig,
    VerticalReviewResult,
    VerticalReviewStatus,
    run_synthetic_reviewer_once,
)


class SyntheticAdjudicationStatus(str, Enum):
    PREPARED = "PREPARED"
    PERSISTED = "PERSISTED"
    UNCHANGED = "UNCHANGED"
    CONTENDED = "CONTENDED"
    STALE_PLAN = "STALE_PLAN"
    BLOCKED = "BLOCKED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    ADJUDICATOR_FAILED = "ADJUDICATOR_FAILED"
    OUTPUT_REJECTED = "OUTPUT_REJECTED"


class AdjudicatorAdapterError(RuntimeError):
    """Expected, sanitized failure from the synthetic adjudicator port."""


@dataclass(frozen=True)
class SyntheticAdjudicationConfig:
    review: VerticalReviewConfig
    prompt_path: Path
    prompt_version: str = "synthetic-review-adjudication-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.review, VerticalReviewConfig):
            raise TypeError("review must be a VerticalReviewConfig")
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise ValueError("prompt_version must be a non-empty string")
        path = _absolute(Path(self.prompt_path))
        if not _is_relative_to(path, self.review.fixture_root):
            raise ValueError("synthetic adjudication prompt must be inside fixture_root")
        _require_plain_file(path, "synthetic adjudication prompt")
        object.__setattr__(self, "prompt_path", path)


@dataclass(frozen=True)
class AdjudicationRequest:
    """Validated final-report adjudicator input assembled from current contracts."""

    plan: StatementPlan
    inputs: ReviewInputValidation
    prompt_text: str
    prompt_version: str
    prompt_sha256: str
    reviews: Mapping[Reviewer, CompleteReviewResult]
    confirmed_sets: Mapping[Reviewer, ConfirmedReviewSet]

    def __post_init__(self) -> None:
        reviews = dict(self.reviews)
        confirmed = dict(self.confirmed_sets)
        object.__setattr__(self, "reviews", MappingProxyType(reviews))
        object.__setattr__(self, "confirmed_sets", MappingProxyType(confirmed))
        if self.plan.action not in {
            PlanAction.RUN_ADJUDICATIONS,
            PlanAction.RECOVER_APPLICATION,
            PlanAction.ADJUDICATION_COMMITTED,
        }:
            raise ValueError("adjudication request requires an adjudication plan")
        if not self.inputs.inputs_complete or self.inputs.statement is None:
            raise ValueError("adjudication request requires complete aligned inputs")
        if (
            self.plan.statement_index != self.inputs.statement_index
            or self.plan.run_id != self.inputs.selected_run_id
        ):
            raise ValueError("adjudication request mixes statement or run identities")
        if set(self.inputs.outputs) != set(BenchmarkModel):
            raise ValueError("adjudication request requires exactly three model outputs")
        if set(reviews) != set(Reviewer) or set(confirmed) != set(Reviewer):
            raise ValueError("adjudication request requires exactly three reviewers")
        if not isinstance(self.prompt_text, str) or not self.prompt_text.strip():
            raise ValueError("adjudication prompt must be non-empty")
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise ValueError("adjudication prompt version must be non-empty")
        if self.prompt_sha256 != hashlib.sha256(
            self.prompt_text.encode("utf-8")
        ).hexdigest():
            raise ValueError("adjudication prompt hash does not match its text")
        for reviewer in Reviewer:
            review = reviews[reviewer]
            active = confirmed[reviewer]
            metadata = active.attempt_metadata
            if (
                review.statement_index != self.inputs.statement_index
                or review.reviewer is not reviewer
                or review.task_id != active.task_id
                or review.attempt_id != active.attempt_id
            ):
                raise ValueError("active reviewer provenance contradicts its result")
            if metadata is None or metadata.run_id != self.inputs.selected_run_id:
                raise ValueError("active reviewer run contradicts the selected run")

    @property
    def reviewer_artifact_count(self) -> int:
        return sum(
            1 + len(review.histories) + len(review.profile_proposals)
            for review in self.reviews.values()
        )


@dataclass(frozen=True)
class AdjudicationBundleRequest:
    adjudication: AdjudicationRequest
    current_profiles: Mapping[BenchmarkModel, Mapping[str, object]]

    def __post_init__(self) -> None:
        profiles = {
            model: MappingProxyType(dict(value))
            for model, value in self.current_profiles.items()
        }
        if set(profiles) != set(BenchmarkModel):
            raise ValueError("bundle request requires exactly three profile preimages")
        for model, profile in profiles.items():
            validate_profile(dict(profile), model)
        object.__setattr__(self, "current_profiles", MappingProxyType(profiles))

    @property
    def statement_index(self) -> int:
        return self.adjudication.inputs.statement_index


@dataclass(frozen=True)
class AdjudicationBundle:
    adjudication: Mapping[str, object]
    histories: Sequence[Mapping[str, object]]
    profiles: Sequence[Mapping[str, object]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "adjudication", MappingProxyType(dict(self.adjudication)))
        object.__setattr__(
            self,
            "histories",
            tuple(MappingProxyType(dict(value)) for value in self.histories),
        )
        object.__setattr__(
            self,
            "profiles",
            tuple(MappingProxyType(dict(value)) for value in self.profiles),
        )


class AdjudicatorAdapter(Protocol):
    def execute(self, request: AdjudicationRequest) -> Mapping[str, object]:
        """Return one candidate using the existing adjudication.json schema."""


class BundleAdjudicatorAdapter(Protocol):
    def execute_bundle(
        self, request: "AdjudicationBundleRequest"
    ) -> Mapping[str, object]:
        """Return an in-memory 1+3+3 response; it is never persisted as a wrapper."""


class DeterministicFakeAdjudicatorAdapter:
    """Offline final-report adjudicator for synthetic fixtures only."""

    def __init__(self, *, category: str = "synthetic-category") -> None:
        if not isinstance(category, str) or not category.strip():
            raise ValueError("category must be a non-empty string")
        self.category = category

    def execute(self, request: AdjudicationRequest) -> Mapping[str, object]:
        classifications = {
            reviewer.value: self.category for reviewer in Reviewer
        }
        statement = request.inputs.statement
        assert statement is not None
        statement_value = statement.get("statement")
        narrative = (
            statement_value.get("report")
            if isinstance(statement_value, Mapping)
            else None
        )
        if not isinstance(narrative, str) or not narrative.strip():
            raise AdjudicatorAdapterError("synthetic statement report is unavailable")
        return {
            "statement_index": request.inputs.statement_index,
            "reviewers": [reviewer.value for reviewer in Reviewer],
            "classification_consensus": {
                "reviewer_classifications": classifications,
                "final_category": self.category,
                "consensus": "unanimous",
                "final_justification": "supported by the synthetic statement fixture",
            },
            "selected_elements": {
                "factuality": ["synthetic statement"],
                "narrative_structure": [],
                "classification": [self.category],
                "discarded_elements": [],
            },
            "final_report": {
                "narrative": narrative,
                "closing": "",
                "crime_classification": {
                    "category": self.category,
                    "justification": "supported by the synthetic statement fixture",
                },
            },
            "quality_control": {
                "factual_consistency": "pass",
                "unsupported_information": "pass",
                "relevant_information_preserved": "pass",
                "temporal_consistency": "pass",
                "spatial_consistency": "pass",
                "third_person": "pass",
                "institutional_language": "pass",
                "classification_supported": "pass",
                "narrative_classification_consistency": "pass",
                "format_valid": "pass",
            },
            "decision": {
                "status": "approved",
                "reason": "deterministic synthetic adjudication",
            },
        }

    def execute_bundle(
        self, request: AdjudicationBundleRequest
    ) -> Mapping[str, object]:
        """Build deterministic mono-statement histories and accumulated profiles."""

        adjudication = self.execute(request.adjudication)
        histories = []
        for model in BenchmarkModel:
            observations = []
            date = None
            for reviewer in Reviewer:
                review = request.adjudication.reviews[reviewer]
                proposal = next(item for item in review.histories if item.model is model)
                date = proposal.output.get("date", date)
                for observation in proposal.output.get("observations", []):
                    if observation not in observations:
                        observations.append(observation)
            histories.append(
                {
                    "model": model.value,
                    "output": request.statement_index,
                    "date": date or "30/10/2026",
                    "observations": observations,
                }
            )

        profiles = []
        for model in BenchmarkModel:
            profile = json.loads(
                json.dumps(dict(request.current_profiles[model]), ensure_ascii=False)
            )
            metadata = profile["profile_metadata"]
            metadata["outputs_reviewed"] = request.statement_index + 1
            metadata["last_updated"] = "30/10/2026"
            metadata["version"] += 1
            profiles.append(profile)
        return {
            "adjudication": adjudication,
            "histories": histories,
            "profiles": profiles,
            "ADJUDICATION_NOTES": "synthetic notes are intentionally discarded",
        }


@dataclass(frozen=True)
class ReviewerPreparationEvidence:
    task_id: str
    attempt_id: str
    set_id: str
    run_id: str
    artifact_count: int = 7


@dataclass(frozen=True)
class PreparedAdjudicationEvidence:
    statement_index: int
    run_id: str
    prompt_version: str
    prompt_sha256: str
    adjudication_sha256: str
    reviewers: Mapping[Reviewer, ReviewerPreparationEvidence]
    reviewer_artifact_count: int
    persisted: bool = False

    def __post_init__(self) -> None:
        reviewers = dict(self.reviewers)
        object.__setattr__(self, "reviewers", MappingProxyType(reviewers))
        if set(reviewers) != set(Reviewer):
            raise ValueError("preparation evidence requires exactly three reviewers")
        if self.reviewer_artifact_count != 21:
            raise ValueError("preparation evidence requires exactly 21 reviewer artifacts")
        if self.persisted:
            raise ValueError("synthetic prepared adjudication must not be persisted")


@dataclass(frozen=True)
class PreparedAdjudication:
    fixture_root: Path
    adjudication: Mapping[str, object]
    evidence: PreparedAdjudicationEvidence

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "adjudication", MappingProxyType(dict(self.adjudication))
        )


@dataclass(frozen=True)
class SyntheticAdjudicationResult:
    status: SyntheticAdjudicationStatus
    reason: str
    plan: Optional[StatementPlan] = None
    request: Optional[AdjudicationRequest] = None
    prepared: Optional[PreparedAdjudication] = None
    bundle: Optional[AdjudicationBundle] = None
    application: Optional[AdjudicationApplication] = None
    lease_owner: Optional[LeaseOwner] = None


@dataclass(frozen=True)
class SyntheticAdjudicationFlowResult:
    reviewer_results: Sequence[VerticalReviewResult]
    adjudication: SyntheticAdjudicationResult

    def __post_init__(self) -> None:
        object.__setattr__(self, "reviewer_results", tuple(self.reviewer_results))


def run_synthetic_three_reviewers_and_adjudicate(
    config: SyntheticAdjudicationConfig,
    reviewer_adapter: ReviewerAdapter,
    adjudicator_adapter: AdjudicatorAdapter,
) -> SyntheticAdjudicationFlowResult:
    """Publish any missing fake reviewers, then prepare one adjudication."""

    results: list[VerticalReviewResult] = []
    for _ in Reviewer:
        current = plan_next_statement(config.review.coordinator.workflow)
        if current.action is PlanAction.RUN_ADJUDICATIONS:
            break
        # The reviewer entry point reconstructs and protects O-01 itself; this
        # read-only check only prevents calling it after the barrier is reached.
        result = run_synthetic_reviewer_once(config.review, reviewer_adapter)
        results.append(result)
        if result.status is not VerticalReviewStatus.COMPLETED:
            return SyntheticAdjudicationFlowResult(
                results,
                SyntheticAdjudicationResult(
                    SyntheticAdjudicationStatus.BLOCKED,
                    "reviewer flow did not reach the adjudication barrier",
                    plan=result.after_plan or result.before_plan,
                ),
            )
        if result.after_plan is not None and (
            result.after_plan.action is PlanAction.RUN_ADJUDICATIONS
        ):
            break
    return SyntheticAdjudicationFlowResult(
        results, run_synthetic_adjudication_once(config, adjudicator_adapter)
    )


def run_synthetic_adjudication_once(
    config: SyntheticAdjudicationConfig,
    adapter: AdjudicatorAdapter,
) -> SyntheticAdjudicationResult:
    """Prepare and validate one final-report adjudication without persisting it."""

    if not isinstance(config, SyntheticAdjudicationConfig):
        raise TypeError("config must be a SyntheticAdjudicationConfig")
    if not callable(getattr(adapter, "execute", None)):
        raise TypeError("adapter must implement execute(request)")
    coordinator = config.review.coordinator
    lease = WorkflowLease(coordinator.lease_path, coordinator.owner_id)
    try:
        owner = lease.acquire(blocking=False)
    except WorkflowLeaseInUseError as error:
        return SyntheticAdjudicationResult(
            SyntheticAdjudicationStatus.CONTENDED, str(error), lease_owner=error.owner
        )
    except WorkflowLeaseError as error:
        return SyntheticAdjudicationResult(
            SyntheticAdjudicationStatus.VALIDATION_FAILED, str(error)
        )

    result: Optional[SyntheticAdjudicationResult] = None
    try:
        coordinated = coordinate_workflow_under_lease(coordinator, lease)
        if coordinated.status is CoordinationStatus.STALE_PLAN:
            result = SyntheticAdjudicationResult(
                SyntheticAdjudicationStatus.STALE_PLAN,
                coordinated.reason,
                coordinated.plan,
                lease_owner=owner,
            )
        elif coordinated.status is not CoordinationStatus.ACTION_REQUIRED:
            status = (
                SyntheticAdjudicationStatus.BLOCKED
                if coordinated.status is CoordinationStatus.BLOCKED
                else SyntheticAdjudicationStatus.VALIDATION_FAILED
            )
            result = SyntheticAdjudicationResult(
                status, coordinated.reason, coordinated.plan, lease_owner=owner
            )
        elif (
            coordinated.plan is None
            or coordinated.plan.action is not PlanAction.RUN_ADJUDICATIONS
        ):
            result = SyntheticAdjudicationResult(
                SyntheticAdjudicationStatus.BLOCKED,
                "O-01 has not authorized adjudication preparation",
                coordinated.plan,
                lease_owner=owner,
            )
        else:
            request = _build_request(config, coordinated.plan)
            immediate = confirm_workflow_expectation_under_lease(
                coordinator, lease, coordinated.expectation()
            )
            if immediate.status is CoordinationStatus.STALE_PLAN:
                result = SyntheticAdjudicationResult(
                    SyntheticAdjudicationStatus.STALE_PLAN,
                    immediate.reason,
                    coordinated.plan,
                    request=request,
                    lease_owner=owner,
                )
            elif (
                immediate.status is not CoordinationStatus.ACTION_REQUIRED
                or immediate.plan != coordinated.plan
            ):
                result = SyntheticAdjudicationResult(
                    SyntheticAdjudicationStatus.VALIDATION_FAILED,
                    immediate.reason,
                    coordinated.plan,
                    request=request,
                    lease_owner=owner,
                )
            else:
                try:
                    candidate = adapter.execute(request)
                except AdjudicatorAdapterError:
                    result = SyntheticAdjudicationResult(
                        SyntheticAdjudicationStatus.ADJUDICATOR_FAILED,
                        "synthetic adjudicator failed before producing a result",
                        coordinated.plan,
                        request=request,
                        lease_owner=owner,
                    )
                except Exception:
                    result = SyntheticAdjudicationResult(
                        SyntheticAdjudicationStatus.ADJUDICATOR_FAILED,
                        "synthetic adjudicator failed unexpectedly",
                        coordinated.plan,
                        request=request,
                        lease_owner=owner,
                    )
                else:
                    try:
                        adjudication = _validated_adjudication(
                            candidate, request.inputs.statement_index
                        )
                    except (TypeError, ValueError) as error:
                        result = SyntheticAdjudicationResult(
                            SyntheticAdjudicationStatus.OUTPUT_REJECTED,
                            f"synthetic adjudication was rejected: {error}",
                            coordinated.plan,
                            request=request,
                            lease_owner=owner,
                        )
                    else:
                        final_check = confirm_workflow_expectation_under_lease(
                            coordinator, lease, coordinated.expectation()
                        )
                        if (
                            final_check.status is not CoordinationStatus.ACTION_REQUIRED
                            or final_check.plan != coordinated.plan
                        ):
                            result = SyntheticAdjudicationResult(
                                SyntheticAdjudicationStatus.STALE_PLAN,
                                "workflow inputs changed while adjudication was prepared",
                                coordinated.plan,
                                request=request,
                                lease_owner=owner,
                            )
                        else:
                            lease.validate()
                            prepared = _prepared_result(config, request, adjudication)
                            result = SyntheticAdjudicationResult(
                                SyntheticAdjudicationStatus.PREPARED,
                                "final-report adjudication prepared and validated in memory; no result was persisted",
                                coordinated.plan,
                                request=request,
                                prepared=prepared,
                                lease_owner=owner,
                            )
    except (OSError, TypeError, ValueError) as error:
        result = SyntheticAdjudicationResult(
            SyntheticAdjudicationStatus.VALIDATION_FAILED,
            f"synthetic adjudication preparation failed validation: {error}",
            lease_owner=owner,
        )
    finally:
        try:
            lease.release()
        except WorkflowLeaseError as error:
            result = SyntheticAdjudicationResult(
                SyntheticAdjudicationStatus.VALIDATION_FAILED,
                f"workflow lease could not be released safely: {error}",
                plan=None if result is None else result.plan,
                request=None if result is None else result.request,
                lease_owner=owner,
            )
    assert result is not None
    return result


def run_synthetic_three_reviewers_and_persist(
    config: SyntheticAdjudicationConfig,
    reviewer_adapter: ReviewerAdapter,
    adjudicator_adapter: BundleAdjudicatorAdapter,
    *,
    failure_injector: Optional[Callable[[str], None]] = None,
) -> SyntheticAdjudicationFlowResult:
    """Publish missing fake reviewers, then persist the scoped OLBM-31 result."""

    results: list[VerticalReviewResult] = []
    for _ in Reviewer:
        current = plan_next_statement(config.review.coordinator.workflow)
        if current.action in {
            PlanAction.RUN_ADJUDICATIONS,
            PlanAction.RECOVER_APPLICATION,
            PlanAction.ADJUDICATION_COMMITTED,
        }:
            break
        review_result = run_synthetic_reviewer_once(config.review, reviewer_adapter)
        results.append(review_result)
        if review_result.status is not VerticalReviewStatus.COMPLETED:
            return SyntheticAdjudicationFlowResult(
                results,
                SyntheticAdjudicationResult(
                    SyntheticAdjudicationStatus.BLOCKED,
                    "reviewer flow did not reach the adjudication barrier",
                    plan=review_result.after_plan or review_result.before_plan,
                ),
            )
    return SyntheticAdjudicationFlowResult(
        results,
        run_synthetic_adjudication_and_persist_once(
            config,
            adjudicator_adapter,
            failure_injector=failure_injector,
        ),
    )


def run_synthetic_adjudication_and_persist_once(
    config: SyntheticAdjudicationConfig,
    adapter: BundleAdjudicatorAdapter,
    *,
    expected: Optional[CoordinationExpectation] = None,
    failure_injector: Optional[Callable[[str], None]] = None,
) -> SyntheticAdjudicationResult:
    """Validate a 1+3+3 bundle and commit only its final adjudication."""

    if not isinstance(config, SyntheticAdjudicationConfig):
        raise TypeError("config must be a SyntheticAdjudicationConfig")
    if not callable(getattr(adapter, "execute_bundle", None)):
        raise TypeError("adapter must implement execute_bundle(request)")
    coordinator = config.review.coordinator
    lease = WorkflowLease(coordinator.lease_path, coordinator.owner_id)
    try:
        owner = lease.acquire(blocking=False)
    except WorkflowLeaseInUseError as error:
        return SyntheticAdjudicationResult(
            SyntheticAdjudicationStatus.CONTENDED, str(error), lease_owner=error.owner
        )
    except WorkflowLeaseError as error:
        return SyntheticAdjudicationResult(
            SyntheticAdjudicationStatus.VALIDATION_FAILED, str(error)
        )

    result: Optional[SyntheticAdjudicationResult] = None
    try:
        coordinated = coordinate_workflow_under_lease(coordinator, lease, expected=expected)
        if coordinated.status is CoordinationStatus.STALE_PLAN:
            result = SyntheticAdjudicationResult(
                SyntheticAdjudicationStatus.STALE_PLAN,
                coordinated.reason,
                coordinated.plan,
                lease_owner=owner,
            )
        elif coordinated.status is not CoordinationStatus.ACTION_REQUIRED:
            result = SyntheticAdjudicationResult(
                SyntheticAdjudicationStatus.BLOCKED,
                coordinated.reason,
                coordinated.plan,
                lease_owner=owner,
            )
        elif (
            coordinated.plan is None
            or coordinated.plan.action not in {
                PlanAction.RUN_ADJUDICATIONS,
                PlanAction.RECOVER_APPLICATION,
                PlanAction.ADJUDICATION_COMMITTED,
            }
        ):
            result = SyntheticAdjudicationResult(
                SyntheticAdjudicationStatus.BLOCKED,
                "O-01 has not authorized adjudication persistence",
                coordinated.plan,
                lease_owner=owner,
            )
        else:
            request = _build_request(config, coordinated.plan)
            existing = None
            if coordinated.plan.action is not PlanAction.RUN_ADJUDICATIONS:
                existing = _verified_pending_application(config, request)
            if coordinated.plan.action is PlanAction.ADJUDICATION_COMMITTED:
                immediate = confirm_workflow_expectation_under_lease(
                    coordinator, lease, coordinated.expectation()
                )
                if (
                    immediate.status is not CoordinationStatus.ACTION_REQUIRED
                    or immediate.plan != coordinated.plan
                ):
                    result = SyntheticAdjudicationResult(
                        SyntheticAdjudicationStatus.STALE_PLAN,
                        "workflow inputs changed before committed adjudication was verified",
                        coordinated.plan,
                        request=request,
                        lease_owner=owner,
                    )
                else:
                    lease.validate()
                    result = SyntheticAdjudicationResult(
                        SyntheticAdjudicationStatus.UNCHANGED,
                        "final adjudication was already committed and verified; incremental application remains pending",
                        coordinated.plan,
                        request=request,
                        application=existing,
                        lease_owner=owner,
                    )
            else:
                bundle_request = _build_bundle_request(config, request)
                try:
                    raw_bundle = adapter.execute_bundle(bundle_request)
                except AdjudicatorAdapterError:
                    result = SyntheticAdjudicationResult(
                        SyntheticAdjudicationStatus.ADJUDICATOR_FAILED,
                        "synthetic adjudicator failed before producing a bundle",
                        coordinated.plan,
                        request=request,
                        lease_owner=owner,
                    )
                except Exception:
                    result = SyntheticAdjudicationResult(
                        SyntheticAdjudicationStatus.ADJUDICATOR_FAILED,
                        "synthetic adjudicator failed unexpectedly",
                        coordinated.plan,
                        request=request,
                        lease_owner=owner,
                    )
                else:
                    try:
                        bundle = _validated_bundle(raw_bundle, request.inputs.statement_index)
                    except (TypeError, ValueError) as error:
                        result = SyntheticAdjudicationResult(
                            SyntheticAdjudicationStatus.OUTPUT_REJECTED,
                            f"synthetic adjudication bundle was rejected: {error}",
                            coordinated.plan,
                            request=request,
                            lease_owner=owner,
                        )
                    else:
                        immediate = confirm_workflow_expectation_under_lease(
                            coordinator, lease, coordinated.expectation()
                        )
                        if (
                            immediate.status is not CoordinationStatus.ACTION_REQUIRED
                            or immediate.plan != coordinated.plan
                        ):
                            result = SyntheticAdjudicationResult(
                                SyntheticAdjudicationStatus.STALE_PLAN,
                                "workflow inputs changed before adjudication commit",
                                coordinated.plan,
                                request=request,
                                bundle=bundle,
                                lease_owner=owner,
                            )
                        else:
                            lease.validate()
                            workflow = coordinator.workflow
                            statement_path = workflow.review_cases_root / (
                                f"statement_{request.inputs.statement_index:03d}"
                            )
                            commit = commit_adjudication_bundle(
                                statement_index=request.inputs.statement_index,
                                statement_path=statement_path,
                                feedback_root=workflow.feedback_root,
                                profiles_root=workflow.profiles_root,
                                adjudication=bundle.adjudication,
                                histories=bundle.histories,
                                profiles=bundle.profiles,
                                run_id=request.inputs.selected_run_id,
                                review_attempts={
                                    reviewer: {
                                        "task_id": request.reviews[reviewer].task_id,
                                        "attempt_id": request.reviews[reviewer].attempt_id,
                                    }
                                    for reviewer in Reviewer
                                },
                                failure_injector=failure_injector,
                            )
                            lease.validate()
                            status = (
                                SyntheticAdjudicationStatus.UNCHANGED
                                if commit.status is AdjudicationCommitStatus.UNCHANGED
                                else SyntheticAdjudicationStatus.PERSISTED
                            )
                            result = SyntheticAdjudicationResult(
                                status,
                                "final adjudication is committed and verifiable; accumulated histories and profiles remain unchanged",
                                coordinated.plan,
                                request=request,
                                bundle=bundle,
                                application=commit.application,
                                lease_owner=owner,
                            )
    except (OSError, TypeError, ValueError) as error:
        result = SyntheticAdjudicationResult(
            SyntheticAdjudicationStatus.VALIDATION_FAILED,
            f"synthetic adjudication persistence failed validation: {error}",
            lease_owner=owner,
        )
    finally:
        try:
            lease.release()
        except WorkflowLeaseError as error:
            result = SyntheticAdjudicationResult(
                SyntheticAdjudicationStatus.VALIDATION_FAILED,
                f"workflow lease could not be released safely: {error}",
                plan=None if result is None else result.plan,
                request=None if result is None else result.request,
                bundle=None if result is None else result.bundle,
                application=None if result is None else result.application,
                lease_owner=owner,
            )
    assert result is not None
    return result


def _verified_pending_application(
    config: SyntheticAdjudicationConfig,
    request: AdjudicationRequest,
) -> AdjudicationApplication:
    plan = request.plan
    if plan.application_path is None:
        raise ValueError("adjudication recovery plan has no application path")
    workflow = config.review.coordinator.workflow
    statement_path = workflow.review_cases_root / f"statement_{request.inputs.statement_index:03d}"
    application = inspect_adjudication_application(
        application_path=plan.application_path,
        statement_path=statement_path,
        feedback_root=workflow.feedback_root,
        profiles_root=workflow.profiles_root,
    )
    expected_phase = (
        {ApplicationPhase.PREPARED, ApplicationPhase.INTERRUPTED}
        if plan.action is PlanAction.RECOVER_APPLICATION
        else {ApplicationPhase.ADJUDICATION_COMMITTED}
    )
    if application.phase not in expected_phase:
        raise ValueError("adjudication application phase changed or is invalid")
    if (
        application.statement_index != request.inputs.statement_index
        or application.run_id != request.inputs.selected_run_id
        or plan.run_id != application.run_id
        or set(application.review_attempts) != set(Reviewer)
    ):
        raise ValueError("adjudication application statement or run identity contradicts current inputs")
    for reviewer in Reviewer:
        identity = application.review_attempts[reviewer]
        active = request.confirmed_sets[reviewer]
        if (
            identity.get("task_id") != active.task_id
            or identity.get("attempt_id") != active.attempt_id
        ):
            raise ValueError("adjudication application reviewer attempt contradicts active review")
    return application


def _build_request(
    config: SyntheticAdjudicationConfig, plan: StatementPlan
) -> AdjudicationRequest:
    workflow = config.review.coordinator.workflow
    inputs = inspect_review_inputs(
        statement_index=config.review.statement_index,
        statements_path=workflow.statements_path,
        runs_root=workflow.runs_root,
        run=workflow.run,
    )
    inspection = inspect_reviewer_results(
        statement_index=config.review.statement_index,
        review_cases_root=workflow.review_cases_root,
    )
    if not inspection.adjudication_enabled:
        raise ValueError("three complete reviewer sets are required")
    statement_path = workflow.review_cases_root / (
        f"statement_{config.review.statement_index:03d}"
    )
    reviews: dict[Reviewer, CompleteReviewResult] = {}
    confirmed: dict[Reviewer, ConfirmedReviewSet] = {}
    for reviewer in Reviewer:
        inspected = inspection.reviewers[reviewer]
        if (
            inspected.status is not ReviewerStatus.COMPLETE
            or inspected.complete_result is None
        ):
            raise ValueError(f"reviewer {reviewer.value} is not complete")
        active, error = resolve_confirmed_review_set(
            statement_path=statement_path,
            statement_index=config.review.statement_index,
            reviewer=reviewer,
        )
        if active is None:
            raise ValueError(
                f"reviewer {reviewer.value} has no confirmed set: {error or 'missing'}"
            )
        reviews[reviewer] = inspected.complete_result
        confirmed[reviewer] = active
    prompt_text = config.prompt_path.read_text(encoding="utf-8")
    return AdjudicationRequest(
        plan=plan,
        inputs=inputs,
        prompt_text=prompt_text,
        prompt_version=config.prompt_version,
        prompt_sha256=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        reviews=reviews,
        confirmed_sets=confirmed,
    )


def _build_bundle_request(
    config: SyntheticAdjudicationConfig,
    request: AdjudicationRequest,
) -> AdjudicationBundleRequest:
    profiles = {}
    root = config.review.coordinator.workflow.profiles_root
    for model in BenchmarkModel:
        path = root / f"{model.value}_profile.json"
        _require_plain_file(path, f"profile preimage for {model.value}")
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"profile preimage for {model.value} must be an object")
        profiles[model] = value
    return AdjudicationBundleRequest(request, profiles)


def _validated_bundle(candidate: object, statement_index: int) -> AdjudicationBundle:
    if not isinstance(candidate, Mapping):
        raise TypeError("bundle adjudicator must return a mapping")
    allowed = {"adjudication", "histories", "profiles", "ADJUDICATION_NOTES"}
    if set(candidate) - allowed or not {"adjudication", "histories", "profiles"}.issubset(
        candidate
    ):
        raise ValueError("bundle must contain exactly adjudication, histories, and profiles")
    try:
        value = json.loads(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError) as error:
        raise ValueError("adjudication bundle must be JSON serializable") from error
    # D-034: notes are transport-only and must not enter any persisted artifact.
    value.pop("ADJUDICATION_NOTES", None)
    adjudication = _validated_adjudication(value["adjudication"], statement_index)
    histories = value["histories"]
    profiles = value["profiles"]
    if not isinstance(histories, list) or len(histories) != len(BenchmarkModel):
        raise ValueError("bundle requires exactly three histories")
    if not isinstance(profiles, list) or len(profiles) != len(BenchmarkModel):
        raise ValueError("bundle requires exactly three profiles")

    history_models = set()
    for history in histories:
        if not isinstance(history, dict):
            raise ValueError("bundle history must be an object")
        try:
            model = BenchmarkModel(history.get("model"))
        except (TypeError, ValueError) as error:
            raise ValueError("bundle history has an invalid model") from error
        if model in history_models:
            raise ValueError(f"bundle history duplicates {model.value}")
        validate_history(history, model, statement_index)
        history_models.add(model)

    profile_models = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("bundle profile must be an object")
        try:
            model = BenchmarkModel(profile.get("model_name"))
        except (TypeError, ValueError) as error:
            raise ValueError("bundle profile has an invalid model") from error
        if model in profile_models:
            raise ValueError(f"bundle profile duplicates {model.value}")
        validate_profile(profile, model)
        profile_models.add(model)
    if history_models != set(BenchmarkModel) or profile_models != set(BenchmarkModel):
        raise ValueError("bundle must cover all benchmark models exactly once")
    return AdjudicationBundle(adjudication, histories, profiles)


def _validated_adjudication(
    candidate: object, statement_index: int
) -> dict[str, object]:
    if not isinstance(candidate, Mapping):
        raise TypeError("adjudicator must return a mapping")
    try:
        value = json.loads(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError) as error:
        raise ValueError("adjudication result must be JSON serializable") from error
    validate_adjudication(value, statement_index)
    return value


def _prepared_result(
    config: SyntheticAdjudicationConfig,
    request: AdjudicationRequest,
    adjudication: Mapping[str, object],
) -> PreparedAdjudication:
    digest = hashlib.sha256(
        json.dumps(
            adjudication,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    reviewer_evidence = {
        reviewer: ReviewerPreparationEvidence(
            task_id=request.reviews[reviewer].task_id,
            attempt_id=request.reviews[reviewer].attempt_id,
            set_id=request.confirmed_sets[reviewer].set_id,
            run_id=request.confirmed_sets[reviewer].attempt_metadata.run_id,
        )
        for reviewer in Reviewer
    }
    evidence = PreparedAdjudicationEvidence(
        statement_index=request.inputs.statement_index,
        run_id=request.inputs.selected_run_id,
        prompt_version=request.prompt_version,
        prompt_sha256=request.prompt_sha256,
        adjudication_sha256=digest,
        reviewers=reviewer_evidence,
        reviewer_artifact_count=request.reviewer_artifact_count,
    )
    return PreparedAdjudication(config.review.fixture_root, adjudication, evidence)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_plain_file(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        inspected = current.lstat()
        if stat.S_ISLNK(inspected.st_mode):
            raise ValueError(f"{label} must not contain symbolic links: {current}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"{label} must be a regular file")


__all__ = [
    "AdjudicationBundle",
    "AdjudicationBundleRequest",
    "AdjudicationRequest",
    "AdjudicatorAdapter",
    "AdjudicatorAdapterError",
    "BundleAdjudicatorAdapter",
    "DeterministicFakeAdjudicatorAdapter",
    "PreparedAdjudication",
    "PreparedAdjudicationEvidence",
    "ReviewerPreparationEvidence",
    "SyntheticAdjudicationConfig",
    "SyntheticAdjudicationFlowResult",
    "SyntheticAdjudicationResult",
    "SyntheticAdjudicationStatus",
    "run_synthetic_adjudication_once",
    "run_synthetic_adjudication_and_persist_once",
    "run_synthetic_three_reviewers_and_adjudicate",
    "run_synthetic_three_reviewers_and_persist",
]
