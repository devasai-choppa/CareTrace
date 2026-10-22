{% test gross_gte_discounted_cash(model) %}
select *
from {{ model }}
where gross_charge is not null
  and discounted_cash is not null
  and gross_charge < discounted_cash
{% endtest %}
