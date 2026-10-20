"""Agent tools. Each price or rule comes back with lineage."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from caretrace.audit.appeal import draft_letter
from caretrace.audit.engine import run_audit
from caretrace.audit.estimate import estimate_oop
from caretrace.audit.rules.builtin import AuditContext
from caretrace.clinical.context import get_patient_context
from caretrace.config import get_settings
from caretrace.directory.payer_directory import MockDirectory
from caretrace.directory.trust import score_entry
from caretrace.models.domain import EobLine, RateQuote
from caretrace.policy.chunk import chunk_text
from caretrace.policy.graphrag import GraphRAG
from caretrace.policy.index import PolicyIndex
from caretrace.reference import lookup_codes
from caretrace.warehouse import get_rates


def tool_lookup_codes(text: str) -> dict[str, Any]:
    """Plain language to shoppable code candidates."""
    hits = lookup_codes(text)
    return {"claim_id": "codes", "codes": hits}


def tool_get_prices(code: str, payer_id: str, zip_code: str | None) -> dict[str, Any]:
    """Cited negotiated rates."""
    quotes = get_rates(code, payer_id, zip_code=zip_code)
    payload = []
    for index, cited in enumerate(quotes, start=1):
        quote = cited.value
        lineage = cited.lineage[0]
        payload.append(
            {
                "claim_id": f"q_{index}",
                "hospital": quote.hospital_name,
                "source_id": quote.source_id,
                "code": quote.code,
                "rate_dollar": quote.rate_dollar,
                "discounted_cash": quote.discounted_cash,
                "plan": quote.plan_name,
                "quality_score": quote.quality_score,
                "source_file_uri": lineage.source_file_uri,
                "source_row_hash": lineage.source_row_hash,
                "ingested_at": lineage.ingested_at.isoformat(),
                "imputed": quote.imputed,
            }
        )
    return {"prices": payload}


def tool_check_network(payer_id: str, npi: str | None = None) -> dict[str, Any]:
    """Directory trust for the demo provider list."""
    directory = MockDirectory()
    entries = directory.search(payer_id, npi=npi)
    scores = []
    for entry in entries:
        scored = score_entry(entry, claimed_name=entry.name, claimed_address=entry.address)
        scores.append(
            {
                "npi": entry.npi,
                "name": entry.name,
                "score": scored.score,
                "ghost_risk": scored.ghost_risk,
                "reasons": scored.reasons,
            }
        )
    return {"network": scores}


def tool_prior_auth(payer_id: str, question: str) -> dict[str, Any]:
    """GraphRAG answer over the sample policy."""
    rag = _policy()
    answer = rag.answer(question, payer_id)
    return {
        "prior_auth": {
            "requires_pa": answer.requires_pa,
            "confidence": answer.confidence,
            "citations": answer.citations,
            "documentation": answer.documentation,
            "reasons": answer.confidence_reasons,
        }
    }


def tool_estimate(
    rate: float, deductible: float, coinsurance: float, copay: float, oop_max: float
) -> dict[str, Any]:
    """Out-of-pocket estimate. The rate must already be a cited quote."""
    estimate = estimate_oop(rate, deductible, coinsurance, copay, oop_max)
    return {"estimate": estimate.value.model_dump(), "assumptions": estimate.value.assumptions}


def tool_audit(lines: list[EobLine], rates: dict[str, RateQuote], **kwargs: Any) -> dict[str, Any]:
    """Run the rule engine."""
    context = AuditContext(rates_by_code=rates, **kwargs)
    report = run_audit(lines, context)
    findings = report["findings"]
    if not isinstance(findings, list):
        raise TypeError("audit findings must be a list")
    return {
        "findings": [finding.model_dump() for finding in findings],
        "potential_savings": report["potential_savings"],
    }


def tool_appeal(findings_payload: list[dict[str, Any]], claim_id: str) -> dict[str, Any]:
    """Draft a letter from findings. Numbers stay in the template."""
    from caretrace.models.domain import Finding

    findings = [Finding.model_validate(item) for item in findings_payload]
    letter = draft_letter(findings, claim_id=claim_id)
    return {"letter": letter}


def tool_patient(person_id: str) -> dict[str, Any]:
    """Synthetic patient context."""
    return {"patient": get_patient_context(person_id)}


def tool_blob(results: dict[str, Any]) -> str:
    """Serialize tool results so the verifier can see every allowed number."""
    return json.dumps(results, default=str)


@lru_cache(maxsize=1)
def _policy() -> GraphRAG:
    path = Path("data/samples/policies/bcbsma_imaging.txt")
    text = path.read_text(encoding="utf-8")
    chunks = chunk_text(
        text, payer_id="bcbsma", url="file://data/samples/policies/bcbsma_imaging.txt"
    )
    index = PolicyIndex()
    index.add(chunks)
    get_settings()
    return GraphRAG(index)
