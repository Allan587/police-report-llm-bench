# Execution Environments

## Architectural Decision

The project follows a local-development and remote-execution architecture.

The local MacBook Air is responsible for development, unit testing, synthetic browser testing, and source control.

The remote machine is responsible for production execution of Playwright, Chromium, agents, orchestration, evaluation, feedback processing, and dataset generation.

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
