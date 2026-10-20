select
    person_id,
    payer_source_value as payer_id,
    plan_source_value as plan_name,
    payer_plan_period_start_date,
    payer_plan_period_end_date
from {{ ref('omop_payer_plan_period') }}
qualify row_number() over (
    partition by person_id
    order by payer_plan_period_end_date desc
) = 1
