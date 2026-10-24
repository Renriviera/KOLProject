"""Bundle one KOL run into docs/data/site.json for the static GitHub Pages site.

Usage: uv run kolproject-kol-site models/kol_runs/<run-id> [--out docs/data/site.json]

Reads only the run directory, the published processed tables, and their metadata.
No payment dollar amounts are written; the site shows manufacturer counts only.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from ..common import ROOT, SNAPSHOT, now
from ..identity import HOLDOUT
from .data import sha256

TIER_ORDER = ['firm', 'provisional', 'unstable', 'outside main network']
# Hard-coded descriptions used when the local-only source catalog is absent from a clone.
SOURCE_DOCS = {
    'Europe PMC / PubMed MED': 'https://europepmc.org/RestfulWebService',
    'CMS NPPES NPI Registry': 'https://npiregistry.cms.hhs.gov/api-page',
    'CMS Open Payments': 'https://openpaymentsdata.cms.gov/',
    'CMS Medicare Physician & Other Practitioners by Provider and Service':
        'https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/'
        'medicare-physician-other-practitioners-by-provider-and-service',
}
PUBLICATION_QUERY = ('(TITLE_ABS:"CAR-T" OR TITLE_ABS:"CAR T" OR TITLE_ABS:"chimeric antigen receptor" OR '
                     'TITLE_ABS:axicabtagene OR TITLE_ABS:tisagenlecleucel OR TITLE_ABS:lisocabtagene OR '
                     'TITLE_ABS:brexucabtagene) AND (TITLE:lymphoma OR TITLE:DLBCL OR TITLE:LBCL) AND SRC:MED')


def _clean(value):
    """JSON-safe scalars: NaN -> None, numpy -> Python, bool preserved."""
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(float(value)) else round(float(value), 6)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def records(frame: pd.DataFrame, columns=None, limit=None) -> list[dict]:
    frame = frame if columns is None else frame[[c for c in columns if c in frame]]
    if limit:
        frame = frame.head(limit)
    return [{k: _clean(v) for k, v in row.items()} for row in frame.to_dict('records')]


def read(run: Path, name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(run / f'{name}.csv', dtype={'hcp_id': str, 'npi': str}, **kwargs)


def layout(nodes: list[str], edges: pd.DataFrame, seed: int) -> dict[str, tuple[float, float]]:
    """Seeded spring layout, same parameters as the published network figure in edav.py."""
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    for row in edges.itertuples():
        graph.add_edge(row.hcp_id_a, row.hcp_id_b, weight=row.fractional_weight)
    positions = nx.spring_layout(graph, seed=seed, iterations=150, weight='weight', k=1.5 / math.sqrt(len(nodes)))
    return {node: (round(float(x), 5), round(float(y), 5)) for node, (x, y) in positions.items()}


def build(run: Path, root: Path = ROOT) -> dict:
    run = Path(run)
    meta = json.loads((run / 'run_metadata.json').read_text())
    processed = root / 'Data' / 'processed' / SNAPSHOT
    hcps = pd.read_csv(processed / 'hcps.csv', dtype={'npi': str, 'hcp_id': str})
    edges_raw = pd.read_csv(processed / 'coauthor_edges.csv', dtype=str)
    edges_raw['fractional_weight'] = edges_raw.fractional_weight.astype(float)
    edges_raw['shared_publications'] = edges_raw.shared_publications.astype(int)

    scores = read(run, 'kol_scores').set_index('hcp_id')
    features = read(run, 'features').set_index('hcp_id')
    movers = read(run, 'movers').set_index('hcp_id')
    profiles = read(run, 'community_profiles')
    node_ids = list(scores.index)
    index = {node: i for i, node in enumerate(node_ids)}
    positions = layout(node_ids, edges_raw, int(meta['config']['seed']))

    composites = list(meta['config']['composites'])
    axes = list(meta['config']['axes'])
    nodes = []
    for hcp_id in node_ids:
        s, f, m = scores.loc[hcp_id], features.loc[hcp_id], movers.loc[hcp_id]
        x, y = positions[hcp_id]
        node = {'id': hcp_id, 'name': str(s.display_name).title(), 'institution': s.institution,
                'state': s.npi_state, 'community': int(s.community), 'tier': s.community_tier,
                'hub_role': s.hub_role, 'mover_class': m.mover_class, 'mover_score': _clean(m.mover_score),
                'community_split': m.community_split if isinstance(m.community_split, str) else '',
                'pagerank': _clean(f.pagerank), 'betweenness': _clean(f.betweenness),
                'participation': _clean(f.participation), 'degree': int(f.degree),
                'publications': int(f.publications), 'manufacturers_2024': int(m.manufacturers_2024),
                'consensus': _clean(s.community_node_consensus), 'x': x, 'y': y,
                'ranks': {c: int(s[f'{c}_rank']) for c in composites},
                'scores': {c: _clean(s[f'{c}_score']) for c in composites},
                'axes': {a: _clean(s[f'axis_{a}']) for a in axes}}
        for label in ['guideline_panel', 'cart_industry_advisor']:
            if f'label_{label}' in s:
                node[label] = bool(s[f'label_{label}'])
        nodes.append(node)

    edges = [{'s': index[a], 't': index[b], 'w': round(w, 4), 'n': int(n)}
             for a, b, w, n in zip(edges_raw.hcp_id_a, edges_raw.hcp_id_b,
                                   edges_raw.fractional_weight, edges_raw.shared_publications)]

    xy = pd.DataFrame({'community': scores.community, 'x': [positions[n][0] for n in node_ids],
                       'y': [positions[n][1] for n in node_ids]})
    boxes = xy.groupby('community').agg(x0=('x', 'min'), x1=('x', 'max'), y0=('y', 'min'), y1=('y', 'max'),
                                       cx=('x', 'mean'), cy=('y', 'mean'))
    communities = []
    for row in profiles.sort_values(['community']).itertuples(index=False):
        record = {k: _clean(v) for k, v in row._asdict().items()}
        record['top_manufacturer'] = record.pop('top_manufacturer_usd', None)  # a name, not an amount
        box = boxes.loc[row.community]
        record['bbox'] = {k: round(float(box[k]), 5) for k in ['x0', 'x1', 'y0', 'y1', 'cx', 'cy']}
        record['leader_ids'] = [n['id'] for n in sorted((n for n in nodes if n['community'] == row.community),
                                                        key=lambda n: -(n['pagerank'] or 0))[:3]]
        communities.append(record)

    perturbation = {c: records(read(run, f'weight_perturbation_{c}').sort_values('primary_rank'), limit=50)
                    for c in composites}
    shares = read(run, 'membership_shares').set_index('hcp_id')
    non_anchored = movers[movers.mover_class != 'anchored'].sort_values('mover_score', ascending=False)
    stability = {
        'method_comparison': records(read(run, 'method_comparison')),
        'resolution_sweep': records(read(run, 'resolution_sweep')),
        'node_removal_leader': records(read(run, 'node_removal_leader')),
        'node_removal_global': records(read(run, 'node_removal_global')),
        'bootstrap_communities': records(read(run, 'bootstrap_communities')),
        'weight_perturbation': perturbation,
        'membership_shares': [{'id': h, 'name': str(movers.loc[h, 'display_name']).title(),
                               'mover_class': movers.loc[h, 'mover_class'],
                               'mover_score': _clean(movers.loc[h, 'mover_score']),
                               'publications': int(movers.loc[h, 'publications']),
                               'shares': [round(float(v), 4) for v in shares.loc[h]]}
                              for h in non_anchored.index],
        'mover_breadth_test': records(read(run, 'mover_breadth_test')),
        'mover_breadth_strata': records(read(run, 'mover_breadth_strata')),
        'mover_class_counts': {k: int(v) for k, v in movers.mover_class.value_counts().items()},
    }

    face = read(run, 'face_validity', index_col=0)
    face = face[face.label.isin(['guideline_panel', 'cart_industry_advisor'])]
    curves = read(run, 'roc_curves')
    roc = {}
    for (label, score), group in curves.groupby(['label', 'score'], sort=False):
        roc.setdefault(label, {})[score] = [[round(a, 4), round(b, 4)] for a, b in zip(group.fpr, group.tpr)]
    labels = {'face_validity': records(face), 'roc_curves': roc,
              'positives': {label: int(face[face.label == label].positives.iloc[0]) for label in face.label.unique()}}

    temporal_table = read(run, 'temporal_holdout')
    temporal_hcp = read(run, 'temporal_hcp').set_index('hcp_id')
    score_columns = [c for c in temporal_hcp.columns if c not in
                     ('display_name', 'pre_period_publications', 'guideline_panel')
                     and not c.startswith(('publications_', 'new_coauthors_', 'log_consulting'))]
    target_columns = [c for c in temporal_hcp.columns if c.startswith(('publications_', 'new_coauthors_', 'log_consulting'))]
    temporal = {'table': records(temporal_table), 'score_columns': score_columns, 'target_columns': target_columns,
                'hcps': [{'id': h, 'name': str(r.display_name).title(),
                          'pre_publications': int(r.pre_period_publications),
                          'guideline_panel': bool(r.guideline_panel),
                          **{c: _clean(r[c]) for c in score_columns + target_columns}}
                         for h, r in temporal_hcp.iterrows()],
                'cutoff_year': meta['config']['temporal']['cutoff_year']}

    sources = build_sources(root, hcps)
    return {
        'meta': {'run_id': meta['run_id'], 'created_at_utc': meta['created_at_utc'], 'built_at_utc': now(),
                 'inputs_sha256': meta['inputs_sha256'], 'description': meta['description'],
                 'composites': meta['config']['composites'], 'axes': meta['config']['axes'],
                 'resolution': meta['config']['community']['resolution'],
                 'seeds': meta['config']['community']['seeds'], 'library_versions': meta['library_versions'],
                 'summaries': meta['summaries'], 'hcps': len(nodes), 'edges': len(edges),
                 'tier_order': TIER_ORDER},
        'nodes': nodes, 'edges': edges, 'communities': communities, 'stability': stability,
        'labels': labels, 'temporal': temporal, 'sources': sources,
    }


def build_sources(root: Path, hcps: pd.DataFrame) -> dict:
    processed = root / 'Data' / 'processed'
    dataset = json.loads((processed / SNAPSHOT / 'dataset_metadata.json').read_text())
    utilization = json.loads((processed / '2026-10-15' / 'utilization_metadata.json').read_text())
    catalog_path = root / 'Data' / 'manifests' / 'source_catalog.json'
    catalog = json.loads(catalog_path.read_text())['sources'] if catalog_path.is_file() else []
    by_name = {s['source']: s for s in catalog}
    publications = pd.read_csv(processed / SNAPSHOT / 'publications.csv', dtype=str)
    payments = pd.read_csv(processed / SNAPSHOT / 'general_payments_2024.csv', dtype=str)
    social = pd.read_csv(processed / SNAPSHOT / 'social_profiles.csv', dtype=str)
    authorships = pd.read_csv(processed / SNAPSHOT / 'authorships.csv', dtype=str)
    excluded = publications[publications.analysis_include == '0']
    pmc = by_name.get('Europe PMC / PubMed MED', {})
    sources = [
        {'name': 'Europe PMC / PubMed MED', 'role': 'Topic corpus and coauthorship evidence',
         'url': pmc.get('documentation_url', SOURCE_DOCS['Europe PMC / PubMed MED']),
         'query': pmc.get('original_query', PUBLICATION_QUERY),
         'window': dataset['publication_window'],
         'observed': dataset['publication_metadata_observation_dates'],
         'counts': {'records': int(dataset['tables']['publications']),
                    'included': int((publications.analysis_include == '1').sum()),
                    'excluded': int(len(excluded)),
                    'authorship_links': int(dataset['tables']['authorships']),
                    'coauthor_edges': int(dataset['tables']['coauthor_edges'])},
         'notes': ['Exclusions are corrections, retractions, and the held-out 2023 ASTCT guideline.',
                   'Title must name lymphoma, DLBCL, or LBCL; CAR-T terms may appear in title or abstract.',
                   'Topic-query relevance is not individually adjudicated.']},
        {'name': 'CMS NPPES NPI Registry', 'role': 'Identity verification and registry location',
         'url': SOURCE_DOCS['CMS NPPES NPI Registry'], 'api_version': by_name.get('CMS NPPES NPI Registry', {}).get('api_version', '2.1'),
         'counts': {'hcps': int(dataset['target_hcps']), 'qualified_pool': int(dataset['unique_qualified_pool'])},
         'eligibility': {'npi_status': dataset['npi_status_required'], 'npi_type': dataset['npi_type_required'],
                         'country': dataset['npi_country_required'], 'taxonomies': dataset['allowed_taxonomies'],
                         'minimum_publications': int(dataset['qualification_minimum_publications'])},
         'identity_rule': str(hcps.identity_rule.iloc[0]),
         'notes': ['Registry practice locations may be stale and are not credentialing.']},
        {'name': 'CMS Open Payments', 'role': '2024 general payments from manufacturers',
         'url': SOURCE_DOCS['CMS Open Payments'],
         'dataset_id': by_name.get('CMS Open Payments', {}).get('dataset_id', 'e6b17c6a-2534-4207-a4a1-6746a14911ff'),
         'program_year': int(dataset['payment_program_year']), 'release_date': dataset['payment_source_release_date'],
         'counts': {'records': int(dataset['tables']['general_payments_2024']),
                    'hcps_with_payments': int(payments.hcp_id.nunique()),
                    'manufacturers': int(payments.manufacturer.nunique())},
         'notes': ['General payments only; research payments and ownership are not collected.',
                   'The site shows manufacturer counts and payment natures, never dollar amounts.',
                   'Payments are industry transfers of value, not prescribing or influence.']},
        {'name': 'CMS Medicare Physician & Other Practitioners by Provider and Service',
         'role': '2024 Medicare Part B service activity', 'url': SOURCE_DOCS['CMS Medicare Physician & Other Practitioners by Provider and Service'],
         'dataset_version_id': utilization['source_dataset_version_id'], 'release_date': utilization['source_release_date'],
         'collected': utilization['collection_snapshot_date'], 'service_year': int(utilization['service_year']),
         'counts': {'service_rows': int(utilization['tables']['cms_provider_service']),
                    'hcps_with_rows': int(utilization['hcp_coverage']['published_services_no_selected_codes']
                                          + utilization['hcp_coverage']['selected_codes_observed']),
                    'hcps_without_rows': int(utilization['hcp_coverage']['no_published_services']),
                    'hcps_with_selected_drug_codes': int(utilization['hcp_coverage']['selected_codes_observed'])},
         'notes': ['Not used in any community or composite score: only 7 HCPs have a selected drug code.',
                   'Absence of a published row is not evidence of no activity; small cells are suppressed.']},
        {'name': 'Public LinkedIn search listings', 'role': 'Public profile URLs only',
         'counts': {'profiles': int(len(social))},
         'notes': ['Found by public web search; no follower, post, or engagement metrics were collected.',
                   'Not used in any score.']},
        {'name': '2023 ASTCT DLBCL clinical practice recommendations', 'role': 'Held-out weak label',
         'url': f'https://pubmed.ncbi.nlm.nih.gov/{HOLDOUT}/', 'pmid': HOLDOUT,
         'counts': {'cohort_authors': int((authorships.pmid == HOLDOUT).sum())},
         'notes': ['Withheld from every feature and every edge at build time, so it is a clean label.']},
    ]
    tables = [
        {'table': 'hcps.csv', 'rows': int(dataset['tables']['hcps']), 'grain': 'one row per verified HCP (NPI)'},
        {'table': 'publications.csv', 'rows': int(dataset['tables']['publications']), 'grain': 'one row per PubMed record (PMID)'},
        {'table': 'authorships.csv', 'rows': int(dataset['tables']['authorships']), 'grain': 'one row per HCP × publication'},
        {'table': 'coauthor_edges.csv', 'rows': int(dataset['tables']['coauthor_edges']), 'grain': 'one row per undirected HCP pair with a shared paper'},
        {'table': 'general_payments_2024.csv', 'rows': int(dataset['tables']['general_payments_2024']), 'grain': 'one row per payment record'},
        {'table': 'cms_provider_service.csv', 'rows': int(utilization['tables']['cms_provider_service']), 'grain': 'one row per NPI × HCPCS × setting'},
        {'table': 'hcp_utilization_features.csv', 'rows': int(utilization['tables']['hcp_utilization_features']), 'grain': 'one row per HCP'},
    ]
    return {'sources': sources, 'tables': tables, 'cohort_snapshot_date': dataset['cohort_snapshot_date'],
            'utilization_snapshot_date': utilization['collection_snapshot_date'],
            'scope_limitations': dataset['scope_limitations'] + utilization.get('limitations', []),
            'legacy_pilot': 'A 2025 pilot cohort of 245 clinicians exists only in the local project; its curated seeds '
                            'are not part of this site.'}


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('run', help='run directory, e.g. models/kol_runs/b96b018dcf02')
    parser.add_argument('--out', default='docs/data/site.json')
    args = parser.parse_args(argv)
    run = Path(args.run) if Path(args.run).is_absolute() else ROOT / args.run
    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    bundle = build(run)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, separators=(',', ':'), ensure_ascii=False), encoding='utf-8')
    print(f'SITE {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} '
          f'{out.stat().st_size / 1e6:.2f} MB sha256 {sha256(out)[:12]}')
    return out


if __name__ == '__main__':
    main()
