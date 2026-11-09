# Data dictionary

Every table lives in two places: the DuckDB warehouse that dbt builds
(`data/warehouse/solarpulse.duckdb`), and the lake as Parquet (`s3://solarpulse/...`).
Column-level descriptions also appear in the dbt docs (`make docs`).

## Bronze (lake only, written by Python)

### `bronze/raw_openmeteo`

Partitions: `location_id=<id>/year=<yyyy>/data.parquet`. One row per API hour.

| Column | Type | Description |
| --- | --- | --- |
| `time` | string | Hour start in UTC, ISO 8601, exactly as returned |
| `temperature_2m` | double | °C |
| `shortwave_radiation` | double | GHI, W/m² |
| `direct_radiation` | double | Direct horizontal, W/m² |
| `diffuse_radiation` | double | DHI, W/m² |
| `direct_normal_irradiance` | double | DNI, W/m² |
| `api_latitude`, `api_longitude`, `api_elevation` | double | Grid cell the API actually used |
| `api_timezone` | string | Always GMT |
| `api_model` | string | Weather model (`era5`) |
| `hourly_units` | string | JSON with the unit of every variable |
| `request_params` | string | JSON with the exact request, to reproduce it |
| `requested_start_date`, `requested_end_date` | date | Requested range |
| `extracted_at` | timestamp | Download time; drives incremental loads |
| `source_file` | string | Full path of this file (lineage) |

### `bronze/ref_locations`

Configured locations from `config/locations.yml`: `location_id`, `name`, `department`,
`region`, `latitude`, `longitude`, `elevation_m`, `extracted_at`.

## Silver

### `silver.fact_hourly_weather`

Key: (`location_id`, `timestamp_utc`). Lake partitions: `year=/month=` (local time).

| Column | Description |
| --- | --- |
| `location_id` | FK to `dim_location` |
| `timestamp_utc` / `timestamp_local` | Hour start in UTC / Colombian time (UTC−5) |
| `date_local`, `year`, `month`, `hour_local` | Local calendar fields |
| `ghi_wm2`, `dhi_wm2`, `dni_wm2`, `direct_horizontal_wm2` | Irradiance, W/m² |
| `temperature_c` | °C at 2 m |
| `is_daylight` | GHI > 0 |
| `weather_model`, `grid_latitude`, `grid_longitude`, `grid_elevation_m` | Source grid cell |
| `_bronze_partition` | `location_id/year` of the Bronze file |
| `_bronze_source_file` | Bronze file path |
| `_bronze_extracted_at` | When that Bronze file was downloaded |
| `_loaded_at` | dbt run that loaded the row |

### `silver.quarantine_hourly_weather`

Same measures as the fact table plus `failed_rules` (list of rule names, see
[methodology.md](methodology.md#hourly-quality-rules-silver)) and the original units.

### `silver.dim_location`

`location_id` (PK), `location_name`, `department`, `region`, `latitude`, `longitude`,
`elevation_m`, `utc_offset_hours`.

### `silver.dim_date`

`date_key` (yyyymmdd, PK), `date`, `year`, `quarter`, `month`, `month_name`,
`day_of_year`, `iso_week`, `iso_day_of_week`, `is_weekend`, `season`
(`dry_dec_feb`, `rainy_mar_may`, `dry_jun_aug`, `rainy_sep_nov`), `season_type` (`dry`/`rainy`).

## Gold

### `gold.daily_solar_metrics`

Key: (`location_id`, `date_local`). Lake partitions: `year=`.

| Column | Description |
| --- | --- |
| `hours_observed`, `is_complete_day` | Valid hours in the day; true when 24 |
| `ghi_kwh_m2`, `dhi_kwh_m2`, `dni_kwh_m2`, `direct_horizontal_kwh_m2` | Daily energy, kWh/m² |
| `diffuse_ratio` | DHI / GHI |
| `peak_ghi_wm2` | Highest hourly GHI |
| `sunshine_hours` | Hours with DNI ≥ 120 W/m² |
| `daylight_hours` | Hours with GHI > 0 |
| `ramp_volatility` | Relative hour-to-hour GHI change in daylight |
| `temp_min_c`, `temp_mean_c`, `temp_max_c`, `daylight_temp_mean_c` | Temperature, °C |
| `heat_stress_c` | Mean °C above 25 °C in daylight |

### `gold.monthly_solar_metrics`

Key: (`location_id`, `year`, `month`). Statistics over complete days:
`mean_`, `std_`, `min_`, `p10_`, `p50_`, `p90_`, `max_daily_ghi_kwh_m2`, `cv_daily_ghi`,
`total_ghi_kwh_m2`, `mean_diffuse_ratio`, `mean_sunshine_hours`, `mean_ramp_volatility`,
`low_radiation_days`, `low_radiation_share`, `mean_temp_max_c`, `mean_daylight_temp_c`,
`mean_heat_stress_c`, plus `days_observed` and `complete_days`.

### `gold.location_reliability_score`

Key: (`period`, `location_id`). `period` is `all` or a year.

| Column | Description |
| --- | --- |
| `srs` | Solar Reliability Score, 0–100 |
| `srs_rank` | 1 = best SRS in the period |
| `mean_radiation_rank` | 1 = highest mean daily energy (compare with `srs_rank`) |
| `x_mean_radiation` … `x_temperature` | Normalized components, 0–1, 1 = best |
| `mean_daily_ghi_kwh_m2`, `daily_cv`, `ramp_volatility`, `seasonality_cv`, `low_radiation_share`, `heat_stress_c` | Raw components |
| `complete_days`, `first_date`, `last_date` | Data behind the score |
