"""Evaluation harness: search quality and latency, summary quality.

    python -m eval.run                       # writes eval/results.md (+ eval/summary_results.md)
    python -m eval.run --json                # also prints the metrics as JSON
    python -m eval.run --queries eval/queries.fixtures.yaml

Search: for each labelled query, precision@5 (with the ceiling the label count allows),
recall@5 and reciprocal rank of the first relevant hit, plus p50/p95 latency. The label
file follows the data: eval/queries.yaml for pipeline output, eval/queries.fixtures.yaml
when FIXTURE_MODE=1, or whatever --queries names.

Summaries: Dev A's `eval.summary_eval.run_summary_eval()` re-runs the summarize stage
twice (the second must make zero LLM calls) and measures word budget, disclaimer,
citation resolution, diagnosis support and anomaly recall across all patients. On top
of that, a per-patient grounding table checks that every citation and every
recent_media item points at a resource in the patient's bundle.
"""

import argparse
import datetime as dt
import json
import logging
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from app.config import get_settings
from app.contracts import DISCLAIMER, ClinicalSummary
from app.db import connect
from app.search.service import SearchService
from eval.summary_eval import RESULTS as SUMMARY_RESULTS
from eval.summary_eval import SummaryMetrics, run_summary_eval

log = logging.getLogger("eval")

HERE = Path(__file__).resolve().parent
QUERIES = HERE / "queries.yaml"
QUERIES_FIXTURES = HERE / "queries.fixtures.yaml"
RESULTS = HERE / "results.md"
K = 5


@dataclass
class QueryResult:
    query: str
    relevant: int
    hits: int
    precision: float
    recall: float
    reciprocal_rank: float
    took_ms: int
    top: list[str]


@dataclass
class SummaryResult:
    patient_id: str
    word_count: int
    citations: int
    unresolved_citations: int
    uncited_media: int
    passed: bool
    problems: list[str]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def default_queries_path() -> Path:
    return QUERIES_FIXTURES if get_settings().fixture_mode else QUERIES


def evaluate_search(service: SearchService, queries: list[dict]) -> list[QueryResult]:
    out = []
    for item in queries:
        relevant = set(item.get("relevant") or [])
        resp = service.search(item["query"], top_k=K)
        ids = [r.resource_id for r in resp.results]
        hits = [i for i in ids if i in relevant]
        first = next((rank for rank, i in enumerate(ids, 1) if i in relevant), None)
        out.append(
            QueryResult(
                query=item["query"],
                relevant=len(relevant),
                hits=len(hits),
                precision=len(hits) / K if relevant else 0.0,
                recall=len(hits) / len(relevant) if relevant else 0.0,
                reciprocal_rank=1 / first if first else 0.0,
                took_ms=resp.took_ms,
                top=ids,
            )
        )
    return out


# ---------------------------------------------------------------------------
# summaries: per-patient grounding (complements Dev A's aggregate metrics)
# ---------------------------------------------------------------------------


def evaluate_summaries(service: SearchService) -> list[SummaryResult]:
    with connect(service.db_path) as conn:
        bundles = {
            r["patient_id"]: json.loads(r["bundle_json"])
            for r in conn.execute("SELECT patient_id, bundle_json FROM bundles")
        }
    return [check_summary(s, bundles.get(pid, {})) for pid, s in sorted(service.summaries.items())]


def check_summary(summary: ClinicalSummary, bundle: dict) -> SummaryResult:
    ids = {
        f"{e['resource']['resourceType']}/{e['resource']['id']}"
        for e in bundle.get("entry", [])
        if e.get("resource", {}).get("id")
    }
    problems = []
    unresolved = [c for c in summary.citations if c not in ids]
    if unresolved:
        problems.append(f"{len(unresolved)} citation(s) not in bundle")
    uncited = [
        m.resource_id for m in summary.recent_media if m.resource_id not in summary.citations
    ]
    if uncited:
        problems.append(f"{len(uncited)} recent_media item(s) not cited")
    if not summary.citations:
        problems.append("no citations")
    if summary.word_count > 200:
        problems.append(f"{summary.word_count} words (limit 200)")
    if summary.disclaimer != DISCLAIMER:
        problems.append("disclaimer missing or altered")
    if not summary.chief_concern.strip():
        problems.append("empty chief concern")
    # No "key diagnoses required" check: a healthy patient at a wellness visit has none.
    return SummaryResult(
        patient_id=summary.patient_id,
        word_count=summary.word_count,
        citations=len(summary.citations),
        unresolved_citations=len(unresolved),
        uncited_media=len(uncited),
        passed=not problems,
        problems=problems,
    )


# ---------------------------------------------------------------------------
# aggregation and report
# ---------------------------------------------------------------------------


def percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def aggregate(
    search: list[QueryResult], summaries: list[SummaryResult], agg: SummaryMetrics | None
) -> dict:
    lat = [q.took_ms for q in search]
    mean = statistics.fmean
    totals = {
        "queries": len(search),
        "precision_at_5": round(mean(q.precision for q in search), 3) if search else 0,
        "precision_ceiling": round(mean(min(q.relevant, K) / K for q in search), 3)
        if search
        else 0,
        "recall_at_5": round(mean(q.recall for q in search), 3) if search else 0,
        "mrr": round(mean(q.reciprocal_rank for q in search), 3) if search else 0,
        "latency_p50_ms": percentile(lat, 50),
        "latency_p95_ms": percentile(lat, 95),
        "latency_max_ms": max(lat, default=0),
        "under_2s": all(ms < 2000 for ms in lat),
        "summaries": len(summaries),
        "summaries_passed": sum(s.passed for s in summaries),
    }
    if agg:
        totals.update(
            {
                "summary_patients": agg.patients,
                "summary_max_words": agg.max_words,
                "summary_mean_words": round(agg.mean_words, 1),
                "disclaimer_pct": round(agg.disclaimer_pct, 1),
                "citations_resolving_pct": round(agg.citations_resolving_pct, 1),
                "diagnoses_supported_pct": round(agg.diagnoses_supported_pct, 1),
                "anomaly_recall_pct": round(agg.anomaly_recall, 1),
                "confidence": dict(agg.confidence),
                "second_run_llm_calls": agg.second_run_calls,
            }
        )
    return totals


def render(
    service: SearchService,
    search: list[QueryResult],
    summaries: list[SummaryResult],
    totals: dict,
    queries_path: Path,
    model: str | None,
) -> str:
    s = get_settings()
    lines = [
        "# Evaluation results",
        "",
        f"Generated {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC · embed model `{s.embed_model}`"
        f" · LLM provider `{s.llm_provider}`"
        f"{f' · summary model `{model}`' if model else ''}"
        f" · {service.size} indexed chunks, {len(service.patients)} patients"
        f" · labels `{queries_path.name}`"
        f"{' · fixture mode' if s.fixture_mode else ''}",
        "",
        "## Search",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Precision@{K} | {totals['precision_at_5']:.2f}"
        f" (ceiling {totals['precision_ceiling']:.2f} given the labels per query) |",
        f"| Recall@{K} | {totals['recall_at_5']:.2f} |",
        f"| MRR | {totals['mrr']:.2f} |",
        f"| Latency p50 / p95 / max | {totals['latency_p50_ms']} / {totals['latency_p95_ms']}"
        f" / {totals['latency_max_ms']} ms |",
        f"| All queries under 2 s | {'yes' if totals['under_2s'] else 'NO'} |",
        "",
        "| Query | Relevant | Hits@5 | P@5 | R@5 | RR | ms |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for q in search:
        lines.append(
            f"| {q.query} | {q.relevant} | {q.hits} | {q.precision:.2f} | {q.recall:.2f}"
            f" | {q.reciprocal_rank:.2f} | {q.took_ms} |"
        )
    lines += ["", "## Summaries", ""]
    if "summary_patients" in totals:
        conf = ", ".join(f"{k} {totals['confidence'].get(k, 0)}" for k in ("high", "medium", "low"))
        lines += [
            "Aggregate checks from `eval/summary_eval.py` (the summarize stage is run twice;"
            " the second run must make no LLM calls):",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Patients summarized | {totals['summary_patients']} |",
            f"| Word count, max / mean (limit 200) | {totals['summary_max_words']}"
            f" / {totals['summary_mean_words']} |",
            f"| Disclaimer present | {totals['disclaimer_pct']:.0f}% |",
            "| Citations resolving to a bundle resource |"
            f" {totals['citations_resolving_pct']:.0f}% |",
            f"| Diagnoses supported by cited records | {totals['diagnoses_supported_pct']:.0f}% |",
            f"| Anomaly recall (flagged labs mentioned) | {totals['anomaly_recall_pct']:.0f}% |",
            f"| Confidence | {conf} |",
            f"| LLM calls on a second run | {totals['second_run_llm_calls']} |",
            "",
        ]
    lines += [
        f"Per-patient grounding: {totals['summaries_passed']} of {totals['summaries']} summaries"
        " pass every check (citations resolve, recent_media cited, ≤ 200 words, disclaimer"
        " unchanged, chief concern present).",
        "",
        "| Patient | Words | Citations | Unresolved | Uncited media | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in summaries:
        verdict = "pass" if r.passed else "FAIL: " + "; ".join(r.problems)
        lines.append(
            f"| {r.patient_id} | {r.word_count} | {r.citations} | {r.unresolved_citations}"
            f" | {r.uncited_media} | {verdict} |"
        )
    lines += [
        "",
        "Automated checks cannot judge clinical accuracy. `docs/summary_review.md` is the"
        " manual review template a clinician fills in next to the source records.",
        "",
    ]
    return "\n".join(lines)


def run(
    output: Path = RESULTS,
    queries_path: Path | None = None,
    summary_eval: bool = True,
) -> dict:
    queries_path = queries_path or default_queries_path()
    queries = yaml.safe_load(queries_path.read_text(encoding="utf-8"))["queries"]

    agg = model = None
    if summary_eval:
        s = get_settings()
        # Under the mock provider the numbers come from the cache but the model label would
        # read "mock", so keep eval/summary_results.md as the last real-model run.
        real = s.llm_provider != "mock"
        out = SUMMARY_RESULTS if real else Path(tempfile.mkdtemp()) / "summary_results.md"
        agg = run_summary_eval(out)  # may add summaries; build the service after it
        model = s.llm_model if real else "mock (cached real-model summaries)"
    service = SearchService()
    search = evaluate_search(service, queries)
    summaries = evaluate_summaries(service)
    totals = aggregate(search, summaries, agg)
    output.write_text(
        render(service, search, summaries, totals, queries_path, model), encoding="utf-8"
    )
    return {
        "totals": totals,
        "search": [asdict(q) for q in search],
        "summaries": [asdict(s) for s in summaries],
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print metrics as JSON")
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--queries", type=Path, default=None, help="label file to use")
    parser.add_argument(
        "--no-summary-eval", action="store_true", help="skip eval.summary_eval (search only)"
    )
    args = parser.parse_args(argv)
    report = run(args.out, args.queries, summary_eval=not args.no_summary_eval)
    t = report["totals"]
    print(
        f"search: P@5 {t['precision_at_5']:.2f} (ceiling {t['precision_ceiling']:.2f})"
        f"  R@5 {t['recall_at_5']:.2f}  MRR {t['mrr']:.2f}"
        f"  p95 {t['latency_p95_ms']} ms  under 2s: {t['under_2s']}"
    )
    if "summary_patients" in t:
        print(
            f"summaries: {t['summary_patients']} patients, citations resolve"
            f" {t['citations_resolving_pct']:.0f}%, diagnoses supported"
            f" {t['diagnoses_supported_pct']:.0f}%, anomaly recall {t['anomaly_recall_pct']:.0f}%,"
            f" second-run LLM calls {t['second_run_llm_calls']}"
        )
    print(f"grounding: {t['summaries_passed']}/{t['summaries']} summaries pass")
    print(f"wrote {args.out}")
    if args.json:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
