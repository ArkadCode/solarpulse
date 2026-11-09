-- Fails when the SRS weights in dbt_project.yml do not add up to 1.
{%- set w = var('srs_weights') %}
select {{ w.values() | sum }} as weight_sum
where abs({{ w.values() | sum }} - 1) > 1e-9
