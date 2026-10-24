"""Coauthorship graphs rebuilt from authorships so papers can be resampled or time-sliced.

The published coauthor_edges.csv is the fractional-weight graph over all included
papers. Rebuilding from authorships reproduces it exactly and lets experiments
reweight edges, drop years, or bootstrap papers without touching upstream sources.
"""
from __future__ import annotations

import itertools

import igraph as ig
import numpy as np
import pandas as pd

from .data import Cohort

WEIGHTINGS = ('fractional', 'binary', 'count', 'fractional_min2')
WEIGHTING_NOTES = {
    'fractional': 'sum over shared papers of 1/(author_count - 1); the published weighting',
    'binary': 'one per coauthor pair regardless of how many papers',
    'count': 'number of shared papers',
    'fractional_min2': 'fractional weight, keeping only pairs with at least two shared papers',
}


class CoauthorIncidence:
    """One row per (HCP pair, shared paper), precomputed once and reweighted per experiment."""

    def __init__(self, cohort: Cohort):
        self.nodes = cohort.nodes
        self.n = len(self.nodes)
        self.pmids = list(cohort.papers.index)
        node_index = {node: i for i, node in enumerate(self.nodes)}
        paper_index = {pmid: i for i, pmid in enumerate(self.pmids)}
        a, b, p = [], [], []
        for pmid, group in cohort.links.groupby('pmid', sort=True):
            members = sorted(set(group.hcp_id))
            for x, y in itertools.combinations(members, 2):
                a.append(node_index[x])
                b.append(node_index[y])
                p.append(paper_index[pmid])
        self.a, self.b, self.p = np.array(a), np.array(b), np.array(p)
        author_count = cohort.papers.author_count.to_numpy()
        self.fraction = 1.0 / np.maximum(author_count[self.p] - 1, 1)
        self.paper_year = cohort.papers.publication_year.to_numpy()
        self.pair = self.a * self.n + self.b
        self.link_node = cohort.links.hcp_id.map(node_index).to_numpy()
        self.link_paper = cohort.links.pmid.map(paper_index).to_numpy()

    def paper_mask(self, min_year: int | None = None, max_year: int | None = None) -> np.ndarray:
        mask = np.ones(len(self.pmids), dtype=float)
        if min_year is not None:
            mask[self.paper_year < min_year] = 0
        if max_year is not None:
            mask[self.paper_year > max_year] = 0
        return mask

    def edges(self, multiplicity: np.ndarray | None = None, weighting: str = 'fractional') -> pd.DataFrame:
        if weighting not in WEIGHTINGS:
            raise ValueError(f'Unknown weighting {weighting!r}; choose from {WEIGHTINGS}.')
        m = np.ones(len(self.pmids)) if multiplicity is None else np.asarray(multiplicity, dtype=float)
        rows = m[self.p]
        keep = rows > 0
        pairs, inverse = np.unique(self.pair[keep], return_inverse=True)
        fractional = np.bincount(inverse, weights=rows[keep] * self.fraction[keep])
        shared = np.bincount(inverse, weights=rows[keep])
        distinct = np.bincount(inverse)
        frame = pd.DataFrame({'a': pairs // self.n, 'b': pairs % self.n, 'shared': shared,
                              'distinct_papers': distinct, 'fractional': fractional})
        if weighting == 'fractional':
            frame['weight'] = frame.fractional
        elif weighting == 'count':
            frame['weight'] = frame.shared
        elif weighting == 'binary':
            frame['weight'] = 1.0
        else:
            frame = frame[frame.distinct_papers >= 2].copy()
            frame['weight'] = frame.fractional
        return frame.reset_index(drop=True)

    def graph(self, multiplicity: np.ndarray | None = None, weighting: str = 'fractional') -> ig.Graph:
        edges = self.edges(multiplicity, weighting)
        graph = ig.Graph(n=self.n, edges=list(zip(edges.a, edges.b)), directed=False)
        graph.vs['name'] = self.nodes
        graph.es['weight'] = edges.weight.to_list()
        graph.es['distance'] = (1.0 / edges.weight).to_list()
        return graph
