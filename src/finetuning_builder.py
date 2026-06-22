import json
import random
from pathlib import Path

from prompt_builder import build_finetuning_generation_prompt


BASE_DIR = Path(__file__).resolve().parent.parent

FINETUNING_DIR = BASE_DIR / "data" / "finetuning"
APPROVED_REPORTS_DIR = FINETUNING_DIR / "approved_reports"

TRAIN_PATH = FINETUNING_DIR / "train.jsonl"
VALIDATION_PATH = FINETUNING_DIR / "validation.jsonl"
TEST_PATH = FINETUNING_DIR / "test.jsonl"


def read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def collect_approved_reports() -> list[dict]:
    records = []

    json_files = sorted(APPROVED_REPORTS_DIR.glob("*.json"))

    if not json_files:
        raise ValueError(
            f"No se encontraron archivos en {APPROVED_REPORTS_DIR}"
        )

    for file_path in json_files:
        data = read_json(file_path)

        incident = data["incident"]
        approved_report = data["approved_report"]

        records.append({
            "prompt": build_finetuning_generation_prompt(incident),
            "completion": approved_report,
        })

    return records


def split_dataset(
    records: list[dict],
    train_ratio: float = 0.8,
    validation_ratio: float = 0.1,
):
    random.shuffle(records)

    total = len(records)

    train_end = int(total * train_ratio)
    validation_end = train_end + int(total * validation_ratio)

    train_records = records[:train_end]
    validation_records = records[train_end:validation_end]
    test_records = records[validation_end:]

    return train_records, validation_records, test_records


def build_finetuning_dataset():
    records = collect_approved_reports()

    train_records, validation_records, test_records = split_dataset(records)

    write_jsonl(TRAIN_PATH, train_records)
    write_jsonl(VALIDATION_PATH, validation_records)
    write_jsonl(TEST_PATH, test_records)

    print()
    print("===== DATASET GENERADO =====")
    print(f"Total: {len(records)}")
    print(f"Train: {len(train_records)}")
    print(f"Validation: {len(validation_records)}")
    print(f"Test: {len(test_records)}")
    print()


if __name__ == "__main__":
    build_finetuning_dataset()