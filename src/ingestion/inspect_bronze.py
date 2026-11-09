"""Bronze summary: rows per location and year, plus a duplicate check.

Usage: python -m src.ingestion.inspect_bronze
"""

from __future__ import annotations

import duckdb

from src.common.config import load_settings
from src.common.storage import Lake
from src.ingestion.extractor import BRONZE_TABLE


def main() -> int:
    lake = Lake.from_settings(load_settings())
    bronze = lake.read_dataset(BRONZE_TABLE)
    con = duckdb.connect()
    con.register("bronze", bronze)

    print("Rows per location and year:")
    con.sql(
        """
        SELECT location_id, year, count(*) AS rows, min(time) AS first_hour,
               max(time) AS last_hour, max(extracted_at) AS extracted_at
        FROM bronze GROUP BY ALL ORDER BY ALL
        """
    ).show(max_rows=500)

    dupes = con.sql(
        "SELECT count(*) - count(DISTINCT (location_id, time)) FROM bronze"
    ).fetchone()[0]
    print(f"Duplicate rows on (location_id, time): {dupes}")
    return 1 if dupes else 0


if __name__ == "__main__":
    raise SystemExit(main())
