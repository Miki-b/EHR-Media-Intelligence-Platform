"""Summary cache on the `summaries` table, plus the committed JSONL copy.

Key: (patient_id, content_hash of the stored bundle, PROMPT_VERSION). Bundles are
byte-identical across runs (Task 2), so only a changed record or a new prompt version
misses. data/cache/summaries.jsonl is a committed export of the table, so a fresh clone
can serve real-model summaries with no API key: rows missing from the table are imported
from it before a run, and count as cache hits.
"""

import json
import sqlite3
from pathlib import Path

from app.contracts import ClinicalSummary


def get(
    conn: sqlite3.Connection, patient_id: str, content_hash: str, prompt_version: str
) -> ClinicalSummary | None:
    row = conn.execute(
        "SELECT summary_json FROM summaries"
        " WHERE patient_id = ? AND content_hash = ? AND prompt_version = ?",
        (patient_id, content_hash, prompt_version),
    ).fetchone()
    return ClinicalSummary.model_validate_json(row[0]) if row else None


def put(conn: sqlite3.Connection, summary: ClinicalSummary, content_hash: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO summaries"
        " (patient_id, content_hash, prompt_version, summary_json, model, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            summary.patient_id,
            content_hash,
            summary.prompt_version,
            summary.model_dump_json(),
            summary.model,
            (summary.generated_at.isoformat() if summary.generated_at else ""),
        ),
    )


def export_cache(conn: sqlite3.Connection, path: Path) -> int:
    rows = conn.execute(
        "SELECT patient_id, content_hash, prompt_version, summary_json FROM summaries"
        " ORDER BY patient_id, prompt_version, content_hash"
    ).fetchall()
    lines = [
        json.dumps(
            {
                "patient_id": pid,
                "content_hash": digest,
                "prompt_version": version,
                "summary": json.loads(summary_json),
            },
            sort_keys=True,
        )
        for pid, digest, version, summary_json in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)


def import_cache(conn: sqlite3.Connection, path: Path) -> int:
    """Insert JSONL rows that the table does not have yet. Returns how many were added."""
    if not path.exists():
        return 0
    added = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if get(conn, row["patient_id"], row["content_hash"], row["prompt_version"]):
            continue
        put(conn, ClinicalSummary.model_validate(row["summary"]), row["content_hash"])
        added += 1
    return added
