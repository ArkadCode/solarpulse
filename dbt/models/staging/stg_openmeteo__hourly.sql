-- One row per Bronze hourly record, renamed and typed. No rows are dropped here:
-- the quality rules run downstream so invalid rows can be quarantined, not lost.
select
    location_id,
    cast("year" as integer) as partition_year,
    location_id || '/' || cast("year" as varchar) as bronze_partition,
    try_strptime("time", '%Y-%m-%dT%H:%M') as timestamp_utc,

    cast(temperature_2m as double) as temperature_c,
    cast(shortwave_radiation as double) as ghi_wm2,
    cast(direct_radiation as double) as direct_horizontal_wm2,
    cast(diffuse_radiation as double) as dhi_wm2,
    cast(direct_normal_irradiance as double) as dni_wm2,

    json_extract_string(hourly_units, '$.shortwave_radiation') as irradiance_unit,
    json_extract_string(hourly_units, '$.temperature_2m') as temperature_unit,
    api_model as weather_model,
    api_latitude as grid_latitude,
    api_longitude as grid_longitude,
    api_elevation as grid_elevation_m,

    extracted_at as bronze_extracted_at,
    source_file as bronze_source_file
from {{ source('bronze', 'raw_openmeteo') }}
