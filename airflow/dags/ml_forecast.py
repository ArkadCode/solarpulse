"""Retrains the next-day solar energy forecast whenever Gold is published.

Every run is tracked in MLflow (data/mlflow) and registers a new model version; the
test-period predictions are written to the lake for the dashboard.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG
from solarpulse_common import DEFAULT_ARGS, GOLD, PROJECT_DIR, WAREHOUSE_POOL, module_command

with DAG(
    dag_id="ml_forecast",
    description="Train and evaluate the next-day GHI model, tracked in MLflow",
    schedule=[GOLD],
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["solarpulse", "ml", "mlflow"],
) as dag:
    BashOperator(
        task_id="train_and_evaluate",
        bash_command=module_command("src.ml.forecast"),
        cwd=PROJECT_DIR,
        pool=WAREHOUSE_POOL,  # reads the warehouse, so it waits for writers to finish
    )
