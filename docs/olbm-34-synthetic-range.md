# OLBM-34 — E2E sintético de rangos secuenciales

Fecha: 2026-10-30. Estado: `READY_FOR_QA`; QA independiente
pendiente. Fase 2 `IN_PROGRESS`; OLBM-35 reservado para especificación de
adaptadores reales.

## Alcance y autorización

La solicitud de OLBM-34 declara QA PASS para OLBM-31/32/33/37 y autoriza esta
integración. La bitácora anterior aún indicaba QA pendiente de OLBM-33/37. Se
conserva íntegra y se registra esta diferencia en D-039, sin atribuir al autor
de la implementación una auditoría independiente.

Se utilizan exclusivamente fixtures temporales sintéticos. No hubo ejecución
operacional, modelos reales, navegadores, plataformas, SQL, servicios remotos,
commits ni pushes. Las modificaciones preexistentes del worktree se preservan.

## Archivos de la unidad

| Archivo | Cambio |
|---|---|
| `automation/orchestrator.py` | Límites opcionales inclusivos; selección ordenada dentro del rango; límites en `StatementPlan` |
| `automation/workflow_coordinator.py` | Ambos límites encuadrados en la huella de precondiciones existente |
| `automation/review/vertical_flow.py` | Permitir otros índices únicamente con límites explícitos; conservar entrada anterior para 010 |
| `automation/review/synthetic_adjudication.py` | Expectativa opcional O-02 para la entrada de persistencia |
| `automation/review/synthetic_application.py` | Expectativa opcional, guarda del índice y extracción de la validación temporal compartida |
| `automation/review/synthetic_range.py` | Driver que compone los ejecutores existentes; resultados y pasos solo en memoria |
| `tests/test_synthetic_range.py` | Escenarios A–G, límites inválidos, corrupción, contención e integridad operacional |
| `tests/helpers/range_recovery_process.py` | Hijo independiente que reconstruye configuración y permite cortes con `os._exit(63)` |
| `docs/olbm-34-synthetic-range.md` | Entrega, evidencia y checklist de QA |
| `context/architecture.md` | Integración y compatibilidad |
| `context/decisions.md` | D-039 |
| `context/advances.md` | Evidencia, comandos, resultados y siguiente acción |

No se modifican escritores, contratos de resultados, esquemas de persistencia,
inspector de completitud ni corrección QA33-01.

## Interfaces reutilizadas y ciclo real

1. `coordinate_workflow_cycle()` adquiere el `WorkflowLease` existente,
   reconstruye dos veces `plan_next_statement()` y devuelve la expectativa
   protegida. El driver no crea otro coordinador ni mantiene un contador en disco.
2. O-01 recorre cada índice numérico de `[start_statement, end_statement]`,
   incluidos los que todavía no tienen directorio. Usa
   `inspect_statement_completeness()` y se detiene en el primer estado distinto
   de `COMPLETE`. Solo para ese índice llama `resume_plan()`, que inspecciona
   aplicaciones pendientes, inputs y revisores. Una recuperación de un índice
   posterior no desplaza al menor incompleto. Ambigüedades de nombres de
   directorio bloquean en su índice.
3. `inspect_review_inputs()` sigue siendo la fuente autoritativa para declaración
   y outputs de Gemma/Llama/Mistral. No se usa `src/review_data_loader.py`.
   Los faltantes conservan códigos, modelos, rutas y dependencias en el plan.
   No se crean placeholders ni se generan inputs.
4. `run_synthetic_reviewer_once()` ejecuta un único revisor pendiente. Reutiliza
   `CompleteReviewResult`, `publish_complete_review()`, manifiestos v2 y
   `resolve_recoverable_unconfirmed_attempt()`. Reinspecciona sus siete artefactos
   antes de devolver éxito. Los revisores completos se recuperan sin ejecutarlos.
5. Con 3/3 revisores completos, se llama
   `run_synthetic_adjudication_and_persist_once()`. El bundle existente `1+3+3`
   se valida con statement, run y tareas/intentos activos; se descartan notes y
   se confirma un único `adjudication.json`. Una preparación recuperable sin
   commit usa esta misma entrada; el adjudicador sintético puede volver a
   producir el bundle preparado, que el escritor coteja con la identidad previa.
6. Con adjudicación confirmada, una aplicación parcial o finalización pendiente,
   se llama `run_synthetic_application_once()` con el índice y `application_id`
   inspeccionados. La unidad sigue siendo **`start=end=statement_actual`**.
   Aplica/recupera solo tres historiales y tres perfiles de Gemma/Mistral/Llama,
   omitiendo los objetivos ya confirmados. Reutiliza finalizador, evidencia v2 y
   limpieza existentes.
7. Después de cada etapa se reconstruye el plan desde disco. No se incrementa
   un cursor. Solo `COMPLETE` permite que O-01 seleccione el siguiente índice.
   `ADJUDICATION_COMMITTED` y `APPLIED` sin evidencia/limpieza siguen pendientes.
   Con todo el rango completo se devuelve `SyntheticRangeStatus.COMPLETE` y no
   se inspecciona como candidato `end+1`.

El ciclo de coordinación inicial libera el lease antes de retornar. Cada
ejecutor vuelve a adquirir **el mismo** lease y recibe la expectativa observada;
reconstruye O-02 y mantiene la posesión durante la revalidación inmediata y todos
sus efectos. Liberarlo entre etapas no autoriza una escritura: una expectativa
obsoleta o contención detiene el driver. No hay transacción para todo el rango.

## Configuración y planificación protegida

`OrchestratorConfig` recibe enteros `start_statement` y `end_statement` al final
de su firma. Ambos deben proporcionarse juntos, ser enteros (no booleanos), ser
mayores o iguales a 10 y satisfacer `start <= end`. Se rechazan antes de efectos.
Omitir ambos conserva la planificación anterior sin límites; no se inventa un
límite superior implícito. La entrada histórica de revisión sin límites conserva
su restricción a 010; ampliar índices requiere un rango explícito.

`SyntheticRangeConfig(coordinator, fixture_root, prompt_path,
fixture_location_confirmed=True)` exige los límites en
`coordinator.workflow`, una raíz dedicada bajo el temporal del sistema fuera del
repositorio y rutas confinadas, incluidos lease, prompt y run explícito. Los
límites tienen una única fuente de configuración. `run_synthetic_range(config,
reviewer_adapter, adjudicator_adapter, expected=...)` permite entregar una
expectativa anterior, pero nunca la acepta sin revalidar.

La selección ocurre dentro del rango desde el primer recorrido de completitud,
sin calcular y filtrar un plan global. Los índices históricos 000–009 se rechazan
como límites y jamás se ejecutan. Un directorio ausente dentro del rango recibe
preflight en su propio índice; si sus entradas están completas puede iniciarse.
El acumulado de perfiles/historiales anterior debe ser compatible con ese inicio;
no se fabrica ni se reconstruye un acumulado para comenzar en otro índice.

Ambos límites se incluyen en todos los `StatementPlan`, también los bloqueados y
el plan de rango completo. O-02 compara estos campos incluso con una expectativa
sin hash. Además incorpora `start:end` como payload ASCII de un registro binario
encuadrado con etiqueta `statement_bounds`, usando el dominio y longitudes de
la huella existente. Cambiar cualquiera de los límites invalida la expectativa
aunque el índice y acción siguientes sigan siendo los mismos. No cambia el
almacenamiento del workflow: estas huellas son precondiciones en memoria.

La huella mantiene su alcance conservador sobre las raíces completas de O-02.
Un cambio o dependencia simbólica fuera del rango puede invalidar el ciclo; no
habilita ejecutar un índice ajeno. Los inspectores pueden leer aplicaciones
sucesoras para validar el acumulado previo, conservando el contrato OLBM-33.

## Evidencia permanente de escenarios

| Escenario | Comprobación reproducible |
|---|---|
| A: 010→011→012 | Tres revisores, adjudicación y aplicación por índice; al iniciar cada revisor posterior se exige completitud global de todos sus predecesores |
| B: 010–015 inclusivo | 18 llamadas de revisión, 6 adjudicaciones, 6 aplicaciones; 009 preservado y 016 con staging inválido ignorado como candidato; replay sin llamadas ni cambios |
| Otros límites | 020–021 y 010–010; no hay límites superiores fijos en código ni dependencia del ejemplo |
| C: falta input en 012 | Cuatro variantes: declaración, Gemma, Mistral y Llama; 010/011 completos, ruta concreta en el bloqueo de 012, sin directorios/ejecución 012–015 y sin cambios en inputs |
| D: solo Grok pendiente | ChatGPT y Gemini ya confirmados; una llamada Grok, una adjudicación tras 21 artefactos; sets anteriores byte-idénticos |
| E: procesos nuevos | Cortes de revisión, adjudicación, historiales, perfiles, `APPLIED`, limpieza y evidencia; cada hijo reconstruye config y progreso desde JSON/JSONL |
| F: falso COMPLETE | Almacén ausente, manifiesto corrupto o evidencia v2 corrupta en 010: bloqueo sin efectos, sin revisores/adjudicador y sin iniciar 011 |
| G: límites cambiados | Cambiar inicio o fin manteniendo el mismo índice/acción: hash distinto, expectativa simple o con hash obsoleta y confirmación inmediata rechazada |
| Corrupción/ambigüedad | Tras una aplicación parcial de 011, alterar manifiesto/preparado/destino o añadir staging ambiguo; hijo nuevo bloqueado sin efectos ni 012 |
| Contención | Un proceso posee `WorkflowLease`; otro obtiene `CONTENDED` sin llamadas/efectos; terminar el holder permite completar |

Los fixtures inicializan cada historial con los índices anteriores una vez y el
perfil con `outputs_reviewed=start`. La variante del adjudicador existente
conserva patrones y aumenta la versión; el driver no calcula contadores:

| Statement terminado | Cada historial contiene | outputs_reviewed | versión del perfil |
|---|---|---:|---:|
| 010 | 000–010, una vez | 11 | 2 |
| 011 | 000–011, una vez | 12 | 3 |
| 012 | 000–012, una vez | 13 | 4 |
| 015 | 000–015, una vez | 16 | 7 |

Para recuperación, el segundo hijo se detiene justo antes de iniciar 012 y
permite comparar los destinos ya confirmados de 011 por bytes **y mtime**.
Conserva sets activos y adjudicación previa; no vuelve a ejecutar revisores
completos ni adjudicación confirmada. Un tercer hijo completa solo 012 y un
cuarto hace replay sin revisores ni adjudicaciones. El corte antes de activar un
revisor reutiliza la recuperación de linaje v2 ya aprobada.

## Validación local e integridad

Resultados definitivos y comandos exactos: entrada OLBM-34 en
`context/advances.md`. Python 3.11.17, macOS 27.0.1 arm64.

**300 pruebas únicas PASS:** 43 nuevas y 257 regresiones. Corrida consolidada de
299 casos (42 nuevos + 257 regresiones): PASS, 0 fallos, 0 omitidos en 171.64 s.
Después se añadió el corte explícito de adjudicación preparada, diferenciado
del hook homónimo de preparación de revisión: PASS, 1 caso adicional en 6.04 s
(42 casos no seleccionados). No se repitió toda la suite por ese cambio acotado.
La corrida inicial de los primeros 37 casos también pasó en 105.73 s.

| Regresión focalizada | PASS |
|---|---:|
| O-01 | 28 |
| O-02 | 36 |
| Flujo vertical | 16 |
| Adjudicación sintética | 25 |
| Aplicación sintética, incluido QA33-01 | 72 |
| Recuperación de adjudicación entre procesos | 11 |
| Identidad de run | 8 |
| Completitud global | 37 |
| WorkflowLease | 24 |

Compilación de los ocho Python modificados/nuevos, `git diff --check` y
comprobación de whitespace de los doce archivos de la unidad (incluidos no
rastreados): PASS. No hubo fallos en las corridas de pytest de esta unidad.

Snapshot operacional inicial: 85 archivos en `data/`, `config/feedback/` y
`config/profiles/`, mapa ordenado `ruta → SHA-256(bytes)` guardado en
`/private/tmp/olbm34-operational-before.json`. Hash agregado:
`e90b83b4446a2dd4c60dbb197f6a3966d9371fbdeee1a3813119ebd7347f696a`.
La comparación final exige igualdad completa del mapa, incluidas altas/bajas.
La prueba permanente también compara estas tres raíces antes/después.
Resultado final: **mapa idéntico, 85 archivos, mismo SHA-256 agregado antes y
después**. Evidencia local `LOCAL_TESTED`.

Las regresiones se limitan a las interfaces modificadas O-01/O-02, flujo vertical,
adjudicación/aplicación sintética y sus dependencias críticas de recuperación,
identidad de run, completitud y lease. Suite completa `NOT_RUN` por alcance;
cobertura `NOT_MEASURED`; Linux/Windows `NOT_RUN`; remoto
`REMOTE_NOT_CONFIGURED`, pruebas remotas `NOT_RUN`.

## Límites y OLBM-35

El driver consume puertos sintéticos existentes. No prepara ni certifica un
builder/parser general de `main_review.md`, no especifica transporte de
adaptadores reales, conversaciones, autenticación ni capacidades permitidas.
Esa especificación sigue en OLBM-35; cualquier interacción real requiere su
autorización correspondiente. No se implementa el cuarto generador.

Se conservan los límites de OLBM-32/33: lock cooperativo, reemplazos atómicos por
archivo, hashes sin autenticidad frente a reescritura coordinada y guardas que
reducen TOCTOU. Se prueban caídas de procesos, no pérdidas de energía. El coste
de revalidar cadenas acumuladas aumenta con su longitud; no se certifican rangos
arbitrariamente grandes ni rendimiento remoto. Evidencia corrupta no se repara
automáticamente; la recuperación solo acepta preimagen u objetivo verificable.
La corrección QA33-01 permanece intacta: v2 requiere almacén verificable y no
puede usar el fallback v1 al retirarlo.

## Checklist para QA independiente — pendiente

- [ ] Reproducir pruebas nuevas A–G y regresiones focalizadas de avances.
- [ ] Verificar límites inclusivos, parámetros inválidos, 020–021, 010–010 y protección 000–009.
- [ ] Confirmar selección del menor incompleto antes de cualquier recuperación posterior y preflight de directorios ausentes.
- [ ] Modificar inicio/fin sin cambiar la siguiente acción y exigir `STALE_PLAN` también en la confirmación inmediata.
- [ ] Retirar cada entrada de 012; verificar rutas, 010/011 completos, cero generación y cero avance a 013–015.
- [ ] Confirmar que solo corre Grok cuando ChatGPT/Gemini están completos y que adjudicación espera 3/3.
- [ ] Reproducir cortes con procesos independientes y comprobar linaje, hashes y conservación de sets/adjudicación.
- [ ] Comparar bytes/mtime de destinos aplicados antes del corte y confirmar acumulados sin duplicados al terminar.
- [ ] Retirar almacén v2 y corromper manifiesto/evidencia; exigir bloqueo sin fallback ni siguiente índice.
- [ ] Corromper preimagen/preparado/destino y crear ambigüedad tras un corte; exigir bloqueo sin escrituras.
- [ ] Confirmar contención, liberación tras terminar un proceso y uso del mismo lease dentro de cada efecto.
- [ ] Verificar descartado de notes, seis destinos Gemma/Mistral/Llama y unidad `start=end=actual`.
- [ ] Comparar mapa operacional antes/después y preservar todo el worktree preexistente.
- [ ] Confirmar ausencia de almacenamiento/locks paralelos, SQL, plataformas, commits y pushes.
- [ ] Emitir conclusión independiente antes de marcar OLBM-34 `DONE`; mantener fase 2 `IN_PROGRESS`.
