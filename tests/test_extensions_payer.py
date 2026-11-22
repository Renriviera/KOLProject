"""Shaping, blank and status rules, de-duplication and check() of the payer extension, on invented rows only."""
import csv

from kolproject.extensions import payer as p

A, B, C = '1000000001', '1000000002', '1000000003'


def service(npi, code='96413', place='O', benes='14', units='30', average='12.345'):
    return {'Rndrng_NPI': npi, 'Rndrng_Prvdr_Type': 'Hematology-Oncology', 'HCPCS_Cd': code, 'HCPCS_Drug_Ind': 'N',
            'Place_Of_Srvc': place, 'Tot_Benes': benes, 'Tot_Srvcs': units, 'Tot_Bene_Day_Srvcs': '29',
            'Avg_Mdcr_Alowd_Amt': '15.5', 'Avg_Mdcr_Pymt_Amt': average}


def part_d(npi, generic='Ibrutinib', brand='Imbruvica', benes=''):
    return {'Prscrbr_NPI': npi, 'Prscrbr_Type': 'Medical Oncology', 'Brnd_Name': brand, 'Gnrc_Name': generic,
            'Tot_Clms': '23', 'Tot_30day_Fills': '25.5', 'Tot_Day_Suply': '750', 'Tot_Drug_Cst': '1234.56',
            'Tot_Benes': benes}


def test_marker_row_keeps_code_number_family_and_estimate():
    row = p.marker_row(service(A), 2021, '2026-11-20T20:00:00Z')
    assert row['code_family'] == 'chemo_administration' and row['hcpcs_code'] == '96413'
    assert row['estimated_medicare_payment_usd'] == '370.35' and row['beneficiaries'] == '14'
    assert row['source_dataset_version_id'] == p.SERVICE_VERSIONS[2021] and row['service_year'] == '2021'
    assert not any('desc' in k.lower() for k in row)


def test_part_d_blank_beneficiaries_get_a_status_and_never_zero():
    hidden = p.oral_row(part_d(A), 2019, 't')
    assert hidden['total_beneficiaries'] == '' and hidden['beneficiaries_status'] == 'beneficiaries_suppressed_1_to_10'
    shown = p.oral_row(part_d(B, benes='12'), 2019, 't')
    assert shown['total_beneficiaries'] == '12' and shown['beneficiaries_status'] == 'beneficiaries_published'
    assert hidden['drug_class'] == 'btk_inhibitor'


def test_spending_rows_keep_blank_years_and_missing_brands():
    record = {'Brnd_Name': 'Yescarta', 'HCPCS_Cd': 'Q2041', **{f'Tot_Spndng_{y}': '' for y in p.SPENDING_YEARS}}
    record.update({'Tot_Spndng_2023': '100.5', 'Tot_Clms_2023': '12', 'Tot_Benes_2023': '12', 'Tot_Dsg_Unts_2023': '12'})
    rows = p.spending_rows('medicare_part_b', [record], 't', 'v')
    assert len(rows) == len(p.CART_BRANDS) * len(p.SPENDING_YEARS)
    by = {(r['brand_name'], r['calendar_year']): r for r in rows}
    assert by[('Yescarta', '2023')]['value_status'] == 'published' and by[('Yescarta', '2023')]['total_beneficiaries'] == '12'
    assert by[('Yescarta', '2020')]['value_status'] == 'no_value_in_file' and by[('Yescarta', '2020')]['total_spending_usd'] == ''
    assert by[('Kymriah', '2023')]['value_status'] == 'brand_not_in_file'
    medicaid = p.spending_rows('medicaid', [{**record, 'Mftr_Name': 'Overall'}, {**record, 'Mftr_Name': 'Maker'}], 't', 'v')
    assert {r['beneficiaries_status'] for r in medicaid} == {'not_reported_by_program'}


def test_state_row_blank_status_differs_by_source():
    fl = p.state_row('100271', 2024, p.FL_SOURCE, 'medicaid', '', '110009', {'100271'}, 'r', 't', 'q')
    assert fl['cell_status'] == 'not_shown_below_display_threshold' and p.shown_count(fl) == ''
    shown = p.state_row('100271', 2024, p.FL_SOURCE, 'all_payer', '45', '110009', {'100271'}, 'r', 't', 'q')
    assert shown['fl_ahca_discharges'] == '45' and shown['ca_hcai_discharges'] == ''
    ca = p.state_row('050999', 2022, p.CA_SOURCE, 'all_payer', '', '', set(), 'r', 't', 'q')
    assert ca['cell_status'] == 'drg018_not_in_top25_tables' and ca['in_cart_center_set'] == '0'


def test_florida_shown_cells_are_read_without_subtraction():
    years = {2024: ['Profile Page,File Number,Hospital Name,Discharges', 'x,110009,HOSPITAL ONE,77', 'Total,,,90']}
    payer_lines = ['year,payer,file_number,hospital,discharges,charges', '2024,Medicare,110009,"HOSPITAL ONE",41,1.0',
                   '2024,Medicaid,,TOTAL_SHOWN,0,0']
    shown = p.fl_shown(years, payer_lines)
    assert shown == {('110009', 2024, 'all_payer'): '77', ('110009', 2024, 'medicare_ffs'): '41'}


def test_california_rows_take_one_count_per_facility():
    rows = [{'A': 'FacilityNumber', 'C': 'DRGCode', 'E': 'Discharges'}, {'A': '000000', 'C': '018', 'E': '50'},
            {'A': '106000001', 'C': '018', 'E': '20'}, {'A': '106000001', 'C': '018', 'E': '20'},
            {'A': '106000002', 'C': '871', 'E': '999'}]
    assert p.ca_drg018(rows) == {'000000': '50', '106000001': '20'}


def test_new_rows_drops_published_and_internal_duplicates():
    rows = [p.marker_row(service(A), 2022, 't'), p.marker_row(service(A), 2022, 't'), p.marker_row(service(B), 2022, 't')]
    kept, dropped = p.new_rows(rows, p.MARKER_KEY, {(B, '2022', '96413', 'O')})
    assert [r['npi'] for r in kept] == [A] and dropped == 2


def write(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(rows[0])
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def tree(tmp_path, markers):
    root = tmp_path / 'processed'
    write(root / '2026-10-24' / 'hcp_divisions.csv', [{'npi': A, 'division': 'core'}, {'npi': B, 'division': 'referring_proxy'}])
    write(root / '2026-10-24' / 'cart_centers.csv', [{'ccn': '100271'}])
    write(root / '2026-10-24' / 'treatment_markers_2024.csv', [{'npi': A, 'service_year': '2024', 'hcpcs_code': '96413', 'place_of_service': 'O'}])
    write(root / '2026-11-19' / 'medicare_partb_drug_rows_2024.csv', [{'npi': A, 'hcpcs_code': 'J9312', 'place_of_service': 'O'}])
    write(root / '2026-10-24' / 'part_d_lymphoma_orals_2024.csv', [{'npi': A, 'service_year': '2024', 'generic_name': 'Ibrutinib', 'brand_name': 'Imbruvica'}])
    write(root / '2026-11-19' / 'cart_site_volume_panel.csv', [{'ccn': '050441'}])
    write(root / '2026-11-19' / 'hcp_medicare_partb_payments_by_year.csv', [{'npi': A, 'service_year': '2019'}])
    here = root / p.SNAPSHOT
    write(here / f'{p.MARKERS}.csv', markers)
    write(here / f'{p.ORALS}.csv', [p.oral_row(part_d(A), 2020, 't')])
    write(here / f'{p.SPENDING}.csv', p.spending_rows('medicaid', [], 't', 'v') + p.spending_rows('medicare_part_b', [], 't', 'v'))
    if p.INCLUDE_PAYMENTS:
        write(here / f'{p.PAYMENTS}.csv', [p.payment_row(n, y, provider(n) if n == A else None, 't') for n in (A, B) for y in p.PAYMENT_YEARS])
    write(here / f'{p.GEO}.csv', [p.geo_row({'Rndrng_Prvdr_Geo_Lvl': 'State', 'Rndrng_Prvdr_Geo_Cd': '06',
        'Rndrng_Prvdr_Geo_Desc': 'Somestate', 'HCPCS_Cd': '0540T', 'Place_Of_Srvc': 'F', 'Tot_Rndrng_Prvdrs': '3',
        'Tot_Benes': '15', 'Tot_Srvcs': '15', 'Tot_Bene_Day_Srvcs': '15', 'Avg_Mdcr_Pymt_Amt': '0'}, 2022, 't')])
    dates = {c: ('20200101', '20211231' if c.startswith('C') else '', 2025) for c in p.CODE_FAMILIES if c not in p.ADMIN_CODES}
    national = {(y, c): 50 for y in p.YEARS for c in p.CODE_FAMILIES}
    national.update({(y, c): 0 for y in p.YEARS for c in dates if p.validity_status(*dates[c][:2], y) == 'not_valid'})
    write(here / f'{p.VALIDITY}.csv', p.validity_rows(dates, national, markers, {k: 't' for k in national}))
    write(here / f'{p.STATE}.csv', [p.state_row('100271', 2024, p.FL_SOURCE, 'all_payer', '45', '110009', {'100271'}, 'r', 't', 'q')]
          + [p.state_row('100271', 2024, p.FL_SOURCE, payer, '', '110009', {'100271'}, 'r', 't', 'q') for payer in p.FL_PAYERS.values()])
    return root


def test_check_passes_on_a_clean_tree_and_catches_broken_tables(tmp_path):
    good = [p.marker_row(service(A), 2021, 't'), p.marker_row(service(B, code='J9312'), 2023, 't')]
    result = p.check(tree(tmp_path, good))
    assert all(v == 0 for k, v in result.items() if k not in ('payer_headline_recomputed', 'payer_table_fingerprints')), result
    broken = [dict(good[0], estimated_medicare_payment_usd='1.00'), good[0], dict(good[1], beneficiaries='7'),
              dict(good[1], npi=C, service_year='2024', hcpcs_code='96413')]
    result = p.check(tree(tmp_path / 'b', broken))
    assert result['payer_markers_estimate_recomputed'] >= 1 and result['payer_markers_duplicate_keys'] >= 1
    assert result['payer_markers_beneficiaries_below_11_or_blank'] == 1
    assert result['payer_markers_npi_not_in_universe'] == 1 and result['payer_markers_year_or_version'] == 1


def test_check_catches_overlap_and_florida_threshold(tmp_path):
    root = tree(tmp_path, [dict(p.marker_row(service(A), 2021, 't'), service_year='2024')])
    state = root / p.SNAPSHOT / f'{p.STATE}.csv'
    write(state, [p.state_row('100271', 2024, p.FL_SOURCE, 'all_payer', '12', '110009', {'100271'}, 'r', 't', 'q'),
                  p.state_row('050441', 2024, p.CA_SOURCE, 'all_payer', '0', '1', set(), 'r', 't', 'q')])
    result = p.check(root)
    assert result['payer_markers_overlap_earlier_snapshots'] == 1
    assert result['payer_state_florida_shown_below_threshold'] == 1 and result['payer_state_status_consistency'] == 1
    assert result['payer_state_overlap_volume_panel'] == 1


def test_code_validity_reads_dates_by_year():
    assert p.validity_status('20210401', '', 2021) == 'valid_part_of_year'
    assert p.validity_status('20210401', '', 2022) == 'valid_all_year'
    assert p.validity_status('20211001', '20220331', 2023) == 'not_valid'
    assert p.validity_status('20240101', '', 2023) == 'not_valid'


def test_check_catches_rows_under_an_invalid_code(tmp_path):
    rows = [p.marker_row(service(A, code='J9309'), 2019, 't')]   # invented validity: J9309 valid from 2020 here
    result = p.check(tree(tmp_path, rows))
    assert result['payer_markers_code_valid_in_year'] == 1 and result['payer_validity_recomputed'] >= 1


def provider(npi, drug='400.25', non_drug='600.25', marks=('', '')):
    hidden = bool(marks[0] or marks[1])
    return {'Rndrng_NPI': npi, 'Rndrng_Prvdr_State_Abrvtn': 'ZZ', 'Rndrng_Prvdr_Type': 'Hematology-Oncology',
            'Tot_Benes': '40', 'Tot_Srvcs': '900', 'Tot_Mdcr_Pymt_Amt': '1000.5', 'Drug_Sprsn_Ind': marks[0],
            'Drug_Tot_Benes': '' if hidden else '12', 'Drug_Mdcr_Pymt_Amt': '' if hidden else drug,
            'Med_Sprsn_Ind': marks[1], 'Med_Tot_Benes': '' if hidden else '40', 'Med_Mdcr_Pymt_Amt': '' if hidden else non_drug}


def test_payment_rows_use_the_2026_11_19_status_vocabulary():
    split = p.payment_row(A, 2017, provider(A, drug='0'), 't')
    assert split['by_provider_status'] == 'published_split' and split['drug_medicare_payment_usd'] == '0.00'
    hidden = p.payment_row(B, 2018, provider(B, marks=('*', '#')), 't')
    assert hidden['by_provider_status'] == 'split_suppressed' and hidden['total_medicare_payment_usd'] == '1000.50'
    assert hidden['drug_medicare_payment_usd'] == '' and hidden['drug_suppression_indicator'] == '*'
    absent = p.payment_row(C, 2018, None, 't')
    assert absent['by_provider_status'] == 'no_row' and absent['total_medicare_payment_usd'] == ''
    assert absent['source_dataset_version_id'] == p.BY_PROVIDER_VERSIONS[2018]
    assert set(split) == {c['column'] for c in p.PAYMENT_COLUMNS}


def test_in_filter_lists_every_npi():
    text = p.in_filter([A, B])
    assert text.count('[value]') == 2 and A in text and B in text


def test_check_catches_payment_overlap_and_incomplete_grid(tmp_path):
    root = tree(tmp_path, [p.marker_row(service(A), 2021, 't')])
    path = root / p.SNAPSHOT / f'{p.PAYMENTS}.csv'
    write(path, [p.payment_row(A, 2017, provider(A), 't'), dict(p.payment_row(A, 2018, provider(A), 't'), service_year='2019')])
    result = p.check(root)
    assert result['payer_payments_overlap_earlier_snapshots'] == 1 and result['payer_payments_grid_complete'] >= 1
    assert result['payer_payments_year_or_version'] == 1
