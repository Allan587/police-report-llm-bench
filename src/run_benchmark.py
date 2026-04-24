import json
import time
import yaml
from pathlib import Path

from data_loader import load_jsonl
from evaluator import evaluate_output
from ollama_client import generate_with_ollama
from prompts import build_police_report_prompt


CONFIG_PATH = "config/models.yaml"
TEST_CASES_PATH = "data/processed/test_cases.jsonl"
OUTPUT_DIR = Path("outputs")


def load_models(path: str):
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    return [
        model["name"]
        for model in config.get("models", [])
        if model.get("enabled", True)
    ]


def save_output(record: dict):
    model_safe_name = record["model"].replace(":", "_")
    case_id = record["case_id"]

    output_path = OUTPUT_DIR / f"{model_safe_name}_{case_id}.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(record, file, ensure_ascii=False, indent=2)

    return output_path


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    models = load_models(CONFIG_PATH)
    test_cases = load_jsonl(TEST_CASES_PATH)

    all_results = []

    print("\n=== BENCHMARK DE MODELOS IA ===")
    print(f"Modelos activos: {models}")
    print(f"Casos de prueba: {len(test_cases)}")

    for model_name in models:
        print(f"\nProbando modelo: {model_name}")

        for case in test_cases:
            prompt = build_police_report_prompt(case)

            start_time = time.time()

            try:
                output = generate_with_ollama(
                    model_name=model_name,
                    prompt=prompt,
                    temperature=0.2,
                    max_tokens=350
                )

                latency = round(time.time() - start_time, 2)
                
                evaluation = evaluate_output(case, output)
                
                record = {
                    "model": model_name,
                    "case_id": case["case_id"],
                    "temperature": 0.2,
                    "max_tokens": 350,
                    "latency_seconds": latency,
                    "input": case,
                    "output": output,
                    "evaluation": evaluation
                }

                output_path = save_output(record)
                all_results.append(record)

                print(f"OK - {case['case_id']} guardado en {output_path}")

            except Exception as error:
                print(f"ERROR - {model_name} / {case['case_id']}: {error}")

    all_results_path = OUTPUT_DIR / "all_results.json"

    with all_results_path.open("w", encoding="utf-8") as file:
        json.dump(all_results, file, ensure_ascii=False, indent=2)

    print(f"\nResultados consolidados: {all_results_path}")


if __name__ == "__main__":
    main()