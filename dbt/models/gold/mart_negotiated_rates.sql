with scored as (
    select
        *,
        case
            when negotiated_dollar is not null then 'dollar'
            when negotiated_percentage is not null then 'percent'
            when coalesce(negotiated_algorithm, '') <> '' then 'algorithm'
            else 'missing'
        end as rate_kind,
        greatest(0.0, least(1.0,
            1.0
            - case when negotiated_dollar is null then 0.35 else 0.0 end
            - case
                when negotiated_dollar is null and negotiated_percentage is not null
                     and coalesce(negotiated_algorithm, '') = '' then 0.15 else 0.0
              end
            - case
                when negotiated_dollar is null and coalesce(negotiated_algorithm, '') <> '' then 0.25 else 0.0
              end
            - case when outlier_vs_peer then 0.25 else 0.0 end
        )) as quality_score
    from {{ ref('int_charges_quality') }}
    where payer_id is not null
),
ranked as (
    select
        *,
        row_number() over (
            partition by source_id, code, setting, payer_id, coalesce(plan_name_raw, '')
            order by quality_score desc, ingested_at desc
        ) as rn
    from scored
)
select
    hospital_name,
    source_id,
    file_id,
    code,
    code_type,
    setting,
    payer_id,
    payer_name_raw,
    plan_name_raw as plan_name,
    negotiated_dollar as rate_dollar,
    rate_kind,
    discounted_cash,
    gross_charge,
    last_updated_on,
    source_row_hash,
    parser_version,
    ingested_at,
    quality_score
from ranked
where rn = 1
