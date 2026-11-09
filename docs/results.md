# Results

Full history 2011–2026, 10 Colombian cities, 1,388,400 valid hourly rows, 0 quarantined.
How each number is computed is in [methodology.md](methodology.md).

## Solar Reliability Score

| SRS rank | Location | Region | SRS | Mean daily GHI (kWh/m²) | Radiation rank |
| --- | --- | --- | --- | --- | --- |
| 1 | Barranquilla | Caribbean | 80.0 | 5.69 | 1 |
| 2 | Riohacha (La Guajira) | Caribbean | 79.9 | 5.58 | 2 |
| 3 | Santa Marta | Caribbean | 73.7 | 5.33 | 4 |
| 4 | Cartagena | Caribbean | 70.2 | 5.48 | 3 |
| 5 | Cúcuta | Andean | 66.4 | 5.20 | 6 |
| 6 | Medellín | Andean | 52.6 | 5.25 | 5 |
| 7 | Bogotá | Andean | 46.8 | 4.61 | 8 |
| 8 | Villavicencio | Orinoquia | 33.8 | 4.95 | 7 |
| 9 | Bucaramanga | Andean | 33.6 | 4.53 | 9 |
| 10 | Cali | Andean | 23.2 | 4.28 | 10 |

**Is the city with the most radiation also the most stable?** At the top, yes:
Barranquilla leads both rankings, virtually tied with La Guajira (fewer low-radiation
days, less day-to-day variability). Further down, stability reorders the list: Santa
Marta overtakes Cartagena thanks to lower variability and seasonality, Cúcuta overtakes
Medellín (the least seasonal city of the ten), and Bogotá overtakes Villavicencio, whose
rainy season produces the second-highest share of low-radiation days.

## How far the score can be trusted

- **Weights:** across 5,000 random weight sets the ranking keeps a mean Spearman
  correlation of 0.98 with the baseline, and no city moves more than 2 places when any
  weight shifts by ±10 points. First place is a toss-up: Barranquilla 54 %, La Guajira 46 %.
- **Data source:** the ERA5 ranking agrees with Global Solar Atlas (Spearman 0.82, mean
  absolute error 5 %) about as well as NASA POWER and Global Solar Atlas agree with each
  other (0.83).

## Next-day forecast

| Test period (2024–2026) | MAE (kWh/m²) | RMSE | Skill vs climatology |
| --- | --- | --- | --- |
| Persistence (tomorrow = today) | 0.817 | 1.099 | −15 % |
| Climatology | 0.801 | 1.024 | 0 % |
| Gradient boosting | **0.696** | **0.905** | **+22 %** |

## Scale: national grid and benchmark

The grid over mainland Colombia has 94 points at 1°, 370 at 0.5° and 1,488 at 0.25°.
`--max-partitions N` caps each run so a large grid loads in daily batches within the
Open-Meteo quota.

| Grid | Points | Partitions (16 years) | Approx. API calls* | Days at 10,000 calls/day |
| --- | --- | --- | --- | --- |
| 1° | 94 | 1,504 | ~39,000 | ~4 |
| 0.25° | 1,488 | 23,808 | ~620,000 | ~62 |

\*Open-Meteo counts a request for more than 2 weeks of data as several calls; one year
for one location is about 26.

Because the real 0.25° grid would take about two months of free quota, the benchmark
uses synthetic data at real volume with the Silver schema. Each engine runs the same SQL
in a fresh process; peak memory covers the whole process tree, including Spark's JVM.

| Grid points | Rows | Parquet | Engine | Startup (s) | Monthly agg. (s) | SRS ranking (s) | Hourly scan (s) | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 1.3 M | 0.02 GB | DuckDB | 0.0 | 0.01 | 0.01 | 0.01 | 149 MB |
| 10 | 1.3 M | 0.02 GB | PySpark | 6.1 | 0.45 | 0.49 | 0.18 | 1,016 MB |
| 100 | 13.1 M | 0.24 GB | DuckDB | 0.0 | 0.09 | 0.09 | 0.07 | 303 MB |
| 100 | 13.1 M | 0.24 GB | PySpark | 5.9 | 2.36 | 1.51 | 0.48 | 1,811 MB |
| 500 | 65.7 M | 1.22 GB | DuckDB | 0.0 | 0.72 | 0.60 | 0.32 | 678 MB |
| 500 | 65.7 M | 1.22 GB | PySpark | 6.2 | 7.07 | 5.15 | 1.20 | 2,798 MB |
| 1,000 | 131.5 M | 2.44 GB | DuckDB | 0.0 | 1.63 | 1.66 | 0.72 | 1,181 MB |
| 1,000 | 131.5 M | 2.44 GB | PySpark | 8.8 | 16.06 | 7.19 | 2.52 | 3,570 MB |

Apple M-series, 10 CPUs, 16 GB RAM; median of 3 runs. PySpark 4.2 in `local[*]` mode
with 20 shuffle partitions; DuckDB with default settings.

**When does one machine stop being enough?** Not within this project. DuckDB is 3.5–45×
faster and uses a third of the memory or less at every size, and its memory grows far
more slowly than the data. The full 0.25° grid (~196 M rows, ~3.6 GB of Parquet) is only
1.5× the largest size tested. Spark earns its overhead when data no longer fits one
machine's disk, when it already lives in a cluster, or when many jobs must run in
parallel, none of which applies here.
