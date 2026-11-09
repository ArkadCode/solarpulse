{{ config(materialized='table') }}

-- Monthly distribution of daily solar energy per location. Statistics use complete
-- days only; partial days (edges of the history) are counted but not averaged.
with days as (
    select * from {{ ref('daily_solar_metrics') }}
),

complete as (
    select * from days where is_complete_day
)

select
    d.location_id,
    d.year,
    d.month,
    count(*) as days_observed,
    count(*) filter (where d.is_complete_day) as complete_days,

    avg(c.ghi_kwh_m2) as mean_daily_ghi_kwh_m2,
    stddev_samp(c.ghi_kwh_m2) as std_daily_ghi_kwh_m2,
    stddev_samp(c.ghi_kwh_m2) / nullif(avg(c.ghi_kwh_m2), 0) as cv_daily_ghi,
    min(c.ghi_kwh_m2) as min_daily_ghi_kwh_m2,
    quantile_cont(c.ghi_kwh_m2, 0.10) as p10_daily_ghi_kwh_m2,
    quantile_cont(c.ghi_kwh_m2, 0.50) as p50_daily_ghi_kwh_m2,
    quantile_cont(c.ghi_kwh_m2, 0.90) as p90_daily_ghi_kwh_m2,
    max(c.ghi_kwh_m2) as max_daily_ghi_kwh_m2,
    sum(c.ghi_kwh_m2) as total_ghi_kwh_m2,

    avg(c.diffuse_ratio) as mean_diffuse_ratio,
    avg(c.sunshine_hours) as mean_sunshine_hours,
    avg(c.ramp_volatility) as mean_ramp_volatility,
    count(c.location_id) filter (
        where c.ghi_kwh_m2 < {{ var('low_radiation_day_kwh_m2') }}
    ) as low_radiation_days,
    count(c.location_id) filter (where c.ghi_kwh_m2 < {{ var('low_radiation_day_kwh_m2') }})
        / nullif(count(c.location_id), 0) as low_radiation_share,

    avg(c.temp_max_c) as mean_temp_max_c,
    avg(c.daylight_temp_mean_c) as mean_daylight_temp_c,
    avg(c.heat_stress_c) as mean_heat_stress_c
from days as d
left join complete as c
    on d.location_id = c.location_id and d.date_local = c.date_local
group by d.location_id, d.year, d.month
