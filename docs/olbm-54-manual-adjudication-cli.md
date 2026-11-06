# OLBM-54 — adjudicación manual sintética desde la CLI

## Alcance y contrato

`manual_cli` recibe un bundle **aportado por el operador** y llama al puerto
`BundleAdjudicatorAdapter` y a `run_synthetic_adjudication_and_persist_once()`.
Después llama a `run_synthetic_application_once()` para aplicar y verificar los
historiales y perfiles. No calcula una decisión ni rellena entradas faltantes.
Solo acepta una raíz sintética privada bajo el temporal del sistema; no accede
a proveedores, datos policiales, navegador o servicios remotos.

El paquete JSON de adjudicación debe ser un archivo regular dentro de `--inbox`:

```json
{
  "schema_version": 1,
  "statement_index": 10,
  "run_id": "run-a",
  "review_attempts": {
    "chatgpt": {"task_id": "...", "attempt_id": "..."},
    "gemini": {"task_id": "...", "attempt_id": "..."},
    "grok": {"task_id": "...", "attempt_id": "..."}
  },
  "bundle": {
    "adjudication": {"statement_index": 10, "...": "ver contrato existente"},
    "histories": [{"model": "gemma", "...": "ver contrato existente"}],
    "profiles": [{"model_name": "gemma", "...": "ver contrato existente"}]
  }
}
```

El ejemplo muestra la estructura, no es un paquete válido: se requieren una
adjudicación conforme a `validate_adjudication`, tres historiales conforme a
`validate_history` y tres perfiles acumulados conforme a `validate_profile` y
la transición semántica. Las identidades `task_id` y `attempt_id` se obtienen
con `prepare`/`status`; no se inventan. El parser rechaza claves JSON
duplicadas, rutas fuera del inbox, enlaces simbólicos, archivos no regulares y
paquetes mayores de 2 MiB. `ADJUDICATION_NOTES` es opcional en el bundle y no
se persiste. Los hashes de los siete resultados y las identidades se conservan
en el manifiesto v2 existente.

## Comandos del operador

```sh
PYTHON=/ruta/al/entorno-python-3.11/bin/python
ROOT=/private/tmp/olbm54-demo
INBOX="$ROOT/inbox"
"$PYTHON" -m automation.review.manual_cli --root "$ROOT" --inbox "$INBOX" pending
"$PYTHON" -m automation.review.manual_cli --root "$ROOT" --inbox "$INBOX" prepare
"$PYTHON" -m automation.review.manual_cli --root "$ROOT" --inbox "$INBOX" status
"$PYTHON" -m automation.review.manual_cli --root "$ROOT" --inbox "$INBOX" import "$INBOX/chatgpt.json" --reviewer chatgpt
"$PYTHON" -m automation.review.manual_cli --root "$ROOT" --inbox "$INBOX" adjudicate "$INBOX/adjudication.json" --statement-index 10 --run-id run-a
"$PYTHON" -m automation.review.manual_cli --root "$ROOT" --inbox "$INBOX" status
```

Repetir `prepare` e `import` para Gemini y Grok con sus propios siete
artefactos; `status` muestra 1/3, 2/3 y 3/3. Intentar `adjudicate` tras 1/3
o 2/3 devuelve código 2 con el bloqueo `run_missing_reviewer`. En 3/3,
`adjudication.ready=true`. `adjudicate` exige `--statement-index` y `--run-id`
explícitos y coteja los tres `task_id`/`attempt_id` contra los sets activos.

Para una demostración reproducible sin datos reales, ejecutar
`tests/test_manual_adjudication_cli.py::test_cli_full_flow_replay_and_contradiction`.
La prueba crea entradas y paquetes **sintéticos** con los helpers de fixtures,
pero ejecuta `pending`/`prepare`/`import`/`status`/`adjudicate` como procesos CLI
independientes. Verifica bloqueo 1/3 y 2/3, 3/3, bundle final 1+3+3, seis
destinos, reinicio, replay sin efectos y contradicción rechazada. El fixture
sintético produce una decisión de prueba explícita; la CLI nunca la genera.

## Estado, errores y recuperación

`status.adjudication` muestra `ready`, acción y bloqueo O-01, completitud del
statement, bundle, ID/fase de aplicación y cada destino `history:*` y
`profile:*` como `PENDING`, `APPLIED` o `CONFLICT`. `bundle=INVALID` exige
intervención. Tras el último statement, O-01 puede indicar que no existe un
siguiente input; `statement_completion=COMPLETE` y seis `APPLIED` acreditan
el cierre del statement solicitado.

Se rechazan, sin crear adjudicación: revisiones incompletas o artefactos
faltantes, runs/attempts mezclados, paquete inválido, historiales/perfiles
faltantes, conflicto de publicación y lease ocupado (`CONTENDED`). Los errores
de paquete usan códigos como `adjudication_identity_mismatch`,
`missing_adjudication_input`, `three_histories_required`,
`three_profiles_required` e `invalid_adjudication_bundle`. Una contradicción
posterior al commit devuelve `contradictory_adjudication_bundle`.

Un reinicio antes del commit conserva la barrera 3/3. Un intento preparado se
recupera mediante el motor de adjudicación existente; si la adjudicación ya
está confirmada, la CLI continúa con la aplicación. Ante corte entre las seis
sustituciones, O-01 informa `RECOVER_APPLICATION` y la CLI reanuda la misma
aplicación. Si el estado no se valida, se detiene para intervención. Después
de `APPLIED`, la misma solicitud devuelve `UNCHANGED` tras cotejar bundle,
manifiesto, run, intentos y evidencia de completitud; no repite efectos.
Los seis reemplazos son recuperables individualmente, **no** una transacción
atómica conjunta.

## Verificación funcional final

- [ ] Confirmar `pending` y tres plantillas sin respuestas.
- [ ] Importar siete artefactos sintéticos por revisor; observar 1/3, 2/3, 3/3.
- [ ] Confirmar bloqueo antes de 3/3 y ante identidad, artefacto o entrada faltante.
- [ ] Aportar bundle válido, ejecutar `adjudicate` y comprobar 1+3+3.
- [ ] Comprobar tres historiales, tres perfiles y evidencia v2.
- [ ] Reiniciar, consultar `status`, repetir y comprobar `UNCHANGED` sin cambios de bytes.
- [ ] Probar lease, corte durante aplicación y contradicción posterior.
- [ ] Revisar hashes de `data/`, `config/feedback/`, `config/profiles/` operacionales.

QA independiente, proveedores reales, Linux/Windows y máquina remota siguen
`NOT_RUN`; la autenticidad externa de revisiones manuales es `UNVERIFIED`.
