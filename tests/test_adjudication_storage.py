import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import automation.review.adjudication_storage as storage_module
import automation.review.completeness as completeness_module
from automation.contracts import (
    BenchmarkModel,
    CompleteReviewResult,
    HistoryProposalOutput,
    MainReviewOutput,
    ProfileProposalOutput,
    Reviewer,
)
from automation.review import (
    AdjudicationApplyStatus,
    ApplicationPhase,
    apply_adjudicated_results,
    inspect_adjudication_application,
    publish_complete_review,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def adjudication(index: int = 10, reason: str = "valid") -> dict:
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
        "decision": {"status": "approved", "reason": reason},
    }


def history(model: BenchmarkModel, index: int = 10) -> dict:
    return {
        "model": model.value,
        "output": index,
        "date": "11/10/2026",
        "observations": [f"observation-{model.value}"],
    }


def profile(model: BenchmarkModel, index: int = 10, version: int = 2) -> dict:
    pattern = {
        "pattern": f"pattern-{model.value}",
        "evidence_count": 1,
        "last_observed_output": index,
    }
    return {
        "model_name": model.value,
        "strengths": [pattern],
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


def attempts(index: int = 10) -> dict:
    return {
        reviewer: {
            "task_id": f"task-{reviewer.value}-{index}",
            "attempt_id": f"attempt-{reviewer.value}-{index}",
        }
        for reviewer in Reviewer
    }


def complete_review(reviewer: Reviewer, index: int) -> CompleteReviewResult:
    identity = {
        **attempts(index)[reviewer],
        "statement_index": index,
        "reviewer": reviewer.value,
    }
    model_reviews = [
        {
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
        for model in BenchmarkModel
    ]
    main = MainReviewOutput(
        index,
        reviewer,
        identity["attempt_id"],
        {
            **identity,
            "model_reviews": model_reviews,
            "proposed_ideal_report": {
                "narrative": "synthetic",
                "closing": "",
                "crime_classification": {
                    "category": "category",
                    "justification": "supported",
                },
            },
        },
    )
    histories = tuple(
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
    )
    proposals = tuple(
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
    )
    return CompleteReviewResult(
        task_id=identity["task_id"],
        attempt_id=identity["attempt_id"],
        statement_index=index,
        reviewer=reviewer,
        main_review=main,
        histories=histories,
        profile_proposals=proposals,
        created_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )


def workspace(tmp_path: Path, index: int = 10) -> dict:
    statement = tmp_path / "review_cases" / f"statement_{index:03d}"
    feedback = tmp_path / "feedback"
    profiles = tmp_path / "profiles"
    for model in BenchmarkModel:
        write_jsonl(
            feedback / f"{model.value}_history.jsonl",
            [history(model, 9)],
        )
        write_json(profiles / f"{model.value}_profile.json", profile(model, 9, 1))
    if index >= 10:
        for reviewer in Reviewer:
            publish_complete_review(
                complete_review(reviewer, index), statement_path=statement
            )
    return {
        "statement_index": index,
        "statement_path": statement,
        "feedback_root": feedback,
        "profiles_root": profiles,
        "adjudication": adjudication(index),
        "histories": [history(model, index) for model in BenchmarkModel],
        "profiles": [profile(model, index) for model in BenchmarkModel],
        "run_id": "run-a",
        "review_attempts": attempts(index),
    }


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_applies_valid_final_adjudication(tmp_path):
    args = workspace(tmp_path)

    result = apply_adjudicated_results(**args)

    assert result.status is AdjudicationApplyStatus.APPLIED
    assert json.loads((args["statement_path"] / "adjudication.json").read_text()) == args["adjudication"]


def test_rejects_invalid_adjudication_before_accumulated_writes(tmp_path):
    args = workspace(tmp_path)
    before = snapshot(tmp_path)
    args["adjudication"]["quality_control"].pop("format_valid")

    with pytest.raises(ValueError, match="quality_control"):
        apply_adjudicated_results(**args)

    assert snapshot(tmp_path) == before


def test_applies_exactly_three_histories(tmp_path):
    args = workspace(tmp_path)

    apply_adjudicated_results(**args)

    for model in BenchmarkModel:
        records = read_jsonl(args["feedback_root"] / f"{model.value}_history.jsonl")
        assert records[-1] == history(model)
        assert [item["output"] for item in records] == [9, 10]


def test_rejects_duplicate_history_already_in_destination(tmp_path):
    args = workspace(tmp_path)
    path = args["feedback_root"] / "gemma_history.jsonl"
    write_jsonl(path, [history(BenchmarkModel.GEMMA, 10)] * 2)

    with pytest.raises(ValueError, match="duplicate history"):
        apply_adjudicated_results(**args)


def test_updates_exactly_three_complete_profiles(tmp_path):
    args = workspace(tmp_path)

    apply_adjudicated_results(**args)

    for model in BenchmarkModel:
        stored = json.loads(
            (args["profiles_root"] / f"{model.value}_profile.json").read_text()
        )
        assert stored == profile(model)
        assert stored["strengths"][0]["evidence_count"] == 1
        assert stored["strengths"][0]["last_observed_output"] == 10


def test_rejects_profile_with_wrong_model(tmp_path):
    args = workspace(tmp_path)
    args["profiles"][0]["model_name"] = "llama"

    with pytest.raises(ValueError, match="duplicate model"):
        apply_adjudicated_results(**args)


def test_same_application_is_idempotent(tmp_path):
    args = workspace(tmp_path)
    first = apply_adjudicated_results(**args)
    before = snapshot(tmp_path)

    second = apply_adjudicated_results(**args)

    assert first.status is AdjudicationApplyStatus.APPLIED
    assert second.status is AdjudicationApplyStatus.UNCHANGED
    assert second.application.application_id == first.application.application_id
    assert snapshot(tmp_path) == before


def test_rejects_conflicting_reapplication(tmp_path):
    args = workspace(tmp_path)
    apply_adjudicated_results(**args)
    conflicting = dict(args)
    conflicting["adjudication"] = adjudication(reason="different")

    with pytest.raises(ValueError, match="different adjudication"):
        apply_adjudicated_results(**conflicting)


def test_rejects_different_profile_set_for_same_applied_adjudication(tmp_path):
    args = workspace(tmp_path)
    apply_adjudicated_results(**args)
    conflicting = dict(args)
    conflicting["profiles"] = [profile(model) for model in BenchmarkModel]
    conflicting["profiles"][0]["strengths"][0]["pattern"] = "conflicting pattern"

    with pytest.raises(ValueError, match="changes content|different adjudication application"):
        apply_adjudicated_results(**conflicting)


@pytest.mark.parametrize("index", range(10))
def test_historical_statements_are_protected(tmp_path, index):
    args = workspace(tmp_path, index)
    before = snapshot(tmp_path)

    with pytest.raises(PermissionError, match="historical"):
        apply_adjudicated_results(**args)

    assert snapshot(tmp_path) == before


def test_failure_during_preparation_changes_no_destination(tmp_path, monkeypatch):
    args = workspace(tmp_path)
    before_feedback = snapshot(args["feedback_root"])
    before_profiles = snapshot(args["profiles_root"])
    original = storage_module._write_prepared
    calls = 0

    def fail_second(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated preparation failure")
        original(path, content)

    monkeypatch.setattr(storage_module, "_write_prepared", fail_second)

    with pytest.raises(OSError, match="preparation"):
        apply_adjudicated_results(**args)

    assert snapshot(args["feedback_root"]) == before_feedback
    assert snapshot(args["profiles_root"]) == before_profiles
    assert not (args["statement_path"] / "adjudication.json").exists()


def test_interruption_during_application_is_visible_and_reapplicable(tmp_path):
    args = workspace(tmp_path)
    application_path = None

    def interrupt(stage):
        nonlocal application_path
        roots = list((args["statement_path"] / ".adjudication_applications").glob("*"))
        if roots:
            application_path = roots[0]
        if stage.startswith("after_replace:2:"):
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        apply_adjudicated_results(**args, failure_injector=interrupt)

    interrupted = inspect_adjudication_application(
        application_path=application_path,
        statement_path=args["statement_path"],
        feedback_root=args["feedback_root"],
        profiles_root=args["profiles_root"],
    )
    assert interrupted.phase is ApplicationPhase.INTERRUPTED

    resumed = apply_adjudicated_results(**args)
    assert resumed.status is AdjudicationApplyStatus.APPLIED
    assert resumed.application.phase is ApplicationPhase.APPLIED


def test_application_result_exposes_content_for_future_evidence(tmp_path):
    args = workspace(tmp_path)

    result = apply_adjudicated_results(**args)
    inspected = inspect_adjudication_application(
        application_path=result.application.application_path,
        statement_path=args["statement_path"],
        feedback_root=args["feedback_root"],
        profiles_root=args["profiles_root"],
    )

    assert inspected.phase is ApplicationPhase.APPLIED
    assert set(inspected.history_sha256) == set(BenchmarkModel)
    assert set(inspected.profile_sha256) == set(BenchmarkModel)
    assert inspected.run_id == "run-a"
    assert set(inspected.review_attempts) == set(Reviewer)
    manifest = json.loads(inspected.manifest_path.read_text(encoding="utf-8"))
    assert manifest["review_adjudication_sha256"] == inspected.adjudication_sha256
    assert manifest["history_application"]["adjudication_id"] == inspected.application_id
    assert manifest["profile_application"]["adjudication_id"] == inspected.application_id
    assert not (args["statement_path"] / "application_evidence.json").exists()


def test_inspector_rejects_tampered_application_manifest(tmp_path):
    args = workspace(tmp_path)
    result = apply_adjudicated_results(**args)
    manifest = json.loads(result.application.manifest_path.read_text(encoding="utf-8"))
    manifest["history_application"]["records"]["gemma"] = "0" * 64
    result.application.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    inspected = inspect_adjudication_application(
        application_path=result.application.application_path,
        statement_path=args["statement_path"],
        feedback_root=args["feedback_root"],
        profiles_root=args["profiles_root"],
    )

    assert inspected.phase is ApplicationPhase.INVALID


def test_application_does_not_modify_other_statement(tmp_path):
    args = workspace(tmp_path)
    other = tmp_path / "review_cases/statement_011/adjudication.json"
    write_json(other, {"preserve": True})
    before = other.read_bytes()

    apply_adjudicated_results(**args)

    assert other.read_bytes() == before


def test_invalid_profile_prevents_all_destination_updates(tmp_path):
    args = workspace(tmp_path)
    before = snapshot(tmp_path)
    args["profiles"][2]["profile_metadata"].pop("version")

    with pytest.raises(ValueError, match="profile_metadata"):
        apply_adjudicated_results(**args)

    assert snapshot(tmp_path) == before


def test_preserves_json_and_jsonl_formats(tmp_path):
    args = workspace(tmp_path)

    apply_adjudicated_results(**args)

    assert isinstance(json.loads((args["statement_path"] / "adjudication.json").read_text()), dict)
    for model in BenchmarkModel:
        history_path = args["feedback_root"] / f"{model.value}_history.jsonl"
        assert history_path.read_bytes().endswith(b"\n")
        assert len(read_jsonl(history_path)) == 2
        assert isinstance(
            json.loads((args["profiles_root"] / f"{model.value}_profile.json").read_text()),
            dict,
        )


def test_rejects_conflicting_history_without_touching_profiles(tmp_path):
    args = workspace(tmp_path)
    write_jsonl(
        args["feedback_root"] / "mistral_history.jsonl",
        [{**history(BenchmarkModel.MISTRAL), "observations": ["different"]}],
    )
    before_profiles = snapshot(args["profiles_root"])

    with pytest.raises(ValueError, match="conflicting history"):
        apply_adjudicated_results(**args)

    assert snapshot(args["profiles_root"]) == before_profiles


def test_rejects_review_attempts_that_do_not_match_active_sets(tmp_path):
    args = workspace(tmp_path)
    # One active reference is already enough to make a partially versioned context unsafe.
    active = args["statement_path"] / ".review_sets/chatgpt/active.json"
    write_json(active, {"invalid": True})

    with pytest.raises(ValueError, match="active reviewer sets"):
        apply_adjudicated_results(**args)


def test_rejects_supplied_identity_that_differs_from_complete_active_sets(tmp_path):
    args = workspace(tmp_path)
    args["review_attempts"][Reviewer.GROK]["attempt_id"] = "caller-mismatch"

    with pytest.raises(ValueError, match="do not match"):
        apply_adjudicated_results(**args)

    assert not (args["statement_path"] / "adjudication.json").exists()


@pytest.mark.parametrize(
    "artifact_key",
    [
        "adjudication",
        "history:gemma",
        "history:mistral",
        "history:llama",
        "profile:gemma",
        "profile:mistral",
        "profile:llama",
    ],
)
def test_corrupt_prepared_artifact_blocks_reapplication_without_destination_changes(
    tmp_path, artifact_key
):
    args = workspace(tmp_path)
    application_path = None

    def stop_after_prepare(stage):
        nonlocal application_path
        if stage == "after_prepare":
            application_path = next(
                path
                for path in (args["statement_path"] / ".adjudication_applications").iterdir()
                if path.is_dir()
            )
            raise RuntimeError("prepared only")

    with pytest.raises(RuntimeError, match="prepared only"):
        apply_adjudicated_results(**args, failure_injector=stop_after_prepare)

    manifest = json.loads((application_path / "manifest.json").read_text(encoding="utf-8"))
    prepared = application_path / manifest["files"][artifact_key]["prepared_path"]
    prepared.write_bytes(prepared.read_bytes() + b"corrupt")
    before_feedback = snapshot(args["feedback_root"])
    before_profiles = snapshot(args["profiles_root"])
    assert not (args["statement_path"] / "adjudication.json").exists()

    with pytest.raises(ValueError, match="existing adjudication application is invalid"):
        apply_adjudicated_results(**args)

    assert snapshot(args["feedback_root"]) == before_feedback
    assert snapshot(args["profiles_root"]) == before_profiles
    assert not (args["statement_path"] / "adjudication.json").exists()


def test_rejects_adjudication_without_three_active_review_sets(tmp_path):
    args = workspace(tmp_path)
    review_sets = args["statement_path"] / ".review_sets"
    for active in review_sets.glob("*/active.json"):
        active.unlink()
    before_feedback = snapshot(args["feedback_root"])
    before_profiles = snapshot(args["profiles_root"])

    with pytest.raises(ValueError, match="three complete active reviewer sets"):
        apply_adjudicated_results(**args)

    assert snapshot(args["feedback_root"]) == before_feedback
    assert snapshot(args["profiles_root"]) == before_profiles
    assert not (args["statement_path"] / "adjudication.json").exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["profile_metadata"].update(version=0), "version"),
        (
            lambda value: value["profile_metadata"].update(outputs_reviewed=0),
            "outputs_reviewed",
        ),
        (
            lambda value: value["strengths"][0].update(evidence_count=0),
            "evidence_count",
        ),
        (
            lambda value: value["strengths"][0].update(last_observed_output=8),
            "last_observed_output",
        ),
    ],
)
def test_rejects_accumulated_profile_regressions(tmp_path, mutation, message):
    args = workspace(tmp_path)
    candidate = args["profiles"][0]
    # Retain the current pattern so its cumulative counters can be compared.
    current = profile(BenchmarkModel.GEMMA, 9, 1)
    candidate["strengths"] = current["strengths"]
    mutation(candidate)
    before = snapshot(args["profiles_root"])

    with pytest.raises(ValueError, match=message):
        apply_adjudicated_results(**args)

    assert snapshot(args["profiles_root"]) == before


def test_rejects_evidence_regression_for_same_text_in_different_categories(tmp_path):
    args = workspace(tmp_path)
    current = profile(BenchmarkModel.GEMMA, 9, 1)
    repeated = "same text, distinct category"
    current["strengths"] = [
        {"pattern": repeated, "evidence_count": 5, "last_observed_output": 9}
    ]
    current["weaknesses"] = [
        {"pattern": repeated, "evidence_count": 2, "last_observed_output": 9}
    ]
    write_json(args["profiles_root"] / "gemma_profile.json", current)
    candidate = args["profiles"][0]
    candidate["strengths"] = [
        {"pattern": repeated, "evidence_count": 4, "last_observed_output": 10}
    ]
    candidate["weaknesses"] = [
        {"pattern": repeated, "evidence_count": 2, "last_observed_output": 10}
    ]
    before = snapshot(args["profiles_root"])

    with pytest.raises(ValueError, match="evidence_count"):
        apply_adjudicated_results(**args)

    assert snapshot(args["profiles_root"]) == before


def test_allows_same_pattern_text_in_distinct_categories_without_regression(tmp_path):
    args = workspace(tmp_path)
    current = profile(BenchmarkModel.GEMMA, 9, 1)
    repeated = "same text, distinct category"
    current["strengths"] = [
        {"pattern": repeated, "evidence_count": 2, "last_observed_output": 9}
    ]
    current["weaknesses"] = [
        {"pattern": repeated, "evidence_count": 3, "last_observed_output": 9}
    ]
    write_json(args["profiles_root"] / "gemma_profile.json", current)
    candidate = args["profiles"][0]
    candidate["strengths"] = [
        {"pattern": repeated, "evidence_count": 3, "last_observed_output": 10}
    ]
    candidate["weaknesses"] = [
        {"pattern": repeated, "evidence_count": 3, "last_observed_output": 10}
    ]

    result = apply_adjudicated_results(**args)

    assert result.application.phase is ApplicationPhase.APPLIED


@pytest.mark.parametrize(
    "artifact_key",
    [
        "history:gemma",
        "history:mistral",
        "history:llama",
        "profile:gemma",
        "profile:mistral",
        "profile:llama",
    ],
)
def test_manifest_source_hash_rewrite_cannot_rebind_preimage(tmp_path, artifact_key):
    args = workspace(tmp_path)
    application_path = None

    def stop_after_prepare(stage):
        nonlocal application_path
        if stage == "after_prepare":
            application_path = next(
                path
                for path in (args["statement_path"] / ".adjudication_applications").iterdir()
                if path.is_dir()
            )
            raise RuntimeError("prepared only")

    with pytest.raises(RuntimeError, match="prepared only"):
        apply_adjudicated_results(**args, failure_injector=stop_after_prepare)

    if artifact_key.startswith("history:"):
        model = artifact_key.partition(":")[2]
        destination = args["feedback_root"] / f"{model}_history.jsonl"
        destination.write_bytes(destination.read_bytes() + b'{"later":true}\n')
    else:
        model = artifact_key.partition(":")[2]
        destination = args["profiles_root"] / f"{model}_profile.json"
        value = json.loads(destination.read_text(encoding="utf-8"))
        value["profile_metadata"]["last_updated"] = "later"
        write_json(destination, value)
    manifest_path = application_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][artifact_key]["source_sha256"] = storage_module._file_sha256(
        destination
    )
    write_json(manifest_path, manifest)
    before = snapshot(tmp_path)

    inspected = inspect_adjudication_application(
        application_path=application_path,
        statement_path=args["statement_path"],
        feedback_root=args["feedback_root"],
        profiles_root=args["profiles_root"],
    )
    assert inspected.phase is ApplicationPhase.INVALID
    with pytest.raises(ValueError, match="invalid"):
        apply_adjudicated_results(**args)

    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "artifact_key",
    [
        "adjudication",
        "history:gemma",
        "history:mistral",
        "history:llama",
        "profile:gemma",
        "profile:mistral",
        "profile:llama",
    ],
)
def test_symlinked_prepared_artifact_is_invalid(tmp_path, artifact_key):
    args = workspace(tmp_path)
    application_path = None

    def stop_after_prepare(stage):
        nonlocal application_path
        if stage == "after_prepare":
            application_path = next(
                path
                for path in (args["statement_path"] / ".adjudication_applications").iterdir()
                if path.is_dir()
            )
            raise RuntimeError("prepared only")

    with pytest.raises(RuntimeError, match="prepared only"):
        apply_adjudicated_results(**args, failure_injector=stop_after_prepare)
    manifest = json.loads((application_path / "manifest.json").read_text(encoding="utf-8"))
    prepared = application_path / manifest["files"][artifact_key]["prepared_path"]
    external = tmp_path / f"outside-{artifact_key.replace(':', '-')}.bin"
    external.write_bytes(prepared.read_bytes())
    prepared.unlink()
    prepared.symlink_to(external)
    before = snapshot(tmp_path)

    inspected = inspect_adjudication_application(
        application_path=application_path,
        statement_path=args["statement_path"],
        feedback_root=args["feedback_root"],
        profiles_root=args["profiles_root"],
    )

    assert inspected.phase is ApplicationPhase.INVALID
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("internal_name", ["manifest.json", "status.json"])
def test_symlinked_application_control_file_is_invalid(tmp_path, internal_name):
    args = workspace(tmp_path)
    application_path = None

    def stop_after_prepare(stage):
        nonlocal application_path
        if stage == "after_prepare":
            application_path = next(
                path
                for path in (args["statement_path"] / ".adjudication_applications").iterdir()
                if path.is_dir()
            )
            raise RuntimeError("prepared only")

    with pytest.raises(RuntimeError, match="prepared only"):
        apply_adjudicated_results(**args, failure_injector=stop_after_prepare)
    internal = application_path / internal_name
    external = tmp_path / f"outside-{internal_name}"
    external.write_bytes(internal.read_bytes())
    internal.unlink()
    internal.symlink_to(external)

    inspected = inspect_adjudication_application(
        application_path=application_path,
        statement_path=args["statement_path"],
        feedback_root=args["feedback_root"],
        profiles_root=args["profiles_root"],
    )

    assert inspected.phase is ApplicationPhase.INVALID


def test_rejects_symlinked_profile_destination_without_touching_external_file(tmp_path):
    args = workspace(tmp_path)
    destination = args["profiles_root"] / "gemma_profile.json"
    destination.unlink()
    external = tmp_path / "outside-profile.json"
    external.write_text('{"outside":true}', encoding="utf-8")
    destination.symlink_to(external)
    before = external.read_bytes()

    with pytest.raises(ValueError, match="symbolic links"):
        apply_adjudicated_results(**args)

    assert external.read_bytes() == before


@pytest.mark.parametrize("valid", [True, False])
def test_adjudication_writer_and_completeness_use_compatible_validation(tmp_path, valid):
    value = adjudication()
    if not valid:
        value["unexpected"] = True
    statement = tmp_path / "statement_010"
    write_json(statement / "adjudication.json", value)

    try:
        storage_module._validate_adjudication(value, 10)
        writer_accepts = True
    except ValueError:
        writer_accepts = False
    check, _, _ = completeness_module._validate_adjudication(
        statement_index=10, statement_path=statement
    )

    assert writer_accepts is valid
    assert (check is completeness_module.CheckStatus.PASS) is valid


@pytest.mark.parametrize("valid", [True, False])
def test_profile_writer_and_completeness_use_compatible_validation(valid):
    value = profile(BenchmarkModel.GEMMA)
    if not valid:
        value["strengths"][0]["unexpected"] = True

    try:
        storage_module._validate_profile(value, BenchmarkModel.GEMMA)
        writer_accepts = True
    except ValueError:
        writer_accepts = False

    assert writer_accepts is valid
    assert completeness_module._valid_profile(value, BenchmarkModel.GEMMA) is valid
