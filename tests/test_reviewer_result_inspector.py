import json
from pathlib import Path

import pytest

from automation.contracts import BenchmarkModel, Reviewer
from automation.review import (
    ProvisionalArtifactFiles,
    RecoveryAction,
    ReviewerStatus,
    inspect_reviewer_results,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def provenance(reviewer: Reviewer, index: int, attempt: str) -> dict:
    return {
        "task_id": f"task-{reviewer.value}-{index}",
        "attempt_id": attempt,
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


def main_review(reviewer: Reviewer, index: int, attempt: str) -> dict:
    return {
        **provenance(reviewer, index, attempt),
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


def history(reviewer: Reviewer, model: BenchmarkModel, index: int, attempt: str) -> dict:
    return {
        **provenance(reviewer, index, attempt),
        "model": model.value,
        "output": index,
        "date": "11/10/2026",
        "observations": [],
    }


def profile(reviewer: Reviewer, model: BenchmarkModel, index: int, attempt: str) -> dict:
    return {
        **provenance(reviewer, index, attempt),
        "model_name": model.value,
        "new_strengths": [],
        "new_weaknesses": [],
        "new_hallucination_patterns": [],
        "new_classification_patterns": [],
        "new_narrative_patterns": [],
        "new_factuality_patterns": [],
        "new_finetuning_priorities": [],
    }


def make_complete_reviewer(
    root: Path,
    reviewer: Reviewer,
    *,
    index: int = 4,
    attempt: str = "attempt-1",
) -> ProvisionalArtifactFiles:
    statement = root / f"statement_{index:03d}"
    write_json(statement / f"{reviewer.value}_review.json", main_review(reviewer, index, attempt))
    history_paths = []
    profile_paths = []
    for model in BenchmarkModel:
        history_path = statement / "temporary" / f"{reviewer.value}-{model.value}-history.json"
        profile_path = statement / "temporary" / f"{reviewer.value}-{model.value}-profile.json"
        write_json(history_path, history(reviewer, model, index, attempt))
        write_json(profile_path, profile(reviewer, model, index, attempt))
        history_paths.append(history_path)
        profile_paths.append(profile_path)
    return ProvisionalArtifactFiles(history_paths, profile_paths)


def inspect_one(root: Path, reviewer: Reviewer, files: ProvisionalArtifactFiles, index: int = 4):
    result = inspect_reviewer_results(
        statement_index=index,
        review_cases_root=root,
        provisional_files={reviewer: files},
    )
    return result.reviewers[reviewer]


@pytest.mark.parametrize("reviewer", list(Reviewer))
def test_each_reviewer_can_be_complete(tmp_path, reviewer):
    files = make_complete_reviewer(tmp_path, reviewer)

    result = inspect_one(tmp_path, reviewer, files)

    assert result.status is ReviewerStatus.COMPLETE
    assert result.recovery_action is RecoveryAction.KEEP
    assert result.complete_result is not None


def test_missing_gemini_history_invalidates_entire_set(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.GEMINI)
    files.histories[0].unlink()

    result = inspect_one(tmp_path, Reviewer.GEMINI, files)

    assert result.status is ReviewerStatus.INCOMPLETE
    assert result.recovery_action is RecoveryAction.REPEAT_FULL_REVIEW


def test_missing_profile_proposal_invalidates_entire_set(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.CHATGPT)
    files.profile_proposals[1].unlink()

    result = inspect_one(tmp_path, Reviewer.CHATGPT, files)

    assert result.status is ReviewerStatus.INCOMPLETE


def test_missing_main_review_is_incomplete_when_temporaries_exist(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.GROK)
    (tmp_path / "statement_004/grok_review.json").unlink()

    result = inspect_one(tmp_path, Reviewer.GROK, files)

    assert result.status is ReviewerStatus.INCOMPLETE


def test_corrupt_json_is_invalid(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.CHATGPT)
    files.histories[0].write_text("{broken", encoding="utf-8")

    result = inspect_one(tmp_path, Reviewer.CHATGPT, files)

    assert result.status is ReviewerStatus.INVALID
    assert any(issue.code == "artifact_json_invalid" for issue in result.structural_issues)


def test_wrong_history_model_is_invalid(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.GEMINI)
    value = json.loads(files.histories[0].read_text(encoding="utf-8"))
    value["model"] = "unknown"
    write_json(files.histories[0], value)

    result = inspect_one(tmp_path, Reviewer.GEMINI, files)

    assert result.status is ReviewerStatus.INVALID


def test_wrong_profile_model_name_is_invalid(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.GROK)
    value = json.loads(files.profile_proposals[0].read_text(encoding="utf-8"))
    value["model_name"] = "unknown"
    write_json(files.profile_proposals[0], value)

    result = inspect_one(tmp_path, Reviewer.GROK, files)

    assert result.status is ReviewerStatus.INVALID


def test_duplicate_model_is_invalid(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.CHATGPT)
    duplicate = json.loads(files.histories[0].read_text(encoding="utf-8"))
    write_json(files.histories[1], duplicate)

    result = inspect_one(tmp_path, Reviewer.CHATGPT, files)

    assert result.status is ReviewerStatus.INVALID
    assert any(issue.code == "complete_review_invalid" for issue in result.structural_issues)


def test_wrong_statement_index_is_invalid(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.GEMINI)
    value = json.loads(files.histories[0].read_text(encoding="utf-8"))
    value["output"] = 99
    value["statement_index"] = 99
    write_json(files.histories[0], value)

    result = inspect_one(tmp_path, Reviewer.GEMINI, files)

    assert result.status is ReviewerStatus.INVALID
    assert any(issue.code == "statement_index_mismatch" for issue in result.structural_issues)


def test_inconsistent_main_reviewer_is_invalid(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.GROK)
    path = tmp_path / "statement_004/grok_review.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["reviewer"] = "chatgpt"
    write_json(path, value)

    result = inspect_one(tmp_path, Reviewer.GROK, files)

    assert result.status is ReviewerStatus.INVALID
    assert any(issue.code == "reviewer_mismatch" for issue in result.structural_issues)


def test_mixed_attempts_are_invalid(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.CHATGPT)
    value = json.loads(files.profile_proposals[2].read_text(encoding="utf-8"))
    value["attempt_id"] = "attempt-2"
    write_json(files.profile_proposals[2], value)

    result = inspect_one(tmp_path, Reviewer.CHATGPT, files)

    assert result.status is ReviewerStatus.INVALID
    assert any(issue.code == "provenance_mixed_execution" for issue in result.provenance_issues)


def test_missing_provenance_is_unverified(tmp_path):
    files = make_complete_reviewer(tmp_path, Reviewer.GEMINI)
    for path in (
        tmp_path / "statement_004/gemini_review.json",
        *files.histories,
        *files.profile_proposals,
    ):
        value = json.loads(path.read_text(encoding="utf-8"))
        value.pop("attempt_id")
        write_json(path, value)

    result = inspect_one(tmp_path, Reviewer.GEMINI, files)

    assert result.status is ReviewerStatus.UNVERIFIED
    assert result.recovery_action is RecoveryAction.BLOCK_FOR_PROVENANCE
    assert not result.structural_issues


def test_reviewer_not_started(tmp_path):
    paths = ProvisionalArtifactFiles(
        [tmp_path / "missing-history.json"],
        [tmp_path / "missing-profile.json"],
    )

    result = inspect_one(tmp_path, Reviewer.GROK, paths)

    assert result.status is ReviewerStatus.NOT_STARTED
    assert result.recovery_action is RecoveryAction.START_FULL_REVIEW


def test_historical_review_without_temporaries_is_unverified_not_incomplete(tmp_path):
    statement = tmp_path / "statement_004"
    write_json(statement / "chatgpt_review.json", main_review(Reviewer.CHATGPT, 4, "old"))
    write_json(statement / "adjudication.json", {"statement_index": 4})

    result = inspect_reviewer_results(statement_index=4, review_cases_root=tmp_path)
    chatgpt = result.reviewers[Reviewer.CHATGPT]

    assert result.historical_adjudication_present
    assert chatgpt.status is ReviewerStatus.UNVERIFIED
    assert chatgpt.recovery_action is RecoveryAction.BLOCK_FOR_PROVENANCE


def test_adjudication_barrier_requires_three_complete_reviewers(tmp_path):
    configured = {
        reviewer: make_complete_reviewer(tmp_path, reviewer) for reviewer in Reviewer
    }

    complete = inspect_reviewer_results(
        statement_index=4,
        review_cases_root=tmp_path,
        provisional_files=configured,
    )
    configured[Reviewer.GEMINI].histories[0].unlink()
    blocked = inspect_reviewer_results(
        statement_index=4,
        review_cases_root=tmp_path,
        provisional_files=configured,
    )

    assert complete.adjudication_enabled
    assert not blocked.adjudication_enabled


def test_inspection_does_not_write_or_modify_files(tmp_path):
    configured = {
        reviewer: make_complete_reviewer(tmp_path, reviewer) for reviewer in Reviewer
    }
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    inspect_reviewer_results(
        statement_index=4,
        review_cases_root=tmp_path,
        provisional_files=configured,
    )

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
