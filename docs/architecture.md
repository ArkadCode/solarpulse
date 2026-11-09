# Architecture

```
                 ┌──────────────────────── orchestration (Airflow, phase 3) ────────────────────────┐
                 │                                                                                    │
 Open-Meteo ──► ingestion (Python) ──► Bronze ──► dbt on DuckDB ──► Silver ──► Gold ──► publish ──► lake Parquet
   API          retries, throttling    Parquet     incremental      fact,       daily,              (S3-compatible)
                incremental planner    on S3       per partition    quarantine, monthly,                 │
                                                                     dims        SRS                     ▼
                                                   quality: dbt tests + Great Expectations      Spark, Databricks,
                                                                                                 dashboard, ML
```

Data moves in one direction, from the API to Gold. Each layer has one job:

| Layer | Job | Written by |
| --- | --- | --- |
| Bronze | Keep the API response exactly as received, plus how it was obtained | Python |
| Silver | Typed, validated, one row per location and hour; bad rows quarantined | dbt |
| Gold | Business indicators and the SRS | dbt |

## Storage

- **Lake:** an S3-compatible object store. Locally it is RustFS in Docker; in the cloud it
  could be AWS S3 with no code changes. A `local` backend writes the same layout to
  `data/lake/` for runs without Docker (tests and CI).
- **Warehouse:** a DuckDB file where dbt builds Silver and Gold. DuckDB reads Bronze
  directly from S3 with its `httpfs` extension.
- **Publishing:** `src/lake/publish.py` copies Silver and Gold to the lake as partitioned
  Parquet, so engines other than DuckDB use the same data.

## Incremental loading and idempotency

| Step | Unit | How reruns stay safe |
| --- | --- | --- |
| Ingestion | (location, year) Parquet file | The planner reads each file's footer and fetches only missing or incomplete years. Files are written to a temp path and renamed. |
| Silver | Bronze partition | Partitions whose `extracted_at` is newer than the model's watermark are deleted and reinserted (`macros/incremental_partitions.sql`). |
| Gold daily | (location, local date) | Only days touched by rows loaded in this run are recomputed, always from all of that day's rows. |
| Gold monthly / SRS | Whole table | Small; rebuilt every run from daily metrics. |
| Publish | Lake partition | Each written partition replaces the previous one. |

A backfill is `--backfill <year>` on ingestion followed by `dbt build`: the year is
downloaded again and only that partition flows through Silver and Gold.

## Lineage

1. dbt's graph (`make docs`) traces every Gold column back to the Bronze sources.
2. Each Silver row carries `_bronze_partition`, `_bronze_source_file` and
   `_bronze_extracted_at`.
3. Each Bronze file carries the exact request parameters and download time.

## Data quality

| Check | Where | On failure |
| --- | --- | --- |
| Hourly rules (ranges, nulls, units, duplicates) | Silver models | Row goes to quarantine |
| dbt tests (keys, ranges, relationships, coverage, SRS weights) | `dbt build` | Build stops; downstream models are skipped |
| Great Expectations suites | `src/quality` | Report marked FAIL; process exits non-zero |

The report is written to `reports/data_quality_report.md` and kept in the lake under
`quality/reports/`, one JSON per run.
