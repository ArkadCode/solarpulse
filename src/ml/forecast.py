"""Next-day solar energy forecast per location, tracked in MLflow.

Target: daily GHI energy (kWh/m²) of day t, using only information available at the end
of day t-1. A gradient-boosted model is compared with two naive baselines:
- persistence: tomorrow = today,
- climatology: tomorrow = the training-period mean for that location and day of year.

Time-based split (no shuffling, so the model never sees the future):
  train 2011-2021 · validation 2022-2023 (model selection) · test 2024 onwards.
The skill score is 1 - MSE(model) / MSE(climatology): 0 means no better than the
climatology, 1 means perfect.

Usage: python -m src.ml.forecast
"""

from __future__ import annotations

import json
import os
from typing import Any

import duckdb
import mlflow
import numpy as np
import pandas as pd
import pyarrow as pa
from mlflow.models import infer_signature
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from src.common.config import PROJECT_ROOT, load_settings
from src.common.logging import get_logger
from src.common.run_metrics import tracked_run
from src.common.storage import Lake
from src.transform.dbt_runner import warehouse_path

log = get_logger("forecast")

TRAIN_END, VALID_END = 2021, 2023
EXPERIMENT = "solarpulse-next-day-ghi"
MODEL_NAME = "solarpulse_next_day_ghi"
FEATURES = [
    "location_code", "doy_sin", "doy_cos", "clim_mean", "clim_std",
    "lag_1", "lag_2", "lag_3", "lag_7", "roll_7_mean", "roll_7_std", "roll_30_mean",
    "diffuse_ratio_lag_1", "temp_max_lag_1", "sunshine_hours_lag_1",
]
SKOPS_TRUSTED_TYPES = [
    "functools.partial",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
    "sklearn.utils.validation.check_array",
]
PARAM_GRID = [
    {"learning_rate": lr, "max_depth": depth, "max_iter": 400, "min_samples_leaf": 40}
    for lr in (0.03, 0.08)
    for depth in (3, 6)
]


def load_daily(settings: dict[str, Any]) -> pd.DataFrame:
    con = duckdb.connect(str(warehouse_path(settings)), read_only=True)
    try:
        df = con.execute(
            """
            select location_id, date_local, year, ghi_kwh_m2, diffuse_ratio,
                   temp_max_c, sunshine_hours
            from gold.daily_solar_metrics
            where is_complete_day
            order by location_id, date_local
            """
        ).df()
    finally:
        con.close()
    df["date_local"] = pd.to_datetime(df["date_local"])
    return df


def build_features(daily: pd.DataFrame) -> pd.DataFrame:
    df = daily.copy()
    g = df.groupby("location_id")["ghi_kwh_m2"]
    for lag in (1, 2, 3, 7):
        df[f"lag_{lag}"] = g.shift(lag)
    shifted = g.shift(1)
    by_loc = shifted.groupby(df["location_id"])
    df["roll_7_mean"] = by_loc.transform(lambda s: s.rolling(7, min_periods=5).mean())
    df["roll_7_std"] = by_loc.transform(lambda s: s.rolling(7, min_periods=5).std())
    df["roll_30_mean"] = by_loc.transform(lambda s: s.rolling(30, min_periods=20).mean())
    for col in ("diffuse_ratio", "temp_max_c", "sunshine_hours"):
        name = {"temp_max_c": "temp_max"}.get(col, col)
        df[f"{name}_lag_1"] = df.groupby("location_id")[col].shift(1)

    # A gap (a missing or incomplete day) would make lags wrong, so require lag 1 = yesterday.
    prev_date = df.groupby("location_id")["date_local"].shift(1)
    df = df[(df["date_local"] - prev_date).dt.days == 1].copy()

    doy = df["date_local"].dt.dayofyear.clip(upper=365)
    df["doy"] = doy
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365)
    df["location_code"] = df["location_id"].astype("category").cat.codes

    # Climatology from training years only, smoothed over ±7 days, to avoid leakage.
    train = df[df["year"] <= TRAIN_END]
    clim = train.groupby(["location_id", "doy"])["ghi_kwh_m2"].agg(["mean", "std"])
    clim = (
        clim.groupby(level="location_id", group_keys=False)
        .apply(lambda c: c.reindex(range(1, 366), level="doy"))
    )
    smoothed = []
    for loc, c in clim.groupby(level="location_id"):
        c = c.droplevel("location_id").reindex(range(1, 366))
        padded = pd.concat([c.iloc[-7:], c, c.iloc[:7]])
        rolled = padded.rolling(15, center=True, min_periods=1).mean().iloc[7:-7]
        rolled["location_id"] = loc
        smoothed.append(rolled.reset_index(names="doy"))
    clim = pd.concat(smoothed).rename(columns={"mean": "clim_mean", "std": "clim_std"})
    df = df.merge(clim, on=["location_id", "doy"], how="left")
    return df.dropna(subset=["lag_7", "roll_30_mean", "clim_mean"])


def split(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "train": df[df["year"] <= TRAIN_END],
        "valid": df[(df["year"] > TRAIN_END) & (df["year"] <= VALID_END)],
        "test": df[df["year"] > VALID_END],
    }


def metrics(y: pd.Series, pred: np.ndarray, clim: pd.Series) -> dict[str, float]:
    mse = mean_squared_error(y, pred)
    return {
        "mae": round(float(mean_absolute_error(y, pred)), 4),
        "rmse": round(float(np.sqrt(mse)), 4),
        "skill_vs_climatology": round(float(1 - mse / mean_squared_error(y, clim)), 4),
    }


def configure_tracking() -> None:
    """Use the MLflow server when MLFLOW_TRACKING_URI is set (Docker Compose, host via
    .env); otherwise a local SQLite store, e.g. for tests."""
    uri = os.getenv("MLFLOW_TRACKING_URI")
    if uri:
        mlflow.set_tracking_uri(uri)
        if mlflow.get_experiment_by_name(EXPERIMENT) is None:
            mlflow.create_experiment(EXPERIMENT)
        return
    mlflow_dir = PROJECT_ROOT / "data" / "mlflow"
    mlflow_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{mlflow_dir / 'mlflow.db'}")
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        artifacts = (mlflow_dir / "artifacts").as_uri()
        mlflow.create_experiment(EXPERIMENT, artifact_location=artifacts)


def train_and_evaluate(settings: dict[str, Any]) -> dict[str, Any]:
    data = split(build_features(load_daily(settings)))
    train, valid, test = data["train"], data["valid"], data["test"]

    configure_tracking()
    mlflow.set_experiment(EXPERIMENT)

    baselines = {}
    with mlflow.start_run(run_name="baselines"):
        for name, part in (("valid", valid), ("test", test)):
            naive = (("persistence", part["lag_1"]), ("climatology", part["clim_mean"]))
            for baseline, pred in naive:
                m = metrics(part["ghi_kwh_m2"], pred.to_numpy(), part["clim_mean"])
                baselines[f"{baseline}_{name}"] = m
                mlflow.log_metrics({f"{name}_{k}": v for k, v in m.items()}, step=0)
                mlflow.log_metrics({f"{baseline}_{name}_{k}": v for k, v in m.items()})

    best = None
    for params in PARAM_GRID:
        with mlflow.start_run(run_name=f"hgb_lr{params['learning_rate']}_d{params['max_depth']}"):
            model = HistGradientBoostingRegressor(
                **params, categorical_features=[0], random_state=42, early_stopping=False
            )
            model.fit(train[FEATURES], train["ghi_kwh_m2"])
            m = metrics(valid["ghi_kwh_m2"], model.predict(valid[FEATURES]), valid["clim_mean"])
            mlflow.log_params({**params, "features": len(FEATURES), "train_end": TRAIN_END})
            mlflow.log_metrics({f"valid_{k}": v for k, v in m.items()})
            log.info("candidate_evaluated", extra={**params, **m})
            if best is None or m["rmse"] < best["valid"]["rmse"]:
                best = {"params": params, "valid": m}

    # Refit the chosen configuration on train + validation, then evaluate once on test.
    fit_data = pd.concat([train, valid])
    with mlflow.start_run(run_name="final_model") as run:
        model = HistGradientBoostingRegressor(
            **best["params"], categorical_features=[0], random_state=42, early_stopping=False
        )
        model.fit(fit_data[FEATURES], fit_data["ghi_kwh_m2"])
        test_pred = model.predict(test[FEATURES])
        test_metrics = metrics(test["ghi_kwh_m2"], test_pred, test["clim_mean"])

        per_location = (
            test.assign(pred=test_pred)
            .groupby("location_id")
            .apply(
                lambda d: pd.Series(
                    metrics(d["ghi_kwh_m2"], d["pred"].to_numpy(), d["clim_mean"])
                ),
                include_groups=False,
            )
            .reset_index()
        )
        mlflow.log_params({**best["params"], "train_end": VALID_END, "features": len(FEATURES)})
        mlflow.log_metrics({f"test_{k}": v for k, v in test_metrics.items()})
        mlflow.log_dict(per_location.to_dict(orient="records"), "test_metrics_by_location.json")
        mlflow.sklearn.log_model(
            model,
            name="model",
            signature=infer_signature(test[FEATURES], test_pred),
            registered_model_name=MODEL_NAME,
            # The model is trained here, so these sklearn internals are trusted on load.
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
        )
        run_id = run.info.run_id

    predictions = test[["location_id", "date_local", "ghi_kwh_m2", "lag_1", "clim_mean"]].assign(
        forecast_kwh_m2=test_pred
    )
    return {
        "rows": {k: len(v) for k, v in data.items()},
        "best_params": best["params"],
        "valid_best": best["valid"],
        "test": test_metrics,
        "baselines": baselines,
        "per_location_test": per_location.round(4).to_dict(orient="records"),
        "mlflow_run_id": run_id,
        "predictions": predictions,
    }


def to_markdown(r: dict[str, Any]) -> str:
    rows = [
        ("Persistence (tomorrow = today)", r["baselines"]["persistence_test"]),
        ("Climatology (seasonal mean)", r["baselines"]["climatology_test"]),
        ("Gradient boosting (this model)", r["test"]),
    ]
    lines = [
        "# Next-day solar energy forecast",
        "",
        f"Test period 2024 onwards ({r['rows']['test']:,} location-days). "
        f"Trained on {r['rows']['train'] + r['rows']['valid']:,} location-days.",
        "",
        "| Model | MAE (kWh/m²) | RMSE (kWh/m²) | Skill vs climatology |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {n} | {m['mae']:.3f} | {m['rmse']:.3f} | {m['skill_vs_climatology']:+.1%} |"
              for n, m in rows]
    lines += ["", f"Selected parameters: {json.dumps(r['best_params'])}. "
              f"MLflow run `{r['mlflow_run_id']}`.", ""]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    with tracked_run("ml_forecast", settings) as run:
        result = train_and_evaluate(settings)
        predictions = result.pop("predictions")
        report_dir = PROJECT_ROOT / settings["reports"]["dir"]
        report_dir.mkdir(exist_ok=True)
        (report_dir / "ml_forecast.json").write_text(json.dumps(result, indent=2, default=str))
        (report_dir / "ml_forecast.md").write_text(to_markdown(result))
        Lake.from_settings(settings).write_parquet(
            "gold/ghi_forecast_test/data.parquet",
            pa.Table.from_pandas(predictions, preserve_index=False),
        )
        run.update(test=result["test"], mlflow_run_id=result["mlflow_run_id"])
    print(to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
