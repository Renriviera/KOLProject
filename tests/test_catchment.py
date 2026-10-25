"""Catchment layer: proxy edges, pediatric exclusion, observed-edge upgrade, and bounded layout."""
import math
from kolproject.kol.catchment import proxy_edges, observed_edges, is_pediatric, title, layout, spread_positions

REFERRERS = [
    {'hrr': '301', 'state': 'NY'}, {'hrr': '301', 'state': 'NY'}, {'hrr': '302', 'state': 'NY'},
    {'hrr': '1', 'state': 'AL'}, {'hrr': '', 'state': 'WY'}, {'hrr': '400', 'state': 'WY'},
]
BY_HRR = {'301': ['330154', '330101']}
BY_STATE = {'NY': ['330154', '330101'], 'AL': ['010033']}

def test_proxy_splits_each_referrer_across_region_centers_then_falls_back_to_state():
    edges, unassigned = proxy_edges(REFERRERS, BY_HRR, BY_STATE)
    by = {(e['hrr'], e['ccn']): e for e in edges}
    assert by[('301', '330154')]['w'] == 1.0 and by[('301', '330154')]['kind'] == 'same_hrr'
    assert by[('302', '330154')]['w'] == 0.5 and by[('302', '330154')]['kind'] == 'same_state'
    assert by[('1', '010033')]['w'] == 1.0 and by[('1', '010033')]['kind'] == 'same_state'
    assert unassigned == 2  # no HRR, and a state with no center
    assert round(sum(e['w'] for e in edges), 6) == 4.0

def test_pediatric_flag_uses_legal_name_patterns():
    assert is_pediatric("CHILDREN'S HOSPITAL OF PHILADELPHIA")
    assert is_pediatric('ST JUDE CHILDRENS RESEARCH HOSPITAL INC')
    assert not is_pediatric('THE UNIVERSITY OF CHICAGO MEDICAL CENTER')

def test_title_keeps_possessives_readable():
    assert title("COOK CHILDREN'S MEDICAL CENTER") == "Cook Children's Medical Center"
    assert title('UNIVERSITY OF TEXAS M D ANDERSON') == 'University Of Texas M D Anderson'

def test_observed_edges_carry_patient_counts_and_region_relation():
    flows = [{'hrr': '301', 'ccn': '330154', 'patients': '42', 'referrer_pairs': '3', 'same_hrr': '1'},
             {'hrr': '302', 'ccn': '330154', 'patients': '11', 'referrer_pairs': '1', 'same_hrr': '0'}]
    edges = observed_edges(flows)
    assert edges[0]['w'] == 42 and edges[0]['kind'] == 'observed_same_hrr'
    assert edges[1]['kind'] == 'observed_cross_hrr' and edges[1]['pairs'] == 1

def test_layout_keeps_anchors_near_their_centroid_and_everything_inside_the_frame():
    centers = [{'ccn': '330154', 'x': 0.3, 'y': -0.2, 'core': 31}, {'ccn': '330101', 'x': 0.31, 'y': -0.21, 'core': 9},
               {'ccn': '010033', 'x': None, 'y': None, 'core': 0}, {'ccn': '999999', 'x': None, 'y': None, 'core': 0}]
    hrrs = [{'hrr': '301'}, {'hrr': '302'}, {'hrr': '1'}, {'hrr': '400'}]
    edges, _ = proxy_edges(REFERRERS, BY_HRR, BY_STATE)
    pos = layout(centers, hrrs, edges)
    # Anchored centers stay near their coauthorship centroid but are pushed apart enough to be told apart.
    assert math.hypot(pos['c:330154'][0] - 0.3, pos['c:330154'][1] + 0.2) < 0.08
    assert math.hypot(pos['c:330154'][0] - pos['c:330101'][0], pos['c:330154'][1] - pos['c:330101'][1]) >= 0.04
    assert math.hypot(*pos['h:301']) < 1.0  # placed next to its anchored centers
    assert all(math.hypot(x, y) <= 1.35 + 1e-6 for x, y in pos.values())
    assert len(pos) == 8


def test_spread_keeps_direction_and_distance_rank_but_opens_the_middle():
    centers = [{'ccn': '1', 'x': 0.02, 'y': 0.0, 'core': 30}, {'ccn': '2', 'x': 0.03, 'y': 0.01, 'core': 20},
               {'ccn': '3', 'x': 0.5, 'y': 0.0, 'core': 5}, {'ccn': '4', 'x': None, 'y': None, 'core': 0}]
    hrrs = [{'hrr': 'a'}]
    pos = {'c:1': (0.02, 0.0), 'c:2': (0.03, 0.01), 'c:3': (0.5, 0.0), 'c:4': (-0.4, 0.3), 'h:a': (0.04, 0.0)}
    spread = spread_positions(centers, hrrs, pos)
    centroid = (sum(p[0] for p in list(pos.values())[:3]) / 3, sum(p[1] for p in list(pos.values())[:3]) / 3)
    before = {k: math.hypot(v[0] - centroid[0], v[1] - centroid[1]) for k, v in pos.items()}
    after = {k: math.hypot(v[0] - centroid[0], v[1] - centroid[1]) for k, v in spread.items()}
    assert sorted(before, key=before.get)[-1] == sorted(after, key=after.get)[-1]  # farthest node stays farthest
    gap = lambda p, a, b: math.hypot(p[a][0] - p[b][0], p[a][1] - p[b][1])
    assert gap(spread, 'c:1', 'c:2') > gap(pos, 'c:1', 'c:2')  # the crowded pair is pushed apart
    assert gap(spread, 'c:1', 'c:2') < gap(spread, 'c:1', 'c:3')  # but stays the closer neighbor
