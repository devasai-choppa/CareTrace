select
    source_id,
    location_name,
    source_page_url,
    mrf_url,
    contact_name,
    contact_email,
    discovered_at
from {{ source('bronze', 'mrf_index') }}
