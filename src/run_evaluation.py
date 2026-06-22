import json
import argparse
from pathlib import Path

from rubric_evaluator import evaluate_with_rubric


BASE_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = BASE_DIR / "data" / "generated" / "runs"


def get_latest_run_dir() -> Path:
    run_dirs = [path for path in RUNS_DIR.iterdir() if path.is_dir()]

    if not run_dirs:
        raise FileNotFoundError("No existen ejecuciones en data/generated/runs")

    return sorted(run_dirs)[-1]


def read_jsonl(path: Path) -> list[dict]:
    records = []

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))

    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def evaluate_model_outputs(run_dir: Path, model_dir: Path) -> list[dict]:
    draft_path = model_dir / "generation" / "draft_reports.jsonl"

    if not draft_path.exists():
        return []

    records = read_jsonl(draft_path)
    evaluated_records = []

    for record in records:
        incident = record["incident"]
        evaluated_report = record["draft_report"]

        evaluation = evaluate_with_rubric(
            incident=incident,
            output=evaluated_report,
        )

        evaluated_records.append({
            "run_id": record.get("run_id"),
            "model_name": record.get("model_name", model_dir.name),
            "model_id": record.get("model_id"),
            "incident_index": record.get("incident_index"),
            "draft_report": evaluated_report,
            "evaluation": evaluation,
        })

    evaluation_dir = model_dir / "evaluation"
    output_path = evaluation_dir / "evaluated_reports.jsonl"

    write_jsonl(output_path, evaluated_records)

    return evaluated_records


def summarize_model_results(model_name: str, evaluated_records: list[dict]) -> dict:
    if not evaluated_records:
        return {
            "model_name": model_name,
            "total_reports": 0,
            "average_score": 0,
            "status_counts": {},
        }

    total_score = 0
    status_counts = {}

    for record in evaluated_records:
        evaluation = record["evaluation"]
        total_score += evaluation["weighted_score"]

        status = evaluation["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    average_score = round(total_score / len(evaluated_records), 2)

    return {
        "model_name": model_name,
        "total_reports": len(evaluated_records),
        "average_score": average_score,
        "status_counts": status_counts,
    }


def run_evaluation(run_dir: Path | None = None) -> None:
    if run_dir is None:
        run_dir = get_latest_run_dir()

    print(f"Evaluando corrida: {run_dir}")

    all_model_summaries = []
    all_evaluated_records = []

    model_dirs = [
        path for path in run_dir.iterdir()
        if path.is_dir()
        and (path / "generation" / "draft_reports.jsonl").exists()
    ]

    for model_dir in model_dirs:
        print(f"\nEvaluando modelo: {model_dir.name}")

        evaluated_records = evaluate_model_outputs(
            run_dir=run_dir,
            model_dir=model_dir,
        )

        if not evaluated_records:
            print(f"No hay draft_reports.jsonl para {model_dir.name}")
            continue

        model_summary = summarize_model_results(
            model_name=model_dir.name,
            evaluated_records=evaluated_records,
        )

        all_model_summaries.append(model_summary)
        all_evaluated_records.extend(evaluated_records)

        for record in evaluated_records:
            evaluation = record["evaluation"]

            print(
                f"  Incidente {record['incident_index']} | "
                f"Score: {evaluation['score_1_to_5']}/5 | "
                f"Weighted: {evaluation['weighted_score']} | "
                f"Status: {evaluation['status']}"
            )

            print(
                f"    Data Fidelity: {evaluation['scores']['data_fidelity']} | "
                f"Field Coverage: {evaluation['scores']['field_coverage']} | "
                f"No Fake People: {evaluation['scores']['no_fake_people']} | "
                f"No Fake Weapons: {evaluation['scores']['no_fake_weapons']}"
            )

            print(
                f"    Temporal: {evaluation['scores']['temporal_consistency']} | "
                f"Spatial: {evaluation['scores']['spatial_consistency']} | "
                f"Empty Fields: {evaluation['scores']['empty_fields']}"
            )

            print(
                f"    Crime Type: {evaluation['scores']['crime_type']} | "
                f"Formal Language: {evaluation['scores']['formal_language']} | "
                f"Contradiction Check: {evaluation['scores']['contradiction_check']}"
            )

        print(
            f"OK - {model_dir.name} | "
            f"Promedio: {model_summary['average_score']} | "
            f"Reportes: {model_summary['total_reports']}"
        )

    evaluation_dir = run_dir / "evaluation"

    write_jsonl(
        evaluation_dir / "all_evaluated_reports.jsonl",
        all_evaluated_records,
    )

    summary = {
        "run_id": run_dir.name,
        "models": all_model_summaries,
    }

    write_json(
        evaluation_dir / "evaluation_summary.json",
        summary,
    )

    print(f"\nEvaluación finalizada: {evaluation_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--run-dir",
        type=str,
        default=None,
        help="Ruta de la corrida a evaluar. Si no se indica, usa la última corrida.",
    )

    args = parser.parse_args()

    selected_run_dir = Path(args.run_dir) if args.run_dir else None

    run_evaluation(run_dir=selected_run_dir)