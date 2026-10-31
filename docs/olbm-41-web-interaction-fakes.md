# OLBM-41 — contrato web y páginas simuladas

Fecha: 2026-10-30. Alcance: pruebas locales sintéticas; QA independiente pendiente.

## Contrato y fronteras

`automation/browser/web_interaction.py` define `WebInteraction`, `WebResponseState`,
`ObservedResponse` y `FakePageInteraction`. Una interacción prepara un slot y
un prompt, introduce la entrada, marca posible envío antes del clic, observa
el estado DOM y recupera el contenido únicamente después de una señal
`complete`. `ObservedResponse` conserva fecha UTC, SHA-256 de solicitud y
respuesta, estado y evidencia DOM (`data-state`). No guarda cookies, URL,
cuerpo de error ni datos de sesión. `PARTIAL` nunca se trata como completo.

La capa de navegador reutiliza `LocalBrowserRuntime` y sus guardas HTTP
loopback y WebSocket OLBM-40/49. `TIMEOUT`, cierre después del posible envío,
interrupción del clic y cancelación posterior al envío impiden reenvío
automático; se requiere conciliación externa antes de un nuevo intento. Una
cancelación antes del envío queda `CANCELLED`. Errores visibles,
autenticación requerida y sesión expirada se distinguen por estado.

`automation/review/fake_site_adapter.py` es un puente **solo para pruebas**
que implementa el puerto síncrono `ReviewerAdapter.execute(...)`. Ejecuta
siete interacciones independientes: revisión principal, historiales de
Gemma/Mistral/Llama y propuestas de perfil para los mismos tres modelos. Cada
solicitud incluye slot, revisor, índice, run, tarea e intento junto al prompt
sintético; su huella es distinta. El puente exige que cada respuesta sea un
objeto JSON realmente devuelto por la página, con identidad serializada
exacta. Reutiliza `MainReviewOutput`, `HistoryProposalOutput`,
`ProfileProposalOutput` y `CompleteReviewResult`. La publicación, el
manifiesto v2, la referencia activa, el lease y la barrera de tres revisores
permanecen a cargo del flujo existente. Si falta un artefacto o falla un
validador, no se publica ningún conjunto. Metadatos técnicos no sustituyen
contenido del fake.

## Páginas y escenarios

`tests/helpers/fake_reviewer_sites.py` inicia un servidor exclusivamente en
`127.0.0.1` y ofrece tres variantes de envoltorio DOM, etiquetadas ChatGPT,
Gemini y Grok. Comparten atributos `data-role`; no afirman reproducir las
interfaces comerciales. Cada slot recibe desde el fixture un artefacto
independiente. El JavaScript local simula aceptación, respuesta completa,
streaming parcial→completo, parcial interrumpido, error visible, sesión
expirada, autenticación requerida, espera indefinida y resultado incierto.
Las pruebas ejercitan cancelación antes y después del posible envío y cierre
del contexto. Ninguna página llama a un proveedor ni abre WebSocket.

## Validación y límites

`tests/test_web_interaction_fakes.py` verifica navegación de los tres fakes,
estados, captura completa, rechazo parcial, timeout, cancelación, ausencia de
reenvío, identidad, siete huellas y siete artefactos, rechazo por contenido
faltante/identidad incorrecta y publicación por `run_synthetic_reviewer_once()`
con el almacenamiento vigente. Se ejecuta además la regresión de
`test_local_browser_runtime.py`, `test_browser_manager.py` y
`test_session_manager.py` para conservar las guardas HTTP/WebSocket.
Resultados y comandos exactos están en `context/advances.md`.

Los selectores y estados son inventados para simulación. OLBM-42/43/44 deberán
verificar permisos y capacidades por plataforma, diseñar selectores y parsers
para interfaces autorizadas, resolver preparación real del contexto y
conciliación durable de envíos inciertos. Este entregable no habilita envío,
extracción, autenticación ni operación con ChatGPT, Gemini o Grok reales.
Remoto: `REMOTE_NOT_CONFIGURED`; pruebas remotas `NOT_RUN`.

## Checklist para QA independiente

- [ ] Reproducir los tres fakes, streaming, parcial, error, expiración y autenticación.
- [ ] Reproducir timeout, cancelación y pérdida de navegador sin segundo envío.
- [ ] Alterar u omitir uno de siete artefactos y comprobar ausencia de publicación.
- [ ] Comprobar identidad, hashes, manifiesto v2 y reemplazo atómico existente.
- [ ] Repetir guardas HTTP y WebSocket y comparar datos operacionales antes/después.
- [ ] Confirmar que las páginas no representan interfaces reales verificadas.
