"""Runtime settings, read from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_provider: Literal["anthropic", "openai", "mock"] = "mock"
    llm_model: str = "claude-haiku-5-5"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # FIXTURE_MODE=1 keeps the committed fixture patients in their own database and
    # vector index, so they never collide with pipeline output (ids overlap: p-0001...).
    fixture_mode: bool = False
    db_path: Path = ROOT / "data" / "ehr.db"
    chroma_path: Path = ROOT / "data" / "chroma"
    fixture_db_path: Path = ROOT / "data" / "fixtures.db"
    fixture_chroma_path: Path = ROOT / "data" / "chroma-fixtures"
    raw_dir: Path = ROOT / "data" / "raw"
    rejected_path: Path = ROOT / "data" / "rejected.jsonl"
    fixtures_dir: Path = ROOT / "data" / "fixtures"
    reports_dir: Path = ROOT / "reports"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _apply_fixture_mode(self) -> "Settings":
        if self.fixture_mode:
            if "db_path" not in self.model_fields_set:
                self.db_path = self.fixture_db_path
            if "chroma_path" not in self.model_fields_set:
                self.chroma_path = self.fixture_chroma_path
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
