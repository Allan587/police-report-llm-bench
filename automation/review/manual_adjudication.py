"""Operator supplied synthetic bundle passed to the existing adjudication port."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation.contracts import BenchmarkModel, Reviewer
from automation.orchestrator import PlanAction, plan_next_statement
from .adjudication_storage import ApplicationPhase, inspect_adjudication_application
from .completeness import StatementCompletionStatus, inspect_statement_completeness
from .manual_import_adapter import _read_package
from .result_inspector import inspect_reviewer_results
from .review_storage import resolve_confirmed_review_set
from .synthetic_adjudication import (
    AdjudicationBundleRequest, BundleAdjudicatorAdapter,
    SyntheticAdjudicationConfig, SyntheticAdjudicationStatus,
    _validated_bundle, run_synthetic_adjudication_and_persist_once,
)
from .synthetic_application import (
    SyntheticApplicationConfig, SyntheticApplicationStatus, run_synthetic_application_once,
)
from .manual_supervisor import ManualSupervisorError
from .vertical_flow import VerticalReviewConfig


def _identity(config: VerticalReviewConfig) -> dict:
    w = config.coordinator.workflow
    statement = w.review_cases_root / f"statement_{config.statement_index:03d}"
    attempts = {}
    run_ids = set()
    for reviewer in Reviewer:
        active, error = resolve_confirmed_review_set(
            statement_path=statement, statement_index=config.statement_index, reviewer=reviewer)
        if active is None or active.attempt_metadata is None:
            raise ManualSupervisorError(f"review_{reviewer.value}_{error or 'incomplete'}")
        attempts[reviewer.value] = {"task_id": active.task_id, "attempt_id": active.attempt_id}
        run_ids.add(active.attempt_metadata.run_id)
    if len(run_ids) != 1:
        raise ManualSupervisorError("review_run_mismatch")
    return {"schema_version": 1, "statement_index": config.statement_index,
            "run_id": run_ids.pop(), "review_attempts": attempts}


def _bundle(raw: dict, identity: dict):
    if set(raw) != set(identity) | {"bundle"} or any(
        type(raw.get(key)) is not type(value) or raw[key] != value
        for key, value in identity.items()
    ):
        raise ManualSupervisorError("adjudication_identity_mismatch")
    candidate = raw["bundle"]
    if not isinstance(candidate, dict) or not {"adjudication", "histories", "profiles"}.issubset(candidate):
        raise ManualSupervisorError("missing_adjudication_input")
    if not isinstance(candidate["histories"], list) or len(candidate["histories"]) != 3:
        raise ManualSupervisorError("three_histories_required")
    if not isinstance(candidate["profiles"], list) or len(candidate["profiles"]) != 3:
        raise ManualSupervisorError("three_profiles_required")
    try:
        return _validated_bundle(candidate, identity["statement_index"])
    except (ValueError, TypeError, KeyError) as error:
        raise ManualSupervisorError("invalid_adjudication_bundle") from error


class OperatorBundleAdapter(BundleAdjudicatorAdapter):
    def __init__(self, package: Path, inbox: Path, identity: dict):
        self.package, self.inbox, self.identity = package, inbox, identity

    def execute_bundle(self, request: AdjudicationBundleRequest):
        current = {"schema_version": 1,
                   "statement_index": request.statement_index,
                   "run_id": request.adjudication.inputs.selected_run_id,
                   "review_attempts": {
                       reviewer.value: {
                           "task_id": request.adjudication.confirmed_sets[reviewer].task_id,
                           "attempt_id": request.adjudication.confirmed_sets[reviewer].attempt_id,
                       } for reviewer in Reviewer}}
        if current != self.identity:
            raise ManualSupervisorError("adjudication_identity_changed")
        raw = _read_package(self.package, self.inbox)
        bundle = _bundle(raw, current)
        return {"adjudication": dict(bundle.adjudication),
                "histories": [dict(value) for value in bundle.histories],
                "profiles": [dict(value) for value in bundle.profiles]}


def _application(config: VerticalReviewConfig, path: Path):
    w = config.coordinator.workflow
    statement = w.review_cases_root / f"statement_{config.statement_index:03d}"
    return inspect_adjudication_application(
        application_path=path, statement_path=statement,
        feedback_root=w.feedback_root, profiles_root=w.profiles_root,
        application_evidence_path=statement / w.application_evidence_filename)


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _same_bundle(bundle, application) -> bool:
    manifest = json.loads(application.manifest_path.read_text(encoding="utf-8"))
    if _canonical_digest(dict(bundle.adjudication)) != manifest["review_adjudication_sha256"]:
        return False
    for history in bundle.histories:
        if _canonical_digest(dict(history)) != manifest["history_application"]["records"][history["model"]]:
            return False
    for profile in bundle.profiles:
        if _canonical_digest(dict(profile)) != manifest["profile_application"]["profiles"][profile["model_name"]]["sha256"]:
            return False
    return True


def adjudication_status(config: VerticalReviewConfig) -> dict:
    w = config.coordinator.workflow
    plan = plan_next_statement(w)
    statement = w.review_cases_root / f"statement_{config.statement_index:03d}"
    inspection = inspect_reviewer_results(statement_index=config.statement_index,
                                          review_cases_root=w.review_cases_root)
    completion = inspect_statement_completeness(
        statement_index=config.statement_index, statements_path=w.statements_path,
        runs_root=w.runs_root, review_cases_root=w.review_cases_root,
        feedback_root=w.feedback_root, profiles_root=w.profiles_root, run=w.run,
        application_evidence_path=statement / w.application_evidence_filename)
    result = {"ready": plan.action is PlanAction.RUN_ADJUDICATIONS,
              "run_id": plan.run_id,
              "statement_completion": completion.status.value,
              "plan_action": plan.action.value, "reason": plan.reason,
              "blocker": None if plan.blocker is None else plan.blocker.code,
              "valid_reviewers": sum(item.complete_result is not None for item in inspection.reviewers.values()),
              "bundle": "MISSING", "application_id": None,
              "application_phase": None, "incremental": {}}
    paths = sorted((statement / ".adjudication_applications").glob("*"))
    if len(paths) > 1:
        result["bundle"] = "CONFLICT"
        return result
    if not paths:
        return result
    try:
        application = _application(config, paths[0])
        result["application_id"] = application.application_id
        result["run_id"] = application.run_id
        result["application_phase"] = application.phase.value
        manifest = json.loads(application.manifest_path.read_text(encoding="utf-8"))
        final = statement / "adjudication.json"
        final_hash = hashlib.sha256(final.read_bytes()).hexdigest() if final.is_file() else None
        result["bundle"] = ("COMMITTED" if final_hash == manifest["files"]["adjudication"]["target_sha256"]
                            else "PENDING_COMMIT" if application.phase is not ApplicationPhase.INVALID
                            else "INVALID")
        for model in BenchmarkModel:
            for kind, root, suffix in (("history", w.feedback_root, "history.jsonl"),
                                       ("profile", w.profiles_root, "profile.json")):
                key = f"{kind}:{model.value}"
                name = f"{model.value}_{suffix}"
                path = root / name
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                result["incremental"][key] = (
                    "APPLIED" if digest == manifest["files"][key]["target_sha256"]
                    else "PENDING" if digest == manifest["files"][key]["source_sha256"]
                    else "CONFLICT")
    except (OSError, ValueError, KeyError, TypeError):
        result["bundle"] = "INVALID"
    return result


def adjudicate_manual_package(config: VerticalReviewConfig, package: Path, inbox: Path,
                              *, statement_index: int, run_id: str) -> dict:
    if statement_index != config.statement_index:
        raise ManualSupervisorError("statement_mismatch")
    w = config.coordinator.workflow
    plan = plan_next_statement(w)
    permitted = {PlanAction.RUN_ADJUDICATIONS, PlanAction.RECOVER_APPLICATION,
                 PlanAction.ADJUDICATION_COMMITTED, PlanAction.FINALIZE_APPLICATION}
    statement_path = w.review_cases_root / f"statement_{statement_index:03d}"
    saved = sorted((statement_path / ".adjudication_applications").glob("*"))
    if plan.action not in permitted and len(saved) != 1:
        raise ManualSupervisorError(plan.blocker.code if plan.blocker else plan.action.value.lower())
    identity = _identity(config)
    if identity["run_id"] != run_id or (plan.run_id is not None and plan.run_id != run_id):
        raise ManualSupervisorError("run_mismatch")
    bundle = _bundle(_read_package(package, inbox), identity)
    recovering = (_application(config, plan.application_path)
                  if plan.action is PlanAction.RECOVER_APPLICATION and plan.application_path else None)
    needs_commit = (plan.action is PlanAction.RUN_ADJUDICATIONS or
                    (recovering is not None and not (
                        recovering.phase is ApplicationPhase.INTERRUPTED and
                        (statement_path / "adjudication.json").is_file())))
    if needs_commit:
        prompt = w.statements_path.parent / "prompts" / "review_adjudication.md"
        adjudication = run_synthetic_adjudication_and_persist_once(
            SyntheticAdjudicationConfig(config, prompt), OperatorBundleAdapter(package, inbox, identity))
        if adjudication.status not in {SyntheticAdjudicationStatus.PERSISTED,
                                       SyntheticAdjudicationStatus.UNCHANGED}:
            return {"status": adjudication.status.value, "reason": adjudication.reason,
                    "adjudication": adjudication_status(config)}
        application = adjudication.application
    else:
        path = plan.application_path
        if path is None:
            paths = sorted((w.review_cases_root / f"statement_{statement_index:03d}" /
                            ".adjudication_applications").glob("*"))
            if len(paths) != 1:
                raise ManualSupervisorError("application_ambiguous_or_missing")
            path = paths[0]
        application = _application(config, path)
    if application is None or application.phase is ApplicationPhase.INVALID:
        raise ManualSupervisorError("application_invalid")
    if (application.statement_index != statement_index or application.run_id != run_id
            or {r.value: dict(v) for r, v in application.review_attempts.items()}
            != identity["review_attempts"]):
        raise ManualSupervisorError("application_identity_mismatch")
    if not _same_bundle(bundle, application):
        raise ManualSupervisorError("contradictory_adjudication_bundle")
    if application.phase is ApplicationPhase.APPLIED:
        completion = inspect_statement_completeness(
            statement_index=statement_index, statements_path=w.statements_path,
            runs_root=w.runs_root, review_cases_root=w.review_cases_root,
            feedback_root=w.feedback_root, profiles_root=w.profiles_root, run=w.run,
            application_evidence_path=statement_path / w.application_evidence_filename)
        if completion.status is not StatementCompletionStatus.COMPLETE:
            raise ManualSupervisorError("completed_application_unverified")
        return {"status": "UNCHANGED", "reason": "application and v2 completion verified",
                "adjudication": adjudication_status(config)}
    if plan.action not in permitted:
        raise ManualSupervisorError(plan.blocker.code if plan.blocker else plan.action.value.lower())
    if application.phase is ApplicationPhase.PREPARED:
        return {"status": "RECOVERY_PENDING", "reason": "adjudication commit is not confirmed",
                "adjudication": adjudication_status(config)}
    applied = run_synthetic_application_once(SyntheticApplicationConfig(
        config.coordinator, config.fixture_root, statement_index,
        application.application_id, fixture_location_confirmed=True))
    return {"status": applied.status.value, "reason": applied.reason,
            "adjudication": adjudication_status(config)}
