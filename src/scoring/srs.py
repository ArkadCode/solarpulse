"""Python implementation of the SRS formula, mirroring the dbt model.

dbt computes the official score (gold.location_reliability_score). This module recomputes
it from the raw components so the weights can be varied thousands of times for the
sensitivity analysis. A test checks both implementations agree.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import yaml

from src.common.config import PROJECT_ROOT

COMPONENTS = ["mean_radiation", "variability", "seasonality", "low_radiation", "temperature"]


def dbt_weights() -> dict[str, float]:
    project = yaml.safe_load((PROJECT_ROOT / "dbt" / "dbt_project.yml").read_text())
    return {k: float(v) for k, v in project["vars"]["srs_weights"].items()}


def minmax(values: pd.Series, higher_is_better: bool) -> pd.Series:
    spread = values.max() - values.min()
    if not spread or np.isnan(spread):
        return pd.Series(0.5, index=values.index)
    scaled = (values - values.min()) / spread
    return (scaled if higher_is_better else 1 - scaled).fillna(0.5)


def normalized_components(raw: pd.DataFrame) -> pd.DataFrame:
    """raw: one row per location with the columns of int_reliability_components."""
    return pd.DataFrame(
        {
            "mean_radiation": minmax(raw["mean_daily_ghi_kwh_m2"], True),
            "variability": (
                minmax(raw["daily_cv"], False) + minmax(raw["ramp_volatility"], False)
            )
            / 2,
            "seasonality": minmax(raw["seasonality_cv"], False),
            "low_radiation": minmax(raw["low_radiation_share"], False),
            "temperature": minmax(raw["heat_stress_c"], False),
        },
        index=raw.index,
    )


def scores(x: pd.DataFrame, weights: dict[str, Any] | np.ndarray) -> pd.Series:
    w = np.array([weights[c] for c in COMPONENTS]) if isinstance(weights, dict) else weights
    return pd.Series(100 * x[COMPONENTS].to_numpy() @ w, index=x.index)


def ranks(score: pd.Series) -> pd.Series:
    return score.rank(ascending=False, method="min").astype(int)
