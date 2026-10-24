# Models and descriptive baselines

`uv run kolproject stage=rank` regenerates a 500-HCP, unfitted ranking from the
published `hcps.csv`. No training labels or fitted model weights are available.

| Feature | Weight |
|---|---:|
| Weighted coauthorship PageRank percentile | 0.40 |
| Mean citation-year percentile, ranked within the cohort | 0.30 |
| Recent supported publication count percentile (2023–2025) | 0.20 |
| Weighted betweenness percentile | 0.10 |

Each feature uses the cohort midrank percentile `(rank - 0.5) / cohort_size`.
The score is 100 times the weighted sum. Higher scores rank first; ties use HCP ID.

## Outputs and experiments

- `runs/<weight-hash>/ranking.csv`: HCP ID, NPI, registered name, score, and rank.
- `runs/<weight-hash>/run_metadata.json`: exact weights, input table SHA-256, UTC
  run time, and interpretation. Rerunning identical weights replaces that run.
- [`kol_runs/`](kol_runs/README.md): Leiden communities, three explainable composite
  scores with per-axis contributions, and ten robustness and ablation experiments.
- [`inputs/`](inputs/README.md): instructions for generating a separate descriptive
  modeling table that joins HCP and utilization features one-to-one.

Weights are configured in `configs/config.yaml` and must be nonnegative and sum to one:

```bash
uv run kolproject stage=rank ranking.pagerank_weight=0.5 ranking.citation_percentile_weight=0.2
uv run python pre-processing/join_utilization.py
```

The ranking measures selected academic visibility. It has no supervised performance
metric, prescribing outcome, referral labels, calibration, or causal interpretation.
Payments and social signals are excluded from the score. Publication/network
features observed in 2026 are unsuitable as prospective predictors of 2024 utilization.
