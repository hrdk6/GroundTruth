# GroundTruth. `make help` lists targets.
#
# Windows note: GNU make is not installed by default. `./make.ps1 <target>`
# runs the same commands from PowerShell. Keep the two in sync.

.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE   ?= docker compose
BACKEND   ?= cd backend && uv run
CONFIG    ?= configs/baseline.yaml
SPLIT     ?= dev
MODE      ?= retrieval

.PHONY: help install up db-local db-local-stop down restart logs ps dev migrate revision health llm-check test test-unit \
        lint fmt typecheck check ingest eval results clean

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- environment ----------------------------------------------------------
install: ## Create the venv and install backend deps (incl. dev extras)
	cd backend && uv sync --all-extras

up: ## Start Postgres + backend, then run migrations
	$(COMPOSE) up -d --build db backend
	@echo "Waiting for the API to become healthy..."
	@$(MAKE) --no-print-directory health
	@$(MAKE) --no-print-directory migrate

db-local: ## Start Postgres without Docker (pgserver wheel; no virtualization)
	$(BACKEND) python ../scripts/local_db.py start

db-local-stop: ## Stop the Docker-free Postgres
	$(BACKEND) python ../scripts/local_db.py stop

down: ## Stop the stack (data volumes survive)
	$(COMPOSE) down

restart: ## Recreate the backend container
	$(COMPOSE) restart backend

logs: ## Tail backend logs
	$(COMPOSE) logs -f backend

ps: ## Show container status
	$(COMPOSE) ps

dev: ## Run the API natively (no container). See app/run.py for the Windows loop fix.
	$(BACKEND) python -m app.run

# --- database -------------------------------------------------------------
migrate: ## Apply Alembic migrations
	$(BACKEND) python -m alembic upgrade head

revision: ## Autogenerate a migration: make revision M="add chunks"
	$(BACKEND) python -m alembic revision --autogenerate -m "$(M)"

llm-check: ## Verify the configured LLM provider before a long run
	$(BACKEND) python ../scripts/check_llm.py

health: ## Poll /health until the API answers
	@for i in $$(seq 1 40); do \
	  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then \
	    curl -sS http://localhost:8000/health; echo; exit 0; \
	  fi; sleep 2; \
	done; echo "API did not become healthy in 80s"; exit 1

# --- quality --------------------------------------------------------------
test: ## Run the full test suite
	$(BACKEND) python -m pytest -q

test-unit: ## Run tests that need no database
	$(BACKEND) python -m pytest -q -m "not integration"

lint: ## Lint with ruff
	$(BACKEND) python -m ruff check .
	$(BACKEND) python -m ruff format --check .

fmt: ## Format with ruff
	$(BACKEND) python -m ruff format .
	$(BACKEND) python -m ruff check --fix .

typecheck: ## Type-check with mypy
	$(BACKEND) python -m mypy app evals

check: lint typecheck test ## Everything CI runs

# --- pipeline -------------------------------------------------------------
ingest: ## Ingest the corpus: make ingest CONFIG=configs/baseline.yaml
	$(BACKEND) python -m app.ingestion.run --config ../$(CONFIG)

eval: ## Run an evaluation: make eval CONFIG=... SPLIT=dev MODE=retrieval|full
	$(BACKEND) python -m evals.runner --config ../$(CONFIG) --split $(SPLIT) --mode $(MODE)

results: ## Regenerate the README results table from experiments/
	$(BACKEND) python ../scripts/generate_results_table.py

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache
