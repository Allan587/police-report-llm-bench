import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from automation.contracts import (
    BenchmarkModel,
    CompleteReviewResult,
    HistoryProposalOutput,
    MainReviewOutput,
    ProfileProposalOutput,
    Reviewer,
)
from automation.review import (
    ApplicationPhase,
    CheckStatus,
    FinalizationStatus,
    SelectionAction,
    StatementCompletionStatus,
    apply_adjudicated_results,
    finalize_adjudication_application,
    inspect_adjudication_application,
    inspect_statement_completeness,
    publish_complete_review,
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


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def provenance(reviewer: Reviewer, index: int, attempt: str = "attempt-1") -> dict:
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


def complete_review(reviewer: Reviewer, index: int) -> CompleteReviewResult:
    identity = provenance(reviewer, index)
    return CompleteReviewResult(
        task_id=identity["task_id"],
        attempt_id=identity["attempt_id"],
        statement_index=index,
        reviewer=reviewer,
        main_review=MainReviewOutput(
            index,
            reviewer,
            identity["attempt_id"],
            {
                **identity,
                "model_reviews": [model_review(model) for model in BenchmarkModel],
                "proposed_ideal_report": {
                    "narrative": "narrative",
                    "closing": "",
                    "crime_classification": {
                        "category": "category",
                        "justification": "supported",
                    },
                },
            },
        ),
        histories=tuple(
            HistoryProposalOutput(
                index,
                reviewer,
                identity["attempt_id"],
                model,
                {
                    **identity,
                    "model": model.value,
                    "output": index,
                    "date": "11/10/2026",
                    "observations": [],
                },
            )
            for model in BenchmarkModel
        ),
        profile_proposals=tuple(
            ProfileProposalOutput(
                index,
                reviewer,
                identity["attempt_id"],
                model,
                {
                    **identity,
                    "model_name": model.value,
                    "new_strengths": [],
                    "new_weaknesses": [],
                    "new_hallucination_patterns": [],
                    "new_classification_patterns": [],
                    "new_narrative_patterns": [],
                    "new_factuality_patterns": [],
                    "new_finetuning_priorities": [],
                },
            )
            for model in BenchmarkModel
        ),
        created_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )


def adjudication(index: int) -> dict:
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
        "decision": {"status": "approved", "reason": "valid"},
    }


def history(model: BenchmarkModel, index: int) -> dict:
    return {
        "model": model.value,
        "output": index,
        "date": "11/10/2026",
        "observations": [f"observation-{model.value}"],
    }


def profile(model: BenchmarkModel, index: int, version: int = 2) -> dict:
    return {
        "model_name": model.value,
        "strengths": [
            {
                "pattern": f"pattern-{model.value}",
                "evidence_count": 1,
                "last_observed_output": index,
            }
        ],
        "weaknesses": [],
        "common_hallucinations": [],
        "classification_behavior": {"strengths": [], "weaknesses": []},
        "narrative_behavior": {"strengths": [], "weaknesses": []},
        "factuality_behavior": {"strengths": [], "weaknesses": []},
        "fine_tuning_priorities": [
            {"priority": "preserve fidelity", "priority_level": "high"}
        ],
        "profile_metadata": {
            "outputs_reviewed": index + 1,
            "last_updated": "11/10/2026",
            "version": version,
        },
    }


def make_workspace(tmp_path: Path, index: int = 10) -> dict:
    review_cases = tmp_path / "review_cases"
    statement = review_cases / f"statement_{index:03d}"
    for reviewer in Reviewer:
        publish_complete_review(complete_review(reviewer, index), statement_path=statement)

    feedback = tmp_path / "feedback"
    profiles = tmp_path / "profiles"
    for model in BenchmarkModel:
        write_jsonl(feedback / f"{model.value}_history.jsonl", [history(model, 9)])
        write_json(profiles / f"{model.value}_profile.json", profile(model, 9, 1))

    statements = tmp_path / "statements.jsonl"
    write_jsonl(statements, [{"statement_index": index, "statement": {"report": "source"}}])
    runs = tmp_path / "runs"
    for model, directory in RAW_MODELS.items():
        write_jsonl(
            runs / "run-a" / directory / "generation/draft_reports.jsonl",
            [
                {
                    "run_id": "run-a",
                    "model_name": directory,
                    "statement_index": index,
                    "draft_report": f"report-{model.value}",
                }
            ],
        )
    write_json(runs / "run-a/metadata.json", {"run_id": "run-a"})
    loose = [statement / "temporary/one.json", statement / "temporary/two.json"]
    for position, path in enumerate(loose):
        write_json(path, {"temporary": position})
    return {
        "index": index,
        "statement": statement,
        "review_cases": review_cases,
        "feedback": feedback,
        "profiles_root": profiles,
        "statements": statements,
        "runs": runs,
        "loose": loose,
        "adjudication": adjudication(index),
        "histories": [history(model, index) for model in BenchmarkModel],
        "profiles": [profile(model, index) for model in BenchmarkModel],
    }


def prepare_application(workspace: dict, interrupt_stage: str | None = None) -> Path:
    def interrupt(stage):
        if stage == interrupt_stage:
            raise RuntimeError("simulated interruption")

    try:
        result = apply_adjudicated_results(
            statement_index=workspace["index"],
            statement_path=workspace["statement"],
            feedback_root=workspace["feedback"],
            profiles_root=workspace["profiles_root"],
            adjudication=workspace["adjudication"],
            histories=workspace["histories"],
            profiles=workspace["profiles"],
            run_id="run-a",
            failure_injector=interrupt if interrupt_stage else None,
        )
        return result.application.application_path
    except RuntimeError as error:
        assert "simulated interruption" in str(error)
        return next((workspace["statement"] / ".adjudication_applications").iterdir())


def finalize(workspace: dict, application_path: Path, **kwargs):
    return finalize_adjudication_application(
        application_path=application_path,
        statement_path=workspace["statement"],
        feedback_root=workspace["feedback"],
        profiles_root=workspace["profiles_root"],
        evidence_path=workspace["statement"] / "application_evidence.json",
        temporary_paths=workspace["loose"],
        **kwargs,
    )


def completeness(workspace: dict):
    return inspect_statement_completeness(
        statement_index=workspace["index"],
        statements_path=workspace["statements"],
        runs_root=workspace["runs"],
        run="run-a",
        review_cases_root=workspace["review_cases"],
        feedback_root=workspace["feedback"],
        profiles_root=workspace["profiles_root"],
        application_evidence_path=workspace["statement"] / "application_evidence.json",
    )


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_recovers_prepared_application(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace, "after_prepare")

    result = finalize(workspace, application)

    assert result.application.phase is ApplicationPhase.APPLIED
    assert result.status is FinalizationStatus.FINALIZED


@pytest.mark.parametrize(
    "stage",
    [
        "after_replace:2:history:gemma",
        "after_replace:4:history:llama",
        "after_replace:5:profile:gemma",
    ],
)
def test_recovers_interruption_at_each_accumulated_stage(tmp_path, stage):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace, stage)
    before = inspect_adjudication_application(
        application_path=application,
        statement_path=workspace["statement"],
        feedback_root=workspace["feedback"],
        profiles_root=workspace["profiles_root"],
    )

    result = finalize(workspace, application)

    assert before.phase is ApplicationPhase.INTERRUPTED
    assert result.application.phase is ApplicationPhase.APPLIED
    for model in BenchmarkModel:
        assert sum(
            item.get("output") == 10
            for item in read_jsonl(workspace["feedback"] / f"{model.value}_history.jsonl")
        ) == 1


def test_invalid_application_requires_intervention(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace, "after_prepare")
    manifest = application / "manifest.json"
    value = json.loads(manifest.read_text())
    value["run_id"] = "tampered"
    write_json(manifest, value)

    with pytest.raises(ValueError, match="invalid"):
        finalize(workspace, application)

    assert not (workspace["statement"] / "application_evidence.json").exists()


def test_recovery_does_not_duplicate_history_records(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace, "after_replace:2:history:gemma")

    finalize(workspace, application)
    finalize(workspace, application)

    for model in BenchmarkModel:
        records = read_jsonl(workspace["feedback"] / f"{model.value}_history.jsonl")
        assert len([item for item in records if item.get("output") == 10]) == 1


def test_recovery_rejects_overwriting_later_profiles(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace, "after_replace:4:history:llama")
    later = profile(BenchmarkModel.GEMMA, 11, 3)
    write_json(workspace["profiles_root"] / "gemma_profile.json", later)

    with pytest.raises(ValueError, match="invalid"):
        finalize(workspace, application)

    assert json.loads((workspace["profiles_root"] / "gemma_profile.json").read_text()) == later


def test_recovery_rejects_overwriting_later_histories(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace, "after_replace:4:history:llama")
    path = workspace["feedback"] / "gemma_history.jsonl"
    later = read_jsonl(path) + [history(BenchmarkModel.GEMMA, 11)]
    write_jsonl(path, later)

    with pytest.raises(ValueError, match="invalid"):
        finalize(workspace, application)

    assert read_jsonl(path)[-1] == history(BenchmarkModel.GEMMA, 11)


def test_final_evidence_hashes_match_real_files(tmp_path):
    workspace = make_workspace(tmp_path)
    result = finalize(workspace, prepare_application(workspace))

    evidence = result.evidence
    assert evidence["schema_version"] == 2
    assert evidence["review_adjudication_sha256"] == canonical_digest(workspace["adjudication"])
    for model in BenchmarkModel:
        assert evidence["history_application"]["records"][model.value] == canonical_digest(
            history(model, 10)
        )
        stored = json.loads(
            (workspace["profiles_root"] / f"{model.value}_profile.json").read_text()
        )
        profile_evidence = evidence["profile_application"]["profiles"][model.value]
        assert profile_evidence["sha256"] == canonical_digest(stored)
        assert profile_evidence["applied_profile"] == stored


def test_completeness_rejects_tampered_evidence_hash(tmp_path):
    workspace = make_workspace(tmp_path)
    finalize(workspace, prepare_application(workspace))
    evidence_path = workspace["statement"] / "application_evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["history_application"]["records"]["gemma"] = "0" * 64
    write_json(evidence_path, evidence)

    result = completeness(workspace)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert result.checks["application_evidence"] is CheckStatus.FAIL


def test_completeness_rejects_tampered_applied_profile_snapshot(tmp_path):
    workspace = make_workspace(tmp_path)
    finalize(workspace, prepare_application(workspace))
    evidence_path = workspace["statement"] / "application_evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["profile_application"]["profiles"]["gemma"]["applied_profile"][
        "profile_metadata"
    ]["outputs_reviewed"] = 999
    write_json(evidence_path, evidence)

    result = completeness(workspace)

    assert result.status is StatementCompletionStatus.INCOMPLETE
    assert result.checks["application_evidence"] is CheckStatus.FAIL


def test_conflicting_evidence_blocks_cleanup(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace)
    write_json(workspace["statement"] / "application_evidence.json", {"conflict": True})

    with pytest.raises(ValueError, match="different final evidence"):
        finalize(workspace, application)

    assert all(path.exists() for path in workspace["loose"])


def test_cleanup_removes_only_explicit_loose_temporaries(tmp_path):
    workspace = make_workspace(tmp_path)
    result = finalize(workspace, prepare_application(workspace))

    assert result.cleanup.removed_count == 2
    assert all(not path.exists() for path in workspace["loose"])


def test_cleanup_preserves_active_review_sets(tmp_path):
    workspace = make_workspace(tmp_path)
    before = snapshot(workspace["statement"] / ".review_sets")

    finalize(workspace, prepare_application(workspace))

    assert snapshot(workspace["statement"] / ".review_sets") == before


def test_cleanup_is_idempotent(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace)
    first = finalize(workspace, application)
    second = finalize(workspace, application)

    assert first.cleanup.removed_count == 2
    assert second.cleanup.removed_count == 0
    assert second.status is FinalizationStatus.UNCHANGED


def test_cleanup_failure_is_recoverable_and_delays_evidence(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace)

    def interrupt(stage):
        if stage.startswith("after_cleanup:1:"):
            raise RuntimeError("simulated cleanup failure")

    with pytest.raises(RuntimeError, match="cleanup failure"):
        finalize(workspace, application, failure_injector=interrupt)
    assert not (workspace["statement"] / "application_evidence.json").exists()

    result = finalize(workspace, application)

    assert result.cleanup.removed_count == 1
    assert all(not path.exists() for path in workspace["loose"])


def test_new_statement_becomes_complete_only_after_finalization(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace)

    before = completeness(workspace)
    finalize(workspace, application)
    after = completeness(workspace)

    assert before.status is StatementCompletionStatus.INCOMPLETE
    assert after.status is StatementCompletionStatus.COMPLETE
    assert after.can_advance


def test_two_consecutive_statements_recover_accumulate_and_advance(tmp_path):
    workspace_10 = make_workspace(tmp_path, index=10)
    (workspace_10["review_cases"] / "statement_009").mkdir()
    application_10 = prepare_application(
        workspace_10, "after_replace:2:history:gemma"
    )
    interrupted_10 = inspect_adjudication_application(
        application_path=application_10,
        statement_path=workspace_10["statement"],
        feedback_root=workspace_10["feedback"],
        profiles_root=workspace_10["profiles_root"],
    )
    finalize(workspace_10, application_10)
    complete_10 = completeness(workspace_10)
    first_selection = select_statement_from_verification(
        workspace_10["review_cases"], complete_10
    )

    assert interrupted_10.phase is ApplicationPhase.INTERRUPTED
    assert complete_10.status is StatementCompletionStatus.COMPLETE
    assert first_selection.action is SelectionAction.ADVANCE
    assert first_selection.statement_index == 11

    index = 11
    statement_11 = workspace_10["review_cases"] / "statement_011"
    for reviewer in Reviewer:
        publish_complete_review(
            complete_review(reviewer, index), statement_path=statement_11
        )
    statement_records = read_jsonl(workspace_10["statements"])
    statement_records.append(
        {"statement_index": index, "statement": {"report": "source-11"}}
    )
    write_jsonl(workspace_10["statements"], statement_records)
    for directory in RAW_MODELS.values():
        output_path = (
            workspace_10["runs"]
            / "run-a"
            / directory
            / "generation/draft_reports.jsonl"
        )
        records = read_jsonl(output_path)
        records.append(
            {
                "run_id": "run-a",
                "model_name": directory,
                "statement_index": index,
                "draft_report": f"report-{directory}-11",
            }
        )
        write_jsonl(output_path, records)
    loose_11 = [
        statement_11 / "temporary/one.json",
        statement_11 / "temporary/two.json",
    ]
    for position, path in enumerate(loose_11):
        write_json(path, {"temporary": position})
    workspace_11 = {
        **workspace_10,
        "index": index,
        "statement": statement_11,
        "loose": loose_11,
        "adjudication": adjudication(index),
        "histories": [history(model, index) for model in BenchmarkModel],
        "profiles": [profile(model, index, 3) for model in BenchmarkModel],
    }

    finalize(workspace_11, prepare_application(workspace_11))
    complete_11 = completeness(workspace_11)
    reverified_10 = completeness(workspace_10)
    final_selection = select_statement_from_verification(
        workspace_11["review_cases"],
        complete_11,
        earlier_verifications={10: reverified_10},
    )

    assert reverified_10.status is StatementCompletionStatus.COMPLETE
    assert complete_11.status is StatementCompletionStatus.COMPLETE
    assert final_selection.action is SelectionAction.ADVANCE
    assert final_selection.statement_index == 12
    for model in BenchmarkModel:
        records = read_jsonl(
            workspace_11["feedback"] / f"{model.value}_history.jsonl"
        )
        assert [record["output"] for record in records] == [9, 10, 11]
        current_profile = json.loads(
            (
                workspace_11["profiles_root"] / f"{model.value}_profile.json"
            ).read_text()
        )
        evidence_10 = json.loads(
            (
                workspace_10["statement"] / "application_evidence.json"
            ).read_text()
        )
        assert current_profile["profile_metadata"] == {
            "outputs_reviewed": 12,
            "last_updated": "11/10/2026",
            "version": 3,
        }
        assert (
            evidence_10["profile_application"]["profiles"][model.value]["sha256"]
            != canonical_digest(current_profile)
        )


def test_incomplete_application_does_not_make_statement_complete(tmp_path):
    workspace = make_workspace(tmp_path)
    prepare_application(workspace, "after_replace:2:history:gemma")

    result = completeness(workspace)

    assert result.status is not StatementCompletionStatus.COMPLETE
    assert not result.can_advance


@pytest.mark.parametrize("index", range(10))
def test_historical_statements_are_protected(tmp_path, index):
    statement = tmp_path / f"statement_{index:03d}"
    marker = statement / "marker.json"
    write_json(marker, {"preserve": True})

    with pytest.raises(PermissionError, match="historical"):
        finalize_adjudication_application(
            application_path=statement / ".adjudication_applications/missing",
            statement_path=statement,
            feedback_root=tmp_path / "feedback",
            profiles_root=tmp_path / "profiles",
            evidence_path=statement / "application_evidence.json",
            temporary_paths=(marker,),
        )

    assert marker.exists()


def test_finalization_does_not_modify_other_statement(tmp_path):
    workspace = make_workspace(tmp_path)
    other = workspace["review_cases"] / "statement_011/marker.json"
    write_json(other, {"preserve": True})
    before = other.read_bytes()

    finalize(workspace, prepare_application(workspace))

    assert other.read_bytes() == before


def test_cleanup_rejects_active_set_artifact(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace)
    active_artifact = next((workspace["statement"] / ".review_sets").rglob("gemma_history.json"))

    with pytest.raises(ValueError, match="confirmed review sets"):
        finalize_adjudication_application(
            application_path=application,
            statement_path=workspace["statement"],
            feedback_root=workspace["feedback"],
            profiles_root=workspace["profiles_root"],
            evidence_path=workspace["statement"] / "application_evidence.json",
            temporary_paths=(active_artifact,),
        )

    assert active_artifact.exists()


def test_finalization_preserves_decision_status_semantics(tmp_path):
    workspace = make_workspace(tmp_path)
    workspace["adjudication"]["decision"]["status"] = "needs_human_review"

    finalize(workspace, prepare_application(workspace))
    result = completeness(workspace)

    assert result.status is StatementCompletionStatus.COMPLETE


def test_evidence_rejects_changed_active_review_identity(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace)
    active = workspace["statement"] / ".review_sets/chatgpt/active.json"
    value = json.loads(active.read_text())
    value["attempt_id"] = "changed"
    write_json(active, value)

    with pytest.raises(ValueError, match="active review"):
        finalize(workspace, application)


def test_b_manifest_hashes_match_completeness_canonical_contract(tmp_path):
    workspace = make_workspace(tmp_path)
    application = prepare_application(workspace)
    manifest = json.loads((application / "manifest.json").read_text())

    assert manifest["review_adjudication_sha256"] == canonical_digest(workspace["adjudication"])
    assert manifest["history_application"]["records"]["llama"] == canonical_digest(
        history(BenchmarkModel.LLAMA, 10)
    )
