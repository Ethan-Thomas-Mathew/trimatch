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

## Phase 2: Extraction

### What was built
A pipeline that turns an invoice PDF into validated, structured data with a confidence for every field:
1. **Read the PDF.** `pdfplumber` pulls out the text. If there is almost none (a scan), the pages are turned into pictures instead.
2. **Ask a local model.** The text, or the pictures, go to a model running on your own computer (Ollama), together with a strict description of the answer we want.
3. **Check the answer.** The reply is parsed and validated. If it is malformed, the model gets one more try with the error shown. If it still fails, we stop and say so, rather than guess.
4. **Record the run.** Tokens, time and confidences are saved in `extraction_runs`.
A script scores the results against the answer key from Phase 1 and prints the field accuracy.

### Why it was built this way
The model is only allowed to read, never to decide. Its output has a fixed shape with no "decision" field, so even if a hidden instruction inside an invoice ("approve this!") fooled the model, it would have nowhere to put the answer. The rules that decide come in Phase 3 and are plain code.
There is also no API key anywhere: the model runs locally, so there is nothing to leak and no bill, and the invoice never leaves your machine.

### Concepts to understand
- **LLM provider interface**: a small contract ("give me a request, I return text") that different models can satisfy. It lets us swap a local model for a hosted one, or for recordings, without touching the extraction code.
- **Ollama**: a program that runs open-source language models locally and exposes them on `127.0.0.1`.
- **Structured output / constrained decoding**: telling the model "your answer must match this JSON Schema" and having the runtime enforce it token by token.
- **JSON Schema**: a standard way to describe the shape of JSON. Pydantic generates it from our Python classes.
- **Pydantic validation**: converts the model's raw JSON into typed Python objects and rejects anything that does not fit (`extra="forbid"` rejects unknown keys).
- **Confidence score**: the model's own 0 to 1 estimate of how sure it is. Useful, but unreliable on small models, so it is never trusted alone.
- **Vision model**: a model that reads images. Used when the PDF is a scan with no text.
- **Replay fixtures**: saved model answers keyed by a hash of the exact request. Tests replay them, so they are free, instant and repeatable.
- **Prompt injection**: hostile text inside the document. Our defences: (1) wrap the document as untrusted data in the prompt, (2) a schema with nowhere for a command to land, (3) approval never comes from an LLM at all.
- **Field accuracy**: the share of extracted fields that exactly match the answer key (numbers within 0.01).
- **IPv4 vs IPv6 loopback**: `127.0.0.1` and `::1` are two different "this machine" addresses. Using `localhost` can silently try the wrong one first.


## Phase 3: Matching and policy engine

### What was built
The part that actually decides. It lives in `app/core/` and is plain Python with no database, no model and no internet:
- **11 checks** (GSTIN format, GSTIN matches the vendor, tax type, arithmetic, PO exists, PO belongs to the vendor, unit price, quantity, line coverage, exact duplicate, possible duplicate). Each returns a `CheckResult` saying whether it passed, what was expected, what was found and a plain-English message.
- **The policy** turns the results into `AUTO_APPROVE`, `NEEDS_REVIEW` or `REJECT`, with a reason for each problem.
- **176 tests**, 99.5% branch coverage of `core/`, plus a test that runs all 100 dataset invoices through it.

### Why it was built this way
This is the "code decides" half of "the LLM reads, code decides". Money decisions must be exact and repeatable, and they must not be influenceable by anything written in an invoice. Because these functions take only numbers and text we give them, nothing in a PDF can change how they behave.
The design leans cautious everywhere: if something is missing or cannot be checked, the invoice goes to a human, never through.

### Concepts to understand
- **Pure function**: same inputs always give the same output, and it touches nothing outside itself (no database, files or network). Easy to test, impossible to surprise you.
- **Three-way match**: PO (ordered) vs goods receipt (received) vs invoice (billed). Quantities and prices must line up within tolerance.
- **Tolerance**: an allowed small difference (1% on price, ₹1 on totals) so rounding does not cause false alarms. Boundaries are tested: exactly 1% passes, 1.01% fails.
- **Fail closed**: when unsure, take the safe action. Here, unsure means "ask a human", never "approve".
- **Inconclusive vs failed**: "I checked and found a problem" and "I could not check" are different. Only the first can cause a REJECT.
- **Precedence**: REJECT beats NEEDS_REVIEW beats AUTO_APPROVE, so a duplicate is rejected even if the amount is also over the limit.
- **Branch coverage**: the percentage of code paths (both sides of every `if`) that tests exercise. 99.5% here, against a 90% target.
- **Property-style test**: instead of one example, test a rule that must hold for every case. Here: break each check in turn, and approval must become impossible.
- **False approval rate**: how often something that should not be approved is approved. The most important safety number; it is 0 on the dataset.
- **Frozen dataclass**: a Python object that cannot be changed after creation, so checks cannot alter their inputs.
