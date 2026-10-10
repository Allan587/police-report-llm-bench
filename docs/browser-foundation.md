# Browser foundation

The Phase 1 browser foundation manages Chromium processes and independent,
persistent local sessions by provider. It does not automate authentication,
prompt submission, or response extraction.

## Local setup

Use Python 3.11 for browser development. The local browser suite was validated
with Python 3.11.17 in `.venv311`; the older `.venv` remains untouched.

```bash
python3.11 -m venv .venv311
source .venv311/bin/activate
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

Do not delete or replace an existing `.venv` without first confirming that it
contains no needed local-only work.

## Profile storage

`SessionManager` uses `POLICE_BENCH_BROWSER_PROFILE_ROOT` when it is set.
Otherwise it selects a user-local state directory for the current platform:

- macOS: `~/Library/Application Support/police-report-llm-bench/browser-profiles`
- Linux: `$XDG_STATE_HOME/police-report-llm-bench/browser-profiles`, falling
  back to `~/.local/state/...`
- Windows: `%LOCALAPPDATA%\police-report-llm-bench\browser-profiles`

Use a different `profile_name` for each provider. `BrowserManager` obtains an
exclusive OS lock per name, so two processes cannot open the same persistent
profile concurrently. Profile contents can include cookies and authentication
state: never add them to Git or copy them into logs.

Known synchronized path components—including `CloudStorage`, OneDrive, iCloud,
Dropbox, and Google Drive—are rejected after resolving the path. Configure a
local-only root explicitly if the default is unsuitable:

```bash
export POLICE_BENCH_BROWSER_PROFILE_ROOT="$HOME/Library/Application Support/police-report-llm-bench/browser-profiles"
```

`SessionManager` recognizes `chatgpt`, `gemini`, and `grok` only as isolation
boundaries. This does not authorize navigation or automation on those services.
Both `headless` and `launch_timeout_ms` are explicit constructor parameters.

## Tests

Unit tests use mocks and make no network requests. Without opt-in variables,
pytest reports the three browser tests as skipped by design:

```bash
python -m pytest tests/test_browser_manager.py tests/test_session_manager.py -v -rs
```

The opt-in smoke test launches the locally installed Chromium and navigates only
to a synthetic `data:` URL:

```bash
RUN_BROWSER_SMOKE=1 python -m pytest tests/test_browser_manager.py -v
```

The session integration tests use a synthetic HTTP page served only on
`127.0.0.1`. They verify isolation, persistence across restart, and recovery
after an unexpected context close:

```bash
RUN_BROWSER_SMOKE=1 RUN_BROWSER_SESSION_TESTS=1 \
  python -m pytest tests/test_browser_manager.py tests/test_session_manager.py -v
```

On 2026-10-10, all 17 tests passed locally under Python 3.11.17. The effective
remote command remains `NOT_RUN` until the server is audited.

This foundation still does not include real-site navigation, automated login,
or any platform adapter.
