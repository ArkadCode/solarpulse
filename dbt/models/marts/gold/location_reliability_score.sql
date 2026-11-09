{{ config(materialized='table') }}

{%- set w = var('srs_weights') %}

-- Solar Reliability Score: 100 x sum(weight x normalized component).
-- Every component is min-max normalized across locations within the period, so 1 is
-- the best location and 0 the worst. Scores are relative: adding locations changes them.
with components as (
    select * from {{ ref('int_reliability_components') }}
),

normalized as (
    select
        *,
        {{ minmax('mean_daily_ghi_kwh_m2', higher_is_better=true) }} as x_mean_radiation,
        (
            {{ minmax('daily_cv', higher_is_better=false) }}
            + {{ minmax('ramp_volatility', higher_is_better=false) }}
        ) / 2 as x_variability,
        {{ minmax('seasonality_cv', higher_is_better=false) }} as x_seasonality,
        {{ minmax('low_radiation_share', higher_is_better=false) }} as x_low_radiation,
        {{ minmax('heat_stress_c', higher_is_better=false) }} as x_temperature
    from components
),

scored as (
    select
        *,
        100 * (
            {{ w.mean_radiation }} * x_mean_radiation
            + {{ w.variability }} * x_variability
            + {{ w.seasonality }} * x_seasonality
            + {{ w.low_radiation }} * x_low_radiation
            + {{ w.temperature }} * x_temperature
        ) as srs
    from normalized
)

select
    s.period,
    s.location_id,
    l.location_name,
    l.region,
    round(s.srs, 2) as srs,
    rank() over (partition by s.period order by s.srs desc) as srs_rank,
    rank() over (partition by s.period order by s.mean_daily_ghi_kwh_m2 desc) as mean_radiation_rank,
    count(*) over (partition by s.period) as locations_in_period,

    s.x_mean_radiation,
    s.x_variability,
    s.x_seasonality,
    s.x_low_radiation,
    s.x_temperature,

    s.mean_daily_ghi_kwh_m2,
    s.daily_cv,
    s.ramp_volatility,
    s.seasonality_cv,
    s.low_radiation_share,
    s.heat_stress_c,
    s.mean_diffuse_ratio,
    s.complete_days,
    s.first_date,
    s.last_date
from scored as s
left join {{ ref('dim_location') }} as l on s.location_id = l.location_id
