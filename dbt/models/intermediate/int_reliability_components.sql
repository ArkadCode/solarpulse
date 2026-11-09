{{ config(materialized='table') }}

-- Raw (not yet normalized) SRS components per location and period.
-- period = 'all' for the full history, or a calendar year with enough complete days.
with days as (
    select * from {{ ref('daily_solar_metrics') }}
    where is_complete_day
),

scorable_years as (
    select location_id, year
    from days
    group by location_id, year
    having count(*) >= {{ var('min_complete_days_per_year') }}
),

periods as (
    select 'all' as period, * from days
    union all
    select cast(d.year as varchar) as period, d.*
    from days as d
    semi join scorable_years as y on d.location_id = y.location_id and d.year = y.year
),

monthly_climatology as (
    select period, location_id, month, avg(ghi_kwh_m2) as mean_daily_ghi_kwh_m2
    from periods
    group by period, location_id, month
),

seasonality as (
    -- How much the typical day changes across the 12 calendar months.
    select
        period,
        location_id,
        count(*) as months_covered,
        stddev_pop(mean_daily_ghi_kwh_m2) / nullif(avg(mean_daily_ghi_kwh_m2), 0) as seasonality_cv
    from monthly_climatology
    group by period, location_id
)

select
    p.period,
    p.location_id,
    count(*) as complete_days,
    min(p.date_local) as first_date,
    max(p.date_local) as last_date,

    avg(p.ghi_kwh_m2) as mean_daily_ghi_kwh_m2,
    stddev_samp(p.ghi_kwh_m2) / nullif(avg(p.ghi_kwh_m2), 0) as daily_cv,
    avg(p.ramp_volatility) as ramp_volatility,
    avg(case when p.ghi_kwh_m2 < {{ var('low_radiation_day_kwh_m2') }} then 1.0 else 0.0 end)
        as low_radiation_share,
    avg(p.heat_stress_c) as heat_stress_c,
    avg(p.diffuse_ratio) as mean_diffuse_ratio,
    avg(p.temp_max_c) as mean_temp_max_c,
    any_value(s.seasonality_cv) as seasonality_cv,
    any_value(s.months_covered) as months_covered
from periods as p
join seasonality as s on p.period = s.period and p.location_id = s.location_id
group by p.period, p.location_id
