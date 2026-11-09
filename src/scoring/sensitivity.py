"""How much does the SRS ranking depend on the chosen weights?

Two checks on the full-history period:
1. One at a time: each weight moves ±10 percentage points; the others shrink or grow
   proportionally so the total stays 1. Reports the largest rank change.
2. Monte Carlo: 5,000 random weight vectors from a Dirichlet distribution centered on
   the baseline weights. Reports, per location, how often it ranks first, its median
   rank and its 5-95 % rank interval, plus the mean Spearman correlation with the
   baseline ranking.

Usage: python -m src.scoring.sensitivity
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa

from src.common.config import PROJECT_ROOT, load_settings
from src.common.logging import get_logger
from src.common.run_metrics import tracked_run
from src.common.storage import Lake
from src.scoring.srs import COMPONENTS, dbt_weights, normalized_components, ranks, scores
from src.transform.dbt_runner import warehouse_path

log = get_logger("sensitivity")

SAMPLES = 5000
CONCENTRATION = 50  # higher = samples closer to the baseline weights
SHIFT = 0.10


def load_components(settings: dict[str, Any]) -> pd.DataFrame:
    con = duckdb.connect(str(warehouse_path(settings)), read_only=True)
    try:
        df = con.execute(
            """
            select c.*, l.location_name
            from intermediate.int_reliability_components c
            join silver.dim_location l using (location_id)
            where c.period = 'all'
            """
        ).df()
    finally:
        con.close()
    return df.set_index("location_id")


def shifted_weights(base: dict[str, float], component: str, delta: float) -> dict[str, float]:
    new = base[component] + delta
    rest = 1 - base[component]
    factor = (1 - new) / rest
    return {c: (new if c == component else w * factor) for c, w in base.items()}


def spearman(a: pd.Series, b: pd.Series) -> float:
    return float(np.corrcoef(a.rank(), b.rank())[0, 1])


def analyze(raw: pd.DataFrame, base: dict[str, float], seed: int = 7) -> dict[str, Any]:
    x = normalized_components(raw)
    base_rank = ranks(scores(x, base))

    one_at_a_time = []
    for comp in COMPONENTS:
        for delta in (-SHIFT, SHIFT):
            if not 0 <= base[comp] + delta <= 1:
                continue
            r = ranks(scores(x, shifted_weights(base, comp, delta)))
            one_at_a_time.append(
                {
                    "component": comp,
                    "delta_pp": round(delta * 100),
                    "max_rank_change": int((r - base_rank).abs().max()),
                    "locations_moved": int((r != base_rank).sum()),
                    "top1": r.idxmin(),
                    "spearman": round(spearman(r, base_rank), 3),
                }
            )

    rng = np.random.default_rng(seed)
    alpha = np.array([base[c] for c in COMPONENTS]) * CONCENTRATION
    samples = rng.dirichlet(alpha, size=SAMPLES)
    rank_matrix = np.column_stack([ranks(scores(x, w)).to_numpy() for w in samples])
    correlations = [
        spearman(pd.Series(rank_matrix[:, i]), base_rank.reset_index(drop=True))
        for i in range(SAMPLES)
    ]

    per_location = pd.DataFrame(
        {
            "location_name": raw["location_name"],
            "baseline_rank": base_rank,
            "p_rank_1": (rank_matrix == 1).mean(axis=1),
            "median_rank": np.median(rank_matrix, axis=1),
            "rank_p05": np.percentile(rank_matrix, 5, axis=1),
            "rank_p95": np.percentile(rank_matrix, 95, axis=1),
        },
        index=raw.index,
    ).sort_values("baseline_rank")

    return {
        "baseline_weights": base,
        "samples": SAMPLES,
        "concentration": CONCENTRATION,
        "mean_spearman": round(float(np.mean(correlations)), 3),
        "share_same_top1": round(
            float((rank_matrix[base_rank.to_numpy() == 1].min(axis=0) == 1).mean()), 3
        ),
        "one_at_a_time": one_at_a_time,
        "per_location": per_location.reset_index().round(3).to_dict(orient="records"),
    }


def to_markdown(result: dict[str, Any]) -> str:
    w = ", ".join(f"{k} {v:.2f}" for k, v in result["baseline_weights"].items())
    lines = [
        "# SRS sensitivity analysis",
        "",
        f"Baseline weights: {w}.",
        "",
        f"Monte Carlo: {result['samples']:,} Dirichlet weight samples "
        f"(concentration {result['concentration']}). Mean Spearman correlation with the "
        f"baseline ranking: **{result['mean_spearman']}**. The baseline leader stays first "
        f"in **{result['share_same_top1']:.0%}** of samples.",
        "",
        "| Baseline rank | Location | P(rank 1) | Median rank | 5–95 % rank |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in result["per_location"]:
        lines.append(
            f"| {r['baseline_rank']} | {r['location_name']} | {r['p_rank_1']:.0%} | "
            f"{r['median_rank']:.0f} | {r['rank_p05']:.0f}–{r['rank_p95']:.0f} |"
        )
    lines += [
        "",
        "## One weight at a time (±10 percentage points)",
        "",
        "| Component | Change | Max rank change | Locations moved | Leader | Spearman |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in result["one_at_a_time"]:
        lines.append(
            f"| {r['component']} | {r['delta_pp']:+d} pp | {r['max_rank_change']} | "
            f"{r['locations_moved']} | {r['top1']} | {r['spearman']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    settings = load_settings()
    with tracked_run("srs_sensitivity", settings) as run:
        result = analyze(load_components(settings), dbt_weights())
        report_dir = PROJECT_ROOT / settings["reports"]["dir"]
        report_dir.mkdir(exist_ok=True)
        (report_dir / "srs_sensitivity.json").write_text(json.dumps(result, indent=2))
        (report_dir / "srs_sensitivity.md").write_text(to_markdown(result))
        lake = Lake.from_settings(settings)
        lake.write_parquet(
            "gold/srs_sensitivity/data.parquet",
            pa.Table.from_pylist(result["per_location"]),
        )
        run.update(mean_spearman=result["mean_spearman"], samples=result["samples"])
    print(to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
