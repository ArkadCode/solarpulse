"""Runs dbt with paths and credentials taken from config/settings.yml.

Keeps a single source of configuration: the same settings drive ingestion, dbt and
publishing, whether storage is the S3 object store or the local folder.

Usage:
    python -m src.transform.dbt_runner build
    python -m src.transform.dbt_runner docs generate
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from src.common.config import PROJECT_ROOT, load_settings

DBT_DIR = PROJECT_ROOT / "dbt"


def lake_root_uri(settings: dict[str, Any]) -> str:
    storage = settings["storage"]
    if storage["backend"] == "s3":
        return f"s3://{storage['bucket']}"
    root = Path(storage["local_root"])
    return str(root if root.is_absolute() else PROJECT_ROOT / root)


def warehouse_path(settings: dict[str, Any]) -> Path:
    path = Path(settings["warehouse"]["path"])
    return path if path.is_absolute() else PROJECT_ROOT / path


def dbt_env(settings: dict[str, Any]) -> dict[str, str]:
    storage = settings["storage"]
    return {
        "SOLARPULSE_STORAGE_BACKEND": storage["backend"],
        "SOLARPULSE_LAKE_ROOT": lake_root_uri(settings),
        "SOLARPULSE_WAREHOUSE": str(warehouse_path(settings)),
        "S3_ENDPOINT": storage["s3_endpoint"] or "",
        "S3_ACCESS_KEY": storage["access_key"],
        "S3_SECRET_KEY": storage["secret_key"],
        "S3_REGION": storage.get("s3_region", "us-east-1"),
        "S3_USE_SSL": "true" if storage.get("s3_scheme") == "https" else "false",
    }


def run_dbt(args: list[str], settings: dict[str, Any] | None = None) -> bool:
    from dbt.cli.main import dbtRunner

    settings = settings or load_settings()
    env = dbt_env(settings)
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    warehouse_path(settings).parent.mkdir(parents=True, exist_ok=True)
    try:
        full_args = [*args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
        result = dbtRunner().invoke(full_args)
        return bool(result.success)
    finally:
        _close_duckdb_environment()
        # Restore the caller's environment so runs with different settings don't leak.
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _close_duckdb_environment() -> None:
    """dbt-duckdb keeps the warehouse open in-process; release it so other code can open it."""
    from dbt.adapters.duckdb.connections import DuckDBConnectionManager

    with DuckDBConnectionManager._LOCK:
        if DuckDBConnectionManager._ENV is not None:
            DuckDBConnectionManager._ENV.close()
            DuckDBConnectionManager._ENV = None


def main() -> int:
    return 0 if run_dbt(sys.argv[1:]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
