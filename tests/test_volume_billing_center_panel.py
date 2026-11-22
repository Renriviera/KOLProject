"""Site keys, blank-versus-zero rules, publication split and checks of the CAR-T site volume panel.

Nothing here needs the raw, interim or notes folders: rules run on synthetic rows, and the last test reads
only Data/processed.
"""
from pathlib import Path

import pytest

from kolproject.common import write_csv
from kolproject.volume_billing import center_panel as cp

REAL = Path(__file__).resolve().parents[1] / 'Data' / 'processed'
YEARS = {'2021': '-', '2022': '-', '2023': '-', '2024': '-', '2025': '-'}


def bmt(record_id, site, kind='Adult Program', how='exact_addr', fact='Yes', **volumes):
    return {'list': 'bmt', 'id': record_id, 'ccn': site, 'ptype': kind, 'how': how, 'score': 1.0, 'fact': fact,
            'vol': {**YEARS, **{year[1:]: value for year, value in volumes.items()}}}


def center(ccn, state, evidence, discharges=''):
    return {'ccn': ccn, 'hospital_name': f'LEGAL {ccn}', 'dba_name': f'HOSPITAL {ccn}', 'city': 'TOWN', 'state': state,
            'evidence': evidence, 'core_hcps_affiliated': '2', 'drg018_discharges_2024': discharges}


def sparcs(facility, payer, n):
    return [{'facility_name': facility, 'payment_typology_1': payer}] * n


def synthetic_rows(without_manufacturer_records=False):
    centers = {'220110': center('220110', 'MA', 'drg018_2024', '55'), '220162': center('220162', 'MA', 'fact_iec_accredited'),
               '330024': center('330024', 'NY', 'fact_iec_accredited'), '053302': center('053302', 'CA', 'fact_iec_accredited')}
    enrollment = {ccn: {'DOING BUSINESS AS NAME': '', 'ORGANIZATION NAME': f'ENROLLED {ccn}', 'CITY': 'TOWN', 'STATE': row['state']}
                  for ccn, row in centers.items()}
    enrollment['050999'] = {'DOING BUSINESS AS NAME': 'FMOL| LAKE', 'ORGANIZATION NAME': 'X', 'CITY': 'FRESNO', 'STATE': 'CA'}
    practice, canadian = 'P:Lake Oncology & Blood|St. Paul|MN', 'X:As Written By The Site||Canada'
    records = [bmt('1', '220162', y2024='120', y2025='200'), bmt('2', '330024', y2024='0'),
               bmt('3', '330024', kind='Pediatric Program', fact=''), bmt('4', practice, how='manual', y2024='8'),
               bmt('23202', canadian, how='foreign', y2024='30'),
               {'list': 'kite', 'id': 'k1', 'ccn': '050999', 'how': 'exact_addr', 'score': 0.0, 'ptype': ''},
               {'list': 'bms', 'id': 'b1', 'ccn': '050999', 'how': 'fuzzy', 'score': 0.9, 'ptype': '', 'active': 'No'},
               {'list': 'kite', 'id': 'k2', 'ccn': None, 'how': 'collection_site', 'score': 0}]
    drg = {year: {} for year in cp.MEDICARE_YEARS}
    drg['2024'], drg['2023'] = {'220110': 55}, {'220110': 64}
    ny = {'2023': cp.sparcs_counts(sparcs('Mount Sinai Hospital', 'Medicare', 3)),
          '2024': cp.sparcs_counts(sparcs('Mount Sinai Hospital', 'Medicare', 2) + sparcs('Mount Sinai Hospital', 'Self-Pay', 1)
                                   + sparcs('Mount Sinai Hospital', 'Blue Cross/Blue Shield', 4))}
    ca, _ = cp.hcai_counts(['== 2025', "106190170 CHILDREN'S | discharges 7 | mean charge 1"])
    price = cp.price_counts([{'hospital': 'A (CCN 220110)', 'codes': 'MS-DRG:018', 'payer': 'MEDICARE [2001]', 'plan': '', 'count': '44'},
                             {'hospital': 'A (CCN 220110)', 'codes': 'MS-DRG:018', 'payer': 'Aetna', 'plan': 'HMO', 'count': '1 through 10'}], {})
    research = {'220110': {'cart_product_research_usd': '10.5', 'cart_product_or_text_research_usd': '20'}}
    notes = {'220110': ('note that cites a manufacturer list', '')}
    arguments = (drg, {'220110', '330024', '050999'}, ny, ca, price, research)
    if without_manufacturer_records:
        return cp.build_rows(centers, enrollment, [r for r in records if r['list'] == 'bmt'], *arguments, {}), centers
    return cp.build_rows(centers, enrollment, records, *arguments, notes), centers


def tracked_rows():
    rows, centers = synthetic_rows()
    return cp.split_rows(rows)[0], centers


def write_tree(folder, rows, centers, fields=None):
    write_csv(folder / '2026-11-19' / f'{cp.TABLE}.csv', [{n: row[n] for n in fields or cp.PUBLIC} for row in rows], fields or cp.PUBLIC)
    write_csv(folder / '2026-10-24' / 'cart_centers.csv', list(centers.values()))
    return {name.removeprefix('center_panel_'): n for name, n in cp.check(folder).items()}


def test_site_key_is_the_ccn_or_a_slug_and_ccn_is_blank_without_a_hospital():
    hospital = cp.describe_site('190064', None, {'DOING BUSINESS AS NAME': 'FMOL HEALTH| OUR LADY', 'ORGANIZATION NAME': 'X',
                                                 'CITY': 'BATON ROUGE', 'STATE': 'LA'}, [])
    assert (hospital['site_key'], hospital['ccn'], hospital['site_name']) == ('190064', '190064', 'FMOL HEALTH - OUR LADY')
    assert cp.clean_name('RAINBOW BABIES & CHILDREN¿S') == "RAINBOW BABIES & CHILDREN'S"
    practice = cp.describe_site("P:Dr. Example's Cancer Care / Cell Lab|Twin-Falls|ID", None, None, [])
    assert practice['site_key'] == 'practice-dr-examples-cancer-care-cell-lab-twin-falls-id'
    assert (practice['ccn'], practice['site_type'], practice['state'], practice['country']) == ('', 'physician_practice', 'ID', 'USA')
    canadian = cp.describe_site('X:As Written By The Site||Canada', None, None, [bmt('23202', 'x')])
    assert canadian == {'site_key': 'non-us-princess-margaret-cancer-centre-toronto-can', 'ccn': '', 'country': 'CAN', 'state': '',
                        'site_name': 'Princess Margaret Cancer Centre', 'city': 'Toronto', 'site_type': 'non_us_center'}
    assert cp.describe_site('F:VA Medical Center|Nashville|TN', None, None, [])['site_key'].startswith('federal-')


def test_blank_and_zero_stay_apart_in_counts_and_bands():
    assert [cp.count(v) for v in ('-', '-0', '', None, '1 through 10', '0', '1,234')] == [None] * 5 + [0, 1234]
    assert [cp.band(v) for v in ('', 0, 1, 10, 11, 25, 26, 100, 101, 200, 201)] == [
        '', '0', '1-10', '1-10', '11-25', '11-25', '26-50', '51-100', '101-200', '101-200', '201+']
    reported_zero = cp.bmtinfonet_fields([bmt('2', 's', y2024='0'), bmt('3', 's', kind='Pediatric Program')])
    assert (reported_zero['bmtinfonet_n_2024'], reported_zero['bmtinfonet_n_2024_band'], reported_zero['bmtinfonet_n_2023']) == (0, '0', '')
    assert (reported_zero['bmtinfonet_program_count'], reported_zero['bmtinfonet_programs_reporting_2024']) == (2, 1)
    absent = cp.bmtinfonet_fields([])
    assert (absent['on_bmtinfonet_directory'], absent['bmtinfonet_n_2024_band'], absent['bmtinfonet_directory_ids']) == (0, '', '')


def test_directory_sums_split_pediatric_programs_and_fact_flag():
    out = cp.bmtinfonet_fields([bmt('9', 's', y2024='60'), bmt('8', 's', kind='Pediatric Program', fact='No', y2024='5'),
                                bmt('7', 's', kind='', fact='', how='fuzzy', y2024='-0')])
    assert (out['bmtinfonet_n_2024'], out['bmtinfonet_n_2024_nonpediatric_programs']) == (65, 60)
    assert (out['bmtinfonet_n_2024_band'], out['bmtinfonet_n_2024_nonpediatric_band']) == ('51-100', '51-100')
    assert out['bmtinfonet_directory_ids'] == '7;8;9' and out['bmtinfonet_fact_approved'] == 1
    assert out['bmtinfonet_program_types'] == 'Adult Program;Pediatric Program;unspecified'
    assert out['bmtinfonet_match_methods'] == 'exact_addr;fuzzy' and out['bmtinfonet_programs_reporting_2024'] == 2


def test_manufacturer_and_exact_directory_columns_never_reach_the_tracked_table():
    restricted = {c['column'] for c in cp.COLUMNS if c['restricted']}
    assert {c['column'] for c in cp.COLUMNS if c['source'] in ('kite', 'bms')} <= restricted
    assert {'n_lists', 'population', 'match_methods', 'has_any_all_payer_value', 'bmtinfonet_program_types',
            'linked_site_note', 'set_registration_note', 'search_site_id'} <= restricted
    assert {f'bmtinfonet_n_{year}' for year in cp.BMT_YEARS} | {'bmtinfonet_n_2024_nonpediatric_programs'} <= restricted
    assert not [name for name in cp.PUBLIC if 'kite' in name or 'bms' in name or '340b' in name or name in cp.FORBIDDEN]
    assert {'site_key', 'ccn', 'medicare_file_status_2024', 'has_public_all_payer_count', 'related_center_ccn',
            'bmtinfonet_n_2024_band'} <= set(cp.PUBLIC)
    fields = cp.list_fields('123300', [{'list': 'bms', 'id': 'b', 'how': 'exact_addr', 'score': 0.0, 'active': 'Yes'}])
    assert (fields['on_bms_center_list'], fields['on_kite_atc_list'], fields['n_lists'], fields['population']) == (1, 0, 1, 'adult+pediatric')
    assert fields['list_records_matched_by_address_only'] == 1


def test_medicare_status_separates_suppression_from_absence():
    in_file = {'010033', '040016'}
    assert cp.medicare_file_status('010033', 45, in_file) == 'count_published'
    assert cp.medicare_file_status('040016', '', in_file) == 'in_file_no_drg018_row'
    assert cp.medicare_file_status('453302', '', in_file) == 'not_in_file'
    assert cp.medicare_file_status('', '', in_file) == 'no_ccn'
    assert cp.childrens_ccn('453302') == 1 and cp.childrens_ccn('450076') == 0 and cp.childrens_ccn('') == 0


def test_state_files_count_records_and_say_what_a_blank_means():
    ny = {'2023': cp.sparcs_counts([]), '2024': cp.sparcs_counts(
        sparcs('NYU Langone Hospitals', 'Medicare', 2) + sparcs('NYU LANGONE HOSPITALS', 'Federal/State/Local/VA', 1)
        + sparcs('NYU LANGONE HOSPITALS', 'Private Health Insurance', 3))}
    listed = cp.new_york_fields({'ccn': '330214', 'state': 'NY'}, ny)
    assert [listed[f'ny_sparcs_aprdrg011_2024_{g}'] for g in cp.NY_GROUPS] == [2, 0, 3, 1]
    assert (listed['ny_sparcs_aprdrg011_all_payer_2024'], listed['ny_sparcs_aprdrg011_all_payer_2023'], listed['ny_sparcs_status']) == (6, '', 'discharges_in_file')
    silent = cp.new_york_fields({'ccn': '330013', 'state': 'NY'}, ny)
    assert (silent['ny_sparcs_aprdrg011_all_payer_2024'], silent['ny_sparcs_aprdrg011_2024_medicaid'], silent['ny_sparcs_status']) == ('', '', 'in_state_none_in_file')
    assert cp.new_york_fields({'ccn': '010033', 'state': 'AL'}, ny)['ny_sparcs_status'] == 'outside_new_york'
    ca, statewide = cp.hcai_counts(['== 2024', '000000 00_Statewide | discharges 788 | mean charge 1 | x',
                                    '106430905 STANFORD HEALTH CARE | discharges 214 | mean charge 2 | x', '16 hospitals listing'])
    assert statewide == {'2024': 788} and ca == {'050441': {'2024': 214}}
    assert cp.california_fields({'ccn': '050441', 'state': 'CA'}, ca) == {
        'ca_hcai_msdrg018_all_payer_2024': 214, 'ca_hcai_msdrg018_all_payer_2025': '', 'ca_hcai_status': 'drg018_in_top25_table'}
    assert cp.california_fields({'ccn': '050026', 'state': 'CA'}, ca)['ca_hcai_status'] == 'in_state_drg018_not_in_top25_table'
    assert cp.california_fields({'ccn': '', 'state': 'CA'}, ca)['ca_hcai_status'] == 'in_state_no_ccn'


def test_masked_price_rows_are_counted_as_rows_and_never_summed():
    assert cp.payer_class('MEDICARE [2001]', '') == 'medicare_ffs' and cp.payer_class('Medicare Alternate', '') == 'medicare_other'
    assert cp.payer_class('Kaiser', 'Medicare Senior Advantage') == 'ma' and cp.payer_class('MassHealth', 'MCO') == 'medicaid'
    assert cp.payer_class('Optum', 'Transplant') == 'commercial'
    rows = [{'hospital': 'S (CCN 050441)', 'codes': 'MS-DRG:018', 'payer': 'Kaiser', 'plan': 'Medicare', 'count': '24'},
            {'hospital': 'S (CCN 050441)', 'codes': 'MS-DRG:018', 'payer': 'Aetna', 'plan': 'PPO', 'count': '1 through 10'},
            {'hospital': 'S (CCN 050441)', 'codes': 'MS-DRG:018', 'payer': 'Cigna', 'plan': 'PPO', 'count': '1 through 10'},
            {'hospital': 'S (CCN 050441)', 'codes': 'APC:9413', 'payer': 'Cigna', 'plan': 'PPO', 'count': '99'}]
    mgb = {'220071': ['Car-T|018|inpatient|x|MEDICARE [2001]|plan|a|b|c|d|e|n=47|z', 'Car-T|018|outpatient|x|MEDICARE [2001]|p|a|b|c|d|e|n=9|z',
                      'Car-T|011|inpatient|x|MEDICARE ALTERNATE [9]|p|a|b|c|d|e|n=21|z', 'hits 3']}
    tally = cp.price_counts(rows, mgb)
    stanford = cp.price_fields({'ccn': '050441'}, tally)
    assert (stanford['pt_remit_ma_exact_n'], stanford['pt_remit_commercial_exact_n'], stanford['pt_remit_commercial_masked_rows']) == (24, '', 2)
    assert (stanford['pt_remit_ma_masked_rows'], stanford['pt_remit_medicaid_masked_rows'], stanford['pt_remit_status']) == (0, 0, 'file_parsed')
    assert cp.price_fields({'ccn': '220071'}, tally)['pt_remit_medicare_ffs_exact_n'] == 47 and tally['220071']['medicare_other'] == [21, 0]
    unread = cp.price_fields({'ccn': '010033'}, tally)
    assert (unread['pt_remit_status'], unread['pt_remit_ma_masked_rows'], unread['pt_remit_ma_exact_n']) == ('file_not_parsed', '', '')
    assert cp.price_fields({'ccn': ''}, tally)['pt_remit_status'] == 'no_ccn'


def test_identical_five_year_directory_counts_are_flagged_as_a_suspected_duplicate():
    same = dict(y2021='5', y2022='9', y2023='12', y2024='14', y2025='20')
    zeros = dict(y2021='0', y2022='0', y2023='0', y2024='0', y2025='1')
    records = [bmt('1', 'a', **same), bmt('2', 'b', **same), bmt('3', 'c', **zeros), bmt('4', 'd', **zeros), bmt('5', 'e', y2024='14')]
    assert cp.duplicate_count_suspects(records, {k: k.upper() for k in 'abcde'}) == {'A': 'B', 'B': 'A'}


def test_rows_assemble_with_public_flag_blind_to_lists_and_pointers_returned():
    rows, _ = synthetic_rows()
    by_key = {row['site_key']: row for row in rows}
    assert [row['site_key'] for row in rows] == sorted(by_key) and len(rows) == 7 and len(cp.split_rows(rows)[0]) == 6
    brigham, dana, sinai = by_key['220110'], by_key['220162'], by_key['330024']
    assert (brigham['medicare_ffs_drg018_2024'], brigham['medicare_ffs_drg018_2021'], brigham['medicare_file_status_2024']) == (55, '', 'count_published')
    assert (brigham['related_center_ccn'], dana['related_site_key'], brigham['related_site_relation']) == ('220162', '220110', cp.SPLIT)
    assert (brigham['has_public_all_payer_count'], brigham['open_payments_cart_product_research_usd_2019_2025']) == (1, '10.50')
    assert brigham['linked_site_note'] and 'linked_site_note' not in cp.PUBLIC
    assert (dana['has_public_all_payer_count'], dana['has_any_all_payer_value'], dana['bmtinfonet_n_2024_band']) == (0, 1, '101-200')
    assert (dana['medicare_file_status_2024'], dana['open_payments_research_status']) == ('not_in_file', 'no_published_records')
    assert (sinai['ny_sparcs_aprdrg011_all_payer_2024'], sinai['ny_sparcs_aprdrg011_2024_other'], sinai['medicare_file_status_2024']) == (7, 1, 'in_file_no_drg018_row')
    assert (sinai['bmtinfonet_n_2024_band'], sinai['bmtinfonet_program_count'], sinai['bmtinfonet_programs_reporting_2024']) == ('0', 2, 1)
    listed_only = by_key['050999']
    assert (listed_only['on_kite_atc_list'], listed_only['on_bms_center_list'], listed_only['n_lists'], listed_only['site_name']) == (1, 0, 1, 'FMOL - LAKE')
    assert (listed_only['in_cart_center_set'], listed_only['set_evidence'], listed_only['core_hcps_affiliated']) == (0, '', '')
    practice = by_key['practice-lake-oncology-blood-st-paul-mn']
    assert (practice['ccn'], practice['medicare_file_status_2024'], practice['search_site_id']) == ('', 'no_ccn', 'P:Lake Oncology & Blood|St. Paul|MN')
    assert by_key['053302']['ca_hcai_msdrg018_all_payer_2025'] == 7 and by_key['053302']['childrens_hospital_ccn_range'] == 1


def test_rows_only_a_manufacturer_list_supports_stay_out_of_the_tracked_table(tmp_path):
    rows, centers = synthetic_rows()
    tracked, held = cp.split_rows(rows)
    assert [row['site_key'] for row in held] == ['050999'] and len(tracked) == 6
    assert cp.public_basis(held[0]) == [] and held[0]['open_payments_research_status'] == 'no_published_records'
    by_key = {row['site_key']: row for row in tracked}
    assert cp.public_basis(by_key['220110']) == ['center_set'] and cp.public_basis(by_key[P]) == ['bmtinfonet_directory']
    assert cp.public_basis(by_key['053302']) == ['center_set', 'state_discharge_file']
    assert cp.public_basis({'in_cart_center_set': 0, 'on_bmtinfonet_directory': '0', **dict.fromkeys(cp.STATE_COUNT_COLUMNS, ''),
                            'ca_hcai_msdrg018_all_payer_2025': 4}) == ['state_discharge_file']
    assert write_tree(tmp_path, rows, centers)['rows_without_public_basis'] == 1
    assert cp.HELD_ROWS_FILE.endswith('__restricted.csv')


def test_tracked_table_is_the_same_when_no_manufacturer_record_is_read():
    tracked, _ = tracked_rows()
    blind, _ = synthetic_rows(without_manufacturer_records=True)
    assert [{n: r[n] for n in cp.PUBLIC} for r in blind] == [{n: r[n] for n in cp.PUBLIC} for r in tracked]
    renamed = cp.describe_site('P:Name Typed By The Search|Anytown|TN', None, None, [bmt('30658', 'x')])
    assert (renamed['site_name'], renamed['city'], renamed['site_key']) == ('West Cancer Center', 'Germantown', 'practice-west-cancer-center-germantown-tn')


def test_fingerprints_notice_values_moved_between_rows():
    tracked, _ = tracked_rows()
    before = cp.fingerprints(tracked)
    assert set(before) == {'site', 'related', 'bmtinfonet', 'medicare', 'ny_sparcs', 'ca_hcai', 'pt_remit', 'open_payments'}
    by_key = {row['site_key']: row for row in tracked}
    by_key[B]['medicare_ffs_drg018_2023'], by_key[D]['medicare_ffs_drg018_2023'] = '', 64
    after = cp.fingerprints(tracked)
    assert [group for group in before if before[group] != after[group]] == ['medicare']
    by_key[S]['site_name'], by_key[C]['site_name'] = by_key[C]['site_name'], by_key[S]['site_name']
    assert cp.fingerprints(tracked)['site'] != after['site']


def test_open_payments_agree_with_the_teaching_hospital_table(tmp_path):
    ours = {'open_payments_research_status': 'published_records', 'open_payments_cart_product_research_usd_2019_2025': '10.50',
            'open_payments_cart_product_or_text_research_usd_2019_2025': '20.00'}
    theirs = {'research_data_status': 'published_records', 'cart_product_research_usd_2019_2025': '10.5',
              'cart_product_or_text_research_usd_2019_2025': '20.00'}
    assert cp.payments_differ(ours, theirs) == 0
    assert cp.payments_differ(ours, {**theirs, 'cart_product_research_usd_2019_2025': '10.51'}) == 1
    assert cp.payments_differ(ours, {**theirs, 'research_data_status': 'no_published_records'}) == 1
    blank = {'open_payments_research_status': 'no_published_records', 'open_payments_cart_product_research_usd_2019_2025': '',
             'open_payments_cart_product_or_text_research_usd_2019_2025': ''}
    zero_filled = {'research_data_status': 'no_published_records', 'cart_product_research_usd_2019_2025': '0.00',
                   'cart_product_or_text_research_usd_2019_2025': '0.00'}
    assert cp.payments_differ(blank, zero_filled) == 0 and cp.payments_differ(ours, {'ccn': '220110'}) == 0
    rows, centers = tracked_rows()
    write_csv(tmp_path / '2026-11-19' / 'cart_center_teaching_hospital_payments_2019_2025.csv',
              [{'ccn': B, **theirs, 'cart_product_or_text_research_usd_2019_2025': '19.99'}, {'ccn': D, **zero_filled}])
    assert write_tree(tmp_path, rows, centers)['open_payments_vs_teaching_hospital_table'] == 1


def test_check_passes_structure_on_a_clean_table_and_reports_totals(tmp_path):
    rows, centers = tracked_rows()
    result = write_tree(tmp_path, rows, centers)
    totals = {'row_count', 'site_type_counts', 'medicare_totals', 'medicare_file_status_counts', 'new_york_totals',
              'california_totals', 'price_file_totals', 'open_payments_totals', 'bmtinfonet_totals', 'public_all_payer_flag_totals',
              'column_fingerprint_mismatch', 'site_without_ccn_not_named_by_project'}  # pinned to the real snapshot
    assert {name for name, n in result.items() if n} == totals
    assert all(name.startswith('center_panel_') for name in cp.check(tmp_path))


K, C, B, D, S, P = '050999', '053302', '220110', '220162', '330024', 'practice-lake-oncology-blood-st-paul-mn'


def add_column(by, name, value):
    for row in by.values():
        row[name] = value
    return cp.PUBLIC + [name]


@pytest.mark.parametrize('change, failing', [
    (lambda by: add_column(by, 'on_kite_atc_list', 1), 'restricted_or_unknown_columns_present'),
    (lambda by: add_column(by, 'opais_340b_status', 'active'), 'restricted_or_unknown_columns_present'),
    (lambda by: add_column(by, 'bmtinfonet_n_2024', 12), 'restricted_or_unknown_columns_present'),
    (lambda by: [n for n in cp.PUBLIC if n != 'medicare_file_status_2024'], 'missing_columns'),
    (lambda by: by[P].update(ccn='no CCN'), 'identifier_errors'),
    (lambda by: by[P].update(site_key='P:Lake Oncology|St. Paul|MN'), 'text_errors'),
    (lambda by: by[B].update(site_key=D), 'key_errors'),
    (lambda by: by[D].update(medicare_ffs_drg018_2024=0), 'medicare_cell_errors'),
    (lambda by: by[D].update(medicare_file_status_2024='count_published'), 'medicare_file_status_errors'),
    (lambda by: by[B].update(medicare_ffs_drg018_2024=''), 'medicare_2024_vs_center_table'),
    (lambda by: by[B].update(core_hcps_affiliated='9'), 'center_fields_mismatch'),
    (lambda by: by[P].update(in_cart_center_set=1), 'center_set_mismatch'),
    (lambda by: by[S].update(ny_sparcs_aprdrg011_2024_other=''), 'new_york_errors'),
    (lambda by: by[S].update(ny_sparcs_aprdrg011_2024_medicare=5), 'new_york_errors'),
    (lambda by: by[C].update(ca_hcai_status='outside_california'), 'california_errors'),
    (lambda by: by[C].update(ca_hcai_msdrg018_all_payer_2024=0), 'california_errors'),
    (lambda by: by[B].update(pt_remit_ma_masked_rows=''), 'price_file_errors'),
    (lambda by: by[B].update(open_payments_cart_product_or_text_research_usd_2019_2025='1.00'), 'open_payments_errors'),
    (lambda by: by[S].update(bmtinfonet_n_2024_band=''), 'bmtinfonet_errors'),
    (lambda by: by[D].update(bmtinfonet_n_2024_band='150'), 'bmtinfonet_errors'),
    (lambda by: by[D].update(has_public_all_payer_count=1), 'public_all_payer_flag_errors'),
    (lambda by: by[D].update(related_site_key='', related_center_ccn='', related_site_relation='', related_site_note=''), 'related_site_errors'),
    (lambda by: by[D].update(city=''), 'blank_required_values'),
    (lambda by: by[P].update(on_bmtinfonet_directory=0, bmtinfonet_program_count=0, bmtinfonet_directory_ids='', bmtinfonet_match_methods='',
                             bmtinfonet_fact_approved=0, bmtinfonet_programs_reporting_2024=0, bmtinfonet_n_2024_band='',
                             bmtinfonet_n_2024_nonpediatric_band=''), 'rows_without_public_basis'),
    (lambda by: by[D].update(pt_remit_status='parsed'), 'price_file_errors'),
    (lambda by: by[D].update(open_payments_research_status='none'), 'open_payments_errors'),
    (lambda by: by[B].update(open_payments_cart_product_research_usd_2019_2025='10.5'), 'open_payments_errors'),
    (lambda by: by[S].update(state='NJ'), 'new_york_errors'),
])
def test_check_catches_a_broken_table(tmp_path, change, failing):
    rows, centers = tracked_rows()
    fields = change({row['site_key']: row for row in rows})
    assert write_tree(tmp_path, rows, centers, fields)[failing] > 0


@pytest.mark.skipif(not (REAL / '2026-11-19' / f'{cp.TABLE}.csv').exists(), reason='published table not present')
def test_published_table_passes_every_check():
    assert {name: n for name, n in cp.check(REAL).items() if n} == {}
