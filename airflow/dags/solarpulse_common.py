"""Shared settings for the SolarPulse DAGs.

The DAGs only orchestrate: every task runs a pipeline module from the project in its
own virtualenv (SOLARPULSE_PYTHON), so Airflow never imports dbt or pandas itself.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import timedelta

from airflow.sdk import Asset

PROJECT_DIR = os.getenv("SOLARPULSE_PROJECT_DIR", "/opt/solarpulse")
PYTHON = os.getenv("SOLARPULSE_PYTHON", "python")

# DuckDB allows one writer at a time: every task that touches the warehouse uses this
# single-slot pool, so Silver and Gold runs never collide.
WAREHOUSE_POOL = "duckdb_warehouse"

BRONZE = Asset("s3://solarpulse/bronze/raw_openmeteo")
SILVER = Asset("s3://solarpulse/silver")
GOLD = Asset("s3://solarpulse/gold")

log = logging.getLogger("solarpulse.alerts")


def notify_failure(context) -> None:
    """Failure alert: a structured log line, plus a webhook post when one is configured.

    Set ALERT_WEBHOOK_URL to a Slack (or compatible) incoming webhook to get messages.
    """
    ti = context["task_instance"]
    alert = {
        "event": "task_failed",
        "dag_id": ti.dag_id,
        "task_id": ti.task_id,
        "run_id": ti.run_id,
        "try_number": ti.try_number,
        "error": str(context.get("exception")),
    }
    log.error(json.dumps(alert))

    url = os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        return
    text = f"SolarPulse: {ti.dag_id}.{ti.task_id} failed (run {ti.run_id}): {alert['error']}"
    request = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except Exception as exc:  # an alert must never raise inside the callback
        log.warning("alert_webhook_failed: %s", exc)


DEFAULT_ARGS = {
    "owner": "solarpulse",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
    "execution_timeout": timedelta(hours=2),
    "on_failure_callback": notify_failure,
}


def module_command(module: str, *args: str) -> str:
    """Shell command that runs a project module with the pipeline virtualenv."""
    return " ".join([PYTHON, "-m", module, *args])
