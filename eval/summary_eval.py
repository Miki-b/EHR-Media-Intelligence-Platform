"""Evaluate the stored AI summaries against the bundles they summarize.

    python -m eval.summary_eval        # writes eval/summary_results.md

Runs the summarize stage twice (the first run fills the cache, using whatever provider
LLM_PROVIDER selects; the second must make zero calls), then measures, over all patients:
word count (max/mean), disclaimer present, citations that resolve to a resource in the
bundle, diagnoses supported by the cited records, confidence distribution, and anomaly
recall (flagged H/L labs mentioned in anomalies / flagged labs in the bundle).

Diagnoses are checked against all of the summary's cited records, because the stored
summary keeps citations per summary, not per claim (the per-claim check runs at generation
time, see app/summarize/checks.py). Callable from eval/run.py via run_summary_eval().
"""

import json
import logging
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.contracts import DISCLAIMER, ClinicalSummary
from app.db import connect
from app.summarize.checks import is_supported, mentions_value
from app.summarize.context import build_context, flagged_labs
from app.summarize.llm import LLMClient, get_client
from app.summarize.prompts import PROMPT_VERSION
from app.summarize.run import summarize_all

RESULTS = Path(__file__).resolve().parent / "summary_results.md"


class CountingClient:
    """Wraps a client and counts summarize() calls."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.model = inner.model
        self.calls = 0

    def summarize(self, context: str, feedback: str | None = None):
        self.calls += 1
        return self.inner.summarize(context, feedback)


@dataclass
class SummaryMetrics:
    patients: int
    max_words: int
    mean_words: float
    disclaimer_pct: float
    citations_resolving_pct: float
    diagnoses_supported_pct: float
    confidence: Counter
    flagged_labs: int
    flagged_mentioned: int
    second_run_calls: int

    @property
    def anomaly_recall(self) -> float:
        return 100.0 if not self.flagged_labs else 100 * self.flagged_mentioned / self.flagged_labs


def _pct(part: int, whole: int) -> float:
    return 100.0 if whole == 0 else 100 * part / whole


def measure(db_path: Path, second_run_calls: int) -> SummaryMetrics:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT b.bundle_json, s.summary_json FROM bundles b JOIN summaries s"
            " ON s.patient_id = b.patient_id AND s.content_hash = b.content_hash"
            " WHERE b.is_valid = 1 AND s.prompt_version = ? ORDER BY b.patient_id",
            (PROMPT_VERSION,),
        ).fetchall()
    words, cited, resolving, diagnoses, supported = [], 0, 0, 0, 0
    disclaimers, flagged_total, flagged_hit = 0, 0, 0
    confidence: Counter = Counter()
    for bundle_json, summary_json in rows:
        bundle = json.loads(bundle_json)
        summary = ClinicalSummary.model_validate_json(summary_json)
        _, index = build_context(bundle)
        words.append(summary.word_count)
        disclaimers += summary.disclaimer == DISCLAIMER
        confidence[summary.confidence] += 1
        cited += len(summary.citations)
        resolving += sum(c in index for c in summary.citations)
        texts = [
            f"{index[c]['title']}. {index[c]['text']}" for c in summary.citations if c in index
        ]
        diagnoses += len(summary.key_diagnoses)
        supported += sum(is_supported(d, texts) for d in summary.key_diagnoses)
        for lab in flagged_labs(bundle):
            flagged_total += 1
            flagged_hit += any(
                lab.test.lower() in a.lower() and mentions_value(a, lab.value)
                for a in summary.anomalies
            )
    return SummaryMetrics(
        patients=len(rows),
        max_words=max(words, default=0),
        mean_words=sum(words) / len(words) if words else 0.0,
        disclaimer_pct=_pct(disclaimers, len(rows)),
        citations_resolving_pct=_pct(resolving, cited),
        diagnoses_supported_pct=_pct(supported, diagnoses),
        confidence=confidence,
        flagged_labs=flagged_total,
        flagged_mentioned=flagged_hit,
        second_run_calls=second_run_calls,
    )


def render(m: SummaryMetrics, model: str) -> str:
    conf = ", ".join(f"{k} {m.confidence.get(k, 0)}" for k in ("high", "medium", "low"))
    rows = [
        ("Patients summarized", str(m.patients)),
        ("Word count, max / mean (limit 200)", f"{m.max_words} / {m.mean_words:.1f}"),
        ("Disclaimer present", f"{m.disclaimer_pct:.0f}%"),
        ("Citations resolving to a bundle resource", f"{m.citations_resolving_pct:.0f}%"),
        ("Diagnoses supported by cited records", f"{m.diagnoses_supported_pct:.0f}%"),
        ("Confidence", conf),
        (
            "Anomaly recall (flagged labs mentioned)",
            f"{m.anomaly_recall:.0f}% ({m.flagged_mentioned}/{m.flagged_labs})",
        ),
        ("LLM calls on a second run", str(m.second_run_calls)),
    ]
    lines = [
        "# Summary evaluation",
        "",
        f"Model: `{model}`, prompt version `{PROMPT_VERSION}`. Generated by "
        "`python -m eval.summary_eval`.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        *(f"| {name} | {value} |" for name, value in rows),
        "",
        "Automated checks cannot judge clinical accuracy; see docs/summary_review.md for the "
        "manual review template.",
    ]
    return "\n".join(lines) + "\n"


def run_summary_eval(out_path: Path = RESULTS) -> SummaryMetrics:
    settings = get_settings()
    # The eval's own summarize runs report to a scratch file, so the stage's
    # reports/summary_checks.md (issues found at generation time) is left untouched.
    report = Path(tempfile.mkdtemp()) / "summary_checks.md"
    client = CountingClient(get_client(settings))
    summarize_all(settings.db_path, settings.summary_cache_path, report, client=client)
    second = CountingClient(client.inner)
    summarize_all(settings.db_path, settings.summary_cache_path, report, client=second)
    metrics = measure(settings.db_path, second.calls)
    out_path.write_text(render(metrics, client.model), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_summary_eval()
    sys.exit(0)
