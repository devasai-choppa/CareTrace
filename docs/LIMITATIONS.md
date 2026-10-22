# Limitations and challenge mitigations

CareTrace is built to cite published hospital dollars and draft a review letter.
These are the hard risks, and what the product does about each one.

## Dirty hospital files

**Risk:** MRFs change shape, break CSV quoting, bury CPT next to local codes, or
publish percent/algorithm cells with no dollar.

**Mitigation:** Streamed parsers, quarantine with reasons, CMS v3 preamble skip,
inch-mark repair, and `load-live --shoppable` (default) so demos do not load
tens of millions of unused rows. Empty results abstain instead of inventing a
price.

## Wrong payer or plan

**Risk:** `BCBS`, Medicare managed, and out-of-state Blue Cross strings look
alike. Comparing a commercial EOB to a Medicare rate invents false savings.

**Mitigation:** Alias + confirmation table for known Boston Blue Cross strings.
`commercial_only=True` (API/UI/audit default) drops Medicare/Medicaid/workers-comp
looking plans. Plan text soft-matches HMO/PPO/POS/EPO. If nothing matches,
CareTrace returns no quote.

## Overconfidence (estimate treated as a quote)

**Risk:** Users hear a dollar and assume that is what they will pay.

**Mitigation:** Persistent UI/API disclaimer: estimates are not quotes. Estimate
and audit pages state that deductible, network, and site of care change liability.
Appeal letters are labeled drafts and not legal advice.

## PHI and legal exposure

**Risk:** Real EOBs are PHI; appeals can be mistaken for legal advice.

**Mitigation:** Demo mode rejects SSN-like patterns (without treating CMS hospital
ids inside MRF URLs as SSNs). UI warns to upload synthetic EOBs only. Appeal
templates own the numbers; the model paragraph cannot invent dollars.

## Bad prior-auth answers

**Risk:** Citing the wrong policy page is worse than saying unknown.

**Mitigation:** Policy path returns `unknown` when the code is not in the
retrieved table. Sample policy URLs remain placeholders until a public PDF is
wired; the demo cites the local imaging fixture with a page number.

## Scale

**Risk:** Full Tufts/BMC files are multi-GB.

**Mitigation:** Shoppable-code bronze filter on `load-live`, SQL quality scoring
(no pandas over full bronze), and separate `load-samples` for CI.

## Trust / invented numbers

**Risk:** An LLM or imputed interval shown as a hospital price destroys trust.

**Mitigation:** Published-only quotes in the price API (`published_only=True`).
Imputed intervals stay separate objects. The agent verifier strips uncited
numbers from answers.

## Maintenance drift

**Risk:** `cms-hpt.txt` hosts move; files grow; yesterday's URL 404s.

**Mitigation:** `/v1/health` reports `data_mode` (`sample` / `live` / `mixed` /
`empty`) so a demo can see what is loaded. Discover + download remain explicit
CLI steps; quarantine stats feed the transparency score.

## What is still open

- Real prior-auth policy PDFs instead of `example.invalid`
- Live NPPES directory checks (client exists; demo still uses a mock list)
- Scheduled discover/download with alerting
- Warehouse backend beyond local DuckDB for statewide scale
