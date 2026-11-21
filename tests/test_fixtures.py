"""The committed fixtures must stay valid FHIR R4 and match app/contracts.py.

Search, the API and the UI are built against these files, so a drift here would
hide a drift from the pipeline's real output.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import ROOT
from app.contracts import ClinicalSummary
from app.fixtures import content_hash, load_fixtures

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
BUNDLES = sorted((FIXTURES / "bundles").glob("*.json"))


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resource_ids(bundle: dict) -> set[str]:
    return {f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in bundle["entry"]}


def test_fixture_set_is_present():
    assert len(BUNDLES) >= 5
    for path in BUNDLES:
        assert (FIXTURES / "summaries" / path.name).exists(), f"no summary for {path.stem}"


@pytest.mark.parametrize("path", BUNDLES, ids=lambda p: p.stem)
def test_bundle_is_valid_fhir_r4(path):
    from fhir.resources.R4B.bundle import Bundle

    bundle = Bundle.model_validate(read(path))
    assert bundle.type == "collection"
    types = {e.resource.get_resource_type() for e in bundle.entry}
    assert {"Patient", "DocumentReference", "DiagnosticReport"} <= types


@pytest.mark.parametrize("path", BUNDLES, ids=lambda p: p.stem)
def test_bundle_references_resolve_inside_bundle(path):
    bundle = read(path)
    full_urls = {e["fullUrl"] for e in bundle["entry"]}
    for entry in bundle["entry"]:
        text = json.dumps(entry["resource"])
        for ref in {m for m in text.split('"') if m.startswith("urn:uuid:")}:
            assert ref in full_urls, f"{path.stem}: dangling reference {ref}"


@pytest.mark.parametrize("path", BUNDLES, ids=lambda p: p.stem)
def test_summary_matches_contract_and_cites_real_resources(path):
    summary = ClinicalSummary.model_validate(read(FIXTURES / "summaries" / path.name))
    ids = resource_ids(read(path))
    assert summary.patient_id == path.stem
    assert summary.word_count <= 200
    assert summary.citations, "summary has no citations"
    assert set(summary.citations) <= ids
    assert {m.resource_id for m in summary.recent_media} <= ids


def test_loader_fills_patients_bundles_and_summaries(tmp_path):
    db = tmp_path / "fixtures.db"
    assert load_fixtures(FIXTURES, db) == len(BUNDLES)
    assert load_fixtures(FIXTURES, db) == len(BUNDLES), "loader must be idempotent"

    with sqlite3.connect(db) as conn:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("patients", "bundles", "summaries")
        }
        assert counts == {t: len(BUNDLES) for t in counts}

        row = conn.execute(
            "SELECT p.mrn, p.full_name, p.gender, b.content_hash, s.content_hash"
            " FROM patients p JOIN bundles b USING (patient_id)"
            " JOIN summaries s USING (patient_id) WHERE p.patient_id = 'p-0001'"
        ).fetchone()
    assert row[0].startswith("MRN-")
    assert row[1] == "Abebe Kebede"
    assert row[2] == "male"
    assert row[3] == row[4], "summary must be keyed by the bundle it was generated from"
    assert row[3] == content_hash(read(FIXTURES / "bundles" / "p-0001.json"))


def test_fixture_mode_uses_its_own_paths(monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("DB_PATH")
    monkeypatch.delenv("CHROMA_PATH")
    monkeypatch.setenv("FIXTURE_MODE", "1")
    get_settings.cache_clear()
    s = get_settings()
    assert s.db_path.name == "fixtures.db"
    assert s.chroma_path.name == "chroma-fixtures"

    monkeypatch.setenv("DB_PATH", "elsewhere.db")
    get_settings.cache_clear()
    assert get_settings().db_path == Path("elsewhere.db"), "explicit DB_PATH wins"

    monkeypatch.setenv("FIXTURE_MODE", "0")
    monkeypatch.delenv("DB_PATH")
    get_settings.cache_clear()
    assert get_settings().db_path.name == "ehr.db"


@pytest.mark.parametrize(
    ("db_path", "chroma_path"),
    [
        ("data/ehr.db", "data/chroma"),  # what a .env copied from .env.example used to set
        (str(ROOT / "data" / "ehr.db"), str(ROOT / "data" / "chroma")),  # same, absolute
    ],
)
def test_fixture_mode_ignores_paths_that_restate_the_default(db_path, chroma_path, monkeypatch):
    from app.config import Settings

    for var in ("DB_PATH", "CHROMA_PATH"):  # conftest points these at tmp
        monkeypatch.delenv(var, raising=False)

    s = Settings(_env_file=None, fixture_mode=True, db_path=db_path, chroma_path=chroma_path)
    assert s.db_path.name == "fixtures.db"
    assert s.chroma_path.name == "chroma-fixtures"


def test_fixture_mode_keeps_a_deliberate_path(monkeypatch):
    from app.config import Settings

    for var in ("DB_PATH", "CHROMA_PATH"):  # conftest points these at tmp
        monkeypatch.delenv(var, raising=False)

    s = Settings(_env_file=None, fixture_mode=True, db_path="elsewhere.db")
    assert s.db_path == Path("elsewhere.db")
    assert s.chroma_path.name == "chroma-fixtures"


def test_pipeline_mode_keeps_the_default_paths(monkeypatch):
    from app.config import Settings

    for var in ("DB_PATH", "CHROMA_PATH"):  # conftest points these at tmp
        monkeypatch.delenv(var, raising=False)

    s = Settings(_env_file=None, db_path="data/ehr.db")
    assert s.db_path == Path("data/ehr.db")
