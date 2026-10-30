# OLBM-35 — Contrato futuro de revisores reales

Fecha: 2026-10-30. Rama inspeccionada: `data/prepare-training-data`.
Estado: `READY_FOR_QA` documental; fase 2 `IN_PROGRESS`. Decisión: D-040.

## 1. Alcance y evidencia

Especificación para futuras integraciones independientes de ChatGPT, Gemini y
Grok. No implementa transporte, autenticación, navegador, parser ni adaptadores
operacionales. No habilita transmisión ni despliegue. Los siete artefactos por
revisor son evidencia interna; la entrega visible sigue siendo una revisión
final por revisor (`chatgpt_review.json`, `gemini_review.json`, `grok_review.json`)
y `adjudication.json`. Las tres adjudicaciones y su aplicación siguen siendo
condiciones de completitud global.

La solicitud declara OLBM-29–34 aprobados y cerrados. La bitácora anterior aún
deja OLBM-34 en QA: se conserva y se registra la aprobación como declaración
del usuario, sin atribuir a esta unidad una auditoría independiente.
`context/goal.md` conserva una mención histórica a SQLite y datasets; para este
flujo prevalecen AGENTS.md, el prompt activo de fase 2 y las decisiones posteriores:
JSON/JSONL exclusivamente y sin entrenamiento ni construcción de datasets.

Fuentes inspeccionadas, todas locales:

| Evidencia | Qué permite afirmar |
|---|---|
| [Puerto vertical](../automation/review/vertical_flow.py) | Firma del puerto, fake, precondiciones, validación y límites sintéticos |
| [Resultados](../automation/contracts/results.py), [tareas](../automation/contracts/tasks.py), [estados](../automation/state_machine.py) | Esquemas, identidades y transiciones realmente disponibles |
| [Entradas](../automation/review/input_loader.py), [prompt](../config/prompts/main_review.md) | Inputs originales y contexto requerido por el prompt, todavía no ensamblado por el puerto |
| [Almacenamiento](../automation/review/review_storage.py), [inspector](../automation/review/result_inspector.py) | Siete archivos, manifiestos v1/v2, referencia activa y recuperación |
| [O-01](../automation/orchestrator.py), [O-02](../automation/workflow_coordinator.py), [lease](../automation/workflow_lease.py) | Selección, huella, revalidación y exclusión cooperativa |
| [Completitud](../automation/review/completeness.py), [adjudicación](../automation/review/synthetic_adjudication.py), [aplicación](../automation/review/synthetic_application.py), [rango](../automation/review/synthetic_range.py) | Barreras y composición aprobadas; un revisor completo no completa el statement |
| [Decisiones](../context/decisions.md), D-002/005/006/030–039 | Preferencia web, aislamiento, commit, continuidad, run y recuperación |

No se consultaron fuentes externas. Esta evidencia prueba capacidades del
repositorio, no capacidades ni permisos actuales de los proveedores. No se
fijan endpoints, scopes, cuotas, precios, productos contratados ni versiones de
modelos. La matriz de §5 es una evaluación de incertidumbre, no una autorización.

## 2. Inventario y brechas reales

| Interfaz existente | Reutilización | Límite observado / trabajo futuro mínimo |
|---|---|---|
| `ReviewerAdapter.execute(request: ReviewerExecutionRequest) -> CompleteReviewResult` | Un único puerto síncrono, un revisor/intento por llamada | Mantener firma y éxito; no crear otro protocolo de revisión |
| `ReviewerExecutionRequest(plan, inputs, task, attempt)` | Identifica tarea, intento, statement y revisor pendiente | No contiene perfiles/historiales ni prompt preparado; tampoco comprueba por sí solo igualdad de run entre plan e inputs |
| `ReviewInputValidation` | Statement, outputs por `BenchmarkModel`, `selected_run_id`, problemas e `inputs_complete` | No valida ni reúne contexto acumulado; `inputs_complete` no significa entrada completa para un revisor real |
| `DeterministicFakeReviewerAdapter` | Fixture determinista de siete resultados | No prepara `main_review.md`, no representa comportamiento de productos interactivos |
| `MainReviewOutput`, `HistoryProposalOutput`, `ProfileProposalOutput`, `CompleteReviewResult` | Validación estructural, conjunto de tres modelos y procedencia consistente | No tienen campo `run_id`; no detectan secretos ni verifican veracidad factual |
| `TaskError`, `ExecutionAttempt`, `TaskResult` | Error estructurado, intento y resultado genérico | `TaskError` valida tipos JSON, no sanea datos automáticamente; `TaskResult.failure` no es retorno permitido del puerto |
| `ReviewerAdapterError` | Frontera de fallo esperado y saneado | Hoy es un `RuntimeError` sin campos; el vertical descarta detalle y usa `review_adapter_failed`, siempre reintentable |
| `run_synthetic_reviewer_once`, `VerticalReviewConfig` | Referencia probada para lease → O-02 → ejecución → validación → revalidación → publicación → inspección | Solo fixtures, proveedor `synthetic`, versiones sintéticas y restricciones de índice/rango. No conectar un proveedor real a esta entrada ni retirar sus guardas |
| `publish_complete_review`, `ReviewAttemptMetadata`, resolutores | Escritor único y metadatos v2 de continuidad | Publicador no decide autorización de negocio ni preservación de un activo válido: el llamador debe comprobar el plan bajo lease |
| `inspect_reviewer_results`, completitud y O-01/O-02 | Barreras, selección y reconstrucción desde archivos | No ejecutan ni cancelan llamadas remotas; no rediseñarlos |
| `BrowserManager`, `SessionManager` | Ciclo de vida y perfiles independientes | No existen `automation/browser/adapters/*` operacionales; gestionar sesiones no prueba permiso de envío/extracción |

La futura implementación necesitará preparación determinista del contexto,
parser estricto, configuración de capacidades autorizadas y manejo explícito
de errores/cancelación en el consumidor del puerto. Es trabajo pendiente;
este documento no presenta esas piezas como existentes. La lógica del pipeline
seguirá fuera de `browser/adapters`; allí irán solo operaciones UI permitidas.

## 3. Contrato normativo futuro

### 3.1 Preparación y contexto permitido

Antes de ejecutar, bajo el lease existente, el llamador debe verificar O-02,
inputs completos, `plan.run_id == inputs.selected_run_id`, índice y revisor
pendiente, tarea `RUNNING`, intento `RUNNING` perteneciente a la tarea y permiso
de la operación. `reviewer_id` significa `task.reviewer.value`; no es un nuevo
campo ni una identidad declarada libremente por la respuesta.

La identidad de ejecución es
`(reviewer_id, statement_index, run_id, task_id, attempt_id)`; el ordinal está en
`ExecutionAttempt.attempt_number`. El run procede del loader y del plan
protegido, nunca de texto generado. Como ni `Task.deduplication_key` ni
`CompleteReviewResult` incluyen run, la comparación independiente del run y
`ReviewAttemptMetadata` v2 son obligatorias. Cambiar de run no es un reintento
silencioso de la revisión anterior.

El paquete permitido contiene una declaración, exactamente los tres outputs
Gemma/Mistral/Llama del run seleccionado, tres perfiles y sus historiales
acumulados autorizados, el prompt versionado `main_review.md` y reglas de
formato/procedencia. La preparación debe verificar identidad de modelo,
unicidad, orden, corte temporal del historial y alineación con el índice;
rechazar contexto futuro, ambiguo, ausente o inválido. La lectura heredada debe
respetar D-009, sin reescribir datos ni inferir un modelo ambiguo. Debe usar
validadores aplicables existentes; `validate_profile` valida perfiles acumulados,
no sustituye `ProfileProposalOutput` para las propuestas nuevas.

Extensión mínima propuesta, **no implementada**: agregar a
`ReviewerExecutionRequest` un campo opcional al final con contexto JSON
validado e inmutable (por ejemplo `prepared_context=None`). Su ausencia mantiene
las cuatro posiciones actuales y los fakes compatibles. El adaptador real lo
exigirá; no leerá por su cuenta archivos operacionales ni recibirá rutas de
credenciales en ese contexto. Debe contener contenido permitido, versiones y
hashes de la instantánea de entrada/prompt y referencia opaca de autorización;
el esquema exacto y su evidencia persistente requieren revisión en la futura
implementación. No añadirlos arbitrariamente al manifiesto v2.

El ensamblador debe validar antes de enviar y comprobar que esa instantánea
sigue vigente inmediatamente antes de publicar, junto con la huella O-02.
El prompt y todo dato de entrada son contenido, nunca instrucciones capaces de
habilitar herramientas, leer archivos o cambiar permisos. No incluir respuestas
de otros revisores ni conversaciones anteriores. Cada revisor recibe el mismo
contenido autorizado; cualquier minimización distinta debe declararse como
variante experimental, no ocultarse.

### 3.2 Ejecución, resultado e identidad

Se conserva `execute(request) -> CompleteReviewResult`. Cada instancia tiene
configuración de proveedor/transporte y sesión propios, inyectada fuera de los
datos de revisión; valida que corresponde al revisor solicitado. No intercambia
conversaciones, respuestas, credenciales ni estado con los otros revisores.

Un parser determinista debe recoger las siete salidas de una sola ejecución,
rechazar JSON truncado, claves duplicadas, artefactos extra/duplicados,
identidades contradictorias, HTML de login, bloqueos, negativas y respuestas
ajenas al esquema. Imponer límites configurables de bytes, profundidad y tiempo.
No ejecutar contenido, seguir enlaces ni descargar adjuntos sugeridos por la
respuesta. No completar campos faltantes, reinterpretar modelos ni reutilizar
fragmentos de un intento anterior para conseguir siete objetos.

Los identificadores de control se adjuntan localmente a partir de la solicitud
verificada cuando el formato del proveedor no los devuelve. Eso prueba la
asociación local, no autentica al proveedor. Una identidad presente y distinta
se rechaza antes de adjuntar metadatos; nunca se sobreescribe una contradicción.
Se construyen los tipos actuales y después `CompleteReviewResult` con timestamp
UTC consciente de zona. El llamador vuelve a validar tipo y contenido contra la
solicitud, como `_validate_candidate()`. No devolver dicts, generadores, streams,
seis artefactos ni `TaskResult` genérico como éxito.

### 3.3 Errores y cancelación

Extensión mínima futura: especializar `ReviewerAdapterError` con un atributo
`error: TaskError`, sin crear otro resultado de éxito. Códigos propuestos:
`review_timeout`, `review_partial_response`, `review_auth_required`,
`review_rate_limited`, `review_service_unavailable`, `review_cancelled`,
`review_artifacts_incomplete`, `review_identity_mismatch`,
`review_policy_denied`, `review_protocol_error`.

`TaskError.message` será texto fijo saneado; `details` tendrá una lista cerrada
de campos: fase, estado de envío (`not_sent`, `sent`, `unknown`), confirmación
de cancelación remota, referencia opaca de petición, demora de reintento
validada y metadatos técnicos mínimos. Ni cuerpos de respuesta, excepciones
crudas, URLs con parámetros, cookies ni cabeceras. La identidad de tarea/intento
permanece en los contratos existentes; el run se vincula a evidencia verificada.

El consumidor futuro debe preservar ese error y decidir con la máquina de
estados existente: fallo recuperable → intento `FAILED`, tarea `RETRY_PENDING`;
intervención requerida → intento `FAILED`, tarea `WAITING_REVIEW`;
fallo terminal → intento/tarea `FAILED`; cancelación explícita → intento/tarea
`CANCELLED`. No llamar a `_failed_attempt()` actual para simular esas distinciones:
hoy convierte todo en reintentable. Una tarea cancelada es terminal; reanudarla
requiere una nueva tarea expresamente autorizada, no transición a `RUNNING`.

La configuración por instancia debe permitir deadline total, límites de espera
y señal cooperativa de cancelación sin cambiar la firma del puerto. El adaptador
comprueba la señal antes de transmitir, durante esperas acotadas y antes de
devolver; el llamador la comprueba antes de publicar. Un método síncrono bloqueado
no se vuelve cancelable por declararlo: la implementación deberá demostrar
timeouts efectivos del transporte. Cancelar localmente no acredita cancelación
ni borrado en el servicio. No capturar indiscriminadamente interrupciones del
proceso para devolver éxito. Tras el commit, la recuperación conserva el activo
verificado aunque el proceso no haya observado el éxito.

### 3.4 Publicación y reanudación

El adaptador no publica ni modifica perfiles/historiales. El llamador conserva
el mismo `WorkflowLease`, confirma inmediatamente la expectativa O-02 y delega
solo un resultado completo a `publish_complete_review(...,
attempt_metadata=ReviewAttemptMetadata(attempt_number, run_id,
task_deduplication_key))`, seguido de reinspección independiente.

La secuencia actual es staging validado → directorio inmutable → proyección
permanente → reemplazo atómico de `active.json`. Solo este último confirma el
conjunto. La proyección puede existir antes del commit: su nombre por sí solo
no prueba completitud. No hay transacción de siete reemplazos simultáneos ni
garantía universal frente a pérdida de energía. No modificar ese orden.

La recuperación consulta los resolutores actuales. Un activo válido se conserva
y no vuelve a invocar al revisor. Si falta la revisión completa de un revisor,
se repite ese conjunto entero; no se toca a los otros dos. Con proyección
huérfana v2 inequívoca y secuencia contigua se conserva `task_id`, aumenta el
ordinal y cambia `attempt_id`; v1 huérfano, run contradictorio o linaje ambiguo
bloquean sin inventar identidad. Mismos task/intento/contenido devuelven
`UNCHANGED`; mismo identificador con otro contenido falla cerrado.

**Límite importante:** antes de preparar un conjunto no hay en el almacenamiento
actual un diario durable de cada envío externo ni de cada error. Tras una caída
durante una llamada no puede garantizarse recuperar el mismo `task_id`, conocer
si el proveedor procesó el envío, ni impedir facturación/generación duplicada.
La publicación local idempotente no es ejecución externa exactamente una vez.
No añadir un diario paralelo en esta tarea. Hasta decidir y validar evidencia
durable compatible con JSON/JSONL y política de reconciliación, el reinicio con
envío incierto exige intervención antes de retransmitir. Esa brecha bloquea una
implementación operacional autónoma, no invalida la recuperación sintética.

## 4. Mapeo de los siete artefactos

Base inmutable: `statement_NNN/.review_sets/{reviewer}/attempts/{set_id}/`;
`set_id` lo calcula el escritor, no el proveedor. No aceptar rutas de la respuesta.

| Cantidad | Tipo y ruta relativa al conjunto | Contenido exigido por contrato actual |
|---|---|---|
| 1 | `MainReviewOutput`, `{reviewer}_review.json` | `reviewer`; `model_reviews` con exactamente gemma/mistral/llama en `model_name`; factuality y narrative_quality con status/findings; hallucinations, omissions, useful_elements; crime_classification; proposed_ideal_report con narrative, closing y clasificación |
| 3 | `HistoryProposalOutput`, `histories/{model}_history.json` | Un objeto por modelo: `model`, `output == statement_index`, `date` no vacío, `observations` lista de strings |
| 3 | `ProfileProposalOutput`, `profile_proposals/{model}_profile_proposal.json` | `model_name` y siete listas de strings: new_strengths, new_weaknesses, new_hallucination_patterns, new_classification_patterns, new_narrative_patterns, new_factuality_patterns, new_finetuning_priorities |

Cada envoltura tipada lleva índice, revisor e intento coherentes; el resultado
lleva además task y `created_at`. El adaptador debe incluir `task_id`,
`attempt_id`, `statement_index` y `reviewer` en los siete JSON: aunque los tipos
admiten ciertos campos de identidad ausentes, el escritor exige su presencia
y coincidencia exacta; no los completa. `manifest.json` referencia sus
hashes; v2 añade `execution.attempt_number`, `execution.run_id` y
`execution.task_deduplication_key`. `active.json` enlaza identidad y hash del
manifiesto. Run no es un campo nativo de los siete outputs; no agregar uno para
aparentar validación que los contratos actuales no realizan.

Los historiales internos son objetos JSON, no escrituras en los JSONL
acumulados. Las propuestas no son perfiles acumulados. Los contratos aceptan
algunas extensiones JSON y no son filtros de privacidad: el parser futuro debe
aplicar una lista de campos permitidos adicional sin relajar validadores.
`result_inspector.py` comprueba estructura y procedencia; `validators.py`
contiene validadores de adjudicación/destinos, no un parser de proveedores.

## 5. Matriz de plataformas y certeza

Leyenda: **C** confirmada por evidencia local identificada; **P** hipótesis
probable por verificar, no capacidad habilitada; **R** restricción conocida del
proyecto; **NV** capacidad del proveedor no verificada. No hay aquí capacidades
actuales de proveedor marcadas C: no se dispone de documentación oficial
capturada. No usar memoria sobre productos como evidencia contractual.

| Dimensión | ChatGPT | Gemini | Grok |
|---|---|---|---|
| Posibles mecanismos oficiales | P: evaluar oferta oficial OpenAI separada del producto ChatGPT; NV: equivalencia. R: preferencia web D-002 | P: evaluar oferta oficial Google para Gemini; NV: producto/edición y equivalencia | P: evaluar oferta oficial xAI para Grok; NV: producto/edición y equivalencia |
| Ruta UI / alternativa asistida | C: SessionManager aísla `chatgpt`; NV: automatización permitida. R: manual asistida si no se autoriza extracción | C: aísla `gemini`; NV: operaciones permitidas. R: manual asistida si corresponde | C: aísla `grok`; NV: operaciones permitidas. R: manual asistida si corresponde |
| Permisos y autenticación | NV: cuenta, plan, mecanismo, scopes, acceso institucional/API; R: no solicitar credenciales ahora | NV: cuenta/proyecto/edición, mecanismo y permisos; R: no solicitar credenciales ahora | NV: cuenta/organización, mecanismo y permisos; R: no solicitar credenciales ahora |
| Límites y restricciones de uso | NV: cuotas, contexto, tamaño de entrada/salida, región, concurrencia y coste | NV: mismas dimensiones, específicas de la oferta elegida | NV: mismas dimensiones, específicas de la oferta elegida |
| Sesiones | C: perfil local aislado; NV: caducidad, renovación, conversación nueva y persistencia del servicio | C: perfil local aislado; NV: caducidad, renovación y contaminación de contexto | C: perfil local aislado; NV: caducidad, renovación y contaminación de contexto |
| Timeout y cancelación | NV: deadline remoto, interrupción, consulta de estado y cobro tras cancelar | NV: deadline remoto, interrupción, estado y efectos tras cancelar | NV: deadline remoto, interrupción, estado y efectos tras cancelar |
| Reintentos / tasa | NV: señales, espera aconsejada e idempotencia remota; R: sin bucle automático ante envío incierto | NV: señales, espera e idempotencia; R: igual barrera | NV: señales, espera e idempotencia; R: igual barrera |
| Exportación estructurada | NV: formato oficial, garantías JSON/esquema, streaming y exportación UI | NV: formato oficial, garantías JSON/esquema, streaming y exportación UI | NV: formato oficial, garantías JSON/esquema, streaming y exportación UI |
| Procedencia y reproducibilidad | NV: modelo/versión visible, request ID, parámetros, recibo; C: manifiesto local v2 independiente | NV: modelo/versión, request ID, parámetros, recibo; C: manifiesto local v2 | NV: modelo/versión, request ID, parámetros, recibo; C: manifiesto local v2 |
| Información sensible | NV: uso para entrenamiento, retención, residencia, subprocesadores y borrado según contrato | NV: iguales cuestiones por producto/cuenta | NV: iguales cuestiones por producto/cuenta |
| Automatización | NV: condiciones oficiales de login, envío, lectura, descarga y exportación por operación | NV: mismas operaciones por producto | NV: mismas operaciones por producto |

R para los tres: ninguna operación externa está habilitada por este diseño; no
sortear CAPTCHA, anti-bot, cuotas ni controles. Una API oficial, si luego se
confirma, no representa necesariamente el producto interactivo: cambia identidad
experimental, comportamiento y posiblemente condiciones/coste. Elegirla requiere
autorización separada, sin convertirla en dependencia obligatoria ni cambiar
D-002 unilateralmente. La alternativa manual también transmite datos y requiere
permiso; su importación debe validar identidad y siete artefactos igualmente.

Antes de habilitar una plataforma se debe archivar evidencia oficial con fecha,
producto/edición, región/cuenta aplicable, versión y referencia verificable. La
ficha debe resolver separadamente navegar, autenticar, enviar, leer, exportar,
cancelar y borrar, con permiso institucional y condiciones del proveedor.
Una operación desconocida permanece deshabilitada. No confundir disponer de
sesión con autorización para extraer; no asignar valores numéricos por analogía
con otro proveedor. Verificación externa futura: `NOT_RUN`.

## 6. Escenarios de fallo y recuperación

Los estados de tarea/intento siguientes son **requisitos futuros** usando enums
existentes, no comportamiento ya soportado por el vertical. En todos los fallos
anteriores al commit: ningún nuevo activo; conservar conjuntos previos válidos.
Sin activo previo ni archivos, el inspector puede observar `NOT_STARTED`, aunque
la tarea en memoria haya fallado. Una parcialidad no equivale a un estado durable.

| Escenario | Estado esperado / reintento | Evidencia mínima y regla de idempotencia / fallo cerrado |
|---|---|---|
| Timeout antes de recibir respuesta | Intento FAILED; RETRY_PENDING solo si se acredita no envío o reconciliación segura; de otro modo WAITING_REVIEW | Código timeout, fase, tiempo y envío not_sent/sent/unknown. No asumir que falta de respuesta significa falta de ejecución; no retransmitir lo incierto |
| Respuesta parcial | FAILED / RETRY_PENDING con presupuesto y permiso; WAITING_REVIEW si envío incierto al reiniciar | Código, tamaños y hash restringido si autorizado; nunca fragmentos en logs. Siete nuevos artefactos en el siguiente intento; no activar parcial |
| Error de autenticación | FAILED / WAITING_REVIEW; sin reintento automático | Código auth_required y fase, sin tokens ni página de login. Intervención del operador antes de nueva tarea/intento según estado |
| Límite de tasa | FAILED / RETRY_PENDING si rechazo confirma no procesamiento; si no, WAITING_REVIEW | Demora validada y referencia saneada; espera acotada, presupuesto y backoff con jitter configurables. No fabricar cuota ni evadir límite |
| Servicio no disponible | FAILED / RETRY_PENDING solo si envío reconciliado/no enviado; si no, WAITING_REVIEW | Código, fase y señal técnica permitida. Mismo task, nuevo intento solo cuando la continuidad sea demostrable |
| Cancelación durante revisión | Intento/tarea CANCELLED antes del commit; no reintentar tarea terminal | Momento, fase, envío y cancelación remota confirmed/unknown. No publicar respuesta tardía; no afirmar borrado remoto. Tras commit verificar y conservar activo |
| Respuesta válida con artefactos incompletos | FAILED / RETRY_PENDING; inspector nunca COMPLETE | Roles/modelos faltantes y código artifacts_incomplete. Validar siete antes de publicar; repetir conjunto entero |
| Identidad incorrecta | FAILED / WAITING_REVIEW, sin corregir ni publicar | Campo contradictorio y código identity_mismatch, sin contenido sensible. No cambiar revisor/run/modelo para aceptar; investigar aislamiento |
| Reinicio antes de publicar | Sin activo nuevo; reconstruir desde archivos | Con proyección v2 inequívoca: RETRY_REVIEW según resolutor, mismo task y ordinal siguiente. Sin evidencia durable de envío: intervención. Huérfano ambiguo/v1 bloquea; no reconstruir continuidad desde memoria perdida |
| Reinicio después de publicar conjunto completo | Activo COMPLETE tras resolver, reinspeccionar y cotejar run; seguir con pendientes | Reutilizar referencia/manifiesto/hashes. No llamar otra vez al proveedor; un task RUNNING perdido no invalida el commit verificado |
| Repetición de tarea confirmada | COMPLETED / UNCHANGED en replay válido | Identidad/contenido/manifest iguales; cero llamadas externas. Si difieren, bloquear antes de sobrescribir; no tocar otros revisores |

Errores de publicación, plan obsoleto, pérdida del lease, identidad de run
contradictoria o referencia corrupta bloquean el efecto y exigen reinspección.
La persistencia de evidencia saneada de fallos/envíos todavía no existe: antes
de operación real se debe decidir su ubicación/retención con revisión explícita,
sin introducir SQL ni otro almacén de estado paralelo. Un resultado
`RETRY_PENDING` en memoria no es autorización para un bucle remoto automático.

## 7. Seguridad, privacidad y auditoría

1. **Autorización por transmisión:** requerir autorización institucional expresa
   para proveedor, cuenta, finalidad, campos, región y plazo. La autorización de
   diseñar o probar un fake no autoriza transmitir. Identificar como pendientes
   evaluación jurídica, acuerdos contractuales/procesamiento, transferencias
   internacionales, secreto de investigación y permisos de titulares cuando
   correspondan; no afirmar aquí su suficiencia legal.
2. **Minimización reproducible:** enviar solo el caso y contexto autorizados.
   Seudonimizar de forma consistente statement/outputs/historiales; guardar
   cualquier tabla de correspondencia separada y restringida. Validar que la
   transformación preserva las relaciones necesarias para evaluar; si no es
   posible, bloquear, no omitir silenciosamente entradas. No adjuntar todo un run.
3. **Separación de secretos:** inyectar referencias a almacén seguro del sistema
   o entorno restringido en tiempo de ejecución; nunca valores reales en código,
   ejemplos, `Task.metadata`, prompt, `TaskError`, manifiesto ni Git. Perfiles
   fuera del repositorio y sincronización, con permisos del usuario y bloqueo
   actual. No copiar sesiones al remoto por Git.
4. **Saneamiento de salida:** lista cerrada de campos y límites antes de persistir;
   filtrar secretos señuelo y rechazar respuestas que reproduzcan credenciales
   o campos prohibidos. No confiar en que regex detecte todo secreto: la defensa
   primaria es que las credenciales nunca entren al contexto del modelo. No
   guardar el cuerpo crudo por defecto; una cuarentena requeriría autorización,
   cifrado, control de acceso y plazo, y no sería un resultado activo.
5. **Resultados y hashes:** restringir lectura/escritura de resultados y evidencia;
   cifrado en reposo/transferencia según política aprobada. Un hash no anonimiza
   datos de baja entropía ni acredita autenticidad: protegerlo como evidencia
   sensible. Los hashes actuales detectan inconsistencias bajo el modelo de
   almacenamiento confiable; una reescritura coordinada no queda autenticada.
6. **Retención:** fijar antes de enviar retención local y del proveedor, borrado,
   respaldos, conversación/adjuntos, uso para entrenamiento y responsables de
   acceso. Las políticas pueden variar por cuenta/producto; NV no significa
   retención cero. No borrar temporales internos requeridos por recuperación o
   adjudicación; toda limpieza respeta el finalizador vigente.
7. **Auditoría externa:** registrar solo identidad local, operación, versión de
   prompt/paquete, autorización opaca, tiempos, proveedor/modelo declarado si
   verificable, resultado de envío, request ID saneado cuando exista y código
   de error. Si el modelo/versionado no es visible, declararlo desconocido.
   No registrar nombres de personas, prompts, cookies ni enlaces privados.
   La evidencia de acciones externas y su persistencia son requisito pendiente,
   no nuevos campos implícitos del manifiesto actual.

## 8. Validación de contrato con fake

Se reutilizan los fakes y fixtures existentes. No hace falta añadir código para
probar una propuesta documental. Las pruebas actuales verifican la base
sintética; las brechas futuras no se declaran aprobadas por esos resultados.

| Prioridad / caso | Prueba existente o prueba futura definida | Oráculo observable |
|---|---|---|
| 1. Siete correctos | `tests/test_review_contracts.py`; `test_publishes_seven_valid_outputs_for_each_reviewer` en `tests/test_review_storage.py`; flujo vertical | Exactamente 1+3+3 válidos, cada revisor independiente; solo COMPLETE después de inspección |
| 2. Identidad | Contratos mixtos; `WrongIdentityAdapter`; `test_recovered_attempt_with_other_run_blocks_before_adapter` | Rechazo sin nuevo active ni cambios en revisores válidos |
| 2b. Run de respuesta | Futura: fake devuelve una asociación de respuesta de otro run aunque el JSON tenga identidad válida | Parser/consumidor rechaza antes de construir éxito; comparar manifiesto v2 con run protegido, no un campo ignorado |
| 3. Incompletos | `SixArtifactAdapter` y contratos faltantes/duplicados | OUTPUT_REJECTED; active ausente o previo byte-idéntico; no mezclar intentos |
| 4. Error estructurado | Actual `FailingAdapter` prueba fallo genérico. Futura: parametrizar todos los códigos de §3.3 | Preservar TaskError saneado, retryable y transición específica; hoy este comportamiento está pendiente |
| 5. Reintentos | `test_adapter_failure_retries_same_task_with_new_complete_attempt` | Mismo task, distinto attempt y ordinal siguiente; un solo activo |
| 6. Cancelación | Futura: fake con reloj/señal controlados antes del envío, en espera y antes del commit | Sin activo nuevo; CANCELLED terminal; respuesta tardía descartada y sin secretos. Tras commit conservar completo; distinguir cancelación local/remota |
| 7. Recuperación | Pruebas verticales de corte, reinicio y v2; futuras para envío remoto incierto | Proceso nuevo reconstruye solo evidencia real; no inventa task/ordinal. Envío unknown bloquea retransmisión |
| 8. Publicación única | Replay vertical; idempotencia y conflicto de `tests/test_review_storage.py` | Cero llamadas del fake en replay; hashes/mtime conservados cuando el camino lo garantiza; un active, ningún duplicado confirmado |
| 9. Sin credenciales | Futura: señuelos sintéticos en campos extra, error y cabeceras simuladas | Lista permitida rechaza/omite campos prohibidos; buscar señuelos en resultados, logs, excepciones, manifiestos y evidencia; cero apariciones |
| 10. Compatibilidad | Pruebas focalizadas verticales existentes; futura corrida E2E sintética aprobada al cambiar código | Tres revisores → barrera sin ejecutar adjudicación prematura; posterior revisión/adjudicación/aplicación/rango preservados |
| Contexto | Futura: fake inspecciona paquete preparado con perfiles/historiales sintéticos | Mismo paquete autorizado para los tres, snapshot/hash estables; falta/futuro/duplicado/mutación bloquean; firma antigua sigue funcionando |
| Transporte simulado | Futura: fixture de texto, reloj y respuestas programadas, sin HTTP ni navegador | JSON duplicado/truncado/extra, login/negativa, límite de tamaño, auth, tasa y timeout no se publican; número de envíos acotado |

Estas pruebas futuras deben trabajar bajo temporales fuera del repositorio,
sin red ni secretos reales, verificar los bytes de revisores no afectados y
destinos operacionales, y usar procesos independientes para reinicios. No basta
simular `retryable=True`: verificar que el consumidor respeta presupuesto,
señal de cancelación e incertidumbre de envío.

Comprobación local de esta unidad: ejecutar únicamente contratos, flujo vertical
y almacenamiento de revisiones existentes. Los resultados y comandos exactos
se registran en `context/advances.md`. E2E completo y pruebas de requisitos
futuros: `NOT_RUN`, al no modificarse código ejecutable.

## 9. Dependencias, autorizaciones y riesgos pendientes

| Pendiente | Condición para resolver / riesgo si se ignora |
|---|---|
| Oferta y capacidades por proveedor | Evidencia oficial vigente por operación/cuenta; podría no permitir automatización o siete outputs en una interacción |
| Ruta web/API/manual | Decisión del operador compatible con D-002; API no prueba equivalencia con ChatGPT/Gemini/Grok interactivos |
| Contexto y parser | Contrato mínimo revisado, preparación completa y pruebas offline; truncar contexto o reparar JSON inventaría evidencia |
| Errores/cancelación | Consumidor preserva TaskError y estados existentes; timeout efectivo y cancelación cooperativa demostrados, sin cambiar O-01/O-02 |
| Evidencia durable de envíos | Decisión explícita compatible con almacenamiento aprobado, reconciliación y retención; sin ella no hay continuidad garantizada antes de publicación |
| Privacidad y permisos | Aprobaciones jurídicas/institucionales/contractuales aplicables, minimización y transferencia autorizadas; se bloquea envío hasta resolver |
| Límites/coste | Presupuesto, tamaño, latencia y política de reintentos explícitos; no elegir cuotas arbitrarias ni abrir concurrencia masiva |
| Trazabilidad | Modelo visible/versión/configuración y captura autorizada; variabilidad del producto impide prometer reproducibilidad exacta |
| Lease y demoras | Ejecutar una revisión mantiene el lease; latencia o sesión caducada puede bloquear el workflow. Timeouts acotados; no liberar el lease a mitad del protocolo aprobado |
| Integridad local | Lease cooperativo, TOCTOU residual, ausencia de firma y durabilidad no certificada frente a apagado; no aumentar las garantías del escritor por documentación |
| Remoto | Auditoría y revalidación expresamente autorizadas; `REMOTE_NOT_CONFIGURED`, todas las pruebas remotas `NOT_RUN` |

No se necesitan credenciales, despliegues, aprobación de gasto ni elección de
transporte para cerrar esta especificación documental tras QA. Sí son puertas
para cualquier futura operación externa.

## 10. Nota sobre un cuarto generador

No es un cuarto revisor. A futuro afectaría `BenchmarkModel`, layout/selección
de outputs, barreras de entrada, `_model_set`, validadores de revisión y
adjudicación, prompts, inspector, manifiestos/rutas y hashes, propuesta/aplicación
de perfiles e historiales, completitud, huellas O-02 y fixtures/rangos. La
revisión dejaría de tener siete artefactos (serían 1+4+4 si se conservara ese
diseño). Requeriría decisión explícita, versionado, migración/compatibilidad y
pruebas independientes. Nada de ello se implementa ahora: se mantienen tres
outputs obligatorios, siete artefactos, tres revisores y destinos actuales.

## 11. Criterios para una implementación posterior

- [ ] Capacidades y permisos oficiales por plataforma verificados y autorizados;
  ruta y diferencias experimentales declaradas; sin acceso por defecto.
- [ ] Paquete completo de contexto minimizado, versionado y alineado; instantánea
  revalidada antes del commit; fakes antiguos compatibles.
- [ ] Puerto actual conservado; parser produce exclusivamente siete artefactos
  tipados de un intento con identidad y run verificados independientemente.
- [ ] Error estructurado, timeout, tasa, cancelación y límite de reintentos
  probados; sin asumir que cancelación local detiene o borra la ejecución remota.
- [ ] Incertidumbre de envío y evidencia durable resueltas mediante decisión
  revisada, sin SQL ni almacén paralelo improvisado.
- [ ] Publicador, referencia activa, manifiestos v2, lease y O-01/O-02 reutilizados;
  replay sin llamada externa y recuperación sin mezclar artefactos.
- [ ] Pruebas fake de §8 y regresión E2E sintética aplicable aprobadas localmente;
  integridad operacional y ausencia de secretos verificadas.
- [ ] Interacciones reales autorizadas y comprobadas separadamente; solo datos
  sintéticos iniciales; remoto validado en su propia máquina si se despliega.

## 12. Checklist de QA independiente documental

- [ ] Contrastar inventario con los símbolos y firmas enlazados; distinguir
  código presente de extensiones propuestas y criterios futuros.
- [ ] Verificar la brecha de perfiles/historiales, run fuera del resultado/clave,
  `_new_task(provider="synthetic")` y error genérico siempre reintentable.
- [ ] Confirmar 1+3+3, campos/identidades, rutas y manifiesto v2 sin cambios de
  esquema; active es el commit y la proyección puede precederlo.
- [ ] Revisar cada escenario de §6, especialmente caída antes de persistir,
  cancelación terminal, incertidumbre remota y replay tras activación.
- [ ] Confirmar C/P/R/NV sin capacidades de proveedor inventadas ni equivalencia
  UI/API, endpoints, cuotas o permisos asumidos.
- [ ] Revisar autorización, minimización, retención, hashes y saneamiento;
  distinguir protección exigida de garantías efectivamente implementadas.
- [ ] Verificar cobertura actual/futura de las diez prioridades de pruebas,
  comandos/resultados locales y pruebas no ejecutadas.
- [ ] Confirmar cambios solo documentales, integridad operacional, ausencia de
  clientes, modelos, navegador, red, SQL, remoto, commits y pushes.
- [ ] OLBM-35 permanece `READY_FOR_QA` hasta dictamen independiente; fase 2
  `IN_PROGRESS`. No certificar implementación real por aceptar este diseño.
