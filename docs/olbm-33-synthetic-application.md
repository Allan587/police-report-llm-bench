# OLBM-33 — Aplicación sintética incremental

Fecha: 2026-10-30. Estado de entrega: `READY_FOR_QA`; QA independiente pendiente. Fase 2: `IN_PROGRESS`.

## Alcance y decisiones de integración

La autorización de esta unidad es la solicitud de OLBM-33, que declara QA PASS
para OLBM-31/32. La bitácora anterior conservaba `READY_FOR_QA`; no se reescriben
sus entradas ni se presenta esta implementación como una auditoría independiente.

Los seis destinos del contrato real son los perfiles y los historiales de
**Gemma, Mistral y Llama** (`BenchmarkModel`). ChatGPT, Gemini y Grok son los
revisores, cuyas identidades permanecen en la procedencia. Las menciones del
ticket a perfiles/historiales de los revisores no introducen seis archivos nuevos
ni cambian el significado de `config/profiles/` y `config/feedback/`.

La entrada nueva, `run_synthetic_application_once(SyntheticApplicationConfig)`,
recibe un índice y el `application_id` ya confirmado. Exige un directorio dedicado
bajo el temporal del sistema, confirmación explícita y confinamiento de todas
las rutas del workflow y del lease. Rechaza solapamiento con el repositorio.
No admite adaptadores, no ejecuta revisores/adjudicadores y no conduce un rango.
Todos los datos usados por las pruebas son sintéticos y temporales.

## Archivos de esta unidad

| Archivo | Cambio |
|---|---|
| `automation/review/synthetic_application.py` | Nueva entrada sintética mono-statement, confinamiento y barreras |
| `automation/review/adjudication_storage.py` | Reanudación desde commit y lectura de cadenas sucesoras |
| `automation/review/finalization.py` | Verificación de la misma evidencia v2 y limpieza |
| `automation/review/completeness.py` | Cotejo de evidencia con aplicación y destinos cuando existe almacén interno |
| `automation/orchestrator.py` | Suministrar la ruta de evidencia al inspector existente |
| `tests/test_synthetic_application.py` | 65 pruebas permanentes nuevas |
| `tests/helpers/application_recovery_process.py` | Hijo independiente para cortes y reanudación |
| `docs/olbm-33-synthetic-application.md` | Informe de entrega y checklist independiente |
| `context/architecture.md` | Integración y límites reales |
| `context/decisions.md` | D-037 |
| `context/advances.md` | Comandos, resultados e integridad operacional |

Los demás cambios del worktree preceden esta unidad y se conservaron.

## Inspección de las interfaces existentes

1. `apply_adjudicated_results()` valida un bundle `1 + 3 + 3`, comprueba
   revisiones, prepara siete objetivos y reemplaza destinos individualmente.
   Conserva su API histórica. La entrada nueva continúa la aplicación creada por
   `commit_adjudication_bundle()` mediante `resume_adjudication_application()`;
   no vuelve a preparar ni calcula otra identidad.
2. Las preimágenes son los `source_sha256` del manifiesto esquema 2, incluidos
   en el hash que identifica la aplicación. **No hay copias completas de todas
   las preimágenes**. Los siete objetivos completos permanecen en `prepared/`.
3. `finalize_adjudication_application()` usa `_evidence()` para generar la
   evidencia v2 existente con índices, run, intentos, hashes y snapshots de
   perfiles. `verify_application_evidence()` reutiliza exactamente esa definición
   y confirma el manifiesto/estado de limpieza; no define otro esquema.
4. La identidad y los hashes detectan aplicaciones anteriores; el inspector
   verifica material preparado, manifiesto y destinos. El replay exige la misma
   identidad, evidencia v2 exacta y completitud reconstruida, y devuelve
   `UNCHANGED` sin llamar al escritor/finalizador.
5. La garantía multiarchivo es recuperación por preimagen u objetivo exactos,
   con reemplazo atómico de cada archivo y verificación posterior. No es una
   transacción de seis archivos. Cualquier tercer contenido bloquea.
6. Se reutilizan `validate_profile_adjudication_transition()`,
   `validate_profile_transition()`, `validate_history()` y los validadores del
   inspector. El escritor no inventa ni calcula contadores: aplica el candidato
   que pasó la validación de OLBM-31, y revalida los perfiles aún pendientes contra
   sus preimágenes actuales. Si un perfil ya fue reemplazado, se verifica su hash
   objetivo; no se reconstruye una preimagen perdida.
7. `inspect_statement_completeness()` sigue exigiendo inputs, tres revisores,
   adjudicación, tres historiales, tres perfiles y evidencia íntegra. Si existe
   almacenamiento interno, también coteja identidad, material de aplicación,
   destinos y limpieza contra esa evidencia; conserva la lectura heredada sin
   ese almacenamiento. La entrada nueva exige además la barrera de run OLBM-36. `APPLIED` sin
   evidencia final no devuelve una aplicación globalmente completa.

## Protocolo de aplicación de un statement

La unidad es `start=end=statement_actual`; no se recibe un rango exterior.

Bajo el mismo `WorkflowLease`, O-02 reconstruye O-01 y su huella. Se inspecciona
la aplicación solicitada, se exige el hash final de `adjudication.json`, se leen
inputs completos y se comparan run, statement y los tres intentos activos.
Se reutiliza `_validate_active_review_run()`, la barrera pública corregida en
OLBM-36. Una preparación sin commit no autoriza esta aplicación.

Antes del primer efecto en destinos se verifican los siete objetivos y sus
preimágenes. Los historiales preparados deben conservar el acumulado anterior,
tener índices únicos y no futuros, y terminar en el índice actual. La lectura
compatible de registros históricos sin `model` no los modifica. Los perfiles
pendientes vuelven a pasar la validación semántica mono-statement de OLBM-31.
El candidato 010 con contadores/última observación 999 se rechaza sin efectos.

Después se repiten plan, huella y lease inmediatamente antes de llamar al
finalizador existente. Este reanuda únicamente destinos pendientes, verifica
`APPLIED`, confirma limpieza explícita vacía (no se eliminan sets de revisores)
y publica la evidencia v2. La entrada nueva verifica esa evidencia y vuelve a
inspeccionar completitud antes de devolver `APPLIED` con `completion=COMPLETE`.

No se pasa al siguiente índice, no se regeneran inputs y no se recalculan perfiles.

## Puntos reales de confirmación y cortes

| Frontera | Confirmación persistida | Recuperación |
|---|---|---|
| Antes de aplicar | Adjudicación y bundle OLBM-31 ya confirmados | Validar todo; cero cambios en los seis destinos |
| Cada historial/perfil | `os.replace()` individual, seguido de SHA-256 | Omitir objetivos exactos; escribir solo preimágenes exactas |
| Seis destinos escritos, estado aún `APPLYING` | Inspección `INTERRUPTED` | Revalidar y confirmar `APPLIED`; todavía no hay completitud |
| `status.json=APPLIED` | Siete destinos coinciden con objetivos | Finalizar limpieza/evidencia pendientes |
| Limpieza confirmada | `cleanup_status.json=CLEANED` | Conservar manifiestos y publicar evidencia pendiente |
| Evidencia v2 publicada | `os.replace(application_evidence.json)` y verificación | Replay de solo lectura; completitud verificable incluso si muere el proceso antes de retornar |

El hook nuevo `after_application_commit` observa la confirmación de `APPLIED`;
no añade otro estado. Se reutilizan los hooks `after_replace`, `after_recovery`,
`after_cleanup_complete` y `after_evidence`. El hijo termina con `os._exit(53)`;
el siguiente hijo reconstruye configuración y estado desde archivos.

No se revierte una operación parcial. Un destino cambiado, preparado corrupto,
identidad contradictoria, adjudicación eliminada o evidencia inválida bloquea
la continuación y conserva los bytes que encontró. La ausencia de preimágenes
completas impide reparar arbitrariamente un tercer contenido; se requiere
resolución del operador fuera de esta unidad.

## Acumulación y replay tardío

El inspector incorpora una ruta **de solo lectura**, activada al suministrar
`application_evidence_path`, para validar aplicaciones antiguas ya finalizadas.
Cada sucesor debe ser único, contiguo y verificable, incluso si está parcialmente
aplicado. Sus seis hashes de preimagen deben coincidir con los seis objetivos
del predecesor. Su historial añade exactamente un registro, y sus perfiles pasan
la misma transición semántica mono-statement contra los preparados anteriores.
El último eslabón se verifica contra los destinos actuales. Las cadenas legadas
cuyos sets carecen por completo de metadatos v2 mantienen su contrato previo de
lectura no regresiva; no habilitan la entrada de aplicación nueva, que exige
siempre runs v2 verificables. Las cadenas nuevas usan la puerta semántica estricta.
Cada evidencia
finalizada presente debe coincidir exactamente con la definición v2 existente.

Esta conexión es necesaria en O-01: comparar siempre el acumulado global con el
objetivo antiguo bloquearía 011 y la recuperación de sus escrituras parciales.
O-01 solo recibe la ruta de evidencia; no cambia de selección ni conduce rangos.
`resume_adjudication_application()` conserva su comparación estricta contra los
destinos actuales y **no** usa esta ruta de sucesores para escribir.

El fixture empieza con historiales 000–009 y perfiles de diez outputs, versión 1,
un patrón establecido con una evidencia y última observación 009:

| Aplicación unitaria | Índices en cada historial | outputs_reviewed | versión | evidencia del patrón | última observación |
|---|---|---:|---:|---:|---:|
| 010 | 000–010, una vez cada uno | 11 | 2 | 2 | 10 |
| 011 | 000–011, una vez cada uno | 12 | 3 | 3 | 11 |
| 012 | 000–012, una vez cada uno | 13 | 4 | 4 | 12 |
| Replay 011 desde un hijo nuevo | Sin cambios | 13 | 4 | 4 | 12 |

Los statements anteriores conservan sus bytes y evidencia, siguen `COMPLETE`, y
el replay no vuelve a sumar ni incrementa timestamps de destinos confirmados.
Las pruebas también interrumpen 011 y 012 después del primer historial, durante
perfiles, después de los seis destinos y tras confirmar `APPLIED`.

## Validación local y límites

Corrida final consolidada: **331 PASS, 0 FAIL, 0 SKIP en 64.75 s**, Python
3.11.17/macOS 27.0.1 arm64. Todos los grupos de la tabla se ejecutaron juntos:

| Grupo permanente | Resultado |
|---|---:|
| `test_synthetic_application.py` | 65 PASS |
| `test_synthetic_adjudication_flow.py` | 25 PASS |
| `test_adjudication_process_recovery.py` | 11 PASS |
| `test_adjudication_run_identity.py` | 8 PASS |
| `test_adjudication_storage.py` | 63 PASS |
| `test_review_finalization.py` | 34 PASS |
| `test_statement_completeness.py` | 37 PASS |
| `test_orchestrator_planner.py` | 28 PASS |
| `test_workflow_coordinator.py` | 36 PASS |
| `test_workflow_lease.py` | 24 PASS |

Compilación de los siete Python afectados, whitespace incluyendo no rastreados
y `git diff --check`: PASS. Huella SHA-256 del mapa ordenado
`ruta relativa → SHA-256(contenido)` de 85 archivos operacionales, antes/después:
`e90b83b4446a2dd4c60dbb197f6a3966d9371fbdeee1a3813119ebd7347f696a`.
Suite completa y cobertura: `NOT_RUN` / `NOT_MEASURED`.
Los comandos exactos están en la entrada OLBM-33 de `context/advances.md`. Ninguna prueba usa servicios, modelos, navegador, SQL ni
remoto. Los fingerprints de los 85 archivos operacionales se comparan antes y
después; la prueba permanente comprueba también esas tres raíces reales.

La primera corrida de desarrollo detectó una aceptación incorrecta de un perfil
con versión aumentada sin sucesor real (41 PASS, 1 FAIL). La solución final
verifica aplicaciones sucesoras y sus preimágenes, en lugar de aceptar solo
no regresión de los perfiles. Una corrida ampliada posterior detectó dos incompatibilidades adicionales:
la confirmación inmediata de O-02 retorna `ACTION_REQUIRED` aun cuando el plan
observado bloquea solo por falta del siguiente input, y las cadenas legadas
tenían que conservar su contrato previo de lectura (318 PASS, 13 FAIL). Se
corrigieron la comparación de estado y la compatibilidad de lectura sin relajar
la barrera v2 de la entrada nueva. La corrida final de 331 pruebas pasa después de esas correcciones; no quedan
regresiones conocidas en el alcance ejecutado.

Permanecen para OLBM-34 los límites inclusivos configurables, su selección y
huella en O-01/O-02 y el driver que avance automáticamente. El flujo vertical
histórico de revisores sigue limitado a 010; los fixtures de 011/012 se publican
explícitamente con el escritor existente. La inspección encadena sucesores por
lectura; no se certifican rendimiento ni profundidad de recorridos largos.

El lease es cooperativo; los hashes no autentican frente a una reescritura
coordinada de todo el árbol, ni las guardas eliminan todas las carreras TOCTOU.
Se prueban caídas de procesos, no pérdidas de energía del equipo. Linux, Windows,
plataformas reales y remoto permanecen `NOT_RUN`/`REMOTE_NOT_CONFIGURED`.

## Checklist para QA independiente — pendiente de ejecución por QA

- [ ] Reproducir las pruebas nuevas y las regresiones focalizadas indicadas en avances.
- [ ] Confirmar seis destinos reales de `BenchmarkModel`, bundle e identidad existentes.
- [ ] Rechazar inputs/revisores ausentes, preparación sin commit, run e intentos ajenos.
- [ ] Rechazar 999, preimágenes contradictorias y registros duplicados/futuros antes de aplicar.
- [ ] Verificar que solo cambian seis destinos, estado y evidencia/limpieza previstos.
- [ ] Reproducir los diez cortes de 010 con hijos independientes; verificar bytes y mtime.
- [ ] Reproducir los ocho cortes acumulados de 011/012; preservar predecesores completos.
- [ ] Corromper preparado, destino, adjudicación y evidencia tras una aplicación parcial; exigir bloqueo sin más escrituras.
- [ ] Confirmar evidencia v2 exacta, identidad y limpieza; no aceptar seis archivos como `COMPLETE` sin evidencia.
- [ ] Reproducir 010→011→012 y replay tardío 011 sin duplicar ni restaurar perfiles antiguos.
- [ ] Corromper el estado final o una aplicación antigua después de acumular; rechazar el replay.
- [ ] Confirmar contención entre procesos y liberación del lease al terminar el holder.
- [ ] Verificar protección 000–009, compatibilidad histórica sin model y huella operacional intacta.
- [ ] Confirmar ausencia de otro almacén, lock, esquema de evidencia, notes, driver de rango, servicios, commits y pushes.
- [ ] Emitir conclusión independiente antes de marcar OLBM-33 `DONE`; mantener fase 2 `IN_PROGRESS`.
