"""Validation of synthetic web observations before the existing reviewer result.

Hashes establish local integrity, never external provider authorship.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from automation.browser.web_interaction import FAKE_REVIEW_SLOTS, ObservedResponse, WebResponseState
from automation.contracts import BenchmarkModel, CompleteReviewResult, HistoryProposalOutput, MainReviewOutput, ProfileProposalOutput, utc_now
from .vertical_flow import ReviewerAdapterError, ReviewerExecutionRequest


class CaptureStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNCERTAIN = "UNCERTAIN"


class CaptureError(ReviewerAdapterError):
    """Stable code without page text or other untrusted content."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Synthetic capture rejected ({code}).")


@dataclass(frozen=True)
class CapturedComponent:
    slot: str
    output: dict
    source: str
    observed_sha256: str
    declared_provenance: str
    observed_at: str
    request_sha256: str
    integrity: str = "local_hash_validated"
    provider_authenticity: str = "UNVERIFIED"
    state: CaptureStatus = CaptureStatus.COMPLETE


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CaptureError("duplicate_json_key")
        result[key] = value
    return result


def _non_finite(_value):
    raise CaptureError("non_finite_json")


def _decode(content: str) -> dict:
    try:
        if len(content.encode("utf-8")) > 2 * 1024 * 1024:
            raise CaptureError("capture_too_large")
        value = json.loads(content, object_pairs_hook=_object, parse_constant=_non_finite)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise CaptureError("invalid_json") from None
    if not isinstance(value, dict):
        raise CaptureError("invalid_structure")
    return value


def _identity(request: ReviewerExecutionRequest) -> dict:
    if request.plan.run_id != request.inputs.selected_run_id:
        raise CaptureError("request_run_mismatch")
    return {
        "task_id": request.task.task_id,
        "attempt_id": request.attempt.attempt_id,
        "statement_index": request.inputs.statement_index,
        "reviewer": request.task.reviewer.value,
        "run_id": request.inputs.selected_run_id,
    }


def _check_output(output: dict, identity: dict) -> None:
    if not isinstance(output, dict) or "web_capture" in output:
        raise CaptureError("invalid_output")
    for key, expected in identity.items():
        if key == "run_id" and key not in output:
            continue
        if type(output.get(key)) is not type(expected) or output.get(key) != expected:
            raise CaptureError("identity_mismatch")


def _check_observation(observation: ObservedResponse) -> str:
    if observation.state is WebResponseState.UNCERTAIN:
        raise CaptureError("uncertain_capture")
    if observation.state is not WebResponseState.COMPLETE or not observation.content:
        raise CaptureError("partial_capture")
    digest = hashlib.sha256(observation.content.encode("utf-8")).hexdigest()
    if digest != observation.content_sha256:
        raise CaptureError("observation_hash_mismatch")
    return digest


def capture_separate(request: ReviewerExecutionRequest, observations: dict[str, ObservedResponse]) -> tuple[CapturedComponent, ...]:
    identity = _identity(request)
    if set(observations) != set(FAKE_REVIEW_SLOTS):
        raise CaptureError("missing_or_extra_slot")
    components = []
    for slot in FAKE_REVIEW_SLOTS:
        observation = observations[slot]
        digest = _check_observation(observation)
        output = _decode(observation.content)
        _check_output(output, identity)
        components.append(CapturedComponent(slot, output, f"fake_route:{request.task.reviewer.value}/{slot}",
                                            digest, "synthetic_loopback_page",
                                            observation.observed_at.isoformat(), observation.request_sha256))
    return tuple(components)


def capture_structured(request: ReviewerExecutionRequest, observation: ObservedResponse) -> tuple[CapturedComponent, ...]:
    identity = _identity(request)
    _check_observation(observation)
    document = _decode(observation.content)
    if set(document) != {"schema_version", "components"} or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise CaptureError("structure_changed")
    sections = document["components"]
    if not isinstance(sections, list) or len(sections) != 7:
        raise CaptureError("missing_or_extra_slot")
    components = []
    seen = set()
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"slot", "output", "content_sha256", "identity"}:
            raise CaptureError("ambiguous_section")
        slot = section["slot"]
        if not isinstance(slot, str) or slot not in FAKE_REVIEW_SLOTS:
            raise CaptureError("unknown_slot")
        if slot in seen:
            raise CaptureError("duplicate_slot")
        seen.add(slot)
        if not isinstance(section["identity"], dict) or set(section["identity"]) != set(identity) or any(
            type(section["identity"][key]) is not type(value) or section["identity"][key] != value
            for key, value in identity.items()
        ):
            raise CaptureError("identity_mismatch")
        output = section["output"]
        _check_output(output, identity)
        try:
            canonical = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise CaptureError("invalid_output") from None
        digest = hashlib.sha256(canonical).hexdigest()
        if section["content_sha256"] != digest:
            raise CaptureError("section_hash_mismatch")
        components.append(CapturedComponent(slot, output, f"fake_bundle:{request.task.reviewer.value}#{slot}",
                                            digest, "synthetic_loopback_page",
                                            observation.observed_at.isoformat(), observation.request_sha256))
    if seen != set(FAKE_REVIEW_SLOTS):
        raise CaptureError("missing_or_extra_slot")
    return tuple(components)


def result_from_capture(request: ReviewerExecutionRequest, components: tuple[CapturedComponent, ...]) -> CompleteReviewResult:
    identity = _identity(request)
    if len(components) != 7 or {item.slot for item in components} != set(FAKE_REVIEW_SLOTS):
        raise CaptureError("missing_or_extra_slot")
    outputs = {}
    for item in components:
        if item.state is not CaptureStatus.COMPLETE or item.integrity != "local_hash_validated" or item.provider_authenticity != "UNVERIFIED":
            raise CaptureError("unconfirmed_capture")
        _check_output(item.output, identity)
        outputs[item.slot] = {**item.output, "web_capture": {
            "source": item.source, "state": item.state.value,
            "observed_sha256": item.observed_sha256,
            "observed_at": item.observed_at, "request_sha256": item.request_sha256,
            "declared_provenance": item.declared_provenance,
            "integrity": item.integrity, "provider_authenticity": item.provider_authenticity,
            "run_id": identity["run_id"],
        }}
    index, reviewer, attempt = identity["statement_index"], request.task.reviewer, identity["attempt_id"]
    try:
        main = MainReviewOutput(index, reviewer, attempt, outputs["main"])
        histories = tuple(HistoryProposalOutput(index, reviewer, attempt, model, outputs[f"history_{model.value}"]) for model in BenchmarkModel)
        profiles = tuple(ProfileProposalOutput(index, reviewer, attempt, model, outputs[f"profile_{model.value}"]) for model in BenchmarkModel)
        return CompleteReviewResult(identity["task_id"], attempt, index, reviewer, main, histories, profiles, utc_now())
    except (KeyError, ValueError, TypeError):
        raise CaptureError("review_validation_failed") from None
