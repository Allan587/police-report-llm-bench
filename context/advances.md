# Avances y estado de implementación

> **Regla:** registrar únicamente implementación y pruebas observadas en el repositorio real.

**Fase activa:** ninguna; fase 1 cerrada y fase 2 no iniciada.
**Última actualización:** 2026-10-10 (fase 1 aceptada para el entorno local macOS).

| Fase | Estado | Entregable principal | Evidencia de pruebas |
|---|---|---|---|
| 1. Base técnica y navegador | DONE | `BrowserManager`, `SessionManager`, portabilidad, dependencias y documentación remota aceptados en macOS local | 17/17 pruebas pasan con Python 3.11.17 y Chromium; Linux, Windows y remoto `NOT_RUN` |
| 2. Orquestación y persistencia | TODO | Estados, SQLite, idempotencia, reanudación | Pendiente |
| 3. Conectores y generación | TODO | Adaptadores, matriz de permisos, generación/importación trazable | Pendiente |
| 4. Evaluación, feedback y perfiles | TODO | Rúbricas, feedback, aprobación y perfiles versionados | Pendiente |
| 5. Datasets y validación integral | TODO | Exportación, splits, E2E, guía operativa | Pendiente |

## Bitácora
### 2026-10-10 — Fase 1 — Revisión final de aceptación
- **Estado:** DONE para desarrollo local macOS. Linux, Windows y despliegue remoto permanecen `NOT_RUN` y no forman parte de esta aceptación local.
- **Archivos modificados durante la revisión:** `.gitignore`, `requirements.txt` (se retiró Playwright; el único cambio restante frente a Git es el salto de línea final), `automation/browser/manager.py`, `tests/test_browser_manager.py`, `docs/browser-foundation.md`, `context/environments.md`, `context/decisions.md`, `context/advances.md`.
- **Defectos bloqueantes corregidos:** se retiró la exclusión accidental de `src/review_data_loader.py`; se eliminó Playwright de `requirements.txt` para que inferencia y automatización queden realmente separadas; se serializó el ciclo de vida de cada `BrowserManager` con `asyncio.Lock` para evitar dos lanzamientos simultáneos desde el futuro orquestador; se añadió una prueba de regresión concurrente.
- **Revisión de pruebas:** las 17 pruebas ejercen comportamiento verificable. Siete unitarias de `BrowserManager` comprueban idempotencia, concurrencia, propagación de configuración, validación, errores, bloqueo y recuperación; el smoke abre Chromium real sobre `data:`. Seis unitarias de `SessionManager` comprueban fronteras por proveedor, cierre parcial, errores, ruta configurable/sincronizada y selección Linux; una prueba usa un subproceso real para disputar el bloqueo; dos integraciones abren Chromium real sobre `127.0.0.1` y verifican aislamiento/persistencia de `localStorage` y reapertura tras cierre externo. No se identificaron aserciones meramente triviales que sustituyan el comportamiento declarado.
- **Perfiles y concurrencia:** cada proveedor recibe un directorio persistente distinto; la prueba real conserva valores diferentes tras reiniciar Chromium. Las rutas conocidas de OneDrive, iCloud, Dropbox, Google Drive y `CloudStorage` se rechazan. El directorio predeterminado queda fuera del checkout sincronizado. Bloqueos de sistema operativo impiden compartir perfil entre procesos y el bloqueo async evita carreras dentro de una instancia.
- **Auditoría de `.gitignore`:** `AGENTS.md`, `automation/`, `tests/`, `config/feedback/`, `config/profiles/`, datasets versionables, `src/review_data_loader.py` y archivos de dependencias permanecen visibles. Se ignoran `.venv*`, `.pytest_cache/`, perfiles Chromium, estados de autenticación, `.env`, SQLite, modelos y salidas/datos locales ya definidos. No se detectó otra exclusión accidental bloqueante.
- **API pública:** `BrowserManager(profile_root, profile_name, *, headless, launch_timeout_ms)` expone `open`, `close`, `context`, `is_open` y context manager async. `SessionManager(profile_root, *, headless, launch_timeout_ms, browser_manager_factory)` expone `open`, `context`, `is_open`, `close`, `close_all`, `providers` y context manager async. La inyección de fábrica permite integración y pruebas sin acoplar el futuro orquestador a Playwright interno.
- **Dependencias:** `requirements.txt` vuelve a contener únicamente inferencia/datos; `requirements-automation.txt` contiene Playwright; `requirements-dev.txt` incorpora automatización, pytest y pytest-asyncio. `pip check` y la resolución offline con `--dry-run --no-index` pasan en `.venv311`.
- **Compatibilidad con el repositorio:** todos los módulos de `src/`, navegador y pruebas compilan con Python 3.11. No se modificó lógica en `src/`, `config/` ni `data/`; `data/.DS_Store` continúa como cambio previo del usuario. La infraestructura nueva vive en `automation/browser/` y no importa librerías de inferencia.
- **Pruebas:** `PYTHONPYCACHEPREFIX=/tmp/police-report-final-review-cache .venv311/bin/python -m pytest tests/test_browser_manager.py tests/test_session_manager.py -v -rs` → PASS, 14 aprobadas, 3 omitidas por las puertas opt-in, 0 fallidas. `PYTHONPYCACHEPREFIX=/tmp/police-report-final-review-cache RUN_BROWSER_SMOKE=1 RUN_BROWSER_SESSION_TESTS=1 .venv311/bin/python -m pytest tests/test_browser_manager.py tests/test_session_manager.py -v -rs` → PASS, 17 aprobadas, 0 omitidas, 0 fallidas en 5.39 s. `py_compile` sobre `src/*.py`, navegador y pruebas → PASS. `pip check` → PASS. `pip install --dry-run --no-index -r requirements-dev.txt` → PASS. Auditoría `git check-ignore` y `git diff --check` → PASS.
- **Documentación:** arquitectura, entornos, despliegue, seguridad, dependencias, decisiones D-005/D-006/D-007, comandos y resultados están documentados. Los registros históricos se conservaron.
- **Limitaciones pendientes:** backend de bloqueo Windows, ruta Linux/Windows y Chromium en esos sistemas: `NOT_RUN`; auditoría y pruebas remotas: `NOT_RUN`; automatización, permisos y autenticación de plataformas externas: fuera del alcance de fase 1 y `NOT_RUN`. Estas limitaciones no invalidan la aceptación local macOS y deben verificarse antes del despliegue.
- **Criterios de aceptación:** implementación, pruebas locales obligatorias, Chromium real, aislamiento, persistencia, concurrencia, errores, seguridad, documentación y ausencia de regresiones conocidas en macOS → PASS. Fase 1 marcada `DONE` únicamente para el entorno local validado.
- **Próximo paso:** ninguno automático. Esperar instrucción explícita antes de iniciar fase 2 o antes de auditar la máquina remota.
- **Commit/PR (si existe):** `UNCOMMITTED`.

### 2026-10-10 — Fase 1 — Validación final local y preparación remota
- **Estado:** READY_FOR_REVIEW
- **Archivos modificados:** `.gitignore`, `automation/browser/manager.py`, `automation/browser/sessions.py`, `tests/test_browser_manager.py`, `tests/test_session_manager.py`, `requirements-automation.txt`, `requirements-dev.txt`, `docs/browser-foundation.md`, `context/architecture.md`, `context/environments.md`, `context/deployment.md`, `context/decisions.md`, `context/advances.md`. `AGENTS.md` ya contenía las instrucciones de desarrollo local/ejecución remota y no requirió cambios.
- **Implementado:** Python 3.11 validado en `.venv311`; `headless` y `launch_timeout_ms` configurables y propagados a Playwright; raíz de perfiles configurable y predeterminada según macOS/Linux/Windows; bloqueo con backend `fcntl` en POSIX y `msvcrt` en Windows; exclusión de `.venv*` y `.pytest_cache`; dependencias de automatización y desarrollo separadas sin retirar Playwright de `requirements.txt`, preservando la configuración previa. Documentación local/remota actualizada sin asumir características del servidor.
- **Pruebas omitidas investigadas:** `pytest -rs` confirmó que `BrowserManagerSmokeTests::test_chromium_opens_navigates_locally_and_closes` se omite salvo `RUN_BROWSER_SMOKE=1`; las dos pruebas `SessionManagerBrowserIntegrationTests` se omiten salvo `RUN_BROWSER_SESSION_TESTS=1`. Son puertas de seguridad deliberadas para impedir lanzar Chromium en una corrida ordinaria; no se eliminaron.
- **Pruebas:** `PYTHONPYCACHEPREFIX=/tmp/police-report-py311-cache .venv311/bin/python -m pytest tests/test_browser_manager.py tests/test_session_manager.py -v -rs` → PASS, 13 aprobadas, 3 omitidas por las variables anteriores, 0 fallidas. `PYTHONPYCACHEPREFIX=/tmp/police-report-py311-cache RUN_BROWSER_SMOKE=1 RUN_BROWSER_SESSION_TESTS=1 .venv311/bin/python -m pytest tests/test_browser_manager.py tests/test_session_manager.py -v -rs` → PASS, 16 aprobadas, 0 omitidas, 0 fallidas; repetición final tras el ajuste portable de bloqueo: PASS en 4.92 s. Las pruebas reales usaron únicamente una URL `data:` y un servidor sintético en `127.0.0.1`.
- **Validaciones adicionales:** `.venv311/bin/python -m py_compile automation/__init__.py automation/browser/__init__.py automation/browser/manager.py automation/browser/sessions.py tests/helpers/profile_lock_holder.py tests/test_browser_manager.py tests/test_session_manager.py` → PASS. `.venv311/bin/python -m pip check` → PASS. `.venv311/bin/python -m pip install --dry-run --no-index -r requirements-dev.txt` → PASS, todas las dependencias ya satisfechas. `git diff --check` → PASS. `git check-ignore` → PASS para `.venv311/`, `.pytest_cache/`, `.browser_profiles/`, `playwright/.auth/` y SQLite.
- **Entorno:** macOS 27.0.1 (26A434), Apple Silicon; Python 3.11.17; Playwright 1.60.0; pytest 9.1.1; pytest-asyncio 1.4.0; Google Chrome for Testing 148.0.7778.96; rama `data/prepare-training-data`, HEAD `0938842`; cambios `UNCOMMITTED`.
- **Portabilidad:** macOS y Chromium fueron validados realmente. La selección de ruta Linux mediante `XDG_STATE_HOME`, los valores de `headless=False` y timeout personalizado se verificaron con pruebas unitarias. El backend Windows y la ejecución real en Linux/Windows están implementados pero `NOT_RUN`. No hay rutas absolutas del checkout ni dependencias de inferencia en los módulos de navegador.
- **Problemas:** la primera corrida tras añadir la prueba Linux falló solo por normalización macOS `/var` → `/private/var`; la expectativa se corrigió y las repeticiones pasaron. La ejecución final tardó inicialmente más de 30 segundos en devolver salida, pero finalizó correctamente. Cobertura: `NOT_MEASURED`.
- **Riesgos pendientes:** sistema operativo, arquitectura, Python, bibliotecas de Chromium, disponibilidad gráfica/headless, red, permisos, almacenamiento privado y coexistencia con inferencia en el servidor permanecen sin verificar. Toda validación remota es `NOT_RUN`. Las capacidades y permisos de ChatGPT, Gemini y Grok siguen pendientes; ninguna plataforma externa fue contactada.
- **Decisión:** D-007 formaliza desarrollo local, ejecución remota y separación de dependencias. D-005 y D-006 se actualizaron para rutas y bloqueo portables.
- **Criterios de aceptación:** pruebas locales obligatorias con Python 3.11, Chromium real, aislamiento, persistencia, recuperación, bloqueo multiproceso, configuración portable, dependencias separadas, documentación y seguridad → PASS. Revisión humana → pendiente. Validación remota → `NOT_RUN`. La fase no se marca `DONE`.
- **Próximo paso:** revisión humana de la fase 1 y, cuando exista acceso autorizado, auditoría remota de solo lectura seguida por la misma suite sintética. No iniciar fase 2 hasta aprobar formalmente la fase 1.
- **Commit/PR (si existe):** `UNCOMMITTED`.

### 2026-10-10 — Fase 1 — SessionManager, aislamiento y persistencia
- **Estado:** IN_PROGRESS
- **Archivos modificados:** `automation/browser/manager.py`, `automation/browser/__init__.py`, `automation/browser/sessions.py`, `tests/test_browser_manager.py`, `tests/test_session_manager.py`, `tests/helpers/profile_lock_holder.py`, `docs/browser-foundation.md`, `context/decisions.md`, `context/advances.md`.
- **Implementado:** `SessionManager` crea y reutiliza un `BrowserManager` independiente para `chatgpt`, `gemini` y `grok`; configura la raíz mediante `POLICE_BENCH_BROWSER_PROFILE_ROOT`; usa por defecto una ruta local en `~/Library/Application Support`; rechaza componentes conocidos de OneDrive, iCloud, Dropbox, Google Drive y `CloudStorage`; cierra todas las sesiones aunque una falle. `BrowserManager` ahora observa cierres externos del contexto, libera el bloqueo y permite reapertura segura. No se añadió navegación a plataformas, autenticación, envío, lectura ni extracción.
- **Componentes reutilizados:** `automation/browser/manager.py` conserva en exclusiva el lanzamiento de Chromium, contexto persistente, bloqueo de perfil y limpieza; `SessionManager` solo coordina proveedores y no duplica esas responsabilidades.
- **Pruebas:** `PYTHONPYCACHEPREFIX=/tmp/police-report-session-pycache .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v` → PASS, 10 pruebas pasan y 3 pruebas opt-in se omiten; incluye bloqueo real del mismo perfil entre dos procesos. `PYTHONPYCACHEPREFIX=/tmp/police-report-session-pycache RUN_BROWSER_SESSION_TESTS=1 .venv/bin/python -m unittest tests.test_session_manager.SessionManagerBrowserIntegrationTests -v` → PASS, 2 pruebas pasan usando Chromium y un servidor sintético exclusivo en `127.0.0.1`; verifica `localStorage` aislado para dos proveedores, persistencia tras reinicio y reapertura tras cierre externo. `PYTHONPYCACHEPREFIX=/tmp/police-report-session-pycache RUN_BROWSER_SMOKE=1 .venv/bin/python -m unittest tests.test_browser_manager.BrowserManagerSmokeTests -v` → PASS, 1 prueba pasa con URL `data:`. `py_compile` de los siete archivos Python del entregable → PASS. Parser `ast` con `feature_version=(3, 11)` sobre 16 archivos de `src/`, `automation/` y `tests/` → PASS. `git diff --check` → PASS.
- **Entorno:** macOS 27.0.1 (26A434), Apple Silicon; rama `data/prepare-training-data`, HEAD `0938842`; `.venv` con Python 3.9.6; Playwright 1.60.0; Google Chrome for Testing 148.0.7778.96.
- **Resultados:** 13 pruebas únicas aprobadas al ejecutar también las tres opt-in; 0 fallos en la validación final; cobertura `NOT_MEASURED`. Prueba real con Python 3.11: `NOT_RUN`, porque `command -v python3.11` confirmó que no está instalado. Resolución completa de `requirements.txt` para Python 3.11: `NOT_RUN`; un intento de `pip --dry-run` no pudo consultar el índice por la restricción de red del entorno y no demuestra incompatibilidad de paquetes.
- **Problemas:** durante el desarrollo, la prueba real detectó una carrera entre el evento `BrowserContext.close` y `Playwright.stop`; se corrigió difiriendo la detención del driver y la repetición final pasó. La `.venv` existente no se sustituyó ni se eliminó. Solo contiene Playwright y sus dependencias, no todas las dependencias declaradas. La detección de carpetas sincronizadas se basa en componentes conocidos de ruta; una ruta personalizada sigue requiriendo revisión humana. Los perfiles temporales de prueba se eliminaron al terminar. Los cambios previos del usuario y los datos existentes permanecen intactos.
- **Decisión:** D-006 documenta la frontera entre coordinación de sesiones y capacidades web; D-005 sigue rigiendo ubicación y bloqueo de perfiles.
- **Criterios de aceptación del entregable:** funcionalidad, aislamiento, persistencia, cierre inesperado, bloqueo multiproceso, documentación, datos sintéticos y ausencia de automatización externa → PASS. Ejecución bajo Python 3.11 → `NOT_RUN`; por ello el entregable no se marca `DONE` todavía.
- **Próximo paso:** disponer de un intérprete Python 3.11 sin reemplazar automáticamente `.venv`, crear un entorno temporal o aprobado, instalar dependencias y repetir la suite; después implementar diagnósticos de navegación como siguiente unidad de la fase 1.
- **Commit/PR (si existe):** `UNCOMMITTED`.

### 2026-10-10 — Fase 1 — Ciclo de vida de BrowserManager
- **Estado:** IN_PROGRESS
- **Archivos modificados:** `.gitignore`, `requirements.txt`, `automation/__init__.py`, `automation/browser/__init__.py`, `automation/browser/manager.py`, `tests/test_browser_manager.py`, `docs/browser-foundation.md`, `context/decisions.md`, `context/advances.md`.
- **Implementado:** auditoría del árbol, dependencias, pruebas, `.venv`, contratos de `config/feedback`, `config/profiles` y formatos de `data`; `BrowserManager` async con contexto persistente Chromium, apertura/cierre idempotentes, context manager, nombres de perfil validados, bloqueo exclusivo por perfil y limpieza tras fallos; dependencia Playwright declarada; exclusiones para perfiles, estados de autenticación y SQLite; guía de configuración segura. No se implementó login, navegación a servicios, envío ni extracción.
- **Componentes reutilizables:** `src/prompt_builder.py`, `src/report_generator.py`, `src/rubric_evaluator.py`, `src/run_evaluation.py`, `src/review_data_loader.py`, `config/models.yaml`, `config/rubric.yaml`, perfiles JSON e historiales JSONL. Son candidatos para fases posteriores, pero no se movieron ni adaptaron en esta unidad.
- **Contraste arquitectónico:** el repositorio real usa scripts en `src/` y generación local Hugging Face; no existían `automation/`, infraestructura web, tests ejecutables ni contratos tipados del pipeline propuesto. Los formatos actuales usan `statement_index`/`incident_index` y no contienen todavía todos los identificadores y datos de procedencia propuestos. Se añadió solo la capa mínima de navegador, sin duplicar generación o evaluación.
- **Pruebas:** `PYTHONPYCACHEPREFIX=/tmp/police-report-llm-bench-pycache .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v` → 3 pruebas unitarias pasan y 1 smoke opt-in se omite; `PYTHONPYCACHEPREFIX=/tmp/police-report-llm-bench-pycache RUN_BROWSER_SMOKE=1 .venv/bin/python -m unittest tests.test_browser_manager.BrowserManagerSmokeTests -v` → 1 prueba pasa fuera del sandbox de la app, abre Chromium 1.60.0, visita únicamente una URL `data:` sintética y cierra; `py_compile` de los cuatro archivos Python nuevos → pasa; `git check-ignore` confirma reglas para `.browser_profiles/`, `playwright/.auth/` y archivos SQLite.
- **Problemas:** `.venv` usa Python 3.9.6, pero módulos existentes con anotaciones `X | None` requieren Python 3.10+ al importarse; solo está disponible `/usr/bin/python3` 3.9.6 en el entorno inspeccionado. Playwright 1.60.0 y Chromium estaban instalados localmente, pero Playwright no estaba declarado. `pytest` no está instalado, por lo que se usó `unittest`. `src/review_data_loader.py` existe pero está ignorado por una modificación previa de `.gitignore`; no se alteró. Hay cambios previos del usuario (`.gitignore`, `.DS_Store`, `data/.DS_Store`, `AGENTS.md`, `context/` y un run no rastreado) que se preservaron. El smoke necesita salir del sandbox macOS para lanzar Chromium. La compatibilidad del bloqueo con Windows queda fuera del alcance macOS actual.
- **Próximo paso:** implementar y probar aislamiento real entre dos perfiles persistentes, sin red, como siguiente unidad verificable de la fase 1; después abordar `SessionManager` y diagnósticos.
- **Commit/PR (si existe):** no existe.

### 2026-10-10 — Inicialización del contexto
- **Cambio:** definidos objetivo, arquitectura, decisiones, avances y cinco prompts de fase.
- **Validación:** paquete documental creado; **no** demuestra funcionamiento del sistema.
- **Pendiente:** auditar proyecto y comenzar fase 1.

## Formato obligatorio de nuevas entradas
### AAAA-MM-DD — Fase N — Descripción
- **Estado:** TODO / IN_PROGRESS / BLOCKED / DONE
- **Archivos modificados:** rutas exactas
- **Implementado:** hechos verificables, no aspiraciones
- **Pruebas:** comandos, resultado y entorno
- **Problemas:** fallos, restricciones, deuda técnica
- **Próximo paso:** unidad concreta
- **Commit/PR (si existe):** identificador

## Puerta para declarar una fase DONE
- [ ] Criterios de aceptación de `context/prompts/phase-0N-*.md` satisfechos.
- [ ] Código y/o documentos comprometidos/identificados.
- [ ] Pruebas relevantes ejecutadas, resultado anotado.
- [ ] Sin secretos ni datos sensibles incluidos.
- [ ] `context/advances.md` y `context/decisions.md` actualizados.
- [ ] Revisión humana de riesgos y restricciones donde aplique.

## Registro de evidencias y validación

### Propósito

Este registro permite mantener la trazabilidad técnica de cada entregable implementado por Codex.

Toda funcionalidad debe contar con evidencia verificable antes de marcarse como `DONE`.

### 1. Estados permitidos

| Estado | Significado |
|---|---|
| `TODO` | Entregable pendiente de implementación. |
| `IN_PROGRESS` | Desarrollo iniciado, sin completar todos los criterios de aceptación. |
| `BLOCKED` | Existe un impedimento técnico o una dependencia sin resolver. |
| `READY_FOR_REVIEW` | Implementación terminada, pendiente de revisión. |
| `DONE` | Implementación verificada y criterios de aceptación satisfechos. |

### 2. Plantilla de registro por entregable

Cada entregable deberá documentarse utilizando el siguiente formato:

#### Entregable: [Nombre del componente]

**Información general**

- Fase: [1–5]
- Estado: `TODO`
- Fecha: [YYYY-MM-DD]
- Responsable: Codex
- Rama Git: [nombre de la rama]
- Commit: [SHA o `UNCOMMITTED`]

**Entorno de ejecución**

- Sistema operativo: [versión]
- Python: [versión]
- Playwright: [versión]
- Navegador: [versión de Chromium, si aplica]

**Implementación**

- Objetivo: [descripción]
- Archivos creados: [rutas]
- Archivos modificados: [rutas]
- Componentes reutilizados: [rutas]
- Decisiones técnicas: [descripción o referencia a decisions.md]

**Evidencia de validación**

| Prueba | Comando | Resultado |
|---|---|---|
| Pruebas unitarias | [comando exacto] | PASS / FAIL / NOT_RUN |
| Pruebas de integración | [comando exacto] | PASS / FAIL / NOT_RUN |
| Smoke test | [comando exacto] | PASS / FAIL / NOT_RUN |
| Validación sintáctica | [comando exacto] | PASS / FAIL / NOT_RUN |
| Git diff check | `git diff --check` | PASS / FAIL / NOT_RUN |

**Resultados**

- Pruebas aprobadas: [cantidad]
- Pruebas fallidas: [cantidad]
- Pruebas omitidas: [cantidad]
- Cobertura: [porcentaje medido o `NOT_MEASURED`]
- Evidencia adicional: [logs o archivos]

**Problemas y riesgos**

- Problemas detectados: [descripción]
- Riesgos pendientes: [descripción]
- Dependencias bloqueantes: [descripción]

**Criterios de aceptación**

- [ ] Funcionalidad implementada.
- [ ] Pruebas requeridas ejecutadas satisfactoriamente.
- [ ] Casos de error relevantes verificados.
- [ ] Compatibilidad con la arquitectura existente comprobada.
- [ ] Documentación actualizada.
- [ ] Sin modificaciones destructivas de datos existentes.
- [ ] Sin problemas bloqueantes pendientes.

**Siguiente acción**

[Descripción precisa del siguiente entregable verificable.]

---

### 3. Reglas obligatorias para Codex

1. Antes de comenzar una tarea, leer el estado más reciente de `context/advances.md`.
2. Verificar que los avances registrados coincidan con el código y Git.
3. No sobrescribir ni eliminar evidencias históricas.
4. Actualizar el registro después de implementar y ejecutar las pruebas.
5. Registrar los comandos exactos utilizados para validar cada entregable.
6. No inventar resultados, cobertura, versiones ni identificadores de commits.
7. Si no se ejecutó una prueba, registrar `NOT_RUN` y explicar el motivo.
8. Si una prueba falla, documentar el error y su impacto.
9. No establecer `DONE` cuando existan criterios obligatorios incumplidos.
10. No avanzar a la siguiente fase sin satisfacer los criterios de aceptación definidos para la fase actual.
11. Mantener referencias a las decisiones arquitectónicas relevantes.
12. Diferenciar claramente entre implementación terminada, pruebas aprobadas y funcionalidad validada.

### 4. Condiciones para finalizar una fase

Una fase podrá marcarse como `DONE` únicamente cuando:

- Todos sus entregables obligatorios estén en estado `DONE`.
- Las pruebas requeridas para esa fase hayan sido ejecutadas y aprobadas.
- No existan riesgos bloqueantes pendientes.
- Se haya verificado la integración con los componentes existentes.
- `context/advances.md` refleje el estado real del repositorio.
- Las decisiones técnicas relevantes estén documentadas en `context/decisions.md`.

El cierre de una fase no implica que todo el proyecto esté terminado.

### 5. Estado actual

**Fase 1 — Infraestructura Playwright**

Estado: `DONE` para el entorno local macOS.

Entregables locales: `BrowserManager`, `SessionManager`, aislamiento,
persistencia, recuperación, bloqueo multiproceso, portabilidad de configuración,
separación de dependencias y documentación remota.

Evidencia actual:

- 17 de 17 pruebas aprobadas con Python 3.11.17 y Chromium.
- 0 pruebas omitidas al habilitar explícitamente los smoke tests.
- Compilación, dependencias, exclusiones de seguridad y `git diff --check`: PASS.
- Validación remota: `NOT_RUN`.

**Pendientes:**

- Auditoría de solo lectura del servidor cuando exista acceso autorizado.
- Validación sintética en el sistema operativo remoto.
- No iniciar fase 2 sin una instrucción explícita del usuario.
