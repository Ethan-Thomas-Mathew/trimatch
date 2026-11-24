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


## D-017: The core fails closed, and "could not check" is not "found a problem"
- **Decision:** A check whose inputs are missing returns `passed=False, inconclusive=True`. The policy only REJECTs on conclusive findings (a duplicate was found, a GSTIN is present but malformed). Inconclusive and not-run checks block approval and send the invoice to a human. The policy also requires every one of the 11 checks to be present before it will approve.
- **Alternatives:** Treat unevaluable checks as passed; treat them as the failure they would have been (REJECT).
- **Why:** Approving on missing evidence is the dangerous direction, but rejecting on missing evidence is wrong too: an unknown vendor is not a duplicate. A unit test caught exactly this bug (an unknown vendor produced REJECT) before it shipped.
- **Trade-off:** More invoices reach human review when extraction or lookups are incomplete. That is deliberate.

## D-018: PO-dependent checks only run against a trustworthy PO
- **Decision:** Line coverage, price and quantity checks run only if the PO exists and belongs to the invoicing vendor. Otherwise they are skipped, and the policy explains the skip by the failed prerequisite instead of listing each as "not run".
- **Alternatives:** Match against whatever PO the invoice quotes.
- **Why:** Matching an invoice against another vendor's PO yields a pile of misleading price and quantity failures that bury the real problem (wrong PO).
- **Trade-off:** A reviewer sees one clear reason instead of four noisy ones; the skipped checks still block approval.

## D-019: An invoice with no GST at all fails the tax-type check
- **Decision:** CGST = SGST = IGST = 0 is a failure ("tax type cannot be verified"), not a pass.
- **Alternatives:** Treat zero tax as valid for exempt goods.
- **Why:** The tax-type rule cannot be verified without a tax amount, and silently passing it would let a missing-tax invoice through. Genuinely exempt supplies are rare and cheap for a human to confirm.
- **Trade-off:** Exempt-goods invoices always go to review. A known limitation, to be listed in the README.

## D-020: Rules are verified against the dataset with a perfect extraction
- **Decision:** `test_dataset_decisions.py` feeds the answer key (as if extracted perfectly) and the seeded PO/GRN/history through the real engine and policy for all 100 invoices.
- **Alternatives:** Only unit-test rules in isolation.
- **Why:** It checks the deterministic half of the system end to end, independent of any model: every decision equals its label, the false-approval rate is 0, all 5 injection invoices are not approved, and the failed checks equal the labelled ones.
- **Trade-off:** It says nothing about extraction quality, which is measured separately (and is still unmeasured, D-016).

## D-021: The agent speaks JSON actions, not provider-native tool calling
- **Decision:** Each agent turn returns one JSON action (`call_tool` with a tool name and arguments, or `finish`), constrained by a JSON Schema and validated with Pydantic. The loop is hand-written.
- **Alternatives:** Native tool calling in a provider SDK (the original plan); a framework such as LangChain.
- **Why:** The same loop works with any `LLMProvider` (a local model, recorded replays, a test double) and its transcripts can be replayed deterministically. The plan's "no LangChain, write the loop ourselves so every step can be explained" is kept. Native tool calling can be added behind the provider interface later.
- **Trade-off:** Small local models can be less reliable at emitting this shape than at native tool calls. Mitigated by constrained decoding, validation, a structured error fed back to the model, and the hard 8-call limit.

## D-022: Traces go to `audit_log`, not Langfuse (plan item not met)
- **Decision:** A `Tracer` interface records every LLM and tool call (model, tokens, latency, arguments) as `audit_log` rows, shown in the review UI. A Langfuse exporter is **not** implemented.
- **Alternatives:** Integrate the Langfuse SDK with local keys.
- **Why:** Langfuse needs public and secret keys, which conflicts with the decision to keep secrets out of the project (D-011), and it needs a running Langfuse to verify against. The audit log already satisfies "every action is audit-logged" and is visible to reviewers.
- **Trade-off:** No Langfuse dashboards. The plan's "every LLM call traced in Langfuse" is only partly met; the Langfuse container remains in compose, unused.

## D-023: A simulated model (the "oracle") tests the pipeline, and is never reported as a result
- **Decision:** `evals/oracle.py` answers extraction perfectly from the answer key and walks the agent through a sensible tool sequence; `hostile=True` makes it behave like a fully manipulated model. Eval reports from it are written to `report.oracle.md`, carry a banner, and mark field accuracy "n/a (simulated)".
- **Alternatives:** Record real model output as CI fixtures (impossible without a model); skip end-to-end tests.
- **Why:** It exercises the database, tools, rules, policy and API end to end, and proves the safety properties against an adversarial model, without needing a language model.
- **Trade-off:** It measures nothing about model quality. The README and reports say so explicitly, and `evals/report.md` is reserved for a real run.

## D-024: Single reviewer, HTTP Basic auth, random password by default
- **Decision:** One demo reviewer. If `REVIEWER_PASSWORD` is unset, a random password is generated at start-up and logged once. Constant-time comparison; one generic error for any bad credential.
- **Alternatives:** A built-in default password; sessions with a login endpoint; an auth provider.
- **Why:** The plan scopes auth to a single demo login. There is no default password to leak, and Basic auth is stateless and trivially testable.
- **Trade-off:** Basic auth over plain HTTP is only appropriate on localhost; a network deployment needs TLS (docs/DEPLOY.md). The generated password appears in the backend log, which is acceptable locally and must not be shipped as-is.

## D-025: A human approval is stored as `APPROVE`, with `decided_by=human`
- **Decision:** Migration 0002 widens the `decisions.decision` check to include `APPROVE`. A reviewer's approval writes `APPROVE`; a rejection writes `REJECT`. The policy's own values are unchanged and policy rows are never edited, so the history shows both the system's decision and the human's.
- **Alternatives:** Reuse `AUTO_APPROVE` for human approvals (misleading); overwrite the policy row.
- **Why:** Honest labelling and an append-only decision history.
- **Trade-off:** A small deviation from the three values in the plan's data model.

## D-026: Reviewing is limited to invoices that need review
- **Decision:** Only `NEEDS_REVIEW` and `FAILED` invoices can be reviewed (409 otherwise); a row lock prevents double reviews. A policy REJECT (duplicate, malformed GSTIN) cannot be approved by a human through the API.
- **Alternatives:** Let a reviewer override anything.
- **Why:** The policy's REJECT cases are the ones where approving risks paying twice or paying a non-existent supplier; those need a separate, deliberate process.
- **Trade-off:** A genuinely wrong rejection needs the invoice to be re-submitted or handled outside the tool.

## D-027: Frontend toolchain chosen to keep `npm audit` at zero
- **Decision:** React 18, React Router 7, Vite 6, Tailwind 3, no UI test framework; helper logic is tested with Node's built-in runner (`node --test`, type-stripping).
- **Alternatives:** Vitest (its dependency chain carried a critical advisory in `tinypool`); React Router 6 (advisories fixed only in 7).
- **Why:** Zero known vulnerabilities across runtime and dev tooling, and fewer dependencies to maintain. CI runs `npm audit` and fails on any finding.
- **Trade-off:** No component or browser tests. The UI is type-checked and built in CI, and the API it calls is covered by integration tests; the rendered UI has not been exercised in a browser.

## D-028: Docker, with the browser reaching only one origin
- **Decision:** `docker compose --profile app up --build` runs Postgres, the API and an nginx that serves the built UI and proxies `/api` to the backend. The backend publishes no port; the UI port is bound to `127.0.0.1`; both images run as non-root; nginx sets a strict Content-Security-Policy (`frame-src blob:` for the PDF viewer).
- **Alternatives:** Publish the API directly with CORS.
- **Why:** One origin means no CORS surface and credentials never cross sites.
- **Trade-off:** The container reaches the host's Ollama through `host.docker.internal`, which is not loopback, so the container sets `ALLOW_REMOTE_LLM=true`. That is deliberate and documented: the host is this machine.

## D-029: A rejected invoice can be approved later (supersedes part of D-026)
- **Decision:** `REJECTED` invoices can be approved by a reviewer, with a comment. If a *person* rejected it, that is all that is needed. If the *rules engine* rejected it (an exact duplicate or a malformed GSTIN), the request must also include `acknowledge_override=true`, the UI shows the reasons and requires a ticked checkbox, and the action is stored as `approve_override` with the overridden reason codes in the audit log. A rejected invoice still cannot be rejected again, and an approved, auto-approved or processing invoice cannot be reviewed.
- **Alternatives:** Keep rejections final (D-026); allow override with no extra step; a separate "reopen" state.
- **Why:** The user needs to correct a mistaken or outdated rejection (for example after the supplier sends a credit note). The rules engine's rejections are the cases where approving risks paying twice or paying a non-existent supplier, so the override is allowed but must be deliberate, explicit and visible in the history rather than accidental.
- **Trade-off:** The one-way safety of D-026 is relaxed. The guard is now a conscious acknowledgement plus a permanent audit record, not a hard block. The decision history is append-only, so the original rejection is never lost.

## D-030: Average cost is shown in rupees using a rate you configure, never a guessed one
- **Decision:** The Metrics page shows the average model cost in ₹. A cost of zero is ₹0.00 with no rate needed (the normal case with a local model). A non-zero cost is converted with `USD_TO_INR`, which has no default; without it the page shows the dollar figure and says to set the rate.
- **Alternatives:** Hard-code a rate; fetch a live rate from the internet.
- **Why:** An exchange rate changes and I cannot know today's; a stale hard-coded rate would look authoritative and be wrong. A live lookup would add an outbound network call to a system that is otherwise local-only.
- **Trade-off:** One more setting to maintain if a paid model is ever used. The database still stores cost in USD, as the plan specifies.
