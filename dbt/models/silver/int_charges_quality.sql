with base as (
    select * from {{ ref('int_charges_resolved') }}
),
peer as (
    select
        code,
        payer_id,
        quantile_cont(negotiated_dollar, 0.25) as q1,
        quantile_cont(negotiated_dollar, 0.75) as q3,
        count(*) as peer_n
    from base
    where negotiated_dollar is not null and payer_id is not null
    group by 1, 2
)
select
    b.*,
    (b.negotiated_dollar is null and b.negotiated_percentage is null and coalesce(b.negotiated_algorithm, '') = '') as missing_negotiated,
    (b.negotiated_dollar is null and b.negotiated_percentage is not null and coalesce(b.negotiated_algorithm, '') = '') as percent_only,
    (b.negotiated_dollar is null and coalesce(b.negotiated_algorithm, '') <> '') as algorithm_only,
    (
        b.negotiated_dollar is not null
        and p.peer_n >= 4
        and b.negotiated_dollar > p.q3 + 3 * (p.q3 - p.q1)
    ) as outlier_vs_peer
from base b
left join peer p on b.code = p.code and b.payer_id = p.payer_id
