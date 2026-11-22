"""Money part of the agent pack: build and checks on invented source tables, then every named query on the result.

Every person, company-specific amount, study and paper below is invented. The product and company-group names
are the public ones the pack is keyed on.
"""
import csv
import sqlite3
from decimal import Decimal

import pytest

from kolproject.agent import pack_money, queries, queries_money
from kolproject.agent.packbase import PACK, create_table
from kolproject.agent.queries import QueryError

A, B, C, D, E, F = (f'100000000{k}' for k in range(1, 7))
PEOPLE = [(A, 'Ada', 'Stone', 'core', 1, 1), (B, 'Ben', 'Rivers', 'core', 1, 1), (C, 'Cy', 'Marsh', 'affiliates', 0, 1),
          (D, 'Dee', 'Flint', 'referring', 0, 2), (E, 'Eli', 'Wren', 'other', 0, None), (F, 'Fay', 'Holt', 'core', 1, 2)]
KITE, BMS, NOVARTIS, JANSSEN = 'Kite/Gilead', 'Bristol Myers Squibb/Celgene/Juno', 'Novartis', 'Janssen/Johnson & Johnson'
PRODUCTS = [('Kymriah', 'tisagenlecleucel', 'tisa-cel', NOVARTIS, None, 'CD19', 1, 'Q2042', '2017-08-30'),
            ('Yescarta', 'axicabtagene ciloleucel', 'axi-cel', KITE, None, 'CD19', 1, 'Q2041', '2017-10-18'),
            ('Tecartus', 'brexucabtagene autoleucel', 'brexu-cel', KITE, None, 'CD19', 1, 'Q2053', '2020-07-24'),
            ('Breyanzi', 'lisocabtagene maraleucel', 'liso-cel', BMS, None, 'CD19', 1, 'Q2054', '2021-02-05'),
            ('Abecma', 'idecabtagene vicleucel', 'ide-cel', BMS, 'bluebird bio/2seventy bio', 'BCMA', 0, 'Q2055', '2021-03-26'),
            ('Carvykti', 'ciltacabtagene autoleucel', 'cilta-cel', JANSSEN, 'Legend Biotech', 'BCMA', 0, 'Q2056', '2022-02-28'),
            ('Aucatzyl', 'obecabtagene autoleucel', 'obe-cel', 'Autolus', None, 'CD19', 0, None, '2024-11-08')]
EVENTS = [('2017-10-18', 'Yescarta', 'first_approval', 'Yescarta approved', 1, 1, None),
          ('2019-01-01', None, 'coding', 'Procedure codes start', 0, 1, None),
          ('2021-03-26', 'Abecma', 'first_approval', 'Abecma approved', 0, 0, None),
          ('2022-04-01', 'Yescarta', 'new_indication', 'Yescarta: second line', 1, 1, None)]
P1, P2, P3, P4, P9 = '90000001', '90000002', '90000003', '90000004', '90000009'
ZUMA = 'ZUMA-TEST A Study of Cell Therapy'


def dims(con):
    """The core tables the money part reads, with invented people and places."""
    create_table(con, 'dim_community', [('community_id', 'int'), ('label', 'text'), ('short_label', 'text'), ('kind', 'text')],
                 [(1, 'North orbit (invented)', 'North orbit', 'named'), (2, 'Outside main network', 'Outside main network', 'isolated')],
                 primary_key=['community_id'])
    create_table(con, 'dim_hcp', [('npi', 'text'), ('name', 'text'), ('first_name', 'text'), ('last_name', 'text'), ('city', 'text'),
                                  ('state', 'text'), ('division', 'text'), ('division_group', 'text'), ('is_core', 'int'),
                                  ('community_id', 'int')],
                 [(npi, f'{first} {last}', first, last, 'Springfield', 'ZZ', group, group, core, community)
                  for npi, first, last, group, core, community in PEOPLE], primary_key=['npi'])
    create_table(con, 'dim_center', [('ccn', 'text'), ('name', 'text'), ('community_id', 'int')],
                 [('990001', 'North Invented Hospital', 1), ('990002', 'South Invented Hospital', 2)], primary_key=['ccn'])
    create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')], [(A, '990001'), (B, '990001'), (C, '990001'), (D, '990002')])
    create_table(con, 'dim_product', [('brand', 'text'), ('generic', 'text'), ('short_name', 'text'), ('company_group', 'text'),
                                      ('partner', 'text'), ('target', 'text'), ('lymphoma_product', 'int'), ('hcpcs_code', 'text'),
                                      ('first_us_approval', 'text')], PRODUCTS, primary_key=['brand'])
    create_table(con, 'product_event', [('date', 'text'), ('brand', 'text'), ('event_type', 'text'), ('label', 'text'),
                                        ('lymphoma', 'int'), ('verified', 'int'), ('source_url', 'text')], EVENTS)


def general(record, npi, date, amount, payer, nature, brands, flags, direct=1):
    any_slot, cart, lymphoma, pure = flags
    return {'record_id': record, 'npi': npi, 'program_year': date[:4], 'payment_date': date, 'amount_usd': amount, 'manufacturer': payer,
            'nature_class': nature, 'cart_products': brands, 'any_slot': any_slot, 'cart_only': cart, 'lymphoma_only': lymphoma,
            'development_name_only': 0, 'pure_play_payer': pure, 'direct_payment': direct}


GENERAL = [
    general('G1', A, '2019-03-05', '1000.00', 'Kite Pharma, Inc.', 'services', 'YESCARTA', (1, 1, 1, 1)),
    general('G2', A, '2020-07-01', '50.50', 'Kite Pharma, Inc.', 'hospitality_education', 'YESCARTA;TECARTUS', (1, 1, 1, 1)),
    general('G3', A, '2021-02-10', '300.00', 'Celgene Corporation', 'services', 'ABECMA', (1, 1, 0, 0), direct=0),
    general('G4', B, '2019-03-20', '200.01', 'Novartis Pharmaceuticals Corporation', 'services', 'KYMRIAH', (1, 1, 1, 0)),
    general('G5', C, '2019-11-11', '20.00', 'Kite Pharma, Inc.', 'hospitality_education', '', (0, 0, 0, 1)),
    general('G6', D, '2025-12-31', '75.00', 'Janssen Biotech, Inc.', 'hospitality_education', 'CARVYKTI', (1, 0, 0, 0)),
    general('G7', D, '2025-01-15', '40.00', 'Janssen Biotech, Inc.', 'hospitality_education', 'CARVYKTI', (1, 1, 0, 0)),
    general('G8', C, '2022-05-05', '0.03', 'Kite Pharma, Inc.', 'hospitality_education', 'YESCARTA;TECARTUS', (1, 1, 1, 1))]
YEAR_RULES = (('cart_any_slot', 'any_slot'), ('cart_only', 'cart_only'), ('lymphoma_only', 'lymphoma_only'), ('pure_play_payer', 'pure_play_payer'))


def general_year(rows):
    """The published yearly table for the invented records: seven years per NPI with a record."""
    total = lambda part: f"{sum((Decimal(r['amount_usd']) for r in part), Decimal(0)):.2f}"
    out = []
    for npi in sorted({r['npi'] for r in rows}):
        for year in range(2019, 2026):
            mine = [r for r in rows if r['npi'] == npi and r['program_year'] == str(year)]
            pick = lambda flag, nature=None, direct=False: [r for r in mine if r[flag] and (nature is None or r['nature_class'] == nature)
                                                              and (not direct or r['direct_payment'])]
            row = {'npi': npi, 'program_year': year, 'year_status': 'records_in_year' if mine else 'no_records_in_year',
                   'records': len(mine), 'usd': total(mine)}
            for prefix, flag in YEAR_RULES:
                row[f'{prefix}_rows'], row[f'{prefix}_usd'] = len(pick(flag)), total(pick(flag))
            for prefix in ('cart_only', 'lymphoma_only'):
                row[f'{prefix}_services_usd'] = total(pick(prefix, 'services'))
                row[f'{prefix}_hospitality_education_usd'] = total(pick(prefix, 'hospitality_education'))
            row['cart_only_direct_services_usd'] = total(pick('cart_only', 'services', True))
            row['manufacturers'] = len({r['manufacturer'] for r in mine})
            out.append(row)
    return out


def general_features(yearly):
    """The seven-year features table: one zero-filled row per universe NPI."""
    names = [n for n in yearly[0] if n.startswith(('cart_', 'lymphoma_only', 'pure_play')) and 'hospitality' not in n]
    out = []
    for npi, *_ in PEOPLE:
        mine = [r for r in yearly if r['npi'] == npi]
        out.append({'npi': npi, **{f'{n}_2019_2025': (f"{sum((Decimal(r[n]) for r in mine), Decimal(0)):.2f}" if n.endswith('_usd')
                                                    else sum(r[n] for r in mine)) for n in names}})
    return out


def core_research(record, npi, year, amount, slots, pi_count, payer, products, study, text=0, nct='', ccn='',
                  recipient=('Covered Recipient Teaching Hospital', 'Invented Hospital')):
    brands = products.upper()
    return {'npi': npi, 'record_id': record, 'program_year': year, 'payment_date': f'{year}-06-01', 'amount_usd': amount,
            'pi_slots': slots, 'record_pi_count': pi_count, 'recipient_type': recipient[0], 'recipient_name': recipient[1],
            'teaching_hospital_ccn': ccn, 'manufacturer': payer, 'products': products or 'Otherdrug', 'cart_products': brands,
            'cart_product_mentioned': int(bool(brands)), 'lymphoma_cart_product_mentioned': int(brands in ('YESCARTA', 'TECARTUS', 'KYMRIAH', 'BREYANZI')),
            'cart_text_match': text, 'study_name': study, 'nct_id': nct}


def universe_research(record, npi, year, amount, role, pi_count, payer, brands, study, screen='passed', nct='', ccn=''):
    return {'npi': npi, 'record_id': record, 'role': role, 'pi_slots': '1' if role == 'investigator' else '', 'program_year': year,
            'payment_date': f'{year}-06-01', 'amount_usd': amount, 'record_pi_count': pi_count, 'teaching_hospital_ccn': ccn,
            'manufacturer': payer, 'cart_products': brands, 'match_basis': 'product_field' if brands else 'study_or_context_text',
            'study_name': study, 'study_name_screen': screen, 'nct_id': nct}


CORE_RESEARCH = [
    core_research('R1', A, 2019, '5000.00', '1', 2, 'Kite Pharma, Inc.', 'Yescarta', ZUMA, nct='NCT00000001', ccn='990001'),
    core_research('R1', B, 2019, '5000.00', '2', 2, 'Kite Pharma, Inc.', 'Yescarta', ZUMA, nct='NCT00000001', ccn='990001'),
    core_research('R2', A, 2020, '700.00', '1', 1, 'Jazz Invented Pharma Inc', '', 'Supportive care after CAR-T', text=1,
                  recipient=('Non-covered Recipient Entity', 'Invented Institute')),
    core_research('R3', A, 2021, '900.00', '1', 1, 'Kite Pharma, Inc.', 'Tecartus', 'Stone investigator study of cells', ccn='990001'),
    core_research('R4', B, 2021, '100.00', '1', 2, 'Novartis Pharmaceuticals Corporation', 'Kymriah', 'Study with an outside investigator',
                  ccn='123456'),
    core_research('R5', B, 2022, '50.00', '1', 1, 'Celgene Corporation', 'Breyanzi', 'Vale fellowship in cell therapy',
                  recipient=('Covered Recipient Physician', 'Cora Vale')),
    core_research('R6', A, 2022, '999.00', '1', 1, 'Jazz Invented Pharma Inc', '', 'A study of something else'),
    core_research('R7', A, 2023, '2000.00', '1', 2, 'Janssen Research & Development, LLC', 'Carvykti', 'CARTITUDE-TEST',
                  nct='NCT00000007', ccn='990001'),
    core_research('R8', B, 2024, '30.00', '', 0, 'Kite Pharma, Inc.', 'Yescarta', 'Registry of cell therapy outcomes',
                  recipient=('Covered Recipient Physician', 'Ben Rivers'))]
UNIVERSE_RESEARCH = [
    universe_research('R7', C, 2023, '2000.00', 'investigator', 2, 'Janssen Research & Development, LLC', 'CARVYKTI', 'CARTITUDE-TEST',
                      nct='NCT00000007', ccn='990001'),
    universe_research('U2', D, 2024, '60.00', 'recipient_only', 0, 'Autolus Inc', '', '', screen='failed'),
    universe_research('U3', C, 2023, '10.00', 'investigator', 1, 'Kite Pharma, Inc.', 'YESCARTA', 'Zuma-Test  a study of cell therapy',
                      ccn='990002')]
# (npi, year): all records, product, text only, product as investigator, product as recipient only, product as sole
# investigator, investigator, product dollars, text dollars, lymphoma product, brands, ClinicalTrials.gov ids, groups
RESEARCH_YEAR = {
    (A, 2019): (1, 1, 0, 1, 0, 0, 1, '5000.00', '0.00', 1, 'YESCARTA', 1, KITE),
    (A, 2020): (1, 0, 1, 0, 0, 0, 1, '0.00', '700.00', 0, '', 0, ''),
    (A, 2021): (1, 1, 0, 1, 0, 1, 1, '900.00', '0.00', 1, 'TECARTUS', 0, KITE),
    (A, 2023): (1, 1, 0, 1, 0, 0, 1, '2000.00', '0.00', 0, 'CARVYKTI', 1, JANSSEN),
    (B, 2019): (1, 1, 0, 1, 0, 0, 1, '5000.00', '0.00', 1, 'YESCARTA', 1, KITE),
    (B, 2021): (1, 1, 0, 1, 0, 0, 1, '100.00', '0.00', 1, 'KYMRIAH', 0, NOVARTIS),
    (B, 2022): (1, 1, 0, 1, 0, 1, 1, '50.00', '0.00', 1, 'BREYANZI', 0, BMS),
    (B, 2024): (1, 1, 0, 0, 1, 0, 0, '30.00', '0.00', 1, 'YESCARTA', 0, KITE),
    (C, 2023): (2, 2, 0, 2, 0, 1, 2, '2010.00', '0.00', 1, 'CARVYKTI;YESCARTA', 1, f'{KITE};{JANSSEN}'),
    (D, 2024): (1, 0, 1, 0, 0, 0, 0, '0.00', '60.00', 0, '', 0, 'Autolus')}
RESEARCH_YEAR_COLUMNS = ['cart_research_rows', 'product_field_rows', 'text_only_rows', 'product_field_investigator_rows',
                         'product_field_recipient_only_rows', 'product_field_sole_pi_rows', 'investigator_rows', 'product_field_record_usd',
                         'text_only_record_usd', 'lymphoma_product_rows', 'product_field_brands', 'nct_ids', 'manufacturer_groups']


def research_year():
    blank = (0, 0, 0, 0, 0, 0, 0, '0.00', '0.00', 0, '', 0, '')
    return [{'npi': npi, 'program_year': year, 'year_status': 'records_in_year' if (npi, year) in RESEARCH_YEAR else 'no_records_in_year',
             **dict(zip(RESEARCH_YEAR_COLUMNS, RESEARCH_YEAR.get((npi, year), blank)))}
            for npi in sorted({k[0] for k in RESEARCH_YEAR}) for year in range(2019, 2026)]


def owned(record, npi, year, company, value, invested, group='', holder='Physician Covered Recipient'):
    return {'record_id': record, 'npi': npi, 'program_year': year, 'manufacturer': company, 'cart_manufacturer': int(bool(group)),
            'cart_manufacturer_group': group, 'value_of_interest_usd': value, 'amount_invested_usd': invested, 'interest_holder': holder}


OWNERSHIP = [owned('O1', C, 2020, 'Acme Devices LLC', '9000.00', '0.00'), owned('O2', C, 2021, 'ACME DEVICES LLC', '9500.00', ''),
             owned('O3', D, 2021, 'Novartis Pharma AG', '100.00', '50.00', NOVARTIS, 'Immediate family member'),
             owned('O4', C, 2021, 'Acme Devices LLC', '500.00', '10.00')]


def paper(pmid, statement, groups='', returned=True):
    stated = statement == 1
    return {'pmid': pmid, 'pubmed_record_status': 'returned' if returned else 'not_returned', 'pubmed_record_type': 'journal_article' if returned else '',
            'coi_statement_present': statement if returned else '', 'coi_cart_manufacturers': groups,
            'coi_cart_manufacturer_count': len([g for g in groups.split(';') if g]) if stated else '', 'coi_no_conflict_wording': 0 if stated else '',
            'coi_names_authors': int(bool(groups)) if stated else '', 'industry_affiliation_authors': 0 if returned else '',
            'industry_coauthorship': 0 if returned else '', 'industry_affiliation_groups': ''}


def author(npi, pmid, status, named_in, named_with, groups='', own=0, link=None, core=None):
    row = {'npi': npi, 'pmid': pmid, 'author_position': 1, 'pubmed_author_located': 'position_and_surname' if status else 'record_not_returned',
           'statement_status': status, 'named_in_statement': named_in, 'named_with_cart_manufacturer': named_with,
           'cart_manufacturers_named_for_author': groups, 'own_affiliation_cart_manufacturer': own}
    return row if link is None else {**row, 'link_table': link, 'is_core': core}


CORE_AUTHORS = [author(A, P1, 'author_named', 1, 1, f'{KITE};{NOVARTIS}'), author(B, P1, 'author_not_named', 0, 0),
                author(A, P2, 'no_statement', '', ''), author(B, P3, 'no_cart_manufacturer', '', 0), author(A, P4, '', '', '', own='')]
UNIVERSE_AUTHORS = [author(C, P1, 'author_named', 1, 1, KITE, link='universe_authorships', core=0),
                    author(A, P9, 'author_named', 1, 1, KITE, own=1, link='new_authorships', core=1),
                    author(D, P9, 'author_not_named', 0, 0, link='new_authorships', core=0)]


def tie_year(npi, year, held, stated, readable, named, **groups):
    labels = ['kite_gilead', 'bms_celgene_juno', 'novartis', 'janssen_jnj', 'legend', 'autolus', 'bluebird_2seventy']
    return {'npi': npi, 'publication_year': year, 'held_papers': held, 'papers_with_coi_statement': stated, 'papers_determinable': readable,
            'papers_named_with_cart_manufacturer': named, **{f'papers_named_with_{g}': groups.get(g, 0) for g in labels}}


def summary(npi, owned_rows, cart_owned, research, papers):
    names = ['cart_product_research_rows_2019_2025', 'cart_product_research_investigator_rows_2019_2025',
             'cart_product_research_sole_pi_rows_2019_2025', 'cart_product_research_nct_ids', 'cart_product_research_manufacturer_groups',
             'held_paper_links', 'papers_with_coi_statement', 'papers_named_with_cart_manufacturer', 'papers_not_named_with_cart_manufacturer',
             'papers_undetermined', 'disclosed_cart_manufacturer_groups', 'papers_with_own_cart_affiliation']
    return {'npi': npi, 'ownership_rows_2019_2025': owned_rows, 'cart_manufacturer_ownership_rows_2019_2025': cart_owned,
            **dict(zip(names, (*research, *papers)))}


def sources():
    yearly = general_year(GENERAL)
    return {
        'universe_cart_general_payments_2019_2025': GENERAL, 'hcp_universe_cart_general_payments_by_year': yearly,
        'hcp_universe_general_payment_features': general_features(yearly),
        'research_payments_2019_2025': CORE_RESEARCH, 'universe_cart_research_payments_2019_2025': UNIVERSE_RESEARCH,
        'hcp_universe_cart_research_by_year': research_year(), 'universe_ownership_interests_2019_2025': OWNERSHIP,
        'publication_industry_disclosures': [paper(P1, 1, f'{KITE};{NOVARTIS}'), paper(P2, 0), paper(P3, 1), paper(P4, '', returned=False)],
        'new_publication_industry_disclosures': [paper(P9, 1, KITE)],
        'core_author_publication_industry_ties': CORE_AUTHORS, 'universe_author_publication_industry_ties': UNIVERSE_AUTHORS,
        'core_hcp_disclosed_ties_by_year': [tie_year(A, 2019, 1, 1, 1, 1, kite_gilead=1, novartis=1), tie_year(A, 2020, 1, 0, 0, 0),
                                            tie_year(A, 2021, 1, 0, 0, 0), tie_year(B, 2019, 1, 1, 1, 0), tie_year(B, 2021, 1, 1, 1, 0)],
        'core_hcp_industry_ties_summary': [
            summary(A, 0, 0, (3, 3, 1, 2, f'{KITE};{JANSSEN}'), (3, 1, 1, 0, 2, f'{KITE};{NOVARTIS}', 0)),
            summary(B, 0, 0, (4, 3, 1, 1, f'{KITE};{BMS};{NOVARTIS}'), (2, 2, 0, 2, 0, '', 0)),
            summary(F, 0, 0, (0, 0, 0, 0, ''), (0, 0, 0, 0, 0, '', 0))],
        'universe_hcp_industry_ties_summary': [
            summary(C, 3, 0, (2, 2, 1, 1, f'{KITE};{JANSSEN}'), (1, 1, 1, 0, 0, KITE, 0)),
            summary(D, 1, 1, (0, 0, 0, 0, ''), (1, 1, 0, 1, 0, '', 0))],
        'publications': [{'pmid': P1, 'publication_year': 2019}, {'pmid': P2, 'publication_year': 2020},
                         {'pmid': P3, 'publication_year': 2021}, {'pmid': P4, 'publication_year': 2021}],
        'new_publications': [{'pmid': P9, 'publication_year': 2026}]}


@pytest.fixture
def con(tmp_path, monkeypatch):
    """A temporary pack: invented core tables, invented source CSVs, and the money tables built from them."""
    for table, rows in sources().items():
        with (tmp_path / f'{table}.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in rows for k in row)), restval='')
            writer.writeheader()
            writer.writerows(rows)
    monkeypatch.setattr(pack_money, 'source', lambda snapshot, table: tmp_path / f'{table}.csv')
    con = sqlite3.connect(tmp_path / 'pack.sqlite', isolation_level=None)
    dims(con)
    pack_money.build(con)
    yield con
    con.close()


def failing(con):
    return {name: count for name, count in pack_money.check(con).items() if count}


def rows(con, name, **params):
    return queries.run(name, params, con=con)['rows']


def scoped(con, name, scope='community', ident='1', **params):
    return rows(con, name, scope=scope, id=ident, **params)


# ---------------------------------------------------------------- build and checks

def test_build_creates_every_table_and_passes_its_checks(con):
    counts = {t: con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in pack_money.TABLES}
    assert counts == {'pay_general_record': 8, 'pay_general_record_brand': 9, 'pay_general_year': 28, 'pay_general_hcp_mfr_year': 6,
                      'pay_research_record': 11, 'pay_research_year': 28, 'pay_research_hcp_center_year': 6, 'ownership': 4,
                      'tie_paper': 5, 'tie_author_paper': 8, 'tie_author_group': 4, 'tie_hcp_year': 8, 'manufacturer': 7}
    assert failing(con) == {}
    assert {r[0] for r in con.execute('select name from pack_table')} >= set(pack_money.TABLES)


def test_brand_split_is_equal_and_adds_back_to_the_cent(con):
    shares = lambda record: con.execute('select brand, brands_on_record, amount_share_usd from pay_general_record_brand '
                                        'where record_id = ? order by rowid', [record]).fetchall()
    assert shares('G2') == [('Yescarta', 2, 25.25), ('Tecartus', 2, 25.25)]
    assert shares('G8') == [('Yescarta', 2, 0.02), ('Tecartus', 2, 0.01)]      # the odd cent goes to the first brand
    assert shares('G5') == []                                                  # in the table because of its payer only
    assert con.execute("select brands, month, manufacturer_group, direct from pay_general_record where record_id = 'G3'").fetchone() == \
        ('Abecma', '2021-02', BMS, 0)
    assert pack_money.split_cents(10, 3) == [4, 3, 3]


def test_research_keeps_car_t_records_and_screens_study_names(con):
    status = dict(con.execute("select npi || ':' || record_id, study_name_screen from pay_research_record"))
    assert f'{A}:R6' not in status                                             # no CAR-T product and no CAR-T wording
    assert status == {f'{A}:R1': 'passed', f'{B}:R1': 'passed', f'{A}:R2': 'passed',
                      f'{A}:R3': 'withheld',                                   # holds the investigator's own surname
                      f'{B}:R4': 'withheld',                                   # an investigator outside the universe cannot be screened
                      f'{B}:R5': 'withheld',                                   # holds the name of a recipient who is a person
                      f'{A}:R7': 'passed', f'{C}:R7': 'passed', f'{B}:R8': 'passed',
                      f'{D}:U2': 'withheld', f'{C}:U3': 'passed'}
    assert con.execute("select count(*) from pay_research_record where (study_name is null) != (study_name_screen != 'passed')").fetchone()[0] == 0
    row = con.execute('select record_total_usd, manufacturer_group, match_basis, brands, lymphoma_product, role, sole_investigator, nct_id, '
                      'teaching_hospital_ccn, is_core from pay_research_record where npi = ? and record_id = ?', [A, 'R3']).fetchone()
    assert row == (900.0, KITE, 'product_field', 'Tecartus', 1, 'investigator', 1, None, '990001', 1)
    assert con.execute("select manufacturer_group, match_basis, brands, role from pay_research_record where record_id = 'R2'").fetchone() == \
        (None, 'study_or_context_text', None, 'investigator')
    assert con.execute("select role, study_name from pay_research_record where record_id = 'R8'").fetchone() == \
        ('recipient_only', 'Registry of cell therapy outcomes')


def test_core_study_name_follows_the_published_screen_on_a_shared_record():
    row = {'study_name': 'A plain study', 'record_id': 'X', 'record_pi_count': '3', 'recipient_type': 'Non-covered Recipient Entity',
           'recipient_name': 'Invented Institute'}
    people, investigators, names = {'X': {A}}, {'X': {A}}, {A: {'ada', 'stone'}}
    assert pack_money.core_study_status(row, people, investigators, names, {}) == 'withheld'
    assert pack_money.core_study_status(row, people, investigators, names, {'X': 'passed'}) == 'passed'
    assert pack_money.core_study_status(row, people, investigators, names, {'X': 'failed'}) == 'withheld'
    assert pack_money.core_study_status({**row, 'study_name': 'Write to dr. someone'}, people, investigators, names, {'X': 'passed'}) == 'withheld'
    assert pack_money.core_study_status({**row, 'study_name': ' '}, people, investigators, names, {}) == 'none_reported'


def test_research_links_to_centers_have_the_exact_columns(con):
    assert [r[1] for r in con.execute('pragma table_info(pay_research_hcp_center_year)')] == ['npi', 'ccn', 'year', 'records', 'usd']
    assert con.execute('select * from pay_research_hcp_center_year order by year, npi, ccn').fetchall() == [
        (A, '990001', 2019, 1, 5000.0), (B, '990001', 2019, 1, 5000.0), (A, '990001', 2021, 1, 900.0), (A, '990001', 2023, 1, 2000.0),
        (C, '990001', 2023, 1, 2000.0), (C, '990002', 2023, 1, 10.0)]


def test_ties_cover_core_and_other_hcps_in_one_yearly_table(con):
    table = {(r[0], r[1]): r[2:] for r in con.execute('select * from tie_hcp_year')}
    assert table[A, 2019] == (1, 1, 1, 1, f'{KITE};{NOVARTIS}', 'published_core_table')
    assert table[A, 2021] == (1, 0, 0, 0, None, 'published_core_table')       # the record PubMed did not return
    assert table[A, 2026] == (1, 1, 1, 1, KITE, 'derived_from_author_links')   # a core HCP on a 2026 paper
    assert table[C, 2019] == (1, 1, 1, 1, KITE, 'derived_from_author_links')   # a non-core HCP on a held paper
    assert table[D, 2026] == (1, 1, 1, 0, None, 'derived_from_author_links')
    assert con.execute('select npi, pmid, manufacturer_group from tie_author_group order by pmid, npi, manufacturer_group').fetchall() == [
        (A, P1, KITE), (A, P1, NOVARTIS), (C, P1, KITE), (A, P9, KITE)]
    assert con.execute('select publication_year, paper_set, record_returned, statement_present, manufacturer_groups from tie_paper '
                       'where pmid in (?, ?) order by pmid', [P4, P9]).fetchall() == [(2021, 'held', 0, None, None), (2026, 'new_2026', 1, 1, KITE)]
    assert con.execute('select named_with_cart_manufacturer, statement_status from tie_author_paper where pmid = ?', [P2]).fetchone() == \
        (None, 'no_statement')                                                 # blank stays blank, never zero


def test_manufacturer_table_lists_the_company_names_seen(con):
    table = {r[0]: r[1:] for r in con.execute('select * from manufacturer')}
    assert list(table) == pack_money.GROUPS
    assert table[KITE] == ('Kite Pharma, Inc.', 'Yescarta;Tecartus', None)
    assert table[NOVARTIS] == ('Novartis Pharmaceuticals Corporation; Novartis Pharma AG', 'Kymriah', None)
    assert table['bluebird bio/2seventy bio'] == (None, None, 'Abecma')


BREAKS = [
    ("update pay_general_record set amount_usd = amount_usd + 1 where record_id = 'G1'", 'money_general_usd'),
    ("delete from pay_general_record where record_id = 'G5'", 'money_general_rows'),
    ("update pay_general_record set lymphoma_only = 0 where record_id = 'G4'", 'money_general_lymphoma_only'),
    ("delete from pay_general_record_brand where record_id = 'G2' and brand = 'Tecartus'", 'money_brand_split'),
    ("update pay_general_record_brand set amount_share_usd = 0.03 where record_id = 'G8' and brand = 'Yescarta'", 'money_brand_split'),
    (f"update pay_general_year set lymphoma_only_usd = 7 where npi = '{A}' and year = 2019", 'money_general_year_vs_features'),
    (f"update pay_general_year set cart_only_records = 5 where npi = '{A}' and year = 2021", 'money_general_year_vs_records'),
    (f"update pay_general_hcp_mfr_year set cart_only_usd = 1 where npi = '{A}' and year = 2021", 'money_general_mfr_year_vs_records'),
    ("delete from pay_research_record where record_id = 'U3'", 'money_research_rows'),
    ("update pay_research_record set role = 'recipient_only' where record_id = 'R3'", 'money_research_vs_year'),
    ("update pay_research_record set record_total_usd = 1 where record_id = 'R1' and npi = '" + B + "'", 'money_research_record_disagrees'),
    ("update pay_research_record set study_name = 'Stone investigator study of cells' where record_id = 'R3'", 'money_research_study_names'),
    ("update pay_research_record set study_name = 'Call 555-123-4567', study_name_screen = 'passed' where record_id = 'U2'", 'money_research_study_names'),
    (f"insert into pay_research_hcp_center_year values ('{A}', '123456', 2021, 1, 100.0)", 'money_research_center_links'),
    ("update ownership set value_of_interest_usd = 1 where record_id = 'O1'", 'money_ownership_usd'),
    ("update ownership set cart_manufacturer = 0 where record_id = 'O3'", 'money_ownership_cart_rows'),
    ("update ownership set npi = '1999999999' where record_id = 'O1'", 'money_unknown_npi'),
    (f"update tie_author_paper set named_with_cart_manufacturer = 0, manufacturer_groups = null where npi = '{C}'", 'money_tie_author_named'),
    (f"update tie_hcp_year set papers = 9 where npi = '{C}'", 'money_tie_year_vs_links'),
    (f"update tie_hcp_year set papers_with_statement = 0 where npi = '{B}' and year = 2019", 'money_tie_year_vs_core_table'),
    (f"delete from tie_author_group where npi = '{A}' and pmid = '{P1}' and manufacturer_group = 'Novartis'", 'money_tie_group_rows'),
    (f"update tie_author_paper set own_affiliation_cart_manufacturer = 1 where npi = '{B}'", 'money_ties_vs_core_summary'),
    (f"update tie_author_paper set statement_status = 'no_statement' where npi = '{D}'", 'money_ties_vs_universe_summary'),
    ("delete from tie_paper where pmid = '" + P3 + "'", 'money_tie_author_orphans'),
    ('alter table ownership add column terms_of_interest text', 'money_free_text_columns'),
    ("update pay_general_year set cart_only_usd = -1 where records = 0", 'money_negative_values'),
    ("delete from manufacturer where manufacturer_group = 'Autolus'", 'money_manufacturer_groups'),
    ('drop table tie_author_group', 'money_missing_tables')]


@pytest.mark.parametrize('statement, check', BREAKS)
def test_check_catches_a_broken_table(con, statement, check):
    con.execute('savepoint broken')
    con.execute(statement)
    assert failing(con).get(check, 0) > 0
    con.execute('rollback to broken')
    assert failing(con) == {}


def test_card_facts_are_short_and_name_no_person(con):
    facts = pack_money.card_facts(con)
    assert {kind for kind, _ in facts} == {'hcp', 'community', 'center', 'manufacturer', 'product'}
    assert all(isinstance(ident, str) and 1 <= len(sentences) <= 3 for (_, ident), sentences in facts.items())
    text = ' '.join(s for sentences in facts.values() for s in sentences)
    assert not any(name in text for _, first, last, *_ in PEOPLE for name in (first, last))
    assert facts[('hcp', A)][0] == ('Manufacturers reported $1,050 in general payments naming lymphoma CAR-T products on 2 records in '
                                    '2019 to 2020 (Kite/Gilead); $1,350 on 3 records for all CAR-T products.')
    assert facts[('hcp', D)][0].startswith('Manufacturers reported $40 in general payments naming CAR-T products on 1 record in 2025')
    assert facts[('hcp', C)][0].startswith('Manufacturers reported $0 in general payments naming lymphoma CAR-T products on 1 record in 2022')
    assert 'study totals, not personal income' in facts[('hcp', A)][1] and '3 research payment records' in facts[('hcp', A)][1]
    assert facts[('hcp', A)][2].startswith('Named with a CAR-T manufacturer in the conflict-of-interest statement of 2 of 4 linked papers')
    assert ('hcp', F) not in facts and ('hcp', E) not in facts
    assert facts[('community', '1')][0].startswith('General payments naming lymphoma CAR-T products to HCPs attached to this community: $1,251 in '
                                                    '2019 to 2021 and $0 in 2022 to 2025, to 3 HCPs on 4 records')
    assert ('community', '2') not in facts and ('center', '990002') in facts     # isolated HCPs share one card: no community totals
    assert facts[('product', 'Aucatzyl')] == ['No general payment record to the HCP universe names Aucatzyl in 2019 to 2025.']
    assert 'another 1 records name it beside' in facts[('product', 'Carvykti')][0]
    assert facts[('manufacturer', 'Legend Biotech')] == ['No general payment record to the HCP universe in 2019 to 2025.']


# ---------------------------------------------------------------- queries: general payments

def test_payments_by_year_counts_under_each_rule_and_fills_zero_years(con):
    lymphoma = scoped(con, 'payments_by_year')
    assert [r['year'] for r in lymphoma] == list(range(2019, 2026))
    assert lymphoma[0] == {'year': 2019, 'hcps_paid': 2, 'records': 2, 'total_usd': 1200.01, 'services_usd': 1200.01, 'hospitality_education_usd': 0.0}
    assert lymphoma[1] == {'year': 2020, 'hcps_paid': 1, 'records': 1, 'total_usd': 50.5, 'services_usd': 0.0, 'hospitality_education_usd': 50.5}
    assert lymphoma[2] == {'year': 2021, 'hcps_paid': 0, 'records': 0, 'total_usd': 0.0, 'services_usd': 0.0, 'hospitality_education_usd': 0.0}
    assert scoped(con, 'payments_by_year', rule='cart_only')[2]['total_usd'] == 300.0
    assert [r['total_usd'] for r in scoped(con, 'payments_by_year', 'hcp', F)] == [0.0] * 7      # no record: seven real zeros
    assert scoped(con, 'payments_by_year', 'center', '990002', rule='cart_only')[6]['records'] == 1   # the any-slot record is left out
    assert sum(r['total_usd'] for r in scoped(con, 'payments_by_year', 'division_group', 'affiliates')) == 0.03


def test_payments_by_product_splits_records_naming_two_brands(con):
    out = scoped(con, 'payments_by_product')
    assert [r['brand'] for r in out[::7]] == ['Kymriah', 'Yescarta', 'Tecartus', 'Abecma'] and len(out) == 28
    cell = {(r['brand'], r['year']): (r['records'], r['hcps_paid'], r['total_usd']) for r in out}
    assert cell['Yescarta', 2019] == (1, 1, 1000.0) and cell['Yescarta', 2020] == (1, 1, 25.25) and cell['Tecartus', 2020] == (1, 1, 25.25)
    assert cell['Yescarta', 2022] == (1, 1, 0.02) and cell['Tecartus', 2022] == (1, 1, 0.01) and cell['Tecartus', 2019] == (0, 0, 0.0)
    assert round(sum(r['total_usd'] for r in out), 2) == round(sum(r['total_usd'] for r in scoped(con, 'payments_by_year', rule='cart_only')), 2)
    one = scoped(con, 'payments_by_product', brand='Aucatzyl', year_from=2024)
    assert one == [{'brand': 'Aucatzyl', 'year': year, 'records': 0, 'hcps_paid': 0, 'total_usd': 0.0} for year in (2024, 2025)]
    assert [r['year'] for r in scoped(con, 'payments_by_product', brand='Kymriah', year_from=2019, year_to=2019)] == [2019]
    assert scoped(con, 'payments_by_product', 'hcp', E) == []
    with pytest.raises(QueryError, match='year_from'):
        scoped(con, 'payments_by_product', year_from=2024, year_to=2020)


def test_payments_by_manufacturer_and_monthly(con):
    assert scoped(con, 'payments_by_manufacturer') == [
        {'manufacturer_group': KITE, 'hcps_paid': 2, 'records': 3, 'total_usd': 1050.53, 'services_usd': 1000.0, 'first_year': 2019, 'last_year': 2022},
        {'manufacturer_group': NOVARTIS, 'hcps_paid': 1, 'records': 1, 'total_usd': 200.01, 'services_usd': 200.01, 'first_year': 2019, 'last_year': 2019}]
    assert [r['manufacturer_group'] for r in scoped(con, 'payments_by_manufacturer', rule='cart_only')] == [KITE, BMS, NOVARTIS]
    assert scoped(con, 'payments_by_manufacturer', 'community', '2') == []     # Carvykti is not a lymphoma product
    months = scoped(con, 'payments_monthly')
    assert len(months) == 84 and months[0]['month'] == '2019-01' and months[-1]['month'] == '2025-12'
    assert {r['month']: (r['records'], r['total_usd']) for r in months if r['records']} == {
        '2019-03': (2, 1200.01), '2020-07': (1, 50.5), '2022-05': (1, 0.03)}
    assert months[1] == {'month': '2019-02', 'records': 0, 'total_usd': 0.0}
    for brand in ('Yescarta', 'Kymriah', 'Carvykti', 'Aucatzyl'):  # one product by month adds up to the same product by year
        monthly = scoped(con, 'payments_monthly', brand=brand)
        yearly = scoped(con, 'payments_by_product', brand=brand)
        assert len(monthly) == 84 and round(sum(r['total_usd'] for r in monthly), 2) == round(sum(r['total_usd'] for r in yearly), 2)
        assert sum(r['records'] for r in monthly) == sum(r['records'] for r in yearly)


def test_top_paid_hcps_orders_filters_and_refuses_one_hcp(con):
    top = scoped(con, 'top_paid_hcps')
    assert [(r['rank'], r['npi'], r['total_usd'], r['records']) for r in top] == [(1, A, 1000.0, 1), (2, B, 200.01, 1)]
    assert top[0] == {'rank': 1, 'name': 'Ada Stone', 'npi': A, 'division_group': 'core', 'community': 'North orbit', 'city': 'Springfield',
                      'state': 'ZZ', 'total_usd': 1000.0, 'records': 1}
    assert [(r['npi'], r['total_usd']) for r in scoped(con, 'top_paid_hcps', nature='all')] == [(A, 1050.5), (B, 200.01), (C, 0.03)]
    assert [r['npi'] for r in scoped(con, 'top_paid_hcps', nature='all', year=2022)] == [C]
    assert [r['npi'] for r in scoped(con, 'top_paid_hcps', nature='all', n=1)] == [A]
    assert [(r['npi'], r['total_usd']) for r in scoped(con, 'top_paid_hcps', rule='cart_only')] == [(A, 1300.0), (B, 200.01)]
    assert scoped(con, 'top_paid_hcps', 'division_group', 'other') == []
    assert scoped(con, 'top_paid_hcps', 'center', '990002', nature='hospitality_education', rule='cart_only')[0]['community'] == 'Outside main network'
    with pytest.raises(QueryError, match='payments_by_year'):
        scoped(con, 'top_paid_hcps', 'hcp', A)


# ---------------------------------------------------------------- queries: research and ownership

def test_research_by_year_counts_a_shared_record_once(con):
    out = {r['year']: r for r in scoped(con, 'research_by_year')}
    assert out[2019] == {'year': 2019, 'hcps_named': 2, 'product_field_records': 1, 'investigator_records': 1, 'record_total_usd': 5000.0,
                         'text_only_records': 0}                               # two HCPs, one record, one total
    assert out[2020] == {'year': 2020, 'hcps_named': 0, 'product_field_records': 0, 'investigator_records': 0, 'record_total_usd': 0.0,
                         'text_only_records': 1}
    assert (out[2021]['hcps_named'], out[2021]['product_field_records'], out[2021]['record_total_usd']) == (2, 2, 1000.0)
    assert (out[2023]['hcps_named'], out[2023]['product_field_records'], out[2023]['record_total_usd']) == (2, 2, 2010.0)
    assert (out[2024]['product_field_records'], out[2024]['investigator_records']) == (1, 0)      # named as recipient only
    assert out[2025]['product_field_records'] == 0 and len(out) == 7
    alone = {r['year']: r for r in scoped(con, 'research_by_year', 'hcp', B)}
    assert alone[2019]['record_total_usd'] == 5000.0 and alone[2019]['hcps_named'] == 1
    assert all(c['name'] != 'total_usd' for c in queries.REGISTRY['research_by_year']['columns'])
    assert 'not personal income' in next(c['label'] for c in queries.REGISTRY['research_by_year']['columns'] if c['name'] == 'record_total_usd')


def test_research_studies_groups_by_name_and_keeps_unnamed_records_apart(con):
    out = scoped(con, 'research_studies')
    assert [r['study_name'] for r in out] == [ZUMA, 'CARTITUDE-TEST', 'Registry of cell therapy outcomes', 'Supportive care after CAR-T',
                                              queries_money.NOT_SHOWN]
    assert out[0] == {'study_name': ZUMA, 'nct_id': 'NCT00000001', 'manufacturer_group': KITE, 'brands': 'Yescarta', 'match_basis': 'product named',
                      'first_year': 2019, 'last_year': 2023, 'records': 2, 'investigators': 3}     # two spellings, one study
    assert (out[1]['records'], out[1]['investigators'], out[1]['brands']) == (1, 2, 'Carvykti')
    assert (out[2]['investigators'], out[3]['match_basis'], out[3]['manufacturer_group'], out[3]['brands']) == (0, 'study text only', None, None)
    assert out[4] == {'study_name': queries_money.NOT_SHOWN, 'nct_id': None, 'manufacturer_group': None, 'brands': 'Kymriah; Tecartus; Breyanzi',
                      'match_basis': 'product named', 'first_year': 2021, 'last_year': 2022, 'records': 3, 'investigators': 2}
    assert [r['study_name'] for r in scoped(con, 'research_studies', n=1)] == [ZUMA, queries_money.NOT_SHOWN]
    assert [r['study_name'] for r in scoped(con, 'research_studies', 'hcp', D)] == [queries_money.NOT_SHOWN]
    assert scoped(con, 'research_studies', 'hcp', F) == []
    assert not any('usd' in c['name'] for c in queries.REGISTRY['research_studies']['columns'])


def test_ownership_always_states_the_car_t_manufacturer_count(con):
    north = scoped(con, 'ownership')
    assert north[0] == {'company': 'All CAR-T manufacturers, 2019 to 2025', 'year': None, 'hcps': 0, 'interests': 0,
                        'value_of_interest_usd': None, 'amount_invested_usd': None, 'cart_manufacturer': 'yes'}
    assert north[1:] == [
        {'company': 'ACME DEVICES LLC', 'year': 2021, 'hcps': 1, 'interests': 2, 'value_of_interest_usd': 10000.0, 'amount_invested_usd': 10.0,
         'cart_manufacturer': 'no'},                                           # two spellings of one company; a blank is not a zero
        {'company': 'Acme Devices LLC', 'year': 2020, 'hcps': 1, 'interests': 1, 'value_of_interest_usd': 9000.0, 'amount_invested_usd': 0.0,
         'cart_manufacturer': 'no'}]
    south = scoped(con, 'ownership', 'community', '2')
    assert (south[0]['hcps'], south[0]['interests'], south[0]['value_of_interest_usd']) == (1, 1, 100.0)
    assert south[1]['company'] == 'Novartis Pharma AG' and south[1]['cart_manufacturer'] == 'yes'
    assert [r['interests'] for r in scoped(con, 'ownership', 'hcp', A)] == [0]


# ---------------------------------------------------------------- queries: disclosed ties

def test_ties_by_year_and_by_manufacturer(con):
    assert scoped(con, 'ties_by_year') == [
        {'year': 2019, 'papers': 1, 'papers_with_statement': 1, 'papers_named': 1, 'hcps_named': 2},
        {'year': 2020, 'papers': 1, 'papers_with_statement': 0, 'papers_named': 0, 'hcps_named': 0},
        {'year': 2021, 'papers': 2, 'papers_with_statement': 1, 'papers_named': 0, 'hcps_named': 0},
        *({'year': year, 'papers': 0, 'papers_with_statement': 0, 'papers_named': 0, 'hcps_named': 0} for year in range(2022, 2026)),
        {'year': 2026, 'papers': 1, 'papers_with_statement': 1, 'papers_named': 1, 'hcps_named': 1}]
    assert scoped(con, 'ties_by_year', 'hcp', E) == []                         # no linked paper: nothing to say, not zero ties
    assert [r['year'] for r in scoped(con, 'ties_by_year', 'hcp', D)] == [2026]
    assert scoped(con, 'ties_by_manufacturer') == [
        {'manufacturer_group': KITE, 'hcps_named': 2, 'papers': 2, 'first_year': 2019, 'last_year': 2026},
        {'manufacturer_group': NOVARTIS, 'hcps_named': 1, 'papers': 1, 'first_year': 2019, 'last_year': 2019}]
    assert scoped(con, 'ties_by_manufacturer', 'community', '2') == []


def test_hcp_ties_papers_with_and_without_the_paper_table(con):
    bare = rows(con, 'hcp_ties_papers', npi=A)
    assert bare == [{'pmid': P9, 'title': None, 'journal': None, 'year': 2026, 'manufacturer_groups': KITE},
                    {'pmid': P1, 'title': None, 'journal': None, 'year': 2019, 'manufacturer_groups': f'{KITE}; {NOVARTIS}'}]
    assert rows(con, 'hcp_ties_papers', npi=B) == [] and len(rows(con, 'hcp_ties_papers', npi=A, n=1)) == 1
    con.execute('create table pub (pmid integer primary key, title text, journal text, year integer)')
    con.execute('insert into pub values (?, ?, ?, ?)', [int(P1), 'An invented paper', 'Invented Journal', 2019])
    full = rows(con, 'hcp_ties_papers', npi=A)
    assert [(r['pmid'], r['title'], r['journal']) for r in full] == [(P9, None, None), (P1, 'An invented paper', 'Invented Journal')]
    with pytest.raises(QueryError, match='find_entity'):
        rows(con, 'hcp_ties_papers', npi='1999999999')


def test_ties_vs_payments_sets_the_two_sources_side_by_side(con):
    out = {r['manufacturer_group']: r for r in scoped(con, 'ties_vs_payments')}
    assert list(out) == pack_money.GROUPS
    assert out[KITE] == {'manufacturer_group': KITE, 'hcps_with_tie': 2, 'tie_first_year': 2019, 'tie_last_year': 2026, 'hcps_with_payment': 2,
                         'payment_first_year': 2019, 'payment_last_year': 2022, 'hcps_with_both': 2}
    assert (out[NOVARTIS]['hcps_with_tie'], out[NOVARTIS]['hcps_with_payment'], out[NOVARTIS]['hcps_with_both']) == (1, 1, 0)
    assert (out[BMS]['hcps_with_tie'], out[BMS]['tie_first_year'], out[BMS]['hcps_with_payment'], out[BMS]['payment_last_year']) == (0, None, 1, 2021)
    assert (out[JANSSEN]['hcps_with_tie'], out[JANSSEN]['hcps_with_payment'], out[JANSSEN]['payment_first_year']) == (0, 0, None)
    spec = queries.REGISTRY['ties_vs_payments']
    words = (spec['description'] + ' '.join(c['label'] for c in spec['columns'])).lower()
    assert not any(word in words for word in ('undisclosed', 'match', 'failed', 'conflict of interest violation', 'discrepan'))


# ---------------------------------------------------------------- queries: products, registry and parameters

def test_product_facts_and_events(con):
    assert [r['brand'] for r in rows(con, 'product_facts')] == [p[0] for p in PRODUCTS]
    assert rows(con, 'product_facts', brand='Abecma') == [dict(zip(['brand', 'generic', 'short_name', 'company_group', 'partner', 'target',
                                                                    'lymphoma_product', 'hcpcs_code', 'first_us_approval'], PRODUCTS[4]))]
    assert [(r['date'], r['brand'], r['verified']) for r in rows(con, 'product_events')] == [
        ('2017-10-18', 'Yescarta', 1), ('2019-01-01', None, 1), ('2021-03-26', 'Abecma', 0), ('2022-04-01', 'Yescarta', 1)]
    assert [r['label'] for r in rows(con, 'product_events', brand='Yescarta')] == ['Yescarta approved', 'Yescarta: second line']
    assert [r['brand'] for r in rows(con, 'product_events', lymphoma_only='yes')] == ['Yescarta', 'Yescarta']
    assert rows(con, 'product_events', brand='Aucatzyl') == []


MONEY_QUERIES = ['payments_by_year', 'payments_by_product', 'payments_by_manufacturer', 'payments_monthly', 'top_paid_hcps', 'research_by_year',
                 'research_studies', 'ownership', 'ties_by_year', 'ties_by_manufacturer', 'hcp_ties_papers', 'ties_vs_payments', 'product_facts',
                 'product_events']


def test_every_query_is_registered_with_units_panel_sources_and_caveats():
    queries.load_parts()
    mine = {name: spec for name, spec in queries.REGISTRY.items() if spec['function'].__module__ == queries_money.__name__}
    assert sorted(mine) == sorted(MONEY_QUERIES)
    for name, spec in mine.items():
        names = [c['name'] for c in spec['columns']]
        assert spec['default_panel'] and spec['tables'] and spec['years'] and spec['skills'], name
        assert all(c in names for c in [spec['default_panel'].get('x'), *spec['default_panel'].get('y', [])] if c), name
        assert all(c['unit'] == 'usd' for c in spec['columns'] if c['name'].endswith('_usd')), name
        assert all(c['name'].endswith('_usd') for c in spec['columns'] if c['unit'] == 'usd'), name
        assert ('PAYMENTS' in spec['caveats']) == any(t.startswith(('pay_', 'ownership')) for t in spec['tables']), name
        assert ('TIES' in spec['caveats']) == any(t.startswith('tie_') for t in spec['tables']), name
    assert mine['payments_by_year']['params']['rule']['default'] == 'lymphoma_only'
    assert mine['top_paid_hcps']['params']['nature']['default'] == 'services' and mine['top_paid_hcps']['params']['n']['default'] == 15
    assert 'split equally' in mine['payments_by_product']['description']
    assert mine['payments_by_product']['default_panel'] == {'type': 'line', 'x': 'year', 'y': ['total_usd'], 'split_by': 'brand', 'markers': 'lymphoma_products'}


def test_parameters_are_validated_before_any_sql_runs(con):
    bad = [('payments_by_year', {'scope': 'community', 'id': '1', 'rule': 'any_slot'}, 'rule must be one of'),
           ('payments_by_year', {'scope': 'community', 'id': '9'}, 'No community with id 9'),
           ('payments_by_year', {'scope': 'region', 'id': '1'}, 'scope must be one of'),
           ('payments_by_year', {'scope': 'community'}, 'needs the parameter id'),
           ('payments_by_year', {'scope': 'hcp', 'id': A, 'sql': 'drop table dim_hcp'}, 'Unknown parameter sql'),
           ('payments_by_product', {'scope': 'hcp', 'id': A, 'brand': 'Aspirin'}, 'brand must be one of'),
           ('payments_by_product', {'scope': 'hcp', 'id': A, 'year_from': 2018}, 'between 2019 and 2025'),
           ('top_paid_hcps', {'scope': 'community', 'id': '1', 'year': 'last'}, 'whole number'),
           ('top_paid_hcps', {'scope': 'community', 'id': '1', 'nature': 'royalty'}, 'nature must be one of'),
           ('top_paid_hcps', {'scope': 'community', 'id': '1', 'n': 500}, 'between 1 and 50'),
           ('research_studies', {'scope': 'center', 'id': '000000'}, 'No center with id'),
           ('hcp_ties_papers', {}, 'needs the parameter npi'),
           ('product_events', {'lymphoma_only': 'maybe'}, 'lymphoma_only must be one of'),
           ('payments_by_everything', {}, 'no query named')]
    for name, params, message in bad:
        with pytest.raises(QueryError, match=message):
            queries.run(name, params, con=con)
    assert queries.run('payments_by_year', {'scope': 'community', 'id': 1, 'rule': ''}, con=con)['params']['rule'] == 'lymphoma_only'
    assert queries.run('top_paid_hcps', {'scope': 'community', 'id': '1', 'year': '2019'}, con=con)['params']['year'] == 2019
    assert queries.run('payments_by_year', {'scope': 'community', 'id': '1'}, con=con, skill='industry-payments')['scope_label'].startswith('North orbit')
    with pytest.raises(QueryError, match='not part of the skill'):
        queries.run('ownership', {'scope': 'community', 'id': '1'}, con=con, skill='disclosed-ties')
    with pytest.raises(QueryError):
        queries_money.payments_by_year(con, 'community', '1', rule='1 = 1; drop table dim_hcp')
    assert con.execute('select count(*) from dim_hcp').fetchone()[0] == len(PEOPLE)


# ---------------------------------------------------------------- the real development pack, when it has been built

DEV = PACK / 'dev_money.sqlite'


@pytest.mark.skipif(not DEV.exists(), reason='agent/pack/dev_money.sqlite has not been built')
def test_every_query_runs_on_the_development_pack_and_totals_agree():
    con = sqlite3.connect(f'file:{DEV}?mode=ro', uri=True)
    one = lambda sql: con.execute(sql).fetchone()[0]
    assert {k: v for k, v in pack_money.check(con).items() if v} == {}
    ids = {'community': '11', 'division_group': 'core',
           'center': one('select ccn from pay_research_hcp_center_year group by ccn order by sum(records) desc limit 1'),
           'hcp': one('''select g.npi from pay_general_year g join dim_hcp h on h.npi = g.npi where h.is_core = 1
                         and g.npi in (select npi from tie_author_group) and g.npi in (select npi from pay_research_year)
                         group by g.npi order by sum(g.lymphoma_only_usd) desc limit 1''')}
    for name in MONEY_QUERIES:
        spec = queries.REGISTRY.get(name) or queries.load_parts()[name]
        for scope, ident in ids.items() if 'scope' in spec['params'] else [(None, None)]:
            if (name, scope) == ('top_paid_hcps', 'hcp'):
                continue
            params = {'scope': scope, 'id': ident} if scope else {'npi': ids['hcp']} if 'npi' in spec['params'] else {}
            out = queries.run(name, params, con=con)
            assert out['row_count'] <= queries.MAX_ROWS and (out['rows'] or name in ('ownership', 'hcp_ties_papers')), (name, scope)
    total = lambda name, column='total_usd', **params: round(sum(r[column] for r in queries.run(name, params, con=con)['rows']), 2)
    groups = ('core', 'affiliates', 'referring', 'other')
    for rule in ('lymphoma_only', 'cart_only'):
        by_group = sum(total('payments_by_year', scope='division_group', id=g, rule=rule) for g in groups)
        assert round(by_group, 2) == round(one(f'select sum(amount_usd) from pay_general_record where {rule} = 1'), 2)
        year = total('payments_by_year', scope='community', id='11', rule=rule)
        assert year == total('payments_monthly', scope='community', id='11', rule=rule) == total('payments_by_manufacturer', scope='community', id='11', rule=rule)
    assert total('payments_by_product', scope='community', id='11') == total('payments_by_year', scope='community', id='11', rule='cart_only')
    assert total('payments_monthly', scope='community', id='11', brand='Yescarta') == total('payments_by_product', scope='community', id='11', brand='Yescarta') > 0
    mine = {r['year']: r for r in queries.run('research_by_year', {'scope': 'hcp', 'id': ids['hcp']}, con=con)['rows']}
    for year, product, investigator, usd, text in con.execute(
            'select year, product_field_records, product_field_investigator_records, product_field_record_total_usd, text_only_records '
            'from pay_research_year where npi = ?', [ids['hcp']]):
        assert (mine[year]['product_field_records'], mine[year]['investigator_records'], mine[year]['record_total_usd'],
                mine[year]['text_only_records']) == (product, investigator, round(usd, 2), text)
    assert queries.run('ownership', {'scope': 'division_group', 'id': 'affiliates'}, con=con)['rows'][0]['interests'] == 0
    con.close()
