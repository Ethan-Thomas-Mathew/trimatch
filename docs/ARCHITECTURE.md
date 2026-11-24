# Architecture

TriMatch follows one principle: **the LLM reads, code decides.** The LLM turns messy PDFs into structured data and explains results in plain English. All arithmetic, tolerances and approval decisions are made by deterministic Python in `backend/app/core/`. The agent has no tool that can approve a payment.

```
Upload PDF -> Extraction -> InvoiceExtraction (per-field confidence)
                                  |
                                  v
                       Agent loop (LLM + read-only tools)
                                  |
                                  v
                  Matching engine (pure Python) -> Policy engine
                                  |
                                  v
                    Decision + reasons + plain-English explanation
                                  |
                  AUTO_APPROVE (logged)  |  NEEDS_REVIEW -> human review UI
```

## Module map

| Path | Responsibility | Phase |
|---|---|---|
| `backend/app/config.py` | Typed settings from environment | 0 |
| `backend/app/core/matching/` | Pure rule functions returning `CheckResult`: validation, matching, duplicates, engine | 3 |
| `backend/app/core/policy.py` | Combines check results into a decision; fails closed | 3 |
| `backend/app/matching_config.py` | Builds core config from settings (kept out of `core/`) | 3 |
| `backend/app/extraction/` | PDF to `InvoiceExtraction`: pdf reading, prompts, service, scoring, persistence | 2 |
| `backend/app/llm/` | `LLMProvider` interface; Ollama (local), record and replay providers | 2 |
| `backend/app/agent/` | Agent loop, read-only tools, explanation, engine-input assembly | 4 |
| `backend/app/pipeline.py` | End-to-end orchestration and persistence | 4 |
| `backend/app/tracing.py` | `Tracer` interface; audit-log tracer | 4 |
| `backend/app/db/` | SQLAlchemy models and sessions; Alembic migrations in `backend/alembic/` | 1 |
| `scripts/` | Synthetic world, dataset generator (`generate_dataset.py`), DB seed (`seed.py`) | 1 |
| `backend/app/api/` | FastAPI routes, schemas, auth dependencies | 6 |
| `backend/app/main.py`, `logging_config.py` | App factory, middleware, JSON logging | 6, 7 |
| `frontend/` | React review UI (nginx serves it and proxies `/api`) | 6 |
| `evals/` | Evaluation harness and recorded fixtures | 5 |

## Synthetic dataset data flow (Phase 1)

```
world.build_world(seed) --> seed.py --> Postgres (vendors, POs, GRNs, history)
          |
          +----------------> generate_dataset.py --> data/dataset/invoices/*.pdf
                                                 \-> data/dataset/ground_truth/*.json
```

Both outputs come from the same seeded object, so the database always agrees with the PDFs. The check names used in ground truth (`app/schemas/enums.py`, `CheckName`) are the names the matching engine will emit in Phase 3.

## Extraction flow (Phase 2)

```
PDF bytes --> pdfplumber text --(enough text?)--> text model  --+
                  |                                              |--> JSON --> Pydantic validate
                  +--(little/no text: scan)--> page PNGs --> vision model --+          |
                                                                  invalid? retry once with the error
                                                                  still invalid? ExtractionFailedError
```

The model only ever returns the fixed `InvoiceExtraction` shape (value + confidence per field). It has no decision field and no tools. `LLMProvider` is the only seam to a model, so Ollama, replay fixtures, or any future provider are interchangeable. Defaults to local Ollama on `127.0.0.1`; a non-local host is refused unless `ALLOW_REMOTE_LLM=true`.

## Decision flow (Phase 3)

```
MatchInputs (invoice, vendor, PO, received qty, already-invoiced qty, prior invoices)
      |
      v
run_three_way_match  -- 11 checks; PO-dependent ones only if the PO is trustworthy
      |
      v
decide(results, total, min confidence, config)
      |-- exact duplicate / malformed GSTIN (conclusive) --> REJECT
      |-- any failed, inconclusive or missing check, over limit, low or unknown confidence --> NEEDS_REVIEW
      '-- everything passed, within limit, confident --> AUTO_APPROVE
```

`app/core/` imports nothing but the standard library and shared enums: no database, settings, HTTP or LLM. Mypy runs in strict mode on it.

## Agent and pipeline (Phase 4)

```
upload -> store PDF (random name) -> Invoice row (PROCESSING)
       -> extract (LLM)  --fail--> reason: extraction_failed / llm_unavailable
       -> agent loop (LLM picks read-only tools, max 8)  --limit--> reason: agent_step_limit
       -> assemble MatchInputs from the DATABASE  -> run_three_way_match -> decide(...)
       -> explanation (LLM, display only; deterministic fallback)
       -> save checks, decision, status, audit events
```

Tools run in a `READ ONLY` transaction on their own session; the pipeline writes on separate sessions. Extraction failure, agent failure and model outage only add reasons for human review.

## Review workflow (Phase 6)

`NEEDS_REVIEW` and `FAILED` invoices can be approved or rejected by a reviewer with a mandatory comment. A `REJECTED` invoice can be approved later (action `approve_override`); if the rules engine made the rejection, the request must also carry `acknowledge_override`. Each decision writes a `review_actions` row, a new `decisions` row (`decided_by=human`, kept alongside the earlier ones), the new status and an audit event (with `previous_status`, `reversal` and any overridden policy reasons), under a row lock. Approved, auto-approved and processing invoices cannot be reviewed.

## Deployment shape (Phase 7)

```
browser --> nginx :8080 (serves the UI, CSP) --/api--> backend :8000 (not published) --> Postgres
                                                                \--> Ollama on the host (optional)
```

This document is extended as each phase lands.
