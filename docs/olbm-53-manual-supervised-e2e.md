# OLBM-53 — flujo manual supervisado local

## Alcance

El flujo acepta únicamente paquetes locales de prueba con datos sintéticos. La CLI
no contacta proveedores ni produce respuestas. ChatGPT, Gemini y Grok son
identidades de revisión dentro del fixture. La autenticidad externa permanece
`UNVERIFIED`. No hay QA independiente ni validación remota en esta entrega.

## Preparar un fixture y operar

El fixture debe contener `operational/statements.jsonl`, `runs/`,
`review_cases/`, `feedback/` y `profiles/` bajo una carpeta dedicada del
directorio temporal del sistema. Para reproducir un fixture sintético de
desarrollo, desde la raíz de este worktree:

```sh
TMPDIR=/private/tmp python -c 'from pathlib import Path; from tests.test_synthetic_adjudication_flow import ready_fixture; p=Path("/private/tmp/olbm53-demo"); p.mkdir(exist_ok=True); ready_fixture(p); (p/"inbox").mkdir(exist_ok=True)'
```

Usar el intérprete Python 3.11 del entorno dedicado para este comando y los
siguientes. La carpeta debe ser privada y estar fuera de repositorios, datos
operacionales y servicios sincronizados. La CLI rechaza raíces fuera del
directorio temporal. El ejemplo es solo un fixture; las funciones de prueba
crean datos sintéticos de entrada, no respuestas reales de revisores.

```sh
TMPDIR=/private/tmp python -m automation.review.manual_cli --root /private/tmp/olbm53-demo --inbox /private/tmp/olbm53-demo/inbox pending
TMPDIR=/private/tmp python -m automation.review.manual_cli --root /private/tmp/olbm53-demo --inbox /private/tmp/olbm53-demo/inbox prepare
TMPDIR=/private/tmp python -m automation.review.manual_cli --root /private/tmp/olbm53-demo --inbox /private/tmp/olbm53-demo/inbox status
TMPDIR=/private/tmp python -m automation.review.manual_cli --root /private/tmp/olbm53-demo --inbox /private/tmp/olbm53-demo/inbox status --package paquete.json
TMPDIR=/private/tmp python -m automation.review.manual_cli --root /private/tmp/olbm53-demo --inbox /private/tmp/olbm53-demo/inbox import paquete.json --reviewer chatgpt
```

`prepare` devuelve statement, run, revisor, task, attempt y número de intento.
Escribe `manual_request_<revisor>.json` junto al caso y una plantilla con
siete slots en el inbox. La plantilla deja `output` en `null` y
`state=PARTIAL`; el operador debe aportar contenido independiente y válido,
`observed_at` con zona, procedencia declarada y SHA-256 canónico para cada
slot. El formato exacto y las reglas están en
`docs/olbm-52-manual-review-import.md`. Los slots son `main`, tres
`history_*` y tres `profile_*`. Cada componente debe usar la identidad de
la solicitud y `state=COMPLETE`. No se aceptan referencias a archivos externos
al JSON ni paquetes parciales.

`status` expone `WAIT_OPERATOR`, `CAPTURE_INCOMPLETE`,
`CAPTURE_UNCERTAIN`, `REJECTED`, `VALID` o `PUBLISHED` según la solicitud,
el paquete opcional y el conjunto v2 confirmado. Sin paquete, una solicitud
pendiente se muestra como `WAIT_OPERATOR`. Los rechazos de `import` devuelven
un código estable por stderr y lo conservan en la solicitud para consultas
posteriores, sin mostrar contenido del paquete. `pending`
incluye el número de revisores válidos y faltantes.

## Persistencia, barrera y recuperación

La solicitud JSON preserva la identidad mientras el operador prepara el
paquete. La fuente de verdad de una revisión publicada sigue siendo
`active.json` y su conjunto v2 validado. La preparación, las publicaciones y
las adjudicaciones usan el lease del flujo. La importación primero valida el
paquete y luego llama al vertical existente, que vuelve a validar bajo lease,
publica de forma atómica e inspecciona el resultado. Ningún componente parcial
llega al publicador.

Después de reiniciar, `prepare` recupera la misma solicitud pendiente. Si la
publicación ya quedó activa, importar otra vez el mismo paquete produce
`UNCHANGED`; un paquete distinto con la misma identidad se rechaza. Si una
interrupción dejó un conjunto v2 no confirmado, O-01 solicita
`RETRY_REVIEWER`; `prepare` conserva `task_id`, incrementa
`attempt_number` y crea otro `attempt_id`. El operador debe preparar un
paquete completo nuevo para ese intento. Una captura `UNCERTAIN` nunca se
convierte en respuesta confirmada por proveedor ni autoriza retransmisión.

Tras 1/3 y 2/3 revisiones, O-01 continúa en `RUN_MISSING_REVIEWER` y el
adjudicador existente permanece bloqueado. En 3/3, O-01 pasa a
`RUN_ADJUDICATIONS`. La prueba E2E de este entregable usa el adjudicador
determinista existente para crear el bundle sintético 1+3+3 y la aplicación
existente para verificar historiales y perfiles incrementales. La CLI cubre la
entrega de revisiones; la adjudicación sintética se invoca mediante los módulos
existentes, sin crear otro orquestador ni generar adjudicaciones desde la CLI.

## Resultados de implementación

La suite focalizada de revisión, coordinación, almacenamiento v2,
adjudicación, aplicación y recuperación terminó con **452 PASS, 0 FAIL,
0 SKIP**. Incluye 10 pruebas nuevas del flujo supervisado: 1/3, 2/3, 3/3,
adjudicación, aplicación, reinicios de CLI, replay, captura incompleta o
incierta, identidades incorrectas y corte antes de activación. Comando exacto
en `context/advances.md`. Compilación, diff e integridad operacional se
registran allí.

## Límites

No se probaron proveedores reales, permisos web, autenticación, sistema remoto
ni QA independiente. La CLI opera únicamente sobre fixtures sintéticos y no
prepara datos policiales. El archivo de solicitud no es prueba de entrega
externa. Si el inbox o sus ancestros pueden ser alterados por procesos
concurrentes hostiles, el operador debe asegurar ese entorno antes de usarlo.
La aplicación incremental sigue las restricciones de los contratos existentes,
incluidos los datos de perfil e historial requeridos y la protección de
statements 000–009.
