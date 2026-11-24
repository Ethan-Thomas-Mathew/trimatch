# TriMatch: AI Invoice Reconciliation Agent

TriMatch handles supplier invoices the way an accounts-payable team does. It extracts the invoice data from a PDF, fetches the matching purchase order and goods receipts, performs a deterministic three-way match, and returns `AUTO_APPROVE`, `NEEDS_REVIEW` or `REJECT` with a plain-English explanation. It is built for finance teams that process hundreds of invoices a month.

> **Status:** Phase 0 (foundation) complete. The full README (demo, eval results, safety design) is written in Phase 7. See `docs/` for architecture, decisions and learning notes.

## Prerequisites
- Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, Git
- `make` (on Windows: via Git Bash/Chocolatey, or run the commands in the Makefile directly)

## Quick start
```bash
cp .env.example .env     # then fill in ANTHROPIC_API_KEY
make up                  # Postgres + Langfuse
cd backend && uv sync    # install dependencies
make test                # run the test suite
make lint                # ruff lint + format check
make typecheck           # mypy
```
