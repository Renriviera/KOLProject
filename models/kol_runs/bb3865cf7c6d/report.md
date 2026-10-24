# KOL community and composite-score experiments (run bb3865cf7c6d)

Scores describe academic visibility and observed 2024 industry engagement in a publication-selected cohort of 500 CAR-T lymphoma HCPs. They are not measures of prescribing influence, clinical quality, or referral volume.

- Inputs: 500 HCPs, 9378 coauthorship edges, 3320 included papers.
- Primary partition: Leiden (RB configuration), resolution 1.0, medoid of 50 seeds; 17 communities.
- Weak labels: guideline_panel (16 positives), cart_industry_advisor (58 positives), curated_seed (44 positives).
- curated_seed: 48 manually curated seeds in the local legacy pilot, 44 present in the 500-HCP cohort.

## Composite definitions

Each axis is the mean cohort percentile of its features; each composite is 100 × the weighted sum of axes. Per-HCP contribution columns in `kol_scores.csv` sum to the score.

| axis | features | academic_authority | network_connector | rising_voice |
|---|---|---|---|---|
| prestige | pagerank | 0.35 | 0.20 | 0.15 |
| bridging | betweenness + participation | 0.05 | 0.40 | 0.10 |
| impact | mean_citation_pct | 0.25 | 0.10 | 0.15 |
| leadership | senior_rate | 0.20 | 0.10 | 0.10 |
| momentum | recent_share | 0.05 | 0.10 | 0.40 |
| industry | log_consulting_usd + log_cart_product_usd | 0.10 | 0.10 | 0.10 |

### Top 15: academic_authority

| display_name | institution | academic_authority_score | academic_authority_rank | academic_authority_bootstrap_rank_p2_5 | academic_authority_bootstrap_rank_p97_5 | academic_authority_weight_p_top25 | academic_authority_robustness_class |
|---|---|---|---|---|---|---|---|
| OLALEKAN OLANIYI OLUWOLE | Vanderbilt | 84.38 | 1 | 1.00 | 13.00 | 1.00 | robust leader |
| NARENDRANATH EPPERLA | Ohio State | 83.51 | 2 | 1.00 | 15.03 | 0.99 | robust leader |
| MIGUEL A PERALES | Memorial Sloan Kettering | 82.37 | 3 | 1.00 | 22.08 | 1.00 | robust leader |
| PETER RIEDELL | University of Chicago | 82.04 | 4 | 1.00 | 41.00 | 0.97 | robust leader |
| NAUSHEEN AHMED | University of Kansas | 81.85 | 5 | 1.00 | 46.08 | 0.97 | robust leader |
| FREDERICK LUNDRY LOCKE | Moffitt | 80.84 | 6 | 3.00 | 21.03 | 0.98 | robust leader |
| SATTVA S NEELAPU | MD Anderson | 80.82 | 7 | 2.00 | 28.05 | 0.98 | robust leader |
| REEM KARMALI | Northwestern / Lurie | 80.26 | 8 | 1.00 | 81.33 | 1.00 | robust leader |
| CARON ALYCE JACOBSON | Dana-Farber | 80.22 | 9 | 2.00 | 22.00 | 0.94 | robust leader |
| JEREMY S ABRAMSON | Mass General Brigham | 79.70 | 10 | 1.00 | 30.03 | 0.91 | robust leader |
| LEO GORDON | Northwestern / Lurie | 79.27 | 11 | 1.98 | 65.05 | 0.92 | robust leader |
| NIRAV N SHAH | Medical College of Wisconsin | 78.85 | 12 | 3.00 | 76.00 | 0.91 | robust leader |
| MICHAEL DAVID JAIN | Moffitt | 78.66 | 13 | 4.00 | 40.03 | 0.94 | robust leader |
| ARMIN GHOBADI | WashU / Siteman | 77.36 | 14 | 8.98 | 65.05 | 0.73 | weight-sensitive |
| MANALI K KAMDAR | University of Colorado | 77.32 | 15 | 3.98 | 68.03 | 0.73 | weight-sensitive |

### Top 15: network_connector

| display_name | institution | network_connector_score | network_connector_rank | network_connector_bootstrap_rank_p2_5 | network_connector_bootstrap_rank_p97_5 | network_connector_weight_p_top25 | network_connector_robustness_class |
|---|---|---|---|---|---|---|---|
| NAUSHEEN AHMED | University of Kansas | 89.24 | 1 | 1.00 | 14.00 | 1.00 | robust leader |
| PETER RIEDELL | University of Chicago | 89.04 | 2 | 1.00 | 10.00 | 1.00 | robust leader |
| OLALEKAN OLANIYI OLUWOLE | Vanderbilt | 88.41 | 3 | 1.00 | 14.00 | 1.00 | robust leader |
| NARENDRANATH EPPERLA | Ohio State | 86.64 | 4 | 1.00 | 20.03 | 1.00 | robust leader |
| CARON ALYCE JACOBSON | Dana-Farber | 85.15 | 5 | 2.00 | 12.00 | 1.00 | robust leader |
| MIGUEL A PERALES | Memorial Sloan Kettering | 84.99 | 6 | 1.98 | 11.03 | 1.00 | robust leader |
| FREDERICK LUNDRY LOCKE | Moffitt | 84.32 | 7 | 4.00 | 27.05 | 0.98 | robust leader |
| NATALIE SOPHIA GROVER | UNC / Lineberger | 83.83 | 8 | 3.00 | 41.05 | 1.00 | robust leader |
| NILANJAN GHOSH | Atrium / Levine | 83.47 | 9 | 3.00 | 43.00 | 1.00 | robust leader |
| SAYED MEHDI HUSSAIN HAMADANI | Medical College of Wisconsin | 82.82 | 10 | 7.00 | 30.03 | 0.95 | robust leader |
| LORETTA J NASTOUPIL | MD Anderson | 82.27 | 11 | 3.98 | 31.03 | 0.99 | robust leader |
| MAZYAR SHADMAN | Fred Hutch / UW | 82.20 | 12 | 5.00 | 36.03 | 0.98 | robust leader |
| BRIAN THOMAS HILL | Cleveland Clinic | 81.68 | 13 | 4.00 | 38.03 | 0.93 | robust leader |
| MANALI K KAMDAR | University of Colorado | 81.41 | 14 | 4.98 | 42.00 | 0.93 | robust leader |
| LEO GORDON | Northwestern / Lurie | 81.15 | 15 | 5.98 | 59.13 | 0.92 | robust leader |

### Top 15: rising_voice

| display_name | institution | rising_voice_score | rising_voice_rank | rising_voice_bootstrap_rank_p2_5 | rising_voice_bootstrap_rank_p97_5 | rising_voice_weight_p_top25 | rising_voice_robustness_class |
|---|---|---|---|---|---|---|---|
| NAUSHEEN AHMED | University of Kansas | 88.45 | 1 | 1.00 | 15.00 | 1.00 | robust leader |
| PETER RIEDELL | University of Chicago | 87.05 | 2 | 1.00 | 18.00 | 1.00 | robust leader |
| NARENDRANATH EPPERLA | Ohio State | 85.45 | 3 | 1.00 | 54.03 | 1.00 | robust leader |
| SAURABH DAHIYA | Stanford | 84.62 | 4 | 1.00 | 19.03 | 1.00 | robust leader |
| REEM KARMALI | Northwestern / Lurie | 80.94 | 5 | 1.00 | 118.03 | 1.00 | robust leader |
| MANALI K KAMDAR | University of Colorado | 79.56 | 6 | 3.00 | 119.33 | 0.99 | robust leader |
| LORI ANN LESLIE | Hackensack / Theurer | 79.38 | 7 | 3.98 | 112.20 | 0.99 | robust leader |
| JAE HONG PARK | Memorial Sloan Kettering | 78.84 | 8 | 2.98 | 88.18 | 0.93 | robust leader |
| ANDREW C. IP | Hackensack / Theurer | 78.69 | 9 | 4.00 | 101.13 | 0.93 | robust leader |
| EVANDRO D BEZERRA | Ohio State | 78.37 | 10 | 5.00 | 115.05 | 0.98 | robust leader |
| OLALEKAN OLANIYI OLUWOLE | Vanderbilt | 76.86 | 11 | 3.00 | 86.00 | 0.74 | weight-sensitive |
| KRISH PATEL | Sarah Cannon Research Institute | 76.82 | 12 | 5.00 | 68.18 | 0.72 | weight-sensitive |
| MIGUEL A PERALES | Memorial Sloan Kettering | 76.71 | 13 | 5.00 | 64.03 | 0.74 | weight-sensitive |
| SAMUEL YAMSHON | Weill Cornell | 76.62 | 14 | 2.00 | 222.13 | 0.79 | weight-sensitive |
| PRIYANKA A POPHALI | University of Wisconsin | 76.23 | 15 | 5.98 | 145.18 | 0.81 | weight-sensitive |

## 1. Partition stability

_Seed and resolution stability of Leiden versus Louvain and the published greedy partition._

- **primary_resolution**: 1.0
- **primary_communities**: 17
- **leiden_seed_ami_mean**: 0.871
- **louvain_seed_ami_mean**: 0.792
- **greedy_ami_vs_leiden**: 0.778
- **seed_stable_communities**: 16
- **nodes_below_consensus_threshold**: 79
- runtime: 20.1s

| method | seeds | seed_to_seed_ami_mean | seed_to_seed_ami_min | ami_vs_leiden_medoid | modularity | communities | disconnected_communities |
|---|---|---|---|---|---|---|---|
| leiden (RB configuration) | 50 | 0.871 | 0.732 | 1.000 | 0.497 | 17.000 | 0 |
| louvain (igraph multilevel) | 50 | 0.792 | 0.635 | 0.803 | 0.491 | 16.000 | 0 |
| greedy modularity (published community_id) | 1 |  |  | 0.778 | 0.487 | 16.000 | 0 |

| resolution | communities_median | modularity_medoid | seed_ami_mean | seed_ami_min | largest | smallest_nontrivial | communities_ge_5 |
|---|---|---|---|---|---|---|---|
| 0.500 | 10.000 | 0.416 | 0.721 | 0.447 | 219 | 5 | 7 |
| 0.750 | 13.000 | 0.490 | 0.829 | 0.641 | 109 | 6 | 9 |
| 1.000 | 16.000 | 0.497 | 0.871 | 0.732 | 61 | 10 | 13 |
| 1.250 | 17.000 | 0.496 | 0.913 | 0.758 | 53 | 11 | 13 |
| 1.500 | 20.000 | 0.490 | 0.905 | 0.804 | 48 | 3 | 15 |
| 2.000 | 26.000 | 0.479 | 0.961 | 0.899 | 42 | 5 | 22 |

## 2. Edge-weighting ablation

_Recompute everything under four edge weightings and compare to the fractional primary._

- **robust_top50_academic_authority**: 42
- **robust_top50_network_connector**: 36
- **robust_top50_rising_voice**: 38
- **min_ami_vs_fractional**: 0.5
- **least_similar_weighting**: binary
- runtime: 1.2s

| weighting | definition | edges | communities | ami_vs_fractional | spearman_pagerank | spearman_betweenness | spearman_participation | spearman_strength | top50_overlap_academic_authority | top50_overlap_network_connector | top50_overlap_rising_voice |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fractional | sum over shared papers of 1/(author_count - 1); the published weighting | 9378 | 17 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| binary | one per coauthor pair regardless of how many papers | 9378 | 11 | 0.500 | 0.850 | 0.663 | 0.799 | 0.846 | 0.860 | 0.880 | 0.860 |
| count | number of shared papers | 9378 | 15 | 0.558 | 0.935 | 0.797 | 0.743 | 0.928 | 0.960 | 0.840 | 0.920 |
| fractional_min2 | fractional weight, keeping only pairs with at least two shared papers | 3851 | 70 | 0.680 | 0.922 | 0.826 | 0.702 | 0.939 | 0.940 | 0.880 | 0.880 |

## 3. Bootstrap over papers

_Perturb the literature, rebuild graph, communities, features, and scores, and measure what survives._

- **replicates_per_perturbation**: 200
- **drop_fraction**: 0.1
- **resample_stable_communities**: 0
- **resample_dissolving_communities**: 4
- **drop10_stable_communities**: 8
- **drop10_dissolving_communities**: 1
- **academic_authority_median_ci_width_top25**: 56.1
- **network_connector_median_ci_width_top25**: 34.0
- **rising_voice_median_ci_width_top25**: 110.1
- runtime: 96.3s

| community | size | jaccard_resample_mean | jaccard_resample_p10 | verdict_resample | jaccard_drop10_mean | jaccard_drop10_p10 | verdict_drop10 |
|---|---|---|---|---|---|---|---|
| 1 | 61 | 0.439 | 0.276 | dissolves | 0.755 | 0.494 | stable |
| 2 | 56 | 0.499 | 0.338 | dissolves | 0.729 | 0.603 | pattern, not firm |
| 3 | 55 | 0.541 | 0.355 | weak | 0.711 | 0.486 | pattern, not firm |
| 4 | 53 | 0.697 | 0.525 | pattern, not firm | 0.893 | 0.836 | stable |
| 5 | 49 | 0.607 | 0.481 | pattern, not firm | 0.822 | 0.634 | stable |
| 6 | 44 | 0.745 | 0.629 | pattern, not firm | 0.911 | 0.823 | stable |
| 7 | 37 | 0.546 | 0.283 | weak | 0.736 | 0.354 | pattern, not firm |
| 8 | 32 | 0.563 | 0.329 | weak | 0.765 | 0.562 | stable |
| 9 | 30 | 0.698 | 0.519 | pattern, not firm | 0.818 | 0.674 | stable |
| 10 | 25 | 0.207 | 0.111 | dissolves | 0.290 | 0.143 | dissolves |
| 11 | 22 | 0.561 | 0.210 | weak | 0.897 | 0.840 | stable |
| 12 | 21 | 0.552 | 0.176 | weak | 0.798 | 0.277 | stable |
| 13 | 10 | 0.401 | 0.120 | dissolves | 0.677 | 0.159 | pattern, not firm |
| 14 | 2 | 0.912 | 0.500 | too small to assess | 0.998 | 1.000 | too small to assess |
| 15 | 1 | 1.000 | 1.000 | too small to assess | 1.000 | 1.000 | too small to assess |
| 16 | 1 | 1.000 | 1.000 | too small to assess | 1.000 | 1.000 | too small to assess |
| 17 | 1 | 1.000 | 1.000 | too small to assess | 1.000 | 1.000 | too small to assess |

| composite | median_ci_width_top25 | mean_p_top25_for_primary_top25 |
|---|---|---|
| academic_authority | 56.075 | 0.700 |
| network_connector | 34.025 | 0.766 |
| rising_voice | 110.050 | 0.610 |

## 4. Node-removal sensitivity

_Remove top PageRank nodes, globally and per community, and see which communities survive._

- **leader_dependent_communities**: 5
- **communities_tested**: 13
- **mean_jaccard_after_top10_removed**: 0.735
- runtime: 9.0s

| removed_top_pagerank | removed_names | mean_best_jaccard | communities_below_0_5 | ami_on_remaining |
|---|---|---|---|---|
| 1 | FREDERICK LUNDRY LOCKE | 0.921 | 0 | 0.932 |
| 3 | FREDERICK LUNDRY LOCKE; MIGUEL A PERALES; SAYED MEHDI HUSSAIN HAMADANI | 0.976 | 0 | 0.970 |
| 5 | FREDERICK LUNDRY LOCKE; MIGUEL A PERALES; SAYED MEHDI HUSSAIN HAMADANI; JULIO C CHAVEZ; CARON ALYCE JACOBSON | 0.926 | 0 | 0.950 |
| 10 | FREDERICK LUNDRY LOCKE; MIGUEL A PERALES; SAYED MEHDI HUSSAIN HAMADANI; JULIO C CHAVEZ; CARON ALYCE JACOBSON; OLALEKAN OLANIYI OLUWOLE; MOHAMED A KHARFAN DABAJA; SATTVA S NEELAPU; YI LIN; SAIRAH AHMED | 0.735 | 3 | 0.836 |
| 20 | FREDERICK LUNDRY LOCKE; MIGUEL A PERALES; SAYED MEHDI HUSSAIN HAMADANI; JULIO C CHAVEZ; CARON ALYCE JACOBSON; OLALEKAN OLANIYI OLUWOLE; MOHAMED A KHARFAN DABAJA; SATTVA S NEELAPU; YI LIN; SAIRAH AHMED; MICHAEL DAVID JAIN; LORETTA J NASTOUPIL; DAVID BERNARD MIKLOS; MAZYAR SHADMAN; ALEX HERRERA; MARIA LIA PALOMBA; STEPHEN J SCHUSTER; PETER RIEDELL; BRIAN THOMAS HILL; GUNJAN LALITCHANDRA SHAH | 0.764 | 1 | 0.823 |

| community | size | removed_leader | noise_floor_jaccard | jaccard_after_removal | drop_beyond_noise | pieces_holding_25pct | leader_dependent |
|---|---|---|---|---|---|---|---|
| 1 | 61 | OLALEKAN OLANIYI OLUWOLE | 0.968 | 0.574 | 0.394 | 1 | True |
| 2 | 56 | SAYED MEHDI HUSSAIN HAMADANI | 0.852 | 0.860 | -0.007 | 1 | False |
| 3 | 55 | NARENDRANATH EPPERLA | 0.846 | 0.365 | 0.481 | 2 | True |
| 4 | 53 | STEPHEN J SCHUSTER | 0.929 | 0.981 | -0.052 | 1 | False |
| 5 | 49 | CARON ALYCE JACOBSON | 0.796 | 0.896 | -0.100 | 1 | False |
| 6 | 44 | MIGUEL A PERALES | 0.956 | 0.955 | 0.001 | 1 | False |
| 7 | 37 | DAVID BERNARD MIKLOS | 0.974 | 1.000 | -0.026 | 1 | False |
| 8 | 32 | MAZYAR SHADMAN | 0.906 | 0.743 | 0.163 | 1 | True |
| 9 | 30 | SATTVA S NEELAPU | 0.933 | 0.700 | 0.233 | 1 | True |
| 10 | 25 | CRAIG SAUTER | 0.846 | 0.960 | -0.114 | 1 | False |
| 11 | 22 | FREDERICK LUNDRY LOCKE | 0.909 | 0.952 | -0.043 | 1 | False |
| 12 | 21 | ALEX HERRERA | 0.875 | 0.286 | 0.589 | 1 | True |
| 13 | 10 | NATALIE SOPHIA GROVER | 1.000 | 1.000 | 0.000 | 1 | False |

## 5. Leave-one-axis-out importance

_Drop one axis at a time, renormalize, and measure how much the ranking moves._

- **most_important_axis_academic_authority**: prestige
- **most_important_axis_network_connector**: bridging
- **most_important_axis_rising_voice**: momentum
- runtime: 0.0s

| composite | axis | weight | spearman_vs_full | top50_overlap | mean_abs_rank_shift_top50 | marginal_spearman_axis_vs_score | importance_rank |
|---|---|---|---|---|---|---|---|
| academic_authority | prestige | 0.350 | 0.712 | 0.540 | 34.260 | 0.811 | 1.000 |
| academic_authority | leadership | 0.200 | 0.920 | 0.760 | 19.020 | 0.236 | 2.000 |
| academic_authority | impact | 0.250 | 0.855 | 0.760 | 16.380 | 0.430 | 3.000 |
| academic_authority | industry | 0.100 | 0.992 | 0.900 | 9.620 | 0.412 | 4.000 |
| academic_authority | momentum | 0.050 | 0.994 | 0.920 | 5.300 | 0.146 | 5.000 |
| academic_authority | bridging | 0.050 | 0.998 | 0.960 | 2.700 | 0.623 | 6.000 |
| network_connector | bridging | 0.400 | 0.880 | 0.780 | 14.680 | 0.907 | 1.000 |
| network_connector | industry | 0.100 | 0.993 | 0.940 | 6.900 | 0.459 | 2.000 |
| network_connector | momentum | 0.100 | 0.981 | 0.940 | 6.700 | 0.300 | 3.000 |
| network_connector | leadership | 0.100 | 0.982 | 0.900 | 6.320 | 0.202 | 4.000 |
| network_connector | prestige | 0.200 | 0.944 | 0.840 | 5.740 | 0.773 | 5.000 |
| network_connector | impact | 0.100 | 0.981 | 0.920 | 5.200 | 0.177 | 6.000 |
| rising_voice | momentum | 0.400 | 0.611 | 0.340 | 60.320 | 0.820 | 1.000 |
| rising_voice | prestige | 0.150 | 0.956 | 0.820 | 15.380 | 0.461 | 2.000 |
| rising_voice | impact | 0.150 | 0.958 | 0.760 | 14.060 | 0.224 | 3.000 |
| rising_voice | leadership | 0.100 | 0.981 | 0.780 | 11.460 | 0.150 | 4.000 |
| rising_voice | industry | 0.100 | 0.992 | 0.880 | 8.160 | 0.365 | 5.000 |
| rising_voice | bridging | 0.100 | 0.990 | 0.840 | 6.320 | 0.522 | 6.000 |

## 6. Weight perturbation

_Sample weights near each composite (Dirichlet) and uniformly over its axes; count top-k stays._

- **academic_authority_mean_l1_shift_near**: 0.29
- **academic_authority_mean_l1_shift_uninformative**: 0.847
- **academic_authority_robust_leaders**: 13
- **academic_authority_weight_sensitive**: 19
- **academic_authority_top25_under_any_weights**: 3
- **network_connector_mean_l1_shift_near**: 0.295
- **network_connector_mean_l1_shift_uninformative**: 0.815
- **network_connector_robust_leaders**: 15
- **network_connector_weight_sensitive**: 16
- **network_connector_top25_under_any_weights**: 4
- **rising_voice_mean_l1_shift_near**: 0.298
- **rising_voice_mean_l1_shift_uninformative**: 0.798
- **rising_voice_robust_leaders**: 10
- **rising_voice_weight_sensitive**: 18
- **rising_voice_top25_under_any_weights**: 3
- runtime: 0.2s

## 7. Collinearity and volume loading

_Show which features are the same signal, and whether volume-loading changes the leaders._

- **redundant_pairs_rho_ge_0_85**: ['strength~pagerank (0.97)', 'publications~strength (0.90)', 'publications~pagerank (0.90)', 'pagerank~within_module_z (0.86)']
- **highest_vif**: strength (24.2)
- **academic_authority_top50_overlap_if_volume_loaded**: 0.98
- **network_connector_top50_overlap_if_volume_loaded**: 0.98
- **rising_voice_top50_overlap_if_volume_loaded**: 0.98
- runtime: 0.0s

| composite | prestige_axis_primary | prestige_axis_volume_loaded | spearman_loaded_vs_primary | top50_overlap_loaded_vs_primary | spearman_primary_vs_publication_count | spearman_loaded_vs_publication_count |
|---|---|---|---|---|---|---|
| academic_authority | pagerank | pagerank+strength+degree+publications | 0.981 | 0.980 | 0.762 | 0.769 |
| network_connector | pagerank | pagerank+strength+degree+publications | 0.995 | 0.980 | 0.721 | 0.718 |
| rising_voice | pagerank | pagerank+strength+degree+publications | 0.997 | 0.980 | 0.399 | 0.397 |

| feature | r2_from_others | vif |
|---|---|---|
| strength | 0.959 | 24.218 |
| pagerank | 0.957 | 23.246 |
| recent_publications | 0.951 | 20.342 |
| publications | 0.942 | 17.209 |
| degree | 0.857 | 6.995 |
| within_module_z | 0.831 | 5.905 |
| recent_share | 0.822 | 5.610 |
| participation | 0.624 | 2.660 |
| betweenness | 0.593 | 2.455 |
| log_consulting_usd | 0.225 | 1.291 |
| log_cart_product_usd | 0.216 | 1.276 |
| senior_rate | 0.171 | 1.207 |
| mean_citation_pct | 0.149 | 1.175 |

## 8. Face validity vs weak labels

_Agreement with weak external labels. A sanity check, never validation of influence._

- **cart_industry_advisor_best**: publications AUC 0.81 [0.74, 0.86]
- **cart_industry_advisor_publication_count_auc**: 0.81
- **curated_seed_best**: publications AUC 0.95 [0.92, 0.97]
- **curated_seed_publication_count_auc**: 0.95
- **guideline_panel_best**: pagerank AUC 0.87 [0.76, 0.95]
- **guideline_panel_publication_count_auc**: 0.83
- runtime: 1.8s

| label | score | circular | auc | auc_ci_low | auc_ci_high | precision_at_25 | lift_at_50 |
|---|---|---|---|---|---|---|---|
| cart_industry_advisor | equal_weight_consensus | True | 0.880 | 0.837 | 0.919 | 0.640 | 5.345 |
| cart_industry_advisor | network_connector | True | 0.853 | 0.799 | 0.899 | 0.560 | 4.483 |
| cart_industry_advisor | academic_authority | True | 0.847 | 0.788 | 0.896 | 0.560 | 4.655 |
| cart_industry_advisor | log_consulting_usd | True | 0.838 | 0.788 | 0.882 | 0.440 | 3.621 |
| cart_industry_advisor | publications | False | 0.805 | 0.743 | 0.863 | 0.480 | 2.931 |
| cart_industry_advisor | baseline_exploratory_score (published) | False | 0.791 | 0.721 | 0.849 | 0.360 | 3.276 |
| cart_industry_advisor | pagerank | False | 0.789 | 0.718 | 0.851 | 0.480 | 3.276 |
| cart_industry_advisor | rising_voice | True | 0.763 | 0.704 | 0.823 | 0.640 | 3.103 |
| cart_industry_advisor | betweenness | False | 0.753 | 0.684 | 0.819 | 0.480 | 3.448 |
| cart_industry_advisor | participation | False | 0.691 | 0.632 | 0.751 | 0.240 | 1.724 |
| cart_industry_advisor | senior_rate | False | 0.607 | 0.534 | 0.678 | 0.200 | 1.379 |
| cart_industry_advisor | recent_share | False | 0.556 | 0.481 | 0.631 | 0.200 | 1.207 |
| cart_industry_advisor | mean_citation_pct | False | 0.531 | 0.469 | 0.587 | 0.000 | 0.345 |
| curated_seed | publications | False | 0.948 | 0.925 | 0.969 | 0.760 | 6.364 |
| curated_seed | pagerank | False | 0.929 | 0.880 | 0.965 | 0.760 | 5.682 |
| curated_seed | academic_authority | False | 0.929 | 0.893 | 0.958 | 0.600 | 6.591 |
| curated_seed | baseline_exploratory_score (published) | False | 0.926 | 0.890 | 0.957 | 0.560 | 5.909 |
| curated_seed | network_connector | False | 0.919 | 0.870 | 0.956 | 0.760 | 6.136 |
| curated_seed | equal_weight_consensus | False | 0.908 | 0.856 | 0.945 | 0.720 | 6.136 |
| curated_seed | betweenness | False | 0.884 | 0.821 | 0.933 | 0.800 | 6.364 |
| curated_seed | rising_voice | False | 0.794 | 0.727 | 0.857 | 0.360 | 3.182 |
| curated_seed | log_consulting_usd | False | 0.761 | 0.689 | 0.832 | 0.360 | 3.182 |
| curated_seed | participation | False | 0.742 | 0.667 | 0.803 | 0.280 | 2.955 |
| curated_seed | senior_rate | False | 0.646 | 0.565 | 0.728 | 0.160 | 1.364 |
| curated_seed | mean_citation_pct | False | 0.566 | 0.514 | 0.618 | 0.000 | 0.000 |
| curated_seed | recent_share | False | 0.520 | 0.445 | 0.603 | 0.040 | 0.682 |
| guideline_panel | pagerank | False | 0.869 | 0.757 | 0.952 | 0.320 | 5.625 |
| guideline_panel | baseline_exploratory_score (published) | False | 0.839 | 0.735 | 0.930 | 0.200 | 5.000 |
| guideline_panel | network_connector | False | 0.834 | 0.696 | 0.933 | 0.280 | 6.250 |
| guideline_panel | publications | False | 0.833 | 0.715 | 0.940 | 0.280 | 5.625 |
| guideline_panel | betweenness | False | 0.828 | 0.698 | 0.932 | 0.320 | 5.625 |
| guideline_panel | academic_authority | False | 0.817 | 0.709 | 0.915 | 0.200 | 5.000 |
| guideline_panel | equal_weight_consensus | False | 0.790 | 0.646 | 0.911 | 0.200 | 5.000 |
| guideline_panel | participation | False | 0.761 | 0.634 | 0.869 | 0.200 | 3.750 |
| guideline_panel | rising_voice | False | 0.720 | 0.573 | 0.849 | 0.160 | 3.125 |
| guideline_panel | log_consulting_usd | False | 0.666 | 0.528 | 0.803 | 0.120 | 2.500 |
| guideline_panel | senior_rate | False | 0.548 | 0.416 | 0.682 | 0.040 | 0.625 |
| guideline_panel | mean_citation_pct | False | 0.532 | 0.440 | 0.640 | 0.000 | 0.625 |
| guideline_panel | recent_share | False | 0.521 | 0.387 | 0.642 | 0.040 | 1.250 |

## 9. Temporal holdout

_Score from papers through the cutoff year only; test against what happened afterward._

- **cutoff_year**: 2022
- **hcps_without_pre_period_papers**: 80
- **pre_period_edges**: 3768
- **post_period_new_edges**: 5610
- **scores_beating_volume_on_publications_2023_2025**: ['network_connector (no industry, no impact)']
- **best_on_publications_2023_2025**: network_connector (no industry, no impact) (rho 0.43, delta +0.04)
- **scores_beating_volume_on_new_coauthors_2023_2025**: ['academic_authority (no industry, no impact)', 'network_connector (no industry)', 'network_connector (no industry, no impact)', 'pagerank alone']
- **best_on_new_coauthors_2023_2025**: network_connector (no industry, no impact) (rho 0.28, delta +0.13)
- **scores_beating_volume_on_log_consulting_usd_2024**: ['pagerank alone']
- **best_on_log_consulting_usd_2024**: academic_authority (no industry, no impact) (rho 0.30, delta +0.05)
- **best_on_guideline_panel**: pagerank alone AUC 0.82
- **volume_null_guideline_panel_auc**: 0.8
- runtime: 4.7s
- cells read: Spearman rho (change versus the volume-only null [95% bootstrap CI])

| score | publications_2023_2025 | new_coauthors_2023_2025 | log_consulting_usd_2024 | guideline_panel_auc |
|---|---|---|---|---|
| academic_authority (no industry) | 0.37 (-0.02 [-0.07, +0.03]) | 0.18 (+0.02 [-0.03, +0.07]) | 0.27 (+0.03 [-0.02, +0.09]) | 0.81 [0.68, 0.91] |
| academic_authority (no industry, no impact) | 0.37 (-0.02 [-0.07, +0.03]) | 0.24 (+0.08 [+0.03, +0.13]) | 0.30 (+0.05 [-0.00, +0.11]) | 0.79 [0.65, 0.91] |
| network_connector (no industry) | 0.42 (+0.03 [-0.01, +0.07]) | 0.25 (+0.09 [+0.05, +0.14]) | 0.28 (+0.04 [-0.01, +0.08]) | 0.81 [0.70, 0.91] |
| network_connector (no industry, no impact) | 0.43 (+0.04 [+0.01, +0.09]) | 0.28 (+0.13 [+0.08, +0.18]) | 0.28 (+0.04 [-0.01, +0.09]) | 0.81 [0.67, 0.91] |
| rising_voice (no industry) | 0.22 (-0.17 [-0.25, -0.09]) | 0.14 (-0.02 [-0.11, +0.08]) | 0.13 (-0.12 [-0.20, -0.03]) | 0.68 [0.56, 0.80] |
| rising_voice (no industry, no impact) | 0.23 (-0.15 [-0.24, -0.07]) | 0.19 (+0.03 [-0.06, +0.13]) | 0.13 (-0.12 [-0.20, -0.02]) | 0.65 [0.50, 0.79] |
| publications (volume-only null) | 0.39 (+0.00 [+0.00, +0.00]) | 0.16 (+0.00 [+0.00, +0.00]) | 0.24 (+0.00 [+0.00, +0.00]) | 0.80 [0.67, 0.90] |
| pagerank alone | 0.40 (+0.01 [-0.02, +0.04]) | 0.20 (+0.05 [+0.01, +0.08]) | 0.28 (+0.04 [+0.00, +0.08]) | 0.82 [0.69, 0.92] |
| mean_citation_pct alone | 0.02 (-0.37 [-0.46, -0.27]) | -0.15 (-0.30 [-0.39, -0.21]) | 0.06 (-0.19 [-0.29, -0.10]) | 0.61 [0.53, 0.69] |

## 10. Community interpretability audit

_Describe each community in facts a brand team can read, and flag the ones safe to name._

- **communities**: 17
- **nameable_communities**: 8
- **institution_anchored (purity>=0.4)**: 13
- **labels**: ['Cross-regional network around Cleveland Clinic', 'Penn / Abramson orbit', 'Cross-regional network around Mass General Brigham', 'Memorial Sloan Kettering orbit', 'Fred Hutch / UW orbit', 'MD Anderson orbit', 'Moffitt orbit', 'City of Hope orbit']
- runtime: 0.1s

| community | label | size | nameable | seed_consensus | jaccard_literature_drop | jaccard_full_bootstrap | institution_purity | leaders |
|---|---|---|---|---|---|---|---|---|
| 1 | Cross-regional network around Cleveland Clinic | 61 | True | 0.925 | 0.755 | 0.439 | 0.148 | OLALEKAN OLANIYI OLUWOLE; BRIAN THOMAS HILL; LAZAROS J LEKAKIS |
| 2 | Mayo Clinic orbit | 56 | False | 0.858 | 0.729 | 0.499 | 0.571 | SAYED MEHDI HUSSAIN HAMADANI; MOHAMED A KHARFAN DABAJA; YI LIN |
| 3 | Cross-regional network around Ohio State | 55 | False | 0.934 | 0.711 | 0.541 | 0.364 | NARENDRANATH EPPERLA; REEM KARMALI; NIRAV N SHAH |
| 4 | Penn / Abramson orbit | 53 | True | 0.973 | 0.893 | 0.697 | 0.434 | STEPHEN J SCHUSTER; PETER RIEDELL; JOSEPH PATRICK MCGUIRK |
| 5 | Cross-regional network around Mass General Brigham | 49 | True | 0.849 | 0.822 | 0.607 | 0.306 | CARON ALYCE JACOBSON; MATTHEW JOHN FRIGAULT; JEREMY S ABRAMSON |
| 6 | Memorial Sloan Kettering orbit | 44 | True | 0.988 | 0.911 | 0.745 | 0.773 | MIGUEL A PERALES; MARIA LIA PALOMBA; GUNJAN LALITCHANDRA SHAH |
| 7 | Stanford orbit | 37 | False | 0.961 | 0.736 | 0.546 | 0.541 | DAVID BERNARD MIKLOS; AARON PAUL RAPOPORT; SAURABH DAHIYA |
| 8 | Fred Hutch / UW orbit | 32 | True | 0.867 | 0.765 | 0.563 | 0.719 | MAZYAR SHADMAN; DAVID G MALONEY; JORDAN GAUTHIER |
| 9 | MD Anderson orbit | 30 | True | 0.972 | 0.818 | 0.698 | 0.767 | SATTVA S NEELAPU; SAIRAH AHMED; LORETTA J NASTOUPIL |
| 10 | Cross-regional network around Weill Cornell | 25 | False | 0.565 | 0.290 | 0.207 | 0.240 | CRAIG SAUTER; PAOLO F CAIMI; BRAD STEVEN KAHL |
| 11 | Moffitt orbit | 22 | True | 0.937 | 0.897 | 0.561 | 0.864 | FREDERICK LUNDRY LOCKE; JULIO C CHAVEZ; MICHAEL DAVID JAIN |
| 12 | City of Hope orbit | 21 | True | 1.000 | 0.798 | 0.552 | 0.905 | ALEX HERRERA; GEOFFREY SHOUSE; TANYA SIDDIQI |
| 13 | Baylor orbit | 10 | False | 0.840 | 0.677 | 0.401 | 0.500 | NATALIE SOPHIA GROVER; HELEN HESLOP; PREMAL DILIP LULLA |
| 14 | Small group (2) at University of California Davis Comprehensive Cancer Center | 2 | False | 1.000 | 0.998 | 0.912 | 1.000 | NASEEM SHAMS ESTEGHAMAT; JOSEPH M TUSCANO |
| 15 | Small group (1) at Roswell Park | 1 | False | 1.000 | 1.000 | 1.000 | 1.000 | MATTHEW J BARTH |
| 16 | Small group (1) at University of Kentucky HealthCare | 1 | False | 1.000 | 1.000 | 1.000 | 1.000 | REINHOLD MUNKER |
| 17 | Small group (1) at Beth Israel Deaconess Medical Center | 1 | False | 1.000 | 1.000 | 1.000 | 1.000 | DAVID E AVIGAN |

## Edge weightings

- **fractional**: sum over shared papers of 1/(author_count - 1); the published weighting
- **binary**: one per coauthor pair regardless of how many papers
- **count**: number of shared papers
- **fractional_min2**: fractional weight, keeping only pairs with at least two shared papers
