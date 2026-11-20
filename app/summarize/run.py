"""Task 3 entry point: valid FHIR bundles -> cached, grounded ClinicalSummary per patient.

    python -m app.pipeline summarize             # via the pipeline
    python -m app.summarize.run [--refresh]      # same, --refresh ignores the cache
    python -m app.summarize.run --export         # table -> data/cache/summaries.jsonl

Per patient: cache lookup (after importing any committed JSONL rows); on a miss, build the
context, call the model, validate the draft and run the grounding checks. A draft that is
invalid or over 200 words is retried once with the reason appended; a second failure, or an
API that stays unreachable, falls back to MockClient output with confidence "low". Fallback
summaries are not cached, so the next run tries the real model again.
Writes reports/summary_checks.md (patient_id, source, confidence, issues).
Logs ids, counts and token totals only, never clinical text.
"""

import argparse
import json
import logging
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.config import get_settings
from app.contracts import ClinicalSummary
from app.db import connect
from app.summarize import cache
from app.summarize.checks import check, finalize, mentions_value
from app.summarize.context import FlaggedLab, build_context, flagged_labs
from app.summarize.llm import InvalidOutput, LLMClient, LLMUnavailable, MockClient, get_client
from app.summarize.prompts import PROMPT_VERSION
from app.summarize.tool_schema import Draft

log = logging.getLogger("summarize")
MAX_ATTEMPTS = 2  # first answer + one retry with the error appended


@dataclass
class Outcome:
    patient_id: str
    source: str  # "cache" | "llm" | "fallback"
    confidence: str
    issues: list[str] = field(default_factory=list)


@dataclass
class SummarizeResult:
    outcomes: list[Outcome] = field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    imported: int = 0

    def count(self, source: str) -> int:
        return sum(o.source == source for o in self.outcomes)


def _draft_summary(
    patient_id: str, draft: dict, index: dict, flagged: list[FlaggedLab], model: str
) -> tuple[ClinicalSummary, list[str]]:
    """Validate, ground and finalize one draft. Raises ValidationError / ValueError."""
    result = check(Draft.model_validate(draft), index, flagged)
    return finalize(patient_id, result, model), result.issues


def generate(
    patient_id: str, bundle: dict, client: LLMClient, result: SummarizeResult
) -> tuple[ClinicalSummary, list[str], bool]:
    """(summary, issues, fell_back) for one patient; counts calls and tokens in `result`."""
    context, index = build_context(bundle)
    flagged = flagged_labs(bundle)
    feedback = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log.info("%s llm call (attempt %d)", patient_id, attempt)
        try:
            draft, usage = client.summarize(context, feedback)
        except LLMUnavailable as error:
            log.warning("%s llm unavailable (%s)", patient_id, error)
            break
        except InvalidOutput as error:
            result.llm_calls += 1
            feedback = str(error)
            log.warning("%s invalid answer on attempt %d", patient_id, attempt)
            continue
        result.llm_calls += 1
        result.input_tokens += usage.input_tokens
        result.output_tokens += usage.output_tokens
        try:
            summary, issues = _draft_summary(patient_id, draft, index, flagged, client.model)
            return summary, issues, False
        except (ValidationError, ValueError) as error:
            feedback = _reason(error)
            log.warning("%s invalid answer on attempt %d", patient_id, attempt)
    log.warning("%s falling back to the mock summary", patient_id)
    draft, _ = MockClient().summarize(context)
    result_fields = check(Draft.model_validate(draft), index, flagged)
    summary = finalize(patient_id, result_fields, "mock-fallback", confidence="low")
    return summary, [*result_fields.issues, "llm_fallback"], True


def _reason(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        return f"{'.'.join(map(str, first['loc']))}: {first['msg']}"
    return str(error)


def recheck_cached(summary: ClinicalSummary, index: dict, flagged: list[FlaggedLab]) -> list[str]:
    """Cheap checks on a cached summary: citations still resolve, flagged labs still covered."""
    issues = ["unknown_citation" for cid in summary.citations if cid not in index]
    for lab in flagged:
        if not any(
            lab.test.lower() in a.lower() and mentions_value(a, lab.value)
            for a in summary.anomalies
        ):
            issues.append("anomaly_missing")
    return issues


def _bundles(conn: sqlite3.Connection) -> list[tuple[str, dict, str]]:
    rows = conn.execute(
        "SELECT patient_id, bundle_json, content_hash FROM bundles WHERE is_valid = 1 ORDER BY 1"
    ).fetchall()
    return [(r[0], json.loads(r[1]), r[2]) for r in rows]


def render_report(outcomes: list[Outcome]) -> str:
    confidence = Counter(o.confidence for o in outcomes)
    issues = Counter(i for o in outcomes for i in o.issues)
    lines = [
        "# Summary checks",
        "",
        f"- Patients: {len(outcomes)}; confidence "
        + ", ".join(f"{k} {confidence[k]}" for k in ("high", "medium", "low")),
        "- Issues: " + (", ".join(f"{k} {v}" for k, v in sorted(issues.items())) or "none"),
        "",
        "| Patient | Source | Confidence | Issues |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {o.patient_id} | {o.source} | {o.confidence} | {', '.join(o.issues) or '-'} |"
        for o in outcomes
    ]
    return "\n".join(lines) + "\n"


def summarize_all(
    db_path: Path,
    cache_path: Path,
    report_path: Path,
    client: LLMClient | None = None,
    refresh: bool = False,
) -> SummarizeResult:
    client = client or get_client(get_settings())
    result = SummarizeResult()
    with connect(db_path) as conn:
        result.imported = cache.import_cache(conn, cache_path)
        for patient_id, bundle, digest in _bundles(conn):
            cached = None if refresh else cache.get(conn, patient_id, digest, PROMPT_VERSION)
            if cached is not None:
                log.info("%s cache hit", patient_id)
                _, index = build_context(bundle)
                issues = recheck_cached(cached, index, flagged_labs(bundle))
                result.outcomes.append(Outcome(patient_id, "cache", cached.confidence, issues))
                continue
            log.info("%s cache miss", patient_id)
            summary, issues, fell_back = generate(patient_id, bundle, client, result)
            if not fell_back:
                cache.put(conn, summary, digest)
            source = "fallback" if fell_back else "llm"
            result.outcomes.append(Outcome(patient_id, source, summary.confidence, issues))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result.outcomes), encoding="utf-8")
    log.info(
        "%d patients: %d cache hits (%d imported), %d generated, %d fallbacks; "
        "%d llm calls, %d input + %d output tokens",
        len(result.outcomes),
        result.count("cache"),
        result.imported,
        result.count("llm"),
        result.count("fallback"),
        result.llm_calls,
        result.input_tokens,
        result.output_tokens,
    )
    return result


def main(refresh: bool = False) -> SummarizeResult:
    """Pipeline entry point (`python -m app.pipeline summarize`); takes no CLI flags."""
    settings = get_settings()
    return summarize_all(
        settings.db_path,
        settings.summary_cache_path,
        settings.reports_dir / "summary_checks.md",
        refresh=refresh,
    )


def export() -> int:
    settings = get_settings()
    with connect(settings.db_path) as conn:
        n = cache.export_cache(conn, settings.summary_cache_path)
    log.info("exported %d summaries to %s", n, settings.summary_cache_path)
    return n


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate cached, grounded AI summaries.")
    parser.add_argument("--export", action="store_true", help="write the cache to JSONL")
    parser.add_argument("--refresh", action="store_true", help="ignore cached summaries")
    args = parser.parse_args(argv)
    if args.export:
        export()
    else:
        main(refresh=args.refresh)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(cli())
