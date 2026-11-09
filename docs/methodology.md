# Methodology

How SolarPulse turns hourly irradiance into daily indicators and the Solar Reliability
Score (SRS). The SRS is a project indicator for comparing solar *conditions* between
locations. It is not an official metric and not an estimate of electricity production,
which would require modeling panels, orientation, inverters and losses.

## Source data

- Open-Meteo Historical Weather API, ERA5 reanalysis (~25 km grid), hourly, UTC.
- Irradiance values are W/m², averaged over the hour **before** the timestamp.
- Variables: GHI (`shortwave_radiation`), DHI (`diffuse_radiation`), direct horizontal
  (`direct_radiation`), DNI (`direct_normal_irradiance`), 2 m temperature.
- Reanalysis values are model reconstructions, not station measurements. A grid cell
  smooths local effects such as valleys, coastlines and urban haze.

## Hourly quality rules (Silver)

Each hourly row is checked before it enters `silver.fact_hourly_weather`. Rows that fail
go to `silver.quarantine_hourly_weather` with the list of rules they broke.

| Rule | Condition |
| --- | --- |
| `missing_required` | timestamp or any measured variable is null |
| `unexpected_unit` | irradiance unit is not W/m² or temperature unit is not °C |
| `ghi_out_of_range` | GHI < 0 or > 1400 W/m² |
| `dhi_out_of_range` | DHI < 0 or DHI > GHI + 1 W/m² |
| `direct_out_of_range` | direct horizontal < 0 or > GHI + 1 W/m² |
| `dni_out_of_range` | DNI < 0 or > 1400 W/m² |
| `temperature_out_of_range` | temperature < −20 °C or > 50 °C |
| `duplicate_key` | another row has the same (location, hour); the latest download wins |

1400 W/m² sits just above the solar constant (~1361 W/m²), which surface irradiance
cannot exceed for long. The 1 W/m² tolerance absorbs rounding in the API values.

## Daily indicators (Gold)

Days use Colombian local time (UTC−5, no daylight saving time). Only days with all
24 hours (`is_complete_day`) feed the monthly statistics and the SRS.

| Indicator | Column | Definition |
| --- | --- | --- |
| Solar Energy Potential | `ghi_kwh_m2` | $E = \sum_{h=1}^{24} GHI_h / 1000$ (kWh/m²) |
| Diffuse Radiation Ratio | `diffuse_ratio` | $\sum DHI / \sum GHI$ over the day |
| Solar Volatility | `ramp_volatility` | mean $\lvert GHI_h - GHI_{h-1}\rvert$ in daylight ÷ mean daylight GHI |
| Temperature Stress Index | `heat_stress_c` | mean of $\max(T - 25, 0)$ over daylight hours (°C) |
| Sunshine hours | `sunshine_hours` | hours with DNI ≥ 120 W/m² (WMO definition) |

Daylight means GHI > 0. Because hourly values are averages over one hour, summing them
gives Wh/m² directly. `ramp_volatility` includes the normal sunrise-to-sunset curve, so
it is only meaningful when comparing locations, not as an absolute value.

## Solar Reliability Score

For each location and period (`all`, or a calendar year with at least 360 complete days)
five raw components are computed from complete days:

| Component | Raw measure | Better when |
| --- | --- | --- |
| Mean radiation | mean daily GHI energy (kWh/m²) | higher |
| Variability | average of normalized day-to-day CV and normalized ramp volatility | lower |
| Seasonality | CV of the 12 monthly mean daily energies | lower |
| Low-radiation frequency | share of days with GHI energy < 3 kWh/m² | lower |
| Temperature | mean Temperature Stress Index | lower |

CV is the coefficient of variation, standard deviation ÷ mean.

Each component is min-max normalized across locations within the same period, and
flipped when lower is better, so 1 is always the best location and 0 the worst:

$$x_i = \frac{v_i - \min(v)}{\max(v) - \min(v)} \quad\text{or}\quad 1 - \frac{v_i - \min(v)}{\max(v) - \min(v)}$$

When every location has the same value, the component is neutral (0.5). The score is:

$$SRS = 100 \times \sum_i w_i \, x_i, \qquad \sum_i w_i = 1$$

| Component | Initial weight |
| --- | --- |
| Mean radiation | 0.35 |
| Variability | 0.25 |
| Seasonality | 0.15 |
| Low-radiation frequency | 0.15 |
| Temperature | 0.10 |

The weights live in `dbt/dbt_project.yml` (`srs_weights`), and a dbt test fails the
build if they do not sum to 1. They are a starting hypothesis, not a scientific truth.

## Known limitations

- **Relative scores.** Min-max normalization ranks locations against each other. Adding
  or removing a location changes every score, and a gap of a few points may not mean
  much. Compare ranks and components, not absolute scores across runs.
- **Seasons.** `dim_date.season` uses the Andean bimodal pattern. Parts of the Caribbean
  coast have a single long rainy season.
- **Grid resolution.** ERA5 cells are ~25 km wide, so a city value represents its
  surroundings, not a specific rooftop.
- **Low-radiation threshold.** 3 kWh/m²/day is a fixed project choice
  (`low_radiation_day_kwh_m2`). A different threshold changes that component.

## Planned checks (phase 5)

- **Sensitivity analysis:** vary the weights and measure how much the ranking changes.
- **External validation:** compare mean daily GHI with the IDEAM/UPME Solar Radiation
  Atlas of Colombia or Global Solar Atlas.
