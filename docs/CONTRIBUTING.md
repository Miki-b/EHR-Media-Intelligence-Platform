# Contribution workflow

## Branches

| Branch | Merged as |
| --- | --- |
| `task-1-ingestion` | `feat(task-1): ingestion and cleaning pipeline with audit log` |
| `task-2-fhir` | `feat(task-2): FHIR R4 bundles with validation report` |
| `task-3-summaries` | `feat(task-3): cached, grounded AI clinical summaries` |
| `task-4-search` | `feat(task-4): semantic search API with filters` |
| `task-5-frontend` | `feat(task-5): clinician search and summary UI` |

## Rules

1. Never push directly to `main`. Open a PR and review the diff before merging.
2. Merge with **Squash and merge**, in task order 1 to 5, using the message above. `main` ends with one commit per task.
3. Changing `app/contracts.py`? Every stage reads it: update the affected stages in the same PR.
4. Before pushing: `make lint && make test`.
5. Never commit `.env`, API keys, or real patient data.
6. Use the `integration` branch on Saturday evening to test the full flow without touching `main`.
