# Working notes

Shared scratchpad. Add what you look up and what breaks, with the date. Most of the write-up comes from here.

## Task 3 notes (2026-11-21)

`app/summarize/`: `context.py` (bundle -> model context + resource index), `prompts.py`,
`tool_schema.py`, `llm.py` (Anthropic / OpenAI / Mock clients), `checks.py` (grounding),
`cache.py` (SQLite + committed JSONL), `run.py`. Evaluation in `eval/summary_eval.py`,
manual review template in `docs/summary_review.md`.

Concepts used:
- **Structured output via tool use**: the model returns one `record_summary` tool call whose
  arguments follow a JSON schema; `strict: true` makes the API enforce that schema, and
  Pydantic validates it again. Current models reject a *forced* tool choice and non-default
  temperature, so the prompt names the tool and the cache provides repeatability
  (decisions 25-26).
- **Grounding**: every diagnosis, media item and anomaly carries the ids it came from;
  `checks.py` drops claims citing unknown ids, flags diagnoses the cited text does not
  support, overwrites media dates from the bundle, and adds any H/L lab the model missed.
  Confidence comes from what the checks found, never from the model.
- **Prompt-injection defence**: records are wrapped in `<records>` tags and the system
  prompt says text inside them is data, not instructions; the model has no tools that act,
  only the one that records the summary, and every claim is re-checked against the source.
- **Cache keying**: (patient_id, bundle content_hash, prompt_version). Task 2 bundles are
  byte-identical across runs, so only a changed record or a new prompt version calls the
  model; the second run makes zero calls. `data/cache/summaries.jsonl` carries real-model
  summaries to fresh clones.
- **Why not temperature 0**: the intent (same input, same output) is met by the cache; the
  current models refuse a temperature setting, and even at 0 outputs are not guaranteed
  bit-identical across API versions, which the cache does guarantee.
- **Minimal context**: the model gets age and gender only (no name, MRN or DOB), newest
  records first, capped at ~6,000 tokens by dropping the oldest.

Problems hit:
- The mock's correct diagnoses were flagged as unsupported when a note used abbreviations
  ("HTN" for hypertension). A real model would hit the same false positive, so the checker,
  not the mock, now expands common abbreviations (decision 29).
- `app.pipeline` calls `main()` with no arguments, so argparse in `main` would have read the
  pipeline's own `sys.argv`; the CLI flags live in a separate `cli()`.
- First real-model run (Haiku 5.5, prompt v1): 12 high / 6 medium. Reviewing the six
  `unsupported_diagnosis` flags by hand: five were checker false positives (a bracketed
  qualifier, or an inflection such as "pack-years" vs "pack-year") and one was a real
  inference ("postoperative anemia" from a hemoglobin of 9.4). Measuring rapidfuzz showed
  it also accepted near-misses ("type 1" vs "type 2 diabetes" at 93), so the fuzzy match
  was replaced by stem matching (decision 32). Several flagged items were not diagnoses
  at all (a fall, smoking history), which is a prompt problem: prompt v2 (decision 33).
  Before/after numbers: `eval/summary_results_v1.md` vs `eval/summary_results.md`.
- The eval re-runs the summarize stage, and that run overwrote `reports/summary_checks.md`
  with cache-hit rows, losing the generation-time issues. The eval now reports its own runs
  to a scratch file.

## Task 2 notes (2026-11-21)

`app/fhir/`: `mappers.py` (canonical model -> resource dict), `bundle.py` (validate each
resource, assemble, validate the bundle), `run.py` (reads `patients`/`records`, writes
`bundles`, `validation_errors` and `reports/validation.md`).

Concepts used:
- **R4 resource choice**: narrative documents -> `DocumentReference` (LOINC 18842-5
  discharge summary, 11506-3 progress note); imaging -> `DiagnosticReport` (v2-0074 `RAD`)
  with `conclusion` + `presentedForm`; labs -> `Observation` grouped under a `LAB`
  DiagnosticReport via `result[]`.
- **Observation details**: `valueQuantity` with UCUM codes (`10^3/uL` is `10*3/uL` in UCUM),
  `referenceRange.low/high` as Quantities, `interpretation` from v3-ObservationInterpretation
  (H/L/N).
- **`DocumentReference.date` is an `instant`**, so it needs a time and zone; other dates
  (`effectiveDateTime`, `birthDate`) accept date-only values.
- **Validation granularity**: fhir.resources validates structure (required fields,
  cardinality, types), not terminology; a bogus LOINC code would still pass.

Decisions: see decisions.md rows 20-24 (row 19 is the Task 4 fixtures entry).

Problems hit:
- The first lab reports repeated each value three times (conclusion, `result[]`,
  `presentedForm`) in the text search indexes. Dropped `presentedForm` for labs and kept
  only the abnormal values in the conclusion (decision 21).
- The `patients` table stores `full_name`, not given/family; Task 1 always makes the family
  name the last token, so `run.py` splits on the last space.
- Checked compatibility by running the search stage's real `chunk_bundle` over all
  18 bundles: 48 chunks, one per DocumentReference/DiagnosticReport, no dangling references.

## Task 1 notes (2026-11-21)

Pipeline: `app/ingest/` runs five pure stages (parse -> normalize -> identity -> dedupe ->
validate), each returning `(data, audits)`; only `run.py` touches SQLite.

Concepts used:
- **Record linkage**: deterministic rules (same MRN plus compatible demographics; same DOB
  plus strong name match across MRNs) rather than probabilistic Fellegi-Sunter scoring,
  which needs far more data than 18 patients to estimate weights. Name similarity is
  rapidfuzz `token_sort_ratio`, which ignores token order ("SMITH, JOHN" vs "John Smith").
- **Overlay vs. duplicate MRNs**: one MRN on two people (an "overlay") is the dangerous
  case; merging would mix two charts. Records are never merged when DOB and name both disagree.
- **ISO/IEC 5218** sex codes: 1 = male, 2 = female (0 = not known, 9 = not applicable).
- **LOINC** codes are inferred from lab test names so Task 2 can code Observations.
- Content hashing for dedupe: case-folded, whitespace-collapsed text, so re-exports with a
  new id or different case collapse.

Decisions: see decisions.md rows 9-17.

Problems hit:
- A patient whose JSON object has empty record arrays (all their records are in CSV/TXT)
  lost their JSON gender: demographics were only read from nested records. Fixed by
  emitting a demographics-only parse entry per JSON patient (decision 15).
- `AuditEntry` strips whitespace, so `" 015592 "` was recorded as `015592`. Values with
  significant whitespace are stored JSON-quoted (decision 17).
- `audit_log.id` is AUTOINCREMENT: deleting rows does not reset it, so reruns produced
  different ids. `run.py` also clears `sqlite_sequence` for that table.

## FHIR and clinical concepts researched

- 2026-11-21 **Bundle `collection` vs `transaction`.** A collection is just a set of resources; a transaction is a batch of requests for a FHIR server. Nothing is POSTed to a server, so collection is right. Inside a bundle, `urn:uuid:` references resolve against each entry's `fullUrl`, so the bundle is self-contained.
- 2026-11-21 **Where document text lives.** `DocumentReference.content[].attachment.data` is base64 (`base64Binary`), with `contentType`. Scanned PDFs would be binary here; the synthetic notes are `text/plain`. The chunker decodes text types only and falls back to `description` for anything else.
- 2026-11-21 **DiagnosticReport anatomy.** `code` (LOINC panel, e.g. 36643-5 "Chest X-ray 2 views", 4548-4 HbA1c), `category` (v2-0074: LAB, RAD), `conclusion` (free-text impression), `result[]` references to `Observation`s, `presentedForm[]` for the full report. Imaging reports have no Observations; labs do.
- 2026-11-21 **Observation interpretation flags** use v3-ObservationInterpretation codes H / L / N, and `referenceRange.low/high` carry the normal range. These are folded into the parent report's chunk text ("HbA1c: 9.8 % (High; reference 4 %-5.6 %)") so lab values are searchable.
- 2026-11-21 **Partial dates are legal FHIR.** `date`/`dateTime` can be "2025" or "2025-03". They resolve to the first day so date filters still work; `DocumentReference.date` is an `instant` and must carry a time.
- 2026-11-21 **fhir.resources 8.x** exposes R4 as `fhir.resources.R4B`; the top-level package is R5. Validation catches missing required fields (e.g. `Encounter.class`, `DocumentReference.status`) but not clinical sense.

## Problems hit and how I solved them

- 2026-11-21 **Importing torch + sentence-transformers takes ~30 s on the Windows dev laptop**, while loading MiniLM takes 1.4 s and embedding 20 chunks 0.1 s. It is a per-process startup cost, not per-query: the API pays it once at boot, queries take 40-190 ms. Tests use `EMBED_MODEL=hash`, a deterministic bag-of-words embedder, so they never import torch.
- 2026-11-21 **Hugging Face Hub round-trips on every model load.** Fixed by trying `local_files_only=True` first and only going to the network when the cache is empty.
- 2026-11-21 **Embeddings do not understand negation.** For "abnormal chest x-ray" the *normal* CXR ("no acute cardiopulmonary abnormality") ranks third, above the consolidation CXR. All three abnormal films are still in the top 5 (recall 1.0), but it is the clearest limitation of pure dense retrieval on clinical text. Hybrid BM25 helps exact terms (CXR, HbA1c) but not negation.
- 2026-11-21 **Chroma metadata must be scalar.** Dates go in twice: ISO string for display, `YYYYMMDD` int for `$gte`/`$lte` range filters. Chroma also needs `$and` for more than one clause.
- 2026-11-21 **Precision@5 has a ceiling.** With 2-3 labelled records per query, P@5 cannot exceed 0.4-0.6. results.md prints the ceiling next to the number; MRR and recall are the better signals at this corpus size.
- 2026-11-21 **Headless Chromium on Windows will not render narrower than ~500 px** with `--window-size`, so phone screenshots looked clipped. Driving Edge over the DevTools protocol with `Emulation.setDeviceMetricsOverride` gives a true 390 px viewport; `document.documentElement.scrollWidth === innerWidth` confirmed no horizontal overflow.
- 2026-11-21 **Summary-chunk snippets read like data** ("document 2025-02-09: Discharged…") because the embedded summary text is a flattened record list. The UI shows them in the summary voice; a friendlier summary_text format is a small follow-up on the search side.

- 2026-11-21 **Eval on real data.** Relabelled `eval/queries.yaml` against the 18 pipeline patients (10 queries, every record read by hand); fixture labels moved to `eval/queries.fixtures.yaml` and `eval.run` picks the file by `FIXTURE_MODE`. `eval.run` now calls `run_summary_eval()` so `make eval` produces one report. First pass showed two "failures" that were healthy patients with no diagnoses (check removed) and two misses that were AI summaries genuinely discussing HbA1c (labels added). The remaining misses are negation: normal pre-employment CXRs outrank abnormal ones for "abnormal chest x-ray".
- 2026-11-21 **Two prompt versions in the summaries table.** The search service picked v2 only because it was created later; it now orders by `prompt_version = PROMPT_VERSION` first, so a re-run of an old prompt can never shadow the current summaries.

- 2026-11-21 **FIXTURE_MODE was ignored for anyone who copied `.env.example`.** The
  example set `DB_PATH=data/ehr.db` and `CHROMA_PATH=data/chroma`, which pydantic-settings
  treats as explicitly set, so fixture mode kept the pipeline database and the demo patients
  would overwrite real rows (ids overlap). CI never saw it because CI has no `.env`. Fixed in
  `app/config.py`: a path that just restates the pipeline default no longer counts as an
  override; the two lines in `.env.example` are now commented out.

## Ideas for "with more time"

- Cross-encoder reranker (e.g. `ms-marco-MiniLM`) over the top 20 hybrid candidates; handles negation and phrasing far better than bi-encoder cosine alone.
- Index Observations as first-class hits with a `value` + `flag` facet, so "all patients with HbA1c over 9" becomes a structured filter, not a text match.
- Query-time patient grouping: one card per patient with the best matching records nested, instead of one card per record.
- Larger labelled query set (30+) with a second annotator and inter-annotator agreement, plus nDCG.
- Clinician review of summaries: a small rubric (accuracy, omissions, hallucinations) scored on a sample, which the automated grounding checks cannot replace.
- Auth, audit logging of searches and PHI access controls before anything near real data.
