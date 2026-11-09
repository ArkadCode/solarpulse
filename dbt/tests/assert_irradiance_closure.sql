{{ config(severity='warn') }}
-- Direct + diffuse horizontal radiation should add up to GHI (within rounding).
select location_id, timestamp_utc, ghi_wm2, direct_horizontal_wm2, dhi_wm2
from {{ ref('fact_hourly_weather') }}
where abs(direct_horizontal_wm2 + dhi_wm2 - ghi_wm2) > 2
