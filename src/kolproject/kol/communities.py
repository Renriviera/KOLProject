"""Leiden communities, seed ensembles, consensus membership, and Guimerà–Amaral hub roles."""
from __future__ import annotations

import itertools
import random

import igraph as ig
import leidenalg as la
import numpy as np
from sklearn.metrics import adjusted_mutual_info_score


def canonical(membership) -> np.ndarray:
    """Relabel communities 1..k by descending size, ties by smallest member index."""
    membership = np.asarray(membership)
    groups = {}
    for node, label in enumerate(membership):
        groups.setdefault(label, []).append(node)
    order = sorted(groups.values(), key=lambda g: (-len(g), g[0]))
    out = np.empty(len(membership), dtype=int)
    for new, members in enumerate(order, start=1):
        out[members] = new
    return out


def leiden(graph: ig.Graph, resolution: float = 1.0, seed: int = 0) -> np.ndarray:
    weights = 'weight' if graph.ecount() else None
    partition = la.find_partition(graph, la.RBConfigurationVertexPartition, weights=weights,
                                  resolution_parameter=resolution, seed=int(seed), n_iterations=-1)
    return canonical(partition.membership)


def louvain(graph: ig.Graph, resolution: float = 1.0, seed: int = 0) -> np.ndarray:
    random.seed(int(seed))
    ig.set_random_number_generator(random)
    return canonical(graph.community_multilevel(weights='weight', resolution=resolution).membership)


def ami(a, b) -> float:
    return float(adjusted_mutual_info_score(a, b))


def pairwise_ami(partitions) -> np.ndarray:
    n = len(partitions)
    matrix = np.eye(n)
    for i, j in itertools.combinations(range(n), 2):
        matrix[i, j] = matrix[j, i] = ami(partitions[i], partitions[j])
    return matrix


def ensemble(graph: ig.Graph, resolution: float, seeds) -> list[np.ndarray]:
    return [leiden(graph, resolution, seed) for seed in seeds]


def medoid(partitions) -> tuple[np.ndarray, np.ndarray]:
    """The partition that agrees most with the rest of its ensemble, plus the AMI matrix."""
    if len(partitions) == 1:
        return partitions[0], np.ones((1, 1))
    matrix = pairwise_ami(partitions)
    best = int(np.argmax(matrix.sum(axis=1)))
    return partitions[best], matrix


def coassignment(partitions) -> np.ndarray:
    stack = np.vstack(partitions)
    n = stack.shape[1]
    total = np.zeros((n, n))
    for row in stack:
        total += row[:, None] == row[None, :]
    return total / len(stack)


def node_consensus(co: np.ndarray, membership: np.ndarray) -> np.ndarray:
    """Mean share of runs in which a node sits with the other members of its consensus community."""
    out = np.ones(len(membership))
    for label in np.unique(membership):
        members = np.flatnonzero(membership == label)
        if len(members) < 2:
            continue
        block = co[np.ix_(members, members)]
        out[members] = (block.sum(axis=1) - 1) / (len(members) - 1)
    return out


def best_jaccard(members: set, partition: np.ndarray, universe=None) -> float:
    """Clusterwise stability: best Jaccard match of a community within another partition."""
    if not members:
        return float('nan')
    labels = partition if universe is None else partition[universe]
    index = np.arange(len(partition)) if universe is None else np.asarray(universe)
    best = 0.0
    for label in np.unique(labels):
        other = set(index[labels == label])
        best = max(best, len(members & other) / len(members | other))
    return best


def participation_and_z(graph: ig.Graph, membership: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Participation coefficient and within-module strength z-score (Guimerà & Amaral 2005)."""
    n = graph.vcount()
    k_total = np.zeros(n)
    k_by_module = {}
    for edge, weight in zip(graph.es, graph.es['weight'] if graph.ecount() else []):
        u, v = edge.tuple
        k_total[u] += weight
        k_total[v] += weight
        k_by_module[u, membership[v]] = k_by_module.get((u, membership[v]), 0) + weight
        k_by_module[v, membership[u]] = k_by_module.get((v, membership[u]), 0) + weight
    squares = np.zeros(n)
    within = np.zeros(n)
    for (node, module), value in k_by_module.items():
        squares[node] += value ** 2
        if module == membership[node]:
            within[node] = value
    participation = np.where(k_total > 0, 1 - squares / np.where(k_total > 0, k_total, 1) ** 2, 0.0)
    z = np.zeros(n)
    for label in np.unique(membership):
        members = membership == label
        values = within[members]
        sd = values.std()
        z[members] = (values - values.mean()) / sd if sd > 0 else 0.0
    return participation, z


def hub_role(participation: float, z: float, hub_z: float = 2.5) -> str:
    """Role taxonomy from Guimerà & Amaral, with their published thresholds."""
    if z >= hub_z:
        if participation <= 0.30:
            return 'provincial hub'
        return 'connector hub' if participation <= 0.75 else 'kinless hub'
    if participation <= 0.05:
        return 'ultra-peripheral'
    if participation <= 0.62:
        return 'peripheral'
    return 'connector' if participation <= 0.80 else 'kinless'


def disconnected_communities(graph: ig.Graph, membership: np.ndarray) -> int:
    """Count communities whose induced subgraph is not connected (Leiden guarantees zero)."""
    count = 0
    for label in np.unique(membership):
        members = np.flatnonzero(membership == label).tolist()
        if len(members) > 1 and not graph.induced_subgraph(members).is_connected():
            count += 1
    return count


def align(run: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Relabel a partition with reference labels: each run community takes its members' majority label."""
    out = np.empty_like(reference)
    for label in np.unique(run):
        members = run == label
        values, counts = np.unique(reference[members], return_counts=True)
        out[members] = values[np.argmax(counts)]
    return out


def membership_shares(runs, reference: np.ndarray) -> np.ndarray:
    """Share of runs in which each node sits in each reference community (rows sum to one)."""
    k = int(reference.max())
    counts = np.zeros((len(reference), k))
    rows = np.arange(len(reference))
    for run in runs:
        counts[rows, align(np.asarray(run), reference) - 1] += 1
    return counts / len(runs)
