import yaml
from pathlib import Path
from datetime import datetime


NOT_SPECIFIED = "Not specified"

BASE_DIR = Path(__file__).resolve().parent.parent
RUBRIC_PATH = BASE_DIR / "config" / "rubric.yaml"


def load_rubric(path: Path = RUBRIC_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    return config["rubric"]


def score_1_to_5(value: float) -> int:
    if value >= 0.90:
        return 5
    if value >= 0.75:
        return 4
    if value >= 0.55:
        return 3
    if value >= 0.35:
        return 2
    return 1


def normalize_text(value) -> str:
    if value is None:
        return ""
    return str(value).lower().strip()


def incident_text(incident: dict) -> str:
    return normalize_text(incident)


def is_not_specified(value) -> bool:
    return normalize_text(value) in [
        "",
        "nan",
        "none",
        "null",
        "unknown",
        "desconocido",
        normalize_text(NOT_SPECIFIED),
    ]


def contains_value(output: str, value) -> bool:
    if is_not_specified(value):
        return True
    return normalize_text(value) in normalize_text(output)


def has_required_sections(output: str) -> bool:
    output_norm = normalize_text(output)
    return "narrativa:" in output_norm and "cierre:" in output_norm


def contains_placeholders(output: str) -> bool:
    return bool(output and "[" in output and "]" in output)


def ends_incomplete(output: str) -> bool:
    text = output.strip()

    if not text:
        return True

    text_norm = normalize_text(text)

    incomplete_endings = [
        " el caso ha",
        " el caso",
        " se ha",
        " de",
        " la",
        " el",
        " en",
        " con",
        " por",
        " para",
        " sin",
        " no se",
        " no especific",
        " que contribuyan a",
    ]

    if any(text_norm.endswith(ending) for ending in incomplete_endings):
        return True

    if text[-1] not in [".", ":", ";"]:
        return True

    return False


def has_combined_occurrence_date_time(incident: dict, output: str) -> bool:
    occurrence_date = incident.get("occurrence_date")
    occurrence_time = incident.get("occurrence_time")

    if is_not_specified(occurrence_date) or is_not_specified(occurrence_time):
        return False

    date_only = str(occurrence_date).split()[0]
    combined_value = f"{date_only} {occurrence_time}"

    return normalize_text(combined_value) in normalize_text(output)


def has_translated_not_specified(incident: dict, output: str) -> bool:
    output_norm = normalize_text(output)

    has_not_specified_field = any(
        value == NOT_SPECIFIED
        for key, value in incident.items()
        if key != "crimes"
    )

    if not has_not_specified_field:
        return False

    translated_terms = [
        "no especificado",
        "no se especifica",
        "desconocido",
    ]

    return any(term in output_norm for term in translated_terms)


def has_operational_recommendation(output: str) -> bool:
    output_norm = normalize_text(output)

    forbidden_terms = [
        "se recomienda",
        "recomienda",
        "continuar con la investigación",
        "continuar la investigación",
        "seguir investigando",
        "investigación adicional",
        "investigacion adicional",
        "acciones futuras",
        "acción futura",
        "accion futura",
        "instrucciones operativas",
        "se solicita",
        "se informará",
        "desarrollo futuro",
        "mantener al día",
        "recopilando pruebas",
        "puede dirigirse",
        "contactarnos",
        "vigilancia continua",
        "futuras investigaciones",
        "cualquier testigo",
        "aportar información",
        "aportar informacion",
        "brindar información",
        "brindar informacion",
    ]

    return any(term in output_norm for term in forbidden_terms)


def has_invented_location(incident: dict, output: str) -> bool:
    output_norm = normalize_text(output)

    known_location_values = [
        normalize_text(incident.get("area")),
        normalize_text(incident.get("premises")),
        normalize_text(incident.get("location")),
        normalize_text(incident.get("nearby_cross_street")),
    ]

    known_location_text = " ".join(value for value in known_location_values if value)

    risky_locations = [
        "san josé",
        "san jose",
        "avenida segunda",
        "avenidas segunda",
        "floresta",
        "floresta sur",
        "zona norte",
        "capital",
        "calle san francisco",
        "san francisco street",
    ]

    for location in risky_locations:
        if location in output_norm and location not in known_location_text:
            return True

    return False


def has_invented_violence(incident: dict, output: str) -> bool:
    output_norm = normalize_text(output)
    incident_norm = incident_text(incident)

    violence_terms = [
        "violencia",
        "intimidación",
        "intimidacion",
        "amenaza",
        "amenazó",
        "amenazo",
        "asalto",
        "agresión",
        "agresion",
        "lesión",
        "lesion",
        "lesiones",
        "golpe",
        "golpes",
        "forcejeo",
    ]

    for term in violence_terms:
        if term in output_norm and term not in incident_norm:
            return True

    return False


def has_invented_weapon(incident: dict, output: str) -> bool:
    output_norm = normalize_text(output)
    incident_norm = incident_text(incident)

    negative_weapon_phrases = [
        "no se reportaron armas",
        "no se reportó arma",
        "no se reporto arma",
        "no se registraron armas",
        "no se registró arma",
        "no se registro arma",
        "no se dispone de información sobre armas",
        "no se dispone de informacion sobre armas",
        "no se dispone de información sobre arma",
        "no se dispone de informacion sobre arma",
        "no se cuenta con información sobre armas",
        "no se cuenta con informacion sobre armas",
        "no se cuenta con información sobre arma",
        "no se cuenta con informacion sobre arma",
        "sin arma",
        "sin armas",
        "arma utilizada: not specified",
        "arma utilizada not specified",
    ]

    if any(phrase in output_norm for phrase in negative_weapon_phrases):
        return False

    weapon_terms = [
        "arma de fuego",
        "arma blanca",
        "pistola",
        "revólver",
        "revolver",
        "cuchillo",
        "firearm",
        "knife",
        "gun",
        "disparos",
        "shots fired",
    ]

    for term in weapon_terms:
        if term in output_norm and term not in incident_norm:
            return True

    return False


def has_invented_people(incident: dict, output: str) -> bool:
    output_norm = normalize_text(output)

    risky_terms = [
        "sospechoso huyó",
        "sospechoso huyo",
        "sospechoso fue",
        "sospechosos huyeron",
        "responsable huyó",
        "responsable huyo",
        "responsables huyeron",
        "testigo indicó",
        "testigo indico",
        "testigos indicaron",
        "oficial entrevistó",
        "oficial entrevisto",
        "declaró",
        "declaro",
        "nombre del sospechoso",
        "identificado como",
    ]

    return any(term in output_norm for term in risky_terms)


def has_invented_additional_incidents(output: str) -> bool:
    output_norm = normalize_text(output)

    risky_terms = [
        "otro incidente",
        "otros incidentes",
        "incidente adicional",
        "incidentes adicionales",
        "caso adicional",
        "casos adicionales",
        "hecho adicional",
        "hechos adicionales",
        "conexiones directas",
        "conexión directa",
        "conexion directa",
        "caso relacionado",
        "hecho similar",
        "evento relacionado",
        "incidente relacionado",
    ]

    return any(term in output_norm for term in risky_terms)


def has_false_multiple_crimes_reference(incident: dict, output: str) -> bool:
    crimes = incident.get("crimes", [])

    if len(crimes) > 1:
        return False

    output_norm = normalize_text(output)

    risky_terms = [
        "otros crímenes registrados",
        "otros crimenes registrados",
        "otros delitos registrados",
        "crímenes adicionales",
        "crimenes adicionales",
        "delitos adicionales",
        "otro crimen",
        "otro delito",
        "otro robo",
        "incluyendo otro",
        "incluye otro",
    ]

    return any(term in output_norm for term in risky_terms)


def parse_dataset_date(date_value: str):
    if not date_value:
        return None

    raw_date = str(date_value).split()[0].strip()

    possible_formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
    ]

    for date_format in possible_formats:
        try:
            return datetime.strptime(raw_date, date_format)
        except ValueError:
            continue

    return None


def build_spanish_date_variants(date_obj: datetime) -> list[str]:
    months = {
        1: "enero",
        2: "febrero",
        3: "marzo",
        4: "abril",
        5: "mayo",
        6: "junio",
        7: "julio",
        8: "agosto",
        9: "septiembre",
        10: "octubre",
        11: "noviembre",
        12: "diciembre",
    }

    day = date_obj.day
    month = months[date_obj.month]
    year = date_obj.year

    return [
        f"{day} de {month} de {year}",
        f"{day:02d} de {month} de {year}",
    ]


def has_mixed_report_date_with_occurrence_time(incident: dict, output: str) -> bool:
    output_norm = normalize_text(output)

    report_date_raw = str(incident.get("report_date", "")).split()[0].strip()
    occurrence_date_raw = str(incident.get("occurrence_date", "")).split()[0].strip()
    occurrence_time = str(incident.get("occurrence_time", "")).strip()

    if not report_date_raw or not occurrence_time:
        return False

    if report_date_raw == occurrence_date_raw:
        return False

    report_date_obj = parse_dataset_date(report_date_raw)

    numeric_patterns = [
        f"{report_date_raw} a las {occurrence_time}",
        f"{report_date_raw}, a las {occurrence_time}",
        f"{report_date_raw} {occurrence_time}",
    ]

    for pattern in numeric_patterns:
        if normalize_text(pattern) in output_norm:
            return True

    if report_date_obj:
        for spanish_date in build_spanish_date_variants(report_date_obj):
            textual_patterns = [
                f"{spanish_date} a las {occurrence_time}",
                f"{spanish_date}, a las {occurrence_time}",
                f"{spanish_date} {occurrence_time}",
            ]

            for pattern in textual_patterns:
                if normalize_text(pattern) in output_norm:
                    return True

    return False


def has_crime_meaning_distortion(incident: dict, output: str) -> bool:
    output_norm = normalize_text(output)
    crime_type_norm = normalize_text(incident.get("crime_type", ""))

    if is_not_specified(crime_type_norm):
        return False

    if "burglary" in crime_type_norm:
        if "vehículo robado" in output_norm or "vehiculo robado" in output_norm:
            return True

    if "robbery" in crime_type_norm and "hurto" in output_norm:
        return True

    if ("theft" in crime_type_norm or "stolen" in crime_type_norm) and any(
        term in output_norm
        for term in [
            "robo con violencia",
            "robo mediante intimidación",
            "robo mediante intimidacion",
            "asalto",
        ]
    ):
        return True

    wrong_meaning_terms = [
        "extraviado",
        "extraviada",
        "perdido",
        "perdida",
    ]

    return any(term in output_norm for term in wrong_meaning_terms)


def get_crime_descriptions(incident: dict) -> list[str]:
    crimes = incident.get("crimes", [])
    descriptions = []

    for crime in crimes:
        description = crime.get("description", NOT_SPECIFIED)

        if not is_not_specified(description):
            descriptions.append(description)

    if not descriptions and not is_not_specified(incident.get("crime_type")):
        descriptions.append(incident["crime_type"])

    return descriptions


def evaluate_field_coverage(incident: dict, output: str) -> int:
    if not has_required_sections(output):
        return 1

    if contains_placeholders(output):
        return 1

    key_fields = [
        "premises",
        "location",
    ]

    present = 0
    total = 0

    for field in key_fields:
        value = incident.get(field)

        if is_not_specified(value):
            continue

        total += 1

        if contains_value(output, value):
            present += 1

    base_score = score_1_to_5(present / total) if total else 5
    crime_score = evaluate_crime_type(incident, output)

    return round((base_score + crime_score) / 2)


def evaluate_crime_type(incident: dict, output: str) -> int:
    if contains_placeholders(output):
        return 1

    crime_type = incident.get("crime_type", NOT_SPECIFIED)

    if is_not_specified(crime_type):
        return 5

    if contains_value(output, crime_type):
        return 5

    if has_crime_meaning_distortion(incident, output):
        return 1

    return 3


def evaluate_temporal_consistency(incident: dict, output: str) -> int:
    if has_combined_occurrence_date_time(incident, output):
        return 1

    if has_mixed_report_date_with_occurrence_time(incident, output):
        return 1

    return 5


def evaluate_spatial_consistency(incident: dict, output: str) -> int:
    if contains_placeholders(output):
        return 1

    if has_invented_location(incident, output):
        return 1

    spatial_fields = [
        "area",
        "premises",
        "location",
    ]

    missing = 0
    total = 0

    for field in spatial_fields:
        value = incident.get(field)

        if is_not_specified(value):
            continue

        total += 1

        if not contains_value(output, value):
            missing += 1

    if total == 0:
        return 5

    ratio = 1 - (missing / total)

    return score_1_to_5(ratio)


def evaluate_empty_fields(incident: dict, output: str) -> int:
    if has_translated_not_specified(incident, output):
        return 3

    return 5


def evaluate_no_fake_weapons(incident: dict, output: str) -> int:
    if has_invented_weapon(incident, output):
        return 1

    return 5


def evaluate_no_fake_people(incident: dict, output: str) -> int:
    if has_invented_people(incident, output):
        return 1

    return 5


def evaluate_formal_language(output: str) -> int:
    output_norm = normalize_text(output)

    informal_terms = [
        "mae",
        "tipo",
        "la vara",
        "obvio",
        "seguro que",
    ]

    if any(term in output_norm for term in informal_terms):
        return 1

    if len(output.strip()) < 40:
        return 2

    if contains_placeholders(output):
        return 1

    if ends_incomplete(output):
        return 1

    if not has_required_sections(output):
        return 2

    return 5


def evaluate_data_fidelity(incident: dict, output: str) -> int:
    if not has_required_sections(output):
        return 1

    if contains_placeholders(output):
        return 1

    if ends_incomplete(output):
        return 1

    if has_combined_occurrence_date_time(incident, output):
        return 1

    if has_crime_meaning_distortion(incident, output):
        return 1

    if has_invented_violence(incident, output):
        return 1

    if has_invented_weapon(incident, output):
        return 1

    if has_invented_people(incident, output):
        return 1

    if has_translated_not_specified(incident, output):
        return 3

    if has_operational_recommendation(output):
        return 1

    if has_invented_location(incident, output):
        return 1

    if has_invented_additional_incidents(output):
        return 1

    if has_false_multiple_crimes_reference(incident, output):
        return 1

    if has_mixed_report_date_with_occurrence_time(incident, output):
        return 1

    field_coverage = evaluate_field_coverage(incident, output)
    crime_type = evaluate_crime_type(incident, output)
    temporal = evaluate_temporal_consistency(incident, output)
    spatial = evaluate_spatial_consistency(incident, output)
    weapons = evaluate_no_fake_weapons(incident, output)

    average = (field_coverage + crime_type + temporal + spatial + weapons) / 5

    return round(average)


def evaluate_contradiction_check(incident: dict, output: str) -> int:
    if not output or len(output.strip()) < 40:
        return 1

    if not has_required_sections(output):
        return 1

    if contains_placeholders(output):
        return 1

    if ends_incomplete(output):
        return 1

    if has_combined_occurrence_date_time(incident, output):
        return 1

    if has_crime_meaning_distortion(incident, output):
        return 1

    if has_invented_violence(incident, output):
        return 1

    if has_invented_weapon(incident, output):
        return 1

    if has_invented_people(incident, output):
        return 1

    if has_operational_recommendation(output):
        return 1

    if has_invented_location(incident, output):
        return 1

    if has_mixed_report_date_with_occurrence_time(incident, output):
        return 1

    if has_invented_additional_incidents(output):
        return 1

    if has_false_multiple_crimes_reference(incident, output):
        return 1

    return 5


def calculate_weighted_score(scores: dict, rubric: list[dict]) -> float:
    total = 0

    for item in rubric:
        criterion_id = item["id"]
        weight = item["weight"]
        total += scores.get(criterion_id, 1) * weight

    return round(total, 2)


def build_score_classification(score: int, status: str) -> dict:
    labels = {
        1: "muy_deficiente",
        2: "deficiente",
        3: "aceptable_con_riesgo",
        4: "bueno",
        5: "excelente",
    }

    descriptions = {
        1: "El reporte presenta errores graves de fidelidad factual o estructura.",
        2: "El reporte presenta varios errores relevantes y requiere corrección importante.",
        3: "El reporte es parcialmente útil, pero requiere revisión antes de ser aceptado.",
        4: "El reporte es mayormente fiel y utilizable, con ajustes menores.",
        5: "El reporte es fiel, claro y no presenta errores relevantes detectados.",
    }

    return {
        "score_1_to_5": score,
        "label": labels.get(score, "sin_clasificacion"),
        "description": descriptions.get(score, "No hay descripción disponible."),
        "status": status,
    }


def evaluate_with_rubric(incident: dict, output: str) -> dict:
    scores = {
        "data_fidelity": evaluate_data_fidelity(incident, output),
        "field_coverage": evaluate_field_coverage(incident, output),
        "no_fake_people": evaluate_no_fake_people(incident, output),
        "no_fake_weapons": evaluate_no_fake_weapons(incident, output),
        "temporal_consistency": evaluate_temporal_consistency(incident, output),
        "spatial_consistency": evaluate_spatial_consistency(incident, output),
        "empty_fields": evaluate_empty_fields(incident, output),
        "crime_type": evaluate_crime_type(incident, output),
        "formal_language": evaluate_formal_language(output),
        "contradiction_check": evaluate_contradiction_check(incident, output),
    }

    weighted_score = round(sum(scores.values()) / len(scores), 2)

    score_1_to_5_result = 1
    if weighted_score >= 4.5:
        score_1_to_5_result = 5
    elif weighted_score >= 3.5:
        score_1_to_5_result = 4
    elif weighted_score >= 2.5:
        score_1_to_5_result = 3
    elif weighted_score >= 1.5:
        score_1_to_5_result = 2

    critical_errors = (
        ends_incomplete(output)
        or not has_required_sections(output)
        or contains_placeholders(output)
        or has_operational_recommendation(output)
        or has_invented_location(incident, output)
        or has_crime_meaning_distortion(incident, output)
        or has_invented_violence(incident, output)
        or has_invented_weapon(incident, output)
        or has_invented_people(incident, output)
        or has_invented_additional_incidents(output)
        or has_false_multiple_crimes_reference(incident, output)
        or has_mixed_report_date_with_occurrence_time(incident, output)
    )

    if critical_errors or scores["data_fidelity"] <= 2 or scores["contradiction_check"] <= 2:
        status = "no_apto"
        score_1_to_5_result = min(score_1_to_5_result, 2)
    elif weighted_score < 4:
        status = "requiere_revision"
        score_1_to_5_result = min(score_1_to_5_result, 3)
    else:
        status = "apto"

    return {
        "scores": scores,
        "weighted_score": weighted_score,
        "score_1_to_5": score_1_to_5_result,
        "score_classification": {
            "score_1_to_5": score_1_to_5_result,
            "label": status_label(status),
            "description": status_description(status),
            "status": status,
        },
        "status": status,
    }


def status_label(status: str) -> str:
    mapping = {
        "apto": "bueno",
        "requiere_revision": "aceptable_con_riesgo",
        "no_apto": "deficiente",
    }
    return mapping.get(status, "desconocido")


def status_description(status: str) -> str:
    mapping = {
        "apto": "El reporte es mayormente fiel y utilizable, con ajustes menores.",
        "requiere_revision": "El reporte es parcialmente útil, pero requiere revisión antes de ser aceptado.",
        "no_apto": "El reporte presenta varios errores relevantes y requiere corrección importante.",
    }
    return mapping.get(status, "")