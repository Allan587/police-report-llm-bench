# OLBM-55 — entorno local portable de revisión

## Arquitectura auditada

`src/data_loader.py` lee `data/raw/Crime_Data.csv` y escribe incidentes limpios;
`src/report_generator.py` lee `data/statements/statements.jsonl` y genera runs
locales de Gemma/Mistral/Llama en `data/generated/runs/`. Ambos scripts tienen
rutas derivadas del checkout y la generación exige dependencias de inferencia.
No existe un generador general de statements ni de casos de revisión separado
del flujo sintético. El formato aceptado para OLBM-55 empieza **después** de
esa generación: `statements.jsonl`, `runs/<run_id>/metadata.json`, tres
`draft_reports.jsonl`, historiales y perfiles ya preparados. Un formato crudo
distinto falla; este adaptador no inventa statements ni ejecuta modelos.

`OrchestratorConfig` ya recibe rutas explícitas. `inspect_review_inputs`
comprueba statement, run y outputs por índice; `statement_selector` reconoce
`review_cases/statement_XXX`. `InputAdapter` reutiliza el inspector, descubre
todos los índices aportados desde 010, calcula SHA-256 por archivo, copia los
inputs a un directorio aislado mediante staging y crea solo el primer directorio
de caso. O-01 avanza a los siguientes índices después del cierre verificable.
Los índices activos deben ser contiguos desde 010. Un manifiesto liga índice, run y huella del
input. Repetir la preparación con el mismo input deja la salida intacta;
alterar el input o reutilizar la salida con otro dataset se rechaza. Los
escritores posteriores siguen siendo los existentes de revisión y
adjudicación. El directorio fuente no se modifica.

`BrowserSessionFactory` crea el `BrowserManager` existente bajo
`profile_root/<operador>/<revisor>`, con Chromium, modo headless, locale y
timeout configurados. El factory entrega el manager al llamador, que debe
cerrarlo. `doctor` usa un perfil temporal y solo una página `data:` local.
No se conecta a ningún proveedor externo. Los perfiles reales permanecen
en el equipo y bajo control del operador.

## Instalación y configuración

Usar Python 3.11 y un entorno local independiente de los modelos:

```sh
python3.11 -m venv .venv-automation
.venv-automation/bin/python -m pip install -r requirements-dev.txt
.venv-automation/bin/python -m playwright install chromium
cp config/portable.example.json config/portable.local.json
```

En Windows, usar el ejecutable Python del entorno en lugar de `.venv-automation/bin/python`.
Editar solo `config/portable.local.json`. Las rutas relativas se interpretan
desde ese archivo; se admiten rutas absolutas externas si `allowed_roots`
las incluye. `inputs.root` debe contener exactamente el layout auditado:

```text
statements.jsonl
runs/<run_id>/metadata.json (opcional; si existe, run_id debe coincidir)
runs/<run_id>/{gemma_2b,mistral,llama_3_2}/generation/draft_reports.jsonl
feedback/{gemma,mistral,llama}_history.jsonl
profiles/{gemma,mistral,llama}_profile.json
prompts/review_adjudication.md
```

El prompt de adjudicación se necesita para continuar el flujo sintético; debe
ser el prompt versionado apropiado para el operador, sin contenido sensible.
Cada statement activo debe tener un único run completo. Los índices 000–009
siguen protegidos por el flujo existente. La configuración exige esquema 1,
Chromium y Playwright 1.60.0; otros motores no se declaran compatibles.
Si existe `review_cases/` en la fuente, solo admite directorios históricos
vacíos `statement_000` a `statement_009`; datos de casos activos preexistentes
requieren otro proceso de migración y no se mezclan automáticamente.
`profile_root` debe estar fuera de Git y de carpetas sincronizadas. Una ruta
externa legítima de dataset puede incluirse en `allowed_roots`; symlinks,
traversal fuera de esas raíces y superposición entrada/salida se rechazan.
`local-inputs/`, `local-outputs/` y `config/portable.local.json` están ignorados.

```sh
.venv-automation/bin/python -m automation.portable doctor --config config/portable.local.json
.venv-automation/bin/python -m automation.portable prepare --config config/portable.local.json
```

`doctor` verifica Python, versión de Playwright, binario de Chromium, esquema,
inputs, permisos del padre de salida y apertura/cierre del navegador con la
página local. Si falta algo, devuelve `FAIL` con instrucción o categoría de
error. `prepare` devuelve número de casos, huella fuente y ruta de salida.
Ningún comando abre ChatGPT, Gemini o Grok. `doctor` comprueba permisos en el
ancestro existente más cercano de la salida antes de que `prepare` cree carpetas.

## Continuación del flujo sintético

La salida contiene los cinco caminos de `OrchestratorConfig`: statements,
runs, review_cases, feedback y profiles. Usar ese config con
`WorkflowCoordinatorConfig` y `VerticalReviewConfig` bajo una raíz sintética
temporal permite ejecutar el vertical y la adjudicación existentes. La CLI
manual de OLBM-53/54 mantiene su confinamiento al temporal del sistema:
`--root` apunta a la carpeta que contiene `operational/` e `inbox/`.
`prepare` de OLBM-55 prepara datos y directorios; no produce respuestas de
revisor ni un bundle de adjudicación. La demo automatizada usa los adaptadores
deterministas existentes y no acredita proveedores reales.

## Reproducción A/B y límites

Crear dos carpetas fuente sintéticas distintas con el layout anterior y dos
copias de la configuración, cada una con `inputs.root`, `allowed_roots`,
`outputs.root` y `browser.profile_root` propios. Ejecutar `doctor` y `prepare`
para cada una. Las huellas fuente deben diferir, los manifiestos apuntar a los
índices/runs correctos y los outputs estar separados. Repetir `prepare` debe
conservar los bytes ya preparados; cambiar un input debe devolver error. La
prueba `tests/test_portable_environment.py` reproduce este patrón sin editar
código fuente y continúa ambas copias hasta adjudicación y aplicación sintéticas.

No se validaron Linux, Windows ni la máquina remota. No se transfieren perfiles,
cookies o estados autenticados. La autorización de interacción con proveedores
reales sigue pendiente por plataforma; los fakes no la otorgan. La generación
de nuevos statements desde CSV, la inferencia y el entrenamiento están fuera
de este adaptador. La copia de un dataset grande requiere espacio equivalente
en el destino. El staging no es protección contra un actor local hostil que
modifique archivos durante la copia.

## Checklist funcional posterior

- Confirmar Python 3.11, Playwright 1.60.0 y Chromium en cada computadora.
- Ejecutar `doctor` y `prepare` con dos datasets sintéticos distintos.
- Comprobar identidad de run, statement, outputs, perfiles e historiales.
- Ejecutar 1/3, 2/3 y 3/3 revisiones sintéticas y la adjudicación existente.
- Verificar replay, recuperación e integridad de datos fuente.
- Auditar por separado sistema remoto y permisos de proveedores antes de usarlos.
