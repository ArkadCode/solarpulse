"""Incremental load into Bronze: works out which partitions are missing and fetches only those.

Unit of work: (location, year). Each partition is rewritten in full, so running the
same period twice produces exactly the same result.

Usage:
    python -m src.ingestion.incremental_loader                 # incremental
    python -m src.ingestion.incremental_loader --backfill 2015 # reprocess one year
    python -m src.ingestion.incremental_loader --locations bogota cali --dry-run
"""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

from src.common.config import Location, load_locations, load_settings
from src.common.logging import get_logger
from src.common.run_metrics import tracked_run
from src.common.storage import Lake
from src.ingestion.api_client import OpenMeteoClient
from src.ingestion.extractor import (
    BRONZE_TABLE,
    LOCATIONS_TABLE,
    locations_to_table,
    partition_path,
    response_to_table,
)
from src.ingestion.grid import grid_locations
from src.ingestion.synthetic import SyntheticClient

GRID_TABLE = "bronze/raw_openmeteo_grid"

log = get_logger("incremental_loader")


class WeatherClient(Protocol):
    def fetch_hourly(
        self, latitude: float, longitude: float, start_date: date, end_date: date
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


@dataclass(frozen=True)
class Period:
    location_id: str
    year: int
    start_date: date
    end_date: date
    reason: str  # missing | incomplete | backfill


@dataclass
class RunMetrics:
    planned: int = 0
    written: int = 0
    failed: int = 0
    rows_written: int = 0
    duration_s: float = 0.0
    failures: list[str] = field(default_factory=list)


def year_window(year: int, history_start: date, end_date: date) -> tuple[date, date] | None:
    start = max(date(year, 1, 1), history_start)
    end = min(date(year, 12, 31), end_date)
    return (start, end) if start <= end else None


def plan_periods(
    lake: Lake,
    locations: list[Location],
    history_start: date,
    end_date: date,
    backfill_years: set[int] | None = None,
    table: str = BRONZE_TABLE,
) -> list[Period]:
    """Partitions to fetch.

    - missing: the partition does not exist.
    - incomplete: it exists but was requested up to a date earlier than the current
      window (typically the current year, which grows every day).
    - backfill: the year was explicitly requested for reprocessing.
    """
    backfill_years = backfill_years or set()
    periods: list[Period] = []
    for loc in locations:
        for year in range(history_start.year, end_date.year + 1):
            window = year_window(year, history_start, end_date)
            if window is None:
                continue
            start, end = window
            path = partition_path(loc.location_id, year, table)

            if year in backfill_years:
                reason = "backfill"
            elif not lake.exists(path):
                reason = "missing"
            else:
                meta = lake.read_schema_metadata(path)
                stored_start = date.fromisoformat(meta.get("requested_start_date", "9999-12-31"))
                stored_end = date.fromisoformat(meta.get("requested_end_date", "0001-01-01"))
                if stored_start <= start and stored_end >= end:
                    continue
                reason = "incomplete"
            periods.append(Period(loc.location_id, year, start, end, reason))
    return periods


def run_ingestion(
    lake: Lake,
    client: WeatherClient,
    locations: list[Location],
    history_start: date,
    end_date: date,
    backfill_years: set[int] | None = None,
    dry_run: bool = False,
    table: str = BRONZE_TABLE,
    max_partitions: int | None = None,
) -> RunMetrics:
    """Fetches the planned partitions. `max_partitions` caps one run so a large grid is
    loaded in batches that fit the API's daily quota; the next run continues."""
    t0 = time.monotonic()
    by_id = {loc.location_id: loc for loc in locations}
    pending = plan_periods(lake, locations, history_start, end_date, backfill_years, table)
    periods = pending[:max_partitions] if max_partitions else pending
    metrics = RunMetrics(planned=len(periods))
    log.info(
        "plan_ready",
        extra={
            "periods": len(periods),
            "pending_total": len(pending),
            "dry_run": dry_run,
            "end_date": end_date.isoformat(),
            "table": table,
        },
    )

    for p in periods:
        if dry_run:
            log.info("would_fetch", extra=asdict(p))
            continue
        loc = by_id[p.location_id]
        rel = partition_path(p.location_id, p.year, table)
        try:
            response, params = client.fetch_hourly(
                loc.latitude, loc.longitude, p.start_date, p.end_date
            )
            batch = response_to_table(
                response, params, loc, p.start_date, p.end_date, source_file=lake.path(rel)
            )
            lake.write_parquet(rel, batch)
        except Exception as exc:  # one failed partition does not stop the others
            metrics.failed += 1
            metrics.failures.append(f"{p.location_id}/{p.year}: {exc}")
            log.error("partition_failed", extra={**asdict(p), "error": str(exc)})
            continue
        metrics.written += 1
        metrics.rows_written += batch.num_rows
        log.info("partition_written", extra={**asdict(p), "rows": batch.num_rows, "path": rel})

    metrics.duration_s = round(time.monotonic() - t0, 2)
    log.info("run_finished", extra=asdict(metrics))
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incremental Open-Meteo ingestion into Bronze")
    parser.add_argument("--locations", nargs="*", help="Location IDs (default: from settings)")
    parser.add_argument("--all-locations", action="store_true", help="Use every location")
    parser.add_argument("--backfill", nargs="*", type=int, default=[], help="Years to reprocess")
    parser.add_argument("--start-date", type=date.fromisoformat, help="History start date")
    parser.add_argument("--end-date", type=date.fromisoformat, help="End date (default: today-lag)")
    parser.add_argument("--dry-run", action="store_true", help="Only print the plan")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic data, no API calls")
    parser.add_argument(
        "--grid", type=float, metavar="DEG", help="Ingest a national grid at this resolution"
    )
    parser.add_argument(
        "--max-partitions", type=int, help="Cap partitions per run (API quota batching)"
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    ing = settings["ingestion"]
    only = None if args.all_locations else (args.locations or ing.get("active_locations") or None)
    locations = grid_locations(args.grid) if args.grid else load_locations(only=only)
    table = GRID_TABLE if args.grid else BRONZE_TABLE
    max_partitions = args.max_partitions or ing.get("max_partitions_per_run")
    history_start = args.start_date or date.fromisoformat(ing["history_start_date"])
    end_date = args.end_date or date.today() - timedelta(days=ing.get("end_date_lag_days", 7))

    lake = Lake.from_settings(settings)
    client: WeatherClient = (
        SyntheticClient(settings["api"])
        if args.synthetic
        else OpenMeteoClient(settings["api"], settings["retry"])
    )
    if not args.dry_run and not args.grid:
        # Reference data always lists every configured location, not just this run's subset.
        lake.write_parquet(f"{LOCATIONS_TABLE}/data.parquet", locations_to_table(load_locations()))

    with tracked_run("ingestion", settings) as run:
        metrics = run_ingestion(
            lake=lake,
            client=client,
            locations=locations,
            history_start=history_start,
            end_date=end_date,
            backfill_years=set(args.backfill),
            dry_run=args.dry_run,
            table=table,
            max_partitions=max_partitions,
        )
        run.update(
            table=table,
            partitions_planned=metrics.planned,
            partitions_written=metrics.written,
            partitions_failed=metrics.failed,
            rows_written=metrics.rows_written,
            backfill_years=sorted(args.backfill),
            dry_run=args.dry_run,
            _failed=metrics.failed > 0,
        )
    return 1 if metrics.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
