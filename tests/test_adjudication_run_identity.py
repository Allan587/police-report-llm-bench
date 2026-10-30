"""Direct public-writer regressions for confirmed reviewer run identity."""

import hashlib
import json

import pytest

from automation.contracts import BenchmarkModel, Reviewer
from automation.review import commit_adjudication_bundle
from automation.review.synthetic_adjudication import (
    AdjudicationBundleRequest,
    DeterministicFakeAdjudicatorAdapter,
    SyntheticAdjudicationStatus,
    run_synthetic_adjudication_once,
)
from tests.test_synthetic_adjudication_flow import (
    publish_reviewers,
    ready_fixture,
    snapshot,
    write_json,
)


def prepared_args(tmp_path):
    config = ready_fixture(tmp_path)
    publish_reviewers(config, 3)
    prepared = run_synthetic_adjudication_once(
        config, DeterministicFakeAdjudicatorAdapter()
    )
    assert prepared.status is SyntheticAdjudicationStatus.PREPARED
    workflow = config.review.coordinator.workflow
    current_profiles = {
        model: json.loads(
            (workflow.profiles_root / f"{model.value}_profile.json").read_text(
                encoding="utf-8"
            )
        )
        for model in BenchmarkModel
    }
    bundle = DeterministicFakeAdjudicatorAdapter().execute_bundle(
        AdjudicationBundleRequest(prepared.request, current_profiles)
    )
    return {
        "statement_index": 10,
        "statement_path": workflow.review_cases_root / "statement_010",
        "feedback_root": workflow.feedback_root,
        "profiles_root": workflow.profiles_root,
        "adjudication": bundle["adjudication"],
        "histories": bundle["histories"],
        "profiles": bundle["profiles"],
        "run_id": "run-a",
        "review_attempts": {
            reviewer: {
                "task_id": prepared.request.reviews[reviewer].task_id,
                "attempt_id": prepared.request.reviews[reviewer].attempt_id,
            }
            for reviewer in Reviewer
        },
    }


def change_active_manifest(args, reviewer, change):
    statement = args["statement_path"]
    reference_path = statement / ".review_sets" / reviewer.value / "active.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    manifest_path = reference_path.parent / "attempts" / reference["set_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    change(manifest, reference)
    write_json(manifest_path, manifest)
    encoded = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    reference["manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
    write_json(reference_path, reference)


def test_foreign_run_rejected_before_any_application_or_commit(tmp_path):
    args = prepared_args(tmp_path)
    before = snapshot(tmp_path)

    with pytest.raises(ValueError, match="run_id contradicts"):
        commit_adjudication_bundle(**{**args, "run_id": "invented-run"})

    assert snapshot(tmp_path) == before
    assert not (args["statement_path"] / "adjudication.json").exists()
    assert not (args["statement_path"] / ".adjudication_applications").exists()


def test_authentic_run_commits_and_identical_replay_is_unchanged(tmp_path):
    args = prepared_args(tmp_path)
    before_feedback = snapshot(args["feedback_root"])
    before_profiles = snapshot(args["profiles_root"])

    first = commit_adjudication_bundle(**args)
    assert first.status.value == "COMMITTED"
    assert first.application.run_id == "run-a"
    adjudication_path = args["statement_path"] / "adjudication.json"
    assert json.loads(adjudication_path.read_text(encoding="utf-8")) == args["adjudication"]
    manifest = json.loads(first.application.manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-a"
    assert hashlib.sha256(adjudication_path.read_bytes()).hexdigest() == manifest["files"]["adjudication"]["target_sha256"]
    canonical = json.dumps(args["adjudication"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == first.application.adjudication_sha256
    committed = snapshot(tmp_path)

    second = commit_adjudication_bundle(**args)
    assert second.status.value == "UNCHANGED"
    assert snapshot(tmp_path) == committed
    assert snapshot(args["feedback_root"]) == before_feedback
    assert snapshot(args["profiles_root"]) == before_profiles


def test_different_verified_reviewer_run_rejected(tmp_path):
    args = prepared_args(tmp_path)
    change_active_manifest(
        args, Reviewer.GROK,
        lambda manifest, reference: manifest["execution"].__setitem__("run_id", "run-b"),
    )
    before = snapshot(tmp_path)

    with pytest.raises(ValueError, match="run_id contradicts"):
        commit_adjudication_bundle(**args)

    assert snapshot(tmp_path) == before
    assert not (args["statement_path"] / "adjudication.json").exists()


@pytest.mark.parametrize("run_id", [None, "", " "])
def test_missing_requested_run_fails_closed(tmp_path, run_id):
    args = prepared_args(tmp_path)
    before = snapshot(tmp_path)

    with pytest.raises(ValueError, match="run_id"):
        commit_adjudication_bundle(**{**args, "run_id": run_id})

    assert snapshot(tmp_path) == before
    assert not (args["statement_path"] / ".adjudication_applications").exists()


def test_legacy_active_set_without_run_evidence_fails_closed(tmp_path):
    args = prepared_args(tmp_path)

    def make_legacy(manifest, reference):
        manifest["schema_version"] = 1
        manifest.pop("execution")
        reference["schema_version"] = 1

    change_active_manifest(args, Reviewer.GROK, make_legacy)
    before = snapshot(tmp_path)

    with pytest.raises(ValueError, match="no verifiable run_id"):
        commit_adjudication_bundle(**args)

    assert snapshot(tmp_path) == before
    assert not (args["statement_path"] / ".adjudication_applications").exists()


def test_contradictory_replay_preserves_committed_result(tmp_path):
    args = prepared_args(tmp_path)
    first = commit_adjudication_bundle(**args)
    committed = snapshot(tmp_path)

    with pytest.raises(ValueError, match="run_id contradicts"):
        commit_adjudication_bundle(**{**args, "run_id": "invented-run"})

    assert snapshot(tmp_path) == committed
    assert first.application.manifest_path.is_file()
