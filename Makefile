PYTHON ?= python3.10
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: help install up down test lint plan ingest ingest-local sample inspect transform quality publish pipeline docs clean

help:
	@echo "install       create the virtualenv and install dependencies"
	@echo "up / down     start or stop the object store (console: http://localhost:9001)"
	@echo "plan          show which partitions would be fetched, without fetching"
	@echo "ingest        incremental ingestion into Bronze on the object store"
	@echo "ingest-local  ingestion into data/lake without Docker"
	@echo "sample        synthetic Bronze in data/lake (no API, no Docker)"
	@echo "inspect       Bronze summary and duplicate check"
	@echo "transform     dbt build: Silver and Gold models plus their tests"
	@echo "quality       Great Expectations report in reports/"
	@echo "publish       copy Silver and Gold from DuckDB to the lake as Parquet"
	@echo "pipeline      ingest -> transform -> quality -> publish"
	@echo "docs          generate and serve dbt docs with the lineage graph"
	@echo "test / lint   run pytest and ruff"

$(BIN)/activate: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements.txt
	touch $(BIN)/activate

install: $(BIN)/activate

up:
	docker compose up -d

down:
	docker compose down

plan: install
	$(BIN)/python -m src.ingestion.incremental_loader --dry-run

ingest: install
	$(BIN)/python -m src.ingestion.incremental_loader

ingest-local: install
	SOLARPULSE_STORAGE_BACKEND=local $(BIN)/python -m src.ingestion.incremental_loader

sample: install
	SOLARPULSE_STORAGE_BACKEND=local $(BIN)/python -m src.ingestion.incremental_loader \
		--synthetic --start-date 2023-01-01 --end-date 2024-12-31

inspect: install
	$(BIN)/python -m src.ingestion.inspect_bronze

transform: install
	$(BIN)/python -m src.transform.dbt_runner build

quality: install
	$(BIN)/python -m src.quality.run_quality

publish: install
	$(BIN)/python -m src.lake.publish

pipeline: ingest transform quality publish

docs: install
	$(BIN)/python -m src.transform.dbt_runner docs generate
	$(BIN)/python -m src.transform.dbt_runner docs serve --port 8081

test: install
	$(BIN)/pytest -q

lint: install
	$(BIN)/ruff check src tests

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache data reports dbt/target dbt/logs
