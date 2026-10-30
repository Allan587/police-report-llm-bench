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

## Desarrollo local y ejecución remota

El proyecto usa una arquitectura de desarrollo y validación inicial en macOS, seguida de ejecución definitiva en la máquina remota.

- Todas las implementaciones y pruebas iniciales del flujo Playwright se realizan en la Mac local con Python 3.11 y el entorno dedicado de automatización.
- La validación local debe cubrir, con datos sintéticos o fixtures aislados cuando corresponda: selección del `statement_index`; recopilación y alineación de la declaración, los outputs de Gemma/Mistral/Llama y sus perfiles e historiales; preparación de `main_review.md`; identificación y validación independiente de ChatGPT/Gemini/Grok; recuperación completa del revisor con resultados incompletos; barreras de adjudicación; y persistencia sin alterar datos existentes.
- Las pruebas de preparación, validación y recuperación deben poder ejecutarse sin plataformas externas. Las interacciones web reales requieren autorización expresa y deben respetar las capacidades permitidas por cada plataforma.
- La máquina remota es el entorno final de ejecución de Playwright, Chromium, agentes y pipeline, pero solo se configura después de que el flujo supere la validación local aplicable.
- El despliegue remoto requiere una auditoría propia de sistema operativo, Python, Chromium, ejecución headless, red, permisos, almacenamiento y autenticación. Un resultado local no prueba compatibilidad ni despliegue remoto.
- Git transfiere únicamente cambios de código y configuración revisados. Perfiles de navegador, cookies, secretos, credenciales y estado de ejecución permanecen fuera de Git; el estado de producción permanece en la máquina remota.
- La lógica debe ser portable y configurable: no se permiten rutas macOS hardcodeadas ni dependencias locales de los entornos de inferencia remotos.

## Persistencia del flujo de revisión y adjudicación

- La fuente de verdad del flujo son los archivos JSON y JSONL reales del proyecto. No usar SQLite, SQL ni otra base de datos para estados, intentos o resultados de este flujo.
- Reconstruir el estado inspeccionando y validando los archivos existentes: caso por `statement_index`, revisiones permanentes, temporales de historial y perfil, adjudicaciones y destinos aplicados en `config/feedback/` y `config/profiles/`.
- Toda escritura futura debe ser validada, segura y atómica; debe prevenir duplicados, conservar resultados válidos y permitir reanudar después de fallos.
- Si una revisión está incompleta, reemplazar como unidad indivisible el conjunto completo del revisor afectado únicamente después de validar sus siete salidas nuevas. No mezclar intentos ni alterar los conjuntos válidos de otros revisores.
- Distinguir la completitud de una tarea individual de la completitud global del statement. Solo avanzar al siguiente índice cuando revisión, tres adjudicaciones, aplicación verificada y limpieza segura estén completas.
- La construcción o filtrado de datasets y el entrenamiento o fine-tuning quedan fuera del flujo activo de revisión y adjudicación. El nombre heredado de `data/finetuning/review_cases/` identifica una ruta real, no una etapa ni una dependencia de entrenamiento.
- No introducir una base de datos para este flujo salvo una decisión futura, explícita y documentada que sustituya esta instrucción.

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

Todo entregable relevante debe validarse primero localmente y, cuando corresponda al despliegue, volver a validarse en la máquina remota.

Registra en `context/advances.md` evidencia real usando `LOCAL_TESTED`, `LOCAL_PENDING`, `REMOTE_NOT_CONFIGURED` o `REMOTE_TESTED`. Una prueba no ejecutada se registra como `NOT_RUN`; no conviertas evidencia local en evidencia remota.

No configures accesos, credenciales, sesiones ni servicios remotos sin autorización explícita. No marques el despliegue remoto como completado hasta que las pruebas necesarias se hayan ejecutado satisfactoriamente en esa máquina.

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

## Context Recovery Protocol

At the beginning of every new Codex session:

1. Read `context/goal.md` to understand the project objective.
2. Read `context/architecture.md` and `context/decisions.md` to understand the technical architecture.
3. Read `context/environments.md` and `context/deployment.md` to understand local development and remote execution.
4. Read `context/advances.md` to identify the current phase and the next pending deliverable.
5. Read the corresponding `context/prompts/phase-XX-*.md` file for the active phase.
6. Inspect `git status`, the current branch and relevant source files before implementing changes.
7. Verify that documented progress matches the actual code and tests.
8. Implement only the next authorized deliverable.
9. Update `context/advances.md` after completing and testing the work.

Do not assume that a previous conversation remains available.

Do not repeat completed work or overwrite uncommitted user changes.

If the active phase is not specified, determine it from `context/advances.md` and report the proposed next deliverable before implementation.

Never treat documentation as proof that code works; verify it against the repository.
