{{
    config(
        materialized='incremental',
        incremental_strategy='append',
        pre_hook="{{ delete_reprocessed_partitions() }}",
    )
}}

-- Valid hourly rows. Key: (location_id, timestamp_utc).
-- Loaded per Bronze partition: see macros/incremental_partitions.sql.
select
    location_id,
    timestamp_utc,
    timestamp_local,
    cast(timestamp_local as date) as date_local,
    year(timestamp_local) as year,
    month(timestamp_local) as month,
    hour(timestamp_local) as hour_local,

    ghi_wm2,
    dhi_wm2,
    dni_wm2,
    direct_horizontal_wm2,
    temperature_c,
    ghi_wm2 > 0 as is_daylight,

    weather_model,
    grid_latitude,
    grid_longitude,
    grid_elevation_m,

    -- lineage back to the Bronze file
    bronze_partition as _bronze_partition,
    bronze_source_file as _bronze_source_file,
    bronze_extracted_at as _bronze_extracted_at,
    '{{ run_started_at }}'::timestamptz as _loaded_at
from ({{ checked_hourly_rows(partitions_to_process()) }})
where is_valid
