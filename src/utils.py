import json
import time
from pathlib import Path


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def save_json(data, path: str) -> None:
    file_path = Path(path)
    ensure_dir(str(file_path.parent))

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_json(path: str):
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {path}")

    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def now_seconds() -> float:
    return time.time()


def elapsed_seconds(start_time: float) -> float:
    return round(time.time() - start_time, 2)


def safe_get(data: dict, key: str, default="Información no proporcionada"):
    value = data.get(key, default)

    if value is None or value == "":
        return default

    return value