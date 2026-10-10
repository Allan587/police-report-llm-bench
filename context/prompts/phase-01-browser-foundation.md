# Fase 1 — Base técnica y navegador

## Misión para Codex
Audita el repositorio y prepara el entorno Playwright local **sin automatizar todavía envío ni extracción de respuestas de plataformas**.

## Pasos
1. Lee instrucciones de `AGENTS.md` y documentos de contexto. Ejecuta `git status`, inspecciona árbol de archivos, tests, dependencias, `.gitignore` y contratos actuales sin modificar archivos ajenos.
2. Registra hallazgos y diferencias con el árbol propuesto en `context/advances.md`; no elimines componentes existentes.
3. Comprueba Python/venv en macOS. Documenta reconstrucción de `.venv` si apunta a un intérprete roto; no borres entornos sin aprobación.
4. Construye `BrowserManager` async para abrir/cerrar Chromium y contextos persistentes independientes. Evita perfiles concurrentes sobre un mismo directorio y almacena sesiones fuera del control de versiones.
5. Implementa `SessionManager` y diagnósticos de navegación **sin automatizar login ni superar challenges**.
6. Añade smoke test con `example.com`, mocks y guía de configuración.

## Entregables
- Módulos de `automation/browser/` (solo los necesarios).
- Pruebas `tests/` de apertura/cierre y aislamiento sin red cuando sea posible.
- Reglas `.gitignore` y documentación de seguridad.

## Aceptación
- [ ] Chromium abre y cierra usando datos de prueba.
- [ ] Dos contextos mantienen almacenamiento aislado.
- [ ] Los perfiles no quedan en Git.
- [ ] Errores de inicio de sesión y de navegador tienen mensajes claros.
- [ ] Evidencias y comandos de prueba anotados en `advances.md`.

## No hacer
No implementar scraping, extracción, bypass de controles o automatización de credenciales.
