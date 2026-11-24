# Learning notes

Plain-language notes on what was built in each phase, why, and the concepts worth understanding.

## Phase 0: Foundation

### What was built
The empty-but-working shell of the project: a Python project with linting, type checking and tests; a Docker setup that starts a database and a tracing tool; and an automated pipeline (CI) that runs the checks on every push. The only real code is a `/health` endpoint and a settings class, there so the tools have something to check.

### Why it was built this way
Setting up the safety net first means every later phase is checked automatically from day one. It is much cheaper to have the linter and tests running before there is code than to retrofit them onto a large codebase.

### Concepts to understand
- **uv**: a fast Python package and environment manager. It reads `pyproject.toml` and writes `uv.lock`, so everyone installs identical versions.
- **Lockfile**: a file that pins the exact version of every dependency, so builds are repeatable.
- **ruff**: a linter (finds likely bugs and style problems) and formatter (auto-formats code) in one tool.
- **mypy**: a static type checker. It reads your type hints and catches mismatches without running the code. "Strict" mode requires hints everywhere.
- **pytest**: the standard Python test runner. A test is a function whose name starts with `test_` and uses `assert`.
- **pre-commit**: runs checks (formatting, secret detection) automatically before each `git commit`.
- **Docker Compose**: starts several containers (here Postgres and Langfuse) from one `docker-compose.yml`.
- **CI (GitHub Actions)**: a server that re-runs lint, type-check and tests on every push, so broken code is caught before it merges.
- **pydantic-settings**: loads configuration from environment variables into a typed, validated Python object.
- **Decimal**: Python's exact decimal number type. Unlike `float`, `0.1 + 0.2 == 0.3`, which matters for money.
- **Environment variable / `.env`**: configuration (including secrets) kept outside the code. `.env.example` documents the variables; the real `.env` is never committed.
