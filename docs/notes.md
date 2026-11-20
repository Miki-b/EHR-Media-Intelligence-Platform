# Working notes

Shared scratchpad. Add what you look up and what breaks, with the date. Most of the write-up comes from here.

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
