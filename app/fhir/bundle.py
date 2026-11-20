"""Assemble and validate one FHIR R4 collection Bundle per patient.

Rules:
- Every resource is validated on its own with fhir.resources.R4B before it is added. A
  resource that fails is left out and reported as a ValidationIssue (path + message); the
  rest of the bundle is unaffected, so one broken lab row does not hide a patient.
- Lab Observations are validated before their DiagnosticReport is built, so the report's
  result[] only ever references Observations that are in the bundle. A date group whose
  Observations all failed gets no report.
- The assembled Bundle is validated as a whole; it is marked invalid only if that fails
  (or the Patient itself is invalid, since every resource references it).
- The stored bundle is the validated model's JSON, and content_hash is sha256 of that JSON
  with sorted keys and compact separators (the same function as app/fixtures.py), so the
  Task 3 summary cache sees identical hashes for identical content.
"""

import hashlib
import json
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any

from fhir.resources.R4B import get_fhir_model_class
from pydantic import ValidationError

from app.contracts import CanonicalPatient, CanonicalRecord, ValidationIssue
from app.fhir import mappers
from app.fhir.mappers import Resource


@dataclass
class PatientBundle:
    patient_id: str
    bundle: dict[str, Any]
    content_hash: str
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)


def content_hash(bundle: dict[str, Any]) -> str:
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _issues(patient_id: str, resource: Resource, error: ValidationError) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            patient_id=patient_id,
            resource_type=resource["resourceType"],
            resource_id=mappers.ref(resource),
            path=".".join(str(part) for part in e["loc"]) or None,
            message=e["msg"],
        )
        for e in error.errors()
    ]


def validate_resource(patient_id: str, resource: Resource) -> tuple[Resource | None, list]:
    """Return (validated resource JSON, []) or (None, issues)."""
    model = get_fhir_model_class(resource["resourceType"])
    try:
        parsed = model.model_validate(resource)
    except ValidationError as error:
        return None, _issues(patient_id, resource, error)
    return json.loads(parsed.model_dump_json()), []


def _sort_key(record: CanonicalRecord) -> tuple:
    return (record.date is None, record.date or "", record.kind, record.record_id)


def build_resources(
    patient: CanonicalPatient, records: list[CanonicalRecord], patient_rid: str
) -> tuple[list[Resource], list[ValidationIssue]]:
    """Map and validate every record of one patient. Patient resource not included."""
    pid = patient.patient_id
    resources: list[Resource] = []
    issues: list[ValidationIssue] = []
    labs: list[tuple[CanonicalRecord, Resource]] = []
    for record in sorted(records, key=_sort_key):
        if record.kind == "lab":
            candidate = mappers.observation(record, patient_rid)
        elif record.kind == "imaging":
            candidate = mappers.imaging_report(record, patient_rid)
        else:
            candidate = mappers.document_reference(record, patient_rid)
        valid, found = validate_resource(pid, candidate)
        issues += found
        if valid is None:
            continue
        if record.kind == "lab":
            labs.append((record, valid))
        else:
            resources.append(valid)
    for day, group in groupby(labs, key=lambda pair: pair[0].date):
        report, found = validate_resource(
            pid, mappers.lab_report(pid, day, list(group), patient_rid)
        )
        issues += found
        resources += [report] if report else []
    resources += [obs for _, obs in labs]
    return resources, issues


def build_bundle(patient: CanonicalPatient, records: list[CanonicalRecord]) -> PatientBundle:
    pid = patient.patient_id
    patient_resource, issues = validate_resource(pid, mappers.patient_resource(patient))
    patient_rid = mappers.resource_id("Patient", pid)
    resources, record_issues = build_resources(patient, records, patient_rid)
    issues += record_issues
    entries = [patient_resource] if patient_resource else []
    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "id": mappers.resource_id("Bundle", pid),
        "type": "collection",
        "entry": [
            {"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in [*entries, *resources]
        ],
    }
    validated, bundle_issues = validate_resource(pid, bundle)
    issues += bundle_issues
    is_valid = validated is not None and patient_resource is not None
    final = validated if validated is not None else bundle
    return PatientBundle(pid, final, content_hash(final), is_valid, issues)
