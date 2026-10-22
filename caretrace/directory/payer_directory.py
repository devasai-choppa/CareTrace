"""Payer provider-directory adapters. FHIR client plus an offline mock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field


class DirectoryEntry(BaseModel):
    """One payer directory listing."""

    payer_id: str
    npi: str
    name: str
    address: str = ""
    phone: str = ""
    taxonomy: str = ""
    last_updated: str = ""
    network_status: str = "in-network"
    in_mrf: bool = False
    npi_active: bool = True


class DirectoryAdapter(Protocol):
    """Search a payer directory."""

    def search(
        self, payer_id: str, *, npi: str | None = None, name: str | None = None
    ) -> list[DirectoryEntry]:
        """Return listings for a payer."""


class MockDirectory:
    """Fixture-backed directory used in demo mode and tests."""

    def __init__(
        self, entries: list[DirectoryEntry] | None = None, path: Path | None = None
    ) -> None:
        if entries is not None:
            self.entries = entries
        else:
            fixture = path or Path("data/samples/directory/bcbsma_providers.json")
            raw = json.loads(fixture.read_text(encoding="utf-8"))
            self.entries = [DirectoryEntry.model_validate(item) for item in raw]

    def search(
        self, payer_id: str, *, npi: str | None = None, name: str | None = None
    ) -> list[DirectoryEntry]:
        rows = [entry for entry in self.entries if entry.payer_id == payer_id]
        if npi:
            rows = [entry for entry in rows if entry.npi == npi]
        if name:
            needle = name.lower()
            rows = [entry for entry in rows if needle in entry.name.lower()]
        return rows


class FhirDirectory:
    """Minimal FHIR R4 reader for PractitionerRole, Practitioner, and Location."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def search(
        self, payer_id: str, *, npi: str | None = None, name: str | None = None
    ) -> list[DirectoryEntry]:
        params: dict[str, str] = {}
        if npi:
            params["practitioner.identifier"] = npi
        if name:
            params["practitioner.name"] = name
        response = self.client.get(f"{self.base_url}/PractitionerRole", params=params)
        response.raise_for_status()
        bundle = response.json()
        entries: list[DirectoryEntry] = []
        for item in bundle.get("entry", []):
            resource = item.get("resource", {})
            if resource.get("resourceType") != "PractitionerRole":
                continue
            npi_value = ""
            for identifier in resource.get("identifier", []):
                if str(identifier.get("value", "")).isdigit():
                    npi_value = str(identifier["value"])
            practitioner_name = ""
            for contained in resource.get("contained", []):
                if contained.get("resourceType") == "Practitioner":
                    names = contained.get("name", [{}])
                    practitioner_name = names[0].get("text", "")
            entries.append(
                DirectoryEntry(
                    payer_id=payer_id,
                    npi=npi_value,
                    name=practitioner_name,
                    network_status="in-network",
                )
            )
        return entries


class DirectoryRecord(BaseModel):
    """Alias kept for callers that want a pydantic list wrapper."""

    entries: list[DirectoryEntry] = Field(default_factory=list)


def fixture_entries() -> list[dict[str, Any]]:
    """Default Boston listings shipped with the demo."""
    return [
        {
            "payer_id": "bcbsma",
            "npi": "1234567893",
            "name": "Priya Shah",
            "address": "55 Fruit Street, Boston, MA 02114",
            "phone": "617-726-2000",
            "taxonomy": "Diagnostic Radiology",
            "last_updated": "2026-08-01",
            "network_status": "in-network",
            "in_mrf": True,
            "npi_active": True,
        },
        {
            "payer_id": "bcbsma",
            "npi": "1000000001",
            "name": "Ghost Imaging LLC",
            "address": "1 Nowhere Ave, Boston, MA 02115",
            "phone": "000-000-0000",
            "taxonomy": "General Practice",
            "last_updated": "2019-01-01",
            "network_status": "in-network",
            "in_mrf": False,
            "npi_active": False,
        },
    ]
