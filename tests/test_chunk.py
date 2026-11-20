"""Chunking: bundles and summaries become searchable text with the right metadata."""

import base64
import json
from datetime import date
from pathlib import Path

import pytest

from app.contracts import ClinicalSummary
from app.search.chunk import (
    attachment_text,
    chunk_bundle,
    chunk_summary,
    parse_fhir_date,
    split_words,
)

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def load(kind: str, pid: str) -> dict:
    return json.loads((FIXTURES / kind / f"{pid}.json").read_text(encoding="utf-8"))


def test_only_documents_and_reports_become_chunks():
    chunks = chunk_bundle("p-0002", load("bundles", "p-0002"))
    assert {c.resource_type for c in chunks} == {"DocumentReference", "DiagnosticReport"}
    # p-0002 has one note and one lab panel; its two Observations are folded, not indexed
    assert len(chunks) == 2
    assert all(c.patient_id == "p-0002" for c in chunks)
    assert all(c.chunk_id == f"{c.resource_id}#0" for c in chunks)


def test_report_chunk_folds_in_observation_values():
    chunks = chunk_bundle("p-0002", load("bundles", "p-0002"))
    report = next(c for c in chunks if c.resource_type == "DiagnosticReport")
    assert "Hemoglobin A1c" in report.text
    assert "9.8 %" in report.text
    assert "High" in report.text
    assert "reference 4 %-5.6 %" in report.text
    assert "Conclusion:" in report.text
    assert report.date == date(2025, 3, 14)
    assert report.title == "Hemoglobin A1c panel"


def test_document_chunk_decodes_attachment_and_keeps_title():
    chunks = chunk_bundle("p-0002", load("bundles", "p-0002"))
    doc = next(c for c in chunks if c.resource_type == "DocumentReference")
    assert doc.title.startswith("Endocrinology progress note")
    assert "poorly controlled blood sugar" in doc.text
    assert "missing metformin" in doc.text
    assert doc.date == date(2025, 3, 14)


def test_every_fixture_citation_has_a_chunk():
    for path in sorted((FIXTURES / "bundles").glob("*.json")):
        pid = path.stem
        ids = {c.resource_id for c in chunk_bundle(pid, load("bundles", pid))}
        summary = ClinicalSummary.model_validate(load("summaries", pid))
        assert set(summary.citations) <= ids, f"{pid}: citation without a chunk"


def test_summary_chunk():
    summary = ClinicalSummary.model_validate(load("summaries", "p-0001"))
    chunks = chunk_summary(summary)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.resource_id == "Summary/p-0001"
    assert c.resource_type == "Summary"
    assert "heart failure" in c.text.lower()
    assert "BNP 1480" in c.text
    assert c.date == date(2025, 2, 9), "summary dated by its most recent record"


def test_document_without_inline_data_falls_back_to_description():
    bundle = {
        "entry": [
            {
                "fullUrl": "urn:uuid:d1",
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": "d1",
                    "status": "current",
                    "type": {"text": "Radiology note"},
                    "description": "Scanned imaging note",
                    "date": "2024-01",
                    "content": [{"attachment": {"contentType": "application/pdf", "url": "x"}}],
                },
            }
        ]
    }
    (chunk,) = chunk_bundle("p-x", bundle)
    assert chunk.text.startswith("Scanned imaging note (Radiology note)")
    assert chunk.date == date(2024, 1, 1)


def test_long_text_splits_into_overlapping_windows():
    text = " ".join(f"w{i}" for i in range(400))
    windows = split_words(text, max_words=180, overlap=30)
    assert len(windows) == 3
    assert windows[0].split()[-30:] == windows[1].split()[:30]
    assert windows[-1].split()[-1] == "w399"
    bundle = {
        "entry": [
            {
                "fullUrl": "urn:uuid:d2",
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": "d2",
                    "status": "current",
                    "description": "Long note",
                    "content": [
                        {
                            "attachment": {
                                "contentType": "text/plain",
                                "data": base64.b64encode(text.encode()).decode(),
                            }
                        }
                    ],
                },
            }
        ]
    }
    chunks = chunk_bundle("p-x", bundle)
    assert [c.chunk_id for c in chunks] == [f"DocumentReference/d2#{i}" for i in range(3)]
    assert len({c.resource_id for c in chunks}) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-03-14", date(2025, 3, 14)),
        ("2025-03-14T12:00:00Z", date(2025, 3, 14)),
        ("2025-03", date(2025, 3, 1)),
        ("2025", date(2025, 1, 1)),
        ("not a date", None),
        (None, None),
    ],
)
def test_parse_fhir_date(value, expected):
    assert parse_fhir_date(value) == expected


def test_attachment_text_ignores_binary_and_bad_base64():
    assert attachment_text({"contentType": "application/pdf", "data": "AAAA"}) is None
    assert attachment_text({"contentType": "text/plain", "data": "@@not-base64@@"}) is None
    assert attachment_text({"contentType": "text/plain", "data": "aGVsbG8="}) == "hello"
    assert attachment_text(None) is None
