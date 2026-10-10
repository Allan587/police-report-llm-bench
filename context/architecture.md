# Arquitectura técnica propuesta

## Árbol objetivo (no describe estado actual)
```text
police-report-llm-bench/
├── AGENTS.md
├── context/
│   ├── README.md
│   ├── goal.md
│   ├── architecture.md
│   ├── decisions.md
│   ├── advances.md
│   └── prompts/phase-01...phase-05.md
├── automation/
│   ├── __init__.py
│   ├── orchestrator.py
│   ├── pipeline.py
│   ├── state_machine.py
│   ├── browser/
│   │   ├── manager.py
│   │   ├── sessions.py
│   │   └── adapters/
│   │       ├── base_adapter.py
│   │       ├── chatgpt_adapter.py
│   │       ├── gemini_adapter.py
│   │       └── grok_adapter.py
│   ├── agents/
│   │   ├── generator.py
│   │   ├── evaluator.py
│   │   ├── feedback.py
│   │   └── profile_optimizer.py
│   ├── evaluation/
│   │   ├── deterministic.py
│   │   ├── rubric.py
│   │   └── quality_gate.py
│   ├── storage/
│   │   ├── database.py
│   │   ├── repository.py
│   │   └── jsonl_exporter.py
│   └── datasets/
│       ├── builder.py
│       └── splitter.py
├── config/            # conservar y adaptar lo existente
├── data/              # conservar y adaptar lo existente
└── tests/
```
Añadir `__init__.py` donde sean necesarios. Reusar componentes equivalentes si ya existen: no crear duplicados innecesarios.

## Entornos de ejecución

- **Desarrollo local:** macOS, Python 3.11, pruebas unitarias y páginas sintéticas de Playwright. Las dependencias de inferencia no son requisito para validar la capa de navegador.
- **Ejecución final:** máquina remota todavía no auditada. Playwright, Chromium, perfiles persistentes y procesos de automatización se ejecutarán allí, junto a los procesos de inferencia existentes pero en un entorno Python separado.
- Git transfiere únicamente código y configuración versionable. Entornos virtuales, perfiles, cookies, secretos, bases activas y estado de navegador permanecen fuera de Git.
- No se presupone sistema operativo, escritorio gráfico, versión de Python, dependencias del sistema ni compatibilidad headless del servidor. Esas propiedades deben verificarse antes del despliegue.

## Flujo de datos
`CaseInput → GenerationJob → ModelResult → EvaluationResult → FeedbackRecord → ProfileProposal → (aprobación) ProfileVersion → DatasetExample`.

Campos mínimos sugeridos para un resultado: `run_id`, `job_id`, `case_id`, `provider`, `model_label`, `prompt_version`, `profile_version`, `iteration`, `status`, `created_at`, `completed_at`, `response_text`, `response_source`, `error_code`, `provenance`. No asumir que la UI revela una versión exacta del modelo. Los tipos finales y migraciones dependen de la auditoría inicial.

## Estados
`PENDING → RUNNING → GENERATED → EVALUATED → FEEDBACK_READY → REVIEW_PENDING → COMPLETED`; estados laterales `FAILED`, `BLOCKED`, `SKIPPED`. Un resultado aprobado puede iniciar una nueva iteración con nueva versión de perfil. Las transiciones se validan y persisten de forma transaccional.

## Restricciones técnicas
- Async Playwright, `headless=False` para diagnóstico/autenticación manual inicial; perfiles aislados por proveedor y nunca versionados.
- `headless` y timeout de lanzamiento configurables. La raíz de perfiles se obtiene de `POLICE_BENCH_BROWSER_PROFILE_ROOT` o de un directorio de estado local apropiado para el sistema operativo.
- SQLite (WAL donde proceda) para ejecuciones, eventos y estados; JSONL como exportación compatible, nunca como única fuente de verdad de concurrencia.
- Tareas idempotentes: clave compuesta por caso, proveedor, versión de prompt, versión de perfil, iteración y número explícito de repetición experimental.
- Capabilities de adaptador: `open_site`, `check_session`, `prepare_prompt`, `submit_prompt`, `read_response`, `export_result`. Cada capacidad puede ser `ALLOWED`, `MANUAL` o `DISABLED` según revisión verificable.
- Separar los tests: unitarios con mocks, integración de BD/locales, smoke web interactivo sujeto a permisos.
- Conservar metadatos de cambios, razones de fallos, tiempos y evidencias sanitizadas.
- Nunca desarrollar tácticas anti-detección, suplantación para evadir políticas, rotación de identidades, CAPTCHA-bypass o scraping restringido.

## Interfaces de módulos
- `BrowserManager`: abre/cierra contextos y garantiza exclusividad del perfil de cada servicio.
- `BaseWebAdapter`: capacidades, sesión, navegación y comportamiento autorizado. No decide la rúbrica.
- `Orchestrator`: despacho, secuenciación, timeouts, backoff admisible, reanudación, persistencia.
- `Generator`: construye prompts versionados; invoca adaptador conforme a capacidades; acepta importaciones manuales trazables.
- `Evaluator`: verificaciones deterministas y rúbricas auditables; no asume que un juez LLM es verdad absoluta.
- `Feedback`: produce observaciones respaldadas por evidencia.
- `ProfileOptimizer`: **propone** nuevas versiones y solicita aprobación; no reescribe perfiles activos en caliente.
- `DatasetBuilder`: aplica exclusión de PII, normalización, deduplicación y particionado por caso de origen.

## Seguridad y calidad
`.gitignore` debe incluir `.browser_profiles/`, `.env`, `*.sqlite`, artefactos de sesión y trazas sensibles, sin ignorar indebidamente datos que el proyecto ya versiona. Priorizar datos sintéticos. El acceso web requiere verificación de condiciones por servicio; no inferir autorización por posibilidad técnica.
