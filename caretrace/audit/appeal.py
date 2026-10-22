"""Draft appeal letters. The template inserts every number. The model writes prose only."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from caretrace.llm.client import complete
from caretrace.models.domain import Finding

TEMPLATE_DIR = Path(__file__).parent / "templates"
DIGIT_RE = re.compile(r"\d")


def draft_letter(
    findings: list[Finding],
    *,
    template: str = "payer_appeal.md",
    claim_id: str = "CLAIM-PLACEHOLDER",
    account: str = "ACCOUNT-PLACEHOLDER",
    payer_id: str = "bcbsma",
    deadline: str = "DEADLINE-PLACEHOLDER",
    file_date: str = "2026-06-15",
) -> str:
    """Render a markdown letter. Findings without a citation are rejected."""
    for finding in findings:
        if finding.severity == "ask":
            continue
        if not finding.evidence_hashes and not finding.source_file_uris:
            raise ValueError(f"{finding.rule_id} is missing a citation")
    narrative = _narrative()
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape([]))
    text = env.get_template(template).render(
        findings=[finding for finding in findings if finding.severity != "ask"],
        narrative=narrative,
        total=sum(finding.impact for finding in findings if finding.severity != "ask"),
        claim_id=claim_id,
        account=account,
        payer_id=payer_id,
        deadline=deadline,
        file_date=file_date,
    )
    return text


def write_pdf(markdown: str, path: Path) -> Path:
    """Write the draft to a simple text PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setTitle("CareTrace draft — not legal advice")
    pdf.setFont("Courier", 9)
    y = 740
    for line in markdown.splitlines():
        if y < 48:
            pdf.showPage()
            pdf.setFont("Courier", 9)
            y = 740
        pdf.drawString(48, y, line[:110])
        y -= 12
    pdf.save()
    return path


def _narrative() -> str:
    raw = complete(
        "Write one short paragraph asking the payer to review the cited discrepancies.",
        system="TASK: narrative. Do not include any digits, codes, dates, or dollar amounts.",
    )
    cleaned = DIGIT_RE.sub("", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = (
            "Please review the discrepancies listed below. "
            "Each figure comes from the hospital file or the bill and is cited in the sections that follow."
        )
    return cleaned
