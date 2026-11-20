"""Tests for the synthetic data generator (scripts/generate_data.py)."""

import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_data.py"
OUTPUTS = ["ehr_export.json", "lab_results.csv", "notes", "DEFECTS.md", "ground_truth.json"]
DEFECT_IDS = [str(i) for i in range(1, 13)]


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture(scope="module")
def out(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("seed42")
    gen.generate(42, path)
    return path


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(f.relative_to(path)): f.read_bytes() for f in sorted(path.rglob("*")) if f.is_file()
    }


def _ehr(out: Path) -> list[dict]:
    return json.loads((out / "ehr_export.json").read_text(encoding="utf-8"))


def _csv_rows(out: Path) -> list[dict]:
    with open(out / "lab_results.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _truth(out: Path) -> dict:
    return json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))


def _record_count(out: Path) -> int:
    json_records = sum(len(p[a]) for p in _ehr(out) for a in ("documents", "labs", "imaging"))
    return json_records + len(_csv_rows(out)) + len(list((out / "notes").glob("*.txt")))


def _resolve(out: Path, location: str):
    """Return the raw item at a ground-truth location, or raise if it does not exist."""
    if location.startswith("ehr_export.json#/"):
        node = _ehr(out)
        for part in location.split("#/", 1)[1].split("/"):
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node
    if location.startswith("lab_results.csv#row="):
        n = int(location.split("=", 1)[1])
        assert n >= 1
        return _csv_rows(out)[n - 1]
    if location.startswith("notes/"):
        return (out / location).read_bytes()
    raise AssertionError(f"unknown location format: {location}")


def test_same_seed_is_byte_identical(tmp_path):
    gen.generate(42, tmp_path / "a")
    gen.generate(42, tmp_path / "b")
    assert _snapshot(tmp_path / "a") == _snapshot(tmp_path / "b")


def test_different_seed_differs(tmp_path, out):
    gen.generate(7, tmp_path / "other")
    other = _snapshot(tmp_path / "other")
    base = _snapshot(out)
    for name in ("ehr_export.json", "lab_results.csv", "ground_truth.json"):
        assert other[name] != base[name]


def test_all_outputs_exist_and_parse(out):
    for name in OUTPUTS:
        assert (out / name).exists(), name
    assert isinstance(_ehr(out), list)
    assert list(_csv_rows(out)[0]) == gen.CSV_COLUMNS
    notes = list((out / "notes").glob("*.txt"))
    assert 8 <= len(notes) <= 10
    assert _truth(out)["patients"]


def test_every_defect_id_documented(out):
    md = (out / "DEFECTS.md").read_text(encoding="utf-8")
    md_ids = set(re.findall(r"^\| \d+ \| (\w+) \|", md, flags=re.M))
    truth_ids = {d["id"] for d in _truth(out)["defects"]}
    for defect_id in [*DEFECT_IDS, "FHIR"]:
        assert defect_id in md_ids, f"defect {defect_id} missing from DEFECTS.md"
        assert defect_id in truth_ids, f"defect {defect_id} missing from ground_truth.json"
    assert "seeded fhir failure" in md.lower()
    assert sum(d["id"] == "FHIR" for d in _truth(out)["defects"]) == 1


def test_ground_truth_locations_exist(out):
    truth = _truth(out)
    locations = [loc for p in truth["patients"] for loc in p["demographics"] + p["records"]]
    locations += [loc for d in truth["duplicates"] for loc in d["records"]]
    locations += [d["location"] for d in truth["defects"]]
    locations.append(truth["fhir_failure"])
    for location in locations:
        assert _resolve(out, location) is not None


def test_every_record_belongs_to_exactly_one_patient(out):
    owned = [loc for p in _truth(out)["patients"] for loc in p["records"]]
    assert len(owned) == len(set(owned))
    assert len(owned) == _record_count(out)


def test_patient_and_record_counts(out):
    assert len(_truth(out)["patients"]) == 18
    assert 50 <= _record_count(out) <= 70


def test_duplicates_and_conflict(out):
    truth = _truth(out)
    exact = next(d for d in truth["duplicates"] if d["type"] == "exact")
    a, b = (_resolve(out, loc) for loc in exact["records"])
    assert a == b
    p03, p04 = (next(p for p in truth["patients"] if p["key"] == k) for k in ("P03", "P04"))
    assert truth["mrn_conflict"]["raw_mrn"] in p03["mrn_variants"]
    assert truth["mrn_conflict"]["raw_mrn"] in p04["mrn_variants"]
    assert p03["name"] != p04["name"] and p03["dob"] != p04["dob"]
    p01 = next(p for p in truth["patients"] if p["key"] == "P01")
    assert len(p01["mrns_normalized"]) == 2 and len(p01["name_variants"]) == 2


def test_encoding_noise_present(out):
    notes = [f.read_bytes() for f in (out / "notes").glob("*.txt")]
    assert any(b"\r\n" in n for n in notes)
    assert any(" ".encode() in n for n in notes)
    assert " " in (out / "ehr_export.json").read_text(encoding="utf-8")
    assert "\t" in (out / "lab_results.csv").read_text(encoding="utf-8")


def test_clinical_text_length(out):
    texts = [d["text"] for p in _ehr(out) for d in p["documents"]]
    texts += [f.read_text(encoding="utf-8") for f in (out / "notes").glob("*.txt")]
    for text in texts:
        assert 60 <= len(text.split()) <= 250
