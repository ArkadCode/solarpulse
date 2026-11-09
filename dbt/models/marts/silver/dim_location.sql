{{ config(materialized='table') }}

select
    location_id,
    location_name,
    department,
    region,
    latitude,
    longitude,
    elevation_m,
    {{ var('local_utc_offset_hours') }} as utc_offset_hours
from {{ ref('stg_locations') }}
