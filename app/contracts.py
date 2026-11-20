"""Shared contracts between the pipeline stages (data + AI) and search + UI.

Every stage is built against these shapes, so change a model here only together
with the stages that read it.

Sections:
  1. Canonical models   - output of Task 1, input to Task 2
  2. Validation issues  - output of Task 2 validation, shown by the UI
  3. Clinical summary   - output of Task 3, shown by the UI
  4. Search API         - request/response of Task 4, read by the UI
"""

# Dates use the `dt.` prefix because a field named `date` would shadow the type.
import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DISCLAIMER = (
    "AI-generated summary for information only. It is not a clinical decision "
    "and must be verified against the source records."
)

Gender = Literal["male", "female", "other", "unknown"]
RecordKind = Literal["document", "note", "lab", "imaging"]
ResourceType = Literal["DocumentReference", "DiagnosticReport", "Summary"]
Confidence = Literal["high", "medium", "low"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# 1. Canonical models (Task 1)
# ---------------------------------------------------------------------------


class AuditEntry(_Model):
    """One change made while cleaning one field of one record."""

    record_id: str
    patient_id: str | None = None
    field: str
    old_value: str | None = None
    new_value: str | None = None
    rule: str  # e.g. "date_normalized", "ambiguous_date_assumed_us", "duplicate_dropped"
    source_file: str | None = None


class CanonicalPatient(_Model):
    patient_id: str  # canonical id, e.g. "p-0001"
    mrn: str  # primary normalized MRN, e.g. "MRN-000123"
    given_name: str | None = None
    family_name: str | None = None
    dob: dt.date | None = None
    gender: Gender = "unknown"
    source_ids: list[str] = Field(default_factory=list)  # every MRN seen for this patient
    audit: list[AuditEntry] = Field(default_factory=list)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.given_name, self.family_name) if p) or "Unknown"


class CanonicalRecord(_Model):
    record_id: str  # stable id, e.g. "r-0042"
    patient_id: str
    kind: RecordKind
    date: dt.date | None = None
    title: str
    text: str
    code: str | None = None  # LOINC code when known
    flag: Literal["H", "L", "N"] | None = None  # lab abnormality flag from the source
    source_file: str
    audit: list[AuditEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. Validation issues (Task 2)
# ---------------------------------------------------------------------------


class ValidationIssue(_Model):
    patient_id: str
    resource_type: str
    resource_id: str | None = None
    path: str | None = None  # e.g. "subject.reference"
    message: str


# ---------------------------------------------------------------------------
# 3. Clinical summary (Task 3)
# ---------------------------------------------------------------------------


class MediaItem(_Model):
    resource_id: str  # e.g. "DiagnosticReport/5b1c..."
    type: Literal["imaging", "lab", "document", "note"]
    date: dt.date | None = None
    note: str


class ClinicalSummary(_Model):
    """Fields above the line come from the LLM; fields below are filled by code."""

    patient_id: str
    chief_concern: str
    key_diagnoses: list[str] = Field(default_factory=list)
    recent_media: list[MediaItem] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)  # resource ids backing the claims

    # --- filled by code, never by the model ---
    word_count: int = 0
    confidence: Confidence = "low"
    disclaimer: str = DISCLAIMER
    model: str = "mock"
    prompt_version: str = "v1"
    generated_at: dt.datetime | None = None


# ---------------------------------------------------------------------------
# 4. Search API (Task 4)
# ---------------------------------------------------------------------------


class SearchRequest(_Model):
    """Body of POST /search. Filters travel as query parameters:
    ?resource_type=DiagnosticReport&date_from=2024-01-01&date_to=2025-12-31
    """

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchResult(_Model):
    patient_id: str
    patient_name: str
    mrn: str
    resource_id: str
    resource_type: ResourceType
    date: dt.date | None = None
    score: float = Field(ge=0, le=1)
    snippet: str
    summary_snippet: str | None = None


class SearchResponse(_Model):
    results: list[SearchResult]
    took_ms: int


class LinkedResource(_Model):
    resource_id: str
    resource_type: str
    date: dt.date | None = None
    title: str
    cited: bool = False  # True when the summary cites this resource


class PatientDetail(_Model):
    """Response of GET /patients/{patient_id}; feeds the UI drawer."""

    patient_id: str
    patient_name: str
    mrn: str
    dob: dt.date | None = None
    gender: Gender = "unknown"
    summary: ClinicalSummary | None = None
    resources: list[LinkedResource] = Field(default_factory=list)
