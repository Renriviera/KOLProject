"""Publication tables of the agent pack: papers, authorship links, co-author pairs and per-HCP summaries.

Held papers (2017 to 2025) come from the 2026-10-09 snapshot and the papers first published in 2026 from the
2026-11-20 snapshot. A co-author pair is stored once: its held-paper count comes from `coauthor_edges` (core
pairs) or `universe_coauthor_edges` (pairs with a non-core HCP), and its 2026 count from
`new_paper_coauthor_edges`, so the three sources add without double counting. Link confidence tiers, matching
rules, affiliation text and the lists of evidence papers are not loaded.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

from .packbase import connect, create_table, load_csv, read_rows, register, source, value
from .paths import PACK

TABLES = ['pub', 'authorship', 'coauthor_pair', 'coauthor_pair_year', 'hcp_pub_year', 'hcp_pub_summary']
PAPERS = (('held', 'cohort', 'publications', 'citation_observed_on'), ('new', 'extensions', 'new_publications', 'retrieved_at_utc'))
LINKS = (('core_held', 'cohort', 'authorships'), ('universe_held', 'extensions', 'universe_authorships'),
         ('new_paper', 'extensions', 'new_authorships'))
PAIR_TYPES = {2: 'core_core', 1: 'core_universe', 0: 'universe_universe'}
KEYS = {'pub': 'pmid', 'authorship': 'npi, pmid', 'coauthor_pair': 'npi_a, npi_b', 'coauthor_pair_year': 'npi_a, npi_b, year',
        'hcp_pub_year': 'npi, year', 'hcp_pub_summary': 'npi'}
WEIGHT_TOLERANCE = 1e-9
HELD_LAST_YEAR = 2025  # held papers are 2017 to 2025; every paper of the new corpus was first published in 2026


def npi_of(ident):
    """NPI from an id written as ten digits or as the 2026-10-09 hcp_id 'NPI##########'."""
    return ident[3:] if ident.startswith('NPI') else ident


def pub_rows():
    for corpus, snapshot, table, observed in PAPERS:
        for r in read_rows(source(snapshot, table)):
            yield {'pmid': r['pmid'], 'title': value(r['title'], 'text'), 'journal': value(r['journal'], 'text'),
                   'year': value(r['publication_year'], 'int'), 'publication_date': value(r['first_publication_date'], 'text'),
                   'author_count': value(r['author_count'], 'int'), 'citation_count': value(r['citation_count_epmc'], 'int'),
                   'citation_observed_on': value(r[observed][:10], 'text'), 'corpus': corpus,
                   'analysis_include': value(r['analysis_include'], 'int')}


def included_links():
    """Analysis-included (link source, source row) of the three authorship tables."""
    for link_source, snapshot, table in LINKS:
        for r in read_rows(source(snapshot, table)):
            if r['analysis_include'] == '1':
                yield link_source, r


def authorship_rows(year_of):
    for link_source, r in included_links():
        position, count = value(r['author_position'], 'int'), value(r['author_count'], 'int')
        yield {'npi': r['npi'], 'pmid': r['pmid'], 'year': year_of[r['pmid']], 'author_position': position,
               'author_count': count, 'first_or_last': int(position in (1, count)), 'link_source': link_source}


def held_edges():
    """(npi_a, npi_b, pair type, source row) of the two held-paper pair tables."""
    for r in read_rows(source('cohort', 'coauthor_edges')):
        yield npi_of(r['hcp_id_a']), npi_of(r['hcp_id_b']), 'core_core', r
    for r in read_rows(source('extensions', 'universe_coauthor_edges')):
        yield r['npi_a'], r['npi_b'], r['pair_type'], r


def span(row, r):
    """Widen a pair's first and last year with one source row."""
    first, last = int(r['first_shared_publication_date'][:4]), int(r['last_shared_publication_date'][:4])
    row['first_year'] = min(row['first_year'] or first, first)
    row['last_year'] = max(row['last_year'] or last, last)


def pair_rows():
    """One row per pair: held-paper counts from the two held tables, then the 2026 counts added on top."""
    blank = {'shared_publications_held': 0, 'shared_publications_new': 0, 'fractional_weight': 0.0, 'first_year': None, 'last_year': None}
    pairs = {}
    for a, b, pair_type, r in held_edges():
        assert a < b and (a, b) not in pairs, f'Held pair {a} {b} is out of order or listed twice'
        row = pairs[a, b] = {'npi_a': a, 'npi_b': b, 'pair_type': pair_type, **blank}
        row['shared_publications_held'] = int(r['shared_publications'])
        row['fractional_weight'] = float(r['fractional_weight'])
        span(row, r)
    for r in read_rows(source('extensions', 'new_paper_coauthor_edges')):
        key = (r['npi_a'], r['npi_b'])
        assert (key in pairs) == (r['prior_pair_status'] != 'new_pair'), f'prior_pair_status disagrees with the held tables for {key}'
        row = pairs.setdefault(key, {'npi_a': key[0], 'npi_b': key[1], 'pair_type': r['pair_type'], **blank})
        assert key[0] < key[1] and row['pair_type'] == r['pair_type'] and row['shared_publications_new'] == 0, f'Bad 2026 pair {key}'
        row['shared_publications_new'] = int(r['shared_publications'])
        row['fractional_weight'] += float(r['fractional_weight'])
        span(row, r)
    for row in pairs.values():
        row['shared_publications'] = row['shared_publications_held'] + row['shared_publications_new']
    return sorted(pairs.values(), key=lambda row: (row['npi_a'], row['npi_b']))


def summary_rows(pairs):
    """Linked NPIs of the feature table, with the number of distinct co-authors over held and 2026 papers."""
    partners = Counter()
    for row in pairs:
        partners[row['npi_a']] += 1
        partners[row['npi_b']] += 1
    names = {'papers_held': 'held_publications_2017_2025', 'papers_new': 'new_publications_2026', 'first_year': 'first_publication_year',
             'last_year': 'last_publication_year', 'first_or_last_author_papers': 'first_or_last_author_publications',
             'coauthors_core_held': 'distinct_core_coauthors', 'coauthors_noncore_held': 'distinct_universe_coauthors',
             'coauthors_core_new': 'new_paper_distinct_core_coauthors', 'coauthors_noncore_new': 'new_paper_distinct_universe_coauthors',
             'citation_links_received': 'corpus_citation_links_received',
             'citation_links_received_not_coauthors': 'corpus_citation_links_received_not_coauthors',
             'distinct_citing_hcps': 'distinct_citing_npis'}
    for r in read_rows(source('extensions', 'hcp_universe_publication_features')):
        if r['link_status'] == 'no_included_link':
            continue
        row = {'npi': r['npi'], **{name: value(r[column], 'int') for name, column in names.items()}}
        row['papers'] = row['papers_held'] + row['papers_new']
        row['distinct_coauthors'] = partners[r['npi']]
        yield row


def build(con):
    papers = list(pub_rows())
    year_of = {p['pmid']: p['year'] for p in papers}
    pairs = pair_rows()
    counts = {
        'pub': create_table(con, 'pub', [
            ('pmid', 'text'), ('title', 'text'), ('journal', 'text'), ('year', 'int'), ('publication_date', 'text'),
            ('author_count', 'int'), ('citation_count', 'int'), ('citation_observed_on', 'text'), ('corpus', 'text'),
            ('analysis_include', 'int')], papers, primary_key=['pmid'], indexes=['year']),
        'authorship': create_table(con, 'authorship', [
            ('npi', 'text'), ('pmid', 'text'), ('year', 'int'), ('author_position', 'int'), ('author_count', 'int'),
            ('first_or_last', 'int'), ('link_source', 'text')], authorship_rows(year_of), primary_key=['npi', 'pmid'],
            indexes=['pmid', 'year']),
        'coauthor_pair': create_table(con, 'coauthor_pair', [
            ('npi_a', 'text'), ('npi_b', 'text'), ('pair_type', 'text'), ('shared_publications_held', 'int'),
            ('shared_publications_new', 'int'), ('shared_publications', 'int'), ('fractional_weight', 'real'),
            ('first_year', 'int'), ('last_year', 'int')], pairs, primary_key=['npi_a', 'npi_b'], indexes=['npi_b']),
        'coauthor_pair_year': load_csv(con, 'coauthor_pair_year', source('extensions', 'coauthor_pairs_by_year'), [
            ('npi_a', 'text'), ('npi_b', 'text'), ('year', 'int'), ('shared_publications', 'int'), ('fractional_weight', 'real'),
            ('pair_type', 'text')], rename={'year': 'publication_year'}, primary_key=['npi_a', 'npi_b', 'year'],
            indexes=['npi_b', 'year']),
        'hcp_pub_year': load_csv(con, 'hcp_pub_year', source('extensions', 'hcp_publications_by_year'), [
            ('npi', 'text'), ('year', 'int'), ('papers', 'int'), ('first_author_papers', 'int'), ('last_author_papers', 'int'),
            ('distinct_core_coauthors', 'int'), ('distinct_noncore_coauthors', 'int'), ('new_coauthors', 'int'),
            ('citations_received', 'int')], rename={'year': 'publication_year', 'citations_received': 'citations_received_from_other_papers'},
            primary_key=['npi', 'year'], indexes=['year']),
        'hcp_pub_summary': create_table(con, 'hcp_pub_summary', [
            ('npi', 'text'), ('papers_held', 'int'), ('papers_new', 'int'), ('papers', 'int'), ('first_year', 'int'),
            ('last_year', 'int'), ('first_or_last_author_papers', 'int'), ('coauthors_core_held', 'int'),
            ('coauthors_noncore_held', 'int'), ('coauthors_core_new', 'int'), ('coauthors_noncore_new', 'int'),
            ('distinct_coauthors', 'int'), ('citation_links_received', 'int'), ('citation_links_received_not_coauthors', 'int'),
            ('distinct_citing_hcps', 'int')], summary_rows(pairs), primary_key=['npi']),
    }
    register(con, 'pub', 'paper', ['2026-10-09/publications', '2026-11-20/new_publications'],
             'CAR-T lymphoma papers: the papers held on 2026-10-09 (published 2017 to 2025) and the papers first published in 2026, '
             'with journal, date, author count and Europe PMC citation count.', '2017 to 2026')
    register(con, 'authorship', 'HCP x paper', ['2026-10-09/authorships', '2026-11-20/universe_authorships', '2026-11-20/new_authorships'],
             'Which HCP is an author of which paper, with author position; only links kept for analysis.', '2017 to 2026')
    register(con, 'coauthor_pair', 'pair of HCPs', ['2026-10-09/coauthor_edges', '2026-11-20/universe_coauthor_edges',
                                                   '2026-11-20/new_paper_coauthor_edges'],
             'Two HCPs who share at least one paper, with shared papers on held papers (2017 to 2025) and on 2026 papers.', '2017 to 2026')
    register(con, 'coauthor_pair_year', 'pair of HCPs x year', ['2026-11-20/coauthor_pairs_by_year'],
             'Shared papers of each co-author pair by publication year.', '2017 to 2026')
    register(con, 'hcp_pub_year', 'HCP x year', ['2026-11-20/hcp_publications_by_year'],
             'Papers per HCP and year, with first-author and last-author papers, co-authors and citations from other papers in the database.',
             '2017 to 2026')
    register(con, 'hcp_pub_summary', 'HCP with a paper', ['2026-11-20/hcp_universe_publication_features', 'coauthor_pair'],
             'One line per HCP linked to at least one paper: paper counts, first and last year, distinct co-authors, citation links received.',
             '2017 to 2026')
    return counts


def plural(n, word):
    return f'{n:,} {word}' if n == 1 else f'{n:,} {word}s'


def top(counter, n=3):
    """The n most frequent names with their counts, as text."""
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:n]
    return ', '.join(f'{name} ({count})' for name, count in ranked)


def corpus_years(con):
    first, last = con.execute('select min(year), max(year) from pub where analysis_include = 1').fetchone()
    return f'{first} to {last}'


def hcp_facts(con):
    out, span = {}, corpus_years(con)
    for r in con.execute('select s.*, (select count(*) from coauthor_pair p join dim_hcp o on o.npi = case when p.npi_a = s.npi then p.npi_b '
                         'else p.npi_a end where s.npi in (p.npi_a, p.npi_b) and o.is_core = 1) as core_partners from hcp_pub_summary s'):
        years = str(r['first_year']) if r['first_year'] == r['last_year'] else f"{r['first_year']} to {r['last_year']}"
        new = f", {r['papers_new']} of them in {HELD_LAST_YEAR + 1}" if r['papers_new'] and r['papers_new'] != r['papers'] else ''
        facts = [f"{plural(r['papers'], 'CAR-T lymphoma paper')} in the database, published {years}{new}."]
        if r['first_or_last_author_papers']:
            facts.append(f"First or last author on {plural(r['first_or_last_author_papers'], 'paper')}.")
        if r['distinct_coauthors']:
            facts.append(f"Shares papers with {plural(r['distinct_coauthors'], 'other HCP')} in the database, "
                         f"{r['core_partners']:,} of them core HCPs, {span}.")
        out['hcp', r['npi']] = facts
    return out


def community_facts(con):
    out, span = {}, corpus_years(con)
    institutions = defaultdict(Counter)
    for r in con.execute('select community_id, institution from dim_hcp where is_core = 1 and institution is not null'):
        institutions[r['community_id']][r['institution']] += 1
    links = {r['community_id']: r for r in con.execute(
        'select c.community_id, sum(a.community_id = c.community_id and b.community_id = c.community_id) as inside, '
        'sum(a.community_id != b.community_id) as outside from dim_community c join coauthor_pair p join dim_hcp a on a.npi = p.npi_a '
        "join dim_hcp b on b.npi = p.npi_b where p.pair_type = 'core_core' and c.community_id in (a.community_id, b.community_id) "
        'group by c.community_id')}
    papers = dict(con.execute('select h.community_id, count(distinct a.pmid) from authorship a join dim_hcp h on h.npi = a.npi '
                              'where h.is_core = 1 group by h.community_id').fetchall())
    for r in con.execute("select community_id from dim_community where kind = 'named'"):
        cid, link = r['community_id'], links.get(r['community_id'])
        facts = [f"Core members are authors of {plural(papers.get(cid, 0), 'CAR-T lymphoma paper')} in the database, {span}."]
        if link:
            facts.append(f"{plural(link['inside'], 'co-author pair')} between two core members of the community; "
                         f"{plural(link['outside'], 'pair')} with a core member of another community.")
        if institutions[cid]:
            facts.append(f'Most frequent institutions of core members: {top(institutions[cid])}.')
        out['community', str(cid)] = facts
    return out


def center_facts(con):
    out = {}
    communities = defaultdict(Counter)
    for r in con.execute('select x.ccn, m.short_label from hcp_center x join dim_hcp h on h.npi = x.npi '
                         'join dim_community m on m.community_id = h.community_id where h.is_core = 1'):
        communities[r['ccn']][r['short_label']] += 1
    for r in con.execute('select ccn, core_hcps from dim_center'):
        if not r['core_hcps']:
            out['center', r['ccn']] = ['No core HCP is linked to this center.']
            continue
        verb = 'is' if r['core_hcps'] == 1 else 'are'
        out['center', r['ccn']] = [f"{plural(r['core_hcps'], 'core HCP')} {verb} linked to this center.",
                                   f"Communities of these core HCPs: {top(communities[r['ccn']])}."]
    return out


def card_facts(con):
    """Short factual sentences for the HCP, community and center cards, keyed by (kind, id)."""
    return {**hcp_facts(con), **community_facts(con), **center_facts(con)}


def source_pairs():
    """Expected held count, 2026 count and weight of every pair, summed straight from the three source tables."""
    expected = defaultdict(lambda: [0, 0, 0.0])
    for a, b, _, r in held_edges():
        expected[a, b][0] += int(r['shared_publications'])
        expected[a, b][2] += float(r['fractional_weight'])
    for r in read_rows(source('extensions', 'new_paper_coauthor_edges')):
        expected[r['npi_a'], r['npi_b']][1] += int(r['shared_publications'])
        expected[r['npi_a'], r['npi_b']][2] += float(r['fractional_weight'])
    return expected


def pair_errors(con):
    """Pairs whose counts or weight differ from the three sources, plus pairs present on one side only."""
    expected = source_pairs()
    found = {(r['npi_a'], r['npi_b']): r for r in con.execute('select * from coauthor_pair')}
    wrong = sum(1 for key, (held, new, weight) in expected.items() if key in found and (
        found[key]['shared_publications_held'] != held or found[key]['shared_publications_new'] != new
        or found[key]['shared_publications'] != held + new or abs(found[key]['fractional_weight'] - weight) > WEIGHT_TOLERANCE))
    return wrong + len(set(expected) ^ set(found))


def check(con):
    """Row counts, keys, foreign keys and totals against the source tables; zero means pass."""
    one = lambda sql: con.execute(sql).fetchone()[0] or 0
    lines = lambda snapshot, table, keep=None: sum(1 for r in read_rows(source(snapshot, table)) if keep is None or keep(r))
    held = list(held_edges())
    new = list(read_rows(source('extensions', 'new_paper_coauthor_edges')))
    shared = lambda rows: sum(int(r['shared_publications']) for r in rows)
    weight = lambda rows: sum(float(r['fractional_weight']) for r in rows)
    held_rows = [r for _, _, _, r in held]
    is_core = 'select npi from dim_hcp where is_core = 1'
    by_year = ('select p.npi_a, p.npi_b from coauthor_pair p left join (select npi_a, npi_b, sum(shared_publications) as shared, '
               f'sum(case when year <= {HELD_LAST_YEAR} then shared_publications else 0 end) as held, '
               f'sum(case when year > {HELD_LAST_YEAR} then shared_publications else 0 end) as new, sum(fractional_weight) as weight, '
               'min(year) as first, max(year) as last from coauthor_pair_year group by npi_a, npi_b) y on y.npi_a = p.npi_a and y.npi_b = p.npi_b')
    return {
        'people_pub_rows': int(one('select count(*) from pub') != lines('cohort', 'publications') + lines('extensions', 'new_publications')),
        'people_duplicate_keys': sum(one(f'select count(*) from (select 1 from {table} group by {key} having count(*) > 1)')
                                     for table, key in KEYS.items()),
        'people_pub_blank': one('select count(*) from pub where title is null or year is null or analysis_include is null'),
        'people_authorship_rows': int(one('select count(*) from authorship') != sum(1 for _ in included_links())),
        'people_authorship_npi': one('select count(*) from authorship where npi not in (select npi from dim_hcp)'),
        'people_authorship_pmid': one('select count(*) from authorship where pmid not in (select pmid from pub where analysis_include = 1)'),
        'people_authorship_position': one('select count(*) from authorship where author_position < 1 or author_position > author_count'),
        'people_pair_rows': int(one('select count(*) from coauthor_pair') != len(held) + sum(r['prior_pair_status'] == 'new_pair' for r in new)),
        'people_pair_order': one('select count(*) from coauthor_pair where npi_a >= npi_b'),
        'people_pair_npi': one('select count(*) from coauthor_pair where npi_a not in (select npi from dim_hcp) or npi_b not in (select npi from dim_hcp)'),
        'people_pair_type': one(f"select count(*) from coauthor_pair where pair_type != case (npi_a in ({is_core})) + (npi_b in ({is_core})) "
                                f"when 2 then '{PAIR_TYPES[2]}' when 1 then '{PAIR_TYPES[1]}' else '{PAIR_TYPES[0]}' end"),
        'people_pair_held_total': int(one('select sum(shared_publications_held) from coauthor_pair') != shared(held_rows)),
        'people_pair_new_total': int(one('select sum(shared_publications_new) from coauthor_pair') != shared(new)),
        'people_pair_total': int(one('select sum(shared_publications) from coauthor_pair') != shared(held_rows) + shared(new)),
        'people_pair_weight_total': int(abs(one('select sum(fractional_weight) from coauthor_pair') - weight(held_rows) - weight(new)) > 1e-6),
        'people_pair_source_rows': pair_errors(con),
        'people_pair_negative': one('select count(*) from coauthor_pair where shared_publications_held < 0 or shared_publications_new < 0 '
                                    'or shared_publications < 1 or fractional_weight <= 0'),
        'people_pair_year_rows': int(one('select count(*) from coauthor_pair_year') != lines('extensions', 'coauthor_pairs_by_year')),
        'people_pair_year_orphans': one('select count(*) from coauthor_pair_year y where not exists (select 1 from coauthor_pair p '
                                        'where p.npi_a = y.npi_a and p.npi_b = y.npi_b and p.pair_type = y.pair_type)'),
        'people_pair_year_sums': one(f'select count(*) from ({by_year} where y.shared is null or y.shared != p.shared_publications '
                                     f'or y.held != p.shared_publications_held or y.new != p.shared_publications_new '
                                     f'or abs(y.weight - p.fractional_weight) > {WEIGHT_TOLERANCE})'),
        'people_pair_year_span': one(f'select count(*) from ({by_year} where y.first is not p.first_year or y.last is not p.last_year)'),
        'people_hcp_year_rows': int(one('select count(*) from hcp_pub_year') != lines('extensions', 'hcp_publications_by_year')),
        'people_hcp_year_npi': one('select count(*) from hcp_pub_year where npi not in (select npi from dim_hcp)'),
        'people_hcp_year_vs_authorship': one(
            'select count(*) from (select npi, year, count(*) as papers, sum(author_position = 1) as first from authorship group by npi, year) a '
            'full join hcp_pub_year y on y.npi = a.npi and y.year = a.year where a.papers is not y.papers or a.first is not y.first_author_papers'),
        'people_summary_rows': int(one('select count(*) from hcp_pub_summary') != lines(
            'extensions', 'hcp_universe_publication_features', lambda r: r['link_status'] != 'no_included_link')),
        'people_summary_npi': one('select count(*) from hcp_pub_summary where npi not in (select npi from dim_hcp)'),
        'people_summary_vs_authorship': one(
            f'select count(*) from (select npi, sum(year <= {HELD_LAST_YEAR}) as held, sum(year > {HELD_LAST_YEAR}) as new, min(year) as first, max(year) as last, '
            'sum(first_or_last) as lead from authorship group by npi) a full join hcp_pub_summary s on s.npi = a.npi where a.held is not s.papers_held '
            'or a.new is not s.papers_new or a.first is not s.first_year or a.last is not s.last_year or a.lead is not s.first_or_last_author_papers'),
        'people_summary_coauthors': one(
            'select count(*) from hcp_pub_summary s where distinct_coauthors != (select count(*) from coauthor_pair p where s.npi in (p.npi_a, p.npi_b)) '
            'or coauthors_core_held + coauthors_noncore_held != (select count(*) from coauthor_pair p where s.npi in (p.npi_a, p.npi_b) and p.shared_publications_held > 0) '
            'or coauthors_core_new + coauthors_noncore_new != (select count(*) from coauthor_pair p where s.npi in (p.npi_a, p.npi_b) and p.shared_publications_new > 0)'),
        'people_core_publications': one(
            'select count(*) from dim_hcp h where h.is_core = 1 and h.core_publications is not '
            "(select count(*) from authorship a where a.npi = h.npi and a.link_source = 'core_held')"),
        'people_year_outside_2017_2026': sum(one(f'select count(*) from {table} where year not between 2017 and 2026')
                                             for table in ('pub', 'authorship', 'coauthor_pair_year', 'hcp_pub_year')),
        'people_negative_counts': one('select count(*) from hcp_pub_year where papers < 1 or new_coauthors < 0 or citations_received < 0')
                                  + one('select count(*) from hcp_pub_summary where papers < 1 or distinct_coauthors < 0 or citation_links_received < 0'),
    }


def main():
    """Build the publication tables into agent/pack/dev_people.sqlite, a copy of the core tables, and print the checks."""
    path = PACK / 'dev_people.sqlite'
    for suffix in ('', '-wal', '-shm'):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    dims = sqlite3.connect(f"file:{PACK / 'dims.sqlite'}?mode=ro", uri=True)
    con = connect(path)
    dims.backup(con)
    dims.close()
    summary = build(con)
    con.commit()
    checks = check(con)
    facts = card_facts(con)
    print('PEOPLE', json.dumps(summary), 'CARD FACTS', dict(Counter(kind for kind, _ in facts)),
          'CHECKS', len(checks), 'FAILED', {k: v for k, v in checks.items() if v})
    con.close()


if __name__ == '__main__':
    main()
