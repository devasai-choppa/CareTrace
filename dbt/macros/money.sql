{% macro money(column_name) %}
cast({{ column_name }} as double)
{% endmacro %}
