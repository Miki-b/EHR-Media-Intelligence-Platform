"""Task 2 entry point: canonical models in SQLite -> validated FHIR bundles + report.

    python -m app.pipeline fhir

Reads the `patients` and `records` tables written by Task 1 and writes `bundles` and
`validation_errors` (cleared first, including the id sequence, so reruns are identical
apart from created_at) plus reports/validation.md. It never writes `patients`.
Logging and the report carry ids and counts only, never names, DOBs or note text.

The patients table stores full_name, not given/family. Task 1 always makes the family
name the last token, so splitting on the last space recovers both exactly.
"""

import datetime as dt
import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.contracts import CanonicalPatient, CanonicalRecord
from app.db import connect
from app.fhir.bundle import PatientBundle, build_bundle

log = logging.getLogger("fhir")


@dataclass
class FhirResult:
    bundles: int = 0
    valid: int = 0
    resources: Counter = field(default_factory=Counter)
    issues: int = 0


def _patient_from_row(row: sqlite3.Row) -> CanonicalPatient:
    full_name = row["full_name"] if row["full_name"] != "Unknown" else ""  # contract default
    given, _, family = (full_name or "").rpartition(" ")
    return CanonicalPatient(
        patient_id=row["patient_id"],
        mrn=row["mrn"],
        given_name=given or None,
        family_name=family or None,
        dob=row["dob"],
        gender=row["gender"] or "unknown",
        source_ids=json.loads(row["source_ids"] or "[]"),
    )


def load_canonical(
    conn: sqlite3.Connection,
) -> list[tuple[CanonicalPatient, list[CanonicalRecord]]]:
    patients = [_patient_from_row(r) for r in conn.execute("SELECT * FROM patients ORDER BY 1")]
    by_patient: dict[str, list[CanonicalRecord]] = {p.patient_id: [] for p in patients}
    for row in conn.execute("SELECT record_json FROM records ORDER BY record_id"):
        record = CanonicalRecord.model_validate_json(row["record_json"])
        by_patient.setdefault(record.patient_id, []).append(record)
    return [(p, by_patient[p.patient_id]) for p in patients]


def store(conn: sqlite3.Connection, bundles: list[PatientBundle]) -> None:
    now = dt.datetime.now(dt.UTC).isoformat()
    conn.execute("DELETE FROM bundles")
    conn.execute("DELETE FROM validation_errors")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'validation_errors'")
    conn.executemany(
        "INSERT INTO bundles (patient_id, bundle_json, content_hash, is_valid, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (b.patient_id, json.dumps(b.bundle), b.content_hash, int(b.is_valid), now)
            for b in bundles
        ],
    )
    conn.executemany(
        "INSERT INTO validation_errors (patient_id, resource_type, resource_id, path, message)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (i.patient_id, i.resource_type, i.resource_id, i.path, i.message)
            for b in bundles
            for i in b.issues
        ],
    )


def resource_counts(bundles: list[PatientBundle]) -> Counter:
    return Counter(
        e["resource"]["resourceType"] for b in bundles for e in b.bundle.get("entry", [])
    )


def render_report(bundles: list[PatientBundle]) -> str:
    counts = resource_counts(bundles)
    issues = [i for b in bundles for i in b.issues]
    lines = [
        "# FHIR validation report",
        "",
        f"- Bundles: {len(bundles)} ({sum(b.is_valid for b in bundles)} valid, "
        f"{sum(not b.is_valid for b in bundles)} invalid)",
        f"- Resources kept: {sum(counts.values())}",
        f"- Resources rejected: {len({i.resource_id for i in issues})} "
        f"({len(issues)} validation issues)",
        "",
        "| Resource type | Count |",
        "| --- | --- |",
        *(f"| {t} | {n} |" for t, n in sorted(counts.items())),
        "",
        "## Validation issues",
        "",
    ]
    if not issues:
        lines.append("None.")
    else:
        lines += ["| Patient | Resource | Path | Message |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {i.patient_id} | `{i.resource_id}` | `{i.path or ''}` | {i.message} |"
            for i in issues
        ]
    return "\n".join(lines) + "\n"


def build_fhir(db_path: Path, report_path: Path) -> FhirResult:
    with connect(db_path) as conn:
        canonical = load_canonical(conn)
        bundles = [build_bundle(patient, records) for patient, records in canonical]
        store(conn, bundles)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(bundles), encoding="utf-8")
    result = FhirResult(
        bundles=len(bundles),
        valid=sum(b.is_valid for b in bundles),
        resources=resource_counts(bundles),
        issues=sum(len(b.issues) for b in bundles),
    )
    log.info(
        "%d bundles (%d valid), %d resources %s, %d validation issues",
        result.bundles,
        result.valid,
        sum(result.resources.values()),
        dict(sorted(result.resources.items())),
        result.issues,
    )
    return result


def main() -> None:
    settings = get_settings()
    build_fhir(settings.db_path, settings.reports_dir / "validation.md")
