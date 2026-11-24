# Design decisions

Each entry: **Decision → Alternatives considered → Why this one → Trade-off**.

## D-001: uv for packaging, project rooted in `backend/`
- **Decision:** The Python project (`pyproject.toml`, `uv.lock`) lives in `backend/`; the repo root holds infra (Docker, Makefile, CI).
- **Alternatives:** pip + requirements.txt; Poetry; a root-level Python project.
- **Why:** uv is fast, produces a lockfile, and is the packaging choice specified in the plan. A `backend/` root keeps the future `frontend/` toolchain cleanly separate.
- **Trade-off:** Make targets must `cd backend`; contributors need uv installed.

## D-002: Strict mypy only on `app/core/`
- **Decision:** `app.core.*` is checked with `strict = true`; the rest of the app uses the default mypy settings plus the Pydantic plugin.
- **Alternatives:** Strict everywhere; no type checking.
- **Why:** `core/` holds the money-critical pure rules, where type errors are most costly. Strict mode on SQLAlchemy/FastAPI glue code adds noise for little safety.
- **Trade-off:** Looser guarantees outside `core/`; mitigated by tests and type hints throughout.

## D-003: Langfuse v2 (Postgres-only) for local tracing
- **Decision:** Self-host `langfuse/langfuse:2` with its own Postgres container.
- **Alternatives:** Langfuse v3 (needs ClickHouse, Redis and S3-compatible storage); a hosted tracing service; hand-rolled logging.
- **Why:** v2 gives per-call LLM and tool traces with a two-container footprint, which suits a local developer machine and a small demo deployment.
- **Trade-off:** v2 is the older line; migrating to v3 later means adding services. Tracing code goes behind a thin wrapper to keep that switch cheap.

## D-004: Settings via pydantic-settings, `Decimal` for money
- **Decision:** All limits and tolerances are fields on `app.config.Settings`, read from environment variables. Money values are `Decimal`, never `float`.
- **Alternatives:** Constants in the rules modules; a YAML config file; floats.
- **Why:** The plan requires every tolerance to be configurable. `Decimal` avoids floating-point drift in exact-arithmetic checks.
- **Trade-off:** Slightly more verbose than floats; Pydantic handles the parsing.
