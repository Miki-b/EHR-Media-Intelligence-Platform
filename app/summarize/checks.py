"""Grounding and format checks on a model draft, before it becomes a ClinicalSummary.

- Citations exist: every cited id must be in the resource index. A claim citing only
  unknown ids is removed (`unknown_citation`); a claim citing nothing is removed
  (`missing_citation`).
- Diagnoses supported: see "Matching rule" below. An unsupported diagnosis is kept and
  flagged (`unsupported_diagnosis`).
- Media ids and dates: each recent_media id must exist (else removed, `unknown_citation`);
  date and type are always taken from the bundle (`date_corrected` when the date differed).
- Anomalies cross-check: every H/L lab in the bundle must be mentioned (test name and
  value) in anomalies; a missing one gets a rule-based entry
  "<test> <value> <unit> (<flag>) on <date>" citing its report (`anomaly_added`).
- Length: word_count <= 200, else finalize raises and the caller retries.

Confidence: "high" when the only issues are date_corrected; "medium" when there is exactly
one unsupported_diagnosis or anomaly_added; "low" otherwise. The disclaimer is always
injected by the code (finalize), never trusted from the model.

Matching rule (is_supported), applied to each cited record in turn:
1. Bracketed qualifiers are removed from the diagnosis: "Obesity (comorbidity)" -> "Obesity".
2. Known clinical abbreviations (ABBREVIATIONS) are replaced by their expansion in the
   diagnosis and appended after themselves in the record, so either spelling matches.
3. Both sides are lower-cased, tokenized into words and numbers, and each word is stemmed
   with an inflectional S-stemmer (plural -ies/-es/-s, then -ing/-ed, a doubled final
   consonant and a final -e), so "pack-years"/"pack-year" and "tripping"/"tripped" meet.
   Only function words (FUNCTION_WORDS) are ignored; laterality, severity and qualifiers
   such as "left", "severe" or "postoperative" must be supported like any other word.
4. Every content stem of the diagnosis must occur in the record, and every number must
   stay next to at least one of its neighbouring words, in the same order ("type 2",
   "45 pack"), so "type 1 diabetes" is not supported by a record about type 2 diabetes
   that mentions "1" elsewhere.
No fuzzy string similarity: rapidfuzz partial_ratio scored "type 1 diabetes" against
"type 2 diabetes" at 93 and "postoperative anemia" against "postoperative hemoglobin" at
86.5, so any useful threshold accepted both near-misses.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from app.contracts import DISCLAIMER, ClinicalSummary, Confidence, MediaItem
from app.summarize.context import FLAG_WORDS, FlaggedLab
from app.summarize.prompts import PROMPT_VERSION
from app.summarize.tool_schema import Claim, Draft

MAX_WORDS = 200
FUNCTION_WORDS = {
    "a", "an", "the", "of", "on", "in", "at", "to", "for", "and", "or", "with", "by", "from",
    "as", "after", "due", "is", "was", "be",
}  # fmt: skip
TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
WORD = re.compile(r"[a-z0-9]+")
BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
NOT_DOCUMENTED = "not documented"
# Common clinical abbreviations, expanded on both sides before matching, so "Hypertension"
# is supported by a note that says "HTN" (and "CHF" by "congestive heart failure").
ABBREVIATIONS = {
    "chf": "congestive heart failure",
    "hf": "heart failure",
    "t2dm": "type 2 diabetes mellitus",
    "dm": "diabetes mellitus",
    "dka": "diabetic ketoacidosis",
    "copd": "chronic obstructive pulmonary disease",
    "htn": "hypertension",
    "cap": "community-acquired pneumonia",
    "cxr": "chest x-ray",
    "sob": "shortness of breath",
    "hba1c": "hemoglobin a1c",
    "ckd": "chronic kidney disease",
    "mi": "myocardial infarction",
}
ISSUE_SOFT = {"unsupported_diagnosis", "anomaly_added"}


@dataclass
class CheckResult:
    fields: dict[str, Any]
    issues: list[str] = field(default_factory=list)
    confidence: Confidence = "high"


def stem(word: str) -> str:
    """Inflectional S-stemmer (Harman 1991) plus -ing/-ed; numbers are returned unchanged.

    "is", "us" and "ss" endings are kept ("diagnosis", "mellitus", "loss").
    """
    if not word.isalpha() or len(word) <= 3:
        return word
    if word.endswith("ies") and not word.endswith(("eies", "aies")):
        word = word[:-3] + "y"
    elif word.endswith("s") and not word.endswith(("us", "ss", "is")):
        word = word[:-1]  # Harman's "-es -> -e" and "-s -> ''" rules both drop the "s"
    for suffix in ("ing", "ed"):
        base = word[: -len(suffix)]
        if word.endswith(suffix) and len(base) >= 3 and re.search(r"[aeiouy]", base):
            word = base
            if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiouslz":
                word = word[:-1]  # tripp -> trip
            break
    return word[:-1] if len(word) > 4 and word.endswith("e") else word


def _abbreviations(text: str, keep_original: bool) -> str:
    def swap(m: re.Match) -> str:
        expansion = ABBREVIATIONS.get(m[0])
        if expansion is None:
            return m[0]
        return f"{m[0]} {expansion}" if keep_original else expansion

    return WORD.sub(swap, text.lower())


def _tokens(text: str) -> list[str]:
    return [stem(t) for t in TOKEN.findall(text) if t not in FUNCTION_WORDS]


def _number_neighbours(tokens: list[str]) -> list[set[tuple[str, str]]]:
    """For each number, the word pairs that keep it next to one of its neighbours."""
    found = []
    for i, token in enumerate(tokens):
        if token[0].isdigit():
            pairs = set()
            if i > 0:
                pairs.add((tokens[i - 1], token))  # "type 2"
            if i + 1 < len(tokens):
                pairs.add((token, tokens[i + 1]))  # "45 pack", "9.4 g"
            found.append(pairs)
    return found


def is_supported(diagnosis: str, texts: list[str]) -> bool:
    """True when one cited text supports the diagnosis under the matching rule above."""
    needle = _tokens(_abbreviations(BRACKETED.sub(" ", diagnosis), keep_original=False))
    if not needle:
        return False
    words = {t for t in needle if not t[0].isdigit()}
    numbers = _number_neighbours(needle)
    for text in texts:
        haystack = _tokens(_abbreviations(text, keep_original=True))
        adjacent = set(zip(haystack, haystack[1:], strict=False))
        if words <= set(haystack) and all(pairs & adjacent for pairs in numbers):
            return True
    return False


def _grounded(claims: list[Claim], index: dict, issues: list[str]) -> list[Claim]:
    kept = []
    for claim in claims:
        known = [i for i in claim.cited_ids if i in index]
        if known:
            kept.append(claim.model_copy(update={"cited_ids": known}))
        elif claim.text.strip().lower().rstrip(".") == NOT_DOCUMENTED:
            continue  # "Not documented" placeholder: nothing to cite, nothing to show
        else:
            issues.append("unknown_citation" if claim.cited_ids else "missing_citation")
    return kept


def _media(draft: Draft, index: dict, issues: list[str]) -> list[MediaItem]:
    items = []
    for media in draft.recent_media:
        entry = index.get(media.resource_id)
        if entry is None:
            issues.append("unknown_citation")
            continue
        true_date: dt.date | None = entry["date"]
        if (true_date.isoformat() if true_date else "") != media.date:
            issues.append("date_corrected")
        items.append(
            MediaItem(
                resource_id=media.resource_id, type=entry["type"], date=true_date, note=media.note
            )
        )
    return items


def mentions_value(text: str, value: str) -> bool:
    try:
        target = float(value)
    except ValueError:
        return value.lower() in text.lower()
    return any(float(n) == target for n in re.findall(r"-?\d+(?:\.\d+)?", text))


def _mentioned(lab: FlaggedLab, anomalies: list[Claim]) -> bool:
    return any(
        lab.test.lower() in a.text.lower() and mentions_value(a.text, lab.value) for a in anomalies
    )


def rule_based_anomaly(lab: FlaggedLab) -> Claim:
    when = lab.date.isoformat() if lab.date else "undated"
    unit = f" {lab.unit}" if lab.unit else ""
    return Claim(
        text=f"{lab.test} {lab.value}{unit} ({FLAG_WORDS[lab.flag]}) on {when}",
        cited_ids=[lab.report_id],
    )


def confidence_for(issues: list[str]) -> Confidence:
    serious = [i for i in issues if i != "date_corrected"]
    if not serious:
        return "high"
    if len(serious) == 1 and serious[0] in ISSUE_SOFT:
        return "medium"
    return "low"


def check(draft: Draft, index: dict, flagged: list[FlaggedLab]) -> CheckResult:
    """Ground the draft against the bundle; returns contract fields, issues and confidence."""
    issues: list[str] = []
    diagnoses = _grounded(draft.key_diagnoses, index, issues)
    for claim in diagnoses:
        texts = [f"{index[i]['title']}. {index[i]['text']}" for i in claim.cited_ids]
        if not is_supported(claim.text, texts):
            issues.append("unsupported_diagnosis")
    media = _media(draft, index, issues)
    anomalies = _grounded(draft.anomalies, index, issues)
    for lab in flagged:
        if not _mentioned(lab, anomalies):
            anomalies.append(rule_based_anomaly(lab))
            issues.append("anomaly_added")
    cited = [i for c in diagnoses + anomalies for i in c.cited_ids]
    cited += [m.resource_id for m in media]
    cited += [i for i in draft.citations if i in index]
    fields = {
        "chief_concern": draft.chief_concern.strip(),
        "key_diagnoses": [c.text for c in diagnoses],
        "recent_media": media,
        "anomalies": [c.text for c in anomalies],
        "citations": list(dict.fromkeys(cited)),
    }
    return CheckResult(fields, issues, confidence_for(issues))


def word_count(fields: dict[str, Any]) -> int:
    parts = [fields["chief_concern"], *fields["key_diagnoses"], *fields["anomalies"]]
    parts += [m.note for m in fields["recent_media"]]
    return sum(len(p.split()) for p in parts)


def finalize(
    patient_id: str,
    result: CheckResult,
    model: str,
    confidence: Confidence | None = None,
    now: dt.datetime | None = None,
) -> ClinicalSummary:
    """Add the code-filled fields and validate against the contract."""
    count = word_count(result.fields)
    if count > MAX_WORDS:
        raise ValueError(f"summary is {count} words; the limit is {MAX_WORDS}")
    return ClinicalSummary.model_validate(
        {
            **result.fields,
            "patient_id": patient_id,
            "word_count": count,
            "confidence": confidence or result.confidence,
            "disclaimer": DISCLAIMER,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "generated_at": now or dt.datetime.now(dt.UTC),
        }
    )
