"""The Airflow DAGs import cleanly and are wired as designed.

Skipped when Airflow is not installed (it lives in its own CI job and Docker image).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# "airflow.sdk", not "airflow": the project's airflow/ folder is importable as a namespace.
pytest.importorskip("airflow.sdk")

DAGS_DIR = Path(__file__).resolve().parents[1] / "airflow" / "dags"


@pytest.fixture(scope="module")
def dagbag():
    try:
        from airflow.dag_processing.dagbag import DagBag
    except ImportError:  # older layout
        from airflow.models.dagbag import DagBag
    sys.path.insert(0, str(DAGS_DIR))
    return DagBag(dag_folder=str(DAGS_DIR))


def test_no_import_errors(dagbag):
    assert dagbag.import_errors == {}


def test_expected_dags(dagbag):
    assert set(dagbag.dag_ids) == {"weather_ingestion", "silver_transformation", "gold_analytics"}


def test_gold_task_order(dagbag):
    dag = dagbag.get_dag("gold_analytics")
    assert dag.get_task("dbt_build_gold").downstream_task_ids == {"great_expectations_report"}
    assert dag.get_task("great_expectations_report").downstream_task_ids == {"publish_to_lake"}


def test_warehouse_tasks_share_single_writer_pool(dagbag):
    for dag_id in ("silver_transformation", "gold_analytics"):
        for task in dagbag.get_dag(dag_id).tasks:
            assert task.pool == "duckdb_warehouse", (dag_id, task.task_id)


def test_tasks_retry_and_alert(dagbag):
    for dag in dagbag.dags.values():
        for task in dag.tasks:
            assert task.retries >= 2
            assert task.on_failure_callback
