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

## D-005: One seeded "world" feeds both the database and the PDFs
- **Decision:** `scripts/world.py` builds vendors, POs, GRNs, historical invoices and the 100 dataset invoices (with ground truth) from one `random.Random(seed)`. `seed.py` writes it to Postgres; `generate_dataset.py` renders it to PDFs and JSON.
- **Alternatives:** Generate the PDFs first and derive database rows from the ground-truth files; keep a committed SQL dump.
- **Why:** The database and the PDFs cannot drift apart, and everything is reproducible from a single integer. A built-in validator (`validate_world`) refuses to emit a dataset whose labels contradict its contents; it caught a real labelling bug during development.
- **Trade-off:** The generator is a large module (about 800 lines). Mitigated by the validator and unit tests.

## D-006: Pin SQLAlchemy to 2.0.x
- **Decision:** `sqlalchemy>=2.0.36,<2.1`.
- **Alternatives:** Allow 2.1.
- **Why:** The plan specifies SQLAlchemy 2.0, and 2.1's compiled extensions are blocked by Windows Application Control on the development machine (import fails with "DLL load failed"). 2.0.x runs there.
- **Trade-off:** We do not get 2.1 features until the pin is lifted.

## D-007: Small additions to the section 6 schema
- **Decision:** (a) `invoice_lines.sku` (nullable); (b) a server-side `created_at` on `extraction_runs`, `match_results` and `decisions`; (c) `invoices.vendor_id` is nullable.
- **Alternatives:** Match invoice lines to PO lines by description only; rely on `audit_log` for timing; require a vendor before an invoice row exists.
- **Why:** Item codes make line-to-PO-line mapping deterministic, where free-text descriptions are fragile. Timestamps make each row self-describing for the review UI and metrics. An invoice whose vendor cannot be identified must still be stored so a human can see it.
- **Trade-off:** Slight deviation from the written table; nothing in the plan depends on their absence.

## D-008: 58 clean invoices, and scanned as an overlay
- **Decision:** The 100 invoices are 58 clean + 42 defect cases. The 8 "scanned" invoices are a rendering variant applied on top of existing scenarios (5 clean, 1 price mismatch, 1 over-billed, 1 missing PO), not extra invoices.
- **Alternatives:** Treat scanned as 8 additional invoices.
- **Why:** The section 8 list sums to 97 plus 8 scanned = 105, which contradicts "100 invoice PDFs". Overlaying scanned onto existing scenarios keeps the total at exactly 100 and lets scanned accuracy be compared on the same kinds of invoice. Clean rises from 55 to 58 to reach 100.
- **Trade-off:** Clean is 3 above the plan's figure. The 3 GSTIN cases are split into 2 invalid-format (REJECT) and 1 valid-format-but-wrong-vendor (NEEDS_REVIEW), because section 4.4 only rejects on invalid format.

## D-009: Duplicates are tested against pre-seeded historical invoices
- **Decision:** The 5 exact and 3 possible duplicates point at 8 "historical" approved invoices seeded in the database, not at other invoices in the dataset.
- **Alternatives:** Make duplicates depend on the order in which the dataset is processed.
- **Why:** Each invoice's expected decision is then independent of processing order, and the "already invoiced" quantity check has real history to read.
- **Trade-off:** A duplicate also fails the quantity check (the goods were already billed), so ground truth lists both checks.

## D-010: Integration tests use a throwaway database
- **Decision:** Each test session creates a uniquely named database, runs the real Alembic migration into it, and drops it afterwards.
- **Alternatives:** Tests against the dev database; SQLite; mocking the database.
- **Why:** It exercises the real migration and Postgres-specific features (JSONB, check constraints) without touching dev data. CI provides Postgres as a service; an unreachable database fails CI but only skips locally.
- **Trade-off:** Needs Docker locally to run these tests.
