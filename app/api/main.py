"""FastAPI app: semantic search and patient detail over the indexed records.

    POST /search?resource_type=&date_from=&date_to=   body: SearchRequest
    GET  /patients/{patient_id}
    GET  /health

The search service (Chroma + BM25 + patient rows) is built on first use and
reused for the life of the process. Run `python -m app.pipeline index` to
refresh the index, then restart the API or call POST /reload.
"""

import datetime as dt
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.contracts import PatientDetail, ResourceType, SearchRequest, SearchResponse
from app.search.service import SearchFilters, SearchService

log = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="EHR Media Intelligence API",
    version="0.1.0",
    description="Semantic search and AI summaries over synthetic FHIR R4 records.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_services: dict[tuple, SearchService] = {}


def get_service() -> SearchService:
    """One service per (db, index, model) triple, so tests with throwaway paths
    never share state with each other or with the dev data."""
    s = get_settings()
    key = (str(s.db_path), str(s.chroma_path), s.embed_model)
    if key not in _services:
        _services[key] = SearchService(s.db_path, s.chroma_path)
    return _services[key]


def reset_services() -> None:
    _services.clear()


Service = Annotated[SearchService, Depends(get_service)]


@app.get("/health")
def health(service: Service) -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "llm_provider": s.llm_provider,
        "embed_model": s.embed_model,
        "indexed_chunks": service.size,
        "patients": len(service.patients),
    }


@app.post("/search", response_model=SearchResponse)
def search(
    body: SearchRequest,
    service: Service,
    resource_type: Annotated[ResourceType | None, Query()] = None,
    date_from: Annotated[dt.date | None, Query()] = None,
    date_to: Annotated[dt.date | None, Query()] = None,
) -> SearchResponse:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    filters = SearchFilters(resource_type=resource_type, date_from=date_from, date_to=date_to)
    response = service.search(body.query, top_k=body.top_k, filters=filters)
    log.info(
        "search %r -> %d results in %d ms", body.query, len(response.results), response.took_ms
    )
    return response


@app.get("/patients/{patient_id}", response_model=PatientDetail)
def patient(patient_id: str, service: Service) -> PatientDetail:
    detail = service.patient_detail(patient_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"patient {patient_id!r} not found")
    return detail


@app.post("/reload")
def reload() -> dict:
    """Drop the in-memory service so the next request picks up a fresh index."""
    reset_services()
    return {"status": "reloaded"}
