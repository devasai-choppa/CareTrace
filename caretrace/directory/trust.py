"""Hand-weighted directory trust score. The learned model in trust_model.py is the upgrade."""

from __future__ import annotations

from datetime import UTC, datetime

from rapidfuzz import fuzz

from caretrace.directory.payer_directory import DirectoryEntry
from caretrace.models.domain import TrustScore
from caretrace.models.lineage import Lineage, now_utc

WEIGHTS = {
    "npi_active": 0.20,
    "name_match": 0.20,
    "address_match": 0.20,
    "taxonomy_match": 0.15,
    "phone_match": 0.10,
    "recency": 0.05,
    "mrf_corroboration": 0.10,
}

ADDRESS_WORDS = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "drive": "dr",
    "suite": "ste",
    "massachusetts": "ma",
    "boston": "boston",
}


def normalize_address(value: str) -> str:
    """Lightweight address normalization. usaddress is optional and not required."""
    text = value.lower().replace(",", " ").replace(".", " ")
    tokens = [ADDRESS_WORDS.get(token, token) for token in text.split()]
    return " ".join(tokens)


def score_entry(
    entry: DirectoryEntry,
    *,
    claimed_name: str | None = None,
    claimed_address: str | None = None,
    claimed_phone: str | None = None,
    claimed_taxonomy: str | None = None,
) -> TrustScore:
    """Score one listing. Ghost risk is score below 0.5."""
    signals = {
        "npi_active": 1.0 if entry.npi_active else 0.0,
        "name_match": _name(entry.name, claimed_name),
        "address_match": _address(entry.address, claimed_address),
        "taxonomy_match": _text_match(entry.taxonomy, claimed_taxonomy),
        "phone_match": _phone(entry.phone, claimed_phone),
        "recency": _recency(entry.last_updated),
        "mrf_corroboration": 1.0 if entry.in_mrf else 0.0,
    }
    score = sum(signals[key] * WEIGHTS[key] for key in WEIGHTS)
    reasons = _reasons(signals, entry)
    # An inactive NPI is a hard ghost signal. Weighted name and address fields
    # would otherwise keep a deactivated listing above 0.5.
    ghost = score < 0.5 or not entry.npi_active
    if not entry.npi_active:
        reasons.insert(
            0, "NPI is inactive, so this listing is a ghost risk regardless of the other fields."
        )
    return TrustScore(
        payer_id=entry.payer_id,
        npi=entry.npi,
        provider_name=entry.name,
        score=round(score, 4),
        ghost_risk=ghost,
        reasons=reasons,
        signals=signals,
        method="weighted_signals",
        parameters=WEIGHTS,
    )


def explain(result: TrustScore) -> list[str]:
    """Human-readable signal contributions."""
    return list(result.reasons)


def lineage_for(result: TrustScore) -> Lineage:
    """Lineage for a trust score, including the weight vector."""
    return Lineage(
        source_id=result.payer_id,
        source_file_uri=f"directory://{result.payer_id}/{result.npi}",
        source_row_hash=result.npi,
        ingested_at=now_utc(),
        parser_version="directory-1.0.0",
        method=result.method,
        parameters=result.parameters,
    )


def _name(listed: str, claimed: str | None) -> float:
    if not claimed:
        return 1.0 if listed else 0.0
    return float(fuzz.token_set_ratio(listed.lower(), claimed.lower())) / 100


def _address(listed: str, claimed: str | None) -> float:
    if not claimed:
        return 1.0 if listed else 0.0
    return float(fuzz.token_set_ratio(normalize_address(listed), normalize_address(claimed))) / 100


def _text_match(listed: str, claimed: str | None) -> float:
    if not claimed:
        return 1.0 if listed else 0.0
    return 1.0 if claimed.lower() in listed.lower() or listed.lower() in claimed.lower() else 0.0


def _phone(listed: str, claimed: str | None) -> float:
    digits = "".join(ch for ch in listed if ch.isdigit())
    if not claimed:
        return 1.0 if digits else 0.0
    claimed_digits = "".join(ch for ch in claimed if ch.isdigit())
    return 1.0 if digits and digits == claimed_digits else 0.0


def _recency(value: str) -> float:
    if not value:
        return 0.0
    try:
        updated = datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return 0.0
    age = (datetime.now(UTC) - updated).days
    if age <= 180:
        return 1.0
    if age <= 365:
        return 0.5
    return 0.0


def _reasons(signals: dict[str, float], entry: DirectoryEntry) -> list[str]:
    reasons = []
    if signals["npi_active"] == 1:
        reasons.append("NPI is active in the directory feed.")
    else:
        reasons.append("NPI is not active.")
    if signals["mrf_corroboration"] == 1:
        reasons.append("The same payer appears in this hospital's published MRF.")
    else:
        reasons.append("No hospital MRF row corroborates this payer and provider.")
    if signals["name_match"] >= 0.9:
        reasons.append("Provider name matches the claim.")
    elif signals["name_match"] < 0.75:
        reasons.append("Provider name is a weak match.")
    if signals["address_match"] >= 0.9:
        reasons.append("Address matches after normalization.")
    if signals["recency"] == 0:
        reasons.append("Directory record is older than a year.")
    if entry.network_status:
        reasons.append(f"Payer lists the provider as {entry.network_status}.")
    return reasons
