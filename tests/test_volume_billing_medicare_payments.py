"""Blank, status and sum rules of the Medicare Part B payment tables, and check() on a small processed tree."""
from collections import Counter

import pytest

from kolproject.volume_billing import base
from kolproject.volume_billing import medicare_payments as m

A, B, C, D = '1000000001', '1000000002', '1000000003', '1000000004'
DIVISIONS = {A: 'core', B: 'referring_proxy', C: 'affiliate_A', D: 'hemonc_other'}


def cms(npi, total='1000.5', drug='400.25', non_drug='600.25', drug_people='12', marks=('', '')):
    hidden = bool(marks[0] or marks[1])
    return {'Rndrng_NPI': npi, 'Rndrng_Prvdr_State_Abrvtn': 'NY', 'Rndrng_Prvdr_Type': 'Hematology-Oncology',
            'Tot_Benes': '40', 'Tot_Srvcs': '900', 'Tot_Mdcr_Pymt_Amt': total, 'Drug_Sprsn_Ind': marks[0],
            'Drug_Tot_Benes': '' if hidden else drug_people, 'Drug_Mdcr_Pymt_Amt': '' if hidden else drug,
            'Med_Sprsn_Ind': marks[1], 'Med_Tot_Benes': '' if hidden else '40',
            'Med_Mdcr_Pymt_Amt': '' if hidden else non_drug}


def released(npi, code, units='10', average='12.345', place='O'):
    return {'Rndrng_NPI': npi, 'Rndrng_Prvdr_Type': 'Hematology-Oncology', 'HCPCS_Cd': code, 'HCPCS_Drug_Ind': 'Y',
            'Place_Of_Srvc': place, 'Tot_Benes': '11', 'Tot_Srvcs': units, 'Tot_Bene_Day_Srvcs': '20',
            'Avg_Mdcr_Alowd_Amt': '15', 'Avg_Mdcr_Pymt_Amt': average}


def test_status_in_words_and_blanks_are_never_zero():
    split = m.year_row(A, 2024, cms(A, drug='0', non_drug='1000.5', drug_people='0'))
    assert split['by_provider_status'] == 'published_split' and split['drug_medicare_payment_usd'] == '0.00'
    assert split['total_medicare_payment_usd'] == '1000.50' and split['drug_beneficiaries'] == '0'
    hidden = m.year_row(B, 2023, cms(B, marks=('*', '#')))
    assert hidden['by_provider_status'] == 'split_suppressed' and hidden['total_medicare_payment_usd'] == '1000.50'
    assert (hidden['drug_medicare_payment_usd'], hidden['non_drug_medicare_payment_usd'],
            hidden['drug_beneficiaries'], hidden['non_drug_beneficiaries']) == ('', '', '', '')
    assert (hidden['drug_suppression_indicator'], hidden['non_drug_suppression_indicator']) == ('*', '#')
    absent = m.year_row(C, 2019, None)
    assert absent['by_provider_status'] == 'no_row' and absent['service_year'] == 2019
    assert absent['source_dataset_version_id'] == m.BY_PROVIDER_VERSIONS[2019]
    assert not any(v for k, v in absent.items()
                   if k not in ('npi', 'service_year', 'by_provider_status', 'source_dataset_version_id'))
    assert m.BY_PROVIDER_ALIAS not in m.BY_PROVIDER_VERSIONS.values()
    with pytest.raises(AssertionError):
        m.provider_status({**cms(A), 'Drug_Sprsn_Ind': '*'})


def test_drug_row_keeps_code_numbers_and_rounds_the_estimate_half_up():
    row = m.drug_row(released(A, 'J9312', units='10', average='12.3455'))
    assert row['estimated_medicare_payment_usd'] == '123.46' and row['average_medicare_payment_usd'] == '12.3455'
    assert row['lymphoma_product_family'] == 'rituximab_intravenous' and row['beneficiaries'] == '11'
    assert m.drug_row(released(A, 'J0185'))['lymphoma_product_family'] == ''
    assert m.drug_row(released(A, 'J9034', units='433.5'))['services_or_units'] == '433.5'
    assert not any('desc' in name.lower() for name in row)
    assert not any('desc' in field.lower() for field in m.SERVICE_FIELDS + m.BY_PROVIDER_FIELDS)
    assert len(m.PRODUCT_FAMILIES) == 25 and sorted(m.LOCAL_CODES + m.PULLED_CODES) == sorted(m.PRODUCT_FAMILIES)
    with pytest.raises(AssertionError):
        m.drug_row({**released(A, '96413'), 'HCPCS_Drug_Ind': 'N'})


def test_sums_are_absent_not_zero_and_status_explains_each_blank():
    rows = [m.drug_row(released(A, 'J9312')), m.drug_row(released(A, 'J0185', units='3', average='1.005')),
            m.drug_row(released(A, 'J9301', place='F'))]
    assert m.total_by_npi(rows, 'estimated_medicare_payment_usd') == {A: 12345 + 302 + 12345}
    assert m.total_by_npi(filter(m.is_office_product, rows), 'estimated_medicare_payment_usd') == {A: 12345}
    sums = {m.SUMS[0]: {A: 24992}, m.SUMS[1]: {A: 12345}, m.SUMS[2]: {}, m.SUMS[3]: {C: 5000}}
    with_rows = m.feature_row(A, m.year_row(A, 2024, cms(A)), sums)
    assert with_rows['partb_drug_rows_status'] == 'released_rows' and with_rows[m.SUMS[0]] == '249.92'
    assert with_rows[m.SUMS[2]] == '' and with_rows['service_product_data_status'] == 'released_cells'
    cells = {'no_released_rows': cms(D), 'drug_payment_zero': cms(D, drug='0', non_drug='1000.5', drug_people='0'),
             'split_suppressed': cms(D, marks=('*', '*')), 'no_by_provider_row': None}
    for status, record in cells.items():
        row = m.feature_row(D, m.year_row(D, 2024, record), sums)
        assert row['partb_drug_rows_status'] == status and row['service_product_data_status'] == 'no_released_cells'
        assert [row[name] for name in m.SUMS] == [''] * 4
    part_d_only = m.feature_row(C, m.year_row(C, 2024, None), sums)
    assert part_d_only[m.SUMS[3]] == '50.00' and part_d_only['service_product_data_status'] == 'released_cells'
    with pytest.raises(AssertionError):
        m.feature_row(A, m.year_row(A, 2024, cms(A, marks=('*', '#'))), sums)


def test_requests_are_batched_on_public_identifiers_only_and_cells_are_compared():
    assert m.batches(['3', '1', '2']) == [['1', '2', '3']] and len(m.batches(map(str, range(250)))) == 3
    assert m.in_filter([A, B]).endswith(f'[value][0]={A}&filter[a][condition][value][1]={B}')
    left = [released(A, 'J9312'), released(B, 'J9312')]
    assert m.same_cells(left, list(reversed(left))) == 0
    assert m.same_cells(left, [released(A, 'J9312', units='11'), released(B, 'J9312')]) == 1
    assert m.same_cells(left, left[:1]) == 1


def tree(tmp_path, monkeypatch):
    """A four-NPI processed tree built with the module's own row functions, and matching expected figures."""
    records = {year: {A: cms(A), B: cms(B, marks=('*', '#')), D: cms(D, drug='0', non_drug='1000.5', drug_people='0')}
               for year in m.YEARS}
    del records[2019][D]
    year_rows = [m.year_row(npi, year, records[year].get(npi)) for npi in sorted(DIVISIONS) for year in m.YEARS]
    drug_rows = [m.drug_row(released(A, 'J9312')), m.drug_row(released(A, 'J0185', units='3', average='1.005'))]
    part_d = [{'npi': C, 'total_drug_cost_usd': '20.5'}, {'npi': C, 'total_drug_cost_usd': '29.5'}]
    sums = {m.SUMS[0]: m.total_by_npi(drug_rows, 'estimated_medicare_payment_usd'),
            m.SUMS[1]: m.total_by_npi(filter(m.is_office_product, drug_rows), 'estimated_medicare_payment_usd'),
            m.SUMS[2]: {A: 9900}, m.SUMS[3]: m.total_by_npi(part_d, 'total_drug_cost_usd')}
    cell = {r['npi']: r for r in year_rows if r['service_year'] == 2024}
    features = [m.feature_row(npi, cell[npi], sums) for npi in sorted(DIVISIONS)]
    people = [{'npi': npi, 'division': division, 'chemo_administration_office_rows': int(npi == A),
               'part_d_lymphoma_oral_rows': 2 * (npi == C),
               'cms_provider_type_2024': 'Hematology-Oncology' if npi == A else '',
               'medicare_beneficiaries_2024': '40' if npi == A else ''} for npi, division in DIVISIONS.items()]
    core = [{'npi': A, 'cms_hcpcs_drug_indicator': 'Y', 'estimated_medicare_payment_usd': r['estimated_medicare_payment_usd']}
            for r in drug_rows] + [{'npi': A, 'cms_hcpcs_drug_indicator': 'N', 'estimated_medicare_payment_usd': '77.00'}]
    base.write_csv(tmp_path / base.UNIVERSE_SNAPSHOT / 'hcp_divisions.csv', people)
    base.write_csv(tmp_path / base.UNIVERSE_SNAPSHOT / 'part_d_lymphoma_orals_2024.csv', part_d)
    base.write_csv(tmp_path / '2026-10-15' / 'cms_provider_service.csv', core)
    for table, rows in zip(m.TABLES, [year_rows, features, drug_rows]):
        base.write_csv(tmp_path / m.SNAPSHOT / f'{table}.csv', rows)
    monkeypatch.setattr(m, 'EXPECTED_ROWS_WITH_RECORD', {year: 2 if year == 2019 else 3 for year in m.YEARS})
    monkeypatch.setattr(m, 'EXPECTED_SUPPRESSED', dict.fromkeys(m.YEARS, 1))
    monkeypatch.setattr(m, 'EXPECTED_2024_TOTAL_MILLION', {'referring': '0.0', 'affiliates': '0.0', 'core': '0.0'})
    monkeypatch.setattr(m, 'EXPECTED_2024_PUBLISHED_DRUG_BILLERS', {'referring': 0, 'affiliates': 0, 'core': 1})
    monkeypatch.setattr(m, 'EXPECTED_FEATURE_NPIS', dict(zip(m.SUMS, [1, 1, 1, 1])))
    monkeypatch.setattr(m, 'EXPECTED_FEATURE_MILLION', dict(zip(m.SUMS, ['0.0'] * 4)))
    monkeypatch.setattr(m, 'EXPECTED_DRUG_ROWS', 2)
    # Figures of this tree worked out by hand: A, B and D have a 2024 row of $1,000.50; only A and D show the split.
    monkeypatch.setattr(m, 'EXPECTED_HEADLINE', {
        'npis_with_row_in_all_six_years': 2, 'npis_with_no_row_in_any_year': 1,
        'universe_by_year': {2019: {'npis_with_row': 2, 'total_usd': '2001.00', 'drug_usd': '400.25'},
                             2024: {'npis_with_row': 3, 'total_usd': '3001.50', 'drug_usd': '400.25',
                                    'non_drug_usd': '1600.75'}},
        'division_groups_by_year': {2024: {'referring': {'npis_drug_side_suppressed_1_to_10_beneficiaries': 1}}},
        'drug_rows': {'rows': 2, 'place_of_service': {'O': 2}}, 'features': {m.SUMS[2]: {'npis': 1, 'usd': '99.00'}}})
    monkeypatch.setattr(m, 'TABLE_SHA256', {table: m.fingerprint(base.read_csv(tmp_path / m.SNAPSHOT / f'{table}.csv'))
                                            for table in m.TABLES})
    return tmp_path


def failing(processed, content=False):
    """Names of the failing checks; the three whole-table fingerprints fire on any edit and are left out by default."""
    names = {name.removeprefix('medicare_payments.') for name, count in m.check(processed).items() if count}
    return names if content else names - set(m.CONTENT_CHECKS.values())


def rewrite(processed, table, change):
    path = processed / m.SNAPSHOT / f'{table}.csv'
    rows = base.read_csv(path)
    rows = change(rows) or rows
    base.write_csv(path, rows)


def test_check_passes_a_consistent_tree_and_names_every_check_with_the_module(tmp_path, monkeypatch):
    result = m.check(tree(tmp_path, monkeypatch))
    assert all(name.startswith('medicare_payments.') for name in result) and len(result) > 30
    assert not {name: count for name, count in result.items() if count}


def test_check_catches_a_zero_filled_blank_and_a_broken_split(tmp_path, monkeypatch):
    processed = tree(tmp_path, monkeypatch)

    def zero_fill(rows):
        for row in rows:
            if row['by_provider_status'] == 'no_row' and row['npi'] == C:
                row['total_medicare_payment_usd'] = '0.00'
    rewrite(processed, m.BY_YEAR, zero_fill)
    assert failing(processed) == {'by_year_no_row_cell_filled'}

    processed = tree(tmp_path, monkeypatch)

    def unhide(rows):
        next(r for r in rows if r['npi'] == B and r['service_year'] == '2021')['drug_medicare_payment_usd'] = '5.00'
    rewrite(processed, m.BY_YEAR, unhide)
    assert failing(processed) == {'by_year_suppressed_split_filled'}

    processed = tree(tmp_path, monkeypatch)

    def shift(rows):
        next(r for r in rows if r['npi'] == A and r['service_year'] == '2020')['non_drug_medicare_payment_usd'] = '600.27'
    rewrite(processed, m.BY_YEAR, shift)
    assert failing(processed) == {'by_year_total_not_drug_plus_non_drug'}

    processed = tree(tmp_path, monkeypatch)
    rewrite(processed, m.BY_YEAR, lambda rows: rows[:-1])
    assert 'by_year_grid_incomplete' in failing(processed)

    processed = tree(tmp_path, monkeypatch)

    def alias(rows):
        rows[0]['source_dataset_version_id'] = m.BY_PROVIDER_ALIAS
    rewrite(processed, m.BY_YEAR, alias)
    assert failing(processed) == {'by_year_version_id_wrong'}


def test_check_catches_sums_that_do_not_follow_from_published_rows(tmp_path, monkeypatch):
    processed = tree(tmp_path, monkeypatch)

    def zero_sum(rows):
        next(r for r in rows if r['npi'] == D)[m.SUMS[0]] = '0.00'
    rewrite(processed, m.FEATURES, zero_sum)
    assert {'features_status_wrong', 'features_drug_rows_sum_wrong'} <= failing(processed)

    processed = tree(tmp_path, monkeypatch)

    def edit_units(rows):
        rows[0]['services_or_units'] = '4'
    rewrite(processed, m.DRUG_ROWS, edit_units)
    assert failing(processed) == {'drug_rows_estimate_wrong'}

    processed = tree(tmp_path, monkeypatch)

    def inflate(rows):
        for row in rows:
            row['average_medicare_payment_usd'] = '100'
            row['estimated_medicare_payment_usd'] = m.estimate('100', row['services_or_units'])
    rewrite(processed, m.DRUG_ROWS, inflate)
    assert failing(processed) == {'features_drug_rows_sum_wrong', 'features_product_sum_wrong'}

    processed = tree(tmp_path, monkeypatch)
    core = processed / '2026-10-15' / 'cms_provider_service.csv'
    base.write_csv(core, base.read_csv(core)[1:])
    assert failing(processed) == {'features_core_drug_rows_differ'}

    processed = tree(tmp_path, monkeypatch)

    def drop_part_d(rows):
        next(r for r in rows if r['npi'] == C)[m.SUMS[3]] = ''
    rewrite(processed, m.FEATURES, drop_part_d)
    assert {'features_part_d_sum_wrong', 'features_part_d_marker_disagrees'} <= failing(processed)

    processed = tree(tmp_path, monkeypatch)
    (processed / m.SNAPSHOT / f'{m.DRUG_ROWS}.csv').unlink()
    assert failing(processed) == {'tables_missing'}


def test_check_catches_a_sum_above_the_by_provider_payment(tmp_path, monkeypatch):
    processed = tree(tmp_path, monkeypatch)

    def shrink(rows):
        for row in rows:
            if row['npi'] == A and row['service_year'] == '2024':
                row.update(total_medicare_payment_usd='90.00', drug_medicare_payment_usd='50.00',
                           non_drug_medicare_payment_usd='40.00')
    rewrite(processed, m.BY_YEAR, shrink)
    assert failing(processed) == {'features_drug_rows_above_by_provider_drug', 'features_admin_above_by_provider_total'}


def test_check_catches_edits_that_keep_every_row_rule(tmp_path, monkeypatch):
    processed = tree(tmp_path, monkeypatch)

    def swap(rows):
        first, second = (next(r for r in rows if r['npi'] == npi and r['service_year'] == '2022') for npi in (A, D))
        for name in first:
            if name != 'npi':
                first[name], second[name] = second[name], first[name]
    rewrite(processed, m.BY_YEAR, swap)
    assert failing(processed, content=True) == {'by_year_content_changed'}

    processed = tree(tmp_path, monkeypatch)

    def five_dollars(rows):
        row = next(r for r in rows if r['npi'] == D and r['service_year'] == '2024')
        row.update(total_medicare_payment_usd='1005.50', non_drug_medicare_payment_usd='1005.50')
    rewrite(processed, m.BY_YEAR, five_dollars)
    assert failing(processed, content=True) == {'headline_figure_changed', 'by_year_content_changed'}

    processed = tree(tmp_path, monkeypatch)

    def admin(rows):
        next(r for r in rows if r['npi'] == A)[m.SUMS[2]] = '109.00'
    rewrite(processed, m.FEATURES, admin)
    assert failing(processed, content=True) == {'headline_figure_changed', 'features_content_changed'}

    processed = tree(tmp_path, monkeypatch)

    def facility(rows):
        next(r for r in rows if r['hcpcs_code'] == 'J0185')['place_of_service'] = 'F'
    rewrite(processed, m.DRUG_ROWS, facility)
    assert failing(processed, content=True) == {'headline_figure_changed', 'drug_rows_content_changed'}


def test_check_counts_malformed_cells_instead_of_stopping(tmp_path, monkeypatch):
    processed = tree(tmp_path, monkeypatch)

    def words(rows):
        next(r for r in rows if r['npi'] == A and r['service_year'] == '2024')['total_medicare_payment_usd'] = 'n/a'
    rewrite(processed, m.BY_YEAR, words)
    assert 'by_year_cell_not_numeric' in failing(processed)

    processed = tree(tmp_path, monkeypatch)

    def zero_to_blank(rows):
        next(r for r in rows if r['npi'] == D and r['service_year'] == '2024')['drug_medicare_payment_usd'] = ''
    rewrite(processed, m.BY_YEAR, zero_to_blank)
    assert failing(processed) == {'by_year_published_split_blank'}

    processed = tree(tmp_path, monkeypatch)

    def sum_in_words(rows):
        next(r for r in rows if r['npi'] == A)[m.SUMS[0]] = '1e3'
    rewrite(processed, m.FEATURES, sum_in_words)
    assert failing(processed) == {'features_cell_not_numeric', 'features_headline_changed'}

    processed = tree(tmp_path, monkeypatch)

    def people_in_words(rows):
        rows[0]['beneficiaries'] = 'eleven'
    rewrite(processed, m.DRUG_ROWS, people_in_words)
    assert 'drug_rows_cell_invalid' in failing(processed)


def test_check_catches_impossible_indicators_counts_and_a_break_with_the_universe_table(tmp_path, monkeypatch):
    processed = tree(tmp_path, monkeypatch)

    def both_protective(rows):
        next(r for r in rows if r['npi'] == B and r['service_year'] == '2020')['drug_suppression_indicator'] = '#'
    rewrite(processed, m.BY_YEAR, both_protective)
    assert failing(processed) == {'by_year_status_disagrees_with_indicators'}

    processed = tree(tmp_path, monkeypatch)

    def one_more(rows):
        next(r for r in rows if r['npi'] == D and r['service_year'] == '2021')['total_beneficiaries'] = '41'
    rewrite(processed, m.BY_YEAR, one_more)
    assert failing(processed) == {'by_year_beneficiaries_inconsistent'}

    processed = tree(tmp_path, monkeypatch)

    def other_type(rows):
        next(r for r in rows if r['npi'] == A and r['service_year'] == '2024')['cms_provider_type'] = 'Hematology'
    rewrite(processed, m.BY_YEAR, other_type)
    assert failing(processed) == {'by_year_2024_disagrees_with_universe_table'}

    processed = tree(tmp_path, monkeypatch)
    (processed / '2026-10-15' / 'cms_provider_service.csv').unlink()
    assert failing(processed) == {'features_core_drug_rows_differ'}


def test_fingerprint_and_figure_comparison():
    rows = [{'npi': A, 'x': '1'}, {'npi': B, 'x': '2'}]
    assert m.fingerprint(rows) == m.fingerprint(list(reversed(rows)))
    assert m.fingerprint(rows) != m.fingerprint([{'npi': A, 'x': '2'}, {'npi': B, 'x': '1'}])
    assert m.fingerprint(rows) != m.fingerprint([{'npi': A, 'y': '1'}, {'npi': B, 'y': '2'}])
    assert m.figures_differing({'a': 1, 'b': {'c': '2.00', 'd': 3}}, {'a': 1, 'b': {'c': '2.00', 'd': 3}, 'e': 9}) == 0
    assert m.figures_differing({'a': 1, 'b': {'c': '2.00', 'd': 3}}, {'a': 2, 'b': {'c': '2.01'}}) == 3
    assert m.figures_differing({'b': {'c': 1}}, {'b': 5}) == 1


def test_saved_queries_are_found_in_the_frozen_copy_when_the_older_folder_is_gone(tmp_path, monkeypatch):
    frozen = tmp_path / 'raw' / f'cms_queries_{m.UNIVERSE_SNAPSHOT}'
    frozen.mkdir(parents=True)
    for name in ('provider_b.json', 'provider_a.json', 'service_96413.json'):
        (frozen / name).write_text('{}')
    monkeypatch.setattr(m, 'QUERIES', tmp_path / 'absent')
    monkeypatch.setattr(m, 'RAW', tmp_path / 'raw')
    monkeypatch.setattr(m, 'SAVED_PROVIDER_QUERIES', 2)
    assert m.saved_provider_queries() == ['provider_a.json', 'provider_b.json']
    monkeypatch.setattr(m, 'SAVED_PROVIDER_QUERIES', 3)
    with pytest.raises(AssertionError):
        m.saved_provider_queries()


def test_division_groups_are_an_explicit_list():
    groups = Counter(base.DIVISION_GROUPS.values())
    assert groups == {'affiliates': 4, 'core': 1, 'referring': 1, 'other': 1}
    assert m.group_of({A: {'division': 'center_affiliate_unlinked'}}) == {A: 'affiliates'}


def test_published_tables_pass_when_present():
    processed = base.OUT.parent
    if not all((processed / m.SNAPSHOT / f'{table}.csv').is_file() for table in m.TABLES):
        pytest.skip('2026-11-19 Medicare tables not built here')
    result = m.check(processed)
    assert not {name: count for name, count in result.items() if count}
