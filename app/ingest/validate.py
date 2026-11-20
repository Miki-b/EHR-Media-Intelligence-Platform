"""Stage 5: build CanonicalPatient and CanonicalRecord models, quarantining what cannot be used.

Rules:
- A record dated before its patient's DOB keeps its content, but its date becomes None
  (`date_before_dob`).
- A patient with no usable DOB or gender in any source gets a `field_missing` audit for
  that field (an invalid DOB is already audited as `invalid_date`).
- A record is rejected only when it has no patient link, or no content (empty text).
  Lab rows are structured, so a lab row is always content: a row with an empty test name
  and no value passes through here on purpose and fails FHIR validation in Task 2.
- Anything that raises a pydantic ValidationError is rejected instead of crashing the run.
Rejections are returned as {location, reason} for run.py to write to data/rejected.jsonl.
"""

from dataclasses import dataclass, field, replace

from pydantic import ValidationError

from app.contracts import AuditEntry, CanonicalPatient, CanonicalRecord
from app.ingest import audit as rules
from app.ingest.audit import AuditContext, make_audit
from app.ingest.identity import PatientCluster
from app.ingest.normalizers import NormRecord


@dataclass
class Rejection:
    location: str
    reason: str


@dataclass
class Validated:
    patients: list[CanonicalPatient] = field(default_factory=list)
    records: list[CanonicalRecord] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)


def _reason(error: ValidationError) -> str:
    first = error.errors()[0]
    return f"invalid {'.'.join(map(str, first['loc']))}: {first['msg']}"


def check_dates_against_dob(
    records: list[NormRecord], patients: list[PatientCluster]
) -> tuple[list[NormRecord], list[AuditEntry]]:
    dob_by_patient = {p.patient_id: p.dob for p in patients}
    checked, audits = [], []
    for record in records:
        dob = dob_by_patient.get(record.patient_id or "")
        if record.date and dob and record.date < dob:
            ctx = AuditContext(record.record_id, record.source_file, record.patient_id)
            entry = make_audit(ctx, "date", record.date, None, rules.DATE_BEFORE_DOB)
            record = replace(record, date=None, audit=[*record.audit, entry])
            audits.append(entry)
        checked.append(record)
    return checked, audits


def _missing_field_audits(cluster: PatientCluster) -> list[AuditEntry]:
    ctx = AuditContext(cluster.first_record_id, cluster.first_source_file, cluster.patient_id)
    audits = []
    if cluster.dob is None and not cluster.dob_present:
        audits.append(make_audit(ctx, "dob", None, None, rules.FIELD_MISSING))
    if cluster.gender == "unknown" and not cluster.gender_present:
        audits.append(make_audit(ctx, "gender", None, "unknown", rules.FIELD_MISSING))
    return audits


def build_patient(cluster: PatientCluster, audits: list[AuditEntry]) -> CanonicalPatient:
    return CanonicalPatient(
        patient_id=cluster.patient_id,
        mrn=cluster.mrns[0] if cluster.mrns else "",
        given_name=cluster.given,
        family_name=cluster.family,
        dob=cluster.dob,
        gender=cluster.gender,
        source_ids=cluster.mrns,
        audit=audits,
    )


def build_record(record: NormRecord) -> CanonicalRecord:
    pid = record.patient_id
    return CanonicalRecord(
        record_id=record.record_id,
        patient_id=pid,
        kind=record.kind,
        date=record.date,
        title=record.title,
        text=record.text,
        code=record.code,
        flag=record.flag,
        source_file=record.source_file,
        audit=[a.model_copy(update={"patient_id": pid}) for a in record.audit],
    )


def _reject(record: NormRecord, reason: str, out: Validated, audits: list[AuditEntry]) -> None:
    out.rejections.append(Rejection(record.location, reason))
    ctx = AuditContext(record.record_id, record.source_file, record.patient_id)
    audits.append(make_audit(ctx, "record", None, reason, rules.RECORD_REJECTED))


def validate(
    patients: list[PatientCluster], records: list[NormRecord]
) -> tuple[Validated, list[AuditEntry]]:
    out = Validated()
    records, audits = check_dates_against_dob(records, patients)
    valid_ids = set()
    for cluster in patients:
        missing = _missing_field_audits(cluster)
        audits += missing
        try:
            out.patients.append(build_patient(cluster, cluster.audit + missing))
            valid_ids.add(cluster.patient_id)
        except ValidationError as error:
            out.rejections.append(Rejection(cluster.patient_id, _reason(error)))
    for record in records:
        if record.patient_id is None:
            _reject(record, "no patient link", out, audits)
        elif record.patient_id not in valid_ids:
            _reject(record, "patient failed validation", out, audits)
        elif not record.text and record.kind != "lab":
            _reject(record, "no content", out, audits)
        else:
            try:
                out.records.append(build_record(record))
            except ValidationError as error:
                _reject(record, _reason(error), out, audits)
    return out, audits
