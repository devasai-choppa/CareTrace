select * from {{ source('bronze', 'payer_plan_period') }}
