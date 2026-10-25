"""Bundle the affiliation and catchment layers into docs/data/catchment.json.

Layer A (coauthorship among the core 500) stays in site.json and anchors this
graph: each CAR-T center is placed at the centroid of its core members on the
coauthorship map and colored by their dominant community. Layer B attaches
affiliates to centers as counts. Layer C connects hospital referral regions
(HRRs) that hold referring physicians to centers, either by the geographic
proxy (same HRR, else same state) or by CareSet DocGraph patient flows when a
`shared_patient_*_hrr_center_flows.csv` table exists in the division snapshot.

Usage: uv run kolproject-catchment [--site docs/data/site.json] [--out docs/data/catchment.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

import networkx as nx

from ..common import ROOT, now, read_csv
from ..divisions_source import SNAPSHOT as DIVISION_SNAPSHOT
from .data import sha256

DIVISIONS = ROOT / 'Data' / 'processed' / DIVISION_SNAPSHOT
# CAR-T regulatory milestones shown as vertical markers on the time-progression charts (month resolution).
MILESTONES = [
    {'date': '2017-08', 'label': 'Kymriah approved', 'detail': 'August 2017: tisagenlecleucel (Kymriah) becomes the first FDA-approved CAR-T therapy (pediatric and young adult B-ALL).'},
    {'date': '2017-10', 'label': 'Yescarta approved', 'detail': 'October 2017: axicabtagene ciloleucel (Yescarta) becomes the second approved CAR-T, for relapsed or refractory large B-cell lymphoma.'},
    {'date': '2018-05', 'label': 'Kymriah: DLBCL', 'detail': 'May 2018: Kymriah wins its second indication, relapsed or refractory DLBCL.'},
    {'date': '2019-06', 'label': 'ASCO 2019', 'detail': 'May 31 to June 4, 2019: ASCO Annual Meeting, Chicago. The 2019 conference season (ASCO, then ASH in December) carried the long-term follow-up data for the first CAR-T approvals.'},
    {'date': '2020-07', 'label': 'Tecartus approved', 'detail': 'July 2020: brexucabtagene autoleucel (Tecartus) becomes the third approved CAR-T, for mantle cell lymphoma.'},
    {'date': '2021-02', 'label': 'Breyanzi approved', 'detail': 'February 2021: lisocabtagene maraleucel (Breyanzi) is approved for relapsed or refractory large B-cell lymphoma.'},
    {'date': '2021-03', 'label': 'Abecma approved', 'detail': 'March 2021: idecabtagene vicleucel (Abecma) is approved for multiple myeloma.'},
    {'date': '2021-10', 'label': 'Tecartus: ALL', 'detail': '2021: Tecartus expands to adult B-cell acute lymphoblastic leukemia.'},
    {'date': '2022-02', 'label': 'Carvykti approved', 'detail': '2022: ciltacabtagene autoleucel (Carvykti) is approved for multiple myeloma.'},
    {'date': '2022-04', 'label': 'Yescarta: 2nd line', 'detail': '2022: Yescarta moves to second-line large B-cell lymphoma.'},
]


def mode_share(values):
    counts = collections.Counter(v for v in values if v is not None)
    if not counts:
        return None, 0.0
    value, n = counts.most_common(1)[0]
    return value, n / sum(counts.values())


PEDIATRIC = ('CHILDREN', 'PEDIATRIC', 'NEMOURS', 'ST JUDE', 'ST. JUDE')


def is_pediatric(*names):
    return any(any(p in (n or '').upper() for p in PEDIATRIC) for n in names)


def title(value):
    return ' '.join(w[:1].upper() + w[1:].lower() for w in str(value).split()).replace("'S ", "'s ").replace("'S", "'s")


def proxy_edges(referrers, centers_by_hrr, centers_by_state):
    """Geographic proxy: each referrer's unit of weight is split across the adult centers in its HRR, else its state."""
    weights = collections.Counter()
    kinds = {}
    unassigned = 0
    for r in referrers:
        targets, kind = centers_by_hrr.get(r['hrr'], []), 'same_hrr'
        if not targets:
            targets, kind = centers_by_state.get(r['state'], []), 'same_state'
        if not targets or not r['hrr']:
            unassigned += 1
            continue
        for ccn in targets:
            weights[(r['hrr'], ccn)] += 1 / len(targets)
            kinds[(r['hrr'], ccn)] = kind
    return [{'hrr': h, 'ccn': c, 'w': round(w, 3), 'kind': kinds[(h, c)]} for (h, c), w in sorted(weights.items())], unassigned


def observed_edges(flows):
    return [{'hrr': f['hrr'], 'ccn': f['ccn'], 'w': int(f['patients']), 'pairs': int(f['referrer_pairs']),
             'kind': 'observed_same_hrr' if f['same_hrr'] == '1' else 'observed_cross_hrr'} for f in flows]


def layout(centers, hrrs, edges, seed=2357):
    """Anchored centers keep their coauthorship centroid; everything else is placed near what it links to.

    Order: regions next to the anchored centers inside them, centers without core members next to their
    region, remaining regions next to their state's centers, and anything still unplaced on an outer ring.
    A short spring pass with every anchored node fixed spreads overlaps without letting nodes drift away.
    """
    import random
    rng = random.Random(seed)
    pos = {'c:' + c['ccn']: (c['x'], c['y']) for c in centers if c['x'] is not None}
    links = collections.defaultdict(list)
    for e in edges:
        links['h:' + e['hrr']].append(('c:' + e['ccn'], e['kind'], e['w']))
        links['c:' + e['ccn']].append(('h:' + e['hrr'], e['kind'], e['w']))
    jitter = lambda r: (rng.uniform(-r, r), rng.uniform(-r, r))

    def near(node, kinds, radius):
        pts = [(pos[n], w) for n, kind, w in links[node] if n in pos and kind in kinds]
        if not pts:
            return False
        total = sum(w for _, w in pts) or 1
        cx = sum(p[0] * w for p, w in pts) / total
        cy = sum(p[1] * w for p, w in pts) / total
        dx, dy = jitter(radius)
        pos[node] = (cx + dx, cy + dy)
        return True
    for h in hrrs:
        near('h:' + h['hrr'], {'same_hrr', 'observed_same_hrr', 'observed_cross_hrr'}, 0.06)
    for c in centers:
        if 'c:' + c['ccn'] not in pos:
            near('c:' + c['ccn'], {'same_hrr', 'observed_same_hrr', 'observed_cross_hrr', 'same_state'}, 0.05)
    for h in hrrs:
        if 'h:' + h['hrr'] not in pos:
            near('h:' + h['hrr'], {'same_state'}, 0.18)
    # Anything still unplaced (for example a children's hospital with no flows) sits next to its state's
    # placed centers or regions; only nodes in a state with nothing placed fall back to an outer ring.
    state_of = {'c:' + c['ccn']: c.get('state', '') for c in centers}
    state_of.update({'h:' + h['hrr']: h.get('state', '') for h in hrrs})
    by_state = collections.defaultdict(list)
    for node, (x, y) in pos.items():
        if state_of.get(node):
            by_state[state_of[node]].append((x, y))
    unplaced = [n for n in ['c:' + c['ccn'] for c in centers] + ['h:' + h['hrr'] for h in hrrs] if n not in pos]
    ring = []
    for node in sorted(unplaced):
        pts = by_state.get(state_of.get(node, ''), [])
        if pts:
            cx, cy = sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts)
            dx, dy = jitter(0.12)
            pos[node] = (cx + dx, cy + dy)
        else:
            ring.append(node)
    for i, node in enumerate(ring):
        angle = 2 * math.pi * i / max(1, len(ring))
        pos[node] = (1.25 * math.cos(angle), 1.25 * math.sin(angle))
    graph = nx.Graph()
    graph.add_nodes_from(pos)
    for e in edges:
        graph.add_edge('h:' + e['hrr'], 'c:' + e['ccn'], weight=1.0 if e['kind'] != 'same_state' else 0.2)
    # Anchored centers and edge-less nodes stay put; the spring pass only spreads nodes that have edges.
    isolated = [n for n in graph.nodes if graph.degree(n) == 0]
    fixed = (['c:' + c['ccn'] for c in centers if c['x'] is not None] + isolated) or None
    result = nx.spring_layout(graph, pos=pos, fixed=fixed, weight='weight', seed=seed, iterations=25, k=0.04)
    limit = 1.34  # inside the 1.35 frame even after rounding
    placed = {}
    for node, (x, y) in result.items():
        if not (math.isfinite(x) and math.isfinite(y)):
            x, y = pos[node]
        r = math.hypot(x, y)
        if r > limit:
            x, y = x * limit / r, y * limit / r
        placed[node] = [float(x), float(y)]
    relax_centers(centers, placed, rng)
    return {n: (round(x, 5), round(y, 5)) for n, (x, y) in placed.items()}


def relax_centers(centers, placed, rng, iterations=40):
    """Push overlapping centers apart so institutions that share a community stay legible.

    Anchored centers move half as far as unanchored ones, which keeps them near their coauthorship centroid.
    """
    max_core = max((c['core'] for c in centers), default=1) or 1
    radius = {('c:' + c['ccn']): 0.015 + 0.045 * math.sqrt(c['core'] / max_core) for c in centers}
    weight = {('c:' + c['ccn']): 0.5 if c['x'] is not None else 1.0 for c in centers}
    keys = list(radius)
    for _ in range(iterations):
        moved = False
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                dx, dy = placed[b][0] - placed[a][0], placed[b][1] - placed[a][1]
                dist = math.hypot(dx, dy)
                gap = radius[a] + radius[b] - dist
                if gap <= 0:
                    continue
                if dist < 1e-6:
                    dx, dy, dist = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                ux, uy = dx / dist, dy / dist
                share = weight[a] + weight[b]
                placed[a][0] -= ux * gap * weight[a] / share
                placed[a][1] -= uy * gap * weight[a] / share
                placed[b][0] += ux * gap * weight[b] / share
                placed[b][1] += uy * gap * weight[b] / share
                moved = True
        if not moved:
            break


def spread_positions(centers, hrrs, pos, alpha=0.55, seed=2357, iterations=60):
    """A readable copy of the layout: expand the dense middle radially and keep nodes from overlapping.

    Coordinates in these layouts carry no metric meaning; only neighborhoods do. The radial power
    transform about the anchored centers' centroid is monotone in distance, so every node keeps its
    direction and its rank by distance from the middle, and the collision pass only resolves overlaps.
    """
    import random
    rng = random.Random(seed)
    anchored = [pos['c:' + c['ccn']] for c in centers if c['x'] is not None] or list(pos.values())
    cx = sum(p[0] for p in anchored) / len(anchored)
    cy = sum(p[1] for p in anchored) / len(anchored)
    radii = {n: math.hypot(x - cx, y - cy) for n, (x, y) in pos.items()}
    rmax = max(radii.values()) or 1.0
    out = {}
    for n, (x, y) in pos.items():
        r = radii[n]
        scale = (r / rmax) ** (alpha - 1) if r > 1e-9 else 1.0
        out[n] = [cx + (x - cx) * scale, cy + (y - cy) * scale]
    max_core = max((c['core'] for c in centers), default=1) or 1
    size = {('c:' + c['ccn']): 0.03 + 0.06 * math.sqrt(c['core'] / max_core) for c in centers}
    size.update({('h:' + h['hrr']): 0.018 for h in hrrs})
    keys = [k for k in size if k in out]
    for _ in range(iterations):
        moved = False
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                dx, dy = out[b][0] - out[a][0], out[b][1] - out[a][1]
                dist = math.hypot(dx, dy)
                gap = size[a] + size[b] - dist
                if gap <= 0:
                    continue
                if dist < 1e-6:
                    dx, dy, dist = rng.uniform(-1, 1), rng.uniform(-1, 1), 1.0
                ux, uy = dx / dist, dy / dist
                out[a][0] -= ux * gap / 2; out[a][1] -= uy * gap / 2
                out[b][0] += ux * gap / 2; out[b][1] += uy * gap / 2
                moved = True
        if not moved:
            break
    return {n: (round(x, 5), round(y, 5)) for n, (x, y) in out.items()}


def build(site_path: Path, root: Path = ROOT) -> dict:
    site = json.loads(Path(site_path).read_text(encoding='utf-8'))
    core_nodes = {n['id'].replace('NPI', '', 1): n for n in site['nodes']}
    communities = {c['community']: c for c in site['communities']}
    processed = root / 'Data' / 'processed' / DIVISION_SNAPSHOT
    people = read_csv(processed / 'hcp_divisions.csv')
    centers_raw = read_csv(processed / 'cart_centers.csv')
    groups = read_csv(processed / 'hcp_group_memberships.csv')
    center_ccns = {c['ccn'] for c in centers_raw}

    by_division = collections.defaultdict(list)
    for r in people:
        by_division[r['division']].append(r)
    referrers = by_division['referring_proxy']

    # --- centers (Layer B counts, Layer A anchor)
    members = collections.defaultdict(list)       # ccn -> core rows
    affiliates = collections.defaultdict(collections.Counter)
    for r in people:
        for ccn in r['hospital_ccns'].split(';'):
            if ccn not in center_ccns:
                continue
            if r['is_core'] == '1':
                members[ccn].append(r)
            elif r['division'] in ('affiliate_A', 'affiliate_B', 'center_affiliate_unlinked'):
                affiliates[ccn][r['division']] += 1
    centers = []
    for c in centers_raw:
        core = [core_nodes[r['npi']] for r in members[c['ccn']] if r['npi'] in core_nodes]
        community, share = mode_share([n['community'] for n in core])
        xs = [n['x'] for n in core]
        centers.append({
            'ccn': c['ccn'], 'name': title(c['hospital_name']), 'dba': title(c['dba_name']), 'city': title(c['city']),
            'pediatric': is_pediatric(c['hospital_name']),  # legal name only: adult centers may carry a children's DBA
            'state': c['state'], 'hrr': c['hrr'], 'hrr_city': c['hrr_city'], 'evidence': c['evidence'],
            'drg018_discharges': int(c['drg018_discharges_2024']) if c['drg018_discharges_2024'] else None,
            'fact_programs': int(c['fact_iec_program_count']), 'core': len(core),
            'community': community, 'community_share': round(share, 3),
            'tier': communities[community]['tier'] if community is not None else None,
            'affiliate_A': affiliates[c['ccn']]['affiliate_A'], 'affiliate_B': affiliates[c['ccn']]['affiliate_B'],
            'unlinked': affiliates[c['ccn']]['center_affiliate_unlinked'],
            'leaders': [{'id': n['id'], 'name': n['name'], 'pagerank': n['pagerank'], 'community': n['community']}
                        for n in sorted(core, key=lambda n: -(n['pagerank'] or 0))[:5]],
            'x': round(sum(xs) / len(xs), 5) if xs else None,
            'y': round(sum(n['y'] for n in core) / len(core), 5) if core else None,
        })
    centers_by_hrr = collections.defaultdict(list)       # all centers, for region cards
    centers_by_state = collections.defaultdict(list)
    adult_by_hrr = collections.defaultdict(list)         # proxy targets: adult lymphoma referrals skip children's hospitals
    adult_by_state = collections.defaultdict(list)
    for c in centers:
        if c['hrr']:
            centers_by_hrr[c['hrr']].append(c['ccn'])
            if not c['pediatric']:
                adult_by_hrr[c['hrr']].append(c['ccn'])
        centers_by_state[c['state']].append(c['ccn'])
        if not c['pediatric']:
            adult_by_state[c['state']].append(c['ccn'])

    # --- HRR nodes (Layer C sources)
    hrr_label = {}
    for r in people:
        if r['hrr'] and r['hrr'] not in hrr_label:
            hrr_label[r['hrr']] = f"{r['hrr_city']}, {r['hrr_state']}"
    for c in centers_raw:
        if c['hrr'] and c['hrr'] not in hrr_label:
            hrr_label[c['hrr']] = f"{title(c['hrr_city'])}, {c['state']}"
    group_name = collections.defaultdict(set)
    for g in groups:
        if g['group_legal_name']:
            group_name[g['npi']].add(g['group_legal_name'])
    hrrs = {}
    for r in people:
        if not r['hrr'] or r['division'] not in ('referring_proxy', 'hemonc_other', 'affiliate_C'):
            continue
        h = hrrs.setdefault(r['hrr'], {'hrr': r['hrr'], 'label': hrr_label[r['hrr']], 'state': r['hrr_state'],
                                       'referrers': 0, 'other': 0, 'office_chemo': 0, 'part_d': 0, 'groups': collections.Counter()})
        if r['division'] == 'referring_proxy':
            h['referrers'] += 1
            h['office_chemo'] += int(int(r['chemo_administration_office_rows']) > 0)
            h['part_d'] += int(int(r['part_d_lymphoma_oral_rows']) > 0)
            for name in group_name.get(r['npi'], ()):
                h['groups'][name] += 1
        else:
            h['other'] += 1
    for h in hrrs.values():
        h['top_groups'] = [{'name': title(n), 'n': k} for n, k in h['groups'].most_common(3)]
        del h['groups']
        h['centers'] = centers_by_hrr.get(h['hrr'], [])
        h['has_center'] = bool(h['centers'])
    for c in centers:
        if c['hrr'] and c['hrr'] not in hrrs:
            hrrs[c['hrr']] = {'hrr': c['hrr'], 'label': hrr_label[c['hrr']], 'state': c['state'], 'referrers': 0, 'other': 0,
                              'office_chemo': 0, 'part_d': 0, 'top_groups': [], 'centers': centers_by_hrr[c['hrr']], 'has_center': True}

    # --- Layer C: one edge layer per source. The geographic proxy is always present; each ingested
    # CareSet year adds an observed layer. Positions are computed once from the union so that switching
    # layers on the site never moves a node.
    proxy, unassigned = proxy_edges(referrers, adult_by_hrr, adult_by_state)
    layers = {'proxy': {'label': 'Geographic proxy', 'kind': 'proxy', 'edges': proxy, 'unit': 'referring physicians'}}
    for flow_file in sorted(processed.glob('shared_patient_*_hrr_center_flows.csv')):
        stem = flow_file.name.replace('_hrr_center_flows.csv', '')
        meta_path = flow_file.with_name(stem + '_metadata.json')
        info = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        year, kind = info.get('year') or stem.split('_')[-1], info.get('kind', stem.split('_')[2])
        received = collections.Counter()
        sources = collections.Counter()
        flows = read_csv(flow_file)
        for f in flows:
            received[f['ccn']] += int(f['patients'])
            sources[f['ccn']] += int(f['referrer_pairs'])
        features_path = flow_file.with_name(stem + '_features.csv')
        core_inflow = {}
        if features_path.exists():
            for f in read_csv(features_path):
                if f['division'] == 'core':
                    core_inflow[f['npi']] = {'in_patients_from_referrers': int(f['in_patients_from_referrers']),
                                            'referrer_sources': int(f['referrer_sources'])}
        layers[f'observed_{year}'] = {'label': f'CareSet {kind.replace("_", " ")} {year}', 'kind': 'observed', 'year': int(year),
                                      'edges': observed_edges(flows), 'unit': 'shared patients',
                                      'rows_total': info.get('rows_total'), 'rows_kept': info.get('rows_kept'),
                                      'edge_pairs': sum(sources.values()), 'patients': sum(received.values()),
                                      'same_hrr_share': round(sum(int(f['patients']) for f in flows if f['same_hrr'] == '1') / max(1, sum(received.values())), 3),
                                      'received': dict(received), 'sources': dict(sources), 'core_inflow': core_inflow,
                                      'license': info.get('license', '')}
    observed_keys = sorted(k for k in layers if k.startswith('observed_'))
    default_layer = observed_keys[-1] if observed_keys else 'proxy'
    mode = 'careset' if observed_keys else 'hrr_proxy'
    careset_meta = {k: {kk: vv for kk, vv in v.items() if kk not in ('edges', 'received', 'sources', 'core_inflow')} for k, v in layers.items() if k != 'proxy'} or None
    union = []
    for key, layer in layers.items():
        wmax = max((e['w'] for e in layer['edges']), default=1) or 1
        union.extend({**e, 'w': e['w'] / wmax} for e in layer['edges'])
    hrr_list = sorted(hrrs.values(), key=lambda h: h['hrr'])
    positions = layout(centers, hrr_list, union)
    spread = spread_positions(centers, hrr_list, positions)
    for c in centers:
        c['x'], c['y'] = positions['c:' + c['ccn']]
        c['xs'], c['ys'] = spread['c:' + c['ccn']]
    for h in hrr_list:
        h['x'], h['y'] = positions['h:' + h['hrr']]
        h['xs'], h['ys'] = spread['h:' + h['hrr']]
    reach = collections.defaultdict(float)
    reach_state = collections.defaultdict(float)
    for e in proxy:
        (reach_state if e['kind'] == 'same_state' else reach)[e['ccn']] += e['w']
    for c in centers:
        c['reach'] = round(reach[c['ccn']], 2)
        c['reach_state'] = round(reach_state[c['ccn']], 2)
        c['observed'] = {k: {'patients': layers[k]['received'].get(c['ccn'], 0), 'referrer_pairs': layers[k]['sources'].get(c['ccn'], 0)} for k in observed_keys}
        for leader in c['leaders']:
            leader['inflow'] = {k: layers[k]['core_inflow'].get(leader['id'].replace('NPI', '', 1), {'in_patients_from_referrers': 0, 'referrer_sources': 0}) for k in observed_keys}
    edges = layers[default_layer]['edges']

    division_counts = {k: len(v) for k, v in by_division.items()}
    inputs = {str(p.relative_to(root)): sha256(p) for p in [processed / 'hcp_divisions.csv', processed / 'cart_centers.csv', Path(site_path)]}
    return {
        'meta': {'built_at_utc': now(), 'mode': mode, 'default_layer': default_layer, 'site_run_id': site['meta']['run_id'], 'division_snapshot': DIVISION_SNAPSHOT,
                 'inputs_sha256': inputs, 'centers': len(centers), 'hrrs': len(hrr_list), 'edges': len(edges),
                 'referrers_unassigned': unassigned, 'proxy_edges': len(proxy), 'observed_edges': sum(len(layers[k]['edges']) for k in observed_keys),
                 'pediatric_centers_excluded_from_proxy': sum(c['pediatric'] for c in centers),
                 'careset': careset_meta, 'divisions': division_counts,
                 'milestones': MILESTONES,
                 'layout_note': 'x,y: centers at the centroid of their core members on the seeded spring layout of the coauthorship graph, other nodes placed next to what they link to; xs,ys: the same layout after a monotone radial expansion and collision pass. Neither has metric axes; only neighborhoods carry meaning.',
                 'description': 'Layer B attaches affiliates to CAR-T centers as counts; Layer C links referral regions to centers. '
                                + ('Observed CareSet shared-patient flows are available alongside the geographic proxy.' if observed_keys else
                                   'Edges are a geographic proxy (same referral region, else same state), not observed referrals.')},
        'communities': [{'community': k, 'label': v['label'], 'tier': v['tier'], 'size': v['size']} for k, v in sorted(communities.items())],
        'centers': centers, 'hrrs': hrr_list,
        'layers': {k: {kk: vv for kk, vv in v.items() if kk not in ('received', 'sources', 'core_inflow')} for k, v in layers.items()},
    }


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--site', default='docs/data/site.json')
    parser.add_argument('--out', default='docs/data/catchment.json')
    args = parser.parse_args(argv)
    site = Path(args.site) if Path(args.site).is_absolute() else ROOT / args.site
    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    bundle = build(site)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, separators=(',', ':'), ensure_ascii=False), encoding='utf-8')
    print(f"CATCHMENT {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} mode={bundle['meta']['mode']} "
          f"centers={bundle['meta']['centers']} hrrs={bundle['meta']['hrrs']} edges={bundle['meta']['edges']} "
          f"{out.stat().st_size / 1e6:.2f} MB")
    return out


if __name__ == '__main__':
    main()
