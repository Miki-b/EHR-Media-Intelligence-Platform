"""Stage 2: normalize every field of every RawRecord, auditing each change.

Rules:
- Dates: a trailing time ("T10:15:00", " 06:05") is dropped, then explicit formats are tried
  in order: ISO YYYY-MM-DD, YYYYMMDD, D-Mon-YY(YY), "Month D, YYYY", then slash dates.
  A slash date whose first part is > 12 is DD/MM; otherwise it is read as US MM/DD and, if
  both parts are <= 12 and differ, audited as `ambiguous_date_assumed_us` (decision 5).
  Two-digit years become 20YY unless that is in the future, then 19YY. Future dates,
  dates before 1900 and unparseable strings become None with `invalid_date`.
  Any non-ISO input that parses is audited as `date_normalized`.
- Gender: m/male/1 -> male, f/female/2 -> female, o/other -> other, anything else -> unknown.
  An absent field is not a change, so it is not audited here (see validate.py).
- MRN: keep the digits, pad to 6, prefix "MRN-". No digits -> None with `mrn_missing`.
- Names: "FAMILY, GIVEN" is flipped, spaces collapsed, titles (Dr., Mr., Mrs., Ms.) and
  suffixes (Jr., Sr., II, III) removed, all-upper/all-lower tokens title-cased. The last
  token is the family name, the rest the given name.
- Text: non-breaking/zero-width spaces replaced, CRLF -> LF, runs of spaces/tabs collapsed,
  lines and the whole text stripped.
- Labs: test, value, unit and range are cleaned; a missing LOINC code is inferred from the
  test name; a blank flag is derived from value and reference range when both parse.
  The contract has no value/unit fields, so the lab facts are also written into `text`
  as "Test: value unit (ref lo-hi) flag F", which Task 2 can parse.

Demographics are normalized once per source object (patient_ref) and audited on its first
record, so a JSON patient with five records does not produce five identical audits.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from app.contracts import AuditEntry, Gender, RecordKind
from app.ingest import audit as rules
from app.ingest.audit import AuditContext, make_audit
from app.ingest.parsers import RawKind, RawRecord

MIN_YEAR = 1900
MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]  # fmt: skip
MONTH_NUMBER = {m: i for i, m in enumerate(MONTHS, 1)} | {m[:3]: i for i, m in enumerate(MONTHS, 1)}
MONTH_NUMBER["sept"] = 9

TIME_SUFFIX = re.compile(r"[T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
DAY_MON_YEAR = re.compile(r"^(\d{1,2})[- ]([A-Za-z]{3,9})[- ](\d{2}|\d{4})$")
MONTH_DAY_YEAR = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})$")
SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")

GENDERS: dict[str, Gender] = {
    "m": "male", "male": "male", "1": "male",
    "f": "female", "female": "female", "2": "female",
    "o": "other", "other": "other",
}  # fmt: skip
NAME_TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof"}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
INVISIBLE_SPACES = {" ": " ", " ": " ", " ": " ", "​": "", "﻿": ""}

LOINC_BY_TEST = {
    "hba1c": "4548-4",
    "hemoglobin a1c": "4548-4",
    "glucose": "2345-7",
    "creatinine": "2160-0",
    "hemoglobin": "718-7",
    "hgb": "718-7",
    "wbc": "6690-2",
    "white blood cell count": "6690-2",
    "troponin i": "10839-9",
    "potassium": "2823-3",
}
DEFAULT_TITLES: dict[RecordKind, str] = {
    "lab": "Lab result",
    "imaging": "Imaging report",
    "document": "Clinical document",
    "note": "Clinical note",
}
LAB_FLAGS = {"H", "L", "N"}
REF_RANGE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")


def record_id(location: str) -> str:
    """Deterministic record id from the raw location."""
    return "r-" + hashlib.sha256(location.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def _full_year(year: str, month: int, day: int, today: date) -> int:
    if len(year) == 4:
        return int(year)
    full = 2000 + int(year)
    try:
        if date(full, month, day) > today:
            full -= 100
    except ValueError:
        pass  # invalid day/month; the caller's date() call reports it
    return full


def _month(name: str) -> int:
    number = MONTH_NUMBER.get(name.lower())
    if number is None:
        raise ValueError(f"unknown month {name!r}")
    return number


def parse_date_text(text: str, today: date) -> tuple[date, str, bool]:
    """Parse one date string. Returns (date, format name, ambiguous). Raises ValueError."""
    if m := ISO_DATE.match(text):
        return date(int(m[1]), int(m[2]), int(m[3])), "iso", False
    if m := COMPACT_DATE.match(text):
        return date(int(m[1]), int(m[2]), int(m[3])), "compact", False
    if m := DAY_MON_YEAR.match(text):
        month, day = _month(m[2]), int(m[1])
        return date(_full_year(m[3], month, day, today), month, day), "day_mon_year", False
    if m := MONTH_DAY_YEAR.match(text):
        return date(int(m[3]), _month(m[1]), int(m[2])), "month_day_year", False
    if m := SLASH_DATE.match(text):
        first, second = int(m[1]), int(m[2])
        if first > 12:  # unambiguous DD/MM
            month, day, ambiguous = second, first, False
        else:  # US MM/DD; ambiguous only when the swap would give a different date
            month, day, ambiguous = first, second, second <= 12 and first != second
        return date(_full_year(m[3], month, day, today), month, day), "slash", ambiguous
    raise ValueError(f"no known date format matches {text!r}")


def normalize_date(
    raw: str | None,
    ctx: AuditContext | None = None,
    field: str = "date",
    today: date | None = None,
) -> tuple[date | None, AuditEntry | None]:
    """Normalize a date string; an absent or empty value is returned as None unaudited."""
    if raw is None or not raw.strip():
        return None, None
    today = today or date.today()
    text = " ".join(_replace_invisible(raw).split())
    core = TIME_SUFFIX.sub("", text)
    try:
        value, fmt, ambiguous = parse_date_text(core, today)
    except ValueError:
        return None, make_audit(ctx, field, raw, None, rules.INVALID_DATE)
    if value > today or value.year < MIN_YEAR:
        return None, make_audit(ctx, field, raw, None, rules.INVALID_DATE)
    if ambiguous:
        return value, make_audit(ctx, field, raw, value, rules.AMBIGUOUS_DATE_ASSUMED_US)
    if fmt != "iso":
        return value, make_audit(ctx, field, raw, value, rules.DATE_NORMALIZED)
    return value, None


# ---------------------------------------------------------------------------
# Gender, MRN, names, text
# ---------------------------------------------------------------------------


def normalize_gender(
    raw: str | None, ctx: AuditContext | None = None
) -> tuple[Gender, AuditEntry | None]:
    if raw is None:
        return "unknown", None
    value: Gender = GENDERS.get(raw.strip().lower(), "unknown")
    if value == raw:
        return value, None
    return value, make_audit(ctx, "gender", raw, value, rules.GENDER_NORMALIZED)


def normalize_mrn(
    raw: str | None, ctx: AuditContext | None = None
) -> tuple[str | None, AuditEntry | None]:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None, make_audit(ctx, "mrn", raw, None, rules.MRN_MISSING)
    value = f"MRN-{int(digits):06d}"
    if value == raw:
        return value, None
    return value, make_audit(ctx, "mrn", raw, value, rules.MRN_NORMALIZED)


def _replace_invisible(text: str) -> str:
    for char, replacement in INVISIBLE_SPACES.items():
        text = text.replace(char, replacement)
    return text


def _recase(token: str) -> str:
    """Title-case tokens written all upper or all lower; keep deliberate mixed case."""
    if token.isupper() or token.islower():
        return re.sub(r"[^\W\d_]+", lambda m: m[0].capitalize(), token)
    return token


def normalize_name(
    raw: str | None, ctx: AuditContext | None = None
) -> tuple[tuple[str | None, str | None], AuditEntry | None]:
    """Return ((given, family), audit). An absent or empty name gives (None, None)."""
    if raw is None or not raw.strip():
        return (None, None), None
    text = " ".join(_replace_invisible(raw).split())
    if "," in text:
        family, _, given = text.partition(",")
        text = f"{given} {family}"
    skip = NAME_TITLES | NAME_SUFFIXES
    tokens = [_recase(t) for t in text.split() if t.lower().strip(".") not in skip]
    if not tokens:
        return (None, None), make_audit(ctx, "name", raw, None, rules.NAME_NORMALIZED)
    given, family = " ".join(tokens[:-1]) or None, tokens[-1]
    full = " ".join(tokens)
    if full == raw:
        return (given, family), None
    return (given, family), make_audit(ctx, "name", raw, full, rules.NAME_NORMALIZED)


def clean_text(
    raw: str | None, ctx: AuditContext | None = None, field: str = "text"
) -> tuple[str | None, AuditEntry | None]:
    """Clean free text. Long text fields are audited by length, never by content."""
    if raw is None:
        return None, None
    text = _replace_invisible(raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    value = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if value == raw:
        return value, None
    if field == "text":
        old, new = f"{len(raw)} chars", f"{len(value)} chars"
    else:
        old, new = raw, value
    return value, make_audit(ctx, field, old, new, rules.TEXT_CLEANED)


# ---------------------------------------------------------------------------
# Labs
# ---------------------------------------------------------------------------


def derive_flag(value: str | None, ref_range: str | None) -> str | None:
    """H/L/N from a numeric value and a "lo-hi" range, or None when either does not parse."""
    match = REF_RANGE.match(ref_range or "")
    try:
        number = float(value or "")
    except ValueError:
        return None
    if not match:
        return None
    low, high = float(match[1]), float(match[2])
    return "L" if number < low else "H" if number > high else "N"


def lab_text(
    test: str | None,
    value: str | None,
    unit: str | None,
    ref: str | None,
    flag: str | None,
) -> str:
    """Serialize lab facts as "Test: value unit (ref lo-hi) flag F"; empty if no test/value."""
    if not test and not value:
        return ""
    text = f"{test or 'Unnamed test'}: {' '.join(p for p in (value, unit) if p) or 'no value'}"
    if ref:
        text += f" (ref {ref})"
    if flag:
        text += f" flag {flag}"
    return text


# ---------------------------------------------------------------------------
# Whole records
# ---------------------------------------------------------------------------


@dataclass
class Demographics:
    mrn: str | None
    given: str | None
    family: str | None
    dob: date | None
    gender: Gender
    dob_present: bool  # a raw DOB value existed, even if it was invalid
    gender_present: bool


@dataclass
class NormRecord:
    """A record with every field normalized; patient_id is set by identity.py."""

    record_id: str
    location: str
    source_file: str
    patient_ref: str
    kind: RawKind
    demographics: Demographics
    date: date | None
    title: str
    text: str
    code: str | None = None
    flag: str | None = None
    test: str | None = None
    value: str | None = None
    audit: list[AuditEntry] = field(default_factory=list)
    patient_id: str | None = None


def normalize_demographics(
    raw: RawRecord, ctx: AuditContext, today: date
) -> tuple[Demographics, list[AuditEntry]]:
    mrn, a1 = normalize_mrn(raw.mrn, ctx)
    (given, family), a2 = normalize_name(raw.name, ctx)
    dob, a3 = normalize_date(raw.dob, ctx, field="dob", today=today)
    gender, a4 = normalize_gender(raw.gender, ctx)
    demo = Demographics(
        mrn=mrn,
        given=given,
        family=family,
        dob=dob,
        gender=gender,
        dob_present=bool(raw.dob and raw.dob.strip()),
        gender_present=bool(raw.gender and raw.gender.strip()),
    )
    return demo, [a for a in (a1, a2, a3, a4) if a]


def _normalize_lab(raw: RawRecord, ctx: AuditContext) -> tuple[dict, list[AuditEntry]]:
    audits: list[AuditEntry] = []
    cleaned = {}
    for name in ("test", "value", "unit", "ref_range", "flag", "code"):
        cleaned[name], entry = clean_text(getattr(raw, name), ctx, field=name)
        cleaned[name] = cleaned[name] or None
        audits += [entry] if entry else []
    flag = (cleaned["flag"] or "").upper()
    if flag not in LAB_FLAGS:
        flag = derive_flag(cleaned["value"], cleaned["ref_range"])
        if flag:
            audits.append(make_audit(ctx, "flag", raw.flag, flag, rules.FLAG_DERIVED))
    code = cleaned["code"]
    if code is None and cleaned["test"]:
        code = LOINC_BY_TEST.get(cleaned["test"].lower())
        if code:
            audits.append(make_audit(ctx, "code", None, code, rules.LOINC_INFERRED))
    text = lab_text(cleaned["test"], cleaned["value"], cleaned["unit"], cleaned["ref_range"], flag)
    fields = {"title": cleaned["test"], "text": text, "code": code, "flag": flag}
    return fields | {"test": cleaned["test"], "value": cleaned["value"]}, audits


def normalize_record(
    raw: RawRecord, demographics: Demographics, today: date
) -> tuple[NormRecord, list[AuditEntry]]:
    ctx = AuditContext(record_id(raw.location), raw.source_file)
    if raw.kind == "patient":  # demographics only; identity uses it, then it is dropped
        return _norm(raw, ctx, demographics, None, {"title": "", "text": ""}), []
    audits: list[AuditEntry] = []
    if raw.date is None or not raw.date.strip():
        record_date = None
        audits.append(make_audit(ctx, "date", raw.date, None, rules.FIELD_MISSING))
    else:
        record_date, entry = normalize_date(raw.date, ctx, today=today)
        audits += [entry] if entry else []
    if raw.kind == "lab":
        fields, lab_audits = _normalize_lab(raw, ctx)
        audits += lab_audits
    else:
        title, a1 = clean_text(raw.title, ctx, field="title")
        text, a2 = clean_text(raw.text, ctx)
        audits += [a for a in (a1, a2) if a]
        fields = {"title": title, "text": text or ""}
    if not fields["title"]:
        fields["title"] = DEFAULT_TITLES[raw.kind]
        audits.append(make_audit(ctx, "title", raw.title, fields["title"], rules.TITLE_DEFAULTED))
    return _norm(raw, ctx, demographics, record_date, fields), audits


def _norm(
    raw: RawRecord, ctx: AuditContext, demographics: Demographics, when: date | None, fields: dict
) -> NormRecord:
    return NormRecord(
        record_id=ctx.record_id,
        location=raw.location,
        source_file=raw.source_file,
        patient_ref=raw.patient_ref,
        kind=raw.kind,
        demographics=demographics,
        date=when,
        **fields,
    )


def normalize_records(
    raws: list[RawRecord], today: date | None = None
) -> tuple[list[NormRecord], list[AuditEntry]]:
    """Normalize all records; demographics once per patient_ref, audited on its first record."""
    today = today or date.today()
    seen: dict[str, Demographics] = {}
    records, audits = [], []
    for raw in raws:
        record_audits: list[AuditEntry] = []
        if raw.patient_ref not in seen:
            ctx = AuditContext(record_id(raw.location), raw.source_file)
            seen[raw.patient_ref], record_audits = normalize_demographics(raw, ctx, today)
        record, entries = normalize_record(raw, seen[raw.patient_ref], today)
        record.audit = record_audits + entries
        records.append(record)
        audits += record.audit
    return records, audits
