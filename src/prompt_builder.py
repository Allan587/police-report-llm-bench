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

''' 
Adapt build_finetuning_gereration_prompt to the current workflow

def build_finetuning_generation_prompt(
    incident: Dict[str, Any]
) -> str:

    incident_data = format_incident_data(
        incident,
        include_status=False,
    )

    return f"""
DATOS DEL INCIDENTE:
{incident_data}

TAREA:

Redacta únicamente la narrativa y el cierre del reporte policial.

REQUISITOS:
- Redacta en español.
- Mantén fidelidad total a los datos.
- No inventes personas, armas ni hechos.
- Conserva valores técnicos como "Not specified".
- No hagas recomendaciones.
- No agregues acciones futuras.
- No hagas conclusiones legales definitivas.
""".strip()
'''