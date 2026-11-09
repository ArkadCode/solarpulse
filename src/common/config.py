"""Configuration loading from config/*.yml, with environment variable overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


@dataclass(frozen=True)
class Location:
    location_id: str
    name: str
    department: str
    region: str
    latitude: float
    longitude: float
    elevation_m: float


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load .env without overriding variables already set in the environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_settings(path: Path | None = None) -> dict[str, Any]:
    _load_dotenv()
    settings = _read_yaml(path or CONFIG_DIR / "settings.yml")
    storage = settings.setdefault("storage", {})
    # Credentials and backend selection never live in the YAML file.
    storage["backend"] = os.getenv("SOLARPULSE_STORAGE_BACKEND", storage.get("backend", "local"))
    storage["s3_endpoint"] = os.getenv("S3_ENDPOINT", storage.get("s3_endpoint"))
    storage["access_key"] = os.getenv("S3_ACCESS_KEY", "solarpulse")
    storage["secret_key"] = os.getenv("S3_SECRET_KEY", "solarpulse-secret")
    return settings


def load_locations(path: Path | None = None, only: list[str] | None = None) -> list[Location]:
    raw = _read_yaml(path or CONFIG_DIR / "locations.yml")["locations"]
    locations = [Location(**item) for item in raw]
    if only:
        known = {loc.location_id for loc in locations}
        unknown = set(only) - known
        if unknown:
            raise ValueError(f"Unknown locations: {sorted(unknown)}")
        locations = [loc for loc in locations if loc.location_id in only]
    return locations
