"""Checks for the KOL community and composite-score harness."""
import shutil

import igraph as ig
import numpy as np
import pandas as pd
import pytest

from kolproject.common import ROOT
from kolproject.identity import HOLDOUT
from kolproject.kol.communities import (canonical, disconnected_communities, hub_role, leiden,
                                        participation_and_z)
from kolproject.kol.data import institution_label, load_cohort
from kolproject.kol.features import compute_features, shrunk_rate
from kolproject.kol.graph import CoauthorIncidence
from kolproject.kol.run import resolve, run, run_id
from kolproject.kol.scores import composite, percentile, renormalize


@pytest.fixture(scope='module')
def cohort():
    return load_cohort(ROOT)


@pytest.fixture(scope='module')
def incidence(cohort):
    return CoauthorIncidence(cohort)


def test_rebuilt_graph_matches_published_edges(cohort, incidence):
    edges = incidence.edges()
    index = {node: i for i, node in enumerate(cohort.nodes)}
    published = cohort.published_edges.assign(a=lambda d: d.hcp_id_a.map(index), b=lambda d: d.hcp_id_b.map(index))
    merged = edges.merge(published, on=['a', 'b'], how='outer', indicator=True, validate='one_to_one')
    assert (merged._merge == 'both').all()
    assert np.allclose(merged.fractional, merged.fractional_weight, rtol=0, atol=1e-12)
    assert (merged.shared == merged.shared_publications).all()


def test_features_reproduce_published_columns(cohort, incidence):
    graph = incidence.graph()
    features = compute_features(cohort, incidence, graph, leiden(graph, 1.0, 0))
    hcps = cohort.hcps
    assert np.allclose(features.pagerank, hcps.pagerank, atol=1e-10)
    assert np.allclose(features.betweenness, hcps.weighted_betweenness, atol=1e-12)
    assert np.allclose(features.strength, hcps.coauthor_fractional_strength, atol=1e-10)
    assert (features.publications == hcps.publications_2017_2025).all()
    assert (features.recent_publications == hcps.recent_publications_2023_2025).all()
    assert np.allclose(features.mean_citation_pct, hcps.mean_citation_year_percentile, atol=1e-12)


def test_guideline_holdout_label_never_enters_the_graph(cohort, incidence):
    assert cohort.labels.guideline_panel.sum() == 16
    assert HOLDOUT not in incidence.pmids
    assert not cohort.links.pmid.eq(HOLDOUT).any()


def test_leiden_is_seeded_canonical_and_connected(incidence):
    graph = incidence.graph()
    first, second = leiden(graph, 1.0, 7), leiden(graph, 1.0, 7)
    assert (first == second).all()
    sizes = np.bincount(first)[1:]
    assert (np.diff(sizes) <= 0).all()
    assert disconnected_communities(graph, first) == 0


def test_participation_and_roles_on_two_triangles():
    graph = ig.Graph(n=6, edges=[(0, 1), (1, 2), (0, 2), (3, 4), (4, 5), (3, 5), (2, 3)])
    graph.es['weight'] = [1.0] * 7
    participation, z = participation_and_z(graph, np.array([1, 1, 1, 2, 2, 2]))
    # Bridge node 2 has strength 3: two edges inside, one outside, so P = 1 - (2/3)^2 - (1/3)^2.
    assert participation[2] == pytest.approx(4 / 9)
    assert participation[0] == pytest.approx(0.0)
    assert z[0] == pytest.approx(0.0)
    assert hub_role(0.0, 0.0) == 'ultra-peripheral'
    assert hub_role(0.5, 3.0) == 'connector hub'
    assert (canonical([5, 5, 9, 9, 9]) == [2, 2, 1, 1, 1]).all()


def test_composite_contributions_sum_and_weights_are_checked():
    axes = pd.DataFrame({'a': [0.1, 0.5, 0.9], 'b': [0.9, 0.5, 0.1]}, index=['x', 'y', 'z'])
    scored = composite(axes, {'a': 0.75, 'b': 0.25})
    contributions = scored.filter(like='contrib_').sum(axis=1)
    assert np.allclose(contributions, scored.score)
    assert scored['rank'].to_dict() == {'z': 1, 'y': 2, 'x': 3}
    with pytest.raises(ValueError):
        composite(axes, {'a': 0.5, 'b': 0.6})
    with pytest.raises(ValueError):
        composite(axes, {'a': 1.2, 'b': -0.2})
    assert renormalize({'a': 0.5, 'b': 0.25, 'c': 0.25}, drop=['c']) == {'a': 2 / 3, 'b': 1 / 3}


def test_helpers_rank_missing_lowest_and_shrink_small_samples():
    assert percentile(pd.Series([np.nan, 1.0, 2.0])).tolist() == pytest.approx([1 / 6, 1 / 2, 5 / 6])
    rates = shrunk_rate(np.array([2.0, 20.0]), np.array([2.0, 40.0]), pseudo=4)
    assert rates[0] < 1.0 and rates[0] > rates[1] - 0.2
    assert institution_label('Lymphoma Service, Memorial Sloan Kettering Cancer Center, New York') == 'Memorial Sloan Kettering'
    assert institution_label('Division of Hematology, Some Regional Hospital, Ohio') == 'Some Regional Hospital'


def test_run_id_tracks_config_not_output_location():
    cfg = resolve({'seed': 1, 'quick': False, 'experiments': 'all', 'output_dir': 'a',
                   'community': {}, 'bootstrap': {}, 'perturbation': {}, 'face_validity': {},
                   'temporal': {}, 'node_removal': {}})
    moved = dict(cfg, output_dir='b')
    assert run_id(cfg, {'x': '1'}) == run_id(moved, {'x': '1'})
    assert run_id(cfg, {'x': '1'}) != run_id(dict(cfg, seed=2), {'x': '1'})


def test_quick_run_writes_scores_for_every_hcp(tmp_path):
    shutil.copytree(ROOT / 'Data' / 'processed', tmp_path / 'Data' / 'processed')
    import yaml
    cfg = yaml.safe_load((ROOT / 'configs' / 'kol.yaml').read_text())
    cfg.pop('hydra'), cfg.pop('defaults')
    cfg.update(quick=True, experiments=['leave_one_axis_out', 'interpretability'])
    out = run(cfg, root=tmp_path)
    scores = pd.read_csv(out / 'kol_scores.csv', dtype={'hcp_id': str, 'npi': str})
    assert len(scores) == 500 and scores.hcp_id.is_unique
    for name in cfg['composites']:
        contributions = scores.filter(like=f'{name}_contrib_').sum(axis=1)
        assert np.allclose(contributions, scores[f'{name}_score'])
        assert sorted(scores[f'{name}_rank']) == list(range(1, 501))
    assert 'label_curated_seed' not in scores  # legacy pilot is local-only and absent here
    assert (out / 'report.md').read_text().count('## ') >= 4
    assert (out / 'community_profiles.csv').exists() and (out / 'bootstrap_communities.csv').exists()
