{#-
  Min-max normalization to [0, 1] across locations within the same period.
  higher_is_better=false flips the scale so 1 is always the best value.
  When every location has the same value the component is neutral (0.5).
-#}
{% macro minmax(column, higher_is_better=true, partition_by='period') %}
    {%- set scaled -%}
        ({{ column }} - min({{ column }}) over (partition by {{ partition_by }}))
        / nullif(
            max({{ column }}) over (partition by {{ partition_by }})
            - min({{ column }}) over (partition by {{ partition_by }}),
            0
        )
    {%- endset -%}
    coalesce({{ scaled if higher_is_better else '1 - ' ~ scaled }}, 0.5)
{% endmacro %}
