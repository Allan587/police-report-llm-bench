import hashlib
import json
from pathlib import Path

import pytest

from automation.contracts import BenchmarkModel, Reviewer
from automation.review import (
    CheckStatus,
    ProvisionalArtifactFiles,
    SelectionAction,
    StatementCompletionStatus,
    inspect_statement_completeness,
    select_statement_from_verification,
)


RAW_MODELS = {
    BenchmarkModel.GEMMA: "gemma_2b",
    BenchmarkModel.MISTRAL: "mistral",
    BenchmarkModel.LLAMA: "llama_3_2",
}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def provenance(reviewer: Reviewer, index: int) -> dict:
    return {
        "task_id": f"task-{reviewer.value}-{index}",
        "attempt_id": f"attempt-{reviewer.value}-{index}",
        "statement_index": index,
        "reviewer": reviewer.value,
    }


def model_review(model: BenchmarkModel) -> dict:
    return {
        "model_name": model.value,
        "factuality": {"status": "pass", "findings": []},
        "hallucinations": [],
        "omissions": [],
        "narrative_quality": {"status": "pass", "findings": []},
        "crime_classification": {
            "model_category": "category",
            "assessment": "correct",
            "proposed_category_cr": "category",
            "justification": "supported",
        },
        "useful_elements": [],
    }


def main_review(reviewer: Reviewer, index: int) -> dict:
    return {
        **provenance(reviewer, index),
        "model_reviews": [model_review(model) for model in BenchmarkModel],
        "proposed_ideal_report": {
            "narrative": "narrative",
            "closing": "closing",
            "crime_classification": {
                "category": "category",
                "justification": "supported",
            },
        },
    }


def provisional_history(reviewer: Reviewer, model: BenchmarkModel, index: int) -> dict:
    return {
        **provenance(reviewer, index),
        "model": model.value,
        "output": index,
        "date": "11/10/2026",
        "observations": [],
    }


def profile_proposal(reviewer: Reviewer, model: BenchmarkModel, index: int) -> dict:
    return {
        **provenance(reviewer, index),
        "model_name": model.value,
        "new_strengths": [],
        "new_weaknesses": [],
        "new_hallucination_patterns": [],
        "new_classification_patterns": [],
        "new_narrative_patterns": [],
        "new_factuality_patterns": [],
        "new_finetuning_priorities": [],
    }


def adjudication(index: int, status: str = "approved") -> dict:
    return {
        "statement_index": index,
        "reviewers": [reviewer.value for reviewer in Reviewer],
        "classification_consensus": {
            "reviewer_classifications": {
                reviewer.value: "category" for reviewer in Reviewer
            },
            "final_category": "category",
            "consensus": "unanimous",
            "final_justification": "supported",
        },
        "selected_elements": {
            "factuality": [],
            "narrative_structure": [],
            "classification": [],
            "discarded_elements": [],
        },
        "final_report": {
            "narrative": "final narrative",
            "closing": "",
            "crime_classification": {
                "category": "category",
                "justification": "supported",
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
        "decision": {"status": status, "reason": "valid decision"},
    }


def history(model: BenchmarkModel, index: int) -> dict:
    return {
        "model": model.value,
        "output": index,
        "date": "11/10/2026",
        "observations": ["observation"],
    }


def profile(model: BenchmarkModel, index: int) -> dict:
    return {
        "model_name": model.value,
        "strengths": [],
        "weaknesses": [],
        "common_hallucinations": [],
        "classification_behavior": {"strengths": [], "weaknesses": []},
        "narrative_behavior": {"strengths": [], "weaknesses": []},
        "factuality_behavior": {"strengths": [], "weaknesses": []},
        "fine_tuning_priorities": [],
        "profile_metadata": {
            "outputs_reviewed": index + 1,
            "last_updated": "11/10/2026",
            "version": 2,
        },
    }


def make_workspace(tmp_path: Path, *, index: int = 10, decision: str = "approved") -> dict:
    statements = tmp_path / "statements.jsonl"
    write_jsonl(
        statements,
        [{"statement_index": index, "statement": {"report": "statement"}}],
    )
    runs = tmp_path / "runs"
    run_id = "run-a"
    for model, raw_name in RAW_MODELS.items():
        write_jsonl(
            runs / run_id / raw_name / "generation/draft_reports.jsonl",
            [
                {
                    "run_id": run_id,
                    "model_name": raw_name,
                    "statement_index": index,
                    "draft_report": f"report {model.value}",
                }
            ],
        )
    write_json(runs / run_id / "metadata.json", {"run_id": run_id})

    review_cases = tmp_path / "review_cases"
    statement = review_cases / f"statement_{index:03d}"
    provisional_files = {}
    for reviewer in Reviewer:
        write_json(statement / f"{reviewer.value}_review.json", main_review(reviewer, index))
        histories = []
        proposals = []
        for model in BenchmarkModel:
            history_path = statement / "temporary" / f"{reviewer.value}-{model.value}-history.json"
            profile_path = statement / "temporary" / f"{reviewer.value}-{model.value}-profile.json"
            write_json(history_path, provisional_history(reviewer, model, index))
            write_json(profile_path, profile_proposal(reviewer, model, index))
            histories.append(history_path)
            proposals.append(profile_path)
        provisional_files[reviewer] = ProvisionalArtifactFiles(histories, proposals)

    adjudication_value = adjudication(index, decision)
    write_json(statement / "adjudication.json", adjudication_value)
    feedback = tmp_path / "feedback"
    history_values = {}
    for model in BenchmarkModel:
        value = history(model, index)
        history_values[model] = value
        write_jsonl(feedback / f"{model.value}_history.jsonl", [value])
    profiles = tmp_path / "profiles"
    profile_values = {}
    for model in BenchmarkModel:
        value = profile(model, index)
        profile_values[model] = value
        write_json(profiles / f"{model.value}_profile.json", value)

    evidence_path = statement / "application_evidence.json"
    evidence = {
        "schema_version": 1,
        "statement_index": index,
        "run_id": run_id,
        "review_attempts": {
            reviewer.value: {
                "task_id": provenance(reviewer, index)["task_id"],
                "attempt_id": provenance(reviewer, index)["attempt_id"],
            }
            for reviewer in Reviewer
        },
        "review_adjudication_sha256": digest(adjudication_value),
        "history_application": {
            "adjudication_id": f"history-{index}",
            "records": {
                model.value: digest(history_values[model]) for model in BenchmarkModel
            },
        },
        "profile_application": {
            "adjudication_id": f"profile-{index}",
            "profiles": {
                model.value: {
                    "sha256": digest(profile_values[model]),
                    "outputs_reviewed": index + 1,
                    "version": 2,
                }
                for model in BenchmarkModel
            },
        },
        "temporary_artifacts_cleaned": True,
    }
    write_json(evidence_path, evidence)
    return {
        "statement_index": index,
        "statements_path": statements,
        "runs_root": runs,
        "review_cases_root": review_cases,
        "feedback_root": feedback,
        "profiles_root": profiles,
        "run": run_id,
        "provisional_files": provisional_files,
        "application_evidence_path": evidence_path,
    }


@pytest.mark.parametrize("index", range(10))
def test_historical_statements_are_complete_without_retroactive_evidence(tmp_path, index):
    result = inspect_statement_completeness(
        statement_index=index,
        statements_path=tmp_path / "missing-statements",
        runs_root=tmp_path / "missing-runs",
        review_cases_root=tmp_path / "missing-cases",
        feedback_root=tmp_path / "missing-feedback",
        profiles_root=tmp_path / "missing-profiles",
    )

    assert result.status is StatementCompletionStatus.HISTORICAL_COMPLETE
    assert result.can_advance
    assert result.checks["application_evidence"] is CheckStatus.NOT_APPLICABLE


def test_statement_010_has_no_historical_privilege(tmp_path):
    args = make_workspace(tmp_path)
    (args["review_cases_root"] / "statement_010/chatgpt_review.json").unlink()

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE


def test_new_statement_without_reviews_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    statement = args["review_cases_root"] / "statement_010"
    for path in tuple(statement.rglob("*")):
        if path.is_file() and ("review.json" in path.name or path.parent.name == "temporary"):
            path.unlink()

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert any(code.endswith("not_started") for code in result.missing_dependencies)


def test_invalid_main_review_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    path = args["review_cases_root"] / "statement_010/gemini_review.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["model_reviews"] = []
    write_json(path, value)

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert result.checks["reviews"] is CheckStatus.FAIL


def test_missing_final_adjudication_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    (args["review_cases_root"] / "statement_010/adjudication.json").unlink()

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert "adjudication_missing" in result.missing_dependencies


def test_structurally_invalid_adjudication_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    path = args["review_cases_root"] / "statement_010/adjudication.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["quality_control"].pop("format_valid")
    write_json(path, value)

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert any(issue.code == "adjudication_quality_control_invalid" for issue in result.issues)


def test_malformed_reviewer_collection_is_reported_not_raised(tmp_path):
    args = make_workspace(tmp_path)
    path = args["review_cases_root"] / "statement_010/adjudication.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["reviewers"] = [{"unexpected": "object"}]
    write_json(path, value)

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert any(issue.code == "adjudication_reviewers_invalid" for issue in result.issues)


@pytest.mark.parametrize("decision", ["approved", "needs_human_review", "rejected"])
def test_decision_status_does_not_change_operational_completeness(tmp_path, decision):
    result = inspect_statement_completeness(**make_workspace(tmp_path, decision=decision))

    assert result.status is StatementCompletionStatus.COMPLETE
    assert result.can_advance


def test_missing_adjudicated_history_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    (args["feedback_root"] / "llama_history.jsonl").write_text("", encoding="utf-8")

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert "history_record_missing" in result.missing_dependencies


def test_duplicate_adjudicated_history_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    path = args["feedback_root"] / "gemma_history.jsonl"
    value = path.read_text(encoding="utf-8")
    path.write_text(value + value, encoding="utf-8")

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert any(issue.code == "history_record_duplicate" for issue in result.issues)


def test_invalid_accumulated_profile_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    path = args["profiles_root"] / "mistral_profile.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("profile_metadata")
    write_json(path, value)

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert result.checks["profiles"] is CheckStatus.FAIL


def test_missing_application_evidence_is_unverified(tmp_path):
    args = make_workspace(tmp_path)
    args["application_evidence_path"].unlink()

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.UNVERIFIED
    assert not result.can_advance
    assert result.checks["application_evidence"] is CheckStatus.UNVERIFIED


def test_inconsistent_application_evidence_is_incomplete(tmp_path):
    args = make_workspace(tmp_path)
    path = args["application_evidence_path"]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["history_application"]["records"]["gemma"] = "0" * 64
    write_json(path, value)

    result = inspect_statement_completeness(**args)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert any(
        issue.code == "history_application_evidence_mismatch" for issue in result.issues
    )


def test_coherent_synthetic_statement_is_complete(tmp_path):
    result = inspect_statement_completeness(**make_workspace(tmp_path))

    assert result.status is StatementCompletionStatus.COMPLETE
    assert all(status in {CheckStatus.PASS, CheckStatus.NOT_APPLICABLE} for status in result.checks.values())
    assert result.can_advance
    assert not result.issues


def test_incomplete_verification_resumes_current_statement(tmp_path):
    args = make_workspace(tmp_path)
    (args["feedback_root"] / "gemma_history.jsonl").write_text("", encoding="utf-8")
    result = inspect_statement_completeness(**args)

    selection = select_statement_from_verification(args["review_cases_root"], result)

    assert selection.action is SelectionAction.RESUME
    assert selection.statement_index == 10


@pytest.mark.parametrize("historical", [True, False])
def test_complete_or_historical_verification_advances(tmp_path, historical):
    if historical:
        review_cases = tmp_path / "review_cases"
        (review_cases / "statement_009").mkdir(parents=True)
        result = inspect_statement_completeness(
            statement_index=9,
            statements_path=tmp_path / "missing",
            runs_root=tmp_path / "missing",
            review_cases_root=review_cases,
            feedback_root=tmp_path / "missing",
            profiles_root=tmp_path / "missing",
        )
    else:
        args = make_workspace(tmp_path)
        review_cases = args["review_cases_root"]
        result = inspect_statement_completeness(**args)

    selection = select_statement_from_verification(review_cases, result)

    assert selection.action is SelectionAction.ADVANCE
    assert selection.statement_index == (10 if historical else 11)


def test_unverified_verification_blocks_automatic_advance(tmp_path):
    args = make_workspace(tmp_path)
    args["application_evidence_path"].unlink()
    result = inspect_statement_completeness(**args)

    selection = select_statement_from_verification(args["review_cases_root"], result)

    assert result.status is StatementCompletionStatus.UNVERIFIED
    assert selection.action is SelectionAction.PENDING
    assert selection.statement_index is None


def test_later_complete_statement_does_not_hide_earlier_incomplete_statement(tmp_path):
    args = make_workspace(tmp_path, index=10)
    (args["feedback_root"] / "gemma_history.jsonl").write_text("", encoding="utf-8")
    incomplete = inspect_statement_completeness(**args)
    (args["review_cases_root"] / "statement_011").mkdir()

    selection = select_statement_from_verification(args["review_cases_root"], incomplete)

    assert incomplete.status is StatementCompletionStatus.INCOMPLETE
    assert selection.action is SelectionAction.RESUME
    assert selection.statement_index == 10


def test_latest_complete_blocks_when_earlier_new_statement_was_not_verified(tmp_path):
    args = make_workspace(tmp_path, index=11)
    (args["review_cases_root"] / "statement_010").mkdir()
    complete = inspect_statement_completeness(**args)

    selection = select_statement_from_verification(args["review_cases_root"], complete)

    assert complete.status is StatementCompletionStatus.COMPLETE
    assert selection.action is SelectionAction.PENDING
    assert selection.statement_index is None


def test_missing_earlier_statement_blocks_complete_latest(tmp_path):
    args = make_workspace(tmp_path, index=11)
    complete_11 = inspect_statement_completeness(**args)

    selection = select_statement_from_verification(args["review_cases_root"], complete_11)

    assert selection.action is SelectionAction.PENDING
    assert selection.statement_index is None
    assert "statement_010 is missing" in selection.reason


def test_two_verified_complete_statements_advance_to_next_index(tmp_path):
    ten_args = make_workspace(tmp_path / "ten", index=10)
    eleven_args = make_workspace(tmp_path / "eleven", index=11)
    complete_10 = inspect_statement_completeness(**ten_args)
    complete_11 = inspect_statement_completeness(**eleven_args)
    review_cases = tmp_path / "review_cases"
    (review_cases / "statement_010").mkdir(parents=True)
    (review_cases / "statement_011").mkdir()

    selection = select_statement_from_verification(
        review_cases,
        complete_11,
        earlier_verifications={10: complete_10},
    )

    assert complete_10.status is StatementCompletionStatus.COMPLETE
    assert complete_11.status is StatementCompletionStatus.COMPLETE
    assert selection.action is SelectionAction.ADVANCE
    assert selection.statement_index == 12


def test_earlier_incomplete_statement_is_resumed_before_complete_latest(tmp_path):
    ten_args = make_workspace(tmp_path / "ten", index=10)
    (ten_args["feedback_root"] / "gemma_history.jsonl").write_text("", encoding="utf-8")
    incomplete_10 = inspect_statement_completeness(**ten_args)
    eleven_args = make_workspace(tmp_path / "eleven", index=11)
    complete_11 = inspect_statement_completeness(**eleven_args)
    review_cases = tmp_path / "review_cases"
    (review_cases / "statement_010").mkdir(parents=True)
    (review_cases / "statement_011").mkdir()

    selection = select_statement_from_verification(
        review_cases,
        complete_11,
        earlier_verifications={10: incomplete_10},
    )

    assert selection.action is SelectionAction.RESUME
    assert selection.statement_index == 10


def test_earlier_unverified_statement_blocks_complete_latest(tmp_path):
    ten_args = make_workspace(tmp_path / "ten", index=10)
    ten_args["application_evidence_path"].unlink()
    unverified_10 = inspect_statement_completeness(**ten_args)
    eleven_args = make_workspace(tmp_path / "eleven", index=11)
    complete_11 = inspect_statement_completeness(**eleven_args)
    review_cases = tmp_path / "review_cases"
    (review_cases / "statement_010").mkdir(parents=True)
    (review_cases / "statement_011").mkdir()

    selection = select_statement_from_verification(
        review_cases,
        complete_11,
        earlier_verifications={10: unverified_10},
    )

    assert unverified_10.status is StatementCompletionStatus.UNVERIFIED
    assert selection.action is SelectionAction.PENDING
    assert selection.statement_index is None


def test_complete_earlier_and_incomplete_latest_resumes_latest(tmp_path):
    ten_args = make_workspace(tmp_path / "ten", index=10)
    complete_10 = inspect_statement_completeness(**ten_args)
    eleven_args = make_workspace(tmp_path / "eleven", index=11)
    (eleven_args["feedback_root"] / "gemma_history.jsonl").write_text("", encoding="utf-8")
    incomplete_11 = inspect_statement_completeness(**eleven_args)
    review_cases = tmp_path / "review_cases"
    (review_cases / "statement_010").mkdir(parents=True)
    (review_cases / "statement_011").mkdir()

    selection = select_statement_from_verification(
        review_cases,
        incomplete_11,
        earlier_verifications={10: complete_10},
    )

    assert selection.action is SelectionAction.RESUME
    assert selection.statement_index == 11


def test_global_inspection_does_not_modify_files(tmp_path):
    args = make_workspace(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    inspect_statement_completeness(**args)

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
