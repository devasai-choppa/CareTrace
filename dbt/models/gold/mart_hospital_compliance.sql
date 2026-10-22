-- transparency_score =
--   0.40 * freshness (1 through day 365, then linear to 0 at day 730)
-- + 0.30 * share of negotiated rows with a dollar rate
-- + 0.20 * resolved payer coverage, capped at 1
-- + 0.10 * (1 - parse quarantine share)
with files as (
    select
        source_id,
        max(hospital_name) as hospital_name,
        max(last_updated_on) as last_updated_on,
        count(*) filter (
            where negotiated_dollar is not null
               or negotiated_percentage is not null
               or coalesce(negotiated_algorithm, '') <> ''
        ) as n_negotiated,
        count(*) filter (where negotiated_dollar is not null) as n_dollar,
        count(distinct payer_id) filter (where payer_id is not null) as n_payers
    from {{ ref('int_charges_quality') }}
    group by 1
),
stats as (
    select source_id, max(quarantine_pct) as quarantine_pct
    from {{ ref('stg_parse_stats') }}
    group by 1
)
select
    f.source_id,
    f.hospital_name,
    date_diff('day', try_cast(f.last_updated_on as date), current_date) as file_age_days,
    case when f.n_negotiated = 0 then 0 else f.n_dollar::double / f.n_negotiated end as pct_dollar_rates,
    least(1.0, f.n_payers::double / {{ var('expected_payers') }}) as payer_coverage,
    coalesce(s.quarantine_pct, 0) as quarantine_pct,
    (
        0.40 * case
            when date_diff('day', try_cast(f.last_updated_on as date), current_date) <= 365 then 1.0
            else greatest(0.0, 1.0 - (date_diff('day', try_cast(f.last_updated_on as date), current_date) - 365) / 365.0)
        end
        + 0.30 * case when f.n_negotiated = 0 then 0 else f.n_dollar::double / f.n_negotiated end
        + 0.20 * least(1.0, f.n_payers::double / {{ var('expected_payers') }})
        + 0.10 * (1 - coalesce(s.quarantine_pct, 0))
    ) as transparency_score
from files f
left join stats s on f.source_id = s.source_id
