{{ config(materialized='table') }}

-- Flags days with unusually low (or high) solar energy for their location and season.
-- Climatology: mean and standard deviation of daily GHI energy over every year, using
-- a ±7-day window around the same day of the year (circular, so late December and
-- early January are neighbors). Leap day 366 is folded into 365.
{%- set window = 7 %}
with days as (
    select
        location_id,
        date_local,
        year,
        month,
        least(dayofyear(date_local), 365) as doy,
        ghi_kwh_m2
    from {{ ref('daily_solar_metrics') }}
    where is_complete_day
),

doys as (
    select distinct location_id, doy from days
),

climatology as (
    select
        d.location_id,
        d.doy,
        avg(s.ghi_kwh_m2) as clim_mean_kwh_m2,
        stddev_samp(s.ghi_kwh_m2) as clim_std_kwh_m2,
        count(*) as clim_days
    from doys as d
    join days as s
        on s.location_id = d.location_id
        and least(abs(s.doy - d.doy), 365 - abs(s.doy - d.doy)) <= {{ window }}
    group by d.location_id, d.doy
)

select
    d.location_id,
    d.date_local,
    d.year,
    d.month,
    d.ghi_kwh_m2,
    c.clim_mean_kwh_m2,
    c.clim_std_kwh_m2,
    (d.ghi_kwh_m2 - c.clim_mean_kwh_m2) / nullif(c.clim_std_kwh_m2, 0) as z_score,
    d.ghi_kwh_m2 / nullif(c.clim_mean_kwh_m2, 0) - 1 as pct_anomaly,
    case
        when (d.ghi_kwh_m2 - c.clim_mean_kwh_m2) / nullif(c.clim_std_kwh_m2, 0) <= -2
            then 'unusually_low'
        when (d.ghi_kwh_m2 - c.clim_mean_kwh_m2) / nullif(c.clim_std_kwh_m2, 0) >= 2
            then 'unusually_high'
        else 'normal'
    end as anomaly_class
from days as d
join climatology as c on d.location_id = c.location_id and d.doy = c.doy
