"""Stage 3: decide which records belong to the same person.

Records are processed in a fixed order (JSON, then CSV, then notes, each in file order) and
compared with the patients found so far:

a. Same normalized MRN and compatible demographics (DOB equal or one missing, and name
   similarity >= MRN_MATCH_MIN_SIMILARITY) -> same patient.
b. No patient with that MRN, but one with exactly the same DOB and name similarity >=
   CROSS_MRN_MIN_SIMILARITY -> same patient. The new MRN is added to source_ids
   (`mrn_merged`); the primary MRN stays the first one seen.
c. Same MRN, but DOB differs AND name similarity < MRN_MATCH_MIN_SIMILARITY -> a different
   person. Both patients get an `mrn_conflict` audit and the newcomer gets a suffixed id
   (p-0003-b), so the conflict is visible in every downstream id.
   Same MRN with only one of the two disagreeing is treated as the same patient and
   audited as `demographics_mismatch` for a human to review.
Otherwise the record starts a new patient: p-0001, p-0002, ... in processing order.

Names are compared with rapidfuzz token_sort_ratio on lower-cased names after common
nicknames are expanded (Jon -> Jonathan), so "Jon Smith" and "Jonathan Smith" score 100
instead of ~80. A side without a name counts as compatible for rule a and never matches
rule b. A record with no MRN, no DOB and no name cannot be linked (patient_id None).
"""

from dataclasses import dataclass, field, replace
from datetime import date

from rapidfuzz import fuzz

from app.contracts import AuditEntry, Gender
from app.ingest import audit as rules
from app.ingest.audit import AuditContext, make_audit
from app.ingest.normalizers import NormRecord

MRN_MATCH_MIN_SIMILARITY = 70  # rules a and c
CROSS_MRN_MIN_SIMILARITY = 85  # rule b
SOURCE_ORDER = {".json": 0, ".csv": 1, ".txt": 2}

NICKNAMES = {
    "jon": "jonathan", "johnny": "john", "bill": "william", "will": "william",
    "bob": "robert", "rob": "robert", "bobby": "robert", "mike": "michael",
    "jim": "james", "jimmy": "james", "tom": "thomas", "dick": "richard", "rick": "richard",
    "dave": "david", "dan": "daniel", "joe": "joseph", "chris": "christopher",
    "matt": "matthew", "steve": "steven", "tony": "anthony", "ed": "edward",
    "liz": "elizabeth", "beth": "elizabeth", "kate": "katherine", "katie": "katherine",
    "kathy": "katherine", "sue": "susan", "jen": "jennifer", "jenny": "jennifer",
    "meg": "margaret", "peggy": "margaret", "patty": "patricia", "trish": "patricia",
    "becky": "rebecca", "abby": "abigail", "sam": "samuel",
}  # fmt: skip


def name_key(given: str | None, family: str | None) -> str | None:
    tokens = [t for t in f"{given or ''} {family or ''}".lower().split() if t]
    return " ".join(NICKNAMES.get(t, t) for t in tokens) or None


def similarity(a: str | None, b: str | None) -> float | None:
    """Name similarity 0-100, or None when either name is unknown."""
    if not a or not b:
        return None
    return fuzz.token_sort_ratio(a, b)


@dataclass
class PatientCluster:
    """A canonical patient being assembled from the records linked to it."""

    patient_id: str
    mrns: list[str]
    given: str | None
    family: str | None
    dob: date | None
    gender: Gender
    first_record_id: str
    first_source_file: str
    names: list[str] = field(default_factory=list)  # comparison keys seen so far
    record_ids: list[str] = field(default_factory=list)
    dob_present: bool = False
    gender_present: bool = False
    audit: list[AuditEntry] = field(default_factory=list)

    def name_similarity(self, key: str | None) -> float | None:
        scores = [s for k in self.names if (s := similarity(key, k)) is not None]
        return max(scores) if scores else None

    def dob_compatible(self, dob: date | None) -> bool:
        return dob is None or self.dob is None or dob == self.dob

    def add(self, record: NormRecord) -> None:
        demo = record.demographics
        self.record_ids.append(record.record_id)
        if demo.mrn and demo.mrn not in self.mrns:
            self.mrns.append(demo.mrn)
        key = name_key(demo.given, demo.family)
        if key and key not in self.names:
            self.names.append(key)
        if self.given is None and self.family is None:
            self.given, self.family = demo.given, demo.family
        self.dob = self.dob or demo.dob
        if self.gender == "unknown":
            self.gender = demo.gender
        self.dob_present |= demo.dob_present
        self.gender_present |= demo.gender_present


def _new_cluster(patient_id: str, record: NormRecord) -> PatientCluster:
    cluster = PatientCluster(
        patient_id=patient_id,
        mrns=[],
        given=None,
        family=None,
        dob=None,
        gender="unknown",
        first_record_id=record.record_id,
        first_source_file=record.source_file,
    )
    cluster.add(record)
    return cluster


def _source_rank(record: NormRecord) -> int:
    suffix = "." + record.source_file.rsplit(".", 1)[-1].lower()
    return SOURCE_ORDER.get(suffix, len(SOURCE_ORDER))


def _patient_audit(
    record: NormRecord, patient_id: str, field_name: str, old: object, new: object, rule: str
) -> AuditEntry:
    ctx = AuditContext(record.record_id, record.source_file, patient_id)
    return make_audit(ctx, field_name, old, new, rule)


class _Resolver:
    """Holds the clusters found so far and applies rules a-c to one record at a time."""

    def __init__(self) -> None:
        self.clusters: list[PatientCluster] = []
        self.audits: list[AuditEntry] = []
        self._next = 1

    def _sequence_id(self) -> str:
        patient_id = f"p-{self._next:04d}"
        self._next += 1
        return patient_id

    def _log(self, cluster: PatientCluster, entry: AuditEntry) -> None:
        cluster.audit.append(entry)
        self.audits.append(entry)

    def assign(self, record: NormRecord) -> str | None:
        demo = record.demographics
        key = name_key(demo.given, demo.family)
        if demo.mrn is None and demo.dob is None and key is None:
            return None
        same_mrn = [c for c in self.clusters if demo.mrn and demo.mrn in c.mrns]
        if same_mrn:
            return self._by_mrn(record, key, same_mrn)
        for cluster in self.clusters:  # rule b
            score = cluster.name_similarity(key)
            if demo.dob and demo.dob == cluster.dob and (score or 0) >= CROSS_MRN_MIN_SIMILARITY:
                if demo.mrn:
                    entry = _patient_audit(
                        record,
                        cluster.patient_id,
                        "mrn",
                        demo.mrn,
                        cluster.mrns[0],
                        rules.MRN_MERGED,
                    )
                    self._log(cluster, entry)
                cluster.add(record)
                return cluster.patient_id
        cluster = _new_cluster(self._sequence_id(), record)
        self.clusters.append(cluster)
        return cluster.patient_id

    def _by_mrn(self, record: NormRecord, key: str | None, same_mrn: list[PatientCluster]) -> str:
        demo = record.demographics
        for cluster in same_mrn:  # rule a
            score = cluster.name_similarity(key)
            name_ok = score is None or score >= MRN_MATCH_MIN_SIMILARITY
            if cluster.dob_compatible(demo.dob) and name_ok:
                cluster.add(record)
                return cluster.patient_id
        for cluster in same_mrn:  # partial mismatch: same person, flag for review
            score = cluster.name_similarity(key)
            name_ok = score is None or score >= MRN_MATCH_MIN_SIMILARITY
            if cluster.dob_compatible(demo.dob) or name_ok:
                entry = _patient_audit(
                    record,
                    cluster.patient_id,
                    "identity",
                    None,
                    f"name score {score}",
                    rules.DEMOGRAPHICS_MISMATCH,
                )
                self._log(cluster, entry)
                cluster.add(record)
                return cluster.patient_id
        return self._conflict(record, same_mrn[0])  # rule c

    def _conflict(self, record: NormRecord, existing: PatientCluster) -> str:
        base = "-".join(existing.patient_id.split("-")[:2])  # p-0003-b -> p-0003
        taken = {c.patient_id for c in self.clusters}
        suffix = next(s for s in "bcdefghijklmnopqrstuvwxyz" if f"{base}-{s}" not in taken)
        cluster = _new_cluster(f"{base}-{suffix}", record)
        self.clusters.append(cluster)
        mrn = record.demographics.mrn
        self._log(
            existing,
            _patient_audit(
                record, existing.patient_id, "mrn", mrn, cluster.patient_id, rules.MRN_CONFLICT
            ),
        )
        self._log(
            cluster,
            _patient_audit(
                record, cluster.patient_id, "mrn", mrn, existing.patient_id, rules.MRN_CONFLICT
            ),
        )
        return cluster.patient_id


def resolve_patients(
    records: list[NormRecord],
) -> tuple[tuple[list[PatientCluster], list[NormRecord]], list[AuditEntry]]:
    """Link every record to a canonical patient. Returns ((patients, records), audits)."""
    resolver = _Resolver()
    linked = []
    for record in sorted(records, key=_source_rank):  # stable: keeps file order
        linked.append(replace(record, patient_id=resolver.assign(record)))
    return (resolver.clusters, linked), resolver.audits
