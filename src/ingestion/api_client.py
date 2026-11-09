"""Client for the Open-Meteo Historical Weather API."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import requests

from src.common.logging import get_logger
from src.ingestion.retry_handler import RetryableError, retry_with_backoff

log = get_logger("api_client")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenMeteoClient:
    def __init__(self, api_settings: dict[str, Any], retry_settings: dict[str, Any]):
        self.base_url = api_settings["base_url"]
        self.model = api_settings["model"]
        self.timezone = api_settings.get("timezone", "GMT")
        self.hourly_variables = list(api_settings["hourly_variables"])
        self.timeout = api_settings.get("timeout_seconds", 60)
        self.min_interval = api_settings.get("min_seconds_between_requests", 1.0)
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "solarpulse-data-pipeline/0.1"
        self._get = retry_with_backoff(
            max_attempts=retry_settings.get("max_attempts", 5),
            base_delay=retry_settings.get("base_delay_seconds", 2.0),
            max_delay=retry_settings.get("max_delay_seconds", 60.0),
        )(self._get_once)

    def build_params(
        self, latitude: float, longitude: float, start_date: date, end_date: date
    ) -> dict[str, Any]:
        return {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": ",".join(self.hourly_variables),
            "models": self.model,
            "timezone": self.timezone,
        }

    def fetch_hourly(
        self, latitude: float, longitude: float, start_date: date, end_date: date
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (JSON response, request parameters used)."""
        params = self.build_params(latitude, longitude, start_date, end_date)
        return self._get(params), params

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _get_once(self, params: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        try:
            resp = self.session.get(self.base_url, params=params, timeout=self.timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise RetryableError(f"network error: {exc}") from exc

        if resp.status_code in RETRYABLE_STATUS:
            retry_after = resp.headers.get("Retry-After")
            raise RetryableError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if resp.status_code != 200:
            # 400 = invalid parameters: retrying won't fix it.
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        body = resp.json()
        if body.get("error"):
            raise RuntimeError(f"API error: {body.get('reason')}")
        return body
