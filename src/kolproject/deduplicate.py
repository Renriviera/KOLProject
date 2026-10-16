"""Canonical NPI identity merging and exhaustive fuzzy-name pair review."""
from __future__ import annotations

import collections
import itertools
from rapidfuzz import fuzz
from .common import norm, tokens

def full_name(record):
    basic = record['basic']
    return ' '.join(basic.get(k, '').strip() for k in ['first_name', 'middle_name', 'last_name'] if basic.get(k, '').strip())

def canonicalize(results):
    """Merge candidate spellings only on identical verified Type-1 NPI."""
    by_npi = collections.defaultdict(list)
    for result in results:
        if result['status'] == 'qualifies':
            by_npi[str(result['accepted']['record']['number'])].append(result)
    people, aliases = [], []
    for npi, matches in by_npi.items():
        matches.sort(key=lambda r: (not r['candidate_id'].startswith('SEED_'), r.get('rank', 0), r['candidate_id']))
        first = matches[0]
        person = dict(first)
        person['accepted'] = dict(first['accepted'])
        links = {}
        for result in matches:
            aliases.append({'npi': npi, 'canonical_name': full_name(first['accepted']['record']),
                            'candidate_id': result['candidate_id'],
                            'candidate_name': result['first'] + ' ' + result['last'],
                            'deduplication_rule': 'exact_verified_type1_npi',
                            'npi_source_url': result['source']['source_url'],
                            'npi_retrieved_at_utc': result['source']['retrieved_at_utc']})
            for link in result['accepted']['authorships']:
                key = (link['pmid'], link['author_position'])
                links[key] = link
        grouped = collections.defaultdict(list)
        for link in links.values():
            grouped[link['pmid']].append(link)
        # Name aliases that expose conflicting indexed mentions cannot inflate counts.
        person['accepted']['authorships'] = [items[0] for items in grouped.values() if len(items) == 1]
        person['accepted']['supported_papers'] = sum(a['analysis_include'] for a in person['accepted']['authorships'])
        person['candidate_ids'] = [m['candidate_id'] for m in matches]
        person['is_legacy_seed'] = any(m['candidate_id'].startswith('SEED_') for m in matches)
        if person['accepted']['supported_papers'] >= 2:
            people.append(person)
    return people, aliases

def fuzzy_pairs(people, threshold=88.0, first_threshold=80.0):
    """Compare every pair; no fuzzy score alone authorizes a merge."""
    flagged = []
    for left, right in itertools.combinations(people, 2):
        a, b = left['accepted'], right['accepted']
        ra, rb = a['record'], b['record']
        ba, bb = ra['basic'], rb['basic']
        name_a = ' '.join(tokens(full_name(ra)))
        name_b = ' '.join(tokens(full_name(rb)))
        surname_equal = tokens(ba['last_name']) == tokens(bb['last_name'])
        given_similarity = fuzz.ratio(' '.join(tokens(ba['first_name'])), ' '.join(tokens(bb['first_name'])))
        similarity = max(fuzz.ratio(name_a, name_b), fuzz.token_sort_ratio(name_a, name_b))
        orcids_a = {x['orcid'] for x in a['authorships'] if x['orcid']}
        orcids_b = {x['orcid'] for x in b['authorships'] if x['orcid']}
        shared_orcids = orcids_a & orcids_b
        if similarity < threshold and not (surname_equal and given_similarity >= first_threshold) and not shared_orcids:
            continue
        both_on_same_paper = []
        for x in a['authorships']:
            for y in b['authorships']:
                if x['pmid'] == y['pmid'] and x['author_position'] != y['author_position']:
                    both_on_same_paper.append(x['pmid'])
        if shared_orcids:
            decision = 'requires_manual_review_shared_orcid'
        elif surname_equal and tokens(ba['first_name']) == tokens(bb['first_name']):
            decision = 'requires_manual_review_same_given_and_family_name'
        elif both_on_same_paper:
            decision = 'retain_distinct_verified_npis_and_separate_coauthor_positions'
        elif surname_equal and given_similarity >= first_threshold:
            decision = 'requires_manual_review_possible_given_name_variant'
        elif tokens(ba['first_name']) != tokens(bb['first_name']) or not surname_equal:
            decision = 'retain_distinct_verified_npis_and_distinct_registered_names'
        else:
            decision = 'requires_manual_review'
        flagged.append({'npi_a': str(ra['number']), 'name_a': full_name(ra),
                        'npi_b': str(rb['number']), 'name_b': full_name(rb),
                        'name_similarity_0_100': round(similarity, 2),
                        'same_surname': surname_equal, 'given_name_similarity_0_100': round(given_similarity, 2),
                        'shared_orcids': ';'.join(sorted(shared_orcids)),
                        'distinct_positions_same_paper_pmids': ';'.join(sorted(set(both_on_same_paper))),
                        'decision': decision,
                        'source_url_a': 'https://npiregistry.cms.hhs.gov/provider-view/' + str(ra['number']),
                        'source_url_b': 'https://npiregistry.cms.hhs.gov/provider-view/' + str(rb['number'])})
    return flagged

