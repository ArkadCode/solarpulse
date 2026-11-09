# SolarPulse

A data platform that turns 15 years of hourly weather reanalysis from
[Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api) into a **Solar
Reliability Score (SRS)** for Colombian regions. Cities are ranked not only by mean
solar radiation but also by variability, seasonality and how often low-radiation
periods occur.

> The indicators compare solar *conditions*. They are not an estimate of real
> electricity production, which would require modeling panels, orientation and losses.

Built with free, open-source tools that run locally on Docker. Databricks is an
optional final phase.

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Ingestion and Bronze | Done |
| 2 | Silver and Gold with DuckDB + dbt, data quality, first SRS | Done |
| 3 | Airflow, CI/CD, single-command environment | Done |
| 4 | Scale to a national grid, PySpark vs DuckDB benchmark | Done |
| 5 | ML with MLflow, SRS validation, dashboard, docs | Next |
| 6 | Databricks Free Edition port (optional) | Planned |

## Quickstart

Requirements: Docker and Python 3.10+.

```bash
make up        # builds and starts everything: lake (http://localhost:9001), Airflow (http://localhost:8080)
make trigger   # runs the pipeline in Airflow: ingestion -> silver -> gold
```

Airflow runs the pipeline daily. To run the steps by hand on the host instead:

```bash
make plan      # shows which partitions are missing, without downloading
make ingest    # downloads only the missing partitions into Bronze
make transform # dbt build: Silver and Gold plus 42 data tests
make quality   # Great Expectations report in reports/
make publish   # Silver and Gold as partitioned Parquet in the lake
make test      # unit and end-to-end tests (no network or Docker needed)
```

`make pipeline` runs ingest → transform → quality → publish. `make docs` opens the dbt
docs with the lineage graph. No Docker? Set `SOLARPULSE_STORAGE_BACKEND=local` in `.env`
and everything writes to `data/lake/`; `make sample` fills it with synthetic data.

## Results

Full history 2011–2026, 10 cities, 1,388,400 valid hours, 0 quarantined.

| SRS rank | Location | Region | SRS | Mean daily GHI (kWh/m²) | Radiation rank |
| --- | --- | --- | --- | --- | --- |
| 1 | Barranquilla | Caribbean | 80.0 | 5.69 | 1 |
| 2 | Riohacha (La Guajira) | Caribbean | 79.9 | 5.58 | 2 |
| 3 | Santa Marta | Caribbean | 73.7 | 5.33 | 4 |
| 4 | Cartagena | Caribbean | 70.2 | 5.48 | 3 |
| 5 | Cúcuta | Andean | 66.4 | 5.20 | 6 |
| 6 | Medellín | Andean | 52.6 | 5.25 | 5 |
| 7 | Bogotá | Andean | 46.8 | 4.61 | 8 |
| 8 | Villavicencio | Orinoquia | 33.8 | 4.95 | 7 |
| 9 | Bucaramanga | Andean | 33.6 | 4.53 | 9 |
| 10 | Cali | Andean | 23.2 | 4.28 | 10 |

**Is the city with the most radiation also the most stable?** At the top, yes:
Barranquilla leads both rankings, virtually tied with La Guajira (fewer low-radiation
days, less day-to-day variability). Further down, stability reorders the list: Santa
Marta overtakes Cartagena thanks to lower variability and seasonality, Cúcuta overtakes
Medellín (the least seasonal city of the ten), and Bogotá overtakes Villavicencio, whose
rainy season produces the second-highest share of low-radiation days. See
[docs/methodology.md](docs/methodology.md).

## Phase 2: Silver, Gold and data quality

| Model | Layer | Load | Purpose |
| --- | --- | --- | --- |
| `stg_openmeteo__hourly` | staging | view | Rename and type Bronze, read straight from S3 |
| `fact_hourly_weather` | Silver | incremental per Bronze partition | Valid hours with lineage columns |
| `quarantine_hourly_weather` | Silver | incremental per Bronze partition | Rows that broke a rule, with the rules |
| `dim_location`, `dim_date` | Silver | table | Locations; calendar with Colombian seasons |
| `daily_solar_metrics` | Gold | incremental per affected day | Energy, diffuse ratio, volatility, heat stress |
| `monthly_solar_metrics` | Gold | table | Mean, deviation and percentiles of daily energy |
| `location_reliability_score` | Gold | table | SRS components, score and ranks per period |

Docs: [architecture](docs/architecture.md) · [data dictionary](docs/data_dictionary.md) ·
[methodology](docs/methodology.md).

### Backfills and options

```bash
make backfill YEARS="2015 2016"   # in Airflow
.venv/bin/python -m src.ingestion.incremental_loader --backfill 2015 2016
.venv/bin/python -m src.ingestion.incremental_loader --locations bogota cali
.venv/bin/python -m src.ingestion.incremental_loader --all-locations
```

## Phase 1: ingestion and Bronze

```
Open-Meteo API ──> api_client ──> extractor ──> Bronze (Parquet on S3)
                   (retries,      (raw values +   bronze/raw_openmeteo/
                    throttling)    metadata)        location_id=<id>/year=<yyyy>/data.parquet
                        ▲
              incremental_loader
         (plans missing partitions)
```

| Module | Responsibility |
| --- | --- |
| [api_client.py](src/ingestion/api_client.py) | Builds reproducible requests, throttles calls, classifies errors as retryable (network, 429, 5xx) or not (400). |
| [retry_handler.py](src/ingestion/retry_handler.py) | Exponential backoff with jitter; honors `Retry-After`. |
| [extractor.py](src/ingestion/extractor.py) | Validates the response shape and converts it to an Arrow table with extraction metadata. |
| [incremental_loader.py](src/ingestion/incremental_loader.py) | Plans which `(location, year)` partitions are missing or incomplete, fetches only those, and reports run metrics. |
| [storage.py](src/common/storage.py) | Same interface for S3 and local disk; atomic writes (temp file + rename). |

**How it stays idempotent.** The unit of work is one `(location, year)` partition, and
each one is overwritten in full. Running the same period twice gives the same result.
The tests in [tests/test_ingestion.py](tests/test_ingestion.py) prove that a rerun does
not call the API or duplicate rows, and that a backfill rewrites a partition in place.

**How it stays incremental.** Each Parquet footer stores the requested date range. The
planner reads only the footers (not the data) to decide what is missing. Only the
current year is refreshed on every run, because it grows every day.

**What Bronze keeps.** Hourly values exactly as received, plus the coordinates, elevation
and timezone returned by the API, the weather model, the full request parameters, the
extraction timestamp and the source file path. This is the start of the lineage chain.

**Failure handling.** A failed partition is logged and skipped; the others continue, and
the next run picks it up again. The process exits with a non-zero code if anything
failed, so an orchestrator can alert on it. Logs are structured JSON, one event per line.

## Phase 3: orchestration, CI/CD and observability

**Airflow 3** runs three DAGs chained by data assets, so each layer runs as soon as the
one before it changes:

```
weather_ingestion (@daily) ──Bronze asset──► silver_transformation ──Silver asset──► gold_analytics
  ingest_to_bronze                             dbt_build_silver                        dbt_build_gold
  params: backfill_years, locations                                                    → great_expectations_report
                                                                                       → publish_to_lake
```

- **Retries** with exponential backoff on every task, and an **alert callback** that
  logs a structured event and posts to `ALERT_WEBHOOK_URL` (Slack-compatible) if set.
- **Backfills** are a trigger parameter validated by a strict schema, because the values
  end up in a shell command.
- **One writer at a time:** DuckDB allows a single writer, so every task that touches the
  warehouse shares a one-slot Airflow pool.
- **Isolated dependencies:** the image installs the pipeline in its own virtualenv;
  Airflow only runs `python -m src...` commands and never imports dbt or pandas.

**Docker Compose** starts the lake, Postgres and Airflow (API server, scheduler, DAG
processor) with `make up`.

**GitHub Actions** ([ci.yml](.github/workflows/ci.yml)) runs on every pull request:
ruff, pytest on Python 3.10 and 3.12, a full `dbt build` + quality report + publish on
synthetic data (the report and dbt docs are uploaded as artifacts), an import check of
every DAG against Airflow 3.3.2, and `docker compose config`.

**Observability:** every step records status, duration, peak memory and its own counts
(partitions and rows written, dbt node statuses and per-model seconds, failed
expectations, rows quarantined per rule) in the lake under
`observability/runs/step=<step>/`, keyed by the Airflow run ID.

## Phase 4: scale and benchmark

**All 10 cities** are now ingested (1.39 M hourly rows).

**National grid.** [src/ingestion/grid.py](src/ingestion/grid.py) builds a regular grid
over a simplified outline of mainland Colombia: 94 points at 1°, 370 at 0.5° and 1,488 at
0.25°. `--grid 0.25` ingests it into a separate Bronze table, and `--max-partitions N`
caps each run so the load fits the API quota and continues the next day:

| Grid | Points | Partitions (16 years) | Approx. API calls* | Days at 10,000 calls/day |
| --- | --- | --- | --- | --- |
| 1° | 94 | 1,504 | ~39,000 | ~4 |
| 0.25° | 1,488 | 23,808 | ~620,000 | ~62 |

\*Open-Meteo counts a request for more than 2 weeks of data as several calls; one year
for one location is about 26. `make grid-plan GRID=1.0` prints the plan.

**Benchmark.** Downloading the 0.25° grid takes about two months of free quota, so the
benchmark ([benchmarks/](benchmarks/)) uses synthetic data at real volume and with the Silver schema: 15 years
for up to 1,000 grid points (131 M rows). Each engine runs the same three SQL queries
(monthly aggregation, SRS-style ranking, hourly scan) in a fresh process; peak memory
covers the whole process tree, including Spark's JVM.

| Grid points | Rows | Parquet | Engine | Startup (s) | Monthly agg. (s) | SRS ranking (s) | Hourly scan (s) | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 1.3 M | 0.02 GB | DuckDB | 0.0 | 0.01 | 0.01 | 0.01 | 149 MB |
| 10 | 1.3 M | 0.02 GB | PySpark | 6.1 | 0.45 | 0.49 | 0.18 | 1,016 MB |
| 100 | 13.1 M | 0.24 GB | DuckDB | 0.0 | 0.09 | 0.09 | 0.07 | 303 MB |
| 100 | 13.1 M | 0.24 GB | PySpark | 5.9 | 2.36 | 1.51 | 0.48 | 1,811 MB |
| 500 | 65.7 M | 1.22 GB | DuckDB | 0.0 | 0.72 | 0.60 | 0.32 | 678 MB |
| 500 | 65.7 M | 1.22 GB | PySpark | 6.2 | 7.07 | 5.15 | 1.20 | 2,798 MB |
| 1,000 | 131.5 M | 2.44 GB | DuckDB | 0.0 | 1.63 | 1.66 | 0.72 | 1,181 MB |
| 1,000 | 131.5 M | 2.44 GB | PySpark | 8.8 | 16.06 | 7.19 | 2.52 | 3,570 MB |

Apple M-series, 10 CPUs, 16 GB RAM; median of 3 runs. PySpark 4.2 in `local[*]` mode
with 20 shuffle partitions; DuckDB with default settings.

**When does one machine stop being enough?** Not within this project. DuckDB is 3.5–45×
faster and uses a third of the memory or less at every size, and its memory grows far more
slowly than the data. The full 0.25° grid (~196 M rows, ~3.6 GB of Parquet) is only 1.5×
the largest size tested. Spark earns its overhead when data no longer fits one machine's
disk, when it already lives in a cluster, or when many jobs must run in parallel, none of
which applies here. That is why the pipeline uses DuckDB and keeps Spark for the benchmark
and the optional Databricks phase.

## Data source

- **Open-Meteo Historical Weather API**: hourly reanalysis, i.e. model reconstructions
  combined with observations, not measurements from a station at each coordinate.
- **Model**: `era5` for every location, so locations are comparable over the whole
  history. ERA5 has a spatial resolution of roughly 25 km. Higher-resolution models
  (~9 km) exist but either do not cover the full period or mix models over time.
- **Variables**: 2 m temperature (°C), global horizontal irradiance (GHI), direct
  radiation, diffuse horizontal irradiance (DHI) and direct normal irradiance (DNI),
  all in W/m² as the mean over the preceding hour. Timestamps are UTC.
- **Terms**: free for non-commercial use, with request limits. The client waits between
  requests and backs off on HTTP 429.

## Locations

Phase 1 uses 5 cities: La Guajira (Riohacha), Barranquilla, Medellín, Bogotá and Cali.
The other 5 (Cartagena, Santa Marta, Bucaramanga, Cúcuta, Villavicencio) are already in
[config/locations.yml](config/locations.yml); enable them in
[config/settings.yml](config/settings.yml) or run with `--all-locations`.

## Technical decisions

- **S3-compatible object store instead of a cloud bucket.** The pipeline only speaks
  S3, so moving to AWS S3 is a configuration change. Locally it runs on
  [RustFS](https://github.com/rustfs/rustfs) (Apache-2.0), a drop-in replacement for
  MinIO, which stopped publishing container images in 2025.
- **One partition per location and year.** Small enough to backfill cheaply, large
  enough to keep the request count low (~16 requests per location for full history).
- **Bronze stays raw.** No type casting or unit conversion here; that belongs to Silver,
  so Bronze can always be reprocessed with new rules.
