with charges as (
    select * from {{ ref('stg_standard_charges') }}
),
resolved as (
    select
        payer_name_raw,
        case
            when method in ('alias', 'fuzzy', 'confirmed', 'fellegi_sunter') then payer_id
        end as payer_id,
        method as resolve_method,
        score as resolve_score
    from {{ source('bronze', 'payer_resolution') }}
),
shoppable as (
    select code, code_type, plain_label
    from {{ ref('shoppable_services') }}
),
hcpcs as (
    select code, description as hcpcs_description
    from {{ ref('hcpcs_descriptions') }}
),
drg as (
    select code, description as ms_drg_description
    from {{ ref('ms_drg_descriptions') }}
)
select
    c.*,
    r.payer_id,
    r.resolve_method,
    r.resolve_score,
    s.plain_label,
    coalesce(s.code is not null, false) as is_shoppable,
    h.hcpcs_description,
    d.ms_drg_description
from charges c
left join resolved r on c.payer_name_raw = r.payer_name_raw
left join shoppable s on c.code = s.code and c.code_type = s.code_type
left join hcpcs h on c.code = h.code and c.code_type = 'HCPCS'
left join drg d on c.code = d.code and c.code_type = 'MS-DRG'
