PYTHON ?= python3.10
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: help install up down trigger backfill grid-plan benchmark scoring ml dashboard figures test lint plan ingest ingest-local sample inspect transform quality publish pipeline docs clean

help:
	@echo "install       create the virtualenv and install dependencies"
	@echo "up / down     start or stop everything: lake, Airflow (http://localhost:8080)"
	@echo "trigger       run the Airflow pipeline now (ingestion -> silver -> gold)"
	@echo "backfill      reprocess years in Airflow, e.g. make backfill YEARS=\"2015 2016\""
	@echo "plan          show which partitions would be fetched, without fetching"
	@echo "ingest        incremental ingestion into Bronze on the object store"
	@echo "ingest-local  ingestion into data/lake without Docker"
	@echo "sample        synthetic Bronze in data/lake (no API, no Docker)"
	@echo "inspect       Bronze summary and duplicate check"
	@echo "transform     dbt build: Silver and Gold models plus their tests"
	@echo "quality       Great Expectations report in reports/"
	@echo "publish       copy Silver and Gold from DuckDB to the lake as Parquet"
	@echo "scoring       SRS weight sensitivity + validation against NASA POWER and GSA"
	@echo "ml            train the next-day forecast, tracked in MLflow"
	@echo "pipeline      ingest -> transform -> quality -> scoring -> publish -> ml"
	@echo "dashboard     Streamlit dashboard on the host (http://localhost:8501)"
	@echo "figures       regenerate the README images in docs/images from Gold"
	@echo "docs          generate and serve dbt docs with the lineage graph"
	@echo "grid-plan     how many partitions a national grid needs (GRID=0.25)"
	@echo "benchmark     PySpark local vs DuckDB at 10-1000 grid points (needs Java 17+)"
	@echo "test / lint   run pytest and ruff"

$(BIN)/activate: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements.txt
	touch $(BIN)/activate

install: $(BIN)/activate

up:
	@test -f .env || cp .env.example .env
	docker compose up -d --build --wait

down:
	docker compose down

trigger:
	docker compose exec airflow-scheduler airflow dags trigger weather_ingestion

backfill:
	docker compose exec airflow-scheduler airflow dags trigger weather_ingestion \
		--conf '{"backfill_years": [$(shell echo $(YEARS) | tr ' ' ',')]}'

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

scoring: install
	$(BIN)/python -m src.scoring.sensitivity
	$(BIN)/python -m src.scoring.validation

ml: install
	$(BIN)/python -m src.ml.forecast

pipeline: ingest transform quality scoring publish ml

figures: install
	$(BIN)/python -m src.reporting.figures

dashboard: install
	$(BIN)/pip install -q -r dashboard/requirements.txt
	$(BIN)/streamlit run dashboard/app.py

docs: install
	$(BIN)/python -m src.transform.dbt_runner docs generate
	$(BIN)/python -m src.transform.dbt_runner docs serve --port 8081

GRID ?= 0.25
grid-plan: install
	$(BIN)/python -m src.ingestion.incremental_loader --grid $(GRID) --dry-run | grep plan_ready

benchmark: install
	$(BIN)/pip install -q -r benchmarks/requirements.txt
	$(BIN)/python -m benchmarks.generate_data --locations 1000
	$(BIN)/python -m benchmarks.run_benchmark --sizes 10 100 500 1000

test: install
	$(BIN)/pytest -q

lint: install
	$(BIN)/ruff check src tests airflow benchmarks dashboard

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache data reports dbt/target dbt/logs
