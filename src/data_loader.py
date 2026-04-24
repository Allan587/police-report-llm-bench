import json
from pathlib import Path


def load_jsonl(path: str):
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {file_path}")

    records = []

    with file_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"Error JSON en línea {line_number}: {error}")

    return records