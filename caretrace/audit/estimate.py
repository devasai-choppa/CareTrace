"""Out-of-pocket estimator with explicit assumptions. An estimate is not a quote."""

from __future__ import annotations

from caretrace.models.domain import OopEstimate
from caretrace.models.lineage import Cited, Lineage

ASSUMPTIONS = [
    "The negotiated dollar rate is treated as the allowed amount.",
    "A fixed copay is paid first and reduces the balance subject to the deductible.",
    "The next dollars are applied to the remaining deductible.",
    "Coinsurance applies to the balance after copay and deductible.",
    "Patient responsibility is capped by the remaining out-of-pocket maximum.",
    "This is an estimate, not a quote. Benefit order varies by plan.",
]


def estimate_oop(
    rate: float,
    deductible_remaining: float,
    coinsurance: float,
    copay: float,
    oop_max_remaining: float,
    *,
    lineage: list[Lineage] | None = None,
) -> Cited[OopEstimate]:
    """Estimate patient responsibility from an allowed amount and benefit inputs.

    Coinsurance is a fraction (0.2 means 20 percent), not a percent integer.
    """
    if rate < 0 or deductible_remaining < 0 or copay < 0 or oop_max_remaining < 0:
        raise ValueError("money inputs must be non-negative")
    if not 0 <= coinsurance <= 1:
        raise ValueError("coinsurance must be a fraction between 0 and 1")
    balance = rate
    copay_applied = min(copay, balance)
    balance -= copay_applied
    deductible_applied = min(deductible_remaining, balance)
    balance -= deductible_applied
    coinsurance_applied = balance * coinsurance
    patient = copay_applied + deductible_applied + coinsurance_applied
    patient = min(patient, oop_max_remaining)
    # If the cap binds, keep the components proportional so they still sum to patient.
    raw = copay_applied + deductible_applied + coinsurance_applied
    if raw > oop_max_remaining and raw > 0:
        scale = oop_max_remaining / raw
        copay_applied *= scale
        deductible_applied *= scale
        coinsurance_applied *= scale
        patient = oop_max_remaining
    estimate = OopEstimate(
        allowed_amount=round(rate, 2),
        patient_responsibility=round(patient, 2),
        copay_applied=round(copay_applied, 2),
        deductible_applied=round(deductible_applied, 2),
        coinsurance_applied=round(coinsurance_applied, 2),
        assumptions=list(ASSUMPTIONS),
    )
    reasons = ["Computed from the stated benefit assumptions."]
    if raw > oop_max_remaining:
        reasons.append("Capped by remaining out-of-pocket maximum.")
    return Cited(
        value=estimate,
        lineage=list(lineage or []),
        confidence=0.6 if lineage else 0.4,
        confidence_reasons=reasons,
    )
