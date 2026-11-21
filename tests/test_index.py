"""Indexing: fixtures -> Chroma, idempotent, with filterable metadata."""

from pathlib import Path

import pytest

from app.fixtures import load_fixtures
from app.search.embed import HashEmbedder, OnnxEmbedder, get_embedder, tokenize
from app.search.index import build_index, get_collection, load_corpus

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


@pytest.fixture
def loaded_db(tmp_path):
    db = tmp_path / "ehr.db"
    load_fixtures(FIXTURES, db)
    return db


def test_tests_use_the_hash_embedder():
    assert isinstance(get_embedder(), HashEmbedder)


def test_tokenize_keeps_clinical_tokens():
    assert tokenize("HbA1c 9.8% (mg/dL), CXR") == ["hba1c", "9.8", "mg/dl", "cxr"]


def test_hash_embedder_is_deterministic_and_normalised():
    e = HashEmbedder()
    a, b = e.encode(["elevated HbA1c", "elevated HbA1c"])
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
    (c,) = e.encode(["normal chest x-ray"])
    sim_same = sum(x * y for x, y in zip(a, b, strict=True))
    sim_diff = sum(x * y for x, y in zip(a, c, strict=True))
    assert sim_same > sim_diff


def test_corpus_has_documents_reports_and_summaries(loaded_db):
    corpus = load_corpus(loaded_db)
    types = {c.resource_type for c, _ in corpus}
    assert types == {"DocumentReference", "DiagnosticReport", "Summary"}
    assert len({c.patient_id for c, _ in corpus}) == 6
    for chunk, meta in corpus:
        assert meta["patient_name"] != "Unknown"
        assert meta["mrn"].startswith("MRN-")
        assert meta["resource_id"] == chunk.resource_id
        assert isinstance(meta["date_int"], int)


def test_build_index_is_idempotent_and_removes_stale_chunks(loaded_db, tmp_path):
    chroma = tmp_path / "chroma"
    first = build_index(loaded_db, chroma)
    assert first.patients == 6
    assert first.chunks == len(load_corpus(loaded_db))
    assert first.deleted == 0

    second = build_index(loaded_db, chroma)
    assert second.chunks == first.chunks
    assert get_collection(chroma).count() == first.chunks

    collection = get_collection(chroma)
    collection.upsert(
        ids=["Summary/ghost#0"],
        documents=["stale"],
        embeddings=HashEmbedder().encode(["stale"]),
        metadatas=[{"patient_id": "ghost"}],
    )
    third = build_index(loaded_db, chroma)
    assert third.deleted == 1
    assert get_collection(chroma).count() == first.chunks


def test_metadata_filters_work_in_chroma(loaded_db, tmp_path):
    chroma = tmp_path / "chroma"
    build_index(loaded_db, chroma)
    collection = get_collection(chroma)

    reports = collection.get(where={"resource_type": "DiagnosticReport"}, include=["metadatas"])
    assert reports["ids"]
    assert all(m["resource_type"] == "DiagnosticReport" for m in reports["metadatas"])

    in_2025 = collection.get(
        where={"$and": [{"date_int": {"$gte": 20250101}}, {"date_int": {"$lte": 20251231}}]},
        include=["metadatas"],
    )
    years = {m["date"][:4] for m in in_2025["metadatas"]}
    assert years == {"2025"}


@pytest.mark.slow
def test_real_minilm_model_indexes_fixtures(loaded_db, tmp_path):
    from app.search.embed import SentenceTransformerEmbedder

    embedder = SentenceTransformerEmbedder("sentence-transformers/all-MiniLM-L6-v2")
    assert embedder.dimension == 384
    stats = build_index(loaded_db, tmp_path / "chroma", embedder)
    assert stats.chunks > 0


def test_backend_selection_keeps_hash_for_tests(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("EMBED_BACKEND", "onnx")
    get_settings.cache_clear()
    assert get_settings().embed_backend == "onnx"
    assert isinstance(get_embedder(), HashEmbedder), "EMBED_MODEL=hash wins over the backend"


@pytest.mark.slow
def test_onnx_backend_matches_torch_dimension(loaded_db, tmp_path):
    embedder = OnnxEmbedder("sentence-transformers/all-MiniLM-L6-v2")
    assert embedder.dimension == 384
    (vec,) = embedder.encode(["elevated HbA1c"])
    assert abs(sum(v * v for v in vec) - 1.0) < 1e-4
    stats = build_index(loaded_db, tmp_path / "chroma", embedder)
    assert stats.chunks > 0
