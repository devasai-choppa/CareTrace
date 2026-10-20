"""Shared domain objects for prices, audits, policies, and answers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CodeType = Literal["CPT", "HCPCS", "MS-DRG", "APR-DRG", "RC", "NDC", "LOCAL", "OTHER"]
Setting = Literal["inpatient", "outpatient", "both"]
Severity = Literal["high", "medium", "low", "ask"]
PaDecision = Literal["yes", "no", "conditional", "unknown"]


class RateQuote(BaseModel):
    """One published or imputed rate at hospital x code x setting x payer x plan."""

    hospital_name: str
    source_id: str
    code: str
    code_type: str
    setting: str
    payer_id: str | None = None
    payer_name_raw: str | None = None
    plan_name: str | None = None
    rate_dollar: float | None = None
    rate_kind: str = "missing"
    discounted_cash: float | None = None
    gross_charge: float | None = None
    last_updated_on: str | None = None
    quality_score: float = 0.0
    imputed: bool = False
    interval_low: float | None = None
    interval_high: float | None = None
    file_id: str | None = None
    source_row_hash: str | None = None
    source_file_uri: str | None = None
    explanation: list[str] = Field(default_factory=list)


class OopEstimate(BaseModel):
    """Patient share under explicit, stated assumptions. Not a quote."""

    allowed_amount: float
    patient_responsibility: float
    copay_applied: float
    deductible_applied: float
    coinsurance_applied: float
    assumptions: list[str]


class Finding(BaseModel):
    """One audit result. Impact is dollars the patient might reasonably question."""

    rule_id: str
    severity: Severity
    line_refs: list[int]
    expected: str
    actual: str
    impact: float
    confidence: float
    confidence_reasons: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    explanation: str
    source_ids: list[str] = Field(default_factory=list)
    source_file_uris: list[str] = Field(default_factory=list)
    method: str | None = None


class EobLine(BaseModel):
    """A single extracted bill line with field-level provenance."""

    line_no: int
    service_date: str | None = None
    code: str | None = None
    modifiers: list[str] = Field(default_factory=list)
    units: float | None = None
    billed: float | None = None
    allowed: float | None = None
    paid: float | None = None
    patient_resp: float | None = None
    provider_npi: str | None = None
    provider_name: str | None = None
    network_status: str | None = None
    denial_codes: list[str] = Field(default_factory=list)
    description: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)
    needs_review: list[str] = Field(default_factory=list)
    claim_id: str | None = None


class Citation(BaseModel):
    """Footnote pointing at a tool result and its lineage."""

    claim_id: str
    text: str
    source_id: str | None = None
    source_file_uri: str | None = None
    source_row_hash: str | None = None
    page: int | None = None
    quote: str | None = None
    url: str | None = None


class PriorAuthAnswer(BaseModel):
    """Structured prior-auth answer. Unknown when no chunk supports a decision."""

    requires_pa: PaDecision
    criteria: list[str] = Field(default_factory=list)
    documentation: list[str] = Field(default_factory=list)
    codes_mentioned: list[str] = Field(default_factory=list)
    citations: list[dict[str, str | int | None]] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_reasons: list[str] = Field(default_factory=list)
    stale_policy: bool = False
    explanation: list[str] = Field(default_factory=list)


class TrustScore(BaseModel):
    """Directory confidence for one payer, provider, and location."""

    payer_id: str
    npi: str
    provider_name: str = ""
    score: float
    ghost_risk: bool
    reasons: list[str]
    signals: dict[str, float]
    method: str = "weighted_signals"
    parameters: dict[str, float] = Field(default_factory=dict)


class AgentAnswer(BaseModel):
    """What the API and CLI return after the verifier runs."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float
    confidence_reasons: list[str] = Field(default_factory=list)
    intent: str
    needs_clarification: str | None = None
    trace_id: str = ""
    abstained: bool = False
