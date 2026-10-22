"""Bind every number in a draft answer to a tool result, or remove it."""

from __future__ import annotations

import re
from typing import Any

from caretrace.llm.client import numbers_in

TOKEN_RE = re.compile(r"\[\[(q_\d+)\]\]")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")


def uncited_numbers(answer: str, tool_blob: str) -> list[str]:
    """Numbers in the answer that do not appear in the tool payload."""
    allowed = set(_normalize(number) for number in numbers_in(tool_blob))
    bad = []
    for number in numbers_in(answer):
        if _normalize(number) not in allowed:
            bad.append(number)
    return bad


def strip_uncited(answer: str, tool_blob: str) -> str:
    """Drop sentences that contain a number the tools did not return."""
    bad = set(uncited_numbers(answer, tool_blob))
    if not bad:
        return answer.strip()
    kept = []
    for sentence in SENTENCE_RE.findall(answer):
        if any(number in sentence for number in bad):
            continue
        kept.append(sentence.strip())
    text = " ".join(part for part in kept if part)
    if not text:
        return "I cannot show a figure for that because no cited tool result supports it."
    return text


def verify_answer(answer: str, tool_blob: str) -> dict[str, Any]:
    """Return the cleaned answer and whether anything was removed."""
    bad = uncited_numbers(answer, tool_blob)
    cleaned = strip_uncited(answer, tool_blob) if bad else answer.strip()
    return {"answer": cleaned, "removed": bad, "ok": not bad}


def claim_ids(answer: str) -> list[str]:
    """Provenance tokens the synthesizer inserted."""
    return TOKEN_RE.findall(answer)


def _normalize(number: str) -> str:
    cleaned = number.replace(",", "")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned
