select
    payer_id,
    npi,
    provider_name,
    score as directory_confidence,
    ghost_risk,
    reasons
from {{ source('bronze', 'directory_trust') }}
