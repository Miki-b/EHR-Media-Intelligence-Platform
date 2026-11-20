"""Task 1 tests: normalizers in isolation, then the whole pipeline against ground_truth.json.

ground_truth.json and DEFECTS.md are read here, as the oracle; the ingestion code never
reads them.
"""

import importlib.util
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from app.ingest import audit as rules
from app.ingest.normalizers import (
    clean_text,
    normalize_date,
    normalize_gender,
    normalize_mrn,
    normalize_name,
    record_id,
)
from app.ingest.run import ingest

TODAY = date(2026, 6, 1)  # fixed, so date checks do not drift with the calendar
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_data.py"


def _generator():
    spec = importlib.util.spec_from_file_location("generate_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "1985-04-03",
        "19850403",
        "3-Apr-85",
        "April 3, 1985",
        "04/03/1985",
        "1985-04-03T10:15:00",
        "04/03/1985 06:05",
        " 1985-04-03 ",
        "Apr 3, 1985",
    ],
)
def test_date_formats_give_same_iso_date(raw):
    value, _ = normalize_date(raw, today=TODAY)
    assert value == date(1985, 4, 3)


def test_ambiguous_slash_date_assumes_us_and_audits():
    value, entry = normalize_date("04/03/1985", today=TODAY)
    assert value == date(1985, 4, 3)
    assert entry.rule == rules.AMBIGUOUS_DATE_ASSUMED_US


def test_unambiguous_slash_dates():
    assert normalize_date("13/04/1985", today=TODAY)[0] == date(1985, 4, 13)  # DD/MM
    value, entry = normalize_date("04/13/1985", today=TODAY)
    assert value == date(1985, 4, 13) and entry.rule == rules.DATE_NORMALIZED
    assert normalize_date("05/05/1985", today=TODAY)[1].rule == rules.DATE_NORMALIZED


def test_iso_date_is_not_audited():
    assert normalize_date("1985-04-03", today=TODAY) == (date(1985, 4, 3), None)


def test_two_digit_year_pivots_away_from_the_future():
    assert normalize_date("28-Apr-90", today=TODAY)[0] == date(1990, 4, 28)
    assert normalize_date("13-Oct-23", today=TODAY)[0] == date(2023, 10, 13)


@pytest.mark.parametrize("raw", ["2032-02-19", "1899-12-31", "not a date", "02/30/2020"])
def test_invalid_dates_become_none_with_audit(raw):
    value, entry = normalize_date(raw, field="dob", today=TODAY)
    assert value is None
    assert entry.rule == rules.INVALID_DATE and entry.field == "dob"


def test_missing_date_is_none_without_audit():
    assert normalize_date(None) == (None, None)
    assert normalize_date("") == (None, None)


@pytest.mark.parametrize(
    ("raw", "expected", "audited"),
    [
        ("M", "male", True),
        ("male", "male", False),
        ("1", "male", True),
        ("F", "female", True),
        ("Female", "female", True),
        ("2", "female", True),
        ("o", "other", True),
        ("U", "unknown", True),
        ("", "unknown", True),
        ("xyz", "unknown", True),
        (None, "unknown", False),
    ],
)
def test_gender_table(raw, expected, audited):
    value, entry = normalize_gender(raw)
    assert value == expected
    assert (entry is not None) == audited


@pytest.mark.parametrize("raw", ["123", "MRN123", "mrn-000123", " 000123 ", "MRN-000123"])
def test_mrn_variants_normalize_to_one_value(raw):
    value, entry = normalize_mrn(raw)
    assert value == "MRN-000123"
    assert (entry is None) == (raw == "MRN-000123")


def test_empty_mrn_is_none_with_audit():
    value, entry = normalize_mrn("  ")
    assert value is None and entry.rule == rules.MRN_MISSING


def test_mrn_audit_keeps_whitespace_visible():
    _, entry = normalize_mrn(" 000123 ")
    assert entry.old_value == '" 000123 "'


@pytest.mark.parametrize(
    "raw",
    [
        "SMITH, JOHN",
        "john  smith",
        "Dr. John Smith Jr.",
        "John Smith",
        "  John Smith ",
        "Mr. JOHN SMITH III",
        "Smith, John Sr.",
    ],
)
def test_name_noise_gives_same_given_and_family(raw):
    (given, family), entry = normalize_name(raw)
    assert (given, family) == ("John", "Smith")
    assert entry.rule == rules.NAME_NORMALIZED


def test_name_keeps_deliberate_mixed_case():
    assert normalize_name("Mary McDonald")[0] == ("Mary", "McDonald")
    assert normalize_name("Mary McDonald")[1] is None


def test_clean_text_whitespace_and_encoding():
    value, entry = clean_text("Chest X-ray:\r\n\tclear   lungs  \r\n")
    assert value == "Chest X-ray:\nclear lungs"
    assert entry.rule == rules.TEXT_CLEANED
    assert "clear" not in (entry.old_value or "")  # text audits record lengths, not content


# ---------------------------------------------------------------------------
# End to end against the ground truth
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("ingest")
    _generator().generate(42, root / "raw")
    result = ingest(root / "raw", root / "ehr.db", root / "rejected.jsonl", today=TODAY)
    truth = json.loads((root / "raw" / "ground_truth.json").read_text(encoding="utf-8"))
    return {"root": root, "result": result, "truth": truth, "db": root / "ehr.db"}


def _rows(db: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def _patients(run) -> dict[str, dict]:
    rows = _rows(run["db"], "SELECT patient_id, full_name, dob, gender, source_ids FROM patients")
    return {
        r[0]: {"name": r[1], "dob": r[2], "gender": r[3], "source_ids": json.loads(r[4])}
        for r in rows
    }


def _owner(run) -> dict[str, str]:
    """record_id -> patient_id, including records dropped as duplicates."""
    owner = dict(_rows(run["db"], "SELECT record_id, patient_id FROM records"))
    for rid, pid in _rows(run["db"], "SELECT record_id, patient_id FROM audit_log"):
        owner.setdefault(rid, pid)
    return owner


def _audits(run) -> list[dict]:
    keys = ("record_id", "patient_id", "field", "old_value", "new_value", "rule")
    return [
        dict(zip(keys, r, strict=True))
        for r in _rows(run["db"], f"SELECT {', '.join(keys)} FROM audit_log")
    ]


def _truth_patient(run, key: str) -> dict:
    return next(p for p in run["truth"]["patients"] if p["key"] == key)


def _pid_of(run, key: str) -> str:
    owner = _owner(run)
    pids = {owner[record_id(loc)] for loc in _truth_patient(run, key)["records"]}
    assert len(pids) == 1, f"{key} split across {pids}"
    return pids.pop()


def test_exactly_18_patients_and_nothing_rejected(run):
    assert len(_patients(run)) == 18
    assert run["result"].rejected == 0
    assert (run["root"] / "rejected.jsonl").read_text(encoding="utf-8") == ""


def test_every_record_maps_to_the_right_patient(run):
    patients, owner = _patients(run), _owner(run)
    for truth in run["truth"]["patients"]:
        for location in truth["records"]:
            patient = patients[owner[record_id(location)]]
            assert (patient["name"], patient["dob"]) == (truth["name"], truth["dob"]), location


def test_patient_dob_and_gender_match_expected(run):
    patients = _patients(run)
    for truth in run["truth"]["patients"]:
        patient = patients[_pid_of(run, truth["key"])]
        assert patient["dob"] == truth["dob"], truth["key"]
        assert patient["gender"] == truth["gender"], truth["key"]


def test_duplicates_collapse_to_the_first_copy(run):
    kept = {r[0] for r in _rows(run["db"], "SELECT record_id FROM records")}
    dropped = {
        a["record_id"]: a["new_value"] for a in _audits(run) if a["rule"] == rules.DUPLICATE_DROPPED
    }
    pairs = run["truth"]["duplicates"]
    assert {d["type"] for d in pairs} == {"exact", "near"}
    for pair in pairs:
        first, copy = (record_id(loc) for loc in pair["records"])
        assert first in kept and copy not in kept
        assert dropped[copy] == first
    assert len(dropped) == len(pairs)


def test_two_mrns_one_person_is_one_patient(run):
    truth = _truth_patient(run, "P01")
    patient = _patients(run)[_pid_of(run, "P01")]
    assert sorted(patient["source_ids"]) == sorted(truth["mrns_normalized"])
    assert len(patient["source_ids"]) == 2


def test_same_mrn_two_people_stay_apart(run):
    pid_a, pid_b = _pid_of(run, "P03"), _pid_of(run, "P04")
    assert pid_a != pid_b
    assert pid_b == f"{pid_a}-b"
    conflicts = {a["patient_id"] for a in _audits(run) if a["rule"] == rules.MRN_CONFLICT}
    assert conflicts == {pid_a, pid_b}


def test_record_before_dob_and_future_dob(run):
    defects = [d for d in run["truth"]["defects"] if d["id"] == "2"]
    by_record = {a["record_id"]: a for a in _audits(run) if a["field"] in ("dob", "date")}
    rules_seen = {by_record[record_id(d["location"])]["rule"] for d in defects}
    assert rules_seen == {rules.INVALID_DATE, rules.DATE_BEFORE_DOB}
    before_dob = next(d for d in defects if "/imaging/" in d["location"])
    (record_json,) = _rows(
        run["db"],
        f"SELECT record_json FROM records WHERE record_id = '{record_id(before_dob['location'])}'",
    )[0]
    assert json.loads(record_json)["date"] is None


def test_seeded_fhir_failure_passes_through(run):
    rid = record_id(run["truth"]["fhir_failure"])
    rows = _rows(run["db"], f"SELECT record_json FROM records WHERE record_id = '{rid}'")
    assert len(rows) == 1
    record = json.loads(rows[0][0])
    assert record["code"] is None and record["title"] == "Lab result"


def test_ingest_is_idempotent(run, tmp_path):
    second_db = tmp_path / "again.db"
    for _ in range(2):  # running twice into one DB must leave the same rows as one run
        ingest(run["root"] / "raw", second_db, tmp_path / "rej.jsonl", today=TODAY)
    for table in ("patients", "records", "audit_log"):
        sql = f"SELECT * FROM {table} ORDER BY 1"
        assert _rows(second_db, sql) == _rows(run["db"], sql), table


# Which audit rules evidence each defect family; 12 is evidenced by identity linking.
DEFECT_RULES = {
    "1": {rules.DATE_NORMALIZED, rules.AMBIGUOUS_DATE_ASSUMED_US},
    "2": {rules.INVALID_DATE, rules.DATE_BEFORE_DOB},
    "3": {rules.GENDER_NORMALIZED},
    "4": {rules.MRN_NORMALIZED},
    "5": {rules.DUPLICATE_DROPPED},
    "6": {rules.DUPLICATE_DROPPED},
    "7": {rules.MRN_MERGED},
    "8": {rules.MRN_CONFLICT},
    "9": {rules.FIELD_MISSING, rules.FLAG_DERIVED},
    "10": {rules.NAME_NORMALIZED},
    "11": {rules.TEXT_CLEANED, rules.NAME_NORMALIZED, rules.MRN_NORMALIZED},
}


def test_every_defect_instance_has_evidence(run):
    md = (run["root"] / "raw" / "DEFECTS.md").read_text(encoding="utf-8")
    md_ids = set(re.findall(r"^\| \d+ \| (\d+) \|", md, flags=re.M))
    assert md_ids == {str(i) for i in range(1, 13)}
    audits, owner = _audits(run), _owner(run)
    for defect in run["truth"]["defects"]:
        rid = record_id(defect["location"])
        if defect["id"] == "12":  # cross-source: all locations link to one patient
            assert owner[rid] == _pid_of(run, defect["patient"])
            continue
        if defect["id"] == "FHIR":
            continue  # covered by test_seeded_fhir_failure_passes_through
        expected = DEFECT_RULES[defect["id"]]
        pid = _pid_of(run, defect["patient"])
        matches = [
            a
            for a in audits
            if a["rule"] in expected and (a["record_id"] == rid or a["patient_id"] == pid)
        ]
        assert matches, f"no audit evidence for defect #{defect['n']} at {defect['location']}"
