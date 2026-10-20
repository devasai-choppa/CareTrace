select
    code,
    payer_id,
    min(rate_dollar) as min_rate,
    quantile_cont(rate_dollar, 0.25) as p25,
    quantile_cont(rate_dollar, 0.50) as median_rate,
    quantile_cont(rate_dollar, 0.75) as p75,
    max(rate_dollar) as max_rate,
    max(rate_dollar) / nullif(min(rate_dollar), 0) as spread_ratio,
    count(distinct source_id) as n_hospitals
from {{ ref('mart_negotiated_rates') }}
where rate_dollar is not null
group by 1, 2
