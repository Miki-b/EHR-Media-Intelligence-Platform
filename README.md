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

| Path | Contents | Owner |
| --- | --- | --- |
| `app/contracts.py` | Shared Pydantic models between all stages | Both |
| `app/ingest/` | Task 1: parsers, normalizers, identity matching, dedupe, audit | A |
| `app/fhir/` | Task 2: FHIR mappers, bundle builder, validator, store | A |
| `app/summarize/` | Task 3: prompt, LLM clients, cache, grounding checks | A |
| `app/search/` | Task 4: chunking, embeddings, Chroma index, hybrid ranking | B |
| `app/api/` | FastAPI app | B |
| `frontend/` | Task 5: React + Tailwind UI | B |
| `scripts/` | Synthetic data generator, fixture loader | A / B |
| `eval/` | Search and summary evaluation harness | Both |
| `docs/` | Design decisions, working notes, write-up | Both |

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

## Design decisions

See [`docs/decisions.md`](docs/decisions.md).

## Evaluation results

_Filled in from `eval/results.md` before submission._

## Limitations and future work

_Filled in before submission._
