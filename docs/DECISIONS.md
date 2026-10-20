# Decisions

## ADR 1 — DuckDB locally, Snowflake or Databricks as profiles

Local development uses DuckDB and Parquet so `make check` needs no warehouse account. `dbt/profiles.yml.example` has `snowflake` and `databricks` targets that read the same environment variables. The serving marts the API reads are built in Python (`caretrace/warehouse.py`) into a `gold` schema so they do not collide with dbt's `main_analytics` schema. dbt remains the documented transform and the CI build.

## ADR 2 — Fellegi-Sunter in-repo, rapidfuzz as the baseline

Splink is listed as an optional extra and is not required to run the demo. `caretrace/er/linkage.py` fits the Fellegi-Sunter model with expectation maximization and a plan-token block: if both sides name a plan and the token overlap is under 0.2, the match probability is forced to 0.05. On the 300-pair scaffold, that model F1 is 1.000 and the rapidfuzz token-set baseline (threshold 90) is 0.667, because the baseline matches same-name rows that disagree on plan. `explain()` returns the match-weight waterfall.

## ADR 3 — Embedded Kuzu for the market graph

The graph is a file, not a server. Nodes are hospitals, providers, payers, plans, and services. Negotiated edges store the rate and the row hash. Cypher in `caretrace/graph/queries.py` returns the cheapest in-network option with that path.

## ADR 4 — Quantile gradient boosting plus split conformal, not a published price

Many MRF cells are a percent of charge or an algorithm. `caretrace/ml/price_model.py` fits three quantile models on log price and adds a Mondrian (per code family) conformal slack. The output is always flagged `imputed`. It is not shown as the hospital's published dollar. The default model is scikit-learn gradient boosting. LightGBM and MAPIE are the production swap and live in the optional `ml` extra. On a synthetic price process the holdout coverage was 0.957 against a 0.90 nominal target. The finite-sample quantile is conservative, so coverage sits above 90 percent rather than inside a tight 88–92 band.

## ADR 5 — Rules before anomaly scores

Rules R1–R7 are the findings that can enter an appeal. Isolation Forest plus a simple ECOD score is a separate `ask` severity. Those rows are excluded from the savings total and from the letter unless a person confirms them.

## ADR 6 — Verifier node

The synthesizer copies numbers from tool payloads and tags them `[[q_n]]`. `caretrace/agent/verifier.py` drops any remaining number that is absent from the tool JSON. A prompt injection inside a policy page is treated as text, not as an instruction.

## ADR 7 — Differential privacy without a live user table

Employer market cells use a Laplace-noised histogram median. Sensitivity of the histogram is 1. Cells with fewer than 11 people are suppressed. `PrivacyAccountant` refuses a release that would exceed the epsilon budget. OpenDP is the library to swap in when a deployment needs a reviewed accountant; the mechanism and the tests are local so the demo runs offline.

## ADR 8 — Lexical entailment instead of a cross-encoder by default

GraphRAG hops from a code table to the policy page, then checks that claim words appear in the cited chunk (threshold 0.6). A DeBERTa MNLI model is the documented replacement. If the question names a code that no chunk contains, the answer is `unknown`.
