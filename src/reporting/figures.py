"""Renders the README figures as SVG, in light and dark versions.

- docs/images/srs-ranking-{light,dark}.svg: SRS ranking bars next to a map of Colombia,
  built from gold.location_reliability_score.
- docs/images/architecture-{light,dark}.svg: the pipeline diagram.

Hand-written SVG keeps the files small, crisp on GitHub and free of plotting
dependencies. Colors are the validated palette also used by the dashboard.

Usage: python -m src.reporting.figures
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape

import duckdb

from src.common.config import PROJECT_ROOT, load_settings
from src.ingestion.grid import BBOX, COLOMBIA_OUTLINE
from src.transform.dbt_runner import warehouse_path

OUT_DIR = PROJECT_ROOT / "docs" / "images"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    surface_2: str
    border: str
    text: str
    text_2: str
    muted: str
    grid: str
    regions: dict[str, str]
    bronze: str = "#b07a45"
    silver: str = "#8e98a4"
    gold: str = "#c99a1e"


LIGHT = Theme(
    "light", "#fcfcfb", "#f0efec", "#d9d8d3", "#0b0b0b", "#52514e", "#8a8985", "#e6e5e1",
    {"Caribbean": "#2a78d6", "Andean": "#eb6834", "Orinoquia": "#1baf7a"},
)
DARK = Theme(
    "dark", "#1a1a19", "#262624", "#3d3d3a", "#f0efec", "#c3c2b7", "#8f8e88", "#33332f",
    {"Caribbean": "#3987e5", "Andean": "#d95926", "Orinoquia": "#199e70"},
)


def one_decimal(value: float) -> str:
    """Round half up (66.35 -> 66.4), matching the tables in the docs."""
    return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def text(x, y, s, t: Theme, size=13, color=None, weight=400, anchor="start", family=FONT):
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color or t.text}" text-anchor="{anchor}">'
        f"{escape(str(s))}</text>"
    )


def svg(width: int, height: int, body: list[str], t: Theme, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
        f"<title>{escape(title)}</title>"
        f'<rect width="{width}" height="{height}" rx="12" fill="{t.surface}"/>'
        + "".join(body)
        + "</svg>"
    )


# --- ranking figure -----------------------------------------------------------------

# Label placement on the map: (dx, dy, anchor). The Caribbean cities sit close together.
MAP_LABELS = {
    "barranquilla": (-14, 3, "end"),
    "la_guajira": (12, -9, "start"),
    "santa_marta": (-6, -15, "end"),
    "cartagena": (-14, 12, "end"),
    "cucuta": (11, 4, "start"),
    "medellin": (-11, 4, "end"),
    "bogota": (-11, 4, "end"),
    "villavicencio": (11, 8, "start"),
    "bucaramanga": (11, 6, "start"),
    "cali": (-11, 4, "end"),
}
SHORT_NAMES = {"la_guajira": "Riohacha"}


def load_ranking() -> tuple[list[dict], str]:
    con = duckdb.connect(str(warehouse_path(load_settings())), read_only=True)
    try:
        rows = con.execute(
            """
            select s.location_id, s.location_name, s.region, s.srs, s.srs_rank,
                   s.mean_radiation_rank, s.mean_daily_ghi_kwh_m2, l.latitude, l.longitude,
                   s.first_date, s.last_date
            from gold.location_reliability_score s
            join silver.dim_location l using (location_id)
            where s.period = 'all'
            order by s.srs_rank
            """
        ).df()
    finally:
        con.close()
    period = f"{rows['first_date'].min():%Y}–{rows['last_date'].max():%Y}"
    return rows.to_dict(orient="records"), period


def ranking_svg(rows: list[dict], period: str, t: Theme) -> str:
    width, height = 1000, 600
    body: list[str] = []
    body.append(text(32, 46, "Solar Reliability Score", t, 24, weight=700))
    body.append(text(
        32, 70,
        f"10 Colombian cities · hourly ERA5 reanalysis {period} · 0–100, higher is better",
        t, 13, t.text_2,
    ))
    # legend
    x = 32
    for region, color in t.regions.items():
        body.append(f'<rect x="{x}" y="88" width="12" height="12" rx="3" fill="{color}"/>')
        body.append(text(x + 18, 99, region, t, 12, t.text_2))
        x += 18 + len(region) * 7.2 + 22
    body.append(text(x + 6, 99, "▲▼ places vs ranking by radiation alone", t, 12, t.muted))

    # bars
    top, row_h, bar_x = 132, 44, 222
    bar_max = 300
    for i, r in enumerate(rows):
        y = top + i * row_h
        cy = y + row_h / 2
        body.append(text(32, cy + 5, f"{int(r['srs_rank'])}", t, 14, t.muted, 600,
                         family=MONO))
        name = SHORT_NAMES.get(r["location_id"], r["location_name"])
        body.append(text(56, cy + 1, name, t, 14, weight=600))
        body.append(text(56, cy + 16, f"{r['mean_daily_ghi_kwh_m2']:.2f} kWh/m²/day", t, 11,
                         t.muted))
        # track + bar with a rounded data end anchored at the baseline
        body.append(f'<rect x="{bar_x}" y="{cy - 7}" width="{bar_max}" height="14" rx="4" '
                    f'fill="{t.surface_2}"/>')
        w = bar_max * r["srs"] / 100
        body.append(f'<rect x="{bar_x}" y="{cy - 7}" width="{w:.1f}" height="14" rx="4" '
                    f'fill="{t.regions[r["region"]]}"/>')
        body.append(text(bar_x + bar_max + 12, cy + 5, one_decimal(r["srs"]), t, 14, weight=600))
        moved = int(r["mean_radiation_rank"]) - int(r["srs_rank"])
        if moved:
            arrow = f"▲ {moved}" if moved > 0 else f"▼ {-moved}"
            body.append(text(bar_x + bar_max + 58, cy + 5, arrow, t, 12, t.text_2, 600))

    # map
    mx, my, mw = 660, 110, 300
    lon0, lon1 = BBOX["min_lon"], BBOX["max_lon"]
    lat0, lat1 = BBOX["min_lat"], BBOX["max_lat"]
    scale = mw / (lon1 - lon0)

    def project(lon: float, lat: float) -> tuple[float, float]:
        return mx + (lon - lon0) * scale, my + (lat1 - lat) * scale

    outline = " ".join(f"{x:.1f},{y:.1f}" for x, y in (project(*p) for p in COLOMBIA_OUTLINE))
    body.append(f'<polygon points="{outline}" fill="{t.surface_2}" stroke="{t.border}" '
                f'stroke-width="1.5" stroke-linejoin="round"/>')
    body.append(text(mx + mw / 2 + 40, my + (lat1 - lat0) * scale - 6,
                     "Mainland Colombia (simplified outline)", t, 11, t.muted, anchor="middle"))
    for r in sorted(rows, key=lambda r: -r["srs_rank"]):  # best city drawn on top
        px, py = project(r["longitude"], r["latitude"])
        radius = 4 + 6 * r["srs"] / 100
        body.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{radius:.1f}" '
                    f'fill="{t.regions[r["region"]]}" stroke="{t.surface}" stroke-width="2"/>')
        dx, dy, anchor = MAP_LABELS.get(r["location_id"], (11, 4, "start"))
        name = SHORT_NAMES.get(r["location_id"], r["location_name"])
        body.append(text(px + dx, py + dy, f"{int(r['srs_rank'])} {name}", t, 11, t.text_2,
                         500, anchor))
    body.append(text(mx, height - 22, "Dot size grows with the score.", t, 11, t.muted))
    return svg(width, height, body, t, "Solar Reliability Score ranking of 10 Colombian cities")


# --- architecture figure ------------------------------------------------------------


def box(x, y, w, h, t: Theme, title, lines, accent=None, badge=None) -> list[str]:
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{t.surface_2}" '
             f'stroke="{t.border}" stroke-width="1.2"/>']
    if accent:
        parts.append(f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{accent}"/>')
    tx = x + (18 if accent else 14)
    parts.append(text(tx, y + 24, title, t, 14, weight=700))
    if badge:
        parts.append(f'<rect x="{x + w - 72}" y="{y + 10}" width="60" height="20" rx="10" '
                     f'fill="{badge[1]}"/>')
        parts.append(text(x + w - 42, y + 24, badge[0], t, 11, "#ffffff", 700, "middle"))
    for i, line in enumerate(lines):
        parts.append(text(tx, y + 46 + i * 18, line, t, 12, t.text_2))
    return parts


def arrow(x1, y1, x2, y2, t: Theme, dashed=False) -> str:
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    return (f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{t.muted}" stroke-width="2" '
            f'fill="none" marker-end="url(#arrow-{t.name})"{dash}/>')


def architecture_svg(t: Theme) -> str:
    width, height = 1200, 640
    body = [
        f'<defs><marker id="arrow-{t.name}" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{t.muted}"/></marker></defs>',
        text(32, 46, "SolarPulse architecture", t, 24, weight=700),
        text(32, 70, "Data flows left to right, from the API to Gold. Airflow runs every step; "
             "CI checks every change.", t, 13, t.text_2),
    ]

    # orchestration band
    body.append(f'<rect x="32" y="92" width="1136" height="72" rx="12" fill="none" '
                f'stroke="{t.border}" stroke-dasharray="6 5" stroke-width="1.5"/>')
    body.append(text(52, 118, "Apache Airflow 3 · asset-triggered DAGs", t, 14, weight=700))
    dags = ["weather_ingestion (@daily)", "silver_transformation", "gold_analytics",
            "ml_forecast"]
    x = 52
    for i, d in enumerate(dags):
        w = len(d) * 7.4 + 24
        body.append(f'<rect x="{x:.0f}" y="128" width="{w:.0f}" height="26" rx="13" '
                    f'fill="{t.surface_2}" stroke="{t.border}"/>')
        body.append(text(x + w / 2, 146, d, t, 12, t.text, 600, "middle", MONO))
        if i < len(dags) - 1:
            body.append(arrow(x + w + 4, 141, x + w + 30, 141, t))
        x += w + 36
    body.append(text(1148, 146, "retries · backfills · alerts", t, 12, t.muted, anchor="end"))

    # main flow
    y, h = 196, 196
    cols = [
        (32, 170, "Open-Meteo API", ["ERA5 hourly reanalysis", "GHI, DNI, DHI, temp.",
                                     "10 cities, 2011 → today"], None, None),
        (232, 170, "Ingestion", ["Python, requests", "incremental by year", "retries + quota cap",
                                 "backfill parameters"], None, None),
        (432, 170, "Bronze", ["raw Parquet on S3", "1 file per city-year", "+ request metadata",
                              "RustFS object store"], t.bronze, ("RAW", t.bronze)),
        (632, 190, "Silver", ["dbt on DuckDB", "typed hourly facts", "quarantine table",
                              "location + date dims"], t.silver, ("CLEAN", t.silver)),
        (852, 190, "Gold", ["daily + monthly metrics", "seasonal anomalies",
                            "Solar Reliability Score", "ranks per period"], t.gold,
         ("SCORE", t.gold)),
    ]
    for x, w, title, lines, accent, badge in cols:
        body += box(x, y, w, h, t, title, lines, accent, badge)
    for (x, w, *_), (nx, *_) in zip(cols, cols[1:], strict=False):
        body.append(arrow(x + w + 4, y + h / 2, nx - 6, y + h / 2, t))

    # serving column
    sx = 1068
    body.append(arrow(852 + 190 + 4, y + h / 2, sx - 6, y + h / 2, t))
    serve = [("Lake Parquet", "Silver + Gold"), ("Dashboard", "Streamlit"),
             ("ML forecast", "MLflow registry"), ("Benchmark", "PySpark vs DuckDB")]
    for i, (title, sub) in enumerate(serve):
        by = y + i * 50 - 2
        body.append(f'<rect x="{sx}" y="{by}" width="100" height="44" rx="8" '
                    f'fill="{t.surface_2}" stroke="{t.border}"/>')
        body.append(text(sx + 50, by + 19, title, t, 12, weight=700, anchor="middle"))
        body.append(text(sx + 50, by + 35, sub, t, 10, t.text_2, anchor="middle"))

    # quality row
    qy = 420
    body += box(632, qy, 410, 92, t, "Quality and scoring checks",
                ["44 dbt tests stop the build · Great Expectations report",
                 "SRS weight sensitivity · validation vs NASA POWER + Solar Atlas"])
    body.append(arrow(737, y + h + 4, 737, qy - 6, t, dashed=True))
    body.append(arrow(947, y + h + 4, 947, qy - 6, t, dashed=True))
    body += box(32, qy, 570, 92, t, "Observability and lineage",
                ["every step logs JSON + run metrics (rows, seconds, peak memory) to the lake",
                 "dbt lineage graph; each Silver row keeps its Bronze file and download time"])

    # CI band
    body.append(f'<rect x="32" y="540" width="1136" height="64" rx="12" fill="none" '
                f'stroke="{t.border}" stroke-dasharray="6 5" stroke-width="1.5"/>')
    body.append(text(52, 566, "GitHub Actions CI on every pull request", t, 14, weight=700))
    body.append(text(52, 588, "ruff · pytest on Python 3.10 and 3.12 · dbt build on synthetic "
                     "data · DAG import on Airflow 3.3.2 · Docker image build", t, 12, t.text_2))
    return svg(width, height, body, t, "SolarPulse architecture diagram")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, period = load_ranking()
    for theme in (LIGHT, DARK):
        (OUT_DIR / f"srs-ranking-{theme.name}.svg").write_text(
            ranking_svg(rows, period, theme), encoding="utf-8"
        )
        (OUT_DIR / f"architecture-{theme.name}.svg").write_text(
            architecture_svg(theme), encoding="utf-8"
        )
    print(f"Figures written to {OUT_DIR.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
