"""Silver -> Gold, data quality report and publishing to the lake.

Runs whenever Silver is updated: dbt builds the Gold models and the SRS, Great
Expectations validates Silver and Gold, the SRS is checked (weight sensitivity and
external validation), and the layers are published as Parquet.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG
from solarpulse_common import (
    DEFAULT_ARGS,
    GOLD,
    PROJECT_DIR,
    SILVER,
    WAREHOUSE_POOL,
    module_command,
)

with DAG(
    dag_id="gold_analytics",
    description="dbt Gold models, Great Expectations report, publish to the lake",
    schedule=[SILVER],
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["solarpulse", "gold", "dbt", "quality"],
) as dag:
    build_gold = BashOperator(
        task_id="dbt_build_gold",
        bash_command=module_command(
            "src.transform.dbt_runner",
            "build",
            "--select", "path:models/intermediate", "path:models/marts/gold",
            "--indirect-selection", "cautious",
        ),
        cwd=PROJECT_DIR,
        pool=WAREHOUSE_POOL,
    )

    quality_report = BashOperator(
        task_id="great_expectations_report",
        bash_command=module_command("src.quality.run_quality"),
        cwd=PROJECT_DIR,
        pool=WAREHOUSE_POOL,
    )

    publish = BashOperator(
        task_id="publish_to_lake",
        bash_command=module_command("src.lake.publish"),
        cwd=PROJECT_DIR,
        pool=WAREHOUSE_POOL,
        outlets=[GOLD],
    )

    sensitivity = BashOperator(
        task_id="srs_sensitivity",
        bash_command=module_command("src.scoring.sensitivity"),
        cwd=PROJECT_DIR,
        pool=WAREHOUSE_POOL,
    )

    validation = BashOperator(
        task_id="srs_external_validation",
        bash_command=module_command("src.scoring.validation"),
        cwd=PROJECT_DIR,
        pool=WAREHOUSE_POOL,
    )

    build_gold >> quality_report >> [sensitivity, validation] >> publish
