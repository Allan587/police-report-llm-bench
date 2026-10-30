# Arquitectura técnica propuesta

## Especificación futura de revisores reales — OLBM-35

Diseño verificable en [OLBM-35](../docs/olbm-35-real-reviewer-adapter-design.md),
decisión D-040; `READY_FOR_QA` documental, fase 2 `IN_PROGRESS`. No incorpora
adaptadores operacionales ni modifica las interfaces ejecutables aprobadas.
Se conserva `ReviewerAdapter.execute(ReviewerExecutionRequest) -> CompleteReviewResult`,
los siete artefactos, manifiesto v2, referencia activa, lease y O-01/O-02.
El vertical actual sigue confinado a fixtures y proveedor sintético.

Pendientes para una implementación real: contexto de perfiles/historiales y
prompt preparado, parser estricto, capacidades/autorizaciones por plataforma,
errores estructurados y cancelación consumidos con estados existentes.
Run se valida por separado del resultado y se vincula mediante los metadatos v2;
la clave lógica de tarea no contiene run. Antes de preparar un conjunto no hay
registro durable de cada envío externo: idempotencia de publicación local no
garantiza ejecución externa única. El diseño exige reconciliación ante envío
incierto y una decisión revisada antes de habilitar operación autónoma.
No se usa red, SQL, navegador ni datos reales en esta especificación; no cambia
la preferencia web de D-002 ni habilita un cuarto generador.

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
│   ├── review/
│   │   ├── statement_selector.py
│   │   ├── input_loader.py
│   │   ├── completeness.py
│   │   └── adjudication.py
│   └── storage/
│   │   ├── file_repository.py
│   │   └── atomic_writer.py
├── config/            # conservar y adaptar lo existente
├── data/              # conservar y adaptar lo existente
└── tests/
```
Añadir `__init__.py` donde sean necesarios. Reusar componentes equivalentes si ya existen: no crear duplicados innecesarios.

## Entornos de ejecución

- **Desarrollo local:** macOS, Python 3.11, pruebas unitarias y páginas sintéticas de Playwright. Las dependencias de inferencia no son requisito para validar la capa de navegador.
- **Ejecución final:** máquina remota todavía no auditada. Playwright, Chromium, perfiles persistentes y procesos de automatización se ejecutarán allí, junto a los procesos de inferencia existentes pero en un entorno Python separado.
- Git transfiere únicamente código y configuración versionable. Entornos virtuales, perfiles, cookies, secretos y estado de navegador permanecen fuera de Git.
- No se presupone sistema operativo, escritorio gráfico, versión de Python, dependencias del sistema ni compatibilidad headless del servidor. Esas propiedades deben verificarse antes del despliegue.

## Flujo de datos

`statement + outputs + perfiles + historiales → entrada de revisión → ChatGPT/Gemini/Grok → barrera de resultados completos → tres adjudicaciones → aplicación atómica de historiales y perfiles → statement completo`.

El flujo activo empieza con outputs ya existentes y termina cuando el statement queda verificado. La construcción o filtrado de datasets y el entrenamiento o fine-tuning no son etapas ni dependencias de este workflow.

### Selección y validación inicial implementadas en 2.2.1

`automation/review/statement_selector.py` reconoce exclusivamente directorios `statement_NNN` con al menos tres dígitos, los ordena por el índice numérico y expone una decisión `resume`, `advance` o `pending`. La completitud del último statement siempre llega como entrada mediante `CompletionState`; esta capa no la infiere a partir de archivos. Desde 2.2.3, un adaptador traduce el resultado del verificador global a ese contrato sin modificarlo.

`automation/review/input_loader.py` lee `data/statements/statements.jsonl` y los tres `draft_reports.jsonl` de un único run. Valida JSON, unicidad, `statement_index`, contenido `statement.report`, `draft_report`, identificador físico del modelo y `run_id`. Un run puede indicarse explícitamente; sin esa selección, solo se acepta cuando exactamente un run contiene el índice. Cero candidatos se reportan como dependencia faltante y varios candidatos como ambigüedad, sin elegir por nombre o fecha.

`ReviewInputValidation` mantiene separados el statement, el run, los outputs aceptados y los problemas `missing`, `invalid`, `duplicate` o `ambiguous`. `inputs_complete` significa únicamente que las entradas originales están listas para una revisión; no representa completitud global del statement ni estado de una tarea de 2.1. Toda esta inspección es de solo lectura.

### Inspección de resultados implementada en 2.2.2

`automation/review/result_inspector.py` inspecciona por separado ChatGPT, Gemini y Grok. Conserva el modo de rutas explícitas mediante `ProvisionalArtifactFiles` para candidatos aún no publicados y la compatibilidad histórica. Desde 2.2.4-A, si existe una referencia versionada confirmada, esta tiene prioridad y el inspector resuelve desde ella los siete archivos mediante el contrato compartido con el escritor.

Cada conjunto se clasifica como `COMPLETE`, `INCOMPLETE`, `INVALID`, `NOT_STARTED` o `UNVERIFIED`. Los problemas estructurales permanecen separados de los de procedencia. `CompleteReviewResult` solo se construye cuando existen siete artefactos válidos y todos contienen `task_id`, `attempt_id`, `statement_index` y revisor coherentes. Una mezcla demostrable de intentos es inválida; la ausencia de metadatos suficientes produce `UNVERIFIED`, no una identidad inventada.

La recuperación es solo una recomendación: conservar, repetir la revisión completa, iniciar la primera revisión o bloquear por incertidumbre de procedencia. `adjudication_enabled` es una barrera informativa y solo resulta verdadero cuando los tres revisores están `COMPLETE`; no ejecuta recuperación, adjudicación ni completitud global.

Los casos históricos que conservan revisiones principales y `adjudication.json`, pero no temporales ni procedencia, se reconocen como históricos y quedan `UNVERIFIED`. La ausencia de temporales eliminados no se convierte retroactivamente en `INCOMPLETE`, y la existencia de la adjudicación no demuestra por sí sola aplicación de historiales o perfiles.

### Completitud histórica confirmada y completitud automática

La clasificación técnica de artefactos y el cierre operativo son dimensiones distintas. `COMPLETE`, `UNVERIFIED`, `INCOMPLETE`, `INVALID` y `NOT_STARTED` describen lo que el inspector puede demostrar desde los archivos disponibles. La completitud histórica confirmada registra una decisión explícita del operador sin convertirla retroactivamente en evidencia técnica.

`statement_000` a `statement_009`, ambos inclusive, están cerrados históricamente porque el operador confirmó que sus revisiones, adjudicaciones y aplicaciones de perfiles e historiales finalizaron manualmente. Sus diagnósticos `UNVERIFIED` pueden conservarse, pero el rango queda fuera de recuperación automática, repetición de revisiones o adjudicaciones y reaplicación de resultados. No se exigirán identificadores o temporales heredados, no se modificarán sus archivos y no se fabricarán metadatos.

El verificador global de 2.2.3 respeta esta frontera: reconoce el cierre operativo del rango histórico sin afirmar verificación automática y aplica las reglas técnicas ordinarias solo a statements activos o futuros.

### Verificador global implementado en 2.2.3

`automation/review/completeness.py` compone, sin escribir, el validador de entradas de 2.2.1 y el inspector de revisores de 2.2.2 con validaciones de adjudicación final, historiales adjudicados, perfiles acumulados y evidencia de aplicación. Expone `HISTORICAL_COMPLETE`, `COMPLETE`, `INCOMPLETE` y `UNVERIFIED`: el rango `000`–`009` se resuelve por la política D-015 antes de consultar artefactos; desde `statement_010`, faltantes o estructuras inválidas producen `INCOMPLETE`, mientras una procedencia o aplicación que no puede confirmarse ni descartarse produce `UNVERIFIED`.

La adjudicación final se valida contra los campos obligatorios de `review_adjudication.md`, incluidos los tres revisores, consenso, elementos seleccionados, reporte final, diez controles de calidad y los tres valores permitidos de `decision.status`. `approved`, `needs_human_review` y `rejected` son equivalentes para completitud operativa cuando el resto de condiciones pasa.

Los historiales exigen exactamente un registro estricto por `statement_index` y modelo en cada JSONL. Los perfiles exigen los tres documentos acumulados con su modelo, secciones de patrones, prioridades y metadatos válidos. La presencia de esos destinos no acredita por sí sola que provengan de la adjudicación actual.

El contrato mínimo de evidencia se lee desde una ruta explícita suministrada por el llamador; no se deduce ni se crea un nombre real. Su versión 1 vincula `statement_index`, `run_id`, los `task_id`/`attempt_id` de las tres revisiones, el hash canónico de `adjudication.json`, un identificador de adjudicación y hash del registro aplicado para cada historial, un identificador de adjudicación y hash/metadatos del estado aplicado para cada perfil, y la confirmación de limpieza de temporales. La ausencia del archivo deja el statement `UNVERIFIED`; una contradicción demostrable lo deja `INCOMPLETE`.

`select_statement_from_verification()` adapta el resultado al contrato existente del selector: cierres históricos y completos avanzan, incompletos reanudan y no verificados bloquean. También rechaza usar una verificación perteneciente a un índice distinto del último directorio descubierto. Para el rango activo iniciado en `010`, el selector comprueba todos los índices hasta el mayor directorio reconocido; un hueco bloquea el avance porque este contrato no recibe evidencia de entradas originales del índice ausente. Cada predecesor activo presente debe aportar explícitamente su `StatementCompletionInspection`: `COMPLETE` permite continuar, `INCOMPLETE` selecciona ese índice para recuperación y `UNVERIFIED` o la falta de verificación bloquean. La existencia del directorio nunca acredita completitud. No crea directorios ni inventa declaraciones. La política histórica `000`–`009` no cambia.

La etapa de escritura posterior deberá producir la evidencia únicamente después de validar y publicar las tres adjudicaciones, comprobar los destinos, registrar sus hashes e identidades y limpiar los temporales permitidos. Deberá hacerlo de forma atómica e idempotente; 2.2.3 solo define y verifica el contrato de lectura.

### Planificador determinista de solo lectura implementado en O-01

`automation/orchestrator.py` compone el descubrimiento secuencial, el verificador
global, el inspector de revisores y el inspector de aplicaciones sin ejecutar
ninguna recomendación. `plan_next_statement()` prioriza aplicaciones
`PREPARED`/`INTERRUPTED` y aplicaciones `APPLIED` sin evidencia antes de un
statement nuevo; después selecciona el primer índice activo no completo en orden
o valida las entradas del siguiente índice legítimo. `resume_plan()` distingue
recuperar, finalizar, repetir un conjunto completo de un revisor, iniciar
revisores faltantes, preparar adjudicaciones, completar, esperar intervención o
bloquear por dependencia.

`StatementPlan` y `RecoveryPlan` conservan la acción, el statement, el run
validado, revisores afectados, evidencia consultada, verificaciones ordenadas y
un bloqueo explícito. Múltiples aplicaciones accionables, procedencia
`UNVERIFIED`, un run ambiguo, huecos, aplicaciones inválidas o evidencia
contradictoria no se resuelven por orden de nombre o fecha. El planificador nunca
crea el directorio del siguiente statement y mantiene `statement_000`–`009`
fuera de toda propuesta de reprocesamiento.

La remediación O01-QA valida además que cada aplicación inspeccionada declare el
mismo `statement_index` que su directorio contenedor; una contradicción produce
`WAIT_OPERATOR` con `application_statement_mismatch`. Los errores contractuales
de acceso o inspección, incluida la primera inspección después de la frontera
histórica, se convierten en `statement_inspection_failed` sin capturar excepciones
arbitrarias. Los mappings de revisores deben contener exactamente claves
`Reviewer` y toda evidencia o lista resultante sigue siempre el orden canónico
`chatgpt`, `gemini`, `grok`, independientemente del orden de inserción.

### Lease global implementado como primer componente de O-02

`automation/workflow_lease.py` expone `WorkflowLease`, `LeaseOwner`, `validate()`
y errores tipados de adquisición, integridad, contención, propiedad y liberación.
En macOS/POSIX la exclusión efectiva usa `flock` sobre el descriptor del
directorio padre privado y estable; el JSON indicado por `lock_path` contiene
únicamente metadatos diagnósticos. Eliminar o reemplazar ese JSON no crea un
segundo propietario cooperante, porque todos disputan el mismo inode del
directorio. El backend Windows mantiene `msvcrt` sobre el archivo y continúa sin
validación real.

El directorio POSIX debe pertenecer al usuario efectivo, tener modo `0700` o más
restrictivo, no ser un symlink y permanecer estable durante el lease. La identidad
`(device, inode)` se comprueba después de adquirir y mediante `validate()`;
`is_acquired` deja de afirmar posesión si el descriptor o el ancla cambian. Un
hijo creado con `fork` cierra sus copias heredadas mediante `register_at_fork` y
reinicia su estado local sin liberar prematuramente el lock del padre. Un
descriptor cerrado o reutilizado produce error explícito de liberación y limpia
el estado interno.

La ruta se suministra explícitamente al construir el lease y debe ubicarse en un
directorio de estado local aprobado, fuera de `data/`, `config/` y directorios
sincronizados. Un proceso con permisos suficientes todavía puede renombrar o
reemplazar el propio directorio: esto invalida el lease y queda fuera del modelo
cooperativo; no se afirma resistencia adversarial absoluta y el futuro
coordinador deberá llamar `validate()` dentro de la sección crítica. O-01
permanece completamente de solo lectura y no adquiere el lease. El coordinador
local de decisión ya lo adquiere antes de planificar y revalida el snapshot; el
futuro ejecutor deberá conservarlo durante las escrituras exclusivas, repetir las
precondiciones inmediatamente antes de cada efecto y liberarlo también ante
excepciones. Esta exclusión es cooperativa: no detiene escritores
heredados que ignoren el contrato y no sustituye las validaciones de ruta,
identidad, preimagen, atomicidad o idempotencia existentes.

El siguiente incremento de O-02 añade `automation/workflow_coordinator.py` sin
conectar todavía ningún escritor. `coordinate_workflow_cycle()` exige una ruta
de lease explícitamente confirmada por el operador, rechaza ubicaciones conocidas
como sincronizadas o dentro de las raíces `data`/`config`, adquiere y valida el
lease antes de planificar, ejecuta dos reconstrucciones completas de O-01 y
compara tanto los planes como una huella SHA-256 de sus precondiciones de archivos.
La huella detecta cambios entre ciclos y durante la revalidación, pero nunca
sustituye los inspectores: cada pasada vuelve a validar entradas, revisiones,
aplicaciones, evidencia, hashes y preimágenes que correspondan a la acción.

La remediación O02-QA-04 hace esa huella fail-closed ante enlaces simbólicos.
Antes de la primera llamada a O-01, entre las dos planificaciones y después de la
segunda, el coordinador recorre `statements`, todos los runs observables, review
cases, feedback y perfiles sin resolver symlinks. Rechaza raíces, archivos,
directorios o componentes intermedios simbólicos —incluidos enlaces rotos,
circulares o externos— y devuelve `VALIDATION_FAILED` sin plan accionable. Los
archivos ordinarios se abren con `O_NOFOLLOW` cuando está disponible y se compara
su identidad y metadatos antes, durante y después de leerlos; los directorios se
comprueban antes y después de su recorrido ordenado. Device/inode solo sirven
para detectar sustituciones durante una inspección y no entran en el digest, por
lo que dos estados ordinarios con iguales rutas relativas y bytes conservan la
misma huella.

La remediación O02-QA-05 elimina la ambigüedad de concatenación en esa huella.
El snapshot usa un dominio versionado y registros binarios con prefijo fijo,
tipo, longitud de ruta, ruta, longitud de payload y payload. Para archivos el
payload son exactamente los bytes leídos de forma streaming; directorios,
ausencias y otros tipos tienen códigos distintos. Así, contenido arbitrario —
incluido NUL o bytes que imiten encabezados— no puede confundirse con otra
entrada. Si dos reconstrucciones difieren, el coordinador devuelve
`VALIDATION_FAILED`, `authorization_required=false` y ningún plan accionable.

Estas comprobaciones reducen las ventanas TOCTOU dentro del modelo cooperativo,
pero no prometen resistencia absoluta ante un actor no cooperante que pueda
sustituir y restaurar rutas entre dos observaciones sin dejar un cambio detectable.
El lease y las revalidaciones siguen siendo obligatorios antes de futuros efectos.

El resultado tipado distingue una acción vigente aún no autorizada, completitud,
plan previo obsoleto, contención, bloqueo de inspectores y fallo de revalidación.
El coordinador libera el lease en todos los caminos y no publica conjuntos, no
recupera ni finaliza aplicaciones y no modifica perfiles, historiales o evidencia.

### Ejecución sintética protegida como extensión de O-02

`automation/synthetic_executor.py` demuestra el protocolo de ejecución sin
conectar escritores operacionales. `coordinate_workflow_under_lease()` reutiliza
la doble reconstrucción y la huella del coordinador, pero deja adquisición y
liberación en manos del llamador. `confirm_workflow_expectation_under_lease()`
repite plan, huella y validación del ancla inmediatamente antes del efecto. La API
original `coordinate_workflow_cycle()` conserva su contrato de solo decisión y
nunca debe usarse como autorización después de liberar el lease.

El único efecto admitido es crear con `O_EXCL` y `O_NOFOLLOW` un archivo nuevo
bajo una raíz temporal explícitamente confirmada y externa al contenedor común
del workflow. La preparación captura las identidades de raíz y directorio padre y
una preimagen ausente; la ejecución vuelve a comprobarlas dentro del mismo lease,
abre el padre por descriptor, no sobrescribe destinos y devuelve evidencia en
memoria con plan, huella, propietario, ruta, tamaño y SHA-256. No se afirma
atomicidad general, idempotencia, recuperación ni autorización para
`review_storage`, `adjudication_storage` o `finalization`.

La evidencia de `COMPLETED` usa el SHA-256 de una lectura completa realizada
mediante el mismo descriptor del archivo creado y solo se emite si esos bytes
coinciden con el payload solicitado, mientras identidad y tamaño permanecen
válidos. El contrato de creación exige preimagen ausente y rechaza cualquier
hash de preimagen no nulo, incluso en operaciones construidas manualmente. Un
fallo posterior a `O_EXCL` puede dejar un archivo parcial: se informa como
`EXECUTION_ERROR` sin evidencia exitosa y no se afirma ausencia de efectos. La
prueba tampoco promete durabilidad ante fallos eléctricos ni integridad después
de liberar el lease; el futuro escritor operacional deberá definir recuperación
e idempotencia propias sin interpretar este resultado como transacción.

Los resultados distinguen `COMPLETED`, `CONTENDED`, `STALE_PLAN`,
`VALIDATION_FAILED`, `OPERATION_REJECTED` y `EXECUTION_ERROR`. Un fallo no reintenta
automáticamente ni convierte la evidencia parcial en autorización. El lease se
libera también ante excepciones inesperadas; estas últimas se propagan para no
ocultar errores de programación.

### Escritura segura de revisiones implementada en 2.2.4-A

`automation/review/review_storage.py` acepta exclusivamente `CompleteReviewResult` y vuelve a validar tanto sus objetos tipados como los siete JSON serializados. Rechaza `TaskResult`, conjuntos parciales, modelos duplicados, intentos mezclados, destinos cuyo nombre no coincide con `statement_index` y cualquier índice histórico `000`–`009` antes de crear archivos.

Cada intento se prepara y valida en un directorio temporal dentro de `statement_NNN/.review_sets/<revisor>/`. Después se publica como conjunto inmutable en `attempts/<set_id>/`, donde `set_id` es el SHA-256 estable de `task_id` y `attempt_id`. El conjunto contiene:

- `<revisor>_review.json`;
- `histories/gemma_history.json`, `histories/mistral_history.json` y `histories/llama_history.json`;
- `profile_proposals/gemma_profile_proposal.json`, `profile_proposals/mistral_profile_proposal.json` y `profile_proposals/llama_profile_proposal.json`;
- `manifest.json`, con identidad, rutas y hashes canónicos de los siete artefactos. Los manifiestos v2 añaden `attempt_number`, `run_id` y `task_deduplication_key` como evidencia mínima de continuidad; `task_id`, `attempt_id`, statement y revisor permanecen en el contrato existente.

`active.json` bajo el directorio del revisor es la única referencia que confirma una versión. Se escribe mediante un archivo temporal, `fsync` y `os.replace()` después de preparar, releer y validar todo el conjunto. Esta es atomicidad de la referencia, no una transacción multiarchivo: un conjunto huérfano o una preparación interrumpida se ignora, mientras la referencia anterior continúa definiendo el conjunto válido. El archivo fijo `statement_NNN/<revisor>_review.json` se mantiene como proyección de compatibilidad y nunca sustituye la validación de `active.json` más manifiesto y hashes.

Si una interrupción ocurre después de proyectar el nombre fijo pero antes de
activar el conjunto, el inspector solo clasifica esa proyección como intento
incompleto recuperable cuando sus bytes coinciden con la revisión principal de
un conjunto inmutable cuyos siete artefactos, manifiesto, identidad y nombre de
set se verifican íntegramente. No activa ni elimina el huérfano. Una proyección
sin esa evidencia continúa como `UNVERIFIED`, preservando la recuperación
conservadora y la compatibilidad histórica. Los conjuntos activos v1 siguen
siendo legibles, pero una proyección huérfana v1 no habilita reintento automático
porque carece de número de intento, run y huella lógica verificables.

Una repetición con los mismos `task_id` y `attempt_id` y contenido idéntico devuelve `UNCHANGED`; la misma identidad con contenido diferente se rechaza. Un intento nuevo crea otro conjunto inmutable y cambia íntegramente la referencia activa, sin tocar los conjuntos de los otros revisores. El inspector prioriza siempre la versión confirmada sobre rutas provisionales proporcionadas por el llamador, rechaza referencias o hashes inconsistentes y continúa siendo de solo lectura.

La verificación de integración posterior cerró 2.2.4-A: el único consumidor de producción que lee revisiones es `result_inspector.py`, que usa `resolve_confirmed_review_set()` antes de recurrir al nombre fijo heredado; `completeness.py` consume ese inspector y `src/review_data_loader.py` no lee revisiones. Las pruebas cubren interrupciones antes y después de `active.json`, proyecciones obsoletas, conjuntos no confirmados o alterados y lectura histórica sin referencia. Por ello una proyección fija no puede prevalecer sobre un conjunto activo válido ni servir de fallback silencioso cuando la referencia activa existe pero es inválida.

### Primer flujo vertical sintético de revisión

`automation/review/vertical_flow.py` integra por primera vez selección, carga,
ejecución y publicación para un único revisor de `statement_010` sin conectar
plataformas. `run_synthetic_reviewer_once()` adquiere el `WorkflowLease`, obtiene
un plan estable mediante O-02, carga el run inequívoco con
`inspect_review_inputs()`, elige el primer revisor pendiente en el orden canónico
y crea `Task` y `ExecutionAttempt` usando los contratos de 2.1.

El puerto sustituible `ReviewerAdapter` devuelve directamente el
`CompleteReviewResult` vigente. `DeterministicFakeReviewerAdapter` es una
implementación local que genera exactamente una revisión principal, tres
historiales provisionales y tres propuestas de perfil; no define un formato de
transporte ni anticipa los builders o parsers generales de O-03. Antes de
publicar se reconstruye y valida de nuevo el contrato completo, se repiten plan,
huella y ancla mediante `confirm_workflow_expectation_under_lease()`, y se llama
al escritor inmutable existente `publish_complete_review()` dentro del mismo
lease.

Después del punto de confirmación `active.json`, el flujo reinspecciona el set
con `inspect_reviewer_results()` y vuelve a ejecutar O-01. El resultado es
`COMPLETED` cuando el revisor publicado queda `COMPLETE` y O-01 solicita los
revisores restantes, o cuando O-01 devuelve `RUN_ADJUDICATIONS` y la misma
reinspección confirma los tres revisores completos. Esta última rama solo
informa que la barrera está satisfecha; no ejecuta adjudicaciones. Fallos del adaptador, seis
artefactos, identidad contradictoria o publicación fallida dejan la tarea en
`RETRY_PENDING` y nunca se convierten en éxito. Un replay del mismo intento ya
confirmado devuelve `UNCHANGED` sin llamar de nuevo al adaptador ni crear otro
set. Un reintento válido conserva `task_id`, incrementa `attempt_number` y usa un
`attempt_id` nuevo. Si el proceso anterior terminó, el flujo obtiene esos valores
del último huérfano v2 inequívoco y comprueba su `run_id` y clave de deduplicación
contra la tarea que volvería a construir. Secuencias discontinuas, identidades
competidoras, manifiestos insuficientes o contradicciones bloquean antes de
invocar el adaptador.

Este incremento permanece deliberadamente limitado a un árbol sintético
confirmado, `statement_010` y un revisor por llamada. No mantiene una base o
registro paralelo de tareas: la continuidad mínima queda dentro del manifiesto
del conjunto inmutable. No aplica
adjudicaciones y no escribe perfiles o historiales acumulados.

### Preparación y persistencia sintética de la adjudicación final de reporte

`automation/review/synthetic_adjudication.py` compone el flujo vertical de
revisores hasta confirmar los tres conjuntos activos y vuelve a exigir que O-01
devuelva `RUN_ADJUDICATIONS` bajo el mismo protocolo de lease y revalidación de
O-02. `AdjudicationRequest` reúne la declaración, los tres outputs alineados, el
prompt y su SHA-256, los tres `CompleteReviewResult` —21 artefactos— y los tres
`ConfirmedReviewSet`. Para cada conjunto exige coincidencia de statement,
revisor, `task_id`, `attempt_id` y `run_id` con el run seleccionado.

`DeterministicFakeAdjudicatorAdapter` produce exactamente el objeto definido por
`review_adjudication.md`. Antes de exponerlo, el flujo lo clona como JSON y aplica
`validate_adjudication()`; después vuelve a comprobar plan, huella y lease. El
resultado `PreparedAdjudication` conserva en memoria el hash canónico de la
adjudicación, el prompt, el run y las identidades de los tres sets, y declara
explícitamente `persisted=false`.

La entrada histórica `run_synthetic_adjudication_once()` conserva esa semántica
de solo memoria. OLBM-31 añade
`run_synthetic_adjudication_and_persist_once()`: el adaptador falso produce un
bundle en memoria `1 + 3 + 3`, el parser descarta `ADJUDICATION_NOTES`, valida los
siete componentes y revalida O-02 bajo el mismo lease antes del primer efecto.
El bundle no crea un segundo formato persistido.

La confirmación reutiliza el manifiesto y las siete copias preparadas del
almacenamiento existente, pero fija `ADJUDICATION_COMMITTED` después de reemplazar
y verificar atómicamente solo `statement_NNN/adjudication.json`. Los tres
historiales y tres perfiles quedan como material preparado recuperable; sus
destinos en `config/feedback/` y `config/profiles/` no se modifican en OLBM-31.
Una repetición idéntica devuelve `UNCHANGED`; una adjudicación o bundle diferente
se rechaza. `inspect_adjudication_application()` reconoce el commit incluso si
el proceso cae después del reemplazo y antes de actualizar `status.json`.

OLBM-32 hace recuperable esa frontera entre procesos. O-01 distingue una
aplicación `PREPARED` de `ADJUDICATION_COMMITTED` sin inferir completitud global;
una preparación huérfana o varias aplicaciones candidatas bloquean. Bajo el
`WorkflowLease` de O-02, el ejecutor vuelve a leer declaración, outputs, tres
sets activos y la aplicación. Compara `statement_index`, `run_id`, tareas e
intentos con el manifiesto verificado. Para `PREPARED` reconstruye el bundle y
usa `commit_adjudication_bundle()`; para `ADJUDICATION_COMMITTED` devuelve
`UNCHANGED` sin ejecutar el adjudicador ni escribir. En ambos casos perfiles e
historiales operacionales siguen pendientes de OLBM-33.

### Aplicación segura de adjudicaciones implementada en 2.2.4-B

`automation/review/adjudication_storage.py` recibe resultados ya adjudicados; no ejecuta prompts ni modelos. Antes de escribir valida un `adjudication.json` completo, exactamente un historial estricto por Gemma, Mistral y Llama y exactamente un perfil acumulado completo por modelo. Los perfiles conservan los conteos, últimos outputs y metadatos entregados: la persistencia no calcula ni incrementa valores. La puerta semántica de OLBM-31 compara cada candidato con su preimagen y el índice mono-statement: exige que `outputs_reviewed` incorpore exactamente el output actual, que la versión avance una vez y que un patrón gane como máximo una evidencia del índice actual, sin observaciones futuras. Cualquier índice `000`–`009` se rechaza antes de preparar una operación.

Cada aplicación usa el esquema 2 y se identifica por un hash canónico de statement, adjudicación, tres historiales, tres perfiles, los siete hashes de preimagen y, cuando se proporcionan, `run_id` e identidades de las revisiones. Se prepara en `statement_NNN/.adjudication_applications/<application_id>/`, con copias inmutables de los siete destinos, un `manifest.json` que registra hashes de origen y destino y un `status.json` atómico con `PREPARED`, `APPLYING`, `ADJUDICATION_COMMITTED` o `APPLIED`. El nombre del directorio compromete las preimágenes independientemente de los campos mutables del manifiesto: cambiar un `source_sha256` sin cambiar esa identidad produce `INVALID`. Una repetición localiza la aplicación previa por los resultados objetivo y conserva reanudación e idempotencia aunque algunos destinos ya hayan sido aplicados. Si existen conjuntos versionados de revisión, deben estar presentes y ser válidos para los tres revisores; las identidades suministradas deben coincidir con sus referencias activas.

Después de validar y preparar todo, cada destino se reemplaza individualmente y se relee por hash. Antes de cada reemplazo se exige que el archivo conserve su hash de origen o ya tenga el hash objetivo. La misma aplicación completa devuelve `UNCHANGED`; una ejecución interrumpida de la misma identidad puede continuar desde los destinos que aún conservan la preimagen; historiales duplicados o conflictivos, una adjudicación distinta y una segunda identidad de aplicación para el mismo statement se rechazan.

No existe atomicidad multiarchivo: `inspect_adjudication_application()` compara material preparado, estado declarado y siete destinos y distingue `PREPARED`, `ADJUDICATION_COMMITTED`, `INTERRUPTED`, `APPLIED` e `INVALID`. El manifiesto expone el hash de adjudicación, hashes de historiales, hashes y metadatos de perfiles, `run_id` e intentos de revisión que 2.2.4-C utiliza para construir la evidencia de 2.2.3. Un commit OLBM-31 no crea `application_evidence.json`, no confirma limpieza y no declara el statement `COMPLETE`.

Las rutas de `manifest.json`, `status.json` y de cada artefacto bajo `prepared/` se confinan y se rechazan si ellas o alguno de sus componentes son enlaces simbólicos; la guarda se repite antes de lecturas y operaciones sensibles. Esto reduce sustituciones accidentales o inconsistentes, pero no aporta autenticidad criptográfica contra un actor que pueda reescribir todos los archivos y referencias locales ni elimina por completo carreras TOCTOU sin primitivas por descriptor.

La continuidad de perfiles identifica cada patrón por su ruta de categoría y texto. El mismo texto puede existir legítimamente en categorías distintas, pero los contadores de una categoría no pueden ocultar ni sobrescribir la comparación de otra; la política existente de permitir eliminaciones adjudicadas se conserva.

### Recuperación, evidencia y limpieza implementadas en 2.2.4-C

`resume_adjudication_application()` reconstruye la operación exclusivamente desde el manifiesto y las copias preparadas de B. Para cada destino acepta solo la preimagen registrada o el resultado objetivo ya aplicado: omite este último y reemplaza únicamente la preimagen pendiente. Un contenido distinto convierte la operación en no recuperable, lo que impide que una aplicación antigua restaure perfiles o historiales después de que un statement posterior los haya actualizado.

`automation/review/finalization.py` coordina la finalización. Exige una aplicación `APPLIED`, `run_id`, tres conjuntos activos válidos y las mismas identidades de tarea/intento registradas al preparar la adjudicación. Después limpia únicamente rutas sueltas explícitas dentro del directorio del statement. Un `cleanup_manifest.json` conserva rutas y hashes y `cleanup_status.json` registra `PREPARED`, `APPLYING` o `CLEANED`; la repetición omite archivos ya eliminados y bloquea los que hayan cambiado. Los conjuntos bajo `.review_sets/`, las aplicaciones bajo `.adjudication_applications/`, revisiones permanentes, adjudicación y evidencia están protegidos y no son candidatos de limpieza.

Solo después de recuperar, verificar, limpiar y volver a comprobar los destinos se publica atómicamente la evidencia versión 2 consumida por `completeness.py`. Contiene statement, run, identidades de los tres revisores, hash canónico de adjudicación, identificador y hashes de los tres historiales, identificador, hashes, metadatos y el perfil efectivamente aplicado para cada modelo, además de `temporary_artifacts_cleaned=true`. El perfil incluido se lee del material inmutable preparado por la aplicación; no se reconstruye ni se inventa. El lector conserva compatibilidad con evidencia versión 1 mientras el perfil global siga coincidiendo exactamente con ella. Se corrigió en B el hash canónico de objetos para usar el mismo JSON sin salto final que 2.2.3; los hashes de archivos preparados continúan calculándose sobre bytes completos.

La evidencia no sustituye los archivos: 2.2.3 vuelve a validar entradas, revisiones activas, adjudicación, historiales y perfiles. Para el statement más reciente exige coincidencia exacta; cuando el perfil global ya avanzó, la versión 2 verifica el hash y esquema del perfil aplicado conservado y exige que el perfil vigente sea un sucesor no regresivo mediante las mismas reglas compartidas que usa el escritor. Un statement sintético nuevo alcanza `COMPLETE` solo después de la finalización completa; una aplicación parcial queda `INCOMPLETE` o `UNVERIFIED`. Los manifiestos y copias preparadas se conservan para auditoría y recuperación; C no limpia conjuntos activos ni coordina escritores concurrentes.

Campos mínimos sugeridos para un resultado: `run_id`, `job_id`, `case_id`, `provider`, `model_label`, `prompt_version`, `profile_version`, `iteration`, `status`, `created_at`, `completed_at`, `response_text`, `response_source`, `error_code`, `provenance`. No asumir que la UI revela una versión exacta del modelo. Los tipos finales y sus validadores de archivo dependen de la auditoría inicial.

## Estados
La etapa del pipeline se representa mediante el tipo de tarea (`generation`,
`evaluation`, `feedback`, `profile_optimization` o `dataset_preparation`), no
mediante estados distintos. Cada tarea usa el ciclo de vida común `PENDING`,
`RUNNING`, `RETRY_PENDING`, `WAITING_REVIEW`, `COMPLETED`, `FAILED` y
`CANCELLED`. `COMPLETED`, `FAILED` y `CANCELLED` son terminales. Un fallo que
todavía admite reintento usa `RETRY_PENDING`; los intentos pertenecen a la misma
tarea y no representan una repetición experimental nueva. Las transiciones se
validan en `automation/state_machine.py`; su representación y recuperación
mediante archivos quedan pendientes de un entregable posterior a la aprobación
de 2.1.

Para una revisión principal, `TaskType.EVALUATION` se conserva y se especializa
con `ReviewOperation.MAIN_REVIEW`, un `statement_index` no negativo y un
`Reviewer` limitado a `chatgpt`, `gemini` o `grok`. Los tres elementos forman
parte de la clave de deduplicación; `task_id` e intentos quedan excluidos, de
modo que un reintento conserva la identidad lógica y recibe un nuevo
`attempt_id`.

`CompleteReviewResult` es el contrato atómico de una ejecución de revisor:
contiene una revisión principal válida, tres historiales provisionales y tres
propuestas de perfil para Gemma, Mistral y Llama. Cada artefacto conserva
`statement_index`, revisor e `attempt_id`; no se admite mezclar intentos ni
convertir el conjunto a un `TaskResult` exitoso antes de validar las siete
salidas. `TaskResult` continúa siendo el envoltorio genérico para los demás
tipos de tarea.

En revisiones, `RETRY_PENDING` indica que la validación automática exige repetir
la ejecución completa del revisor. `WAITING_REVIEW` queda reservado para
intervención humana o externa. El valor de datos
`decision.status = needs_human_review` no cambia por sí mismo el estado de la
tarea.

`dataset_preparation` se conserva únicamente como valor genérico preexistente
por compatibilidad contractual. No forma parte del flujo activo de revisión y
adjudicación ni autoriza trabajo sobre datasets.

## Restricciones técnicas
- Async Playwright, `headless=False` para diagnóstico/autenticación manual inicial; perfiles aislados por proveedor y nunca versionados.
- `headless` y timeout de lanzamiento configurables. La raíz de perfiles se obtiene de `POLICE_BENCH_BROWSER_PROFILE_ROOT` o de un directorio de estado local apropiado para el sistema operativo.
- JSON y JSONL son la fuente de verdad exclusiva para estados, intentos y resultados del flujo de revisión y adjudicación. El estado se reconstruye inspeccionando y validando los archivos reales; no se usa SQL ni una base de datos.
- Las escrituras deben usar un archivo temporal en el mismo sistema de archivos, validación previa y reemplazo atómico. Una recuperación conserva los conjuntos válidos de otros revisores y solo sustituye el conjunto completo del revisor afectado.
- La idempotencia debe impedir aplicaciones duplicadas y diferenciar completitud de tarea, completitud del revisor y completitud global del statement.
- Tareas idempotentes: clave compuesta por caso, proveedor, versión de prompt, versión de perfil, iteración y número explícito de repetición experimental.
- Capabilities de adaptador: `open_site`, `check_session`, `prepare_prompt`, `submit_prompt`, `read_response`, `export_result`. Cada capacidad puede ser `ALLOWED`, `MANUAL` o `DISABLED` según revisión verificable.
- Separar los tests: unitarios con mocks, integración local de archivos en directorios temporales y smoke web interactivo sujeto a permisos.
- Conservar metadatos de cambios, razones de fallos, tiempos y evidencias sanitizadas.
- Nunca desarrollar tácticas anti-detección, suplantación para evadir políticas, rotación de identidades, CAPTCHA-bypass o scraping restringido.

## Interfaces de módulos
- `BrowserManager`: abre/cierra contextos y garantiza exclusividad del perfil de cada servicio.
- `BaseWebAdapter`: capacidades, sesión, navegación y comportamiento autorizado. No decide la rúbrica.
- `StatementSelector`: inspecciona `data/finetuning/review_cases/`, retoma el mayor índice incompleto o selecciona el siguiente índice disponible.
- `InputLoader`: recopila declaración, outputs, perfiles, historiales y prompt, y exige alineación por `statement_index`.
- `CompletenessChecker`: valida conjuntos completos por revisor y separa completitud de tarea de completitud del statement.
- `FileRepository`: reconstruye estado desde JSON/JSONL y realiza lecturas y escrituras atómicas sin una base de datos.
- `Orchestrator`: coordina revisiones, reintentos completos, barrera y adjudicaciones; no declara éxito antes de verificar los archivos aplicados.

## Seguridad y calidad
`.gitignore` debe incluir `.browser_profiles/`, `.env`, artefactos de sesión y trazas sensibles, sin ignorar indebidamente los JSON/JSONL que el proyecto ya versiona. Priorizar datos sintéticos. El acceso web requiere verificación de condiciones por servicio; no inferir autorización por posibilidad técnica.

### Aplicación sintética incremental de adjudicaciones — OLBM-33

`automation/review/synthetic_application.py` añade una entrada limitada a un
statement y al `application_id` confirmado. Todas las rutas, incluido el lease,
deben quedar bajo un árbol temporal explícitamente confirmado, separado del
repositorio. No recibe adaptadores ni ejecuta revisores, adjudicadores o rangos.
Los tres perfiles y tres historiales son los de Gemma, Mistral y Llama; las
identidades ChatGPT/Gemini/Grok se conservan como procedencia de revisores.

Bajo el mismo `WorkflowLease`, reconstruye O-01/O-02, valida inputs completos,
los tres sets activos y su run mediante la barrera OLBM-36, el hash final de la
adjudicación y la identidad/preimágenes/objetivos existentes. Revalida los perfiles
pendientes con `validate_profile_adjudication_transition()`, comprueba historiales
únicos sin registros futuros y conservación de su acumulado. Repite plan y huella
antes de llamar `finalize_adjudication_application()`. Este reutiliza
`resume_adjudication_application()`, que ahora admite la fase
`ADJUDICATION_COMMITTED`, omite objetivos ya escritos y reemplaza solo preimágenes
exactas. No recalcula contadores ni genera otra aplicación.

Cada destino conserva su reemplazo atómico individual; no hay transacción de
seis archivos. Un corte tras los seis reemplazos sigue `INTERRUPTED` si falta
confirmar `APPLIED`. Un `APPLIED` sin evidencia sigue pendiente de finalización.
La publicación atómica de evidencia v2, limpieza confirmada y reinspección global
permiten devolver `APPLIED` con `completion=COMPLETE`. El helper de solo lectura
`verify_application_evidence()` reutiliza `_evidence()` para comprobar identidad,
hashes, snapshots y confirmación de limpieza; no introduce otro formato.
Para statements activos, evidencia v2 sin `.adjudication_applications` verificable
queda `INCOMPLETE`; la lectura de evidencia sin ese almacén solo corresponde al
contrato v1. El cierre histórico 000–009 se decide antes de ambas rutas (D-038).

Para soportar 010→011→012 y sus interrupciones, el inspector puede recibir
`application_evidence_path`. Las aplicaciones anteriores finalizadas se validan
contra su evidencia y una cadena contigua de aplicaciones sucesoras, cuyos seis
hashes de origen coinciden con los objetivos anteriores. Se verifican los
historiales que añaden un registro y las transiciones de perfiles con el validador
mono-statement existente. El último eslabón puede estar parcialmente aplicado si
cada destino conserva su preimagen u objetivo. Las cadenas íntegramente legadas
sin metadatos de run v2 mantienen su contrato previo de lectura no regresiva,
pero no autorizan la entrada nueva, que exige identidad v2 verificable. O-01 utiliza esta inspección de
solo lectura; el escritor mantiene la comprobación estricta de destinos actuales
y nunca restaura una aplicación antigua sobre un sucesor. Cuando existe almacén
interno, `inspect_statement_completeness()` exige también que confirme la
evidencia y los destinos; la compatibilidad de lectura heredada se mantiene.

Un replay de la misma identidad con evidencia v2 válida devuelve `UNCHANGED`
sin escrituras. Una preparación sin commit, input/revisor faltante, run/intento
ajeno, preimagen alterada, evidencia corrupta o tercer contenido bloquea. No se
reconstruyen preimágenes perdidas ni se mezclan intentos. Las escrituras y pruebas
se restringen a fixtures temporales. D-037 y
`docs/olbm-33-synthetic-application.md` documentan decisiones, confirmaciones,
recuperación, evidencia y checklist pendiente de QA. OLBM-34 conserva el driver
de rangos; fase 2 sigue `IN_PROGRESS` y remoto `REMOTE_NOT_CONFIGURED`.

### Rango inclusivo sintético — OLBM-34

`OrchestratorConfig.start_statement/end_statement` son límites opcionales,
proporcionados juntos, enteros >=10 y con inicio <= fin. Sin ellos continúa la
planificación anterior. Con ellos O-01 inspecciona todos los índices numéricos
del rango desde el inicio de la planificación y elige el menor no `COMPLETE`,
incluidos directorios todavía ausentes cuyo preflight debe validar las entradas.
No calcula un plan global para filtrarlo ni deja que una recuperación posterior
desplace al menor incompleto. Cada `StatementPlan` conserva ambos límites y O-02
los incluye como payload de un registro encuadrado de su huella. Cambiar inicio o
fin invalida expectativas aun cuando el índice/acción siguiente coincidan.

`automation/review/synthetic_range.py` compone el coordinador y los tres
ejecutores aprobados de revisión, adjudicación persistida y aplicación. Requiere
una raíz temporal dedicada confirmada fuera del repositorio y confinamiento de
rutas, prompt, lease y run. El flujo vertical admite índices adicionales solo
con límites explícitos; sin límites se conserva su restricción anterior a 010.
Los ejecutores de adjudicación/aplicación reciben opcionalmente la expectativa
O-02. Cada etapa vuelve a adquirir el mismo `WorkflowLease`, reconstruye y
revalida esa expectativa y mantiene la posesión durante sus efectos. Liberar el
lease entre etapas requiere una nueva revalidación, no autoriza escrituras.

No hay cursor persistido, coordinador alternativo, otro lock ni otro almacén.
Después de cada etapa se vuelve a planificar desde JSON/JSONL; aplicación y
finalización siguen siendo `start=end=statement_actual` para los seis destinos
Gemma/Mistral/Llama. Solo completitud global verificada con evidencia v2 y
limpieza permite seleccionar el siguiente índice. Termina al verificar todos los
índices del rango y se detiene ante su primer bloqueo, sin procesar `end+1`.
Reutiliza el cargador autoritativo, siete artefactos por revisor, identidades y
recuperación OLBM-32/33. No altera QA33-01 ni el fallback limitado a evidencia v1.
D-039 e informe `docs/olbm-34-synthetic-range.md` documentan alcance y pruebas.
OLBM-35 conserva adaptadores reales; fase 2 sigue `IN_PROGRESS`.
