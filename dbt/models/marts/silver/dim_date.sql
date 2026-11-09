{{ config(materialized='table') }}

-- Calendar covering every local date in the fact table.
-- Seasons follow Colombia's bimodal Andean pattern (two dry and two rainy seasons).
-- It is a simplification: parts of the Caribbean coast have a single long rainy season.
with bounds as (
    select min(date_local) as first_date, max(date_local) as last_date
    from {{ ref('fact_hourly_weather') }}
),

dates as (
    select cast(d as date) as date_day
    from bounds, generate_series(first_date, last_date, interval 1 day) as t(d)
)

select
    cast(strftime(date_day, '%Y%m%d') as integer) as date_key,
    date_day as date,
    year(date_day) as year,
    quarter(date_day) as quarter,
    month(date_day) as month,
    strftime(date_day, '%B') as month_name,
    dayofyear(date_day) as day_of_year,
    weekofyear(date_day) as iso_week,
    isodow(date_day) as iso_day_of_week,
    isodow(date_day) >= 6 as is_weekend,
    case
        when month(date_day) in (12, 1, 2) then 'dry_dec_feb'
        when month(date_day) in (3, 4, 5) then 'rainy_mar_may'
        when month(date_day) in (6, 7, 8) then 'dry_jun_aug'
        else 'rainy_sep_nov'
    end as season,
    case when month(date_day) in (12, 1, 2, 6, 7, 8) then 'dry' else 'rainy' end as season_type
from dates
