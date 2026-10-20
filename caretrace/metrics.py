"""Score formulas shared by the serving marts and the docs."""

from __future__ import annotations


def quality_score(
    *,
    has_dollar: bool,
    percent_only: bool,
    algorithm_only: bool,
    outlier: bool,
) -> float:
    """1.0 for a clean published dollar rate, with documented penalties."""
    score = 1.0
    if not has_dollar:
        score -= 0.35
    if percent_only:
        score -= 0.15
    if algorithm_only:
        score -= 0.25
    if outlier:
        score -= 0.25
    return max(0.0, min(1.0, score))


def quality_reasons(
    *,
    has_dollar: bool,
    percent_only: bool,
    algorithm_only: bool,
    outlier: bool,
) -> list[str]:
    """Human-readable reasons that accompany quality_score."""
    reasons: list[str] = []
    if has_dollar:
        reasons.append("Published negotiated dollar amount.")
    elif percent_only:
        reasons.append("Percent of charge only; dollar rate was not published.")
    elif algorithm_only:
        reasons.append("Algorithm only; dollar rate was not published.")
    else:
        reasons.append("No negotiated dollar, percent, or algorithm was published.")
    if outlier:
        reasons.append("Dollar rate is above the peer fence (Q3 + 3 * IQR).")
    return reasons


def transparency_score(
    file_age_days: float,
    pct_dollar_rates: float,
    payer_coverage: float,
    quarantine_pct: float,
) -> float:
    """Hospital transparency score in [0, 1].

    0.40 freshness (full credit through 365 days, then linear to 0 at 730)
    + 0.30 share of negotiated rows that publish a dollar rate
    + 0.20 payer coverage, capped at 1 (resolved payers / payers in the registry)
    + 0.10 share of rows that were not quarantined
    """
    if file_age_days <= 365:
        freshness = 1.0
    else:
        freshness = max(0.0, 1.0 - (file_age_days - 365.0) / 365.0)
    coverage = min(1.0, max(0.0, payer_coverage))
    clean = 1.0 - min(1.0, max(0.0, quarantine_pct))
    score = 0.4 * freshness + 0.3 * pct_dollar_rates + 0.2 * coverage + 0.1 * clean
    return max(0.0, min(1.0, score))
