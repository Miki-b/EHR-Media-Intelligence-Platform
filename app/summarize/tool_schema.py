"""The structured-output schema for the LLM-filled fields of ClinicalSummary.

The model fills one `record_summary` tool call. Its top-level fields are exactly the
contract's LLM fields (chief_concern, key_diagnoses, recent_media, anomalies, citations);
the code-filled fields (CODE_FIELDS) are never in the schema. Diagnoses and anomalies are
claims {text, cited_ids} so each one can be checked against the records it cites; the code
then flattens them to the contract's plain strings plus `citations`.

`Draft` is the Pydantic mirror of TOOL_SCHEMA used to validate whatever comes back; a test
keeps the three (contract, schema, Draft) in sync.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts import ClinicalSummary

TOOL_NAME = "record_summary"
CODE_FIELDS = {
    "patient_id",
    "word_count",
    "confidence",
    "disclaimer",
    "model",
    "prompt_version",
    "generated_at",
}
LLM_FIELDS = [name for name in ClinicalSummary.model_fields if name not in CODE_FIELDS]
MEDIA_TYPES = ["imaging", "lab", "document", "note"]

_CLAIM = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "Short phrase, no ids inside."},
        "cited_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Bracketed ids this claim comes from, copied exactly.",
        },
    },
    "required": ["text", "cited_ids"],
    "additionalProperties": False,
}

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "chief_concern": {
            "type": "string",
            "description": "One sentence: the most recent or most significant reason for care.",
        },
        "key_diagnoses": {
            "type": "array",
            "items": _CLAIM,
            "description": "Up to 5 diagnoses stated in the records.",
        },
        "recent_media": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "type": {"type": "string", "enum": MEDIA_TYPES},
                    "date": {"type": "string", "description": "YYYY-MM-DD, or empty if undated."},
                    "note": {"type": "string", "description": "One short finding."},
                },
                "required": ["resource_id", "type", "date", "note"],
                "additionalProperties": False,
            },
            "description": "Up to 5 most recent imaging or lab reports.",
        },
        "anomalies": {
            "type": "array",
            "items": _CLAIM,
            "description": "Abnormal labs and abnormal imaging findings, each with value and date.",
        },
        "citations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every id cited anywhere above.",
        },
    },
    "required": LLM_FIELDS,
    "additionalProperties": False,
}

TOOL = {
    "name": TOOL_NAME,
    "description": "Record the grounded clinical summary of the patient's records.",
    "strict": True,
    "input_schema": TOOL_SCHEMA,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(_Strict):
    text: str = Field(min_length=1)
    cited_ids: list[str] = Field(default_factory=list)


class DraftMedia(_Strict):
    resource_id: str
    type: Literal["imaging", "lab", "document", "note"]
    date: str = ""
    note: str


class Draft(_Strict):
    """What the model returns, before grounding checks and flattening."""

    chief_concern: str = Field(min_length=1)
    key_diagnoses: list[Claim] = Field(default_factory=list)
    recent_media: list[DraftMedia] = Field(default_factory=list)
    anomalies: list[Claim] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
