"""Publication tables and people queries of the agent pack, on a tiny invented network (no real person or paper)."""
import csv

import pytest

from kolproject.agent import pack_people, queries, queries_people
from kolproject.agent.packbase import PACK, connect, create_table, register

ADA, BEN, CY, DI, ED, FLO, GUS = (f'100000000{i}' for i in range(1, 8))
DEV = PACK / 'dev_people.sqlite'
MINE = [name for name, spec in queries.REGISTRY.items() if spec['function'].__module__ == queries_people.__name__]


def paper(pmid, year, authors, include=1, **extra):
    return {'pmid': pmid, 'title': f'Invented paper {pmid}', 'first_publication_date': f'{year}-03-01', 'publication_year': year,
            'journal': 'Journal of Examples', 'author_count': authors, 'citation_count_epmc': 5, 'analysis_include': include, **extra}


def link(npi, pmid, position, authors, include=1):
    return {'npi': npi, 'pmid': pmid, 'author_position': position, 'author_count': authors, 'analysis_include': include}


def edge(a, b, shared, weight, first, last, **extra):
    return {'npi_a': a, 'npi_b': b, 'shared_publications': shared, 'fractional_weight': weight,
            'first_shared_publication_date': f'{first}-03-01', 'last_shared_publication_date': f'{last}-03-01', **extra}


def by_year(npi, year, papers, first, last, core, noncore, new, citations):
    return {'npi': npi, 'publication_year': year, 'papers': papers, 'first_author_papers': first, 'last_author_papers': last,
            'distinct_core_coauthors': core, 'distinct_noncore_coauthors': noncore, 'new_coauthors': new,
            'citations_received_from_other_papers': citations}


def features(npi, status, held=0, new=0, first='', last='', lead=0, coauthors=(0, 0, 0, 0), citations=(0, 0, 0)):
    names = ('distinct_core_coauthors', 'distinct_universe_coauthors', 'new_paper_distinct_core_coauthors',
             'new_paper_distinct_universe_coauthors')
    cited = ('corpus_citation_links_received', 'corpus_citation_links_received_not_coauthors', 'distinct_citing_npis')
    return {'npi': npi, 'link_status': status, 'held_publications_2017_2025': held, 'new_publications_2026': new,
            'first_publication_year': first, 'last_publication_year': last, 'first_or_last_author_publications': lead,
            **dict(zip(names, coauthors)), **dict(zip(cited, citations))}


def sources():
    """Invented source tables: five held papers (one excluded), two 2026 papers (one excluded), seven pairs."""
    held_pair = lambda a, b, *rest: {'hcp_id_a': f'NPI{a}', 'hcp_id_b': f'NPI{b}',
                                     **{k: v for k, v in edge(a, b, *rest).items() if not k.startswith('npi')}}
    pair_year = lambda a, b, year, kind, weight: {'npi_a': a, 'npi_b': b, 'publication_year': year, 'pair_type': kind,
                                                  'shared_publications': 1, 'fractional_weight': weight}
    return {
        'publications': [paper('900001', 2019, 3, citation_observed_on='2026-10-08'), paper('900002', 2021, 4, citation_observed_on='2026-10-08'),
                         paper('900003', 2021, 2, include=0, citation_observed_on='2026-10-08'),
                         paper('900004', 2024, 2, citation_observed_on='2026-10-09'), paper('900005', 2023, 1, citation_observed_on='2026-10-09')],
        'new_publications': [paper('900101', 2026, 5, retrieved_at_utc='2026-11-20T21:01:24Z'),
                             paper('900102', 2026, 3, include=0, retrieved_at_utc='2026-11-20T21:01:24Z')],
        'authorships': [link(ADA, '900001', 1, 3), link(BEN, '900001', 3, 3), link(ADA, '900002', 2, 4), link(CY, '900002', 4, 4),
                        link(BEN, '900003', 1, 2, include=0), link(ADA, '900004', 1, 2), link(BEN, '900004', 2, 2), link(GUS, '900005', 1, 1)],
        'universe_authorships': [link(DI, '900002', 1, 4), link(FLO, '900003', 2, 2, include=0)],
        'new_authorships': [link(ADA, '900101', 1, 5), link(GUS, '900101', 2, 5), link(DI, '900101', 3, 5), link(CY, '900101', 5, 5)],
        'coauthor_edges': [held_pair(ADA, BEN, 2, 1.5, 2019, 2024), held_pair(ADA, CY, 1, 0.3, 2021, 2021)],
        'universe_coauthor_edges': [edge(ADA, DI, 1, 0.2, 2021, 2021, pair_type='core_universe'), edge(CY, DI, 1, 0.2, 2021, 2021, pair_type='core_universe')],
        'new_paper_coauthor_edges': [
            edge(ADA, CY, 1, 0.25, 2026, 2026, pair_type='core_core', prior_pair_status='in_coauthor_edges_2026_10_09'),
            edge(ADA, DI, 1, 0.25, 2026, 2026, pair_type='core_universe', prior_pair_status='in_universe_coauthor_edges'),
            edge(ADA, GUS, 1, 0.25, 2026, 2026, pair_type='core_core', prior_pair_status='new_pair'),
            edge(CY, DI, 1, 0.25, 2026, 2026, pair_type='core_universe', prior_pair_status='in_universe_coauthor_edges'),
            edge(CY, GUS, 1, 0.25, 2026, 2026, pair_type='core_core', prior_pair_status='new_pair'),
            edge(DI, GUS, 1, 0.25, 2026, 2026, pair_type='core_universe', prior_pair_status='new_pair')],
        'coauthor_pairs_by_year': [
            pair_year(ADA, BEN, 2019, 'core_core', 0.5), pair_year(ADA, BEN, 2024, 'core_core', 1.0), pair_year(ADA, CY, 2021, 'core_core', 0.3),
            pair_year(ADA, CY, 2026, 'core_core', 0.25), pair_year(ADA, DI, 2021, 'core_universe', 0.2), pair_year(ADA, DI, 2026, 'core_universe', 0.25),
            pair_year(CY, DI, 2021, 'core_universe', 0.2), pair_year(CY, DI, 2026, 'core_universe', 0.25), pair_year(ADA, GUS, 2026, 'core_core', 0.25),
            pair_year(CY, GUS, 2026, 'core_core', 0.25), pair_year(DI, GUS, 2026, 'core_universe', 0.25)],
        'hcp_publications_by_year': [
            by_year(ADA, 2019, 1, 1, 0, 1, 0, 1, 3), by_year(ADA, 2021, 1, 0, 0, 1, 1, 2, 0), by_year(ADA, 2024, 1, 1, 0, 1, 0, 0, 1),
            by_year(ADA, 2026, 1, 1, 0, 2, 1, 1, 0), by_year(BEN, 2019, 1, 0, 1, 1, 0, 1, 3), by_year(BEN, 2024, 1, 0, 1, 1, 0, 0, 1),
            by_year(CY, 2021, 1, 0, 1, 1, 1, 2, 0), by_year(CY, 2026, 1, 0, 1, 2, 1, 1, 0), by_year(DI, 2021, 1, 1, 0, 2, 0, 2, 0),
            by_year(DI, 2026, 1, 0, 0, 3, 0, 1, 0), by_year(GUS, 2023, 1, 1, 0, 0, 0, 0, 2), by_year(GUS, 2026, 1, 0, 0, 2, 1, 3, 0)],
        'hcp_universe_publication_features': [
            features(ADA, 'core', 3, 1, 2019, 2026, 3, (2, 1, 2, 1), (4, 1, 2)), features(BEN, 'core', 2, 0, 2019, 2024, 2, (1, 0, 0, 0)),
            features(CY, 'core', 1, 1, 2021, 2026, 2, (1, 1, 2, 1)), features(DI, 'linked', 1, 1, 2021, 2026, 1, (2, 0, 3, 0)),
            features(ED, 'no_included_link'), features(FLO, 'no_included_link'), features(GUS, 'core', 1, 1, 2023, 2026, 1, (0, 0, 2, 1))],
    }


def write(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def dims(con):
    """Invented core tables: two communities, seven HCPs, three centers."""
    hcp = lambda npi, first, group, core, community, basis, pagerank, rank, institution, papers, center, city, state, credentials=None: {
        'npi': npi, 'name': f'{first} Example', 'first_name': first, 'last_name': 'Example', 'credentials': credentials, 'city': city,
        'state': state, 'division': group, 'division_group': group, 'is_core': core, 'community_id': community, 'community_basis': basis,
        'pagerank': pagerank, 'pagerank_rank': rank, 'is_leader': int(rank == 1), 'institution': institution, 'core_publications': papers,
        'primary_center_ccn': center, 'specialty': 'Hematology'}
    people = [hcp(ADA, 'Ada', 'core', 1, 1, 'core_member', 0.05, 1, 'Alpha Institute', 3, '100001', 'Tampa', 'FL', 'M.D., Ph.D.'),
              hcp(BEN, 'Ben', 'core', 1, 1, 'core_member', 0.02, 2, 'Alpha Institute', 2, '100001', 'Tampa', 'FL'),
              hcp(CY, 'Cy', 'core', 1, 2, 'core_member', 0.04, 1, 'Beta Institute', 1, '220001', 'Boston', 'MA'),
              hcp(DI, 'Di', 'affiliates', 0, 1, 'coauthorship', None, None, None, None, '100001', 'Tampa', 'FL'),
              hcp(ED, 'Ed', 'referring', 0, 1, 'referral_primary_center', None, None, None, None, '100001', 'Ocala', 'FL'),
              hcp(FLO, 'Flo', 'other', 0, None, 'unassigned', None, None, None, None, None, None, 'TX'),
              hcp(GUS, 'Gus', 'core', 1, 2, 'core_member', 0.03, 2, 'Beta Institute', 1, '100001', 'Tampa', 'FL')]
    create_table(con, 'dim_hcp', [(c, 'real' if c == 'pagerank' else 'int' if c in ('is_core', 'community_id', 'pagerank_rank', 'is_leader', 'core_publications')
                                  else 'text') for c in people[0]], people, primary_key=['npi'])
    create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')],
                 [(ADA, '100001'), (BEN, '100001'), (GUS, '100001'), (DI, '100001'), (CY, '220001')])
    create_table(con, 'dim_center', [
        ('ccn', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('community_id', 'int'), ('community_basis', 'text'),
        ('core_hcps', 'int'), ('linked_hcps', 'int'), ('is_main_center', 'int'), ('fact_accredited', 'int'), ('class_340b', 'text'),
        ('pps_exempt_cancer_hospital', 'int'), ('medicare_file_status_2024', 'text')], [
        ('100001', 'Alpha Cancer Center', 'Tampa', 'FL', 1, 'core_majority', 3, 4, 1, 1, 'A', 0, 'count_published'),
        ('220001', 'Beta Hospital', 'Boston', 'MA', 2, 'core_majority', 1, 1, 1, 0, 'C', 1, 'not_in_file'),
        ('450001', 'Gamma Clinic', 'Austin', 'TX', None, 'unassigned', 0, 0, 0, 1, 'B', 0, 'in_file_no_drg018_row')], primary_key=['ccn'])
    create_table(con, 'dim_community', [
        ('community_id', 'int'), ('label', 'text'), ('short_label', 'text'), ('kind', 'text'), ('region', 'text'),
        ('top_institution', 'text'), ('core_members', 'int'), ('attached_hcps', 'int'), ('centers', 'int')], [
        (1, 'Loose Alpha orbit', 'Alpha orbit', 'named', 'South', 'Alpha Institute', 2, 4, 1),
        (2, 'Beta orbit', 'Beta orbit', 'named', 'Northeast', 'Beta Institute', 2, 2, 1),
        (14, 'Outside main network', 'Outside main network', 'isolated', 'West', 'Gamma Institute', 0, 0, 0)], primary_key=['community_id'])
    register(con, 'dim_hcp', 'HCP', ['invented'], 'Invented HCPs.', '2024 snapshot')


@pytest.fixture
def tables(tmp_path, monkeypatch):
    """Source rows by table name; a test may change them before calling `build`."""
    monkeypatch.setattr(pack_people, 'source', lambda snapshot, table: tmp_path / f'{table}.csv')
    monkeypatch.setattr(queries, 'PARTS', ['queries_people'])
    return sources()


def build(tables, tmp_path):
    for name, rows in tables.items():
        write(tmp_path / f'{name}.csv', rows)
    con = connect(tmp_path / 'pack.sqlite')
    dims(con)
    counts = pack_people.build(con)
    con.commit()
    return con, counts


@pytest.fixture
def pack(tables, tmp_path):
    return build(tables, tmp_path)[0]


def run(con, name, **params):
    return queries.run(name, params, con=con)['rows']


def test_build_keeps_included_links_and_adds_the_three_pair_sources_without_double_counting(tables, tmp_path):
    con, counts = build(tables, tmp_path)
    assert counts == {'pub': 7, 'authorship': 12, 'coauthor_pair': 7, 'coauthor_pair_year': 11, 'hcp_pub_year': 12, 'hcp_pub_summary': 5}
    assert [tuple(r) for r in con.execute('select pmid, year, corpus, analysis_include, citation_observed_on from pub where pmid in (?, ?)',
                                          ('900003', '900101'))] == [('900003', 2021, 'held', 0, '2026-10-08'), ('900101', 2026, 'new', 1, '2026-11-20')]
    assert [tuple(r) for r in con.execute('select pmid, year, author_position, first_or_last, link_source from authorship where npi = ? order by pmid', (DI,))] \
        == [('900002', 2021, 1, 1, 'universe_held'), ('900101', 2026, 3, 0, 'new_paper')]
    assert [r[1] for r in con.execute('pragma table_info(coauthor_pair)')] == [
        'npi_a', 'npi_b', 'pair_type', 'shared_publications_held', 'shared_publications_new', 'shared_publications', 'fractional_weight',
        'first_year', 'last_year']
    pairs = {(r['npi_a'], r['npi_b']): tuple(r)[2:] for r in con.execute('select * from coauthor_pair')}
    assert pairs[ADA, BEN] == ('core_core', 2, 0, 2, 1.5, 2019, 2024)
    assert pairs[ADA, CY] == ('core_core', 1, 1, 2, pytest.approx(0.55), 2021, 2026)
    assert pairs[ADA, DI] == ('core_universe', 1, 1, 2, pytest.approx(0.45), 2021, 2026)
    assert pairs[DI, GUS] == ('core_universe', 0, 1, 1, 0.25, 2026, 2026)
    summary = {r['npi']: dict(r) for r in con.execute('select * from hcp_pub_summary')}
    assert set(summary) == {ADA, BEN, CY, DI, GUS}
    assert (summary[ADA]['papers_held'], summary[ADA]['papers_new'], summary[ADA]['papers'], summary[ADA]['distinct_coauthors']) == (3, 1, 4, 4)
    assert summary[BEN]['distinct_coauthors'] == 1 and summary[GUS]['first_year'] == 2023
    assert {r['name'] for r in con.execute('select name from pack_table')} >= set(pack_people.TABLES)


def test_check_passes_on_a_clean_build(pack):
    assert {name: errors for name, errors in pack_people.check(pack).items() if errors} == {}


@pytest.mark.parametrize('damage, failing', [
    ('delete from pub where pmid = "900001"', {'people_pub_rows', 'people_authorship_pmid'}),
    ('update authorship set npi = "1999999999" where npi = "%s" and pmid = "900005"' % GUS,
     {'people_authorship_npi', 'people_hcp_year_vs_authorship', 'people_summary_vs_authorship', 'people_core_publications'}),
    ('update coauthor_pair set shared_publications_new = 2, shared_publications = 3 where npi_b = "%s"' % BEN,
     {'people_pair_new_total', 'people_pair_total', 'people_pair_source_rows', 'people_pair_year_sums', 'people_summary_coauthors'}),
    ('delete from coauthor_pair where npi_a = "%s" and npi_b = "%s"' % (DI, GUS),
     {'people_pair_rows', 'people_pair_new_total', 'people_pair_total', 'people_pair_weight_total', 'people_pair_source_rows',
      'people_pair_year_orphans', 'people_summary_coauthors'}),
    ('update coauthor_pair set pair_type = "universe_universe" where npi_b = "%s"' % BEN, {'people_pair_type', 'people_pair_year_orphans'}),
    ('delete from coauthor_pair_year where year = 2019', {'people_pair_year_rows', 'people_pair_year_sums', 'people_pair_year_span'}),
    ('update hcp_pub_year set papers = 2 where npi = "%s" and year = 2019' % ADA, {'people_hcp_year_vs_authorship'}),
    ('delete from hcp_pub_summary where npi = "%s"' % DI, {'people_summary_rows', 'people_summary_vs_authorship'}),
    ('update dim_hcp set core_publications = 9 where npi = "%s"' % CY, {'people_core_publications'}),
    ('update pub set year = 2016 where pmid = "900003"', {'people_year_outside_2017_2026'}),
])
def test_check_catches_a_broken_table(pack, damage, failing):
    pack.execute(damage.replace('"', "'"))
    assert {name for name, errors in pack_people.check(pack).items() if errors} == failing


def test_build_stops_when_a_2026_pair_disagrees_with_the_held_tables(tables, tmp_path):
    tables['new_paper_coauthor_edges'][0]['prior_pair_status'] = 'new_pair'
    with pytest.raises(AssertionError, match='prior_pair_status'):
        build(tables, tmp_path)


def test_community_summary_counts_core_members_and_compare_returns_both(pack):
    alpha = {'community': 'Alpha orbit', 'core_members': 2, 'affiliate_hcps': 1, 'referring_hcps': 1, 'other_hcps': 0, 'centers': 1,
             'main_centers': 1, 'papers': 4, 'pairs_inside': 1, 'top_institution': 'Alpha Institute', 'region': 'South', 'community_id': 1}
    assert run(pack, 'community_summary', community_id=1) == [alpha]
    both = run(pack, 'community_compare', community_id='2', other_community_id=1)
    assert both[1] == alpha and (both[0]['community'], both[0]['papers'], both[0]['pairs_inside']) == ('Beta orbit', 3, 1)
    assert run(pack, 'community_summary', community_id=14)[0]['papers'] == 0
    for name, params in [('community_summary', {'community_id': 99}), ('community_compare', {'community_id': 1, 'other_community_id': 99}),
                         ('community_leaders', {'community_id': 99}), ('community_coauthor_by_year', {'community_id': 99})]:
        with pytest.raises(queries.QueryError, match='No community with id 99'):
            queries.run(name, params, con=pack)
    with pytest.raises(queries.QueryError, match='two different communities'):
        run(pack, 'community_compare', community_id=1, other_community_id=1)


def test_community_leaders_members_and_centers(pack):
    leaders = run(pack, 'community_leaders', community_id=1)
    assert [(r['pagerank_rank'], r['name'], r['papers'], r['coauthors'], r['npi']) for r in leaders] == [
        (1, 'Ada Example', 4, 4, ADA), (2, 'Ben Example', 2, 1, BEN)]
    assert [r['npi'] for r in run(pack, 'community_leaders', community_id=1, n=1)] == [ADA]
    assert [r['npi'] for r in run(pack, 'community_members', community_id=1)] == [ADA, BEN]
    affiliate = run(pack, 'community_members', community_id=1, division_group='affiliates')
    assert affiliate == [{'name': 'Di Example', 'division': 'Affiliate', 'attached_by': 'Attached through co-authorship with core members',
                          'city': 'Tampa', 'state': 'FL', 'center': 'Alpha Cancer Center', 'pagerank_rank': None, 'papers': 2, 'npi': DI}]
    referring = run(pack, 'community_members', community_id=1, division_group='referring')
    assert [(r['npi'], r['papers'], r['division']) for r in referring] == [(ED, None, 'Referring')] and 'Medicare patients' in referring[0]['attached_by']
    assert run(pack, 'community_members', community_id=2, division_group='other') == []
    assert run(pack, 'community_centers', community_id=1) == [
        {'name': 'Alpha Cancer Center', 'city': 'Tampa', 'state': 'FL', 'core_hcps': 3, 'core_hcps_in_community': 2, 'linked_hcps': 4,
         'main_center': 'yes', 'ccn': '100001'}]


def test_community_coauthor_by_year_fills_every_year_and_leaves_the_share_blank_without_pairs(pack):
    rows = run(pack, 'community_coauthor_by_year', community_id=1)
    assert [tuple(r.values()) for r in rows] == [
        (2019, 1, 1, 0, 0.0), (2020, 0, 0, 0, None), (2021, 1, 0, 1, 1.0), (2022, 0, 0, 0, None), (2023, 0, 0, 0, None),
        (2024, 1, 1, 0, 0.0), (2025, 0, 0, 0, None), (2026, 1, 0, 2, 1.0)]
    beta = {r['year']: r for r in run(pack, 'community_coauthor_by_year', community_id=2)}
    assert (beta[2026]['papers'], beta[2026]['pairs_inside'], beta[2026]['pairs_outside'], beta[2026]['share_outside']) == (1, 1, 2, 0.6667)
    assert beta[2023]['papers'] == 1 and beta[2023]['share_outside'] is None


def test_hcp_profile_keeps_blanks_for_people_without_papers_or_community(pack):
    ada = run(pack, 'hcp_profile', npi=ADA)[0]
    assert ada == {'name': 'Ada Example', 'credentials': 'MD, PHD', 'specialty': 'Hematology', 'city': 'Tampa', 'state': 'FL', 'division': 'Core',
                   'community': 'Alpha orbit', 'community_basis': 'Core member of the community', 'institution': 'Alpha Institute',
                   'center': 'Alpha Cancer Center', 'pagerank_rank': 1, 'papers_held': 3, 'papers_new': 1, 'first_year': 2019, 'last_year': 2026,
                   'coauthors': 4, 'citation_links': 4, 'npi': ADA, 'center_ccn': '100001'}
    flo = run(pack, 'hcp_profile', npi=FLO)[0]
    assert flo['division'] == 'Other hematology-oncology' and flo['community_basis'] == 'Not attached to a community'
    assert all(flo[c] is None for c in ('credentials', 'city', 'community', 'center', 'pagerank_rank', 'papers_held', 'papers_new', 'first_year',
                                        'coauthors', 'citation_links', 'center_ccn'))
    di = run(pack, 'hcp_profile', npi=DI)[0]
    assert (di['pagerank_rank'], di['papers_held'], di['papers_new'], di['coauthors']) == (None, 1, 1, 3)
    with pytest.raises(queries.QueryError, match='No HCP with id 1999999999. Call find_entity first'):
        run(pack, 'hcp_profile', npi='1999999999')


def test_hcp_publications_by_year_and_top_coauthors(pack):
    rows = run(pack, 'hcp_publications_by_year', npi=ADA)
    assert [r['year'] for r in rows] == list(range(2019, 2027)) and [r['papers'] for r in rows] == [1, 0, 1, 0, 0, 1, 0, 1]
    assert rows[2] == {'year': 2021, 'papers': 1, 'first_author_papers': 0, 'last_author_papers': 0, 'coauthors': 2, 'new_coauthors': 2, 'citations': 0}
    assert rows[1] == {'year': 2020, 'papers': 0, 'first_author_papers': 0, 'last_author_papers': 0, 'coauthors': 0, 'new_coauthors': 0, 'citations': 0}
    assert run(pack, 'hcp_publications_by_year', npi=ED) == [] and run(pack, 'hcp_top_coauthors', npi=ED) == []
    top = run(pack, 'hcp_top_coauthors', npi=ADA)
    assert [(r['npi'], r['shared_publications'], r['first_year'], r['last_year']) for r in top] == [
        (BEN, 2, 2019, 2024), (CY, 2, 2021, 2026), (DI, 2, 2021, 2026), (GUS, 1, 2026, 2026)]
    assert top[2] == {'name': 'Di Example', 'division': 'Affiliate', 'community': 'Alpha orbit', 'shared_publications': 2, 'first_year': 2021,
                      'last_year': 2026, 'npi': DI}
    assert [r['npi'] for r in run(pack, 'hcp_top_coauthors', npi=GUS, n=2)] == [ADA, CY]
    with pytest.raises(queries.QueryError, match='No HCP with id'):
        run(pack, 'hcp_top_coauthors', npi='42')


def test_center_profile_states_the_medicare_status_in_words(pack):
    alpha = run(pack, 'center_profile', ccn='100001')[0]
    assert alpha == {'name': 'Alpha Cancer Center', 'city': 'Tampa', 'state': 'FL', 'community': 'Alpha orbit',
                     'community_basis': 'Community of most of its core HCPs', 'core_hcps': 3, 'affiliate_hcps': 1, 'linked_hcps': 4,
                     'main_center': 'yes', 'fact_accredited': 'yes', 'class_340b': 'A: hospital type for which a 340B price is legally possible',
                     'medicare_status': 'CAR-T stay count published for 2024', 'ccn': '100001'}
    beta, gamma = run(pack, 'center_profile', ccn='220001')[0], run(pack, 'center_profile', ccn='450001')[0]
    assert beta['fact_accredited'] == 'no' and beta['class_340b'].startswith('C:') and 'cancer hospital paid outside' in beta['medicare_status']
    assert gamma['community'] is None and gamma['community_basis'].startswith('No core HCP') and gamma['main_center'] == 'no'
    assert gamma['class_340b'].startswith('B:') and gamma['medicare_status'].startswith('Count not shown for 2024')
    with pytest.raises(queries.QueryError, match='No center with id 999999'):
        run(pack, 'center_profile', ccn='999999')


def test_center_core_hcps_and_centers_in_state(pack):
    core = run(pack, 'center_core_hcps', ccn='100001')
    assert [(r['npi'], r['community'], r['pagerank_rank'], r['papers']) for r in core] == [
        (ADA, 'Alpha orbit', 1, 4), (GUS, 'Beta orbit', 2, 2), (BEN, 'Alpha orbit', 2, 2)]
    assert run(pack, 'center_core_hcps', ccn='450001') == []
    assert run(pack, 'centers_in_state', state='fl') == [
        {'name': 'Alpha Cancer Center', 'city': 'Tampa', 'state': 'FL', 'community': 'Alpha orbit', 'core_hcps': 3, 'linked_hcps': 4, 'ccn': '100001'}]
    assert run(pack, 'centers_in_state', state='TX')[0]['community'] is None and run(pack, 'centers_in_state', state='WY') == []
    with pytest.raises(queries.QueryError, match='two-letter state code'):
        run(pack, 'centers_in_state', state='Texas')


def test_data_inventory_reads_the_registry_when_it_runs(pack):
    create_table(pack, 'ref_demo', [('npi', 'text')], [(ADA,)])
    create_table(pack, 'zzz_demo', [('npi', 'text')], [])
    register(pack, 'ref_demo', 'HCP', ['invented'], 'A table another part registered later.', '2017 to 2022')
    register(pack, 'zzz_demo', 'HCP', ['invented'], 'A table no family rule knows.')
    rows = run(pack, 'data_inventory')
    assert [r['table'] for r in rows] == ['dim_hcp', 'authorship', 'coauthor_pair', 'coauthor_pair_year', 'hcp_pub_summary', 'hcp_pub_year', 'pub',
                                          'ref_demo', 'zzz_demo']
    assert [r['family'] for r in rows] == ['People and places'] + ['Publications'] * 6 + ['Referral links', 'Other']
    assert rows[-2] == {'family': 'Referral links', 'table': 'ref_demo', 'grain': 'HCP', 'description': 'A table another part registered later.',
                        'years': '2017 to 2022', 'rows': 1}
    assert rows[-1]['years'] is None and rows[2]['rows'] == 7


def test_parameters_are_validated_before_a_query_runs(pack):
    for name, params, words in [
            ('community_leaders', {'community_id': 1, 'n': 0}, 'between 1 and 50'), ('community_leaders', {'community_id': 'eleven'}, 'whole number'),
            ('community_members', {'community_id': 1, 'division_group': 'leaders'}, 'must be one of: core, affiliates, referring, other'),
            ('hcp_profile', {}, 'needs the parameter npi'), ('center_profile', {'ccn': '100001', 'year': 2024}, 'Unknown parameter year'),
            ('community_compare', {'community_id': 1}, 'needs the parameter other_community_id'), ('data_inventory', {'scope': 'hcp'}, 'Unknown parameter')]:
        with pytest.raises(queries.QueryError, match=words):
            queries.run(name, params, con=pack)
    with pytest.raises(queries.QueryError, match='not part of the skill'):
        queries.run('hcp_profile', {'npi': ADA}, con=pack, skill='community-view')
    result = queries.run('community_members', {'community_id': '1'}, con=pack, skill='community-view')
    assert result['params'] == {'community_id': 1, 'division_group': 'core'} and result['caveats'] == ['CORE_ONLY', 'LABELS']
    assert result['default_panel'] == {'type': 'table'} and [c['name'] for c in result['columns']][-1] == 'npi'


def test_every_query_declares_a_panel_known_units_and_a_skill():
    assert set(MINE) == {'community_summary', 'community_compare', 'community_leaders', 'community_members', 'community_centers',
                         'community_coauthor_by_year', 'hcp_profile', 'hcp_publications_by_year', 'hcp_top_coauthors', 'center_profile',
                         'center_core_hcps', 'centers_in_state', 'data_inventory'}
    for name in MINE:
        spec = queries.REGISTRY[name]
        names = [c['name'] for c in spec['columns']]
        assert spec['default_panel']['type'] in ('kpis', 'table', 'bar', 'line', 'ranked_list') and spec['skills'] and spec['tables'], name
        assert set([spec['default_panel'].get('x', names[0])] + spec['default_panel'].get('y', [])) <= set(names), name
        assert set(spec['caveats']) <= {'CORE_ONLY', 'LABELS', 'MEDICARE'}, name


def test_card_facts_give_counts_with_years_and_name_no_other_person(pack):
    facts = pack_people.card_facts(pack)
    assert facts['hcp', ADA] == ['4 CAR-T lymphoma papers in the database, published 2019 to 2026, 1 of them in 2026.',
                                 'First or last author on 3 papers.',
                                 'Shares papers with 4 other HCPs in the database, 3 of them core HCPs, 2019 to 2026.']
    assert facts['hcp', BEN][2] == 'Shares papers with 1 other HCP in the database, 1 of them core HCPs, 2019 to 2026.'
    assert ('hcp', ED) not in facts and ('hcp', FLO) not in facts
    assert facts['community', '1'] == ['Core members are authors of 4 CAR-T lymphoma papers in the database, 2019 to 2026.',
                                       '1 co-author pair between two core members of the community; 2 pairs with a core member of another community.',
                                       'Most frequent institutions of core members: Alpha Institute (2).']
    assert ('community', '14') not in facts
    assert facts['center', '100001'] == ['3 core HCPs are linked to this center.', 'Communities of these core HCPs: Alpha orbit (2), Beta orbit (1).']
    assert facts['center', '450001'] == ['No core HCP is linked to this center.']
    assert all(len(sentences) <= 3 and 'Example' not in ' '.join(sentences) for sentences in facts.values())


@pytest.mark.skipif(not DEV.exists(), reason='the dev pack is built by `python -m kolproject.agent.pack_people`')
def test_every_query_runs_on_the_dev_pack(monkeypatch):
    monkeypatch.setattr(queries, 'PARTS', ['queries_people'])
    con = connect(DEV, readonly=True)
    one = lambda sql: con.execute(sql).fetchone()[0]
    ids = {'community_id': 11, 'other_community_id': 4, 'npi': one('select npi from dim_hcp where community_id = 11 and pagerank_rank = 1'),
           'ccn': one('select ccn from dim_center where community_id = 11 order by core_hcps desc limit 1'), 'state': 'TX'}
    for name in MINE:
        spec = queries.REGISTRY[name]
        result = queries.run(name, {p: ids[p] for p in spec['params'] if p in ids}, con=con)
        assert 0 < result['row_count'] <= queries.MAX_ROWS, name
        assert list(result['rows'][0]) == [c['name'] for c in spec['columns']], name
    assert queries.run('community_summary', {'community_id': 11}, con=con)['rows'][0]['core_members'] == 22
    assert {name: errors for name, errors in pack_people.check(con).items() if errors} == {}
    con.close()
