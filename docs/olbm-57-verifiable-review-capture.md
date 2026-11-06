# OLBM-57 — Captura verificable de revisores web simulados

## Alcance y arquitectura

`FakePageInteraction` observa texto del DOM simulado y su estado; `capture_protocol.py` lo valida y construye los siete tipos del `CompleteReviewResult` existente. `FakeSiteReviewerAdapter` implementa el puerto `ReviewerAdapter`. `vertical_flow.py` vuelve a validar el resultado bajo O-02 y usa `publish_complete_review` sin cambios. El manifiesto v2, referencia activa, barrera de tres revisores, adjudicador y aplicador son los componentes existentes. No hay almacén paralelo.

Dos gramáticas locales ilustran la diferencia entre estructura de interfaz y contrato del benchmark:

| Patrón | Navegación y evidencia | Regla de extracción |
|---|---|---|
| A, respuestas separadas | Siete GET confinados a `127.0.0.1`; cada respuesta DOM tiene un slot en la ruta, estado `COMPLETE`, hash del texto observado y hash de solicitud. | Cada JSON debe tener identidad coherente. Faltantes y errores bloquean el conjunto. |
| B, respuesta estructurada | Un GET `/bundle`; el DOM contiene un objeto `schema_version=1` con siete secciones. | Cada sección exige `slot`, identidad explícita, `output` y SHA-256 del JSON canónico. Límites ambiguos, duplicados, omisiones y contradicciones se rechazan. Una sola respuesta no acredita siete envíos independientes. |

Los slots son `main`, `history_gemma`, `history_mistral`, `history_llama`, `profile_gemma`, `profile_mistral` y `profile_llama`. Cada salida conserva `task_id`, `attempt_id`, `statement_index` y `reviewer`; `run_id` se coteja con la solicitud y el plan, y se guarda en `web_capture`. El adaptador conserva `source`, `state`, `observed_sha256`, `observed_at`, `request_sha256`, `declared_provenance`, `integrity` y `provider_authenticity` dentro del JSON publicado. El contenido observado permanece en los siete outputs; un hash correcto prueba igualdad local de bytes observados o JSON canónico, no autoría. La procedencia declarada es siempre `synthetic_loopback_page`; la autenticidad externa siempre `UNVERIFIED`.

La gramática B impone una identidad por sección y límites explícitos, pero no infiere que las siete secciones sean siete revisiones externas. Se usa el mismo `CompleteReviewResult` y el validador posterior. `web_capture` es metadato dentro de cada artefacto existente, cubierto por las huellas v2.

## Errores e incertidumbre

El estado inicial del conjunto es `PARTIAL`; pasa a `COMPLETE` solo después de validar las siete piezas y el resultado tipado. Timeout o pérdida del navegador tras posible envío se clasifican `UNCERTAIN`. Un cambio de ruta, DOM o estructura, respuesta parcial, identidad incorrecta, hash discordante, slot repetido o faltante provoca `ADAPTER_FAILED` sin publicación. Los errores del protocolo tienen códigos estables sin texto de la página. La interacción marca posible envío antes del clic y no vuelve a pulsar en el mismo intento. El flujo local no posee un diario durable de envíos externos: no se habilita ningún proveedor ni reintento autónomo de una acción real incierta. Una futura integración exige conciliación humana o evidencia durable antes de repetir.

## Matriz técnica y gates de proveedores

Esta matriz enumera evidencias **pendientes**, no capacidades observadas. La evaluación contractual histórica está en [OLBM-48](olbm-48-web-provider-permissions.md); deberá actualizarse para la cuenta, región y fecha antes de una habilitación. Ningún selector DOM real se propone.

| Proveedor | Captura requerida y dependencia UI | Sesión/identidad y causas de incompletitud | Permisos, datos y gate |
|---|---|---|---|
| ChatGPT | Demostrar siete respuestas identificables o una exportación privada con siete secciones inequívocas; documentar UI y cambios de estructura. | Cuenta/modelo visible, task/run/attempt y siete slots; truncamiento, sesión vencida, exportación parcial. | OLBM-48 identifica restricción de extracción programática en términos individuales. Revisar contrato aplicable, permiso por operación, autorización institucional y tratamiento/retención de datos. `NOT_AUTHORIZED` para lectura programática bajo el análisis histórico; integración `DISABLED`. |
| Gemini | Demostrar cómo obtener siete salidas de una cuenta elegible, por lectura permitida o exportación asistida, y validar límites. | Cuenta y producto exactos, run/attempt/slot; exportación incompleta, cambio de UI, controles Workspace. | Automatización y extracción `UNKNOWN`; verificar instrucciones y términos de cuenta, autorización institucional, privacidad y uso posterior para entrenamiento. Integración `DISABLED`. |
| Grok | Demostrar exportación privada o captura autorizada con identidad y siete salidas; no usar enlace público como prueba privada. | Cuenta/producto/región, run/attempt/slot; link parcial, sesión o UI cambiante. | OLBM-48 identifica acceso automatizado no autorizado sin permiso específico. Exigir autorización escrita aplicable, aprobación institucional, privacidad y límites de uso/entrenamiento. Integración `DISABLED`. |

Para OLBM-42/43/44 se requieren, **por plataforma y operación**, permiso contractual actual, permisos efectivos de la cuenta, autorización del titular y de la institución, clasificación y autorización de datos a transmitir, política de retención y uso posterior para entrenamiento, contrato de siete piezas, origen verificable, estrategia de conciliación de envíos inciertos y pruebas locales sintéticas seguidas de validación remota autorizada. La capacidad técnica y estas pruebas fake no satisfacen ese gate.

## Evidencia y límites

Las pruebas de `tests/test_verifiable_capture.py` recorren publicación v2, barrera 1/3–3/3, adjudicación y aplicación con el patrón B; `tests/test_portable_fake_reviewers.py` conserva el patrón A. Ambas usan solo fixtures temporales, servidor loopback y Chromium local. Los casos adversariales incluyen ausencia, duplicado, identidad, hash, límites ambiguos, contradicción, cambio de estructura/ruta, parcial y timeout. Los resultados y comandos exactos se registran en `context/advances.md`.

Los tres SKIP de OLBM-56 son puertas opt-in de Chromium: `BrowserManagerSmokeTests::test_chromium_opens_navigates_locally_and_closes` requiere `RUN_BROWSER_SMOKE=1`; `SessionManagerBrowserIntegrationTests::test_provider_storage_is_isolated_and_persists_after_restart` y `::test_session_recovers_after_unexpected_context_close` requieren `RUN_BROWSER_SESSION_TESTS=1`. No son fallos funcionales de captura; deben ejecutarse en la regresión integral o sistema objetivo compatible. Las pruebas nuevas ya abren Chromium para los dos patrones.

Investigación OLBM-57: `pytest -rs` confirmó exactamente esos tres motivos, sin dependencia faltante. Al habilitar ambas variables, las tres pruebas pasaron localmente (3 PASS, 0 FAIL, 0 SKIP). No bloquean la aceptación funcional local; deben repetirse en la validación integral final y, tras auditarlo, en el sistema operativo remoto.

Proveedores reales, API, autenticación, QA independiente, Linux/Windows y entorno remoto: `NOT_RUN`; remoto `REMOTE_NOT_CONFIGURED`. Ninguna prueba fake certifica términos, permisos, autoría externa ni compatibilidad remota.
