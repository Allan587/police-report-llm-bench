from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from automation.contracts import (
    AttemptStatus,
    ExecutionAttempt,
    ReviewOperation,
    Reviewer,
    Task,
    TaskError,
    TaskResult,
    TaskStatus,
    TaskType,
)


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def generation_task(**overrides) -> Task:
    values = {
        "experiment_id": "experiment-1",
        "task_type": TaskType.GENERATION,
        "case_id": "case-1",
        "provider": "synthetic",
        "model": "mock-model",
        "prompt_version": "prompt-v1",
        "profile_version": "profile-v1",
        "iteration": 0,
        "now": NOW,
    }
    values.update(overrides)
    return Task.create(**values)


class TaskContractTests(unittest.TestCase):
    def main_review_task(self, **overrides) -> Task:
        values = {
            "experiment_id": "experiment-1",
            "task_type": TaskType.EVALUATION,
            "case_id": "statement_007",
            "statement_index": 7,
            "reviewer": Reviewer.CHATGPT,
            "review_operation": ReviewOperation.MAIN_REVIEW,
            "prompt_version": "main-review-v1",
            "now": NOW,
        }
        values.update(overrides)
        return Task.create(**values)

    def test_supported_task_types_enforce_their_required_fields(self) -> None:
        valid_tasks = (
            generation_task(),
            Task.create(
                experiment_id="exp", task_type=TaskType.EVALUATION, case_id="case"
            ),
            Task.create(
                experiment_id="exp", task_type=TaskType.FEEDBACK, case_id="case"
            ),
            Task.create(
                experiment_id="exp",
                task_type=TaskType.PROFILE_OPTIMIZATION,
                provider="synthetic",
                model="mock",
                profile_version="v1",
            ),
            Task.create(experiment_id="exp", task_type=TaskType.DATASET_PREPARATION),
        )

        self.assertEqual({task.task_type for task in valid_tasks}, set(TaskType))
        with self.assertRaisesRegex(ValueError, "generation tasks require.*model"):
            generation_task(model=None)
        with self.assertRaisesRegex(ValueError, "evaluation tasks require.*case_id"):
            Task.create(experiment_id="exp", task_type=TaskType.EVALUATION)
        with self.assertRaisesRegex(
            ValueError, "profile_optimization tasks require.*profile_version"
        ):
            Task.create(
                experiment_id="exp",
                task_type=TaskType.PROFILE_OPTIMIZATION,
                provider="synthetic",
                model="mock",
            )

    def test_retries_keep_task_identity_while_repetitions_change_deduplication(self) -> None:
        original = generation_task(experimental_repetition=0)
        retry_one = ExecutionAttempt.start(original.task_id, 1, now=NOW)
        retry_two = ExecutionAttempt.start(original.task_id, 2, now=NOW)
        same_logical_task = generation_task(experimental_repetition=0)
        repetition = generation_task(experimental_repetition=1)

        self.assertEqual(retry_one.task_id, retry_two.task_id)
        self.assertNotEqual(retry_one.attempt_id, retry_two.attempt_id)
        self.assertEqual(original.deduplication_key, same_logical_task.deduplication_key)
        self.assertNotEqual(original.task_id, same_logical_task.task_id)
        self.assertNotEqual(original.deduplication_key, repetition.deduplication_key)

    def test_main_review_identity_distinguishes_reviewer_and_statement(self) -> None:
        chatgpt = self.main_review_task(reviewer=Reviewer.CHATGPT)
        gemini = self.main_review_task(reviewer=Reviewer.GEMINI)
        grok = self.main_review_task(reviewer=Reviewer.GROK)
        next_statement = self.main_review_task(
            case_id="statement_008", statement_index=8
        )

        self.assertEqual(
            len(
                {
                    chatgpt.deduplication_key,
                    gemini.deduplication_key,
                    grok.deduplication_key,
                }
            ),
            3,
        )
        self.assertNotEqual(chatgpt.deduplication_key, next_statement.deduplication_key)

    def test_main_review_retries_preserve_task_and_logical_identity(self) -> None:
        task = self.main_review_task()
        same_logical_review = self.main_review_task()
        first = ExecutionAttempt.start(task.task_id, 1, now=NOW)
        second = ExecutionAttempt.start(task.task_id, 2, now=NOW)

        self.assertEqual(first.task_id, second.task_id)
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        self.assertNotEqual(task.task_id, same_logical_review.task_id)
        self.assertEqual(task.deduplication_key, same_logical_review.deduplication_key)

    def test_partial_review_identity_is_rejected_without_affecting_generic_tasks(self) -> None:
        with self.assertRaisesRegex(ValueError, "require statement_index"):
            self.main_review_task(reviewer=None)
        with self.assertRaisesRegex(ValueError, "evaluation task type"):
            self.main_review_task(task_type=TaskType.FEEDBACK)

        self.assertIsNone(generation_task().review_operation)

    def test_identity_changes_for_each_experimental_dimension_not_metadata(self) -> None:
        baseline = generation_task(metadata={"trace": "one"})
        same_identity = generation_task(metadata={"trace": "two"})

        self.assertEqual(baseline.deduplication_key, same_identity.deduplication_key)
        for changes in (
            {"case_id": "case-2"},
            {"provider": "other"},
            {"model": "other"},
            {"prompt_version": "prompt-v2"},
            {"profile_version": "profile-v2"},
            {"iteration": 1},
            {"experimental_repetition": 1},
        ):
            self.assertNotEqual(
                baseline.deduplication_key,
                generation_task(**changes).deduplication_key,
            )

    def test_rejects_invalid_counts_timestamps_and_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "iteration"):
            generation_task(iteration=-1)
        with self.assertRaisesRegex(ValueError, "experimental_repetition"):
            generation_task(experimental_repetition=-1)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            generation_task(now=datetime(2026, 10, 10))
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            generation_task(metadata={"unsafe": object()})
        with self.assertRaisesRegex(ValueError, "non-finite"):
            generation_task(metadata={"score": float("nan")})
        with self.assertRaisesRegex(ValueError, "metadata must be a mapping"):
            generation_task(metadata=[])

    def test_attempt_lifecycle_invariants_require_consistent_terminal_data(self) -> None:
        error = TaskError(code="temporary", message="synthetic", retryable=True)
        failed = ExecutionAttempt(
            attempt_id="attempt-1",
            task_id="task-1",
            attempt_number=1,
            status=AttemptStatus.FAILED,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=1),
            error=error,
        )

        self.assertTrue(failed.error.retryable)
        with self.assertRaisesRegex(ValueError, "failed attempt requires an error"):
            replace(failed, error=None)
        with self.assertRaisesRegex(ValueError, "running attempt"):
            replace(failed, status=AttemptStatus.RUNNING)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            ExecutionAttempt.start("task-1", 0, now=NOW)

    def test_result_requires_error_exactly_when_execution_failed(self) -> None:
        error = TaskError(code="permanent", message="synthetic", retryable=False)
        success = TaskResult.success(
            task_id="task-1",
            attempt_id="attempt-1",
            output={"response": "synthetic"},
            now=NOW,
        )
        failure = TaskResult.failure(
            task_id="task-1", attempt_id="attempt-2", error=error, now=NOW
        )

        self.assertEqual(success.output["response"], "synthetic")
        self.assertIsNone(success.error)
        self.assertEqual(failure.error.code, "permanent")
        with self.assertRaisesRegex(ValueError, "successful result"):
            replace(success, error=error)
        with self.assertRaisesRegex(ValueError, "failed result requires"):
            replace(failure, error=None)
        with self.assertRaisesRegex(ValueError, "output must be a mapping"):
            TaskResult.success(
                task_id="task-1", attempt_id="attempt-1", output=[], now=NOW
            )


if __name__ == "__main__":
    unittest.main()
