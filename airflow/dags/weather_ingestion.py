"""Daily incremental ingestion from Open-Meteo into Bronze.

Each run downloads only the missing or incomplete (location, year) partitions. Backfills
are a trigger parameter, so reprocessing a year never re-downloads the whole history:

    airflow dags trigger weather_ingestion --conf '{"backfill_years": [2015, 2016]}'
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param
from solarpulse_common import BRONZE, DEFAULT_ARGS, PROJECT_DIR, module_command

INGEST_COMMAND = module_command(
    "src.ingestion.incremental_loader",
    "{% if params.backfill_years %}--backfill {{ params.backfill_years | join(' ') }}{% endif %}",
    "{% if params.locations %}--locations {{ params.locations | join(' ') }}"
    "{% elif params.all_locations %}--all-locations{% endif %}",
)

with DAG(
    dag_id="weather_ingestion",
    description="Open-Meteo -> Bronze, incremental with backfills",
    schedule="@daily",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["solarpulse", "bronze"],
    params={
        # Strict schemas: values are rendered into a shell command.
        "backfill_years": Param(
            [], type="array", items={"type": "integer", "minimum": 1940, "maximum": 2100},
            description="Years to download again even if they already exist.",
        ),
        "locations": Param(
            [], type="array", items={"type": "string", "pattern": "^[a-z_]+$"},
            description="Location IDs to ingest. Empty = active_locations in settings.yml.",
        ),
        "all_locations": Param(False, type="boolean", description="Ingest every location."),
    },
) as dag:
    BashOperator(
        task_id="ingest_to_bronze",
        bash_command=INGEST_COMMAND,
        cwd=PROJECT_DIR,
        outlets=[BRONZE],
    )
