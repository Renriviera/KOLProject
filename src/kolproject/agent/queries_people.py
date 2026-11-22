"""Named queries about people: communities, HCP profiles, center profiles, co-authorship and the data inventory.

Co-authorship numbers of a community are counted on its core members, because communities are defined on the
500 core HCPs; attached HCPs are counted by division group only. Every list returns a name and the id column
(`npi` or `ccn`). An id that does not exist raises `QueryError` with a sentence the model can act on.
"""
from __future__ import annotations

from .packbase import DIVISION_GROUP_NAMES
from .queries import QueryError, query

YEARS = '2017 to 2026 (2026 is a partial year)'
SNAPSHOT = '2024 snapshot'
MAX_ROWS = 2000
TOP_N = lambda default: {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': default}
CORE_LABELS = ('CORE_ONLY', 'LABELS')
FAMILIES = (('People and places', ('dim_hcp', 'dim_center', 'hcp_center')), ('Communities', ('dim_community',)),
            ('CAR-T products', ('dim_product', 'product_event')),
            ('Publications', ('pub', 'authorship', 'coauthor_', 'hcp_pub_')), ('Industry payments', ('pay_', 'ownership')),
            ('Disclosed ties', ('tie_',)), ('Prescribing and claims', ('mcr_', 'mcd_')),
            ('Hospital CAR-T volume', ('center_medicare', 'national_spending')), ('Referral links', ('ref_',)))
COMMUNITY = 'm.short_label'
YES_NO = "case {} when 1 then 'yes' else 'no' end"
CREDENTIALS = "upper(replace(h.credentials, '.', ''))"
BASIS = ("case h.community_basis when 'core_member' then 'Core member of the community' "
         "when 'coauthorship' then 'Attached through co-authorship with core members' "
         "when 'center' then 'Attached through their center' "
         "when 'referral_primary_center' then 'Attached through the center they share most Medicare patients with "
         "(any condition, 2017 to 2022)' "
         "else 'Not attached to a community' end")
CENTER_BASIS = ("case c.community_basis when 'core_majority' then 'Community of most of its core HCPs' "
                "else 'No core HCP linked: not attached to a community' end")
CLASS_340B = ("case c.class_340b when 'A' then 'A: hospital type for which a 340B price is legally possible' "
              "when 'B' then 'B: hospital type for which 340B pricing excludes orphan drugs such as CAR-T products' "
              "when 'C' then 'C: outside the 340B program' end")
MEDICARE_STATUS = ("case when c.medicare_file_status_2024 = 'count_published' then 'CAR-T stay count published for 2024' "
                   "when c.medicare_file_status_2024 = 'in_file_no_drg018_row' then 'Count not shown for 2024: fewer than 11 stays, possibly none' "
                   "when c.medicare_file_status_2024 = 'not_in_file' and c.pps_exempt_cancer_hospital = 1 "
                   "then 'Not published for this hospital: cancer hospital paid outside the standard Medicare system' "
                   "when c.medicare_file_status_2024 = 'not_in_file' then 'Not published for this hospital: it is not in the 2024 Medicare hospital file' end")
EVERY_YEAR = ('with recursive years(year) as (select min(year) from pub where analysis_include = 1 union all '
              'select year + 1 from years where year < (select max(year) from pub where analysis_include = 1))')
SUMMARY = f'''
    select c.community_id, c.short_label as community, c.core_members,
      (select count(*) from dim_hcp h where h.community_id = c.community_id and h.division_group = 'affiliates') as affiliate_hcps,
      (select count(*) from dim_hcp h where h.community_id = c.community_id and h.division_group = 'referring') as referring_hcps,
      (select count(*) from dim_hcp h where h.community_id = c.community_id and h.division_group = 'other') as other_hcps,
      c.centers,
      (select count(*) from dim_center x where x.community_id = c.community_id and x.is_main_center = 1) as main_centers,
      (select count(distinct a.pmid) from authorship a join dim_hcp h on h.npi = a.npi
        where h.is_core = 1 and h.community_id = c.community_id) as papers,
      (select count(*) from coauthor_pair p join dim_hcp a on a.npi = p.npi_a join dim_hcp b on b.npi = p.npi_b
        where p.pair_type = 'core_core' and a.community_id = c.community_id and b.community_id = c.community_id) as pairs_inside,
      c.top_institution, c.region
    from dim_community c where c.community_id = ?'''
SUMMARY_COLUMNS = [
    ('community', 'Community', 'text'), ('core_members', 'Core members', 'count'),
    ('affiliate_hcps', 'Attached affiliate HCPs', 'count'), ('referring_hcps', 'Attached referring HCPs', 'count'),
    ('other_hcps', 'Attached other hematology-oncology HCPs', 'count'), ('centers', 'Centers', 'count'),
    ('main_centers', 'Main centers', 'count'), ('papers', 'Papers by core members', 'count'),
    ('pairs_inside', 'Co-author pairs between core members', 'count'), ('top_institution', 'Most frequent institution', 'text'),
    ('region', 'Region', 'text'), ('community_id', 'Community id', 'id')]
SUMMARY_TABLES = ['dim_community', 'dim_hcp', 'dim_center', 'authorship', 'coauthor_pair']


def division(alias):
    """Division group of an HCP in plain words."""
    return (f"case {alias}.division_group when 'core' then 'Core' when 'affiliates' then 'Affiliate' "
            f"when 'referring' then 'Referring' else 'Other hematology-oncology' end")


def fetch(con, sql, params=()):
    cursor = con.execute(sql, params)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def need(con, table, key, ident, noun):
    """Stop with a usable sentence when an id is not in a core table."""
    if con.execute(f'select 1 from {table} where {key} = ?', (ident,)).fetchone() is None:
        raise QueryError(f'No {noun} with id {ident}. Call find_entity first.')


def need_community(con, community_id):
    need(con, 'dim_community', 'community_id', community_id, 'community')


def family(table):
    return next((name for name, prefixes in FAMILIES if table.startswith(prefixes)), 'Other')


@query('community_summary', 'Headline numbers of one community: core members, attached HCPs by division group, centers, '
       'papers by core members and co-author pairs between core members.',
       {'community_id': 'community_id'}, SUMMARY_COLUMNS, ['community-view'], default_panel={'type': 'kpis'},
       caveats=CORE_LABELS, tables=SUMMARY_TABLES, years=f'{SNAPSHOT}; papers {YEARS}')
def community_summary(con, community_id):
    need_community(con, community_id)
    return fetch(con, SUMMARY, (community_id,))


@query('community_compare', 'The headline numbers of two communities, one row each, in the order given.',
       {'community_id': 'community_id', 'other_community_id': 'community_id'}, SUMMARY_COLUMNS, ['community-view'],
       default_panel={'type': 'table'}, caveats=CORE_LABELS, tables=SUMMARY_TABLES, years=f'{SNAPSHOT}; papers {YEARS}')
def community_compare(con, community_id, other_community_id):
    if community_id == other_community_id:
        raise QueryError('community_id and other_community_id must be two different communities.')
    need_community(con, community_id)
    need_community(con, other_community_id)
    return fetch(con, SUMMARY, (community_id,)) + fetch(con, SUMMARY, (other_community_id,))


@query('community_leaders', 'Core members of a community ranked by PageRank in the co-authorship network, first n.',
       {'community_id': 'community_id', 'n': TOP_N(5)},
       [('pagerank_rank', 'PageRank rank in community', 'rank'), ('name', 'Name', 'text'), ('institution', 'Institution', 'text'),
        ('city', 'City', 'text'), ('state', 'State', 'text'), ('papers', 'Papers', 'count'),
        ('coauthors', 'Distinct co-authors', 'count'), ('npi', 'NPI', 'id')],
       ['community-view'], default_panel={'type': 'ranked_list', 'x': 'name', 'y': ['papers', 'coauthors']},
       caveats=CORE_LABELS, tables=['dim_hcp', 'hcp_pub_summary'], years=f'{SNAPSHOT}; papers {YEARS}')
def community_leaders(con, community_id, n):
    need_community(con, community_id)
    return fetch(con, '''
        select h.pagerank_rank, h.name, h.institution, h.city, h.state, s.papers, s.distinct_coauthors as coauthors, h.npi
        from dim_hcp h left join hcp_pub_summary s on s.npi = h.npi
        where h.community_id = ? and h.is_core = 1 order by h.pagerank_rank, h.npi limit ?''', (community_id, n))


@query('community_members', 'HCPs of one division group in a community: core members by PageRank rank (the default), '
       'or the attached affiliate, referring or other HCPs by number of papers.',
       {'community_id': 'community_id', 'division_group': {'type': 'string', 'enum': list(DIVISION_GROUP_NAMES), 'default': 'core'}},
       [('name', 'Name', 'text'), ('division', 'Division', 'text'), ('attached_by', 'Community attachment', 'text'),
        ('city', 'City', 'text'), ('state', 'State', 'text'), ('center', 'Primary center', 'text'),
        ('pagerank_rank', 'PageRank rank in community', 'rank'), ('papers', 'Papers', 'count'), ('npi', 'NPI', 'id')],
       ['community-view'], default_panel={'type': 'table'}, caveats=CORE_LABELS,
       tables=['dim_hcp', 'dim_center', 'hcp_pub_summary'], years=f'{SNAPSHOT}; papers {YEARS}')
def community_members(con, community_id, division_group):
    need_community(con, community_id)
    return fetch(con, f'''
        select h.name, {division('h')} as division, {BASIS} as attached_by, h.city, h.state, c.name as center,
          h.pagerank_rank, s.papers, h.npi
        from dim_hcp h left join dim_center c on c.ccn = h.primary_center_ccn left join hcp_pub_summary s on s.npi = h.npi
        where h.community_id = ? and h.division_group = ?
        order by h.pagerank_rank is null, h.pagerank_rank, s.papers is null, s.papers desc, h.last_name, h.first_name, h.npi
        limit {MAX_ROWS}''', (community_id, division_group))


@query('community_centers', 'Centers attached to a community (most of their core HCPs belong to it), largest first.',
       {'community_id': 'community_id'},
       [('name', 'Center', 'text'), ('city', 'City', 'text'), ('state', 'State', 'text'), ('core_hcps', 'Core HCPs', 'count'),
        ('core_hcps_in_community', 'Core HCPs of this community', 'count'), ('linked_hcps', 'Linked HCPs', 'count'),
        ('main_center', 'Main center of the community', 'text'), ('ccn', 'CCN', 'id')],
       ['community-view'], default_panel={'type': 'table'}, caveats=CORE_LABELS,
       tables=['dim_center', 'hcp_center', 'dim_hcp'], years=SNAPSHOT)
def community_centers(con, community_id):
    need_community(con, community_id)
    return fetch(con, f'''
        select c.name, c.city, c.state, c.core_hcps,
          (select count(*) from hcp_center x join dim_hcp h on h.npi = x.npi
            where x.ccn = c.ccn and h.is_core = 1 and h.community_id = c.community_id) as core_hcps_in_community,
          c.linked_hcps, {YES_NO.format('c.is_main_center')} as main_center, c.ccn
        from dim_center c where c.community_id = ? order by c.core_hcps desc, c.name, c.ccn''', (community_id,))


@query('community_coauthor_by_year', 'Per year for one community: papers by its core members, co-author pairs between two of its '
       'core members, pairs with core members of other communities, and the share of pairs with other communities.',
       {'community_id': 'community_id'},
       [('year', 'Year', 'year'), ('papers', 'Papers by core members', 'count'),
        ('pairs_inside', 'Co-author pairs inside the community', 'count'),
        ('pairs_outside', 'Co-author pairs with other communities', 'count'),
        ('share_outside', 'Share of pairs with other communities', 'share')],
       ['community-view'], default_panel={'type': 'line', 'x': 'year', 'y': ['papers', 'pairs_inside', 'pairs_outside']},
       caveats=('CORE_ONLY',), tables=['authorship', 'coauthor_pair_year', 'dim_hcp', 'pub'], years=YEARS)
def community_coauthor_by_year(con, community_id):
    need_community(con, community_id)
    return fetch(con, f'''{EVERY_YEAR},
        papers as (select a.year, count(distinct a.pmid) as papers from authorship a join dim_hcp h on h.npi = a.npi
                   where h.is_core = 1 and h.community_id = ?1 group by a.year),
        links as (select p.year, sum(a.community_id = ?1 and b.community_id = ?1) as inside,
                    sum(a.community_id != b.community_id) as outside
                  from coauthor_pair_year p join dim_hcp a on a.npi = p.npi_a join dim_hcp b on b.npi = p.npi_b
                  where p.pair_type = 'core_core' and ?1 in (a.community_id, b.community_id) group by p.year)
        select y.year, coalesce(papers.papers, 0) as papers, coalesce(links.inside, 0) as pairs_inside,
          coalesce(links.outside, 0) as pairs_outside,
          case when links.inside + links.outside > 0 then round(1.0 * links.outside / (links.inside + links.outside), 4) end as share_outside
        from years y left join papers on papers.year = y.year left join links on links.year = y.year order by y.year''',
                 (community_id,))


@query('hcp_profile', 'One HCP: who and where, division, community and how they are attached to it, primary center, '
       'PageRank rank in the community (core HCPs only) and publication counts.',
       {'npi': 'npi'},
       [('name', 'Name', 'text'), ('credentials', 'Credentials', 'text'), ('specialty', 'Specialty', 'text'), ('city', 'City', 'text'),
        ('state', 'State', 'text'), ('division', 'Division', 'text'), ('community', 'Community', 'text'),
        ('community_basis', 'Community attachment', 'text'), ('institution', 'Institution', 'text'),
        ('center', 'Primary center', 'text'), ('pagerank_rank', 'PageRank rank in community', 'rank'),
        ('papers_held', 'Papers 2017 to 2025', 'count'), ('papers_new', 'Papers 2026', 'count'),
        ('first_year', 'First publication year', 'year'), ('last_year', 'Latest publication year', 'year'),
        ('coauthors', 'Distinct co-authors', 'count'),
        ('citation_links', 'Citation links received from papers of other HCPs in the database', 'count'),
        ('npi', 'NPI', 'id'), ('center_ccn', 'Primary center CCN', 'id')],
       ['hcp-profile'], default_panel={'type': 'kpis'}, caveats=CORE_LABELS,
       tables=['dim_hcp', 'dim_community', 'dim_center', 'hcp_pub_summary'], years=f'{SNAPSHOT}; papers {YEARS}')
def hcp_profile(con, npi):
    need(con, 'dim_hcp', 'npi', npi, 'HCP')
    return fetch(con, f'''
        select h.name, {CREDENTIALS} as credentials, h.specialty, h.city, h.state, {division('h')} as division,
          {COMMUNITY} as community, {BASIS} as community_basis, h.institution, c.name as center, h.pagerank_rank,
          s.papers_held, s.papers_new, s.first_year, s.last_year, s.distinct_coauthors as coauthors,
          s.citation_links_received as citation_links, h.npi, h.primary_center_ccn as center_ccn
        from dim_hcp h left join dim_community m on m.community_id = h.community_id
          left join dim_center c on c.ccn = h.primary_center_ccn left join hcp_pub_summary s on s.npi = h.npi
        where h.npi = ?''', (npi,))


@query('hcp_publications_by_year', 'Papers of one HCP per year, with first-author and last-author papers, co-authors, new co-authors '
       'and citations from other papers in the database. Empty when no paper is linked to the HCP.',
       {'npi': 'npi'},
       [('year', 'Year', 'year'), ('papers', 'Papers', 'count'), ('first_author_papers', 'First-author papers', 'count'),
        ('last_author_papers', 'Last-author papers', 'count'), ('coauthors', 'Co-authors that year', 'count'),
        ('new_coauthors', 'Co-authors not seen in an earlier year', 'count'),
        ('citations', 'Citations of that year\'s papers by other papers in the database', 'count')],
       ['hcp-profile'], default_panel={'type': 'bar', 'x': 'year', 'y': ['papers']}, tables=['hcp_pub_year', 'pub'], years=YEARS)
def hcp_publications_by_year(con, npi):
    need(con, 'dim_hcp', 'npi', npi, 'HCP')
    return fetch(con, f'''{EVERY_YEAR}
        select y.year, coalesce(p.papers, 0) as papers, coalesce(p.first_author_papers, 0) as first_author_papers,
          coalesce(p.last_author_papers, 0) as last_author_papers,
          coalesce(p.distinct_core_coauthors + p.distinct_noncore_coauthors, 0) as coauthors,
          coalesce(p.new_coauthors, 0) as new_coauthors, coalesce(p.citations_received, 0) as citations
        from years y left join hcp_pub_year p on p.year = y.year and p.npi = ?1
        where exists (select 1 from hcp_pub_year where npi = ?1) order by y.year''', (npi,))


@query('hcp_top_coauthors', 'The HCPs who share the most papers with one HCP, first n.',
       {'npi': 'npi', 'n': TOP_N(10)},
       [('name', 'Co-author', 'text'), ('division', 'Division', 'text'), ('community', 'Community', 'text'),
        ('shared_publications', 'Shared papers', 'count'), ('first_year', 'First shared year', 'year'),
        ('last_year', 'Latest shared year', 'year'), ('npi', 'NPI', 'id')],
       ['hcp-profile'], default_panel={'type': 'ranked_list', 'x': 'name', 'y': ['shared_publications']},
       caveats=CORE_LABELS, tables=['coauthor_pair', 'dim_hcp', 'dim_community'], years=YEARS)
def hcp_top_coauthors(con, npi, n):
    need(con, 'dim_hcp', 'npi', npi, 'HCP')
    return fetch(con, f'''
        select o.name, {division('o')} as division, {COMMUNITY} as community, p.shared_publications, p.first_year, p.last_year, o.npi
        from (select npi_b as other, shared_publications, fractional_weight, first_year, last_year from coauthor_pair where npi_a = ?1
              union all
              select npi_a, shared_publications, fractional_weight, first_year, last_year from coauthor_pair where npi_b = ?1) p
          join dim_hcp o on o.npi = p.other left join dim_community m on m.community_id = o.community_id
        order by p.shared_publications desc, p.fractional_weight desc, o.last_name, o.npi limit ?2''', (npi, n))


CENTER_LIST = [('name', 'Center', 'text'), ('city', 'City', 'text'), ('state', 'State', 'text'), ('community', 'Community', 'text')]


@query('center_profile', 'One CAR-T center: place, community, linked core and affiliate HCPs, accreditation, 340B class and '
       'whether Medicare publishes its CAR-T stay count.',
       {'ccn': 'ccn'},
       CENTER_LIST + [
           ('community_basis', 'Community attachment', 'text'), ('core_hcps', 'Core HCPs', 'count'),
           ('affiliate_hcps', 'Affiliate HCPs', 'count'), ('linked_hcps', 'Linked HCPs', 'count'),
           ('main_center', 'Main center of its community', 'text'),
           ('fact_accredited', 'FACT accredited for immune effector cell therapy', 'text'), ('class_340b', '340B class', 'text'),
           ('medicare_status', 'Medicare CAR-T stay count, 2024', 'text'), ('ccn', 'CCN', 'id')],
       ['center-profile'], default_panel={'type': 'kpis'}, caveats=CORE_LABELS + ('MEDICARE',),
       tables=['dim_center', 'dim_community', 'hcp_center', 'dim_hcp'], years=SNAPSHOT)
def center_profile(con, ccn):
    need(con, 'dim_center', 'ccn', ccn, 'center')
    return fetch(con, f'''
        select c.name, c.city, c.state, {COMMUNITY} as community, {CENTER_BASIS} as community_basis, c.core_hcps,
          (select count(*) from hcp_center x join dim_hcp h on h.npi = x.npi
            where x.ccn = c.ccn and h.division_group = 'affiliates') as affiliate_hcps,
          c.linked_hcps, {YES_NO.format('c.is_main_center')} as main_center, {YES_NO.format('c.fact_accredited')} as fact_accredited,
          {CLASS_340B} as class_340b, {MEDICARE_STATUS} as medicare_status, c.ccn
        from dim_center c left join dim_community m on m.community_id = c.community_id where c.ccn = ?''', (ccn,))


@query('center_core_hcps', 'Core HCPs linked to a center, highest PageRank first, with their community and papers.',
       {'ccn': 'ccn'},
       [('name', 'Name', 'text'), ('community', 'Community', 'text'), ('pagerank_rank', 'PageRank rank in own community', 'rank'),
        ('papers', 'Papers', 'count'), ('npi', 'NPI', 'id')],
       ['center-profile'], default_panel={'type': 'table'}, caveats=CORE_LABELS,
       tables=['hcp_center', 'dim_hcp', 'dim_community', 'hcp_pub_summary'], years=f'{SNAPSHOT}; papers {YEARS}')
def center_core_hcps(con, ccn):
    need(con, 'dim_center', 'ccn', ccn, 'center')
    return fetch(con, f'''
        select h.name, {COMMUNITY} as community, h.pagerank_rank, s.papers, h.npi
        from hcp_center x join dim_hcp h on h.npi = x.npi left join dim_community m on m.community_id = h.community_id
          left join hcp_pub_summary s on s.npi = h.npi
        where x.ccn = ? and h.is_core = 1 order by h.pagerank desc, h.npi''', (ccn,))


@query('centers_in_state', 'CAR-T centers in one state (two-letter code), with their community and counts of linked HCPs.',
       {'state': {'type': 'string'}},
       CENTER_LIST + [('core_hcps', 'Core HCPs', 'count'), ('linked_hcps', 'Linked HCPs', 'count'), ('ccn', 'CCN', 'id')],
       ['center-profile'], default_panel={'type': 'table'}, caveats=CORE_LABELS, tables=['dim_center', 'dim_community'], years=SNAPSHOT)
def centers_in_state(con, state):
    state = state.upper()
    if len(state) != 2 or not state.isalpha():
        raise QueryError('state must be a two-letter state code such as TX.')
    return fetch(con, f'''
        select c.name, c.city, c.state, {COMMUNITY} as community, c.core_hcps, c.linked_hcps, c.ccn
        from dim_center c left join dim_community m on m.community_id = c.community_id
        where c.state = ? order by c.core_hcps desc, c.name, c.ccn''', (state,))


@query('data_inventory', 'What the database holds: one row per table with what a row is, a description, the years covered and the row count.',
       {},
       [('family', 'Data family', 'text'), ('table', 'Table', 'text'), ('grain', 'One row is', 'text'),
        ('description', 'What it holds', 'text'), ('years', 'Years', 'text'), ('rows', 'Rows', 'count')],
       ['scope-and-help'], default_panel={'type': 'table'}, tables=['pack_table'])
def data_inventory(con):
    rows = fetch(con, '''select name as "table", grain, description, nullif(years, '') as years, rows from pack_table''')
    order = [name for name, _ in FAMILIES] + ['Other']
    for row in rows:
        row['family'] = family(row['table'])
    return sorted(rows, key=lambda row: (order.index(row['family']), row['table']))
