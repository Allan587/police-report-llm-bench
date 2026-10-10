# Objetivo del proyecto — automatización local

**Proyecto:** `police-report-llm-bench`
**Estado:** especificación propuesta; pendiente de auditoría del repositorio.
**Última revisión del documento:** 2026-10-10.

## Objetivo principal
Construir en el repositorio un pipeline **local, auditable, recuperable y reproducible** para procesar casos de prueba relacionados con reportes policiales. Debe integrar generación, evaluación, retroalimentación, evolución versionada de perfiles y exportación de datasets, con Python como orquestador y Playwright como infraestructura para interacciones web permitidas.

## Decisiones de alcance
- El usuario ha elegido **Playwright y sesiones web** como arquitectura preferida; no introducir API de pago como dependencia obligatoria ni cambiar esa preferencia de forma unilateral.
- No se garantiza ni se presupone permiso de automatizar la extracción de respuestas en ChatGPT, Gemini o Grok. Cada adaptador tendrá una matriz de capacidades autorizadas, con operaciones deshabilitadas hasta validación. Nunca eludir restricciones de uso o protecciones técnicas.
- Mantener separadas las responsabilidades: interacción web, orquestación, generación, evaluación, retroalimentación, perfiles, almacenamiento y datasets.
- Proteger los datos de investigación y evitar transmitir información identificable sin revisión de permisos y anonimización.
- Conservar compatibilidad con archivos y pipelines existentes, especialmente `config/feedback/`, `config/profiles/` y `data/`.

## Cinco fases
1. **Base técnica y navegador:** auditoría, entorno, sesiones independientes, diagnósticos Playwright y seguridad.
2. **Orquestación y persistencia:** contratos, máquina de estados, cola de trabajos, SQLite, idempotencia y recuperación.
3. **Conectores y generación:** adaptadores por plataforma, capacidad/permiso por operación, tareas de generación y captura de resultados cuando estén autorizadas, alternativa asistida si no.
4. **Evaluación, feedback y perfiles:** rúbricas, calidad, evidencia, historial JSONL, versionado de perfiles y puertas de aprobación.
5. **Datasets, validación integral y operación:** exportaciones, particiones sin fuga, pruebas E2E, métricas, documentación y entrega.

## Requisitos no funcionales
- Reanudación tras errores, sin repetir silenciosamente trabajos terminados.
- Pruebas unitarias y de integración con mocks; pruebas web reales sólo con permiso y sesión gestionada por el usuario.
- Registro de `case_id`, proveedor, configuración/modelo visible, versión de prompt/perfil, iteración, timestamps, resultado, origen y errores.
- Versionado de esquemas y trazabilidad del origen de cada ejemplo de dataset.
- Privacidad: no loguear prompts confidenciales en texto plano por defecto; credenciales fuera de Git; datos sintéticos para tests.
- MacOS + `.venv`; evitar sincronizar estado volátil de Chromium y bases activas con OneDrive.

## Fuera de alcance inicial
- Entrenamiento/fine-tuning automático de pesos del proveedor.
- Agentes que esquiven controles, CAPTCHAs o límites de plataformas.
- Benchmark científicamente válido sin separar conjuntos, fijar configuraciones, auditar sesgos y definir rúbricas.
- Concurrencia masiva y despliegue en servidores externos.

## Definición de éxito
Una ejecución local de demostración con casos **sintéticos** recorre todas las etapas con un adaptador simulado, persiste resultados, se recupera tras interrupción, conserva versiones de perfiles, produce exportaciones verificables, y cuenta con documentación para conectores web autorizados. Los conectores reales solo se consideran entregados si cumplen permisos y superan pruebas específicas.
