.PHONY: up down app test lint fmt typecheck eval eval-sim seed migrate dataset

UV ?= uv

up:
	docker compose up -d --wait

down:
	docker compose --profile app down

# The whole application (Postgres, API, UI): http://127.0.0.1:8080
app:
	docker compose --profile app up --build

test:
	cd backend && $(UV) run python -m pytest --cov=app/core --cov-branch --cov-fail-under=90

lint:
	cd backend && $(UV) run ruff check .. && $(UV) run ruff format --check ..

fmt:
	cd backend && $(UV) run ruff check --fix .. && $(UV) run ruff format ..

typecheck:
	cd backend && $(UV) run python -m mypy app

migrate:
	cd backend && $(UV) run python -m alembic upgrade head

# Applies migrations, then replaces all data with the deterministic seed world.
seed: migrate
	cd backend && $(UV) run python ../scripts/seed.py

# 100 invoice PDFs + ground truth under data/dataset (reproducible, gitignored).
dataset:
	cd backend && $(UV) run python ../scripts/generate_dataset.py

# Live evaluation with a real local model (needs Ollama). Writes evals/report.md.
eval:
	cd backend && $(UV) run python ../evals/run_eval.py

# Pipeline check with a SIMULATED model (no LLM needed). Writes evals/report.oracle.md.
eval-sim:
	cd backend && $(UV) run python ../evals/run_eval.py --provider oracle
