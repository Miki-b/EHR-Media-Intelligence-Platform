# Team workflow

## Branches

| Branch | Owner | Merged as |
| --- | --- | --- |
| `task-1-ingestion` | A | `feat(task-1): ingestion and cleaning pipeline with audit log` |
| `task-2-fhir` | A | `feat(task-2): FHIR R4 bundles with validation report` |
| `task-3-summaries` | A | `feat(task-3): cached, grounded AI clinical summaries` |
| `task-4-search` | B | `feat(task-4): semantic search API with filters` |
| `task-5-frontend` | B | `feat(task-5): clinician search and summary UI` |

## Rules

1. Never push directly to `main`. Open a PR; the other person reviews within 15 minutes.
2. Merge with **Squash and merge**, in task order 1 to 5, using the message above. `main` ends with one commit per task.
3. Changing `app/contracts.py`? Tell the other person first.
4. Before pushing: `make lint && make test`.
5. Never commit `.env`, API keys, or real patient data.
6. Use the `integration` branch on Saturday evening to test the full flow without touching `main`.
