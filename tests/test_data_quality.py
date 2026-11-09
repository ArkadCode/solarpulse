"""Great Expectations suites: clean data passes, broken data fails the right expectation."""

from __future__ import annotations

import pandas as pd
import pytest

from src.quality import expectations
from src.quality.run_quality import to_markdown, validate_dataframe


def hourly_frame(n: int = 48) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="h")
    ghi = [max(0.0, 800 * (1 - abs(h % 24 - 17) / 6)) for h in range(n)]
    return pd.DataFrame(
        {
            "location_id": "bogota",
            "timestamp_utc": ts,
            "hour_local": (ts - pd.Timedelta(hours=5)).hour,
            "ghi_wm2": ghi,
            "dhi_wm2": [g * 0.3 for g in ghi],
            "dni_wm2": [g * 1.1 for g in ghi],
            "direct_horizontal_wm2": [g * 0.7 for g in ghi],
            "temperature_c": 15.0,
        }
    )


def failed(outcome) -> set[tuple[str, str | None]]:
    return {(e.expectation, e.column) for e in outcome.expectations if not e.success}


def test_clean_hourly_data_passes():
    outcome = validate_dataframe("fact", hourly_frame(), expectations.fact_hourly_weather())
    assert outcome.success, failed(outcome)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda df: df.assign(ghi_wm2=df.ghi_wm2.where(df.index != 5, 1600.0)),
         ("expect_column_values_to_be_between", "ghi_wm2")),
        (lambda df: df.assign(temperature_c=df.temperature_c.where(df.index != 3, None)),
         ("expect_column_values_to_not_be_null", "temperature_c")),
        (lambda df: pd.concat([df, df.iloc[[0]]]),
         ("expect_compound_columns_to_be_unique", "location_id,timestamp_utc")),
    ],
)
def test_broken_hourly_data_fails_expected_check(mutate, expected):
    outcome = validate_dataframe("fact", mutate(hourly_frame()), expectations.fact_hourly_weather())
    assert not outcome.success
    assert expected in failed(outcome)


def test_score_out_of_range_fails():
    df = pd.DataFrame(
        {
            "period": ["all", "all"],
            "location_id": ["a", "b"],
            "srs": [55.0, 120.0],
            "x_mean_radiation": [0.0, 1.0],
            "x_variability": [0.5, 0.5],
            "x_seasonality": [0.2, 0.8],
            "x_low_radiation": [1.0, 0.0],
            "x_temperature": [0.3, 0.7],
        }
    )
    outcome = validate_dataframe("score", df, expectations.location_reliability_score())
    assert ("expect_column_values_to_be_between", "srs") in failed(outcome)


def test_report_renders_markdown():
    outcome = validate_dataframe("fact", hourly_frame(), expectations.fact_hourly_weather())
    report = {
        "run_at": "2026-01-01T00:00:00+00:00",
        "success": outcome.success,
        "quarantine": [{"rule": "ghi_out_of_range", "rows": 3}],
        "suites": [
            {**vars(outcome), "expectations": [vars(e) for e in outcome.expectations]}
        ],
    }
    md = to_markdown(report)
    assert "overall: **PASS**" in md and "ghi_out_of_range" in md
