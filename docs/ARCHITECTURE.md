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
| `backend/app/core/matching/` | Pure rule functions returning `CheckResult` | 3 |
| `backend/app/core/policy.py` | Combines check results into a decision | 3 |
| `backend/app/extraction/` | PDF to `InvoiceExtraction`: pdf reading, prompts, service, scoring, persistence | 2 |
| `backend/app/llm/` | `LLMProvider` interface; Ollama (local), record and replay providers | 2 |
| `backend/app/agent/` | Agent loop, tools, prompts | 4 |
| `backend/app/db/` | SQLAlchemy models and sessions; Alembic migrations in `backend/alembic/` | 1 |
| `scripts/` | Synthetic world, dataset generator (`generate_dataset.py`), DB seed (`seed.py`) | 1 |
| `backend/app/api/` | FastAPI routers | 6 |
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

This document is extended as each phase lands.
