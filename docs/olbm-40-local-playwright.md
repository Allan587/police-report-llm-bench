# OLBM-40 — infraestructura Playwright local

Fecha: 2026-10-30. Estado: `READY_FOR_QA` local, sin dictamen QA independiente.

`automation/browser/local_runtime.py` reutiliza `SessionManager` y
`BrowserManager`. Un `LocalBrowserRuntime` abre un contexto persistente
separado por `Reviewer` (ChatGPT, Gemini, Grok), instala una guarda de
solicitudes para HTTP loopback y una ruta WebSocket que cierra toda conexión
con código 1008 antes de conectar al servidor. Ambas guardas se instalan
antes de crear la página; si alguna instalación falla, se cierra el contexto
y `open()` falla. Entrega un `LocalReviewBrowser` con una
página. `async with` cierra todos los contextos incluso ante excepciones.
`cancel()` cierra el contexto de ese revisor. Los perfiles se configuran
fuera del checkout mediante `profile_root`; `SessionManager` rechaza rutas
sincronizadas conocidas y la nueva capa rechaza cualquier perfil dentro de un
checkout Git. El navegador no visita plataformas externas.

`BrowserOperationState` representa `READY`, `WAITING_FOR_USER`,
`WAITING_FOR_RESPONSE`, `RESPONSE_UNCERTAIN`, `FAILED` y `CANCELLED`.
`require_user()` modela una sesión expirada y `user_resumed()` permite
reanudarla manualmente. `begin_response_wait()` registra que una interacción
podría haberse enviado; pérdida del navegador, timeout o cancelación durante
la espera deja `RESPONSE_UNCERTAIN`. Ese estado no tiene reenvío automático.
El caller futuro deberá reconciliarlo antes de repetir una solicitud externa.

`LocalBrowserTimeouts` controla navegación y espera. La navegación acepta
solo `http://127.0.0.1:<puerto>` o IPv6 `::1` con puerto explícito; la guarda
de rutas bloquea también redirects y subrecursos externos. `context.route()`
no protege WebSockets; `context.route_web_socket("**/*", ...)` deniega tanto
`ws://` como `wss://` sin llamar a `connect()`. Los errores
devueltos son textos fijos sin URL, cuerpos, cookies ni tokens. El módulo no
persiste respuestas, no autentica usuarios y no implementa DOM de proveedores.

## Contratos y límites

El puerto vigente sigue siendo
`ReviewerAdapter.execute(ReviewerExecutionRequest) -> CompleteReviewResult`.
Esta capa solo proporciona operaciones y estados de navegador que podrá usar
un adaptador futuro. No se modificaron `ReviewerAdapter`, los siete resultados,
el vertical sintético, O-01/O-02, `WorkflowLease`, ni JSON/JSONL. Un futuro
adaptador necesita preparación del contexto, autorización por proveedor,
parser estricto, consumo de errores y cancelación, y reconciliación de envíos
inciertos según OLBM-35. OLBM-41 no está implementado.

`context/prompts/phase-03-platform-adapters-generation.md` aún contiene un
criterio heredado de BD/generación. Para este incremento prevalecen AGENTS.md,
la persistencia JSON/JSONL aprobada y el alcance explícito de OLBM-40; no se
introdujo SQL, generación ni otro orquestador. Ninguna capacidad web real
queda autorizada por las pruebas locales. Remoto: `REMOTE_NOT_CONFIGURED`.

## Configuración y validación local

La dependencia existente es `playwright==1.60.0` en
`requirements-automation.txt`; no se instalaron paquetes ni navegadores.
Se usó Python 3.11.17 del entorno `.venv311` del worktree original y Google
Chrome for Testing 148.0.7778.96 ya instalado. Para un entorno nuevo, crear
un venv Python 3.11 fuera del repositorio o ignorado, instalar
`requirements-dev.txt` y, solo con autorización de red/instalación, el
Chromium correspondiente. Los tests usan perfiles temporales y servidor
sintético en `127.0.0.1`; requieren permiso local para sockets y Chromium.

Comando focalizado ejecutado:

```bash
TMPDIR=/private/tmp PYTHONPYCACHEPREFIX=/private/tmp/olbm40-pycache PYTHONDONTWRITEBYTECODE=1 RUN_BROWSER_SMOKE=1 RUN_BROWSER_SESSION_TESTS=1 /ruta/al/.venv311/bin/python -m pytest -q -p no:cacheprovider tests/test_local_browser_runtime.py tests/test_browser_manager.py tests/test_session_manager.py
```

Resultado final: 29 PASS, 0 FAIL, 0 SKIP. Las 12 pruebas nuevas cubren apertura,
navegación, aislamiento de cookie, bloqueo externo/redirect, timeout,
cancelación, cierre inesperado, expiración simulada, recuperación/reapertura y
saneamiento de errores. La primera corrida dentro del sandbox sin permisos
de socket/Chromium terminó con 2 FAIL y 6 ERROR por restricciones del entorno;
la repetición con permisos locales pasó. Ninguna plataforma externa se visitó.

OLBM-49 añade dos pruebas WebSocket con servidores locales independientes:
`ws://localhost` y `ws://127.0.0.1` reciben cierre por política y el servidor
destino registra cero conexiones. La suite focalizada completa, incluidas
las 29 pruebas anteriores, termina con 31 PASS, 0 FAIL, 0 SKIP. La prueba
`wss://` con TLS local queda `NOT_RUN`; la ruta WebSocket aplica a ambos
esquemas por contrato de Playwright, pero no se acredita una ejecución TLS.
