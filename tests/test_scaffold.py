"""Smoke tests that keep the shared scaffold honest."""

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts import DISCLAIMER, CanonicalPatient, ClinicalSummary, SearchRequest
from app.db import init_db


def test_health_endpoint():
    from app.api.main import app

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_schema_creates_all_tables(tmp_path):
    path = init_db(tmp_path / "x.db")
    with sqlite3.connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "patients",
        "records",
        "audit_log",
        "bundles",
        "validation_errors",
        "summaries",
    } <= tables


def test_patient_contract_rejects_bad_gender():
    with pytest.raises(ValidationError):
        CanonicalPatient(patient_id="p-0001", mrn="MRN-000001", gender="M")


def test_patient_full_name():
    p = CanonicalPatient(patient_id="p-1", mrn="MRN-1", given_name="Abebe", family_name="Kebede")
    assert p.full_name == "Abebe Kebede"
    assert p.dob is None


def test_summary_carries_disclaimer_by_default():
    s = ClinicalSummary(patient_id="p-0001", chief_concern="Shortness of breath")
    assert s.disclaimer == DISCLAIMER


def test_search_request_rejects_empty_query():
    with pytest.raises(ValidationError):
        SearchRequest(query="")


def test_dates_parse_from_iso_strings():
    p = CanonicalPatient.model_validate({"patient_id": "p-1", "mrn": "MRN-1", "dob": "1985-04-03"})
    assert p.dob == date(1985, 4, 3)
