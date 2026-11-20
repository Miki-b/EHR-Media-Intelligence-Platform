PY ?= python
.DEFAULT_GOAL := help

.PHONY: help install data fixtures ingest fhir summarize index pipeline serve api ui test lint format eval demo clean

help: ## Show available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-11s %s\n", $$1, $$2}'

install: ## Install Python (with dev extras) and frontend dependencies
	$(PY) -m pip install -e ".[dev]"
	cd frontend && npm install

data: ## Generate synthetic raw EHR files (fixed seed)
	$(PY) scripts/generate_data.py

fixtures: ## Load committed fixture bundles and summaries into SQLite (until the pipeline lands)
	$(PY) scripts/load_fixtures.py

ingest: ## Task 1: raw files -> canonical models + audit log
	$(PY) -m app.pipeline ingest

fhir: ## Task 2: canonical models -> validated FHIR bundles
	$(PY) -m app.pipeline fhir

summarize: ## Task 3: bundles -> cached AI summaries
	$(PY) -m app.pipeline summarize

index: ## Task 4: bundles + summaries -> Chroma
	$(PY) -m app.pipeline index

pipeline: data ## Run every stage in order
	$(PY) -m app.pipeline all

api: ## Run the API on :8000
	$(PY) -m uvicorn app.api.main:app --reload --port 8000

ui: ## Run the frontend dev server on :5173
	cd frontend && npm run dev

serve: ## Run API and UI together
	$(MAKE) -j2 api ui

test: ## Run tests (offline, no API key)
	$(PY) -m pytest -m "not slow"

lint: ## Lint with ruff
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format: ## Auto-fix lint and formatting
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

eval: ## Run search and summary evaluation, writes eval/results.md
	$(PY) -m eval.run

demo: ## Full pipeline with the mock LLM, then serve
	LLM_PROVIDER=mock $(MAKE) pipeline
	$(MAKE) serve

clean: ## Delete generated data (keeps committed fixtures and cache)
	rm -rf data/ehr.db data/chroma data/rejected.jsonl reports/*.md
	rm -f data/raw/*.json data/raw/*.csv data/raw/notes/*.txt
