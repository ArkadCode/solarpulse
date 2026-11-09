"""End-to-end test of Bronze -> Silver -> Gold with dbt on synthetic data.

Runs without network or Docker: synthetic Bronze is written to a temp folder and dbt
builds a temp DuckDB warehouse. A few rows are corrupted on purpose to prove they land
in quarantine, and a backfill proves partitions are replaced without duplicates.
"""

from __future__ import annotations

import copy
from datetime import date

import duckdb
import pytest

from src.common.config import load_locations, load_settings
from src.common.storage import Lake
from src.ingestion.extractor import LOCATIONS_TABLE, locations_to_table
from src.ingestion.incremental_loader import run_ingestion
from src.ingestion.synthetic import SyntheticClient
from src.lake.publish import publish_all
from src.transform.dbt_runner import run_dbt, warehouse_path

START, END = date(2023, 1, 1), date(2024, 12, 31)
LOCATION_IDS = ["la_guajira", "bogota"]

# (location latitude, hour index in the year's response, variable, bad value, expected rule)
CORRUPTIONS = [
    ("bogota", 2023, 100, "shortwave_radiation", -5.0, "ghi_out_of_range"),
    ("bogota", 2023, 200, "temperature_2m", 80.0, "temperature_out_of_range"),
    ("bogota", 2023, 300, "shortwave_radiation", None, "missing_required"),
    ("la_guajira", 2024, 400, "diffuse_radiation", 2000.0, "dhi_out_of_range"),
]


class CorruptingClient(SyntheticClient):
    """Synthetic data with a few known bad values."""

    def __init__(self, corruptions):
        super().__init__()
        self.corruptions = corruptions
        self.by_latitude = {loc.latitude: loc.location_id for loc in load_locations()}

    def fetch_hourly(self, latitude, longitude, start_date, end_date):
        response, params = super().fetch_hourly(latitude, longitude, start_date, end_date)
        location_id = self.by_latitude[latitude]
        for loc, year, hour, var, value, _ in self.corruptions:
            if loc == location_id and year == start_date.year:
                response["hourly"][var][hour] = value
        return response, params


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    settings = copy.deepcopy(load_settings())
    settings["storage"]["backend"] = "local"
    settings["storage"]["local_root"] = str(tmp / "lake")
    settings["warehouse"]["path"] = str(tmp / "warehouse.duckdb")
    lake = Lake.from_settings(settings)
    locations = load_locations(only=LOCATION_IDS)
    lake.write_parquet(f"{LOCATIONS_TABLE}/data.parquet", locations_to_table(locations))
    dbt_args = [
        "--target-path", str(tmp / "target"),
        "--log-path", str(tmp / "logs"),
        "--no-partial-parse",
    ]
    return {"settings": settings, "lake": lake, "locations": locations, "dbt_args": dbt_args}


def build(env) -> bool:
    return run_dbt(["build", *env["dbt_args"]], env["settings"])


def query(env, sql: str):
    con = duckdb.connect(str(warehouse_path(env["settings"])), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def scalar(env, sql: str):
    return query(env, sql)[0][0]


@pytest.fixture(scope="module")
def built(env):
    run_ingestion(env["lake"], CorruptingClient(CORRUPTIONS), env["locations"], START, END)
    assert build(env), "dbt build failed"
    return env


def test_every_bronze_row_lands_in_silver_or_quarantine(built):
    bronze = built["lake"].read_dataset("bronze/raw_openmeteo").num_rows
    fact = scalar(built, "select count(*) from silver.fact_hourly_weather")
    quarantine = scalar(built, "select count(*) from silver.quarantine_hourly_weather")
    assert bronze == (365 + 366) * 24 * len(LOCATION_IDS)
    assert fact + quarantine == bronze
    assert quarantine == len(CORRUPTIONS)


def test_quarantine_records_the_broken_rule(built):
    rules = {
        (loc, rule)
        for loc, rules_list in query(
            built, "select location_id, failed_rules from silver.quarantine_hourly_weather"
        )
        for rule in rules_list
    }
    # A row can break several rules (a negative GHI also makes DHI > GHI), so each
    # corruption's main rule must be present.
    assert {(loc, rule) for loc, _, _, _, _, rule in CORRUPTIONS} <= rules


def test_gold_tables_are_built(built):
    periods = query(
        built, "select distinct period from gold.location_reliability_score order by 1"
    )
    assert [p[0] for p in periods] == ["2023", "2024", "all"]
    srs = query(built, "select min(srs), max(srs) from gold.location_reliability_score")[0]
    assert 0 <= srs[0] <= srs[1] <= 100
    # local days from 2022-12-31 (5 hours) to 2024-12-31 (19 hours)
    days = scalar(built, "select count(*) from gold.daily_solar_metrics")
    assert days == (1 + 365 + 366) * len(LOCATION_IDS)


def test_daily_energy_matches_hourly_sum(built):
    diff = scalar(
        built,
        """
        select max(abs(d.ghi_kwh_m2 - h.kwh))
        from gold.daily_solar_metrics d
        join (
            select location_id, date_local, sum(ghi_wm2) / 1000 as kwh
            from silver.fact_hourly_weather group by all
        ) h using (location_id, date_local)
        """,
    )
    assert diff < 1e-9


def test_rebuild_without_new_bronze_changes_nothing(built):
    before = query(built, "select count(*), max(_loaded_at) from silver.fact_hourly_weather")
    assert build(built)
    after = query(built, "select count(*), max(_loaded_at) from silver.fact_hourly_weather")
    assert before == after


def test_backfill_replaces_partition_without_duplicates(built):
    clean = CorruptingClient(corruptions=[])
    m = run_ingestion(
        built["lake"], clean, built["locations"], START, END, backfill_years={2023}
    )
    assert m.written == len(LOCATION_IDS)
    assert build(built)

    dupes = scalar(
        built,
        "select count(*) - count(distinct (location_id, timestamp_utc)) "
        "from silver.fact_hourly_weather",
    )
    assert dupes == 0
    # The 2023 corruptions are gone; only the 2024 one remains quarantined.
    remaining = query(built, "select location_id from silver.quarantine_hourly_weather")
    assert remaining == [("la_guajira",)]
    reloaded = query(
        built,
        "select distinct _bronze_partition from silver.fact_hourly_weather "
        "where _loaded_at = (select max(_loaded_at) from silver.fact_hourly_weather) order by 1",
    )
    assert [r[0] for r in reloaded] == ["bogota/2023", "la_guajira/2023"]


def test_publish_writes_partitioned_parquet_to_the_lake(built):
    published = publish_all(built["settings"])
    fact = built["lake"].read_dataset("silver/fact_hourly_weather")
    assert fact.num_rows == published["silver.fact_hourly_weather"]
    assert {"year", "month"} <= set(fact.column_names)
    # Publishing again replaces partitions instead of appending.
    publish_all(built["settings"])
    assert built["lake"].read_dataset("silver/fact_hourly_weather").num_rows == fact.num_rows


# --- Phase 5: scoring checks on the same warehouse ----------------------------------


def test_python_srs_matches_dbt(built):
    from src.scoring.sensitivity import load_components
    from src.scoring.srs import dbt_weights, normalized_components, scores

    raw = load_components(built["settings"])
    python_srs = scores(normalized_components(raw), dbt_weights()).round(2)
    dbt_srs = dict(
        query(built, "select location_id, srs from gold.location_reliability_score "
                     "where period = 'all'")
    )
    for location_id, value in python_srs.items():
        assert value == pytest.approx(dbt_srs[location_id], abs=0.01)


def test_sensitivity_analysis_runs(built):
    from src.scoring.sensitivity import analyze, load_components
    from src.scoring.srs import dbt_weights

    result = analyze(load_components(built["settings"]), dbt_weights())
    assert len(result["per_location"]) == len(LOCATION_IDS)
    assert -1 <= result["mean_spearman"] <= 1
    assert sum(r["p_rank_1"] for r in result["per_location"]) == pytest.approx(1, abs=0.01)


def test_anomalies_are_centered_on_the_climatology(built):
    mean_z, classes = query(
        built,
        "select avg(z_score), list(distinct anomaly_class) from gold.daily_radiation_anomalies",
    )[0]
    assert abs(mean_z) < 0.1
    assert set(classes) <= {"unusually_low", "normal", "unusually_high"}
