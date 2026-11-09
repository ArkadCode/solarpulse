-- depends_on: {{ ref('quarantine_hourly_weather') }}
{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['location_id', 'date_local'],
    )
}}

-- Daily solar indicators per location (local calendar day, UTC-5).
-- Incremental: only days touched by rows loaded since the last run are recomputed,
-- always from every Silver row of that day, so partial updates never skew a day.
with
{% if is_incremental() %}
last_load as (
    select max(_loaded_at) as loaded_at from {{ this }}
),

affected_days as (
    select distinct location_id, date_local
    from {{ ref('fact_hourly_weather') }}
    where _loaded_at > (select loaded_at from last_load)
    union
    select distinct location_id, date_local
    from {{ ref('quarantine_hourly_weather') }}
    where _loaded_at > (select loaded_at from last_load)
),
{% endif %}

hourly as (
    select
        f.*,
        f.ghi_wm2 - lag(f.ghi_wm2) over (
            partition by f.location_id, f.date_local order by f.timestamp_utc
        ) as ghi_ramp_wm2
    from {{ ref('fact_hourly_weather') }} as f
    {% if is_incremental() %}
    semi join affected_days as a
        on f.location_id = a.location_id and f.date_local = a.date_local
    {% endif %}
)

select
    location_id,
    date_local,
    any_value(year) as year,
    any_value(month) as month,
    count(*) as hours_observed,
    count(*) = 24 as is_complete_day,

    -- Hourly W/m² averages summed over 1-hour steps give Wh/m²; /1000 gives kWh/m².
    sum(ghi_wm2) / 1000 as ghi_kwh_m2,
    sum(dhi_wm2) / 1000 as dhi_kwh_m2,
    sum(dni_wm2) / 1000 as dni_kwh_m2,
    sum(direct_horizontal_wm2) / 1000 as direct_horizontal_kwh_m2,
    sum(dhi_wm2) / nullif(sum(ghi_wm2), 0) as diffuse_ratio,
    max(ghi_wm2) as peak_ghi_wm2,
    count(*) filter (where dni_wm2 >= {{ var('sunshine_dni_threshold_wm2') }}) as sunshine_hours,
    count(*) filter (where is_daylight) as daylight_hours,

    -- Mean absolute hour-to-hour GHI change in daylight, relative to mean daylight GHI.
    -- Includes the normal daily curve, so compare it across locations, not as an absolute.
    avg(abs(ghi_ramp_wm2)) filter (where is_daylight)
        / nullif(avg(ghi_wm2) filter (where is_daylight), 0) as ramp_volatility,

    min(temperature_c) as temp_min_c,
    avg(temperature_c) as temp_mean_c,
    max(temperature_c) as temp_max_c,
    avg(temperature_c) filter (where is_daylight) as daylight_temp_mean_c,
    -- Mean °C above the heat-stress base during daylight (0 when never above it).
    avg(greatest(temperature_c - {{ var('heat_stress_base_c') }}, 0))
        filter (where is_daylight) as heat_stress_c,

    '{{ run_started_at }}'::timestamptz as _loaded_at
from hourly
group by location_id, date_local
