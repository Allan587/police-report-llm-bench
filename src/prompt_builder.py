def build_prompt(case: dict) -> str:
    return f"""
Usted es un asistente especializado en redacción de reportes policiales formales.

Genere un reporte policial claro, objetivo y estructurado usando únicamente la información proporcionada.

Reglas obligatorias:
- No invente nombres, direcciones, armas, placas, testigos, sospechosos ni detenciones.
- Si un dato no está disponible, indique "Información no proporcionada".
- Mantenga un lenguaje formal, neutral y policial.
- No agregue conclusiones no presentes en los datos.
- Respete fechas, horas, ubicaciones y tipo de incidente exactamente como aparecen.

Datos del caso:
{format_case_data(case)}

Estructura obligatoria del reporte:
1. Identificación del caso
2. Fecha y hora
3. Lugar
4. Tipo de incidente
5. Descripción de los hechos
6. Testigos
7. Lesionados
8. Información no proporcionada

Reporte:
""".strip()


def format_case_data(case: dict) -> str:
    lines = []

    for key, value in case.items():
        clean_key = key.replace("_", " ").title()

        if value == "" or value is None:
            value = "Información no proporcionada"

        lines.append(f"- {clean_key}: {value}")

    return "\n".join(lines)