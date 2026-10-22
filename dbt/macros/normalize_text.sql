{% macro normalize_text(column_name) %}
lower(trim(cast({{ column_name }} as varchar)))
{% endmacro %}
