# Airflow plus an isolated virtualenv for the SolarPulse pipeline.
# The pipeline (dbt, Great Expectations, DuckDB) lives in its own venv so its
# dependencies never conflict with Airflow's. DAGs call it through SOLARPULSE_PYTHON.
FROM apache/airflow:3.3.2-python3.12

COPY requirements.txt /tmp/solarpulse-requirements.txt
RUN python -m venv /home/airflow/solarpulse-venv \
    && /home/airflow/solarpulse-venv/bin/pip install --no-cache-dir -q --upgrade pip \
    && /home/airflow/solarpulse-venv/bin/pip install --no-cache-dir -q -r /tmp/solarpulse-requirements.txt \
    && /home/airflow/solarpulse-venv/bin/python -c "import duckdb; duckdb.sql('INSTALL httpfs')"

ENV SOLARPULSE_PYTHON=/home/airflow/solarpulse-venv/bin/python
