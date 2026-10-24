# KOL communities, composite scores, and robustness experiments

`uv run kolproject-kol` detects coauthorship communities with Leiden, scores every
HCP on three explainable composites, and runs ten experiments that test whether
the communities, the leaders, and the feature-importance claims survive
perturbation. It reads only the published tables in `Data/processed/`.

```bash
uv run kolproject-kol                                   # full run, a few minutes
uv run kolproject-kol quick=true                        # smoke test, written to evaluations/runs/kol_quick/
uv run kolproject-kol community.resolution=1.5          # finer communities
uv run kolproject-kol experiments=[leave_one_axis_out,weight_perturbation]
uv run kolproject-kol composites.rising_voice.momentum=0.3 composites.rising_voice.impact=0.25
```

Settings live in `configs/kol.yaml`. Each run is written to `kol_runs/<run-id>/`,
where the ID hashes the resolved configuration and the input-table SHA-256s, so
identical settings on identical data overwrite the same folder.

## What a run contains

| File | Contents |
|---|---|
| `report.md` | Readable summary of every experiment, the composite definitions, and the top 15 per composite |
| `kol_scores.csv` | One row per HCP: institution, community and its label, hub role, six axis percentiles, each composite's score, rank, per-axis contributions, bootstrap rank interval, and weight-robustness class |
| `features.csv` | The raw per-HCP features behind the axes |
| `community_profiles.csv` | One row per community: label, whether it is safe to name, stability, institution and state purity, leaders, connectors, a one-paragraph description |
| `run_metadata.json` | Resolved config, input hashes, library versions, and every experiment summary |
| Other CSVs | Full tables for each experiment, named after it |

## Communities

The primary partition is Leiden with the RB-configuration objective on the
fractional-weight coauthorship graph, the same weighting as the published
`coauthor_edges.csv`. Leiden is run with 50 seeds, and the run whose partition
agrees most with the others is used. Each HCP gets a consensus score: the share
of runs in which it sits with the rest of its community.

Every community gets a label, including the unstable ones. The label is worded
by **archetype**, which depends on the share of members at the largest institution:

| Archetype | Rule | Example label |
|---|---|---|
| single-institution orbit | at least 60% at one institution | Memorial Sloan Kettering orbit |
| anchored network | 40% to 60% at one institution | Mayo Clinic orbit |
| multi-institution network | under 40% | Mass General Brigham – Dana-Farber network (Northeast) |
| outside main network | fewer than 5 members | Isolated: name (institution), no coauthorship with the cohort |

A **tier** says how much weight the label can bear, and `tier_reason` explains it:

| Tier | Rule | Use |
|---|---|---|
| firm | seed consensus at least 0.8 and Jaccard at least 0.75 when 10% of papers are dropped | Safe to name in a deck |
| provisional | consensus at least 0.8, Jaccard 0.6 to 0.75 | Name it, with a caveat |
| unstable | lower consensus or Jaccard; the label starts with "Loose" | A loose grouping. `drifts_to` lists where its members also land |
| outside main network | fewer than 5 members | Describe individuals, not a group |

Each community also gets a **theme** from its members' paper titles. The product
lean shows how much more often the community's titles name a CAR-T product or its
trials than the other communities' titles do, for example "tisa-cel (Kymriah) 3.8x".
The topic terms are the title words and phrases most over-represented relative to
the other communities, using log-odds with an informative Dirichlet prior (Monroe,
Colaresi & Quinn 2008). Comparing against the other communities, rather than the
whole corpus, keeps the large multi-center papers that span many groups from
inflating every theme. A `leader-dependent` flag marks communities that break up
when their top PageRank member is removed.

Each HCP also gets a Guimerà–Amaral hub role. The role comes from within-community
strength and the participation coefficient, the share of their ties that leave
their own community. Connector hubs and connectors are the people who link
institutions.

## Composite scores

Each **axis** is the mean cohort percentile of its features. Each **composite** is
100 times a weighted sum of axes. The `contrib_*` columns in `kol_scores.csv` add
up to the score, so every rank can be explained as "where the points came from".

| Axis | Features | Plain-language meaning |
|---|---|---|
| prestige | PageRank | Connected to people who are themselves well connected |
| bridging | betweenness, participation coefficient | Links groups that would otherwise be separate |
| impact | mean citation-year percentile of papers | Attention per paper, relative to papers from the same year |
| leadership | senior-author rate, shrunk toward the cohort mean | Drives papers rather than appears on them |
| momentum | 2023–2025 share of papers, shrunk toward the cohort mean | Rising rather than established |
| industry | log consulting dollars, log CAR-T-product-tagged dollars (2024) | Already advises manufacturers |

| Composite | prestige | bridging | impact | leadership | momentum | industry | Question it answers |
|---|---:|---:|---:|---:|---:|---:|---|
| academic_authority | 0.35 | 0.05 | 0.25 | 0.20 | 0.05 | 0.10 | Who is the recognized scientific voice? |
| network_connector | 0.20 | 0.40 | 0.10 | 0.10 | 0.10 | 0.10 | Who carries messages between institutions? |
| rising_voice | 0.15 | 0.10 | 0.15 | 0.10 | 0.40 | 0.10 | Who is gaining visibility now? |

PageRank, coauthor strength, degree, and publication count are close to a single
signal, with Spearman correlations of 0.90 to 0.97. Each composite therefore uses
only one of them. Experiment 7 measures what would change if all four were used.

Rates are shrunk with a beta-binomial prior worth 4 papers at the cohort-wide rate.
Without shrinkage, an HCP with two papers, both senior-authored, would outrank
one with forty papers and a 60% senior rate.

## Experiments

| # | Name | Question | Method |
|---|---|---|---|
| 1 | stability | Are the communities an artifact of the random seed or the algorithm? | 50-seed Leiden ensembles across six resolutions. The run is compared with Louvain and with the published greedy-modularity partition using adjusted mutual information. |
| 2 | weighting | Do the conclusions depend on how edges are weighted? | Everything is recomputed under binary, shared-count, fractional, and fractional with at least two shared papers. Rank correlations, top-50 overlap, and leaders who are top-50 under every weighting are reported. |
| 3 | bootstrap | Would the answer change with a different sample of the literature? | Each replicate either resamples papers with replacement or drops 10% of them, then rebuilds everything. The output is rank intervals and Hennig clusterwise Jaccard stability per community. |
| 4 | node_removal | Is a community a stable network or a single person's orbit? | Remove the top-k PageRank HCPs, or each community's own leader, and rerun Leiden. The effect is the drop beyond a fresh-seed noise floor. |
| 4b | movers | Which HCPs belong to several communities? | Membership shares across all seed runs and literature-drop replicates, aligned to the primary communities. HCPs are classed as anchored, multi-community mover, diffuse mover, or data-thin mover. A partial Spearman test, controlling for volume, checks whether movers have broader 2024 manufacturer relationships. |
| 5 | leave_one_axis_out | Which axis does each composite actually depend on? | Drop one axis, renormalize the weights, and measure rank displacement of the top 50. |
| 6 | weight_perturbation | Do the leaders depend on our exact weights? | 2,000 Dirichlet draws near the stated weights, and 2,000 drawn uniformly. The output is each HCP's probability of staying top 25 and a robustness class. |
| 7 | collinearity | Is a composite just publication volume in disguise? | Spearman matrix and variance inflation factors. The prestige axis is swapped for four volume features and the top-50 change is measured. |
| 8 | face_validity | Do scores agree with independent signs of standing? | AUC, average precision, and precision at 25 against three weak labels, with bootstrap CIs. |
| 9 | temporal | Do scores computed from the past anticipate what happened next? | Score from papers through 2022 only. Test against 2023–2025 publications, new coauthors, 2024 consulting, and the 2023 guideline panel. Each score is compared with a publication-count-only null. |
| 10 | interpretability | Can each community be described in a sentence? | Per-community institution and state purity, journals, paper years, payers, leaders, and connectors, plus the nameable flag. |

### Movers

A mover is an HCP whose community changes across runs. `mover_score` is the share
of runs outside their most common community, and `community_split` lists every
community holding at least 15% of their runs. The class depends on how much
evidence sits behind the instability:

- **anchored**: one community holds at least 90% of runs.
- **multi-community mover**: split across two or more communities, with at least 5
  papers and 10 cohort coauthors. These are the movers worth acting on.
- **diffuse mover**: well-evidenced but spread thinly, with no second community
  reaching 15%.
- **data-thin mover**: unstable with little evidence, so the instability may only
  reflect sparse data.

Being split across communities shows that an HCP collaborates with several groups.
It does not show openness to other medications. The breadth test is the closest
proxy in this data. It measures whether movers already receive 2024 payments from
more distinct manufacturers than peers with the same publication count, degree,
and PageRank. Any association is contemporaneous and does not show causation.

### Weak labels

- **guideline_panel**: authors of the 2023 ASTCT DLBCL clinical practice
  recommendations (PMID 37419325). The build withheld this paper from every
  feature and edge, so it is a clean holdout. It has 16 positives.
- **curated_seed**: the 48 clinicians hand-picked for the original pilot. This label
  exists only when the local `Data/legacy/` pilot is present, and 44 of them are in
  this cohort. It is partly circular, because those people were retained in the
  cohort by rule.
- **cart_industry_advisor**: received 2024 consulting, speaking, or honoraria
  payments on records naming a lymphoma CAR-T product. Scores that include the
  industry axis are marked `circular` against this label.

## Interpretation limits

These scores measure academic visibility and observed industry engagement inside a
publication-selected cohort. Coauthorship is not referral, and a high score is not
evidence of prescribing influence, clinical quality, or patient volume. Citation
percentiles were observed in 2026, so experiment 9 also reports variants without
the impact axis to bound citation look-ahead. Run the harness again after any
change to the processed tables. Every run records the input hashes it used.
