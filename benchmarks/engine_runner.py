"""Runs every benchmark query on one engine and one data size, then prints JSON.

Called as a subprocess by run_benchmark.py, so each measurement starts from a fresh
process and its memory can be measured on its own.

Usage: python -m benchmarks.engine_runner --engine duckdb --locations 100
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time

from benchmarks.generate_data import dataset_files
from benchmarks.queries import QUERIES

REPEATS = 3


def run_duckdb(files: list[str]) -> tuple[float, dict[str, list[float]]]:
    import duckdb

    t0 = time.perf_counter()
    con = duckdb.connect()
    con.execute(f"create view hourly as select * from read_parquet({files!r})")
    startup = time.perf_counter() - t0

    timings: dict[str, list[float]] = {}
    for name, sql in QUERIES.items():
        timings[name] = []
        for _ in range(REPEATS):
            t = time.perf_counter()
            con.execute(sql).fetchall()
            timings[name].append(time.perf_counter() - t)
    return startup, timings


def run_spark(files: list[str], driver_memory: str) -> tuple[float, dict[str, list[float]]]:
    os.environ.setdefault("PYSPARK_PYTHON", "python")
    from pyspark.sql import SparkSession

    t0 = time.perf_counter()
    spark = (
        SparkSession.builder.master("local[*]")
        .appName("solarpulse-benchmark")
        .config("spark.driver.memory", driver_memory)
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        # The default (200) is tuned for clusters; 2-3x the cores suits one machine.
        .config("spark.sql.shuffle.partitions", str((os.cpu_count() or 4) * 2))
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    spark.read.parquet(*files).createOrReplaceTempView("hourly")
    startup = time.perf_counter() - t0

    timings: dict[str, list[float]] = {}
    try:
        for name, sql in QUERIES.items():
            timings[name] = []
            for _ in range(REPEATS):
                t = time.perf_counter()
                spark.sql(sql).collect()
                timings[name].append(time.perf_counter() - t)
    finally:
        spark.stop()
    return startup, timings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["duckdb", "spark"], required=True)
    parser.add_argument("--locations", type=int, required=True)
    parser.add_argument("--spark-driver-memory", default="8g")
    args = parser.parse_args()

    files = dataset_files(args.locations)
    if args.engine == "duckdb":
        startup, timings = run_duckdb(files)
    else:
        startup, timings = run_spark(files, args.spark_driver_memory)

    print(
        json.dumps(
            {
                "engine": args.engine,
                "locations": args.locations,
                "startup_s": round(startup, 3),
                "queries": {
                    name: {
                        "first_s": round(t[0], 3),
                        "median_s": round(statistics.median(t), 3),
                    }
                    for name, t in timings.items()
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
