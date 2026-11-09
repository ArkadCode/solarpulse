"""Deterministic synthetic weather client with the same shape as the Open-Meteo API.

Used by tests and CI so the full pipeline can run without network access. Values are
physically plausible (a daylight curve, cloudy days, DHI <= GHI, direct + diffuse = GHI)
but they are not real weather.
"""

from __future__ import annotations

import math
import random
import zlib
from datetime import date, datetime, timedelta
from typing import Any

LOCAL_UTC_OFFSET_HOURS = -5  # Colombia, no daylight saving time


class SyntheticClient:
    def __init__(self, api_settings: dict[str, Any] | None = None, model: str = "synthetic"):
        api_settings = api_settings or {}
        self.hourly_variables = list(
            api_settings.get(
                "hourly_variables",
                [
                    "temperature_2m",
                    "shortwave_radiation",
                    "direct_radiation",
                    "diffuse_radiation",
                    "direct_normal_irradiance",
                ],
            )
        )
        self.model = model

    def fetch_hourly(
        self, latitude: float, longitude: float, start_date: date, end_date: date
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        start = datetime.combine(start_date, datetime.min.time())
        hours = ((end_date - start_date).days + 1) * 24
        # Higher latitude -> sunnier in this toy model, so locations rank differently.
        peak = 850 + 15 * latitude
        base_temp = 30 - 0.4 * abs(longitude + 74)

        series: dict[str, list] = {v: [] for v in ["time", *self.hourly_variables]}
        for h in range(hours):
            ts = start + timedelta(hours=h)
            local = ts + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)
            seed = zlib.crc32(f"{latitude:.4f}|{longitude:.4f}|{local.date()}".encode())
            day_rng = random.Random(seed)
            cloud = day_rng.uniform(0.35, 1.0)
            diffuse_share = 0.15 + 0.6 * (1 - cloud)

            solar_angle = math.sin(math.pi * (local.hour + 0.5 - 6) / 12)
            ghi = round(max(0.0, peak * solar_angle * cloud), 1)
            dhi = round(ghi * diffuse_share, 1)
            direct = round(ghi - dhi, 1)
            dni = round(min(1100.0, direct / max(solar_angle, 0.1)), 1) if ghi > 0 else 0.0
            temp = round(base_temp - 6 + 8 * max(solar_angle, 0) * cloud, 1)

            values = {
                "temperature_2m": temp,
                "shortwave_radiation": ghi,
                "direct_radiation": direct,
                "diffuse_radiation": dhi,
                "direct_normal_irradiance": dni,
            }
            series["time"].append(ts.strftime("%Y-%m-%dT%H:%M"))
            for var in self.hourly_variables:
                series[var].append(values[var])

        units = {"time": "iso8601", "temperature_2m": "°C"}
        units.update({v: "W/m²" for v in self.hourly_variables if v != "temperature_2m"})
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": ",".join(self.hourly_variables),
            "models": self.model,
            "timezone": "GMT",
        }
        response = {
            "latitude": latitude,
            "longitude": longitude,
            "elevation": 0.0,
            "timezone": "GMT",
            "hourly_units": units,
            "hourly": series,
        }
        return response, params
