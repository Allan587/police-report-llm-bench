# OLBM-58 — reproducción desde un clon limpio

## Manifiesto de entrega

| Elemento | Valor o requisito |
|---|---|
| Baseline aprobado | `f2818c9fd0a0a87e8e163ee014423f22495160aa` |
| Rama local de entrega | `feature/f3-17-clean-clone-reproducibility` |
| Commit de entrega | Resolver con `git rev-parse HEAD` en la rama de entrega; el SHA se registra en el informe final. |
| Python | 3.11 exacto en la versión menor; validado con 3.11.17 en macOS Apple Silicon |
| Runtime | `requirements-automation.txt`: Playwright 1.60.0; trae `pyee` y `greenlet` |
| Validación | `requirements-dev.txt`: runtime, pytest 9.1.1 y pytest-asyncio 1.4.0; dependencias transitivas resueltas por pip |
| Navegador | Chromium instalado por `python -m playwright install chromium`; validado Chrome for Testing 148.0.7778.96 |
| Código versionado | `automation/`, `tests/`, `config/portable.example.json`, documentos y requisitos |
| Cada operador aporta | `config/portable.local.json`, inputs autorizados, raíces de output y perfiles privados; no se versionan |
| Servicios externos | Ninguno para la prueba sintética; solo página `data:` y servidor fake `127.0.0.1` |

El baseline y F3-11 a F3-16 son commits locales. `origin/main` estaba en
`4047600cb37b0724f4499585a64c33ad43d29997` al preparar esta entrega y
**no** contiene F3-11 a F3-17. Un clon nuevo de GitHub aún no puede ejecutar
esta funcionalidad. Para transferirla se deben integrar y publicar los commits
lineales, después de revisión y aprobación del propietario; este ticket no
realiza push, PR, merge ni rebase. La cadena verificada por `git log` es:

```text
4047600  fase 2 publicada
0b20bd2  F3-01 navegador local
5f53403  F3-02 interacción fake
4e89715  F3-10 permiso documental
3f08d31  F3-11 importador manual
f175a3c  respaldo F3-11
d67dc7c  F3-12 supervisión manual
847a40a  F3-13 adjudicación CLI
bb56fbd  F3-14 entorno portable
cd461dc  F3-15 puente Playwright fake
f2818c9  F3-16 captura verificable
```

La historia es lineal hasta el baseline, por lo que no se observó conflicto de
integración *entre esos commits*. Conflictos futuros con nuevas modificaciones
de `main` son `NOT_RUN`; no se intentó una fusión.

## Instalación desde código que ya contiene F3-17

Usar una copia independiente. Hasta que se publique la rama, obtener el commit
local por transferencia Git autorizada; `git clone` de GitHub `main` no basta.
En macOS probado:

```sh
python3.11 -m venv .venv-clean
.venv-clean/bin/python -m pip install -r requirements-dev.txt
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
.venv-clean/bin/python -m playwright install chromium
.venv-clean/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 .venv-clean/bin/python -m pytest -q -p no:cacheprovider tests/test_clean_clone_reproducibility.py
```

`PLAYWRIGHT_BROWSERS_PATH` obliga a usar el Chromium de la copia actual. La
carpeta `.playwright-browsers/` y el venv quedan fuera de Git. No se necesita
el `requirements.txt` de inferencia para CLI portable, validadores,
publicación v2, adjudicación o pruebas fake. No se encontró lectura de rutas
absolutas del checkout original en `automation/`; el fixture de prueba carga
el prompt versionado desde el checkout en ejecución.

La prueba crea **dos** datasets y configuraciones nuevos en el temporal del
sistema; el `InputAdapter` inspecciona y prepara ambos. Ejecuta `doctor`,
`prepare`, tres capturas estructuradas desde sitios fake, publicación v2 con
barreras 1/3, 2/3 y 3/3, adjudicación 1+3+3 y aplicación de tres historiales y
tres perfiles. Un nuevo proceso repite la aplicación y comprueba `UNCHANGED`
sin cambiar los hashes. No copia perfiles, cookies, secretos ni datos reales.
Los temporales de pytest son efímeros. Para conservar evidencia, pasar
`--basetemp` a una carpeta privada externa a Git bajo control del operador.

## Configuración e inputs propios

Copiar `config/portable.example.json` a `config/portable.local.json` y editar
las rutas. Las relativas se resuelven desde el archivo de configuración.
`inputs.allowed_roots` debe contener la raíz de entrada. Entrada, salida y
perfiles no pueden solaparse; los perfiles deben estar fuera del checkout y de
carpetas sincronizadas. Cambiar el nombre de operador se hace en la llamada a
`BrowserSessionFactory`, no en la configuración JSON. Usar nombres simples
alfanuméricos, guion o subrayado.

Layout de entrada requerido, ya preparado y autorizado:

```text
statements.jsonl
runs/<run_id>/metadata.json                 # opcional
runs/<run_id>/{gemma_2b,mistral,llama_3_2}/generation/draft_reports.jsonl
feedback/{gemma,mistral,llama}_history.jsonl
profiles/{gemma,mistral,llama}_profile.json
prompts/review_adjudication.md
```

Se exige `statement_index` activo desde 010, consecutivo, y un run completo e
inequívoco por índice. Los outputs deben incluir `run_id`, modelo físico,
índice y `draft_report`. Los historiales y perfiles deben ser JSON/JSONL
válidos y coherentes con los validadores posteriores. `InputAdapter` no acepta
CSV arbitrario, no crea declaraciones ni ejecuta Gemma/Mistral/Llama. La
preparación copia por staging, registra SHA-256 por archivo y un manifiesto
`portable_manifest.json` en `outputs.root`; la fuente queda intacta. La
repetición idéntica conserva la salida, mientras un input cambiado o un output
existente incompatible se rechaza.

```sh
.venv-clean/bin/python -m automation.portable doctor --config config/portable.local.json
.venv-clean/bin/python -m automation.portable prepare --config config/portable.local.json
```

`doctor` devuelve JSON `PASS` con versión Python, Playwright, número de casos,
permiso de salida y prueba local de Chromium. `prepare` devuelve casos, hash de
fuente y ruta de salida. Los resultados quedan bajo `outputs.root`, incluyendo
`review_cases/statement_010/.review_sets/`, `adjudication.json`, evidencia de
aplicación, `feedback/` y `profiles/`. La CLI de adjudicación manual exige un
fixture sintético bajo el temporal del sistema y un bundle válido aportado por
el operador; ver `docs/olbm-54-manual-adjudication-cli.md`. La prueba de este
ticket usa el adjudicador fake determinista, sin decisiones de proveedor.

## Diagnóstico y límites

| Síntoma | Acción |
|---|---|
| Configuración ausente o JSON inválido | Crear/validar `portable.local.json`; `doctor` devuelve `FAIL`. |
| Python distinto de 3.11 o Playwright distinto de 1.60.0 | Crear un venv nuevo e instalar los requisitos declarados. |
| Chromium ausente | Ejecutar `python -m playwright install chromium` con el mismo `PLAYWRIGHT_BROWSERS_PATH`. |
| Input inválido, run ambiguo o índice duplicado | Corregir los JSON/JSONL de origen; no editar la copia preparada. |
| Output no escribible o ya asociado a otro hash | Elegir una raíz privada escribible y diferente; conservar la salida anterior. |
| Perfil en Git, nube o symlink | Elegir una ruta privada externa y real. |
| Captura incierta | Conciliar el intento; no reutilizar el perfil ni reenviar automáticamente. |

La navegación fake se confina a `127.0.0.1`; una URL externa se rechaza. Los
servicios ChatGPT, Gemini y Grok reales permanecen sin autorización operativa
ni pruebas. El ensayo solo acredita macOS Apple Silicon; Linux, Windows,
otro equipo y la máquina remota son `NOT_RUN`/`REMOTE_NOT_CONFIGURED`.

## Evidencia local de esta entrega

La regresión completa en F3-17 aprobó 731 pruebas y 26 subtests, con los tres
smokes Chromium opt-in habilitados. La ejecución focal desde la copia Git
aislada aprobó 56 pruebas de instalación, preparación, navegador y flujo fake;
las tres pruebas negativas añadidas al cierre aprobaron junto con el E2E
(5/5) tanto en F3-17 como en el clon. El detalle de comandos, entorno, fallos
iniciales y límites está en `context/advances.md`. La validación de otro
equipo, Linux, Windows, remoto y proveedores reales permanece `NOT_RUN`.
