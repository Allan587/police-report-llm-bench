# Registro de decisiones (ADR ligero)

## D-001 — Enfoque local
**Fecha:** 2026-10-10
**Decisión:** Python es el orquestador; Playwright es la capa de navegador, sin depender de n8n.
**Motivo:** control local y modularidad.
**Consecuencia:** deben implementarse persistencia, reintentos y observabilidad.

## D-002 — Interfaces web como preferencia
**Fecha:** 2026-10-10
**Decisión:** priorizar sesiones web frente a API de pago, sin asumir permiso para automatizar plataformas de terceros.
**Consecuencia:** cada adaptador documentará capacidades autorizadas; si una operación no está permitida, se deshabilita y puede quedar un paso manual trazable.

## D-003 — Contexto de Codex
**Fecha:** 2026-10-10
**Decisión:** `AGENTS.md` en raíz como instrucción automática; `context/` guarda metas, arquitectura, decisiones, avances y prompts.
**Consecuencia:** Codex debe leer documentos explícitamente; no se confunde un archivo Markdown con memoria automática.

## D-004 — Investigación reproducible
**Fecha:** 2026-10-10
**Decisión:** rúbricas versionadas, validación de feedback, separación por caso y control humano de perfiles.
**Consecuencia:** no marcar una mejora sin evidencia ni entrenar/evaluar sobre mismos casos derivados.

## D-005 — Perfiles persistentes fuera del repositorio y bloqueo exclusivo
**Fecha:** 2026-10-10
**Contexto:** el checkout está dentro de OneDrive y un perfil Chromium puede contener cookies, credenciales y archivos volátiles que no deben sincronizarse ni abrirse concurrentemente.
**Opciones:** guardar perfiles bajo el repositorio; usar contextos efímeros; exigir una raíz externa explícita con bloqueo por perfil.
**Decisión:** `BrowserManager` recibe una raíz de perfiles explícita, crea un subdirectorio por nombre y mantiene un bloqueo de sistema operativo mientras el contexto persistente está abierto.
**Consecuencias:** el llamador debe configurar una ruta local fuera de OneDrive; dos procesos no pueden compartir el mismo perfil; `.gitignore` actúa como defensa adicional, no como ubicación recomendada. El bloqueo usa `fcntl` en POSIX y `msvcrt` en Windows; solo macOS se ha validado efectivamente.
**Evidencia:** `tests/test_browser_manager.py` valida ciclo de vida, exclusividad y liberación tras un fallo; el smoke local abre Chromium con datos sintéticos.

## D-006 — SessionManager coordina proveedores sin asumir capacidades web
**Fecha:** 2026-10-10
**Contexto:** la fase 1 requiere sesiones persistentes e independientes, pero aún no existe autorización ni matriz de capacidades para navegar, autenticar, enviar o extraer contenido de plataformas externas.
**Opciones:** incorporar navegación en el administrador de sesiones; compartir un contexto; coordinar un `BrowserManager` persistente por proveedor sin lógica de plataforma.
**Decisión:** `SessionManager` acepta únicamente `chatgpt`, `gemini` y `grok` como nombres de fronteras de aislamiento, crea un `BrowserManager` por proveedor y delega completamente apertura, cierre, persistencia y bloqueo. La raíz se configura con `POLICE_BENCH_BROWSER_PROFILE_ROOT` o usa el directorio de estado del usuario apropiado para macOS, Linux o Windows; rechaza componentes conocidos de carpetas sincronizadas.
**Consecuencias:** las sesiones no comparten almacenamiento; cerrar o fallar un proveedor no impide intentar cerrar los demás; los adaptadores y permisos siguen pendientes. La detección de carpetas sincronizadas es una defensa basada en nombres conocidos y no sustituye la revisión de una ruta personalizada.
**Evidencia:** pruebas con página HTTP sintética en `127.0.0.1` verifican aislamiento y persistencia de `localStorage`; pruebas adicionales verifican reapertura tras cierre inesperado y bloqueo del mismo perfil desde otro proceso.

## D-007 — Desarrollo local y ejecución remota con dependencias separadas
**Fecha:** 2026-10-10
**Contexto:** Playwright se desarrolla en macOS, pero la ejecución final ocurrirá en una máquina remota que también contiene inferencia y cuyas características aún no han sido auditadas. Instalar el conjunto completo de inferencia en el entorno local de navegador introduce acoplamiento y riesgo innecesarios.
**Opciones:** un único archivo y entorno para inferencia, automatización y pruebas; separar dependencias y entornos conservando el archivo histórico de inferencia.
**Decisión:** preservar `requirements.txt`; declarar el runtime web en `requirements-automation.txt` y las herramientas locales en `requirements-dev.txt`. Usar `.venv311` localmente y un entorno dedicado distinto en el servidor. Transferir código mediante Git, nunca perfiles o secretos.
**Consecuencias:** la capa de navegador puede probarse sin instalar modelos; el servidor requiere auditoría antes de instalar Chromium o dependencias; la validación remota permanece `NOT_RUN`.
**Evidencia:** suite local completa con Python 3.11.17, Playwright 1.60.0 y pytest 9.1.1: 17 pruebas aprobadas, incluidas las tres pruebas Chromium opt-in y la regresión de aperturas concurrentes.

## Por resolver tras auditoría
- Formalizar esquemas y migraciones para los formatos reales de casos, feedback y perfiles ya identificados.
- Auditar el sistema operativo remoto y ejecutar allí la suite sintética; actualmente `NOT_RUN`.
- Validar efectivamente los backends y directorios predeterminados en Linux/Windows si el servidor usa uno de esos sistemas.
- Método y permisos concretos para entrada/salida de datos en cada servicio web.
- Rúbrica académica exacta y umbrales de aceptación.
- Ruta segura fuera de OneDrive para perfiles Chromium y SQLite activos.

## Plantilla para nuevas decisiones
`D-XXX — Título | Fecha | Contexto | Opciones | Decisión | Consecuencias | Evidencia`.
