"""Task 2 tests: FHIR mapping and bundles, built from the seed-42 data via Task 1.

The shape checks mirror what Dev B's search chunker reads (agreed 2026-11-21): document
text in content[].attachment.data, lab values reachable through a LAB DiagnosticReport's
result[], numeric referenceRange low/high, and no reference that fails to resolve.
"""

import base64
import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from fhir.resources.R4B.bundle import Bundle

from app.contracts import CanonicalRecord
from app.fhir import mappers
from app.fhir.run import build_fhir
from app.ingest.normalizers import record_id
from app.ingest.run import ingest

TODAY = date(2026, 6, 1)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_data.py"


def _generator():
    spec = importlib.util.spec_from_file_location("generate_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("fhir")
    _generator().generate(42, root / "raw")
    ingest(root / "raw", root / "ehr.db", root / "rejected.jsonl", today=TODAY)
    result = build_fhir(root / "ehr.db", root / "validation.md")
    truth = json.loads((root / "raw" / "ground_truth.json").read_text(encoding="utf-8"))
    return {"root": root, "db": root / "ehr.db", "result": result, "truth": truth}


def _rows(db: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def _bundles(run) -> dict[str, dict]:
    rows = _rows(run["db"], "SELECT patient_id, bundle_json FROM bundles")
    return {pid: json.loads(b) for pid, b in rows}


def _resources(bundle: dict, rtype: str | None = None) -> list[dict]:
    found = [e["resource"] for e in bundle["entry"]]
    return [r for r in found if rtype is None or r["resourceType"] == rtype]


def _records(run) -> list[CanonicalRecord]:
    rows = _rows(run["db"], "SELECT record_json FROM records")
    return [CanonicalRecord.model_validate_json(r[0]) for r in rows]


def _decode(data: str) -> str:
    return base64.b64decode(data).decode("utf-8")


def test_one_valid_bundle_per_patient(run):
    bundles = _bundles(run)
    assert len(bundles) == 18
    assert {r[0] for r in _rows(run["db"], "SELECT is_valid FROM bundles")} == {1}
    for bundle in bundles.values():
        Bundle.model_validate(bundle)  # stored JSON is still valid R4
        assert bundle["type"] == "collection"
        assert len(_resources(bundle, "Patient")) == 1


def test_every_reference_resolves_inside_its_bundle(run):
    for pid, bundle in _bundles(run).items():
        urls = {e["fullUrl"] for e in bundle["entry"]}
        assert all(e["fullUrl"] == f"urn:uuid:{e['resource']['id']}" for e in bundle["entry"])
        for resource in _resources(bundle):
            refs = [resource.get("subject", {}).get("reference")]
            refs += [r["reference"] for r in resource.get("result", [])]
            for reference in filter(None, refs):
                assert reference in urls, (pid, resource["resourceType"], reference)


def test_every_kept_record_becomes_one_resource(run):
    records, bundles = _records(run), _bundles(run)
    ids = {r["id"] for b in bundles.values() for r in _resources(b)}
    fhir_failure = record_id(run["truth"]["fhir_failure"])
    for record in records:
        rtype = {"lab": "Observation", "imaging": "DiagnosticReport"}.get(
            record.kind, "DocumentReference"
        )
        rid = mappers.resource_id(rtype, record.record_id)
        assert (rid in ids) == (record.record_id != fhir_failure), record.record_id


def test_seeded_fhir_failure_is_the_only_issue(run):
    obs_id = mappers.resource_id("Observation", record_id(run["truth"]["fhir_failure"]))
    issues = _rows(run["db"], "SELECT resource_type, resource_id, path FROM validation_errors")
    assert issues == [("Observation", f"Observation/{obs_id}", "code")]
    report = (run["root"] / "validation.md").read_text(encoding="utf-8")
    assert f"Observation/{obs_id}" in report and "18 valid" in report


def test_report_has_no_patient_names(run):
    report = (run["root"] / "validation.md").read_text(encoding="utf-8")
    for patient in run["truth"]["patients"]:
        assert patient["family_name"] not in report


def test_document_text_is_a_base64_attachment(run):
    texts = {r.record_id: r.text for r in _records(run) if r.kind in ("document", "note")}
    by_id = {mappers.resource_id("DocumentReference", rid): text for rid, text in texts.items()}
    docs = [d for b in _bundles(run).values() for d in _resources(b, "DocumentReference")]
    assert len(docs) == len(texts)
    for doc in docs:
        attachment = doc["content"][0]["attachment"]
        assert attachment["contentType"] == "text/plain"
        assert _decode(attachment["data"]) == by_id[doc["id"]]
        assert doc["description"]
        if "date" in doc:
            assert doc["date"].endswith("T12:00:00Z")


def test_lab_values_reach_search_through_a_lab_report(run):
    for bundle in _bundles(run).values():
        observations = {f"urn:uuid:{o['id']}": o for o in _resources(bundle, "Observation")}
        lab_reports = [
            r
            for r in _resources(bundle, "DiagnosticReport")
            if r["category"][0]["coding"][0]["code"] == "LAB"
        ]
        referenced = [ref["reference"] for r in lab_reports for ref in r["result"]]
        assert sorted(referenced) == sorted(observations)  # each Observation exactly once


def test_observations_are_coded_with_numeric_values_and_ranges(run):
    labs = {
        mappers.resource_id("Observation", r.record_id): r for r in _records(run) if r.kind == "lab"
    }
    observations = [o for b in _bundles(run).values() for o in _resources(b, "Observation")]
    assert observations
    for obs in observations:
        record = labs[obs["id"]]
        assert obs["code"]["coding"][0] == {
            "system": mappers.LOINC,
            "code": record.code,
            "display": mappers.LOINC_DISPLAY[record.code],
        }
        quantity = obs["valueQuantity"]
        assert quantity["value"] == float(record.value)
        assert quantity["system"] == mappers.UCUM and quantity["unit"] == record.unit
        (ref_range,) = obs["referenceRange"]
        low, high = (float(x) for x in record.ref_range.split("-"))
        assert (ref_range["low"]["value"], ref_range["high"]["value"]) == (low, high)
        assert obs["interpretation"][0]["coding"][0]["code"] == record.flag


def test_ucum_code_differs_from_display_unit_where_needed(run):
    wbc = [
        o
        for b in _bundles(run).values()
        for o in _resources(b, "Observation")
        if o["valueQuantity"]["unit"] == "10^3/uL"
    ]
    assert wbc and all(o["valueQuantity"]["code"] == "10*3/uL" for o in wbc)


def test_imaging_reports_carry_conclusion_and_full_text(run):
    imaging = {r.record_id: r for r in _records(run) if r.kind == "imaging"}
    by_id = {mappers.resource_id("DiagnosticReport", rid): rec for rid, rec in imaging.items()}
    reports = [
        r
        for b in _bundles(run).values()
        for r in _resources(b, "DiagnosticReport")
        if r["id"] in by_id
    ]
    assert len(reports) == len(imaging)
    for report in reports:
        assert report["category"][0]["coding"][0]["code"] == "RAD"
        assert _decode(report["presentedForm"][0]["data"]) == by_id[report["id"]].text
        assert report["conclusion"] and "IMPRESSION" not in report["conclusion"]


def test_patient_identifiers_and_demographics(run):
    bundles, truth = _bundles(run), run["truth"]
    owner = dict(_rows(run["db"], "SELECT record_id, patient_id FROM records"))
    for patient in truth["patients"]:
        pid = owner[record_id(patient["records"][0])]
        (resource,) = _resources(bundles[pid], "Patient")
        assert resource["id"] == mappers.resource_id("Patient", pid)
        mrns = [i["value"] for i in resource["identifier"]]
        assert sorted(mrns) == patient["mrns_normalized"]
        assert {i["system"] for i in resource["identifier"]} == {mappers.MRN_SYSTEM}
        assert resource.get("birthDate") == patient["dob"]
        assert resource["gender"] == patient["gender"]
        assert resource["name"] == [
            {"family": patient["family_name"], "given": patient["given_name"].split()}
        ]


def test_rerun_is_deterministic(run, tmp_path):
    db = tmp_path / "copy.db"
    with sqlite3.connect(run["db"]) as src, sqlite3.connect(db) as dst:
        src.backup(dst)
    for _ in range(2):
        build_fhir(db, tmp_path / "validation.md")
    sql = "SELECT patient_id, bundle_json, content_hash, is_valid FROM bundles ORDER BY 1"
    assert _rows(db, sql) == _rows(run["db"], sql)
    assert _rows(db, "SELECT * FROM validation_errors") == _rows(
        run["db"], "SELECT * FROM validation_errors"
    )


def test_content_hash_matches_shared_definition(run):
    for bundle_json, digest in _rows(run["db"], "SELECT bundle_json, content_hash FROM bundles"):
        canonical = json.dumps(json.loads(bundle_json), sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(canonical.encode()).hexdigest() == digest


# ---------------------------------------------------------------------------
# Mapper edge cases
# ---------------------------------------------------------------------------


def _lab(**fields) -> CanonicalRecord:
    base = {
        "record_id": "r-test",
        "patient_id": "p-9999",
        "kind": "lab",
        "title": "Ferritin",
        "text": "Ferritin",
        "source_file": "x.csv",
    }
    return CanonicalRecord(**(base | fields))


def test_non_numeric_lab_value_and_text_range():
    obs = mappers.observation(_lab(value="positive", ref_range="<0.04", unit="ng/mL"), "pid")
    assert obs["valueString"] == "positive" and "valueQuantity" not in obs
    assert obs["referenceRange"] == [{"text": "<0.04"}]


def test_named_test_without_loinc_gets_a_text_code():
    obs = mappers.observation(_lab(value="40", unit="ng/mL"), "pid")
    assert obs["code"] == {"text": "Ferritin"}
    assert obs["valueQuantity"] == {
        "value": 40.0,
        "unit": "ng/mL",
        "system": mappers.UCUM,
        "code": "ng/mL",
    }
