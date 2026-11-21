# Evaluation results

Generated 2026-11-21 13:37 UTC · embed model `sentence-transformers/all-MiniLM-L6-v2` · LLM provider `mock` · summary model `mock (cached real-model summaries)` · 66 indexed chunks, 18 patients · labels `queries.yaml`

## Search

| Metric | Value |
| --- | --- |
| Precision@5 | 0.70 (ceiling 0.78 given the labels per query) |
| Recall@5 | 0.80 |
| MRR | 0.93 |
| Latency p50 / p95 / max | 56 / 138 / 138 ms |
| All queries under 2 s | yes |

| Query | Relevant | Hits@5 | P@5 | R@5 | RR | ms |
| --- | --- | --- | --- | --- | --- | --- |
| abnormal chest x-ray | 6 | 2 | 0.40 | 0.33 | 0.25 | 138 |
| CXR showing consolidation | 4 | 3 | 0.60 | 0.75 | 1.00 | 92 |
| poorly controlled blood sugar | 10 | 5 | 1.00 | 0.50 | 1.00 | 47 |
| patients discharged after heart failure | 5 | 5 | 1.00 | 1.00 | 1.00 | 56 |
| elevated HbA1c | 8 | 5 | 1.00 | 0.62 | 1.00 | 45 |
| low potassium | 2 | 2 | 0.40 | 1.00 | 1.00 | 57 |
| hip fracture after a fall | 4 | 4 | 0.80 | 1.00 | 1.00 | 39 |
| meniscus tear on knee MRI | 2 | 2 | 0.40 | 1.00 | 1.00 | 73 |
| COPD exacerbation | 6 | 5 | 1.00 | 0.83 | 1.00 | 38 |
| new daily headaches | 2 | 2 | 0.40 | 1.00 | 1.00 | 64 |

## Summaries

Aggregate checks from `eval/summary_eval.py` (the summarize stage is run twice; the second run must make no LLM calls):

| Metric | Value |
| --- | --- |
| Patients summarized | 18 |
| Word count, max / mean (limit 200) | 103 / 58.5 |
| Disclaimer present | 100% |
| Citations resolving to a bundle resource | 100% |
| Diagnoses supported by cited records | 100% |
| Anomaly recall (flagged labs mentioned) | 100% |
| Confidence | high 18, medium 0, low 0 |
| LLM calls on a second run | 0 |

Per-patient grounding: 18 of 18 summaries pass every check (citations resolve, recent_media cited, ≤ 200 words, disclaimer unchanged, chief concern present).

| Patient | Words | Citations | Unresolved | Uncited media | Result |
| --- | --- | --- | --- | --- | --- |
| p-0001 | 103 | 3 | 0 | 0 | pass |
| p-0002 | 69 | 4 | 0 | 0 | pass |
| p-0003 | 65 | 2 | 0 | 0 | pass |
| p-0003-b | 37 | 2 | 0 | 0 | pass |
| p-0004 | 68 | 3 | 0 | 0 | pass |
| p-0005 | 56 | 3 | 0 | 0 | pass |
| p-0006 | 56 | 2 | 0 | 0 | pass |
| p-0007 | 47 | 2 | 0 | 0 | pass |
| p-0008 | 33 | 3 | 0 | 0 | pass |
| p-0009 | 70 | 3 | 0 | 0 | pass |
| p-0010 | 94 | 4 | 0 | 0 | pass |
| p-0011 | 54 | 3 | 0 | 0 | pass |
| p-0012 | 69 | 2 | 0 | 0 | pass |
| p-0013 | 51 | 3 | 0 | 0 | pass |
| p-0014 | 43 | 2 | 0 | 0 | pass |
| p-0015 | 74 | 2 | 0 | 0 | pass |
| p-0016 | 31 | 3 | 0 | 0 | pass |
| p-0017 | 33 | 2 | 0 | 0 | pass |

Automated checks cannot judge clinical accuracy. `docs/summary_review.md` is the manual review template a clinician fills in next to the source records.
