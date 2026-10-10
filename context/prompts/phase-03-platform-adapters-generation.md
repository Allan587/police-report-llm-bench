# Fase 3 — Adaptadores y generación

## Misión para Codex
Implementar la interfaz común de plataformas y el módulo de generación respetando las operaciones permitidas por cada proveedor.

## Pasos
1. Investiga y registra fecha, fuente oficial y conclusión sobre cada operación web de ChatGPT, Gemini y Grok. Que una interfaz sea navegable **no** autoriza scraping.
2. Define `BaseWebAdapter` y una matriz `ALLOWED / MANUAL / DISABLED` por capacidad (`open_site`, `check_session`, `prepare_prompt`, `submit_prompt`, `read_response`, `export_result`). El valor predeterminado para automatización no verificada será `DISABLED`.
3. Implementa navegación de solo lectura y sesiones a través de Playwright; operaciones de envío/recogida solo donde estén expresamente autorizadas.
4. Implementa `GenerationAgent` desacoplado de selectores HTML y con importación manual trazable si corresponde.
5. Normaliza resultado, origen, metadatos observables, timestamp y versión de prompt; registra si no se conoce el identificador preciso del modelo.
6. Implementa fixtures con HTML local/simulado para probar flujos y selectores permitidos. No usar datos policiales reales.

## Entregables
Adaptadores de `automation/browser/adapters/`, `automation/agents/generator.py`, matriz de permisos en documentación, pruebas con mocks/fixtures.

## Aceptación
- [ ] Tests del adaptador base y simulador pasan.
- [ ] Los adaptadores no autorizados bloquean operaciones restringidas.
- [ ] Una generación simulada o una importación asistida recorre orquestador y BD.
- [ ] No se almacenan cookies ni secretos en el repositorio.
- [ ] Evaluación de condiciones de plataforma documentada con fuentes y fecha.

## No hacer
No evadir CAPTCHA, rate limits, anti-bot, ni falsear identidad de navegador; no prometer extracción masiva.
