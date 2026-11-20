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
  case; merging would mix two charts. We never merge when DOB and name both disagree.
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

-

## Problems hit and how we solved them

-

## Ideas for "with more time"

-
