"""PySpark (local mode) vs DuckDB on the same Parquet files at growing sizes.

For every (engine, size) a fresh subprocess runs all queries. Peak memory is sampled
every 100 ms over the whole process tree, which includes Spark's JVM. Results go to
benchmarks/results/results.json and results.md.

Usage: python -m benchmarks.run_benchmark --sizes 10 100 500 1000
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from benchmarks.generate_data import dataset_files
from benchmarks.queries import QUERIES

RESULTS_DIR = Path(__file__).resolve().parent / "results"
ROWS_PER_LOCATION = 131_496


def tree_rss(proc: psutil.Process) -> int:
    total = 0
    for p in [proc, *proc.children(recursive=True)]:
        try:
            total += p.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def measure(engine: str, locations: int, driver_memory: str) -> dict:
    cmd = [
        sys.executable, "-m", "benchmarks.engine_runner",
        "--engine", engine, "--locations", str(locations),
        "--spark-driver-memory", driver_memory,
    ]
    env = {**os.environ, "PYSPARK_PYTHON": sys.executable}
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    ps = psutil.Process(proc.pid)
    peak = 0
    stop = threading.Event()

    def sample() -> None:
        nonlocal peak
        while not stop.is_set():
            try:
                peak = max(peak, tree_rss(ps))
            except psutil.NoSuchProcess:
                return
            time.sleep(0.1)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    out, err = proc.communicate()
    stop.set()
    sampler.join()
    if proc.returncode != 0:
        raise RuntimeError(f"{engine} @ {locations} failed:\n{err[-2000:]}")

    result = json.loads(out.strip().splitlines()[-1])
    result["rows"] = locations * ROWS_PER_LOCATION
    size = sum(Path(f).stat().st_size for f in dataset_files(locations))
    result["data_gb"] = round(size / 1e9, 3)
    result["peak_memory_mb"] = round(peak / 1e6)
    result["wall_s"] = round(time.perf_counter() - t0, 2)
    return result


def to_markdown(results: list[dict], machine: str) -> str:
    names = list(QUERIES)
    lines = [
        "# Benchmark: PySpark local vs DuckDB",
        "",
        f"Machine: {machine}. Median of 3 runs per query, in seconds; startup is separate.",
        "",
        "| Locations | Rows | Parquet | Engine | Startup | "
        + " | ".join(names)
        + " | Peak memory |",
        "| --- | --- | --- | --- | --- | " + " | ".join("---" for _ in names) + " | --- |",
    ]
    for r in results:
        cells = " | ".join(f"{r['queries'][n]['median_s']:.2f}" for n in names)
        lines.append(
            f"| {r['locations']:,} | {r['rows'] / 1e6:.1f} M | {r['data_gb']:.2f} GB | "
            f"{r['engine']} | {r['startup_s']:.1f} | {cells} | {r['peak_memory_mb']:,} MB |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 100, 500, 1000])
    parser.add_argument("--engines", nargs="+", default=["duckdb", "spark"])
    parser.add_argument("--spark-driver-memory", default="8g")
    args = parser.parse_args()

    machine = (
        f"{platform.machine()}, {psutil.cpu_count()} CPUs, "
        f"{psutil.virtual_memory().total / 2**30:.0f} GB RAM, {platform.system()}"
    )
    results = []
    for size in args.sizes:
        for engine in args.engines:
            r = measure(engine, size, args.spark_driver_memory)
            print(json.dumps(r), flush=True)
            results.append(r)

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(
        json.dumps({"machine": machine, "results": results}, indent=2)
    )
    (RESULTS_DIR / "results.md").write_text(to_markdown(results, machine))
    print(to_markdown(results, machine))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
