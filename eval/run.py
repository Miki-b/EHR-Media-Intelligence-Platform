"""Evaluation harness: search quality and latency, summary grounding.

    python -m eval.run            # writes eval/results.md
    python -m eval.run --json     # also prints the metrics as JSON

Search: for each labelled query in eval/queries.yaml, precision@5, recall@5
and reciprocal rank of the first relevant hit, plus p50/p95 latency.

Summaries: for every stored summary, checks that can be made without a
clinician: every citation resolves to a resource in the patient's bundle,
every recent_media item is cited, the word budget holds, the disclaimer is
present, and the required sections are non-empty.
"""

import argparse
import datetime as dt
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from app.config import get_settings
from app.contracts import DISCLAIMER, ClinicalSummary
from app.db import connect
from app.search.service import SearchService

HERE = Path(__file__).resolve().parent
QUERIES = HERE / "queries.yaml"
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


def evaluate_summaries(service: SearchService) -> list[SummaryResult]:
    out = []
    with connect(service.db_path) as conn:
        bundles = {
            r["patient_id"]: json.loads(r["bundle_json"])
            for r in conn.execute("SELECT patient_id, bundle_json FROM bundles")
        }
    for pid, summary in sorted(service.summaries.items()):
        out.append(check_summary(summary, bundles.get(pid, {})))
    return out


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
    if not summary.key_diagnoses:
        problems.append("no key diagnoses")
    return SummaryResult(
        patient_id=summary.patient_id,
        word_count=summary.word_count,
        citations=len(summary.citations),
        unresolved_citations=len(unresolved),
        uncited_media=len(uncited),
        passed=not problems,
        problems=problems,
    )


def percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def aggregate(search: list[QueryResult], summaries: list[SummaryResult]) -> dict:
    lat = [q.took_ms for q in search]
    return {
        "queries": len(search),
        "precision_at_5": round(statistics.fmean(q.precision for q in search), 3) if search else 0,
        "recall_at_5": round(statistics.fmean(q.recall for q in search), 3) if search else 0,
        "precision_ceiling": round(statistics.fmean(min(q.relevant, K) / K for q in search), 3)
        if search
        else 0,
        "mrr": round(statistics.fmean(q.reciprocal_rank for q in search), 3) if search else 0,
        "latency_p50_ms": percentile(lat, 50),
        "latency_p95_ms": percentile(lat, 95),
        "latency_max_ms": max(lat, default=0),
        "under_2s": all(ms < 2000 for ms in lat),
        "summaries": len(summaries),
        "summaries_passed": sum(s.passed for s in summaries),
    }


def render(
    service: SearchService,
    search: list[QueryResult],
    summaries: list[SummaryResult],
    totals: dict,
) -> str:
    s = get_settings()
    lines = [
        "# Evaluation results",
        "",
        f"Generated {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC · embed model `{s.embed_model}`"
        f" · LLM provider `{s.llm_provider}` · {service.size} indexed chunks,"
        f" {len(service.patients)} patients",
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
    lines += [
        "",
        "## Summary grounding",
        "",
        f"{totals['summaries_passed']} of {totals['summaries']} summaries pass every check.",
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
        "Checks: citations resolve to resources in the patient's bundle; every recent_media"
        " item is cited; at most 200 words; disclaimer unchanged; chief concern and"
        " diagnoses present. Clinical accuracy still needs a human read.",
        "",
    ]
    return "\n".join(lines)


def run(output: Path = RESULTS, queries_path: Path = QUERIES) -> dict:
    service = SearchService()
    queries = yaml.safe_load(queries_path.read_text(encoding="utf-8"))["queries"]
    search = evaluate_search(service, queries)
    summaries = evaluate_summaries(service)
    totals = aggregate(search, summaries)
    output.write_text(render(service, search, summaries, totals), encoding="utf-8")
    return {
        "totals": totals,
        "search": [asdict(q) for q in search],
        "summaries": [asdict(s) for s in summaries],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print metrics as JSON")
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    report = run(args.out)
    t = report["totals"]
    print(
        f"search: P@5 {t['precision_at_5']:.2f}  R@5 {t['recall_at_5']:.2f}  MRR {t['mrr']:.2f}"
        f"  p95 {t['latency_p95_ms']} ms  under 2s: {t['under_2s']}"
    )
    print(f"summaries: {t['summaries_passed']}/{t['summaries']} grounded")
    print(f"wrote {args.out}")
    if args.json:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
