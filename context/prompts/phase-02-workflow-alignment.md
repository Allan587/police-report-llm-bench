# Codex — Alinear fase 2 con el flujo real

Lee `AGENTS.md`, `context/goal.md`, `context/architecture.md`, `context/decisions.md`, `context/advances.md`, `context/workflow-review-adjudication.md` y los cuatro prompts en `config/prompts/`.

## Alcance de esta tarea

1. Ejecutar `git status` y preservar los cambios del usuario.
2. Auditar los esquemas reales y rutas de `data/statements/statements.jsonl`, `data/generated/runs/`, `data/finetuning/review_cases/`, `config/profiles/`, `config/feedback/` y `config/prompts/`. No inventar contenido faltante.
3. Verificar que el entregable 2.1 (`Task`, `ExecutionAttempt`, `TaskError`, `TaskResult` y la máquina de estados) puede representar tareas por `statement_index`, revisión principal por revisor y adjudicaciones de reporte, perfil e historial; documentar discrepancias antes de cambiar contratos.
4. Documentar la selección secuencial del statement: revisar el mayor índice existente, retomarlo si está incompleto y avanzar a `+1` si está completo y las entradas existen.
5. Convertir al repositorio los cuatro DOCX originales a `config/prompts/*.md` preservando sus instrucciones. Comparar el resultado con los originales, sobre todo los bloques JSON y JSONL. No modificar sus reglas de adjudicación en esta tarea.
6. Respetar los archivos de revisión principal permanentes `chatgpt_review.json`, `gemini_review.json` y `grok_review.json`. Separar los temporales de historial y perfil por revisor. No eliminarlos mientras sean necesarios para el rango de adjudicación.
7. Actualizar `context/architecture.md`, `context/decisions.md` y `context/advances.md` con referencias al flujo real, sin declarar pruebas o acciones que no se ejecutaron.
8. Proponer un siguiente entregable mínimo de persistencia y recuperación sobre los archivos JSON/JSONL reales, con pruebas locales sintéticas. No implementar por anticipado automatizaciones externas.

Los archivos JSON/JSONL son la fuente de verdad exclusiva para estados, intentos y resultados del workflow. El estado debe reconstruirse mediante inspección y validación de archivos, con futuras escrituras atómicas, prevención de duplicados y recuperación reproducible. No planificar ni implementar SQLite, SQL u otra base de datos para este flujo.

## Entorno obligatorio para la fase 2

Toda implementación y prueba inicial de la fase 2, incluida la infraestructura Playwright necesaria, debe realizarse en la Mac local con Python 3.11 y el entorno dedicado de automatización.

The current objective is to verify that the system can correctly:

1. Identify the current incomplete statement.
2. Collect the corresponding declaration and the three benchmark outputs.
3. Collect the accumulated profiles and histories.
4. Prepare the complete reviewer input using `main_review.md`.
5. Manage and validate reviewer results.
6. Detect incomplete reviewer executions.
7. Repeat the complete review for an affected reviewer when required.
8. Prevent adjudication until all three reviewers have supplied complete results.

Las pruebas deterministas deben usar datos sintéticos, directorios temporales o copias controladas y ejecutarse separadamente de las interacciones web. No sobrescribir revisiones, perfiles, historiales ni datos existentes.

No implementar acceso, autenticación, perfiles, credenciales ni infraestructura remota durante este entregable. Su configuración corresponde a una etapa posterior, una vez que el flujo aplicable esté `LOCAL_TESTED`.

Keep the implementation portable and configurable for future remote deployment.

Registrar evidencia local y requisitos remotos pendientes en `context/advances.md` usando `LOCAL_TESTED`, `LOCAL_PENDING`, `REMOTE_NOT_CONFIGURED` o `REMOTE_TESTED`. No interpretar pruebas locales como validación remota.

Do not advance to the next deliverable without satisfying its acceptance criteria.

## Reglas

- **Un caso por ejecución de revisión principal.** El modo lote conservado en los prompts no es la política operativa actual.
- La adjudicación de perfiles puede necesitar un rango definido por el operador: no inventar un rango fijo ni aplicar perfiles incompletos.
- En todos los procesos se preserva `statement_index` y se verifica alineación de inputs.
- Solo marcar completo un statement cuando revisión, las tres adjudicaciones y las actualizaciones estén efectivamente aplicadas y verificadas.
- La construcción o filtrado de datasets y el entrenamiento o fine-tuning están fuera del alcance y no son etapas ni dependencias de este workflow. La ruta heredada `data/finetuning/review_cases/` conserva su nombre sin cambiar ese alcance.
- No hacer commits, no alterar datos existentes, no eliminar temporales reales, no editar la máquina remota.
- No conectarse a ChatGPT, Gemini o Grok durante la auditoría.

Al finalizar: entregar rutas y esquemas verificados, incompatibilidades reales, archivos modificados, validaciones, bloqueos y próximos pasos. Mantener fase 2 `IN_PROGRESS`.
