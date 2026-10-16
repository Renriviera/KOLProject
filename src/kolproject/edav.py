"""Descriptive statistics and a seeded observed-coauthorship network figure."""
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
from .common import ROOT, PROCESSED, load, dump, now

def profile(seed=2357):
    h = pd.read_csv(PROCESSED / 'hcps.csv', dtype={'npi': str, 'hcp_id': str})
    a = pd.read_csv(PROCESSED / 'authorships.csv', dtype={'pmid': str})
    p = pd.read_csv(PROCESSED / 'publications.csv', dtype={'pmid': str})
    e = pd.read_csv(PROCESSED / 'coauthor_edges.csv')
    out = ROOT / 'eda'
    figures = out / 'visualizations'
    figures.mkdir(parents=True, exist_ok=True)
    h.isna().sum().rename('missing_count').to_frame().assign(total_rows=len(h)).to_csv(out / 'missingness.csv', index_label='column')
    states = h.groupby('npi_state').size().sort_values(ascending=False)
    states.rename('hcp_count').to_csv(out / 'hcp_by_registry_state.csv')
    linked_pmids = set(a.loc[a.analysis_include == 1, 'pmid'])
    years = p[p.pmid.isin(linked_pmids)].groupby('publication_year').size()
    years.rename('linked_distinct_publications').to_csv(out / 'publications_by_year.csv')
    summary = {'created_at_utc': now(), 'hcps': len(h), 'coauthor_edges': len(e),
               'unique_linked_eligible_publications': len(linked_pmids),
               'publication_count_median': float(h.publications_2017_2025.median()),
               'publication_count_min': int(h.publications_2017_2025.min()),
               'publication_count_max': int(h.publications_2017_2025.max()),
               'registry_states': int(states.size), 'communities': int(h.community_id.nunique()),
               'note': 'Publication-selected cohort. Registry locations may be stale; counts are not US workforce prevalence.'}
    dump(out / 'profile.json', summary)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'axes.spines.top': False, 'axes.spines.right': False})
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.8))
    bins = pd.cut(h.publications_2017_2025, bins=[2, 3, 5, 10, 20, 40, 80, float('inf')], right=False,
                  labels=['2', '3–4', '5–9', '10–19', '20–39', '40–79', '80+'])
    counts = bins.value_counts(sort=False)
    bars = ax[0].bar(range(len(counts)), counts.values, color='#286c8e')
    ax[0].set_xticks(range(len(counts)), counts.index.astype(str))
    ax[0].bar_label(bars, padding=3, fontsize=9)
    ax[0].set_ylim(0, counts.max() * 1.14)
    ax[0].set(xlabel='Supported eligible publications (2017–2025)', ylabel='HCPs', title='Publication evidence per HCP')
    ax[1].bar(years.index.astype(str), years.values, color='#34a18b')
    ax[1].set(xlabel='First publication year', ylabel='Distinct linked publications', title='Publications linked to the cohort')
    fig.suptitle(f'CAR-T lymphoma cohort • {len(h):,} qualified HCPs', fontsize=16, fontweight='bold')
    fig.text(.5, .005, 'Snapshot 2026-10-09 UTC • Publication metadata observations: 2026-10-08 to 2026-10-09', ha='center', fontsize=8, color='#555555')
    fig.tight_layout(rect=[0, .04, 1, .93])
    fig.savefig(figures / 'cohort_profile.png', dpi=180)
    plt.close(fig)
    graph = nx.Graph()
    graph.add_nodes_from(h.hcp_id)
    for row in e.itertuples():
        graph.add_edge(row.hcp_id_a, row.hcp_id_b, weight=row.fractional_weight)
    positions = nx.spring_layout(graph, seed=seed, iterations=150, weight='weight', k=1.5 / math.sqrt(len(h)))
    hmap = h.set_index('hcp_id')
    nodes = list(graph)
    sizes = [12 + 32 * math.log1p(hmap.loc[n, 'publications_2017_2025']) for n in nodes]
    colors = [int(hmap.loc[n, 'community_id']) % 20 for n in nodes]
    fig, ax = plt.subplots(figsize=(13, 10))
    nx.draw_networkx_edges(graph, positions, ax=ax, alpha=.06, edge_color='#436779', width=.35)
    nx.draw_networkx_nodes(graph, positions, ax=ax, nodelist=nodes, node_size=sizes, node_color=colors, cmap=plt.cm.tab20, edgecolors='white', linewidths=.35, alpha=.9)
    # Dense-network labels overlap; preserve the named ranking in a separate table.
    h.nlargest(12, 'pagerank')[['hcp_id', 'display_name', 'pagerank', 'publications_2017_2025']].to_csv(out / 'top_pagerank.csv', index=False)
    ax.set_title(f'Observed CAR-T lymphoma coauthorship\n{len(h):,} HCPs • {len(e):,} edges', fontsize=17, fontweight='bold', loc='left')
    ax.set_axis_off()
    fig.text(.05, .035, 'Node size: supported publication count • Color: greedy-modularity community\nEdges: shared publications; fractional weights adjust for author-list size. These are not patient referrals.', fontsize=9, color='#4b5563')
    fig.text(.95, .018, 'Snapshot 2026-10-09 UTC • Spring layout seed 2357', ha='right', fontsize=8, color='#6b7280')
    fig.tight_layout(rect=[0, .07, 1, .97])
    fig.savefig(figures / 'CART_KOL_network_500.png', dpi=180)
    plt.close(fig)
    return summary
