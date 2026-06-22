import json
import yaml
import pandas as pd
from pathlib import Path


NOT_SPECIFIED = "Not specified"

BASE_DIR = Path(__file__).resolve().parent.parent

RAW_DATA_PATH = BASE_DIR / "data" / "raw" / "Crime_Data.csv"
FIELDS_PATH = BASE_DIR / "config" / "fields.yaml"
OUTPUT_PATH = BASE_DIR / "data" / "processed" / "clean_incidents.jsonl"


def load_fields(path: Path = FIELDS_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")

    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    return config["fields"]


def clean_value(value) -> str:
    if pd.isna(value):
        return NOT_SPECIFIED

    value = str(value).strip()

    if value == "" or value.lower() in ["nan", "none", "null"]:
        return NOT_SPECIFIED

    return " ".join(value.split())


def clean_crime_code(value) -> str:
    value = clean_value(value)

    if value == NOT_SPECIFIED:
        return NOT_SPECIFIED

    value = value.replace(".0", "").strip()

    if not value.isdigit():
        return NOT_SPECIFIED

    return value


def format_time(value) -> str:
    value = clean_value(value)

    if value == NOT_SPECIFIED:
        return NOT_SPECIFIED

    value = value.replace(".0", "").strip()

    if not value.isdigit():
        return NOT_SPECIFIED

    value = value.zfill(4)

    hour = int(value[:2])
    minute = int(value[2:])

    if hour < 0 or hour > 23:
        return NOT_SPECIFIED

    if minute < 0 or minute > 59:
        return NOT_SPECIFIED

    return f"{hour:02d}:{minute:02d}"


def build_crime_code_map(df: pd.DataFrame) -> dict:
    """
    Construye un mapa código -> descripción usando Crm Cd y Crm Cd Desc.

    Esto sirve para traducir los códigos Crm Cd 1, Crm Cd 2,
    Crm Cd 3 y Crm Cd 4 a descripciones entendibles.
    """

    crime_code_map = {}

    if "Crm Cd" not in df.columns or "Crm Cd Desc" not in df.columns:
        return crime_code_map

    for _, row in df[["Crm Cd", "Crm Cd Desc"]].dropna().iterrows():
        code = clean_crime_code(row["Crm Cd"])
        description = clean_value(row["Crm Cd Desc"])

        if code != NOT_SPECIFIED and description != NOT_SPECIFIED:
            crime_code_map[code] = description

    return crime_code_map


def get_crime_description_by_code(code_value, crime_code_map: dict) -> str:
    code = clean_crime_code(code_value)

    if code == NOT_SPECIFIED:
        return NOT_SPECIFIED

    return crime_code_map.get(code, f"Unknown crime code")


def build_crimes(row: pd.Series, fields: dict, crime_code_map: dict) -> list[dict]:
    """
    Limpia los cuatro espacios de crímenes registrados en el CSV:

    - Crm Cd 1
    - Crm Cd 2
    - Crm Cd 3
    - Crm Cd 4

    No se agrega Crm Cd por separado porque Crm Cd y Crm Cd 1
    representan el mismo dato principal.
    """

    crime_code_fields = [
        "crime_code_1",
        "crime_code_2",
        "crime_code_3",
        "crime_code_4",
    ]

    crimes = []

    for field_name in crime_code_fields:
        csv_column = fields.get(field_name)

        if not csv_column or csv_column not in row.index:
            continue

        code = clean_crime_code(row[csv_column])

        if code == NOT_SPECIFIED:
            continue

        description = get_crime_description_by_code(code, crime_code_map)

        crimes.append({
            "description": description
        })

    return crimes


def load_crime_csv(limit: int | None = None) -> list[dict]:
    fields = load_fields()

    df = pd.read_csv(RAW_DATA_PATH)

    if limit:
        df = df.head(limit)

    crime_code_map = build_crime_code_map(df)

    records = []

    skip_direct_fields = {
        "crime_code_1",
        "crime_code_2",
        "crime_code_3",
        "crime_code_4",
    }

    for _, row in df.iterrows():
        incident = {}

        for target_field, csv_column in fields.items():
            if target_field in skip_direct_fields:
                continue

            if csv_column not in df.columns:
                incident[target_field] = NOT_SPECIFIED
                continue

            value = row[csv_column]

            if target_field == "occurrence_time":
                incident[target_field] = format_time(value)
            else:
                incident[target_field] = clean_value(value)

        incident["crimes"] = build_crimes(
            row=row,
            fields=fields,
            crime_code_map=crime_code_map
        )

        records.append(incident)

    return records


def save_jsonl(
    records: list[dict],
    output_path: Path = OUTPUT_PATH,
    append: bool = True,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    mode = "a" if append else "w"

    with open(output_path, mode, encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def clear_data_reports(start_index: int = 0, limit: int = 1) -> None:
    incidents = load_crime_csv(limit=start_index + limit)
    incidents = incidents[start_index:start_index + limit]

    incident_list = []

    for local_index, incident in enumerate(incidents):
        incident_index = start_index + local_index

        incident_list.append({
            "incident_index": incident_index,
            "incident": incident
        })

    save_jsonl(incident_list, append=True)

    print(
        f"Se guardaron {len(incident_list)} incidentes "
        f"desde el índice {start_index} hasta {start_index + len(incident_list) - 1}"
    )

if __name__ == "__main__":
    clear_data_reports(start_index=320, limit= 320)