"""Audit helpers: one AuditEntry per change made while cleaning one field of one record.

Every cleaning stage reports what it changed as AuditEntry objects; only run.py writes them
to the audit_log table. Rule names are the constants below, so tests and the UI can rely
on them.

AuditEntry strips surrounding whitespace from its values (contracts._Model). When the
whitespace *is* the defect (" 015592 ", a non-breaking space, a tab), the old value is
stored JSON-quoted instead, so the audit trail still shows what the raw value looked like.
"""

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from app.contracts import AuditEntry

DATE_NORMALIZED = "date_normalized"
AMBIGUOUS_DATE_ASSUMED_US = "ambiguous_date_assumed_us"
INVALID_DATE = "invalid_date"
DATE_BEFORE_DOB = "date_before_dob"
GENDER_NORMALIZED = "gender_normalized"
MRN_NORMALIZED = "mrn_normalized"
MRN_MISSING = "mrn_missing"
NAME_NORMALIZED = "name_normalized"
TEXT_CLEANED = "text_cleaned"
FIELD_MISSING = "field_missing"
TITLE_DEFAULTED = "title_defaulted"
LOINC_INFERRED = "loinc_inferred"
FLAG_DERIVED = "flag_derived"
MRN_MERGED = "mrn_merged"
MRN_CONFLICT = "mrn_conflict"
DEMOGRAPHICS_MISMATCH = "demographics_mismatch"
DUPLICATE_DROPPED = "duplicate_dropped"
RECORD_REJECTED = "record_rejected"

_INVISIBLE = (" ", "\t", "\r", "​", "﻿")


@dataclass(frozen=True)
class AuditContext:
    """Where a change happened: the record being cleaned and the file it came from."""

    record_id: str = ""
    source_file: str | None = None
    patient_id: str | None = None


def show(value: object) -> str | None:
    """Render a value for the audit log, quoting it when whitespace would be lost."""
    if value is None:
        return None
    text = str(value)
    if text != text.strip() or any(c in text for c in _INVISIBLE):
        return json.dumps(text)
    return text


def make_audit(
    ctx: AuditContext | None, field: str, old: object, new: object, rule: str
) -> AuditEntry:
    ctx = ctx or AuditContext()
    return AuditEntry(
        record_id=ctx.record_id,
        patient_id=ctx.patient_id,
        field=field,
        old_value=show(old),
        new_value=show(new),
        rule=rule,
        source_file=ctx.source_file,
    )


def write_audit(conn: sqlite3.Connection, entries: Iterable[AuditEntry]) -> int:
    rows = [
        (e.record_id, e.patient_id, e.field, e.old_value, e.new_value, e.rule, e.source_file)
        for e in entries
    ]
    conn.executemany(
        "INSERT INTO audit_log (record_id, patient_id, field, old_value, new_value, rule, "
        "source_file) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)
