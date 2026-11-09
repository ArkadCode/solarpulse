{#-
  Applies the hourly quality rules and returns every row with:
    failed_rules  list of rule names the row breaks (empty when valid)
    is_valid      true when failed_rules is empty
  fact_hourly_weather keeps the valid rows; quarantine_hourly_weather keeps the rest.
  The partition filter is applied before the duplicate check so only the partitions
  being reprocessed are read.
-#}
{% macro checked_hourly_rows(partitions) %}
    {%- set max_wm2 = var('max_irradiance_wm2') -%}
    {%- set tol = var('irradiance_tolerance_wm2') -%}
    with source_rows as (
        select *
        from {{ ref('stg_openmeteo__hourly') }}
        where {{ partition_filter(partitions) }}
    ),

    ranked as (
        select
            *,
            row_number() over (
                partition by location_id, timestamp_utc
                order by bronze_extracted_at desc
            ) as _duplicate_rank
        from source_rows
    ),

    checked as (
        select
            *,
            list_filter([
                case when timestamp_utc is null or ghi_wm2 is null or dhi_wm2 is null
                          or dni_wm2 is null or direct_horizontal_wm2 is null
                          or temperature_c is null
                     then 'missing_required' end,
                case when irradiance_unit != 'W/m²' or temperature_unit != '°C'
                     then 'unexpected_unit' end,
                case when ghi_wm2 < 0 or ghi_wm2 > {{ max_wm2 }}
                     then 'ghi_out_of_range' end,
                case when dhi_wm2 < 0 or dhi_wm2 > ghi_wm2 + {{ tol }}
                     then 'dhi_out_of_range' end,
                case when direct_horizontal_wm2 < 0 or direct_horizontal_wm2 > ghi_wm2 + {{ tol }}
                     then 'direct_out_of_range' end,
                case when dni_wm2 < 0 or dni_wm2 > {{ max_wm2 }}
                     then 'dni_out_of_range' end,
                case when temperature_c < {{ var('min_temperature_c') }}
                          or temperature_c > {{ var('max_temperature_c') }}
                     then 'temperature_out_of_range' end,
                case when _duplicate_rank > 1 then 'duplicate_key' end
            ], rule -> rule is not null) as failed_rules
        from ranked
    )

    select
        * exclude (_duplicate_rank),
        len(failed_rules) = 0 as is_valid,
        timestamp_utc + interval ({{ var('local_utc_offset_hours') }}) hour as timestamp_local
    from checked
{% endmacro %}
