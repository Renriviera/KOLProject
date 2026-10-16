from copy import deepcopy
from kolproject.identity import active_oncology, index_authors, supported_authorships
from kolproject.deduplicate import canonicalize, fuzzy_pairs

def record(npi='1234567890', first='Alice', middle='B', city='Boston'):
    return {'number': npi, 'enumeration_type': 'NPI-1',
            'basic': {'first_name': first, 'middle_name': middle, 'last_name': 'Example', 'status': 'A'},
            'addresses': [{'address_purpose': 'LOCATION', 'country_code': 'US', 'city': city}],
            'taxonomies': [{'code': '207RH0003X'}]}

def paper(pmid, city='Boston', given='Alice B', orcid='', duplicate=False):
    author = {'firstName': given, 'lastName': 'Example',
              'authorAffiliationDetailsList': {'authorAffiliation': [{'affiliation': 'University Hospital, ' + city}]}}
    if orcid:
        author['authorId'] = {'type': 'ORCID', 'value': orcid}
    return {'id': pmid, 'title': 'CAR-T in lymphoma', 'firstPublicationDate': '2024-01-01',
            'authorList': {'author': [author, deepcopy(author)] if duplicate else [author]}}

def test_inactive_and_wrong_taxonomy_do_not_qualify():
    r = record()
    assert active_oncology(r)
    r['basic']['status'] = 'D'
    assert not active_oncology(r)
    r['basic']['status'] = 'A'
    r['taxonomies'] = [{'code': '207R00000X'}]
    assert not active_oncology(r)

def test_city_anchored_orcid_can_link_moved_affiliation():
    corpus = [paper('1', orcid='0000-test'), paper('2', city='Seattle', orcid='0000-test')]
    links, withheld = supported_authorships(record(), [('Alice', 'Example')], index_authors(corpus))
    assert {a['pmid'] for a in links} == {'1', '2'}
    assert links[1]['match_rule'].endswith('anchored_orcid')
    assert not withheld

def test_middle_initial_conflict_is_rejected():
    links, _ = supported_authorships(record(), [('Alice', 'Example')], index_authors([paper('1', given='Alice C')]))
    assert not links

def test_duplicate_author_mentions_cannot_count_as_two_papers():
    links, withheld = supported_authorships(record(), [('Alice', 'Example')], index_authors([paper('1', duplicate=True)]))
    assert not links
    assert len(withheld) == 2

def test_secondary_practice_is_evidence_but_mailing_address_is_not():
    r = record(city='Seattle')
    r['addresses'].append({'address_purpose': 'MAILING', 'country_code': 'US', 'city': 'Boston'})
    idx = index_authors([paper('1')])
    assert not supported_authorships(r, [('Alice', 'Example')], idx)[0]
    r['practiceLocations'] = [{'address_purpose': 'LOCATION', 'country_code': 'US', 'city': 'Boston'}]
    assert len(supported_authorships(r, [('Alice', 'Example')], idx)[0]) == 1

def person(npi='1234567890', candidate='C1'):
    r = record(npi=npi)
    links, withheld = supported_authorships(r, [('Alice', 'Example')], index_authors([paper('1'), paper('2')]))
    return {'candidate_id': candidate, 'rank': 1, 'first': 'Alice', 'last': 'Example', 'status': 'qualifies',
            'source': {'source_url': 'https://example.test/npi', 'retrieved_at_utc': '2026-10-09T00:00:00Z'},
            'accepted': {'record': r, 'authorships': links, 'withheld': withheld, 'supported_papers': 2}}

def test_same_npi_aliases_merge_without_inflating_publications():
    cohort, aliases = canonicalize([person(candidate='C1'), person(candidate='C2')])
    assert len(cohort) == 1
    assert cohort[0]['accepted']['supported_papers'] == 2
    assert len(aliases) == 2

def test_same_name_different_npis_requires_review_not_automatic_merge():
    pairs = fuzzy_pairs([person(), person(npi='9876543210')])
    assert len(pairs) == 1
    assert pairs[0]['decision'].startswith('requires_manual_review')

def test_institution_evidence_requires_exact_npi_and_matching_institution(monkeypatch):
    import kolproject.identity as identity
    evidence = {'1234567890': [{'city': 'Boston', 'institution_pattern': 'University Hospital',
                               'source_url': 'https://example.test/institution',
                               'observed_at_utc': '2026-10-09T00:00:00Z'}]}
    monkeypatch.setattr(identity, 'verified_institution_evidence', lambda: evidence)
    idx = index_authors([paper('1')])
    links, _ = supported_authorships(record(city='Seattle'), [('Alice', 'Example')], idx)
    assert len(links) == 1
    assert links[0]['match_rule'].endswith('npi_linked_institution_city')
    assert not supported_authorships(record(npi='0000000000', city='Seattle'), [('Alice', 'Example')], idx)[0]

