.PHONY: up down test lint fmt typecheck eval seed

UV ?= uv

up:
	docker compose up -d --wait

down:
	docker compose down

test:
	cd backend && $(UV) run python -m pytest

lint:
	cd backend && $(UV) run ruff check . && $(UV) run ruff format --check .

fmt:
	cd backend && $(UV) run ruff check --fix . && $(UV) run ruff format .

typecheck:
	cd backend && $(UV) run python -m mypy app

# Phase 1 / Phase 5 targets: wired up when those scripts exist.
seed:
	cd backend && $(UV) run python ../scripts/seed.py

eval:
	cd backend && $(UV) run python ../evals/run_eval.py
