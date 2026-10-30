import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import automation.review.review_storage as storage_module
from automation.contracts import (
    BenchmarkModel,
    CompleteReviewResult,
    HistoryProposalOutput,
    MainReviewOutput,
    ProfileProposalOutput,
    Reviewer,
    TaskResult,
)
from automation.review import (
    ProvisionalArtifactFiles,
    ReviewAttemptMetadata,
    ReviewPublishStatus,
    ReviewerStatus,
    inspect_reviewer_results,
    publish_complete_review,
    resolve_confirmed_review_set,
    resolve_recoverable_unconfirmed_attempt,
    set_artifact_paths,
)


def attempt_metadata(
    attempt_number: int = 1,
    *,
    run_id: str = "run-a",
    deduplication_key: str = "a" * 64,
) -> ReviewAttemptMetadata:
    return ReviewAttemptMetadata(attempt_number, run_id, deduplication_key)


def provenance(reviewer: Reviewer, index: int, task: str, attempt: str) -> dict:
    return {
        "task_id": task,
        "attempt_id": attempt,
        "statement_index": index,
        "reviewer": reviewer.value,
    }


def model_review(model: BenchmarkModel, marker: str) -> dict:
    return {
        "model_name": model.value,
        "factuality": {"status": "pass", "findings": [marker]},
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


def complete_result(
    reviewer: Reviewer,
    *,
    index: int = 10,
    attempt: str = "attempt-1",
    marker: str = "first",
    task: str | None = None,
) -> CompleteReviewResult:
    task = task or f"task-{reviewer.value}-{index}"
    identity = provenance(reviewer, index, task, attempt)
    main = MainReviewOutput(
        index,
        reviewer,
        attempt,
        {
            **identity,
            "model_reviews": [model_review(model, marker) for model in BenchmarkModel],
            "proposed_ideal_report": {
                "narrative": marker,
                "closing": "closing",
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
            attempt,
            model,
            {
                **identity,
                "model": model.value,
                "output": index,
                "date": "11/10/2026",
                "observations": [marker],
            },
        )
        for model in BenchmarkModel
    )
    proposals = tuple(
        ProfileProposalOutput(
            index,
            reviewer,
            attempt,
            model,
            {
                **identity,
                "model_name": model.value,
                "new_strengths": [marker],
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
        task_id=task,
        attempt_id=attempt,
        statement_index=index,
        reviewer=reviewer,
        main_review=main,
        histories=histories,
        profile_proposals=proposals,
        created_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
    )


def statement_path(root: Path, index: int = 10) -> Path:
    return root / f"statement_{index:03d}"


def file_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def tamper_review_set_identity(
    published,
    *,
    target: str,
    field: str,
    value: object,
) -> None:
    if target == "manifest.json":
        manifest_path = published.confirmed.manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[field] = value
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        active_path = published.confirmed.active_reference_path
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["manifest_sha256"] = storage_module._canonical_sha256(manifest)
        active_path.write_text(json.dumps(active), encoding="utf-8")
        return

    active_path = published.confirmed.active_reference_path
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active[field] = value
    active_path.write_text(json.dumps(active), encoding="utf-8")


@pytest.mark.parametrize("reviewer", list(Reviewer))
def test_publishes_seven_valid_outputs_for_each_reviewer(tmp_path, reviewer):
    result = complete_result(reviewer)

    published = publish_complete_review(
        result, statement_path=statement_path(tmp_path)
    )

    assert published.status is ReviewPublishStatus.CREATED
    assert published.permanent_review_path.name == f"{reviewer.value}_review.json"
    assert published.confirmed.artifacts.main_review.is_file()
    assert len(published.confirmed.artifacts.histories) == 3
    assert len(published.confirmed.artifacts.profile_proposals) == 3
    inspected = inspect_reviewer_results(
        statement_index=10, review_cases_root=tmp_path
    )
    assert inspected.reviewers[reviewer].status is ReviewerStatus.COMPLETE


def test_rejects_missing_history_even_if_contract_is_tampered(tmp_path):
    result = complete_result(Reviewer.CHATGPT)
    object.__setattr__(result, "histories", result.histories[:-1])

    with pytest.raises((KeyError, ValueError)):
        publish_complete_review(result, statement_path=statement_path(tmp_path))

    assert not statement_path(tmp_path).exists()


def test_rejects_missing_profile_proposal_even_if_contract_is_tampered(tmp_path):
    result = complete_result(Reviewer.GEMINI)
    object.__setattr__(result, "profile_proposals", result.profile_proposals[:-1])

    with pytest.raises((KeyError, ValueError)):
        publish_complete_review(result, statement_path=statement_path(tmp_path))


def test_rejects_inconsistent_statement_destination(tmp_path):
    result = complete_result(Reviewer.GROK)

    with pytest.raises(ValueError, match="statement_010"):
        publish_complete_review(result, statement_path=statement_path(tmp_path, 11))


def test_rejects_duplicate_models_after_tampering(tmp_path):
    result = complete_result(Reviewer.CHATGPT)
    object.__setattr__(result, "histories", (result.histories[0],) * 3)

    with pytest.raises((KeyError, ValueError)):
        publish_complete_review(result, statement_path=statement_path(tmp_path))


def test_rejects_mixed_attempts_after_tampering(tmp_path):
    result = complete_result(Reviewer.GEMINI)
    object.__setattr__(result.histories[0], "attempt_id", "different-attempt")

    with pytest.raises(ValueError, match="attempt"):
        publish_complete_review(result, statement_path=statement_path(tmp_path))


def test_rejects_generic_task_result(tmp_path):
    generic = TaskResult.success(task_id="task", attempt_id="attempt")

    with pytest.raises(TypeError, match="CompleteReviewResult"):
        publish_complete_review(generic, statement_path=statement_path(tmp_path))


@pytest.mark.parametrize("index", range(10))
def test_historical_statements_are_read_only(tmp_path, index):
    result = complete_result(Reviewer.CHATGPT, index=index)

    with pytest.raises(PermissionError, match="historical"):
        publish_complete_review(result, statement_path=statement_path(tmp_path, index))

    assert not statement_path(tmp_path, index).exists()


def test_identical_retry_is_idempotent_and_creates_no_duplicate(tmp_path):
    result = complete_result(Reviewer.CHATGPT)
    first = publish_complete_review(result, statement_path=statement_path(tmp_path))
    before = file_snapshot(statement_path(tmp_path))

    second = publish_complete_review(result, statement_path=statement_path(tmp_path))

    assert first.status is ReviewPublishStatus.CREATED
    assert second.status is ReviewPublishStatus.UNCHANGED
    assert second.confirmed.set_id == first.confirmed.set_id
    assert file_snapshot(statement_path(tmp_path)) == before
    assert len(list((first.confirmed.set_path.parent).iterdir())) == 1


def test_same_identity_cannot_be_reused_for_different_output(tmp_path):
    publish_complete_review(
        complete_result(Reviewer.CHATGPT, marker="old"),
        statement_path=statement_path(tmp_path),
    )

    with pytest.raises(ValueError, match="same task_id/attempt_id"):
        publish_complete_review(
            complete_result(Reviewer.CHATGPT, marker="different"),
            statement_path=statement_path(tmp_path),
        )


def test_replaces_gemini_as_one_confirmed_set(tmp_path):
    first = complete_result(Reviewer.GEMINI, attempt="attempt-1", marker="old")
    second = complete_result(Reviewer.GEMINI, attempt="attempt-2", marker="new")
    publish_complete_review(first, statement_path=statement_path(tmp_path))

    replaced = publish_complete_review(second, statement_path=statement_path(tmp_path))

    assert replaced.status is ReviewPublishStatus.REPLACED
    assert replaced.confirmed.attempt_id == "attempt-2"
    assert len(list(replaced.confirmed.set_path.parent.iterdir())) == 2
    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    complete = inspected.reviewers[Reviewer.GEMINI].complete_result
    assert complete is not None
    assert complete.attempt_id == "attempt-2"
    assert complete.main_review.output["proposed_ideal_report"]["narrative"] == "new"


def test_new_attempt_replaces_an_invalid_confirmed_reference(tmp_path):
    first = publish_complete_review(
        complete_result(Reviewer.GEMINI, marker="old"),
        statement_path=statement_path(tmp_path),
    )
    first.confirmed.manifest_path.write_text("{broken", encoding="utf-8")

    replaced = publish_complete_review(
        complete_result(Reviewer.GEMINI, attempt="attempt-2", marker="new"),
        statement_path=statement_path(tmp_path),
    )

    assert replaced.status is ReviewPublishStatus.REPLACED
    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    assert inspected.reviewers[Reviewer.GEMINI].status is ReviewerStatus.COMPLETE
    assert (
        inspected.reviewers[Reviewer.GEMINI].complete_result.attempt_id
        == "attempt-2"
    )


def test_replacing_gemini_preserves_chatgpt_and_grok(tmp_path):
    for reviewer in Reviewer:
        publish_complete_review(
            complete_result(reviewer), statement_path=statement_path(tmp_path)
        )
    chatgpt_before = file_snapshot(
        statement_path(tmp_path) / ".review_sets/chatgpt"
    )
    grok_before = file_snapshot(statement_path(tmp_path) / ".review_sets/grok")

    publish_complete_review(
        complete_result(Reviewer.GEMINI, attempt="attempt-2", marker="new"),
        statement_path=statement_path(tmp_path),
    )

    assert file_snapshot(statement_path(tmp_path) / ".review_sets/chatgpt") == chatgpt_before
    assert file_snapshot(statement_path(tmp_path) / ".review_sets/grok") == grok_before


def test_failure_during_preparation_leaves_previous_set_intact(tmp_path, monkeypatch):
    old = complete_result(Reviewer.CHATGPT, marker="old")
    publish_complete_review(old, statement_path=statement_path(tmp_path))
    before = file_snapshot(statement_path(tmp_path))
    original = storage_module._write_prepared_json
    calls = 0

    def fail_on_third_file(path, value):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated preparation failure")
        original(path, value)

    monkeypatch.setattr(storage_module, "_write_prepared_json", fail_on_third_file)

    with pytest.raises(OSError, match="preparation"):
        publish_complete_review(
            complete_result(Reviewer.CHATGPT, attempt="attempt-2", marker="new"),
            statement_path=statement_path(tmp_path),
        )

    confirmed, error = resolve_confirmed_review_set(
        statement_path=statement_path(tmp_path),
        statement_index=10,
        reviewer=Reviewer.CHATGPT,
    )
    assert error is None
    assert confirmed is not None and confirmed.attempt_id == "attempt-1"
    assert file_snapshot(statement_path(tmp_path)) == before


def test_interruption_before_confirmation_ignores_prepared_orphan(tmp_path):
    publish_complete_review(
        complete_result(Reviewer.GEMINI, marker="old"),
        statement_path=statement_path(tmp_path),
    )

    def interrupt(stage):
        if stage == "after_set_publish":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        publish_complete_review(
            complete_result(Reviewer.GEMINI, attempt="attempt-2", marker="new"),
            statement_path=statement_path(tmp_path),
            failure_injector=interrupt,
        )

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    complete = inspected.reviewers[Reviewer.GEMINI].complete_result
    assert complete is not None and complete.attempt_id == "attempt-1"
    confirmed, _ = resolve_confirmed_review_set(
        statement_path=statement_path(tmp_path),
        statement_index=10,
        reviewer=Reviewer.GEMINI,
    )
    attempt_paths = list(confirmed.set_path.parent.iterdir())
    orphan = next(path for path in attempt_paths if path != confirmed.set_path)
    orphan_artifacts = set_artifact_paths(orphan, Reviewer.GEMINI)
    explicit_orphan = ProvisionalArtifactFiles(
        tuple(orphan_artifacts.histories.values()),
        tuple(orphan_artifacts.profile_proposals.values()),
    )

    inspected_with_override = inspect_reviewer_results(
        statement_index=10,
        review_cases_root=tmp_path,
        provisional_files={Reviewer.GEMINI: explicit_orphan},
    )

    assert (
        inspected_with_override.reviewers[Reviewer.GEMINI].complete_result.attempt_id
        == "attempt-1"
    )


def test_failure_during_active_publication_preserves_previous_reference(
    tmp_path, monkeypatch
):
    publish_complete_review(
        complete_result(Reviewer.GROK, marker="old"),
        statement_path=statement_path(tmp_path),
    )
    original_replace = storage_module.os.replace

    def fail_active(source, target):
        if Path(target).name == "active.json":
            raise OSError("simulated publication failure")
        original_replace(source, target)

    monkeypatch.setattr(storage_module.os, "replace", fail_active)

    with pytest.raises(OSError, match="publication"):
        publish_complete_review(
            complete_result(Reviewer.GROK, attempt="attempt-2", marker="new"),
            statement_path=statement_path(tmp_path),
        )

    confirmed, error = resolve_confirmed_review_set(
        statement_path=statement_path(tmp_path),
        statement_index=10,
        reviewer=Reviewer.GROK,
    )
    assert error is None
    assert confirmed is not None and confirmed.attempt_id == "attempt-1"
    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    assert inspected.reviewers[Reviewer.GROK].complete_result.attempt_id == "attempt-1"


def test_failed_first_activation_leaves_only_a_recoverable_unconfirmed_projection(
    tmp_path, monkeypatch
):
    original_replace = storage_module.os.replace

    def fail_active(source, target):
        if Path(target).name == "active.json":
            raise OSError("simulated first activation failure")
        original_replace(source, target)

    monkeypatch.setattr(storage_module.os, "replace", fail_active)

    with pytest.raises(OSError, match="first activation"):
        publish_complete_review(
            complete_result(Reviewer.CHATGPT),
            statement_path=statement_path(tmp_path),
            attempt_metadata=attempt_metadata(),
        )

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    reviewer = inspected.reviewers[Reviewer.CHATGPT]
    assert reviewer.status is ReviewerStatus.INCOMPLETE
    assert reviewer.recovery_action.value == "REPEAT_FULL_REVIEW"
    assert [issue.code for issue in reviewer.structural_issues] == [
        "unconfirmed_projection"
    ]


def test_legacy_unconfirmed_projection_blocks_without_inventing_retry_identity(
    tmp_path,
):
    def interrupt(stage):
        if stage == "before_activation":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        publish_complete_review(
            complete_result(Reviewer.CHATGPT),
            statement_path=statement_path(tmp_path),
            failure_injector=interrupt,
        )

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    reviewer = inspected.reviewers[Reviewer.CHATGPT]
    assert reviewer.status is ReviewerStatus.UNVERIFIED
    assert [issue.code for issue in reviewer.provenance_issues] == [
        "retry_identity_unverified"
    ]


def test_v2_manifest_persists_minimal_retry_identity(tmp_path):
    result = publish_complete_review(
        complete_result(Reviewer.CHATGPT),
        statement_path=statement_path(tmp_path),
        attempt_metadata=attempt_metadata(3),
    )

    manifest = json.loads(result.confirmed.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == storage_module.STORAGE_SCHEMA_VERSION
    assert manifest["execution"] == {
        "attempt_number": 3,
        "run_id": "run-a",
        "task_deduplication_key": "a" * 64,
    }
    assert result.confirmed.attempt_metadata == attempt_metadata(3)


@pytest.mark.parametrize("use_v2", [False, True], ids=["v1", "v2"])
@pytest.mark.parametrize("target", ["manifest.json", "active.json"])
def test_manifest_and_active_accept_legitimate_json_integers(tmp_path, use_v2, target):
    metadata = attempt_metadata() if use_v2 else None
    published = publish_complete_review(
        complete_result(Reviewer.CHATGPT),
        statement_path=statement_path(tmp_path),
        attempt_metadata=metadata,
    )

    confirmed, error = resolve_confirmed_review_set(
        statement_path=statement_path(tmp_path),
        statement_index=10,
        reviewer=Reviewer.CHATGPT,
    )

    assert error is None
    assert confirmed is not None
    payload_path = (
        confirmed.manifest_path
        if target == "manifest.json"
        else confirmed.active_reference_path
    )
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert type(payload["schema_version"]) is int
    assert payload["schema_version"] == (2 if use_v2 else 1)
    assert type(payload["statement_index"]) is int
    assert payload["statement_index"] == 10


@pytest.mark.parametrize("use_v2", [False, True], ids=["v1", "v2"])
@pytest.mark.parametrize("target", ["manifest.json", "active.json"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2.0),
        ("schema_version", True),
        ("schema_version", False),
        ("schema_version", "2"),
        ("schema_version", None),
        ("statement_index", 10.0),
        ("statement_index", True),
        ("statement_index", "10"),
        ("statement_index", None),
    ],
    ids=[
        "schema-float",
        "schema-true",
        "schema-false",
        "schema-string",
        "schema-null",
        "statement-float",
        "statement-true",
        "statement-string",
        "statement-null",
    ],
)
def test_manifest_and_active_reject_non_integer_json_types(
    tmp_path,
    use_v2,
    target,
    field,
    value,
):
    metadata = attempt_metadata() if use_v2 else None
    published = publish_complete_review(
        complete_result(Reviewer.CHATGPT),
        statement_path=statement_path(tmp_path),
        attempt_metadata=metadata,
    )
    tamper_review_set_identity(
        published,
        target=target,
        field=field,
        value=value,
    )

    confirmed, error = resolve_confirmed_review_set(
        statement_path=statement_path(tmp_path),
        statement_index=10,
        reviewer=Reviewer.CHATGPT,
    )

    assert confirmed is None
    assert error is not None


def test_two_sequential_orphans_recover_latest_attempt_of_same_task(tmp_path):
    def interrupt(stage):
        if stage == "before_activation":
            raise RuntimeError("simulated interruption")

    for number in (1, 2):
        with pytest.raises(RuntimeError, match="interruption"):
            publish_complete_review(
                complete_result(
                    Reviewer.CHATGPT,
                    attempt=f"attempt-{number}",
                    marker=f"attempt-{number}",
                ),
                statement_path=statement_path(tmp_path),
                attempt_metadata=attempt_metadata(number),
                failure_injector=interrupt,
            )

    recovered, error = resolve_recoverable_unconfirmed_attempt(
        statement_path=statement_path(tmp_path),
        statement_index=10,
        reviewer=Reviewer.CHATGPT,
    )
    assert error is None
    assert recovered is not None
    assert recovered.task_id == "task-chatgpt-10"
    assert recovered.attempt_id == "attempt-2"
    assert recovered.attempt_number == 2


def test_contradictory_orphan_runs_block_recovery(tmp_path):
    def interrupt(stage):
        if stage == "before_activation":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        publish_complete_review(
            complete_result(Reviewer.CHATGPT, marker="first"),
            statement_path=statement_path(tmp_path),
            attempt_metadata=attempt_metadata(1, run_id="run-a"),
            failure_injector=interrupt,
        )
    with pytest.raises(RuntimeError, match="interruption"):
        publish_complete_review(
            complete_result(
                Reviewer.CHATGPT,
                attempt="attempt-2",
                marker="second",
            ),
            statement_path=statement_path(tmp_path),
            attempt_metadata=attempt_metadata(2, run_id="run-b"),
            failure_injector=interrupt,
        )

    recovered, error = resolve_recoverable_unconfirmed_attempt(
        statement_path=statement_path(tmp_path),
        statement_index=10,
        reviewer=Reviewer.CHATGPT,
    )
    assert recovered is None
    assert error == "unconfirmed review attempts have contradictory logical identity"
    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    assert inspected.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.UNVERIFIED


def test_unmatched_permanent_review_is_not_treated_as_recoverable_projection(tmp_path):
    def interrupt(stage):
        if stage == "before_activation":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        publish_complete_review(
            complete_result(Reviewer.GEMINI),
            statement_path=statement_path(tmp_path),
            failure_injector=interrupt,
        )
    permanent = statement_path(tmp_path) / "gemini_review.json"
    value = json.loads(permanent.read_text(encoding="utf-8"))
    value["proposed_ideal_report"]["narrative"] = "untrusted replacement"
    permanent.write_text(json.dumps(value), encoding="utf-8")

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)

    assert inspected.reviewers[Reviewer.GEMINI].status is ReviewerStatus.UNVERIFIED


def test_interruption_after_activation_leaves_new_set_authoritative(tmp_path):
    publish_complete_review(
        complete_result(Reviewer.CHATGPT, marker="old"),
        statement_path=statement_path(tmp_path),
    )

    def interrupt(stage):
        if stage == "after_activation":
            raise RuntimeError("simulated post-activation interruption")

    with pytest.raises(RuntimeError, match="post-activation"):
        publish_complete_review(
            complete_result(Reviewer.CHATGPT, attempt="attempt-2", marker="new"),
            statement_path=statement_path(tmp_path),
            failure_injector=interrupt,
        )

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)
    assert inspected.reviewers[Reviewer.CHATGPT].complete_result.attempt_id == "attempt-2"


def test_stale_permanent_projection_never_overrides_active_set(tmp_path):
    old = complete_result(Reviewer.GEMINI, marker="old")
    publish_complete_review(old, statement_path=statement_path(tmp_path))
    permanent = statement_path(tmp_path) / "gemini_review.json"
    stale = permanent.read_bytes()
    publish_complete_review(
        complete_result(Reviewer.GEMINI, attempt="attempt-2", marker="new"),
        statement_path=statement_path(tmp_path),
    )
    permanent.write_bytes(stale)

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)

    complete = inspected.reviewers[Reviewer.GEMINI].complete_result
    assert complete is not None and complete.attempt_id == "attempt-2"
    assert complete.main_review.output["proposed_ideal_report"]["narrative"] == "new"


def test_tampered_active_reference_is_rejected_without_projection_fallback(tmp_path):
    published = publish_complete_review(
        complete_result(Reviewer.GROK), statement_path=statement_path(tmp_path)
    )
    active = published.confirmed.active_reference_path
    value = json.loads(active.read_text(encoding="utf-8"))
    value["attempt_id"] = "tampered"
    active.write_text(json.dumps(value), encoding="utf-8")

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)

    assert inspected.reviewers[Reviewer.GROK].status is ReviewerStatus.INVALID
    assert any(
        issue.code == "confirmed_set_invalid"
        for issue in inspected.reviewers[Reviewer.GROK].structural_issues
    )


def test_inspector_rejects_tampered_active_set_instead_of_mixing_versions(tmp_path):
    published = publish_complete_review(
        complete_result(Reviewer.CHATGPT), statement_path=statement_path(tmp_path)
    )
    path = published.confirmed.artifacts.histories[BenchmarkModel.GEMMA]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["attempt_id"] = "mixed-attempt"
    path.write_text(json.dumps(value), encoding="utf-8")

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)

    assert inspected.reviewers[Reviewer.CHATGPT].status is ReviewerStatus.INVALID
    assert any(
        issue.code == "confirmed_set_invalid"
        for issue in inspected.reviewers[Reviewer.CHATGPT].structural_issues
    )


def test_all_produced_artifacts_are_valid_json(tmp_path):
    published = publish_complete_review(
        complete_result(Reviewer.GEMINI), statement_path=statement_path(tmp_path)
    )

    for path in statement_path(tmp_path).rglob("*.json"):
        with path.open("r", encoding="utf-8") as handle:
            assert json.load(handle) is not None
    assert published.confirmed.manifest_path.is_file()


def test_publication_does_not_touch_accumulated_or_unrelated_files(tmp_path):
    profiles = tmp_path / "config/profiles/gemma_profile.json"
    feedback = tmp_path / "config/feedback/gemma_history.jsonl"
    adjudication = statement_path(tmp_path) / "adjudication.json"
    other = statement_path(tmp_path, 11) / "chatgpt_review.json"
    for path, content in (
        (profiles, "profile"),
        (feedback, "history"),
        (adjudication, "adjudication"),
        (other, "other"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    before = {path: path.read_bytes() for path in (profiles, feedback, adjudication, other)}

    publish_complete_review(
        complete_result(Reviewer.GROK), statement_path=statement_path(tmp_path)
    )

    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("field", ["task_id", "attempt_id", "statement_index", "reviewer"])
def test_writer_rejects_serialized_identity_that_contradicts_result(tmp_path, field):
    result = complete_result(Reviewer.CHATGPT)
    payload = dict(result.main_review.output)
    payload[field] = 11 if field == "statement_index" else f"wrong-{field}"
    object.__setattr__(result.main_review, "output", payload)

    with pytest.raises(ValueError, match="serialized identity"):
        publish_complete_review(result, statement_path=statement_path(tmp_path))

    assert not statement_path(tmp_path).exists()


def test_inspector_rejects_manifest_consistent_set_with_contradictory_internal_task(tmp_path):
    result = complete_result(Reviewer.GEMINI)
    published = publish_complete_review(result, statement_path=statement_path(tmp_path))
    main_path = published.confirmed.artifacts.main_review
    main = json.loads(main_path.read_text(encoding="utf-8"))
    main["task_id"] = "different-internal-task"
    main_path.write_text(json.dumps(main), encoding="utf-8")
    manifest_path = published.confirmed.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["main_review"]["sha256"] = storage_module._canonical_sha256(main)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    active_path = published.confirmed.active_reference_path
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active["manifest_sha256"] = storage_module._canonical_sha256(manifest)
    active_path.write_text(json.dumps(active), encoding="utf-8")

    inspected = inspect_reviewer_results(statement_index=10, review_cases_root=tmp_path)

    assert inspected.reviewers[Reviewer.GEMINI].status is ReviewerStatus.INVALID


def test_rejects_symlinked_review_storage_without_writing_external_files(tmp_path):
    statement = statement_path(tmp_path)
    statement.mkdir(parents=True)
    external = tmp_path / "outside"
    external.mkdir()
    (statement / ".review_sets").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic links"):
        publish_complete_review(
            complete_result(Reviewer.GROK), statement_path=statement
        )

    assert list(external.iterdir()) == []
