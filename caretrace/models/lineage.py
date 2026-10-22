"""Lineage primitives. Every user-visible number should hang off one of these."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def row_hash(row: dict[str, Any]) -> str:
    """Stable SHA-256 of a row's canonical JSON (keys sorted, compact separators)."""
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Lineage(BaseModel):
    """Where a value came from, plus the method that produced a derived value."""

    source_id: str
    source_file_uri: str
    source_row_hash: str
    ingested_at: datetime
    parser_version: str
    method: str | None = None
    parameters: dict[str, Any] | None = None

    def log_payload(self) -> dict[str, Any]:
        """JSON-ready lineage, including ML method and parameters when present."""
        payload = self.model_dump(mode="json")
        return payload


class Cited(BaseModel, Generic[T]):
    """A value that cannot be shown without lineage, confidence, and reasons."""

    value: T
    lineage: list[Lineage] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    confidence_reasons: list[str] = Field(default_factory=list)


def now_utc() -> datetime:
    """Timezone-aware timestamp for ingest events."""
    return datetime.now(UTC)
