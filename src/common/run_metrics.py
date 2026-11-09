"""Per-run observability: duration, peak memory, status and step-specific counts.

Each pipeline step wraps its work in `tracked_run`. On exit (success or failure) one
JSON record is logged and stored in the lake under observability/runs/step=<step>/.
On a laptop the limited resource is the machine, not money, so memory and time are
what gets measured.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from src.common.config import load_settings
from src.common.logging import get_logger
from src.common.storage import Lake

log = get_logger("run_metrics")


def peak_memory_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)


def current_run_id() -> str:
    # Inside Airflow, tie the record to the DAG run; otherwise make one up.
    return os.getenv("AIRFLOW_CTX_DAG_RUN_ID") or f"manual__{uuid.uuid4().hex[:12]}"


@contextmanager
def tracked_run(step: str, settings: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """Yields a dict the step fills with its own metrics (rows written, etc.)."""
    metrics: dict[str, Any] = {}
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    status = "success"
    try:
        yield metrics
        if metrics.pop("_failed", False):
            status = "failed"
    except BaseException:
        status = "failed"
        raise
    finally:
        record = {
            "step": step,
            "run_id": current_run_id(),
            "status": status,
            "started_at": started.isoformat(timespec="seconds"),
            "duration_s": round(time.monotonic() - t0, 2),
            "peak_memory_mb": peak_memory_mb(),
            **metrics,
        }
        log.info("run_metrics", extra=record)
        _store(record, settings)


def _store(record: dict[str, Any], settings: dict[str, Any] | None) -> None:
    try:
        lake = Lake.from_settings(settings or load_settings())
        stamp = record["started_at"].replace(":", "").replace("-", "").replace("+0000", "Z")
        lake.write_text(
            f"observability/runs/step={record['step']}/{stamp}_{record['run_id']}.json",
            json.dumps(record, default=str),
        )
    except Exception as exc:  # metrics must never break the pipeline
        log.warning("run_metrics_not_stored", extra={"error": str(exc)})
