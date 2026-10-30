from dataclasses import replace
from datetime import datetime, timezone
import unittest

from automation.contracts import (
    BenchmarkModel,
    CompleteReviewResult,
    HistoryProposalOutput,
    MainReviewOutput,
    ProfileProposalOutput,
    Reviewer,
    TaskResult,
)


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
MODELS = tuple(BenchmarkModel)


def main_review(
    *,
    statement_index: int = 7,
    reviewer: Reviewer = Reviewer.GEMINI,
    attempt_id: str = "attempt-1",
) -> MainReviewOutput:
    return MainReviewOutput(
        statement_index=statement_index,
        reviewer=reviewer,
        attempt_id=attempt_id,
        output={
            "reviewer": reviewer.value,
            "model_reviews": [
                {
                    "model_name": model.value,
                    "factuality": {"status": "pass", "findings": []},
                    "hallucinations": [],
                    "omissions": [],
                    "narrative_quality": {"status": "pass", "findings": []},
                    "crime_classification": {
                        "model_category": "synthetic",
                        "assessment": "correct",
                        "proposed_category_cr": "synthetic",
                        "justification": "synthetic",
                    },
                    "useful_elements": [],
                }
                for model in MODELS
            ],
            "proposed_ideal_report": {
                "narrative": "Synthetic narrative.",
                "closing": "Synthetic closing.",
                "crime_classification": {
                    "category": "synthetic",
                    "justification": "synthetic",
                },
            },
        },
    )


def history(
    model: BenchmarkModel,
    *,
    statement_index: int = 7,
    reviewer: Reviewer = Reviewer.GEMINI,
    attempt_id: str = "attempt-1",
) -> HistoryProposalOutput:
    return HistoryProposalOutput(
        statement_index=statement_index,
        reviewer=reviewer,
        attempt_id=attempt_id,
        model=model,
        output={
            "model": model.value,
            "output": statement_index,
            "date": "11/10/2026",
            "observations": [],
        },
    )


def profile(
    model: BenchmarkModel,
    *,
    statement_index: int = 7,
    reviewer: Reviewer = Reviewer.GEMINI,
    attempt_id: str = "attempt-1",
) -> ProfileProposalOutput:
    return ProfileProposalOutput(
        statement_index=statement_index,
        reviewer=reviewer,
        attempt_id=attempt_id,
        model=model,
        output={
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


def complete_result(**overrides) -> CompleteReviewResult:
    values = {
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "statement_index": 7,
        "reviewer": Reviewer.GEMINI,
        "main_review": main_review(),
        "histories": tuple(history(model) for model in MODELS),
        "profile_proposals": tuple(profile(model) for model in MODELS),
        "created_at": NOW,
    }
    values.update(overrides)
    return CompleteReviewResult(**values)


class CompleteReviewResultTests(unittest.TestCase):
    def test_accepts_complete_review_and_converts_to_generic_result(self) -> None:
        complete = complete_result()
        generic = complete.as_task_result()

        self.assertIsInstance(generic, TaskResult)
        self.assertTrue(generic.succeeded)
        self.assertEqual(generic.attempt_id, complete.attempt_id)
        self.assertEqual(generic.output["statement_index"], 7)
        self.assertEqual(len(generic.output["histories"]), 3)
        self.assertEqual(len(generic.output["profile_proposals"]), 3)

    def test_rejects_missing_history(self) -> None:
        with self.assertRaisesRegex(ValueError, "must contain gemma, mistral, and llama"):
            complete_result(histories=tuple(history(model) for model in MODELS[:2]))

    def test_rejects_missing_main_review(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a MainReviewOutput"):
            complete_result(main_review=None)

    def test_rejects_missing_profile_proposal(self) -> None:
        with self.assertRaisesRegex(ValueError, "must contain gemma, mistral, and llama"):
            complete_result(
                profile_proposals=tuple(profile(model) for model in MODELS[:2])
            )

    def test_rejects_duplicate_or_incorrect_model_identifiers(self) -> None:
        duplicate_histories = (
            history(BenchmarkModel.GEMMA),
            history(BenchmarkModel.GEMMA),
            history(BenchmarkModel.LLAMA),
        )
        with self.assertRaisesRegex(ValueError, "duplicate models"):
            complete_result(histories=duplicate_histories)

        with self.assertRaisesRegex(ValueError, "history model"):
            replace(
                history(BenchmarkModel.GEMMA),
                output={
                    "model": "mistral",
                    "output": 7,
                    "date": "11/10/2026",
                    "observations": [],
                },
            )

        with self.assertRaisesRegex(ValueError, "profile proposal model_name"):
            replace(
                profile(BenchmarkModel.GEMMA),
                output={
                    **dict(profile(BenchmarkModel.GEMMA).output),
                    "model_name": "llama",
                },
            )

    def test_rejects_result_from_another_statement(self) -> None:
        histories = tuple(
            history(model, statement_index=8)
            if model is BenchmarkModel.LLAMA
            else history(model)
            for model in MODELS
        )
        with self.assertRaisesRegex(ValueError, "mixes statement_index"):
            complete_result(histories=histories)

    def test_rejects_mixed_attempts(self) -> None:
        profiles = tuple(
            profile(model, attempt_id="attempt-2")
            if model is BenchmarkModel.MISTRAL
            else profile(model)
            for model in MODELS
        )
        with self.assertRaisesRegex(ValueError, "mixes execution attempts"):
            complete_result(profile_proposals=profiles)

    def test_rejects_mixed_reviewers(self) -> None:
        histories = tuple(
            history(model, reviewer=Reviewer.GROK)
            if model is BenchmarkModel.LLAMA
            else history(model)
            for model in MODELS
        )
        with self.assertRaisesRegex(ValueError, "mixes reviewers"):
            complete_result(histories=histories)

        with self.assertRaisesRegex(ValueError, "reviewer does not match"):
            replace(
                main_review(),
                output={
                    **dict(main_review().output),
                    "reviewer": "grok",
                },
            )

    def test_rejects_wrong_or_duplicate_models_in_main_review(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate model identifiers"):
            replace(
                main_review(),
                output={
                    "reviewer": "gemini",
                    "model_reviews": [
                        main_review().output["model_reviews"][0],
                        main_review().output["model_reviews"][0],
                        main_review().output["model_reviews"][2],
                    ],
                    "proposed_ideal_report": main_review().output[
                        "proposed_ideal_report"
                    ],
                },
            )

    def test_generic_task_result_remains_compatible(self) -> None:
        generic = TaskResult.success(
            task_id="generic-task",
            attempt_id="generic-attempt",
            output={"response": "synthetic"},
            now=NOW,
        )

        self.assertEqual(generic.output["response"], "synthetic")


if __name__ == "__main__":
    unittest.main()
