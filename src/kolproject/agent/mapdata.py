"""Turn a map directive into the points, links, bounding box and legend the interface draws.

Only what the directive keeps is returned: everything else is removed from the map, not dimmed. Points are
HCPs and centers at their geocoded locations; links are co-authorship, shared patients (referral) or
research dollars, never more than one kind at a time. Caps keep the picture readable.
"""
from __future__ import annotations

from .packbase import connect

CAPS = {'focus_only': {'linked_hcps': 120, 'edges': 900}, 'focus_and_linked': {'linked_hcps': 400, 'edges': 2500},
        'all': {'linked_hcps': 0, 'edges': 2500}}
LATEST_REFERRAL_YEAR = 2022
REFERRAL_YEARS = range(2017, 2023)
HCP_COLUMNS = ('npi, name, city, state, lat, lon, division_group, is_core, community_id, community_basis, pagerank, '
               'pagerank_rank, is_leader, primary_center_ccn')
CENTER_COLUMNS = 'ccn, name, city, state, lat, lon, community_id, core_hcps, linked_hcps, is_main_center'


def has_table(con, name):
    return con.execute("select 1 from sqlite_master where type = 'table' and name = ?", (name,)).fetchone() is not None


def marks(values):
    return ', '.join('?' for _ in values)


def validate(con, spec):
    """A sentence the model can act on when the directive names something that does not exist, else None."""
    kind, ids = spec['focus']['kind'], spec['focus']['ids']
    if kind == 'usa':
        return None
    if kind not in ('community', 'center', 'hcp', 'state'):
        return (f'focus.kind cannot be {kind}. Use community, center, hcp, state or usa. For a division group or a national '
                'question use {"kind": "usa", "ids": []} with show "all".')
    if not ids:
        return f'focus.ids is empty. Give the id from find_entity for a {kind}.'
    table, column = {'community': ('dim_community', 'community_id'), 'center': ('dim_center', 'ccn'),
                     'hcp': ('dim_hcp', 'npi'), 'state': ('dim_center', 'state')}[kind]
    known = {str(r[0]) for r in con.execute(f'select distinct {column} from {table} where cast({column} as text) in ({marks(ids)})', ids)}
    missing = [i for i in ids if i not in known]
    if missing:
        return f'No {kind} with id {missing[0]}. Use the id returned by find_entity.'
    return None


def focus_sets(con, spec):
    """(npis, ccns) that the focus itself covers."""
    kind, ids = spec['focus']['kind'], spec['focus']['ids']
    if kind == 'community':
        npis = [r[0] for r in con.execute(f'select npi from dim_hcp where is_core = 1 and community_id in ({marks(ids)})', ids)]
        ccns = [r[0] for r in con.execute(f'select ccn from dim_center where community_id in ({marks(ids)})', ids)]
    elif kind == 'center':
        ccns = list(ids)
        npis = [r[0] for r in con.execute(f'select h.npi from hcp_center c join dim_hcp h using (npi) where h.is_core = 1 and c.ccn in ({marks(ids)})', ids)]
    elif kind == 'hcp':
        npis = list(ids)
        ccns = [r[0] for r in con.execute(f'select distinct primary_center_ccn from dim_hcp where npi in ({marks(ids)}) and primary_center_ccn is not null', ids)]
    elif kind == 'state':
        ccns = [r[0] for r in con.execute(f'select ccn from dim_center where state in ({marks(ids)})', ids)]
        npis = [r[0] for r in con.execute(f'select npi from dim_hcp where is_core = 1 and state in ({marks(ids)})', ids)]
    else:
        ccns = [r[0] for r in con.execute('select ccn from dim_center')]
        npis = [r[0] for r in con.execute('select npi from dim_hcp where is_core = 1')]
    return npis, ccns


def coauthor_links(con, spec, npis, cap):
    """Co-author pairs among the focus HCPs, plus their strongest outside co-authors when linked points are kept."""
    if not npis or not has_table(con, 'coauthor_pair'):
        return [], []
    inside = set(npis)
    rows = con.execute(f'select npi_a, npi_b, shared_publications from coauthor_pair where npi_a in ({marks(npis)}) or npi_b in ({marks(npis)}) '
                       'order by shared_publications desc', npis + npis).fetchall()
    links, extra = [], {}
    for a, b, weight in rows:
        if a in inside and b in inside:
            links.append((f'hcp:{a}', f'hcp:{b}', weight))
        elif cap['linked_hcps'] and (spec['show'] == 'focus_and_linked' or spec['focus']['kind'] == 'hcp'):
            other = b if a in inside else a
            if other in extra or len(extra) < (40 if spec['focus']['kind'] == 'hcp' else cap['linked_hcps']):
                extra[other] = extra.get(other, 0) + weight
                links.append((f'hcp:{a}', f'hcp:{b}', weight))
    return links[:cap['edges']], list(extra)


def referral_links(con, spec, npis, ccns, cap):
    """Shared-patient links for one year: into the focus centers, or out of a focus HCP."""
    if not has_table(con, 'ref_link_year'):
        return [], [], []
    year = spec.get('year') if spec.get('year') in REFERRAL_YEARS else LATEST_REFERRAL_YEAR
    if spec['focus']['kind'] == 'hcp':
        rows = con.execute(f'select npi, ccn, patients from ref_link_year where year = ? and npi in ({marks(npis)}) order by patients desc limit 60',
                           [year] + npis).fetchall()
    elif ccns:
        limit = max(cap['linked_hcps'], 60) * (2 if len(ccns) > 3 else 1)
        rows = con.execute(f'select npi, ccn, patients from ref_link_year where year = ? and ccn in ({marks(ccns)}) order by patients desc limit ?',
                           [year] + ccns + [limit]).fetchall()
    else:
        rows = []
    links = [(f'hcp:{n}', f'center:{c}', p) for n, c, p in rows][:cap['edges']]
    return links, sorted({n for n, _, _ in rows}), sorted({c for _, c, _ in rows})


def payment_links(con, spec, npis, ccns, cap):
    """Research-payment links between investigators and the hospital named on the payment record."""
    if not has_table(con, 'pay_research_hcp_center_year'):
        return [], [], []
    where, args = [], []
    if spec.get('year'):
        where.append('year = ?')
        args.append(spec['year'])
    if spec['focus']['kind'] in ('center', 'state') and ccns:
        where.append(f'ccn in ({marks(ccns)})')
        args += ccns
    elif npis:
        where.append(f'npi in ({marks(npis)})')
        args += npis
    else:
        return [], [], []
    rows = con.execute('select npi, ccn, sum(usd) from pay_research_hcp_center_year where ' + ' and '.join(where)
                       + ' group by npi, ccn order by 3 desc limit ?', args + [cap['edges']]).fetchall()
    return [(f'hcp:{n}', f'center:{c}', usd) for n, c, usd in rows], sorted({n for n, _, _ in rows}), sorted({c for _, c, _ in rows})


def payment_sizes(con, npis):
    """Dollars of payments naming lymphoma CAR-T products, per HCP, all years."""
    if not npis or not has_table(con, 'pay_general_year'):
        return {}
    return dict(con.execute(f'select npi, sum(lymphoma_only_usd) from pay_general_year where npi in ({marks(npis)}) group by npi', npis).fetchall())


def select(con, spec):
    """(npis, ccns, links, sizes, year) kept by the directive."""
    cap = CAPS[spec['show']]
    npis, ccns = focus_sets(con, spec)
    links, sizes, year = [], {}, None
    if spec['edges'] == 'coauthor':
        links, more = coauthor_links(con, spec, npis, cap)
        npis = npis + [n for n in more if n not in set(npis)]
    elif spec['edges'] == 'referral':
        links, more_n, more_c = referral_links(con, spec, npis, ccns, cap)
        year = spec.get('year') if spec.get('year') in REFERRAL_YEARS else LATEST_REFERRAL_YEAR
        seen = set(npis)
        npis = npis + [n for n in more_n if n not in seen]
        ccns = ccns + [c for c in more_c if c not in set(ccns)]
        for source, target, weight in links:
            sizes[source] = sizes.get(source, 0) + weight
            sizes[target] = sizes.get(target, 0) + weight
    elif spec['edges'] == 'payments':
        links, more_n, more_c = payment_links(con, spec, npis, ccns, cap)
        seen = set(npis)
        npis = npis + [n for n in more_n if n not in seen]
        ccns = ccns + [c for c in more_c if c not in set(ccns)]
        sizes = {f'hcp:{n}': v for n, v in payment_sizes(con, npis).items() if v}
        year = spec.get('year')
    if spec['show'] == 'focus_and_linked' and spec['focus']['kind'] in ('community', 'center') and spec['edges'] in ('none', 'coauthor'):
        ids = spec['focus']['ids']
        if spec['focus']['kind'] == 'community':
            extra = con.execute(f'select npi from dim_hcp where is_core = 0 and community_id in ({marks(ids)}) order by '
                                "case community_basis when 'coauthorship' then 0 when 'center' then 1 else 2 end, npi limit ?", ids + [cap['linked_hcps']])
        else:
            extra = con.execute(f'select h.npi from hcp_center c join dim_hcp h using (npi) where h.is_core = 0 and c.ccn in ({marks(ids)}) limit ?',
                                ids + [cap['linked_hcps']])
        seen = set(npis)
        npis = npis + [r[0] for r in extra if r[0] not in seen]
    return npis, ccns, links, sizes, year


def counts(con, spec):
    """What the directive keeps on the map, for the model: how many HCPs, centers and links."""
    npis, ccns, links, _, _ = select(con, spec)
    return {'hcps': len(npis), 'centers': len(ccns), 'links': len(links)}


def highlighted(spec, hcps, centers):
    """Ids that get a ring and a name label."""
    mode = spec.get('highlight', 'none')
    if isinstance(mode, list):
        wanted = {str(i) for i in mode}
        return {f"hcp:{h['npi']}" for h in hcps if h['npi'] in wanted} | {f"center:{c['ccn']}" for c in centers if c['ccn'] in wanted}
    focus_communities = set(spec['focus']['ids']) if spec['focus']['kind'] == 'community' else None
    if mode == 'leaders':
        return {f"hcp:{h['npi']}" for h in hcps if h['is_leader'] and (focus_communities is None or str(h['community_id']) in focus_communities)}
    if mode == 'main_centers':
        if spec['focus']['kind'] == 'center':
            return {f'center:{i}' for i in spec['focus']['ids']}
        return {f"center:{c['ccn']}" for c in centers if c['is_main_center']}
    return set()


def legend(con, spec, year, hcps, centers):
    """Words the interface prints beside the map; fixed per link kind so every answer reads the same."""
    communities = sorted({p['community_id'] for p in hcps + centers if p['community_id'] is not None})
    rows = con.execute(f'select community_id, short_label, color_light, color_dark from dim_community where community_id in ({marks(communities)}) '
                       'order by community_id', communities).fetchall() if communities else []
    named = [dict(r) for r in rows if r['community_id'] <= 13]
    if any(r['community_id'] > 13 for r in rows):
        other = next(r for r in rows if r['community_id'] > 13)
        named.append({'community_id': 14, 'short_label': 'Outside main network', 'color_light': other['color_light'], 'color_dark': other['color_dark']})
    out = {'communities': named, 'shapes': [{'shape': 'circle', 'label': 'HCP'}, {'shape': 'square', 'label': 'CAR-T center'}],
           'unassigned': 'Grey, hollow: not attached to a community', 'highlight': None, 'links': None, 'size': 'Circle size: PageRank (core HCPs)',
           'note': None}
    mode = spec.get('highlight', 'none')
    if mode == 'leaders':
        out['highlight'] = 'Ringed and named: community leaders by PageRank'
    elif mode == 'main_centers':
        out['highlight'] = 'Named: main centers'
    elif isinstance(mode, list) and mode:
        out['highlight'] = 'Ringed and named: the HCPs or centers asked about'
    if spec['edges'] == 'coauthor':
        out['links'] = {'kind': 'coauthor', 'label': 'Line: co-authored CAR-T lymphoma papers, 2017 to 2026', 'width': 'Width: papers shared'}
    elif spec['edges'] == 'referral':
        out['links'] = {'kind': 'referral', 'label': f'Arrow: shared Medicare patients from an HCP to a center, {year}', 'width': 'Width: shared patients'}
        out['size'] = 'Size: shared patients on the links shown'
        out['note'] = 'Any condition, Medicare fee-for-service only. Not CAR-T referrals. Source: CareSet Systems DocGraph, non-commercial licence.'
    elif spec['edges'] == 'payments':
        when = str(year) if year else '2019 to 2025'
        out['links'] = {'kind': 'payments', 'label': f'Dashed line: research payments naming a CAR-T product, investigator to hospital, {when}',
                        'width': 'Width: whole-study dollars on the records'}
        out['size'] = 'Circle size: payments naming lymphoma CAR-T products, 2019 to 2025'
        out['note'] = 'Amounts reported by manufacturers (CMS Open Payments). Research amounts are whole-study totals, not personal income.'
    return out


def view(con, spec):
    """Everything the interface needs to draw the map for one directive."""
    npis, ccns, links, sizes, year = select(con, spec)
    hcps = [dict(r) for r in con.execute(f'select {HCP_COLUMNS} from dim_hcp where npi in ({marks(npis)})', npis)] if npis else []
    centers = [dict(r) for r in con.execute(f'select {CENTER_COLUMNS} from dim_center where ccn in ({marks(ccns)})', ccns)] if ccns else []
    ringed = highlighted(spec, hcps, centers)
    focus_kind, focus_ids = spec['focus']['kind'], set(spec['focus']['ids'])
    points, unplaced = [], 0
    for h in hcps:
        key = f"hcp:{h['npi']}"
        if h['lat'] is None:
            unplaced += 1
            continue
        points.append({'id': key, 'kind': 'hcp', 'name': h['name'], 'place': ', '.join(x for x in (h['city'], h['state']) if x),
                       'lat': h['lat'], 'lon': h['lon'], 'community_id': h['community_id'], 'division_group': h['division_group'],
                       'is_core': h['is_core'], 'size': sizes.get(key, h['pagerank'] or 0), 'label': key in ringed,
                       'focus': focus_kind == 'hcp' and h['npi'] in focus_ids})
    for c in centers:
        key = f"center:{c['ccn']}"
        if c['lat'] is None:
            unplaced += 1
            continue
        points.append({'id': key, 'kind': 'center', 'name': c['name'], 'place': f"{c['city']}, {c['state']}", 'lat': c['lat'], 'lon': c['lon'],
                       'community_id': c['community_id'], 'division_group': None, 'is_core': 0, 'size': sizes.get(key, c['core_hcps'] or 0),
                       'label': key in ringed, 'focus': focus_kind == 'center' and c['ccn'] in focus_ids})
    placed = {p['id'] for p in points}
    drawn = [{'source': s, 'target': t, 'weight': w} for s, t, w in links if s in placed and t in placed]
    box_points = [p for p in points if p['focus']] if focus_kind == 'hcp' and len(points) > 1 and spec['edges'] == 'none' else points
    if spec['edges'] == 'referral' and focus_kind in ('center', 'community'):
        box_points = [p for p in points if p['kind'] == 'center' or p['id'] in {e['source'] for e in drawn[:80]}] or points
    bbox = bounds(box_points) if focus_kind != 'usa' and box_points else [-125.0, 24.0, -66.5, 49.5]
    return {'spec': spec, 'points': points, 'links': drawn, 'bbox': bbox, 'legend': legend(con, spec, year, hcps, centers),
            'counts': {'hcps': sum(p['kind'] == 'hcp' for p in points), 'centers': sum(p['kind'] == 'center' for p in points),
                       'links': len(drawn), 'not_placed': unplaced}}


def bounds(points):
    """Bounding box [min_lon, min_lat, max_lon, max_lat] with padding; far-flung points (Alaska, Hawaii,
    Puerto Rico) do not stretch a box that is otherwise in the contiguous states."""
    mainland = [p for p in points if -125.5 <= p['lon'] <= -66 and 24 <= p['lat'] <= 50]
    use = mainland if len(mainland) >= max(1, len(points) * 0.8) else points
    lons, lats = [p['lon'] for p in use], [p['lat'] for p in use]
    pad_lon = max(0.35, (max(lons) - min(lons)) * 0.12)
    pad_lat = max(0.25, (max(lats) - min(lats)) * 0.12)
    return [round(min(lons) - pad_lon, 4), round(min(lats) - pad_lat, 4), round(max(lons) + pad_lon, 4), round(max(lats) + pad_lat, 4)]


def default_view(con=None):
    """The opening map: every center and every core HCP, no links."""
    own = con is None
    con = con or connect(readonly=True)
    try:
        return view(con, {'focus': {'kind': 'usa', 'ids': []}, 'show': 'all', 'edges': 'none', 'highlight': 'none'})
    finally:
        if own:
            con.close()
