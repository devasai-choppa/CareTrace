# Dashboard spec

Three views. `scripts/export_bi.py` writes the gold marts to `exports/` as CSV and Parquet after `caretrace load-samples`. dbt exposures of the same names are in `dbt/models/exposures.yml`.

## 1. Boston price variance by service

Grain: code × payer.

| Field | Meaning |
| --- | --- |
| `code` | Billing code. No CPT descriptor text. |
| `payer_id` | Canonical payer. |
| `min_rate`, `p25`, `median_rate`, `p75`, `max_rate` | Dollar rates across hospitals that published a dollar. |
| `spread_ratio` | `max_rate / min_rate`. |
| `n_hospitals` | Hospitals contributing a dollar rate. |

KPI: spread ratio for CPT 73721 and `bcbsma`. On the samples the published outpatient dollars are 980, 1100, 1250, 1400, and 1750, so the spread ratio is 1750/980.

## 2. Hospital transparency scorecard

`transparency_score` =

0.40 × freshness + 0.30 × share of negotiated rows with a dollar + 0.20 × payer coverage + 0.10 × (1 − quarantine share).

Freshness is 1.0 through day 365 and falls linearly to 0 at day 730. Payer coverage is the count of resolved payers divided by 3, capped at 1. The same formula is in `dbt/models/gold/mart_hospital_compliance.sql` and `caretrace/warehouse.py`.

## 3. Payer coverage and ghost risk

Join `mart_directory_trust` to the negotiated rates on `payer_id`. A listing with `ghost_risk = true` is a directory row whose trust score is under 0.5, or whose NPI is inactive. The mock directory marks NPI `1000000001` (Ghost Imaging LLC) as inactive and NPI `1234567893` (Priya Shah) as corroborated.

## Parity

The Streamlit Market view reads `/v1/sources` and `/v1/prices`, which read the same DuckDB tables the export script copies. A mismatch means the export was taken before `load-samples`.
