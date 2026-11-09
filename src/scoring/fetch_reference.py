"""Downloads independent long-term GHI references for every location.

Sources (both are independent of ERA5, which SolarPulse uses):
- NASA POWER climatology API: CERES/SYN1deg satellite data, 2001-2025, ~1° grid.
  Official, documented open API.
- Global Solar Atlas (World Bank / Solargis): satellite model, ~250 m. Read from the
  public endpoint behind globalsolaratlas.info; it is not a documented API and may
  change. Data is CC BY 4.0, attribution: "Global Solar Atlas 2.0, Solargis".

The result is saved to config/external_reference.csv and committed, so validation is
reproducible and CI never calls these services.

Usage: python -m src.scoring.fetch_reference
"""

from __future__ import annotations

import csv
import time
from datetime import date

import requests

from src.common.config import CONFIG_DIR, load_locations
from src.common.logging import get_logger

log = get_logger("fetch_reference")

OUTPUT = CONFIG_DIR / "external_reference.csv"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
GSA_URL = "https://api.globalsolaratlas.info/data/lta"
HEADERS = {"User-Agent": "solarpulse-data-pipeline/0.1 (portfolio project)"}


def nasa_power_ghi(lat: float, lon: float) -> float:
    resp = requests.get(
        NASA_URL,
        params={
            "parameters": "ALLSKY_SFC_SW_DWN",
            "community": "RE",
            "latitude": lat,
            "longitude": lon,
            "format": "JSON",
        },
        headers=HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    return float(resp.json()["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]["ANN"])


def global_solar_atlas_ghi(lat: float, lon: float) -> float:
    resp = requests.get(GSA_URL, params={"loc": f"{lat},{lon}"}, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    annual_kwh_m2 = float(resp.json()["annual"]["data"]["GHI"])
    return annual_kwh_m2 / 365.25  # kWh/m² per year -> per day


def main() -> int:
    rows = []
    for loc in load_locations():
        nasa = nasa_power_ghi(loc.latitude, loc.longitude)
        time.sleep(1)
        gsa = global_solar_atlas_ghi(loc.latitude, loc.longitude)
        time.sleep(1)
        rows.append(
            {
                "location_id": loc.location_id,
                "nasa_power_ghi_kwh_m2_day": round(nasa, 3),
                "global_solar_atlas_ghi_kwh_m2_day": round(gsa, 3),
            }
        )
        log.info("reference_fetched", extra=rows[-1])

    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# Long-term mean daily GHI. Retrieved {date.today().isoformat()}.\n")
        fh.write("# nasa_power: NASA POWER climatology 2001-2025 (CERES SYN1deg).\n")
        fh.write("# global_solar_atlas: Global Solar Atlas 2.0, Solargis (CC BY 4.0).\n")
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    log.info("reference_saved", extra={"path": str(OUTPUT), "locations": len(rows)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
