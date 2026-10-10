# Fase 4 — Evaluación, retroalimentación y perfiles

## Misión para Codex
Desarrollar evaluación reproducible, historial de feedback y actualización controlada de perfiles.

## Pasos
1. Inspecciona los formatos reales en `config/feedback/*.jsonl` y `config/profiles/*.json` y su código consumidor.
2. Alinea rúbrica con objetivos de reportes policiales: estructura, completitud, fidelidad a la fuente, contradicciones, información inventada y protección de datos. Deja umbrales configurables si no están definidos.
3. Aplica primero validaciones deterministas; los juicios semánticos requieren evidencia y pueden quedar pendientes de revisión humana.
4. Produce feedback con `case_id`, respuesta, versión de rúbrica, observación, evidencia, gravedad y estado de revisión.
5. Propón `profile_version` inmutable a partir del feedback aprobado; exige aprobación antes de activarlo. No reescribas perfiles usados por ejecuciones históricas.
6. Integra con los estados del pipeline, implementa exportación JSONL compatible y pruebas de no regresión.

## Entregables
`automation/evaluation/`, `automation/agents/evaluator.py`, `feedback.py`, `profile_optimizer.py` y pruebas.

## Aceptación
- [ ] Rúbricas y feedback son versionados y trazables.
- [ ] Se detectan ejemplos sintéticos con omisiones/contradicciones conocidas.
- [ ] Una propuesta de perfil no se activa sin aprobación.
- [ ] Exportación respeta o migra de forma documentada los esquemas existentes.
- [ ] Historial previo no se pierde.

## No hacer
No inventar ground truth ni usar automáticamente evaluaciones del mismo modelo como verdad definitiva.
