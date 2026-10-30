# Flujo real: revisión y adjudicación secuencial

**Estado:** especificación operativa con persistencia local implementada hasta 2.2.4-C y pendiente de revisión; ejecución de adjudicaciones por LLM y automatización web permanecen fuera de este entregable.

## Objetivo y alcance

Procesar **un statement_index por ejecución**, en orden ascendente, sin generación de revisiones principales por lotes. Conservar los prompts originales convertidos a Markdown; sus secciones de modo lote quedan documentadas, pero el modo operativo elegido para las revisiones principales es individual.

- Modelos del benchmark: **Gemma, Mistral, Llama**.
- Revisores: **ChatGPT, Gemini, Grok**.
- Adjudicador: **ChatGPT**, en conversaciones separadas de la revisión principal y separadas para cada adjudicación.
- Ruta definitiva de casos revisados: `data/finetuning/review_cases/statement_NNN/`, con `NNN = statement_index` rellenado al menos a tres dígitos (`9` → `009`).
- El flujo debe consultar el caso de mayor índice y **verificar su completitud antes de avanzar**. Para índices activos desde `010`, también debe comprobar que no falte ningún índice anterior de la secuencia. Si existe un hueco, no puede proponer un índice superior: solo podrá seleccionar el faltante cuando otro componente haya validado sus entradas originales; de lo contrario debe bloquear. No crea directorios ni declaraciones ausentes. Si el caso actual no está completo, debe reanudarlo; si está completo y la secuencia es continua, el siguiente objetivo es `statement_index + 1`, validando que existan sus entradas.

## Estrategia de implementación y validación

- Toda implementación y prueba inicial de este flujo, incluida su infraestructura Playwright, se realiza en la Mac local.
- La validación local cubre la recopilación y alineación de declaraciones, outputs, perfiles e historiales; la preparación de entradas; la validación independiente de los tres revisores; la recuperación completa de revisiones incompletas; las barreras de adjudicación; y la persistencia recuperable.
- La lógica determinista se prueba con fixtures sintéticos, directorios temporales o copias controladas, sin modificar revisiones, perfiles, historiales ni datos existentes. La preparación y validación de datos se prueba separadamente de las interacciones de navegador.
- Ninguna prueba local autoriza por sí misma el acceso a ChatGPT, Gemini o Grok. Las interacciones reales solo se ejecutan cuando estén expresamente autorizadas y permitidas por cada plataforma.
- Después de superar la validación local aplicable, el mismo flujo se desplegará en la máquina remota para su ejecución definitiva. Sus accesos, perfiles, credenciales, rutas y dependencias se configuran en esa etapa posterior y se validan de forma independiente.
- El progreso se registra como `LOCAL_TESTED`, `LOCAL_PENDING`, `REMOTE_NOT_CONFIGURED` o `REMOTE_TESTED`. La evidencia local no se reutiliza para declarar validación remota.

## Persistencia y recuperación desde archivos

- Los JSON y JSONL reales del proyecto son la fuente de verdad exclusiva del workflow. No existe un registro paralelo en SQLite, SQL ni otra base de datos para estados, intentos o resultados.
- Al iniciar o reanudar, el sistema inspecciona `data/finetuning/review_cases/`, valida el mayor `statement_NNN` existente y decide si debe retomarlo o avanzar al índice siguiente con entradas disponibles.
- El estado se deriva de la presencia y validez estructural de las entradas, los tres conjuntos de revisión, `adjudication.json`, los resultados de las otras dos adjudicaciones y la aplicación verificada en `config/feedback/` y `config/profiles/`.
- Las escrituras permanentes deben prepararse en el mismo sistema de archivos, validarse y publicarse mediante reemplazo atómico. La ausencia o invalidez de una salida impide publicar como válido el conjunto nuevo del revisor.
- La recuperación conserva los conjuntos válidos de los otros revisores y reemplaza íntegramente el conjunto del revisor afectado. La procedencia por `statement_index`, revisor e intento impide mezclar ejecuciones.
- La idempotencia debe impedir aplicar dos veces una adjudicación y permitir reconstruir ese hecho después de reiniciar. La completitud de una tarea individual no equivale a la completitud global del statement.
- La construcción o filtrado de datasets y el entrenamiento o fine-tuning quedan fuera de este flujo. `data/finetuning/review_cases/` es el nombre vigente de una ruta de casos revisados, no una dependencia de entrenamiento.

## Política de statements históricos 000–009

El operador confirmó explícitamente que `statement_000` a `statement_009`, ambos inclusive, fueron revisados y adjudicados, y que sus actualizaciones de perfiles e historiales se aplicaron manualmente. Este rango constituye un conjunto histórico finalizado.

Para estos diez statements:

- no se repiten automáticamente revisiones ni adjudicaciones;
- no se reaplican perfiles ni historiales;
- no se exigen identificadores de ejecución que no existían ni temporales ya eliminados;
- no se modifican sus archivos;
- no se infieren ni fabrican metadatos para simular verificaciones no realizadas.

La completitud histórica confirmada es una decisión operativa basada en la declaración explícita del operador. Es independiente de la completitud verificada automáticamente, que exige evidencia técnica suficiente en los artefactos. Por ello, el inspector puede conservar `UNVERIFIED` para las revisiones históricas sin que ese diagnóstico autorice recuperación, repetición o reaplicación.

Las reglas automáticas de validación y recuperación de este documento se aplican a statements activos o futuros. El verificador global de 2.2.3 reconoce el rango `000`–`009` como cerrado y excluido de recuperación automática, sin atribuirle evidencia técnica retroactiva.

## Entradas verificadas antes de revisar

1. Localizar el run correspondiente en `data/generated/runs/` **según la regla de selección existente, si está definida**; no inferir equivalencias si hay varios runs o resultados.
2. Extraer exactamente los tres `draft_report` de Gemma, Mistral y Llama con **el mismo** `statement_index`. Verificar también la alineación de `incident_index` cuando el dato esté presente, sin corregir inconsistencias por inferencia.
3. Leer `data/statements/statements.jsonl` y buscar la declaración por `statement_index`, no por posición física en el archivo.
4. Leer los perfiles actuales de `config/profiles/` y los historiales actuales de `config/feedback/` para los tres modelos, si se mantienen en esas rutas reales.
5. Leer `config/prompts/main_review.md`, convertido del DOCX proporcionado.
6. Si falta alguna entrada obligatoria, detener ese statement y registrar el bloqueo. No sustituir los datos con los de otro índice.

## Salida de cada revisor

Cada revisor ChatGPT, Gemini y Grok recibe la entrada requerida por `main_review.md` y produce tres categorías independientes:

1. **Revisión principal**: archivo permanente `chatgpt_review.json`, `gemini_review.json` o `grok_review.json` dentro de `statement_NNN/`.
2. **Propuestas de historial**: archivos temporales diferenciados por revisor.
3. **Propuestas de actualización de perfil**: archivos temporales diferenciados por revisor.

Desde 2.2.4-A, el contrato único de almacenamiento por revisor es `statement_NNN/.review_sets/<revisor>/attempts/<set_id>/`. Conserva `<revisor>_review.json`; los temporales se denominan `histories/<modelo>_history.json` y `profile_proposals/<modelo>_profile_proposal.json`, con `<modelo>` igual a `gemma`, `mistral` o `llama`. `manifest.json` vincula las siete salidas y `statement_NNN/.review_sets/<revisor>/active.json` identifica el único conjunto confirmado. No mezclar el contenido temporal dentro del JSON de revisión principal: el prompt pide salidas separadas.

El nombre permanente `statement_NNN/<revisor>_review.json` se conserva como proyección de compatibilidad. Para statements nuevos, su presencia aislada no demuestra completitud: el inspector utiliza el conjunto versionado confirmado cuando existe y verifica manifiesto, hashes, statement, revisor, tarea e intento.

### Publicación y reemplazo de una revisión completa

1. Aceptar únicamente un `CompleteReviewResult` y revalidar su identidad y siete salidas.
2. Rechazar en el propio escritor cualquier índice `000`–`009` y cualquier destino cuyo nombre no corresponda al índice.
3. Serializar y releer los siete JSON en un directorio temporal del mismo árbol del revisor.
4. Crear y validar el manifiesto con hashes canónicos.
5. Renombrar el directorio preparado a su ruta inmutable por intento.
6. Proyectar la revisión principal al nombre permanente mediante reemplazo de archivo.
7. Confirmar el conjunto reemplazando atómicamente `active.json`.
8. Releer la referencia, manifiesto y siete artefactos antes de informar éxito.

El paso 7 es el punto de confirmación. No constituye una transacción multiarchivo: antes de ese paso, el inspector conserva la versión activa anterior e ignora directorios huérfanos; después, resuelve exclusivamente el conjunto nuevo completo. Un reintento idéntico no crea otra versión y una identidad reutilizada con contenido diferente se rechaza.

Una proyección fija creada antes de una interrupción solo habilita repetir la
revisión cuando coincide exactamente con un conjunto inmutable no activo que
supera la validación completa de manifiesto, hashes, statement, revisor, tarea,
intento y nombre de set. Esa evidencia se clasifica como `INCOMPLETE`, nunca como
confirmación. El inspector no elimina ni activa artefactos; una proyección que
no pueda vincularse de esta forma permanece `UNVERIFIED`.

Para conjuntos nuevos, el manifiesto v2 conserva además `attempt_number`,
`run_id` y `task_deduplication_key`. Un reinicio solo puede reanudar la tarea si
la secuencia de intentos es contigua, corresponde a un único `task_id`, run,
statement y revisor, y la proyección identifica inequívocamente el último
intento. El nuevo intento conserva `task_id`, incrementa `attempt_number` y crea
otro `attempt_id`. Los conjuntos activos v1 siguen siendo válidos para lectura;
un huérfano v1 queda `UNVERIFIED` y requiere resolución externa, sin metadatos
retroactivos.

La integración de esta lectura quedó validada localmente: `active.json` es la única autoridad lógica cuando existe un conjunto versionado. Una proyección fija desactualizada nunca prevalece; una referencia activa alterada bloquea la lectura en vez de retroceder al archivo permanente. Sin `active.json`, los statements históricos conservan la lectura heredada y la política de cierre de `000`–`009`.

## Adjudicaciones posteriores

- **Perfiles**: tomar las tres propuestas por modelo del benchmark, el perfil acumulado anterior y el rango `start=end=statement_actual`; utilizar `config/prompts/profile_adjudication.md` en una conversación de ChatGPT distinta de la revisión principal. El prompt DOCX permite rangos inclusivos y cuenta evidencia por **outputs distintos**, nunca por revisores. Aplicar los perfiles adjudicados validados a `config/profiles/` (sin inventar nombres específicos).
- **Historiales**: tomar las propuestas de los tres revisores para Gemma/Mistral/Llama, utilizar `config/prompts/history_adjudication.md` en una conversación distinta, y persistir únicamente los tres historiales adjudicados en los archivos correspondientes de `config/feedback/`. `ADJUDICATION_NOTES` puede existir en la respuesta cruda, pero no se persiste ni participa en completitud, identidad o evidencia.
- **Revisión final**: tomar declaración original, tres outputs, tres revisiones y el resto del contexto exigido por `config/prompts/review_adjudication.md`. Utilizar una conversación distinta de ChatGPT; guardar `adjudication.json` dentro de `statement_NNN/`, sujeto a validación del esquema.

**No se impone un orden adicional** entre las tres adjudicaciones: todas dependen de disponer de sus entradas. Ninguna actualización se considera aplicada por la mera generación de texto.

### Persistencia de resultados adjudicados

2.2.4-B aplica conjuntamente un resultado ya generado para un solo statement activo:

1. Validar la adjudicación final, los tres historiales y los tres perfiles completos antes de modificar destinos.
2. Rechazar históricos, duplicados, conflictos y, cuando existan revisiones versionadas, cualquier desacuerdo con sus tres referencias activas.
3. Preparar los siete archivos objetivo y un manifiesto con hashes de origen y destino bajo `.adjudication_applications/<application_id>/`.
4. Marcar la operación `APPLYING`, reemplazar cada archivo solo desde su preimagen validada y comprobar inmediatamente su hash.
5. Marcar `APPLIED` únicamente después de verificar los siete destinos. La misma aplicación ya verificada produce `UNCHANGED`.

Los estados `PREPARED` o `INTERRUPTED` no significan que el statement esté completo. Una reanudación solo puede continuar la misma identidad y los cambios externos incompatibles bloquean la operación. El manifiesto conserva la información de run, intentos y hashes necesaria para la evidencia futura, pero 2.2.4-B no genera `application_evidence.json`, no elimina temporales y no satisface por sí solo el contrato global de 2.2.3. Estas responsabilidades permanecen en 2.2.4-C.

### Recuperación, evidencia y limpieza final

2.2.4-C recupera desde los archivos de B sin regenerar adjudicaciones:

1. Inspeccionar el manifiesto, copias preparadas, estado y siete destinos.
2. Continuar solo si cada destino mantiene su preimagen o ya contiene su objetivo; cualquier otro contenido bloquea la operación.
3. Verificar que `run_id` esté presente y que los tres `task_id`/`attempt_id` sigan coincidiendo con los conjuntos activos.
4. Preparar una lista explícita de archivos sueltos consumidos, con hashes, y eliminarlos de forma reanudable. No descubrir nombres ni borrar por patrones.
5. Conservar siempre `.review_sets/`, `.adjudication_applications/`, sus manifiestos, revisiones permanentes y adjudicación.
6. Revalidar la aplicación y publicar atómicamente la evidencia versión 2 únicamente después de terminar la limpieza. La evidencia conserva el perfil efectivamente aplicado desde el material preparado inmutable, de modo que un statement previo pueda verificarse tras actualizaciones acumulativas posteriores sin exigir igualdad con el perfil global más reciente. La versión 1 continúa siendo legible mientras exista coincidencia exacta.
7. Ejecutar el verificador global; solo el conjunto completo de archivos y evidencia permite `COMPLETE`.

Una recuperación de un statement anterior nunca revierte el estado acumulado: si un historial recibió otra entrada o un perfil cambió desde la preparación, su hash ya no coincide con la preimagen ni el objetivo y la recuperación se detiene. Los contadores y versiones provienen del perfil adjudicado y no se recalculan durante persistencia o recuperación.

Desde el esquema 2 de aplicaciones, los hashes de las siete preimágenes forman parte de `application_id`. El inspector no acepta sustituir un hash de origen en `manifest.json` porque la identidad esperada deja de coincidir con el nombre estable de la aplicación. Los reintentos legítimos buscan la misma solicitud por sus resultados objetivo antes de reanudarla, de modo que la identidad comprometida no rompe la idempotencia cuando algunos destinos ya contienen el resultado. Esta vinculación detecta inconsistencias locales; no constituye autenticidad frente a un actor capaz de reescribir coordinadamente todo el almacenamiento.

Los archivos individuales de control y de `prepared/` deben permanecer como archivos regulares confinados bajo la aplicación. Un symlink en cualquiera de los siete preparados, el manifiesto o el estado invalida la aplicación. Las guardas se repiten antes de accesos sensibles, aunque subsiste una ventana TOCTOU sin operaciones por descriptor.

En perfiles, la identidad de continuidad de un patrón es `(categoría, texto)`. Repetir el mismo texto en `strengths` y `weaknesses` es válido si el perfil cumple el esquema; cada categoría conserva por separado sus restricciones de `evidence_count` y `last_observed_output`.

## Estado de completitud

Los criterios siguientes definen la completitud verificada automáticamente para statements activos o futuros. No sustituyen la política de cierre histórico confirmado para `statement_000`–`statement_009`.

Un statement solo se considera procesado por completo cuando:

- Existen y son válidos los tres archivos permanentes de revisión principal.
- Existe `statement_NNN/adjudication.json` con estructura válida y decisión registrada.
- Se completaron las adjudicaciones correspondientes de historial y perfil.
- Se escribieron y verificaron los tres perfiles acumulados y los tres historiales adjudicados según sus esquemas.
- Se registró evidencia suficiente para **no aplicar dos veces** el mismo resultado en una reanudación.
- Los temporales de perfil e historial utilizados se eliminaron **después** de comprobar la persistencia y conservar evidencia de aplicación; si una adjudicación depende de un rango aún abierto, conservar los temporales que ese rango requiera.

`approved`, `needs_human_review` y `rejected` son **resultados de adjudicación del dato**, no estados de ejecución de la tarea: cualquiera de ellos puede existir en un statement procesado por completo. No condicionan la completitud operativa a usos posteriores fuera de este workflow.

## Restricciones

- No modificar el diseño de los cuatro prompts originales en la conversión inicial a Markdown. Cambios posteriores deben estar versionados y aprobados.
- No inventar runs, índices, reportes, archivos, resultados, adjudicaciones ni rutas no confirmadas.
- No borrar temporales en fallos parciales ni archivos que todavía se requieran para adjudicar un rango.
- No sobrescribir perfiles o historiales sin validación y mecanismo de recuperación apropiado.
- La capa Playwright debe respetar las condiciones de los servicios externos; este documento define la lógica del proyecto, no autoriza extracciones automatizadas prohibidas.

## Validación, recuperación y reanudación de revisiones

### 1. Principio general

El procesamiento de revisiones se realiza secuencialmente, utilizando un único `statement_index` a la vez.

Antes de avanzar al siguiente statement, el sistema debe verificar que el actual haya completado todas las etapas obligatorias.

Esta recuperación excluye expresamente `statement_000`–`statement_009`, cerrados por confirmación del operador. Un diagnóstico técnico `UNVERIFIED` dentro de ese rango se conserva con fines de inspección y no inicia acciones automáticas.

Si un statement está incompleto, el sistema debe identificar la etapa pendiente y reanudar el procesamiento sin repetir innecesariamente las revisiones válidas de otros modelos revisores.

**Regla fundamental:** la unidad mínima de recuperación de una revisión incompleta es la ejecución completa del modelo revisor afectado, no cada archivo individual.

### 2. Validación de entradas del statement

Antes de iniciar o repetir una revisión, el sistema debe recopilar y verificar la información correspondiente al `statement_index` actual:

1. Declaración original desde `data/statements/statements.jsonl`.
2. Output de Gemma.
3. Output de Mistral.
4. Output de Llama.
5. Perfil acumulado vigente de Gemma, Mistral y Llama.
6. Historial acumulado vigente de Gemma, Mistral y Llama.
7. Instrucciones completas de `config/prompts/main_review.md`.

Los outputs originales deben corresponder al mismo `statement_index` que la declaración.

No debe utilizarse información de otro statement para completar entradas faltantes.

Si alguna entrada obligatoria no está disponible, la ejecución de la revisión debe permanecer pendiente hasta resolver la dependencia.

### 3. Integridad de las salidas de cada revisor

Cada modelo revisor (`chatgpt`, `gemini`, `grok`) debe proporcionar, mediante una ejecución completa de `main_review.md`, tres grupos de resultados:

**A. Revisión principal**

Una evaluación completa de los outputs de Gemma, Mistral y Llama, conservando la estructura definida en `main_review.md`.

**B. Historiales provisionales**

Tres registros independientes:

- Historial de Gemma.
- Historial de Mistral.
- Historial de Llama.

Cada registro debe incluir:

- `model`
- `output`
- `date`
- `observations`

**C. Propuestas de actualización de perfiles**

Tres objetos independientes:

- Propuesta de actualización del perfil de Gemma.
- Propuesta de actualización del perfil de Mistral.
- Propuesta de actualización del perfil de Llama.

Cada objeto debe mantener el campo `model_name` y la estructura establecida en `main_review.md`.

Una revisión solamente se considera completa cuando los tres grupos de resultados existen, son estructuralmente válidos y corresponden a los modelos y al statement esperado.

### 4. Detección de revisiones incompletas

El sistema debe verificar de manera independiente las salidas de ChatGPT, Gemini y Grok.

Una revisión debe considerarse incompleta cuando:

- Falta la revisión principal.
- Falta cualquiera de los tres historiales provisionales.
- Falta cualquiera de las tres propuestas de perfil.
- Alguna salida contiene JSON inválido.
- Alguna salida no cumple la estructura esperada.
- El campo `model` o `model_name` no coincide con el modelo del benchmark correspondiente.
- El identificador `output` no corresponde al `statement_index` actual.
- La revisión principal no contiene las evaluaciones de los tres modelos del benchmark.

La ausencia de una sola salida obligatoria invalida el conjunto completo de resultados del revisor afectado para esa ejecución.

### 5. Recuperación completa del revisor afectado

Cuando se detecta que un revisor tiene resultados incompletos o inválidos, debe ejecutarse nuevamente su revisión completa.

El procedimiento obligatorio será:

1. Identificar el modelo revisor afectado.
2. Recopilar nuevamente la declaración original correspondiente al statement.
3. Recopilar nuevamente los outputs de Gemma, Mistral y Llama.
4. Recopilar los perfiles e historiales acumulados requeridos por `main_review.md`.
5. Preparar nuevamente la entrada completa utilizando `config/prompts/main_review.md`.
6. Ejecutar una nueva revisión completa con el mismo modelo revisor afectado.
7. Recopilar nuevamente sus tres grupos de resultados: revisión principal, historiales y propuestas de perfil.
8. Validar íntegramente las nuevas salidas.
9. Reemplazar el conjunto anterior de resultados del revisor afectado por el nuevo conjunto validado.
10. Comprobar nuevamente la completitud de los tres revisores.

No se permite recuperar una revisión solicitando únicamente el historial o la propuesta de perfil faltante.

Tampoco se permite completar una revisión combinando fragmentos de ejecuciones diferentes del mismo revisor.

### 6. Reemplazo consistente de resultados

Las salidas de un revisor deben tratarse como un conjunto lógico indivisible.

Por ejemplo, si Gemini tiene una revisión principal válida, tres propuestas de perfil válidas y solamente dos historiales válidos, se considera incompleta toda la revisión de Gemini.

Al repetir la evaluación:

- Se reemplaza `gemini_review.json`.
- Se reemplazan los tres historiales provisionales de Gemini.
- Se reemplazan las tres propuestas provisionales de perfil de Gemini.
- Se conservan las revisiones y propuestas válidas de ChatGPT y Grok.

El sistema no debe mezclar la revisión principal de una ejecución de Gemini con historiales o perfiles procedentes de otra ejecución de Gemini.

**Regla de seguridad de persistencia:** el reemplazo definitivo solo puede producirse después de que todas las nuevas salidas hayan sido recibidas y validadas.

Si la nueva ejecución falla o devuelve resultados incompletos, no debe considerarse completada la recuperación y no debe habilitarse ninguna adjudicación.

Los archivos deben conservar un estado recuperable hasta que pueda aplicarse el reemplazo completo y consistente.

### 7. Barrera obligatoria de adjudicación

Las adjudicaciones quedan bloqueadas mientras cualquiera de los tres modelos revisores mantenga resultados incompletos o inválidos.

Para habilitar las adjudicaciones deben existir:

- Tres revisiones principales válidas: ChatGPT, Gemini y Grok.
- Nueve historiales provisionales válidos: tres por cada revisor.
- Nueve propuestas provisionales de perfil válidas: tres por cada revisor.

Todos los resultados deben corresponder al statement actual y a revisiones completas de sus respectivos revisores.

No debe iniciarse ninguna adjudicación utilizando información parcial.

### 8. Reanudación de las adjudicaciones

Cuando las tres revisiones estén completas y validadas, se podrá continuar con los procesos de adjudicación establecidos en el flujo:

**Adjudicación de revisiones principales**

Utiliza `config/prompts/review_adjudication.md` y las entradas correspondientes para generar `adjudication.json`.

**Adjudicación de historiales**

Utiliza `config/prompts/history_adjudication.md` para consolidar los historiales de Gemma, Mistral y Llama propuestos por ChatGPT, Gemini y Grok.

Los historiales adjudicados se aplican a sus respectivos archivos de `config/feedback/`.

**Adjudicación de perfiles**

Utiliza `config/prompts/profile_adjudication.md` para consolidar las propuestas de actualización de perfiles de Gemma, Mistral y Llama.

Los perfiles adjudicados se aplican a sus respectivos archivos de `config/profiles/`, respetando sus estructuras y reglas de actualización.

ChatGPT actúa también como adjudicador, utilizando una conversación distinta de la empleada para su revisión principal.

La recuperación de revisiones incompletas debe finalizar antes de iniciar cualquiera de estas adjudicaciones.

### 9. Conservación y eliminación de archivos temporales

Los historiales provisionales y las propuestas de perfil de cada revisor son archivos temporales necesarios para las adjudicaciones.

Estos archivos deben conservarse mientras:

- Exista una revisión incompleta.
- Falte alguna adjudicación obligatoria.
- No se haya confirmado la aplicación de los historiales adjudicados.
- No se haya confirmado la actualización de los perfiles adjudicados.
- Existan errores de validación o persistencia pendientes.

Después de verificar que las adjudicaciones correspondientes se realizaron y aplicaron correctamente, solo podrán eliminarse archivos sueltos consumidos que el llamador identifique explícitamente. Los historiales y propuestas contenidos en un conjunto confirmado de revisión se conservan porque forman parte de su manifiesto y de la validación de completitud.

Los archivos de revisión principal deberán conservarse permanentemente:

- `chatgpt_review.json`
- `gemini_review.json`
- `grok_review.json`

También deberá conservarse `adjudication.json`.

La limpieza de temporales no debe eliminar los resultados permanentes ni archivos requeridos para recuperar una ejecución fallida.

### 10. Condiciones de completitud del statement

Un statement activo (`statement_010` en adelante) solamente podrá considerarse completo cuando:

- Las entradas originales hayan sido verificadas.
- ChatGPT, Gemini y Grok hayan generado revisiones completas y válidas.
- Las tres revisiones principales estén almacenadas correctamente.
- Se haya generado y validado `adjudication.json`.
- Se haya realizado y aplicado la adjudicación de historiales.
- Se haya realizado y aplicado la adjudicación de perfiles.
- Se haya comprobado la persistencia de los resultados.
- Se haya completado la limpieza de los temporales que ya no son necesarios.

La completitud operativa es independiente del valor de `decision.status` de la adjudicación final.

Después de verificar estas condiciones podrá continuarse con el siguiente `statement_index` en orden ascendente.

Estas exigencias no se aplican retroactivamente a `statement_000`–`statement_009`: ese rango conserva `HISTORICAL_COMPLETE` por la decisión operativa documentada, sin fabricar evidencia automática ni reabrir revisiones, adjudicaciones o actualizaciones históricas.

### 11. Restricciones

- No completar parcialmente una revisión usando fragmentos de diferentes ejecuciones.
- No repetir revisiones completas de modelos revisores cuyos resultados ya sean válidos.
- No iniciar adjudicaciones mientras falte alguna salida obligatoria.
- No inferir outputs ni declaraciones faltantes.
- No mezclar información entre distintos `statement_index`.
- No sobrescribir resultados válidos antes de validar el conjunto de reemplazo.
- No eliminar archivos temporales mientras sean necesarios para finalizar o recuperar las adjudicaciones.
- No marcar como completo un statement únicamente porque exista `adjudication.json`.
- No avanzar al siguiente statement mientras el actual permanezca incompleto.

## Decisiones consolidadas y pendientes externos

1. La política de aplicación es `start=end=statement_actual`: cada iteración adjudica y aplica únicamente el índice seleccionado sobre perfiles e historiales acumulados previos.
2. `ADJUDICATION_NOTES` no se persiste. El parser futuro debe extraer los tres historiales válidos y descartar ese bloque después de validarlo como salida no aplicable.
3. Cuando existen varios runs con el mismo `statement_index`, el operador debe seleccionar `run_id`; no se elige por nombre o fecha.
4. La evidencia v2 ya permite revalidar un statement previo contra el perfil efectivamente aplicado y un perfil vigente sucesor no regresivo. Una política de autenticidad o snapshots externos queda fuera del contrato actual.

La especificación vinculante para OLBM-29/30 está en `docs/olbm-29-30-persistence-sequencing-contract.md` y la decisión se registra en D-034.
