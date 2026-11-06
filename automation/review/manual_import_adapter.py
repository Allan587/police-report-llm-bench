"""Read-only local manual package adapter. Provider authenticity is unverified."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from datetime import datetime
from pathlib import Path

from automation.contracts import (
    BenchmarkModel, CompleteReviewResult, HistoryProposalOutput, MainReviewOutput,
    ProfileProposalOutput, utc_now,
)
from .path_safety import confined_path
from .vertical_flow import ReviewerAdapterError, ReviewerExecutionRequest


MANUAL_REVIEW_SLOTS = (
    "main", "history_gemma", "history_mistral", "history_llama",
    "profile_gemma", "profile_mistral", "profile_llama",
)
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 64
_IDENTITY_FIELDS = {"task_id", "attempt_id", "statement_index", "reviewer", "run_id"}
_COMPONENT_FIELDS = _IDENTITY_FIELDS | {
    "slot", "output", "content_sha256", "declared_provenance", "observed_at", "state",
}


class ManualReviewImportError(ReviewerAdapterError):
    """Sanitized, stable error code; never includes untrusted package content."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Manual review import rejected ({code}).")


def _reject(code: str) -> None:
    raise ManualReviewImportError(code)


def manual_content_sha256(output: dict) -> str:
    """SHA-256 of UTF-8, sorted compact JSON plus LF (same encoding as v2)."""
    return hashlib.sha256((json.dumps(
        output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ) + "\n").encode("utf-8")).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate_json_key")
        result[key] = value
    return result


def _finite_constant(_: str) -> None:
    _reject("non_finite_json")


def _check_depth(value: object, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        _reject("excessive_depth")
    if isinstance(value, float) and not math.isfinite(value):
        _reject("non_finite_json")
    if isinstance(value, str):
        value.encode("utf-8")  # Reject escaped lone surrogates too.
    if isinstance(value, dict):
        for key in value:
            key.encode("utf-8")
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for child in children:
        _check_depth(child, depth + 1)


def _read_package(package_path: Path, allowed_root: Path) -> dict:
    try:
        root = allowed_root.expanduser().absolute()
        # Check ancestors too: a symlink above the authorized root is ambiguous.
        for ancestor in (root, *root.parents):
            if ancestor.is_symlink():
                _reject("unsafe_path")
        if not root.is_dir():
            _reject("unsafe_path")
        path = package_path if package_path.is_absolute() else root / package_path
        path = confined_path(path, root, label="manual package")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                _reject("not_regular_file")
            if info.st_size > MAX_PACKAGE_BYTES:
                _reject("package_too_large")
            content = handle.read(MAX_PACKAGE_BYTES + 1)
        if len(content) > MAX_PACKAGE_BYTES:
            _reject("package_too_large")
    except (OSError, ValueError):
        _reject("unreadable_or_unsafe_path")
    try:
        raw = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object,
                         parse_constant=_finite_constant)
        _check_depth(raw)
    except (ValueError, UnicodeError, RecursionError):
        _reject("invalid_json")
    if not isinstance(raw, dict):
        _reject("invalid_package")
    return raw


def _identity_matches(raw: dict, identity: dict) -> bool:
    return all(type(raw.get(key)) is type(expected) and raw[key] == expected
               for key, expected in identity.items())


class ManualImportReviewerAdapter:
    """Validate seven inline components, then return the existing result type.

    No writes, network, file references, prompts, or publication are performed.
    Paths are confined by a pre-operation guard; the inbox must not be writable
    by concurrent hostile processes (same boundary as existing persistence).
    """

    def __init__(self, package_path: Path, *, allowed_root: Path):
        self.package_path = Path(package_path)
        self.allowed_root = Path(allowed_root)

    def execute(self, request: ReviewerExecutionRequest) -> CompleteReviewResult:
        if not isinstance(request, ReviewerExecutionRequest):
            raise TypeError("request must be ReviewerExecutionRequest")
        identity = {
            "task_id": request.task.task_id,
            "attempt_id": request.attempt.attempt_id,
            "statement_index": request.inputs.statement_index,
            "reviewer": request.task.reviewer.value,
            "run_id": request.inputs.selected_run_id,
        }
        if not identity["run_id"] or request.plan.run_id != identity["run_id"]:
            _reject("request_run_mismatch")
        raw = _read_package(self.package_path, self.allowed_root)
        if (set(raw) != _IDENTITY_FIELDS | {"schema_version", "components"}
                or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1):
            _reject("unsupported_package_schema")
        if not _identity_matches(raw, identity):
            _reject("package_identity_mismatch")
        components = raw["components"]
        if not isinstance(components, list) or len(components) != 7:
            _reject("seven_components_required")
        outputs = {}
        for component in components:
            if not isinstance(component, dict) or set(component) != _COMPONENT_FIELDS:
                _reject("invalid_component_schema")
            slot = component["slot"]
            if not isinstance(slot, str) or slot not in MANUAL_REVIEW_SLOTS:
                _reject("invalid_slot")
            if slot in outputs:
                _reject("duplicate_slot")
            if not _identity_matches(component, identity):
                _reject("component_identity_mismatch")
            if component["state"] != "COMPLETE":
                _reject("capture_not_complete")
            observed_at = component["observed_at"]
            try:
                observed = datetime.fromisoformat(observed_at) if isinstance(observed_at, str) else None
                if observed is None or observed.utcoffset() is None:
                    _reject("invalid_observed_at")
            except ValueError:
                _reject("invalid_observed_at")
            provenance = component["declared_provenance"]
            if not isinstance(provenance, str) or not provenance.strip() or len(provenance) > 2048:
                _reject("invalid_declared_provenance")
            output = component["output"]
            output_identity = {key: value for key, value in identity.items() if key != "run_id"}
            if not isinstance(output, dict) or not _identity_matches(output, output_identity):
                _reject("output_identity_mismatch")
            if "run_id" in output and output["run_id"] != identity["run_id"]:
                _reject("output_identity_mismatch")
            if "manual_import" in output:
                _reject("reserved_metadata")
            if component["content_sha256"] != manual_content_sha256(output):
                _reject("content_hash_mismatch")
            # Preserve declarations inside each published JSON, covered by v2 hashes.
            # These are local checks, never an assertion of provider authenticity.
            outputs[slot] = {**output, "manual_import": {
                "source": "operator_supplied", "run_id": identity["run_id"],
                "slot": slot, "state": "COMPLETE", "observed_at": observed_at,
                "declared_provenance": provenance,
                "content_sha256": component["content_sha256"],
                "integrity": "local_hash_validated", "provider_authenticity": "UNVERIFIED",
            }}
        if set(outputs) != set(MANUAL_REVIEW_SLOTS):
            _reject("seven_components_required")
        index, reviewer, attempt = identity["statement_index"], request.task.reviewer, identity["attempt_id"]
        try:
            main = MainReviewOutput(index, reviewer, attempt, outputs["main"])
            histories = tuple(HistoryProposalOutput(index, reviewer, attempt, model,
                              outputs[f"history_{model.value}"]) for model in BenchmarkModel)
            profiles = tuple(ProfileProposalOutput(index, reviewer, attempt, model,
                             outputs[f"profile_{model.value}"]) for model in BenchmarkModel)
            return CompleteReviewResult(identity["task_id"], attempt, index, reviewer,
                                        main, histories, profiles, utc_now())
        except (TypeError, ValueError, KeyError, RecursionError):
            _reject("invalid_review_content")
