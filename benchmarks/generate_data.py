"""Generates grid-scale hourly data with the same columns as silver.fact_hourly_weather.

Downloading a real 0.25° grid would take about two months of Open-Meteo's free quota,
so the benchmark uses synthetic values at real volume: one Parquet file per grid point,
15 full years (2011-2025, 131,496 hours each). The values follow a daylight curve with
random cloudy days; only their size and shape matter for timing.

Usage: python -m benchmarks.generate_data --locations 1000
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from src.ingestion.grid import grid_points

DATA_DIR = Path(__file__).resolve().parent / "data"
START, END = np.datetime64("2011-01-01T00"), np.datetime64("2026-01-01T00")


def location_table(index: int, lat: float, lon: float) -> pa.Table:
    rng = np.random.default_rng(index)
    ts_utc = np.arange(START, END, dtype="datetime64[h]")
    ts_local = ts_utc - np.timedelta64(5, "h")
    hours = len(ts_utc)
    local_hour = (ts_local.astype("int64") % 24).astype(np.int8)
    day_index = (ts_local.astype("datetime64[D]") - ts_local[0].astype("datetime64[D]")).astype(
        np.int64
    )

    cloud = rng.uniform(0.35, 1.0, size=day_index.max() + 1)[day_index]
    angle = np.sin(np.pi * (local_hour + 0.5 - 6) / 12)
    ghi = np.maximum(0, (850 + 15 * lat) * angle * cloud).astype(np.float32)
    dhi = (ghi * (0.15 + 0.6 * (1 - cloud))).astype(np.float32)
    dni = np.where(ghi > 0, np.minimum(1100, (ghi - dhi) / np.maximum(angle, 0.1)), 0)
    temp = (24 + 8 * np.maximum(angle, 0) * cloud + rng.normal(0, 1, hours)).astype(np.float32)

    dates = ts_local.astype("datetime64[D]")
    years = dates.astype("datetime64[Y]").astype(np.int64) + 1970
    months = dates.astype("datetime64[M]").astype(np.int64) % 12 + 1
    location_id = f"g{index:05d}"
    return pa.table(
        {
            "location_id": pa.DictionaryArray.from_arrays(
                pa.array(np.zeros(hours, dtype=np.int32)), pa.array([location_id])
            ),
            "timestamp_utc": pa.array(ts_utc.astype("datetime64[us]")),
            "date_local": pa.array(dates),
            "year": pa.array(years.astype(np.int16)),
            "month": pa.array(months.astype(np.int8)),
            "hour_local": pa.array(local_hour),
            "ghi_wm2": pa.array(ghi),
            "dhi_wm2": pa.array(dhi),
            "dni_wm2": pa.array(dni.astype(np.float32)),
            "temperature_c": pa.array(temp),
        }
    )


def generate(n_locations: int, resolution: float = 0.25) -> list[Path]:
    points = grid_points(resolution)
    if n_locations > len(points):
        raise ValueError(f"The {resolution}° grid has only {len(points)} points")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    # Spread the sample across the whole country instead of taking one corner.
    step = len(points) / n_locations
    for i in range(n_locations):
        path = DATA_DIR / f"loc-{i:05d}.parquet"
        if not path.exists():
            lat, lon = points[int(i * step)]
            pq.write_table(location_table(i, lat, lon), path, compression="zstd")
        files.append(path)
    return files


def dataset_files(n_locations: int) -> list[str]:
    """The first N files: every scale is a subset of the largest one."""
    files = [str(DATA_DIR / f"loc-{i:05d}.parquet") for i in range(n_locations)]
    missing = [f for f in files if not Path(f).exists()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} files missing; run generate_data first")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--locations", type=int, default=1000)
    args = parser.parse_args()
    files = generate(args.locations)
    size_gb = sum(f.stat().st_size for f in files) / 1e9
    print(f"{len(files)} files, {len(files) * 131_496:,} rows, {size_gb:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
