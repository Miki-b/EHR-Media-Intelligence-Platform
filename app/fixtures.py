"""Load the committed fixtures into SQLite, writing the same rows Dev A's stages write.

Task 1 fills `patients`, Task 2 fills `bundles`, Task 3 fills `summaries`, keyed by the bundle's
content hash and the prompt version. Downstream code (search index, API, UI) reads
only those tables, so loading fixtures here lets Dev B build and test without the
real pipeline. Once the pipeline lands, `make pipeline` replaces these rows.
"""

import datetime as dt
import hashlib
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.contracts import ClinicalSummary
from app.db import connect

log = logging.getLogger(__name__)


def content_hash(bundle: dict) -> str:
    """Stable hash of a bundle: key order and whitespace do not matter."""
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def patient_entry(bundle: dict) -> dict:
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Patient":
            return resource
    raise ValueError("bundle has no Patient entry")


def patient_row(patient_id: str, bundle: dict) -> tuple:
    p = patient_entry(bundle)
    identifiers = [i.get("value") for i in p.get("identifier", []) if i.get("value")]
    name = (p.get("name") or [{}])[0]
    full_name = " ".join([*name.get("given", []), name.get("family", "")]).strip() or None
    return (
        patient_id,
        identifiers[0] if identifiers else "",
        full_name,
        p.get("birthDate"),
        p.get("gender", "unknown"),
        json.dumps(identifiers),
    )


def load_fixtures(fixtures_dir: Path | None = None, db_path: Path | None = None) -> int:
    """Insert or replace every fixture patient. Returns the number of bundles loaded."""
    fixtures_dir = Path(fixtures_dir or get_settings().fixtures_dir)
    now = dt.datetime.now(dt.UTC).isoformat()
    loaded = 0
    with connect(db_path) as conn:
        for path in sorted((fixtures_dir / "bundles").glob("*.json")):
            patient_id = path.stem
            bundle = json.loads(path.read_text(encoding="utf-8"))
            digest = content_hash(bundle)
            conn.execute(
                "INSERT OR REPLACE INTO patients"
                " (patient_id, mrn, full_name, dob, gender, source_ids) VALUES (?,?,?,?,?,?)",
                patient_row(patient_id, bundle),
            )
            conn.execute(
                "INSERT OR REPLACE INTO bundles"
                " (patient_id, bundle_json, content_hash, is_valid, created_at)"
                " VALUES (?,?,?,1,?)",
                (patient_id, json.dumps(bundle), digest, now),
            )
            summary_path = fixtures_dir / "summaries" / f"{patient_id}.json"
            if summary_path.exists():
                summary = ClinicalSummary.model_validate_json(
                    summary_path.read_text(encoding="utf-8")
                )
                conn.execute(
                    "INSERT OR REPLACE INTO summaries"
                    " (patient_id, content_hash, prompt_version, summary_json, model, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        patient_id,
                        digest,
                        summary.prompt_version,
                        summary.model_dump_json(),
                        summary.model,
                        now,
                    ),
                )
            loaded += 1
            log.info("loaded %s (%s entries)", patient_id, len(bundle.get("entry", [])))
    return loaded
