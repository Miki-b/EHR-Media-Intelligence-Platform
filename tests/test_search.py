"""Hybrid search and patient detail over the indexed fixtures (hash embedder)."""

from datetime import date
from pathlib import Path

import pytest

from app.fixtures import load_fixtures
from app.search.index import build_index
from app.search.rank import Candidate, collapse_by_resource, normalise, snippet
from app.search.service import SearchFilters, SearchService

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    root = tmp_path_factory.mktemp("search")
    db, chroma = root / "ehr.db", root / "chroma"
    load_fixtures(FIXTURES, db)
    build_index(db, chroma)
    return SearchService(db, chroma)


# -- rank helpers -----------------------------------------------------------


def test_normalise_scales_to_unit_and_handles_zeros():
    assert normalise([2.0, 1.0, 0.0]) == [1.0, 0.5, 0.0]
    assert normalise([0.0, 0.0]) == [0.0, 0.0]
    assert normalise([]) == []


def test_collapse_keeps_best_window_per_resource():
    a0 = Candidate("r/a#0", "", {"resource_id": "r/a"}, vector=0.2).blend()
    a1 = Candidate("r/a#1", "", {"resource_id": "r/a"}, vector=0.9).blend()
    b0 = Candidate("r/b#0", "", {"resource_id": "r/b"}, vector=0.5).blend()
    out = collapse_by_resource([a0, b0, a1])
    assert [c.chunk_id for c in out] == ["r/a#1", "r/b#0"]


def test_snippet_picks_sentence_with_query_terms():
    text = "Header line. Lungs clear. Dense consolidation in the right lower lobe. Heart normal."
    s = snippet(text, "CXR showing consolidation", limit=60)
    assert s.startswith("Dense consolidation")
    assert len(s) <= 60


# -- search -----------------------------------------------------------------


def test_search_returns_contract_shaped_results(service):
    resp = service.search("elevated HbA1c", top_k=5)
    assert isinstance(resp.took_ms, int)
    assert 1 <= len(resp.results) <= 5
    scores = [r.score for r in resp.results]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 1 for s in scores)
    assert len({r.resource_id for r in resp.results}) == len(resp.results)
    top = resp.results[0]
    assert top.patient_id == "p-0002"
    assert top.mrn == "MRN-000102"
    assert top.patient_name == "Sara Tesfaye"
    assert top.summary_snippet and "diabetes" in top.summary_snippet.lower()
    assert top.snippet


def test_search_finds_consolidation_report(service):
    resp = service.search("CXR showing consolidation", top_k=3)
    assert resp.results[0].patient_id == "p-0003"
    assert resp.results[0].resource_type == "DiagnosticReport"
    assert "consolidation" in resp.results[0].snippet.lower()


def test_resource_type_filter(service):
    for rtype in ("DocumentReference", "DiagnosticReport", "Summary"):
        resp = service.search("heart failure", top_k=10, filters=SearchFilters(resource_type=rtype))
        assert resp.results, rtype
        assert {r.resource_type for r in resp.results} == {rtype}


def test_date_range_filter(service):
    everything = service.search("kidney function creatinine", top_k=10)
    assert any(r.patient_id == "p-0006" for r in everything.results)

    only_2025 = service.search(
        "kidney function creatinine",
        top_k=10,
        filters=SearchFilters(date_from=date(2025, 1, 1), date_to=date(2025, 12, 31)),
    )
    assert only_2025.results
    assert all(r.date and r.date.year == 2025 for r in only_2025.results)
    assert not any(r.patient_id == "p-0006" for r in only_2025.results)


def test_top_k_is_respected(service):
    assert len(service.search("note", top_k=2).results) == 2
    assert len(service.search("note", top_k=20).results) <= 20


def test_nonsense_query_still_returns_valid_response(service):
    resp = service.search("zzqx plorf", top_k=5)
    assert all(0 <= r.score <= 1 for r in resp.results)


def test_search_latency_budget(service):
    resp = service.search("patients discharged after heart failure")
    assert resp.took_ms < 2000


# -- patient detail ---------------------------------------------------------


def test_patient_detail_with_summary_and_cited_resources(service):
    detail = service.patient_detail("p-0001")
    assert detail is not None
    assert detail.patient_name == "Abebe Kebede"
    assert detail.mrn == "MRN-000101"
    assert detail.dob == date(1961, 5, 14)
    assert detail.gender == "male"
    assert detail.summary and detail.summary.chief_concern
    types = {r.resource_type for r in detail.resources}
    assert {"DocumentReference", "DiagnosticReport", "Observation", "Encounter"} <= types
    cited = {r.resource_id for r in detail.resources if r.cited}
    assert cited == set(detail.summary.citations)
    dates = [r.date for r in detail.resources if r.date]
    assert dates == sorted(dates, reverse=True)


def test_patient_detail_unknown_is_none(service):
    assert service.patient_detail("p-9999") is None


def test_empty_index_gives_empty_results(tmp_path):
    db, chroma = tmp_path / "ehr.db", tmp_path / "chroma"
    load_fixtures(FIXTURES, db)  # patients exist, nothing indexed
    svc = SearchService(db, chroma)
    assert svc.size == 0
    assert svc.search("anything").results == []
    assert svc.patient_detail("p-0001") is not None
