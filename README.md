# SolarPulse

A data platform that turns 15 years of hourly weather reanalysis from
[Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api) into a **Solar
Reliability Score (SRS)** for Colombian cities. Cities are ranked not only by mean solar
radiation but also by variability, seasonality, how often low-radiation days occur and
heat stress.

It is built as a real system rather than a notebook: incremental ingestion, a Medallion
lakehouse on an S3-compatible object store, dbt models with tests and quarantine,
Great Expectations, Airflow orchestration with backfills, CI/CD, run metrics, an ML model
tracked in MLflow, a PySpark vs DuckDB benchmark and a dashboard. Everything runs locally
on free, open-source tools.

> The indicators compare solar *conditions*. They are not an estimate of electricity
> production, which would require modeling panels, orientation and losses.

## Results

Full history 2011–2026, 10 cities, 1,388,400 valid hourly rows, 0 quarantined.

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
rainy season produces the second-highest share of low-radiation days.

**How far can the score be trusted?**

- **Weights:** across 5,000 random weight sets, the ranking keeps a mean Spearman
  correlation of 0.98 with the baseline and no city moves more than 2 places when any
  weight shifts by ±10 points. First place is a toss-up: Barranquilla 54 %, La Guajira 46 %.
- **Data source:** the ERA5 ranking agrees with Global Solar Atlas (Spearman 0.82, mean
  absolute error 5 %) about as well as NASA POWER and Global Solar Atlas agree with each
  other (0.83). See [docs/methodology.md](docs/methodology.md#robustness-checks).

**Next-day forecast:** a gradient-boosting model beats the seasonal climatology by 22 %
(MSE skill) on 2024–2026, which the model never saw; "tomorrow = today" is 15 % *worse*
than climatology.

**Scale:** on 131 M rows (2.4 GB of Parquet), DuckDB answers the Gold queries in under
2 s with 1.2 GB of memory; PySpark in local mode takes 3.5–10× longer with 3× the memory.
One machine is enough for the whole national grid. See [Scale and benchmark](#scale-and-benchmark).

## Architecture

```
                ┌──────────────────────────── Airflow 3 (orchestration) ────────────────────────────┐
                │                                                                                    │
 Open-Meteo ──► ingestion ──► Bronze ──► dbt on DuckDB ──► Silver ──► Gold ──► quality ──► scoring ──► publish ──► lake Parquet ──► dashboard
   API          (Python)      Parquet    incremental       fact,       daily,   GX + dbt   sensitivity            (S3-compatible)   (Streamlit)
                retries,      on S3      per partition     quarantine, monthly, tests      + validation                  │
                incremental                                dims        SRS,                                              ├──► ML forecast (MLflow)
                                                                       anomalies                                        └──► PySpark / Databricks
```

| Component | Technology | Why |
| --- | --- | --- |
| Ingestion | Python, `requests` | Retries, quota-aware batching, reproducible parameters |
| Lake | RustFS (S3-compatible) in Docker | Same interface as S3; MinIO no longer ships images |
| Format | Parquet with PyArrow | Columnar, compressed, partitioned |
| Processing | DuckDB | Fast SQL on one machine, reads Parquet straight from S3 |
| Transformation | dbt with dbt-duckdb | Versioned SQL, tests, lineage graph |
| Data quality | dbt tests, Great Expectations, quarantine table | Ranges, nulls, duplicates, coverage |
| Orchestration | Apache Airflow 3 | Asset-driven DAGs, retries, backfills, alerts |
| ML | scikit-learn, MLflow | Tracked experiments, model registry |
| Scale benchmark | PySpark 4 (local mode) | Measure when one machine stops being enough |
| CI/CD | GitHub Actions | Lint, tests, dbt build, DAG import, image build |
| Containers | Docker Compose | The whole environment with one command |
| Dashboard | Streamlit, Altair | The visible layer |

Docs: [architecture](docs/architecture.md) · [data dictionary](docs/data_dictionary.md) ·
[methodology](docs/methodology.md).

## Quickstart

Requirements: Docker and Python 3.10+ (Java 17+ only for the benchmark).

```bash
make up        # builds and starts everything
make trigger   # runs the pipeline in Airflow now (it also runs daily)
```

| Service | URL |
| --- | --- |
| Dashboard | http://localhost:8501 |
| Airflow | http://localhost:8080 |
| MLflow | http://localhost:5001 |
| Lake console (RustFS) | http://localhost:9001 |

To run the steps on the host instead of Airflow:

```bash
make plan       # which partitions are missing, without downloading
make ingest     # download only the missing partitions into Bronze
make transform  # dbt build: Silver and Gold plus their tests
make quality    # Great Expectations report in reports/
make scoring    # SRS weight sensitivity and external validation
make publish    # Silver and Gold as partitioned Parquet in the lake
make ml         # train the forecast, tracked in MLflow
make pipeline   # all of the above in order
make test       # 36 unit and end-to-end tests, no network or Docker needed
make docs       # dbt docs with the lineage graph
```

Backfills: `make backfill YEARS="2015 2016"` in Airflow, or
`python -m src.ingestion.incremental_loader --backfill 2015 2016` on the host.
No Docker? Set `SOLARPULSE_STORAGE_BACKEND=local` in `.env`; `make sample` fills
`data/lake/` with synthetic data.

## How it works

### Ingestion and Bronze

| Module | Responsibility |
| --- | --- |
| [api_client.py](src/ingestion/api_client.py) | Reproducible requests, throttling, retryable (network, 429, 5xx) vs fatal (400) errors |
| [retry_handler.py](src/ingestion/retry_handler.py) | Exponential backoff with jitter; honors `Retry-After` |
| [extractor.py](src/ingestion/extractor.py) | Validates the response and adds extraction metadata |
| [incremental_loader.py](src/ingestion/incremental_loader.py) | Plans missing `(location, year)` partitions and fetches only those |
| [storage.py](src/common/storage.py) | Same interface for S3 and local disk; atomic writes |

- **Incremental:** each Parquet footer stores the requested date range, so the planner
  reads footers only, never the data. Only the current year is refreshed daily.
- **Idempotent:** each `(location, year)` partition is overwritten in full; tests prove
  a rerun calls no API and a backfill never duplicates rows.
- **Reproducible:** Bronze keeps the values as received plus the request parameters,
  weather model, grid cell and download time.
- **Resilient:** a failed partition is logged and retried next run; the others continue.

### Silver and Gold (dbt)

| Model | Layer | Load | Purpose |
| --- | --- | --- | --- |
| `stg_openmeteo__hourly` | staging | view | Rename and type Bronze, read straight from S3 |
| `fact_hourly_weather` | Silver | incremental per Bronze partition | Valid hours with lineage columns |
| `quarantine_hourly_weather` | Silver | incremental per Bronze partition | Rows that broke a rule, with the rules |
| `dim_location`, `dim_date` | Silver | table | Locations; calendar with Colombian seasons |
| `daily_solar_metrics` | Gold | incremental per affected day | Energy, diffuse ratio, volatility, heat stress |
| `monthly_solar_metrics` | Gold | table | Mean, deviation and percentiles of daily energy |
| `daily_radiation_anomalies` | Gold | table | Days far below their seasonal norm |
| `location_reliability_score` | Gold | table | SRS components, score and ranks per period |

A Silver model only reprocesses Bronze partitions downloaded since its last run: their
rows are deleted and reinserted ([incremental_partitions.sql](dbt/macros/incremental_partitions.sql)).
Rows that break a physical rule (ranges, DHI ≤ GHI, units, nulls, duplicates) go to
quarantine with the list of rules they broke. 44 dbt tests guard keys, ranges,
relationships, hourly coverage and the SRS weights.

### Quality, lineage and observability

- **Great Expectations** validates Silver and Gold independently of dbt and writes a
  report to `reports/` and to the lake, with quarantine counts per rule.
- **Lineage:** the dbt graph traces every Gold column to Bronze; every Silver row
  carries its Bronze file, partition and download time.
- **Run metrics:** every step records status, duration, peak memory and its own counts
  (rows written, dbt node statuses and per-model seconds, failed expectations) in the
  lake under `observability/runs/`, keyed by the Airflow run ID. The dashboard's
  *Pipeline health* tab reads them.

### Orchestration and CI/CD

```
weather_ingestion (@daily) ─Bronze─► silver_transformation ─Silver─► gold_analytics ─Gold─► ml_forecast
  params: backfill_years,             dbt build silver                dbt build gold          train + register
  locations                                                           → Great Expectations
                                                                      → sensitivity, validation
                                                                      → publish to the lake
```

- DAGs are chained by **data assets**, so each layer runs as soon as the previous changes.
- **Retries** with exponential backoff and a **failure alert** (structured log, plus a
  Slack-compatible webhook when `ALERT_WEBHOOK_URL` is set).
- **Backfill parameters** are validated by a strict schema because they reach a shell command.
- **One writer at a time:** DuckDB allows a single writer, so every warehouse task shares
  a one-slot pool. The dashboard reads the published Parquet, never the warehouse.
- **Isolated dependencies:** the pipeline runs in its own virtualenv inside the Airflow
  image; Airflow never imports dbt or pandas.
- **GitHub Actions** ([ci.yml](.github/workflows/ci.yml)): ruff, pytest on Python 3.10
  and 3.12, a full dbt build + quality + scoring + publish on synthetic data (report and
  dbt docs uploaded as artifacts), an import check of every DAG on Airflow 3.3.2, and a
  build of both Docker images.

## Scale and benchmark

**National grid.** [grid.py](src/ingestion/grid.py) builds a grid over a simplified outline
of mainland Colombia: 94 points at 1°, 370 at 0.5°, 1,488 at 0.25°. `--grid 0.25` ingests
it into a separate Bronze table, and `--max-partitions N` caps each run so the load fits
the API quota and continues the next day.

| Grid | Points | Partitions (16 years) | Approx. API calls* | Days at 10,000 calls/day |
| --- | --- | --- | --- | --- |
| 1° | 94 | 1,504 | ~39,000 | ~4 |
| 0.25° | 1,488 | 23,808 | ~620,000 | ~62 |

\*Open-Meteo counts a request for more than 2 weeks of data as several calls; one year
for one location is about 26. `make grid-plan GRID=1.0` prints the plan.

**Benchmark** ([benchmarks/](benchmarks/)). Downloading the 0.25° grid would take about two
months of free quota, so the benchmark uses synthetic data at real volume and with the
Silver schema: 15 years for up to 1,000 grid points. Each engine runs the same SQL in a
fresh process; peak memory covers the whole process tree, including Spark's JVM.

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
faster and uses a third of the memory or less at every size, and its memory grows far
more slowly than the data. The full 0.25° grid (~196 M rows, ~3.6 GB of Parquet) is only
1.5× the largest size tested. Spark earns its overhead when data no longer fits one
machine's disk, when it already lives in a cluster, or when many jobs must run in
parallel, none of which applies here.

## Technical decisions

- **Parquet + DuckDB instead of a cluster.** The benchmark shows one machine handles the
  national grid with room to spare. I would move to Spark (or a warehouse) when the data
  outgrows one machine's disk, roughly hundreds of GB, or when many jobs must run
  concurrently.
- **dbt for transformation, Airflow as the only orchestrator.** dbt owns *what* the data
  means (SQL, tests, lineage); Airflow owns *when* it runs (schedules, retries, backfills,
  alerts). dbt never schedules and Airflow never contains business logic.
- **Partition-level incremental loads.** The unit of work everywhere is the Bronze
  partition (location, year): ingestion overwrites it, Silver deletes and reinserts it,
  Gold recomputes the days it touches. Backfills and reruns are safe by construction.
- **Quarantine instead of delete.** Bad rows stay inspectable with the rules they broke,
  so rules can be tuned without re-downloading data.
- **The dashboard reads the lake, not the warehouse.** DuckDB has a single writer; reading
  the published Parquet keeps the dashboard available while Airflow is writing.
- **Databricks last.** The core is portable open source; Databricks would be an
  extension, not a dependency.
- **When I would not use Spark:** for anything this project does. It is 3.5–45× slower here
  and starts in 6–9 s before running any query.

## Data source and limitations

- **Open-Meteo Historical Weather API**, ERA5 reanalysis (~25 km grid): model
  reconstructions combined with observations, not station measurements. A single model
  for all locations keeps them comparable over the whole history; ~9 km models exist but
  only from 2017 or as a blend of models over time.
- **Variables:** 2 m temperature, GHI, direct horizontal radiation, DHI and DNI, in W/m²
  as the mean over the preceding hour, UTC.
- **Terms:** free for non-commercial use, with request limits.
- **The SRS is relative:** min-max normalization ranks locations against each other, so
  adding a location changes every score. Its weights are a hypothesis (see the
  sensitivity analysis), and it compares conditions, not production.
- **Grid cells smooth terrain:** cities in valleys next to mountains (Cali, Cúcuta,
  Bucaramanga) disagree most with the satellite references.

## Repository layout

```
solarpulse/
├── airflow/dags/          # 4 DAGs chained by data assets
├── benchmarks/            # synthetic grid data, PySpark vs DuckDB runner, results
├── config/                # locations, settings, external reference values
├── dashboard/             # Streamlit app reading the published lake
├── dbt/                   # models (staging, intermediate, silver, gold), macros, tests
├── docker/                # Airflow and dashboard images
├── docs/                  # architecture, data dictionary, methodology
├── src/
│   ├── common/            # config, storage, JSON logs, run metrics
│   ├── ingestion/         # API client, retries, incremental loader, grid, synthetic data
│   ├── transform/         # dbt runner
│   ├── quality/           # Great Expectations suites and report
│   ├── scoring/           # SRS in Python, sensitivity, external validation
│   ├── ml/                # next-day forecast with MLflow
│   └── lake/              # publish Silver and Gold to the lake
├── tests/                 # unit, end-to-end (dbt on synthetic data), DAG import
├── docker-compose.yml
└── Makefile
```

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Ingestion and Bronze | Done |
| 2 | Silver and Gold with DuckDB + dbt, data quality, first SRS | Done |
| 3 | Airflow, CI/CD, single-command environment | Done |
| 4 | All 10 cities, national grid, PySpark vs DuckDB benchmark | Done |
| 5 | ML with MLflow, SRS validation and sensitivity, dashboard, docs | Done |
| 6 | Databricks Free Edition port (optional) | Planned |
