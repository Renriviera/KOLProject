"""Ten experiments that test whether communities, leaders, and feature-importance claims are robust.

Every experiment returns a dict of output tables plus a small summary dict. The
runner writes the tables to the run directory and the summaries to report.md.
There is no ground-truth KOL label, so rigor comes from stability under
perturbation, agreement across methods, and weak external labels.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr
from sklearn.linear_model import LinearRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from .communities import (best_jaccard, coassignment, disconnected_communities, ensemble, leiden,
                          louvain, medoid, node_consensus, pairwise_ami, ami)
from .data import Cohort
from .features import compute_features
from .graph import WEIGHTINGS, WEIGHTING_NOTES, CoauthorIncidence
from .scores import axis_table, composite, overlap, percentile, rank_of, renormalize, top_set

ALL_EXPERIMENTS = ('stability', 'weighting', 'bootstrap', 'node_removal', 'leave_one_axis_out',
                   'weight_perturbation', 'collinearity', 'face_validity', 'temporal', 'interpretability')


@dataclass
class Analysis:
    graph: object
    membership: np.ndarray
    features: pd.DataFrame
    axes: pd.DataFrame
    scores: dict  # composite name -> DataFrame(score, rank, contrib_*)


class Context:
    """Shared inputs plus the primary (fractional, full-period) analysis every experiment compares to."""

    def __init__(self, cohort: Cohort, cfg: dict):
        self.cohort = cohort
        self.cfg = cfg
        self.incidence = CoauthorIncidence(cohort)
        self.seed = int(cfg['seed'])
        self.composites = {name: dict(w) for name, w in cfg['composites'].items()}
        self.axes_def = {name: list(v) for name, v in cfg['axes'].items()}
        self.resolution = float(cfg['community']['resolution'])
        seeds = self.seeds(int(cfg['community']['seeds']))
        graph = self.incidence.graph()
        self.ensemble = ensemble(graph, self.resolution, seeds)
        membership, self.ensemble_ami = medoid(self.ensemble)
        self.primary = self.analyze(membership=membership)
        self.coassignment = coassignment(self.ensemble)
        self.node_consensus = pd.Series(node_consensus(self.coassignment, membership), index=cohort.nodes)
        self.cache = {}

    def seeds(self, count: int, offset: int = 0) -> list[int]:
        return [self.seed + offset + i for i in range(count)]

    def analyze(self, multiplicity=None, weighting='fractional', membership=None, n_seeds=None,
                seed_offset=0, recent_from=None, composites=None, axes=None) -> Analysis:
        graph = self.incidence.graph(multiplicity, weighting)
        if membership is None:
            n_seeds = n_seeds or int(self.cfg['community']['small_seeds'])
            parts = ensemble(graph, self.resolution, self.seeds(n_seeds, seed_offset))
            membership, _ = medoid(parts)
        features = compute_features(self.cohort, self.incidence, graph, membership, multiplicity,
                                    recent_from or int(self.cfg['features']['recent_from']),
                                    float(self.cfg['features']['shrinkage_pseudo_papers']))
        axes_frame = axis_table(features, axes or self.axes_def)
        scores = {name: composite(axes_frame, w) for name, w in (composites or self.composites).items()}
        return Analysis(graph, membership, features, axes_frame, scores)

    def communities(self, membership=None) -> dict[int, set]:
        membership = self.primary.membership if membership is None else membership
        return {int(c): set(np.flatnonzero(membership == c)) for c in np.unique(membership)}


def _spearman(a, b) -> float:
    return float(spearmanr(a, b, nan_policy='omit').statistic)


def _fast_spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman for complete data; used inside bootstrap loops."""
    return float(np.corrcoef(rankdata(a), rankdata(b))[0, 1])


def _fast_auc(y: np.ndarray, score: np.ndarray) -> float:
    """Mann-Whitney AUC with average ranks for ties."""
    ranks = rankdata(score)
    n1 = y.sum()
    n0 = len(y) - n1
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# 1 ─────────────────────────────────────────────────────────────────────────────
def stability(ctx: Context) -> tuple[dict, dict]:
    """Seed and resolution stability of Leiden versus Louvain and the published greedy partition."""
    cfg = ctx.cfg['community']
    graph = ctx.primary.graph
    n_seeds = int(cfg['seeds'])
    sweep = []
    for resolution in cfg['resolution_sweep']:
        parts = ensemble(graph, float(resolution), ctx.seeds(n_seeds))
        matrix = pairwise_ami(parts)
        upper = matrix[np.triu_indices(len(parts), 1)]
        best, _ = medoid(parts)
        sizes = np.bincount(best)[1:]
        sweep.append({'resolution': resolution, 'communities_median': float(np.median([p.max() for p in parts])),
                      'modularity_medoid': graph.modularity(best - 1, weights='weight'),
                      'seed_ami_mean': upper.mean(), 'seed_ami_min': upper.min(),
                      'largest': int(sizes.max()), 'smallest_nontrivial': int(sizes[sizes >= 3].min()),
                      'communities_ge_5': int((sizes >= 5).sum())})
    sweep = pd.DataFrame(sweep)

    louvain_parts = [louvain(graph, ctx.resolution, s) for s in ctx.seeds(n_seeds)]
    greedy = ctx.cohort.hcps.community_id.to_numpy()
    leiden_upper = ctx.ensemble_ami[np.triu_indices(len(ctx.ensemble), 1)]
    louvain_ami = pairwise_ami(louvain_parts)[np.triu_indices(n_seeds, 1)]
    methods = pd.DataFrame([
        {'method': 'leiden (RB configuration)', 'seeds': n_seeds, 'seed_to_seed_ami_mean': leiden_upper.mean(),
         'seed_to_seed_ami_min': leiden_upper.min(),
         'ami_vs_leiden_medoid': 1.0, 'modularity': graph.modularity(ctx.primary.membership - 1, weights='weight'),
         'communities': int(ctx.primary.membership.max()),
         'disconnected_communities': disconnected_communities(graph, ctx.primary.membership)},
        {'method': 'louvain (igraph multilevel)', 'seeds': n_seeds, 'seed_to_seed_ami_mean': louvain_ami.mean(),
         'seed_to_seed_ami_min': louvain_ami.min(),
         'ami_vs_leiden_medoid': float(np.mean([ami(p, ctx.primary.membership) for p in louvain_parts])),
         'modularity': float(np.mean([graph.modularity(p - 1, weights='weight') for p in louvain_parts])),
         'communities': float(np.median([p.max() for p in louvain_parts])),
         'disconnected_communities': int(sum(disconnected_communities(graph, p) for p in louvain_parts))},
        {'method': 'greedy modularity (published community_id)', 'seeds': 1, 'seed_to_seed_ami_mean': np.nan,
         'seed_to_seed_ami_min': np.nan, 'ami_vs_leiden_medoid': ami(greedy, ctx.primary.membership),
         'modularity': graph.modularity(greedy - 1, weights='weight'), 'communities': int(greedy.max()),
         'disconnected_communities': disconnected_communities(graph, greedy)},
    ])

    threshold = float(cfg['stability_threshold'])
    rows = []
    for label, members in ctx.communities().items():
        consensus = ctx.node_consensus.iloc[list(members)]
        rows.append({'community': label, 'size': len(members), 'mean_node_consensus': consensus.mean(),
                     'share_nodes_below_threshold': float((consensus < threshold).mean()),
                     'seed_stable': bool(consensus.mean() >= threshold)})
    table = pd.DataFrame(rows)
    summary = {
        'primary_resolution': ctx.resolution, 'primary_communities': int(ctx.primary.membership.max()),
        'leiden_seed_ami_mean': round(float(leiden_upper.mean()), 3),
        'louvain_seed_ami_mean': round(float(louvain_ami.mean()), 3),
        'greedy_ami_vs_leiden': round(float(methods.ami_vs_leiden_medoid.iloc[2]), 3),
        'seed_stable_communities': int(table.seed_stable.sum()),
        'nodes_below_consensus_threshold': int((ctx.node_consensus < threshold).sum()),
    }
    return {'resolution_sweep': sweep, 'method_comparison': methods, 'community_seed_stability': table}, summary


# 2 ─────────────────────────────────────────────────────────────────────────────
def weighting(ctx: Context) -> tuple[dict, dict]:
    """Recompute everything under four edge weightings and compare to the fractional primary."""
    top_n = int(ctx.cfg['top_n_overlap'])
    base = ctx.primary
    rows, membership_in_top = [], {name: pd.DataFrame(index=ctx.cohort.nodes) for name in ctx.composites}
    for weighting_name in WEIGHTINGS:
        result = base if weighting_name == 'fractional' else ctx.analyze(weighting=weighting_name)
        row = {'weighting': weighting_name, 'definition': WEIGHTING_NOTES[weighting_name],
               'edges': result.graph.ecount(), 'communities': int(result.membership.max()),
               'ami_vs_fractional': ami(result.membership, base.membership)}
        for feature in ['pagerank', 'betweenness', 'participation', 'strength']:
            row[f'spearman_{feature}'] = _spearman(result.features[feature], base.features[feature])
        for name in ctx.composites:
            row[f'top{top_n}_overlap_{name}'] = overlap(result.scores[name].score, base.scores[name].score, top_n)
            membership_in_top[name][weighting_name] = result.scores[name]['rank'] <= top_n
        rows.append(row)
    table = pd.DataFrame(rows)
    robust = pd.DataFrame({f'top{top_n}_all_weightings_{name}': frame.all(axis=1)
                           for name, frame in membership_in_top.items()})
    robust['display_name'] = ctx.cohort.hcps.display_name
    robust.index.name = 'hcp_id'
    summary = {f'robust_top{top_n}_{name}': int(robust[f'top{top_n}_all_weightings_{name}'].sum())
               for name in ctx.composites}
    summary['min_ami_vs_fractional'] = round(float(table.ami_vs_fractional.min()), 3)
    summary['least_similar_weighting'] = table.sort_values('ami_vs_fractional').weighting.iloc[0]
    return {'weighting_comparison': table, 'weighting_robust_leaders': robust}, summary


# 3 ─────────────────────────────────────────────────────────────────────────────
def _verdict(size: int, value: float, min_size: int) -> str:
    """Hennig (2007) clusterwise-stability bands for mean best-match Jaccard."""
    if size < min_size:
        return 'too small to assess'
    if value >= 0.75:
        return 'stable'
    if value >= 0.6:
        return 'pattern, not firm'
    return 'weak' if value >= 0.5 else 'dissolves'


def bootstrap(ctx: Context) -> tuple[dict, dict]:
    """Perturb the literature, rebuild graph, communities, features, and scores, and measure what survives.

    Two perturbations: a standard paper bootstrap (resample with replacement,
    about 37% of papers absent per replicate) and a gentler drop of a fixed share
    of papers, which asks what happens if the corpus missed part of the literature.
    Rank intervals come from the standard bootstrap.
    """
    cfg = ctx.cfg['bootstrap']
    replicates = int(cfg['replicates'])
    leiden_seeds = int(cfg['leiden_seeds'])
    drop = float(cfg['drop_fraction'])
    min_size = int(ctx.cfg['community']['min_named_size'])
    top_k = int(ctx.cfg['top_k'])
    rng = np.random.default_rng(ctx.seed)
    n_papers = len(ctx.incidence.pmids)
    communities = ctx.communities()
    ranks = {name: np.zeros((len(ctx.cohort.nodes), replicates), dtype=int) for name in ctx.composites}
    drop_label = f'drop{round(100 * drop)}'
    jaccard = {mode: {label: [] for label in communities} for mode in ('resample', drop_label)}
    for b in range(replicates):
        for mode in ('resample', drop_label):
            if mode == 'resample':
                multiplicity = np.bincount(rng.integers(0, n_papers, n_papers), minlength=n_papers)
            else:
                multiplicity = (rng.random(n_papers) >= drop).astype(float)
            offset = 10_000 + 100 * b + (0 if mode == 'resample' else 50)
            result = ctx.analyze(multiplicity=multiplicity, n_seeds=leiden_seeds, seed_offset=offset)
            for label, members in communities.items():
                jaccard[mode][label].append(best_jaccard(members, result.membership))
            if mode == 'resample':
                for name in ctx.composites:
                    ranks[name][:, b] = result.scores[name]['rank'].to_numpy()
    tables = {}
    rows = []
    for name, matrix in ranks.items():
        frame = pd.DataFrame({
            'display_name': ctx.cohort.hcps.display_name,
            'primary_rank': ctx.primary.scores[name]['rank'],
            'bootstrap_rank_median': np.median(matrix, axis=1),
            'bootstrap_rank_p2_5': np.percentile(matrix, 2.5, axis=1),
            'bootstrap_rank_p97_5': np.percentile(matrix, 97.5, axis=1),
            f'p_top{top_k}': (matrix <= top_k).mean(axis=1),
        }, index=pd.Index(ctx.cohort.nodes, name='hcp_id')).sort_values('primary_rank')
        tables[f'bootstrap_ranks_{name}'] = frame
        head = frame.head(top_k)
        rows.append({'composite': name, f'median_ci_width_top{top_k}':
                     float((head.bootstrap_rank_p97_5 - head.bootstrap_rank_p2_5).median()),
                     f'mean_p_top{top_k}_for_primary_top{top_k}': float(head[f'p_top{top_k}'].mean())})
    community_rows = []
    for label, members in communities.items():
        row = {'community': label, 'size': len(members)}
        for mode, values in jaccard.items():
            row[f'jaccard_{mode}_mean'] = float(np.mean(values[label]))
            row[f'jaccard_{mode}_p10'] = float(np.percentile(values[label], 10))
            row[f'verdict_{mode}'] = _verdict(len(members), row[f'jaccard_{mode}_mean'], min_size)
        community_rows.append(row)
    community_table = pd.DataFrame(community_rows)
    tables['bootstrap_communities'] = community_table
    tables['bootstrap_summary'] = pd.DataFrame(rows)
    summary = {'replicates_per_perturbation': replicates, 'drop_fraction': drop}
    for mode in jaccard:
        verdicts = community_table[f'verdict_{mode}']
        summary[f'{mode}_stable_communities'] = int((verdicts == 'stable').sum())
        summary[f'{mode}_dissolving_communities'] = int((verdicts == 'dissolves').sum())
    for row in rows:
        summary[f'{row["composite"]}_median_ci_width_top{top_k}'] = round(row[f'median_ci_width_top{top_k}'], 1)
    return tables, summary


# 4 ─────────────────────────────────────────────────────────────────────────────
def node_removal(ctx: Context) -> tuple[dict, dict]:
    """Remove top PageRank nodes, globally and per community, and see which communities survive.

    A Leiden rerun with fresh seeds and no removal sets the noise floor, so the
    reported effect is the Jaccard drop beyond ordinary seed variation.
    """
    small = int(ctx.cfg['community']['small_seeds'])
    graph = ctx.primary.graph
    pagerank = ctx.primary.features.pagerank.to_numpy()
    communities = ctx.communities()

    def rerun(removed: list[int], offset: int):
        keep = [i for i in range(graph.vcount()) if i not in set(removed)]
        sub = graph.induced_subgraph(keep)
        parts = ensemble(sub, ctx.resolution, ctx.seeds(small, offset))
        best, _ = medoid(parts)
        full = np.zeros(graph.vcount(), dtype=int)
        full[keep] = best
        return keep, full

    _, noise = rerun([], 50_000)
    noise_floor = {label: best_jaccard(members, noise) for label, members in communities.items()}

    global_rows = []
    order = list(np.argsort(-pagerank))
    for k in ctx.cfg['node_removal']['top_k']:
        removed = order[:int(k)]
        keep, after = rerun(removed, 60_000 + int(k))
        values = []
        for label, members in communities.items():
            remaining = members - set(removed)
            if len(remaining) >= 5:
                values.append(best_jaccard(remaining, after, keep))
        global_rows.append({'removed_top_pagerank': int(k),
                            'removed_names': '; '.join(ctx.cohort.hcps.display_name.iloc[removed]),
                            'mean_best_jaccard': float(np.mean(values)),
                            'communities_below_0_5': int(np.sum(np.array(values) < 0.5)),
                            'ami_on_remaining': ami(ctx.primary.membership[keep], after[keep])})

    leader_rows = []
    for label, members in communities.items():
        if len(members) < 5:
            continue
        leader = max(members, key=lambda i: pagerank[i])
        keep, after = rerun([leader], 70_000 + label)
        remaining = members - {leader}
        jac = best_jaccard(remaining, after, keep)
        pieces = pd.Series(after[list(remaining)]).value_counts(normalize=True)
        leader_rows.append({'community': label, 'size': len(members),
                            'removed_leader': ctx.cohort.hcps.display_name.iloc[leader],
                            'noise_floor_jaccard': noise_floor[label], 'jaccard_after_removal': jac,
                            'drop_beyond_noise': noise_floor[label] - jac,
                            'pieces_holding_25pct': int((pieces >= 0.25).sum()),
                            'leader_dependent': bool(noise_floor[label] - jac >= 0.15 or (pieces >= 0.25).sum() >= 2)})
    leaders = pd.DataFrame(leader_rows)
    summary = {'leader_dependent_communities': int(leaders.leader_dependent.sum()),
               'communities_tested': len(leaders),
               'mean_jaccard_after_top10_removed': round(float(
                   next(r['mean_best_jaccard'] for r in global_rows if r['removed_top_pagerank'] == 10)), 3)
               if 10 in [int(k) for k in ctx.cfg['node_removal']['top_k']] else None}
    return {'node_removal_global': pd.DataFrame(global_rows), 'node_removal_leader': leaders}, summary


# 5 ─────────────────────────────────────────────────────────────────────────────
def leave_one_axis_out(ctx: Context) -> tuple[dict, dict]:
    """Drop one axis at a time, renormalize, and measure how much the ranking moves."""
    top_n = int(ctx.cfg['top_n_overlap'])
    axes = ctx.primary.axes
    rows = []
    for name, weights in ctx.composites.items():
        full = ctx.primary.scores[name]
        top = full.nsmallest(top_n, 'rank').index
        for axis, weight in weights.items():
            if weight <= 0:
                continue
            reduced = composite(axes, renormalize(weights, drop=[axis]))
            rows.append({'composite': name, 'axis': axis, 'weight': weight,
                         'spearman_vs_full': _spearman(reduced.score, full.score),
                         f'top{top_n}_overlap': overlap(reduced.score, full.score, top_n),
                         f'mean_abs_rank_shift_top{top_n}': float((reduced['rank'][top] - full['rank'][top]).abs().mean()),
                         'marginal_spearman_axis_vs_score': _spearman(axes[axis], full.score)})
    table = pd.DataFrame(rows)
    table['importance_rank'] = table.groupby('composite')[f'mean_abs_rank_shift_top{top_n}'].rank(ascending=False)
    summary = {f'most_important_axis_{name}': group.sort_values(f'mean_abs_rank_shift_top{top_n}').axis.iloc[-1]
               for name, group in table.groupby('composite')}
    return {'axis_importance': table.sort_values(['composite', 'importance_rank'])}, summary


# 6 ─────────────────────────────────────────────────────────────────────────────
def weight_perturbation(ctx: Context) -> tuple[dict, dict]:
    """Sample weights near each composite (Dirichlet) and uniformly over its axes; count top-k stays."""
    cfg = ctx.cfg['perturbation']
    draws = int(cfg['draws'])
    concentration = float(cfg['concentration'])
    top_k = int(ctx.cfg['top_k'])
    rng = np.random.default_rng(ctx.seed + 1)
    axes = ctx.primary.axes
    tables, summary = {}, {}
    n = len(axes)
    for name, weights in ctx.composites.items():
        active = [a for a, w in weights.items() if w > 0]
        base = np.array([weights[a] for a in active])
        matrix = axes[active].to_numpy()
        frame = pd.DataFrame({'display_name': ctx.cohort.hcps.display_name,
                              'primary_rank': ctx.primary.scores[name]['rank']}, index=axes.index)
        for regime, alpha in [('near', concentration * base), ('uninformative', np.ones(len(active)))]:
            sampled = rng.dirichlet(alpha, draws)
            scores = matrix @ sampled.T
            # Ties are vanishingly rare with continuous weights; argsort gives a strict ranking.
            order = np.argsort(-scores, axis=0, kind='stable')
            ranks = np.empty_like(order)
            ranks[order, np.arange(draws)] = np.arange(1, n + 1)[:, None]
            frame[f'p_top{top_k}_{regime}'] = (ranks <= top_k).mean(axis=1)
            frame[f'rank_p5_{regime}'] = np.percentile(ranks, 5, axis=1)
            frame[f'rank_p95_{regime}'] = np.percentile(ranks, 95, axis=1)
            summary[f'{name}_mean_l1_shift_{regime}'] = round(float(np.abs(sampled - base).sum(axis=1).mean()), 3)
        p = frame[f'p_top{top_k}_near']
        frame['robustness_class'] = np.select(
            [p >= 0.9, p >= 0.3, p >= 0.05], ['robust leader', 'weight-sensitive', 'occasional'], 'rarely')
        frame = frame.sort_values('primary_rank')
        tables[f'weight_perturbation_{name}'] = frame
        summary[f'{name}_robust_leaders'] = int((frame.robustness_class == 'robust leader').sum())
        summary[f'{name}_weight_sensitive'] = int((frame.robustness_class == 'weight-sensitive').sum())
        summary[f'{name}_top{top_k}_under_any_weights'] = int((frame[f'p_top{top_k}_uninformative'] >= 0.9).sum())
    return tables, summary


# 7 ─────────────────────────────────────────────────────────────────────────────
VOLUME_FEATURES = ['pagerank', 'strength', 'degree', 'publications']


def collinearity(ctx: Context) -> tuple[dict, dict]:
    """Show which features are the same signal, and whether volume-loading changes the leaders."""
    top_n = int(ctx.cfg['top_n_overlap'])
    features = ctx.primary.features.copy()
    features['industry_total'] = features.log_consulting_usd + features.log_cart_product_usd
    candidates = ['publications', 'recent_publications', 'strength', 'degree', 'pagerank', 'betweenness',
                  'participation', 'within_module_z', 'mean_citation_pct', 'senior_rate', 'recent_share',
                  'log_consulting_usd', 'log_cart_product_usd']
    ranked = pd.DataFrame({c: percentile(features[c]) for c in candidates})
    correlation = ranked.corr(method='spearman').rename_axis('feature')
    vif = []
    for column in candidates:
        others = [c for c in candidates if c != column]
        r2 = LinearRegression().fit(ranked[others], ranked[column]).score(ranked[others], ranked[column])
        vif.append({'feature': column, 'r2_from_others': r2, 'vif': 1 / max(1 - r2, 1e-9)})
    vif = pd.DataFrame(vif).sort_values('vif', ascending=False)

    loaded_axes = dict(ctx.axes_def)
    loaded_axes['prestige'] = VOLUME_FEATURES
    loaded = axis_table(ctx.primary.features, loaded_axes)
    rows = []
    for name, weights in ctx.composites.items():
        base = ctx.primary.scores[name].score
        volume_loaded = composite(loaded, weights).score
        rows.append({'composite': name,
                     'prestige_axis_primary': '+'.join(ctx.axes_def['prestige']),
                     'prestige_axis_volume_loaded': '+'.join(VOLUME_FEATURES),
                     'spearman_loaded_vs_primary': _spearman(volume_loaded, base),
                     f'top{top_n}_overlap_loaded_vs_primary': overlap(volume_loaded, base, top_n),
                     'spearman_primary_vs_publication_count': _spearman(base, features.publications),
                     'spearman_loaded_vs_publication_count': _spearman(volume_loaded, features.publications)})
    comparison = pd.DataFrame(rows)
    pairs = [(a, b, correlation.loc[a, b]) for a, b in itertools.combinations(candidates, 2)]
    redundant = sorted([p for p in pairs if abs(p[2]) >= 0.85], key=lambda p: -abs(p[2]))
    summary = {'redundant_pairs_rho_ge_0_85': [f'{a}~{b} ({r:.2f})' for a, b, r in redundant],
               'highest_vif': f'{vif.feature.iloc[0]} ({vif.vif.iloc[0]:.1f})'}
    for row in rows:
        summary[f'{row["composite"]}_top{top_n}_overlap_if_volume_loaded'] = round(row[f'top{top_n}_overlap_loaded_vs_primary'], 2)
    return {'feature_correlation': correlation, 'feature_vif': vif, 'volume_loading': comparison}, summary


# 8 ─────────────────────────────────────────────────────────────────────────────
INDUSTRY_FEATURES = {'log_consulting_usd', 'log_cart_product_usd'}


def _uses_industry(ctx: Context, name: str) -> bool:
    if name in ctx.composites:
        return any(w > 0 and set(ctx.axes_def[a]) & INDUSTRY_FEATURES for a, w in ctx.composites[name].items())
    return name in INDUSTRY_FEATURES


def _label_metrics(y: np.ndarray, score: np.ndarray, rng, n_boot: int) -> dict:
    order = np.argsort(-score, kind='stable')
    out = {'auc': roc_auc_score(y, score), 'average_precision': average_precision_score(y, score),
           'precision_at_25': y[order[:25]].mean(), 'precision_at_50': y[order[:50]].mean(),
           'recall_at_50': y[order[:50]].sum() / y.sum()}
    out['lift_at_50'] = out['precision_at_50'] / y.mean()
    positives, negatives = np.flatnonzero(y), np.flatnonzero(~y)
    aucs = []
    for _ in range(n_boot):
        sample = np.concatenate([rng.choice(positives, len(positives)), rng.choice(negatives, len(negatives))])
        aucs.append(_fast_auc(y[sample], score[sample]))
    out['auc_ci_low'], out['auc_ci_high'] = np.percentile(aucs, [2.5, 97.5])
    return out


def score_panel(ctx: Context, analysis: Analysis, extra: dict | None = None) -> dict[str, pd.Series]:
    """Composites, an equal-weight consensus, the frozen baseline, and single features, as comparable scores."""
    scores = {name: frame.score for name, frame in analysis.scores.items()}
    equal = {axis: 1 / len(analysis.axes.columns) for axis in analysis.axes.columns}
    scores['equal_weight_consensus'] = composite(analysis.axes, equal).score
    for feature in ['publications', 'pagerank', 'betweenness', 'participation', 'mean_citation_pct',
                    'senior_rate', 'recent_share', 'log_consulting_usd']:
        scores[feature] = percentile(analysis.features[feature])
    scores.update(extra or {})
    return scores


def face_validity(ctx: Context) -> tuple[dict, dict]:
    """Agreement with weak external labels. A sanity check, never validation of influence."""
    rng = np.random.default_rng(ctx.seed + 2)
    n_boot = int(ctx.cfg['face_validity']['auc_bootstrap'])
    baseline = {'baseline_exploratory_score (published)': ctx.cohort.hcps.exploratory_score}
    scores = score_panel(ctx, ctx.primary, baseline)
    rows = []
    for label in ctx.cohort.labels.columns:
        y = ctx.cohort.labels[label].to_numpy()
        for name, score in scores.items():
            metrics = _label_metrics(y, score.reindex(ctx.cohort.nodes).fillna(-np.inf).to_numpy(), rng, n_boot)
            rows.append({'label': label, 'positives': int(y.sum()), 'prevalence': y.mean(), 'score': name,
                         'circular': label == 'cart_industry_advisor' and
                                     (_uses_industry(ctx, name) or name == 'equal_weight_consensus'),
                         **metrics})
    table = pd.DataFrame(rows).sort_values(['label', 'auc'], ascending=[True, False])
    summary = {}
    for label, group in table[~table.circular].groupby('label'):
        best = group.iloc[0]
        volume = group[group.score == 'publications'].iloc[0]
        summary[f'{label}_best'] = f'{best.score} AUC {best.auc:.2f} [{best.auc_ci_low:.2f}, {best.auc_ci_high:.2f}]'
        summary[f'{label}_publication_count_auc'] = round(float(volume.auc), 2)
    return {'face_validity': table}, summary


# 9 ─────────────────────────────────────────────────────────────────────────────
def temporal(ctx: Context) -> tuple[dict, dict]:
    """Score from papers through the cutoff year only; test against what happened afterward.

    Industry is dropped from every composite (2024 payments postdate the window).
    Citation percentiles were observed in 2026, so a no-impact variant shows how
    much of any signal could come from citation look-ahead.
    """
    cfg = ctx.cfg['temporal']
    cutoff = int(cfg['cutoff_year'])
    rng = np.random.default_rng(ctx.seed + 3)
    inc = ctx.incidence
    pre = inc.paper_mask(max_year=cutoff)
    post = inc.paper_mask(min_year=cutoff + 1)
    variants = {}
    for name, weights in ctx.composites.items():
        variants[f'{name} (no industry)'] = renormalize(weights, drop=['industry'])
        variants[f'{name} (no industry, no impact)'] = renormalize(weights, drop=['industry', 'impact'])
    analysis = ctx.analyze(multiplicity=pre, recent_from=cutoff - 2, composites=variants)
    scores = {name: frame.score for name, frame in analysis.scores.items()}
    scores['publications (volume-only null)'] = percentile(analysis.features.publications)
    scores['pagerank alone'] = percentile(analysis.features.pagerank)
    scores['mean_citation_pct alone'] = percentile(analysis.features.mean_citation_pct)

    nodes = ctx.cohort.nodes
    pre_edges = inc.edges(pre)
    post_edges = inc.edges(post)
    known = set(zip(pre_edges.a, pre_edges.b))
    new = post_edges[[pair not in known for pair in zip(post_edges.a, post_edges.b)]]
    new_coauthors = np.bincount(np.concatenate([new.a, new.b]), minlength=len(nodes))
    future_pubs = np.bincount(inc.link_node, weights=post[inc.link_paper], minlength=len(nodes))
    targets = {
        f'publications_{cutoff + 1}_2025': future_pubs,
        f'new_coauthors_{cutoff + 1}_2025': new_coauthors,
        'log_consulting_usd_2024': ctx.primary.features.log_consulting_usd.to_numpy(),
    }
    panel = ctx.cohort.labels.guideline_panel.to_numpy()
    n_boot = int(cfg['difference_bootstrap'])
    null = scores['publications (volume-only null)'].to_numpy()
    rows = []
    for name, series in scores.items():
        s = series.to_numpy()
        row = {'score': name}
        for target, values in targets.items():
            rho = _spearman(s, values)
            diffs = []
            for _ in range(n_boot):
                idx = rng.integers(0, len(s), len(s))
                diffs.append(_fast_spearman(s[idx], values[idx]) - _fast_spearman(null[idx], values[idx]))
            row[f'spearman_{target}'] = rho
            row[f'delta_vs_volume_{target}'] = rho - _spearman(null, values)
            row[f'delta_ci_low_{target}'], row[f'delta_ci_high_{target}'] = np.percentile(diffs, [2.5, 97.5])
        metrics = _label_metrics(panel, s, rng, n_boot)
        row.update({'guideline_panel_auc': metrics['auc'], 'guideline_panel_auc_ci_low': metrics['auc_ci_low'],
                    'guideline_panel_auc_ci_high': metrics['auc_ci_high'],
                    'guideline_panel_precision_at_25': metrics['precision_at_25']})
        rows.append(row)
    table = pd.DataFrame(rows)
    zero_pre = int((analysis.features.publications == 0).sum())
    summary = {'cutoff_year': cutoff, 'hcps_without_pre_period_papers': zero_pre,
               'pre_period_edges': int(len(pre_edges)), 'post_period_new_edges': int(len(new))}
    for target in targets:
        column = f'delta_vs_volume_{target}'
        beats = table[(table[f'delta_ci_low_{target}'] > 0) & ~table.score.str.startswith('publications')]
        summary[f'scores_beating_volume_on_{target}'] = beats.score.tolist()
        best = table.sort_values(f'spearman_{target}', ascending=False).iloc[0]
        summary[f'best_on_{target}'] = f'{best.score} (rho {best[f"spearman_{target}"]:.2f}, delta {best[column]:+.2f})'
    best = table.sort_values('guideline_panel_auc', ascending=False).iloc[0]
    summary['best_on_guideline_panel'] = f'{best.score} AUC {best.guideline_panel_auc:.2f}'
    summary['volume_null_guideline_panel_auc'] = round(float(
        table.loc[table.score == 'publications (volume-only null)', 'guideline_panel_auc'].iloc[0]), 2)
    return {'temporal_holdout': table}, summary


# 10 ────────────────────────────────────────────────────────────────────────────
def interpretability(ctx: Context) -> tuple[dict, dict]:
    """Describe each community in facts a brand team can read, and flag the ones safe to name."""
    hcps = ctx.cohort.hcps
    features = ctx.primary.features
    links = ctx.cohort.links.merge(ctx.cohort.papers[['journal']], left_on='pmid', right_index=True)
    payments = ctx.cohort.payments
    boot = ctx.cache.get('bootstrap')
    threshold = float(ctx.cfg['community']['stability_threshold'])
    rows = []
    for label, members in ctx.communities().items():
        ids = [ctx.cohort.nodes[i] for i in sorted(members)]
        group = hcps.loc[ids]
        inst = group.institution.value_counts()
        states = group.npi_state.value_counts()
        member_links = links[links.hcp_id.isin(ids)]
        member_pay = payments[payments.hcp_id.isin(ids)]
        manufacturers = member_pay.groupby('manufacturer').amount_usd.sum().sort_values(ascending=False)
        f = features.loc[ids]
        leaders = f.sort_values('pagerank', ascending=False).head(3).index
        connectors = f[f.hub_role.isin(['connector hub', 'kinless hub', 'connector'])
                       ].sort_values('betweenness', ascending=False).head(3).index
        consensus = ctx.node_consensus.loc[ids].mean()
        drop_column = next((c for c in boot.columns if c.startswith('jaccard_drop') and c.endswith('_mean')), None) \
            if boot is not None else None
        boot_j = float(boot.set_index('community').loc[label, drop_column]) if drop_column else np.nan
        resample_j = float(boot.set_index('community').loc[label, 'jaccard_resample_mean']) if drop_column else np.nan
        inst_purity = inst.iloc[0] / len(ids)
        state_purity = states.iloc[0] / len(ids)
        if inst_purity >= 0.4:
            name = f'{inst.index[0]} orbit'
        elif state_purity >= 0.5:
            name = f'{states.index[0]} multi-institution network'
        else:
            name = f'Cross-regional network around {inst.index[0]}'
        if len(ids) < int(ctx.cfg['community']['min_named_size']):
            name = f'Small group ({len(ids)}) at {inst.index[0]}'
        nameable = (len(ids) >= int(ctx.cfg['community']['min_named_size']) and consensus >= threshold
                    and (np.isnan(boot_j) or boot_j >= 0.75))
        description = (
            f'{len(ids)} HCPs; {inst_purity:.0%} at {inst.index[0]}'
            + (f', then {inst.index[1]}' if len(inst) > 1 else '')
            + f'; {state_purity:.0%} registered in {states.index[0]}. '
            f'Median paper year {int(member_links.publication_year.median())}; most common journal '
            f'{member_links.journal.mode().iloc[0] if len(member_links) else "n/a"}. '
            f'Network leaders: {", ".join(hcps.display_name.loc[leaders].str.title())}. '
            + (f'Largest 2024 industry payer to members: {manufacturers.index[0]}.' if len(manufacturers) else '')
        )
        rows.append({'community': label, 'label': name, 'size': len(ids), 'nameable': nameable,
                     'seed_consensus': consensus, 'jaccard_literature_drop': boot_j,
                     'jaccard_full_bootstrap': resample_j,
                     'top_institution': inst.index[0], 'institution_purity': inst_purity,
                     'top_state': states.index[0], 'state_purity': state_purity,
                     'median_paper_year': float(member_links.publication_year.median()),
                     'top_journal': member_links.journal.mode().iloc[0] if len(member_links) else '',
                     'top_manufacturer_usd': manufacturers.index[0] if len(manufacturers) else '',
                     'leaders': '; '.join(hcps.display_name.loc[leaders]),
                     'connectors': '; '.join(hcps.display_name.loc[connectors]),
                     'hub_roles': '; '.join(f'{k}: {v}' for k, v in f.hub_role.value_counts().items()),
                     'description': description})
    table = pd.DataFrame(rows)
    summary = {'communities': len(table), 'nameable_communities': int(table.nameable.sum()),
               'institution_anchored (purity>=0.4)': int((table.institution_purity >= 0.4).sum()),
               'labels': table.loc[table.nameable, 'label'].tolist()}
    return {'community_profiles': table}, summary


EXPERIMENTS = {
    'stability': stability, 'weighting': weighting, 'bootstrap': bootstrap, 'node_removal': node_removal,
    'leave_one_axis_out': leave_one_axis_out, 'weight_perturbation': weight_perturbation,
    'collinearity': collinearity, 'face_validity': face_validity, 'temporal': temporal,
    'interpretability': interpretability,
}
