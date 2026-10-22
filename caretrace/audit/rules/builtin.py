"""The seven bill-audit rules. Impacts are dollars a patient might question."""

from __future__ import annotations

from dataclasses import dataclass, field

from caretrace.models.domain import EobLine, Finding, RateQuote
from caretrace.reference import unbundling_pairs

TOLERANCE = 1.0


@dataclass
class AuditContext:
    """Evidence the rules are allowed to use."""

    rates_by_code: dict[str, RateQuote] = field(default_factory=dict)
    directory_confidence: float | None = None
    policy_requires_pa: str | None = None
    visit_complexity: str | None = None
    pairs: set[tuple[str, str]] = field(default_factory=set)


class Rule:
    """Base rule."""

    id = "R0"
    severity = "medium"

    def explain(self) -> str:
        return "Base rule."

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        return []


class BilledOverNegotiated(Rule):
    id = "R1"
    severity = "high"

    def explain(self) -> str:
        return "Allowed amount is above the hospital's published negotiated rate for this code, plus a small tolerance."

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        findings = []
        for line in lines:
            if not line.code or line.allowed is None:
                continue
            quote = context.rates_by_code.get(line.code)
            if quote is None or quote.rate_dollar is None:
                continue
            limit = quote.rate_dollar + max(TOLERANCE, quote.rate_dollar * 0.01)
            if line.allowed > limit:
                impact = round(line.allowed - quote.rate_dollar, 2)
                findings.append(
                    _finding(
                        self,
                        [line.line_no],
                        expected=f"${quote.rate_dollar:.2f} published negotiated rate",
                        actual=f"${line.allowed:.2f} allowed",
                        impact=impact,
                        confidence=quote.quality_score,
                        reasons=["Compared with the hospital MRF row for this code and payer."],
                        quote=quote,
                        explanation=self.explain(),
                    )
                )
        return findings


class DuplicateLine(Rule):
    id = "R2"
    severity = "high"

    def explain(self) -> str:
        return "The same code, date, units, and provider appear on more than one line."

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        del context
        groups: dict[tuple[object, ...], list[EobLine]] = {}
        for line in lines:
            key = (line.code, line.service_date, line.units, line.provider_npi)
            groups.setdefault(key, []).append(line)
        findings = []
        for group in groups.values():
            if len(group) < 2 or not group[0].code:
                continue
            extra = group[1:]
            impact = round(sum(line.allowed or line.billed or 0 for line in extra), 2)
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=self.severity,  # type: ignore[arg-type]
                    line_refs=[line.line_no for line in group],
                    expected="one line for this code, date, units, and provider",
                    actual=f"{len(group)} lines",
                    impact=impact,
                    confidence=0.9,
                    confidence_reasons=["Exact key match across lines on the same claim."],
                    evidence_hashes=["eob-duplicate"],
                    source_file_uris=["eob://claim"],
                    explanation=self.explain(),
                )
            )
        return findings


class Unbundling(Rule):
    id = "R3"
    severity = "medium"

    def explain(self) -> str:
        return (
            "Both codes in an illustrative bundled pair are present. "
            "The seed is not the full CMS NCCI table."
        )

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        pairs = context.pairs or set(unbundling_pairs())
        codes = {line.code: line for line in lines if line.code}
        findings = []
        seen = set()
        for left, right in pairs:
            if (
                left in codes
                and right in codes
                and (left, right) not in seen
                and (right, left) not in seen
            ):
                seen.add((left, right))
                line = codes[right]
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.severity,  # type: ignore[arg-type]
                        line_refs=[codes[left].line_no, line.line_no],
                        expected=f"{right} not billed in addition to {left}",
                        actual=f"both {left} and {right} are present",
                        impact=round(line.allowed or line.billed or 0, 2),
                        confidence=0.55,
                        confidence_reasons=[
                            "Illustrative pair list, not a complete NCCI edit table."
                        ],
                        evidence_hashes=["seed-unbundling"],
                        source_file_uris=["dbt/seeds/unbundling_pairs.csv"],
                        explanation=self.explain(),
                    )
                )
        return findings


class EmLevelOutlier(Rule):
    id = "R4"
    severity = "low"

    def explain(self) -> str:
        return "A high evaluation-and-management level is unusual for a low-complexity visit. This is a heuristic."

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        if context.visit_complexity != "low":
            return []
        findings = []
        for line in lines:
            if line.code in {"99214", "99215", "99205"}:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.severity,  # type: ignore[arg-type]
                        line_refs=[line.line_no],
                        expected="a lower visit level given the recorded complexity",
                        actual=f"code {line.code}",
                        impact=round((line.patient_resp or 0) * 0.25, 2),
                        confidence=0.35,
                        confidence_reasons=["Heuristic only. Not a coding determination."],
                        evidence_hashes=["omop-visit"],
                        source_file_uris=["omop://visit"],
                        explanation=self.explain(),
                    )
                )
        return findings


class NetworkMismatch(Rule):
    id = "R5"
    severity = "high"

    def explain(self) -> str:
        return "The bill says out of network, but the directory trust score for this payer is high."

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        if context.directory_confidence is None or context.directory_confidence < 0.8:
            return []
        findings = []
        for line in lines:
            status = (line.network_status or "").lower().replace("_", "-")
            if "out" in status:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.severity,  # type: ignore[arg-type]
                        line_refs=[line.line_no],
                        expected="in-network benefits",
                        actual=line.network_status or "out-of-network",
                        impact=round(line.patient_resp or 0, 2),
                        confidence=min(0.85, context.directory_confidence),
                        confidence_reasons=[
                            f"Directory confidence is {context.directory_confidence:.2f}."
                        ],
                        evidence_hashes=["directory-trust"],
                        source_file_uris=["directory://payer"],
                        explanation=self.explain(),
                    )
                )
        return findings


class CashPriceBetter(Rule):
    id = "R6"
    severity = "medium"

    def explain(self) -> str:
        return (
            "The hospital's discounted cash price is below the patient responsibility on this line."
        )

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        findings = []
        for line in lines:
            if not line.code or line.patient_resp is None:
                continue
            quote = context.rates_by_code.get(line.code)
            if quote is None or quote.discounted_cash is None:
                continue
            if quote.discounted_cash < line.patient_resp:
                findings.append(
                    _finding(
                        self,
                        [line.line_no],
                        expected=f"${quote.discounted_cash:.2f} discounted cash price",
                        actual=f"${line.patient_resp:.2f} patient responsibility",
                        impact=round(line.patient_resp - quote.discounted_cash, 2),
                        confidence=quote.quality_score,
                        reasons=["Cash price is published on the same MRF row."],
                        quote=quote,
                        explanation=self.explain(),
                    )
                )
        return findings


class PriorAuthDenialCheck(Rule):
    id = "R7"
    severity = "medium"

    def explain(self) -> str:
        return "A prior-authorization denial is present, and the cited policy says prior authorization is not required."

    def evaluate(self, lines: list[EobLine], context: AuditContext) -> list[Finding]:
        if context.policy_requires_pa != "no":
            return []
        findings = []
        for line in lines:
            denials = {code.upper() for code in line.denial_codes}
            if denials & {"CO-197", "197", "PR-197"}:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=self.severity,  # type: ignore[arg-type]
                        line_refs=[line.line_no],
                        expected="no prior authorization required by the cited policy",
                        actual="denial " + ", ".join(sorted(denials)),
                        impact=round(line.billed or line.patient_resp or 0, 2),
                        confidence=0.6,
                        confidence_reasons=["Denial code plus a policy answer of no."],
                        evidence_hashes=["policy-pa"],
                        source_file_uris=["policy://cited"],
                        explanation=self.explain(),
                    )
                )
        return findings


RULES: list[Rule] = [
    BilledOverNegotiated(),
    DuplicateLine(),
    Unbundling(),
    EmLevelOutlier(),
    NetworkMismatch(),
    CashPriceBetter(),
    PriorAuthDenialCheck(),
]
ALL_RULES = {rule.id: rule for rule in RULES}


def _finding(
    rule: Rule,
    line_refs: list[int],
    *,
    expected: str,
    actual: str,
    impact: float,
    confidence: float,
    reasons: list[str],
    quote: RateQuote,
    explanation: str,
) -> Finding:
    return Finding(
        rule_id=rule.id,
        severity=rule.severity,  # type: ignore[arg-type]
        line_refs=line_refs,
        expected=expected,
        actual=actual,
        impact=impact,
        confidence=confidence,
        confidence_reasons=reasons,
        evidence_hashes=[quote.source_row_hash] if quote.source_row_hash else [],
        explanation=explanation,
        source_ids=[quote.source_id],
        source_file_uris=[quote.source_file_uri or ""],
        method="audit_rule",
    )
