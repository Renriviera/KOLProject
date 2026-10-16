# Baseline models

`uv run kolproject stage=rank` produces an unfitted, interpretable ranking:
40% PageRank percentile + 30% mean citation-year-percentile percentile +
20% recent-publication-count percentile + 10% betweenness percentile.
Hydra weights are editable; they must sum to one. Each run records the input SHA-256.

These scores describe academic visibility in the collected network. No labeled
prescribing, referral or therapy-adoption outcome is available, so no supervised
model or causal effect has been fitted. Payments and social signals are excluded.
