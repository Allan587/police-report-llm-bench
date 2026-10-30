import hashlib
import json
from pathlib import Path

import pytest

import automation.orchestrator as planner_module
from automation.contracts import BenchmarkModel, Reviewer
from automation.orchestrator import (
    OrchestratorConfig,
    PlanAction,
    plan_next_statement,
    resume_plan,
)
from automation.review import (
    AdjudicationApplication,
    ApplicationEvidenceInspection,
    ApplicationPhase,
    CheckStatus,
    InputIssue,
    InputIssueCategory,
    ReviewInputValidation,
    ReviewerInspection,
    ReviewerStatus,
    StatementCompletionInspection,
    StatementCompletionStatus,
    StatementReviewInspection,
)


RAW_MODELS = {
    BenchmarkModel.GEMMA: "gemma_2b",
    BenchmarkModel.MISTRAL: "mistral",
    BenchmarkModel.LLAMA: "llama_3_2",
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def config(tmp_path: Path) -> OrchestratorConfig:
    return OrchestratorConfig(
        statements_path=tmp_path / "statements.jsonl",
        runs_root=tmp_path / "runs",
        review_cases_root=tmp_path / "review_cases",
        feedback_root=tmp_path / "feedback",
        profiles_root=tmp_path / "profiles",
    )


def add_statement_directory(cfg: OrchestratorConfig, index: int) -> Path:
    path = cfg.review_cases_root / f"statement_{index:03d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def complete_inputs(index: int, run_id: str = "run-a") -> ReviewInputValidation:
    return ReviewInputValidation(
        statement_index=index,
        selected_run_id=run_id,
        statement={"statement_index": index, "statement": {"report": "synthetic"}},
        outputs={model: {"draft_report": model.value} for model in BenchmarkModel},
    )


def review_inspection(index: int, statuses: dict[Reviewer, ReviewerStatus]):
    statement_path = Path(f"statement_{index:03d}")
    return StatementReviewInspection(
        statement_index=index,
        statement_path=statement_path,
        historical_adjudication_present=False,
        reviewers={
            reviewer: ReviewerInspection(reviewer, status, recovery_action(status))
            for reviewer, status in statuses.items()
        },
    )


def recovery_action(status: ReviewerStatus):
    from automation.review import RecoveryAction

    return {
        ReviewerStatus.COMPLETE: RecoveryAction.KEEP,
        ReviewerStatus.INCOMPLETE: RecoveryAction.REPEAT_FULL_REVIEW,
        ReviewerStatus.INVALID: RecoveryAction.REPEAT_FULL_REVIEW,
        ReviewerStatus.NOT_STARTED: RecoveryAction.START_FULL_REVIEW,
        ReviewerStatus.UNVERIFIED: RecoveryAction.BLOCK_FOR_PROVENANCE,
    }[status]


def completion(
    index: int,
    status: StatementCompletionStatus,
    *,
    inputs: ReviewInputValidation | None = None,
    reviewer_statuses: dict[Reviewer, ReviewerStatus] | None = None,
) -> StatementCompletionInspection:
    reviews = None
    if reviewer_statuses is not None:
        reviews = review_inspection(index, reviewer_statuses)
    return StatementCompletionInspection(
        statement_index=index,
        status=status,
        checks={},
        input_validation=inputs,
        review_inspection=reviews,
        application_evidence=ApplicationEvidenceInspection(CheckStatus.UNVERIFIED, None),
    )


def application(path: Path, index: int, phase: ApplicationPhase) -> AdjudicationApplication:
    return AdjudicationApplication(
        statement_index=index,
        application_id=path.name,
        application_path=path,
        manifest_path=path / "manifest.json",
        status_path=path / "status.json",
        phase=phase,
        adjudication_sha256="digest",
        history_sha256={},
        profile_sha256={},
        run_id="run-a",
        review_attempts={},
    )


@pytest.mark.parametrize(
    "phase", [ApplicationPhase.INTERRUPTED, ApplicationPhase.PREPARED]
)
def test_interrupted_or_prepared_application_is_recovered_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: ApplicationPhase
) -> None:
    cfg = config(tmp_path)
    add_statement_directory(cfg, 9)
    statement = add_statement_directory(cfg, 10)
    app_path = statement / ".adjudication_applications" / "app-a"
    app_path.mkdir(parents=True)
    monkeypatch.setattr(
        planner_module,
        "inspect_adjudication_application",
        lambda **_: application(app_path, 10, phase),
    )

    plan = plan_next_statement(cfg)

    assert plan.action is PlanAction.RECOVER_APPLICATION
    assert plan.statement_index == 10
    assert plan.application_path == app_path


def test_applied_application_without_evidence_is_finalized_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    add_statement_directory(cfg, 9)
    statement = add_statement_directory(cfg, 10)
    app_path = statement / ".adjudication_applications" / "app-a"
    app_path.mkdir(parents=True)
    monkeypatch.setattr(
        planner_module,
        "inspect_adjudication_application",
        lambda **_: application(app_path, 10, ApplicationPhase.APPLIED),
    )

    plan = plan_next_statement(cfg)

    assert plan.action is PlanAction.FINALIZE_APPLICATION
    assert plan.statement_index == 10
    assert plan.application_path == app_path


def test_application_for_another_statement_blocks_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    add_statement_directory(cfg, 9)
    statement = add_statement_directory(cfg, 10)
    app_path = statement / ".adjudication_applications" / "app-wrong-statement"
    app_path.mkdir(parents=True)
    monkeypatch.setattr(
        planner_module,
        "inspect_adjudication_application",
        lambda **_: application(app_path, 11, ApplicationPhase.PREPARED),
    )

    plan = plan_next_statement(cfg)

    assert plan.action is PlanAction.WAIT_OPERATOR
    assert plan.statement_index == 10
    assert plan.blocker is not None
    assert plan.blocker.code == "application_statement_mismatch"
    assert plan.application_path == app_path
    assert any(
        item.code == "application_statement_mismatch" for item in plan.evidence
    )


def test_earlier_incomplete_statement_is_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    for index in (9, 10, 11):
        add_statement_directory(cfg, index)
    statuses = {reviewer: ReviewerStatus.COMPLETE for reviewer in Reviewer}
    statuses[Reviewer.GEMINI] = ReviewerStatus.INCOMPLETE
    inspections = {
        10: completion(
            10,
            StatementCompletionStatus.INCOMPLETE,
            inputs=complete_inputs(10),
            reviewer_statuses=statuses,
        ),
        11: completion(11, StatementCompletionStatus.COMPLETE),
    }
    monkeypatch.setattr(planner_module, "_inspect_completion", lambda _, index: inspections[index])

    plan = plan_next_statement(cfg)

    assert plan.action is PlanAction.RETRY_REVIEWER
    assert plan.statement_index == 10
    assert plan.reviewers == (Reviewer.GEMINI,)


def test_missing_earlier_index_blocks_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    for index in (9, 10, 12):
        add_statement_directory(cfg, index)
    monkeypatch.setattr(
        planner_module,
        "_inspect_completion",
        lambda _, index: completion(index, StatementCompletionStatus.COMPLETE),
    )

    plan = plan_next_statement(cfg)

    assert plan.action is PlanAction.BLOCKED
    assert "statement_011 is missing" in plan.reason


def test_missing_inputs_produce_concrete_blocker(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    issue = InputIssue(
        InputIssueCategory.MISSING,
        "statement_file_missing",
        "statement source is missing",
        cfg.statements_path,
    )
    inputs = ReviewInputValidation(10, None, None, issues=(issue,))
    inspected = completion(
        10,
        StatementCompletionStatus.INCOMPLETE,
        inputs=inputs,
        reviewer_statuses={reviewer: ReviewerStatus.NOT_STARTED for reviewer in Reviewer},
    )

    plan = resume_plan(cfg, 10, verification=inspected)

    assert plan.action is PlanAction.BLOCKED
    assert plan.blocker is not None
    assert plan.blocker.code == "inputs_unavailable"


def test_ambiguous_generation_run_waits_for_operator(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    issue = InputIssue(
        InputIssueCategory.AMBIGUOUS,
        "generation_run_ambiguous",
        "multiple runs contain statement 10",
        cfg.runs_root,
    )
    inputs = ReviewInputValidation(10, None, {"statement_index": 10}, issues=(issue,))
    inspected = completion(10, StatementCompletionStatus.INCOMPLETE, inputs=inputs)

    plan = resume_plan(cfg, 10, verification=inspected)

    assert plan.action is PlanAction.WAIT_OPERATOR
    assert plan.blocker is not None
    assert plan.blocker.code == "inputs_ambiguous"


def test_incomplete_reviewer_retries_only_that_reviewer(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    statuses = {reviewer: ReviewerStatus.COMPLETE for reviewer in Reviewer}
    statuses[Reviewer.GROK] = ReviewerStatus.INCOMPLETE
    inspected = completion(
        10,
        StatementCompletionStatus.INCOMPLETE,
        inputs=complete_inputs(10),
        reviewer_statuses=statuses,
    )

    plan = resume_plan(cfg, 10, verification=inspected)

    assert plan.action is PlanAction.RETRY_REVIEWER
    assert plan.reviewers == (Reviewer.GROK,)


def test_three_complete_reviewers_plan_adjudications(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    inspected = completion(
        10,
        StatementCompletionStatus.INCOMPLETE,
        inputs=complete_inputs(10),
        reviewer_statuses={reviewer: ReviewerStatus.COMPLETE for reviewer in Reviewer},
    )

    plan = resume_plan(cfg, 10, verification=inspected)

    assert plan.action is PlanAction.RUN_ADJUDICATIONS


def test_operationally_complete_statement_advances_to_next_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    for index in (9, 10):
        add_statement_directory(cfg, index)
    inspections = {
        10: completion(10, StatementCompletionStatus.COMPLETE),
        11: completion(
            11,
            StatementCompletionStatus.INCOMPLETE,
            inputs=complete_inputs(11),
            reviewer_statuses={reviewer: ReviewerStatus.NOT_STARTED for reviewer in Reviewer},
        ),
    }
    monkeypatch.setattr(planner_module, "_inspect_completion", lambda _, index: inspections[index])

    plan = plan_next_statement(cfg)

    assert plan.action is PlanAction.RUN_MISSING_REVIEWER
    assert plan.statement_index == 11


def test_initial_statement_permission_error_waits_for_operator(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    add_statement_directory(cfg, 9)
    write_jsonl(
        cfg.statements_path,
        [{"statement_index": 10, "statement": {"report": "synthetic"}}],
    )
    cfg.runs_root.mkdir()
    cfg.runs_root.chmod(0)
    try:
        try:
            tuple(cfg.runs_root.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("the current user can read a mode-000 directory")

        plan = plan_next_statement(cfg)
    finally:
        cfg.runs_root.chmod(0o700)

    assert plan.action is PlanAction.WAIT_OPERATOR
    assert plan.blocker is not None
    assert plan.blocker.code == "statement_inspection_failed"
    assert any(item.code == "statement_inspection_failed" for item in plan.evidence)


def test_reviewer_mapping_order_does_not_change_plan(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    canonical = tuple(Reviewer)
    statuses = {reviewer: ReviewerStatus.NOT_STARTED for reviewer in canonical}
    reversed_statuses = {
        reviewer: ReviewerStatus.NOT_STARTED for reviewer in reversed(canonical)
    }

    first = resume_plan(
        cfg,
        10,
        verification=completion(
            10,
            StatementCompletionStatus.INCOMPLETE,
            inputs=complete_inputs(10),
            reviewer_statuses=statuses,
        ),
    )
    second = resume_plan(
        cfg,
        10,
        verification=completion(
            10,
            StatementCompletionStatus.INCOMPLETE,
            inputs=complete_inputs(10),
            reviewer_statuses=reversed_statuses,
        ),
    )

    assert first == second
    assert first.reviewers == canonical


@pytest.mark.parametrize(
    "reviewer_statuses",
    [
        {Reviewer.CHATGPT: ReviewerStatus.COMPLETE},
        {
            **{reviewer: ReviewerStatus.COMPLETE for reviewer in Reviewer},
            "unexpected": ReviewerStatus.COMPLETE,
        },
        {
            "chatgpt": ReviewerStatus.COMPLETE,
            Reviewer.GEMINI: ReviewerStatus.COMPLETE,
            Reviewer.GROK: ReviewerStatus.COMPLETE,
        },
    ],
    ids=("missing", "additional", "invalid-type"),
)
def test_invalid_reviewer_mapping_blocks(
    tmp_path: Path, reviewer_statuses: dict[object, ReviewerStatus]
) -> None:
    cfg = config(tmp_path)
    inspected = completion(
        10,
        StatementCompletionStatus.INCOMPLETE,
        inputs=complete_inputs(10),
        reviewer_statuses=reviewer_statuses,
    )

    plan = resume_plan(cfg, 10, verification=inspected)

    assert plan.action is PlanAction.WAIT_OPERATOR
    assert plan.blocker is not None
    assert plan.blocker.code == "reviewer_mapping_invalid"


def test_contradictory_adjudication_evidence_blocks(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    statement = add_statement_directory(cfg, 10)
    write_json(statement / "adjudication.json", {"contradictory": True})
    inspected = completion(
        10,
        StatementCompletionStatus.INCOMPLETE,
        inputs=complete_inputs(10),
        reviewer_statuses={reviewer: ReviewerStatus.COMPLETE for reviewer in Reviewer},
    )

    plan = resume_plan(cfg, 10, verification=inspected)

    assert plan.action is PlanAction.WAIT_OPERATOR
    assert plan.blocker is not None
    assert plan.blocker.code == "untracked_or_contradictory_adjudication"


@pytest.mark.parametrize("index", range(10))
def test_historical_statements_are_never_reprocessed(tmp_path: Path, index: int) -> None:
    cfg = config(tmp_path)

    plan = resume_plan(cfg, index)

    assert plan.action is PlanAction.COMPLETE
    assert "must not be reprocessed" in plan.reason


def add_valid_original_inputs(cfg: OrchestratorConfig, index: int) -> None:
    write_jsonl(
        cfg.statements_path,
        [{"statement_index": index, "statement": {"report": "synthetic statement"}}],
    )
    run_id = "run-a"
    write_json(cfg.runs_root / run_id / "metadata.json", {"run_id": run_id})
    for model, raw_name in RAW_MODELS.items():
        write_jsonl(
            cfg.runs_root / run_id / raw_name / "generation" / "draft_reports.jsonl",
            [
                {
                    "run_id": run_id,
                    "model_name": raw_name,
                    "statement_index": index,
                    "draft_report": f"synthetic {model.value}",
                }
            ],
        )


def tree_snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_planner_does_not_modify_any_file(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    add_statement_directory(cfg, 9)
    add_valid_original_inputs(cfg, 10)
    write_jsonl(cfg.feedback_root / "gemma_history.jsonl", [{"sentinel": "history"}])
    write_json(cfg.profiles_root / "gemma_profile.json", {"sentinel": "profile"})
    before = tree_snapshot(tmp_path)

    plan = plan_next_statement(cfg)

    after = tree_snapshot(tmp_path)
    assert plan.action is PlanAction.RUN_MISSING_REVIEWER
    assert plan.statement_index == 10
    assert after == before
