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
