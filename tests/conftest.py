import pytest


@pytest.fixture(autouse=True)
def _offline_settings(monkeypatch, tmp_path):
    """Every test runs with the mock LLM and a throwaway database."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
