"""Read-only access to the published lake for the dashboard.

The dashboard never opens the DuckDB warehouse (which allows one writer at a time and
is busy during Airflow runs). It reads the Parquet that the pipeline publishes.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env = PROJECT_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


@st.cache_resource
def connection() -> tuple[duckdb.DuckDBPyConnection, str]:
    _load_dotenv()
    con = duckdb.connect()
    if os.getenv("SOLARPULSE_STORAGE_BACKEND", "s3") == "local":
        return con, str(PROJECT_ROOT / "data" / "lake")
    con.execute("INSTALL httpfs; LOAD httpfs;")

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    con.execute(
        "CREATE SECRET lake (TYPE s3, URL_STYLE 'path', USE_SSL false, REGION 'us-east-1', "
        f"KEY_ID {literal(os.getenv('S3_ACCESS_KEY', 'solarpulse'))}, "
        f"SECRET {literal(os.getenv('S3_SECRET_KEY', 'solarpulse-secret'))}, "
        f"ENDPOINT {literal(os.getenv('S3_ENDPOINT', 'localhost:9000'))})"
    )
    return con, "s3://solarpulse"


@st.cache_data(ttl=300)
def query(sql: str) -> pd.DataFrame:
    """`{lake}` in the SQL is replaced by the lake root."""
    con, root = connection()
    return con.execute(sql.replace("{lake}", root)).df()


def table(path: str, where: str = "") -> pd.DataFrame:
    return query(f"select * from read_parquet('{{lake}}/{path}/**/*.parquet', "
                 f"hive_partitioning = true) {where}")
