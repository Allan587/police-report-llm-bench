# Execution Environments

## Architectural Decision

The project follows a local-development, local-first-validation and remote-execution architecture.

The local MacBook Air is responsible for every implementation and initial test of the Playwright workflow, including the complete review and adjudication data flow described below.

The remote machine is the final execution environment for Playwright, Chromium, agents, orchestration, evaluation, feedback processing and dataset generation. Its configuration begins only after the applicable local workflow has been validated; the same workflow must then pass independent remote validation.

## Local Environment

- Platform: macOS
- Python: 3.11.17
- Playwright: 1.60.0
- pytest: 9.1.1
- pytest-asyncio: 1.4.0
- Virtual environment: `.venv311` (local, not versioned)
- Browser: Google Chrome for Testing 148.0.7778.96, installed through Playwright
- Purpose: development and validation
- Remote inference dependencies are not required locally.

Local tests must not require access to external model platforms or production authentication credentials.

## Local Execution Strategy for Workflow Validation

### Required local validation before remote configuration

The Playwright automation pipeline is developed and initially tested on macOS. This is a deployment gate, not merely a convenience for unit testing.

This includes:

- Reading statements and benchmark outputs.
- Resolving the current `statement_index`.
- Reading model profiles and accumulated histories.
- Preparing reviewer inputs.
- Testing browser session management.
- Testing browser behavior and any expressly authorized reviewer interactions separately from deterministic data preparation.
- Parsing and validating reviewer results.
- Detecting incomplete reviews.
- Re-executing complete reviews for affected reviewers.
- Verifying adjudication prerequisites.
- Testing persistence and recovery using isolated test data.

Local validation must use synthetic fixtures, temporary directories or controlled copies and must not overwrite existing review cases, profiles, histories or production browser state.

Passing this gate is reported as `LOCAL_TESTED`; pending items remain `LOCAL_PENDING` or `NOT_RUN`.

### Final remote environment

The remote machine will eventually execute the same automation pipeline.

Before deployment, the operator must configure and validate:

- Remote Python environment.
- Playwright and Chromium compatibility.
- Browser runtime configuration.
- Platform authentication and session handling.
- Filesystem paths and permissions.
- Persistent storage.
- Network connectivity.
- Coexistence with existing inference workloads.

These requirements remain `REMOTE_NOT_CONFIGURED` until the remote deployment stage is expressly authorized. After configuration, each remotely executed check is reported as `REMOTE_TESTED` only when evidence exists.

### Portability requirement

Business logic must remain independent of the execution environment.

The same statement validation, reviewer input preparation, output validation and recovery rules must operate on macOS and the remote machine.

Environment-specific settings must be configurable rather than hardcoded.

### Testing policy

Real-platform testing may be conducted only for interactions permitted by the applicable services.

Successful local testing demonstrates local functionality only, not remote readiness.

## Remote Environment

The following properties remain unverified:

- Operating system and version
- CPU architecture
- Available RAM and disk capacity
- Python version
- Existing dependency management
- Graphical environment availability
- Headless Chromium compatibility
- Network accessibility
- Session authentication requirements
- Secrets and persistent storage configuration

Codex must not assume these properties.

## Dependency Strategy

Separate automation dependencies from existing model inference dependencies.

Dependency files:

- `requirements.txt`: preserve existing dependencies and current remote behavior.
- `requirements-automation.txt`: Playwright and automation runtime dependencies.
- `requirements-dev.txt`: test and development tools.

Do not overwrite or reorganize existing dependencies without auditing their consumers.

## Browser Configuration

Browser configuration must support:

- Configurable profile directory
- Configurable headless mode
- Configurable browser launch timeout
- Persistent contexts
- Exclusive profile locking
- Recovery from unexpected browser termination
- Independent profiles per provider

The default profile directory must not be inside a synchronized source repository.

Do not hardcode macOS or Linux filesystem paths.

The current implementation chooses platform-specific user state directories,
supports explicit profile-root, headless and launch-timeout parameters, and
uses the operating system's standard file-locking backend. macOS is validated;
the remote operating system remains `NOT_RUN`.

## Security Requirements

- Never commit Chromium user data directories.
- Never commit cookies, authentication state, tokens, passwords or API keys.
- Never copy production authentication state through Git.
- Store private browser profiles and secrets in restricted locations.
- Do not log confidential police-report data.
- Keep production data separate from development fixtures.
- Only enable external platform automation for operations that are authorized.

## Source Control and Deployment

Code and versionable configuration move from the local machine to the remote machine through Git.

Production session state, logs, databases and sensitive datasets are not automatically synchronized through Git.

Deployment must not overwrite existing production data.

## Local Validation — 2026-10-10

- Python 3.11.17: PASS
- Playwright/Chromium smoke using a `data:` page: PASS
- Provider storage isolation and persistence using `127.0.0.1`: PASS
- Recovery after unexpected context close: PASS
- Same-profile locking between processes: PASS
- Full opt-in suite: 17 passed, 0 skipped, 0 failed
- External model platforms: `NOT_RUN` by design
- Remote environment: `NOT_RUN`, access and audit pending

## Acceptance Criteria

- [x] Local Python 3.11 tests pass.
- [x] Browser paths are portable by configuration and platform default.
- [x] Headless mode and launch timeout are configurable.
- [x] Environment-dependent profile storage is externalized.
- [x] Existing remote inference configuration is preserved.
- [x] Unknown remote system capabilities are documented without assumptions.
- [x] Security exclusions are verified locally.
- [x] Remote deployment prerequisites are identified.
