"""Load and validate config/sources.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class HospitalSource(BaseModel):
    """One hospital price-transparency publisher."""

    source_id: str
    name: str
    city: str
    state: str
    website_root: str = ""
    mrf_url_override: str | None = None


class PayerSource(BaseModel):
    """One canonical payer and the public artifacts we know about."""

    payer_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    policy_urls: list[str] = Field(default_factory=list)
    provider_directory_api: str | None = None


class SourceRegistry(BaseModel):
    """Hospitals and payers the pipeline is allowed to touch."""

    hospitals: list[HospitalSource]
    payers: list[PayerSource]

    def hospital(self, source_id: str) -> HospitalSource | None:
        return next((item for item in self.hospitals if item.source_id == source_id), None)

    def payer(self, payer_id: str) -> PayerSource | None:
        return next((item for item in self.payers if item.payer_id == payer_id), None)


def load_registry(path: Path | None = None) -> SourceRegistry:
    """Parse the source registry. Raises ValidationError on a bad document."""
    registry_path = path or Path("config/sources.yaml")
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{registry_path} must be a mapping with hospitals and payers")
    return SourceRegistry.model_validate(raw)
