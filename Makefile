.PHONY: up down test lint fmt typecheck eval seed migrate dataset

UV ?= uv

up:
	docker compose up -d --wait

down:
	docker compose down

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

# Phase 5 target: wired up when evals/run_eval.py exists.
eval:
	cd backend && $(UV) run python ../evals/run_eval.py
