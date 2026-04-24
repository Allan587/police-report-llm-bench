def build_police_report_prompt(case_data: dict) -> str:
    return f"""
Genera un reporte policial formal basado únicamente en la información proporcionada.

Reglas:
- No inventes datos.
- No agregues nombres, placas, armas, direcciones exactas ni sospechosos si no aparecen en los datos.
- Si falta información, indícalo en una sección llamada "Información no proporcionada".
- Mantén tono neutral, objetivo y administrativo.

Datos del caso:
{case_data}

Estructura requerida:
1. Fecha y hora
2. Lugar
3. Tipo de incidente
4. Descripción de los hechos
5. Testigos
6. Lesionados
7. Información no proporcionada
""".strip()