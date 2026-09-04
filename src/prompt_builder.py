from typing import Dict, Any


def build_statement_generation_prompt(statement_record: dict) -> str:
    statement = statement_record.get("statement", {})

    if isinstance(statement, dict):
        report = statement.get("report", "")
    else:
        report = str(statement)

    return f"""
Eres un redactor de reportes policiales.

OBJETIVO:
A partir de la declaración proporcionada, redacta un reporte policial institucional y realiza una clasificación orientativa del delito.

REGLAS:
- Redacta únicamente en español.
- Usa solo la información contenida en la declaración.
- No inventes fechas, lugares, armas, personas, testigos, lesiones ni vehículos.
- No agregues acciones futuras, recomendaciones ni instrucciones operativas.
- No afirmes una clasificación jurídica definitiva.
- La clasificación del delito debe ser orientativa y basada únicamente en la declaración.
- Si la declaración no permite determinar algo, indícalo de forma prudente.
- No agregues información que no esté explícita o razonablemente derivada de la declaración.

DECLARACIÓN:
{report}

RESPONDE ÚNICAMENTE CON ESTAS SECCIONES:

Narrativa:
Redacta uno o dos párrafos breves con estilo institucional.

Cierre:
Redacta un cierre breve, formal y neutral.

Clasificación del delito:
Indica la clasificación orientativa del delito y una justificación breve.
""".strip()