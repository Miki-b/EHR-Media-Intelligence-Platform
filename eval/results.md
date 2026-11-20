# Evaluation results

Generated 2026-11-20 21:42 UTC · embed model `sentence-transformers/all-MiniLM-L6-v2` · LLM provider `mock` · 20 indexed chunks, 6 patients

## Search

| Metric | Value |
| --- | --- |
| Precision@5 | 0.48 (ceiling 0.48 given the labels per query) |
| Recall@5 | 1.00 |
| MRR | 1.00 |
| Latency p50 / p95 / max | 48 / 65 / 65 ms |
| All queries under 2 s | yes |

| Query | Relevant | Hits@5 | P@5 | R@5 | RR | ms |
| --- | --- | --- | --- | --- | --- | --- |
| abnormal chest x-ray | 3 | 3 | 0.60 | 1.00 | 1.00 | 65 |
| CXR showing consolidation | 2 | 2 | 0.40 | 1.00 | 1.00 | 55 |
| poorly controlled blood sugar | 3 | 3 | 0.60 | 1.00 | 1.00 | 43 |
| patients discharged after heart failure | 2 | 2 | 0.40 | 1.00 | 1.00 | 44 |
| elevated HbA1c | 2 | 2 | 0.40 | 1.00 | 1.00 | 48 |

## Summary grounding

6 of 6 summaries pass every check.

| Patient | Words | Citations | Unresolved | Uncited media | Result |
| --- | --- | --- | --- | --- | --- |
| p-0001 | 45 | 3 | 0 | 0 | pass |
| p-0002 | 40 | 2 | 0 | 0 | pass |
| p-0003 | 42 | 3 | 0 | 0 | pass |
| p-0004 | 24 | 2 | 0 | 0 | pass |
| p-0005 | 44 | 2 | 0 | 0 | pass |
| p-0006 | 45 | 2 | 0 | 0 | pass |

Checks: citations resolve to resources in the patient's bundle; every recent_media item is cited; at most 200 words; disclaimer unchanged; chief concern and diagnoses present. Clinical accuracy still needs a human read.
