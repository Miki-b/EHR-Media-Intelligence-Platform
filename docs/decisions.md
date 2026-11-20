# Design decisions

One entry per decision: what we chose, what we rejected, and why. These feed the README and the write-up.

| # | Decision | Rejected | Why |
| --- | --- | --- | --- |
| 1 | ChromaDB for vectors | FAISS | Built-in metadata filters cover resource type and date range |
| 2 | Local `all-MiniLM-L6-v2` embeddings | API embeddings | Free, offline, fast on CPU for 50 records |
| 3 | FHIR Bundle type `collection` with `urn:uuid` references | `transaction` | We store bundles, we don't post them to a FHIR server |
| 4 | Deterministic UUIDv5 resource ids | Random UUIDs | Re-runs produce identical bundles, so cache keys stay stable |
| 5 | Ambiguous slash dates read as US `MM/DD`, logged in the audit trail | Guessing silently | Epic is a US system; the audit entry keeps the assumption visible |
| 6 | LLM output through a forced tool schema, validated with Pydantic | Parsing free text | Structured output is checkable; malformed output is retried |
| 7 | Mock LLM provider as the default | Requiring a key | Tests, CI and graders run without an API key |
| 8 | Import FHIR models from `fhir.resources.R4B` (library 8.x) | Default `fhir.resources.*` imports | The default package is FHIR R5; R4B is the R4-compatible release the library ships (no plain R4 package exists in 8.x) |
| 9 | Expand common nicknames (Jon -> Jonathan) before name similarity | Lowering the cross-MRN threshold below 85 | "Jon Smith" vs "Jonathan Smith" scores ~80 raw; a nickname table fixes the real case without making unrelated names merge |
| 10 | Same MRN with only one of DOB/name disagreeing -> same patient, audited `demographics_mismatch` | A second patient, or silent merge | The rules only define the both-agree and both-disagree cases; MRN is strong evidence, and the audit puts the case in front of a reviewer |
| 11 | MRN-conflict patient gets a suffixed id (`p-0003-b`) | A fresh sequence number | The conflict stays visible in every downstream id, bundle and UI row |
| 12 | Slash date audited as ambiguous only when both parts are <= 12 and differ | Auditing `05/05/1985` too | Swapping equal parts gives the same date, so there is nothing to assume |
| 13 | Lab facts serialized into `CanonicalRecord.text` ("Test: value unit (ref lo-hi) flag F"), LOINC inferred into `code` | Changing the contract during Task 1 | The contract has no value/unit fields; Task 2 parses the fixed format. Candidate contract change, to agree with Dev B (done: decision 18) |
| 14 | Lab rows count as content even with no test name or value | Rejecting them in Task 1 | Missing code/value is a FHIR-level failure; letting it through keeps the seeded failure visible in Task 2's validation report |
| 15 | Each JSON patient object yields a demographics-only parse entry | Taking demographics from nested records only | A patient whose records are all in CSV/TXT would otherwise lose its JSON gender and DOB |
| 16 | Dropping a time part ("2024-04-26T11:05:00" -> date) is not audited | Auditing every timestamp | The contract stores dates; auditing every JSON record would bury the real changes |
| 17 | Whitespace-significant audit values stored JSON-quoted (`" 015592 "`) | Plain strings | `AuditEntry` strips whitespace, which would erase the evidence of the defect |
| 18 | Optional `value`, `unit`, `ref_range` on `CanonicalRecord`, filled for labs; the human-readable `text` is kept | Parsing lab facts back out of `text` in Task 2 (decision 13) | Task 2 maps them straight to `Observation.valueQuantity`/`referenceRange`, and strings keep the source precision ("1.20"); `text` stays because search indexes it. Supersedes the serialization-only part of decision 13 |
| 19 | Six hand-written fixture patients committed in `data/fixtures/`, validated with `fhir.resources` and loaded by `make fixtures` | Waiting for the Task 1-3 output, or mocking bundles inside tests | Search and UI are built against the real contract shapes from day one; fixtures double as eval ground truth and as a check that Dev A's bundles match what Dev B expects |
| 20 | A resource that fails validation is dropped from its bundle and logged in `validation_errors`; `is_valid = 0` only if the assembled bundle fails (agreed with Dev B) | Marking the whole bundle invalid | The indexer skips invalid bundles, so one bad lab row would hide a patient from search; the issue stays visible in the report |
| 21 | Labs grouped per patient per date into one LAB DiagnosticReport whose `result[]` lists the Observations; lab reports have no `presentedForm` | Standalone Observations, or repeating values in a text form | Search only indexes DocumentReference and DiagnosticReport and reads lab values through `result[]`; no presentedForm keeps each value once in the indexed text |
| 22 | No Encounter resources | Synthesizing encounters from record dates | The raw data has no encounter information; inventing visits would be fabricated clinical data, and nothing downstream needs them |
| 23 | LOINC codes only where verified; otherwise a text-only CodeableConcept. A lab whose source had no test name gets no `code` at all | Guessing codes, or a placeholder code | A wrong code is worse than none; leaving `code` out lets the FHIR validator catch the seeded failure instead of us hiding it |
| 24 | Date-only values written as instants at `T12:00:00Z` (`DocumentReference.date`) | `T00:00:00Z` | Midnight UTC shows as the previous day in timezones west of UTC; noon stays on the right day everywhere |
