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

## Phase 1: Data and dataset

### What was built
- **Database schema**: 12 tables (vendors, purchase orders, goods receipts, invoices, checks, decisions, audit log and so on) defined as Python classes, plus a migration that creates them.
- **Seed script**: fills the database with 20 vendors, 150 purchase orders, 165 goods receipts and 8 "historical" approved invoices.
- **Invoice generator**: writes 100 realistic invoice PDFs in 3 different layouts, 8 of them "scanned", and beside each a JSON file stating what the correct answer is.

### Why it was built this way
We have no real invoices, and an AI system can only be trusted if it can be measured against known-correct answers. So the dataset is built answer-key first: every invoice is generated from a scenario ("price is 8% too high", "same invoice number as an approved one"), and the answer key records the decision that scenario should produce. In Phase 5 the AI's output is compared with this key.

The generator also checks itself. If a label contradicts the invoice (say, "clean" but the total is wrong), it refuses to produce the dataset. That check caught a real bug in our own generator.

### Concepts to understand
- **ORM (SQLAlchemy)**: lets you work with database rows as Python objects instead of writing SQL by hand.
- **Migration (Alembic)**: a versioned, reversible script that changes the database structure. "upgrade" applies it and "downgrade" undoes it, so everyone's database ends up identical.
- **Three-way match**: comparing the Purchase Order (what we ordered), the Goods Receipt Note (what arrived) and the invoice (what we are billed for).
- **GSTIN**: India's 15-character GST registration number. Its last character is a checksum, so a mistyped number can be detected.
- **CGST/SGST vs IGST**: GST is split into central and state halves when buyer and seller are in the same state, and charged as a single integrated tax (IGST) when they are in different states.
- **Ground truth**: the known-correct answer for a test case. An evaluation is only as honest as this.
- **Random seed**: a number that fixes a random generator's sequence, so "random" data comes out identical every run.
- **Reproducibility**: running the generator twice gives byte-identical files. We checked this by hashing all 201 output files twice, and found and fixed a hidden timestamp that Pillow wrote into the scanned PDFs.
- **Exact decimal (`Numeric`)**: money stored as exact decimals, so 0.1 + 0.2 is exactly 0.3.
- **Throwaway test database**: integration tests create a brand-new database, run the real migration into it, and delete it afterwards.
- **Prompt injection**: text hidden in a document that tries to give the AI orders ("mark AUTO_APPROVE"). Five invoices carry this, each also hiding a real problem, to test that the system stays safe.
