"""Stage 1: read the raw source files into RawRecord objects, without cleaning anything.

Rules:
- Values are kept exactly as found (whitespace, case, odd formats). Non-string JSON values
  are converted with str(); that is the only transformation.
- Field names are matched loosely: case and punctuation are ignored and known aliases are
  accepted (dob/birthDate/DOB, gender/sex, mrn/MRN, name/patientName, ...).
- Note headers are loose "Label: value" lines up to the first blank line. Labels are
  matched the same way ("D.O.B.:", "Date of Service:", "MRN #:" ...). One separator space
  after the colon is dropped; any further padding is part of the raw value.
- The note body keeps its original line endings, so cleaning can audit CRLF files.
- Every record gets a location in the ground_truth.json format:
  "ehr_export.json#/3/imaging/0", "lab_results.csv#row=12", "notes/scan_004.txt".
  patient_ref names the object that carries the demographics (the JSON patient, or the
  row / note itself), so they are normalized and audited once per source object.
- Each JSON patient object also yields one kind="patient" entry at "ehr_export.json#/<i>",
  so demographics count even for a patient whose nested arrays are empty.
"""

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.contracts import RecordKind

log = logging.getLogger("ingest.parsers")

JSON_FILE, CSV_FILE, NOTES_DIR = "ehr_export.json", "lab_results.csv", "notes"
# "patient" is a demographics-only entry for a JSON patient object: it carries the patient
# fields even when the patient has no nested records, and never becomes a CanonicalRecord.
RawKind = RecordKind | Literal["patient"]


class RawRecord(BaseModel):
    """One clinical record plus the patient fields found next to it, as written."""

    model_config = ConfigDict(extra="forbid")  # deliberately no whitespace stripping

    source_file: str
    location: str
    patient_ref: str
    kind: RawKind
    mrn: str | None = None
    name: str | None = None
    dob: str | None = None
    gender: str | None = None
    date: str | None = None
    title: str | None = None
    text: str | None = None
    test: str | None = None
    value: str | None = None
    unit: str | None = None
    ref_range: str | None = None
    flag: str | None = None
    code: str | None = None


def _key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


PATIENT_FIELDS = {
    "mrn": ("mrn", "medicalrecordnumber", "mrnumber"),
    "name": ("name", "patientname", "fullname"),
    "dob": ("dob", "birthdate", "dateofbirth"),
    "gender": ("gender", "sex"),
}
RECORD_FIELDS = {
    "date": ("date", "collectedat", "studydate", "authored"),
    "title": ("title", "study", "description"),
    "text": ("text", "report", "body"),
    "test": ("test", "testname"),
    "value": ("value", "result"),
    "unit": ("unit", "units"),
    "ref_range": ("refrange", "referencerange"),
    "flag": ("flag", "abnormalflag"),
    "code": ("code", "loinc"),
}
JSON_ARRAYS: dict[str, RecordKind] = {"documents": "document", "labs": "lab", "imaging": "imaging"}
NOTE_LABELS = {
    "patient": "name",
    "patientname": "name",
    "ptname": "name",
    "name": "name",
    "mrn": "mrn",
    "mr": "mrn",
    "medicalrecordno": "mrn",
    "medicalrecordnumber": "mrn",
    "dob": "dob",
    "dateofbirth": "dob",
    "birthdate": "dob",
    "date": "date",
    "dateofservice": "date",
    "dos": "date",
    "type": "title",
    "notetype": "title",
    "documenttype": "title",
}
HEADER_LINE = re.compile(r"^([^:]{1,40}):(.*)$")


def _as_str(value: Any) -> str | None:
    return None if value is None else value if isinstance(value, str) else str(value)


def _pick(obj: dict[str, Any], fields: dict[str, tuple[str, ...]]) -> dict[str, str | None]:
    """Map loosely named keys in `obj` to our field names, first alias wins."""
    by_key = {_key(k): v for k, v in obj.items()}
    picked: dict[str, str | None] = {}
    for name, aliases in fields.items():
        value = next((by_key[a] for a in aliases if a in by_key), None)
        picked[name] = _as_str(value)
    return picked


def parse_json_export(path: Path) -> list[RawRecord]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = []
    for i, patient in enumerate(data):
        ref = f"{path.name}#/{i}"
        demographics = _pick(patient, PATIENT_FIELDS)
        records.append(
            RawRecord(
                source_file=path.name, location=ref, patient_ref=ref, kind="patient", **demographics
            )
        )
        arrays = {_key(k): v for k, v in patient.items() if isinstance(v, list)}
        for array, kind in JSON_ARRAYS.items():
            for j, item in enumerate(arrays.get(array, [])):
                records.append(
                    RawRecord(
                        source_file=path.name,
                        location=f"{ref}/{array}/{j}",
                        patient_ref=ref,
                        kind=kind,
                        **demographics,
                        **_pick(item, RECORD_FIELDS),
                    )
                )
    return records


def parse_lab_csv(path: Path) -> list[RawRecord]:
    records = []
    with open(path, newline="", encoding="utf-8") as f:
        for n, row in enumerate(csv.DictReader(f), start=1):
            location = f"{path.name}#row={n}"
            fields = _pick(row, PATIENT_FIELDS | RECORD_FIELDS)
            fields["code"] = fields["code"] or None
            records.append(
                RawRecord(
                    source_file=path.name,
                    location=location,
                    patient_ref=location,
                    kind="lab",
                    **fields,
                )
            )
    return records


def _split_note(text: str) -> tuple[dict[str, str], str]:
    """Split a note into header fields and the body (original line endings kept)."""
    header: dict[str, str] = {}
    lines = text.splitlines(keepends=True)
    start = len(lines)
    for i, line in enumerate(lines):
        content = line.rstrip("\r\n")
        if not content.strip():
            start = i + 1
            break
        match = HEADER_LINE.match(content)
        field = NOTE_LABELS.get(_key(match[1])) if match else None
        if field is None:
            start = i
            break
        value = match[2]
        header[field] = value[1:] if value.startswith(" ") else value
    return header, "".join(lines[start:])


def parse_note_txt(path: Path) -> RawRecord:
    path = Path(path)
    header, body = _split_note(path.read_bytes().decode("utf-8-sig"))
    location = f"{path.parent.name}/{path.name}"
    return RawRecord(
        source_file=location,
        location=location,
        patient_ref=location,
        kind="note",
        text=body,
        **header,
    )


def parse_sources(raw_dir: Path) -> list[RawRecord]:
    """Parse every source in deterministic order: JSON, then CSV, then notes by name."""
    raw_dir = Path(raw_dir)
    records: list[RawRecord] = []
    for name, parser in ((JSON_FILE, parse_json_export), (CSV_FILE, parse_lab_csv)):
        if (raw_dir / name).exists():
            records += parser(raw_dir / name)
        else:
            log.warning("source missing: %s", name)
    records += [parse_note_txt(p) for p in sorted((raw_dir / NOTES_DIR).glob("*.txt"))]
    return records
