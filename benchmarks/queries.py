"""Representative queries, written once in SQL that both DuckDB and Spark SQL accept.

They mirror the Gold layer: the table `hourly` has the columns of
silver.fact_hourly_weather.
"""

from __future__ import annotations

DAILY = """
    select location_id, date_local, year, month,
           sum(ghi_wm2) / 1000 as ghi_kwh_m2,
           max(temperature_c) as temp_max_c
    from hourly
    group by location_id, date_local, year, month
"""

QUERIES = {
    # Gold monthly_solar_metrics: hourly -> daily -> monthly distribution.
    "monthly_aggregation": f"""
        with daily as ({DAILY})
        select location_id, year, month,
               avg(ghi_kwh_m2) as mean_kwh,
               stddev_samp(ghi_kwh_m2) as std_kwh,
               percentile_cont(0.9) within group (order by ghi_kwh_m2) as p90_kwh,
               count(*) as days
        from daily
        group by location_id, year, month
    """,
    # Gold location_reliability_score: components per location, then a ranking.
    "srs_ranking": f"""
        with daily as ({DAILY}),
        monthly as (
            select location_id, month, avg(ghi_kwh_m2) as m from daily group by location_id, month
        ),
        seasonality as (
            select location_id, stddev_pop(m) / avg(m) as seasonality_cv
            from monthly group by location_id
        ),
        components as (
            select location_id,
                   avg(ghi_kwh_m2) as mean_kwh,
                   stddev_samp(ghi_kwh_m2) / avg(ghi_kwh_m2) as daily_cv,
                   avg(case when ghi_kwh_m2 < 3 then 1.0 else 0.0 end) as low_share
            from daily group by location_id
        )
        select c.location_id, c.mean_kwh, c.daily_cv, c.low_share, s.seasonality_cv,
               rank() over (order by c.mean_kwh desc) as radiation_rank
        from components c join seasonality s on c.location_id = s.location_id
        order by radiation_rank
        limit 50
    """,
    # Raw scan + filter + group by: the hourly solar profile of the dry season.
    "hourly_profile_scan": """
        select month, hour_local,
               avg(ghi_wm2) as mean_ghi, max(ghi_wm2) as max_ghi, count(*) as hours
        from hourly
        where ghi_wm2 > 0 and month in (12, 1, 2)
        group by month, hour_local
    """,
}
