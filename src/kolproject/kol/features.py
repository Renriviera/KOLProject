"""Per-HCP features recomputed from any (possibly resampled or time-sliced) coauthorship graph."""
from __future__ import annotations

import igraph as ig
import numpy as np
import pandas as pd

from .communities import participation_and_z, hub_role
from .data import Cohort
from .graph import CoauthorIncidence


def shrunk_rate(successes: np.ndarray, trials: np.ndarray, pseudo: float) -> np.ndarray:
    """Beta-binomial shrinkage toward the pooled cohort rate, so two papers cannot dominate."""
    pooled = successes.sum() / trials.sum() if trials.sum() else 0.0
    return (successes + pseudo * pooled) / (trials + pseudo)


def within_percentile(values: pd.Series, groups: pd.Series) -> pd.Series:
    return values.groupby(groups).rank(method='average', pct=True)


def compute_features(cohort: Cohort, incidence: CoauthorIncidence, graph: ig.Graph,
                     membership: np.ndarray, multiplicity: np.ndarray | None = None,
                     recent_from: int = 2023, pseudo_papers: float = 4.0) -> pd.DataFrame:
    n = incidence.n
    m = np.ones(len(incidence.pmids)) if multiplicity is None else np.asarray(multiplicity, dtype=float)
    link_m = m[incidence.link_paper]
    links = cohort.links
    citation = cohort.papers.citation_year_percentile.to_numpy()[incidence.link_paper]
    year = links.publication_year.to_numpy()

    def per_node(values):
        return np.bincount(incidence.link_node, weights=link_m * values, minlength=n)

    publications = per_node(np.ones(len(links)))
    senior = per_node(links.senior.to_numpy().astype(float))
    recent = per_node((year >= recent_from).astype(float))
    citation_sum = per_node(citation)
    fractional_publications = per_node(1.0 / links.author_count.to_numpy())

    with np.errstate(invalid='ignore', divide='ignore'):
        mean_citation = np.where(publications > 0, citation_sum / publications, np.nan)

    weighted = graph.ecount() > 0
    pagerank = np.array(graph.pagerank(weights='weight' if weighted else None, damping=0.85))
    raw_betweenness = np.array(graph.betweenness(weights='distance' if weighted else None, directed=False))
    betweenness = raw_betweenness / (((n - 1) * (n - 2)) / 2)
    strength = np.array(graph.strength(weights='weight' if weighted else None))
    degree = np.array(graph.degree())
    participation, z = participation_and_z(graph, membership)

    hcps = cohort.hcps
    frame = pd.DataFrame({
        'publications': publications,
        'recent_publications': recent,
        'fractional_publications': fractional_publications,
        'senior_rate': shrunk_rate(senior, publications, pseudo_papers),
        'recent_share': shrunk_rate(recent, publications, pseudo_papers),
        'mean_citation_pct': mean_citation,
        'pagerank': pagerank,
        'betweenness': betweenness,
        'strength': strength,
        'degree': degree,
        'participation': participation,
        'within_module_z': z,
        'community': membership,
        'log_consulting_usd': np.log1p(hcps.consulting_usd_2024.fillna(0).to_numpy()),
        'log_cart_product_usd': np.log1p(hcps.cart_product_mentioned_usd_2024.fillna(0).to_numpy()),
    }, index=pd.Index(cohort.nodes, name='hcp_id'))
    frame['pagerank_within_community'] = within_percentile(frame.pagerank, frame.community)
    frame['hub_role'] = [hub_role(p, zz) for p, zz in zip(participation, z)]
    return frame
