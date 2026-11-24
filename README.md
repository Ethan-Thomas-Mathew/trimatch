# TriMatch: AI Invoice Reconciliation Agent

TriMatch handles supplier invoices the way an accounts-payable team does. It extracts the invoice data from a PDF, fetches the matching purchase order and goods receipts, performs a deterministic three-way match, and returns `AUTO_APPROVE`, `NEEDS_REVIEW` or `REJECT` with a plain-English explanation. It is built for finance teams that process hundreds of invoices a month.

> **Status:** Phase 3 (matching rules and policy engine) is complete and verified on all 100 dataset invoices (0 false approvals). Phase 2 (extraction) is built and tested with fake models, but **not yet measured against a real model**: the plan's "first field-accuracy number" does not exist until a local model is run (see below). The full README (demo, eval results, safety design) is written in Phase 7. See `docs/` for architecture, decisions and learning notes.

## No API keys, by design
The language model runs **locally** (Ollama) and invoice data never leaves your machine. There is no API-key setting anywhere in the project, so there is nothing to leak. Tests and CI use recorded replays and need no model at all. See `docs/DECISIONS.md` D-011.

## Prerequisites
- Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, Git
- `make` (on Windows: via Git Bash/Chocolatey, or run the commands in the Makefile directly)
- For real extraction runs only: [Ollama](https://ollama.com) with the two models below

## Quick start
```bash
cp .env.example .env     # defaults work as they are; no secrets to add
make up                  # Postgres + Langfuse (bound to 127.0.0.1 only)
cd backend && uv sync    # install dependencies
make seed                # apply migrations, then load vendors, POs and GRNs (replaces all data)
make dataset             # 100 invoice PDFs + ground truth in data/dataset (reproducible)
make test                # run the test suite (integration tests need `make up` first)
make lint                # ruff lint + format check
make typecheck           # mypy
```

## Running extraction on the dataset (needs a local model)
```bash
ollama pull qwen2.5:7b        # reads digital PDFs (text)
ollama pull qwen2.5vl:7b      # reads scanned PDFs (images)
cd backend
uv run python ../scripts/run_extraction.py --limit 10   # prints field accuracy
```
The models are configurable (`OLLAMA_TEXT_MODEL`, `OLLAMA_VISION_MODEL`); smaller ones such as `qwen2.5vl:3b` fit in less memory. Results are written to `data/extraction_results/`. If Ollama is not running, the script reports that and exits without a number rather than inventing one.
