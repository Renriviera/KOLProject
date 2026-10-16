# Modeling inputs

From the repository root, run:

```bash
uv run python pre-processing/join_utilization.py
```

This generates `hcp_utilization_2024.csv` and `hcp_utilization_2024.metadata.json`
here. The metadata records source hashes, row count, observation time, and purpose.
The script enforces a one-to-one NPI/HCP-ID join and retains all 500 cohort rows.
Generated wide inputs are ignored by Git because they duplicate the published tables.
Both input tables remain unchanged. This join supports descriptive analysis; its
2026-observed predictors do not support a prospective 2024 prediction claim.
