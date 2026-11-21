"""Search service: hybrid vector + BM25 search over the Chroma index, plus the
patient detail view. One instance per process; the API builds it at startup.

Everything the service needs at query time is in memory (BM25 corpus, patient
rows, summaries) except the vector query, which goes to Chroma. For the
50-record target this answers in well under the 2-second budget.
"""

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.contracts import (
    ClinicalSummary,
    LinkedResource,
    PatientDetail,
    ResourceType,
    SearchResponse,
    SearchResult,
)
from app.db import connect
from app.search.chunk import codeable_text, parse_fhir_date
from app.search.embed import Embedder, get_embedder, tokenize
from app.search.index import date_int, get_collection
from app.search.rank import Candidate, collapse_by_resource, cosine_from_distance, normalise
from app.search.rank import snippet as make_snippet
from app.summarize.prompts import PROMPT_VERSION

log = logging.getLogger(__name__)

MAX_CANDIDATES = 200
SUMMARY_SNIPPET_CHARS = 160


@dataclass(frozen=True)
class SearchFilters:
    resource_type: ResourceType | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None

    def chroma_where(self) -> dict | None:
        clauses: list[dict] = []
        if self.resource_type:
            clauses.append({"resource_type": {"$eq": self.resource_type}})
        if self.date_from:
            clauses.append({"date_int": {"$gte": date_int(self.date_from)}})
        if self.date_to:
            clauses.append({"date_int": {"$lte": date_int(self.date_to)}})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def accepts(self, metadata: dict) -> bool:
        if self.resource_type and metadata.get("resource_type") != self.resource_type:
            return False
        d = metadata.get("date_int", 0)
        if self.date_from and d < date_int(self.date_from):
            return False
        return not (self.date_to and d > date_int(self.date_to))


class SearchService:
    def __init__(
        self,
        db_path: Path | None = None,
        chroma_path: Path | None = None,
        embedder: Embedder | None = None,
    ):
        settings = get_settings()
        self.db_path = Path(db_path or settings.db_path)
        self.embedder = embedder or get_embedder()
        self.collection = get_collection(chroma_path or settings.chroma_path)
        self._load_corpus()
        self._load_patients()

    # -- loading ------------------------------------------------------------

    def _load_corpus(self) -> None:
        got = self.collection.get(include=["documents", "metadatas"])
        self.ids: list[str] = list(got["ids"])
        self.texts: list[str] = list(got["documents"] or [])
        self.metadatas: list[dict] = list(got["metadatas"] or [])
        self._bm25 = BM25Okapi([tokenize(t) for t in self.texts]) if self.texts else None
        log.info("search corpus: %d chunks", len(self.ids))

    def _load_patients(self) -> None:
        self.patients: dict[str, dict] = {}
        self.summaries: dict[str, ClinicalSummary] = {}
        with connect(self.db_path) as conn:
            for row in conn.execute("SELECT * FROM patients"):
                self.patients[row["patient_id"]] = dict(row)
            # One summary per patient: the current prompt version wins, then the newest.
            rows = conn.execute(
                "SELECT s.patient_id, s.summary_json FROM summaries s"
                " JOIN bundles b ON b.patient_id = s.patient_id"
                " AND b.content_hash = s.content_hash"
                " ORDER BY (s.prompt_version = ?), s.created_at",
                (PROMPT_VERSION,),
            )
            for row in rows:
                self.summaries[row["patient_id"]] = ClinicalSummary.model_validate_json(
                    row["summary_json"]
                )

    @property
    def size(self) -> int:
        return len(self.ids)

    # -- search -------------------------------------------------------------

    def search(
        self, query: str, top_k: int = 5, filters: SearchFilters | None = None
    ) -> SearchResponse:
        started = time.perf_counter()
        filters = filters or SearchFilters()
        results: list[SearchResult] = []
        if self.size:
            candidates = self._candidates(query, filters)
            for c in collapse_by_resource(candidates)[:top_k]:
                results.append(self._to_result(c, query))
        return SearchResponse(results=results, took_ms=int((time.perf_counter() - started) * 1000))

    def _candidates(self, query: str, filters: SearchFilters) -> list[Candidate]:
        by_id: dict[str, Candidate] = {}

        n_results = min(self.size, MAX_CANDIDATES)
        hits = self.collection.query(
            query_embeddings=self.embedder.encode([query]),
            n_results=n_results,
            where=filters.chroma_where(),
            include=["documents", "metadatas", "distances"],
        )
        for cid, text, meta, dist in zip(
            hits["ids"][0],
            hits["documents"][0],
            hits["metadatas"][0],
            hits["distances"][0],
            strict=True,
        ):
            by_id[cid] = Candidate(cid, text, meta, vector=cosine_from_distance(dist))

        if self._bm25 is not None:
            raw = list(self._bm25.get_scores(tokenize(query)))
            for cid, text, meta, score in zip(
                self.ids, self.texts, self.metadatas, normalise(raw), strict=True
            ):
                if score <= 0 or not filters.accepts(meta):
                    continue
                cand = by_id.setdefault(cid, Candidate(cid, text, meta))
                cand.bm25 = score

        return [c.blend() for c in by_id.values()]

    def _to_result(self, c: Candidate, query: str) -> SearchResult:
        meta = c.metadata
        summary = self.summaries.get(meta["patient_id"])
        return SearchResult(
            patient_id=meta["patient_id"],
            patient_name=meta.get("patient_name") or "Unknown",
            mrn=meta.get("mrn") or "",
            resource_id=meta["resource_id"],
            resource_type=meta["resource_type"],
            date=dt.date.fromisoformat(meta["date"]) if meta.get("date") else None,
            score=c.score,
            snippet=make_snippet(c.text, query),
            summary_snippet=summary_snippet(summary) if summary else None,
        )

    # -- patient detail -----------------------------------------------------

    def patient_detail(self, patient_id: str) -> PatientDetail | None:
        row = self.patients.get(patient_id)
        if not row:
            return None
        summary = self.summaries.get(patient_id)
        cited = set(summary.citations) if summary else set()
        with connect(self.db_path) as conn:
            bundle_row = conn.execute(
                "SELECT bundle_json FROM bundles WHERE patient_id = ?", (patient_id,)
            ).fetchone()
        resources = (
            linked_resources(json.loads(bundle_row["bundle_json"]), cited) if bundle_row else []
        )
        return PatientDetail(
            patient_id=patient_id,
            patient_name=row.get("full_name") or "Unknown",
            mrn=row.get("mrn") or "",
            dob=dt.date.fromisoformat(row["dob"]) if row.get("dob") else None,
            gender=row.get("gender") or "unknown",
            summary=summary,
            resources=resources,
        )


def summary_snippet(summary: ClinicalSummary, limit: int = SUMMARY_SNIPPET_CHARS) -> str:
    text = summary.chief_concern
    if summary.key_diagnoses:
        text = f"{text} Dx: {'; '.join(summary.key_diagnoses)}."
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


_LINKED_TYPES = ("DocumentReference", "DiagnosticReport", "Observation", "Encounter")


def linked_resources(bundle: dict, cited: set[str]) -> list[LinkedResource]:
    """Every clinical resource in a bundle, newest first, flagged when cited."""
    out: list[LinkedResource] = []
    for entry in bundle.get("entry", []):
        r = entry.get("resource") or {}
        rtype = r.get("resourceType")
        if rtype not in _LINKED_TYPES or not r.get("id"):
            continue
        rid = f"{rtype}/{r['id']}"
        if rtype == "DocumentReference":
            title = r.get("description") or codeable_text(r.get("type")) or "Document"
            date = parse_fhir_date(r.get("date"))
        elif rtype == "Encounter":
            cls = r.get("class") or {}
            title = f"Encounter: {cls.get('display') or cls.get('code') or 'visit'}"
            date = parse_fhir_date((r.get("period") or {}).get("start"))
        else:
            title = codeable_text(r.get("code")) or rtype
            date = parse_fhir_date(r.get("effectiveDateTime")) or parse_fhir_date(r.get("issued"))
        out.append(
            LinkedResource(
                resource_id=rid, resource_type=rtype, date=date, title=title, cited=rid in cited
            )
        )
    return sorted(out, key=lambda x: (x.date or dt.date.min, x.resource_type), reverse=True)
