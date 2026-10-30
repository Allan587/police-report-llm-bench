"""Run one vertical-review step in a fresh interpreter for recovery tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from automation.orchestrator import OrchestratorConfig
from automation.review.vertical_flow import (
    DeterministicFakeReviewerAdapter,
    ReviewerExecutionRequest,
    VerticalReviewConfig,
    run_synthetic_reviewer_once,
)
from automation.workflow_coordinator import WorkflowCoordinatorConfig


RAW_MODELS = ("gemma_2b", "mistral", "llama_3_2")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8"
    )


def _config(root: Path) -> VerticalReviewConfig:
    operational = root / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl",
        operational / "runs",
        operational / "review_cases",
        operational / "feedback",
        operational / "profiles",
    )
    return VerticalReviewConfig(
        WorkflowCoordinatorConfig(
            workflow,
            root / "runtime" / "workflow.lock",
            "vertical-review-process-test",
            lease_location_confirmed=True,
        ),
        root,
        fixture_location_confirmed=True,
    )


def _setup(root: Path) -> VerticalReviewConfig:
    config = _config(root)
    workflow = config.coordinator.workflow
    (workflow.review_cases_root / "statement_009").mkdir(parents=True)
    _write_jsonl(
        workflow.statements_path,
        [{"statement_index": 10, "statement": {"report": "synthetic"}}],
    )
    _write_json(workflow.runs_root / "run-a" / "metadata.json", {"run_id": "run-a"})
    for raw_name in RAW_MODELS:
        _write_jsonl(
            workflow.runs_root
            / "run-a"
            / raw_name
            / "generation"
            / "draft_reports.jsonl",
            [
                {
                    "run_id": "run-a",
                    "model_name": raw_name,
                    "statement_index": 10,
                    "draft_report": raw_name,
                }
            ],
        )
    workflow.feedback_root.mkdir(parents=True)
    workflow.profiles_root.mkdir(parents=True)
    return config


class CapturingAdapter(DeterministicFakeReviewerAdapter):
    def __init__(self, marker: str) -> None:
        super().__init__(marker=marker)
        self.identity: dict[str, object] | None = None

    def execute(self, request: ReviewerExecutionRequest):
        self.identity = {
            "task_id": request.task.task_id,
            "attempt_id": request.attempt.attempt_id,
            "attempt_number": request.attempt.attempt_number,
            "reviewer": request.task.reviewer.value,
        }
        return super().execute(request)


def main() -> None:
    mode = sys.argv[1]
    root = Path(sys.argv[2])
    if mode == "interrupt":
        config = _setup(root)
        adapter = CapturingAdapter("orphan")

        def interrupt(stage: str) -> None:
            if stage == "before_activation":
                raise RuntimeError("controlled interruption")

        try:
            run_synthetic_reviewer_once(
                config,
                adapter,
                publication_failure_injector=interrupt,
            )
        except RuntimeError:
            print(json.dumps(adapter.identity), flush=True)
            return
        raise RuntimeError("expected interruption did not occur")
    if mode == "resume":
        config = _config(root)
        adapter = CapturingAdapter("replacement")
        result = run_synthetic_reviewer_once(config, adapter)
        print(
            json.dumps(
                {
                    **(adapter.identity or {}),
                    "status": result.status.value,
                    "active_attempt_id": result.publication.confirmed.attempt_id
                    if result.publication is not None
                    else None,
                    "chatgpt_attempts": len(
                        tuple(
                            (
                                config.coordinator.workflow.review_cases_root
                                / "statement_010/.review_sets/chatgpt/attempts"
                            ).iterdir()
                        )
                    ),
                }
            ),
            flush=True,
        )
        return
    if mode == "after_activation":
        config = _setup(root)
        adapter = CapturingAdapter("active")

        def interrupt(stage: str) -> None:
            if stage == "after_activation":
                raise RuntimeError("controlled interruption")

        try:
            run_synthetic_reviewer_once(
                config,
                adapter,
                publication_failure_injector=interrupt,
            )
        except RuntimeError:
            attempts = (
                config.coordinator.workflow.review_cases_root
                / "statement_010/.review_sets/chatgpt/attempts"
            )
            print(
                json.dumps(
                    {
                        **(adapter.identity or {}),
                        "chatgpt_attempts": len(tuple(attempts.iterdir())),
                    }
                ),
                flush=True,
            )
            return
        raise RuntimeError("expected interruption did not occur")
    raise ValueError(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
