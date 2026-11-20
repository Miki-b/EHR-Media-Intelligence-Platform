"""The eval harness runs offline against the fixtures and writes results.md."""

from pathlib import Path

import yaml

from app.contracts import ClinicalSummary
from app.fixtures import load_fixtures
from app.search.index import build_index
from eval.run import QUERIES, check_summary, percentile, run

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def test_every_labelled_id_exists_in_fixtures():
    ids = set()
    for path in (FIXTURES / "bundles").glob("*.json"):
        import json

        bundle = json.loads(path.read_text(encoding="utf-8"))
        ids |= {f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in bundle["entry"]}
        ids.add(f"Summary/{path.stem}")
    for item in yaml.safe_load(QUERIES.read_text(encoding="utf-8"))["queries"]:
        assert item["relevant"], f"{item['query']!r} has no labels"
        missing = set(item["relevant"]) - ids
        assert not missing, f"{item['query']!r}: unknown ids {missing}"


def test_percentile():
    assert percentile([], 95) == 0
    assert percentile([5], 50) == 5
    assert percentile([1, 2, 3, 4, 100], 50) == 3
    assert percentile([1, 2, 3, 4, 100], 95) == 100


def test_check_summary_flags_problems():
    summary = ClinicalSummary(
        patient_id="p-x",
        chief_concern="x",
        key_diagnoses=["y"],
        citations=["DiagnosticReport/missing"],
        word_count=250,
        disclaimer="nope",
    )
    result = check_summary(summary, {"entry": []})
    assert not result.passed
    assert result.unresolved_citations == 1
    assert any("250 words" in p for p in result.problems)
    assert any("disclaimer" in p for p in result.problems)


def test_run_writes_results_and_meets_targets(tmp_path):
    # conftest points DB_PATH / CHROMA_PATH at tmp_path and uses the hash embedder
    load_fixtures(FIXTURES, tmp_path / "test.db")
    build_index(tmp_path / "test.db", tmp_path / "chroma")
    out = tmp_path / "results.md"
    report = run(out)
    totals = report["totals"]
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "## Search" in text and "## Summary grounding" in text
    assert totals["queries"] == 5
    assert totals["summaries"] == 6
    assert totals["summaries_passed"] == 6
    assert totals["under_2s"]
    # the hash embedder is crude, but exact-term queries must still land
    assert totals["mrr"] >= 0.6
