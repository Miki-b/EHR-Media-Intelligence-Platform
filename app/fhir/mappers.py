"""Map canonical models (Task 1) to FHIR R4 resources as plain dicts.

Rules:
- Ids are UUIDv5 in the shared namespace uuid5(NAMESPACE_URL, "ehr-media-intel"), keyed
  "<ResourceType>/<stable key>", so re-runs give identical ids (decision 4) and match the
  convention of the search fixtures. References use "urn:uuid:<id>" (decision 3).
- Patient: one identifier per MRN in source_ids (system urn:ehr-media-intel:mrn), name,
  gender, birthDate when known.
- document and note records -> DocumentReference: LOINC type 18842-5 "Discharge summary"
  when the title says discharge, else 11506-3 "Progress note"; the title goes in
  `description` and the text in content[].attachment.data (base64, text/plain). The date
  is an instant, so a date-only value is written at 12:00Z (no timezone shift to the
  previous day).
- imaging records -> DiagnosticReport (category RAD); `conclusion` is the report's
  IMPRESSION section, the full text goes in presentedForm. Codes are LOINC only where a
  verified code exists; otherwise the CodeableConcept carries text only.
- lab records -> Observation (category laboratory) with LOINC code, valueQuantity (UCUM
  where the unit is known), numeric referenceRange low/high (text when the range is not
  "lo-hi") and interpretation H/L/N. A lab whose source had no test name and no LOINC code
  gets no `code` at all, so FHIR validation reports it instead of the mapper inventing one.
- Labs are grouped per patient per date into one DiagnosticReport (category LAB) whose
  result[] lists the Observations; search reads lab values through that report. Its
  conclusion names only the abnormal values; there is no presentedForm, so each value
  appears once in the searchable text.
"""

import base64
import datetime as dt
import re
import uuid
from typing import Any

from app.contracts import CanonicalPatient, CanonicalRecord
from app.ingest import audit as ingest_rules

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ehr-media-intel")
MRN_SYSTEM = "urn:ehr-media-intel:mrn"
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
INTERPRETATION = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
REPORT_CATEGORY = "http://terminology.hl7.org/CodeSystem/v2-0074"

DISCHARGE_SUMMARY = ("18842-5", "Discharge summary")
PROGRESS_NOTE = ("11506-3", "Progress note")
LAB_REPORT = ("11502-2", "Laboratory report")
IMAGING_CODES = {"chest x-ray 2 views": ("36643-5", "XR Chest 2 Views")}
LOINC_DISPLAY = {
    "4548-4": "Hemoglobin A1c/Hemoglobin.total in Blood",
    "2345-7": "Glucose [Mass/volume] in Serum or Plasma",
    "2160-0": "Creatinine [Mass/volume] in Serum or Plasma",
    "718-7": "Hemoglobin [Mass/volume] in Blood",
    "6690-2": "Leukocytes [#/volume] in Blood by Automated count",
    "10839-9": "Troponin I.cardiac [Mass/volume] in Serum or Plasma",
    "2823-3": "Potassium [Moles/volume] in Serum or Plasma",
}
UCUM_CODES = {
    "%": "%",
    "mg/dL": "mg/dL",
    "g/dL": "g/dL",
    "ng/mL": "ng/mL",
    "mmol/L": "mmol/L",
    "pg/mL": "pg/mL",
    "10^3/uL": "10*3/uL",
}
FLAG_DISPLAY = {"H": "High", "L": "Low", "N": "Normal"}
RANGE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
IMPRESSION = re.compile(r"IMPRESSION:\s*(.+)", re.IGNORECASE | re.DOTALL)

Resource = dict[str, Any]


def resource_id(resource_type: str, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{resource_type}/{key}"))


def urn(rid: str) -> dict[str, str]:
    return {"reference": f"urn:uuid:{rid}"}


def ref(resource: Resource) -> str:
    """Citation form used by summaries and search: "ResourceType/<id>"."""
    return f"{resource['resourceType']}/{resource['id']}"


def concept(system: str | None, code: str | None, display: str) -> dict[str, Any]:
    if system and code:
        return {"coding": [{"system": system, "code": code, "display": display}], "text": display}
    return {"text": display}


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def instant(day: dt.date) -> str:
    return f"{day.isoformat()}T12:00:00Z"


def patient_resource(patient: CanonicalPatient) -> Resource:
    resource: Resource = {
        "resourceType": "Patient",
        "id": resource_id("Patient", patient.patient_id),
        "identifier": [{"system": MRN_SYSTEM, "value": mrn} for mrn in patient.source_ids],
        "gender": patient.gender,
    }
    name = {k: v for k, v in (("family", patient.family_name),) if v}
    if patient.given_name:
        name["given"] = patient.given_name.split()
    if name:
        resource["name"] = [name]
    if patient.dob:
        resource["birthDate"] = patient.dob.isoformat()
    return resource


def document_reference(record: CanonicalRecord, patient_rid: str) -> Resource:
    code, display = DISCHARGE_SUMMARY if "discharge" in record.title.lower() else PROGRESS_NOTE
    resource: Resource = {
        "resourceType": "DocumentReference",
        "id": resource_id("DocumentReference", record.record_id),
        "status": "current",
        "type": concept(LOINC, code, display),
        "subject": urn(patient_rid),
        "description": record.title,
        "content": [
            {
                "attachment": {
                    "contentType": "text/plain",
                    "data": b64(record.text),
                    "title": record.title,
                }
            }
        ],
    }
    if record.date:
        resource["date"] = instant(record.date)
    return resource


def imaging_report(record: CanonicalRecord, patient_rid: str) -> Resource:
    code = IMAGING_CODES.get(record.title.lower())
    resource: Resource = {
        "resourceType": "DiagnosticReport",
        "id": resource_id("DiagnosticReport", record.record_id),
        "status": "final",
        "category": [concept(REPORT_CATEGORY, "RAD", "Radiology")],
        "code": concept(LOINC, *code) if code else concept(None, None, record.title),
        "subject": urn(patient_rid),
        "presentedForm": [
            {"contentType": "text/plain", "data": b64(record.text), "title": record.title}
        ],
    }
    if match := IMPRESSION.search(record.text):
        resource["conclusion"] = " ".join(match[1].split())
    if record.date:
        resource["effectiveDateTime"] = record.date.isoformat()
    return resource


def _quantity(value: float, unit: str | None) -> dict[str, Any]:
    quantity: dict[str, Any] = {"value": value}
    if unit:
        quantity["unit"] = unit
        if unit in UCUM_CODES:
            quantity |= {"system": UCUM, "code": UCUM_CODES[unit]}
    return quantity


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def _lab_code(record: CanonicalRecord) -> dict[str, Any] | None:
    if record.code:
        display = LOINC_DISPLAY.get(record.code, record.title)
        return {**concept(LOINC, record.code, display), "text": record.title}
    title_defaulted = any(a.rule == ingest_rules.TITLE_DEFAULTED for a in record.audit)
    return None if title_defaulted else concept(None, None, record.title)


def observation(record: CanonicalRecord, patient_rid: str) -> Resource:
    resource: Resource = {
        "resourceType": "Observation",
        "id": resource_id("Observation", record.record_id),
        "status": "final",
        "category": [concept(OBSERVATION_CATEGORY, "laboratory", "Laboratory")],
        "subject": urn(patient_rid),
    }
    if code := _lab_code(record):
        resource["code"] = code
    if record.date:
        resource["effectiveDateTime"] = record.date.isoformat()
    number = _number(record.value)
    if number is not None:
        resource["valueQuantity"] = _quantity(number, record.unit)
    elif record.value:
        resource["valueString"] = record.value
    if record.ref_range:
        if match := RANGE.match(record.ref_range):
            low, high = float(match[1]), float(match[2])
            resource["referenceRange"] = [
                {"low": _quantity(low, record.unit), "high": _quantity(high, record.unit)}
            ]
        else:
            resource["referenceRange"] = [{"text": record.ref_range}]
    if record.flag:
        resource["interpretation"] = [
            concept(INTERPRETATION, record.flag, FLAG_DISPLAY[record.flag])
        ]
    return resource


def _lab_summary(record: CanonicalRecord) -> str:
    return " ".join(p for p in (record.title, record.value, record.unit) if p)


def lab_report(
    patient_id: str,
    day: dt.date | None,
    labs: list[tuple[CanonicalRecord, Resource]],
    patient_rid: str,
) -> Resource:
    """One LAB DiagnosticReport for a patient's (already validated) labs on one date."""
    key = f"{patient_id}/labs/{day.isoformat() if day else 'undated'}"
    codes = {r.code for r, _ in labs}
    single_code = len(codes) == 1 and None not in codes  # one test (possibly repeated)
    code = labs[0][1]["code"] if single_code else concept(LOINC, *LAB_REPORT)
    abnormal = [
        f"{_lab_summary(r)} ({FLAG_DISPLAY[r.flag]})" for r, _ in labs if r.flag in ("H", "L")
    ]
    conclusion = (
        "Abnormal: " + "; ".join(abnormal) if abnormal else "All results within reference range."
    )
    resource: Resource = {
        "resourceType": "DiagnosticReport",
        "id": resource_id("DiagnosticReport", key),
        "status": "final",
        "category": [concept(REPORT_CATEGORY, "LAB", "Laboratory")],
        "code": code,
        "subject": urn(patient_rid),
        "result": [urn(obs["id"]) for _, obs in labs],
        "conclusion": conclusion,
    }
    if day:
        resource["effectiveDateTime"] = day.isoformat()
    return resource
