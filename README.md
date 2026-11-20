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

To use Claude for summaries, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in `.env`.

## Commands

Run `make help` for the full list.

| Command | What it does |
| --- | --- |
| `make test` | Unit tests, offline, no API key |
| `make lint` | ruff checks |
| `make eval` | Search precision@5, latency, summary grounding; writes `eval/results.md` |
| `make demo` | Full pipeline with the mock LLM, then serve |

## Design decisions

See [`docs/decisions.md`](docs/decisions.md).

## Evaluation results

_Filled in from `eval/results.md` before submission._

## Limitations and future work

_Filled in before submission._
