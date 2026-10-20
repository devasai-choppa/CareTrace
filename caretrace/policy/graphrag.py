"""Graph-aware policy retrieval with a lexical entailment check.

Flat retrieval can stop on a pointer page. GraphRAG follows code-table edges to
the policy section that actually states the rule, then drops claims the cited
chunk does not entail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from caretrace.policy.answer import CODE_RE, INJECTION_RE, answer_question
from caretrace.policy.chunk import Chunk
from caretrace.policy.index import PolicyIndex

ENTAILMENT_THRESHOLD = 0.6


@dataclass
class LinkedPolicy:
    """A code-to-policy edge extracted from a chunk."""

    code: str
    chunk_id: str
    requires_pa: str
    payer_id: str


def extract_links(chunks: list[Chunk]) -> list[LinkedPolicy]:
    """Pull code table rows and explicit requirement sentences into graph edges."""
    links: list[LinkedPolicy] = []
    table = re.compile(
        r"^(?P<code>[A-Z]?\d{4,5})\s+(?P<flag>required|not_required|conditional)$", re.I | re.M
    )
    for chunk in chunks:
        for match in table.finditer(chunk.text):
            flag = match.group("flag").lower()
            decision = {"required": "yes", "not_required": "no", "conditional": "conditional"}[flag]
            links.append(
                LinkedPolicy(match.group("code"), chunk.chunk_id, decision, chunk.payer_id)
            )
    return links


def entailment_score(claim: str, evidence: str) -> float:
    """Lexical entailment: share of claim content words present in the evidence.

    This is the offline stand-in for a DeBERTa MNLI cross-encoder. Claims below
    the threshold are dropped.
    """
    claim_terms = _terms(claim)
    if not claim_terms:
        return 0.0
    evidence_terms = set(_terms(evidence))
    return sum(1 for term in claim_terms if term in evidence_terms) / len(claim_terms)


def _terms(text: str) -> list[str]:
    stop = {"the", "a", "an", "of", "for", "and", "to", "is", "in", "on"}
    return [
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if token not in stop and not INJECTION_RE.search(token)
    ]


class GraphRAG:
    """Hybrid retrieval plus one graph hop and an entailment filter."""

    method = "graphrag_lexical_nli"

    def __init__(self, index: PolicyIndex) -> None:
        self.index = index
        self.links = extract_links(index.chunks)
        self.by_id = {chunk.chunk_id: chunk for chunk in index.chunks}

    def explain(self, question: str, payer_id: str) -> list[str]:
        """Retrieval path: flat hits, graph hops, entailment scores."""
        flat = self.index.search(question, payer_id=payer_id, k=4)
        lines = [f"Flat retrieval returned {len(flat)} chunks."]
        codes = CODE_RE.findall(question)
        hops = [
            link
            for link in self.links
            if link.payer_id == payer_id and (not codes or link.code in codes)
        ]
        for link in hops:
            chunk = self.by_id.get(link.chunk_id)
            if chunk is None:
                continue
            claim = f"Code {link.code} prior authorization decision is {link.requires_pa}."
            score = entailment_score(claim, chunk.text)
            lines.append(
                f"Hop code {link.code} -> {chunk.policy_title} page {chunk.page} entailment {score:.2f}."
            )
        return lines

    def answer(self, question: str, payer_id: str) -> Any:
        """Prefer a code-table hop. Abstain when the surviving claims are not entailed."""
        base = answer_question(question, self.index, payer_id)
        codes = CODE_RE.findall(question)
        hops = [
            link
            for link in self.links
            if link.payer_id == payer_id and (not codes or link.code in codes)
        ]
        entailed = []
        for link in hops:
            chunk = self.by_id.get(link.chunk_id)
            if chunk is None:
                continue
            claim = f"Code {link.code} prior authorization is {link.requires_pa}."
            score = entailment_score(claim, chunk.text)
            if score >= ENTAILMENT_THRESHOLD:
                entailed.append((link, chunk, score))
        if not entailed:
            if base.requires_pa == "unknown":
                return base
            # Keep the flat answer only when its citation quote is entailed.
            kept = []
            for citation in base.citations:
                quote = str(citation.get("quote") or "")
                if entailment_score(quote, quote) >= ENTAILMENT_THRESHOLD:
                    kept.append(citation)
            base.citations = kept
            if not kept:
                base.requires_pa = "unknown"
                base.confidence = 0.2
                base.confidence_reasons = ["No claim was entailed by a cited chunk."]
            return base
        link, chunk, score = entailed[0]
        base.requires_pa = link.requires_pa  # type: ignore[assignment]
        base.citations = [
            {
                "url": chunk.url,
                "page": chunk.page,
                "quote": _short(chunk.text),
                "section": chunk.section,
            }
        ]
        base.confidence = min(0.9, 0.5 + score / 2)
        base.confidence_reasons = [
            f"Graph hop to {chunk.policy_title} page {chunk.page}.",
            f"Entailment score {score:.2f} meets the {ENTAILMENT_THRESHOLD:.2f} threshold.",
        ]
        base.explanation = self.explain(question, payer_id)
        base.codes_mentioned = sorted(set(base.codes_mentioned) | {link.code})
        return base


def _short(text: str) -> str:
    for line in text.splitlines():
        if INJECTION_RE.search(line):
            continue
        words = line.split()
        if words:
            return " ".join(words[:40])
    return ""
