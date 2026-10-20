"""Run every rule, drop duplicate findings, and rank by dollars times confidence."""

from __future__ import annotations

from typing import TypedDict

from caretrace.audit.rules.builtin import RULES, AuditContext
from caretrace.models.domain import EobLine, Finding


class AuditReport(TypedDict):
    """Ranked findings plus the dollar total of non-anomaly impacts."""

    findings: list[Finding]
    potential_savings: float


def run_audit(
    lines: list[EobLine],
    context: AuditContext,
    extra: list[Finding] | None = None,
) -> AuditReport:
    """Return ranked findings and a total of positive impacts."""
    found: list[Finding] = []
    for rule in RULES:
        found.extend(rule.evaluate(lines, context))
    if extra:
        found.extend(extra)
    deduped = _dedupe(found)
    deduped.sort(key=lambda item: item.impact * item.confidence, reverse=True)
    savings = round(
        sum(item.impact for item in deduped if item.impact > 0 and item.severity != "ask"), 2
    )
    return {"findings": deduped, "potential_savings": savings}


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen = set()
    kept = []
    for finding in findings:
        key = (finding.rule_id, tuple(finding.line_refs))
        if key in seen:
            continue
        seen.add(key)
        kept.append(finding)
    return kept
