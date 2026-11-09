{#-
  Partition-level incremental loading for models fed by Bronze.

  A Bronze partition is one (location, year) file. When ingestion rewrites a partition
  (new download or backfill) its extracted_at moves forward. On each run we find the
  partitions newer than what the model already holds, delete their rows (pre-hook) and
  insert them again. Running twice gives the same result, and a backfill never duplicates.
-#}

{% macro partitions_to_process() %}
    {#- Returns none to mean "every partition" (first run or full refresh). -#}
    {% if not execute or not is_incremental() %}
        {{ return(none) }}
    {% endif %}
    {% set watermark = run_query("select max(_bronze_extracted_at) from " ~ this).columns[0].values()[0] %}
    {% if watermark is none %}
        {{ return(none) }}
    {% endif %}
    {% set query %}
        select distinct bronze_partition
        from {{ ref('stg_openmeteo__hourly') }}
        where bronze_extracted_at > '{{ watermark }}'::timestamptz
        order by 1
    {% endset %}
    {{ return(run_query(query).columns[0].values() | list) }}
{% endmacro %}


{% macro partition_filter(partitions, column='bronze_partition') %}
    {%- if partitions is none -%}
        true
    {%- elif partitions | length == 0 -%}
        false
    {%- else -%}
        {{ column }} in ({% for p in partitions %}'{{ p }}'{{ "," if not loop.last }}{% endfor %})
    {%- endif -%}
{% endmacro %}


{% macro delete_reprocessed_partitions() %}
    {% if is_incremental() %}
        delete from {{ this }}
        where {{ partition_filter(partitions_to_process(), '_bronze_partition') }}
    {% else %}
        select 1
    {% endif %}
{% endmacro %}
