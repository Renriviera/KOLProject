# Exploratory data analysis

Regenerate all summaries and images from the published tables:

```bash
uv run kolproject stage=edav
```

The implementation is `src/kolproject/edav.py`. The existing `edav` stage name is
preserved; its output directory is now `eda/`.

| Output | Contents |
|---|---|
| `profile.json` | Cohort size, publication evidence distribution, graph size, geographic coverage, and communities |
| `missingness.csv` | Missing values by HCP field; blanks remain missing |
| `hcp_by_registry_state.csv` | Counts by observed registry practice state |
| `publications_by_year.csv` | Distinct eligible publications linked to selected HCPs |
| `top_pagerank.csv` | Twelve HCPs with the highest descriptive PageRank |
| [`visualizations/`](visualizations/README.md) | Publication profile and seeded observed-coauthorship network |

The selected cohort is not a workforce census. Registry addresses may be stale.
Publication counts are linked evidence, not manually adjudicated treatment activity.
