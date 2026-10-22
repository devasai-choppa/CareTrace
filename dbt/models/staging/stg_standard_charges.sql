with src as (
    select * from {{ source('bronze', 'standard_charges') }}
),
cleaned as (
    select
        {{ normalize_text('source_id') }} as source_id,
        file_id,
        hospital_name,
        last_updated_on,
        version,
        description,
        upper(trim(code)) as code,
        upper(trim(code_type)) as code_type,
        lower(trim(setting)) as setting,
        billing_class,
        {{ money('gross_charge') }} as gross_charge,
        {{ money('discounted_cash') }} as discounted_cash,
        payer_name_raw,
        plan_name_raw,
        {{ money('negotiated_dollar') }} as negotiated_dollar,
        {{ money('negotiated_percentage') }} as negotiated_percentage,
        negotiated_algorithm,
        {{ money('estimated_amount') }} as estimated_amount,
        methodology,
        {{ money('min_negotiated') }} as min_negotiated,
        {{ money('max_negotiated') }} as max_negotiated,
        additional_notes,
        source_row_hash,
        parser_version,
        ingested_at
    from src
),
dedup as (
    select
        *,
        row_number() over (partition by source_row_hash order by ingested_at desc) as rn
    from cleaned
)
select * exclude (rn)
from dedup
where rn = 1
