import json
from pathlib import Path

import pytest

from automation.contracts import BenchmarkModel
from automation.review import (
    CompletionState,
    InputIssueCategory,
    SelectionAction,
    discover_statements,
    inspect_review_inputs,
    select_statement,
)


RAW_MODELS = {
    BenchmarkModel.GEMMA: "gemma_2b",
    BenchmarkModel.MISTRAL: "mistral",
    BenchmarkModel.LLAMA: "llama_3_2",
}


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def make_statement(path: Path, index: int) -> None:
    write_jsonl(
        path,
        [{"statement_index": index, "statement": {"report": f"statement {index}"}}],
    )


def make_run(root: Path, run_id: str, index: int, *, models=None) -> Path:
    run_path = root / run_id
    selected_models = set(BenchmarkModel) if models is None else set(models)
    for model, raw_name in RAW_MODELS.items():
        if model not in selected_models:
            continue
        write_jsonl(
            run_path / raw_name / "generation" / "draft_reports.jsonl",
            [
                {
                    "run_id": run_id,
                    "model_name": raw_name,
                    "statement_index": index,
                    "draft_report": f"report {model.value}",
                }
            ],
        )
    (run_path / "metadata.json").write_text(
        json.dumps({"run_id": run_id}), encoding="utf-8"
    )
    return run_path


def issue_codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_discovers_latest_statement_by_numeric_index(tmp_path):
    for name in ("statement_009", "statement_010", "statement_100", "statement_bad"):
        (tmp_path / name).mkdir()

    discovery = discover_statements(tmp_path)

    assert [item.statement_index for item in discovery.statements] == [9, 10, 100]
    assert discovery.latest.statement_index == 100


def test_resumes_latest_confirmed_incomplete(tmp_path):
    for index in range(10, 13):
        (tmp_path / f"statement_{index:03d}").mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.INCOMPLETE)

    assert result.action is SelectionAction.RESUME
    assert result.statement_index == 12
    assert result.statement_path == tmp_path / "statement_012"


def test_advances_only_when_latest_is_confirmed_complete(tmp_path):
    for index in range(10, 13):
        (tmp_path / f"statement_{index:03d}").mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.COMPLETE)

    assert result.action is SelectionAction.ADVANCE
    assert result.statement_index == 13
    assert result.statement_path == tmp_path / "statement_013"
    assert not result.statement_path.exists()


def test_missing_first_active_statement_blocks_skipping_to_later_index(tmp_path):
    (tmp_path / "statement_011").mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.COMPLETE)

    assert result.action is SelectionAction.PENDING
    assert result.statement_index is None
    assert "missing" in result.reason


def test_contiguous_active_statements_can_advance(tmp_path):
    (tmp_path / "statement_010").mkdir()
    (tmp_path / "statement_011").mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.COMPLETE)

    assert result.action is SelectionAction.ADVANCE
    assert result.statement_index == 12


def test_multiple_active_gaps_block_at_the_first_missing_index(tmp_path):
    for name in ("statement_010", "statement_012", "statement_014"):
        (tmp_path / name).mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.COMPLETE)

    assert result.action is SelectionAction.PENDING
    assert result.statement_index is None
    assert "011" in result.reason


def test_invalid_directory_name_does_not_create_or_hide_a_gap(tmp_path):
    (tmp_path / "statement_010").mkdir()
    (tmp_path / "statement_bad").mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.COMPLETE)

    assert result.action is SelectionAction.ADVANCE
    assert result.statement_index == 11


def test_historical_gaps_do_not_change_first_active_index_policy(tmp_path):
    (tmp_path / "statement_009").mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.COMPLETE)

    assert result.action is SelectionAction.ADVANCE
    assert result.statement_index == 10


def test_unknown_completeness_returns_pending(tmp_path):
    (tmp_path / "statement_012").mkdir()

    result = select_statement(tmp_path, latest_completion=CompletionState.UNKNOWN)

    assert result.action is SelectionAction.PENDING
    assert result.statement_index is None


def test_missing_statement_is_reported(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 1)
    runs = tmp_path / "runs"
    make_run(runs, "run-a", 2)

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert "statement_missing" in issue_codes(result)
    assert result.missing


def test_duplicate_statement_is_rejected(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    with statements.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"statement_index": 2, "statement": {"report": "again"}}) + "\n")
    runs = tmp_path / "runs"
    make_run(runs, "run-a", 2)

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert "statement_duplicate" in issue_codes(result)
    assert result.duplicates


def test_invalid_json_is_rejected(tmp_path):
    statements = tmp_path / "statements.jsonl"
    statements.write_text("{broken\n", encoding="utf-8")
    runs = tmp_path / "runs"
    make_run(runs, "run-a", 2)

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert "statement_json_invalid" in issue_codes(result)
    assert result.invalid


@pytest.mark.parametrize("missing_model", list(BenchmarkModel))
def test_missing_any_model_output_is_reported(tmp_path, missing_model):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    make_run(runs, "run-a", 2, models=set(BenchmarkModel) - {missing_model})

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert any(issue.model is missing_model for issue in result.missing)
    assert not result.inputs_complete


def test_output_for_another_statement_is_rejected(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    run_path = make_run(runs, "run-a", 2)
    write_jsonl(
        run_path / "gemma_2b/generation/draft_reports.jsonl",
        [{"run_id": "run-a", "model_name": "gemma_2b", "statement_index": 3, "draft_report": "wrong"}],
    )

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert "output_statement_mismatch" in issue_codes(result)


def test_inconsistent_model_identifier_is_rejected(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    run_path = make_run(runs, "run-a", 2)
    path = run_path / "gemma_2b/generation/draft_reports.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["model_name"] = "mistral"
    write_jsonl(path, [record])

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert "output_model_mismatch" in issue_codes(result)


def test_duplicate_outputs_are_rejected(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    run_path = make_run(runs, "run-a", 2)
    path = run_path / "mistral/generation/draft_reports.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    write_jsonl(path, [record, record])

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert "output_duplicate" in issue_codes(result)
    assert result.duplicates


def test_run_identity_mismatch_prevents_mixing(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    run_path = make_run(runs, "run-a", 2)
    path = run_path / "llama_3_2/generation/draft_reports.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["run_id"] = "run-b"
    write_jsonl(path, [record])

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert "output_run_mismatch" in issue_codes(result)
    assert BenchmarkModel.LLAMA not in result.outputs


def test_multiple_candidate_runs_require_explicit_selection(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    make_run(runs, "run-a", 2)
    make_run(runs, "run-b", 2)

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs
    )

    assert result.selected_run_id is None
    assert "run_candidates_ambiguous" in issue_codes(result)
    assert result.ambiguities


def test_complete_inputs_from_one_explicit_run_are_accepted(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    make_run(runs, "run-a", 2)

    result = inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    assert result.inputs_complete
    assert result.selected_run_id == "run-a"
    assert set(result.outputs) == set(BenchmarkModel)


def test_inspection_does_not_modify_files(tmp_path):
    statements = tmp_path / "statements.jsonl"
    make_statement(statements, 2)
    runs = tmp_path / "runs"
    make_run(runs, "run-a", 2)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    inspect_review_inputs(
        statement_index=2, statements_path=statements, runs_root=runs, run="run-a"
    )

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
