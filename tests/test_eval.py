"""The eval harness runs offline against the fixtures and writes results.md."""

import json
import re
from pathlib import Path

import yaml

from app.contracts import ClinicalSummary
from app.fixtures import load_fixtures
from app.search.index import build_index
from eval.run import QUERIES, QUERIES_FIXTURES, check_summary, default_queries_path, percentile, run

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
RESOURCE_ID = re.compile(
    r"^(DocumentReference|DiagnosticReport)/[0-9a-f-]{36}$|^Summary/p-\d{4}(-[a-z])?$"
)


def labels(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]


def test_fixture_labels_point_at_fixture_resources():
    ids = set()
    for path in (FIXTURES / "bundles").glob("*.json"):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        ids |= {f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in bundle["entry"]}
        ids.add(f"Summary/{path.stem}")
    for item in labels(QUERIES_FIXTURES):
        assert item["relevant"], f"{item['query']!r} has no labels"
        missing = set(item["relevant"]) - ids
        assert not missing, f"{item['query']!r}: unknown ids {missing}"


def test_pipeline_labels_are_well_formed():
    # Real bundle ids only exist after the pipeline runs, so check shape, not existence.
    items = labels(QUERIES)
    assert len(items) >= 5
    for item in items:
        assert item["relevant"], f"{item['query']!r} has no labels"
        for rid in item["relevant"]:
            assert RESOURCE_ID.match(rid), f"{item['query']!r}: odd id {rid}"
        assert len(set(item["relevant"])) == len(item["relevant"]), "duplicate label"


def test_label_file_follows_fixture_mode(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    assert default_queries_path() == QUERIES
    monkeypatch.setenv("FIXTURE_MODE", "1")
    get_settings.cache_clear()
    assert default_queries_path() == QUERIES_FIXTURES


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


def test_run_search_only_on_fixtures(tmp_path):
    # conftest points DB_PATH / CHROMA_PATH at tmp_path and uses the hash embedder
    load_fixtures(FIXTURES, tmp_path / "test.db")
    build_index(tmp_path / "test.db", tmp_path / "chroma")
    out = tmp_path / "results.md"
    report = run(out, QUERIES_FIXTURES, summary_eval=False)
    totals = report["totals"]
    text = out.read_text(encoding="utf-8")
    assert "## Search" in text and "## Summaries" in text
    assert totals["queries"] == 5
    assert totals["summaries"] == 6
    assert totals["summaries_passed"] == 6
    assert totals["under_2s"]
    # the hash embedder is crude, but exact-term queries must still land
    assert totals["mrr"] >= 0.6
    assert "summary_patients" not in totals


def test_run_with_summary_eval_uses_mock_llm(tmp_path):
    load_fixtures(FIXTURES, tmp_path / "test.db")
    build_index(tmp_path / "test.db", tmp_path / "chroma")
    out = tmp_path / "results.md"
    totals = run(out, QUERIES_FIXTURES, summary_eval=True)["totals"]
    assert totals["summary_patients"] == 6
    assert totals["second_run_llm_calls"] == 0, "cache must absorb the second run"
    assert totals["citations_resolving_pct"] == 100
    assert totals["summaries_passed"] == totals["summaries"] == 6
    assert "Aggregate checks" in out.read_text(encoding="utf-8")
