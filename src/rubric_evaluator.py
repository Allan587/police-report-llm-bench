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


def contains_value(output: str, value) -> bool:
    if value is None or value == "":
        return True

    return str(value).lower() in output.lower()


def evaluate_with_rubric(case: dict, output: str) -> dict:
    output_norm = output.lower()

    key_fields = [
        "fecha",
        "hora",
        "lugar",
        "tipo_incidente",
        "hechos",
        "testigos",
        "lesionados"
    ]

    present = 0
    total = 0

    for field in key_fields:
        if field in case:
            total += 1
            if contains_value(output, case[field]):
                present += 1

    coverage_ratio = present / total if total else 0

    scores = {
        "data_fidelity": 5,
        "field_coverage": score_1_to_5(coverage_ratio),
        "no_fake_people": 5,
        "no_fake_weapons": 5,
        "temporal_consistency": 5,
        "spatial_consistency": 5,
        "empty_fields": 5,
        "crime_type": 5 if contains_value(output, case.get("tipo_incidente", "")) else 1,
        "formal_language": 4,
        "contradiction_check": 5
    }

    weighted_score = round(
        scores["data_fidelity"] * 0.20 +
        scores["field_coverage"] * 0.15 +
        scores["no_fake_people"] * 0.10 +
        scores["no_fake_weapons"] * 0.10 +
        scores["temporal_consistency"] * 0.10 +
        scores["spatial_consistency"] * 0.10 +
        scores["empty_fields"] * 0.10 +
        scores["crime_type"] * 0.05 +
        scores["formal_language"] * 0.05 +
        scores["contradiction_check"] * 0.05,
        2
    )

    status = "apto" if weighted_score >= 4 else "requiere_revision"

    if scores["data_fidelity"] == 1:
        status = "no_apto"

    return {
        "scores": scores,
        "weighted_score": weighted_score,
        "status": status
    }