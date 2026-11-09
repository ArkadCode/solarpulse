{{ config(severity='error', warn_if='>0', error_if='>240') }}
-- Every day between a location's first and last day should have all 24 hours in Silver.
-- Only the two edge days can be partial (the history starts and ends at UTC midnight).
-- Quarantined hours make a day incomplete: that warns, and only fails the build when
-- the gaps are widespread (more than ~240 days), which points at an ingestion problem.
with bounds as (
    select location_id, min(date_local) as first_day, max(date_local) as last_day
    from {{ ref('daily_solar_metrics') }}
    group by location_id
)

select d.location_id, d.date_local, d.hours_observed
from {{ ref('daily_solar_metrics') }} as d
join bounds as b on d.location_id = b.location_id
where d.date_local > b.first_day
  and d.date_local < b.last_day
  and d.hours_observed != 24
