import json
import argparse
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from prompt_builder import build_statement_generation_prompt
from model_runner import run_model, unload_model
from utils import get_enabled_models


BASE_DIR = Path(__file__).resolve().parent.parent

STATEMENTS_PATH = BASE_DIR / "data" / "statements" / "statements.jsonl"
RUNS_DIR = BASE_DIR / "data" / "generated" / "runs"


def safe_model_name(model_name: str) -> str:
    return model_name.replace("/", "_").replace("-", "_")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo: {path}")

    records = []

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))

    return records


def create_run_directory() -> tuple[str, Path]:
    run_id = (
        datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        + "_"
        + uuid4().hex[:8]
    )

    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    return run_id, run_dir


def save_metadata(
    run_dir: Path,
    run_id: str,
    start_index: int,
    end_index: int,
    models: list[dict],
) -> None:
    metadata = {
        "run_id": run_id,
        "input_source": str(STATEMENTS_PATH),
        "index_type": "statement_index",
        "start_index": start_index,
        "end_index": end_index,
        "models": [
            {
                "name": model["name"],
                "model_id": model["model_id"],
                "enabled": model.get("enabled", False),
            }
            for model in models
        ],
    }

    metadata_path = run_dir / "metadata.json"

    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=4)


def save_model_reports(
    run_dir: Path,
    model_name: str,
    records: list[dict],
) -> Path:
    model_dir = run_dir / safe_model_name(model_name) / "generation"
    model_dir.mkdir(parents=True, exist_ok=True)

    output_path = model_dir / "draft_reports.jsonl"

    with open(output_path, "w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Reportes guardados para {model_name}: {output_path}")

    return output_path


def filter_statements_by_index(
    statements: list[dict],
    start_index: int,
    end_index: int,
) -> list[dict]:
    selected = []

    for statement in statements:
        statement_index = statement.get("statement_index")

        if statement_index is None:
            continue

        try:
            statement_index = int(statement_index)
        except ValueError:
            continue

        if start_index <= statement_index <= end_index:
            selected.append(statement)

    return selected


def generate_reports(start_index: int = 0, end_index: int = 0) -> None:
    statements = read_jsonl(STATEMENTS_PATH)

    selected_statements = filter_statements_by_index(
        statements=statements,
        start_index=start_index,
        end_index=end_index,
    )

    if not selected_statements:
        raise ValueError(
            f"No se encontraron declaraciones entre statement_index "
            f"{start_index} y {end_index}"
        )

    enabled_models = get_enabled_models()

    if not enabled_models:
        raise ValueError("No hay modelos habilitados en models.yaml")

    run_id, run_dir = create_run_directory()

    save_metadata(
        run_dir=run_dir,
        run_id=run_id,
        start_index=start_index,
        end_index=end_index,
        models=enabled_models,
    )

    for model_config in enabled_models:
        model_name = model_config["name"]
        model_id = model_config["model_id"]

        print(f"\nGenerando reportes con modelo: {model_name}")

        model_records = []

        for statement_record in selected_statements:
            statement_index = statement_record.get("statement_index")
            incident_index = statement_record.get("incident_index")

            print(f"Procesando statement_index {statement_index}")

            prompt = build_statement_generation_prompt(statement_record)

            result = run_model(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=model_config.get("generation", {}).get("max_new_tokens", 700),
                temperature=model_config.get("generation", {}).get("temperature", 0.2),
                top_p=model_config.get("generation", {}).get("top_p"),
                repetition_penalty=model_config.get("generation", {}).get("repetition_penalty"),
            )

            model_records.append({
                "run_id": run_id,
                "model_name": model_name,
                "model_id": model_id,
                "statement_index": statement_index,
                "incident_index": incident_index,
                "statement_record": statement_record,
                "prompt": prompt,
                "draft_report": result,
            })

        save_model_reports(
            run_dir=run_dir,
            model_name=model_name,
            records=model_records,
        )

        unload_model(model_id)

    print(f"\nEjecución completada: {run_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="statement_index inicial.",
    )

    parser.add_argument(
        "--end-index",
        type=int,
        default=0,
        help="statement_index final.",
    )

    args = parser.parse_args()

    generate_reports(start_index=110,end_index=115) 