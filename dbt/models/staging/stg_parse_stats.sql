select
    file_id,
    source_id,
    format,
    rows,
    quarantined,
    quarantine_pct,
    distinct_payers,
    parsed_at
from {{ source('bronze', 'parse_stats') }}
