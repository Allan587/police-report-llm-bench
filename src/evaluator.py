import re


REQUIRED_SECTIONS = [
    "fecha",
    "hora",
    "lugar",
    "tipo",
    "descripción",
    "testigos",
    "lesionados",
    "información no proporcionada"
]


def normalize_text(text: str) -> str:
    return text.lower().strip()


def check_structure(output: str) -> dict:
    output_norm = normalize_text(output)

    found_sections = []
    missing_sections = []

    for section in REQUIRED_SECTIONS:
        if section in output_norm:
            found_sections.append(section)
        else:
            missing_sections.append(section)

    score = round(len(found_sections) / len(REQUIRED_SECTIONS), 2)

    return {
        "score": score,
        "found_sections": found_sections,
        "missing_sections": missing_sections
    }


def check_input_coverage(case_data: dict, output: str) -> dict:
    output_norm = normalize_text(output)

    checked_fields = {
        "fecha": str(case_data.get("fecha", "")),
        "hora": str(case_data.get("hora", "")),
        "lugar": str(case_data.get("lugar", "")),
        "tipo_incidente": str(case_data.get("tipo_incidente", "")),
        "testigos": str(case_data.get("testigos", "")),
        "lesionados": str(case_data.get("lesionados", ""))
    }

    present = []
    missing = []

    for field, value in checked_fields.items():
        if value and normalize_text(value) in output_norm:
            present.append(field)
        else:
            missing.append(field)

    score = round(len(present) / len(checked_fields), 2)

    return {
        "score": score,
        "present_fields": present,
        "missing_fields": missing
    }


def check_possible_hallucinations(case_data: dict, output: str) -> dict:
    output_norm = normalize_text(output)

    suspicious_patterns = {
        "nombres_inventados": r"\b(señor|señora|sr\.|sra\.)\s+[a-záéíóúñ]+\b",
        "placas_inventadas": r"\bplaca[s]?\s+[a-z0-9-]+\b",
        "armas_no_reportadas": r"\b(arma|pistola|cuchillo|revólver|rifle)\b",
        "detencion_no_reportada": r"\b(detenido|capturado|arrestado|aprehendido)\b",
        "direccion_exacta": r"\b(calle|avenida|casa|edificio|apartamento)\b"
    }

    detected = []

    for label, pattern in suspicious_patterns.items():
        if re.search(pattern, output_norm):
            detected.append(label)

    hallucination_score = 1 if not detected else 0

    return {
        "score": hallucination_score,
        "possible_hallucinations": detected
    }


def evaluate_output(case_data: dict, output: str) -> dict:
    structure = check_structure(output)
    coverage = check_input_coverage(case_data, output)
    hallucinations = check_possible_hallucinations(case_data, output)

    final_score = round(
        (
            structure["score"] * 0.30 +
            coverage["score"] * 0.40 +
            hallucinations["score"] * 0.30
        ),
        2
    )

    return {
        "final_score": final_score,
        "structure": structure,
        "coverage": coverage,
        "hallucinations": hallucinations
    }