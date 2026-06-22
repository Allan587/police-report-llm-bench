import yaml
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_PATH = BASE_DIR / "config" / "models.yaml" # Specify the correct model file before to using it.


def load_models_config(path: Path = MODELS_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    return config["models"]


def get_enabled_models(path: Path = MODELS_PATH) -> list[dict]:
    models = load_models_config(path)

    return [
        model
        for model in models
        if model.get("enabled", False)
    ]


def get_enabled_model_ids(path: Path = MODELS_PATH) -> list[str]:
    return [
        model["model_id"]
        for model in get_enabled_models(path)
    ]