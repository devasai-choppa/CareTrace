"""Build synthetic EOB PDFs with known injected errors and an answer key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

CASES = [
    {
        "id": "eob-over-negotiated",
        "claim_id": "CLM100",
        "npi": "1234567893",
        "network": "in-network",
        "lines": [
            "1|2026-06-01|73721|1|2500.00|2200.00|1800.00|400.00|",
        ],
        "errors": [{"rule_id": "R1", "code": "73721"}],
    },
    {
        "id": "eob-duplicate",
        "claim_id": "CLM101",
        "npi": "1234567893",
        "network": "in-network",
        "lines": [
            "1|2026-06-01|73721|1|1400.00|1400.00|1000.00|400.00|",
            "2|2026-06-01|73721|1|1400.00|1400.00|1000.00|400.00|",
        ],
        "errors": [{"rule_id": "R2", "code": "73721"}],
    },
    {
        "id": "eob-network",
        "claim_id": "CLM102",
        "npi": "1234567893",
        "network": "out-of-network",
        "lines": [
            "1|2026-06-01|99213|1|300.00|180.00|0.00|180.00|",
        ],
        "errors": [{"rule_id": "R5", "code": "99213"}],
    },
]


def render_case(case: dict[str, Any], path: Path) -> Path:
    """Write one synthetic EOB PDF in the pipe layout the parser expects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_lines = case["lines"]
    if not isinstance(raw_lines, list):
        raise TypeError("case lines must be a list")
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Courier", 10)
    y = 740
    for line in [
        "EXPLANATION OF BENEFITS (SYNTHETIC)",
        "Not a real bill. Not legal or medical advice.",
        f"Claim: {case['claim_id']}",
        "Payer: bcbsma",
        f"Provider NPI: {case['npi']}",
        f"Network: {case['network']}",
        "LINE|DATE|CODE|UNITS|BILLED|ALLOWED|PAID|PATIENT|DENIAL",
        *[str(item) for item in raw_lines],
    ]:
        pdf.drawString(48, y, line)
        y -= 16
    pdf.save()
    return path


def expanded_cases(count: int = 30) -> list[dict[str, Any]]:
    """Thirty labeled synthetic EOBs: over-negotiated, duplicate, network, and clean."""
    plan = (["R1"] * 10) + (["R2"] * 8) + (["R5"] * 6) + (["clean"] * 6)
    cases: list[dict[str, Any]] = []
    for index, kind in enumerate(plan[:count]):
        claim = f"CLM{200 + index:03d}"
        if kind == "R1":
            lines = ["1|2026-06-01|73721|1|2500.00|2200.00|1800.00|400.00|"]
            errors = [{"rule_id": "R1", "code": "73721", "impact": 800.0}]
            network = "in-network"
        elif kind == "R2":
            lines = [
                "1|2026-06-01|73721|1|1400.00|1400.00|1000.00|50.00|",
                "2|2026-06-01|73721|1|1400.00|1400.00|1000.00|50.00|",
            ]
            errors = [{"rule_id": "R2", "code": "73721", "impact": 1400.0}]
            network = "in-network"
        elif kind == "R5":
            lines = ["1|2026-06-01|99213|1|300.00|180.00|0.00|180.00|"]
            errors = [{"rule_id": "R5", "code": "99213", "impact": 180.0}]
            network = "out-of-network"
        else:
            lines = ["1|2026-06-01|73721|1|1400.00|1400.00|1000.00|50.00|"]
            errors = []
            network = "in-network"
        cases.append(
            {
                "id": f"eob-{index:02d}-{kind.lower()}",
                "claim_id": claim,
                "npi": "1234567893",
                "network": network,
                "lines": lines,
                "errors": errors,
            }
        )
    return cases


def write_suite(directory: Path, cases: list[dict[str, Any]] | None = None) -> list[Path]:
    """Write PDFs plus answer-key JSON for the eval harness."""
    chosen = cases or CASES
    written = []
    keys = []
    for case in chosen:
        path = directory / f"{case['id']}.pdf"
        render_case(case, path)
        written.append(path)
        keys.append(
            {
                "file": path.name,
                "claim_id": case["claim_id"],
                "lines": case["lines"],
                "errors": case["errors"],
            }
        )
    (directory / "answer_key.json").write_text(json.dumps(keys, indent=2), encoding="utf-8")
    return written
