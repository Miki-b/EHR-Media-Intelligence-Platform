# Manual summary review

The automated checks (`reports/summary_checks.md`, `eval/summary_results.md`) prove that
summaries cite real records and catch flagged labs. They cannot tell whether a summary is
clinically right. Each reviewer reads 5 patients' summaries next to their source records
(`GET /patients/{patient_id}` or the UI drawer) and fills in one row per patient.

- **Chief concern correct**: is it the most recent or most significant reason for care?
- **Diagnoses correct**: every listed diagnosis is stated in the records, and nothing
  important is missing.
- **Nothing invented**: no finding, value, date or diagnosis that the records do not contain.
- **Anomalies caught**: every abnormal lab and abnormal imaging finding is listed, with the
  right value and date.

Mark each column Y or N. Explain every N in Notes, citing the resource id.

Reviewer: ______  Date: ______  Model / prompt version: ______

| patient_id | Chief concern correct (Y/N) | Diagnoses correct (Y/N) | Nothing invented (Y/N) | Anomalies caught (Y/N) | Notes |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |
|  |  |  |  |  |  |
|  |  |  |  |  |  |
|  |  |  |  |  |  |
|  |  |  |  |  |  |
