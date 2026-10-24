"""Build the versioned cohort and auditable, normalized analytical tables."""
from __future__ import annotations

import collections
import hashlib
import itertools
import json
import re
from datetime import datetime, date
import networkx as nx
import pandas as pd

from .common import ROOT, RAW, INTERIM, PROCESSED, SNAPSHOT, load, dump, write_csv, read_csv, now, plain, config_input
from .identity import HOLDOUT, eligible, TAXONOMIES
from .deduplicate import canonicalize, fuzzy_pairs, full_name
from .corpus import get_corpus, publication_observation

def all_results():
    return [load(p) for p in sorted((INTERIM / 'screening').glob('*.json'))]

def select_people(target=500, fuzzy_threshold=88, first_threshold=80):
    results = all_results()
    people, aliases = canonicalize(results)
    legacy = load(ROOT / 'Data' / 'raw' / 'legacy_2026-10-08' / 'resolved.json')
    legacy_map = {str(h['npi']): h for h in legacy}
    people.sort(key=lambda p: (str(p['accepted']['record']['number']) not in legacy_map,
                               -p['accepted']['supported_papers'],
                               full_name(p['accepted']['record']), str(p['accepted']['record']['number'])))
    for p in people:
        npi = str(p['accepted']['record']['number'])
        p['legacy_hcp_id'] = legacy_map.get(npi, {}).get('hcp_id', '')
    reviews = fuzzy_pairs(people, fuzzy_threshold, first_threshold)
    review_path = config_input('identity_review.csv')
    decisions = {tuple(sorted([r['npi_a'], r['npi_b']])): r for r in read_csv(review_path)} if review_path.exists() else {}
    for r in reviews:
        key = tuple(sorted([r['npi_a'], r['npi_b']]))
        if key in decisions:
            decision = decisions[key]
            r.update(decision=decision['decision'], review_reason=decision['reason'],
                     review_source_url=decision['source_url'], reviewed_at_utc=decision['reviewed_at_utc'])
    write_csv(PROCESSED / 'candidate_aliases.csv', aliases)
    write_csv(PROCESSED / 'fuzzy_duplicate_review.csv', reviews,
              list(dict.fromkeys(k for r in reviews for k in r)) if reviews else ['npi_a', 'npi_b', 'decision'])
    unresolved = [r for r in reviews if r['decision'].startswith('requires_manual_review')]
    print('QUALIFIED_UNIQUE_NPIS', len(people), 'FUZZY_PAIRS', len(reviews), 'UNRESOLVED_PAIRS', len(unresolved), flush=True)
    dump(INTERIM / 'all_qualified_people.json', people)
    if unresolved:
        raise ValueError(f'{len(unresolved)} potential duplicate pairs require resolution; see fuzzy_duplicate_review.csv')
    if len(people) < target:
        raise ValueError(f'Only {len(people)} independently qualified people; target {target}. Do not weaken eligibility.')
    selected = people[:target]
    reserve = people[target:]
    dump(INTERIM / 'selected_people.json', selected)
    write_csv(PROCESSED / 'qualified_reserve.csv', [
        {'npi': str(p['accepted']['record']['number']), 'display_name': full_name(p['accepted']['record']),
         'supported_publications': p['accepted']['supported_papers'], 'reason_not_selected': 'deterministic_target_cap',
         'npi_retrieved_at_utc': p['source']['retrieved_at_utc']} for p in reserve],
        ['npi', 'display_name', 'supported_publications', 'reason_not_selected', 'npi_retrieved_at_utc'])
    return selected

def parse_payment_date(value):
    return datetime.strptime(value, '%m/%d/%Y').date().isoformat() if value else ''

def build_dataset(target=500, weights=None):
    built_at = now()
    people = load(INTERIM / 'selected_people.json')
    assert len(people) == target
    corpus = get_corpus()
    pmap = {p['id']: p for p in corpus}
    publications = []
    for p in corpus:
        observation = publication_observation(p)
        include = eligible(p)
        reason = '' if include else ('guideline_validation_holdout' if p['id'] == HOLDOUT else 'correction_retraction_or_outside_window')
        title, abstract = plain(p.get('title', '')), plain(p.get('abstractText', ''))
        publications.append({
            'pmid': p['id'], 'doi': p.get('doi', ''), 'title': title,
            'first_publication_date': p['firstPublicationDate'], 'publication_year': int(p['firstPublicationDate'][:4]),
            'journal': p.get('journalInfo', {}).get('journal', {}).get('title', ''),
            'author_count': len(p.get('authorList', {}).get('author', [])),
            'citation_count_epmc': p.get('citedByCount', 0), 'citation_observed_on': observation['retrieved_on'],
            'metadata_retrieved_on': observation['retrieved_on'], 'metadata_retrieved_at_utc': observation['retrieved_at_utc'],
            'retrieval_time_precision': observation['retrieval_time_precision'], 'publication_types': ';'.join(p.get('pubTypeList', {}).get('pubType', [])),
            'analysis_include': int(include), 'exclusion_reason': reason,
            'access_keyword_flag': int(bool(re.search(r'\b(access|disparit\w*|referr\w*|rural|socioeconomic|geographic\w*|out.of.pocket|insurance|affordab\w*|travel|financial toxicity|barriers)\b', title + ' ' + abstract, re.I))),
            'source_url': f'https://europepmc.org/article/MED/{p["id"]}',
            'pubmed_url': f'https://pubmed.ncbi.nlm.nih.gov/{p["id"]}/',
            'source_snapshot_id': observation['source_id'], 'query_family': observation['query_family'],
        })
    pubdf = pd.DataFrame(publications)
    pubdf['citation_year_percentile'] = None
    valid = pubdf['analysis_include'].eq(1)
    for year, indices in pubdf[valid].groupby('publication_year').groups.items():
        x = pubdf.loc[indices, 'citation_count_epmc']
        pubdf.loc[indices, 'citation_year_percentile'] = (x.rank(method='average') - 0.5) / len(x)
    publications = pubdf.to_dict('records')
    public_map = {p['pmid']: p for p in publications}
    hcps, authorships, withheld, payrows, payment_status = [], [], [], [], []
    payments_by_npi = {}
    for p in people:
        npi = str(p['accepted']['record']['number'])
        path = INTERIM / 'payments' / (npi + '.json')
        payments_by_npi[npi] = load(path) if path.exists() else {'status': 'not_collected', 'records': [], 'sources': []}
    npi_to_id = {}
    for person in people:
        record = person['accepted']['record']
        npi = str(record['number'])
        hcp_id = 'NPI' + npi
        npi_to_id[npi] = hcp_id
        basic = record['basic']
        location = next(a for a in record['addresses'] if a.get('address_purpose') == 'LOCATION' and a.get('country_code') == 'US')
        links = [dict(a) for a in person['accepted']['authorships']]
        eligible_links = [a for a in links if a['analysis_include']]
        latest = max(eligible_links, key=lambda a: (a['publication_date'], a['pmid']))
        for a in links:
            a.update(hcp_id=hcp_id, metadata_retrieved_on=public_map[a['pmid']]['metadata_retrieved_on'],
                     metadata_retrieved_at_utc=public_map[a['pmid']]['metadata_retrieved_at_utc'],
                     npi_retrieved_at_utc=person['source']['retrieved_at_utc'],
                     matched_at_utc=built_at, identity_source_url=person['source']['source_url'])
        authorships.extend(links)
        withheld.extend({**a, 'hcp_id': hcp_id, 'reviewed_at_utc': built_at} for a in person['accepted']['withheld'])
        payments = payments_by_npi[npi]
        payment_status.append({'hcp_id': hcp_id, 'npi': npi, 'status': payments['status'],
                               'program_year': 2024, 'source_release_date': '2026-06-30',
                               'retrieved_at_utc': payments.get('retrieved_at_utc', ''),
                               'record_count': payments.get('count', ''), 'error': payments.get('error', '')})
        for r in payments['records']:
            products = [r.get(f'name_of_drug_or_biological_or_device_or_medical_supply_{i}', '') or '' for i in range(1, 6)]
            payrows.append({'hcp_id': hcp_id, 'npi': npi, 'record_id': str(r['record_id']),
                            'cms_profile_id': str(r['covered_recipient_profile_id']),
                            'program_year': int(r['program_year']), 'payment_date': parse_payment_date(r['date_of_payment']),
                            'amount_usd': float(r['total_amount_of_payment_usdollars']),
                            'number_of_payments': int(r['number_of_payments_included_in_total_amount']),
                            'manufacturer': r['applicable_manufacturer_or_applicable_gpo_making_payment_name'],
                            'payment_nature': r['nature_of_payment_or_transfer_of_value'],
                            'payment_form': r['form_of_payment_or_transfer_of_value'],
                            'products': ';'.join(x for x in products if x),
                            'lymphoma_cart_product_mentioned': int(any(re.search('yescarta|kymriah|breyanzi|tecartus|axicabtagene|tisagenlecleucel|lisocabtagene|brexucabtagene', x, re.I) for x in products)),
                            'payment_publication_date': parse_payment_date(r['payment_publication_date']),
                            'source_release_date': '2026-06-30', 'retrieved_at_utc': payments.get('retrieved_at_utc', ''),
                            'source_url': f'https://openpaymentsdata.cms.gov/payment/2024/General/{r["record_id"]}'})
        hcps.append({'hcp_id': hcp_id, 'npi': npi, 'legacy_hcp_id': person.get('legacy_hcp_id', ''),
                     'display_name': full_name(record), 'npi_first_name': basic.get('first_name', ''),
                     'npi_middle_name': basic.get('middle_name', ''), 'npi_last_name': basic.get('last_name', ''),
                     'credential': basic.get('credential', ''), 'npi_type': record['enumeration_type'],
                     'npi_status': basic['status'], 'npi_country': location['country_code'],
                     'npi_city': location['city'], 'npi_state': location.get('state', ''),
                     'npi_taxonomy_codes': ';'.join(sorted({t['code'] for t in record['taxonomies']})),
                     'qualifying_taxonomy_codes': ';'.join(sorted({t['code'] for t in record['taxonomies']} & TAXONOMIES)),
                     'npi_specialties': ';'.join(t.get('desc', '') for t in record['taxonomies']),
                     'npi_enumeration_date': basic.get('enumeration_date', ''),
                     'npi_last_updated_date': basic.get('last_updated', ''),
                     'npi_retrieved_at_utc': person['source']['retrieved_at_utc'],
                     'npi_source_id': person['source']['source_id'], 'npi_source_url': person['source']['source_url'],
                     'publications_2017_2025': len(eligible_links),
                     'supporting_pmids': ';'.join(sorted(a['pmid'] for a in eligible_links)),
                     'orcid_ids': ';'.join(sorted({a['orcid'] for a in eligible_links if a['orcid']})),
                     'latest_publication_affiliation': latest['affiliation_at_publication'],
                     'affiliation_evidence_date': latest['publication_date'], 'affiliation_evidence_pmid': latest['pmid'],
                     'publication_metadata_first_observed_on': min(public_map[a['pmid']]['metadata_retrieved_on'] for a in eligible_links),
                     'publication_metadata_last_observed_on': max(public_map[a['pmid']]['metadata_retrieved_on'] for a in eligible_links),
                     'citation_first_observed_on': min(public_map[a['pmid']]['citation_observed_on'] for a in eligible_links),
                     'citation_last_observed_on': max(public_map[a['pmid']]['citation_observed_on'] for a in eligible_links),
                     'cohort_snapshot_date': SNAPSHOT, 'row_assembled_at_utc': built_at,
                     'identity_rule': 'exact_name_compatible_middle_nppes_or_verified_institution_city_or_anchored_ORCID',
                     'selection_rule': 'retain_eligible_legacy_then_supported_publication_count_name_npi',
                     'patient_copay_usd': '', 'patient_copay_status': 'not_available_from_collected_sources',
                     'social_followers': '', 'social_metrics_status': 'not_collected'})

    by_paper = collections.defaultdict(list)
    by_hcp = collections.defaultdict(list)
    for a in authorships:
        if a['analysis_include']:
            by_paper[a['pmid']].append(a['hcp_id'])
            by_hcp[a['hcp_id']].append(a)
    edge_map = collections.defaultdict(list)
    for pmid, ids in by_paper.items():
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            edge_map[a, b].append(pmid)
    graph = nx.Graph()
    graph.add_nodes_from(npi_to_id.values())
    edges = []
    for (a, b), pmids in sorted(edge_map.items()):
        weight = sum(1 / max(public_map[p]['author_count'] - 1, 1) for p in pmids)
        graph.add_edge(a, b, weight=weight, distance=1 / weight)
        edges.append({'hcp_id_a': a, 'hcp_id_b': b, 'shared_publications': len(pmids),
                      'fractional_weight': weight, 'evidence_pmids': ';'.join(sorted(pmids)),
                      'first_shared_publication_date': min(public_map[p]['first_publication_date'] for p in pmids),
                      'last_shared_publication_date': max(public_map[p]['first_publication_date'] for p in pmids),
                      'edge_type': 'observed_coauthorship', 'computed_at_utc': built_at,
                      'evidence_metadata_first_observed_on': min(public_map[p]['metadata_retrieved_on'] for p in pmids),
                      'evidence_metadata_last_observed_on': max(public_map[p]['metadata_retrieved_on'] for p in pmids), 'is_synthetic': False})
    # NetworkX's NumPy implementation avoids introducing SciPy only for PageRank.
    import numpy as np
    nodes = list(graph.nodes())
    matrix = nx.to_numpy_array(graph, nodelist=nodes, weight='weight')
    sums = matrix.sum(axis=1)
    transition = np.divide(matrix, sums[:, None], out=np.zeros_like(matrix), where=sums[:, None] != 0)
    transition[sums == 0] = 1 / len(nodes)
    vector = np.full(len(nodes), 1 / len(nodes))
    for _ in range(1000):
        updated = 0.15 / len(nodes) + 0.85 * (vector @ transition)
        if np.abs(updated - vector).sum() < 1e-12:
            vector = updated
            break
        vector = updated
    pagerank = dict(zip(nodes, vector))
    betweenness = nx.betweenness_centrality(graph, weight='distance', normalized=True)
    communities = list(nx.community.greedy_modularity_communities(graph, weight='weight'))
    communities.sort(key=lambda group: (-len(group), min(group)))
    community = {node: i + 1 for i, group in enumerate(communities) for node in group}
    pay_by_hcp = collections.defaultdict(list)
    for row in payrows:
        pay_by_hcp[row['hcp_id']].append(row)
    for h in hcps:
        hcp_id = h['hcp_id']
        links = by_hcp[hcp_id]
        papers = [public_map[a['pmid']] for a in links]
        rr = pay_by_hcp[hcp_id]
        complete = payments_by_npi[h['npi']]['status'] == 'complete'
        h.update(recent_publications_2023_2025=sum(p['publication_year'] >= 2023 for p in papers),
                 first_or_last_author_publications=sum(a['author_position'] in [1, a['author_count']] for a in links),
                 fractional_publications=sum(1 / a['author_count'] for a in links),
                 citations_in_topic_corpus=sum(p['citation_count_epmc'] for p in papers),
                 mean_citation_year_percentile=sum(p['citation_year_percentile'] for p in papers) / len(papers),
                 access_keyword_publications=sum(p['access_keyword_flag'] for p in papers),
                 coauthor_degree=graph.degree(hcp_id), coauthor_fractional_strength=graph.degree(hcp_id, weight='weight'),
                 pagerank=float(pagerank[hcp_id]), weighted_betweenness=betweenness[hcp_id], community_id=community[hcp_id],
                 payment_query_status=payments_by_npi[h['npi']]['status'],
                 payment_retrieved_at_utc=payments_by_npi[h['npi']].get('retrieved_at_utc', ''),
                 payment_program_year=2024, payment_source_release_date='2026-06-30',
                 general_payment_rows_2024=len(rr) if complete else '',
                 general_payments_usd_2024=round(sum(r['amount_usd'] for r in rr), 2) if complete else '',
                 consulting_usd_2024=round(sum(r['amount_usd'] for r in rr if r['payment_nature'] == 'Consulting Fee'), 2) if complete else '',
                 cart_product_mentioned_usd_2024=round(sum(r['amount_usd'] for r in rr if r['lymphoma_cart_product_mentioned']), 2) if complete else '')
    df = pd.DataFrame(hcps)
    weights = weights or {'pagerank': .4, 'mean_citation_year_percentile': .3, 'recent_publications_2023_2025': .2, 'weighted_betweenness': .1}
    df['exploratory_score'] = 0.0
    for feature, weight in weights.items():
        percentile = (df[feature].rank(method='average') - .5) / len(df)
        df[feature + '_cohort_percentile'] = percentile
        df['exploratory_score'] += 100 * weight * percentile
    df = df.sort_values(['exploratory_score', 'hcp_id'], ascending=[False, True])
    df['exploratory_rank'] = range(1, len(df) + 1)
    hcps = df.sort_values('hcp_id').to_dict('records')
    screen_rows = []
    for r in all_results():
        screen_rows.append({'candidate_id': r['candidate_id'], 'candidate_name': r['first'] + ' ' + r['last'],
                            'raw_topic_publications': r.get('raw_papers', ''), 'decision': r['status'],
                            'matched_npi': str(r['accepted']['record']['number']) if r.get('accepted') else '',
                            'supported_publications': r.get('accepted', {}).get('supported_papers', ''),
                            'npi_search_url': r['npi_search_url'],
                            'npi_retrieved_at_utc': r.get('source', {}).get('retrieved_at_utc', ''),
                            'screened_at_utc': r['screened_at_utc'], 'error': r.get('error', '')})
    tables = {'hcps': hcps, 'publications': publications, 'authorships': authorships,
              'coauthor_edges': edges, 'general_payments_2024': payrows, 'payment_query_status': payment_status,
              'candidate_screening': screen_rows, 'withheld_authorships': withheld}
    for name, rows in tables.items():
        write_csv(PROCESSED / (name + '.csv'), rows, None if rows else ['hcp_id', 'pmid', 'reason'])
    write_csv(PROCESSED / 'legacy_id_crosswalk.csv', [{'legacy_hcp_id': h['legacy_hcp_id'], 'hcp_id': h['hcp_id'], 'npi': h['npi']} for h in hcps if h['legacy_hcp_id']])
    legacy_hcps = read_csv(ROOT / 'Data/legacy/cohort_245/data/hcps.csv')
    selected_map = {h['npi']: h for h in hcps}
    evidence_map = {}
    for result in all_results():
        for match in result.get('matches', []):
            evidence_map[str(match['record']['number'])] = (result, match)
    changes = []
    for old in legacy_hcps:
        npi = old['npi']
        selected = selected_map.get(npi)
        match = evidence_map.get(npi)
        if selected:
            reason = 'retained_and_revalidated'
        elif not match:
            reason = 'no_active_qualifying_taxonomy_identity_in_current_response'
        elif match[1]['supported_papers'] < 2:
            reason = 'fewer_than_two_supported_papers_after_uniform_identity_check'
        else:
            reason = 'qualified_but_outside_target_cap'
        changes.append({'legacy_hcp_id': old['hcp_id'], 'legacy_name': old['display_name'], 'npi': npi,
                        'retained_in_500': bool(selected), 'new_hcp_id': selected['hcp_id'] if selected else '',
                        'decision_reason': reason, 'reviewed_at_utc': built_at})
    write_csv(PROCESSED / 'legacy_cohort_changes.csv', changes)
    socials = read_csv(ROOT / 'Data' / 'legacy' / 'cohort_245' / 'data' / 'social_profiles.csv')
    legacy_to_hcp = {h['legacy_hcp_id']: h['hcp_id'] for h in hcps if h['legacy_hcp_id']}
    social_rows = [{**r, 'hcp_id': legacy_to_hcp[r['hcp_id']], 'metadata_observed_on': r['checked_date'], 'snapshot_date': SNAPSHOT} for r in socials if r['hcp_id'] in legacy_to_hcp]
    write_csv(PROCESSED / 'social_profiles.csv', social_rows)
    dump(PROCESSED / 'dataset_metadata.json', {
        'dataset_version': '0.2.0', 'cohort_snapshot_date': SNAPSHOT, 'built_at_utc': built_at,
        'user_timezone': 'America/New_York', 'publication_window': ['2017-01-01', '2025-12-31'],
        'publication_metadata_observation_dates': sorted({p['metadata_retrieved_on'] for p in publications}),
        'publication_timestamp_precision': 'original: day; supplemental API requests: second',
        'qualification_minimum_publications': 2, 'allowed_taxonomies': sorted(TAXONOMIES),
        'npi_status_required': 'A', 'npi_type_required': 'NPI-1', 'npi_country_required': 'US',
        'target_hcps': target, 'unique_qualified_pool': len(load(INTERIM / 'all_qualified_people.json')),
        'payment_program_year': 2024, 'payment_source_release_date': '2026-06-30',
        'ranking_weights': weights, 'holdout_pmid': HOLDOUT,
        'tables': {name: len(rows) for name, rows in tables.items()},
        'scope_limitations': ['topic-query relevance is not individually adjudicated', 'registry active status is not proof of current clinical practice', 'no patient-level copays, treatment volumes or actual referrals', 'citation counts were observed in 2026 and cannot serve as historical 2025 features'],
    })
    print('BUILT', len(hcps), 'HCPS', len(authorships), 'AUTHORSHIPS', len(edges), 'EDGES', len(payrows), 'PAYMENTS', flush=True)
    return tables
