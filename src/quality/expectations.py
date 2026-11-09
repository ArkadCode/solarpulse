"""Great Expectations suites for Silver and Gold.

dbt tests guard the build (a failure stops the pipeline). These suites add a second,
independent check and produce a readable data quality report for every run.
"""

from __future__ import annotations

import great_expectations as gx
import great_expectations.expectations as gxe

MAX_IRRADIANCE_WM2 = 1400


def fact_hourly_weather() -> list:
    irradiance = ["ghi_wm2", "dhi_wm2", "dni_wm2", "direct_horizontal_wm2"]
    return [
        gxe.ExpectTableRowCountToBeBetween(min_value=1),
        gxe.ExpectCompoundColumnsToBeUnique(column_list=["location_id", "timestamp_utc"]),
        *[
            gxe.ExpectColumnValuesToNotBeNull(column=c)
            for c in ["location_id", "timestamp_utc", *irradiance, "temperature_c"]
        ],
        *[
            gxe.ExpectColumnValuesToBeBetween(column=c, min_value=0, max_value=MAX_IRRADIANCE_WM2)
            for c in irradiance
        ],
        gxe.ExpectColumnValuesToBeBetween(column="temperature_c", min_value=-20, max_value=50),
        gxe.ExpectColumnPairValuesAToBeGreaterThanB(
            column_A="ghi_wm2", column_B="dhi_wm2", or_equal=True, mostly=0.999
        ),
        gxe.ExpectColumnValuesToBeBetween(column="hour_local", min_value=0, max_value=23),
    ]


def daily_solar_metrics() -> list:
    return [
        gxe.ExpectTableRowCountToBeBetween(min_value=1),
        gxe.ExpectCompoundColumnsToBeUnique(column_list=["location_id", "date_local"]),
        gxe.ExpectColumnValuesToBeBetween(column="hours_observed", min_value=1, max_value=24),
        # Only the first and last day of each location may be partial.
        gxe.ExpectColumnMeanToBeBetween(column="is_complete_day", min_value=0.99, max_value=1),
        gxe.ExpectColumnValuesToBeBetween(column="ghi_kwh_m2", min_value=0, max_value=12),
        gxe.ExpectColumnValuesToBeBetween(
            column="diffuse_ratio", min_value=0, max_value=1.01, mostly=0.999
        ),
        gxe.ExpectColumnValuesToBeBetween(column="sunshine_hours", min_value=0, max_value=24),
    ]


def location_reliability_score() -> list:
    components = [
        "x_mean_radiation",
        "x_variability",
        "x_seasonality",
        "x_low_radiation",
        "x_temperature",
    ]
    return [
        gxe.ExpectTableRowCountToBeBetween(min_value=1),
        gxe.ExpectCompoundColumnsToBeUnique(column_list=["period", "location_id"]),
        gxe.ExpectColumnValuesToNotBeNull(column="srs"),
        gxe.ExpectColumnValuesToBeBetween(column="srs", min_value=0, max_value=100),
        *[
            gxe.ExpectColumnValuesToBeBetween(column=c, min_value=0, max_value=1)
            for c in components
        ],
    ]


SUITES = {
    "silver.fact_hourly_weather": fact_hourly_weather,
    "gold.daily_solar_metrics": daily_solar_metrics,
    "gold.location_reliability_score": location_reliability_score,
}


def build_suite(name: str, expectations: list) -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name=name)
    for expectation in expectations:
        suite.add_expectation(expectation)
    return suite
