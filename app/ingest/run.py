"""Task 1 entry point: parse -> normalize -> identity -> dedupe -> validate -> store.

    python -m app.pipeline ingest

Idempotent: patients, records and audit_log are cleared (including the audit_log id
sequence) and rewritten in one transaction, and data/rejected.jsonl is overwritten, so two
runs on the same files give identical rows. Only this module touches the database.
Logging carries ids and counts only, never names, DOBs or note text.
"""

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.contracts import AuditEntry, CanonicalPatient, CanonicalRecord
from app.db import connect
from app.ingest.audit import write_audit
from app.ingest.dedupe import dedupe_records
from app.ingest.identity import resolve_patients
from app.ingest.normalizers import normalize_records
from app.ingest.parsers import parse_sources
from app.ingest.validate import Rejection, validate

log = logging.getLogger("ingest")


@dataclass
class IngestResult:
    parsed_by_source: Counter = field(default_factory=Counter)
    patients: int = 0
    records_kept: int = 0
    duplicates_dropped: int = 0
    rejected: int = 0
    audit_by_rule: Counter = field(default_factory=Counter)


def _source_format(source_file: str) -> str:
    return source_file.rsplit(".", 1)[-1].lower()


def _fill_patient_ids(audits: list[AuditEntry], owner: dict[str, str | None]) -> list[AuditEntry]:
    return [
        a if a.patient_id else a.model_copy(update={"patient_id": owner.get(a.record_id)})
        for a in audits
    ]


def store(
    conn: sqlite3.Connection,
    patients: list[CanonicalPatient],
    records: list[CanonicalRecord],
    audits: list[AuditEntry],
) -> None:
    conn.execute("DELETE FROM records")
    conn.execute("DELETE FROM patients")
    conn.execute("DELETE FROM audit_log")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'audit_log'")
    conn.executemany(
        "INSERT INTO patients (patient_id, mrn, full_name, dob, gender, source_ids) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                p.patient_id,
                p.mrn,
                p.full_name,
                p.dob.isoformat() if p.dob else None,
                p.gender,
                json.dumps(p.source_ids),
            )
            for p in patients
        ],
    )
    conn.executemany(
        "INSERT INTO records (record_id, patient_id, record_json) VALUES (?, ?, ?)",
        [(r.record_id, r.patient_id, r.model_dump_json()) for r in records],
    )
    write_audit(conn, audits)


def write_rejections(path: Path, rejections: list[Rejection]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"location": r.location, "reason": r.reason}) for r in rejections]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def ingest(
    raw_dir: Path, db_path: Path, rejected_path: Path, today: date | None = None
) -> IngestResult:
    """Run every stage on the files in raw_dir and store the result."""
    result = IngestResult()
    raws = parse_sources(raw_dir)
    result.parsed_by_source = Counter(
        _source_format(r.source_file) for r in raws if r.kind != "patient"
    )
    n_demographics = sum(r.kind == "patient" for r in raws)
    log.info(
        "parse: %d records %s, %d JSON patient objects",
        len(raws) - n_demographics,
        dict(result.parsed_by_source),
        n_demographics,
    )

    normalized, audits = normalize_records(raws, today=today)
    log.info("normalize: %d records, %d audit entries", len(normalized), len(audits))

    (clusters, linked), identity_audits = resolve_patients(normalized)
    audits += identity_audits
    log.info("identity: %d patients", len(clusters))

    clinical = [r for r in linked if r.kind != "patient"]  # demographics entries end here
    kept, dedupe_audits = dedupe_records(clinical)
    audits += dedupe_audits
    result.duplicates_dropped = len(clinical) - len(kept)
    log.info("dedupe: %d kept, %d dropped", len(kept), result.duplicates_dropped)

    validated, validate_audits = validate(clusters, kept)
    audits += validate_audits
    log.info(
        "validate: %d patients, %d records, %d rejected",
        len(validated.patients),
        len(validated.records),
        len(validated.rejections),
    )

    audits = _fill_patient_ids(audits, {r.record_id: r.patient_id for r in linked})
    with connect(db_path) as conn:
        store(conn, validated.patients, validated.records, audits)
    write_rejections(Path(rejected_path), validated.rejections)

    result.patients = len(validated.patients)
    result.records_kept = len(validated.records)
    result.rejected = len(validated.rejections)
    result.audit_by_rule = Counter(a.rule for a in audits)
    log.info("store: %d audit entries %s", len(audits), dict(sorted(result.audit_by_rule.items())))
    return result


def main() -> None:
    settings = get_settings()
    ingest(settings.raw_dir, settings.db_path, settings.rejected_path)
