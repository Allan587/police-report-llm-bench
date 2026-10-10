# Remote Deployment Plan

## Objective

Deploy and execute the Playwright-based automation pipeline on the remote machine while keeping macOS as the development environment.

## Deployment Principles

1. Source code is versioned using Git.
2. Local virtual environments are never deployed.
3. Dependencies are installed independently on the target machine.
4. Browser binaries are installed for the remote operating system.
5. Persistent browser profiles remain on the remote machine.
6. Existing inference services and dependencies must not be disrupted.
7. The automation must support graceful shutdown and restart.
8. Production experiments must be resumable and auditable.

## Step 1 — Remote Audit

Collect and document:

- OS distribution and version
- CPU architecture
- Python interpreter version
- Installed browser dependencies
- Available CPU, memory and disk resources
- GUI/headless capabilities
- Network and DNS accessibility
- Filesystem permissions
- Existing inference workloads
- Existing Python environments

No system-level changes are authorized during this audit.

## Step 2 — Runtime Preparation

After compatibility verification:

- Create a dedicated automation virtual environment.
- Install automation runtime dependencies.
- Install the matching Chromium build.
- Install required operating-system libraries where authorized.
- Create private directories for browser profiles and operational state.
- Configure environment variables.
- Preserve existing inference environments.

Proposed commands, to be adapted only after the remote audit:

```bash
python3.11 -m venv .venv-automation
source .venv-automation/bin/activate
python -m pip install -r requirements-automation.txt
python -m playwright install chromium
```

If the remote OS reports missing Chromium system libraries, install them only
after explicit authorization and without changing the inference environment.
Set `POLICE_BENCH_BROWSER_PROFILE_ROOT` to a private, non-synchronized server
directory. Select `headless=True` unless the audit confirms a usable display.

## Step 3 — Browser Validation

Run synthetic tests that:

- Launch Chromium.
- Open a local or `data:` test page.
- Verify context isolation.
- Verify profile persistence.
- Verify recovery after restart.
- Verify profile locking.
- Close all resources cleanly.

Do not use external model platforms for infrastructure smoke tests.

The local equivalent passed with Python 3.11.17. Every remote validation item
in this section remains `NOT_RUN` until server access and characteristics are
available.

## Step 4 — Pipeline Deployment

- Pull reviewed changes from Git.
- Verify configuration.
- Initialize storage without deleting existing data.
- Run controlled dry-run jobs.
- Confirm logging and state recovery.
- Verify that the pipeline can be stopped and restarted.
- Confirm that external operations comply with provider permissions.

## Step 5 — Production Readiness

- [ ] Automation environment is isolated.
- [ ] Remote Chromium launches successfully.
- [ ] Required session behavior is validated.
- [ ] Secrets and profiles are protected.
- [ ] Storage survives process restarts.
- [ ] Tests pass on the remote system.
- [ ] Failure recovery is verified.
- [ ] Rollback procedure is documented.
- [ ] Operational limitations are recorded.

## Rollback Policy

Deployment failures must not alter existing inference environments.

Do not delete production profiles, datasets, database files or uncommitted changes.

Rollback must restore the previously working code version while preserving persistent operational data.
