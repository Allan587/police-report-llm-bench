from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from automation.contracts import Reviewer, TaskStatus
from automation.orchestrator import OrchestratorConfig, PlanAction, plan_next_statement
from automation.review import (
    ReviewerStatus,
    inspect_reviewer_results,
    resolve_confirmed_review_set,
)
from automation.review.vertical_flow import (
    DeterministicFakeReviewerAdapter,
    ReviewerAdapterError,
    ReviewerExecutionRequest,
    VerticalReviewConfig,
    VerticalReviewStatus,
    run_synthetic_reviewer_once,
)
from automation.workflow_coordinator import (
    WorkflowCoordinatorConfig,
    coordinate_workflow_cycle,
)
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


def vertical_config(root: Path) -> VerticalReviewConfig:
    operational = root / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl",
        operational / "runs",
        operational / "review_cases",
        operational / "feedback",
        operational / "profiles",
    )
    coordinator = WorkflowCoordinatorConfig(
        workflow,
        root / "runtime" / "workflow.lock",
        "vertical-review-test",
        lease_location_confirmed=True,
    )
    return VerticalReviewConfig(
        coordinator,
        root,
        fixture_location_confirmed=True,
    )


def add_run(config: VerticalReviewConfig, run_id: str = "run-a") -> None:
    workflow = config.coordinator.workflow
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


def ready_fixture(tmp_path: Path) -> VerticalReviewConfig:
    config = vertical_config(tmp_path)
    workflow = config.coordinator.workflow
    (workflow.review_cases_root / "statement_009").mkdir(parents=True)
    write_jsonl(
        workflow.statements_path,
        [{"statement_index": 10, "statement": {"report": "synthetic statement"}}],
    )
    add_run(config)
    workflow.feedback_root.mkdir(parents=True)
    workflow.profiles_root.mkdir(parents=True)
    (workflow.feedback_root / "sentinel.txt").write_text("feedback", encoding="utf-8")
    (workflow.profiles_root / "sentinel.txt").write_text("profile", encoding="utf-8")
    return config


def snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def run_vertical_process(mode: str, root: Path) -> dict:
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{REPOSITORY_ROOT}{os.pathsep}{existing}"
        if existing
        else str(REPOSITORY_ROOT)
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "helpers" / "vertical_flow_process.py"),
            mode,
            str(root),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(completed.stdout)


class CountingAdapter(DeterministicFakeReviewerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def execute(self, request: ReviewerExecutionRequest):
        self.calls += 1
        return super().execute(request)


class SixArtifactAdapter(DeterministicFakeReviewerAdapter):
    def execute(self, request: ReviewerExecutionRequest):
        result = super().execute(request)
        object.__setattr__(result, "histories", result.histories[:-1])
        return result


class WrongIdentityAdapter(DeterministicFakeReviewerAdapter):
    def execute(self, request: ReviewerExecutionRequest):
        wrong_task = replace(request.task, reviewer=Reviewer.GEMINI, model="gemini")
        wrong_request = ReviewerExecutionRequest(
            request.plan, request.inputs, wrong_task, request.attempt
        )
        return super().execute(wrong_request)


class FailingAdapter:
    def execute(self, request: ReviewerExecutionRequest):
        raise ReviewerAdapterError("controlled adapter failure")


def test_vertical_flow_publishes_seven_artifacts_and_replans(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    before = plan_next_statement(config.coordinator.workflow)
    feedback_before = snapshot(config.coordinator.workflow.feedback_root)
    profiles_before = snapshot(config.coordinator.workflow.profiles_root)

    result = run_synthetic_reviewer_once(
        config, DeterministicFakeReviewerAdapter()
    )

    assert before.action is PlanAction.RUN_MISSING_REVIEWER
    assert before.statement_index == 10
    assert before.run_id == "run-a"
    assert before.reviewers == tuple(Reviewer)
    assert result.status is VerticalReviewStatus.COMPLETED
    assert result.task is not None and result.task.status is TaskStatus.COMPLETED
    assert result.inputs is not None and result.inputs.inputs_complete
    assert result.publication is not None
    artifacts = result.publication.confirmed.artifacts
    assert artifacts.main_review.is_file()
    assert all(path.is_file() for path in artifacts.histories.values())
    assert all(path.is_file() for path in artifacts.profile_proposals.values())
    assert 1 + len(artifacts.histories) + len(artifacts.profile_proposals) == 7
    assert result.inspection is not None
    assert result.inspection.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.COMPLETE
    assert not result.inspection.adjudication_enabled
    assert result.after_plan is not None
    assert result.after_plan.action is PlanAction.RUN_MISSING_REVIEWER
    assert result.after_plan.reviewers == (Reviewer.GEMINI, Reviewer.GROK)
    assert snapshot(config.coordinator.workflow.feedback_root) == feedback_before
    assert snapshot(config.coordinator.workflow.profiles_root) == profiles_before


@pytest.mark.parametrize(
    ("adapter", "expected_status"),
    [
        (SixArtifactAdapter(), VerticalReviewStatus.OUTPUT_REJECTED),
        (WrongIdentityAdapter(), VerticalReviewStatus.OUTPUT_REJECTED),
        (FailingAdapter(), VerticalReviewStatus.ADAPTER_FAILED),
    ],
)
def test_invalid_or_failed_adapter_never_activates_a_set(
    tmp_path: Path, adapter, expected_status: VerticalReviewStatus
) -> None:
    config = ready_fixture(tmp_path)

    result = run_synthetic_reviewer_once(config, adapter)

    assert result.status is expected_status
    assert result.task is not None and result.task.status is TaskStatus.RETRY_PENDING
    confirmed, error = resolve_confirmed_review_set(
        statement_path=config.coordinator.workflow.review_cases_root / "statement_010",
        statement_index=10,
        reviewer=Reviewer.CHATGPT,
    )
    assert confirmed is None and error is None
    assert not (
        config.coordinator.workflow.review_cases_root
        / "statement_010/.review_sets/chatgpt/active.json"
    ).exists()


def test_adapter_failure_retries_same_task_with_new_complete_attempt(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    failed = run_synthetic_reviewer_once(config, FailingAdapter())

    retried = run_synthetic_reviewer_once(
        config,
        DeterministicFakeReviewerAdapter(marker="retry"),
        task=failed.task,
        previous_attempt=failed.attempt,
    )

    assert retried.status is VerticalReviewStatus.COMPLETED
    assert retried.task.task_id == failed.task.task_id
    assert retried.attempt.attempt_number == failed.attempt.attempt_number + 1
    assert retried.attempt.attempt_id != failed.attempt.attempt_id
    assert retried.publication.confirmed.attempt_id == retried.attempt.attempt_id
    assert retried.inspection.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.COMPLETE


def test_interruption_before_activation_leaves_no_partial_active_set(
    tmp_path: Path,
) -> None:
    config = ready_fixture(tmp_path)

    def interrupt(stage: str) -> None:
        if stage == "after_set_publish":
            raise RuntimeError("controlled interruption")

    with pytest.raises(RuntimeError, match="controlled interruption"):
        run_synthetic_reviewer_once(
            config,
            DeterministicFakeReviewerAdapter(),
            publication_failure_injector=interrupt,
        )

    inspection = inspect_reviewer_results(
        statement_index=10,
        review_cases_root=config.coordinator.workflow.review_cases_root,
    )
    assert inspection.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.NOT_STARTED
    assert not inspection.adjudication_enabled
    with WorkflowLease(config.coordinator.lease_path, "after-interruption"):
        pass


def test_projected_but_unconfirmed_review_retries_with_a_new_complete_attempt(
    tmp_path: Path,
) -> None:
    config = ready_fixture(tmp_path)
    workflow = config.coordinator.workflow
    captured_attempt_ids: list[str] = []
    captured_task_ids: list[str] = []
    captured_attempt_numbers: list[int] = []

    class CapturingAdapter(DeterministicFakeReviewerAdapter):
        def execute(self, request: ReviewerExecutionRequest):
            captured_task_ids.append(request.task.task_id)
            captured_attempt_ids.append(request.attempt.attempt_id)
            captured_attempt_numbers.append(request.attempt.attempt_number)
            return super().execute(request)

    def interrupt(stage: str) -> None:
        if stage == "before_activation":
            raise RuntimeError("controlled interruption before activation")

    with pytest.raises(RuntimeError, match="before activation"):
        run_synthetic_reviewer_once(
            config,
            CapturingAdapter(marker="orphan"),
            publication_failure_injector=interrupt,
        )

    statement = workflow.review_cases_root / "statement_010"
    active = statement / ".review_sets/chatgpt/active.json"
    inspection = inspect_reviewer_results(
        statement_index=10, review_cases_root=workflow.review_cases_root
    )
    interrupted_plan = plan_next_statement(workflow)
    assert not active.exists()
    assert inspection.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.INCOMPLETE
    assert interrupted_plan.action is PlanAction.RETRY_REVIEWER
    assert interrupted_plan.reviewers == (Reviewer.CHATGPT,)

    retried = run_synthetic_reviewer_once(
        config, CapturingAdapter(marker="replacement")
    )

    assert retried.status is VerticalReviewStatus.COMPLETED
    assert retried.task is not None and retried.task.status is TaskStatus.COMPLETED
    assert retried.attempt is not None
    assert retried.task.task_id == captured_task_ids[0]
    assert retried.attempt.attempt_id != captured_attempt_ids[0]
    assert retried.attempt.attempt_number == captured_attempt_numbers[0] + 1
    assert retried.publication is not None
    assert retried.publication.confirmed.attempt_id == retried.attempt.attempt_id
    attempts = tuple(
        path
        for path in (statement / ".review_sets/chatgpt/attempts").iterdir()
        if path.is_dir()
    )
    assert len(attempts) == 2
    confirmed, error = resolve_confirmed_review_set(
        statement_path=statement,
        statement_index=10,
        reviewer=Reviewer.CHATGPT,
    )
    assert error is None
    assert confirmed is not None and confirmed.set_path == retried.publication.confirmed.set_path


def test_restart_recovers_same_task_and_increments_attempt_in_new_process(
    tmp_path: Path,
) -> None:
    first = run_vertical_process("interrupt", tmp_path)
    second = run_vertical_process("resume", tmp_path)

    assert second["status"] == VerticalReviewStatus.COMPLETED.value
    assert second["task_id"] == first["task_id"]
    assert second["attempt_number"] == first["attempt_number"] + 1
    assert second["attempt_id"] != first["attempt_id"]
    assert second["active_attempt_id"] == second["attempt_id"]


def test_restart_after_activation_keeps_active_set_and_runs_next_reviewer(
    tmp_path: Path,
) -> None:
    first = run_vertical_process("after_activation", tmp_path)
    second = run_vertical_process("resume", tmp_path)

    assert first["reviewer"] == Reviewer.CHATGPT.value
    assert second["reviewer"] == Reviewer.GEMINI.value
    assert second["status"] == VerticalReviewStatus.COMPLETED.value
    assert second["chatgpt_attempts"] == first["chatgpt_attempts"] == 1


def test_recovered_attempt_with_other_run_blocks_before_adapter(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)

    def interrupt(stage: str) -> None:
        if stage == "before_activation":
            raise RuntimeError("controlled interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        run_synthetic_reviewer_once(
            config,
            DeterministicFakeReviewerAdapter(marker="orphan"),
            publication_failure_injector=interrupt,
        )
    attempts_root = (
        config.coordinator.workflow.review_cases_root
        / "statement_010/.review_sets/chatgpt/attempts"
    )
    manifest_path = next(attempts_root.iterdir()) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["execution"]["run_id"] = "run-b"
    write_json(manifest_path, manifest)
    adapter = CountingAdapter()

    result = run_synthetic_reviewer_once(config, adapter)

    assert result.status is VerticalReviewStatus.BLOCKED
    assert "selected run or logical task" in result.reason
    assert adapter.calls == 0


def test_three_fake_reviewers_reach_adjudication_barrier_without_running_it(
    tmp_path: Path,
) -> None:
    config = ready_fixture(tmp_path)
    workflow = config.coordinator.workflow
    feedback_before = snapshot(workflow.feedback_root)
    profiles_before = snapshot(workflow.profiles_root)
    results = [
        run_synthetic_reviewer_once(
            config, DeterministicFakeReviewerAdapter(marker=f"review-{number}")
        )
        for number in range(1, 4)
    ]

    assert [result.status for result in results] == [
        VerticalReviewStatus.COMPLETED,
        VerticalReviewStatus.COMPLETED,
        VerticalReviewStatus.COMPLETED,
    ]
    assert all(result.task.status is TaskStatus.COMPLETED for result in results)
    assert [result.after_plan.action for result in results] == [
        PlanAction.RUN_MISSING_REVIEWER,
        PlanAction.RUN_MISSING_REVIEWER,
        PlanAction.RUN_ADJUDICATIONS,
    ]
    assert [result.after_plan.reviewers for result in results[:2]] == [
        (Reviewer.GEMINI, Reviewer.GROK),
        (Reviewer.GROK,),
    ]
    final = inspect_reviewer_results(
        statement_index=10, review_cases_root=workflow.review_cases_root
    )
    assert final.adjudication_enabled
    assert all(
        item.status is ReviewerStatus.COMPLETE
        and item.complete_result is not None
        for item in final.reviewers.values()
    )
    assert not (workflow.review_cases_root / "statement_010/adjudication.json").exists()
    assert snapshot(workflow.feedback_root) == feedback_before
    assert snapshot(workflow.profiles_root) == profiles_before


def test_interrupted_reviewer_can_retry_then_complete_three_reviewer_sequence(
    tmp_path: Path,
) -> None:
    config = ready_fixture(tmp_path)

    def interrupt(stage: str) -> None:
        if stage == "before_activation":
            raise RuntimeError("controlled interruption before activation")

    with pytest.raises(RuntimeError, match="before activation"):
        run_synthetic_reviewer_once(
            config,
            DeterministicFakeReviewerAdapter(marker="interrupted"),
            publication_failure_injector=interrupt,
        )

    results = [
        run_synthetic_reviewer_once(
            config, DeterministicFakeReviewerAdapter(marker=f"retry-sequence-{number}")
        )
        for number in range(1, 4)
    ]

    assert all(result.status is VerticalReviewStatus.COMPLETED for result in results)
    assert results[-1].after_plan.action is PlanAction.RUN_ADJUDICATIONS
    assert results[-1].inspection.adjudication_enabled


def test_completed_attempt_replay_is_idempotent_and_skips_adapter(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    adapter = CountingAdapter()
    first = run_synthetic_reviewer_once(config, adapter)
    before = snapshot(config.coordinator.workflow.review_cases_root)

    replay = run_synthetic_reviewer_once(
        config,
        adapter,
        task=first.task,
        previous_attempt=first.attempt,
    )

    assert first.status is VerticalReviewStatus.COMPLETED
    assert replay.status is VerticalReviewStatus.UNCHANGED
    assert replay.publication.status.value == "UNCHANGED"
    assert adapter.calls == 1
    assert snapshot(config.coordinator.workflow.review_cases_root) == before


def test_ambiguous_run_blocks_before_adapter_execution(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    add_run(config, "run-b")
    adapter = CountingAdapter()

    result = run_synthetic_reviewer_once(config, adapter)

    assert result.status is VerticalReviewStatus.BLOCKED
    assert result.before_plan.action is PlanAction.WAIT_OPERATOR
    assert result.before_plan.blocker.code == "inputs_ambiguous"
    assert adapter.calls == 0
    assert not (
        config.coordinator.workflow.review_cases_root / "statement_010"
    ).exists()


def test_stale_expectation_rejects_publication(tmp_path: Path) -> None:
    config = ready_fixture(tmp_path)
    coordinated = coordinate_workflow_cycle(config.coordinator)
    expectation = coordinated.expectation()
    with config.coordinator.workflow.statements_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    adapter = CountingAdapter()

    result = run_synthetic_reviewer_once(config, adapter, expected=expectation)

    assert result.status is VerticalReviewStatus.STALE_PLAN
    assert adapter.calls == 0
    assert not (
        config.coordinator.workflow.review_cases_root / "statement_010"
    ).exists()


def test_configuration_rejects_any_operational_path_outside_fixture(
    tmp_path: Path,
) -> None:
    config = ready_fixture(tmp_path)
    workflow = config.coordinator.workflow
    escaped = OrchestratorConfig(
        tmp_path.parent / "outside-statements.jsonl",
        workflow.runs_root,
        workflow.review_cases_root,
        workflow.feedback_root,
        workflow.profiles_root,
    )
    coordinator = WorkflowCoordinatorConfig(
        escaped,
        config.coordinator.lease_path,
        "escaped-test",
        lease_location_confirmed=True,
    )

    with pytest.raises(ValueError, match="inside fixture_root"):
        VerticalReviewConfig(
            coordinator,
            tmp_path,
            fixture_location_confirmed=True,
        )
