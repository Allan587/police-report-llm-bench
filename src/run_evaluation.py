from data_loader import load_test_cases
from prompt_builder import build_prompt
from model_runner import run_model
from rubric_evaluator import evaluate_with_rubric
import json
from pathlib import Path


def main():
    cases = load_test_cases("data/processed/test_cases.jsonl")

    active_models = [
        "gemma",
        "mistral",
        "llama",
        "gpt-neox"
    ]

    results = []

    for model_name in active_models:
        print(f"\nEvaluando modelo: {model_name}")

        for case in cases:
            prompt = build_prompt(case)
            output = run_model(model_name, prompt)
            evaluation = evaluate_with_rubric(case, output)

            result = {
                "model": model_name,
                "case_id": case["case_id"],
                "input": case,
                "output": output,
                "evaluation": evaluation
            }

            results.append(result)

            out_path = Path(f"outputs/generations/{model_name}_{case['case_id']}.json")
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            print(f"OK - {model_name} - {case['case_id']}")

    Path("outputs").mkdir(exist_ok=True)

    with open("outputs/all_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\nEvaluación finalizada.")


if __name__ == "__main__":
    main()