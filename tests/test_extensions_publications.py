"""Identity grading, de-duplication and pair rules of the publication extension, and check() on a small tree."""
from kolproject.common import write_csv
from kolproject.extensions import publications as m

A, B, C, D = '1000000001', '1000000002', '1000000003', '1000000004'
PLACE = ('ZEPHYR VALE MEDICAL CENTER', '990001', 'OH', m.distinctive('ZEPHYR VALE MEDICAL CENTER'), False)


def test_name_key_needs_a_given_name_not_an_initial():
    assert m.name_key('Quill R', 'Ostrander-Bey') == (('quill',), ('ostrander', 'bey'))
    assert m.name_key('Q R', 'Ostrander') is None and m.name_key('', 'Ostrander') is None


def test_distinctive_drops_generic_hospital_words():
    assert m.distinctive('MEMORIAL HOSPITAL INC') is None
    need, strong, phrase = m.distinctive('Zephyr Vale Medical Center')
    assert need == {'zephyr', 'vale'} and strong == 2 and phrase == ''
    assert m.distinctive('OHIO ONCOLOGY PA')[2] == 'ohio oncology'   # state words only: phrase needed
    group = ('OHIO ONCOLOGY PA', '', 'OH', m.distinctive('OHIO ONCOLOGY PA'), False)
    assert m.institution_hit('Division of Oncology, Ohio Coastal University, Duneport, OH', [group]) is None
    assert m.institution_hit('Ohio Oncology, Duneport, OH', [group])
    city_group = ('DUNEPORT GENERAL MEDICAL GROUP INC', '', 'OH', m.distinctive('DUNEPORT GENERAL MEDICAL GROUP INC'), False)
    assert m.distinctive('DUNEPORT GENERAL MEDICAL GROUP INC')[2] == 'duneport general'
    assert m.institution_hit('Quillfield Cancer Center, Duneport, Ohio', [city_group]) is None   # a city is not an institution
    assert m.institution_hit('Kestrelwood Clinic, Duneport, OH', [('KESTRELWOOD CLINIC HOSPITAL', '', 'OH',
                                                                   m.distinctive('KESTRELWOOD CLINIC HOSPITAL'), False)])


def test_institution_and_city_evidence():
    aff = 'Division of Hematology, Zephyr Vale Medical Center, Duneport, OH, USA.'
    assert m.institution_hit(aff, [PLACE]) == ('ZEPHYR VALE MEDICAL CENTER', '990001', False)
    assert m.institution_hit('Zephyr Institute, Duneport', [PLACE]) is None
    one_word = ('KESTRELWOOD HOSPITAL', '', 'OH', m.distinctive('KESTRELWOOD HOSPITAL'), False)
    assert m.institution_hit('Kestrelwood Hospital, Duneport, Ohio', [one_word])
    assert m.institution_hit('Kestrelwood Hospital, Duneport', [one_word]) is None   # one strong word needs the state
    state_word = ('UNIVERSITY OF OHIO', '', 'OH', m.distinctive('UNIVERSITY OF OHIO'), False)
    assert m.institution_hit('University of Ohio, Duneport, Ohio', [state_word]) is None   # strong word is the state
    assert m.institution_hit('University of Ohio, Duneport, OH', [state_word])
    assert m.distinctive('Lymphoma Group of Memorial') is None
    sister = (*PLACE[:4], True)   # the words fit several hospitals: the state must be named
    assert m.institution_hit('Zephyr Vale Medical Center, Duneport', [sister]) is None
    assert m.institution_hit('Zephyr Vale Medical Center, Duneport, Ohio', [sister])[2] is True
    assert m.city_hit(aff, [('DUNEPORT', 'OH')]) == ('DUNEPORT', 'OH')
    assert m.city_hit('Duneport, Indiana', [('DUNEPORT', 'OH')]) is None


def test_grade_tiers_and_ambiguity():
    inst, city = {'institution': ('X', '1'), 'city': None}, {'institution': None, 'city': ('Y', 'OH')}
    assert m.grade([A], {A: inst}, 'aff') == (A, 'A', 'name_institution')
    assert m.grade([A], {A: city}, 'aff') == (A, 'B', 'name_city_state_unique')
    assert m.grade([A], {}, 'Duneport, OH, USA') == (A, 'C', 'name_only_unique')
    assert m.grade([A], {}, 'Faraway Institute, Japan')[0] is None
    assert m.grade([A], {}, 'Gulfmouth, TX, USA', {A: {'OH'}})[2] == 'affiliation_state_disagrees'
    assert m.grade([A], {}, 'Duneport, Ohio', {A: {'OH'}})[1] == 'C'
    assert m.grade([A, B], {A: inst}, 'aff') == (A, 'A', 'name_institution_disambiguated')
    assert m.grade([A, B], {A: city}, 'aff')[2] == 'ambiguous_name_multiple_universe_npis'
    assert m.grade([A, B], {A: inst, B: city}, 'aff')[0] is None


def test_multi_site_counts_hospitals_and_groups_sharing_the_words():
    zephyr, kestrel = m.distinctive('Zephyr Vale Hospital')[0], m.distinctive('Kestrelwood Clinic')[0]
    flags = m.multi_site({'ccn:1': {zephyr}, 'ccn:2': {m.distinctive('Zephyr Vale North Campus')[0]},
                          'group:9': {kestrel}})
    assert flags[zephyr] is True and flags[kestrel] is False


def test_hold_back_tier_c_and_namesakes_without_institution():
    row = lambda tier, rule, onc: {'confidence_tier': tier, 'match_rule': rule, 'nppes_hemonc_name_matches_national': onc}
    tracked, review = m.hold_back([row('A', 'name_institution', 3), row('B', 'name_city_state_unique', 2),
                                   row('A', 'name_orcid_anchored', 2), row('C', 'name_only_unique', 1),
                                   row('B', 'name_city_state_unique', 1)])
    assert [r['match_rule'] for r in tracked] == ['name_institution', 'name_city_state_unique']
    assert [r['review_reason'] for r in review] == ['oncologist_namesake_nationally_without_institution_evidence'] * 2 + ['tier_c_name_only']


def test_prior_status_and_core_pairs_on_new_papers():
    assert m.prior_status((A, B), {(A, B)}, set()) == 'in_coauthor_edges_2026_10_09'
    assert m.prior_status((A, C), set(), {(A, C)}) == 'in_universe_coauthor_edges'
    assert m.prior_status((B, C), set(), set()) == 'new_pair'
    rows = m.edges([{'npi': A, 'pmid': '5'}, {'npi': B, 'pmid': '5'}], {'5': '2'}, {'5': '2026-01-01'},
                   {'5': '2026-11-20'}, core={A, B}, core_pairs=True)
    assert rows[0]['pair_type'] == 'core_core' and rows[0]['fractional_weight'] == '1.0'


def slot_row(npi, pmid, position, tier, orcid=''):
    return {'npi': npi, 'pmid': pmid, 'author_position': position, 'orcid': orcid, 'confidence_tier': tier}


def test_orcid_anchor_and_repeat_withholding():
    links = [slot_row(A, '1', 1, 'A', 'X-1'), slot_row(B, '2', 1, 'B', 'X-1'), slot_row(C, '3', 2, 'B', 'X-2'),
             slot_row(D, '4', 1, 'C', 'X-3')]
    assert m.anchor_orcids(links) == {'X-2': C}   # X-1 is on two NPIs, X-3 only on a tier C link
    kept, withheld = m.withhold_repeats([slot_row(A, '1', 1, 'A'), slot_row(A, '1', 3, 'B'), slot_row(B, '1', 2, 'A')])
    assert [r['npi'] for r in kept] == [B] and len(withheld) == 2


def test_new_papers_dedup_on_pmid_doi_and_title():
    held = ({'11'}, {'10.1/held'}, {m.norm_title('Held Paper Title.')})
    records = [{'id': '11', 'source': 'MED', 'doi': '', 'title': 'x'},
               {'id': '12', 'source': 'MED', 'doi': 'https://doi.org/10.1/HELD', 'title': 'y'},
               {'id': '13', 'source': 'MED', 'doi': '', 'title': 'Held paper title'},
               {'id': '14', 'source': 'MED', 'doi': '10.1/new', 'title': 'New paper'},
               {'id': '15', 'source': 'MED', 'doi': '10.1/NEW', 'title': 'Another'},
               {'id': '16', 'source': 'PPR', 'doi': '', 'title': 'Preprint'}]
    kept, audit = m.new_papers(records, held)
    assert [p['id'] for p in kept] == ['14']
    assert (audit['dropped_pmid_held'], audit['dropped_doi_held'], audit['dropped_title_held'],
            audit['dropped_internal_duplicate'], audit['dropped_not_medline']) == (1, 1, 1, 1, 1)


def test_edges_skip_core_pairs_and_weight_by_author_count():
    links = [{'npi': n, 'pmid': p} for n, p in [(A, '1'), (B, '1'), (C, '1'), (A, '2'), (C, '2')]]
    rows = m.edges(links, {'1': '5', '2': '2'}, {'1': '2020-01-01', '2': '2021-06-01'},
                   {'1': '2026-10-08', '2': '2026-10-08'}, core={A, B})
    pairs = {(r['npi_a'], r['npi_b']): r for r in rows}
    assert set(pairs) == {(A, C), (B, C)}
    assert pairs[A, C]['shared_publications'] == 2 and abs(float(pairs[A, C]['fractional_weight']) - 1.25) < 1e-12
    assert pairs[A, C]['evidence_pmids'] == '1;2' and pairs[A, C]['last_shared_publication_date'] == '2021-06-01'
    assert pairs[B, C]['pair_type'] == 'core_universe'


def tree(tmp_path, broken=False):
    """A tiny processed tree with invented people and papers that passes check(), or breaks one rule."""
    P = tmp_path / 'processed'
    write_csv(P / '2026-10-24' / 'hcp_divisions.csv',
              [{'npi': A, 'is_core': '1', 'division': 'core', 'first_name': 'Quill', 'last_name': 'Ostrander'},
               {'npi': B, 'is_core': '0', 'division': 'affiliate_A', 'first_name': 'Mirela', 'last_name': 'Vantongeren'},
               {'npi': C, 'is_core': '0', 'division': 'referring_proxy', 'first_name': 'Teodor', 'last_name': 'Halvorsk'}])
    write_csv(P / '2026-10-09' / 'publications.csv',
              [{'pmid': '1', 'doi': '10.1/a', 'title': 'Invented paper one', 'first_publication_date': '2020-01-01',
                'author_count': '3', 'analysis_include': '1', 'metadata_retrieved_on': '2026-10-08',
                'citation_count_epmc': '3', 'citation_observed_on': '2026-10-08', 'publication_year': '2020'}])
    write_csv(P / '2026-10-09' / 'hcps.csv', [{'npi': A, 'publications_2017_2025': '1'}])
    write_csv(P / '2026-10-09' / 'authorships.csv',
              [{'npi': A, 'pmid': '1', 'author_position': '1', 'author_count': '3', 'analysis_include': '1', 'orcid': '',
                'publication_date': '2020-01-01'}])
    write_csv(P / '2026-10-09' / 'withheld_authorships.csv', [], ['npi', 'pmid', 'author_position', 'orcid'])
    write_csv(P / '2026-10-09' / 'coauthor_edges.csv', [], ['hcp_id_a', 'hcp_id_b'])
    link = {'pmid': '1', 'author_count': '3', 'publication_date': '2020-01-01', 'source_id': 's',
            'metadata_retrieved_on': '2026-10-08', 'retrieved_at_utc': '2026-11-20T00:00:00Z',
            'affiliation_at_publication': 'Zephyr Vale Medical Center', 'analysis_include': '1',
            'nppes_name_matches_national': '1', 'nppes_hemonc_name_matches_national': '1', 'institution_multi_site': '0'}
    ua = [{**link, 'first_name_as_indexed': 'Mirela J', 'last_name_as_indexed': 'Vantongeren', 'npi': B, 'author_position': '2', 'confidence_tier': 'A', 'match_rule': 'name_institution'},
          {**link, 'first_name_as_indexed': 'Teodor', 'last_name_as_indexed': 'Halvorsk', 'npi': C, 'author_position': '3', 'confidence_tier': 'B', 'match_rule': 'name_city_state_unique'}]
    if broken:
        ua.append(dict(ua[0]))
    write_csv(P / '2026-11-20' / f'{m.UA}.csv', ua)
    edge = {'pair_type': 'core_universe', 'shared_publications': '1', 'fractional_weight': '0.5',
            'evidence_pmids': '1', 'first_shared_publication_date': '2020-01-01',
            'last_shared_publication_date': '2020-01-01', 'evidence_metadata_first_observed_on': '2026-10-08',
            'evidence_metadata_last_observed_on': '2026-10-08', 'source_id': 's', 'computed_at_utc': 'T'}
    first = {**edge, 'fractional_weight': '0.75'} if broken else edge   # a wrong derived weight
    write_csv(P / '2026-11-20' / f'{m.UE}.csv', [{'npi_a': A, 'npi_b': B, **first}, {'npi_a': A, 'npi_b': C, **edge},
                                                 {'npi_a': B, 'npi_b': C, **edge, 'pair_type': 'universe_universe'}])
    write_csv(P / '2026-11-20' / f'{m.NP}.csv',
              [{'pmid': '2', 'doi': '10.1/b', 'title': 'Invented paper two', 'first_publication_date': '2026-03-01',
                'publication_year': '2026', 'analysis_include': '1', 'exclusion_reason': '', 'in_2017_2025_window': '0',
                'author_count': '2', 'source_id': 's', 'source_release': 'r', 'retrieved_at_utc': '2026-11-20T00:00:00Z',
                'query_family': 'original_lymphoma_in_title'}])
    write_csv(P / '2026-11-20' / f'{m.NA}.csv',
              [{**link, 'first_name_as_indexed': 'Quill', 'last_name_as_indexed': 'Ostrander', 'pmid': '2', 'npi': A, 'is_core': '1', 'author_count': '2', 'author_position': '1',
                'publication_date': '2026-03-01', 'confidence_tier': 'B', 'match_rule': 'name_city_state_unique'},
               {**link, 'first_name_as_indexed': 'Mirela', 'last_name_as_indexed': 'Vantongeren', 'pmid': '2', 'npi': B, 'is_core': '0', 'author_count': '2', 'author_position': '2',
                'publication_date': '2026-03-01', 'confidence_tier': 'A', 'match_rule': 'name_institution'}])
    write_csv(P / '2026-11-20' / f'{m.NE}.csv',
              [{**edge, 'npi_a': A, 'npi_b': B, 'prior_pair_status': 'in_universe_coauthor_edges', 'fractional_weight': '1.0',
                'evidence_pmids': '2', 'first_shared_publication_date': '2026-03-01',
                'last_shared_publication_date': '2026-03-01', 'evidence_metadata_first_observed_on': '2026-11-20',
                'evidence_metadata_last_observed_on': '2026-11-20'}])
    record = {'source_id': 'europe_pmc:x', 'retrieved_at_utc': '2026-11-20T00:00:00Z'}
    corpus = {'1': {'date': '2020-01-01', 'status': 'held'}, '2': {'date': '2026-03-01', 'status': 'new'}}
    pulled = {'1': ([{'id': '2', 'source': 'MED', '_record': record}], [], 1)}
    write_csv(P / '2026-11-20' / f'{m.CE}.csv', m.citation_edges(pulled, corpus))
    read = lambda snap, t: m.read_csv(P / snap / f'{t}.csv')
    included = [r for r in read('2026-10-09', 'authorships') + read('2026-11-20', m.UA) + read('2026-11-20', m.NA)
                if r['analysis_include'] == '1']
    hc = [{**r, 'source_id': 's', 'source_release': 'r', 'computed_at_utc': 'T'}
          for r in m.hcp_citations(read('2026-11-20', m.CE), included)]
    write_csv(P / '2026-11-20' / f'{m.HC}.csv', hc)
    fresh = {'1': {'citedByCount': 4, '_record': record}, '2': {'citedByCount': 0, '_record': record}}
    held_rows = {'1': {'citation_count_epmc': '3', 'citation_observed_on': '2026-10-08', 'source_snapshot_id': 'old',
                       'metadata_retrieved_at_utc': '', 'metadata_retrieved_on': '2026-10-08'}}
    write_csv(P / '2026-11-20' / f'{m.CC}.csv',
              m.citation_counts(corpus, fresh, held_rows, m.pull_status_of({'1': 4, '2': 0}, {'1': ([{}] * 4, [], 4)})))
    people = {r['npi']: r for r in read('2026-10-24', 'hcp_divisions')}
    ft = m.features(people, read('2026-10-09', 'authorships'), read('2026-11-20', m.UA), read('2026-11-20', m.NA),
                    read('2026-11-20', m.UE), read('2026-10-09', 'coauthor_edges'), read('2026-11-20', m.NE),
                    read('2026-11-20', m.HC), 'T')
    if broken:
        ft[1]['distinct_core_coauthors'] = 7
    write_csv(P / '2026-11-20' / f'{m.FT}.csv', ft)
    years = m.paper_years({r['pmid']: r for r in read('2026-10-09', 'publications')}, {r['pmid']: r for r in read('2026-11-20', m.NP)})
    links = m.included_links(read('2026-10-09', 'authorships'), read('2026-11-20', m.UA), read('2026-11-20', m.NA))
    core = {A}
    py = m.pairs_by_year(links, years, core)
    write_csv(P / '2026-11-20' / f'{m.PY}.csv', py)
    write_csv(P / '2026-11-20' / f'{m.HY}.csv', m.npi_years(links, years, core, read('2026-11-20', m.CE)))
    groups = {r['npi']: m.DIVISION_GROUPS[r['division']] for r in read('2026-10-24', 'hcp_divisions')}
    write_csv(P / '2026-11-20' / f'{m.NY}.csv', m.network_by_year(read('2026-11-20', m.PY), links, years, groups))
    return P


def test_features_count_links_and_partners():
    people = {A: {'is_core': '1'}, B: {'is_core': '0'}, C: {'is_core': '0'}}
    core = [{'npi': A, 'pmid': '1', 'author_position': '1', 'author_count': '3', 'analysis_include': '1',
             'publication_date': '2020-01-01'}]
    ua = [{'npi': B, 'pmid': '1', 'author_position': '3', 'author_count': '3', 'analysis_include': '1',
           'publication_date': '2020-01-01', 'confidence_tier': 'A', 'evidence_ccn': '990001'},
          {'npi': C, 'pmid': '1', 'author_position': '2', 'author_count': '3', 'analysis_include': '0',
           'publication_date': '2020-01-01', 'confidence_tier': 'C', 'evidence_ccn': ''}]
    rows = {r['npi']: r for r in m.features(people, core, ua, [], [{'npi_a': A, 'npi_b': B}], [])}
    assert rows[B]['held_publications_2017_2025'] == 1 and rows[B]['first_or_last_author_publications'] == 1
    assert rows[B]['distinct_core_coauthors'] == 1 and rows[B]['institution_ccns'] == '990001'
    assert rows[C]['link_status'] == 'no_included_link' and rows[C]['first_publication_year'] == ''
    assert rows[A]['best_link_tier'] == 'core_2026_10_09'


def totals(P):
    published = {t: m.read_csv(P / '2026-11-20' / f'{t}.csv') for t in m.TABLES}
    return m.headline(published, {r['npi']: r for r in m.read_csv(P / '2026-10-24' / 'hcp_divisions.csv')})


def test_check_passes_a_clean_tree_and_catches_a_duplicate(tmp_path):
    P = tree(tmp_path / 'a')
    expected = totals(P)
    clean = m.check(P, expected)
    assert all(k.startswith('publications_') for k in clean)
    assert not any(clean.values()), {k: v for k, v in clean.items() if v}
    assert m.check(P, {**expected, 'rows_new_publications': 9})['publications_headline_changed'] == 1
    broken = m.check(tree(tmp_path / 'b', broken=True), expected)
    assert broken['publications_ua_duplicate_keys'] == 1 and broken['publications_ue_recomputed_mismatch'] >= 1
    assert broken['publications_ft_recomputed_mismatch'] == 1 and broken['publications_headline_changed'] >= 1


def test_check_rejects_tier_c_and_namesake_risk_in_tracked_rows(tmp_path):
    P = tree(tmp_path / 'c')
    rows = m.read_csv(P / '2026-11-20' / f'{m.UA}.csv')
    rows[1].update(confidence_tier='C', match_rule='name_only_unique')
    rows[0].update(nppes_name_matches_national='4', nppes_hemonc_name_matches_national='2', match_rule='name_orcid_anchored')
    write_csv(P / '2026-11-20' / f'{m.UA}.csv', rows)
    out = m.check(P, {})
    assert out['publications_ua_name_disagrees_with_npi'] == 0
    assert out['publications_ua_tier_rule_invalid'] == 1 and out['publications_ua_namesake_rule_broken'] == 1


def test_scrub_removes_contact_details():
    text = 'Zephyr Vale Medical Center, 1234 Quillon Avenue, Duneport, OH 99999, USA. Tel 555-010-0199. q@example.org'
    assert m.scrub(text) == 'Zephyr Vale Medical Center, Duneport, OH 99999, USA'


def test_check_catches_npis_swapped_between_links(tmp_path):
    P = tree(tmp_path / 'd')
    rows = m.read_csv(P / '2026-11-20' / f'{m.UA}.csv')
    rows[0]['npi'], rows[1]['npi'] = rows[1]['npi'], rows[0]['npi']
    write_csv(P / '2026-11-20' / f'{m.UA}.csv', rows)
    assert m.check(P, {})['publications_ua_name_disagrees_with_npi'] == 2


def test_citation_edges_keep_corpus_pairs_and_flag_reversed_dates():
    rec = {'source_id': 's', 'retrieved_at_utc': 't'}
    corpus = {'1': {'date': '2021-01-01', 'status': 'held'}, '2': {'date': '2020-05-01', 'status': 'held'},
              '3': {'date': '2026-02-01', 'status': 'new'}}
    pulled = {'1': ([{'id': '2', 'source': 'MED', '_record': rec}, {'id': '1', 'source': 'MED', '_record': rec},
                     {'id': '9', 'source': 'MED', '_record': rec}, {'id': '3', 'source': 'PPR', '_record': rec},
                     {'id': '2', 'source': 'MED', '_record': rec}], [], 5),
              '2': ([{'id': '3', 'source': 'MED', '_record': rec}], [], 1)}
    rows = {(r['citing_pmid'], r['cited_pmid']): r for r in m.citation_edges(pulled, corpus)}
    assert set(rows) == {('2', '1'), ('3', '2')}   # no self-pair, no outside or preprint citer, no duplicate
    assert rows['2', '1']['citing_precedes_cited'] == 1 and rows['3', '2']['cited_corpus_status'] == 'held'


def test_hcp_citations_split_shared_and_exclude_self():
    edges = [{'citing_pmid': '3', 'cited_pmid': '1', 'citing_publication_date': '2026-02-01'},
             {'citing_pmid': '2', 'cited_pmid': '1', 'citing_publication_date': '2021-01-01'}]
    links = [{'npi': A, 'pmid': '1'}, {'npi': B, 'pmid': '1'}, {'npi': B, 'pmid': '3'}, {'npi': C, 'pmid': '2'},
             {'npi': A, 'pmid': '2'}]
    rows = {(r['citing_npi'], r['cited_npi']): r for r in m.hcp_citations(edges, links)}
    assert (B, B) not in rows and (A, A) not in rows
    assert rows[B, A]['citation_pairs'] == 1 and rows[B, A]['citation_pairs_coauthors_on_either_paper'] == 1
    assert rows[C, A]['citation_pairs_not_coauthors'] == 0 and rows[C, B]['citation_pairs_not_coauthors'] == 1
    assert rows[A, B]['citation_pairs_coauthors_on_either_paper'] == 1


def test_citation_counts_keep_the_earlier_count_and_blank_unobserved():
    rec = {'source_id': 's', 'retrieved_at_utc': 't'}
    corpus = {'1': {'date': 'd', 'status': 'held'}, '5': {'date': 'd', 'status': 'held'}}
    held = {p: {'citation_count_epmc': '10', 'citation_observed_on': '2026-10-08', 'source_snapshot_id': 'old',
                'metadata_retrieved_at_utc': '', 'metadata_retrieved_on': '2026-10-08'} for p in corpus}
    rows = {r['pmid']: r for r in m.citation_counts(corpus, {'1': {'citedByCount': 7, '_record': rec}}, held, {'1': 'complete'})}
    assert rows['1']['citation_count_change'] == '-3' and rows['1']['citation_count_epmc_2026_10_09'] == '10'
    assert rows['5']['citation_count_epmc_2026_11_20'] == '' and rows['5']['citation_count_change'] == ''
    assert rows['5']['observation_status'] == 'not_returned_by_2026_11_20_queries' and rows['5']['citation_pull_status'] == 'not_pulled'
    assert m.pull_status_of({'1': 3, '2': 0, '4': 2}, {'1': ([{}] * 3, [], 3), '4': ([{}], [], 2)}) == {
        '1': 'complete', '2': 'no_citations', '4': 'incomplete'}


def test_check_catches_a_citation_self_pair(tmp_path):
    P = tree(tmp_path / 'e')
    rows = m.read_csv(P / '2026-11-20' / f'{m.CE}.csv')
    rows.append({**rows[0], 'citing_pmid': rows[0]['cited_pmid']})
    write_csv(P / '2026-11-20' / f'{m.CE}.csv', rows)
    out = m.check(P, {})
    assert out['publications_ce_self_pair'] == 1


def test_pairs_and_people_by_year():
    years = {'1': ('2020', 3, True), '2': ('2021', 2, True), '3': ('2021', 3, True)}
    links = [{'npi': n, 'pmid': p, 'author_position': pos, 'author_count': c} for n, p, pos, c in
             [(A, '1', '1', '3'), (B, '1', '3', '3'), (A, '2', '2', '2'), (B, '2', '1', '2'), (A, '3', '1', '3'), (C, '3', '2', '3')]]
    rows = {(r['npi_a'], r['npi_b'], r['publication_year']): r for r in m.pairs_by_year(links, years, {A})}
    assert rows[A, B, '2020']['shared_publications'] == 1 and float(rows[A, B, '2020']['fractional_weight']) == 0.5
    assert rows[A, B, '2021']['fractional_weight'] == '1.0' and rows[A, C, '2021']['pair_type'] == 'core_universe'
    people = {(r['npi'], r['publication_year']): r for r in m.npi_years(links, years, {A}, [{'citing_pmid': '3', 'cited_pmid': '1'},
                                                                                         {'citing_pmid': '2', 'cited_pmid': '1'}])}
    assert people[A, '2021']['papers'] == 2 and people[A, '2021']['new_coauthors'] == 1   # B seen in 2020, C new
    assert people[B, '2020']['citations_received_from_other_papers'] == 1   # paper 3 has no B; paper 2 has B
    assert people[B, '2020']['last_author_papers'] == 1 and people[A, '2020']['citations_received_from_other_papers'] == 0
    net = {r['publication_year']: r for r in m.network_by_year(list(rows.values()), links, years,
                                                                {A: 'core', B: 'affiliates', C: 'referring'})}
    assert net['2021']['pairs'] == 2 and net['2021']['new_pairs'] == 1 and net['2021']['cross_group_share'] == '1.0000'


def test_check_reconciles_year_sums_with_pair_tables(tmp_path):
    P = tree(tmp_path / 'f')
    rows = m.read_csv(P / '2026-11-20' / f'{m.PY}.csv')
    rows[0]['shared_publications'] = '2'
    write_csv(P / '2026-11-20' / f'{m.PY}.csv', rows)
    out = m.check(P, {})
    assert out['publications_py_held_sum_not_pair_tables'] >= 1 or out['publications_py_2026_not_new_paper_pairs'] >= 1
    assert out['publications_py_recomputed_mismatch'] == 1
