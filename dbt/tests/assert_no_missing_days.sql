-- No local date may be missing between a location's first and last day.
with bounds as (
    select location_id, min(date_local) as first_day, max(date_local) as last_day,
           count(*) as days
    from {{ ref('daily_solar_metrics') }}
    group by location_id
)

select *
from bounds
where days != date_diff('day', first_day, last_day) + 1
