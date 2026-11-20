"""Task 3 tests: context, schema, cache, retry, grounding checks, end to end. No network:
MockClient, a scripted StubClient, or a fake standing in for the Anthropic SDK client."""

import base64
import importlib.util
import json
import logging
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.contracts import DISCLAIMER, ClinicalSummary
from app.fhir.bundle import content_hash
from app.fhir.run import build_fhir
from app.ingest.run import ingest
from app.summarize import cache, checks, llm, tool_schema
from app.summarize import run as summarize_run
from app.summarize.context import MAX_RESOURCE_CHARS, build_context, flagged_labs
from app.summarize.llm import InvalidOutput, LLMUnavailable, MockClient, Usage
from app.summarize.prompts import PROMPT_VERSION
from app.summarize.run import summarize_all
from app.summarize.tool_schema import Draft

TODAY = date(2026, 6, 1)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_data.py"


def _generator():
    spec = importlib.util.spec_from_file_location("generate_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CountingClient:
    """MockClient that counts calls."""

    model = "mock"

    def __init__(self) -> None:
        self.calls = 0
        self._mock = MockClient()

    def summarize(self, context, feedback=None):
        self.calls += 1
        return self._mock.summarize(context, feedback)


class StubClient:
    """Returns scripted answers in order: a dict, a callable(context) -> dict, or an exception."""

    model = "stub"

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.feedback: list[str | None] = []

    def summarize(self, context, feedback=None):
        self.calls += 1
        self.feedback.append(feedback)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        draft = answer(context) if callable(answer) else answer
        return draft, Usage(100, 50)


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    """Seed-42 data through Tasks 1 and 2; tests copy the DB before changing it."""
    root = tmp_path_factory.mktemp("summarize")
    _generator().generate(42, root / "raw")
    ingest(root / "raw", root / "ehr.db", root / "rejected.jsonl", today=TODAY)
    build_fhir(root / "ehr.db", root / "validation.md")
    truth = json.loads((root / "raw" / "ground_truth.json").read_text(encoding="utf-8"))
    return {"root": root, "db": root / "ehr.db", "truth": truth}


@pytest.fixture
def db(pipeline, tmp_path) -> Path:
    copy = tmp_path / "ehr.db"
    with sqlite3.connect(pipeline["db"]) as src, sqlite3.connect(copy) as dst:
        src.backup(dst)
    return copy


def _run(db: Path, tmp_path: Path, client, **kwargs):
    return summarize_all(
        db, tmp_path / "summaries.jsonl", tmp_path / "summary_checks.md", client=client, **kwargs
    )


def _bundles(db: Path) -> dict[str, dict]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT patient_id, bundle_json FROM bundles").fetchall()
    return {pid: json.loads(b) for pid, b in rows}


def _summaries(db: Path) -> list[ClinicalSummary]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT summary_json FROM summaries").fetchall()
    return [ClinicalSummary.model_validate_json(r[0]) for r in rows]


def _mock_draft(bundle: dict) -> dict:
    context, _ = build_context(bundle)
    return MockClient().summarize(context)[0]


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def _doc(rid: str, day: str, text: str) -> dict:
    return {
        "resource": {
            "resourceType": "DocumentReference",
            "id": rid,
            "status": "current",
            "description": f"Note {rid}",
            "date": f"{day}T12:00:00Z",
            "content": [
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "data": base64.b64encode(text.encode()).decode(),
                    }
                }
            ],
        }
    }


def _bundle(*entries: dict) -> dict:
    patient = {
        "resource": {
            "resourceType": "Patient",
            "id": "pat",
            "identifier": [{"system": "urn:ehr-media-intel:mrn", "value": "MRN-424242"}],
            "name": [{"family": "Zebulon", "given": ["Quintessa"]}],
            "gender": "female",
            "birthDate": "1970-06-15",
        }
    }
    return {"resourceType": "Bundle", "type": "collection", "entry": [patient, *entries]}


def test_context_is_newest_first_with_bracketed_ids():
    context, index = build_context(
        _bundle(
            _doc("a", "2023-01-05", "old"),
            _doc("b", "2025-03-02", "new"),
            _doc("c", "2024-07-01", "mid"),
        )
    )
    lines = context.splitlines()
    assert lines[0] == "Patient: 54-year-old, gender female"  # age at the newest record
    assert [line.split("]")[0] for line in lines[1:]] == [
        "[DocumentReference/b",
        "[DocumentReference/c",
        "[DocumentReference/a",
    ]
    assert lines[1] == "[DocumentReference/b] 2025-03-02 | DOC | Note b | new"
    assert set(index) == {"DocumentReference/a", "DocumentReference/b", "DocumentReference/c"}


def test_context_has_no_name_mrn_or_dob():
    context, _ = build_context(_bundle(_doc("a", "2025-01-01", "text")))
    for secret in ("Zebulon", "Quintessa", "MRN-424242", "1970-06-15", "1970"):
        assert secret not in context


def test_context_truncates_long_text_and_drops_oldest():
    long_text = "word " * 2000
    _, index = build_context(_bundle(_doc("x", "2025-01-01", long_text)))
    context, _ = build_context(_bundle(_doc("x", "2025-01-01", long_text)))
    line = context.splitlines()[1]
    assert len(line.split(" | ", 3)[3]) == MAX_RESOURCE_CHARS and line.endswith("...")
    assert len(index["DocumentReference/x"]["text"]) > MAX_RESOURCE_CHARS  # index keeps full text
    many = [_doc(f"d{i:02d}", f"2024-01-{i:02d}", long_text) for i in range(1, 29)]
    context, index = build_context(_bundle(*many))
    assert len(context) <= 6000 * 4 + 100
    omitted = int(context.splitlines()[-1].split()[0])
    assert omitted > 0 and context.endswith("older records omitted")
    assert "[DocumentReference/d28]" in context and "[DocumentReference/d01]" not in context
    assert len(index) == 28  # the checks still see every resource


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_tool_schema_fields_match_the_contract():
    contract_llm_fields = set(ClinicalSummary.model_fields) - tool_schema.CODE_FIELDS
    assert set(tool_schema.TOOL_SCHEMA["properties"]) == contract_llm_fields
    assert set(tool_schema.TOOL_SCHEMA["required"]) == contract_llm_fields
    assert set(Draft.model_fields) == contract_llm_fields
    assert tool_schema.TOOL_SCHEMA["additionalProperties"] is False
    assert tool_schema.TOOL["strict"] is True and tool_schema.TOOL["name"] == "record_summary"
    media_props = tool_schema.TOOL_SCHEMA["properties"]["recent_media"]["items"]["properties"]
    assert set(media_props) == set(
        ClinicalSummary.model_fields["recent_media"].annotation.__args__[0].model_fields
    )


# ---------------------------------------------------------------------------
# Anthropic client request shape (fake SDK client, no network)
# ---------------------------------------------------------------------------


class _FakeMessages:
    def __init__(self, response) -> None:
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _anthropic_client(response):
    client = llm.AnthropicClient(
        Settings(llm_provider="anthropic", anthropic_api_key="test", llm_model="claude-haiku-5-5")
    )
    fake = _FakeMessages(response)
    client._client = SimpleNamespace(messages=fake)
    return client, fake


def _response(content, stop_reason="tool_use"):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
    )


def test_anthropic_request_uses_strict_tool_and_no_temperature():
    tool_call = SimpleNamespace(
        type="tool_use", name="record_summary", input={"chief_concern": "x"}
    )
    client, fake = _anthropic_client(_response([tool_call]))
    draft, usage = client.summarize("[DocumentReference/a] 2025-01-01 | DOC | t | x")
    assert draft == {"chief_concern": "x"} and usage == Usage(11, 7)
    sent = fake.kwargs
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["tools"] == [tool_schema.TOOL] and sent["tool_choice"] == {"type": "auto"}
    assert "temperature" not in sent
    assert "<records>" in sent["messages"][0]["content"]
    assert client._client is not None


@pytest.mark.parametrize(
    "response",
    [
        _response([SimpleNamespace(type="text", text="no tool")], "end_turn"),
        _response([], "max_tokens"),
        _response([], "refusal"),
    ],
)
def test_anthropic_unusable_answers_raise_invalid_output(response):
    client, _ = _anthropic_client(response)
    with pytest.raises(InvalidOutput):
        client.summarize("context")


def test_sdk_client_is_built_with_retries_and_timeout():
    client = llm.AnthropicClient(Settings(llm_provider="anthropic", anthropic_api_key="test"))
    assert client._client.max_retries == llm.SDK_MAX_RETRIES
    assert client._client.timeout == llm.TIMEOUT_SECONDS


def test_get_client_by_provider():
    assert isinstance(llm.get_client(Settings(llm_provider="mock")), MockClient)
    anthropic_client = llm.get_client(Settings(llm_provider="anthropic", anthropic_api_key="test"))
    assert isinstance(anthropic_client, llm.AnthropicClient)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_second_run_makes_no_calls(db, tmp_path):
    first, second = CountingClient(), CountingClient()
    _run(db, tmp_path, first)
    result = _run(db, tmp_path, second)
    assert first.calls == 18 and second.calls == 0
    assert result.count("cache") == 18


def test_changed_record_misses_only_that_patient(db, tmp_path):
    _run(db, tmp_path, CountingClient())
    with sqlite3.connect(db) as conn:
        pid, bundle_json = conn.execute(
            "SELECT patient_id, bundle_json FROM bundles ORDER BY 1"
        ).fetchone()
        bundle = json.loads(bundle_json)
        doc = next(
            e["resource"]
            for e in bundle["entry"]
            if e["resource"]["resourceType"] == "DocumentReference"
        )
        doc["content"][0]["attachment"]["data"] = base64.b64encode(b"Amended note text.").decode()
        conn.execute(
            "UPDATE bundles SET bundle_json = ?, content_hash = ? WHERE patient_id = ?",
            (json.dumps(bundle), content_hash(bundle), pid),
        )
    client = CountingClient()
    result = _run(db, tmp_path, client)
    assert client.calls == 1
    assert [o.patient_id for o in result.outcomes if o.source == "llm"] == [pid]


def test_new_prompt_version_misses_everything(db, tmp_path, monkeypatch):
    _run(db, tmp_path, CountingClient())
    bumped = f"{PROMPT_VERSION}-next"
    monkeypatch.setattr(summarize_run, "PROMPT_VERSION", bumped)
    monkeypatch.setattr(checks, "PROMPT_VERSION", bumped)
    client = CountingClient()
    _run(db, tmp_path, client)
    assert client.calls == 18


def test_export_import_round_trip(db, tmp_path):
    _run(db, tmp_path, CountingClient())
    path = tmp_path / "export" / "summaries.jsonl"
    with sqlite3.connect(db) as conn:
        assert cache.export_cache(conn, path) == 18
        conn.execute("DELETE FROM summaries")
    before = path.read_text(encoding="utf-8")
    with sqlite3.connect(db) as conn:
        assert cache.import_cache(conn, path) == 18
        assert cache.import_cache(conn, path) == 0  # idempotent
        cache.export_cache(conn, path)
    assert path.read_text(encoding="utf-8") == before


def test_committed_cache_is_served_without_calls(db, tmp_path):
    _run(db, tmp_path, CountingClient())
    with sqlite3.connect(db) as conn:
        cache.export_cache(conn, tmp_path / "summaries.jsonl")
        conn.execute("DELETE FROM summaries")  # fresh clone: only the committed JSONL
    client = CountingClient()
    result = _run(db, tmp_path, client)
    assert client.calls == 0 and result.imported == 18 and result.count("cache") == 18


# ---------------------------------------------------------------------------
# Retry and fallback
# ---------------------------------------------------------------------------


def _one_patient(db: Path) -> tuple[str, dict]:
    pid, bundle = sorted(_bundles(db).items())[0]
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM bundles WHERE patient_id != ?", (pid,))
    return pid, bundle


def _too_long(context: str) -> dict:
    draft = MockClient().summarize(context)[0]
    draft["chief_concern"] = "word " * 210
    return draft


def test_retry_once_after_an_over_long_answer(db, tmp_path):
    _one_patient(db)
    client = StubClient(_too_long, lambda c: MockClient().summarize(c)[0])
    result = _run(db, tmp_path, client)
    assert client.calls == 2
    assert client.feedback[0] is None and "200" in client.feedback[1]
    assert result.outcomes[0].source == "llm" and _summaries(db)[0].model == "stub"


def test_retry_after_invalid_output_then_success(db, tmp_path):
    _one_patient(db)
    client = StubClient(
        InvalidOutput("no record_summary tool call"), lambda c: MockClient().summarize(c)[0]
    )
    result = _run(db, tmp_path, client)
    assert client.calls == 2 and result.outcomes[0].source == "llm"


@pytest.mark.parametrize(
    "answers",
    [
        (_too_long, _too_long),
        ({"chief_concern": ""}, {"not": "a summary"}),
        (LLMUnavailable("APIConnectionError"),),
    ],
)
def test_two_failures_fall_back_to_mock_with_low_confidence(db, tmp_path, answers):
    _one_patient(db)
    client = StubClient(*answers)
    result = _run(db, tmp_path, client)
    assert client.calls == len(answers)
    outcome = result.outcomes[0]
    assert outcome.source == "fallback" and outcome.confidence == "low"
    assert _summaries(db) == []  # fallbacks are not cached, so the next run retries


# ---------------------------------------------------------------------------
# Grounding checks
# ---------------------------------------------------------------------------


def _patient_with_flagged_labs(db: Path) -> dict:
    return next(b for b in _bundles(db).values() if flagged_labs(b))


def _check(bundle: dict, draft: dict):
    _, index = build_context(bundle)
    return checks.check(Draft.model_validate(draft), index, flagged_labs(bundle))


def test_mock_draft_passes_all_checks(db):
    for bundle in _bundles(db).values():
        result = _check(bundle, _mock_draft(bundle))
        assert result.issues == [] and result.confidence == "high"


def test_fabricated_citation_is_removed(db):
    bundle = _patient_with_flagged_labs(db)
    draft = _mock_draft(bundle)
    draft["key_diagnoses"].append(
        {"text": "Sarcoidosis", "cited_ids": ["DiagnosticReport/not-a-real-id"]}
    )
    draft["citations"].append("DiagnosticReport/not-a-real-id")
    result = _check(bundle, draft)
    assert "Sarcoidosis" not in result.fields["key_diagnoses"]
    assert "DiagnosticReport/not-a-real-id" not in result.fields["citations"]
    assert result.issues == ["unknown_citation"] and result.confidence == "low"


def test_uncited_claim_is_removed(db):
    bundle = _patient_with_flagged_labs(db)
    draft = _mock_draft(bundle)
    draft["key_diagnoses"].append({"text": "Gout", "cited_ids": []})
    result = _check(bundle, draft)
    assert "Gout" not in result.fields["key_diagnoses"] and "missing_citation" in result.issues


def test_unsupported_diagnosis_is_kept_and_flagged(db):
    bundle = _patient_with_flagged_labs(db)
    draft = _mock_draft(bundle)
    real_id = draft["citations"][0]
    draft["key_diagnoses"].append({"text": "Pancreatic adenocarcinoma", "cited_ids": [real_id]})
    result = _check(bundle, draft)
    assert "Pancreatic adenocarcinoma" in result.fields["key_diagnoses"]
    assert result.issues == ["unsupported_diagnosis"] and result.confidence == "medium"


def test_abbreviated_diagnosis_counts_as_supported():
    assert checks.is_supported("Hypertension", ["Pt is a 70 yo M with CHF and HTN."])
    assert checks.is_supported("Congestive heart failure", ["CHF exacerbation"])
    assert not checks.is_supported("Pneumonia", ["CHF exacerbation with HTN."])


def _record_texts(db: Path, patient_id: str | None = None) -> list[str]:
    """Title + text of every DocumentReference/DiagnosticReport (one patient, or all)."""
    bundles = _bundles(db)
    chosen = [bundles[patient_id]] if patient_id else list(bundles.values())
    return [f"{e['title']}. {e['text']}" for b in chosen for e in build_context(b)[1].values()]


# Diagnoses the v1 real-model run produced that the fuzzy checker wrongly flagged; each is
# stated in that patient's records, with a qualifier or a different inflection.
V1_FALSE_POSITIVES = [
    ("p-0002", "Hypertension (comorbidity)"),
    ("p-0004", "Former smoker, 45 pack-years"),
    ("p-0005", "Mechanical fall after tripping on bathroom floor"),
    ("p-0009", "Obesity (comorbidity)"),
    ("p-0010", "Acute on chronic systolic heart failure exacerbation (admission 2023-11-25)"),
]


@pytest.mark.parametrize(("patient_id", "diagnosis"), V1_FALSE_POSITIVES)
def test_v1_false_positives_are_now_supported(db, patient_id, diagnosis):
    assert checks.is_supported(diagnosis, _record_texts(db, patient_id))


def test_inferred_diagnosis_is_still_flagged(db):
    texts = _record_texts(db, "p-0013")
    assert any("postoperative hemoglobin" in t.lower() for t in texts)  # the value is there
    assert not checks.is_supported("Postoperative anemia (hemoglobin 9.4 g/dL)", texts)


def test_diagnosis_absent_from_every_record_is_flagged(db):
    assert not checks.is_supported("Pulmonary embolism", _record_texts(db))


def test_near_miss_type_1_vs_type_2_diabetes_is_flagged(db):
    texts = [t for t in _record_texts(db) if "type 2 diabetes" in t.lower()]
    assert texts and any(" 1" in t for t in texts)  # a stray "1" must not rescue it
    assert all(checks.is_supported("Type 2 diabetes mellitus", [t]) for t in texts)
    assert not checks.is_supported("Type 1 diabetes", texts)


@pytest.mark.parametrize(
    ("diagnosis", "record"),
    [
        ("Left hip fracture", "Displaced right intertrochanteric hip fracture."),
        ("Severe COPD", "Chronic obstructive pulmonary disease, FEV1 44% predicted."),
        ("Stage 4 CKD", "Chronic kidney disease stage 3b; recheck in 4 weeks."),
    ],
)
def test_qualifiers_outside_brackets_must_be_supported(diagnosis, record):
    assert not checks.is_supported(diagnosis, [record])


@pytest.mark.parametrize(
    ("word", "stem"),
    [("pack-years", "pack-year"), ("tripping", "tripped"), ("comorbidities", "comorbidity")],
)
def test_inflections_share_a_stem(word, stem):
    assert [checks.stem(w) for w in word.split("-")] == [checks.stem(w) for w in stem.split("-")]


@pytest.mark.parametrize("word", ["diagnosis", "mellitus", "loss", "ketoacidosis"])
def test_stemmer_keeps_is_us_ss_endings(word):
    assert checks.stem(word) == word


def test_missing_flagged_lab_is_added(db):
    bundle = _patient_with_flagged_labs(db)
    draft = _mock_draft(bundle)
    draft["anomalies"] = []
    result = _check(bundle, draft)
    flagged = flagged_labs(bundle)
    assert result.issues == ["anomaly_added"] * len(flagged)
    for lab in flagged:
        assert any(
            lab.test in a and lab.value in a and lab.date.isoformat() in a
            for a in result.fields["anomalies"]
        )
        assert lab.report_id in result.fields["citations"]


def test_media_date_is_corrected_from_the_bundle(db):
    bundle = next(b for b in _bundles(db).values() if _mock_draft(b)["recent_media"])
    draft = _mock_draft(bundle)
    true_date = draft["recent_media"][0]["date"]
    draft["recent_media"][0]["date"] = "1999-01-01"
    result = _check(bundle, draft)
    assert result.fields["recent_media"][0].date.isoformat() == true_date
    assert result.issues == ["date_corrected"] and result.confidence == "high"


def test_confidence_rules():
    assert checks.confidence_for([]) == "high"
    assert checks.confidence_for(["date_corrected", "date_corrected"]) == "high"
    assert checks.confidence_for(["anomaly_added"]) == "medium"
    assert checks.confidence_for(["unsupported_diagnosis", "date_corrected"]) == "medium"
    assert checks.confidence_for(["anomaly_added", "anomaly_added"]) == "low"
    assert checks.confidence_for(["unknown_citation"]) == "low"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_end_to_end_with_mock(db, tmp_path, pipeline, caplog):
    with caplog.at_level(logging.INFO, logger="summarize"):
        result = _run(db, tmp_path, MockClient())
    summaries = _summaries(db)
    assert len(summaries) == 18 and len(result.outcomes) == 18
    assert {s.confidence for s in summaries} <= {"high", "medium"}
    for summary in summaries:
        assert summary.disclaimer == DISCLAIMER
        assert 0 < summary.word_count <= 200
        assert summary.prompt_version == PROMPT_VERSION and summary.generated_at is not None
        index = build_context(_bundles(db)[summary.patient_id])[1]
        assert all(cid in index for cid in summary.citations)
    report = (tmp_path / "summary_checks.md").read_text(encoding="utf-8")
    assert report.count("| p-") == 18
    logs = caplog.text
    for patient in pipeline["truth"]["patients"]:
        assert patient["family_name"] not in logs and patient["family_name"] not in report
    assert "DISCHARGE" not in logs and "mg/dL" not in logs


def test_summary_eval_metrics(db, tmp_path, monkeypatch):
    from app.config import get_settings
    from eval.summary_eval import run_summary_eval

    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    get_settings.cache_clear()
    out = tmp_path / "summary_results.md"
    metrics = run_summary_eval(out)
    assert metrics.patients == 18 and metrics.second_run_calls == 0
    assert metrics.max_words <= 200 and metrics.disclaimer_pct == 100
    assert metrics.citations_resolving_pct == 100 and metrics.anomaly_recall == 100
    assert metrics.flagged_labs > 0
    assert "| LLM calls on a second run | 0 |" in out.read_text(encoding="utf-8")
