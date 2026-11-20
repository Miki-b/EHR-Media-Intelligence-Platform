# Working notes

Shared scratchpad. Add what you look up and what breaks, with the date. Most of the write-up comes from here.

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

Decisions: see decisions.md rows 20-24 (row 19 is Dev B's fixtures entry on task-4-search).

Problems hit:
- The first lab reports repeated each value three times (conclusion, `result[]`,
  `presentedForm`) in the text search indexes. Dropped `presentedForm` for labs and kept
  only the abnormal values in the conclusion (decision 21).
- The `patients` table stores `full_name`, not given/family; Task 1 always makes the family
  name the last token, so `run.py` splits on the last space.
- Checked compatibility by running Dev B's real `chunk_bundle` (from task-4-search) over all
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
