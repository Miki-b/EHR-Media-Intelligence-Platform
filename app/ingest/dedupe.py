"""Stage 4: drop exact and near-duplicate records.

Two records are duplicates when this content hash is equal:
    sha256(patient_id | kind | date | lower-cased, whitespace-collapsed text | test | value)
Titles and source ids are not part of the hash, so a re-export with a new id, a changed
title case or extra whitespace still collapses. The first record (in processing order) is
kept; each later copy is dropped with a `duplicate_dropped` audit naming the kept record_id.
Records without a patient link are passed through untouched for validate.py to reject.
"""

import hashlib

from app.contracts import AuditEntry
from app.ingest import audit as rules
from app.ingest.audit import AuditContext, make_audit
from app.ingest.normalizers import NormRecord


def content_hash(record: NormRecord) -> str:
    text = " ".join(record.text.lower().split())
    parts = [
        record.patient_id or "",
        record.kind,
        record.date.isoformat() if record.date else "",
        text,
        (record.test or "").lower(),
        record.value or "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def dedupe_records(records: list[NormRecord]) -> tuple[list[NormRecord], list[AuditEntry]]:
    kept: list[NormRecord] = []
    audits: list[AuditEntry] = []
    first_by_hash: dict[str, str] = {}
    for record in records:
        if record.patient_id is None:
            kept.append(record)
            continue
        digest = content_hash(record)
        if digest in first_by_hash:
            ctx = AuditContext(record.record_id, record.source_file, record.patient_id)
            kept_id = first_by_hash[digest]
            audits.append(
                make_audit(ctx, "record", record.record_id, kept_id, rules.DUPLICATE_DROPPED)
            )
            continue
        first_by_hash[digest] = record.record_id
        kept.append(record)
    return kept, audits
