"""Turn one FHIR bundle into the text the model reads, plus an index for the checks.

Rules:
- One line per DocumentReference / DiagnosticReport, newest first (undated last):
      [DiagnosticReport/<id>] 2025-03-02 | RAD | Chest X-ray 2 views | <text>
  Categories: DOC (DocumentReference), RAD (imaging report), LAB (lab report).
  Observations are not separate lines: each lab report line lists its values
  ("Glucose: 604 mg/dL (High; ref 70-99)"), so every citation the model can make is a
  DocumentReference or DiagnosticReport, the resource types search and the UI show.
- Base64 attachments are decoded; whitespace is collapsed to one line per resource.
- Each resource's text is cut at MAX_RESOURCE_CHARS; the whole context is capped at about
  MAX_CONTEXT_TOKENS (4 chars/token estimate) by dropping the OLDEST resources first, with
  "N older records omitted" at the end.
- The header carries only age (at the newest record) and gender: no name, MRN or DOB.
- resource_index maps "ResourceType/id" to {type, date, title, text, flag} with the full,
  untruncated text, for the grounding checks.
"""

import base64
import binascii
import datetime as dt
from dataclasses import dataclass
from typing import Any

MAX_RESOURCE_CHARS = 1200
MAX_CONTEXT_TOKENS = 6000
CHARS_PER_TOKEN = 4
FLAG_WORDS = {"H": "High", "L": "Low", "N": "Normal"}
NOTE_LOINC = "11506-3"  # progress note -> MediaItem type "note"


@dataclass(frozen=True)
class FlaggedLab:
    """One H/L Observation, located by the lab report that contains it."""

    report_id: str
    test: str
    value: str
    unit: str
    flag: str
    date: dt.date | None


def _text(concept: dict | None) -> str:
    if not concept:
        return ""
    return concept.get("text") or next(
        (c.get("display", "") for c in concept.get("coding", []) if c.get("display")), ""
    )


def _decode(attachment: dict | None) -> str:
    if not attachment or not attachment.get("data"):
        return ""
    try:
        return base64.b64decode(attachment["data"]).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def _date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _number(value: float | int) -> str:
    return f"{value:g}"


def observation_parts(obs: dict) -> tuple[str, str, str, str | None]:
    """(test, value, unit, flag) of an Observation."""
    quantity = obs.get("valueQuantity") or {}
    value = _number(quantity["value"]) if "value" in quantity else obs.get("valueString", "")
    flag = next(
        (c.get("code") for i in obs.get("interpretation", []) for c in i.get("coding", [])), None
    )
    return _text(obs.get("code")) or "Unnamed test", value, quantity.get("unit", ""), flag


def _observation_text(obs: dict) -> str:
    test, value, unit, flag = observation_parts(obs)
    details = [FLAG_WORDS[flag]] if flag in FLAG_WORDS else []
    for rr in obs.get("referenceRange", []):
        if "low" in rr and "high" in rr:
            details.append(f"ref {_number(rr['low']['value'])}-{_number(rr['high']['value'])}")
        elif rr.get("text"):
            details.append(f"ref {rr['text']}")
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"{test}: {' '.join(p for p in (value, unit) if p)}{suffix}"


def _observations(bundle: dict) -> dict[str, dict]:
    return {
        f"urn:uuid:{e['resource']['id']}": e["resource"]
        for e in bundle.get("entry", [])
        if e["resource"].get("resourceType") == "Observation"
    }


def _report_observations(report: dict, observations: dict[str, dict]) -> list[dict]:
    refs = [r.get("reference", "") for r in report.get("result", [])]
    return [observations[r] for r in refs if r in observations]


def _is_lab(report: dict) -> bool:
    codes = {c.get("code") for cat in report.get("category", []) for c in cat.get("coding", [])}
    return "LAB" in codes


def _entry(resource: dict, observations: dict[str, dict]) -> dict[str, Any] | None:
    """Index entry for a DocumentReference or DiagnosticReport; None for other types."""
    rtype = resource.get("resourceType")
    if rtype == "DocumentReference":
        loinc = {c.get("code") for c in (resource.get("type") or {}).get("coding", [])}
        text = "\n".join(_decode(c.get("attachment")) for c in resource.get("content", []))
        return {
            "type": "note" if NOTE_LOINC in loinc else "document",
            "category": "DOC",
            "date": _date(resource.get("date")),
            "title": resource.get("description") or _text(resource.get("type")) or "Document",
            "text": text,
            "flag": None,
        }
    if rtype == "DiagnosticReport":
        if _is_lab(resource):
            obs = _report_observations(resource, observations)
            flags = [observation_parts(o)[3] for o in obs]
            return {
                "type": "lab",
                "category": "LAB",
                "date": _date(resource.get("effectiveDateTime")),
                "title": _text(resource.get("code")) or "Laboratory report",
                "text": "; ".join(_observation_text(o) for o in obs),
                "flag": next((f for f in flags if f in ("H", "L")), "N" if flags else None),
            }
        forms = "\n".join(_decode(f) for f in resource.get("presentedForm", []))
        return {
            "type": "imaging",
            "category": "RAD",
            "date": _date(resource.get("effectiveDateTime")),
            "title": _text(resource.get("code")) or "Imaging report",
            "text": forms or resource.get("conclusion", ""),
            "flag": None,
        }
    return None


def resource_index(bundle: dict) -> dict[str, dict[str, Any]]:
    observations = _observations(bundle)
    index = {}
    for e in bundle.get("entry", []):
        resource = e["resource"]
        if (entry := _entry(resource, observations)) is not None:
            index[f"{resource['resourceType']}/{resource['id']}"] = entry
    return index


def flagged_labs(bundle: dict) -> list[FlaggedLab]:
    """Every H/L lab value in the bundle, with the id of the report that lists it."""
    observations = _observations(bundle)
    found = []
    for e in bundle.get("entry", []):
        report = e["resource"]
        if report.get("resourceType") != "DiagnosticReport" or not _is_lab(report):
            continue
        for obs in _report_observations(report, observations):
            test, value, unit, flag = observation_parts(obs)
            if flag in ("H", "L"):
                found.append(
                    FlaggedLab(
                        report_id=f"DiagnosticReport/{report['id']}",
                        test=test,
                        value=value,
                        unit=unit,
                        flag=flag,
                        date=_date(obs.get("effectiveDateTime"))
                        or _date(report.get("effectiveDateTime")),
                    )
                )
    return found


def _header(bundle: dict, newest: dt.date | None) -> str:
    patient = next(
        (
            e["resource"]
            for e in bundle.get("entry", [])
            if e["resource"]["resourceType"] == "Patient"
        ),
        {},
    )
    gender = patient.get("gender", "unknown")
    dob = _date(patient.get("birthDate"))
    if dob and newest:
        age = newest.year - dob.year - ((newest.month, newest.day) < (dob.month, dob.day))
        return f"Patient: {age}-year-old, gender {gender}"
    return f"Patient: age not documented, gender {gender}"


def _sort_key(item: tuple[str, dict]) -> tuple:
    rid, entry = item
    return (entry["date"] is None, -(entry["date"].toordinal() if entry["date"] else 0), rid)


def _line(rid: str, entry: dict) -> str:
    text = _one_line(entry["text"])
    if len(text) > MAX_RESOURCE_CHARS:
        text = text[: MAX_RESOURCE_CHARS - 3].rstrip() + "..."
    when = entry["date"].isoformat() if entry["date"] else "undated"
    return f"[{rid}] {when} | {entry['category']} | {_one_line(entry['title'])} | {text}"


def build_context(bundle: dict) -> tuple[str, dict[str, dict[str, Any]]]:
    """(context_text, resource_index) for one patient's bundle."""
    index = resource_index(bundle)
    ordered = sorted(index.items(), key=_sort_key)
    newest = next((e["date"] for _, e in ordered if e["date"]), None)
    header = _header(bundle, newest)
    lines = [_line(rid, entry) for rid, entry in ordered]
    budget = MAX_CONTEXT_TOKENS * CHARS_PER_TOKEN
    omitted = 0
    while lines and len(header) + sum(len(line) + 1 for line in lines) > budget:
        lines.pop()  # newest first, so the last line is the oldest
        omitted += 1
    parts = [header, *lines]
    if omitted:
        parts.append(f"{omitted} older records omitted")
    return "\n".join(parts), index
