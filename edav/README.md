# Exploratory data analysis and visualization

Regenerate with `uv run kolproject stage=edav` from the project root.
The implementation is `src/kolproject/edav.py`.

* `profile.json`: cohort size, publication distribution, graph counts and geographic coverage.
* `missingness.csv`: missing fields; blanks are not replaced by zeros.
* `hcp_by_registry_state.csv`: primary NPI practice geography, which may be stale.
* `publications_by_year.csv`: unique eligible publications linked to at least one selected HCP.
* `figures/cohort_profile.png`: publication-count distribution and publication years.
* `figures/CART_KOL_network_500.png`: actual coauthorship graph, not a generated illustration.

Network positions use NetworkX spring layout with seed 2357. Nodes are verified
cohort NPIs, node area increases with supported publication count, colors denote
greedy-modularity communities, and edges require one or more observed shared PMIDs.
Fractional edge weights sum 1/(all indexed paper authors minus 1). Twelve high-PageRank
nodes are labeled for readability. The graph does not represent actual patient referrals.
