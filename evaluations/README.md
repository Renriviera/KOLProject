# Evaluation and release checks

## Published-table validation

Run from a fresh clone with the uv-managed environment:

```bash
uv run kolproject-evaluate
uv run pytest
```

`processed_validation.json` records counts, named checks, failures, UTC validation
time, and SHA-256 hashes for every published processed file. Any failed check causes
the command to exit with status 1. Use `--output /path/to/report.json` to save a
separate report. Processed tables are never changed by evaluation.

Checks cover the 500-person cohort, unique keys, published registry eligibility
fields, supporting publications, authorship identity/foreign keys, exact payment
totals, query coverage, edge keys, holdout separation, PageRank normalization,
utilization keys, service row counts, and selected missingness rules.
This validates internal table consistency; it does not reverify identities against
live NPPES or prove the completeness of excluded raw API responses.

The regression tests also inject a wrong authorship NPI, a missing utilization
feature row, and a duplicate service row to confirm these defects fail validation.

## Historical source validation

[`snapshots/`](snapshots/README.md) preserves the original collection-time reports.
They were produced using the complete local source caches and are not new source
audits of a GitHub clone. Restore the excluded inputs described in the root README
before running `uv run kolproject stage=validate` or
`uv run kolproject-utilization stage=validate`.

Hydra execution logs/configurations are written under ignored `runs/` and
`multirun/` directories. No clinical-effectiveness, prescribing, or causal performance
metric is implied by passing the consistency and provenance checks.
