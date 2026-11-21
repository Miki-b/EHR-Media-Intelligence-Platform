# EHR Media Intelligence Platform

An AI-powered pipeline that ingests messy, synthetic EHR records, normalizes them to HL7 FHIR R4, generates grounded clinical summaries, and lets clinicians find relevant records in seconds with semantic search.

> **All data in this repository is synthetic**, generated with Faker from a fixed seed. No real patient data is used.

## Architecture

```text
 raw JSON / CSV / TXT
        |
  [1] ingest + clean  --->  audit_log
        |
  [2] FHIR R4 bundles --->  validation report
        |                         \
  [3] AI summaries (cached)        SQLite (data/ehr.db)
        |                         /
  [4] embed + index   --->  Chroma (data/chroma)
        |
  FastAPI  POST /search · GET /patients/{id}
        |
  [5] React + Tailwind clinician UI
```

| Path | Contents |
| --- | --- |
| `app/contracts.py` | Shared Pydantic models between all stages |
| `app/ingest/` | Task 1: parsers, normalizers, identity matching, dedupe, audit |
| `app/fhir/` | Task 2: FHIR mappers, bundle builder, validator, store |
| `app/summarize/` | Task 3: prompt, LLM clients, cache, grounding checks |
| `app/search/` | Task 4: chunking, embeddings, Chroma index, hybrid ranking |
| `app/api/` | FastAPI app |
| `frontend/` | Task 5: React + Tailwind UI |
| `scripts/` | Synthetic data generator, fixture loader |
| `eval/` | Search and summary evaluation harness |
| `docs/` | Design decisions, working notes, write-up |

## Quick start

Requirements: Python 3.11+, Node 20+.

```bash
git clone <repo-url> && cd ehr-media-intel
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
cp .env.example .env                                # works as-is with the mock LLM
make install
make pipeline      # generate data, ingest, FHIR, summarize, index
make serve         # API on :8000 (docs at /docs), UI on :5173
```

Without `make` (plain Windows), the same steps are:

```bash
python -m pip install -e ".[dev]" && (cd frontend && npm install)
python scripts/generate_data.py && python -m app.pipeline all
python -m uvicorn app.api.main:app --reload --port 8000
cd frontend && npm run dev
```

The first `index` run downloads the 90 MB MiniLM model.

### Fixture mode

Six committed, validated FHIR bundles and summaries in `data/fixtures/` let search and the UI run
without the pipeline. They share patient ids with real output, so they live in their own database
and index (`data/fixtures.db`, `data/chroma-fixtures`), selected by `FIXTURE_MODE=1`:

```bash
make fixtures-serve                  # load, index and serve the fixture patients
# or, step by step / without make:
python scripts/load_fixtures.py      # turns fixture mode on by itself
FIXTURE_MODE=1 python -m app.pipeline index
FIXTURE_MODE=1 python -m uvicorn app.api.main:app --port 8000
```

`/health` reports `fixture_mode` so you always know which data you are looking at.

To use Claude for summaries, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in `.env`.

## Commands

Run `make help` for the full list.

| Command | What it does |
| --- | --- |
| `make test` | Unit tests, offline, no API key |
| `make lint` | ruff checks |
| `make eval` | Search precision@5, latency, summary grounding; writes `eval/results.md` |
| `make demo` | Full pipeline with the mock LLM, then serve |
| `make fixtures` | Load and index the committed fixture patients into their own DB |
| `make fixtures-serve` | Serve API and UI over the fixture data (`FIXTURE_MODE=1`) |

## Search API

| Endpoint | Purpose |
| --- | --- |
| `POST /search?resource_type=&date_from=&date_to=` | Body `{"query": "...", "top_k": 5}`. Returns up to `top_k` records ranked by a hybrid score (0.7 cosine similarity from MiniLM + 0.3 BM25), one hit per resource, with a snippet and the patient's summary snippet. Filters are query parameters, as the brief asks. |
| `GET /patients/{patient_id}` | Demographics, the cached AI summary, and every linked FHIR resource, flagged when the summary cites it. |
| `POST /reload` | Drop the in-memory index after re-running `make index`. |
| `GET /health` | Provider, embed model, indexed chunk and patient counts. |

What gets indexed: one chunk per `DocumentReference` (decoded attachment text) and per
`DiagnosticReport` (conclusion, folded `Observation` values with flags and ranges, presented form),
plus one chunk per AI summary (`Summary/<patient_id>`). Long texts are split into overlapping
180-word windows and collapsed back to one result per resource.

## Clinician UI

`make ui` serves the React + Tailwind interface on <http://localhost:5173>; in dev, Vite forwards
`/api/*` to FastAPI so no host is hard-coded. The whole search lives in the URL
(`?q=elevated+HbA1c&resource_type=DiagnosticReport&date_from=2025-01-01&patient=p-0002`), so a
search or an open patient can be bookmarked and shared.

- Results update as you type (250 ms debounce, stale responses discarded). Each row shows the
  relevance score as a meter, patient name and MRN, resource type, record date, the matching
  snippet, and the patient's AI summary snippet.
- Filters: record type, from and to dates. Invalid ranges are flagged before any request is sent.
- Opening a row slides in the patient drawer: demographics, the full AI summary (chief concern,
  diagnoses, recent records, flagged anomalies, confidence, disclaimer) and every linked FHIR
  resource, with the ones the summary cites marked and the clicked record highlighted.
- Keyboard: Tab to the search box, arrow down into the list, arrow keys between rows, Enter opens
  the patient, Esc closes the drawer and returns focus. Focus is trapped inside the drawer; status
  text is announced through live regions; reduced-motion preferences are respected.
- Empty, loading, no-match and API-down states each say what to do next.

## Deploying the demo

`Dockerfile` builds one image that compiles the UI, runs the whole pipeline on the synthetic
dataset at build time (mock LLM plus the committed summary cache, so no API key), pre-downloads the
embedding model, and serves UI and API together on port 7860. It needs about 2 GB of RAM at
runtime for torch and the model.

```bash
docker build -t ehr-demo .
docker run -p 7860:7860 ehr-demo        # http://localhost:7860
```

`Dockerfile.render` is the same app for small hosts: embeddings run through onnxruntime
(`EMBED_BACKEND=onnx`, `pip install -e ".[onnx]"`) instead of torch, so the API fits in about 350 MB
and runs on Render's free tier. `render.yaml` is the matching Blueprint; the service listens on
`$PORT`. Any container host works for the full image (Fly.io, Cloud Run, Hugging Face Docker Spaces
on a PRO plan; `scripts/deploy_space.py` uploads to the latter). For a quick look without a host, run
`make api` after building the frontend with `VITE_API_BASE=""` and expose port 8000 with a tunnel
such as `cloudflared tunnel --url http://127.0.0.1:8000`.

## Design decisions

See [`docs/decisions.md`](docs/decisions.md).

## Evaluation results

`make eval` writes [`eval/results.md`](eval/results.md) (search, plus the summary metrics from
[`eval/summary_eval.py`](eval/summary_eval.py)). Numbers below are from the committed synthetic
dataset: 18 patients, 66 indexed chunks, summaries by `claude-haiku-5-5` (prompt v2) served from the
committed cache, MiniLM embeddings on CPU.

**Search**, 10 labelled queries in [`eval/queries.yaml`](eval/queries.yaml), top 5 returned:

| Metric | Value |
| --- | --- |
| MRR (first relevant hit) | 0.93 |
| Recall@5 | 0.80 |
| Precision@5 | 0.70, against a ceiling of 0.78 set by the number of labels per query |
| Latency | tens of milliseconds per query, p95 under 150 ms across runs on a laptop CPU (budget: 2 s for 50 records) |

Nine of ten queries rank a relevant record first. The one that does not is a negation case:
for "abnormal chest x-ray" the two *normal* pre-employment films rank above the abnormal ones,
because "no acute cardiopulmonary process" embeds close to the query. See limitations.

**AI summaries**, all 18 patients:

| Metric | Value |
| --- | --- |
| Citations resolving to a resource in the patient's bundle | 100% |
| Key diagnoses supported by the cited records | 100% |
| Flagged (H/L) labs mentioned in anomalies | 100% (20 of 20) |
| Word count max / mean (limit 200) | 103 / 58.5 |
| Disclaimer present | 100% |
| LLM calls on a second run (cache) | 0 |

How we validated summary quality: structured output through a tool schema validated by Pydantic;
generation-time grounding checks that drop claims citing unknown ids, flag unsupported diagnoses
and add missed abnormal labs (confidence is derived from those checks, never self-reported);
the automated eval above; and a manual review template,
[`docs/summary_review.md`](docs/summary_review.md), because no automated check can judge
clinical correctness. Prompt v1 to v2 numbers are in `eval/summary_results_v1.md` versus
`eval/summary_results.md`.

## Limitations and future work

- **Negation.** Dense embeddings do not distinguish "consolidation" from "no consolidation", and
  BM25 makes it worse by rewarding the shared term. A cross-encoder reranker over the top 20
  candidates is the standard fix and the first thing we would add.
- **Small, synthetic corpus.** 18 patients and 10 labelled queries by one annotator. Real
  evaluation needs more queries, a second annotator and agreement statistics, and nDCG.
- **One result per record, not per patient.** Clinicians often want "which patients", with the
  matching records nested. The API has what is needed; the grouping is a UI change.
- **Lab values are text, not facets.** "HbA1c over 9" is a text match today. Indexing
  Observations with numeric value and flag metadata would make it a structured filter.
- **Validation is structural, not terminological.** `fhir.resources` checks required fields and
  types; a wrong LOINC code still passes. Terminology validation needs a terminology server.
- **Identity matching is rule-based** (normalised MRN, name similarity, DOB). It handles the
  defects in the synthetic data; real duplicate detection would need probabilistic matching and
  a human review queue for borderline merges.
- **Summaries are checked for grounding, not correctness.** The eval proves every claim points
  at a real record and every abnormal lab is mentioned; whether the chief concern is the *right*
  one still needs a clinician, hence the review template.
- **No auth, audit of access, or PHI controls.** Fine for synthetic data; mandatory before
  anything real. Searches and summary views would need to be logged per user.
- **Startup cost.** Importing torch and sentence-transformers takes tens of seconds on a laptop;
  queries themselves take tens of milliseconds. A long-running API process hides this; a
  serverless deployment would not.
