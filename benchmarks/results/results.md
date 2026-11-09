# Benchmark: PySpark local vs DuckDB

Machine: arm64, 10 CPUs, 16 GB RAM, Darwin. Median of 3 runs per query, in seconds; startup is separate.

| Locations | Rows | Parquet | Engine | Startup | monthly_aggregation | srs_ranking | hourly_profile_scan | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 1.3 M | 0.02 GB | duckdb | 0.0 | 0.01 | 0.01 | 0.01 | 149 MB |
| 10 | 1.3 M | 0.02 GB | spark | 6.1 | 0.45 | 0.49 | 0.18 | 1,016 MB |
| 100 | 13.1 M | 0.24 GB | duckdb | 0.0 | 0.09 | 0.09 | 0.07 | 303 MB |
| 100 | 13.1 M | 0.24 GB | spark | 5.9 | 2.36 | 1.51 | 0.48 | 1,811 MB |
| 500 | 65.7 M | 1.22 GB | duckdb | 0.0 | 0.72 | 0.60 | 0.32 | 678 MB |
| 500 | 65.7 M | 1.22 GB | spark | 6.2 | 7.07 | 5.15 | 1.20 | 2,798 MB |
| 1,000 | 131.5 M | 2.44 GB | duckdb | 0.0 | 1.63 | 1.66 | 0.72 | 1,181 MB |
| 1,000 | 131.5 M | 2.44 GB | spark | 8.8 | 16.06 | 7.19 | 2.52 | 3,570 MB |
