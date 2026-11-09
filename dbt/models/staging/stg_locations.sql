select
    location_id,
    name as location_name,
    department,
    region,
    cast(latitude as double) as latitude,
    cast(longitude as double) as longitude,
    cast(elevation_m as double) as elevation_m
from {{ source('bronze', 'ref_locations') }}
