"""SolarPulse dashboard: the Solar Reliability Score and what is behind it.

Run: streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import PROJECT_ROOT, query, table  # noqa: E402

st.set_page_config(page_title="SolarPulse", layout="wide")

# Validated categorical palette (first three slots pass all-pairs CVD checks in both
# modes). Regions keep their color in every chart.
DARK = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
REGION_COLORS = (
    {"Caribbean": "#3987e5", "Andean": "#d95926", "Orinoquia": "#199e70"}
    if DARK
    else {"Caribbean": "#2a78d6", "Andean": "#eb6834", "Orinoquia": "#1baf7a"}
)
SERIES = ["#3987e5", "#d95926", "#199e70"] if DARK else ["#2a78d6", "#eb6834", "#1baf7a"]
MUTED = "#8a8985"
SEQUENTIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
NAME_AXIS = alt.Axis(labelLimit=220)
region_scale = alt.Scale(domain=list(REGION_COLORS), range=list(REGION_COLORS.values()))


def show_table(df: pd.DataFrame, label: str = "Table view") -> None:
    with st.expander(label):
        st.dataframe(df, hide_index=True, width="stretch")


def stat(col, label: str, value: str, help_text: str | None = None) -> None:
    col.metric(label, value, help=help_text)


# --- data ----------------------------------------------------------------------------

try:
    score_all = table("gold/location_reliability_score")
except Exception as exc:  # noqa: BLE001
    st.error(
        "Could not read the lake. Run the pipeline first (`make pipeline` or "
        f"`make trigger`) and check the object store is up.\n\n{exc}"
    )
    st.stop()

score = score_all[score_all["period"] == "all"].sort_values("srs_rank")
names = dict(zip(score["location_id"], score["location_name"], strict=True))

st.title("SolarPulse")
st.caption(
    "Solar Reliability Score for Colombian cities from 15 years of hourly ERA5 reanalysis "
    "(Open-Meteo). The score compares solar *conditions*; it is not an estimate of "
    "electricity production."
)

tab_rank, tab_loc, tab_robust, tab_ml, tab_ops = st.tabs(
    ["Ranking", "Location detail", "Robustness", "Forecast", "Pipeline health"]
)

# --- ranking ---------------------------------------------------------------------------

with tab_rank:
    c1, c2, c3, c4 = st.columns(4)
    stat(c1, "Locations", f"{len(score)}")
    stat(c2, "Period", f"{score['first_date'].min():%Y} – {score['last_date'].max():%Y}")
    stat(c3, "Complete days scored", f"{int(score['complete_days'].sum()):,}")
    leader = score.iloc[0]
    stat(c4, "Highest SRS", f"{leader['location_name']}", f"SRS {leader['srs']:.1f} / 100")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Solar Reliability Score")
        bars = (
            alt.Chart(score)
            .mark_bar(cornerRadiusEnd=4, height=18)
            .encode(
                x=alt.X("srs:Q", title="SRS (0–100)", scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("location_name:N", sort=alt.SortField("srs_rank"), title=None,
                        axis=NAME_AXIS),
                color=alt.Color("region:N", scale=region_scale, title="Region"),
                tooltip=[
                    alt.Tooltip("location_name:N", title="Location"),
                    alt.Tooltip("region:N", title="Region"),
                    alt.Tooltip("srs:Q", title="SRS", format=".1f"),
                    alt.Tooltip("srs_rank:Q", title="SRS rank"),
                    alt.Tooltip("mean_daily_ghi_kwh_m2:Q", title="Mean GHI kWh/m²/day",
                                format=".2f"),
                ],
            )
        )
        st.altair_chart(bars.properties(height=360), width="stretch")

    with right:
        st.subheader("Radiation rank → SRS rank")
        st.caption("Is the sunniest city also the most reliable? Lines that cross moved.")
        slope = score.melt(
            id_vars=["location_name", "region"],
            value_vars=["mean_radiation_rank", "srs_rank"],
            var_name="ranking",
            value_name="rank",
        )
        slope["ranking"] = slope["ranking"].map(
            {"mean_radiation_rank": "By mean radiation", "srs_rank": "By SRS"}
        )
        base = alt.Chart(slope).encode(
            x=alt.X("ranking:N", title=None, sort=["By mean radiation", "By SRS"],
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("rank:Q", scale=alt.Scale(reverse=True, domain=[1, len(score)]),
                    title="Rank (1 = best)"),
            color=alt.Color("region:N", scale=region_scale, legend=None),
            detail="location_name:N",
            tooltip=["location_name:N", "ranking:N", "rank:Q"],
        )
        labels = base.mark_text(align="left", dx=8, fontSize=11).encode(
            text="location_name:N", color=alt.value(MUTED)
        ).transform_filter(alt.datum.ranking == "By SRS")
        st.altair_chart(
            (base.mark_line(strokeWidth=2) + base.mark_circle(size=80, opacity=1) + labels)
            .properties(height=360),
            width="stretch",
        )

    st.subheader("Score components")
    st.caption("Each component is normalized across locations: 1 = best of the group, 0 = worst.")
    comp_cols = {
        "x_mean_radiation": "Mean radiation (35 %)",
        "x_variability": "Low variability (25 %)",
        "x_seasonality": "Low seasonality (15 %)",
        "x_low_radiation": "Few low-radiation days (15 %)",
        "x_temperature": "Low heat stress (10 %)",
    }
    comps = score.melt(id_vars=["location_name", "srs_rank"], value_vars=list(comp_cols),
                       var_name="component", value_name="value")
    comps["component"] = comps["component"].map(comp_cols)
    heat = (
        alt.Chart(comps)
        .mark_rect(stroke="white", strokeWidth=2, cornerRadius=3)
        .encode(
            x=alt.X("component:N", sort=list(comp_cols.values()), title=None,
                    axis=alt.Axis(labelAngle=0, labelLimit=160)),
            y=alt.Y("location_name:N", sort=alt.SortField("srs_rank"), title=None,
                    axis=NAME_AXIS),
            color=alt.Color("value:Q", scale=alt.Scale(domain=[0, 1], range=SEQUENTIAL),
                            title="Normalized"),
            tooltip=["location_name:N", "component:N", alt.Tooltip("value:Q", format=".2f")],
        )
    )
    st.altair_chart(heat.properties(height=340), width="stretch")
    show_table(score[["srs_rank", "location_name", "region", "srs", "mean_radiation_rank",
                      "mean_daily_ghi_kwh_m2", "daily_cv", "seasonality_cv",
                      "low_radiation_share", "heat_stress_c"]].round(3))

# --- location detail ---------------------------------------------------------------------

with tab_loc:
    loc_id = st.selectbox("Location", list(names), format_func=names.get)
    monthly = table("gold/monthly_solar_metrics", f"where location_id = '{loc_id}'")
    anomalies = table("gold/daily_radiation_anomalies", f"where location_id = '{loc_id}'")
    row = score[score["location_id"] == loc_id].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    stat(c1, "SRS", f"{row['srs']:.1f}", f"Rank {row['srs_rank']} of {len(score)}")
    stat(c2, "Mean daily GHI", f"{row['mean_daily_ghi_kwh_m2']:.2f} kWh/m²")
    stat(c3, "Low-radiation days", f"{row['low_radiation_share']:.1%}",
         "Complete days below 3 kWh/m²")
    stat(c4, "Diffuse share", f"{row['mean_diffuse_ratio']:.0%}", "DHI / GHI")

    left, right = st.columns(2)
    with left:
        st.subheader("Typical year")
        st.caption("Mean daily energy per calendar month; band = 10th–90th percentile.")
        clim = (
            monthly.groupby("month")
            .agg(mean=("mean_daily_ghi_kwh_m2", "mean"),
                 p10=("p10_daily_ghi_kwh_m2", "mean"),
                 p90=("p90_daily_ghi_kwh_m2", "mean"))
            .reset_index()
        )
        x = alt.X("month:O", title="Month", axis=alt.Axis(labelAngle=0))
        band = alt.Chart(clim).mark_area(opacity=0.18, color=SERIES[0]).encode(
            x=x, y=alt.Y("p10:Q", title="kWh/m² per day", scale=alt.Scale(zero=False)),
            y2="p90:Q",
        )
        line = alt.Chart(clim).mark_line(strokeWidth=2, color=SERIES[0], point=True).encode(
            x=x, y="mean:Q",
            tooltip=["month:O", alt.Tooltip("mean:Q", format=".2f"),
                     alt.Tooltip("p10:Q", format=".2f"), alt.Tooltip("p90:Q", format=".2f")],
        )
        st.altair_chart((band + line).properties(height=300), width="stretch")

    with right:
        st.subheader("Year by year")
        st.caption("Mean daily energy per full year.")
        yearly = (
            monthly[monthly["complete_days"] > 0]
            .groupby("year")
            .apply(lambda d: (d["mean_daily_ghi_kwh_m2"] * d["complete_days"]).sum()
                   / d["complete_days"].sum(), include_groups=False)
            .rename("mean")
            .reset_index()
        )
        yearly = yearly[yearly["year"] <= int(pd.Timestamp.today().year) - 1]
        hover = alt.selection_point(fields=["year"], nearest=True, on="pointerover",
                                    empty=False)
        yline = alt.Chart(yearly).mark_line(strokeWidth=2, color=SERIES[0]).encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("mean:Q", title="kWh/m² per day", scale=alt.Scale(zero=False)),
        )
        ypoints = yline.mark_circle(size=70, color=SERIES[0]).encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0.35)),
            tooltip=["year:O", alt.Tooltip("mean:Q", format=".2f", title="kWh/m²/day")],
        ).add_params(hover)
        st.altair_chart((yline + ypoints).properties(height=300), width="stretch")

    st.subheader("Unusually low days per year")
    st.caption("Days at least 2 standard deviations below the seasonal norm for this city.")
    low = (
        anomalies[anomalies["anomaly_class"] == "unusually_low"]
        .groupby("year").size().rename("days").reset_index()
    )
    st.altair_chart(
        alt.Chart(low).mark_bar(cornerRadiusEnd=4, color=SERIES[0]).encode(
            x=alt.X("year:O", title="Year"), y=alt.Y("days:Q", title="Days"),
            tooltip=["year:O", "days:Q"],
        ).properties(height=220),
        width="stretch",
    )
    show_table(
        anomalies[anomalies["anomaly_class"] == "unusually_low"]
        .sort_values("z_score")[["date_local", "ghi_kwh_m2", "clim_mean_kwh_m2", "z_score"]]
        .head(50).round(2),
        "Lowest days (table)",
    )

# --- robustness ------------------------------------------------------------------------

with tab_robust:
    st.subheader("How much do the weights matter?")
    st.caption(
        "5,000 random weight sets centered on the baseline. Bar = 5–95 % range of the rank "
        "each city gets; dot = median rank."
    )
    try:
        sens = table("gold/srs_sensitivity")
        sens = sens.merge(score[["location_name", "region"]], on="location_name")
        y = alt.Y("location_name:N", sort=alt.SortField("baseline_rank"), title=None,
                  axis=NAME_AXIS)
        rank_x = alt.X("rank_p05:Q", title="Rank (1 = best)",
                       scale=alt.Scale(domain=[0.5, len(score) + 0.5]),
                       axis=alt.Axis(values=list(range(1, len(score) + 1)), format="d"))
        rule = alt.Chart(sens).mark_rule(strokeWidth=6, opacity=0.35).encode(
            x=rank_x, x2="rank_p95:Q", y=y,
            color=alt.Color("region:N", scale=region_scale, legend=None),
        )
        dot = alt.Chart(sens).mark_circle(size=110, opacity=1).encode(
            x="median_rank:Q", y=y,
            color=alt.Color("region:N", scale=region_scale, title="Region"),
            tooltip=["location_name:N", "baseline_rank:Q", "median_rank:Q",
                     alt.Tooltip("p_rank_1:Q", format=".0%", title="P(rank 1)"),
                     "rank_p05:Q", "rank_p95:Q"],
        )
        st.altair_chart(
            alt.layer(rule, dot).resolve_legend(color="independent").properties(height=340),
            width="stretch",
        )
        show_table(sens.round(3))
    except Exception:  # noqa: BLE001
        st.info("Run `python -m src.scoring.sensitivity` to produce this view.")

    st.subheader("Does ERA5 agree with independent sources?")
    st.caption("Mean daily GHI from SolarPulse (ERA5) against two satellite-based sources.")
    try:
        val = table("gold/srs_validation")
        long = val.melt(
            id_vars=["location_name", "srs_rank"],
            value_vars=["era5_ghi_kwh_m2_day", "nasa_power_ghi_kwh_m2_day",
                        "global_solar_atlas_ghi_kwh_m2_day"],
            var_name="source", value_name="ghi",
        )
        sources = {"era5_ghi_kwh_m2_day": "SolarPulse (ERA5)",
                   "nasa_power_ghi_kwh_m2_day": "NASA POWER",
                   "global_solar_atlas_ghi_kwh_m2_day": "Global Solar Atlas"}
        long["source"] = long["source"].map(sources)
        chart = alt.Chart(long).mark_point(size=90, filled=True, opacity=1).encode(
            x=alt.X("ghi:Q", title="kWh/m² per day", scale=alt.Scale(zero=False)),
            y=alt.Y("location_name:N", sort=alt.SortField("srs_rank"), title=None,
                    axis=NAME_AXIS),
            color=alt.Color("source:N", title="Source",
                            scale=alt.Scale(domain=list(sources.values()), range=SERIES)),
            shape=alt.Shape("source:N", scale=alt.Scale(domain=list(sources.values()),
                                                        range=["circle", "square", "diamond"])),
            tooltip=["location_name:N", "source:N", alt.Tooltip("ghi:Q", format=".2f")],
        )
        st.altair_chart(chart.properties(height=340), width="stretch")
        report = PROJECT_ROOT / "reports" / "srs_validation.json"
        if report.exists():
            summary = json.loads(report.read_text())["summary"]
            c1, c2 = st.columns(2)
            for col, name in zip((c1, c2), ("NASA POWER", "Global Solar Atlas"), strict=True):
                s = summary[name]
                col.metric(f"Rank agreement with {name}", f"{s['spearman_vs_era5']:.2f}",
                           f"MAE {s['mean_abs_error_kwh_m2_day']:.2f} kWh/m²/day",
                           delta_color="off", help="Spearman correlation of the city ranking")
        show_table(val.round(3))
    except Exception:  # noqa: BLE001
        st.info("Run `python -m src.scoring.validation` to produce this view.")

# --- forecast --------------------------------------------------------------------------

with tab_ml:
    st.subheader("Next-day solar energy forecast")
    report = PROJECT_ROOT / "reports" / "ml_forecast.json"
    try:
        preds = table("gold/ghi_forecast_test")
    except Exception:  # noqa: BLE001
        preds = None
    if preds is None or not report.exists():
        st.info("Run `python -m src.ml.forecast` to train the model.")
    else:
        r = json.loads(report.read_text())
        c1, c2, c3 = st.columns(3)
        c1.metric("Model MAE (test)", f"{r['test']['mae']:.3f} kWh/m²")
        c2.metric("Climatology MAE", f"{r['baselines']['climatology_test']['mae']:.3f} kWh/m²")
        c3.metric("Skill vs climatology", f"{r['test']['skill_vs_climatology']:+.1%}",
                  help="1 − MSE(model) / MSE(climatology)")
        loc = st.selectbox("Location ", list(names), format_func=names.get, key="ml_loc")
        p = preds[preds["location_id"] == loc].sort_values("date_local").tail(120)
        long = p.melt(id_vars=["date_local"], value_vars=["ghi_kwh_m2", "forecast_kwh_m2"],
                      var_name="series", value_name="kwh")
        long["series"] = long["series"].map({"ghi_kwh_m2": "Observed (ERA5)",
                                             "forecast_kwh_m2": "Forecast"})
        nearest = alt.selection_point(fields=["date_local"], nearest=True, on="pointerover",
                                      empty=False)
        lines = alt.Chart(long).mark_line(strokeWidth=2).encode(
            x=alt.X("date_local:T", title=None),
            y=alt.Y("kwh:Q", title="kWh/m² per day"),
            color=alt.Color("series:N", title=None,
                            scale=alt.Scale(domain=["Observed (ERA5)", "Forecast"],
                                            range=SERIES[:2])),
        )
        rule = alt.Chart(long).mark_rule(color=MUTED).encode(
            x="date_local:T",
            opacity=alt.condition(nearest, alt.value(0.6), alt.value(0)),
            tooltip=[alt.Tooltip("date_local:T", title="Date"),
                     alt.Tooltip("kwh:Q", format=".2f")],
        ).add_params(nearest)
        st.altair_chart((lines + rule).properties(height=320), width="stretch")
        st.caption("Last 120 days of the test period. Trained on 2011–2023, never on these days.")
        show_table(pd.DataFrame(r["per_location_test"]).round(3), "Test metrics by location")

# --- pipeline health ---------------------------------------------------------------------

with tab_ops:
    st.subheader("Latest run of each step")
    try:
        runs = query(
            "select * from read_json_auto('{lake}/observability/runs/*/*.json', "
            "union_by_name = true, hive_partitioning = true)"
        )
        runs["started_at"] = pd.to_datetime(runs["started_at"])
        latest = runs.sort_values("started_at").groupby("step").tail(1)
        failed = int((latest["status"] != "success").sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Steps tracked", f"{latest['step'].nunique()}")
        c2.metric("Failing now", "✓ none" if failed == 0 else f"✗ {failed}",
                  help="Steps whose latest run did not succeed")
        c3.metric("Runs recorded", f"{len(runs):,}")
        cols = ["step", "status", "started_at", "duration_s", "peak_memory_mb", "run_id"]
        st.dataframe(
            latest[cols].assign(status=latest["status"].map(
                {"success": "✓ success", "failed": "✗ failed"})).sort_values("step"),
            hide_index=True, width="stretch",
        )
        st.subheader("Duration per run")
        st.altair_chart(
            alt.Chart(runs).mark_circle(size=60, opacity=0.8, color=SERIES[0]).encode(
                x=alt.X("started_at:T", title=None),
                y=alt.Y("step:N", title=None),
                size=alt.Size("duration_s:Q", title="Seconds"),
                tooltip=["step:N", "status:N", "started_at:T", "duration_s:Q",
                         "peak_memory_mb:Q"],
            ).properties(height=320),
            width="stretch",
        )
    except Exception as exc:  # noqa: BLE001
        st.info(f"No run metrics yet. ({exc})")

    quality = PROJECT_ROOT / "reports" / "data_quality_report.md"
    if quality.exists():
        with st.expander("Latest data quality report"):
            st.markdown(quality.read_text())
