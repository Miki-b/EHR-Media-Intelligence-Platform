"""Turn FHIR bundles and AI summaries into searchable chunks.

One chunk per DocumentReference and DiagnosticReport in a bundle, plus one per
ClinicalSummary. Observations are not indexed on their own: the contract only
allows DocumentReference, DiagnosticReport and Summary in search results, so
each lab value is folded into the text of the report that references it.

Long texts are split into overlapping word windows because all-MiniLM-L6-v2
truncates input at 256 word pieces; splitting keeps the tail of a long note
searchable. Ranking later collapses windows back to one hit per resource.
"""

import base64
import binascii
import datetime as dt
from dataclasses import dataclass
from typing import Any

from dateutil.parser import isoparse

from app.contracts import ClinicalSummary, ResourceType

MAX_WORDS = 180
OVERLAP_WORDS = 30

_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/fhir")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # unique in the index, e.g. "DiagnosticReport/<uuid>#0"
    patient_id: str
    resource_id: str  # "ResourceType/<id>", matches summary citations
    resource_type: ResourceType
    date: dt.date | None
    title: str
    text: str


# ---------------------------------------------------------------------------
# FHIR helpers
# ---------------------------------------------------------------------------


def parse_fhir_date(value: Any) -> dt.date | None:
    """FHIR date/dateTime/instant to a date. Partial dates ("2025", "2025-03")
    resolve to the first day; unparsable values become None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return isoparse(value).date()
    except (ValueError, OverflowError):
        return None


def attachment_text(attachment: dict | None) -> str | None:
    """Decode an Attachment's inline data when it is text; otherwise None."""
    if not attachment:
        return None
    data = attachment.get("data")
    content_type = (attachment.get("contentType") or "text/plain").lower()
    if not data or not content_type.startswith(_TEXT_TYPES):
        return None
    try:
        return base64.b64decode(data, validate=True).decode("utf-8", errors="replace").strip()
    except (binascii.Error, ValueError):
        return None


def codeable_text(concept: dict | None) -> str | None:
    if not concept:
        return None
    if concept.get("text"):
        return concept["text"]
    for coding in concept.get("coding", []):
        if coding.get("display"):
            return coding["display"]
    return None


def resource_ref(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _resources_by_urn(bundle: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in bundle.get("entry", []):
        resource = entry.get("resource")
        if not resource:
            continue
        if entry.get("fullUrl"):
            out[entry["fullUrl"]] = resource
        if resource.get("id"):
            out[f"urn:uuid:{resource['id']}"] = resource
            out[resource_ref(resource)] = resource
    return out


def _quantity(q: dict | None) -> str | None:
    if not q or q.get("value") is None:
        return None
    value = q["value"]
    unit = q.get("unit") or q.get("code") or ""
    return f"{value:g} {unit}".strip() if isinstance(value, int | float) else f"{value} {unit}"


def observation_line(obs: dict) -> str:
    """One line per lab value: 'HbA1c: 9.8 % (High; reference 4-5.6 %)'."""
    name = codeable_text(obs.get("code")) or "Observation"
    value = (
        _quantity(obs.get("valueQuantity"))
        or obs.get("valueString")
        or codeable_text(obs.get("valueCodeableConcept"))
    )
    parts = [f"{name}: {value}" if value else name]
    details = []
    for interp in obs.get("interpretation", []):
        if text := codeable_text(interp):
            details.append(text)
    for rr in obs.get("referenceRange", []):
        low, high = _quantity(rr.get("low")), _quantity(rr.get("high"))
        if low or high:
            details.append(f"reference {low or '?'}-{high or '?'}")
    if details:
        parts.append(f"({'; '.join(details)})")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Per-resource text
# ---------------------------------------------------------------------------


def document_text(doc: dict) -> tuple[str, str]:
    """(title, body) for a DocumentReference."""
    title = doc.get("description") or codeable_text(doc.get("type")) or "Document"
    bodies = [attachment_text(c.get("attachment")) for c in doc.get("content", [])]
    body = "\n".join(b for b in bodies if b)
    if not body:
        titles = [c.get("attachment", {}).get("title") for c in doc.get("content", [])]
        body = "\n".join(t for t in titles if t and t != title)
    kind = codeable_text(doc.get("type"))
    header = title if not kind or kind in title else f"{title} ({kind})"
    return title, "\n".join(p for p in (header, body) if p)


def report_text(report: dict, lookup: dict[str, dict]) -> tuple[str, str]:
    """(title, body) for a DiagnosticReport, with its Observations folded in."""
    title = codeable_text(report.get("code")) or "Diagnostic report"
    category = next(
        (codeable_text(c) for c in report.get("category", []) if codeable_text(c)), None
    )
    header = f"{title} ({category})" if category else title
    parts = [header]
    if report.get("conclusion"):
        parts.append(f"Conclusion: {report['conclusion']}")
    lines = []
    for ref in report.get("result", []):
        obs = lookup.get(ref.get("reference", ""))
        if obs and obs.get("resourceType") == "Observation":
            lines.append(observation_line(obs))
    if lines:
        parts.append("Results:\n" + "\n".join(lines))
    for form in report.get("presentedForm", []):
        if text := attachment_text(form):
            parts.append(text)
    return title, "\n".join(parts)


def summary_text(summary: ClinicalSummary) -> str:
    parts = [f"Chief concern: {summary.chief_concern}"]
    if summary.key_diagnoses:
        parts.append("Diagnoses: " + "; ".join(summary.key_diagnoses))
    if summary.recent_media:
        parts.append(
            "Recent records: "
            + " ".join(f"{m.type} {m.date or ''}: {m.note}".strip() for m in summary.recent_media)
        )
    if summary.anomalies:
        parts.append("Flagged anomalies: " + "; ".join(summary.anomalies))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Splitting and public API
# ---------------------------------------------------------------------------


def split_words(text: str, max_words: int = MAX_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    """Overlapping windows of at most `max_words` words. Short text is one window."""
    words = text.split()
    if len(words) <= max_words:
        return [" ".join(words)] if words else []
    step = max(1, max_words - overlap)
    windows = []
    for start in range(0, len(words), step):
        windows.append(" ".join(words[start : start + max_words]))
        if start + max_words >= len(words):
            break
    return windows


def _make_chunks(
    patient_id: str,
    resource_id: str,
    resource_type: ResourceType,
    date: dt.date | None,
    title: str,
    text: str,
    max_words: int,
    overlap: int,
) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"{resource_id}#{i}",
            patient_id=patient_id,
            resource_id=resource_id,
            resource_type=resource_type,
            date=date,
            title=title,
            text=window,
        )
        for i, window in enumerate(split_words(text, max_words, overlap))
    ]


def chunk_bundle(
    patient_id: str,
    bundle: dict,
    max_words: int = MAX_WORDS,
    overlap: int = OVERLAP_WORDS,
) -> list[Chunk]:
    """Chunks for every DocumentReference and DiagnosticReport in a bundle."""
    lookup = _resources_by_urn(bundle)
    chunks: list[Chunk] = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") or {}
        rtype = resource.get("resourceType")
        if rtype == "DocumentReference":
            title, text = document_text(resource)
            date = parse_fhir_date(resource.get("date")) or parse_fhir_date(
                (resource.get("context") or {}).get("period", {}).get("start")
            )
        elif rtype == "DiagnosticReport":
            title, text = report_text(resource, lookup)
            date = (
                parse_fhir_date(resource.get("effectiveDateTime"))
                or parse_fhir_date((resource.get("effectivePeriod") or {}).get("start"))
                or parse_fhir_date(resource.get("issued"))
            )
        else:
            continue
        if not resource.get("id") or not text.strip():
            continue
        chunks.extend(
            _make_chunks(
                patient_id, resource_ref(resource), rtype, date, title, text, max_words, overlap
            )
        )
    return chunks


def chunk_summary(
    summary: ClinicalSummary,
    max_words: int = MAX_WORDS,
    overlap: int = OVERLAP_WORDS,
) -> list[Chunk]:
    """One chunk (or a few windows) for a patient's AI summary."""
    date = summary.generated_at.date() if summary.generated_at else None
    if summary.recent_media:
        dates = [m.date for m in summary.recent_media if m.date]
        date = max(dates) if dates else date
    return _make_chunks(
        summary.patient_id,
        f"Summary/{summary.patient_id}",
        "Summary",
        date,
        f"AI summary: {summary.chief_concern}",
        summary_text(summary),
        max_words,
        overlap,
    )
