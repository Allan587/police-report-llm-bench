# OLBM-56 / F3-15 — Playwright portable con revisores simulados

Fecha: 2026-11-06. Alcance: implementación y pruebas locales con datos sintéticos. QA independiente `NOT_RUN`; remoto `REMOTE_NOT_CONFIGURED`.

## Arquitectura y contrato

`load_config` valida rutas y Chromium; `InputAdapter.prepare` copia entradas JSON/JSONL válidas a un espacio aislado. `BrowserSessionFactory` crea un `BrowserManager` por operador, revisor e intento. `LocalFakeBrowserSession` abre un perfil nuevo, instala guardas HTTP y WebSocket, y entrega `LocalReviewBrowser` a `FakePageInteraction`. `FakeSiteReviewerAdapter` visita las siete rutas del servidor simulado, observa siete respuestas completas, comprueba task, attempt, statement, reviewer y run, y construye `CompleteReviewResult`. `run_synthetic_reviewer_once` vuelve a validar el resultado bajo O-02 y `publish_complete_review` conserva la responsabilidad exclusiva del manifiesto v2 y su referencia activa. La barrera de tres revisores, adjudicador sintético y aplicación incremental existentes completan el flujo. No hay un segundo motor, orquestador ni almacén.

Las siete rutas son `main`, `history_gemma`, `history_mistral`, `history_llama`, `profile_gemma`, `profile_mistral`, `profile_llama`. Cada respuesta debe contener un objeto JSON completo y atribuible al mismo intento. Las páginas fake de F3-02 tienen un marcador DOM propio; el adaptador no fabrica artefactos ausentes. El callback de fixtures en las pruebas crea respuestas sintéticas antes de visitar el sitio. El contenido observado proviene del DOM simulado, y su SHA-256 se registra en `ObservedResponse`; la publicación recalcula sus propios hashes v2.

## Configuración y destinos

El JSON v1 de OLBM-55 configura entradas, salida, `engine=chromium`, modo headless, locale, timeout y raíz de perfiles. `doctor` valida el entorno antes de `prepare` y de una ejecución. El puente F3-15 es solo de pruebas: recibe un origen HTTP explícito en `127.0.0.1`, sin path, query, credenciales ni fragmento. Solo admite GET a una de las siete rutas del revisor actual en ese mismo origen. Aborta cualquier otra petición y todos los WebSocket. Verifica URL final y marcador fake antes de escribir el prompt. Rechaza perfiles ya existentes para evitar cargar estados autenticados. Un nuevo attempt usa un perfil nuevo; cierre y excepciones liberan el manager. No hay CDP remoto, URL de proveedor, conexión entre máquinas ni CLI de acceso real. El valor de una configuración editable nunca equivale a autorización.

Chromium real fue ejercitado en macOS. Linux, Windows, Firefox, WebKit y máquina remota `NOT_RUN`. Una ruta de perfil en carpeta sincronizada o checkout Git se rechaza por OLBM-55. El navegador puede necesitar dependencias de sistema y permiso de lanzamiento en otro entorno; `doctor` no sustituye la auditoría remota.

## Ejecución reproducible con fakes

Desde un entorno Python 3.11 con Playwright y Chromium instalados, ejecutar `PYTHONDONTWRITEBYTECODE=1 <python311> -m pytest -q -p no:cacheprovider tests/test_portable_fake_reviewers.py`. La suite crea dos layouts sintéticos en el temporal de pytest, escribe dos configuraciones JSON v1 independientes, llama a `InputAdapter.prepare`, levanta `FakeSiteServer` en un puerto efímero de `127.0.0.1` y pasa `FakeSiteReviewerAdapter(session_factory=BrowserSessionFactory(config.browser), operator=...)` al driver aprobado. No requiere cuenta, red pública, datos policiales ni ruta fija del checkout. El resultado se verifica en los archivos de salida temporales.

## Fallos e incertidumbre

`FakePageInteraction` marca envío posible antes de hacer click. Timeout, cierre, cancelación, error de UI, sesión inválida, respuesta parcial o DOM inesperado impiden construir un conjunto completo. Tras envío incierto no retransmite en el mismo intento; el flujo requiere reconciliación y un nuevo intento explícito. Un error antes de enviar es distinto de un envío incierto. El publicador nunca confirma menos de siete artefactos. La publicación local idempotente no acredita ejecución externa exactamente una vez.

## Matriz de autorización para una fase futura

Consulta documental de fuentes públicas: 2026-11-06. Esta tabla no autoriza operaciones reales. El servicio/cuenta, jurisdicción contractual, aprobación institucional, titular, clasificación de datos, privacidad, retención y responsable de aprobación no fueron aportados; permanecen `UNKNOWN` en los tres casos. Se requiere evidencia documental específica antes de cambiar cualquier decisión.

| Proveedor | Servicio/cuenta y jurisdicción | Términos oficiales (consulta 2026-11-06) | Permiso de automatización | Extracción de resultados | Entrenamiento posterior | Institución, privacidad, responsable y evidencia | Decisión |
|---|---|---|---|---|---|---|---|
| ChatGPT | Plan/cuenta/jurisdicción `UNKNOWN` | [OpenAI Terms of Use](https://openai.com/policies/row-terms-of-use/); contrato empresarial aplicable `NOT_VERIFIED` | Envío/navegación `UNKNOWN`; ningún permiso del proyecto acreditado | Extracción programática prohibida por términos individuales | Uso de Output para modelos competidores prohibido; demás usos requieren contrato específico | Aprobación institucional, titular, clasificación, retención, responsable y documento `UNKNOWN` | Proyecto `UNKNOWN`; extracción web individual `NOT_AUTHORIZED` |
| Gemini | Gemini Apps; cuenta/jurisdicción `UNKNOWN` | [Google Terms](https://policies.google.com/terms), [términos por servicio](https://policies.google.com/terms/service-specific), [política de IA](https://policies.google.com/terms/generative-ai/use-policy) | Instrucciones legibles por máquina y cuenta no verificadas: `UNKNOWN` | Captura automatizada `UNKNOWN` | Términos generales restringen usar contenido generado para desarrollar modelos de ML | Aprobación institucional, titular, clasificación, retención, responsable y documento `UNKNOWN` | `UNKNOWN` |
| Grok | grok.com; cuenta/jurisdicción `UNKNOWN` | [xAI Acceptable Use Policy](https://x.ai/legal/acceptable-use-policy), [Consumer Terms](https://x.ai/legal/terms-of-service) | Medios automatizados sin autorización específica `NOT_AUTHORIZED` | Scraping/harvesting prohibidos por la política; exportación privada `UNKNOWN` | Desarrollo de modelos o productos competidores prohibido; demás usos `UNKNOWN` | Aprobación institucional, titular, clasificación, retención, responsable y documento `UNKNOWN` | Proyecto `UNKNOWN`; automatización sin permiso `NOT_AUTHORIZED` |

OLBM-48 es evidencia documental previa, no autorización efectiva. Ninguna cuenta, proveedor web real ni API fue contactada. No se enviaron datos policiales.

## Evidencia funcional y checklist QA posterior

El 2026-11-06: regresión focalizada con 19 archivos de pruebas → **521 PASS, 0 FAIL, 3 SKIP** (smokes opt-in); suite F3-15 y diagnóstico de Playwright ausente tras separar el módulo fake → **17 PASS, 0 FAIL, 0 SKIP** (16 casos nuevos + 1 diagnóstico). QA independiente `NOT_RUN`. Las cifras anteriores de OLBM-55 no forman parte de estas corridas.

Las pruebas nuevas verifican dos configuraciones locales, 21 visitas por dataset, siete observaciones por revisor, sets v2 para tres revisores, barrera 1/3–3/3, adjudicación persistida, tres historiales y perfiles, aplicación y preservación de fuentes. También ejercitan rechazo de URLs externas/arbitrarias, perfil usado, redirección, error de sesión, timeout, cierre inesperado, respuesta parcial e identidad incorrecta. F3-02 cubre streaming, timeout, cancelación, cierre y falta de artefacto; OLBM-52–55 y suites de persistencia cubren paquetes contradictorios, reintentos y replay de adjudicación.

- [ ] QA independiente del código, guardas de red y evidencia de siete salidas.
- [ ] Repetir E2E en entorno limpio con Chromium y comparar hashes operacionales.
- [ ] Auditar por separado cuenta, contrato, país, operación, privacidad, retención y aprobación institucional.
- [ ] Auditar máquina remota y repetir pruebas sintéticas allí antes de cualquier despliegue.
- [ ] Diseñar y probar reconciliación durable de un envío real incierto antes de habilitar proveedores.
