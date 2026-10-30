from datetime import datetime, timedelta, timezone
import unittest

from automation.contracts import Task, TaskStatus, TaskType
from automation.state_machine import (
    TERMINAL_STATUSES,
    InvalidStateTransition,
    allowed_transitions,
    transition,
)


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def pending_task() -> Task:
    return Task.create(
        experiment_id="experiment-1",
        task_type=TaskType.GENERATION,
        case_id="case-1",
        provider="synthetic",
        model="mock-model",
        prompt_version="prompt-v1",
        now=NOW,
    )


class StateMachineTests(unittest.TestCase):
    def test_normal_execution_emits_auditable_events(self) -> None:
        task = pending_task()
        started = transition(task, TaskStatus.RUNNING, occurred_at=NOW)
        completed_at = NOW + timedelta(seconds=5)
        completed = transition(
            started.task, TaskStatus.COMPLETED, occurred_at=completed_at
        )

        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertEqual(completed.task.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.task.updated_at, completed_at)
        self.assertEqual(started.event.task_id, task.task_id)
        self.assertEqual(started.event.from_status, TaskStatus.PENDING)
        self.assertEqual(started.event.to_status, TaskStatus.RUNNING)
        self.assertEqual(completed.event.occurred_at, completed_at)

    def test_retry_path_never_uses_terminal_failed_state(self) -> None:
        running = transition(pending_task(), TaskStatus.RUNNING, occurred_at=NOW).task
        retry_pending = transition(
            running, TaskStatus.RETRY_PENDING, occurred_at=NOW
        ).task
        retried = transition(retry_pending, TaskStatus.RUNNING, occurred_at=NOW).task
        completed = transition(retried, TaskStatus.COMPLETED, occurred_at=NOW).task

        self.assertEqual(completed.status, TaskStatus.COMPLETED)

    def test_review_can_complete_fail_cancel_or_request_retry(self) -> None:
        running = transition(pending_task(), TaskStatus.RUNNING, occurred_at=NOW).task
        waiting = transition(running, TaskStatus.WAITING_REVIEW, occurred_at=NOW).task

        self.assertEqual(
            allowed_transitions(waiting.status),
            {
                TaskStatus.RETRY_PENDING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            },
        )

    def test_rejects_unlisted_and_same_state_transitions(self) -> None:
        task = pending_task()

        for target in (
            TaskStatus.PENDING,
            TaskStatus.RETRY_PENDING,
            TaskStatus.WAITING_REVIEW,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        ):
            with self.subTest(target=target):
                with self.assertRaises(InvalidStateTransition):
                    transition(task, target, occurred_at=NOW)

    def test_completed_failed_and_cancelled_are_terminal(self) -> None:
        running = transition(pending_task(), TaskStatus.RUNNING, occurred_at=NOW).task
        terminal_tasks = (
            transition(running, TaskStatus.COMPLETED, occurred_at=NOW).task,
            transition(running, TaskStatus.FAILED, occurred_at=NOW).task,
            transition(running, TaskStatus.CANCELLED, occurred_at=NOW).task,
        )

        self.assertEqual({task.status for task in terminal_tasks}, TERMINAL_STATUSES)
        for task in terminal_tasks:
            self.assertEqual(allowed_transitions(task.status), frozenset())
            for target in TaskStatus:
                with self.subTest(current=task.status, target=target):
                    with self.assertRaises(InvalidStateTransition):
                        transition(task, target, occurred_at=NOW)

    def test_rejects_a_transition_timestamp_before_last_update(self) -> None:
        task = pending_task()

        with self.assertRaisesRegex(ValueError, "earlier than task.updated_at"):
            transition(
                task,
                TaskStatus.RUNNING,
                occurred_at=NOW - timedelta(microseconds=1),
            )


if __name__ == "__main__":
    unittest.main()
