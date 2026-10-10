# Sistema de contexto para Codex

`AGENTS.md` en la raíz es el punto de entrada que Codex detecta de forma automática cuando opera en este repositorio. Los archivos aquí NO se cargan automáticamente por estar en `context/`: Codex debe leerlos conforme a la instrucción de `AGENTS.md` o mediante una petición explícita.

Orden de lectura: `goal.md` → `architecture.md` → `decisions.md` → `advances.md` → prompt de la fase activa. Evitar cargar todos los prompts al mismo tiempo para limitar ruido/contexto.

| Archivo | Función |
|---|---|
| `goal.md` | Alcance, requisitos, límites y definición de éxito |
| `architecture.md` | Contratos, carpetas, flujo y estándares técnicos |
| `decisions.md` | Registro de decisiones y preguntas pendientes |
| `advances.md` | Estado real y evidencias por fase |
| `prompts/phase-01...05.md` | Instrucciones operativas para cada entrega |

**Ciclo:** pedir a Codex leer `AGENTS.md` y el prompt de fase → auditar estado → implementar cambios mínimos → probar → registrar evidencias en `advances.md` → revisión humana → pasar a fase siguiente.

Este paquete define una arquitectura **propuesta**, no demuestra la existencia de archivos ni funcionalidades del repositorio. La primera tarea de Codex será contrastarlo con el árbol y el historial Git reales.
