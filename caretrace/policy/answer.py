"""Answer prior-auth questions from retrieved chunks. Unknown when evidence is missing."""

from __future__ import annotations

import re
from datetime import date

from caretrace.models.domain import PriorAuthAnswer
from caretrace.policy.chunk import Chunk
from caretrace.policy.index import PolicyIndex, stale

CODE_RE = re.compile(r"\b(?:[A-Z]\d{4}|\d{5})\b")
INJECTION_RE = re.compile(r"ignore previous instructions|system\s*:", re.IGNORECASE)
TABLE_RE = re.compile(
    r"^(?P<code>[A-Z]?\d{4,5})\s+(?P<flag>required|not_required|conditional)$",
    re.IGNORECASE | re.MULTILINE,
)


def answer_question(question: str, index: PolicyIndex, payer_id: str) -> PriorAuthAnswer:
    """Decide requires_pa only from chunks for this payer."""
    hits = index.search(question, payer_id=payer_id, k=6)
    chunks = [chunk for chunk, _score in hits if not _injection_only(chunk.text)]
    if not chunks:
        return PriorAuthAnswer(
            requires_pa="unknown",
            confidence=0.15,
            confidence_reasons=["No supporting policy chunk for this payer."],
        )
    asked = set(CODE_RE.findall(question))
    supporting = chunks
    if asked:
        supporting = [chunk for chunk in chunks if any(code in chunk.text for code in asked)]
    codes = sorted({code for chunk in supporting for code in CODE_RE.findall(chunk.text)})
    if asked and not supporting:
        return PriorAuthAnswer(
            requires_pa="unknown",
            codes_mentioned=sorted(asked),
            confidence=0.2,
            confidence_reasons=["The question names a code that no retrieved chunk mentions."],
        )
    table = _table_decision(supporting, question)
    narrative = _narrative_decision(supporting)
    decision = table or narrative or "unknown"
    chunks = supporting
    citations = _citations(chunks, decision)
    if decision != "unknown" and not citations:
        decision = "unknown"
    effective = next((chunk.effective_date for chunk in chunks if chunk.effective_date), None)
    is_stale = stale(effective, today=date.today().isoformat())
    reasons = ["Decision is copied from a retrieved chunk."]
    confidence = 0.2
    if decision != "unknown":
        confidence = 0.72 if table else 0.6
        reasons.append("A code table matched." if table else "Narrative criteria matched.")
    else:
        reasons.append("Retrieved chunks do not state a prior-authorization decision.")
    if is_stale:
        confidence = min(confidence, 0.45)
        reasons.append("Effective date is older than 18 months.")
    criteria, documentation = _lists(chunks)
    return PriorAuthAnswer(
        requires_pa=decision,  # type: ignore[arg-type]
        criteria=criteria,
        documentation=documentation,
        codes_mentioned=codes,
        citations=citations,
        confidence=confidence,
        confidence_reasons=reasons,
        stale_policy=is_stale,
        explanation=reasons,
    )


def _injection_only(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip() and not INJECTION_RE.search(line)]
    return not lines


def _table_decision(chunks: list[Chunk], question: str) -> str | None:
    asked = set(CODE_RE.findall(question))
    for chunk in chunks:
        for match in TABLE_RE.finditer(chunk.text):
            code = match.group("code")
            if asked and code not in asked:
                continue
            flag = match.group("flag").lower()
            if flag == "required":
                return "yes"
            if flag == "not_required":
                return "no"
            if flag == "conditional":
                return "conditional"
    return None


def _narrative_decision(chunks: list[Chunk]) -> str | None:
    for chunk in chunks:
        for line in chunk.text.splitlines():
            if INJECTION_RE.search(line):
                continue
            lowered = line.lower()
            if (
                "does not require prior authorization" in lowered
                or "do not require prior authorization" in lowered
            ):
                return "no"
            if "requires prior authorization" in lowered:
                if "except" in lowered or "unless" in lowered or "if " in lowered:
                    return "conditional"
                return "yes"
    return None


def _citations(chunks: list[Chunk], decision: str) -> list[dict[str, str | int | None]]:
    citations: list[dict[str, str | int | None]] = []
    for chunk in chunks:
        quote = _quote(chunk.text, decision)
        if not quote:
            continue
        citations.append(
            {
                "url": chunk.url,
                "page": chunk.page,
                "quote": quote,
                "section": chunk.section,
            }
        )
    return citations[:3]


def _quote(text: str, decision: str) -> str | None:
    for line in text.splitlines():
        if INJECTION_RE.search(line):
            continue
        lowered = line.lower()
        useful = (
            "prior authorization" in lowered or "required" in lowered or "not_required" in lowered
        )
        if decision == "unknown" or useful:
            words = line.split()
            if not words:
                continue
            return " ".join(words[:40])
    return None


def _lists(chunks: list[Chunk]) -> tuple[list[str], list[str]]:
    criteria, docs = [], []
    for chunk in chunks:
        if "prior authorization" in chunk.text.lower() and chunk.section.lower().startswith(
            "prior"
        ):
            criteria.append(chunk.text.splitlines()[0][:240])
        if "documentation" in chunk.section.lower() or "submit" in chunk.text.lower():
            for line in chunk.text.splitlines():
                if line.strip().startswith("Submit") or "clinical notes" in line.lower():
                    docs.append(line.strip())
    return criteria[:4], docs[:4]
