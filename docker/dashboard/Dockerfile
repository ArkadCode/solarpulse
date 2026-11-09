# Lightweight image for the Streamlit dashboard: it only reads Parquet from the lake.
FROM python:3.12-slim
WORKDIR /opt/solarpulse
COPY dashboard/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -q -r /tmp/requirements.txt \
    && python -c "import duckdb; duckdb.sql('INSTALL httpfs')"
EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
