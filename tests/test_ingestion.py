"""Ingestion tests without network access: the API client is replaced by a fake one."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pyarrow.fs as pafs
import pytest

from src.common.config import Location, load_locations, load_settings
from src.common.storage import Lake
from src.ingestion.extractor import BRONZE_TABLE, partition_path, response_to_table
from src.ingestion.incremental_loader import plan_periods, run_ingestion
from src.ingestion.retry_handler import RetryableError, compute_delay, retry_with_backoff

VARS = [
    "temperature_2m",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
]

LOC = Location("test_city", "Test", "Dep", "Caribbean", 10.0, -74.0, 10)
LOC2 = Location("other_city", "Other", "Dep", "Andean", 4.7, -74.0, 2600)


class FakeClient:
    """Produces a deterministic hourly response shaped like Open-Meteo's."""

    def __init__(self, fail_for: set[str] | None = None):
        self.calls: list[tuple[float, date, date]] = []
        self.fail_for = fail_for or set()

    def fetch_hourly(self, latitude, longitude, start_date, end_date):
        self.calls.append((latitude, start_date, end_date))
        if (latitude, start_date.year) in {(LOC.latitude, int(y)) for y in self.fail_for}:
            raise RuntimeError("simulated failure")
        hours = int(((end_date - start_date).days + 1) * 24)
        t0 = datetime.combine(start_date, datetime.min.time())
        times = [(t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
        hourly = {"time": times}
        for i, var in enumerate(VARS):
            hourly[var] = [float((h % 24) * (i + 1)) for h in range(hours)]
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": ",".join(VARS),
            "models": "era5",
            "timezone": "GMT",
        }
        response = {
            "latitude": latitude,
            "longitude": longitude,
            "elevation": 12.0,
            "timezone": "GMT",
            "hourly_units": {v: "W/m²" for v in VARS},
            "hourly": hourly,
        }
        return response, params


@pytest.fixture
def lake(tmp_path) -> Lake:
    return Lake(pafs.LocalFileSystem(), str(tmp_path))


def bronze_rows(lake: Lake) -> int:
    return lake.read_dataset(BRONZE_TABLE).num_rows


# --- Configuration ---------------------------------------------------------------


def test_config_files_are_valid():
    settings = load_settings()
    locations = load_locations()
    assert len(locations) == 10
    assert len({loc.location_id for loc in locations}) == 10
    active = settings["ingestion"]["active_locations"]
    assert len(load_locations(only=active)) == len(active)


def test_unknown_location_is_rejected():
    with pytest.raises(ValueError):
        load_locations(only=["atlantis"])


# --- Bronze transformation -----------------------------------------------------


def test_response_to_table_keeps_raw_values_and_metadata():
    response, params = FakeClient().fetch_hourly(10.0, -74.0, date(2020, 1, 1), date(2020, 1, 2))
    table = response_to_table(response, params, LOC, date(2020, 1, 1), date(2020, 1, 2), "x")
    assert table.num_rows == 48
    assert set(VARS) <= set(table.column_names)
    assert table.column("time")[0].as_py() == "2020-01-01T00:00"
    assert table.column("api_model")[0].as_py() == "era5"
    assert table.schema.metadata[b"requested_end_date"] == b"2020-01-02"
    assert "location_id" not in table.column_names  # lives in the partition path


def test_response_with_mismatched_lengths_is_rejected():
    response, params = FakeClient().fetch_hourly(10.0, -74.0, date(2020, 1, 1), date(2020, 1, 1))
    response["hourly"]["shortwave_radiation"].pop()
    with pytest.raises(ValueError, match="Length mismatch"):
        response_to_table(response, params, LOC, date(2020, 1, 1), date(2020, 1, 1), "x")


# --- Incremental planning ---------------------------------------------------


def test_plan_on_empty_lake_requests_every_year(lake):
    periods = plan_periods(lake, [LOC, LOC2], date(2019, 6, 1), date(2021, 3, 15))
    assert [(p.location_id, p.year) for p in periods] == [
        ("test_city", 2019), ("test_city", 2020), ("test_city", 2021),
        ("other_city", 2019), ("other_city", 2020), ("other_city", 2021),
    ]
    assert periods[0].start_date == date(2019, 6, 1)  # first year is trimmed
    assert periods[2].end_date == date(2021, 3, 15)  # last year is trimmed
    assert all(p.reason == "missing" for p in periods)


def test_plan_only_refreshes_the_growing_year(lake):
    run_ingestion(lake, FakeClient(), [LOC], date(2020, 1, 1), date(2021, 3, 15))
    periods = plan_periods(lake, [LOC], date(2020, 1, 1), date(2021, 4, 30))
    assert [(p.year, p.reason) for p in periods] == [(2021, "incomplete")]


# --- Idempotency (phase 1 deliverable) --------------------------------------


def test_rerun_does_not_duplicate_and_skips_api(lake):
    first = FakeClient()
    m1 = run_ingestion(lake, first, [LOC], date(2020, 1, 1), date(2021, 12, 31))
    rows_after_first = bronze_rows(lake)
    assert m1.written == 2 and rows_after_first == (366 + 365) * 24

    second = FakeClient()
    m2 = run_ingestion(lake, second, [LOC], date(2020, 1, 1), date(2021, 12, 31))
    assert m2.planned == 0 and second.calls == []
    assert bronze_rows(lake) == rows_after_first


def test_backfill_rewrites_partition_without_duplicates(lake):
    run_ingestion(lake, FakeClient(), [LOC], date(2020, 1, 1), date(2021, 12, 31))
    before = bronze_rows(lake)

    client = FakeClient()
    m = run_ingestion(
        lake, client, [LOC], date(2020, 1, 1), date(2021, 12, 31), backfill_years={2020}
    )
    assert m.written == 1 and len(client.calls) == 1
    assert bronze_rows(lake) == before

    table = lake.read_dataset(BRONZE_TABLE).to_pandas()
    assert not table.duplicated(subset=["location_id", "time"]).any()


def test_failed_partition_does_not_stop_others_and_is_retried_next_run(lake):
    client = FakeClient(fail_for={"2020"})
    m = run_ingestion(lake, client, [LOC], date(2020, 1, 1), date(2021, 12, 31))
    assert (m.written, m.failed) == (1, 1)
    assert not lake.exists(partition_path("test_city", 2020))

    m2 = run_ingestion(lake, FakeClient(), [LOC], date(2020, 1, 1), date(2021, 12, 31))
    assert m2.planned == 1 and m2.written == 1


def test_dry_run_writes_nothing(lake):
    client = FakeClient()
    m = run_ingestion(lake, client, [LOC], date(2020, 1, 1), date(2020, 12, 31), dry_run=True)
    assert m.planned == 1 and m.written == 0 and client.calls == []
    assert not lake.exists(partition_path("test_city", 2020))


# --- Retries ------------------------------------------------------------------


def test_retry_recovers_from_transient_errors():
    attempts = {"n": 0}
    sleeps: list[float] = []

    @retry_with_backoff(max_attempts=4, base_delay=1, max_delay=10, sleep=sleeps.append)
    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RetryableError("429")
        return "ok"

    assert flaky() == "ok"
    assert attempts["n"] == 3 and len(sleeps) == 2


def test_retry_gives_up_after_max_attempts():
    @retry_with_backoff(max_attempts=3, base_delay=1, max_delay=10, sleep=lambda _: None)
    def always_fails():
        raise RetryableError("503")

    with pytest.raises(RetryableError):
        always_fails()


def test_non_retryable_errors_are_not_retried():
    calls = {"n": 0}

    @retry_with_backoff(max_attempts=5, sleep=lambda _: None)
    def bad_request():
        calls["n"] += 1
        raise RuntimeError("HTTP 400")

    with pytest.raises(RuntimeError):
        bad_request()
    assert calls["n"] == 1


def test_backoff_grows_and_respects_cap_and_retry_after():
    assert compute_delay(1, base=2, cap=60) <= 2
    assert 8 <= compute_delay(4, base=2, cap=60) <= 16
    assert compute_delay(20, base=2, cap=60) <= 60
    assert compute_delay(1, base=2, cap=60, retry_after=30) == 30
