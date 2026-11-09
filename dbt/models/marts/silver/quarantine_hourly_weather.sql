{{
    config(
        materialized='incremental',
        incremental_strategy='append',
        pre_hook="{{ delete_reprocessed_partitions() }}",
    )
}}

-- Rows that broke at least one quality rule. They are kept, not deleted, so they can be
-- inspected and the rules tuned. failed_rules lists every rule the row broke.
select
    location_id,
    timestamp_utc,
    cast(timestamp_local as date) as date_local,
    failed_rules,

    ghi_wm2,
    dhi_wm2,
    dni_wm2,
    direct_horizontal_wm2,
    temperature_c,
    irradiance_unit,
    temperature_unit,

    bronze_partition as _bronze_partition,
    bronze_source_file as _bronze_source_file,
    bronze_extracted_at as _bronze_extracted_at,
    '{{ run_started_at }}'::timestamptz as _loaded_at
from ({{ checked_hourly_rows(partitions_to_process()) }})
where not is_valid
