"""Turns an Open-Meteo response into a Bronze partition.

Bronze keeps the data as received (ISO hour strings, unconverted values) plus the
extraction metadata needed to reproduce the request.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

import pyarrow as pa

from src.common.config import Location

BRONZE_TABLE = "bronze/raw_openmeteo"


def partition_path(location_id: str, year: int, table: str = BRONZE_TABLE) -> str:
    return f"{table}/location_id={location_id}/year={year}/data.parquet"


def response_to_table(
    response: dict[str, Any],
    params: dict[str, Any],
    location: Location,
    start_date: date,
    end_date: date,
    source_file: str,
    extracted_at: datetime | None = None,
) -> pa.Table:
    hourly = response.get("hourly") or {}
    times = hourly.get("time")
    if not times:
        raise ValueError(f"Response has no hourly data for {location.location_id}")

    variables = params["hourly"].split(",")
    missing = [v for v in variables if v not in hourly]
    if missing:
        raise ValueError(f"Variables missing from response: {missing}")
    for var in variables:
        if len(hourly[var]) != len(times):
            raise ValueError(f"Length mismatch in '{var}': {len(hourly[var])} vs {len(times)}")

    extracted_at = extracted_at or datetime.now(timezone.utc)
    n = len(times)

    columns: dict[str, pa.Array] = {"time": pa.array(times, pa.string())}
    for var in variables:
        columns[var] = pa.array(hourly[var], pa.float64())

    # location_id and year live in the path (hive partitioning), not inside the file.
    constant = {
        "api_latitude": (response.get("latitude"), pa.float64()),
        "api_longitude": (response.get("longitude"), pa.float64()),
        "api_elevation": (response.get("elevation"), pa.float64()),
        "api_timezone": (response.get("timezone"), pa.string()),
        "api_model": (params["models"], pa.string()),
        "hourly_units": (json.dumps(response.get("hourly_units", {})), pa.string()),
        "request_params": (json.dumps(params, sort_keys=True), pa.string()),
        "requested_start_date": (start_date, pa.date32()),
        "requested_end_date": (end_date, pa.date32()),
        "extracted_at": (extracted_at, pa.timestamp("us", tz="UTC")),
        "source_file": (source_file, pa.string()),
    }
    for name, (value, typ) in constant.items():
        columns[name] = pa.array([value] * n, typ)

    table = pa.table(columns)
    # Footer metadata: the incremental planner reads it without downloading the data.
    return table.replace_schema_metadata(
        {
            "location_id": location.location_id,
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "api_model": params["models"],
            "row_count": str(n),
            "extracted_at": extracted_at.isoformat(),
        }
    )


LOCATIONS_TABLE = "bronze/ref_locations"


def locations_to_table(locations: list[Location]) -> pa.Table:
    """Reference table with every configured location, written as-is to Bronze."""
    rows = [vars(loc) for loc in locations]
    table = pa.Table.from_pylist(rows)
    extracted_at = datetime.now(timezone.utc)
    return table.append_column(
        "extracted_at", pa.array([extracted_at] * len(rows), pa.timestamp("us", tz="UTC"))
    )
