# Fase 5 — Datasets, validación integral y entrega

## Misión para Codex
Cerrar el ciclo con exportaciones auditables y una demostración integral reproducible.

## Pasos
1. Construye dataset de ejemplos aprobados con origen, modelo, versiones, estado de revisión y metadatos relevantes.
2. Deduplica por caso de origen y divide train/validation/test por `source_case_id` para evitar fuga entre variantes de un mismo caso; conserva semilla y asignación.
3. Implementa reglas de anonimización/verificación de PII y excluye respuestas no aprobadas o con licencia/permiso incierto.
4. Ejecuta una demo sintética E2E con adaptador simulado: generar → evaluar → feedback → proponer/aprobar perfil → exportar → reanudar.
5. Documenta instalación macOS, configuración, comandos CLI, pruebas, soluciones de errores y límites por proveedor.
6. Reporta tasa de éxito, fallos, duración y cobertura; no inventes métricas si no se ejecutaron.
7. Actualiza `context/advances.md`, decisiones, y entrega lista de pendientes reales.

## Entregables
`automation/datasets/`, pruebas E2E, comandos reproducibles y guía operativa.

## Aceptación
- [ ] No hay solapamiento de `source_case_id` entre particiones.
- [ ] No se exportan casos no aprobados o no anonimizados.
- [ ] La prueba E2E sintética produce salidas verificables.
- [ ] Una interrupción permite continuar sin duplicación accidental.
- [ ] Estado final y restricciones de conectores reales están documentados.

## No hacer
No describir una prueba simulada como validación real de los sitios de terceros.
