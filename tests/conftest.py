"""Shared fixtures. Each test gets its own DuckDB file."""

from __future__ import annotations

from pathlib import Path

import pytest
from caretrace.config import Settings, get_settings


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("DUCKDB_PATH", str(data / "caretrace.duckdb"))
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("WAREHOUSE", "duckdb")
    get_settings.cache_clear()
    return get_settings()
