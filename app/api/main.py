"""FastAPI app. Dev B adds /search and /patients/{id} on the task-4 branch."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

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


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "embed_model": settings.embed_model,
    }
