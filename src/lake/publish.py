"""Publishes Silver and Gold tables from the DuckDB warehouse to the lake as Parquet.

dbt builds the layers inside DuckDB (fast, incremental, tested). This step copies them
to the lake so other engines (PySpark, Databricks, any S3 reader) can use the same data.
Each published partition is replaced as a whole, so publishing twice is idempotent.

Usage: python -m src.lake.publish
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import duckdb
import pyarrow.dataset as ds

from src.common.config import load_settings
from src.common.logging import get_logger
from src.common.run_metrics import tracked_run
from src.common.storage import Lake
from src.transform.dbt_runner import warehouse_path

log = get_logger("publish")


@dataclass(frozen=True)
class PublishedTable:
    relation: str
    lake_path: str
    partition_by: tuple[str, ...] = ()


TABLES = (
    PublishedTable("silver.fact_hourly_weather", "silver/fact_hourly_weather", ("year", "month")),
    PublishedTable("silver.quarantine_hourly_weather", "silver/quarantine_hourly_weather"),
    PublishedTable("silver.dim_location", "silver/dim_location"),
    PublishedTable("silver.dim_date", "silver/dim_date"),
    PublishedTable("gold.daily_solar_metrics", "gold/daily_solar_metrics", ("year",)),
    PublishedTable("gold.monthly_solar_metrics", "gold/monthly_solar_metrics"),
    PublishedTable("gold.location_reliability_score", "gold/location_reliability_score"),
)


def publish_table(con: duckdb.DuckDBPyConnection, lake: Lake, table: PublishedTable) -> int:
    rows = con.execute(f"select count(*) from {table.relation}").fetchone()[0]
    # Stream in batches so large tables never sit fully in memory.
    reader = con.execute(f"select * from {table.relation}").to_arrow_reader(100_000)
    target = lake.path(table.lake_path)
    if not table.partition_by:
        # Unpartitioned tables are small: replace the whole folder.
        lake.fs.delete_dir_contents(target, missing_dir_ok=True)
    ds.write_dataset(
        reader,
        base_dir=target,
        filesystem=lake.fs,
        format="parquet",
        partitioning=list(table.partition_by) or None,
        partitioning_flavor="hive" if table.partition_by else None,
        existing_data_behavior="delete_matching",
        basename_template="part-{i}.parquet",
        max_rows_per_file=5_000_000,
    )
    return rows


def publish_all(settings: dict | None = None) -> dict[str, int]:
    settings = settings or load_settings()
    lake = Lake.from_settings(settings)
    con = duckdb.connect(str(warehouse_path(settings)), read_only=True)
    published: dict[str, int] = {}
    try:
        for table in TABLES:
            t0 = time.monotonic()
            rows = publish_table(con, lake, table)
            published[table.relation] = rows
            log.info(
                "table_published",
                extra={
                    "relation": table.relation,
                    "path": table.lake_path,
                    "rows": rows,
                    "duration_s": round(time.monotonic() - t0, 2),
                },
            )
    finally:
        con.close()
    return published


def main() -> int:
    with tracked_run("publish") as run:
        published = publish_all()
        run.update(tables=len(published), rows_published=published)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
