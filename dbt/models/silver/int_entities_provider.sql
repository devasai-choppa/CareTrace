select
    npi as entity_id,
    payer_id,
    provider_name,
    score as match_probability,
    'directory_trust' as method
from {{ source('bronze', 'directory_trust') }}
