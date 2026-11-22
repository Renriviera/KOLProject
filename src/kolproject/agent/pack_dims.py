"""Core tables of the agent pack: communities, HCPs, centers, HCP-to-center links, products and their events.

Communities and PageRank come from the published site run (`docs/data/site.json`) and are never recomputed.
Everyone outside the 500 core HCPs is attached to a community by the orbit rule in `attach`, and the basis is
stored so the agent can state it. Method diagnostics (stability tiers, movers, backtests) are not loaded.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict

from ..volume_billing.base import DIVISION_GROUPS
from .packbase import connect, create_table, read_rows, register, source
from .paths import GEO, PACK, REFERENCE, SITE_RUN

LEADERS_PER_COMMUNITY = 5
MAIN_CENTERS_PER_COMMUNITY = 3
# Words kept in capitals when an all-capitals registry name is put into reading case.
ACRONYMS = {'UCSF', 'UCLA', 'UCI', 'UCSD', 'USC', 'UC', 'NYU', 'UPMC', 'UT', 'OHSU', 'UNC', 'UVA', 'VA', 'MD', 'LLC', 'INC', 'MUSC', 'UAB', 'UNM',
            'UK', 'OU', 'IU', 'UF', 'UW', 'WVU', 'VCU', 'LSU', 'UI', 'UH', 'UTMB', 'SSM', 'HCA', 'RWJ', 'UMC', 'II', 'III', 'IV', 'DBA', 'UCHEALTH',
            'UAMS', 'UCM', 'AHN', 'TMC', 'NC', 'SUNY', 'US'}
SMALL_WORDS = {'of', 'and', 'the', 'at', 'for', 'in', 'on', 'de', 'la'}


def reading_case(text):
    """An all-capitals (or all-lower-case) name in reading case; a name already in mixed case is left alone."""
    text = ' '.join(str(text or '').split())
    if not text or not (text.isupper() or text.islower()):
        return text
    words = []
    for i, word in enumerate(text.split(' ')):
        bare = word.strip('(),.&/')
        if bare.upper() in ACRONYMS:
            words.append(word.upper())
        elif bare.lower() in SMALL_WORDS and i:
            words.append(word.lower())
        else:
            words.append('-'.join(part.capitalize() if not part[:1] == '(' else '(' + part[1:].capitalize() for part in word.split('-')))
    return ' '.join(words)


def site_nodes():
    """Core HCP facts from the fixed site run, keyed by NPI."""
    nodes = json.loads(SITE_RUN.read_text(encoding='utf-8'))['nodes']
    return {str(n['id']).replace('NPI', ''): n for n in nodes}


def points(name, key):
    """Geocoded points written by `geo.py`, or nothing when that step has not run yet."""
    path = GEO / name
    if not path.exists():
        return {}
    with path.open(newline='', encoding='utf-8') as stream:
        return {row[key]: row for row in csv.DictReader(stream)}


def center_community(core_at_center, community_of, pagerank_of):
    """Community holding most of a center's core HCPs; ties go to the larger summed PageRank."""
    out = {}
    for ccn, npis in core_at_center.items():
        tally, weight = Counter(), Counter()
        for npi in npis:
            tally[community_of[npi]] += 1
            weight[community_of[npi]] += pagerank_of[npi]
        if tally:
            out[ccn] = max(tally, key=lambda c: (tally[c], weight[c], -c))
    return out


def attach(npi, coauthor_weight, centers_of, center_comm, core_count, referral_center):
    """Orbit rule for a non-core HCP: (community, basis, primary center)."""
    linked = centers_of.get(npi, [])
    by_size = sorted(linked, key=lambda c: (-core_count.get(c, 0), c))
    weights = coauthor_weight.get(npi)
    if weights:
        community = max(weights, key=lambda c: (weights[c], -c))
        preferred = [c for c in by_size if center_comm.get(c) == community]
        return community, 'coauthorship', (preferred or by_size or [referral_center.get(npi)])[0]
    for ccn in by_size:
        if ccn in center_comm:
            return center_comm[ccn], 'center', ccn
    ccn = referral_center.get(npi)
    if ccn and ccn in center_comm:
        return center_comm[ccn], 'referral_primary_center', ccn
    return None, 'unassigned', (by_size or [ccn])[0] if (by_size or ccn) else None


def build(con):
    nodes = site_nodes()
    reference = json.loads((REFERENCE / 'communities.json').read_text(encoding='utf-8'))['communities']
    people = list(read_rows(source('divisions', 'hcp_divisions')))
    center_rows = list(read_rows(source('divisions', 'cart_centers')))
    center_ids = {r['ccn'] for r in center_rows}
    panel = {r['ccn']: r for r in read_rows(source('volume', 'cart_site_volume_panel')) if r['ccn']}
    status = {r['ccn']: r for r in read_rows(source('volume', 'cart_center_340b_status'))}
    hcp_points, center_points = points('hcp_points.csv', 'npi'), points('center_points.csv', 'ccn')

    community_of = {npi: int(n['community']) for npi, n in nodes.items()}
    pagerank_of = {npi: float(n['pagerank']) for npi, n in nodes.items()}
    centers_of = {r['npi']: [c for c in r['hospital_ccns'].split(';') if c in center_ids] for r in people}
    core_at_center = defaultdict(list)
    for npi in nodes:
        for ccn in centers_of.get(npi, []):
            core_at_center[ccn].append(npi)
    core_count = {ccn: len(v) for ccn, v in core_at_center.items()}
    center_comm = center_community(core_at_center, community_of, pagerank_of)

    coauthor_weight = defaultdict(Counter)
    for row in read_rows(source('extensions', 'universe_coauthor_edges')):
        a, b = row['npi_a'], row['npi_b']
        for person, partner in ((a, b), (b, a)):
            if person not in nodes and partner in nodes:
                coauthor_weight[person][community_of[partner]] += float(row['fractional_weight'])
    referral_center = {r['npi']: r['primary_center_ccn'] for r in read_rows(source('extensions', 'shared_patient_npi_longitudinal_2017_2022'))
                       if r['primary_center_ccn']}

    rank = {}
    for community in set(community_of.values()):
        members = sorted((n for n in nodes if community_of[n] == community), key=lambda n: -pagerank_of[n])
        rank.update({npi: i + 1 for i, npi in enumerate(members)})

    hcp_rows, links = [], []
    for r in people:
        npi, node = r['npi'], nodes.get(r['npi'])
        if node:
            community, basis = community_of[npi], 'core_member'
            own = [c for c in centers_of[npi] if center_comm.get(c) == community]
            primary = (sorted(own or centers_of[npi], key=lambda c: (-core_count.get(c, 0), c)) or [None])[0]
        else:
            community, basis, primary = attach(npi, coauthor_weight, centers_of, center_comm, core_count, referral_center)
        point = hcp_points.get(npi, {})
        first, last = reading_case(r['first_name']), reading_case(r['last_name'])
        name = ' '.join(part for part in (first, last) if part)
        hcp_rows.append({
            'npi': npi, 'name': node['name'] if node else name, 'first_name': first,
            'last_name': last, 'credentials': r['credentials'] or None, 'city': reading_case(r['city']) or None,
            'state': r['state'] or None, 'zip5': r['zip5'] or None, 'hrr': r['hrr'] or None,
            'lat': float(point['lat']) if point.get('lat') else None, 'lon': float(point['lon']) if point.get('lon') else None,
            'geo_precision': point.get('geo_precision') or None, 'division': r['division'],
            'division_group': DIVISION_GROUPS[r['division']], 'is_core': int(bool(node)), 'community_id': community,
            'community_basis': basis, 'pagerank': pagerank_of.get(npi), 'pagerank_rank': rank.get(npi),
            'is_leader': int(rank.get(npi, 99) <= LEADERS_PER_COMMUNITY and community <= 13) if node else 0,
            'network_role': node['hub_role'] if node else None, 'institution': node['institution'] if node else None,
            'core_publications': node['publications'] if node else None, 'primary_center_ccn': primary,
            'specialty': reading_case((r['ndf_primary_specialty'] or r['cms_provider_type_2024']).lower()).replace('/oncology', '/Oncology') or None})
        links.extend((npi, ccn) for ccn in centers_of[npi])

    attached = Counter(h['community_id'] for h in hcp_rows if h['community_id'] is not None)
    core_members = Counter(community_of.values())
    centers_in = Counter(center_comm.values())
    main = set()
    for community in set(center_comm.values()):
        ranked = sorted((c for c in center_comm if center_comm[c] == community), key=lambda c: (-core_count[c], c))
        main.update(ranked[:MAIN_CENTERS_PER_COMMUNITY])
    # A hospital whose campus-style address the geocoder could not match takes the address-level point that most
    # of its linked clinicians share, when at least three share it and it lies near the fallback point.
    shared = defaultdict(Counter)
    by_npi = {h['npi']: h for h in hcp_rows}
    for npi, ccn in links:
        h = by_npi[npi]
        if h['geo_precision'] == 'address' and h['lat'] is not None:
            shared[ccn][(h['state'], round(h['lat'], 4), round(h['lon'], 4))] += 1
    center_out = []
    for r in center_rows:
        ccn, point = r['ccn'], dict(center_points.get(r['ccn'], {}))
        if point.get('geo_precision') not in (None, '', 'address') and point.get('lat'):
            reach = 0.06 if point['geo_precision'] == 'zip_centroid' else 0.15  # degrees: about 6 km, or 15 km from a city centroid
            near = [(n, lat, lon) for (state, lat, lon), n in shared[ccn].items()
                    if state == r['state'] and abs(lat - float(point['lat'])) < reach and abs(lon - float(point['lon'])) < reach]
            if near and max(near)[0] >= 3:
                _, lat, lon = max(near)
                point.update(lat=lat, lon=lon, geo_precision='linked_hcp_address')
        center_out.append({
            'ccn': ccn, 'name': reading_case(panel.get(ccn, {}).get('site_name') or r['dba_name'] or r['hospital_name']),
            'city': reading_case(r['city']), 'state': r['state'], 'zip5': r['zip5'], 'hrr': r['hrr'],
            'lat': float(point['lat']) if point.get('lat') else None, 'lon': float(point['lon']) if point.get('lon') else None,
            'geo_precision': point.get('geo_precision') or None, 'community_id': center_comm.get(ccn),
            'community_basis': 'core_majority' if ccn in center_comm else 'unassigned', 'core_hcps': core_count.get(ccn, 0),
            'linked_hcps': sum(1 for _, c in links if c == ccn), 'is_main_center': int(ccn in main),
            'fact_accredited': int('fact' in r['evidence']), 'class_340b': status.get(ccn, {}).get('class_340b') or None,
            'pps_exempt_cancer_hospital': int(status.get(ccn, {}).get('pps_exempt_cancer_hospital') == '1'),
            'medicare_file_status_2024': panel.get(ccn, {}).get('medicare_file_status_2024') or None})

    create_table(con, 'dim_community', [
        ('community_id', 'int'), ('label', 'text'), ('short_label', 'text'), ('kind', 'text'), ('color_light', 'text'),
        ('color_dark', 'text'), ('region', 'text'), ('top_institution', 'text'), ('core_members', 'int'),
        ('attached_hcps', 'int'), ('centers', 'int')],
        ({**{k: c[k] for k in ('community_id', 'label', 'short_label', 'kind', 'color_light', 'color_dark', 'region', 'top_institution')},
          'core_members': core_members[c['community_id']], 'attached_hcps': attached[c['community_id']],
          'centers': centers_in[c['community_id']]} for c in reference), primary_key=['community_id'])
    create_table(con, 'dim_hcp', [
        ('npi', 'text'), ('name', 'text'), ('first_name', 'text'), ('last_name', 'text'), ('credentials', 'text'),
        ('city', 'text'), ('state', 'text'), ('zip5', 'text'), ('hrr', 'text'), ('lat', 'real'), ('lon', 'real'),
        ('geo_precision', 'text'), ('division', 'text'), ('division_group', 'text'), ('is_core', 'int'),
        ('community_id', 'int'), ('community_basis', 'text'), ('pagerank', 'real'), ('pagerank_rank', 'int'),
        ('is_leader', 'int'), ('network_role', 'text'), ('institution', 'text'), ('core_publications', 'int'),
        ('primary_center_ccn', 'text'), ('specialty', 'text')], hcp_rows, primary_key=['npi'],
        indexes=['community_id', 'division_group', 'state', 'primary_center_ccn', 'last_name'])
    create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')], links, primary_key=['npi', 'ccn'], indexes=['ccn'])
    create_table(con, 'dim_center', [
        ('ccn', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('zip5', 'text'), ('hrr', 'text'),
        ('lat', 'real'), ('lon', 'real'), ('geo_precision', 'text'), ('community_id', 'int'), ('community_basis', 'text'),
        ('core_hcps', 'int'), ('linked_hcps', 'int'), ('is_main_center', 'int'), ('fact_accredited', 'int'),
        ('class_340b', 'text'), ('pps_exempt_cancer_hospital', 'int'), ('medicare_file_status_2024', 'text')],
        center_out, primary_key=['ccn'], indexes=['community_id', 'state'])
    build_products(con)
    register(con, 'dim_community', 'community', ['agent/reference/communities.json', 'docs/data/site.json'],
             'The 13 named co-authorship communities of the core HCPs and one entry for isolated HCPs, with fixed colours.')
    register(con, 'dim_hcp', 'HCP', ['2026-10-24/hcp_divisions', 'docs/data/site.json', '2026-11-20/universe_coauthor_edges',
                                    '2026-11-20/shared_patient_npi_longitudinal_2017_2022', 'agent/pack/geo/hcp_points.csv'],
             'Every HCP of the 17,510-NPI universe with division, location, community and how it was attached, and PageRank for core HCPs.',
             '2024 snapshot')
    register(con, 'hcp_center', 'HCP x center', ['2026-10-24/hcp_divisions'], 'Hospital links between HCPs and the 173 CAR-T centers.', '2024 snapshot')
    register(con, 'dim_center', 'center', ['2026-10-24/cart_centers', '2026-11-19/cart_site_volume_panel', '2026-11-19/cart_center_340b_status',
                                          'agent/pack/geo/center_points.csv'],
             'The 173 CAR-T centers with location, community of most of their core HCPs, and counts of linked HCPs.', '2024 snapshot')
    return {'dim_hcp': len(hcp_rows), 'dim_center': len(center_out), 'hcp_center': len(links),
            'communities_with_centers': len(centers_in), 'hcps_by_basis': dict(Counter(h['community_basis'] for h in hcp_rows)),
            'hcps_with_point': sum(h['lat'] is not None for h in hcp_rows), 'centers_with_point': sum(c['lat'] is not None for c in center_out)}


def product_file():
    """The verified product table when it exists, else the draft (whose entries then count as unverified)."""
    verified = REFERENCE / 'cart_products.json'
    path = verified if verified.exists() else REFERENCE / 'cart_products.draft.json'
    return json.loads(path.read_text(encoding='utf-8')), path.name


def build_products(con):
    data, name = product_file()
    draft = name.endswith('.draft.json')
    products, events = [], []
    for p in data['products']:
        products.append({'brand': p['brand'], 'generic': p['generic'], 'short_name': p['short_name'], 'company_group': p['company_group'],
                         'partner': p.get('partner'), 'target': p['target'], 'lymphoma_product': int(p['lymphoma_product']),
                         'hcpcs_code': p.get('hcpcs_code') or None, 'first_us_approval': p['first_us_approval']})
        for e in p['events']:
            events.append({'date': e['date'], 'brand': p['brand'], 'event_type': e['type'], 'label': e['label'],
                           'lymphoma': int(bool(e.get('lymphoma'))), 'verified': int(bool(e.get('verified')) and not draft),
                           'source_url': e.get('source_url')})
    for e in data.get('class_and_coding_events', []):
        events.append({'date': e['date'], 'brand': None, 'event_type': e['type'], 'label': e['label'], 'lymphoma': 0,
                       'verified': int(bool(e.get('verified')) and not draft), 'source_url': e.get('source_url')})
    create_table(con, 'dim_product', [('brand', 'text'), ('generic', 'text'), ('short_name', 'text'), ('company_group', 'text'),
                                      ('partner', 'text'), ('target', 'text'), ('lymphoma_product', 'int'), ('hcpcs_code', 'text'),
                                      ('first_us_approval', 'text')], products, primary_key=['brand'])
    create_table(con, 'product_event', [('date', 'text'), ('brand', 'text'), ('event_type', 'text'), ('label', 'text'),
                                        ('lymphoma', 'int'), ('verified', 'int'), ('source_url', 'text')],
                 sorted(events, key=lambda e: e['date']), indexes=['brand', 'date'])
    register(con, 'dim_product', 'CAR-T product', [f'agent/reference/{name}'], 'The approved CAR-T products: names, company, target, billing code.', '2017 to today')
    register(con, 'product_event', 'dated event', [f'agent/reference/{name}'],
             'Approval, new-indication, coding and class-wide events; only verified events are used as chart markers.', '2017 to today')
    return len(products), len(events)


def check(con):
    """Structural checks on the core tables; zero means pass."""
    one = lambda sql: con.execute(sql).fetchone()[0]
    return {
        'dims_hcp_count': int(one('select count(*) from dim_hcp') != 17510),
        'dims_center_count': int(one('select count(*) from dim_center') != 173),
        'dims_core_count': int(one('select count(*) from dim_hcp where is_core = 1') != 500),
        'dims_core_without_community': one('select count(*) from dim_hcp where is_core = 1 and community_id is null'),
        'dims_leaders_per_named_community': one(f'select count(*) from (select community_id from dim_hcp where is_leader = 1 '
                                                f'group by community_id having count(*) > {LEADERS_PER_COMMUNITY})'),
        'dims_unknown_community': one('select count(*) from dim_hcp where community_id is not null and community_id not in (select community_id from dim_community)'),
        'dims_basis_without_community': one("select count(*) from dim_hcp where (community_id is null) != (community_basis = 'unassigned')"),
        'dims_link_orphans': one('select count(*) from hcp_center where ccn not in (select ccn from dim_center) or npi not in (select npi from dim_hcp)'),
        'dims_products': int(one('select count(*) from dim_product') < 7),
    }


def main():
    """Build only the core tables into agent/pack/dims.sqlite, the base the part modules develop against."""
    path = PACK / 'dims.sqlite'
    path.unlink(missing_ok=True)
    con = connect(path)
    summary = build(con)
    con.commit()
    print('DIMS', json.dumps(summary), 'CHECKS', {k: v for k, v in check(con).items() if v})
    con.close()


if __name__ == '__main__':
    main()
