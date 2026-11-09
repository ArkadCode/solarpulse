"""Compares SolarPulse (ERA5) mean daily GHI with two independent satellite sources.

Agreement in *ranking* matters most: the SRS compares locations, so a constant bias is
less harmful than a source that orders the cities differently.

Usage: python -m src.scoring.validation
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa

from src.common.config import CONFIG_DIR, PROJECT_ROOT, load_settings
from src.common.run_metrics import tracked_run
from src.common.storage import Lake
from src.transform.dbt_runner import warehouse_path

SOURCES = {
    "nasa_power_ghi_kwh_m2_day": "NASA POWER",
    "global_solar_atlas_ghi_kwh_m2_day": "Global Solar Atlas",
}


def load(settings: dict[str, Any]) -> pd.DataFrame:
    reference = pd.read_csv(CONFIG_DIR / "external_reference.csv", comment="#")
    con = duckdb.connect(str(warehouse_path(settings)), read_only=True)
    try:
        ours = con.execute(
            """
            select location_id, location_name, region, srs, srs_rank,
                   mean_daily_ghi_kwh_m2 as era5_ghi_kwh_m2_day
            from gold.location_reliability_score where period = 'all'
            """
        ).df()
    finally:
        con.close()
    return ours.merge(reference, on="location_id", how="inner")


def spearman(a: pd.Series, b: pd.Series) -> float:
    return float(np.corrcoef(a.rank(), b.rank())[0, 1])


def compare(df: pd.DataFrame) -> dict[str, Any]:
    summary = {}
    for col, name in SOURCES.items():
        diff = df["era5_ghi_kwh_m2_day"] - df[col]
        summary[name] = {
            "mean_bias_kwh_m2_day": round(float(diff.mean()), 3),
            "mean_abs_error_kwh_m2_day": round(float(diff.abs().mean()), 3),
            "mean_abs_pct_error": round(float((diff.abs() / df[col]).mean() * 100), 1),
            "spearman_vs_era5": round(spearman(df["era5_ghi_kwh_m2_day"], df[col]), 3),
            "spearman_vs_srs": round(spearman(-df["srs_rank"], df[col]), 3),
        }
    summary["NASA POWER vs Global Solar Atlas"] = {
        "spearman": round(spearman(df[list(SOURCES)[0]], df[list(SOURCES)[1]]), 3)
    }
    table = df.assign(
        **{f"rank_{c.split('_ghi')[0]}": df[c].rank(ascending=False).astype(int) for c in SOURCES},
        rank_era5=df["era5_ghi_kwh_m2_day"].rank(ascending=False).astype(int),
    ).sort_values("srs_rank")
    return {"summary": summary, "locations": table.round(3).to_dict(orient="records")}


def to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# SRS external validation",
        "",
        "Mean daily GHI (kWh/m²/day): SolarPulse (ERA5, 2011–2026) against NASA POWER "
        "(CERES, 2001–2025) and Global Solar Atlas (Solargis).",
        "",
        "| SRS rank | Location | ERA5 | NASA POWER | Global Solar Atlas | "
        "Rank ERA5 / NASA / GSA |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in result["locations"]:
        lines.append(
            f"| {r['srs_rank']} | {r['location_name']} | {r['era5_ghi_kwh_m2_day']:.2f} | "
            f"{r['nasa_power_ghi_kwh_m2_day']:.2f} | "
            f"{r['global_solar_atlas_ghi_kwh_m2_day']:.2f} | "
            f"{r['rank_era5']} / {r['rank_nasa_power']} / {r['rank_global_solar_atlas']} |"
        )
    lines += ["", "| Comparison | Bias | MAE | MAPE | Spearman (GHI) | Spearman (SRS) |",
              "| --- | --- | --- | --- | --- | --- |"]
    for name in SOURCES.values():
        s = result["summary"][name]
        lines.append(
            f"| ERA5 vs {name} | {s['mean_bias_kwh_m2_day']:+.2f} | "
            f"{s['mean_abs_error_kwh_m2_day']:.2f} | {s['mean_abs_pct_error']:.1f} % | "
            f"{s['spearman_vs_era5']:.2f} | {s['spearman_vs_srs']:.2f} |"
        )
    between = result["summary"]["NASA POWER vs Global Solar Atlas"]["spearman"]
    lines += ["", f"Spearman between the two references themselves: {between:.2f}.", ""]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    with tracked_run("srs_validation", settings) as run:
        result = compare(load(settings))
        report_dir = PROJECT_ROOT / settings["reports"]["dir"]
        report_dir.mkdir(exist_ok=True)
        (report_dir / "srs_validation.json").write_text(json.dumps(result, indent=2))
        (report_dir / "srs_validation.md").write_text(to_markdown(result))
        Lake.from_settings(settings).write_parquet(
            "gold/srs_validation/data.parquet", pa.Table.from_pylist(result["locations"])
        )
        run.update(summary=result["summary"])
    print(to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
