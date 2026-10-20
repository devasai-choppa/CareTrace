"""Split policy text into chunks that keep page numbers and headings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PAGE_RE = re.compile(r"^---\s*page\s+(\d+)\s*---\s*$", re.IGNORECASE | re.MULTILINE)
EFFECTIVE_RE = re.compile(r"effective date:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    """One retrieval unit."""

    chunk_id: str
    payer_id: str
    policy_title: str
    effective_date: str | None
    url: str
    page: int
    section: str
    text: str


def chunk_text(
    text: str,
    *,
    payer_id: str,
    url: str,
    policy_title: str | None = None,
    max_chars: int = 3200,
    overlap: int = 400,
) -> list[Chunk]:
    """Chunk a policy. Pages come from markers, form feeds, or pdf page breaks."""
    pages = _pages(text)
    title = policy_title or _title(text)
    effective = _effective(text)
    chunks: list[Chunk] = []
    for page, body in pages:
        section = "overview"
        blocks = re.split(r"\n(?=#{1,3}\s)", body)
        for block in blocks:
            heading = HEADING_RE.search(block)
            if heading:
                section = heading.group(1).strip()
            start = 0
            while start < len(block):
                piece = block[start : start + max_chars].strip()
                if piece:
                    chunks.append(
                        Chunk(
                            chunk_id=f"{payer_id}-p{page}-{len(chunks)}",
                            payer_id=payer_id,
                            policy_title=title,
                            effective_date=effective,
                            url=url,
                            page=page,
                            section=section,
                            text=piece,
                        )
                    )
                if start + max_chars >= len(block):
                    break
                start += max(1, max_chars - overlap)
    return chunks


def chunk_pdf(path: Path, *, payer_id: str, url: str) -> list[Chunk]:
    """Extract a PDF with pdfplumber, preserving page numbers."""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(path) as document:
        for index, page in enumerate(document.pages, start=1):
            body = page.extract_text() or ""
            pages.append(f"--- page {index} ---\n{body}")
    return chunk_text("\n".join(pages), payer_id=payer_id, url=url)


def _pages(text: str) -> list[tuple[int, str]]:
    if "\f" in text:
        return [(index, body) for index, body in enumerate(text.split("\f"), start=1)]
    matches = list(PAGE_RE.finditer(text))
    if not matches:
        return [(1, text)]
    pages = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append((int(match.group(1)), text[start:end].strip()))
    return pages


def _title(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("---"):
            return cleaned[:120]
    return "policy"


def _effective(text: str) -> str | None:
    match = EFFECTIVE_RE.search(text)
    return match.group(1) if match else None
