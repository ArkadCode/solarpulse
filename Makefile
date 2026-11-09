PYTHON ?= python3.10
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: help install up down test lint plan ingest ingest-local inspect clean

help:
	@echo "install       create the virtualenv and install dependencies"
	@echo "up / down     start or stop the object store (console: http://localhost:9001)"
	@echo "plan          show which partitions would be fetched, without fetching"
	@echo "ingest        incremental ingestion into Bronze on the object store"
	@echo "ingest-local  ingestion into data/lake without Docker"
	@echo "inspect       Bronze summary and duplicate check"
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

inspect: install
	$(BIN)/python -m src.ingestion.inspect_bronze

test: install
	$(BIN)/pytest -q

lint: install
	$(BIN)/ruff check src tests

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache data/lake
