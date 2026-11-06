# OLBM-52 — reconstrucción del importador manual local

Fecha: 2026-11-06. Reconstrucción desde
`4e89715f60bb99afd5d21050b04f0637c28eea4c` en la rama
`feature/f3-11-manual-review-rebuild`. No es recuperación del código perdido.
Las 141 PASS históricas no son evidencia de esta implementación.

## Alcance y contrato

`automation/review/manual_import_adapter.py` implementa estructuralmente
`ReviewerAdapter.execute(ReviewerExecutionRequest) -> CompleteReviewResult` con
`ManualImportReviewerAdapter(package_path, allowed_root=...)`. Lee un paquete
JSON local; no escribe, publica, genera respuestas ni conecta proveedores.
`ManualReviewImportError`, subtipo de `ReviewerAdapterError`, expone un `code`
estable y un mensaje saneado sin contenido, identidad ni rutas de entrada.

El alcance es sintético y local. Se reutilizan `MainReviewOutput`,
`HistoryProposalOutput`, `ProfileProposalOutput` y `CompleteReviewResult`.
No cambia O-01/O-02, lease, orquestador, adjudicación, publicador, almacenamiento
ni contratos. D-044 resuelve las menciones históricas a BD/generación del prompt
fase 3: prevalecen AGENTS.md, D-012 y el alcance exclusivo de OLBM-52.

## Formato JSON de entrada, versión 1

La versión 1 identifica este sobre de importación, **no** un manifiesto de
persistencia. La publicación sigue usando manifiestos v2 existentes.

El objeto raíz exige exactamente `schema_version` (entero 1), `task_id`,
`attempt_id`, `statement_index` (entero, no booleano/float), `reviewer`
(`chatgpt`, `gemini` o `grok`), `run_id` y `components`.
`components` es una lista de exactamente siete objetos:

| Slot | Contrato de contenido |
|---|---|
| `main` | Una revisión de Gemma, Mistral y Llama y reporte ideal, `MainReviewOutput` |
| `history_gemma` | `HistoryProposalOutput` con `model=gemma` |
| `history_mistral` | `HistoryProposalOutput` con `model=mistral` |
| `history_llama` | `HistoryProposalOutput` con `model=llama` |
| `profile_gemma` | `ProfileProposalOutput` con `model_name=gemma` |
| `profile_mistral` | `ProfileProposalOutput` con `model_name=mistral` |
| `profile_llama` | `ProfileProposalOutput` con `model_name=llama` |

Cada componente exige exactamente los siguientes campos:

| Campo | Validación |
|---|---|
| `task_id`, `attempt_id`, `statement_index`, `reviewer`, `run_id` | Coincidencia exacta de valor y tipo con raíz y solicitud |
| `slot` | Uno de los siete slots, sin duplicados; el orden es libre |
| `output` | Objeto estructurado inline validado por los contratos existentes |
| `content_sha256` | SHA-256 hexadecimal canónico exacto de `output` |
| `declared_provenance` | Texto no vacío de hasta 2048 caracteres, declaración del operador |
| `observed_at` | Fecha ISO 8601 con zona horaria, declarada por el operador |
| `state` | Solo `COMPLETE`; parcial, incierto o cualquier otro valor se rechaza |

Los siete `output` deben contener los cuatro campos existentes `task_id`,
`attempt_id`, `statement_index` y `reviewer`, coincidentes exactamente con la
solicitud. `run_id` se valida en raíz y componentes y contra
`inputs.selected_run_id` y `plan.run_id`; si aparece en `output` también debe
coincidir. No se añade run a la clave lógica de tarea ni al tipo de resultado.
No se remapean identificadores ni se completan campos ausentes.

Los nombres del contenido mantienen los contratos existentes: historiales usan
`model`, `output` (índice), `date` y `observations`; propuestas usan `model_name`
y las siete listas `new_*`; revisión principal conserva `model_reviews` y
`proposed_ideal_report`. Los campos desconocidos de contenido son datos inertes;
no se ejecutan instrucciones, rutas o código que contengan.

No se admiten referencias a archivos (`output_path`, URL ni descarga): la única
modalidad es contenido inline. Una referencia a un archivo inexistente se
rechaza por esquema; no se sigue. No se necesitan otros archivos del paquete.

## Hashes y procedencia

El hash de entrada se calcula antes de añadir metadatos locales:

```python
import hashlib
import json

encoded = (json.dumps(output, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
content_sha256 = hashlib.sha256(encoded).hexdigest()
```

Esta codificación coincide con la canónica del escritor v2. No es un hash del
texto original de una página ni del archivo JSON completo. El operador puede
cambiar contenido y recalcular el hash: la integridad local no prueba autoría.

Después de validar, cada `output` recibe `manual_import`, reservado y prohibido
en la entrada, con `source=operator_supplied`, `slot`, `run_id`, `state`,
`observed_at`, `declared_provenance`, `content_sha256`,
`integrity=local_hash_validated` y `provider_authenticity=UNVERIFIED`.
Los siete JSON publicados conservan estos metadatos, protegidos por sus hashes
v2. Para comprobar `content_sha256`, retirar solamente `manual_import` del
objeto persistido y aplicar la fórmula anterior. Cambiar una declaración cambia
el contenido publicado y se considera incompatible para la misma identidad.

`created_at` del resultado es la hora local de construcción; no sustituye a
`observed_at`. Esta última es una fecha declarada válida sintácticamente, no
una fecha corroborada externamente. `COMPLETE` solo significa completitud
estructural local. Nunca certifica que ChatGPT, Gemini o Grok produjeron
realmente las respuestas. La declaración del operador, los hashes, la
completitud y la autenticidad externa son dimensiones separadas.

## Límites y entrada no confiable

Tamaño máximo fijo: 2 MiB por paquete, leído con límite aunque el archivo crezca.
Profundidad JSON máxima: 64. Se rechazan JSON malformado, claves duplicadas,
UTF-8 inválido, surrogates aislados, NaN/Infinity y floats no finitos por overflow.
Se comprueba archivo regular; se rechazan directorios, FIFO, enlaces simbólicos,
rutas fuera de `allowed_root`, escapes `..`, ausencias y errores de lectura.
Una ruta relativa se interpreta desde `allowed_root`; se permite una ruta
absoluta únicamente si queda confinada. La raíz debe existir y sus ancestros
no pueden ser enlaces simbólicos. Usar rutas canónicas en el entorno local
(por ejemplo `/private/tmp` en vez del alias `/tmp` de macOS para pruebas).

El confinamiento es una guarda previa con `O_NOFOLLOW` cuando está disponible;
no garantiza resistencia frente a otro proceso hostil que cambie directorios
entre comprobación y apertura. El operador debe controlar el directorio inbox
y sus padres. No se añade un segundo bloqueo ni almacén. El adaptador solo
procesa JSON y no ejecuta instrucciones incluidas en sus valores.

## Integración y publicación

```python
from automation.review.manual_import_adapter import ManualImportReviewerAdapter

adapter = ManualImportReviewerAdapter(package_path, allowed_root=inbox_root)
result = adapter.execute(request)  # request existente; todos los IDs deben coincidir
```

El llamador sigue siendo responsable de la coordinación y publicación. En el
vertical sintético existente, `run_synthetic_reviewer_once(config, adapter)`
valida el resultado, revalida la expectativa bajo el mismo `WorkflowLease` y
llama `publish_complete_review()` con `ReviewAttemptMetadata` v2. El escritor
prepara siete JSON y manifiesto, y confirma por reemplazo atómico de
`active.json`; no hay publicación parcial. El inspector reinspecciona el set.

Un error de importación produce `ADAPTER_FAILED`, ninguna publicación ni
adjudicación. El vertical existente deja la tarea en `RETRY_PENDING` y marca
el intento fallido; no se introduce una nueva política de reintentos. Una
captura incierta requiere corrección/reconciliación humana de la evidencia,
nunca rellenado automático ni reenvío externo. Cada nuevo intento necesita un
paquete para su propia identidad.

Una importación idéntica conserva la idempotencia del escritor. La misma
identidad con contenido o procedencia diferente se rechaza en el escritor
existente antes de cambiar la referencia activa; se conserva el conjunto
anterior válido. El adaptador puede construir un candidato estructuralmente
válido: la compatibilidad con evidencia ya publicada corresponde al publicador,
no a un registro paralelo. La duplicación incompatible nunca llega a confirmar
una nueva revisión. Pruebas existentes cubren también sets preparados huérfanos.

La barrera permanece cerrada tras uno y dos revisores completos y solo se abre
con los tres. Abrirla no ejecuta adjudicación ni indica completitud global del
statement. Este entregable no implementa CLI operacional ni captura interactiva.
El vertical crea sus IDs de intento dentro de la llamada: los tests usan un
operador sintético que prepara el paquete después de recibir la solicitud.
El adaptador real no tiene callback de generación ni cambia IDs; integrar una
pausa durable para operadores reales queda fuera de alcance. No se presenta
como importación operacional de proveedores reales.

## Ejemplos sintéticos y reproducción

[Paquete completo](../tests/fixtures/manual_review/complete.json): siete
componentes con `synthetic-task`, `synthetic-attempt`, `statement_index=10`,
`reviewer=chatgpt`, `run_id=run-a`. No contiene datos policiales ni respuestas
comerciales. Se importa en la prueba con una solicitud sintética coincidente.
[Constructor de fixtures](../tests/helpers/manual_review_package.py) construye
los mismos contratos, y calcula hashes independientemente del importador.
Los tests verticales usan identidades de la solicitud recibida.

Desde la raíz del worktree, con `PYTHON` apuntando al entorno local Python 3.11:

```bash
TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -q -p no:cacheprovider tests/test_manual_import_adapter.py tests/test_review_contracts.py tests/test_review_storage.py tests/test_synthetic_review_flow.py tests/test_adjudication_run_identity.py
PYTHONPYCACHEPREFIX=/private/tmp/olbm52-pycache "$PYTHON" -m py_compile automation/review/manual_import_adapter.py tests/helpers/manual_review_package.py tests/test_manual_import_adapter.py
git diff --check
```

Resultados exactos, integridad operacional y estado se registran en
`context/advances.md`. La suite usa solo fixtures aislados, sin APIs, navegador,
SQL, red ni proveedor real. Linux/Windows y remoto: `NOT_RUN`;
`REMOTE_NOT_CONFIGURED`. QA independiente: `NOT_RUN`.

## Evidencia local de la reconstrucción

El 2026-11-06, Python 3.11.17 en macOS: **178 PASS, 0 FAIL, 0 SKIP**
en 5.18 s, de los cuales **59** son pruebas nuevas y **119** regresiones
focalizadas. La corrida inicial del importador dio 50 PASS; después se añadieron
nueve casos y se ejecutó la corrida consolidada anterior. No se suman ambas
corridas como pruebas únicas. Compilación sintáctica y diff check: PASS.
No se ejecutaron suites ajenas al cambio.

| Caso obligatorio | Resultado observado |
|---|---|
| Siete componentes válidos | PASS: CompleteReviewResult y tres publicaciones v2; barrera cerrada 1/2, abierta 3/3 |
| Cuarto ausente | PASS: seven_components_required, sin publicación |
| Cuarto inválido | PASS: invalid_review_content, sin publicación |
| Statement distinto | PASS: component_identity_mismatch, sin publicación |
| Attempt distinto | PASS: component_identity_mismatch, sin publicación |
| Reviewer distinto | PASS: component_identity_mismatch, sin publicación |
| Hash incorrecto | PASS: content_hash_mismatch, sin publicación |
| Slot duplicado | PASS: duplicate_slot, sin publicación |
| JSON malformado | PASS: invalid_json, sin publicación |
| Captura incierta | PASS: capture_not_complete, sin publicación |
| Duplicado incompatible | PASS: escritor rechaza ValueError; conjunto válido anterior intacto, sin nueva confirmación |
| Ruta fuera del directorio | PASS: ManualReviewImportError/ADAPTER_FAILED, sin publicación |

Todos los casos inválidos mantienen adjudicación bloqueada. No se invoca un
adjudicador en esta entrega. También se prueban run/task mezclados, referencias,
replay idéntico, procedencia cambiada, metadatos, symlinks, FIFO, tamaños,
profundidad, números/Unicode y conservación del primer revisor ante fallo del
segundo. La integridad operacional se comprobó antes/después en los tres
checkouts existentes: 85/62/62 archivos, sin altas/bajas ni cambios de hash.

## Checklist de QA independiente

- [ ] Verificar baseline, rama, worktree persistente y commit local de respaldo.
- [ ] Confirmar que es reconstrucción y no reutiliza las 141 PASS históricas.
- [ ] Ejecutar el comando focalizado y contrastar resultados con avances.
- [ ] Importar el fixture completo; comprobar siete contratos y cero escrituras del adaptador.
- [ ] Comprobar tres manifiestos v2, identidad, metadatos conservados y barrera 1/2/3.
- [ ] Repetir ausencia/invalidez del cuarto componente, statement/attempt/reviewer/run/task mezclados, hash incorrecto, slot duplicado y JSON malformado.
- [ ] Repetir captura incierta/parcial, rutas fuera de inbox, enlaces, archivo inexistente, FIFO y referencias prohibidas.
- [ ] Comprobar límite de tamaño/profundidad, claves repetidas, Unicode inválido, números no finitos y tipos JSON exactos.
- [ ] Repetir replay idéntico e incompatible, incluida declaración cambiada; conservar el set válido previo.
- [ ] Confirmar fallo del segundo revisor sin alterar el conjunto válido del primero.
- [ ] Confirmar ausencia de manifiestos falsos y adjudicación en casos inválidos.
- [ ] Verificar hashes operacionales, staging selectivo y conservación de otros worktrees.
- [ ] Confirmar O-01/O-02 y D-041/D-042/D-043 intactos y ausencia de credenciales/datos reales.
- [ ] Mantener autenticidad externa UNVERIFIED y operación real/remota fuera del alcance validado.

Este checklist está pendiente de una auditoría independiente; no declara PASS.
