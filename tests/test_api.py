"""HTTP contract of POST /search and GET /patients/{id}."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app, reset_services
from app.contracts import PatientDetail, SearchResponse
from app.fixtures import load_fixtures
from app.search.index import build_index

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


@pytest.fixture
def client(tmp_path):
    # conftest already points DB_PATH / CHROMA_PATH at tmp_path
    load_fixtures(FIXTURES, tmp_path / "test.db")
    build_index(tmp_path / "test.db", tmp_path / "chroma")
    reset_services()
    with TestClient(app) as c:
        yield c
    reset_services()


def test_health_reports_index_size(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["indexed_chunks"] > 0
    assert body["patients"] == 6


def test_search_happy_path(client):
    r = client.post("/search", json={"query": "poorly controlled blood sugar"})
    assert r.status_code == 200
    body = SearchResponse.model_validate(r.json())
    assert len(body.results) == 5
    assert body.results[0].patient_id == "p-0002"


def test_search_filters_travel_as_query_params(client):
    r = client.post(
        "/search?resource_type=DiagnosticReport&date_from=2025-01-01&date_to=2025-12-31",
        json={"query": "chest x-ray", "top_k": 10},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results
    assert all(x["resource_type"] == "DiagnosticReport" for x in results)
    assert all(x["date"].startswith("2025") for x in results)


def test_search_validation(client):
    assert client.post("/search", json={"query": ""}).status_code == 422
    assert client.post("/search", json={"query": "x", "top_k": 0}).status_code == 422
    assert client.post("/search", json={"query": "x", "top_k": 21}).status_code == 422
    assert client.post("/search?resource_type=Patient", json={"query": "x"}).status_code == 422
    assert client.post("/search?date_from=nope", json={"query": "x"}).status_code == 422
    r = client.post("/search?date_from=2025-06-01&date_to=2025-01-01", json={"query": "x"})
    assert r.status_code == 422


def test_patient_detail(client):
    r = client.get("/patients/p-0003")
    assert r.status_code == 200
    detail = PatientDetail.model_validate(r.json())
    assert detail.patient_name == "Daniel Haile"
    assert detail.summary is not None
    assert detail.summary.disclaimer.startswith("AI-generated")
    assert any(x.cited for x in detail.resources)


def test_patient_not_found(client):
    assert client.get("/patients/nobody").status_code == 404


def test_reload_rebuilds_service(client):
    assert client.post("/reload").json() == {"status": "reloaded"}
    assert client.get("/health").json()["indexed_chunks"] > 0
