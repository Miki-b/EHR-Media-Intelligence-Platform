"""Stage 4 of the pipeline: bundles + summaries -> Chroma.

    python -m app.pipeline index

Reads every valid bundle, the summary that matches its content hash, and the
patient row, chunks them (see chunk.py), embeds the chunks and upserts them into
one Chroma collection. Chunks that no longer exist are deleted, so re-running
after data changes leaves no stale hits. Chunk ids are deterministic, so a
re-run with unchanged data rewrites the same rows.

Metadata stored per chunk (all scalar, as Chroma requires):
  patient_id, patient_name, mrn, resource_id, resource_type, title,
  date (ISO string or ""), date_int (YYYYMMDD or 0, used for range filters)
"""

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.contracts import ClinicalSummary
from app.db import connect
from app.search.chunk import Chunk, chunk_bundle, chunk_summary
from app.search.embed import Embedder, get_embedder

log = logging.getLogger(__name__)

COLLECTION = "ehr_chunks"
BATCH = 64


@dataclass
class IndexStats:
    patients: int
    chunks: int
    deleted: int
    embed_model: str
    seconds: float


def get_collection(chroma_path: Path | None = None, create: bool = True):
    path = Path(chroma_path or get_settings().chroma_path)
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
    )
    metadata = {"hnsw:space": "cosine"}
    if create:
        return client.get_or_create_collection(COLLECTION, metadata=metadata)
    return client.get_collection(COLLECTION)


def date_int(d: dt.date | None) -> int:
    return d.year * 10000 + d.month * 100 + d.day if d else 0


def chunk_metadata(chunk: Chunk, patient_name: str, mrn: str) -> dict:
    return {
        "patient_id": chunk.patient_id,
        "patient_name": patient_name,
        "mrn": mrn,
        "resource_id": chunk.resource_id,
        "resource_type": chunk.resource_type,
        "title": chunk.title,
        "date": chunk.date.isoformat() if chunk.date else "",
        "date_int": date_int(chunk.date),
    }


def load_corpus(db_path: Path | None = None) -> list[tuple[Chunk, dict]]:
    """Every chunk in the store, paired with its metadata."""
    rows = []
    with connect(db_path) as conn:
        patients = {
            r["patient_id"]: r
            for r in conn.execute("SELECT patient_id, mrn, full_name FROM patients")
        }
        bundles = conn.execute(
            "SELECT patient_id, bundle_json, content_hash FROM bundles WHERE is_valid = 1"
        ).fetchall()
        summaries = {
            (r["patient_id"], r["content_hash"]): r["summary_json"]
            for r in conn.execute(
                "SELECT patient_id, content_hash, summary_json FROM summaries ORDER BY created_at"
            )
        }
    for b in bundles:
        pid = b["patient_id"]
        patient = patients.get(pid)
        name = (patient["full_name"] if patient else None) or "Unknown"
        mrn = (patient["mrn"] if patient else None) or ""
        chunks = chunk_bundle(pid, json.loads(b["bundle_json"]))
        if summary_json := summaries.get((pid, b["content_hash"])):
            chunks += chunk_summary(ClinicalSummary.model_validate_json(summary_json))
        rows.extend((c, chunk_metadata(c, name, mrn)) for c in chunks)
    return rows


def build_index(
    db_path: Path | None = None,
    chroma_path: Path | None = None,
    embedder: Embedder | None = None,
) -> IndexStats:
    started = time.perf_counter()
    embedder = embedder or get_embedder()
    corpus = load_corpus(db_path)
    collection = get_collection(chroma_path)

    ids = [c.chunk_id for c, _ in corpus]
    for start in range(0, len(corpus), BATCH):
        batch = corpus[start : start + BATCH]
        texts = [c.text for c, _ in batch]
        collection.upsert(
            ids=[c.chunk_id for c, _ in batch],
            documents=texts,
            embeddings=embedder.encode(texts),
            metadatas=[m for _, m in batch],
        )

    stale = sorted(set(collection.get(include=[])["ids"]) - set(ids))
    if stale:
        collection.delete(ids=stale)

    stats = IndexStats(
        patients=len({c.patient_id for c, _ in corpus}),
        chunks=len(corpus),
        deleted=len(stale),
        embed_model=embedder.name,
        seconds=round(time.perf_counter() - started, 2),
    )
    log.info(
        "indexed %d chunks for %d patients with %s in %.1fs (%d stale removed)",
        stats.chunks,
        stats.patients,
        stats.embed_model,
        stats.seconds,
        stats.deleted,
    )
    return stats


def main() -> None:
    build_index()
