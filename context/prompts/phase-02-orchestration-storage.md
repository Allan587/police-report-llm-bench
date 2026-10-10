# Fase 2 — Orquestación y persistencia

## Misión para Codex
Construir el motor determinista del pipeline sobre simuladores, sin depender de servicios web.

## Pasos
1. Revisa resultados y pruebas de fase 1.
2. Define contratos tipados para caso, ejecución, tarea, resultado, evaluación, feedback y perfil (alineados con el código real).
3. Implementa máquina de estados con transiciones permitidas, errores verificables y eventos auditables.
4. Implementa persistencia SQLite, migración/esquema versionado, operaciones atómicas y adaptadores de repositorio.
5. Agrega cola local/`asyncio`, control de concurrencia, timeout, cancelación, reintentos acotados y ejecución reanudable.
6. Implementa clave de idempotencia incluyendo repetición experimental explícita, no solo caso/modelo.
7. Prueba fallos simulados e interrupciones.

## Entregables
`automation/orchestrator.py`, `pipeline.py`, `state_machine.py`, `storage/database.py`, `storage/repository.py` y pruebas.

## Aceptación
- [ ] Un trabajo se persiste con metadatos y estado.
- [ ] El reinicio no repite una tarea completada de forma inadvertida.
- [ ] Las transiciones inválidas se rechazan.
- [ ] Reintentos, concurrencia y errores se prueban sin red.
- [ ] `advances.md` conserva comandos y resultados.

## No hacer
No mezclar lógica del navegador con almacenamiento ni asumir versionado de modelos que la UI no revela.
