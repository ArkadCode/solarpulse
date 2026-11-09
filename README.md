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
| 3 | Airflow, CI/CD, single-command environment | Next |
| 4 | Scale to a national grid, PySpark vs DuckDB benchmark | Planned |
| 5 | ML with MLflow, SRS validation, dashboard, docs | Planned |
| 6 | Databricks Free Edition port (optional) | Planned |

## Quickstart

Requirements: Docker and Python 3.10+.

```bash
cp .env.example .env
make up        # starts the S3-compatible object store (console: http://localhost:9001)
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

## First results

Full history 2011–2026, 5 cities, 694,200 valid hours, 0 quarantined.

| SRS rank | Location | SRS | Mean daily GHI (kWh/m²) | Radiation rank |
| --- | --- | --- | --- | --- |
| 1 | Barranquilla | 79.2 | 5.69 | 1 |
| 2 | Riohacha (La Guajira) | 79.1 | 5.58 | 2 |
| 3 | Medellín | 51.4 | 5.25 | 3 |
| 4 | Bogotá | 47.0 | 4.61 | 4 |
| 5 | Cali | 23.4 | 4.28 | 5 |

With 5 cities, the SRS ranking matches the ranking by mean radiation for the full
history, but not every year: in 2011–2013 and 2022 Bogotá beats Medellín on SRS, and
Barranquilla and La Guajira swap first place from year to year. The Caribbean pair are
virtually tied: La Guajira has fewer low-radiation days and less day-to-day
variability, Barranquilla slightly more energy. See [docs/methodology.md](docs/methodology.md).

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
