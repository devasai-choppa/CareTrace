"""Runtime configuration loaded from the environment and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings. Secrets come from the environment, never from source code."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("data")
    warehouse: Literal["duckdb", "snowflake", "databricks"] = "duckdb"
    duckdb_path: Path = Path("data/caretrace.duckdb")
    llm_provider: Literal["groq", "anthropic", "openai", "mock"] = "mock"
    llm_model: str = "llama-3.1-8b-instant"
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    nppes_api_base: str = "https://npiregistry.cms.hhs.gov/api/"
    demo_mode: bool = True
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    caretrace_api_url: str = "http://localhost:8000"
    max_upload_mb: int = 15
    dp_epsilon_budget: float = 1.0
    dp_epsilon_per_release: float = 0.1
    k_anonymity: int = 11
    user_agent: str = Field(
        default="CareTrace/0.1 (hospital price transparency research; demo@caretrace.local)"
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def bronze_dir(self) -> Path:
        return self.data_dir / "bronze"

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def graph_path(self) -> Path:
        return self.data_dir / "caretrace.kuzu"

    @property
    def session_db(self) -> Path:
        return self.data_dir / "sessions.sqlite"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings. Tests call `get_settings.cache_clear()` after env changes."""
    return Settings()
