# Instrucciones persistentes para Codex — police-report-llm-bench

## Contexto obligatorio al iniciar cada tarea
1. Lee `context/goal.md`, `context/architecture.md`, `context/decisions.md` y `context/advances.md`.
2. Identifica la fase activa en `context/advances.md` y consulta SOLO el prompt correspondiente en `context/prompts/`.
3. Inspecciona el código real y `git status` antes de modificar archivos. No asumas que una propuesta documental ya está implementada.
4. Si hay contradicciones entre el repositorio y el diseño propuesto, señala el conflicto y documenta la decisión; evita sobrescribir trabajo existente.

## Normas de ejecución
- Trabaja una fase y un entregable verificable por vez. No marques una fase completa sin evidencias y pruebas.
- Preserva `config/feedback`, `config/profiles`, `data` y formatos existentes salvo migración documentada y probada.
- Usa Python + Playwright para infraestructura de navegador y tareas web expresamente autorizadas; no implementes evasión de CAPTCHA, restricciones anti-bot, límites, ni extracción donde las condiciones la prohíban.
- El uso de Playwright no implica que todos los conectores puedan automatizar el envío o la extracción; documenta por plataforma las operaciones permitidas. Mantén alternativas manuales asistidas cuando corresponda.
- No subas secretos, tokens, cookies, estados de autenticación, datos policiales sensibles ni perfiles de navegador a Git.
- Antes de ejecutar pruebas externas, usa datos sintéticos y evita llamadas reales a servicios sin autorización explícita.
- Separa `browser/adapters` (UI) de `agents` (lógica del pipeline). Prioriza automatización determinista antes que agentes autónomos.
- Mantén contratos tipados, idempotencia, logs sin datos sensibles, trazabilidad y validación reproducible.
- Al terminar cada unidad: ejecuta pruebas pertinentes, resume archivos modificados, anota comandos/resultados y actualiza `context/advances.md`.
- Registra cambios arquitectónicos en `context/decisions.md` con fecha, motivación y consecuencias.
- Si faltan datos esenciales, avanza en tareas seguras independientes y registra el bloqueo; no inventes detalles de negocio.

## Local Development and Remote Execution

The project uses a local-development and remote-execution architecture.

- macOS is the development and testing environment.
- The remote machine is the final execution environment for Playwright, Chromium, agents and the automation pipeline.
- Git is used to transfer reviewed source-code changes.
- Production browser profiles, credentials and runtime state remain on the remote machine.
- Local development must not require installing remote inference dependencies.

Before modifying browser infrastructure, orchestration, persistence or deployment configuration, read:

1. `context/goal.md`
2. `context/architecture.md`
3. `context/environments.md`
4. `context/deployment.md`
5. `context/decisions.md`
6. `context/advances.md`
7. The prompt for the active phase.

Code must remain portable across the supported operating systems.

Do not assume that the remote machine has a graphical desktop, compatible Chromium installation, matching Python version or existing authentication sessions.

Do not modify remote environments or production services without explicit authorization.

Every relevant deliverable must be validated locally first and later validated on the remote environment.

Update `context/advances.md` with actual test evidence. Clearly distinguish local validation from remote validation.

Do not mark remote deployment as completed until the necessary tests have actually passed on the remote machine.

## Control de avances y evidencias

Codex debe utilizar `context/advances.md` como registro persistente del progreso del proyecto.

Antes de implementar cualquier entregable, debe leer este archivo y verificar el estado real del repositorio.

Después de cada implementación debe registrar las modificaciones, pruebas ejecutadas, resultados, problemas pendientes y siguiente acción.

Está prohibido marcar un entregable o fase como `DONE` sin evidencia verificable que satisfaga sus criterios de aceptación.

Los resultados no ejecutados deben indicarse explícitamente como `NOT_RUN`.

Los registros anteriores deben conservarse para mantener la trazabilidad histórica.

No avanzar automáticamente a la siguiente fase sin verificar los criterios de cierre de la fase actual.

## Criterio de aceptación general
Una tarea solo está `DONE` si existe implementación en el repositorio, evidencia de ejecución de pruebas, documentación actualizada y ausencia de regresiones conocidas. Usa estados `TODO`, `IN_PROGRESS`, `BLOCKED` y `DONE`.
