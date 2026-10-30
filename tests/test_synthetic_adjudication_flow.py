from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from automation.contracts import BenchmarkModel, Reviewer
from automation.orchestrator import OrchestratorConfig, PlanAction
from automation.review import (
    ApplicationPhase,
    commit_adjudication_bundle,
    inspect_adjudication_application,
)
from automation.review.synthetic_adjudication import (
    AdjudicationBundleRequest,
    AdjudicationRequest,
    AdjudicatorAdapterError,
    DeterministicFakeAdjudicatorAdapter,
    SyntheticAdjudicationConfig,
    SyntheticAdjudicationStatus,
    run_synthetic_adjudication_and_persist_once,
    run_synthetic_adjudication_once,
    run_synthetic_three_reviewers_and_adjudicate,
    run_synthetic_three_reviewers_and_persist,
)
from automation.review.vertical_flow import (
    DeterministicFakeReviewerAdapter,
    ReviewerExecutionRequest,
    VerticalReviewConfig,
    VerticalReviewStatus,
    run_synthetic_reviewer_once,
)
from automation.workflow_coordinator import WorkflowCoordinatorConfig
from automation.workflow_lease import WorkflowLease


RAW_MODELS = ("gemma_2b", "mistral", "llama_3_2")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8"
    )


def snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def accumulated_profile(model: BenchmarkModel, index: int = 9) -> dict:
    return {
        "model_name": model.value,
        "strengths": [
            {
                "pattern": f"established-{model.value}",
                "evidence_count": 1,
                "last_observed_output": index,
            }
        ],
        "weaknesses": [],
        "common_hallucinations": [],
        "classification_behavior": {"strengths": [], "weaknesses": []},
        "narrative_behavior": {"strengths": [], "weaknesses": []},
        "factuality_behavior": {"strengths": [], "weaknesses": []},
        "fine_tuning_priorities": [],
        "profile_metadata": {
            "outputs_reviewed": index + 1,
            "last_updated": "29/10/2026",
            "version": 1,
        },
    }


def ready_fixture(tmp_path: Path) -> SyntheticAdjudicationConfig:
    operational = tmp_path / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl",
        operational / "runs",
        operational / "review_cases",
        operational / "feedback",
        operational / "profiles",
    )
    coordinator = WorkflowCoordinatorConfig(
        workflow,
        tmp_path / "runtime" / "workflow.lock",
        "synthetic-adjudication-test",
        lease_location_confirmed=True,
    )
    review = VerticalReviewConfig(
        coordinator,
        tmp_path,
        fixture_location_confirmed=True,
    )
    (workflow.review_cases_root / "statement_009").mkdir(parents=True)
    write_jsonl(
        workflow.statements_path,
        [{"statement_index": 10, "statement": {"report": "synthetic statement"}}],
    )
    run_id = "run-a"
    write_json(workflow.runs_root / run_id / "metadata.json", {"run_id": run_id})
    for raw_name in RAW_MODELS:
        write_jsonl(
            workflow.runs_root
            / run_id
            / raw_name
            / "generation"
            / "draft_reports.jsonl",
            [
                {
                    "run_id": run_id,
                    "model_name": raw_name,
                    "statement_index": 10,
                    "draft_report": f"synthetic {raw_name}",
                }
            ],
        )
    workflow.feedback_root.mkdir(parents=True)
    workflow.profiles_root.mkdir(parents=True)
    for model in BenchmarkModel:
        write_jsonl(
            workflow.feedback_root / f"{model.value}_history.jsonl",
            [
                {
                    "model": model.value,
                    "output": 9,
                    "date": "29/10/2026",
                    "observations": ["historical synthetic fixture"],
                }
            ],
        )
        write_json(
            workflow.profiles_root / f"{model.value}_profile.json",
            accumulated_profile(model),
        )
    prompt_path = operational / "prompts" / "review_adjudication.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text(
        (REPOSITORY_ROOT / "config/prompts/review_adjudication.md").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    return SyntheticAdjudicationConfig(review, prompt_path)


class CountingReviewer(DeterministicFakeReviewerAdapter):
    def __init__(self) -> None:
        super().__init__(marker="synthetic-review")
        self.calls = 0

    def execute(self, request: ReviewerExecutionRequest):
        self.calls += 1
        return super().execute(request)


class CountingAdjudicator(DeterministicFakeAdjudicatorAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def execute(self, request: AdjudicationRequest):
        self.calls += 1
        return super().execute(request)


class CountingBundleAdjudicator(DeterministicFakeAdjudicatorAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def execute_bundle(self, request: AdjudicationBundleRequest):
        self.calls += 1
        return super().execute_bundle(request)


class MalformedBundleAdjudicator(DeterministicFakeAdjudicatorAdapter):
    def execute_bundle(self, request: AdjudicationBundleRequest):
        value = dict(super().execute_bundle(request))
        value["histories"] = value["histories"][:2]
        return value


class IncompatibleProfileAdjudicator(DeterministicFakeAdjudicatorAdapter):
    def execute_bundle(self, request: AdjudicationBundleRequest):
        value = dict(super().execute_bundle(request))
        profiles = json.loads(json.dumps(value["profiles"]))
        profiles[0]["profile_metadata"]["outputs_reviewed"] = 999
        profiles[0]["strengths"][0]["evidence_count"] = 999
        profiles[0]["strengths"][0]["last_observed_output"] = 999
        value["profiles"] = profiles
        return value


class ConflictingBundleAdjudicator(DeterministicFakeAdjudicatorAdapter):
    def execute_bundle(self, request: AdjudicationBundleRequest):
        value = dict(super().execute_bundle(request))
        adjudication = json.loads(json.dumps(value["adjudication"]))
        adjudication["decision"]["reason"] = "conflicting synthetic adjudication"
        value["adjudication"] = adjudication
        return value


class InvalidAdjudicator:
    def execute(self, request: AdjudicationRequest):
        return {"statement_index": request.inputs.statement_index}


class FailingAdjudicator:
    def execute(self, request: AdjudicationRequest):
        raise AdjudicatorAdapterError("controlled synthetic failure")


class UnexpectedFailingAdjudicator:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self, request: AdjudicationRequest):
        raise self.error


def publish_reviewers(config: SyntheticAdjudicationConfig, count: int) -> None:
    for number in range(count):
        result = run_synthetic_reviewer_once(
            config.review,
            DeterministicFakeReviewerAdapter(marker=f"review-{number}"),
        )
        assert result.status is VerticalReviewStatus.COMPLETED


def active_attempt_directories(config: SyntheticAdjudicationConfig) -> set[Path]:
    statement = (
        config.review.coordinator.workflow.review_cases_root / "statement_010"
    )
    return {
        path
        for path in statement.glob(".review_sets/*/attempts/*")
        if path.is_dir()
    }


def test_three_valid_reviews_prepare_one_validated_adjudication(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    workflow = config.review.coordinator.workflow
    immutable_before = {
        "statements": snapshot(workflow.statements_path.parent),
        "feedback": snapshot(workflow.feedback_root),
        "profiles": snapshot(workflow.profiles_root),
    }
    reviewer = CountingReviewer()
    adjudicator = CountingAdjudicator()

    result = run_synthetic_three_reviewers_and_adjudicate(
        config, reviewer, adjudicator
    )

    assert len(result.reviewer_results) == 3
    assert all(
        item.status is VerticalReviewStatus.COMPLETED
        for item in result.reviewer_results
    )
    assert result.reviewer_results[-1].after_plan.action is PlanAction.RUN_ADJUDICATIONS
    assert result.adjudication.status is SyntheticAdjudicationStatus.PREPARED
    assert result.adjudication.request.reviewer_artifact_count == 21
    assert set(result.adjudication.request.reviews) == set(Reviewer)
    assert result.adjudication.prepared.evidence.reviewer_artifact_count == 21
    assert result.adjudication.prepared.evidence.run_id == "run-a"
    assert not result.adjudication.prepared.evidence.persisted
    assert reviewer.calls == 3
    assert adjudicator.calls == 1
    assert len(active_attempt_directories(config)) == 3
    assert not (
        workflow.review_cases_root / "statement_010/adjudication.json"
    ).exists()
    assert not (
        workflow.review_cases_root / "statement_010/.adjudication_applications"
    ).exists()
    assert snapshot(workflow.feedback_root) == immutable_before["feedback"]
    assert snapshot(workflow.profiles_root) == immutable_before["profiles"]
    # Review publication changes review_cases only; statements, runs and prompt
    # material remain byte-identical inside the isolated fixture.
    current_operational = snapshot(workflow.statements_path.parent)
    for relative, digest in immutable_before["statements"].items():
        if not relative.startswith("review_cases/") and not relative.startswith("runtime/"):
            assert current_operational[relative] == digest


def test_two_reviews_do_not_execute_adjudicator(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 2)
    adjudicator = CountingAdjudicator()

    result = run_synthetic_adjudication_once(config, adjudicator)

    assert result.status is SyntheticAdjudicationStatus.BLOCKED
    assert result.prepared is None
    assert adjudicator.calls == 0


def test_incomplete_review_blocks_adjudicator(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 2)

    def interrupt(stage: str) -> None:
        if stage == "before_activation":
            raise RuntimeError("controlled incomplete review")

    with pytest.raises(RuntimeError, match="controlled incomplete review"):
        run_synthetic_reviewer_once(
            config.review,
            DeterministicFakeReviewerAdapter(marker="incomplete"),
            publication_failure_injector=interrupt,
        )
    adjudicator = CountingAdjudicator()

    result = run_synthetic_adjudication_once(config, adjudicator)

    assert result.status is SyntheticAdjudicationStatus.BLOCKED
    assert result.prepared is None
    assert adjudicator.calls == 0


def test_contradictory_active_run_blocks_before_adjudicator(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    statement = config.review.coordinator.workflow.review_cases_root / "statement_010"
    reference_path = statement / ".review_sets/grok/active.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    manifest_path = (
        statement
        / ".review_sets/grok/attempts"
        / reference["set_id"]
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["execution"]["run_id"] = "run-b"
    write_json(manifest_path, manifest)
    canonical = (
        json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")
    reference["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    write_json(reference_path, reference)
    adjudicator = CountingAdjudicator()

    result = run_synthetic_adjudication_once(config, adjudicator)

    assert result.status is SyntheticAdjudicationStatus.VALIDATION_FAILED
    assert "run contradicts" in result.reason
    assert result.prepared is None
    assert adjudicator.calls == 0


def test_invalid_provenance_blocks_before_adjudicator(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    reference_path = (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/.review_sets/grok/active.json"
    )
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    reference["attempt_id"] = "foreign-attempt"
    write_json(reference_path, reference)
    adjudicator = CountingAdjudicator()

    result = run_synthetic_adjudication_once(config, adjudicator)

    assert result.status is SyntheticAdjudicationStatus.BLOCKED
    assert result.prepared is None
    assert adjudicator.calls == 0


def test_invalid_adjudicator_structure_is_rejected(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)

    result = run_synthetic_adjudication_once(config, InvalidAdjudicator())

    assert result.status is SyntheticAdjudicationStatus.OUTPUT_REJECTED
    assert result.prepared is None
    assert not (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/adjudication.json"
    ).exists()


def test_adjudicator_failure_publishes_no_valid_result(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)

    result = run_synthetic_adjudication_once(config, FailingAdjudicator())

    assert result.status is SyntheticAdjudicationStatus.ADJUDICATOR_FAILED
    assert result.prepared is None
    assert not (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/adjudication.json"
    ).exists()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("qa unexpected"),
        ValueError("qa unexpected value with sensitive details"),
    ],
)
def test_unexpected_adjudicator_failure_is_sanitized_and_releases_lease(
    tmp_path: Path, error: Exception
) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    workflow = config.review.coordinator.workflow
    immutable_before = {
        "feedback": snapshot(workflow.feedback_root),
        "profiles": snapshot(workflow.profiles_root),
    }

    result = run_synthetic_adjudication_once(
        config, UnexpectedFailingAdjudicator(error)
    )

    assert result.status is SyntheticAdjudicationStatus.ADJUDICATOR_FAILED
    assert result.prepared is None
    assert result.reason == "synthetic adjudicator failed unexpectedly"
    assert "qa unexpected" not in result.reason
    assert not (
        workflow.review_cases_root / "statement_010/adjudication.json"
    ).exists()
    assert not (
        workflow.review_cases_root / "statement_010/.adjudication_applications"
    ).exists()
    assert snapshot(workflow.feedback_root) == immutable_before["feedback"]
    assert snapshot(workflow.profiles_root) == immutable_before["profiles"]

    lease = WorkflowLease(
        config.review.coordinator.lease_path,
        config.review.coordinator.owner_id,
    )
    lease.acquire(blocking=False)
    lease.release()


def test_repeating_flow_does_not_duplicate_active_results(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    reviewer = CountingReviewer()
    adjudicator = CountingAdjudicator()

    first = run_synthetic_three_reviewers_and_adjudicate(
        config, reviewer, adjudicator
    )
    attempts_before = active_attempt_directories(config)
    second = run_synthetic_three_reviewers_and_adjudicate(
        config, reviewer, adjudicator
    )

    assert first.adjudication.status is SyntheticAdjudicationStatus.PREPARED
    assert second.adjudication.status is SyntheticAdjudicationStatus.PREPARED
    assert len(second.reviewer_results) == 0
    assert reviewer.calls == 3
    assert adjudicator.calls == 2
    assert active_attempt_directories(config) == attempts_before
    assert (
        first.adjudication.prepared.evidence.adjudication_sha256
        == second.adjudication.prepared.evidence.adjudication_sha256
    )
    assert not (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/adjudication.json"
    ).exists()


def test_valid_bundle_persists_only_final_adjudication(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    workflow = config.review.coordinator.workflow
    before_feedback = snapshot(workflow.feedback_root)
    before_profiles = snapshot(workflow.profiles_root)

    result = run_synthetic_three_reviewers_and_persist(
        config,
        DeterministicFakeReviewerAdapter(marker="persist-review"),
        DeterministicFakeAdjudicatorAdapter(),
    )

    assert result.adjudication.status is SyntheticAdjudicationStatus.PERSISTED
    assert result.adjudication.application.phase is ApplicationPhase.ADJUDICATION_COMMITTED
    statement = workflow.review_cases_root / "statement_010"
    assert json.loads((statement / "adjudication.json").read_text(encoding="utf-8")) == dict(
        result.adjudication.bundle.adjudication
    )
    assert snapshot(workflow.feedback_root) == before_feedback
    assert snapshot(workflow.profiles_root) == before_profiles
    assert len(result.adjudication.bundle.histories) == 3
    assert len(result.adjudication.bundle.profiles) == 3


def test_missing_review_prevents_persistence(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 2)
    adapter = CountingBundleAdjudicator()

    result = run_synthetic_adjudication_and_persist_once(config, adapter)

    assert result.status is SyntheticAdjudicationStatus.BLOCKED
    assert adapter.calls == 0
    assert not (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/adjudication.json"
    ).exists()


@pytest.mark.parametrize("missing", ["statement", "gemma_2b", "mistral", "llama_3_2"])
def test_missing_original_input_blocks_without_effects(
    tmp_path: Path, missing: str
) -> None:
    config = ready_fixture(tmp_path)
    workflow = config.review.coordinator.workflow
    if missing == "statement":
        workflow.statements_path.unlink()
    else:
        (
            workflow.runs_root
            / "run-a"
            / missing
            / "generation/draft_reports.jsonl"
        ).unlink()
    before_feedback = snapshot(workflow.feedback_root)
    before_profiles = snapshot(workflow.profiles_root)
    adapter = CountingBundleAdjudicator()

    result = run_synthetic_adjudication_and_persist_once(config, adapter)

    assert result.status is SyntheticAdjudicationStatus.BLOCKED
    assert adapter.calls == 0
    assert snapshot(workflow.feedback_root) == before_feedback
    assert snapshot(workflow.profiles_root) == before_profiles
    assert not (
        workflow.review_cases_root / "statement_010/adjudication.json"
    ).exists()


def test_malformed_bundle_is_rejected_without_effects(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    workflow = config.review.coordinator.workflow
    before_feedback = snapshot(workflow.feedback_root)
    before_profiles = snapshot(workflow.profiles_root)

    result = run_synthetic_adjudication_and_persist_once(
        config, MalformedBundleAdjudicator()
    )

    assert result.status is SyntheticAdjudicationStatus.OUTPUT_REJECTED
    assert snapshot(workflow.feedback_root) == before_feedback
    assert snapshot(workflow.profiles_root) == before_profiles
    assert not (
        workflow.review_cases_root / "statement_010/adjudication.json"
    ).exists()


def test_adjudication_notes_are_discarded_from_all_persistence(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    result = run_synthetic_three_reviewers_and_persist(
        config,
        DeterministicFakeReviewerAdapter(),
        DeterministicFakeAdjudicatorAdapter(),
    )

    assert result.adjudication.status is SyntheticAdjudicationStatus.PERSISTED
    statement = config.review.coordinator.workflow.review_cases_root / "statement_010"
    for path in statement.rglob("*"):
        if path.is_file():
            assert b"ADJUDICATION_NOTES" not in path.read_bytes()
            assert b"synthetic notes" not in path.read_bytes()


def test_incompatible_profile_metadata_blocks_before_any_effect(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    workflow = config.review.coordinator.workflow
    before_statement = snapshot(workflow.review_cases_root / "statement_010")
    before_feedback = snapshot(workflow.feedback_root)
    before_profiles = snapshot(workflow.profiles_root)

    result = run_synthetic_adjudication_and_persist_once(
        config, IncompatibleProfileAdjudicator()
    )

    assert result.status is SyntheticAdjudicationStatus.VALIDATION_FAILED
    assert "outputs_reviewed" in result.reason
    assert snapshot(workflow.review_cases_root / "statement_010") == before_statement
    assert snapshot(workflow.feedback_root) == before_feedback
    assert snapshot(workflow.profiles_root) == before_profiles


def test_identical_bundle_commit_is_idempotent(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    adapter = DeterministicFakeAdjudicatorAdapter()
    first = run_synthetic_adjudication_and_persist_once(config, adapter)
    workflow = config.review.coordinator.workflow
    before = snapshot(config.review.fixture_root)

    second = commit_adjudication_bundle(
        statement_index=10,
        statement_path=workflow.review_cases_root / "statement_010",
        feedback_root=workflow.feedback_root,
        profiles_root=workflow.profiles_root,
        adjudication=first.bundle.adjudication,
        histories=first.bundle.histories,
        profiles=first.bundle.profiles,
        run_id="run-a",
        review_attempts={
            reviewer: {
                "task_id": first.request.reviews[reviewer].task_id,
                "attempt_id": first.request.reviews[reviewer].attempt_id,
            }
            for reviewer in Reviewer
        },
    )

    assert second.status.value == "UNCHANGED"
    assert snapshot(config.review.fixture_root) == before


def test_conflicting_recommit_is_rejected_without_overwrite(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    first = run_synthetic_adjudication_and_persist_once(
        config, DeterministicFakeAdjudicatorAdapter()
    )
    workflow = config.review.coordinator.workflow
    stored = (workflow.review_cases_root / "statement_010/adjudication.json").read_bytes()
    conflicting = json.loads(json.dumps(dict(first.bundle.adjudication)))
    conflicting["decision"]["reason"] = "different"

    with pytest.raises(ValueError, match="different adjudication"):
        commit_adjudication_bundle(
            statement_index=10,
            statement_path=workflow.review_cases_root / "statement_010",
            feedback_root=workflow.feedback_root,
            profiles_root=workflow.profiles_root,
            adjudication=conflicting,
            histories=first.bundle.histories,
            profiles=first.bundle.profiles,
            run_id="run-a",
            review_attempts={
                reviewer: {
                    "task_id": first.request.reviews[reviewer].task_id,
                    "attempt_id": first.request.reviews[reviewer].attempt_id,
                }
                for reviewer in Reviewer
            },
        )

    assert (workflow.review_cases_root / "statement_010/adjudication.json").read_bytes() == stored


def test_interruption_before_commit_leaves_no_final_result(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)

    def interrupt(stage: str) -> None:
        if stage == "after_prepare":
            raise RuntimeError("before commit")

    with pytest.raises(RuntimeError, match="before commit"):
        run_synthetic_adjudication_and_persist_once(
            config,
            DeterministicFakeAdjudicatorAdapter(),
            failure_injector=interrupt,
        )

    assert not (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/adjudication.json"
    ).exists()


def test_interruption_after_commit_remains_verifiable(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    application_path = None

    def interrupt(stage: str) -> None:
        nonlocal application_path
        roots = list(
            (
                config.review.coordinator.workflow.review_cases_root
                / "statement_010/.adjudication_applications"
            ).glob("*")
        )
        application_path = roots[0] if roots else None
        if stage == "after_adjudication_commit":
            raise RuntimeError("after commit")

    with pytest.raises(RuntimeError, match="after commit"):
        run_synthetic_adjudication_and_persist_once(
            config,
            DeterministicFakeAdjudicatorAdapter(),
            failure_injector=interrupt,
        )

    workflow = config.review.coordinator.workflow
    inspected = inspect_adjudication_application(
        application_path=application_path,
        statement_path=workflow.review_cases_root / "statement_010",
        feedback_root=workflow.feedback_root,
        profiles_root=workflow.profiles_root,
    )
    assert inspected.phase is ApplicationPhase.ADJUDICATION_COMMITTED
    assert (workflow.review_cases_root / "statement_010/adjudication.json").is_file()


def test_manifest_tampering_after_prepare_blocks_commit(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)

    def tamper(stage: str) -> None:
        if stage != "after_prepare":
            return
        roots = list(
            (
                config.review.coordinator.workflow.review_cases_root
                / "statement_010/.adjudication_applications"
            ).glob("*")
        )
        manifest_path = roots[0] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["run_id"] = "tampered-run"
        write_json(manifest_path, manifest)

    result = run_synthetic_adjudication_and_persist_once(
        config,
        DeterministicFakeAdjudicatorAdapter(),
        failure_injector=tamper,
    )

    assert result.status is SyntheticAdjudicationStatus.VALIDATION_FAILED
    assert not (
        config.review.coordinator.workflow.review_cases_root
        / "statement_010/adjudication.json"
    ).exists()


def test_tampered_prepared_evidence_invalidates_committed_application(
    tmp_path: Path,
) -> None:
    config = ready_fixture(tmp_path)
    result = run_synthetic_three_reviewers_and_persist(
        config,
        DeterministicFakeReviewerAdapter(),
        DeterministicFakeAdjudicatorAdapter(),
    )
    application = result.adjudication.application
    prepared = application.application_path / "prepared/history/gemma_history.jsonl"
    prepared.write_text("{}\n", encoding="utf-8")
    workflow = config.review.coordinator.workflow

    inspected = inspect_adjudication_application(
        application_path=application.application_path,
        statement_path=workflow.review_cases_root / "statement_010",
        feedback_root=workflow.feedback_root,
        profiles_root=workflow.profiles_root,
    )

    assert inspected.phase is ApplicationPhase.INVALID
