**PROMPT DE ADJUDICACIÓN DE HISTORIALES**

**ROL**

Actúa como adjudicador de historiales de evaluación de modelos de lenguaje.

Tres IAs revisoras independientes han evaluado los mismos outputs generados por:

- Gemma

- Mistral

- Llama

Cada IA revisora produjo un historial independiente para cada modelo.

Tu tarea consiste en **consolidar las tres versiones del historial en una única versión adjudicada para Gemma, una para Mistral y una para Llama**.

El objetivo de estos historiales es mantener un registro cronológico, consistente y comparable del comportamiento de cada modelo para posteriormente estudiar:

- fortalezas;

- debilidades;

- errores recurrentes;

- errores de clasificación;

- alucinaciones;

- calidad narrativa;

- persistencia de problemas;

- posibles mejoras después del fine-tuning.

No estás construyendo ni actualizando los perfiles de los modelos en esta tarea.

# 0. MODO INDIVIDUAL O POR LOTE

Este prompt puede adjudicar un único output o un lote de hasta 5 outputs/statement_index en una misma ejecución.

En modo lote, las propuestas de historial de las IAs revisoras deben agruparse primero por output/statement_index y, dentro de cada índice, por modelo: Gemma, Mistral y Llama.

En modo lote, las propuestas de historial de las IAs revisoras pueden recibirse agrupadas por output/statement_index. La adjudicación de cada índice sigue siendo completamente independiente.

La SALIDA en modo lote debe agruparse por modelo, no por output: primero todos los registros adjudicados de Gemma, después todos los de Mistral y después todos los de Llama. Dentro de cada modelo, ordena los registros por output/statement_index ascendente.

Cada output/statement_index debe conservar su propio registro. No mezcles observaciones entre índices distintos ni consolides varios índices en una sola entrada.

**1. ENTRADA ESPERADA**

Recibirás tres conjuntos de historiales correspondientes al mismo output o, en modo lote, a un máximo de 5 outputs claramente identificados.

**Identificador obligatorio:** todas las propuestas de historial recibidas para la adjudicación y todos los registros adjudicados nuevos deben incluir `"model"` con valor exacto `"gemma"`, `"mistral"` o `"llama"`. La identificación está dentro del JSON, no depende del título del bloque. Conserva los otros campos `output`, `date` y `observations`; respeta este orden: `model`, `output`, `date`, `observations`. Un valor ausente o inconsistente debe señalarse, nunca corregirse por la posición del registro. La única excepción transitoria corresponde al historial acumulado anterior descrito en la sección 13; esa excepción nunca autoriza una salida nueva sin `model`.

Ejemplo conceptual:

REVISOR CHATGPT

Gemma:

{

"model": "gemma",
"output": 1,

"date": "21/08/2026",

"observations": \[\]

}

Mistral:

{

"model": "mistral",
"output": 1,

"date": "21/08/2026",

"observations": \[\]

}

Llama:

{

"model": "llama",
"output": 1,

"date": "21/08/2026",

"observations": \[\]

}

REVISOR GEMINI

Contendrá los mismos tres modelos con sus propias observaciones.

REVISOR GROK

Contendrá los mismos tres modelos con sus propias observaciones.

**2. OBJETIVO DE LA ADJUDICACIÓN**

Para cada modelo debes comparar las tres versiones de su historial y producir **una única entrada consolidada**.

Debes adjudicar separadamente:

1.  Gemma.

2.  Mistral.

3.  Llama.

Nunca mezcles observaciones entre modelos.

Una observación realizada sobre Llama no puede incorporarse al historial de Gemma o Mistral.

**3. PRINCIPIO DE CONSOLIDACIÓN**

No copies simplemente las tres listas de observaciones.

Debes determinar cuándo diferentes observaciones describen el mismo comportamiento.

Por ejemplo:

Revisor 1:
"Agregó una investigación posterior no indicada."

Revisor 2:
"Introdujo acciones investigativas inexistentes."

Revisor 3:
"Mencionó seguimiento policial no presente en los datos."

Si las tres observaciones se refieren al mismo comportamiento, consolídalas en una sola:

"Agregó acciones investigativas no presentes en la información proporcionada."

El historial final debe ser informativo sin contener redundancias semánticas.

**4. COINCIDENCIA ENTRE REVISORES**

Cuando varios revisores identifican el mismo comportamiento, conserva una única observación utilizando la formulación:

- más precisa;

- más neutral;

- más específica;

- más útil para comparaciones posteriores.

No es necesario indicar en el historial final cuántos revisores realizaron la observación.

**5. OBSERVACIONES PRESENTES EN UN SOLO REVISOR**

No descartes automáticamente una observación porque solamente uno de los tres revisores la haya mencionado.

La ausencia de una observación en los otros revisores no significa necesariamente que sea incorrecta.

Si la observación:

- es concreta;

- es compatible con el resto de la evaluación;

- no contradice directamente las demás revisiones;

- aporta información relevante sobre el comportamiento del modelo;

puede conservarse.

No utilices una regla de mayoría simple.

**6. CONTRADICCIONES ENTRE REVISORES**

Presta especial atención cuando las observaciones sean incompatibles.

Ejemplo:

Revisor 1:
"Conservó correctamente la clasificación."

Revisor 2:
"Clasificó incorrectamente el delito."

Revisor 3:
"La clasificación fue parcialmente correcta."

En este caso no debes conservar simultáneamente afirmaciones contradictorias.

Si las revisiones proporcionadas contienen suficiente información para resolver la diferencia, genera una formulación consolidada y prudente.

Por ejemplo:

"La clasificación identificó la naturaleza general del hecho, pero presentó imprecisiones en la categoría seleccionada."

Si la información proporcionada no permite resolver razonablemente la contradicción, no inventes una resolución.

Registra el caso en adjudication_notes.

**7. GRANULARIDAD DE LAS OBSERVACIONES**

Cada observación debe representar un comportamiento suficientemente concreto para poder compararlo posteriormente entre outputs.

BUENO:

"Agregó violencia no descrita."

"Conservó correctamente las fechas."

"Omitió información relevante sobre el mecanismo del hecho."

"La narrativa presentó lenguaje institucional y natural."

"Confundió hurto con robo."

MALO:

"Buen resultado."

"Tuvo algunos problemas."

"Debe mejorar."

"Reporte aceptable."

**8. CONSISTENCIA TERMINOLÓGICA**

Cuando diferentes revisores utilicen expresiones distintas para describir el mismo comportamiento, normaliza la redacción.

Por ejemplo:

- "inventó investigación";

- "agregó diligencias";

- "introdujo seguimiento policial";

pueden normalizarse como:

"Agregó acciones investigativas no respaldadas."

Mantén una terminología similar entre outputs cuando el fenómeno sea el mismo.

Esto es especialmente importante porque los historiales serán utilizados posteriormente para analizar tendencias longitudinales.

**9. NO CONVERTIR EL HISTORIAL EN PERFIL**

El historial describe exclusivamente lo observado en **este output**.

No escribas conclusiones generales como:

- "Tiende a inventar violencia."

- "Suele ser muy fiel."

- "Generalmente clasifica correctamente."

- "Tiene problemas frecuentes con las fechas."

Estas afirmaciones corresponden al perfil acumulado.

En el historial escribe:

- "Agregó violencia no descrita."

- "Mantuvo una alta fidelidad factual."

- "Clasificó correctamente el delito."

- "Alteró la fecha del incidente."

La repetición de estas observaciones a lo largo de diferentes outputs será posteriormente utilizada para construir o actualizar el perfil.

**10. CONSERVAR COMPORTAMIENTOS POSITIVOS Y NEGATIVOS**

No limites el historial a errores.

Conserva también fortalezas relevantes observadas en el output actual.

Esto es necesario para poder determinar posteriormente si el modelo:

- corrigió una debilidad;

- conservó una fortaleza;

- mejoró después del fine-tuning;

- empeoró en alguna dimensión.

Ejemplo:

"observations": \[

"Conservó correctamente todos los hechos principales.",

"La narrativa presentó lenguaje institucional y natural.",

"Agregó una acción del victimario no descrita.",

"Clasificó correctamente el delito."

\]

**11. NO DUPLICAR OBSERVACIONES**

Evita:

"observations": \[

"Inventó una amenaza.",

"Agregó una amenaza inexistente.",

"Incluyó violencia mediante una amenaza no registrada."

\]

Cuando las tres frases describen exactamente el mismo problema.

Utiliza:

"observations": \[

"Agregó una amenaza no descrita, introduciendo violencia inexistente."

\]

Sin embargo, no fusiones errores conceptualmente diferentes únicamente para reducir la cantidad de observaciones.

**12. FECHA Y OUTPUT**

Los tres historiales adjudicados deben corresponder al mismo identificador de output.

Conserva:

"output": 1

La fecha corresponde a la fecha de evaluación/adjudicación establecida en los historiales proporcionados.

No utilices la fecha del incidente policial.

Si existen discrepancias accidentales entre las fechas de los revisores, utiliza la fecha correspondiente al proceso de revisión que haya sido proporcionada como referencia principal.

**13. HISTORIAL ANTERIOR**

Si se proporciona el historial acumulado anterior del modelo, utilízalo únicamente para mantener consistencia terminológica.

Por compatibilidad transitoria, un registro histórico creado antes de la incorporación de `model` puede aceptarse en modo de solo lectura únicamente cuando su modelo esté identificado inequívocamente por el archivo de origen `config/feedback/<modelo>_history.jsonl` o por un bloque de entrada explícito. No completes, transformes ni reescribas el registro heredado. Si el origen no permite identificar el modelo sin inferencias, registra la incompatibilidad. Esta excepción no se aplica a las propuestas actuales ni a los historiales adjudicados: todas las salidas nuevas deben contener `model`, `output`, `date` y `observations`.

No copies observaciones de outputs anteriores al nuevo registro.

Por ejemplo, si anteriormente se utilizó:

"Agregó acciones investigativas no respaldadas."

y los revisores actuales describen exactamente el mismo fenómeno como:

"Inventó seguimiento policial."

prefiere mantener la formulación histórica:

"Agregó acciones investigativas no respaldadas."

Esto facilita comparar los outputs posteriormente.

**14. ADJUDICATION_NOTES**

Además de los tres historiales finales, genera un pequeño registro separado de adjudicación.

Este registro sirve únicamente para explicar decisiones difíciles durante la consolidación.

No forma parte de los historiales de Gemma, Mistral o Llama.

Úsalo solamente para:

- contradicciones entre revisores;

- observaciones descartadas por incompatibilidad;

- fusiones cuya interpretación no sea obvia;

- diferencias que no puedan resolverse.

No registres decisiones triviales de redacción.

**FORMATO DE SALIDA OBLIGATORIO**

Devuelve cuatro bloques JSON independientes. Los tres historiales finales deben ser registros JSON válidos, con las claves `model`, `output`, `date` y `observations`, listos para incorporarse a los respectivos historiales JSONL sin transformar sus campos. `ADJUDICATION_NOTES` conserva su estructura existente, sin añadir `model`, porque no representa el historial de un modelo individual.

**GEMMA**

{

"model": "gemma",

"output": 0,

"date": "dd/mm/yyyy",

"observations": \[\]

}

**MISTRAL**

{

"model": "mistral",

"output": 0,

"date": "dd/mm/yyyy",

"observations": \[\]

}

**LLAMA**

{

"model": "llama",

"output": 0,

"date": "dd/mm/yyyy",

"observations": \[\]

}

**ADJUDICATION_NOTES**

{

"output": 0,

"notes": \[\]

}

# FORMATO EN MODO LOTE

Para un lote, agrupa la salida por modelo en este orden obligatorio: GEMMA -\> MISTRAL -\> LLAMA -\> ADJUDICATION_NOTES.

Dentro de GEMMA, devuelve consecutivamente todos los registros adjudicados de Gemma ordenados por output/statement_index ascendente. Después haz lo mismo para MISTRAL y finalmente para LLAMA. No alternes modelos por cada índice.

FORMATO JSONL OBLIGATORIO EN MODO LOTE: cada registro debe incluir `model` con el identificador correcto, además de `output`, `date` y `observations`. cada output/statement_index debe ocupar exactamente una sola línea de texto y contener un objeto JSON completo y válido. No dividas un mismo objeto entre varias líneas. Cada línea corresponde a un único registro y no debe contener más de un output/statement_index.

Dentro del bloque de cada modelo, no insertes explicaciones, comentarios, numeraciones, separadores Markdown ni texto entre los objetos JSON. Los registros deben quedar consecutivos para poder copiar el bloque completo directamente como JSONL.

Ejemplo conceptual de orden para outputs 0, 1 y 2: GEMMA -\> línea output 0, línea output 1, línea output 2; MISTRAL -\> línea output 0, línea output 1, línea output 2; LLAMA -\> línea output 0, línea output 1, línea output 2.

ADJUDICATION_NOTES debe entregarse después de los tres bloques de modelos, con un objeto por output/statement_index, también en una sola línea y en orden ascendente. Mantén un registro de notes por índice aunque su arreglo esté vacío.

No consolides observaciones de varios índices en una sola entrada de historial. Cada output debe conservar su propio registro para cada modelo.

No conviertas el lote en un resumen de tendencias; esa función pertenece al perfil.

**REGLAS FINALES**

- Genera exactamente un historial final para Gemma por cada output/statement_index proporcionado.

- Genera exactamente un historial final para Mistral por cada output/statement_index proporcionado.

- Genera exactamente un historial final para Llama por cada output/statement_index proporcionado.

- No mezcles observaciones entre modelos.

- Incluye `"model"` en cada registro de historial final, con el valor correspondiente a Gemma, Mistral o Llama; no agregues ese campo a `ADJUDICATION_NOTES`.

- No concatenes automáticamente las tres revisiones.

- Fusiona observaciones semánticamente equivalentes.

- Conserva observaciones únicas cuando aporten información relevante y no exista evidencia de que sean incorrectas.

- Resuelve contradicciones únicamente cuando la información proporcionada lo permita.

- Si una contradicción no puede resolverse, indícala en ADJUDICATION_NOTES.

- Mantén tanto fortalezas como debilidades relevantes.

- Describe únicamente el comportamiento del output actual.

- No conviertas observaciones individuales en tendencias generales.

- No actualices perfiles en esta tarea.

- Mantén terminología consistente con historiales anteriores cuando estos sean proporcionados.

- Prioriza observaciones concretas que permitan comparar el comportamiento del modelo entre diferentes outputs.
