"""Synthetic operator packages, built independently of the importer."""
import hashlib
import json

from automation.contracts import Reviewer
from tests.test_review_storage import complete_result


def digest(output):
    encoded = (json.dumps(output, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def synthetic_package(*, task="synthetic-task", attempt="synthetic-attempt",
                      reviewer=Reviewer.CHATGPT, run="run-a"):
    result = complete_result(reviewer, task=task, attempt=attempt,
                             marker="synthetic operator supplied text")
    identity = {"task_id": task, "attempt_id": attempt, "reviewer": reviewer.value,
                "statement_index": 10, "run_id": run}
    artifacts = [("main", result.main_review)]
    artifacts += [(f"history_{item.model.value}", item) for item in result.histories]
    artifacts += [(f"profile_{item.model.value}", item) for item in result.profile_proposals]
    return {"schema_version": 1, **identity, "components": [
        {**identity, "slot": slot, "state": "COMPLETE",
         "observed_at": "2026-11-06T10:00:00-06:00",
         "declared_provenance": "Synthetic fixture entered by operator; no provider contacted.",
         "output": dict(artifact.output), "content_sha256": digest(dict(artifact.output))}
        for slot, artifact in artifacts
    ]}
