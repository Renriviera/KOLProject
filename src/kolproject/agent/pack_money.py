"""Money tables of the agent pack: industry payments, research payments, ownership, disclosed ties, manufacturers.

Everything is read from published tables of the 2026-10-09, 2026-11-11, 2026-11-19 and 2026-11-20 snapshots. Three
rules shape the tables:

- General payments are amounts reported by manufacturers. A record enters under the rules of the 2026-11-19
  table (`any_slot`, `cart_only`, `lymphoma_only`, `pure_play_payer`), kept as flags.
- A research amount is the total of the whole record, repeated on the row of every HCP named on it. It is stored
  as `record_total_usd` and is never money received by a person.
- Free text stays out: no research context, no product strings as typed, no ownership terms, no statement text,
  no payee names. A study name is kept only when it passed the person-and-address screen. For core records, which
  the 2026-11-11 table publishes unscreened, the same kind of screen is applied here (`core_study_status`).
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal

from ..extensions.industry import CART_MANUFACTURERS, GROUP_COLUMNS, SCREEN, manufacturer_group, research_link, screen_study
from .packbase import SNAPSHOTS, connect, create_table, load_csv, read_rows, register, source
from .paths import PACK

TABLES = ['pay_general_record', 'pay_general_record_brand', 'pay_general_year', 'pay_general_hcp_mfr_year',
          'pay_research_record', 'pay_research_year', 'pay_research_hcp_center_year', 'ownership', 'tie_paper',
          'tie_author_paper', 'tie_author_group', 'tie_hcp_year', 'manufacturer']
SRC = {'general': ('volume', 'universe_cart_general_payments_2019_2025'),
       'general_year': ('extensions', 'hcp_universe_cart_general_payments_by_year'),
       'general_features': ('volume', 'hcp_universe_general_payment_features'),
       'research_core': ('payments', 'research_payments_2019_2025'),
       'research_universe': ('extensions', 'universe_cart_research_payments_2019_2025'),
       'research_year': ('extensions', 'hcp_universe_cart_research_by_year'),
       'ownership': ('extensions', 'universe_ownership_interests_2019_2025'),
       'papers_held': ('extensions', 'publication_industry_disclosures'),
       'papers_new': ('extensions', 'new_publication_industry_disclosures'),
       'authors_core': ('extensions', 'core_author_publication_industry_ties'),
       'authors_universe': ('extensions', 'universe_author_publication_industry_ties'),
       'ties_year': ('extensions', 'core_hcp_disclosed_ties_by_year'),
       'ties_summary': ('extensions', 'core_hcp_industry_ties_summary'),
       'ties_summary_universe': ('extensions', 'universe_hcp_industry_ties_summary'),
       'publications': ('cohort', 'publications'),
       'new_publications': ('extensions', 'new_publications')}
RULES = ('any_slot', 'cart_only', 'lymphoma_only', 'pure_play_payer')
GROUPS = list(CART_MANUFACTURERS)
PAYMENT_YEARS = '2019 to 2025'
# Recipient types whose name is an organisation; any other recipient name is a person and is screened against.
ENTITY_RECIPIENTS = {'Non-covered Recipient Entity', 'Covered Recipient Teaching Hospital'}
# Column names that would mean free text, statement text or a payee name reached the pack.
FREE_TEXT_COLUMNS = ('context', 'statement_text', 'coi_statement', 'terms', 'recipient_name', 'payee', 'third_party',
                     'products', 'product_categories', 'submitting', 'withheld', 'affiliation_text')

GENERAL_COLUMNS = [('record_id', 'text'), ('npi', 'text'), ('year', 'int'), ('payment_date', 'text'), ('month', 'text'),
                   ('amount_usd', 'real'), ('manufacturer', 'text'), ('manufacturer_group', 'text'), ('nature_class', 'text'),
                   ('any_slot', 'int'), ('cart_only', 'int'), ('lymphoma_only', 'int'), ('pure_play_payer', 'int'),
                   ('development_name_only', 'int'), ('direct', 'int'), ('brands', 'text')]
GENERAL_YEAR = [('npi', 'npi', 'text'), ('year', 'program_year', 'int'), ('status', 'year_status', 'text'),
                ('records', 'records', 'int'), ('total_usd', 'usd', 'real'),
                ('any_slot_records', 'cart_any_slot_rows', 'int'), ('any_slot_usd', 'cart_any_slot_usd', 'real'),
                ('cart_only_records', 'cart_only_rows', 'int'), ('cart_only_usd', 'cart_only_usd', 'real'),
                ('cart_only_services_usd', 'cart_only_services_usd', 'real'),
                ('cart_only_hospitality_education_usd', 'cart_only_hospitality_education_usd', 'real'),
                ('cart_only_direct_services_usd', 'cart_only_direct_services_usd', 'real'),
                ('lymphoma_only_records', 'lymphoma_only_rows', 'int'), ('lymphoma_only_usd', 'lymphoma_only_usd', 'real'),
                ('lymphoma_only_services_usd', 'lymphoma_only_services_usd', 'real'),
                ('lymphoma_only_hospitality_education_usd', 'lymphoma_only_hospitality_education_usd', 'real'),
                ('pure_play_payer_records', 'pure_play_payer_rows', 'int'), ('pure_play_payer_usd', 'pure_play_payer_usd', 'real'),
                ('manufacturers', 'manufacturers', 'int')]
RESEARCH_COLUMNS = [('npi', 'text'), ('record_id', 'text'), ('year', 'int'), ('payment_date', 'text'), ('record_total_usd', 'real'),
                    ('manufacturer_group', 'text'), ('match_basis', 'text'), ('brands', 'text'), ('lymphoma_product', 'int'),
                    ('role', 'text'), ('sole_investigator', 'int'), ('study_name', 'text'), ('study_name_screen', 'text'),
                    ('nct_id', 'text'), ('teaching_hospital_ccn', 'text'), ('is_core', 'int')]
RESEARCH_YEAR = [('npi', 'npi', 'text'), ('year', 'program_year', 'int'), ('status', 'year_status', 'text'),
                 ('cart_research_records', 'cart_research_rows', 'int'), ('product_field_records', 'product_field_rows', 'int'),
                 ('text_only_records', 'text_only_rows', 'int'),
                 ('product_field_investigator_records', 'product_field_investigator_rows', 'int'),
                 ('product_field_recipient_only_records', 'product_field_recipient_only_rows', 'int'),
                 ('product_field_sole_investigator_records', 'product_field_sole_pi_rows', 'int'),
                 ('investigator_records', 'investigator_rows', 'int'),
                 ('product_field_record_total_usd', 'product_field_record_usd', 'real'),
                 ('text_only_record_total_usd', 'text_only_record_usd', 'real'),
                 ('lymphoma_product_records', 'lymphoma_product_rows', 'int'), ('product_field_brands', 'product_field_brands', 'text'),
                 ('nct_ids', 'nct_ids', 'int'), ('manufacturer_groups', 'manufacturer_groups', 'text')]
OWNERSHIP = [('record_id', 'record_id', 'text'), ('npi', 'npi', 'text'), ('year', 'program_year', 'int'),
             ('manufacturer', 'manufacturer', 'text'), ('cart_manufacturer', 'cart_manufacturer', 'int'),
             ('manufacturer_group', 'cart_manufacturer_group', 'text'), ('value_of_interest_usd', 'value_of_interest_usd', 'real'),
             ('amount_invested_usd', 'amount_invested_usd', 'real'), ('interest_holder', 'interest_holder', 'text')]
TIE_PAPER = [('pmid', 'text'), ('publication_year', 'int'), ('paper_set', 'text'), ('record_returned', 'int'), ('record_type', 'text'),
             ('statement_present', 'int'), ('manufacturer_groups', 'text'), ('manufacturer_group_count', 'int'),
             ('no_conflict_wording', 'int'), ('names_authors', 'int'), ('industry_affiliation_authors', 'int'),
             ('industry_coauthorship', 'int'), ('industry_affiliation_groups', 'text')]
TIE_AUTHOR = [('npi', 'text'), ('pmid', 'text'), ('publication_year', 'int'), ('paper_set', 'text'), ('is_core', 'int'),
              ('author_position', 'int'), ('author_located', 'text'), ('statement_status', 'text'), ('named_in_statement', 'int'),
              ('named_with_cart_manufacturer', 'int'), ('manufacturer_groups', 'text'), ('own_affiliation_cart_manufacturer', 'int')]
TIE_YEAR = [('npi', 'text'), ('year', 'int'), ('papers', 'int'), ('papers_with_statement', 'int'), ('papers_determinable', 'int'),
            ('papers_named_with_cart_manufacturer', 'int'), ('manufacturer_groups', 'text'), ('basis', 'text')]


def src(key):
    """Path of one published source table."""
    return source(*SRC[key])


def ref(key):
    """A source as 'snapshot/table', the form the manifest hashes."""
    return f'{SNAPSHOTS[SRC[key][0]]}/{SRC[key][1]}'


def cents(text):
    """Whole cents of a dollar cell, exactly; a blank is no cents."""
    text = str(text if text is not None else '').strip()
    return int((Decimal(text) * 100).to_integral_value()) if text else 0


def number(text, kind=int):
    text = str(text if text is not None else '').strip()
    return kind(float(text)) if text else None


def tokens(text):
    """Lower-case words of three letters or more, the unit the study-name screen compares."""
    return {w for w in re.findall(r'[a-z]+', (text or '').lower()) if len(w) >= 3}


def split_cents(total, parts):
    """A cent amount split equally across `parts`, the odd cents going to the first ones, so the shares add back."""
    return [total // parts + (k < total % parts) for k in range(parts)]


def brand_names(con):
    """Upper-case brand as the payment tables write it -> the brand as dim_product writes it."""
    return {b.upper(): b for (b,) in con.execute('select brand from dim_product')}


def brand_list(text, names):
    return ';'.join(names[b] for b in (text or '').split(';') if b) or None


def ordered_groups(groups):
    return ';'.join(g for g in GROUPS if g in groups) or None


# ---------------------------------------------------------------- general payments

def build_general(con, seen):
    names = brand_names(con)
    records, shares = [], []
    for r in read_rows(src('general')):
        brands = [names[b] for b in r['cart_products'].split(';') if b]
        group = manufacturer_group(r['manufacturer']) or None
        seen[group][r['manufacturer']] += 1
        records.append((r['record_id'], r['npi'], int(r['program_year']), r['payment_date'], r['payment_date'][:7],
                        float(r['amount_usd']), r['manufacturer'], group, r['nature_class'], int(r['any_slot']),
                        int(r['cart_only']), int(r['lymphoma_only']), int(r['pure_play_payer']),
                        int(r['development_name_only']), int(r['direct_payment']), ';'.join(brands) or None))
        for brand, part in zip(brands, split_cents(cents(r['amount_usd']), len(brands) or 1)):
            shares.append((r['record_id'], brand, len(brands), part / 100))
    create_table(con, 'pay_general_record', GENERAL_COLUMNS, records, primary_key=['record_id'],
                 indexes=['npi', 'year', 'month', 'manufacturer_group'])
    create_table(con, 'pay_general_record_brand', [('record_id', 'text'), ('brand', 'text'), ('brands_on_record', 'int'),
                                                   ('amount_share_usd', 'real')], shares, primary_key=['record_id', 'brand'],
                 indexes=['brand'])
    load_csv(con, 'pay_general_year', src('general_year'), [(c, k) for c, _, k in GENERAL_YEAR],
             rename={c: s for c, s, _ in GENERAL_YEAR}, primary_key=['npi', 'year'], indexes=['year'])
    services = "case when nature_class = 'services' then amount_usd else 0 end"
    by_group = con.execute(f'''
        select npi, manufacturer_group, year, count(*), round(sum(amount_usd), 2), round(sum({services}), 2),
               sum(lymphoma_only), round(sum(amount_usd * lymphoma_only), 2), round(sum({services} * lymphoma_only), 2)
        from pay_general_record where cart_only = 1 group by npi, manufacturer_group, year''').fetchall()
    create_table(con, 'pay_general_hcp_mfr_year', [
        ('npi', 'text'), ('manufacturer_group', 'text'), ('year', 'int'), ('cart_only_records', 'int'), ('cart_only_usd', 'real'),
        ('cart_only_services_usd', 'real'), ('lymphoma_only_records', 'int'), ('lymphoma_only_usd', 'real'),
        ('lymphoma_only_services_usd', 'real')], by_group, primary_key=['npi', 'manufacturer_group', 'year'],
        indexes=['manufacturer_group', 'year'])
    register(con, 'pay_general_record', 'payment record', [ref('general')],
             'General payments (consulting, speaking, meals, travel, education) to an HCP that name a CAR-T product or come '
             'from a CAR-T-only company, with the date, amount, manufacturer group, nature class and record-rule flags.', PAYMENT_YEARS)
    register(con, 'pay_general_record_brand', 'payment record x CAR-T brand', [ref('general')],
             'The CAR-T brands named on each general payment record; a record naming several brands has its amount split '
             'equally across them.', PAYMENT_YEARS)
    register(con, 'pay_general_year', 'HCP x year', [ref('general_year')],
             'Yearly general payments per HCP under the cart_only and lymphoma_only rules, with services and hospitality '
             'parts; only HCPs with at least one record appear, with all seven years.', PAYMENT_YEARS)
    register(con, 'pay_general_hcp_mfr_year', 'HCP x manufacturer group x year', [ref('general')],
             'Dollars and records of cart_only and lymphoma_only general payments per HCP, manufacturer group and year; '
             'derived from pay_general_record.', PAYMENT_YEARS)
    return len(records)


# ---------------------------------------------------------------- research payments

def core_study_status(row, people, investigators, name_tokens, published):
    """Screen of a core record's study name: 'passed', 'withheld' or 'none_reported'.

    The same kind of screen as the 2026-11-20 universe table: the pattern for e-mails, telephone numbers, street
    addresses and honorifics, then the names of the people on the record. Here the people are the universe HCPs
    named on the record and a recipient who is a person. A record with an investigator outside the universe cannot
    be checked against that person's name, so its study name is withheld, unless the record is also in the universe
    table, whose screen saw every name on it. A name that table withheld is withheld here too.
    """
    study = row['study_name'].strip()
    if not study:
        return 'none_reported'
    record = row['record_id']
    if published.get(record, 'passed') != 'passed':
        return 'withheld'
    if record not in published and (number(row['record_pi_count']) or 0) > len(investigators[record]):
        return 'withheld'
    known = set().union(*(name_tokens.get(npi, set()) for npi in people[record]))
    if row['recipient_type'] not in ENTITY_RECIPIENTS:
        known |= tokens(row['recipient_name'])
    return 'passed' if screen_study(study, known) == 'passed' else 'withheld'


def research_row(row, link, core, role, status, names):
    study = ' '.join(row['study_name'].split())
    return (row['npi'], row['record_id'], int(row['program_year']), row['payment_date'] or None, float(row['amount_usd']),
            link['group'] or None, 'product_field' if link['product'] else 'study_or_context_text',
            brand_list(link['brands'], names), int(bool(link['product'] and link['lymphoma'])), role, int(bool(link['sole_pi'])),
            study if status == 'passed' else None, status, row['nct_id'].strip() or None,
            row['teaching_hospital_ccn'].strip() or None, core)


def build_research(con, seen):
    names = brand_names(con)
    name_tokens = {npi: tokens(' '.join(part for part in parts if part))
                   for npi, *parts in con.execute('select npi, name, first_name, last_name from dim_hcp')}
    core = [(r, link) for r in read_rows(src('research_core')) if (link := research_link(r, True))]
    universe = [(r, research_link(r, False)) for r in read_rows(src('research_universe'))]
    people, investigators = defaultdict(set), defaultdict(set)
    for r, link in core + universe:
        people[r['record_id']].add(r['npi'])
        if link['investigator']:
            investigators[r['record_id']].add(r['npi'])
        if link['group']:
            seen[link['group']][r['manufacturer']] += 1
    published = {r['record_id']: r['study_name_screen'] for r, _ in universe}
    rows = [research_row(r, link, 1, 'investigator' if link['investigator'] else 'recipient_only',
                         core_study_status(r, people, investigators, name_tokens, published), names) for r, link in core]
    screen = {'passed': 'passed', 'failed': 'withheld'}
    rows += [research_row(r, link, 0, r['role'], screen.get(r['study_name_screen'], 'none_reported'), names) for r, link in universe]
    create_table(con, 'pay_research_record', RESEARCH_COLUMNS, rows, primary_key=['npi', 'record_id'],
                 indexes=['record_id', 'year', 'teaching_hospital_ccn'])
    load_csv(con, 'pay_research_year', src('research_year'), [(c, k) for c, _, k in RESEARCH_YEAR],
             rename={c: s for c, s, _ in RESEARCH_YEAR}, primary_key=['npi', 'year'], indexes=['year'])
    links = con.execute('''
        select npi, teaching_hospital_ccn, year, count(*), round(sum(record_total_usd), 2) from pay_research_record
        where match_basis = 'product_field' and teaching_hospital_ccn in (select ccn from dim_center)
        group by npi, teaching_hospital_ccn, year''').fetchall()
    create_table(con, 'pay_research_hcp_center_year', [('npi', 'text'), ('ccn', 'text'), ('year', 'int'), ('records', 'int'),
                                                       ('usd', 'real')], links, primary_key=['npi', 'ccn', 'year'], indexes=['ccn', 'year'])
    register(con, 'pay_research_record', 'research record x HCP named on it', [ref('research_core'), ref('research_universe')],
             'CAR-T research payment records naming an HCP as investigator or recipient: match basis (a product field, or '
             'study text only), brands, role, screened study name, ClinicalTrials.gov id and teaching hospital. '
             'record_total_usd is the whole record, repeated for every HCP on it, and is not personal income.', PAYMENT_YEARS)
    register(con, 'pay_research_year', 'HCP x year', [ref('research_year')],
             'Yearly CAR-T research records per HCP named on at least one: product-field and text-only records, records as '
             'investigator, and record totals (not personal income).', PAYMENT_YEARS)
    register(con, 'pay_research_hcp_center_year', 'HCP x center x year', [ref('research_core'), ref('research_universe')],
             'Product-field CAR-T research records linking an HCP to the CAR-T center named as teaching-hospital recipient; '
             'usd is the sum of record totals, not personal income.', PAYMENT_YEARS)
    return len(rows)


# ---------------------------------------------------------------- ownership

def build_ownership(con, seen):
    for r in read_rows(src('ownership')):
        if r['cart_manufacturer_group']:
            seen[r['cart_manufacturer_group']][r['manufacturer']] += 1
    rows = load_csv(con, 'ownership', src('ownership'), [(c, k) for c, _, k in OWNERSHIP], rename={c: s for c, s, _ in OWNERSHIP},
                    primary_key=['record_id'], indexes=['npi', 'year'])
    register(con, 'ownership', 'ownership record', [ref('ownership')],
             'Ownership and investment interests of universe physicians as reported by companies, with a flag for CAR-T '
             'manufacturers; the reported terms are not loaded.', PAYMENT_YEARS)
    return rows


# ---------------------------------------------------------------- disclosed ties

def paper_years():
    years = {r['pmid']: int(r['publication_year']) for r in read_rows(src('publications'))}
    years.update({r['pmid']: int(r['publication_year']) for r in read_rows(src('new_publications'))})
    return years


def paper_rows(years):
    for key, paper_set in (('papers_held', 'held'), ('papers_new', 'new_2026')):
        for r in read_rows(src(key)):
            yield (r['pmid'], years.get(r['pmid']), paper_set, int(r['pubmed_record_status'] == 'returned'),
                   r['pubmed_record_type'] or None, number(r['coi_statement_present']), r['coi_cart_manufacturers'] or None,
                   number(r['coi_cart_manufacturer_count']), number(r['coi_no_conflict_wording']), number(r['coi_names_authors']),
                   number(r['industry_affiliation_authors']), number(r['industry_coauthorship']),
                   r['industry_affiliation_groups'] or None)


def author_rows(years):
    for key in ('authors_core', 'authors_universe'):
        for r in read_rows(src(key)):
            held = key == 'authors_core' or r['link_table'] == 'universe_authorships'
            yield (r['npi'], r['pmid'], years.get(r['pmid']), 'held' if held else 'new_2026',
                   1 if key == 'authors_core' else int(r['is_core']), number(r['author_position']), r['pubmed_author_located'] or None,
                   r['statement_status'] or None, number(r['named_in_statement']), number(r['named_with_cart_manufacturer']),
                   r['cart_manufacturers_named_for_author'] or None, number(r['own_affiliation_cart_manufacturer']))


def tie_years(links):
    """{(npi, year): [papers, with statement, determinable, named, groups]} with the published core table's rules."""
    out = {}
    for npi, _, year, _, _, _, _, status, _, named, groups, _ in links:
        cell = out.setdefault((npi, year), [0, 0, 0, 0, set()])
        cell[0] += 1
        cell[1] += status not in (None, 'no_statement')
        cell[2] += named is not None
        cell[3] += named == 1
        cell[4].update(g for g in (groups or '').split(';') if g)
    return out


def build_ties(con):
    years = paper_years()
    links = list(author_rows(years))
    create_table(con, 'tie_paper', TIE_PAPER, paper_rows(years), primary_key=['pmid'], indexes=['publication_year'])
    create_table(con, 'tie_author_paper', TIE_AUTHOR, links, primary_key=['npi', 'pmid'], indexes=['pmid', 'publication_year'])
    create_table(con, 'tie_author_group', [('npi', 'text'), ('pmid', 'text'), ('manufacturer_group', 'text')],
                 ((r[0], r[1], g) for r in links for g in (r[10] or '').split(';') if g),
                 primary_key=['npi', 'pmid', 'manufacturer_group'], indexes=['manufacturer_group', 'pmid'])
    published = [(r['npi'], int(r['publication_year']), int(r['held_papers']), int(r['papers_with_coi_statement']),
                  int(r['papers_determinable']), int(r['papers_named_with_cart_manufacturer']),
                  ordered_groups({g for g, label in GROUP_COLUMNS.items() if int(r[f'papers_named_with_{label}'])}),
                  'published_core_table') for r in read_rows(src('ties_year'))]
    derived = tie_years(r for r in links if not (r[4] == 1 and r[3] == 'held'))
    assert not {(r[0], r[1]) for r in published} & set(derived), 'A core HCP has a 2026-corpus paper in a year of the published table'
    rows = published + [(npi, year, *cell[:4], ordered_groups(cell[4]), 'derived_from_author_links')
                        for (npi, year), cell in sorted(derived.items())]
    create_table(con, 'tie_hcp_year', TIE_YEAR, rows, primary_key=['npi', 'year'], indexes=['year'])
    sources = [ref('authors_core'), ref('authors_universe'), ref('publications'), ref('new_publications')]
    register(con, 'tie_paper', 'paper', [ref('papers_held'), ref('papers_new'), ref('publications'), ref('new_publications')],
             'Per paper: whether PubMed carries a conflict-of-interest statement and which CAR-T manufacturer groups it '
             'names; flags only, never the statement text.', '2017 to 2026')
    register(con, 'tie_author_paper', 'HCP x paper', sources,
             'Per author link: whether the statement names the HCP with a CAR-T manufacturer group (1, 0, or blank when it '
             'cannot be read), and the groups named.', '2017 to 2026')
    register(con, 'tie_author_group', 'HCP x paper x manufacturer group', sources,
             'One row per CAR-T manufacturer group named for an HCP on a paper.', '2017 to 2026')
    register(con, 'tie_hcp_year', 'HCP x publication year', [ref('ties_year')] + sources,
             'Papers per HCP and publication year: with a statement, readable, and naming the HCP with a CAR-T manufacturer. '
             'Core HCPs on held papers come from the published table; non-core HCPs and 2026 papers are derived the same way.',
             '2017 to 2026')
    return len(links)


def build_manufacturers(con, seen):
    products = con.execute('select brand, company_group, partner from dim_product order by first_us_approval').fetchall()
    rows = []
    for group in GROUPS:
        spelled = {}
        for name, n in seen[group].most_common():
            spelled.setdefault(name.lower(), name)
        rows.append((group, '; '.join(spelled.values()) or None, ';'.join(b for b, g, _ in products if g == group) or None,
                     ';'.join(b for b, _, p in products if p == group) or None))
    create_table(con, 'manufacturer', [('manufacturer_group', 'text'), ('company_names', 'text'), ('brands', 'text'),
                                       ('partner_brands', 'text')], rows, primary_key=['manufacturer_group'])
    register(con, 'manufacturer', 'CAR-T manufacturer group', [ref('general'), ref('research_core'), ref('research_universe'), ref('ownership')],
             'The seven CAR-T manufacturer groups with the company names seen on payment records and their products.', PAYMENT_YEARS)
    return len(rows)


def build(con):
    seen = defaultdict(Counter)
    summary = {'pay_general_record': build_general(con, seen), 'pay_research_record': build_research(con, seen),
               'ownership': build_ownership(con, seen), 'tie_author_paper': build_ties(con)}
    summary['manufacturer'] = build_manufacturers(con, seen)
    return summary


# ---------------------------------------------------------------- card facts

def money(amount):
    return f'${amount:,.0f}'


def span(first, last):
    return str(first) if first == last else f'{first} to {last}'


def plural(n, word):
    return f'{n:,} {word}' + ('' if n == 1 else 's')


def listed(items, top=3):
    return ', '.join(list(items)[:top])


def hcp_payment_facts(con, facts):
    """One sentence per HCP with a general payment record, under the narrowest rule the HCP has records for."""
    payers = defaultdict(list)
    for npi, group, lymphoma, cart in con.execute('''
            select npi, manufacturer_group, sum(lymphoma_only), sum(cart_only) from pay_general_record
            where manufacturer_group is not null group by npi, manufacturer_group order by npi, sum(amount_usd) desc'''):
        payers[npi].append((group, lymphoma, cart))
    year = lambda rule, pick: f'{pick}(case when {rule} = 1 then year end)'
    for (npi, lym_n, lym, lym_first, lym_last, cart_n, cart, cart_first, cart_last, n, total, first, last) in con.execute(f'''
            select npi, sum(lymphoma_only), sum(amount_usd * lymphoma_only), {year('lymphoma_only', 'min')}, {year('lymphoma_only', 'max')},
                   sum(cart_only), sum(amount_usd * cart_only), {year('cart_only', 'min')}, {year('cart_only', 'max')},
                   count(*), sum(amount_usd), min(year), max(year) from pay_general_record group by npi'''):
        if lym_n:
            more = f'; {money(cart)} on {plural(cart_n, "record")} for all CAR-T products' if cart_n > lym_n else ''
            text = (f'Manufacturers reported {money(lym)} in general payments naming lymphoma CAR-T products on {plural(lym_n, "record")} '
                    f'in {span(lym_first, lym_last)} ({listed(g for g, k, _ in payers[npi] if k)}){more}.')
        elif cart_n:
            text = (f'Manufacturers reported {money(cart)} in general payments naming CAR-T products on {plural(cart_n, "record")} '
                    f'in {span(cart_first, cart_last)} ({listed(g for g, _, k in payers[npi] if k)}); none names a lymphoma CAR-T product.')
        else:
            text = (f'Manufacturers reported {plural(n, "general payment record")} ({money(total)}) in {span(first, last)} from a '
                    f'CAR-T-only company or naming a CAR-T product beside another product ({listed(g for g, _, _ in payers[npi])}).')
        facts[('hcp', npi)].append(text)


def hcp_research_facts(con, facts):
    for npi, product, investigator, first, last, text_only, any_first, any_last, groups in con.execute('''
            select npi, sum(product_field_records), sum(product_field_investigator_records),
                   min(case when product_field_records > 0 then year end), max(case when product_field_records > 0 then year end),
                   sum(text_only_records), min(case when cart_research_records > 0 then year end),
                   max(case when cart_research_records > 0 then year end), group_concat(manufacturer_groups, ';')
            from pay_research_year group by npi'''):
        if product:
            named = ordered_groups(set((groups or '').split(';')))
            paid = f", paid by {named.replace(';', ', ')}" if named else ''
            text = (f'Named on {plural(product, "research payment record")} naming a CAR-T product in {span(first, last)} '
                    f'({investigator:,} as investigator){paid}; the amounts are study totals, not personal income.')
        else:
            text = (f'Named on {plural(text_only, "research payment record")} matched to CAR-T by study text only, '
                    f'in {span(any_first, any_last)}.')
        facts[('hcp', npi)].append(text)


def hcp_tie_facts(con, facts):
    groups = defaultdict(set)
    for npi, group in con.execute('select distinct npi, manufacturer_group from tie_author_group'):
        groups[npi].add(group)
    named_year = lambda pick: f'{pick}(case when named_with_cart_manufacturer = 1 then publication_year end)'
    for npi, papers, named, first, last in con.execute(f'''
            select npi, count(*), sum(named_with_cart_manufacturer = 1), {named_year('min')}, {named_year('max')}
            from tie_author_paper group by npi having sum(named_with_cart_manufacturer = 1) > 0'''):
        facts[('hcp', npi)].append(
            f'Named with a CAR-T manufacturer in the conflict-of-interest statement of {named:,} of {plural(papers, "linked paper")}, '
            f"published {span(first, last)}: {ordered_groups(groups[npi]).replace(';', ', ')}.")


def group_scope_facts(con, facts, kind, members, noun):
    """Sentences for communities or centers; `members` is SQL giving (id, npi) pairs."""
    scope = f'with s as ({members}) '
    early = 'sum(case when g.year <= 2021 then g.lymphoma_only_usd else 0 end)'
    late = 'sum(case when g.year >= 2022 then g.lymphoma_only_usd else 0 end)'
    top = defaultdict(list)
    for ident, group, usd in con.execute(scope + '''
            select s.id, m.manufacturer_group, sum(m.lymphoma_only_usd) from s join pay_general_hcp_mfr_year m on m.npi = s.npi
            group by s.id, m.manufacturer_group having sum(m.lymphoma_only_usd) > 0 order by s.id, 3 desc'''):
        top[ident].append(f'{group} {money(usd)}')
    for ident, hcps, records, first, second, cart in con.execute(scope + f'''
            select s.id, count(distinct case when g.lymphoma_only_records > 0 then g.npi end), sum(g.lymphoma_only_records),
                   {early}, {late}, sum(g.cart_only_usd)
            from s join pay_general_year g on g.npi = s.npi group by s.id having sum(g.cart_only_records) > 0''').fetchall():
        facts[(kind, str(ident))].append(
            f'General payments naming lymphoma CAR-T products to HCPs {noun}: {money(first)} in 2019 to 2021 and '
            f'{money(second)} in 2022 to 2025, to {plural(hcps, "HCP")} on {plural(records, "record")}; {money(cart)} for all '
            'CAR-T products in 2019 to 2025.')
        if top[ident]:
            facts[(kind, str(ident))].append(f'Manufacturer groups paying most on lymphoma CAR-T products, 2019 to 2025: {listed(top[ident])}.')
    research = {i: (h, r) for i, h, r in con.execute(scope + '''
        select s.id, count(distinct p.npi), count(distinct p.record_id) from s
        join pay_research_record p on p.npi = s.npi where p.match_basis = 'product_field' group by s.id''')}
    ties = dict(con.execute(scope + '''select s.id, count(distinct a.npi) from s join tie_author_paper a on a.npi = s.npi
                                       where a.named_with_cart_manufacturer = 1 group by s.id''').fetchall())
    for ident in sorted(set(research) | set(ties), key=str):
        parts = []
        if ident in research:
            hcps, records = research[ident]
            parts.append(f'{plural(hcps, "HCP")} {noun} {"is" if hcps == 1 else "are"} named on {plural(records, "research payment record")} '
                         'naming a CAR-T product, 2019 to 2025')
        if ident in ties:
            parts.append(f'{plural(ties[ident], "HCP")} {"is" if ties[ident] == 1 else "are"} named with a CAR-T manufacturer in a '
                         "paper's conflict-of-interest statement, 2017 to 2026")
        facts[(kind, str(ident))].append('; '.join(parts) + '.')


def manufacturer_facts(con, facts):
    paid = {g: row for g, *row in con.execute('''
        select manufacturer_group, count(distinct npi), sum(cart_only_records), sum(cart_only_usd),
               count(distinct case when lymphoma_only_records > 0 then npi end), sum(lymphoma_only_usd)
        from pay_general_hcp_mfr_year group by 1''')}
    every = {g: row for g, *row in con.execute('select manufacturer_group, count(distinct npi), count(*), sum(amount_usd), count(brands) '
                                               'from pay_general_record group by 1')}
    research = {g: row for g, *row in con.execute('''
        select manufacturer_group, count(distinct npi), count(distinct record_id) from pay_research_record
        where match_basis = 'product_field' group by 1''')}
    ties = {g: row for g, *row in con.execute('select manufacturer_group, count(distinct npi), count(distinct pmid) from tie_author_group group by 1')}
    for group in GROUPS:
        if group in paid:
            hcps, records, usd, lym_hcps, lym = paid[group]
            part = ('none names a lymphoma CAR-T product' if not lym_hcps else 'all of them name a lymphoma CAR-T product' if lym_hcps == hcps
                    and round(lym, 2) == round(usd, 2) else f'{money(lym)} to {plural(lym_hcps, "HCP")} names a lymphoma CAR-T product')
            facts[('manufacturer', group)].append(f'General payments on records naming only CAR-T products, 2019 to 2025: {money(usd)} to '
                                                  f'{plural(hcps, "HCP")} on {plural(records, "record")}; {part}.')
        elif group in every:
            hcps, records, usd, named = every[group]
            part = 'none names only CAR-T products' if named else 'none names a CAR-T product in its product fields'
            facts[('manufacturer', group)].append(f'General payments from this CAR-T company, 2019 to 2025: {money(usd)} to '
                                                  f'{plural(hcps, "HCP")} on {plural(records, "record")}; {part}.')
        else:
            facts[('manufacturer', group)].append('No general payment record to the HCP universe in 2019 to 2025.')
        if group in research:
            hcps, records = research[group]
            facts[('manufacturer', group)].append(
                f'Pays {plural(records, "research record")} naming a CAR-T product that name {plural(hcps, "HCP")}, 2019 to 2025; '
                'research amounts are study totals, not personal income.')
        if group in ties:
            hcps, papers = ties[group]
            facts[('manufacturer', group)].append(
                f'Named with {plural(hcps, "HCP")} in conflict-of-interest statements on {plural(papers, "paper")}, 2017 to 2026.')


def product_facts(con, facts):
    paid = {b: row for b, *row in con.execute('''
        select x.brand, count(distinct case when r.cart_only = 1 then r.npi end), sum(r.cart_only), sum(x.amount_share_usd * r.cart_only),
               min(case when r.cart_only = 1 then r.year end), max(case when r.cart_only = 1 then r.year end), count(*)
        from pay_general_record_brand x join pay_general_record r on r.record_id = x.record_id group by 1''')}
    research = defaultdict(lambda: [set(), set()])
    for npi, record, brands in con.execute("select npi, record_id, brands from pay_research_record where match_basis = 'product_field'"):
        for brand in (brands or '').split(';'):
            research[brand][0].add(npi)
            research[brand][1].add(record)
    for (brand,) in con.execute('select brand from dim_product').fetchall():
        hcps, records, usd, first, last, any_slot = paid.get(brand, (0, 0, 0, None, None, 0))
        beside = f'{any_slot - records:,} records name it beside a product that is not a CAR-T therapy'
        if records:
            facts[('product', brand)].append(
                f'General payments on records naming {brand} and only CAR-T products: {money(usd)} to {plural(hcps, "HCP")} on '
                f'{plural(records, "record")} in {span(first, last)}' + (f'; another {beside}.' if any_slot > records else '.'))
        else:
            facts[('product', brand)].append(f'No general payment record to the HCP universe names {brand} in 2019 to 2025.' if not any_slot
                                             else f'No general payment record names {brand} with only CAR-T products in 2019 to 2025; {beside}.')
        if brand in research:
            hcps, records = research[brand]
            facts[('product', brand)].append(
                f'{plural(len(records), "research payment record")} naming {brand} name {plural(len(hcps), "HCP")}, 2019 to 2025; '
                'research amounts are study totals, not personal income.')


def card_facts(con):
    """Up to three sentences per HCP, community, center, manufacturer group and product, from the money tables."""
    facts = defaultdict(list)
    hcp_payment_facts(con, facts)
    hcp_research_facts(con, facts)
    hcp_tie_facts(con, facts)
    named = "select community_id from dim_community where kind = 'named'"   # the isolated HCPs share one card: no totals for them
    group_scope_facts(con, facts, 'community', f'select community_id as id, npi from dim_hcp where community_id in ({named})',
                      'attached to this community')
    group_scope_facts(con, facts, 'center', 'select ccn as id, npi from hcp_center', 'linked to this center')
    manufacturer_facts(con, facts)
    product_facts(con, facts)
    return {key: sentences[:3] for key, sentences in facts.items()}


# ---------------------------------------------------------------- checks

def cent(expression):
    """SQL giving a dollar expression in whole cents, so that every comparison is exact."""
    return f'cast(round({expression} * 100) as integer)'


def db_cents(con, table, column, where='1 = 1'):
    return con.execute(f'select coalesce(sum({cent(column)}), 0) from "{table}" where {where}').fetchone()[0]


def keyed(con, sql, width=2):
    """{first `width` columns: the other columns} for every row of a statement."""
    return {tuple(r[:width]): tuple(r[width:]) for r in con.execute(sql)}


def differences(found, expected):
    """Keys whose values differ between two dicts, plus keys in only one of them."""
    return sum(found.get(k) != expected.get(k) for k in set(found) | set(expected))


def check_general_records(con, one):
    """pay_general_record against its source: rows, dollars, keys and each record rule."""
    source_rows = list(read_rows(src('general')))
    out = {'money_general_rows': abs(one('select count(*) from pay_general_record') - len(source_rows)),
           'money_general_usd': abs(db_cents(con, 'pay_general_record', 'amount_usd') - sum(cents(r['amount_usd']) for r in source_rows)),
           'money_general_record_id_repeats': one('select count(*) - count(distinct record_id) from pay_general_record'),
           'money_general_without_group': one('select count(*) from pay_general_record where manufacturer_group is null'),
           'money_general_bad_month': one("select count(*) from pay_general_record where month not between '2019-01' and '2025-12' "
                                          'or cast(substr(month, 1, 4) as integer) != year')}
    for rule in RULES:
        expected = [r for r in source_rows if r[rule] == '1']
        out[f'money_general_{rule}'] = (abs(one(f'select count(*) from pay_general_record where {rule} = 1') - len(expected))
                                        + abs(db_cents(con, 'pay_general_record', 'amount_usd', f'{rule} = 1')
                                              - sum(cents(r['amount_usd']) for r in expected)))
    return out


def check_brands(con, one):
    """The brand table: a row for every brand named, shares adding back to the record, brands known to dim_product."""
    return {
        'money_brand_split': one(f'''
            select count(*) from pay_general_record r left join (
                select record_id, count(*) as n, sum({cent('amount_share_usd')}) as cents, max(brands_on_record) as stated
                from pay_general_record_brand group by record_id) b on b.record_id = r.record_id
            where (r.brands is null) != (b.record_id is null)
               or (b.record_id is not null and (b.cents != {cent('r.amount_usd')} or b.n != b.stated
                   or b.n != length(r.brands) - length(replace(r.brands, ';', '')) + 1))'''),
        'money_brand_unknown': one('select count(*) from pay_general_record_brand where brand not in (select brand from dim_product) '
                                   'or record_id not in (select record_id from pay_general_record)'),
        'money_brand_without_any_slot': one('select count(*) from pay_general_record where (brands is not null) != (any_slot = 1)')}


def check_general_years(con, one):
    """pay_general_year against its source, the records and the 2026-11-19 features; the manufacturer table against the records."""
    yearly = list(read_rows(src('general_year')))
    out = {'money_general_year_rows': abs(one('select count(*) from pay_general_year') - len(yearly)),
           'money_general_year_usd': sum(abs(db_cents(con, 'pay_general_year', c) - sum(cents(r[s]) for r in yearly))
                                         for c, s, kind in GENERAL_YEAR if kind == 'real')}
    amount = cent('amount_usd')
    services, hospitality = f"{amount} * (nature_class = 'services')", f"{amount} * (nature_class = 'hospitality_education')"
    by_rule = ', '.join(f'sum({rule}), sum({amount} * {rule})' for rule in RULES)
    recomputed = keyed(con, f'''
        select npi, year, count(*), sum({amount}), {by_rule}, sum({services} * cart_only), sum({hospitality} * cart_only),
               sum({services} * cart_only * direct), sum({services} * lymphoma_only), sum({hospitality} * lymphoma_only)
        from pay_general_record group by npi, year''')
    table = keyed(con, f'''
        select npi, year, records, {cent('total_usd')}, any_slot_records, {cent('any_slot_usd')}, cart_only_records, {cent('cart_only_usd')},
               lymphoma_only_records, {cent('lymphoma_only_usd')}, pure_play_payer_records, {cent('pure_play_payer_usd')},
               {cent('cart_only_services_usd')}, {cent('cart_only_hospitality_education_usd')}, {cent('cart_only_direct_services_usd')},
               {cent('lymphoma_only_services_usd')}, {cent('lymphoma_only_hospitality_education_usd')} from pay_general_year''')
    out['money_general_year_vs_records'] = (
        sum(table[k] != recomputed.get(k, (0,) * 15) for k in table) + len(set(recomputed) - set(table))
        + one("select count(*) from pay_general_year where (status = 'records_in_year') != (records > 0)"))
    shared = [('cart_any_slot_rows', 'any_slot_records'), ('cart_any_slot_usd', 'any_slot_usd'), ('cart_only_rows', 'cart_only_records'),
              ('cart_only_usd', 'cart_only_usd'), ('cart_only_services_usd', 'cart_only_services_usd'),
              ('cart_only_direct_services_usd', 'cart_only_direct_services_usd'), ('lymphoma_only_rows', 'lymphoma_only_records'),
              ('lymphoma_only_usd', 'lymphoma_only_usd'), ('lymphoma_only_services_usd', 'lymphoma_only_services_usd'),
              ('pure_play_payer_rows', 'pure_play_payer_records'), ('pure_play_payer_usd', 'pure_play_payer_usd')]
    summed = keyed(con, 'select npi, ' + ', '.join(cent(f'sum({c})') if c.endswith('_usd') else f'sum({c})' for _, c in shared)
                   + ' from pay_general_year group by npi', 1)
    features = {(r['npi'],): tuple(cents(r[f'{name}_2019_2025']) if name.endswith('_usd') else int(r[f'{name}_2019_2025'])
                                   for name, _ in shared) for r in read_rows(src('general_features'))}
    out['money_general_year_vs_features'] = (sum(summed.get(npi, (0,) * len(shared)) != row for npi, row in features.items())
                                             + len(set(summed) - set(features)))
    by_group = keyed(con, f'''
        select npi, manufacturer_group, year, cart_only_records, {cent('cart_only_usd')}, {cent('cart_only_services_usd')},
               lymphoma_only_records, {cent('lymphoma_only_usd')}, {cent('lymphoma_only_services_usd')} from pay_general_hcp_mfr_year''', 3)
    from_records = keyed(con, f'''
        select npi, manufacturer_group, year, count(*), sum({amount}), sum({services}), sum(lymphoma_only), sum({amount} * lymphoma_only),
               sum({services} * lymphoma_only) from pay_general_record where cart_only = 1 group by 1, 2, 3''', 3)
    out['money_general_mfr_year_vs_records'] = differences(by_group, from_records)
    return out


def check_research_records(con, one):
    """pay_research_record against its two sources, and the rule that a study name is shown only when screened."""
    core = [r for r in read_rows(src('research_core')) if r['cart_product_mentioned'] == '1' or r['cart_text_match'] == '1']
    universe = list(read_rows(src('research_universe')))
    out = {'money_research_rows': (abs(one('select count(*) from pay_research_record where is_core = 1') - len(core))
                                   + abs(one('select count(*) from pay_research_record where is_core = 0') - len(universe))),
           'money_research_usd': abs(db_cents(con, 'pay_research_record', 'record_total_usd')
                                     - sum(cents(r['amount_usd']) for r in core + universe)),
           'money_research_key_repeats': one("select count(*) - count(distinct npi || '|' || record_id) from pay_research_record"),
           'money_research_core_flag': one('select count(*) from pay_research_record p join dim_hcp h on h.npi = p.npi '
                                           'where h.is_core != p.is_core'),
           'money_research_record_disagrees': one('''select count(*) from (select record_id from pay_research_record group by record_id
               having count(distinct year) > 1 or count(distinct match_basis) > 1 or count(distinct record_total_usd) > 1
                   or count(distinct coalesce(teaching_hospital_ccn, '')) > 1 or count(distinct study_name_screen) > 1)''')}
    published = {(r['npi'], r['record_id']): ' '.join(r['study_name'].split()) for r in universe if r['study_name_screen'] == 'passed'}
    own_name = {npi: tokens(f'{first} {last}') for npi, first, last in con.execute('select npi, first_name, last_name from dim_hcp')}
    bad = 0
    for npi, record, study, status, is_core in con.execute('select npi, record_id, study_name, study_name_screen, is_core from pay_research_record'):
        if study is None:
            bad += status == 'passed'
        else:
            bad += status != 'passed' or bool(SCREEN.search(study)) or bool(own_name.get(npi, set()) & tokens(study))
            bad += (not is_core) and published.get((npi, record)) != study
    out['money_research_study_names'] = bad
    return out


def check_research_years(con, one):
    """pay_research_year against its source and against the records; the map link table against the records."""
    yearly = list(read_rows(src('research_year')))
    out = {'money_research_year_rows': abs(one('select count(*) from pay_research_year') - len(yearly)),
           'money_research_year_usd': sum(abs(db_cents(con, 'pay_research_year', c) - sum(cents(r[s]) for r in yearly))
                                          for c, s, kind in RESEARCH_YEAR if kind == 'real')}
    cells = defaultdict(lambda: [0] * 10 + [set(), set(), set()])
    for npi, year, amount, basis, role, sole, lymphoma, brands, nct, group in con.execute(f'''
            select npi, year, {cent('record_total_usd')}, match_basis, role, sole_investigator, lymphoma_product, brands, nct_id,
                   manufacturer_group from pay_research_record'''):
        cell, product, investigator = cells[npi, year], basis == 'product_field', role == 'investigator'
        for k, add in enumerate((1, product, not product, product and investigator, product and not investigator, product and sole,
                                 investigator, amount * product, amount * (not product), lymphoma)):
            cell[k] += int(add)
        cell[10].update(b for b in (brands or '').split(';') if b and product)
        cell[11].update([nct] if nct else [])
        cell[12].update([group] if group else [])
    found = {key: (*cell[:10], ';'.join(sorted(cell[10])), len(cell[11]), ordered_groups(cell[12])) for key, cell in cells.items()}
    brand_of = brand_names(con)
    table = {key: (*r[:10], ';'.join(sorted(brand_of[b] for b in (r[10] or '').split(';') if b)), r[11], r[12]) for key, r in keyed(con, f'''
        select npi, year, cart_research_records, product_field_records, text_only_records, product_field_investigator_records,
               product_field_recipient_only_records, product_field_sole_investigator_records, investigator_records,
               {cent('product_field_record_total_usd')}, {cent('text_only_record_total_usd')}, lymphoma_product_records,
               product_field_brands, nct_ids, manufacturer_groups from pay_research_year''').items()}
    zero = (*(0,) * 10, '', 0, None)
    out['money_research_vs_year'] = sum(table[k] != found.get(k, zero) for k in table) + len(set(found) - set(table))
    columns = [r[1] for r in con.execute('pragma table_info(pay_research_hcp_center_year)')]
    links = keyed(con, f"select npi, ccn, year, records, {cent('usd')} from pay_research_hcp_center_year", 3)
    again = keyed(con, f'''
        select npi, teaching_hospital_ccn, year, count(*), sum({cent('record_total_usd')}) from pay_research_record
        where match_basis = 'product_field' and teaching_hospital_ccn in (select ccn from dim_center) group by 1, 2, 3''', 3)
    out['money_research_center_links'] = (int(columns != ['npi', 'ccn', 'year', 'records', 'usd']) + differences(links, again))
    return out


def check_ownership(con, one):
    source_rows = list(read_rows(src('ownership')))
    dollars = lambda column: abs(db_cents(con, 'ownership', column) - sum(cents(r[column]) for r in source_rows))
    return {'money_ownership_rows': abs(one('select count(*) from ownership') - len(source_rows)),
            'money_ownership_usd': dollars('value_of_interest_usd') + dollars('amount_invested_usd'),
            'money_ownership_record_id_repeats': one('select count(*) - count(distinct record_id) from ownership'),
            'money_ownership_cart_rows': abs(one('select count(*) from ownership where cart_manufacturer = 1')
                                             - sum(r['cart_manufacturer'] == '1' for r in source_rows))}


def summary_cells(row):
    """The cells of a published per-HCP ties summary that the pack must reproduce."""
    count = lambda name: int(row[name])
    return (count('ownership_rows_2019_2025'), count('cart_manufacturer_ownership_rows_2019_2025'),
            count('cart_product_research_rows_2019_2025'), count('cart_product_research_investigator_rows_2019_2025'),
            count('cart_product_research_sole_pi_rows_2019_2025'), count('cart_product_research_nct_ids'),
            row['cart_product_research_manufacturer_groups'] or None, count('held_paper_links'), count('papers_with_coi_statement'),
            count('papers_named_with_cart_manufacturer'), count('papers_not_named_with_cart_manufacturer'),
            count('papers_undetermined'), row['disclosed_cart_manufacturer_groups'] or None, count('papers_with_own_cart_affiliation'))


def pack_summary(con, core):
    """The same cells recomputed from the pack; core HCPs count held papers, the others every linked paper."""
    owned = keyed(con, 'select npi, count(*), sum(cart_manufacturer) from ownership group by npi', 1)
    research = defaultdict(lambda: [0, 0, 0, set(), set()])
    for npi, role, sole, nct, group in con.execute("select npi, role, sole_investigator, nct_id, manufacturer_group from pay_research_record "
                                                   "where match_basis = 'product_field'"):
        cell = research[npi]
        for k, add in enumerate((1, role == 'investigator', sole)):
            cell[k] += int(add)
        cell[3].update([nct] if nct else [])
        cell[4].update([group] if group else [])
    papers = defaultdict(lambda: [0, 0, 0, 0, 0, set(), 0])
    where = "is_core = 1 and paper_set = 'held'" if core else 'is_core = 0'
    for npi, status, named, groups, own in con.execute('select npi, statement_status, named_with_cart_manufacturer, manufacturer_groups, '
                                                       f'own_affiliation_cart_manufacturer from tie_author_paper where {where}'):
        cell = papers[npi]
        for k, add in enumerate((1, status not in (None, 'no_statement'), named == 1, named == 0, named is None)):
            cell[k] += int(add)
        cell[5].update(g for g in (groups or '').split(';') if g)
        cell[6] += own == 1

    def cells(npi):
        r, p = research[npi], papers[npi]
        return (*owned.get((npi,), (0, 0)), r[0], r[1], r[2], len(r[3]), ordered_groups(r[4]), *p[:5], ordered_groups(p[5]), p[6])
    return cells


def check_tie_tables(con, one):
    """tie_paper, tie_author_paper and tie_author_group against their sources."""
    papers = list(read_rows(src('papers_held'))) + list(read_rows(src('papers_new')))
    authors = list(read_rows(src('authors_core'))) + list(read_rows(src('authors_universe')))
    groups_named = sum(len([g for g in r['cart_manufacturers_named_for_author'].split(';') if g]) for r in authors)
    return {'money_tie_paper_rows': abs(one('select count(*) from tie_paper') - len(papers)),
            'money_tie_paper_pmid_repeats': one('select count(*) - count(distinct pmid) from tie_paper'),
            'money_tie_paper_without_year': one('select count(*) from tie_paper where publication_year is null'),
            'money_tie_paper_statements': abs(one('select count(*) from tie_paper where statement_present = 1')
                                              - sum(r['coi_statement_present'] == '1' for r in papers)),
            'money_tie_author_rows': abs(one('select count(*) from tie_author_paper') - len(authors)),
            'money_tie_author_key_repeats': one("select count(*) - count(distinct npi || '|' || pmid) from tie_author_paper"),
            'money_tie_author_orphans': one('select count(*) from tie_author_paper where pmid not in (select pmid from tie_paper)'),
            'money_tie_author_core_flag': one('select count(*) from tie_author_paper a join dim_hcp h on h.npi = a.npi where h.is_core != a.is_core'),
            'money_tie_author_named': abs(one('select count(*) from tie_author_paper where named_with_cart_manufacturer = 1')
                                          - sum(r['named_with_cart_manufacturer'] == '1' for r in authors)),
            'money_tie_named_without_group': one('select count(*) from tie_author_paper where (named_with_cart_manufacturer = 1) '
                                                 '!= (manufacturer_groups is not null)'),
            'money_tie_group_rows': abs(one('select count(*) from tie_author_group') - groups_named),
            'money_tie_group_unknown': one('select count(*) from tie_author_group where manufacturer_group not in '
                                           '(select manufacturer_group from manufacturer)')}


def check_tie_years(con, one):
    """tie_hcp_year against the author links for everyone and against the published core table; then the per-HCP summaries."""
    links = [tuple(r) for r in con.execute('select * from tie_author_paper')]
    again = {key: (*cell[:4], ordered_groups(cell[4])) for key, cell in tie_years(links).items()}
    table = {key: row[:5] for key, row in keyed(con, 'select * from tie_hcp_year').items()}
    by_group = keyed(con, '''
        select g.npi, a.publication_year, g.manufacturer_group, count(*) from tie_author_group g
        join tie_author_paper a on a.npi = g.npi and a.pmid = g.pmid where a.is_core = 1 and a.paper_set = 'held' group by 1, 2, 3''', 3)
    published = list(read_rows(src('ties_year')))

    def differs(r):
        key = (r['npi'], int(r['publication_year']))
        counts = tuple(int(r[c]) for c in ('held_papers', 'papers_with_coi_statement', 'papers_determinable', 'papers_named_with_cart_manufacturer'))
        return table.get(key, ())[:4] != counts or any(by_group.get((*key, group), (0,))[0] != int(r[f'papers_named_with_{label}'])
                                                       for group, label in GROUP_COLUMNS.items())
    out = {'money_tie_year_vs_links': differences(table, again),
           'money_tie_year_vs_core_table': (sum(differs(r) for r in published)
                                            + abs(one("select count(*) from tie_hcp_year where basis = 'published_core_table'") - len(published)))}
    for name, key, core in (('money_ties_vs_core_summary', 'ties_summary', True), ('money_ties_vs_universe_summary', 'ties_summary_universe', False)):
        cells = pack_summary(con, core)
        out[name] = sum(cells(r['npi']) != summary_cells(r) for r in read_rows(src(key)))
    return out


def check(con):
    """Zero means pass: counts and dollars equal the source tables, keys are unique, and the tables agree with each other."""
    one = lambda sql, *args: con.execute(sql, args).fetchone()[0]
    missing = sum(not one("select count(*) from sqlite_master where type = 'table' and name = ?", t) for t in TABLES)
    if missing:
        return {'money_missing_tables': missing}
    out = {'money_missing_tables': 0}
    for part in (check_general_records, check_brands, check_general_years, check_research_records, check_research_years,
                 check_ownership, check_tie_tables, check_tie_years):
        out.update(part(con, one))
    columns = [(t, r[1]) for t in TABLES for r in con.execute(f'pragma table_info("{t}")')]
    grouped = [t for t, c in columns if c == 'manufacturer_group' and t != 'manufacturer']
    out['money_unknown_npi'] = sum(one(f'select count(*) from "{t}" where npi not in (select npi from dim_hcp) or length(npi) != 10')
                                   for t, c in columns if c == 'npi')
    out['money_unknown_center'] = one('select count(*) from pay_research_hcp_center_year where ccn not in (select ccn from dim_center)')
    out['money_free_text_columns'] = sum(any(part in c.lower() for part in FREE_TEXT_COLUMNS) for _, c in columns)
    out['money_negative_values'] = sum(one(f'select count(*) from "{t}" where "{c}" < 0') for t, c in columns
                                       if c.endswith(('_usd', '_records')) or c in ('records', 'usd', 'papers'))
    out['money_manufacturer_groups'] = (abs(one('select count(*) from manufacturer') - len(GROUPS)) + sum(
        one(f'select count(*) from "{t}" where manufacturer_group not in (select manufacturer_group from manufacturer)') for t in grouped))
    out['money_unregistered_tables'] = sum(not one('select count(*) from pack_table where name = ?', t) for t in TABLES)
    return out


def main():
    """Build the money tables into agent/pack/dev_money.sqlite, on a copy of the core tables, and check them."""
    path = PACK / 'dev_money.sqlite'
    for suffix in ('', '-wal', '-shm'):
        (PACK / f'dev_money.sqlite{suffix}').unlink(missing_ok=True)
    base = sqlite3.connect(f"file:{PACK / 'dims.sqlite'}?mode=ro", uri=True)
    con = connect(path)
    base.backup(con)
    base.close()
    summary = build(con)
    con.commit()
    rows = dict(con.execute('select name, rows from pack_table').fetchall())
    facts = card_facts(con)
    checks = check(con)
    print('MONEY', summary)
    print('TABLES', {t: rows[t] for t in TABLES})
    print('CARD FACTS', dict(Counter(kind for kind, _ in facts)))
    print('CHECKS', len(checks), 'run; failing:', {k: v for k, v in checks.items() if v})
    con.close()


if __name__ == '__main__':
    main()
