"""Conservative publication-to-NPI matching; fuzzy similarity is review evidence."""
from __future__ import annotations

import collections
import re
from functools import lru_cache
from .common import norm, tokens, clean_affiliation, read_csv, config_input

TAXONOMIES = {'207RH0003X', '207RH0000X', '207RX0202X', '2080P0207X'}
HOLDOUT = '37419325'

@lru_cache(maxsize=1)
def verified_institution_evidence():
    path = config_input('affiliation_evidence.csv')
    result = collections.defaultdict(list)
    if path.exists():
        for row in read_csv(path):
            result[row['npi']].append(row)
    return result

def eligible(publication):
    types = publication.get('pubTypeList', {}).get('pubType', [])
    return (publication['id'] != HOLDOUT
            and '2017-01-01' <= publication.get('firstPublicationDate', '') <= '2025-12-31'
            and not re.search(r'^(retraction|correction|erratum|corrigendum)', publication.get('title', ''), re.I)
            and not any(t.lower() in {'retracted publication', 'retraction of publication', 'published erratum'} for t in types))

def author_affiliation(author):
    return ' ; '.join(x.get('affiliation', '') for x in author.get('authorAffiliationDetailsList', {}).get('authorAffiliation', []))

def city_text(value):
    return norm(value).replace('st.', 'st').replace('saint ', 'st ')

def active_oncology(record):
    return (record.get('enumeration_type') == 'NPI-1'
            and record.get('basic', {}).get('status') == 'A'
            and any(t.get('code') in TAXONOMIES for t in record.get('taxonomies', []))
            and any(a.get('address_purpose') == 'LOCATION' and a.get('country_code') == 'US' for a in record.get('addresses', [])))

def name_compatible(author, first, last, middle=''):
    if tokens(author.get('lastName', '')) != tokens(last):
        return False
    given = author.get('firstName', '').split()
    if not given or tokens(given[0]) != tokens(first):
        return False
    mid = tokens(middle)
    if len(given) > 1 and mid and tokens(given[1]) and tokens(given[1])[0][0] != mid[0][0]:
        return False
    return True

def index_authors(corpus):
    index = collections.defaultdict(list)
    for publication in corpus:
        authors = publication.get('authorList', {}).get('author', [])
        for position, author in enumerate(authors, 1):
            first = author.get('firstName', '').split()
            if first:
                index[(tuple(tokens(first[0])), tuple(tokens(author.get('lastName', ''))))].append((publication, author, position, len(authors)))
    return index

def supported_authorships(record, aliases, index):
    """Each link needs city evidence or an ORCID anchored by such evidence.

    An ORCID with contradictory mentions within a paper cannot rescue that paper.
    Identical duplicate indexed entries count once; conflicting entries are withheld.
    """
    locations = [a for a in record.get('addresses', []) + record.get('practiceLocations', []) if a.get('address_purpose') == 'LOCATION' and a.get('country_code') == 'US']
    cities = {city_text(a['city']) for a in locations}
    middle = record.get('basic', {}).get('middle_name', '')
    mentions = {}
    for first, last in aliases:
        for p, a, position, count in index.get((tuple(tokens(first)), tuple(tokens(last))), []):
            if not name_compatible(a, first, last, middle):
                continue
            mentions[(p['id'], position)] = (p, a, position, count)
    anchored = set()
    city_supported = set()
    institution_supported = {}
    for (pmid, position), (p, a, _, _) in mentions.items():
        af = author_affiliation(a)
        if len(af) > 700 or len(re.findall(r'\([A-Z][A-Z., ]+\)', af)) >= 2:
            continue
        if any(re.search(r'\b' + re.escape(city) + r'\b', city_text(af)) for city in cities):
            city_supported.add((pmid, position))
            if a.get('authorId', {}).get('type') == 'ORCID':
                anchored.add(a['authorId']['value'])
        for evidence in verified_institution_evidence().get(str(record['number']), []):
            if (re.search(r'\b' + re.escape(city_text(evidence['city'])) + r'\b', city_text(af))
                    and re.search(evidence['institution_pattern'], af, re.I)):
                institution_supported[(pmid, position)] = evidence
                if a.get('authorId', {}).get('type') == 'ORCID':
                    anchored.add(a['authorId']['value'])
    rows = []
    for (pmid, position), (p, a, _, count) in mentions.items():
        orcid = a.get('authorId', {}).get('value', '') if a.get('authorId', {}).get('type') == 'ORCID' else ''
        method = ('exact_name_middle_initial_city' if (pmid, position) in city_supported
                  else 'exact_name_middle_initial_npi_linked_institution_city' if (pmid, position) in institution_supported
                  else 'exact_name_middle_initial_anchored_orcid' if orcid in anchored else '')
        if method:
            rows.append({'npi': str(record['number']), 'pmid': pmid,
                         'author_name_as_indexed': a.get('fullName') or a.get('firstName', '') + ' ' + a.get('lastName', ''),
                         'first_name_as_indexed': a.get('firstName', ''), 'last_name_as_indexed': a.get('lastName', ''),
                         'author_position': position, 'author_count': count, 'orcid': orcid,
                         'affiliation_at_publication': clean_affiliation(author_affiliation(a)),
                         'publication_date': p.get('firstPublicationDate', ''),
                         'analysis_include': int(eligible(p)), 'match_rule': method,
                         'source_url': 'https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/',
                         'institution_identity_source_url': institution_supported.get((pmid, position), {}).get('source_url', ''),
                         'institution_identity_observed_at_utc': institution_supported.get((pmid, position), {}).get('observed_at_utc', '')})
    by_pmid = collections.defaultdict(list)
    for row in rows:
        by_pmid[row['pmid']].append(row)
    accepted, withheld = [], []
    for pmid, items in by_pmid.items():
        if len(items) > 1:
            for item in items:
                withheld.append({**item, 'reason': 'multiple_indexed_mentions_of_same_identity_in_one_paper'})
        else:
            accepted.extend(items)
    return accepted, withheld

