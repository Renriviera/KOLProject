"""Claims part of the agent pack: build, blank handling, checks, card facts and every named query, on invented rows."""
import csv

import pytest

from kolproject.agent import pack_claims as pc
from kolproject.agent import queries
from kolproject.agent.packbase import connect, create_table
from kolproject.agent.paths import PACK

A, B, C, D = '1000000001', '1000000002', '1000000003', '1000000004'
PUBLISHED_CENTER, CANCER_CENTER, CHILDRENS, ABSENT, SMALL_CENTER = '000001', '000002', '003301', '000004', '000005'

SOURCES = {
    'hcp_medicare_partb_payments_by_year_2017_2018': (
        'npi,service_year,by_provider_status,cms_provider_type,total_medicare_payment_usd,drug_medicare_payment_usd,'
        'non_drug_medicare_payment_usd,total_beneficiaries',
        [(A, 2017, 'published_split', 'Hematology-Oncology', '1000.50', '600.30', '400.20', 120),
         (B, 2017, 'split_suppressed', 'Hematology', '300.00', '', '', 40),
         (C, 2017, 'no_row', '', '', '', '', '')]),
    'hcp_medicare_partb_payments_by_year': (
        'npi,service_year,by_provider_status,cms_provider_type,total_medicare_payment_usd,drug_medicare_payment_usd,'
        'non_drug_medicare_payment_usd,total_beneficiaries',
        [(A, 2024, 'published_split', 'Hematology-Oncology', '2000.00', '1500.00', '500.00', 150),
         (B, 2024, 'published_split', 'Hematology', '100.00', '0.00', '100.00', 20),
         (C, 2024, 'split_suppressed', 'Medical Oncology', '50.00', '', '', 12),
         (D, 2024, 'no_row', '', '', '', '', '')]),
    'hcp_medicare_partb_treatment_markers_2017_2023': (
        'npi,service_year,hcpcs_code,place_of_service,code_family,beneficiaries,services_or_units,'
        'estimated_medicare_payment_usd',
        [(A, 2018, '96413', 'O', 'chemo_administration', 20, 100, '5000.00'),
         (A, 2018, '96415', 'O', 'chemo_administration', 15, 60, '1200.00'),
         (A, 2018, 'J9310', 'O', 'rituximab_intravenous', 12, 300, '20000.00'),
         (B, 2018, '96413', 'O', 'chemo_administration', 11, 30, '900.00'),
         (A, 2023, 'J9312', 'O', 'rituximab_intravenous', 13, '3000.5', '15000.00')]),
    'treatment_markers_2024': (
        'npi,hcpcs_code,place_of_service,marker_group,beneficiaries,services_or_units,service_year',
        [(A, '96413', 'O', 'chemo_administration', 18, 90, 2024), (A, 'Q5119', 'O', 'rituximab_iv', 14, 2800, 2024)]),
    'medicare_partb_drug_rows_2024': (
        'npi,hcpcs_code,place_of_service,lymphoma_product_family,beneficiaries,services_or_units,'
        'estimated_medicare_payment_usd',
        [(A, 'Q5119', 'O', 'rituximab_intravenous', 14, 2800, '9000.00'), (A, 'J9034', 'O', 'bendamustine', 11, 500, '700.00'),
         (A, 'J0185', 'O', '', 20, 100, '55.00'), (C, 'J9301', 'O', 'obinutuzumab', 12, 1000, '4000.00')]),
    'lymphoma_product_code_validity': (
        'hcpcs_code,service_year,code_family',
        [('J9310', 2017, 'rituximab_intravenous'), ('J9312', 2017, 'rituximab_intravenous'),
         ('Q5119', 2017, 'rituximab_intravenous'), ('J9034', 2017, 'bendamustine'), ('J9301', 2017, 'obinutuzumab'),
         ('J9350', 2017, 'mosunetuzumab'), ('J9309', 2017, 'polatuzumab_vedotin')]),
    'hcp_medicare_partd_lymphoma_orals_2017_2023': (
        'npi,service_year,generic_name,brand_name,drug_class,total_claims,total_30day_fills,total_drug_cost_usd,'
        'total_beneficiaries',
        [(A, 2023, 'Lenalidomide', 'Revlimid', 'lenalidomide', 30, '31.5', '300000.10', 12),
         (A, 2023, 'Lenalidomide', 'Lenalidomide', 'lenalidomide', 12, 12, '50000.00', ''),
         (A, 2023, 'Ibrutinib', 'Imbruvica', 'btk_inhibitor', 20, 20, '200000.00', ''),
         (B, 2023, 'Acalabrutinib', 'Calquence', 'btk_inhibitor', 11, 11, '150000.00', ''),
         (B, 2023, 'Acalabrutinib Maleate', 'Calquence', 'btk_inhibitor', 15, '15.5', '160000.00', '')]),
    'part_d_lymphoma_orals_2024': (
        'npi,generic_name,brand_name,total_claims,total_30day_fills,total_beneficiaries,total_drug_cost_usd,service_year',
        [(A, 'Lenalidomide', 'Revlimid', 40, 40, 13, '400000.5', 2024), (B, 'Venetoclax', 'Venclexta', 25, 25, '', '90000', 2024),
         (C, 'Tazemetostat Hydrobromide', 'Tazverik', 11, 11, '', '70000', 2024),
         (B, 'Lenalidomide', 'Lenalidomide', 50, 50, '', '10000', 2024)]),
    'hcp_medicaid_payments_by_year_2018_2024': (
        'npi,year,medicaid_row_sides,medicaid_servicing_paid_usd,medicaid_servicing_visit_admin_paid_usd,'
        'medicaid_servicing_drug_code_paid_usd,medicaid_billing_paid_usd',
        [(A, 2023, 'servicing', '1000.00', '800.00', '', ''), (B, 2023, 'servicing_and_billing', '500.00', '', '0.00', '77.00'),
         (C, 2023, 'billing', '', '', '', '900.00'), (A, 2024, 'servicing', '200.00', '150.00', '10.00', '')]),
    'cart_site_volume_panel': (
        'site_key,ccn,medicare_ffs_drg018_2021,medicare_ffs_drg018_2022,medicare_ffs_drg018_2023,medicare_ffs_drg018_2024,'
        'medicare_file_status_2024,ny_sparcs_aprdrg011_2024_commercial',
        [(PUBLISHED_CENTER, PUBLISHED_CENTER, '', '', 12, 20, 'count_published', 99),
         (CANCER_CENTER, CANCER_CENTER, '', '', '', '', 'not_in_file', ''), (CHILDRENS, CHILDRENS, '', '', '', '', 'not_in_file', ''),
         (ABSENT, ABSENT, '', '', '', '', 'not_in_file', ''), (SMALL_CENTER, SMALL_CENTER, '', 11, '', '', 'in_file_no_drg018_row', ''),
         ('999999', '999999', '', '', '', 30, 'count_published', ''), ('practice-invented', '', '', '', '', '', 'no_ccn', '')]),
    'cart_product_program_spending_by_year': (
        'brand_name,program,calendar_year,total_spending_usd,total_claims,total_dosage_units,total_beneficiaries,value_status',
        [('Yescarta', 'medicare_part_b', 2023, '1000.5', 20, '12.5', 12, 'published'),
         ('Yescarta', 'medicare_part_b', 2022, '', '', '', '', 'no_value_in_file'),
         ('Tecartus', 'medicare_part_b', 2023, '', '', '', '', 'brand_not_in_file'),
         ('Yescarta', 'medicaid', 2023, '500', 15, 15, '', 'published')]),
    'state_medicare_cart_administration_by_year': (
        'geography_level,geography_code,geography_name,service_year,hcpcs_code,place_of_service,rendering_providers,'
        'beneficiaries,services',
        [('state', '48', 'Texas', 2023, '0540T', 'F', 8, 20, 21), ('national', 'US', 'National', 2023, '0540T', 'O', 5, 12, 12),
         ('national', 'US', 'National', 2023, '0540T', 'F', 50, 100, 105)]),
}


def write_sources(folder):
    for table, (header, rows) in SOURCES.items():
        with (folder / f'{table}.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(header.split(','))
            writer.writerows(rows)


def build_dims(con):
    create_table(con, 'dim_community', [('community_id', 'int'), ('short_label', 'text')], [(1, 'North orbit'), (2, 'South orbit')])
    create_table(con, 'dim_hcp', [('npi', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('division_group', 'text'),
                                  ('community_id', 'int')],
                 [(A, 'Test Alpha', 'Springfield', 'TX', 'referring', 1), (B, 'Test Beta', 'Springfield', 'TX', 'core', 1),
                  (C, 'Test Gamma', 'Riverton', 'OH', 'affiliates', 2), (D, 'Test Delta', 'Riverton', 'OH', 'other', None)])
    create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')], [(A, PUBLISHED_CENTER), (B, PUBLISHED_CENTER)])
    create_table(con, 'dim_center', [('ccn', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('community_id', 'int'),
                                     ('pps_exempt_cancer_hospital', 'int'), ('medicare_file_status_2024', 'text')],
                 [(PUBLISHED_CENTER, 'General Hospital One', 'Springfield', 'TX', 1, 0, 'count_published'),
                  (CANCER_CENTER, 'Cancer Hospital Two', 'Springfield', 'TX', 1, 1, 'not_in_file'),
                  (CHILDRENS, 'Childrens Hospital Three', 'Riverton', 'OH', 2, 0, 'not_in_file'),
                  (ABSENT, 'Island Hospital Four', 'Lakeside', 'PR', None, 0, 'not_in_file'),
                  (SMALL_CENTER, 'General Hospital Five', 'Riverton', 'OH', 2, 0, 'in_file_no_drg018_row')])


@pytest.fixture
def con(tmp_path, monkeypatch):
    write_sources(tmp_path)
    monkeypatch.setattr(pc, 'source', lambda snapshot, table: tmp_path / f'{table}.csv')
    monkeypatch.setattr(queries, 'PARTS', ['queries_claims'])
    con = connect(tmp_path / 'pack.sqlite')
    build_dims(con)
    pc.build(con)
    con.commit()
    yield con
    con.close()


def run(con, name, **params):
    return queries.run(name, params, con=con)['rows']


def cells(con, sql, *args):
    return [tuple(row) for row in con.execute(sql, args)]


def rebuilt_without_key(con, table):
    """The same table without its primary key, so a test can insert a row that breaks the key."""
    con.execute(f'create table copy as select * from "{table}"')
    con.execute(f'drop table "{table}"')
    con.execute(f'alter table copy rename to "{table}"')


def test_build_loads_every_source_row_once_and_checks_pass(con):
    counts = {table: con.execute(f'select count(*) from "{table}"').fetchone()[0] for table in pc.TABLES}
    assert counts == {'mcr_partb_year': 7, 'mcr_marker_year': 9, 'mcr_partd_year': 9, 'mcd_year': 3,
                      'center_medicare_stays': 20, 'national_spending': 4, 'mcr_cart_admin_state_year': 3}
    checks = pc.check(con)
    assert checks and not {name: count for name, count in checks.items() if count}
    registered = dict(cells(con, 'select name, rows from pack_table'))
    assert all(registered[table] == counts[table] for table in pc.TABLES)


def test_2024_drug_rows_in_both_sources_are_loaded_once_with_their_payment(con):
    rows = cells(con, 'select npi, code, family, beneficiaries, services, estimated_payment_usd, source from mcr_marker_year '
                      'where year = 2024 order by npi, code')
    assert rows == [(A, '96413', 'chemotherapy administration', 18, 90.0, None, 'markers_2024'),
                    (A, 'J9034', 'bendamustine', 11, 500.0, 700.0, 'drug_rows_2024'),
                    (A, 'Q5119', 'rituximab', 14, 2800.0, 9000.0, 'markers_2024'),
                    (C, 'J9301', 'obinutuzumab', 12, 1000.0, 4000.0, 'drug_rows_2024')]
    assert not cells(con, "select 1 from mcr_marker_year where code = 'J0185'")
    assert set(pc.code_families().values()) <= set(pc.FAMILIES)
    assert pc.code_families()['J9350'] == 'bispecific antibody' and pc.code_families()['J9309'] == 'polatuzumab'


def test_blank_cells_stay_null_and_billing_only_medicaid_rows_are_left_out(con):
    assert cells(con, 'select status, total_payment_usd, drug_payment_usd, beneficiaries from mcr_partb_year where year = 2017 '
                      'order by npi') == [('published_split', 1000.5, 600.3, 120), ('split_suppressed', 300.0, None, 40),
                                          ('no_row', None, None, None)]
    assert cells(con, 'select drug_payment_usd from mcr_partb_year where npi = ? and year = 2024', B) == [(0.0,)]
    assert cells(con, 'select count(*), count(beneficiaries) from mcr_partd_year') == [(9, 2)]
    assert cells(con, 'select npi, year, paid_usd, visit_admin_paid_usd, drug_code_paid_usd from mcd_year order by year, npi') == [
        (A, 2023, 1000.0, 800.0, None), (B, 2023, 500.0, None, 0.0), (A, 2024, 200.0, 150.0, 10.0)]
    assert cells(con, "select spending_usd, claims, value_status from national_spending where brand = 'Tecartus'") == [
        (None, None, 'brand_not_in_file')]


def test_part_d_drug_names_are_tidy_and_classed(con):
    assert cells(con, 'select distinct drug, drug_class, cms_generic_name from mcr_partd_year where npi = ? and year = 2023 '
                      'order by cms_generic_name', B) == [('acalabrutinib', 'BTK inhibitor', 'Acalabrutinib'),
                                                           ('acalabrutinib', 'BTK inhibitor', 'Acalabrutinib Maleate')]
    assert pc.tidy_drug('Tazemetostat Hydrobromide') == 'tazemetostat'
    with pytest.raises(ValueError):
        pc.tidy_drug('Rituximab')


def test_center_stay_status_in_words(con):
    status = lambda ccn: cells(con, 'select year, stays, status from center_medicare_stays where ccn = ? order by year', ccn)
    assert status(PUBLISHED_CENTER) == [(2021, None, pc.BELOW_THRESHOLD), (2022, None, pc.BELOW_THRESHOLD),
                                        (2023, 12, pc.PUBLISHED), (2024, 20, pc.PUBLISHED)]
    assert {row[2] for row in status(CANCER_CENTER)} == {pc.HOSPITAL_TYPE} == {row[2] for row in status(CHILDRENS)}
    assert {row[2] for row in status(ABSENT)} == {pc.NOT_IN_FILE}
    assert status(SMALL_CENTER)[1] == (2022, 11, pc.PUBLISHED) and status(SMALL_CENTER)[3] == (2024, None, pc.BELOW_THRESHOLD)
    assert not cells(con, "select 1 from center_medicare_stays where ccn = '999999'")
    columns = {row[1] for table in pc.TABLES for row in con.execute(f'pragma table_info("{table}")')}
    assert not any(word in column for column in columns for word in pc.FORBIDDEN)


def test_partd_by_drug_year_adds_brand_and_salt_forms(con):
    rows = run(con, 'partd_by_drug_year', scope='community', id='1')
    assert [(r['year'], r['drug'], r['prescribers'], r['claims'], r['drug_cost_usd']) for r in rows] == [
        (2023, 'acalabrutinib', 1, 26, 310000.0), (2023, 'ibrutinib', 1, 20, 200000.0), (2023, 'lenalidomide', 1, 42, 350000.1),
        (2024, 'lenalidomide', 2, 90, 410000.5), (2024, 'venetoclax', 1, 25, 90000.0)]
    assert rows[0]['drug_class'] == 'BTK inhibitor'
    assert [r['claims'] for r in run(con, 'partd_by_drug_year', scope='hcp', id=A)] == [20, 42, 40]
    assert run(con, 'partd_by_drug_year', scope='hcp', id=D) == []
    assert [r['drug'] for r in run(con, 'partd_by_drug_year', scope='center', id=PUBLISHED_CENTER)] == [
        'acalabrutinib', 'ibrutinib', 'lenalidomide', 'lenalidomide', 'venetoclax']


def test_top_prescribers_ranks_one_year_and_refuses_a_single_hcp(con):
    rows = run(con, 'top_prescribers', scope='community', id='1')
    assert [(r['name'], r['npi'], r['division_group'], r['community'], r['state'], r['claims']) for r in rows] == [
        ('Test Beta', B, 'core', 'North orbit', 'TX', 75), ('Test Alpha', A, 'referring', 'North orbit', 'TX', 40)]
    assert [r['claims'] for r in run(con, 'top_prescribers', scope='community', id='1', drug='lenalidomide', n=1)] == [50]
    assert [r['npi'] for r in run(con, 'top_prescribers', scope='division_group', id='affiliates')] == [C]
    assert [r['claims'] for r in run(con, 'top_prescribers', scope='community', id='1', year=2023)] == [62, 26]
    assert run(con, 'top_prescribers', scope='community', id='1', year=2019) == []
    with pytest.raises(queries.QueryError, match='partd_by_drug_year'):
        run(con, 'top_prescribers', scope='hcp', id=A)


def test_partb_markers_count_hcps_and_never_add_patients_across_codes(con):
    rows = run(con, 'partb_markers_by_year', scope='community', id='1')
    shown = [(r['year'], r['family'], r['hcps_billing'], r['services'], r['beneficiaries'], r['hcps_with_payment'],
              r['estimated_payment_usd']) for r in rows]
    assert shown == [(2018, 'chemotherapy administration', 2, 190.0, 31, 2, 7100.0), (2018, 'rituximab', 1, None, 12, 1, 20000.0),
                     (2023, 'rituximab', 1, None, 13, 1, 15000.0), (2024, 'bendamustine', 1, None, 11, 1, 700.0),
                     (2024, 'chemotherapy administration', 1, 90.0, 18, 0, None), (2024, 'rituximab', 1, None, 14, 1, 9000.0)]
    only = run(con, 'partb_markers_by_year', scope='community', id='1', family='rituximab')
    assert [r['year'] for r in only] == [2018, 2023, 2024] and {r['family'] for r in only} == {'rituximab'}
    assert run(con, 'partb_markers_by_year', scope='community', id='1', family='tafasitamab') == []


def test_partb_payment_keeps_hidden_splits_out_of_the_split_sums(con):
    first, last = run(con, 'partb_payment_by_year', scope='community', id='1')
    assert first == {'year': 2017, 'hcps_with_row': 2, 'total_payment_usd': 1300.5, 'hcps_with_split': 1, 'drug_payment_usd': 600.3,
                     'non_drug_payment_usd': 400.2, 'hcps_split_hidden': 1, 'drug_share': 0.6}
    assert (last['hcps_with_split'], last['drug_payment_usd'], last['non_drug_payment_usd'], last['drug_share']) == (2, 1500.0, 600.0, 0.7143)
    none, hidden = run(con, 'partb_payment_by_year', scope='community', id='2')
    assert none == {'year': 2017, 'hcps_with_row': 0, 'total_payment_usd': None, 'hcps_with_split': 0, 'drug_payment_usd': None,
                    'non_drug_payment_usd': None, 'hcps_split_hidden': 0, 'drug_share': None}
    assert (hidden['hcps_with_row'], hidden['total_payment_usd'], hidden['drug_payment_usd'], hidden['hcps_split_hidden'],
            hidden['drug_share']) == (1, 50.0, None, 1, None)
    zero = run(con, 'partb_payment_by_year', scope='hcp', id=B)[-1]
    assert (zero['drug_payment_usd'], zero['drug_share']) == (0.0, 0.0)


def test_medicaid_by_year_counts_the_hcps_behind_each_class(con):
    first, last = run(con, 'medicaid_by_year', scope='community', id='1')
    assert first == {'year': 2023, 'hcps_with_row': 2, 'paid_usd': 1500.0, 'hcps_with_visit_admin': 1, 'visit_admin_paid_usd': 800.0,
                     'hcps_with_drug_code': 1, 'drug_code_paid_usd': 0.0, 'data_note': 'full year'}
    assert last['year'] == 2024 and last['paid_usd'] == 200.0 and last['data_note'].startswith('preliminary')
    assert run(con, 'medicaid_by_year', scope='community', id='2') == []
    assert queries.REGISTRY['medicaid_by_year']['caveats'] == ['MEDICAID', 'LABELS']


def test_center_and_community_stays_show_what_is_not_published(con):
    assert run(con, 'center_medicare_cart_stays', ccn=PUBLISHED_CENTER) == [
        {'year': 2021, 'stays': None, 'status': pc.BELOW_THRESHOLD}, {'year': 2022, 'stays': None, 'status': pc.BELOW_THRESHOLD},
        {'year': 2023, 'stays': 12, 'status': pc.PUBLISHED}, {'year': 2024, 'stays': 20, 'status': pc.PUBLISHED}]
    assert {r['status'] for r in run(con, 'center_medicare_cart_stays', ccn=CANCER_CENTER)} == {pc.HOSPITAL_TYPE}
    with pytest.raises(queries.QueryError, match='No center'):
        run(con, 'center_medicare_cart_stays', ccn='999999')
    north = run(con, 'community_medicare_cart_stays', community_id=1)
    assert north[0] == {'year': 2021, 'centers_with_count': 0, 'stays': None, 'centers_without_count': 2, 'centers_never_published': 1}
    assert north[3] == {'year': 2024, 'centers_with_count': 1, 'stays': 20, 'centers_without_count': 1, 'centers_never_published': 1}
    assert run(con, 'community_medicare_cart_stays', community_id='2')[1]['stays'] == 11
    with pytest.raises(queries.QueryError, match='No community'):
        run(con, 'community_medicare_cart_stays', community_id=9)


def test_top_centers_rank_published_counts_only(con):
    assert run(con, 'top_centers_by_medicare_stays') == [{'name': 'General Hospital One', 'ccn': PUBLISHED_CENTER, 'city': 'Springfield',
                                                         'state': 'TX', 'community': 'North orbit', 'stays': 20}]
    assert [r['ccn'] for r in run(con, 'top_centers_by_medicare_stays', year=2022)] == [SMALL_CENTER]
    assert [r['ccn'] for r in run(con, 'top_centers_by_medicare_stays', year=2022, state='oh')] == [SMALL_CENTER]
    assert run(con, 'top_centers_by_medicare_stays', year=2022, state='TX') == []
    assert run(con, 'top_centers_by_medicare_stays', year=2021) == []
    with pytest.raises(queries.QueryError, match='two-letter'):
        run(con, 'top_centers_by_medicare_stays', state='Texas')


def test_national_spending_and_state_administration(con):
    rows = run(con, 'national_spending_by_brand_year')
    assert [(r['year'], r['brand'], r['spending_usd'], r['claims'], r['beneficiaries'], r['status']) for r in rows] == [
        (2022, 'Yescarta', None, None, None, 'no value in the file for this year'),
        (2023, 'Tecartus', None, None, None, 'brand not in the file'), (2023, 'Yescarta', 1000.5, 20, 12, 'published')]
    medicaid = run(con, 'national_spending_by_brand_year', program='medicaid')
    assert [(r['spending_usd'], r['beneficiaries']) for r in medicaid] == [(500.0, None)] and medicaid[0]['program'].startswith('Medicaid')
    places = run(con, 'cart_admin_by_state_year')
    assert [(r['geography'], r['state'], r['place'], r['beneficiaries'], r['services']) for r in places] == [
        ('National', None, 'hospital or other facility', 100, 105), ('National', None, 'office', 12, 12),
        ('Texas', 'TX', 'hospital or other facility', 20, 21)]


def test_prescribing_summary_is_one_row_and_blank_where_nothing_is_published(con):
    assert run(con, 'prescribing_summary', scope='community', id='1') == [{
        'year': 2024, 'hcps': 2, 'partd_prescribers': 2, 'partd_claims': 115, 'rituximab_billers': 1, 'infusion_billers': 1,
        'partb_hcps_with_split': 2, 'partb_drug_payment_usd': 1500.0, 'partb_non_drug_payment_usd': 600.0, 'medicaid_hcps': 1,
        'medicaid_paid_usd': 200.0}]
    early = run(con, 'prescribing_summary', scope='community', id='1', year=2018)[0]
    assert (early['partd_prescribers'], early['partd_claims'], early['infusion_billers'], early['rituximab_billers']) == (0, None, 2, 1)
    assert (early['partb_hcps_with_split'], early['partb_drug_payment_usd'], early['medicaid_hcps'], early['medicaid_paid_usd']) == (
        0, None, None, None)
    nobody = run(con, 'prescribing_summary', scope='hcp', id=D, year=2023)[0]
    assert (nobody['hcps'], nobody['partd_claims'], nobody['medicaid_hcps'], nobody['medicaid_paid_usd']) == (1, None, 0, None)


def test_parameters_are_validated_before_any_sql_runs(con):
    for name, params, message in [
            ('partd_by_drug_year', {'scope': 'community', 'id': '99'}, 'No community with id 99'),
            ('partd_by_drug_year', {'scope': 'hcp', 'id': '1999999999'}, 'No hcp'),
            ('partd_by_drug_year', {'scope': 'payer', 'id': '1'}, 'scope must be one of'),
            ('partd_by_drug_year', {'scope': 'community'}, 'needs the parameter id'),
            ('top_prescribers', {'scope': 'community', 'id': '1', 'drug': 'rituximab'}, 'drug must be one of'),
            ('top_prescribers', {'scope': 'community', 'id': '1', 'n': 500}, 'n must be between'),
            ('partb_markers_by_year', {'scope': 'community', 'id': '1', 'family': 'commercial'}, 'family must be one of'),
            ('prescribing_summary', {'scope': 'community', 'id': '1', 'year': 2016}, 'year must be between 2017 and 2024'),
            ('top_centers_by_medicare_stays', {'year': 2020}, 'year must be between 2021 and 2024'),
            ('national_spending_by_brand_year', {'program': 'commercial'}, 'program must be one of'),
            ('medicaid_by_year', {'scope': 'community', 'id': '1', 'payer': 'x'}, 'Unknown parameter payer'),
            ('cart_admin_by_state_year', {'state': 'TX'}, 'Unknown parameter state')]:
        with pytest.raises(queries.QueryError, match=message):
            queries.run(name, params, con=con)
    with pytest.raises(queries.QueryError, match='not part of the skill'):
        queries.run('top_prescribers', {'scope': 'community', 'id': '1'}, con=con, skill='product-timeline')
    assert queries.run('national_spending_by_brand_year', {}, con=con, skill='product-timeline')['row_count'] == 3


def test_every_query_declares_columns_caveats_and_a_panel(con):
    queries.load_parts()
    mine = {name: spec for name, spec in queries.REGISTRY.items() if spec['function'].__module__.endswith('queries_claims')}
    assert set(mine) == {'partd_by_drug_year', 'top_prescribers', 'partb_markers_by_year', 'partb_payment_by_year', 'medicaid_by_year',
                         'center_medicare_cart_stays', 'community_medicare_cart_stays', 'top_centers_by_medicare_stays',
                         'national_spending_by_brand_year', 'cart_admin_by_state_year', 'prescribing_summary'}
    for name, spec in mine.items():
        assert spec['default_panel'] and spec['tables'] and spec['years'] and spec['skills'], name
        assert ('LABELS' in spec['caveats']) == ('scope' in spec['params']), name
        assert all(column['name'].endswith('_usd') == (column['unit'] == 'usd') for column in spec['columns']), name
        assert not any(word in column['name'] for column in spec['columns'] for word in pc.FORBIDDEN), name
        medicare = any(table.startswith(('mcr_', 'center_medicare', 'national')) for table in spec['tables'])
        assert medicare == ('MEDICARE' in spec['caveats'] and 'NOT_PRODUCT_SPECIFIC' in spec['caveats']), name


def test_card_facts_are_short_and_dated(con):
    facts = pc.card_facts(con)
    assert all(kind in ('hcp', 'center', 'community') and isinstance(key, str) and 1 <= len(lines) <= 3
               for (kind, key), lines in facts.items())
    assert facts[('hcp', A)] == [
        'Medicare Part D, 2024: 40 claims for lenalidomide, the only oral lymphoma drug with a published count for this HCP.',
        "Medicare Part B, 2024: billed chemotherapy administration (90 services), bendamustine and rituximab under this HCP's own NPI.",
        "Medicare Part B paid $2,000 in 2024 for services under this HCP's NPI, 75% of it for drugs."]
    assert facts[('hcp', B)][0] == 'Medicare Part D, 2024: 75 claims for oral lymphoma drugs, most for lenalidomide (50 claims).'
    assert facts[('hcp', B)][-1].endswith('none of it for drugs.')
    assert ('hcp', D) not in facts and len(facts[('hcp', C)]) == 2
    assert facts[('center', PUBLISHED_CENTER)] == [
        'Medicare fee-for-service CAR-T inpatient stays, all products together: 12 in 2023 and 20 in 2024.',
        'No count is published for 2021 and 2022, which means fewer than 11 stays, or none.']
    assert 'cancer hospital' in facts[('center', CANCER_CENTER)][0] and "children's hospital" in facts[('center', CHILDRENS)][0]
    assert 'not in the Medicare inpatient file' in facts[('center', ABSENT)][0]
    assert facts[('community', '1')][0] == ('Published Medicare CAR-T inpatient stays at the attached centers: 12 in 2023 '
                                           '(1 of 2 centers) and 20 in 2024 (1 of 2 centers).')
    assert "including 1 cancer or children's hospital that" in facts[('community', '1')][1]
    assert facts[('community', '1')][2].endswith('in 2024: lenalidomide (90 claims) and venetoclax (25 claims).')


@pytest.mark.parametrize('damage, failing', [
    ('update mcr_partb_year set drug_payment_usd = drug_payment_usd + 0.01 where npi = ? and year = 2024', 'claims_partb_totals'),
    ("update mcr_partb_year set drug_payment_usd = 0 where status = 'split_suppressed'", 'claims_partb_status'),
    ('delete from mcr_partd_year where npi = ? and year = 2024', 'claims_partd_rows'),
    ('update mcr_partd_year set beneficiaries = 0 where beneficiaries is null', 'claims_partd_totals'),
    ('update mcr_partd_year set beneficiaries = 5 where npi = ? and year = 2024', 'claims_published_count_under_11'),
    ("update mcr_partd_year set drug_class = 'BCL-2 inhibitor' where drug = 'ibrutinib'", 'claims_partd_drug_names'),
    ("update mcr_marker_year set family = 'rituximab' where code = '96413'", 'claims_marker_family'),
    ('update mcr_marker_year set estimated_payment_usd = 1 where year = 2024 and estimated_payment_usd is null', 'claims_marker_totals_2024'),
    ("insert into mcr_marker_year values ('1000000009', 2024, '96413', 'O', 'chemotherapy administration', 12, 20, null, 'markers_2024')",
     'claims_npi_not_in_dim_hcp'),
    ('update mcd_year set paid_usd = paid_usd + 1 where npi = ? and year = 2024', 'claims_medicaid_totals'),
    ('update mcd_year set year = 2017 where npi = ? and year = 2024', 'claims_years_mcd_year'),
    ("update center_medicare_stays set stays = 3 where ccn = '000001' and year = 2021", 'claims_stays_status'),
    ("update center_medicare_stays set stays = stays + 1 where ccn = '000001' and year = 2024", 'claims_stays_totals'),
    ("insert into center_medicare_stays values ('999999', 2024, 30, 'published')", 'claims_ccn_not_in_dim_center'),
    ('alter table center_medicare_stays add column commercial_stays integer', 'claims_commercial_or_payer_split'),
    ("update national_spending set spending_usd = 1 where value_status != 'published'", 'claims_spending_status'),
    ("update mcr_cart_admin_state_year set state = null where level = 'state'", 'claims_cart_admin_state_code'),
])
def test_check_catches_a_broken_table(con, damage, failing):
    con.execute(damage, (A,) if '?' in damage else ())
    assert pc.check(con)[failing] > 0


def test_check_catches_a_2024_row_counted_twice(con):
    rebuilt_without_key(con, 'mcr_marker_year')
    con.execute("insert into mcr_marker_year select npi, year, code, place, family, beneficiaries, services, estimated_payment_usd, "
                "'drug_rows_2024' from mcr_marker_year where year = 2024 and code = 'Q5119'")
    checks = pc.check(con)
    assert checks['claims_marker_2024_counted_twice'] > 0 and checks['claims_duplicate_key_mcr_marker_year'] == 1


def test_every_query_runs_on_the_real_dev_pack(monkeypatch):
    path = PACK / 'dev_claims.sqlite'
    if not path.exists():
        pytest.skip('agent/pack/dev_claims.sqlite has not been built')
    monkeypatch.setattr(queries, 'PARTS', ['queries_claims'])
    real = connect(path, readonly=True)
    one = lambda sql: str(real.execute(sql).fetchone()[0])
    scopes = [('hcp', one('select p.npi from mcr_partd_year p join mcr_marker_year m on m.npi = p.npi join mcd_year d on d.npi = p.npi limit 1')),
              ('community', '11'), ('division_group', 'core'),
              ('center', one("select ccn from hcp_center where ccn in (select ccn from center_medicare_stays where status = 'published') "
                             'group by ccn order by count(*) desc limit 1'))]
    try:
        for scope, ident in scopes:
            for name in ('partd_by_drug_year', 'partb_markers_by_year', 'partb_payment_by_year', 'medicaid_by_year', 'prescribing_summary'):
                assert queries.run(name, {'scope': scope, 'id': ident}, con=real)['row_count'] > 0, (name, scope)
            if scope != 'hcp':
                assert queries.run('top_prescribers', {'scope': scope, 'id': ident, 'n': 5}, con=real)['row_count'] == 5
        for ccn in (scopes[-1][1], one("select ccn from center_medicare_stays where status != 'published' limit 1")):
            assert queries.run('center_medicare_cart_stays', {'ccn': ccn}, con=real)['row_count'] == 4
        assert queries.run('community_medicare_cart_stays', {'community_id': 11}, con=real)['row_count'] == 4
        top = queries.run('top_centers_by_medicare_stays', {}, con=real)['rows']
        assert len(top) == 15 and top[0]['stays'] >= top[-1]['stays'] >= pc.SMALLEST_PUBLISHED
        assert queries.run('national_spending_by_brand_year', {'program': 'medicaid'}, con=real)['row_count'] == 35
        assert queries.run('cart_admin_by_state_year', {}, con=real)['row_count'] == 56
        referring = queries.run('partb_payment_by_year', {'scope': 'division_group', 'id': 'referring'}, con=real)['rows'][-1]
        assert (referring['year'], referring['hcps_with_row'], referring['drug_payment_usd']) == (2024, 4504, 4766650122.17)
    finally:
        real.close()
