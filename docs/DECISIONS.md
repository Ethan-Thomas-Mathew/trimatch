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

## D-011: Local LLM only, no API keys anywhere (deviates from the plan's "Anthropic SDK")
- **Decision:** The project has no API-key setting and no hosted-LLM dependency. Extraction talks to a local Ollama server (`LLM_PROVIDER=ollama`) or replays recorded answers (`LLM_PROVIDER=replay`). The `anthropic` package was removed.
- **Alternatives:** Anthropic SDK with a spend-capped key (the original plan); a provider interface with Claude as one option.
- **Why:** The goal was the safest possible setup for a public repository: with no key there is nothing to leak, no bill to run up, and invoice data never leaves the machine. The `LLMProvider` interface keeps the door open: adding a hosted provider later is one new file, with no change to extraction code.
- **Trade-off:** Local 7B models are less accurate than frontier hosted models, especially on scans, so the plan's accuracy targets (95% digital, 85% scanned) may be missed. Real numbers will be reported either way. The "built on Claude" talking point is replaced by "model-agnostic design".

## D-012: A remote LLM host is refused unless explicitly allowed
- **Decision:** Settings validation fails if `OLLAMA_HOST` is not loopback, unless `ALLOW_REMOTE_LLM=true`.
- **Alternatives:** Trust whatever URL is configured.
- **Why:** A typo or a stray environment variable must not send invoice contents (vendor names, GSTINs, amounts) to another machine.
- **Trade-off:** One extra setting for people who really do run Ollama on another host.

## D-013: Per-field confidence is self-reported by the model
- **Decision:** Every scalar field is `Scored[T]` = `{value, confidence}`; the model reports the confidence in the same JSON object. The policy engine will compare the lowest one to `MIN_CONFIDENCE`.
- **Alternatives:** Token log-probabilities; a second "verifier" pass; no confidence.
- **Why:** It is the only approach that works uniformly for text and vision models, and it costs no extra call.
- **Trade-off:** Self-reported confidence is poorly calibrated, especially on small models. It is a weak signal, which is why approval also requires every deterministic check to pass. Phase 5 will measure how well confidence predicts errors, and report it honestly.

## D-014: Model output is constrained, validated, and never repaired silently
- **Decision:** The model is given a flat JSON Schema (constrained decoding). The reply is parsed into Pydantic with `extra="forbid"`. If it fails, it is retried exactly once with the error shown; a second failure raises `ExtractionFailedError`, which later sends the invoice to a human.
- **Alternatives:** Best-effort regex repair; unlimited retries.
- **Why:** A guess that looks valid is worse than a clear failure when money is involved. The schema has no decision, approval or instruction field, so a prompt injection has nothing to act through (defence by design, layered on the untrusted-data prompt wrapper).
- **Trade-off:** Some invoices fail extraction that a lenient parser would have passed. They go to review, which is the safe direction.

## D-015: `127.0.0.1` instead of `localhost` for local services
- **Decision:** Default database and Langfuse URLs use `127.0.0.1`.
- **Alternatives:** `localhost`; publish the container ports on all interfaces.
- **Why:** Ports are published on IPv4 loopback only (safer). On Windows `localhost` resolves to IPv6 `::1` first, which has no listener, and each connection waited 5 s for it to time out. The test suite went from 17 s to unusably slow until this was found.
- **Trade-off:** None.

## D-016: Phase 2 acceptance is only partly met (no model run yet)
- **Decision:** Phase 2 is recorded as "built and tested, accuracy unmeasured". The plan's criterion "a first field-accuracy number is produced" is NOT met, because no local model has been run.
- **Alternatives:** Declare it done; write a regex extractor tuned to our own invoice layouts and report its score.
- **Why:** A number from a parser written by the same person who wrote the generator would be near 100% and say nothing about real invoices. Reporting no number is more honest than reporting a flattering one.
- **Trade-off:** The extraction half of the pipeline stays unproven until Ollama (or another provider) is run with `scripts/run_extraction.py`. The matching and policy phases do not depend on it and are verified with perfect extractions from the answer key.

