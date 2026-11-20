"""SQLite schema and connection helper. One file holds every relational table."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id  TEXT PRIMARY KEY,
    mrn         TEXT NOT NULL,
    full_name   TEXT,
    dob         TEXT,
    gender      TEXT,
    source_ids  TEXT            -- JSON list of every MRN seen
);
CREATE TABLE IF NOT EXISTS records (
    record_id   TEXT PRIMARY KEY,
    patient_id  TEXT NOT NULL REFERENCES patients(patient_id),
    record_json TEXT NOT NULL   -- CanonicalRecord as JSON
);
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   TEXT,
    patient_id  TEXT,
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT,
    rule        TEXT,
    source_file TEXT
);
CREATE TABLE IF NOT EXISTS bundles (
    patient_id   TEXT PRIMARY KEY,
    bundle_json  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_valid     INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validation_errors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT,
    resource_type TEXT,
    resource_id   TEXT,
    path          TEXT,
    message       TEXT
);
CREATE TABLE IF NOT EXISTS summaries (
    patient_id     TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    summary_json   TEXT NOT NULL,
    model          TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (patient_id, content_hash, prompt_version)
);
"""


def init_db(path: Path | None = None) -> Path:
    path = Path(path or get_settings().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
    return path


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
