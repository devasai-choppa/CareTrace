select
    payer_name_raw,
    payer_id as entity_id,
    resolve_method as method,
    resolve_score as match_probability
from {{ ref('int_charges_resolved') }}
where payer_name_raw is not null
group by 1, 2, 3, 4
