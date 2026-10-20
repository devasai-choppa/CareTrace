"""Turn a synthetic EOB into line items with field provenance."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from caretrace.models.domain import EobLine
from caretrace.phi import assert_no_phi

LINE_RE = re.compile(
    r"^(?P<line>\d+)\|(?P<date>\d{4}-\d{2}-\d{2})\|(?P<code>[A-Z0-9]+)\|(?P<units>[\d.]+)\|"
    r"(?P<billed>[\d.]+)\|(?P<allowed>[\d.]+)\|(?P<paid>[\d.]+)\|(?P<patient>[\d.]+)\|(?P<denial>[^|\n]*)$",
    re.MULTILINE,
)


def ingest_path(path: Path, *, demo_mode: bool = True) -> list[EobLine]:
    """Dispatch on file type. PDF uses the synthetic layout parser."""
    text = _read_text(path)
    assert_no_phi(text, demo_mode=demo_mode)
    suffix = path.suffix.lower()
    if suffix == ".json":
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("resourceType") == "ExplanationOfBenefit":
            return ingest_fhir(payload)
        return [EobLine.model_validate(item) for item in payload.get("lines", payload)]
    if suffix == ".csv":
        return ingest_csv(path, demo_mode=demo_mode)
    return ingest_text(text, source=path.name)


def ingest_fhir(resource: dict[str, Any]) -> list[EobLine]:
    """Map a FHIR ExplanationOfBenefit item list onto EobLine."""
    lines = []
    provider = ""
    provider_ref = resource.get("provider") or {}
    if isinstance(provider_ref, dict):
        provider = str(provider_ref.get("identifier", {}).get("value") or "")
    network = "in-network"
    for ext in resource.get("extension", []):
        if str(ext.get("url", "")).endswith("network") and ext.get("valueCode"):
            network = str(ext["valueCode"])
    claim_id = str(resource.get("id") or "")
    for index, item in enumerate(resource.get("item", []), start=1):
        code = ""
        for coding in item.get("productOrService", {}).get("coding", []):
            code = str(coding.get("code") or code)
        adjudication = {
            str(adj.get("category", {}).get("coding", [{}])[0].get("code")): adj.get(
                "amount", {}
            ).get("value")
            for adj in item.get("adjudication", [])
        }
        denials = []
        for adj in item.get("adjudication", []):
            reason = adj.get("reason", {}).get("coding", [])
            if reason:
                denials.append(str(reason[0].get("code")))
        lines.append(
            EobLine(
                line_no=index,
                service_date=str(item.get("servicedDate") or "")[:10] or None,
                code=code or None,
                modifiers=[
                    str(mod.get("coding", [{}])[0].get("code"))
                    for mod in item.get("modifier", [])
                    if mod
                ],
                units=float(item.get("quantity", {}).get("value") or 1),
                billed=_money(adjudication.get("submitted") or item.get("net", {}).get("value")),
                allowed=_money(adjudication.get("eligible") or adjudication.get("allowed")),
                paid=_money(adjudication.get("benefit")),
                patient_resp=_money(adjudication.get("copay") or adjudication.get("patient")),
                provider_npi=provider or None,
                network_status=network,
                denial_codes=[code for code in denials if code and code != "None"],
                claim_id=claim_id,
                provenance={"source": "fhir", "item": str(index)},
            )
        )
    return lines


def ingest_csv(path: Path, *, demo_mode: bool = True) -> list[EobLine]:
    """Manual entry template. Columns match EobLine field names."""
    text = path.read_text(encoding="utf-8")
    assert_no_phi(text, demo_mode=demo_mode)
    lines = []
    for index, row in enumerate(csv.DictReader(text.splitlines()), start=1):
        denials = [part for part in (row.get("denial_codes") or "").split(";") if part]
        lines.append(
            EobLine(
                line_no=int(row.get("line_no") or index),
                service_date=row.get("service_date") or None,
                code=row.get("code") or None,
                units=_money(row.get("units")),
                billed=_money(row.get("billed")),
                allowed=_money(row.get("allowed")),
                paid=_money(row.get("paid")),
                patient_resp=_money(row.get("patient_resp")),
                provider_npi=row.get("provider_npi") or None,
                network_status=row.get("network_status") or None,
                denial_codes=denials,
                provenance={"source": path.name, "row": str(index)},
            )
        )
    return lines


def ingest_text(text: str, source: str = "upload") -> list[EobLine]:
    """Parse the synthetic pipe-delimited EOB layout."""
    claim = _field(text, "Claim")
    npi = _field(text, "Provider NPI")
    network = _field(text, "Network") or "in-network"
    lines = []
    for match in LINE_RE.finditer(text):
        denial = match.group("denial").strip()
        line_no = int(match.group("line"))
        lines.append(
            EobLine(
                line_no=line_no,
                service_date=match.group("date"),
                code=match.group("code"),
                units=float(match.group("units")),
                billed=float(match.group("billed")),
                allowed=float(match.group("allowed")),
                paid=float(match.group("paid")),
                patient_resp=float(match.group("patient")),
                provider_npi=npi,
                network_status=network,
                denial_codes=[denial] if denial else [],
                claim_id=claim,
                provenance={
                    "source": source,
                    "page": "1",
                    "span": f"{match.start()}:{match.end()}",
                    "line_no": str(line_no),
                },
            )
        )
    for line in lines:
        if line.allowed is None or line.code is None:
            line.needs_review.append("missing field")
    return lines


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        import pdfplumber

        parts = []
        with pdfplumber.open(path) as document:
            for page in document.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)
    return path.read_text(encoding="utf-8", errors="ignore")


def _field(text: str, label: str) -> str | None:
    match = re.search(rf"{label}:\s*(\S+)", text)
    return match.group(1) if match else None


def _money(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
