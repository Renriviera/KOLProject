# KOLProject

CAR-T / lymphoma HCP research pipeline for Columbia's AI for Life Sciences project.
The published snapshot includes **500 HCPs**, their publication and coauthorship
evidence, 2024 CMS Open Payments, and separate 2024 Medicare utilization tables.

## Start with uv

Use **uv** for environment management, locked dependency installation, and commands.
The project requires Python 3.12; `.python-version`, `pyproject.toml`, and `uv.lock`
define the environment. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/Renriviera/KOLProject.git
cd KOLProject
uv sync --locked
uv run kolproject-evaluate
uv run pytest
```

The evaluation checks the published tables and writes
`evaluations/processed_validation.json`. It works offline from a fresh clone.

## Repository structure

| Folder/file | Contents |
|---|---|
| [`Data/processed/`](Data/README.md) | Dated HCP, publication, payment, network, screening, and utilization CSVs |
| `Data/manifests/` | Field dictionaries for the cohort and utilization extension |
| `src/kolproject/` | Collection, identity matching, deduplication, preprocessing, analysis, ranking, and evaluation code |
| `pre-processing/` | Payment prefetch and one-to-one modeling join helpers |
| `configs/` | Hydra settings, 2024 code definitions, and identity evidence templates |
| [`models/`](models/README.md) | Baseline ranking runs, input hashes, weights, and modeling-input instructions |
| [`evaluations/`](evaluations/README.md) | Reproducible processed-table checks and historical source-validation reports |
| [`eda/`](eda/README.md) | Cohort summaries, missingness, geographic counts, and network statistics |
| [`eda/visualizations/`](eda/visualizations/README.md) | Publication profile and seeded coauthorship network figures |
| `edav/` | Compatibility note for the original output location; the CLI stage remains `edav` |
| `tests/` | Identity, utilization interpretation, and published-table regression checks |
| `pyproject.toml`, `uv.lock`, `.python-version` | uv project and locked Python environment |

## Reproduce analysis from the published tables

These commands use the processed CSVs included in GitHub and do not fetch source data:

```bash
uv run kolproject stage=edav
uv run kolproject stage=rank
uv run python pre-processing/join_utilization.py
uv run kolproject-evaluate
```

EDA writes summaries to `eda/` and images to `eda/visualizations/`. Ranking outputs
go to `models/runs/<weight-hash>/`. The join creates an optional descriptive input
in `models/inputs/`, preserving all 500 HCPs and both original source tables.
Derived output timestamps reflect the rerun; source observation dates are preserved.

![Cohort publication profile](eda/visualizations/cohort_profile.png)

## Use the data

Read [`Data/README.md`](Data/README.md) for table relationships and
[`Data/UTILIZATION_README.md`](Data/UTILIZATION_README.md) for utilization definitions.
Keep identifiers as strings:

```python
import pandas as pd

hcps = pd.read_csv("Data/processed/2026-10-09/hcps.csv", dtype={"npi": str, "hcp_id": str})
features = pd.read_csv("Data/processed/2026-10-15/hcp_utilization_features.csv",
                       dtype={"npi": str, "hcp_id": str})
analysis = hcps.merge(features, on=["npi", "hcp_id"], how="left", validate="one_to_one")
```

Do not join detailed service rows directly into a one-row-per-HCP model: doing so
repeats publication and network features. Blank activity values mean unavailable
or unobserved; they must not be filled with zero by default.

## Collection and full source validation

Large raw responses, intermediate caches, legacy seeds, and populated identity
review evidence remain in the complete original local project/archive and are not
uploaded. Restore `Data/raw/`, `Data/interim/`, `Data/legacy/`,
`configs/affiliation_evidence.csv`, and `configs/identity_review.csv` before full
source validation, frozen rebuilds, or collection/resume:

```bash
uv run kolproject stage=validate
uv run kolproject-utilization stage=validate
uv run kolproject stage=rebuild
uv run kolproject-utilization stage=rebuild
# Resume collection only when needed; live upstream observations can change:
uv run kolproject stage=all
uv run kolproject-utilization stage=all
```

The default `kolproject` stage is full source validation; use `kolproject-evaluate`
for the checks available in a GitHub clone. Historical validation reports are kept
in `evaluations/snapshots/` with their original timestamps and scope. The original
local Git history and release tags remain local; processed tables are added to this
repository as a new commit.

## Interpretation

This is a publication-selected academic cohort, not a representative physician
census. Coauthorship and unfitted baseline rankings describe academic visibility.
CMS Open Payments represents industry transfers of value; Medicare utilization is
published aggregate service activity. Neither establishes prescribing influence,
clinical quality, patient copays, individual patient activity, or causal effects.
2026-observed features must not be used as prospective predictors of 2024 activity.
Third-party source content retains its original terms.
