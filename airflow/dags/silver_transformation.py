"""Bronze -> Silver with dbt. Runs whenever the ingestion DAG updates Bronze.

dbt only reprocesses the Bronze partitions that changed since the last run, and its
tests stop the run before bad data reaches Gold.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG
from solarpulse_common import (
    BRONZE,
    DEFAULT_ARGS,
    PROJECT_DIR,
    SILVER,
    WAREHOUSE_POOL,
    module_command,
)

with DAG(
    dag_id="silver_transformation",
    description="dbt staging + Silver models and their tests",
    schedule=[BRONZE],
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["solarpulse", "silver", "dbt"],
) as dag:
    BashOperator(
        task_id="dbt_build_silver",
        bash_command=module_command(
            "src.transform.dbt_runner",
            "build",
            "--select", "path:models/staging", "path:models/marts/silver",
            # only run tests whose parents were all built in this selection
            "--indirect-selection", "cautious",
        ),
        cwd=PROJECT_DIR,
        pool=WAREHOUSE_POOL,
        outlets=[SILVER],
    )
