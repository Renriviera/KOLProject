"""Code classes, cent-exact sums, blank-versus-zero rules and checks for the Medicaid payment tables."""
import copy

import pytest

from kolproject.common import ROOT, read_csv, write_csv
from kolproject.volume_billing import medicaid_payments as m

A, B, C, D, E = '1111111111', '2222222222', '3333333333', '4444444444', '5555555555'
GROUP, CENTER = '9000000001', '9000000002'
PEOPLE = {npi: {'npi': npi, 'division': division, 'label_version': '1.0.0', 'state': 'NY', 'hospital_ccns': ccns}
          for npi, division, ccns in ((A, 'core', '330001;330002'), (B, 'affiliate_A', ''), (C, 'referring_proxy', '330009'),
                                      (D, 'hemonc_other', ''), (E, 'affiliate_B', ''))}
CENTERS = [{'ccn': '330001', 'organization_npi': CENTER}, {'ccn': '330002', 'organization_npi': '9000000003'}]
SEGMENTS = {A: [{'state': 'NY', 'end': '2021-06-30', 'status': 'ACTIVE - ACTIVE', 'plan': 'MEDICAID'},
                {'state': 'NJ', 'end': '', 'status': 'ACTIVE - ACTIVE', 'plan': 'MEDICAID'}]}
# (billing NPI, servicing NPI, code, month, beneficiaries, claim lines, paid cents)
ROWS = [
    (GROUP, A, '99214', '2023-01', 20, 30, 150050), (GROUP, A, '99214', '2024-02', 15, 15, 100050),
    (CENTER, A, 'J9312', '2024-02', 12, 12, 500000), (CENTER, A, 'G0463', '2022-05', 14, 14, 0),
    (B, B, '96413', '2024-03', 12, 13, 25000), (B, '', '85025', '2024-03', 12, 12, 1000),
    ('', C, '99213', '2024-06', 12, 12, 0),
    ('8000000000', '8000000001', '99213', '2024-06', 50, 50, 999999),
]


def built():
    return m.tables(ROWS, PEOPLE, CENTERS, SEGMENTS)


def tree(path, tables):
    """A tiny Data/processed directory holding the four tables and the two universe tables."""
    for table, rows in tables.items():
        write_csv(path / m.SNAPSHOT / f'{table}.csv', rows, [c['column'] for c in m.COLUMNS[table]])
    write_csv(path / m.UNIVERSE_SNAPSHOT / 'hcp_divisions.csv', PEOPLE.values())
    write_csv(path / m.UNIVERSE_SNAPSHOT / 'cart_centers.csv', CENTERS)
    return path


def structural(result):
    """Checks that do not depend on the 2026-11-19 build's own totals and table digests."""
    return {key: value for key, value in result.items() if not key.endswith(('headline_totals', 'table_fingerprints'))}


def test_code_classes_follow_the_search_rule():
    assert [m.code_subclass(c) for c in ('99202', '99233', '96379', '96401', 'J9312', 'J3490', 'J1447', '38241')] == [
        'em_office', 'em_hospital', 'admin', 'admin', 'drug_listed', 'drug_unclassified', 'drug_other_jq', 'transplant_38241']
    assert {m.code_class(c) for c in ('99215', '99221', '96360', '96417')} == {'visit_admin'}
    assert {m.code_class(c) for c in ('Q5119', 'Q2041', 'C9399', 'S0028', 'Q0138', 'Q0082', 'J0897')} == {'drug_code'}
    # Left out on purpose by the search: old or visit-equivalent codes, other administration codes, Q008x other than Q0082.
    assert {m.code_class(c) for c in ('99201', 'G0463', 'T1015', 'Q0084', 'Q0081', '96380', '96420', '96400', '9636',
                                      '99291', 'J930', 'J93120', 'X4003', '85025', '38241')} == {'other'}


def test_amounts_are_summed_in_cents_whatever_the_row_order():
    halves = [('', A, '99213', f'2024-{k:02d}', 12, 12, cents) for k, cents in enumerate((10, 20, 1005, 15), 1)]
    for rows in (halves, halves[::-1]):
        sides, _ = m.tally_rows(rows, PEOPLE)
        assert m.paid(sides['servicing'][A, '2024']) == '10.50'
    assert m.usd(-39336) == '-393.36' and m.usd(None) == '' and m.to_cents('') is None and m.to_cents('0.00') == 0
    assert m.whole_usd(6898581493) == 68985815 and m.whole_usd(50) == 1
    assert m.median_usd([100, 201]) == '1.51' and m.median_usd([300]) == '3.00' and m.median_usd([]) == ''


def test_blank_means_no_released_row_and_zero_means_rows_paid_nothing():
    tables = built()
    years = {(r['npi'], r['year']): r for r in tables[m.TABLES[0]]}
    assert set(years) == {(A, '2022'), (A, '2023'), (A, '2024'), (B, '2024'), (C, '2024')}
    zero = years[C, '2024']
    assert zero['medicaid_servicing_paid_usd'] == '0.00' and zero['medicaid_servicing_visit_admin_paid_usd'] == '0.00'
    assert zero['medicaid_servicing_drug_code_paid_usd'] == '' and zero['medicaid_servicing_drug_code_claim_lines'] == ''
    assert zero['medicaid_servicing_distinct_billing_npis'] == 0 and zero['medicaid_billing_paid_usd'] == ''
    assert zero['medicaid_row_sides'] == 'servicing' and zero['medicaid_self_billed_paid_usd'] == ''
    clinic = years[A, '2022']
    assert clinic['medicaid_servicing_paid_usd'] == '0.00' and clinic['medicaid_servicing_visit_admin_paid_usd'] == ''
    features = {r['npi']: r for r in tables[m.TABLES[1]]}
    assert len(features) == 5 and features[D]['medicaid_data_status'] == 'not_observed'
    assert features[D]['medicaid_observed_sides'] == 'neither'
    assert all(features[D][c['column']] == '' for c in m.FEATURE_COLUMNS if 'usd' in c['column'] or 'present' in c['column'])
    assert features[D]['medicaid_servicing_distinct_billing_npis'] == '' and features[D]['in_medicaid_enrollment_file'] == 0


def test_year_and_feature_rows_keep_sides_apart_and_show_their_overlap():
    tables = built()
    year = next(r for r in tables[m.TABLES[0]] if r['npi'] == B)
    assert year['medicaid_row_sides'] == 'servicing_and_billing'
    assert (year['medicaid_servicing_paid_usd'], year['medicaid_billing_paid_usd'], year['medicaid_self_billed_paid_usd']) == ('250.00', '260.00', '250.00')
    assert (year['medicaid_servicing_source_rows'], year['medicaid_billing_source_rows'], year['medicaid_self_billed_source_rows']) == (1, 2, 1)
    assert year['medicaid_billing_distinct_servicing_npis'] == 1 and year['medicaid_servicing_distinct_billing_npis'] == 1
    core = next(r for r in tables[m.TABLES[1]] if r['npi'] == A)
    assert core['medicaid_data_status'] == 'observed' and core['medicaid_observed_sides'] == 'servicing'
    assert core['medicaid_servicing_paid_usd_2024'] == '6000.50' and core['medicaid_servicing_paid_usd_2018_2024'] == '7501.00'
    assert core['medicaid_servicing_visit_admin_paid_usd_2018_2024'] == '2501.00'
    assert core['medicaid_servicing_drug_code_paid_usd_2024'] == '5000.00'
    assert core['medicaid_servicing_years_present'] == 3 and core['medicaid_servicing_distinct_billing_npis'] == 2
    assert core['medicaid_billing_paid_usd_2018_2024'] == '' and core['medicaid_billing_years_present'] == ''
    row = next(r for r in tables[m.TABLES[0]] if (r['npi'], r['year']) == (A, '2024'))
    assert (row['medicaid_servicing_months_present'], row['medicaid_servicing_claim_lines'],
            row['medicaid_servicing_patient_counts_summed'], row['medicaid_servicing_max_patients_in_one_row']) == (1, 27, 27, 15)
    assert not any(serv == '8000000001' for serv in {r['npi'] for r in tables[m.TABLES[0]]})


def test_enrollment_flags_judge_each_segment_on_its_own():
    closed = {'state': 'NY', 'end': '2021-06-30', 'status': 'ACTIVE - ACTIVE', 'plan': 'MEDICAID'}
    open_ended = {'state': 'CA', 'end': '', 'status': 'ACTIVE - ACTIVE DO NOT PAY', 'plan': 'NOT STATE PLAN AFFILIATED'}
    # One closed and one open-ended segment: the search's collapsed rule looked only at the closed end date.
    assert m.enrollment_flags([closed, open_ended]) == {
        'in_medicaid_enrollment_file': 1, 'medicaid_enrollment_states': 'CA;NY', 'medicaid_enrollment_medicaid_or_chip_plan': 1,
        'medicaid_enrollment_active_2024_any_state': 1, 'medicaid_enrollment_active_2024_medicaid_or_chip_plan': 0}
    assert m.enrollment_flags([closed])['medicaid_enrollment_active_2024_any_state'] == 0
    assert m.enrollment_flags([{**closed, 'end': '2024-01-01', 'plan': 'CHIP'}])['medicaid_enrollment_active_2024_medicaid_or_chip_plan'] == 1
    assert m.enrollment_flags([{**closed, 'end': '7777-12-13'}])['medicaid_enrollment_active_2024_any_state'] == 1
    assert m.enrollment_flags([{**closed, 'end': '', 'status': 'TERM - OTHER'}])['medicaid_enrollment_active_2024_any_state'] == 0
    assert m.enrollment_flags([]) == {
        'in_medicaid_enrollment_file': 0, 'medicaid_enrollment_states': '', 'medicaid_enrollment_medicaid_or_chip_plan': 0,
        'medicaid_enrollment_active_2024_any_state': 0, 'medicaid_enrollment_active_2024_medicaid_or_chip_plan': 0}


def test_division_summary_counts_rows_and_paid_apart_and_keeps_unobserved_dollars_blank():
    summary = {(r['join_side'], r['division_level'], r['division'], r['year']): r for r in built()[m.TABLES[2]]}
    assert len(summary) == 2 * (5 + 4) * 7
    referring = summary['servicing', 'division_group', 'referring', '2024']
    assert (referring['npis_in_division'], referring['npis_present'], referring['npis_with_visit_admin_rows'],
            referring['npis_with_visit_admin_paid']) == (1, 1, 1, 0)
    assert referring['paid_visit_admin_usd'] == '0.00' and referring['paid_drug_code_usd'] == '' and referring['claim_lines_drug_code'] == 0
    assert referring['median_visit_admin_usd_per_npi_with_any'] == '' and referring['share_rows_other_npi_blank'] == '1.000'
    assert referring['share_paid_servicing_equals_billing'] == ''
    affiliates = summary['servicing', 'division_group', 'affiliates', '2024']
    assert (affiliates['npis_in_division'], affiliates['npis_present'], affiliates['paid_total_usd']) == (2, 1, '250.00')
    assert affiliates['share_rows_servicing_equals_billing'] == '1.000'
    billing = summary['billing', 'division', 'affiliate_A', '2024']
    assert (billing['paid_total_usd'], billing['paid_other_usd'], billing['share_rows_other_npi_blank']) == ('260.00', '10.00', '0.500')
    core = summary['servicing', 'division', 'core', '2024']
    assert (core['paid_listed_oncology_drug_usd'], core['paid_unclassified_drug_usd'], core['label_version']) == ('5000.00', '', '1.0.0')
    empty = summary['servicing', 'division', 'hemonc_other', '2024']
    assert (empty['npis_present'], empty['paid_total_usd'], empty['median_paid_total_usd_per_present_npi'], empty['claim_lines']) == (0, '', '', 0)


def test_center_links_carry_the_ccn_and_flag_centers_outside_the_hcp_hospital_list():
    links = built()[m.TABLES[3]]
    assert links == [{'npi': A, 'ccn': '330001', 'center_organization_npi': CENTER, 'first_year': '2022', 'last_year': '2024',
                      'years_present': 2, 'source_rows': 2, 'claim_lines': 26, 'paid_usd_2018_2024': '5000.00',
                      'visit_admin_paid_usd_2018_2024': '', 'drug_code_paid_usd_2018_2024': '5000.00',
                      'center_in_hcp_hospital_ccns': 1}]
    other = m.tables(ROWS, {**PEOPLE, A: {**PEOPLE[A], 'hospital_ccns': '330002'}}, CENTERS, {})[m.TABLES[3]]
    assert other[0]['center_in_hcp_hospital_ccns'] == 0


def test_no_column_is_named_as_a_service_or_product_column():
    names = [c['column'] for table in m.TABLES for c in m.COLUMNS[table]]
    assert not [n for n in names if '_service_' in n or 'product' in n or n.startswith('mcaid')]
    assert all(not c['restricted'] and c['source'] in ('hhs', 'project') for table in m.TABLES for c in m.COLUMNS[table])
    assert {'visit_admin', 'drug_code'} <= set(m.CLASS_RULES) and "then 'service'" in m.CODE_CLASS_RULE_SQL


def test_check_passes_a_consistent_tree_and_reports_the_wrong_universe(tmp_path):
    result = m.check(tree(tmp_path, built()))
    assert all(key.startswith('medicaid_payments.') for key in result)
    assert not any(structural(result).values()), structural(result)
    assert result['medicaid_payments.headline_totals'] > 0  # five synthetic NPIs are not the 17,510-NPI universe
    assert result['medicaid_payments.table_fingerprints'] == 4


def test_every_headline_number_is_asserted_and_recomputed_from_published_rows():
    tables = built()
    found = m.headline(*(tables[table] for table in m.TABLES), PEOPLE)
    assert set(found) == set(m.EXPECTED)
    assert (found['servicing_npis_by_year']['2024'], found['billing_npis_by_year']['2024'], found['billing_only_npis']) == (3, 1, 0)
    assert (found['servicing_paid_usd_2018_2024'], found['self_billed_paid_usd_2018_2024']) == ('7751.00', '250.00')
    assert (found['core_2024_npis_with_drug_code_rows'], found['referring_2024_npis_with_visit_admin_rows'],
            found['referring_2024_npis_with_visit_admin_paid'], found['servicing_npis_2024_paid_exactly_zero']) == (1, 1, 0, 1)
    assert (found['center_link_pairs_with_gap_years'], found['center_link_claim_lines'], found['center_link_paid_usd_2018_2024']) == (1, 26, '5000.00')
    assert found['core_npis_in_enrollment_file'] == 1 and found['npis_observed_not_in_enrollment_file'] == 2


def test_table_digest_ignores_row_order_and_sees_any_moved_or_changed_cell():
    rows = [{'npi': A, 'paid': '1.00', 'flag': ''}, {'npi': B, 'paid': '2.00', 'flag': '0'}]
    digest = m.fingerprint(rows, ['npi', 'paid', 'flag'])
    assert m.fingerprint(rows[::-1], ['npi', 'paid', 'flag']) == digest
    swapped = [{**rows[0], 'paid': '2.00'}, {**rows[1], 'paid': '1.00'}]
    blank_to_zero = [{**rows[0], 'flag': '0'}, rows[1]]
    crossed = [{'npi': A, 'paid': '', 'flag': '1.00'}, rows[1]]
    assert len({digest, *(m.fingerprint(other, ['npi', 'paid', 'flag']) for other in (swapped, blank_to_zero, crossed, rows[:1], rows + rows[:1]))}) == 6


@pytest.mark.parametrize('table, change, failing', [
    (0, lambda rows: rows[2].update(medicaid_servicing_months_present=''), 'by_year_sides'),
    (0, lambda rows: rows[2].update(medicaid_servicing_paid_usd='n/a'), 'cell_formats'),
    (0, lambda rows: rows[2].update(medicaid_servicing_paid_usd='6000.5'), 'cell_formats'),
    (0, lambda rows: rows[2].update(year=''), 'cell_formats'),
    (1, lambda rows: rows[0].update(in_medicaid_enrollment_file=''), 'cell_formats'),
    (2, lambda rows: rows[0].update(npis_present='many'), 'cell_formats'),
    (3, lambda rows: rows[0].update(paid_usd_2018_2024=''), 'cell_formats'),
    (3, lambda rows: rows.clear(), 'columns'),
])
def test_check_counts_a_damaged_cell_instead_of_stopping(tmp_path, table, change, failing):
    tables = copy.deepcopy(built())
    change(tables[m.TABLES[table]])
    assert m.check(tree(tmp_path, tables))[f'medicaid_payments.{failing}'] > 0


def test_check_reports_a_missing_table_file(tmp_path):
    processed = tree(tmp_path, built())
    (processed / m.SNAPSHOT / f'{m.TABLES[2]}.csv').unlink()
    result = m.check(processed)
    assert result['medicaid_payments.columns'] == 1 and result['medicaid_payments.division_year_key'] > 0


@pytest.mark.parametrize('table, change, failing', [
    (1, lambda rows: rows[3].update(medicaid_servicing_paid_usd_2024='0.00'), 'features_blank_when_unobserved'),
    (1, lambda rows: rows.pop(), 'features_universe'),
    (1, lambda rows: rows[0].update(medicaid_enrollment_active_2024_medicaid_or_chip_plan=1, medicaid_enrollment_medicaid_or_chip_plan=0), 'features_enrollment'),
    (0, lambda rows: rows[2].update(medicaid_servicing_paid_usd='6000.51'), 'features_match_by_year'),
    (0, lambda rows: rows[2].update(medicaid_servicing_paid_usd='6000.51'), 'division_year_match_by_year'),
    (0, lambda rows: rows.append(dict(rows[0])), 'by_year_key'),
    (0, lambda rows: rows[4].update(medicaid_servicing_visit_admin_paid_usd=''), 'by_year_classes'),
    (0, lambda rows: rows[4].update(medicaid_servicing_claim_lines=11), 'by_year_release_threshold'),
    (0, lambda rows: rows[3].update(medicaid_row_sides='servicing'), 'by_year_sides'),
    (0, lambda rows: rows[0].update(npi='7777777777'), 'by_year_joins'),
    (2, lambda rows: rows.pop(), 'division_year_key'),
    (2, lambda rows: [r.update(paid_other_usd='11.00') for r in rows if r['paid_other_usd'] == '10.00'], 'division_year_class_sums'),
    (2, lambda rows: [r.update(npis_in_division=3) for r in rows if r['division'] == 'core'], 'division_year_denominators'),
    (2, lambda rows: [r.update(share_rows_other_npi_blank='') for r in rows if r['npis_present']], 'division_year_denominators'),
    (2, lambda rows: [r.update(share_rows_other_npi_blank='0.000') for r in rows if not r['npis_present']], 'division_year_denominators'),
    (2, lambda rows: [r.update(claim_lines=9999) for r in rows if (r['division_level'], r['division']) == ('division_group', 'core')], 'division_year_levels_agree'),
    (3, lambda rows: rows[0].update(center_organization_npi='9000000003'), 'center_links_joins'),
    (3, lambda rows: rows[0].update(center_in_hcp_hospital_ccns=0), 'center_links_values'),
    (3, lambda rows: rows[0].update(first_year='2021'), 'center_links_values'),
])
def test_check_catches_a_broken_table(tmp_path, table, change, failing):
    tables = copy.deepcopy(built())
    change(tables[m.TABLES[table]])
    assert m.check(tree(tmp_path, tables))[f'medicaid_payments.{failing}'] > 0


def test_a_link_cannot_span_more_years_than_the_hcp_has_servicing_rows(tmp_path):
    tables = copy.deepcopy(built())
    tables[m.TABLES[3]][0].update(years_present=3)  # 2022 to 2024, and the HCP has servicing rows in all three years
    assert m.check(tree(tmp_path / 'three', tables))['medicaid_payments.center_links_values'] == 0
    tables[m.TABLES[0]] = [r for r in tables[m.TABLES[0]] if (r['npi'], r['year']) != (A, '2023')]
    assert m.check(tree(tmp_path / 'two', tables))['medicaid_payments.center_links_values'] == 1


def test_published_tables_pass_every_check():
    processed = ROOT / 'Data' / 'processed'
    if not all((processed / m.SNAPSHOT / f'{table}.csv').exists() for table in m.TABLES):
        pytest.skip('Tables of the 2026-11-19 snapshot are not built here')
    result = m.check(processed)
    assert result and not any(result.values()), {key: value for key, value in result.items() if value}
    features = read_csv(processed / m.SNAPSHOT / f'{m.TABLES[1]}.csv')
    assert len(features) == 17510 and sum(r['medicaid_data_status'] == 'observed' for r in features) == 11602
    assert result['medicaid_payments.table_fingerprints'] == 0 and len(result) == 23
