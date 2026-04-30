import json
import pandas as pd
from pathlib import Path


def load_jsonl(path: str) -> list[dict]:
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {path}")

    cases = []

    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    return cases


def load_csv(path: str, limit: int | None = None) -> list[dict]:
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"No existe el archivo CSV: {path}")

    df = pd.read_csv(file_path)

    if limit:
        df = df.head(limit)

    return df.fillna("").to_dict(orient="records")


def load_test_cases(path: str, limit: int | None = None) -> list[dict]:
    if path.endswith(".jsonl"):
        cases = load_jsonl(path)
    elif path.endswith(".csv"):
        cases = load_csv(path, limit)
    else:
        raise ValueError("Formato no soportado. Use .jsonl o .csv")

    if limit:
        return cases[:limit]

    return cases