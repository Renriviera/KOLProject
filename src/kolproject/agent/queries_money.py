"""Named queries on industry payments, research payments, ownership, disclosed ties and the CAR-T products.

Every scoped query takes `scope` (one of `packbase.SCOPES`: an HCP, a community with or without its attached
HCPs, a center, a division group) and `id`, and reads the HCPs of the scope through `packbase.scope_filter`.
Three reading rules are built in:

- General payments are counted under a record rule: `lymphoma_only` (every named product is a CAR-T product and
  one is Yescarta, Tecartus, Kymriah or Breyanzi) or `cart_only` (every named product is a CAR-T product).
- A research amount is the total of a whole record. It is returned as `record_total_usd`, each record once per
  scope, and is never money received by a person.
- A zero in a payment query is a real zero: the whole Open Payments file was scanned for 2019 to 2025.
"""
from __future__ import annotations

from collections import Counter

from .packbase import SCOPES, scope_filter
from .queries import BRANDS, QueryError, query

SCOPED = {'scope': 'scope', 'id': 'id'}
RULES = ('cart_only', 'lymphoma_only')
RULE = {'type': 'string', 'enum': list(RULES), 'default': 'lymphoma_only'}
NATURE = {'type': 'string', 'enum': ['services', 'hospitality_education', 'all'], 'default': 'services'}
PAY_YEARS, TIE_YEARS = '2019 to 2025', '2017 to 2026'
PROGRAM_YEARS = 'y(year) as (select 2019 union all select year + 1 from y where year < 2025)'
NOT_SHOWN = 'Study name not shown'


def number(low, high, default):
    return {'type': 'integer', 'minimum': low, 'maximum': high, 'default': default}


def fetch(con, sql, args=()):
    """Rows of a read-only statement as dicts, whatever row factory the connection has."""
    cursor = con.execute(sql, args)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def members(scope):
    """SQL selecting the NPIs of a scope, with one `?` for the id."""
    try:
        return f'select h.npi from dim_hcp h where {scope_filter(scope)}'
    except ValueError:
        raise QueryError(f"scope must be one of: {', '.join(SCOPES)}.") from None


def rule_name(rule):
    """The record rule as a column prefix; only the two fixed names ever reach the SQL text."""
    if rule not in RULES:
        raise QueryError('rule must be one of: cart_only, lymphoma_only.')
    return rule


# ---------------------------------------------------------------- general payments

@query('payments_by_year',
       'General payments (consulting, speaking, meals, travel, education) reported for the HCPs of a scope, per program year '
       '2019 to 2025: HCPs paid, records, dollars, and the services and hospitality parts. rule lymphoma_only keeps records naming '
       'Yescarta, Tecartus, Kymriah or Breyanzi; cart_only keeps every CAR-T product. A year with zero is a real zero.',
       {**SCOPED, 'rule': RULE},
       [('year', 'Program year', 'year'), ('hcps_paid', 'HCPs paid', 'count'), ('records', 'Payment records', 'count'),
        ('total_usd', 'Dollars reported', 'usd'), ('services_usd', 'Consulting, speaking and honoraria', 'usd'),
        ('hospitality_education_usd', 'Meals, travel and education', 'usd')],
       skills=['industry-payments', 'hcp-profile'],
       default_panel={'type': 'line', 'x': 'year', 'y': ['total_usd', 'services_usd', 'hospitality_education_usd'], 'markers': 'lymphoma_products'},
       caveats=['PAYMENTS', 'LABELS'], tables=['pay_general_year'], years=PAY_YEARS)
def payments_by_year(con, scope, id, rule='lymphoma_only'):
    p = rule_name(rule)
    return fetch(con, f'''
        with recursive s as ({members(scope)}), {PROGRAM_YEARS},
        g as (select g.* from pay_general_year g join s on s.npi = g.npi)
        select y.year, count(case when g.{p}_records > 0 then 1 end) as hcps_paid, coalesce(sum(g.{p}_records), 0) as records,
               round(coalesce(sum(g.{p}_usd), 0), 2) as total_usd, round(coalesce(sum(g.{p}_services_usd), 0), 2) as services_usd,
               round(coalesce(sum(g.{p}_hospitality_education_usd), 0), 2) as hospitality_education_usd
        from y left join g on g.year = y.year group by y.year order by y.year''', [id])


@query('payments_by_product',
       'General payments to the HCPs of a scope per CAR-T brand and program year: records, HCPs paid and dollars, on records where '
       'every named product is a CAR-T product. Give brand for one product; without it every brand with a record is returned. '
       'A record naming several CAR-T brands counts under each, with its amount split equally between them.',
       {**SCOPED, 'year_from': number(2019, 2025, 2019), 'year_to': number(2019, 2025, 2025),
        'brand': {'type': 'string', 'enum': BRANDS, 'default': None}},
       [('brand', 'CAR-T product', 'text'), ('year', 'Program year', 'year'), ('records', 'Payment records', 'count'),
        ('hcps_paid', 'HCPs paid', 'count'), ('total_usd', 'Dollars reported', 'usd')],
       skills=['industry-payments', 'product-timeline'],
       default_panel={'type': 'line', 'x': 'year', 'y': ['total_usd'], 'split_by': 'brand', 'markers': 'lymphoma_products'},
       caveats=['PAYMENTS', 'LABELS'], tables=['pay_general_record', 'pay_general_record_brand', 'dim_product'], years=PAY_YEARS)
def payments_by_product(con, scope, id, year_from=2019, year_to=2025, brand=None):
    if year_from > year_to:
        raise QueryError('year_from must not be after year_to.')
    return fetch(con, f'''
        with recursive s as ({members(scope)}),
        y(year) as (select ? union all select year + 1 from y where year < ?),
        b as (select x.brand, r.year, r.npi, x.amount_share_usd from pay_general_record r join s on s.npi = r.npi
              join pay_general_record_brand x on x.record_id = r.record_id
              where r.cart_only = 1 and r.year between ? and ? and (? is null or x.brand = ?)),
        named as (select brand from dim_product where brand = ? union select brand from b)
        select p.brand, y.year, count(b.npi) as records, count(distinct b.npi) as hcps_paid,
               round(coalesce(sum(b.amount_share_usd), 0), 2) as total_usd
        from named n join dim_product p on p.brand = n.brand cross join y left join b on b.brand = n.brand and b.year = y.year
        group by p.brand, y.year order by p.first_us_approval, p.brand, y.year''',
                 [id, year_from, year_to, year_from, year_to, brand, brand, brand])


@query('payments_by_manufacturer',
       'General payments to the HCPs of a scope per CAR-T manufacturer group, 2019 to 2025: HCPs paid, records, dollars, services '
       'dollars, and the first and last program year with a record. Same rule choice as payments_by_year.',
       {**SCOPED, 'rule': RULE},
       [('manufacturer_group', 'Manufacturer group', 'text'), ('hcps_paid', 'HCPs paid', 'count'), ('records', 'Payment records', 'count'),
        ('total_usd', 'Dollars reported', 'usd'), ('services_usd', 'Consulting, speaking and honoraria', 'usd'),
        ('first_year', 'First year', 'year'), ('last_year', 'Last year', 'year')],
       skills=['industry-payments', 'disclosed-ties'],
       default_panel={'type': 'bar', 'x': 'manufacturer_group', 'y': ['total_usd']},
       caveats=['PAYMENTS', 'LABELS'], tables=['pay_general_hcp_mfr_year'], years=PAY_YEARS)
def payments_by_manufacturer(con, scope, id, rule='lymphoma_only'):
    p = rule_name(rule)
    return fetch(con, f'''
        with s as ({members(scope)})
        select m.manufacturer_group, count(distinct m.npi) as hcps_paid, sum(m.{p}_records) as records,
               round(sum(m.{p}_usd), 2) as total_usd, round(sum(m.{p}_services_usd), 2) as services_usd,
               min(m.year) as first_year, max(m.year) as last_year
        from pay_general_hcp_mfr_year m join s on s.npi = m.npi where m.{p}_records > 0
        group by m.manufacturer_group order by total_usd desc, m.manufacturer_group''', [id])


@query('payments_monthly',
       'General payments to the HCPs of a scope per month, January 2019 to December 2025, by payment date: records and dollars, '
       'for a timeline with approval markers. Months without a record are returned as zero, a real zero. Give brand for one '
       'product: the records are then those of payments_by_product (every named product a CAR-T product, the amount split '
       'equally between the brands named) and rule is not used.',
       {**SCOPED, 'rule': RULE, 'brand': {'type': 'string', 'enum': BRANDS, 'default': None}},
       [('month', 'Month', 'month'), ('records', 'Payment records', 'count'), ('total_usd', 'Dollars reported', 'usd')],
       skills=['industry-payments'],
       default_panel={'type': 'line', 'x': 'month', 'y': ['total_usd'], 'markers': 'lymphoma_products'},
       caveats=['PAYMENTS', 'LABELS'], tables=['pay_general_record', 'pay_general_record_brand'], years=PAY_YEARS)
def payments_monthly(con, scope, id, rule='lymphoma_only', brand=None):
    p = rule_name(rule)
    records = (f'select r.month, r.amount_usd from pay_general_record r join s on s.npi = r.npi where r.{p} = 1' if brand is None else
               'select r.month, x.amount_share_usd as amount_usd from pay_general_record r join s on s.npi = r.npi '
               'join pay_general_record_brand x on x.record_id = r.record_id where r.cart_only = 1 and x.brand = ?')
    return fetch(con, f'''
        with recursive s as ({members(scope)}),
        n(k) as (select 0 union all select k + 1 from n where k < 83),
        m as (select printf('%04d-%02d', 2019 + k / 12, 1 + k % 12) as month from n),
        r as ({records})
        select m.month, count(r.month) as records, round(coalesce(sum(r.amount_usd), 0), 2) as total_usd
        from m left join r on r.month = m.month group by m.month order by m.month''', [id] if brand is None else [id, brand])


@query('top_paid_hcps',
       'The HCPs of a community, center or division group with the most general-payment dollars, for one program year or for '
       '2019 to 2025 when year is absent. nature is services (consulting, speaking, honoraria), hospitality_education (meals, '
       'travel, education) or all. Not for scope hcp.',
       {**SCOPED, 'year': number(2019, 2025, None), 'nature': NATURE, 'rule': RULE, 'n': number(1, 50, 15)},
       [('rank', 'Rank', 'rank'), ('name', 'HCP', 'text'), ('npi', 'NPI', 'id'), ('division_group', 'Division group', 'text'),
        ('community', 'Community', 'text'), ('city', 'City', 'text'), ('state', 'State', 'text'),
        ('total_usd', 'Dollars reported', 'usd'), ('records', 'Payment records', 'count')],
       skills=['industry-payments'], default_panel={'type': 'ranked_list', 'x': 'name', 'y': ['total_usd']},
       caveats=['PAYMENTS', 'LABELS', 'CORE_ONLY'], tables=['pay_general_record', 'dim_hcp', 'dim_community'], years=PAY_YEARS)
def top_paid_hcps(con, scope, id, year=None, nature='services', rule='lymphoma_only', n=15):
    if scope == 'hcp':
        raise QueryError('top_paid_hcps compares several HCPs, so scope must be community, center or division_group. '
                         'For one HCP use payments_by_year.')
    members(scope)
    rows = fetch(con, f'''
        select h.name, h.npi, h.division_group, c.short_label as community, h.city, h.state,
               round(sum(r.amount_usd), 2) as total_usd, count(*) as records
        from dim_hcp h join pay_general_record r on r.npi = h.npi left join dim_community c on c.community_id = h.community_id
        where {scope_filter(scope)} and r.{rule_name(rule)} = 1 and (? is null or r.year = ?) and (? = 'all' or r.nature_class = ?)
        group by h.npi order by total_usd desc, h.npi limit ?''', [id, year, year, nature, nature, n])
    return [{'rank': k + 1, **row} for k, row in enumerate(rows)]


# ---------------------------------------------------------------- research payments and ownership

@query('research_by_year',
       'CAR-T research payment records naming the HCPs of a scope, per program year 2019 to 2025: HCPs named, records naming a '
       'CAR-T product, those with an HCP of the scope as investigator, the total of those records, and records matched by study '
       'text only. Each record counts once. record_total_usd is the whole study payment, mostly paid to institutions: never call it '
       'income of the HCP.',
       SCOPED,
       [('year', 'Program year', 'year'), ('hcps_named', 'HCPs named on a product record', 'count'),
        ('product_field_records', 'Research records naming a CAR-T product', 'count'),
        ('investigator_records', 'Of those, as investigator', 'count'),
        ('record_total_usd', 'Total of those records (whole-study amounts, not personal income)', 'usd'),
        ('text_only_records', 'Records matched by study text only', 'count')],
       skills=['industry-payments'], default_panel={'type': 'line', 'x': 'year', 'y': ['product_field_records', 'hcps_named']},
       caveats=['PAYMENTS', 'LABELS'], tables=['pay_research_record'], years=PAY_YEARS)
def research_by_year(con, scope, id):
    product = "r.match_basis = 'product_field'"
    return fetch(con, f'''
        with recursive s as ({members(scope)}), {PROGRAM_YEARS},
        p as (select p.* from pay_research_record p join s on s.npi = p.npi),
        r as (select record_id, year, match_basis, max(record_total_usd) as record_total_usd, max(role = 'investigator') as investigator
              from p group by record_id),
        h as (select year, count(distinct npi) as hcps from p where match_basis = 'product_field' group by year)
        select y.year, coalesce(max(h.hcps), 0) as hcps_named, count(case when {product} then 1 end) as product_field_records,
               coalesce(sum(case when {product} then r.investigator end), 0) as investigator_records,
               round(coalesce(sum(case when {product} then r.record_total_usd end), 0), 2) as record_total_usd,
               count(case when not {product} then 1 end) as text_only_records
        from y left join r on r.year = y.year left join h on h.year = y.year group by y.year order by y.year''', [id])


def study_rows(records, n):
    """Research rows grouped into studies by name (case and spacing ignored); unnamed rows form one last group."""
    studies = {}
    for r in records:
        key = ' '.join(r['study_name'].split()).upper() if r['study_name'] else None
        s = studies.setdefault(key, {'names': Counter(), 'ncts': Counter(), 'groups': Counter(), 'brands': set(), 'product': False,
                                     'years': set(), 'records': set(), 'investigators': set()})
        s['names'][r['study_name']] += 1
        s['ncts'].update([r['nct_id']] if r['nct_id'] else [])
        s['groups'].update([r['manufacturer_group']] if r['manufacturer_group'] else [])
        s['brands'].update(b for b in (r['brands'] or '').split(';') if b)
        s['product'] |= r['match_basis'] == 'product_field'
        s['years'].add(r['year'])
        s['records'].add(r['record_id'])
        s['investigators'].update([r['npi']] if r['role'] == 'investigator' else [])
    top = lambda counter: sorted(counter, key=lambda v: (-counter[v], v))[0] if counter else None

    def row(key, s):
        return {'study_name': top(s['names']) if key else NOT_SHOWN, 'nct_id': top(s['ncts']) if key else None,
                'manufacturer_group': top(s['groups']) if key else None,
                'brands': '; '.join(b for b in BRANDS if b in s['brands']) or None,
                'match_basis': 'product named' if s['product'] else 'study text only',
                'first_year': min(s['years']), 'last_year': max(s['years']), 'records': len(s['records']),
                'investigators': len(s['investigators'])}
    named = sorted((row(k, s) for k, s in studies.items() if k), key=lambda r: (-r['records'], r['study_name']))[:n]
    return named + ([row(None, studies[None])] if None in studies else [])


@query('research_studies',
       'Studies behind the CAR-T research payment records naming the HCPs of a scope, largest first: study name, '
       f'ClinicalTrials.gov id, manufacturer group, brands, years, records and investigators in the scope. Records whose study '
       f'name cannot be shown are grouped in one last row, "{NOT_SHOWN}". No dollars: research amounts are not personal income.',
       {**SCOPED, 'n': number(1, 50, 20)},
       [('study_name', 'Study', 'text'), ('nct_id', 'ClinicalTrials.gov id', 'id'), ('manufacturer_group', 'Manufacturer group', 'text'),
        ('brands', 'CAR-T products named', 'text'), ('match_basis', 'Matched by', 'text'), ('first_year', 'First year', 'year'),
        ('last_year', 'Last year', 'year'), ('records', 'Research records', 'count'), ('investigators', 'Investigators in scope', 'count')],
       skills=['industry-payments'], default_panel={'type': 'table'},
       caveats=['PAYMENTS', 'LABELS'], tables=['pay_research_record'], years=PAY_YEARS)
def research_studies(con, scope, id, n=20):
    members(scope)
    return study_rows(fetch(con, f'''
        select p.record_id, p.npi, p.year, p.match_basis, p.study_name, p.nct_id, p.manufacturer_group, p.brands, p.role
        from pay_research_record p join dim_hcp h on h.npi = p.npi where {scope_filter(scope)}''', [id]), n)


@query('ownership',
       'Ownership and investment interests reported for the HCPs of a scope, 2019 to 2025, by company and program year, with the '
       'value and the amount invested. The first row always counts interests in CAR-T manufacturers; zero there is a real finding, '
       'because the whole file was scanned.',
       SCOPED,
       [('company', 'Company', 'text'), ('year', 'Program year', 'year'), ('hcps', 'HCPs with an interest', 'count'),
        ('interests', 'Interests reported', 'count'), ('value_of_interest_usd', 'Value of interest', 'usd'),
        ('amount_invested_usd', 'Amount invested', 'usd'), ('cart_manufacturer', 'CAR-T manufacturer', 'text')],
       skills=['industry-payments'], default_panel={'type': 'table'},
       caveats=['PAYMENTS', 'LABELS'], tables=['ownership'], years=PAY_YEARS)
def ownership(con, scope, id):
    scoped = f'with s as ({members(scope)}), o as (select o.* from ownership o join s on s.npi = o.npi)'
    money = 'round(sum(value_of_interest_usd), 2) as value_of_interest_usd, round(sum(amount_invested_usd), 2) as amount_invested_usd'
    total = fetch(con, f'''{scoped}
        select 'All CAR-T manufacturers, 2019 to 2025' as company, null as year, count(distinct npi) as hcps, count(*) as interests,
               {money}, 'yes' as cart_manufacturer from o where cart_manufacturer = 1''', [id])
    return total + fetch(con, f'''{scoped}
        select min(manufacturer) as company, year, count(distinct npi) as hcps, count(*) as interests, {money},
               case when max(cart_manufacturer) = 1 then 'yes' else 'no' end as cart_manufacturer
        from o group by upper(manufacturer), year order by year desc, interests desc, company''', [id])


# ---------------------------------------------------------------- disclosed ties

@query('ties_by_year',
       'Conflict-of-interest disclosures on the papers of the HCPs of a scope, per publication year: papers, papers with a '
       'statement, papers where an HCP of the scope is named with a CAR-T manufacturer, and HCPs so named. The publication year is '
       'not the year of the relationship.',
       SCOPED,
       [('year', 'Publication year', 'year'), ('papers', 'Papers', 'count'), ('papers_with_statement', 'Papers with a statement', 'count'),
        ('papers_named', 'Papers naming an HCP with a CAR-T manufacturer', 'count'),
        ('hcps_named', 'HCPs named with a CAR-T manufacturer', 'count')],
       skills=['disclosed-ties'], default_panel={'type': 'line', 'x': 'year', 'y': ['papers_with_statement', 'papers_named']},
       caveats=['TIES', 'LABELS'], tables=['tie_author_paper'], years=TIE_YEARS)
def ties_by_year(con, scope, id):
    return fetch(con, f'''
        with recursive s as ({members(scope)}),
        t as (select a.npi, a.pmid, a.publication_year as year, a.named_with_cart_manufacturer as named,
                     a.statement_status is not null and a.statement_status != 'no_statement' as stated
              from tie_author_paper a join s on s.npi = a.npi),
        y(year) as (select min(year) from t union all select year + 1 from y where year < (select max(year) from t))
        select y.year, count(distinct t.pmid) as papers, count(distinct case when t.stated then t.pmid end) as papers_with_statement,
               count(distinct case when t.named = 1 then t.pmid end) as papers_named,
               count(distinct case when t.named = 1 then t.npi end) as hcps_named
        from y left join t on t.year = y.year where y.year is not null group by y.year order by y.year''', [id])


@query('ties_by_manufacturer',
       'CAR-T manufacturer groups named with the HCPs of a scope in conflict-of-interest statements: HCPs named, papers, and the '
       'first and last publication year. A tie is to the company, not to a CAR-T product.',
       SCOPED,
       [('manufacturer_group', 'Manufacturer group', 'text'), ('hcps_named', 'HCPs named with it', 'count'), ('papers', 'Papers', 'count'),
        ('first_year', 'First publication year', 'year'), ('last_year', 'Last publication year', 'year')],
       skills=['disclosed-ties'], default_panel={'type': 'bar', 'x': 'manufacturer_group', 'y': ['hcps_named', 'papers']},
       caveats=['TIES', 'LABELS'], tables=['tie_author_group', 'tie_author_paper'], years=TIE_YEARS)
def ties_by_manufacturer(con, scope, id):
    return fetch(con, f'''
        with s as ({members(scope)})
        select g.manufacturer_group, count(distinct g.npi) as hcps_named, count(distinct g.pmid) as papers,
               min(a.publication_year) as first_year, max(a.publication_year) as last_year
        from tie_author_group g join s on s.npi = g.npi join tie_author_paper a on a.npi = g.npi and a.pmid = g.pmid
        group by g.manufacturer_group order by hcps_named desc, papers desc, g.manufacturer_group''', [id])


def paper_columns(con):
    """(title, journal) expressions from the `pub` table when the people part has built it, else blanks."""
    columns = {r[1] for r in con.execute("pragma table_info('pub')")}
    if 'pmid' not in columns:
        return 'null', 'null', ''
    return ('p.title' if 'title' in columns else 'null', 'p.journal' if 'journal' in columns else 'null',
            'left join pub p on cast(p.pmid as text) = t.pmid')


@query('hcp_ties_papers',
       'Papers on which one HCP is named with a CAR-T manufacturer in the conflict-of-interest statement, newest first, with the '
       'manufacturer groups named. The statement text is never shown.',
       {'npi': 'npi', 'n': number(1, 50, 25)},
       [('pmid', 'PubMed id', 'id'), ('title', 'Title', 'text'), ('journal', 'Journal', 'text'), ('year', 'Publication year', 'year'),
        ('manufacturer_groups', 'Manufacturer groups named', 'text')],
       skills=['disclosed-ties', 'hcp-profile'], default_panel={'type': 'table'},
       caveats=['TIES'], tables=['tie_author_paper', 'pub'], years=TIE_YEARS)
def hcp_ties_papers(con, npi, n=25):
    if not con.execute('select 1 from dim_hcp where npi = ?', [npi]).fetchone():
        raise QueryError(f'No hcp with id {npi}. Call find_entity first.')
    title, journal, join = paper_columns(con)
    return fetch(con, f'''
        select t.pmid, {title} as title, {journal} as journal, t.year, t.manufacturer_groups
        from (select pmid, publication_year as year, replace(manufacturer_groups, ';', '; ') as manufacturer_groups
              from tie_author_paper where npi = ? and named_with_cart_manufacturer = 1
              order by publication_year desc, pmid desc limit ?) t {join}
        order by t.year desc, t.pmid desc''', [npi, n])


@query('ties_vs_payments',
       'Per CAR-T manufacturer group, side by side for the HCPs of a scope: HCPs named with it in a conflict-of-interest statement '
       '(papers 2017 to 2026), HCPs with a general payment record from it that names a CAR-T product or comes from a CAR-T-only '
       'company (2019 to 2025), and HCPs with both. The two sources cover different years and different things: show both.',
       SCOPED,
       [('manufacturer_group', 'Manufacturer group', 'text'), ('hcps_with_tie', 'HCPs named with it on a paper', 'count'),
        ('tie_first_year', 'First publication year', 'year'), ('tie_last_year', 'Last publication year', 'year'),
        ('hcps_with_payment', 'HCPs with a reported CAR-T payment', 'count'), ('payment_first_year', 'First payment year', 'year'),
        ('payment_last_year', 'Last payment year', 'year'), ('hcps_with_both', 'HCPs with both', 'count')],
       skills=['disclosed-ties', 'industry-payments'], default_panel={'type': 'table'},
       caveats=['TIES', 'PAYMENTS', 'LABELS'], tables=['tie_author_group', 'tie_author_paper', 'pay_general_record', 'manufacturer'],
       years='papers 2017 to 2026; payments 2019 to 2025')
def ties_vs_payments(con, scope, id):
    return fetch(con, f'''
        with s as ({members(scope)}),
        t as (select g.manufacturer_group, g.npi, min(a.publication_year) as first, max(a.publication_year) as last
              from tie_author_group g join s on s.npi = g.npi join tie_author_paper a on a.npi = g.npi and a.pmid = g.pmid
              group by g.manufacturer_group, g.npi),
        p as (select r.manufacturer_group, r.npi, min(r.year) as first, max(r.year) as last
              from pay_general_record r join s on s.npi = r.npi group by r.manufacturer_group, r.npi)
        select m.manufacturer_group,
               (select count(*) from t where t.manufacturer_group = m.manufacturer_group) as hcps_with_tie,
               (select min(first) from t where t.manufacturer_group = m.manufacturer_group) as tie_first_year,
               (select max(last) from t where t.manufacturer_group = m.manufacturer_group) as tie_last_year,
               (select count(*) from p where p.manufacturer_group = m.manufacturer_group) as hcps_with_payment,
               (select min(first) from p where p.manufacturer_group = m.manufacturer_group) as payment_first_year,
               (select max(last) from p where p.manufacturer_group = m.manufacturer_group) as payment_last_year,
               (select count(*) from t join p on p.manufacturer_group = t.manufacturer_group and p.npi = t.npi
                where t.manufacturer_group = m.manufacturer_group) as hcps_with_both
        from manufacturer m order by m.rowid''', [id])


# ---------------------------------------------------------------- products

@query('product_facts',
       'The approved CAR-T products as the product table holds them: brand, generic and short name, company, partner, target, '
       'lymphoma product (1 yes, 0 no), Medicare billing code and first US approval date. One product when brand is given.',
       {'brand': {'type': 'string', 'enum': BRANDS, 'default': None}},
       [('brand', 'Brand', 'text'), ('generic', 'Generic name', 'text'), ('short_name', 'Short name', 'text'),
        ('company_group', 'Company', 'text'), ('partner', 'Partner', 'text'), ('target', 'Target', 'text'),
        ('lymphoma_product', 'Lymphoma product (1 yes, 0 no)', 'count'), ('hcpcs_code', 'Medicare billing code', 'text'),
        ('first_us_approval', 'First US approval', 'text')],
       skills=['product-timeline'], default_panel={'type': 'table'}, tables=['dim_product'], years='2017 to today')
def product_facts(con, brand=None):
    return fetch(con, '''
        select brand, generic, short_name, company_group, partner, target, lymphoma_product, hcpcs_code, first_us_approval
        from dim_product where ? is null or brand = ? order by first_us_approval, brand''', [brand, brand])


@query('product_events',
       'Dated approval, new-indication, coding and class-wide events, the vertical markers of time charts. With brand, only that '
       "product's events; lymphoma_only yes keeps lymphoma events. lymphoma and verified are flags (1 yes, 0 no); verified is 1 "
       'once the date was confirmed against its source.',
       {'brand': {'type': 'string', 'enum': BRANDS, 'default': None},
        'lymphoma_only': {'type': 'string', 'enum': ['yes', 'no'], 'default': 'no'}},
       [('date', 'Date', 'text'), ('label', 'Event', 'text'), ('brand', 'Brand', 'text'), ('event_type', 'Type', 'text'),
        ('lymphoma', 'Lymphoma event (1 yes, 0 no)', 'count'), ('verified', 'Date verified (1 yes, 0 no)', 'count')],
       skills=['product-timeline'], default_panel={'type': 'timeline', 'x': 'date', 'y': ['label'], 'split_by': 'brand'},
       tables=['product_event'], years='2017 to today')
def product_events(con, brand=None, lymphoma_only='no'):
    return fetch(con, '''
        select date, label, brand, event_type, lymphoma, verified
        from product_event where (? is null or brand = ?) and (? = 'no' or lymphoma = 1) order by date, label''',
                 [brand, brand, lymphoma_only])
