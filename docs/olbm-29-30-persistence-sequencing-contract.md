# OLBM-29/30 — Contrato de persistencia y avance secuencial

**Estado:** especificación técnica lista para revisión.
**Fecha:** 2026-10-30.
**Alcance:** documentación de OLBM-29 y OLBM-30; no implementa OLBM-31–34.

## 1. Alcance y fuentes inspeccionadas

Esta especificación contrasta el contrato funcional aprobado con el árbol de
trabajo de la rama `data/prepare-training-data`. El worktree contiene cambios
preexistentes no confirmados; se trataron como propiedad del usuario. La fase 2
permanece `IN_PROGRESS`.

Fuentes principales:

- Planificación y secuencia: `automation/orchestrator.py`,
  `automation/review/statement_selector.py` y
  `automation/review/completeness.py`.
- Coordinación: `automation/workflow_coordinator.py` y
  `automation/workflow_lease.py`.
- Entradas: `automation/review/input_loader.py`.
- Contratos y almacenamiento de revisiones:
  `automation/contracts/results.py`, `automation/review/result_inspector.py` y
  `automation/review/review_storage.py`.
- Adjudicación preparada, validación, aplicación y finalización:
  `automation/review/synthetic_adjudication.py`,
  `automation/review/validators.py`,
  `automation/review/adjudication_storage.py` y
  `automation/review/finalization.py`.
- Compatibilidad heredada: `src/review_data_loader.py`.
- Pruebas focales: `tests/test_review_input_inspection.py`,
  `tests/test_review_storage.py`, `tests/test_adjudication_storage.py`,
  `tests/test_review_finalization.py`, `tests/test_statement_completeness.py`,
  `tests/test_orchestrator_planner.py`,
  `tests/test_synthetic_review_flow.py` y
  `tests/test_synthetic_adjudication_flow.py`.

No se inspeccionó contenido policial real más allá de rutas y metadatos ya
necesarios para reconstruir los contratos. No se ejecutaron modelos,
navegadores, plataformas externas ni infraestructura remota.

## 2. Inventario de interfaces y archivos existentes

| Responsabilidad | Interfaz existente | Persistencia o salida | Referencia |
|---|---|---|---|
| Validar declaración y tres outputs | `inspect_review_inputs()` / `ReviewInputValidation` | Solo lectura de `statements.jsonl` y tres `draft_reports.jsonl` de un run inequívoco | `automation/review/input_loader.py:31-73`, `256-258`, `376-439`, `441-475` |
| Seleccionar ordenadamente | `discover_statements()`, `select_statement()`, `select_statement_from_verification()` | Ninguna escritura | `automation/review/statement_selector.py:60-184`; `automation/review/completeness.py:749-855` |
| Planificar la etapa pendiente | `plan_next_statement()`, `resume_plan()` | `StatementPlan` / `RecoveryPlan` en memoria | `automation/orchestrator.py:388-662`, `686-866` |
| Excluir un segundo escritor cooperante | `WorkflowLease` y `coordinate_workflow_under_lease()` | Lock del sistema operativo y metadato diagnóstico fuera de `data/`/`config/` | `automation/workflow_lease.py:112-294`; `automation/workflow_coordinator.py:178-321` |
| Representar siete salidas de un revisor | `CompleteReviewResult` | Una revisión, tres historiales provisionales y tres propuestas de perfil | `automation/contracts/results.py:221-296` |
| Publicar/reemplazar un revisor | `publish_complete_review()` | `.review_sets/<revisor>/attempts/<set_id>/`, manifiesto, `active.json` y proyección `<revisor>_review.json` | `automation/review/review_storage.py:112-134`, `279-335`, `703-812` |
| Inspeccionar revisiones | `inspect_reviewer_results()` | `COMPLETE`, `INCOMPLETE`, `INVALID`, `NOT_STARTED` o `UNVERIFIED`; barrera `adjudication_enabled` | `automation/review/result_inspector.py:28-104`, `600-682` |
| Preparar adjudicación final sintética | `run_synthetic_adjudication_once()` / `PreparedAdjudication` | Solo memoria, `persisted=false` | `automation/review/synthetic_adjudication.py:306-458`, `527-558` |
| Validar adjudicación final | `validate_adjudication()` | Rechaza toda clave fuera del esquema de siete campos | `automation/review/validators.py:73-146` |
| Aplicar bundle adjudicado | `apply_adjudicated_results()` | `adjudication.json`, tres JSONL acumulados, tres perfiles; aplicación interna con preimágenes y hashes | `automation/review/adjudication_storage.py:813-1028` |
| Inspeccionar/reanudar aplicación | `inspect_adjudication_application()`, `resume_adjudication_application()` | `PREPARED`, `INTERRUPTED`, `APPLIED` o `INVALID` | `automation/review/adjudication_storage.py:545-717`, `720-810` |
| Finalizar y publicar evidencia | `finalize_adjudication_application()` | `application_evidence.json`, manifiesto/estado de limpieza; conserva conjuntos y aplicaciones | `automation/review/finalization.py:127-237`, `240-292`, `295-396` |
| Determinar completitud global | `inspect_statement_completeness()` | Solo lectura; exige entradas, revisiones, adjudicación, historiales, perfiles y evidencia | `automation/review/completeness.py:594-746` |

### 2.1 Clasificación de archivos dentro de `statement_NNN`

El contrato no equipara “archivo dentro del directorio” con “resultado final del
benchmark”. Las categorías son:

1. **Resultados finales del benchmark**
   - `chatgpt_review.json`
   - `gemini_review.json`
   - `grok_review.json`
   - `adjudication.json`
2. **Artefactos internos de revisión**
   - `.review_sets/<revisor>/attempts/<set_id>/...`: los siete artefactos del
     intento, `manifest.json` y los metadatos mínimos de continuidad.
   - `.review_sets/<revisor>/active.json`: único punto de confirmación de la
     revisión activa.
3. **Metadatos y evidencia técnica de recuperación**
   - `.adjudication_applications/<application_id>/prepared/...`,
     `manifest.json`, `status.json`, `cleanup_manifest.json` y
     `cleanup_status.json`.
   - `application_evidence.json`: comprobante final consumido por el verificador
     global; no es otro resultado del benchmark.

`ADJUDICATION_NOTES` no pertenece a ninguna categoría persistida. Puede existir
en la respuesta cruda del adjudicador de historiales, pero el parser futuro debe
descartarlo después de validar y extraer los tres historiales. No se incorpora a
`adjudication.json`, historiales, perfiles, manifiestos ni evidencia.

## 3. Matriz de compatibilidad funcional

| Regla aprobada | Estado | Evidencia y consecuencia |
|---|---|---|
| Exigir declaración y outputs de Gemma, Llama y Mistral del mismo índice/run | `YA SOPORTADA` | `ReviewInputValidation.inputs_complete` exige statement, run y exactamente `set(BenchmarkModel)`; cada archivo y registro se valida en `automation/review/input_loader.py:67-73`, `203-253`, `376-439`. |
| Informar exactamente el faltante y detener sin saltar | `YA SOPORTADA` | Los issues conservan código, ruta y modelo (`automation/review/input_loader.py:22-28`, `97-143`, `396-419`); `resume_plan()` devuelve `BLOCKED`/`WAIT_OPERATOR` antes de revisar (`automation/orchestrator.py:476-513`). |
| No regenerar inputs ni modificar destinos ante entradas incompletas | `YA SOPORTADA` | Loader, completitud y O-01 son de solo lectura; las pruebas `test_missing_any_model_output_is_reported`, `test_missing_inputs_produce_concrete_blocker` y pruebas de solo lectura cubren la frontera. |
| Reutilizar revisores completos y ejecutar solo los pendientes | `YA SOPORTADA` | O-01 separa `RETRY_REVIEWER` de `RUN_MISSING_REVIEWER` y enumera solo afectados (`automation/orchestrator.py:569-622`); el flujo vertical publica un revisor por llamada. |
| Cada revisor produce exactamente siete artefactos coherentes | `YA SOPORTADA` | `CompleteReviewResult` exige `1 + 3 + 3` y una sola identidad (`automation/contracts/results.py:221-278`); el escritor revalida antes de publicar (`automation/review/review_storage.py:212-276`). |
| No adjudicar hasta tres revisiones completas | `YA SOPORTADA` | `adjudication_enabled` exige exactamente ChatGPT, Gemini y Grok `COMPLETE` (`automation/review/result_inspector.py:86-104`); O-01 solo entonces emite `RUN_ADJUDICATIONS` (`automation/orchestrator.py:623-648`). |
| Conservar identidad, hashes, manifiestos, activación e idempotencia de revisiones | `YA SOPORTADA` | Conjuntos inmutables, `active.json`, hash del manifiesto y reintento idéntico `UNCHANGED` en `automation/review/review_storage.py:279-335`, `443-514`, `703-812`. |
| Resultados finales visibles limitados a tres revisiones y `adjudication.json` | `YA SOPORTADA` | La proyección permanente solo crea `<revisor>_review.json` (`automation/review/review_storage.py:686-700`) y el bundle solo crea `adjudication.json` como resultado final dentro del statement (`automation/review/adjudication_storage.py:872-883`). Los directorios ocultos y `application_evidence.json` son evidencia técnica, no resultados del benchmark. |
| No persistir `ADJUDICATION_NOTES` | `SOPORTADA PARCIALMENTE` | `validate_adjudication()` rechaza claves extra y ningún escritor actual tiene destino para notes. Falta el parser/adjudicador de historiales que extraiga tres historiales y descarte explícitamente el bloque; los documentos anteriores que exigían preservarlo se sustituyen por D-034. |
| Preparar siete destinos con preimágenes y recuperación idempotente | `YA SOPORTADA` | La aplicación identifica preimagen/objetivo, valida todo antes de escribir y reanuda solo la misma identidad (`automation/review/adjudication_storage.py:813-1028`). No promete atomicidad multiarchivo. |
| Aplicar a historiales y perfiles solo el statement actual | `SOPORTADA PARCIALMENTE` | `apply_adjudicated_results()` acepta un único `statement_index`, añade solo su registro de historial si falta y recibe tres perfiles completos (`automation/review/adjudication_storage.py:813-883`). El escritor comprueba esquema y no regresión, pero no garantiza todavía que `last_observed_output` corresponda al statement actual, que `outputs_reviewed` avance únicamente conforme al statement adjudicado ni que `evidence_count` sea consistente con su evidencia. |
| Política operacional `start=end=statement_actual` | `SOPORTADA PARCIALMENTE` | Encaja directamente con el escritor mono-statement y con los prompts que aceptan un rango inclusivo de un elemento. Falta una interfaz de orquestación que reciba límites inclusivos y aplique esta política en cada iteración. |
| Elegir el incompleto de menor índice dentro de un rango inclusivo | `SOPORTADA PARCIALMENTE` | O-01 reanuda el predecesor activo incompleto de menor índice y no avanza sobre `UNVERIFIED` (`automation/review/completeness.py:749-855`; `automation/orchestrator.py:784-866`), pero `OrchestratorConfig` no contiene `start_index`/`end_index`; hoy la búsqueda abarca toda la secuencia activa descubierta. |
| No considerar terminado por la sola existencia de `adjudication.json` | `YA SOPORTADA` | `inspect_statement_completeness()` exige además tres revisiones, tres historiales, tres perfiles y evidencia coherente (`automation/review/completeness.py:629-729`). |
| Recuperar el mismo statement y etapa después de interrupción | `SOPORTADA PARCIALMENTE` | O-01 prioriza aplicaciones `PREPARED`/`INTERRUPTED` y `APPLIED` sin evidencia (`automation/orchestrator.py:207-385`, `414-417`); aplicación y finalización son reanudables. Falta el ejecutor integrado de OLBM-31 que mantenga el lease y conecte esas acciones. |
| Avanzar solo tras aplicación verificada y evidencia final | `YA SOPORTADA` | `can_advance` solo acepta `COMPLETE`/`HISTORICAL_COMPLETE` (`automation/review/completeness.py:97-110`); la evidencia v2 vincula hashes, perfiles aplicados y limpieza (`automation/review/finalization.py:259-292`). |
| Proteger `statement_000`–`statement_009` | `YA SOPORTADA` | Planner, escritor de revisiones, aplicación y finalización rechazan o cierran el rango histórico (`automation/orchestrator.py:400-412`; `automation/review/review_storage.py:716-719`; `automation/review/adjudication_storage.py:828-832`; `automation/review/finalization.py:307-314`). |
| Mantener únicamente tres generadores actuales | `YA SOPORTADA` | `BenchmarkModel` y validadores exigen Gemma, Mistral y Llama. No se modifica el contrato para un cuarto generador en OLBM-29/30. |
| Usar `src/review_data_loader.py` como ruta del nuevo workflow | `CONFLICTO DE CONTRATO` | El helper heredado elige el run por nombre máximo (`75-95`), permite hasta dos índices (`163-178`) y convierte un output ausente en un objeto de error para formateo (`248-260`). OLBM-31 no debe invocarlo; debe reutilizar `inspect_review_inputs()`. |

## 4. Contrato técnico consolidado

### 4.1 Precondición y bloqueo

Para el índice seleccionado, `inspect_review_inputs()` debe producir
`inputs_complete=true`. Cualquier issue `missing`, `invalid`, `duplicate` o
`ambiguous` bloquea el statement y se reporta con `code`, `path` y, cuando
aplique, `model`. No se crea el directorio siguiente, no se ejecuta un revisor o
adjudicador y no se toca ningún resultado válido.

Si varios runs contienen el índice, el contrato vigente continúa exigiendo una
selección explícita de `run_id`; no se elige el más reciente.

### 4.2 Confirmación de revisiones y adjudicación

Una revisión queda confirmada solo por un `active.json` válido que referencia un
set inmutable completo y verificable. La proyección `<revisor>_review.json` por sí
sola no confirma el intento. Un revisor completo se conserva; uno no iniciado se
ejecuta; uno incompleto o inválido se reemplaza como conjunto íntegro de siete;
uno `UNVERIFIED` bloquea para resolución de procedencia.

La adjudicación solo puede empezar cuando los tres revisores están `COMPLETE`.
Antes de aplicar debe existir y validarse en memoria el bundle completo:

- una adjudicación final de reporte;
- un historial adjudicado para cada modelo;
- un perfil acumulado adjudicado para cada modelo;
- `run_id` y las identidades activas de los tres revisores.

No se persiste una adjudicación parcial. En particular, no se debe escribir
`adjudication.json` antes de tener validados los otros seis destinos.

### 4.3 Aplicación acumulada

El **rango operacional configurable** es `[start, end]`, inclusivo, y lo
proporciona quien ejecuta el workflow. Por ejemplo, el operador puede solicitar
`start=statement_010` y `end=statement_015`: deben poder procesarse tanto 010
como 015, mientras que 009 y 016 quedan fuera de esos límites. Este ejemplo no
establece un rango fijo obligatorio.

La **unidad de actualización incremental** es independiente de esos límites
exteriores: para cada iteración se suministra a la adjudicación de perfiles e
historiales `start=end=statement_actual`. El perfil anterior y el historial
anterior son la preimagen acumulada; el resultado adjudicado es el nuevo
acumulado. La persistencia no recalcula todo el rango operacional y no
incrementa contadores por su cuenta.

Antes del primer efecto persistente, la futura integración debe validar de
forma conjunta la adjudicación recibida, la preimagen acumulada y el
`statement_index` actual. Debe rechazar toda transición incompatible, incluida
una en la que `last_observed_output` no corresponda al statement actual,
`outputs_reviewed` avance por evidencia ajena al statement adjudicado o
`evidence_count` sea inconsistente con la evidencia aplicable. Esta
especificación no define fórmulas nuevas para esos contadores: la validación
debe derivarse de los contratos vigentes y documentarse antes de implementarse.

El caso adversarial observado por QA demuestra la brecha actual: un fixture de
`statement_010` fue aceptado con metadatos de perfil que contenían valores
`999`. Por ello, la garantía semántica de actualización acumulada de perfiles
permanece `SOPORTADA PARCIALMENTE`; el escritor y los validadores actuales no se
modifican en OLBM-29/30.

El escritor prepara siete resultados y sus siete preimágenes, aplica cada
archivo atómicamente y verifica su hash. No existe transacción atómica
multiarchivo. Una interrupción deja estado `PREPARED` o `INTERRUPTED`; el reinicio
continúa únicamente si cada destino conserva su preimagen o ya contiene el
objetivo. Un cambio externo diferente bloquea sin revertir datos posteriores.

### 4.4 Evidencia que sobrevive reinicios

Se conservan:

- sets de revisión, manifiestos, hashes, `active.json`, `task_id`, `attempt_id`,
  `attempt_number`, `run_id` y clave de deduplicación;
- aplicación adjudicada, copias preparadas, hashes de origen/destino,
  `application_id`, estado y manifiestos de limpieza;
- evidencia final v2 con identidades de revisores, hash de adjudicación,
  identificadores/hashes de historiales, hashes/metadatos/snapshot de perfiles y
  confirmación de limpieza.

La limpieza solo recibe rutas sueltas explícitas y nunca elimina resultados
finales, sets confirmados, aplicaciones o evidencia. Que el conjunto interno
permanezca no lo convierte en un quinto resultado final del benchmark.

### 4.5 Completitud y avance

Un statement activo es `COMPLETE` solo cuando:

1. sus cuatro entradas originales son válidas y alineadas;
2. los tres sets de revisión activos son completos;
3. `adjudication.json` pasa `validate_adjudication()`;
4. existe exactamente un historial adjudicado válido por modelo e índice;
5. los tres perfiles acumulados son válidos y no regresivos;
6. la aplicación está `APPLIED` y la evidencia v2 concuerda con destinos e
   identidades;
7. la limpieza explícita terminó y quedó confirmada.

El valor `approved`, `needs_human_review` o `rejected` no cambia esta completitud
operativa. Dentro de límites inclusivos `[start, end]`, el coordinador debe
seleccionar el menor índice no completo. Solo después de obtener `COMPLETE`
puede evaluar el siguiente. `UNVERIFIED` bloquea; no equivale a incompleto
reintentable.

Los índices `000`–`009` permanecen `HISTORICAL_COMPLETE` por política y están
fuera de cualquier revisión, adjudicación, reaplicación o reconstrucción de
evidencia.

## 5. Encaje de `start=end`

La política encaja **directamente** con los contratos de persistencia actuales:
`apply_adjudicated_results()` y `finalize_adjudication_application()` ya operan
con un único `statement_index`. No debe cambiarse su semántica ni añadírseles un
rango.

La adaptación mínima corresponde al nivel superior:

1. añadir a la planificación de O-01 y a la coordinación/revalidación de O-02
   ambos límites inclusivos, proporcionados por el ejecutor, y protegerlos en la
   huella de precondiciones;
2. seleccionar allí el menor índice pendiente dentro de esos límites;
3. invocar los adjudicadores de historial/perfil con
   `start_index=end_index=statement_actual`;
4. aplicar/finalizar ese único statement bajo el mismo `WorkflowLease`;
5. reinspeccionar hasta `COMPLETE` antes de incrementar el índice.

No es seguro ejecutar O-01 global y filtrar su resultado después: O-01 puede
priorizar una aplicación recuperable o un statement incompleto fuera del rango.
La adaptación debe revalidarse y formar parte de la huella de O-02. OLBM-29/30
solo documenta esta incompatibilidad; no modifica componentes aprobados.

Así, `statement_010`–`statement_015` es únicamente un ejemplo de rango
operacional configurable: incluye ambas fronteras, excluye 009 y 016, y no
altera la unidad incremental `start=end=statement_actual`.

No debe reutilizarse `src/review_data_loader.main()` para esta adaptación: su
selección de run, tolerancia a outputs ausentes y rango de dos elementos son un
contrato heredado distinto.

## 6. Dependencias para OLBM-31, OLBM-32 y OLBM-33

El repositorio solo identifica explícitamente a OLBM-31 como persistencia
sintética; esta sección fija dependencias técnicas y no inventa títulos o alcance
adicional para OLBM-32/33.

- **OLBM-31** debe conectar la adjudicación preparada y un bundle sintético
  validado `1 + 3 + 3` con `apply_adjudicated_results()`,
  `finalize_adjudication_application()` e
  `inspect_statement_completeness()`, manteniendo el mismo lease. Antes del
  primer efecto debe incorporar la validación semántica de perfiles contra la
  adjudicación, la preimagen acumulada y el índice actual. No necesita modificar
  escritores, esquemas ni O-01/O-02.
- **OLBM-32** debe consumir la postcondición verificable de OLBM-31 —no la mera
  existencia de `adjudication.json`— para cualquier coordinación posterior. Si
  introduce el driver de rango, este debe permanecer fuera de los escritores,
  aplicar la selección acotada descrita en la sección 5 y extender la
  revalidación/huella de O-02; no puede filtrar un plan global ya emitido.
- **OLBM-33** debe depender de la evidencia v2 y de la selección secuencial ya
  probadas. Cualquier integración posterior debe reanudar primero
  `RECOVER_APPLICATION`/`FINALIZE_APPLICATION`, preservar 000–009 y no crear otro
  registro de estado. También debe conservar como puerta previa a efectos la
  validación semántica de perfiles introducida para la integración de OLBM-31,
  sin confiar únicamente en la no regresión del escritor.

Las tres dependencias comunes son: parser/fixture determinista para tres
historiales y tres perfiles adjudicados, descarte explícito de notes, bundle
completo antes del primer efecto y revalidación inmediata bajo lease.

## 7. Criterios verificables de OLBM-31

OLBM-31 estará listo para QA solo si pruebas sintéticas bajo `tmp_path` demuestran:

1. Tres revisores activos completos producen exactamente un bundle persistible
   `1 + 3 + 3`; dos revisores producen cero efectos.
2. Faltar declaración o cualquiera de los tres outputs devuelve el issue con la
   ruta/modelo exactos, no llama adjudicadores y mantiene byte-idénticos perfiles,
   historiales y statements.
3. Un bundle incompleto, duplicado, con índice/modelo/run/identidad incorrectos o
   con `ADJUDICATION_NOTES` en un destino persistible se rechaza antes del primer
   reemplazo.
4. Una transición de perfil incompatible con la adjudicación, la preimagen
   acumulada o el índice actual —incluidos metadatos `999` para
   `statement_010`— se rechaza antes del primer reemplazo, sin imponer fórmulas
   de contadores ajenas a los contratos vigentes.
5. `statement_010` crea como resultados finales únicamente las tres proyecciones
   de revisión y `adjudication.json`; notes no aparece en ningún archivo.
6. La aplicación conserva manifiesto, siete preimágenes/objetivos, intentos,
   `run_id` y estado; la finalización publica evidencia v2 coherente.
7. Cortes después de preparar, después de cada uno de los siete reemplazos,
   después de marcar `APPLIED`, durante limpieza y antes/después de evidencia se
   recuperan sin duplicar historiales ni regresar perfiles.
8. Repetir el flujo completo devuelve comportamiento idempotente: no vuelve a
   ejecutar revisores completos, no crea otra aplicación competidora y no añade
   una segunda entrada del mismo índice.
9. `inspect_statement_completeness(10)` es `COMPLETE` solo después de
   finalización; antes es `INCOMPLETE` o `UNVERIFIED` según la evidencia.
10. Con 010 completo y 011 incompleto, la siguiente decisión es 011; con 010
   incompleto y 011 completo, se reanuda 010; con un predecesor `UNVERIFIED`, se
   bloquea.
11. Todo intento contra 000–009 falla antes de escribir.
12. El lease permanece poseído desde la revalidación inmediata hasta aplicación
    y finalización; contención produce cero efectos.
13. La huella agregada de `data/`, `config/feedback/` y `config/profiles/` reales
    es idéntica antes y después de la suite.

## 8. Bloqueantes reales y límites

No queda una decisión funcional pendiente sobre `ADJUDICATION_NOTES` o el rango:
notes no se persiste y la aplicación es mono-statement. Para implementar
OLBM-31 todavía faltan cuatro piezas de integración, no cambios a los escritores:

- producir/parsear sintéticamente los tres historiales y tres perfiles
  adjudicados;
- validar semánticamente cada perfil adjudicado contra su preimagen acumulada y
  el índice actual antes del primer efecto persistente;
- conectar el bundle completo al escritor/finalizador bajo el mismo lease;
- añadir, en un incremento posterior si corresponde, ambos límites inclusivos a
  O-01/O-02 y a la huella protegida del ejecutor de rango.

Persisten límites ya documentados: no hay atomicidad multiarchivo, el lease es
cooperativo, las guardas de ruta no eliminan toda carrera TOCTOU y los hashes no
aportan autenticidad frente a reescritura coordinada. No bloquean la persistencia
sintética dentro del modelo de amenazas vigente.

## 9. Lista de revisión de QA independiente

QA debe contrastar esta especificación con líneas vigentes del código y probar,
como mínimo, entradas incompletas, barrera de tres revisores, publicación de siete
artefactos, descarte de notes, bundle `1 + 3 + 3`, cortes entre cada destino,
idempotencia, evidencia v2, perfiles posteriores no regresados, selección del
menor índice, límites inclusivos cuando existan y protección 000–009. Debe
confirmar además que no se introdujo un segundo almacenamiento ni se modificaron
datos operacionales.
