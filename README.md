# KOLProject

CAR-T / lymphoma HCP research pipeline for Columbia's AI for Life Sciences project.
This repository contains the Python implementation, scraping and collection helpers,
preprocessing, identity matching and deduplication, CMS Open Payments and Medicare
utilization processing, exploratory analysis, ranking, validation, and tests.

## Install and test

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/Renriviera/KOLProject.git
cd KOLProject
uv sync --locked
uv run pytest
```

## Included functionality

- `src/kolproject/`: publication discovery and collection, NPI identity screening,
  deduplication, payment collection, table construction, coauthorship graphs,
  descriptive analysis, baseline ranking, manifests, and validation.
- `pre-processing/`: payment prefetch and one-to-one utilization join helpers.
- `configs/`: Hydra settings, 2024 utilization code definitions, and empty identity
  review/affiliation evidence templates with the original column headers.
- `tests/`: controlled identity matching and utilization interpretation fixtures.
- `uv.lock` and `pyproject.toml`: locked dependencies and installable CLI entry points.
- `Data/UTILIZATION_README.md`, `models/README.md`, `edav/README.md`, and
  `evaluations/README.md`: methodology and output documentation.

## Local data requirements

This is a code-only publication. HCP-level tables, source responses, intermediate
caches, manifests containing source requests, generated results/figures, and populated
identity review evidence are excluded. `.gitignore` keeps data and generated outputs
local. Dataset statistics in the methodology document describe the complete local
snapshot; the tables themselves are not included here.

To operate on the frozen release, restore `Data/` and the populated
`configs/affiliation_evidence.csv` and `configs/identity_review.csv` from the complete
local project/archive, preserving relative paths. The data are already present in
the original local `KOLProject` folder. Without these inputs, the tests run, but the
full validation, collection-resume and rebuild commands cannot reproduce the snapshot.
Live collection can change upstream observations.

After restoring the complete inputs, run from the project root:

```bash
uv run kolproject stage=validate
uv run kolproject-utilization stage=validate
uv run kolproject stage=rebuild
uv run kolproject-utilization stage=rebuild
```

For collection/resume, use `uv run kolproject stage=all` and
`uv run kolproject-utilization stage=all`. The default CLI stage is validation.
The project freezes cohort eligibility to 2017–2025 publications and a dated
500-HCP snapshot; new cohorts require a separately versioned configuration.

The original local Git history and release tags contain datasets and are retained
in the complete project. This code-only repository starts with a separate history.

## Interpretation

The cohort is selected research metadata, not a representative physician census.
Coauthorship edges and baseline rankings describe academic visibility. CMS Open
Payments represents industry transfers of value; Medicare utilization represents
published aggregate service activity. Neither establishes prescribing influence,
clinical quality, individual patient activity, or causal effects. Source content
retains its original terms; third-party data are not relicensed here.
