"""Runtime settings, read from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DB = ROOT / "data" / "ehr.db"
PIPELINE_CHROMA = ROOT / "data" / "chroma"


def _is_pipeline_default(path: Path, default: Path) -> bool:
    """True when `path` just restates the pipeline default (absolute or repo-relative), as a
    .env copied from .env.example does; such a value is not a deliberate override."""
    candidate = path if path.is_absolute() else ROOT / path
    return candidate.resolve() == default.resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_provider: Literal["anthropic", "openai", "mock"] = "mock"
    llm_model: str = "claude-haiku-5-5"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # FIXTURE_MODE=1 keeps the committed fixture patients in their own database and
    # vector index, so they never collide with pipeline output (ids overlap: p-0001...).
    fixture_mode: bool = False
    db_path: Path = PIPELINE_DB
    chroma_path: Path = PIPELINE_CHROMA
    fixture_db_path: Path = ROOT / "data" / "fixtures.db"
    fixture_chroma_path: Path = ROOT / "data" / "chroma-fixtures"
    raw_dir: Path = ROOT / "data" / "raw"
    rejected_path: Path = ROOT / "data" / "rejected.jsonl"
    summary_cache_path: Path = ROOT / "data" / "cache" / "summaries.jsonl"
    fixtures_dir: Path = ROOT / "data" / "fixtures"
    reports_dir: Path = ROOT / "reports"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # torch: sentence-transformers (default). onnx: the same model through fastembed /
    # onnxruntime, ~1 GB less memory, for small hosts. EMBED_MODEL=hash ignores this.
    embed_backend: Literal["torch", "onnx"] = "torch"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _apply_fixture_mode(self) -> "Settings":
        """In fixture mode, use the fixture paths unless a path was deliberately set
        elsewhere. A value equal to the pipeline default (e.g. DB_PATH=data/ehr.db from a
        .env copied from .env.example) is not deliberate: honouring it would load the demo
        patients into the pipeline database, whose patient ids overlap."""
        if self.fixture_mode:
            if _is_pipeline_default(self.db_path, PIPELINE_DB):
                self.db_path = self.fixture_db_path
            if _is_pipeline_default(self.chroma_path, PIPELINE_CHROMA):
                self.chroma_path = self.fixture_chroma_path
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
