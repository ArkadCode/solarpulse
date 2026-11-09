"""Feature engineering for the forecast never looks into the future."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml.forecast import TRAIN_END, build_features, split


def daily_frame() -> pd.DataFrame:
    dates = pd.date_range("2019-01-01", "2024-06-30", freq="D")
    rng = np.random.default_rng(0)
    frames = []
    for loc in ("a", "b"):
        ghi = 5 + np.sin(2 * np.pi * dates.dayofyear / 365) + rng.normal(0, 0.5, len(dates))
        frames.append(pd.DataFrame({
            "location_id": loc, "date_local": dates, "year": dates.year, "ghi_kwh_m2": ghi,
            "diffuse_ratio": 0.3, "temp_max_c": 30.0, "sunshine_hours": 8,
        }))
    return pd.concat(frames, ignore_index=True)


def test_lags_use_previous_days_only():
    daily = daily_frame()
    feats = build_features(daily)
    lookup = daily.set_index(["location_id", "date_local"])["ghi_kwh_m2"]
    sample = feats.sample(200, random_state=1)
    for _, row in sample.iterrows():
        yesterday = row["date_local"] - pd.Timedelta(days=1)
        assert row["lag_1"] == lookup[(row["location_id"], yesterday)]
        week_ago = row["date_local"] - pd.Timedelta(days=7)
        assert row["lag_7"] == lookup[(row["location_id"], week_ago)]


def test_rolling_means_exclude_the_target_day():
    daily = daily_frame()
    feats = build_features(daily).set_index(["location_id", "date_local"])
    series = daily[daily["location_id"] == "a"].set_index("date_local")["ghi_kwh_m2"]
    day = pd.Timestamp("2022-06-15")
    expected = series.loc[day - pd.Timedelta(days=7): day - pd.Timedelta(days=1)].mean()
    assert feats.loc[("a", day), "roll_7_mean"] == pytest.approx(expected)


def test_climatology_comes_from_training_years_only():
    daily = daily_frame()
    # Make the test years wildly different: a leaking climatology would follow them.
    daily.loc[daily["year"] > TRAIN_END, "ghi_kwh_m2"] += 100
    feats = build_features(daily)
    assert feats["clim_mean"].max() < 10


def test_split_is_chronological():
    parts = split(build_features(daily_frame()))
    assert parts["train"]["year"].max() <= TRAIN_END < parts["valid"]["year"].min()
    assert parts["valid"]["year"].max() < parts["test"]["year"].min()
