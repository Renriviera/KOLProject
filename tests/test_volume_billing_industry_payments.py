"""Record rules, aggregation to NPI, division and center, and validation of the Open Payments universe tables."""
from pathlib import Path

import pytest

from kolproject.common import read_csv, write_csv
from kolproject.volume_billing import industry_payments as ip

CORE, AFFILIATE, REFERRING, UNPAID, OUTSIDE = '1000000001', '1000000002', '1000000003', '1000000004', '1999999999'
PEOPLE = {CORE: 'core', AFFILIATE: 'affiliate_A', REFERRING: 'referring_proxy', UNPAID: 'hemonc_other'}
CENTERS = ['010033', '050224']
SOURCES = {str(year): {'member': f'OP_DTL_GNRL_PGYR{year}_TEST.csv', 'retrieved_at_utc': '2026-11-12T00:00:00Z'} for year in range(2019, 2026)}
SPEAKING = ('Compensation for services other than consulting, including serving as faculty or as a speaker '
            'at a venue other than a continuing education program')
SPACE = 'Space rental or facility fees (teaching hospital only)'
P = ip.PERIOD


def general(record_id, npi, year, payer, nature, amount, products=(), third='No Third Party Payment', ccn=''):
    row = {'record_id': record_id, 'program_year': str(year), 'date_of_payment': f'07/04/{year}',
           'covered_recipient_type': 'Covered Recipient Physician' if npi else 'Covered Recipient Teaching Hospital',
           'teaching_hospital_ccn': ccn, 'teaching_hospital_name': 'Example Hospital' if ccn else '',
           'covered_recipient_npi': npi, 'covered_recipient_profile_id': '77' if npi else '',
           'total_amount_of_payment_usdollars': amount, 'number_of_payments_included_in_total_amount': '1',
           'applicable_manufacturer_or_applicable_gpo_making_payment_name': payer,
           'applicable_manufacturer_or_applicable_gpo_making_payment_id': '1',
           'submitting_applicable_manufacturer_or_applicable_gpo_name': payer,
           'nature_of_payment_or_transfer_of_value': nature,
           'form_of_payment_or_transfer_of_value': 'Cash or cash equivalent',
           'third_party_payment_recipient_indicator': third,
           'name_of_third_party_entity_receiving_payment_or_transfer_of_value': '' if third.startswith('No') else 'Example Clinic LLC',
           'contextual_information': 'free text', 'delay_in_publication_indicator': 'No',
           'dispute_status_for_publication': 'No', 'change_type': 'UNCHANGED', 'payment_publication_date': '06/30/2026'}
    for k in range(1, 6):
        row[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] = products[k - 1] if k <= len(products) else ''
        row[f'product_category_or_therapeutic_area_{k}'] = 'Oncology' if k <= len(products) else ''
    return row


def research(year, investigators, recipient, amount, products=(), study='', ccn=''):
    row = {'program_year': str(year), 'covered_recipient_npi': recipient, 'teaching_hospital_ccn': ccn,
           'teaching_hospital_name': 'Example Hospital' if ccn else '', 'total_amount_of_payment_usdollars': amount,
           'covered_recipient_type': 'Covered Recipient Teaching Hospital' if ccn else 'Non-covered Recipient Entity',
           'name_of_study': study, 'context_of_research': ''}
    for k in range(1, 6):
        row[f'principal_investigator_{k}_npi'] = investigators[k - 1] if k <= len(investigators) else ''
        row[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] = products[k - 1] if k <= len(products) else ''
    return row


GENERAL_RECORDS = [
    general('1', CORE, 2022, 'Kite Pharma, Inc.', 'Consulting Fee', '2500.00', ['Yescarta']),
    general('2', CORE, 2023, 'Kite Pharma, Inc.', 'Food and Beverage', '40.00', third='Entity'),
    general('3', AFFILIATE, 2024, 'Janssen Biotech, Inc.', 'Food and Beverage', '25.10', ['RYBREVANT', 'CARVYKTI']),
    general('4', REFERRING, 2019, 'Celgene Corporation', 'Consulting Fee', '1000.00', ['JCAR017']),
    general('5', AFFILIATE, 2021, 'Pfizer Inc.', 'Grant', '500.00'),
    general('6', OUTSIDE, 2022, 'Kite Pharma, Inc.', 'Consulting Fee', '999.00', ['Yescarta']),
    general('7', '', 2022, 'Kite Pharma, Inc.', SPACE, '3000.00', ['Yescarta'], ccn='010033'),
    general('8', '', 2023, 'Other Co', 'Royalty or License', '100000.00', ccn='010033'),
    general('9', '', 2023, 'Other Co', 'Grant', '10.00', ccn='999999'),
    general('10', '', 2025, 'Other Co', 'Grant', '70.00', ccn='010033'),
]
RESEARCH_RECORDS = [
    research(2022, [CORE, AFFILIATE], '', '50000.00', ['Yescarta'], ccn='010033'),
    research(2023, [CORE], CORE, '1000.00', study='A CAR-T study'),
    research(2024, [], REFERRING, '200.00'),
    research(2024, [OUTSIDE], '', '900.00', ['Kymriah']),
]


def tallies():
    by_year = {}
    for year in sorted({r['program_year'] for r in GENERAL_RECORDS + RESEARCH_RECORDS}):
        mine = [r for r in GENERAL_RECORDS if r['program_year'] == year]
        studies = [r for r in RESEARCH_RECORDS if r['program_year'] == year]
        by_year[year] = (
            ip.tally_general(list(GENERAL_RECORDS[0]), [list(r.values()) for r in mine], set(PEOPLE)),
            ip.tally_research(list(RESEARCH_RECORDS[0]), [list(r.values()) for r in studies], set(PEOPLE)))
    return by_year


def tables():
    """The five tables built from the synthetic records through the same functions the build uses."""
    detail, cells = [], {name: [] for name in ip.CELL_FIELDS}
    header = list(GENERAL_RECORDS[0])
    for year, (found, studies) in tallies().items():
        detail += [ip.detail_row(dict(zip(header, row)), SOURCES[year]) for row in found['candidates']]
        for kind, result in [('general', found), ('research', studies)]:
            for part in ['people', 'hospitals']:
                cells[f'{kind}_{part}'] += [{**c, 'program_year': year} for c in ip.cell_rows(kind, part, result[part])]
    return ip.assemble(PEOPLE, CENTERS, detail, cells)


def write_tree(root, built):
    """A small Data/processed tree: the five tables, the universe, the centers and the 2026-11-11 core tables."""
    for table, rows in built.items():
        fields = [c['column'] for c in ip.COLUMNS[table]()]
        write_csv(root / ip.SNAPSHOT / f'{table}.csv', [{f: row[f] for f in fields} for row in rows], fields)
    write_csv(root / '2026-10-24' / 'hcp_divisions.csv', [{'npi': n, 'division': d, 'label_version': '1.0.0'} for n, d in PEOPLE.items()])
    write_csv(root / '2026-10-24' / 'cart_centers.csv', [{'ccn': c} for c in CENTERS])
    old = root / ip.CORE_SNAPSHOT
    write_csv(old / 'hcp_general_payment_features.csv', [{
        'npi': CORE, 'general_data_status': 'published_records', 'general_payment_rows_2019_2025': 2,
        'general_payments_usd_2019_2025': 2540.0, 'general_active_years': 2, 'cart_product_rows_2019_2025': 1,
        'cart_product_usd_2019_2025': 2500.0, 'cart_active_years': 1, 'cart_products_2019_2025': 'YESCARTA'}])
    write_csv(old / 'general_payments_2019_2025.csv', [
        {'record_id': '1', 'npi': CORE, 'amount_usd': 2500.0, 'cart_product_mentioned': 1, 'manufacturer': 'Kite Pharma, Inc.',
         'payment_nature': 'Consulting Fee', 'products': 'Yescarta', 'third_party_recipient': ''},
        {'record_id': '2', 'npi': CORE, 'amount_usd': 40.0, 'cart_product_mentioned': 0, 'manufacturer': 'Kite Pharma, Inc.',
         'payment_nature': 'Food and Beverage', 'products': '', 'third_party_recipient': 'Example Clinic LLC'}])
    write_csv(old / 'hcp_research_payment_features.csv', [{
        'npi': CORE, 'research_data_status': 'published_records', 'research_rows_2019_2025': 2,
        'research_record_usd_2019_2025': 51000.0, 'research_active_years': 2, 'cart_research_rows_2019_2025': 2,
        'cart_research_record_usd_2019_2025': 51000.0}])
    write_csv(old / 'research_payments_2019_2025.csv', [
        {'npi': CORE, 'pi_slots': '1', 'cart_product_mentioned': 1, 'amount_usd': 50000.0},
        {'npi': CORE, 'pi_slots': '1', 'cart_product_mentioned': 0, 'amount_usd': 1000.0}])


# Checks that cannot pass on a four-person universe: totals and fingerprints of the real publication and the 500-HCP core count.
SCALE_ONLY = {'headline_totals', 'report_headline_direct_cart_only_services', 'core_general_equals_2026_11_11',
              'core_research_equals_2026_11_11', 'table_fingerprints'}


def failing(root):
    return {name.split('.', 1)[1] for name, errors in ip.check(root).items() if errors}


def test_nature_classes_follow_the_cms_category():
    assert ip.nature_class('Consulting Fee') == ip.nature_class('Honoraria') == ip.nature_class(SPEAKING) == 'services'
    assert ip.nature_class('Compensation for serving as faculty or as a speaker for a medical education program') == 'services'
    assert {ip.nature_class(n) for n in ['Food and Beverage', 'Travel and Lodging', 'Education', 'Gift', 'Entertainment']} == {'hospitality_education'}
    assert {ip.nature_class(n) for n in ['Grant', 'Royalty or License', 'Acquisitions', 'Unknown future category']} == {'other_financial'}


def test_product_rules_are_nested_and_read_each_field():
    assert ip.product_flags(['Yescarta', '', '', '', '']) == {
        'cart_products': 'YESCARTA', 'any_slot': 1, 'cart_only': 1, 'lymphoma_only': 1, 'development_name_only': 0}
    co_listed = ip.product_flags(['RYBREVANT', 'CARVYKTI'])
    assert (co_listed['any_slot'], co_listed['cart_only'], co_listed['lymphoma_only']) == (1, 0, 0)
    myeloma = ip.product_flags(['Abecma', 'CARVYKTI'])
    assert (myeloma['cart_only'], myeloma['lymphoma_only'], myeloma['cart_products']) == (1, 0, 'ABECMA;CARVYKTI')
    assert ip.product_flags(['JCAR017'])['development_name_only'] == 1 and ip.product_flags(['JCAR017'])['lymphoma_only'] == 1
    assert ip.product_flags(['bb2121', 'Abecma'])['development_name_only'] == 0
    assert ip.product_flags(['', ' ']) == ip.NO_PRODUCT == {
        'cart_products': '', 'any_slot': 0, 'cart_only': 0, 'lymphoma_only': 0, 'development_name_only': 0}
    assert ip.product_flags(['Trodelvy'])['any_slot'] == 0


def test_every_product_pattern_is_either_a_name_or_a_development_code():
    codes = {'kte-c19', 'kte-x19', 'ctl019', 'jcar017', 'bb2121', 'jnj-68284528', 'jnj-4528', 'lcar-b38m', 'auto1'}
    names = {'yescarta', 'axicabtagene', 'axi-cel', 'tecartus', 'brexucabtagene', 'brexu-cel', 'kymriah', 'tisagenlecleucel',
             'tisa-cel', 'breyanzi', 'lisocabtagene', 'liso-cel', 'abecma', 'idecabtagene', 'ide-cel', 'carvykti',
             'ciltacabtagene', 'cilta-cel', 'aucatzyl', 'obecabtagene', 'obe-cel'}
    assert all(ip.ANY_CART.search(text) for text in codes | names)
    assert all(ip.NAMED_CART.search(text) for text in names) and not any(ip.NAMED_CART.search(text) for text in codes)
    assert sum(pattern.count('|') + 1 for pattern in ip.CART_PRODUCTS.values()) == len(names) + len(codes) - 1


def test_pure_play_payers_are_matched_on_the_paying_entity_name():
    assert [ip.pure_play(n) for n in ['Kite Pharma, Inc.', 'Legend Biotech USA Inc.', 'Autolus Inc']] == [1, 1, 1]
    assert [ip.pure_play(n) for n in ['Gilead Sciences, Inc.', 'Janssen Biotech, Inc.', 'Celgene Corporation', 'Kitef Co']] == [0, 0, 0, 0]


def test_research_links_count_each_npi_once_and_separate_recipient_only():
    npis = set(PEOPLE)
    assert ip.research_links([CORE, AFFILIATE, CORE, OUTSIDE], '', npis) == [(CORE, 'investigator'), (AFFILIATE, 'investigator')]
    assert ip.research_links([CORE], CORE, npis) == [(CORE, 'investigator')]
    assert ip.research_links([OUTSIDE], REFERRING, npis) == [(REFERRING, 'recipient_only')]
    assert ip.research_links([OUTSIDE], OUTSIDE, npis) == []
    assert ip.research_text_match('A CAR-T study', '') == 1 and ip.research_text_match('axi-cel in lymphoma', '') == 1
    assert ip.research_text_match('A cartilage study', '') == 0


def test_general_tally_keeps_candidates_and_counts_every_universe_and_hospital_row():
    header = list(GENERAL_RECORDS[0])
    found = ip.tally_general(header, [list(r.values()) for r in GENERAL_RECORDS], set(PEOPLE))
    assert found['counts']['rows_scanned'] == 10 and found['counts']['universe_rows'] == 5
    assert found['counts']['teaching_hospital_rows'] == 4 and found['counts']['teaching_hospital_rows_with_npi'] == 0
    assert found['counts']['amounts_not_in_whole_cents'] == 0
    assert [row[header.index('record_id')] for row in found['candidates']] == ['1', '2', '3', '4']
    assert found['people'][AFFILIATE, 'Grant', 'No Third Party Payment'] == [1, 50000]
    assert found['people'][CORE, 'Food and Beverage', 'Entity'] == [1, 4000]
    assert found['hospitals']['010033', 'Example Hospital', SPACE, 1, 1, 1] == [1, 300000]
    assert not any(key[0] == OUTSIDE for key in found['people'])
    big = ip.tally_general(header, [list(general('11', '', 2021, 'Other Co', 'Royalty or License', '162972633.17', ccn='010033').values())], set())
    assert big['counts']['amounts_not_in_whole_cents'] == 0 and list(big['hospitals'].values()) == [[1, 16297263317]]


def test_research_tally_gives_cells_by_role_and_hospital():
    header = list(RESEARCH_RECORDS[0])
    found = ip.tally_research(header, [list(r.values()) for r in RESEARCH_RECORDS], set(PEOPLE))
    assert found['counts']['rows_scanned'] == 4 and found['counts']['records_naming_universe_npi'] == 3
    assert found['people'][CORE, 'investigator', 'YESCARTA', 0] == [1, 5000000]
    assert found['people'][CORE, 'investigator', '', 1] == [1, 100000]
    assert found['people'][REFERRING, 'recipient_only', '', 0] == [1, 20000]
    assert found['hospitals']['010033', 'Example Hospital', 1, 0] == [1, 5000000]
    assert found['cart_product_records_by_recipient_type'] == {
        'Covered Recipient Teaching Hospital': {'rows': 1, 'usd': 50000.0}, 'Non-covered Recipient Entity': {'rows': 1, 'usd': 900.0}}


def test_detail_row_carries_flags_and_leaves_out_free_text_and_payee_names():
    row = ip.detail_row(GENERAL_RECORDS[1], SOURCES['2023'])
    assert (row['any_slot'], row['pure_play_payer'], row['direct_payment'], row['nature_class']) == ('0', '1', '0', 'hospitality_education')
    assert row['third_party_payment_indicator'] == 'Entity' and row['amount_usd'] == '40.00' and row['payment_date'] == '2023-07-04'
    co_listed = ip.detail_row(GENERAL_RECORDS[2], SOURCES['2024'])
    assert (co_listed['any_slot'], co_listed['cart_only'], co_listed['products']) == ('1', '0', 'RYBREVANT;CARVYKTI')
    public = [c['column'] for c in ip.COLUMNS[ip.DETAIL]() if not c['restricted']]
    assert not {'contextual_information', 'third_party_recipient', 'hcp_id', 'division', 'last_name', 'state'} & set(public)
    assert set(public) <= set(row)


def test_general_features_are_zero_filled_and_split_direct_from_third_party():
    built = {r['npi']: r for r in tables()[ip.GENERAL]}
    core, unpaid = built[CORE], built[UNPAID]
    assert (core['general_data_status'], core[f'general_payment_rows_{P}'], core[f'general_payments_usd_{P}']) == ('published_records', 2, '2540.00')
    assert (core[f'services_usd_{P}'], core[f'hospitality_education_usd_{P}'], core[f'other_financial_usd_{P}']) == ('2500.00', '40.00', '0.00')
    assert (core[f'general_direct_rows_{P}'], core[f'general_third_party_rows_{P}'], core[f'general_third_party_usd_{P}']) == (1, 1, '40.00')
    assert (core[f'cart_any_slot_rows_{P}'], core[f'cart_only_usd_{P}'], core[f'lymphoma_only_services_usd_{P}']) == (1, '2500.00', '2500.00')
    assert (core[f'pure_play_payer_rows_{P}'], core[f'pure_play_payer_direct_rows_{P}'], core[f'pure_play_payer_third_party_usd_{P}']) == (2, 1, '40.00')
    assert (core[f'cart_only_or_pure_play_rows_{P}'], core['cart_any_slot_brands'], core['general_active_years']) == (2, 'YESCARTA', 2)
    affiliate = built[AFFILIATE]
    assert (affiliate[f'cart_any_slot_rows_{P}'], affiliate[f'cart_only_rows_{P}'], affiliate[f'other_financial_usd_{P}']) == (1, 0, '500.00')
    assert unpaid['general_data_status'] == 'no_published_records'
    assert all(unpaid[c['column']] in (0, '0.00') for c in ip.COLUMNS[ip.GENERAL]() if c['missing_value'] == ip.ZERO)


def test_research_features_zero_fill_counts_and_leave_dollars_blank_without_records():
    built = {r['npi']: r for r in tables()[ip.RESEARCH]}
    core, referring, unpaid = built[CORE], built[REFERRING], built[UNPAID]
    assert (core[f'research_rows_{P}'], core[f'research_investigator_rows_{P}'], core[f'research_record_usd_{P}']) == (2, 2, '51000.00')
    assert (core[f'cart_product_research_rows_{P}'], core[f'cart_product_research_record_usd_{P}'], core['cart_product_research_brands']) == (1, '50000.00', 'YESCARTA')
    assert (core[f'cart_product_or_text_research_rows_{P}'], core[f'cart_product_or_text_research_record_usd_{P}']) == (2, '51000.00')
    assert (referring[f'research_recipient_only_rows_{P}'], referring[f'research_investigator_rows_{P}']) == (1, 0)
    assert referring[f'cart_product_research_rows_{P}'] == 0 and referring[f'cart_product_research_record_usd_{P}'] == ''
    assert (unpaid['research_data_status'], unpaid[f'research_rows_{P}'], unpaid[f'research_record_usd_{P}']) == ('no_published_records', 0, '')


def test_division_table_groups_labels_and_states_direct_payments():
    cells = {(r['rule'], r['division_group'], r['nature_class']): r for r in tables()[ip.DIVISION]}
    assert len(cells) == len(ip.RULES) * 4 * 4
    core = cells['pure_play_payer', 'core', 'all']
    assert (core['npis_in_division_group'], core['rows'], core['direct_rows'], core['third_party_rows']) == ('1', '2', '1', '1')
    assert (core['usd'], core['direct_usd'], core['third_party_usd'], core['direct_median_usd_per_paid_npi']) == ('2540.00', '2500.00', '40.00', '2500.00')
    assert cells['any_slot', 'affiliates', 'all']['rows'] == '1' and cells['cart_only', 'affiliates', 'all']['rows'] == '0'
    assert cells['cart_only', 'affiliates', 'all']['direct_median_usd_per_paid_npi'] == ''
    assert cells['cart_only', 'referring', 'services']['direct_usd'] == '1000.00'
    assert cells['cart_only_named_product', 'referring', 'services']['direct_usd'] == '0.00'
    assert cells['any_slot', 'other', 'all']['npis_paid'] == '0'
    assert ip.median_usd([8350, 8351]) == '83.51' and ip.median_usd([100, 300, 500]) == '3.00'


def test_center_rows_are_zero_filled_and_natures_sum_to_the_total():
    built = {r['ccn']: r for r in tables()[ip.CENTER]}
    paid, unpaid = built['010033'], built['050224']
    assert (paid['general_data_status'], paid[f'general_payment_rows_{P}'], paid[f'general_payments_usd_{P}']) == ('published_records', 3, '103070.00')
    assert (paid[f'space_rental_usd_{P}'], paid[f'royalty_license_usd_{P}'], paid[f'other_nature_usd_{P}']) == ('3000.00', '100000.00', '0.00')
    assert (paid[f'general_payments_usd_{ip.RECENT}'], paid[f'grant_usd_{P}'], paid['general_active_years']) == ('70.00', '70.00', 3)
    assert unpaid[f'general_payments_usd_{ip.RECENT}'] == '0.00'
    assert (paid[f'cart_any_slot_rows_{P}'], paid[f'lymphoma_only_usd_{P}'], paid['teaching_hospital_name']) == (1, '3000.00', 'Example Hospital')
    assert (paid[f'research_rows_{P}'], paid[f'cart_product_research_usd_{P}'], paid['research_active_years']) == (1, '50000.00', 1)
    assert (unpaid['general_data_status'], unpaid['research_data_status'], unpaid['teaching_hospital_name']) == ('no_published_records',) * 2 + ('',)
    assert unpaid[f'general_payment_rows_{P}'] == 0 and unpaid[f'general_payments_usd_{P}'] == '0.00'


def test_check_passes_on_a_consistent_tree_except_for_real_publication_totals(tmp_path):
    write_tree(tmp_path, tables())
    assert failing(tmp_path) == SCALE_ONLY
    assert all(name.startswith('industry_payments.') for name in ip.check(tmp_path))


def test_check_reports_a_missing_table(tmp_path):
    write_tree(tmp_path, tables())
    (tmp_path / ip.SNAPSHOT / f'{ip.DIVISION}.csv').unlink()
    assert ip.check(tmp_path) == {'industry_payments.files_present': 1}


@pytest.mark.parametrize('table, key, change, expected', [
    (ip.DETAIL, '1', {'amount_usd': '2600.00'}, {'general_equals_row_level_table', 'division_equals_row_level_table', 'core_row_level_equals_2026_11_11'}),
    (ip.DETAIL, '3', {'cart_only': '1'}, {'detail_flags_recomputed', 'general_equals_row_level_table', 'division_equals_row_level_table'}),
    (ip.DETAIL, '2', {'pure_play_payer': '0'}, {'detail_scope', 'detail_flags_recomputed', 'general_equals_row_level_table', 'division_equals_row_level_table'}),
    (ip.GENERAL, AFFILIATE, {f'services_usd_{P}': '9.00'}, {'general_class_sums', 'general_rows_and_dollars_agree'}),
    (ip.GENERAL, AFFILIATE, {f'general_payment_rows_{P}': '2.0'}, {'cells_well_formed'}),
    (ip.GENERAL, AFFILIATE, {f'general_payments_usd_{P}': '525.1'}, {'cells_well_formed'}),
    (ip.DETAIL, '4', {'program_year': '2020'}, {'detail_fields_consistent'}),
    (ip.DETAIL, '4', {'nature_group': 'honoraria'}, {'detail_fields_consistent'}),
    (ip.DETAIL, '2', {'cms_profile_id': '78'}, {'detail_fields_consistent'}),
    (ip.DETAIL, '1', {'direct_payment': 'yes'}, {'cells_well_formed'}),
    (ip.GENERAL, CORE, {f'services_usd_{P}': '2400.00', f'hospitality_education_usd_{P}': '140.00'}, {'general_class_sums', 'general_rules_nested', 'core_classes_equal_2026_11_11'}),
    (ip.GENERAL, CORE, {f'cart_only_direct_usd_{P}': '1.00'}, {'general_direct_plus_third_party', 'general_rules_nested', 'general_equals_row_level_table'}),
    (ip.GENERAL, UNPAID, {f'general_payment_rows_{P}': ''}, {'cells_well_formed'}),
    (ip.RESEARCH, REFERRING, {f'research_investigator_rows_{P}': '1'}, {'research_arithmetic'}),
    (ip.RESEARCH, UNPAID, {f'research_record_usd_{P}': '0.00'}, {'research_arithmetic'}),
    (ip.DIVISION, 'cart_only|core|services', {'direct_usd': '1.00'}, {'division_equals_row_level_table', 'division_direct_plus_third_party'}),
    (ip.CENTER, '010033', {f'grant_usd_{P}': '5.00'}, {'center_arithmetic'}),
    (ip.CENTER, '050224', {'general_data_status': 'published_records'}, {'center_arithmetic'}),
    (ip.CENTER, '010033', {f'general_payments_usd_{ip.RECENT}': '103070.01'}, {'center_arithmetic'}),
    (ip.CENTER, '050224', {'research_data_status': 'unknown'}, {'center_arithmetic'}),
])
def test_check_catches_a_broken_table(tmp_path, table, key, change, expected):
    built = tables()
    built[table] = [{**row, **change} if '|'.join(str(row[k]) for k in ip.KEYS[table]) == key else row for row in built[table]]
    write_tree(tmp_path, built)
    assert failing(tmp_path) - SCALE_ONLY == expected


def test_fingerprint_ignores_row_order_and_sees_swapped_or_changed_cells():
    rows = [{'npi': CORE, 'rows': '2', 'usd': '5.00'}, {'npi': AFFILIATE, 'rows': '1', 'usd': '7.00'}]
    assert ip.fingerprint(rows, ['npi']) == ip.fingerprint(rows[::-1], ['npi'])
    swapped = [{**rows[0], 'npi': AFFILIATE}, {**rows[1], 'npi': CORE}]
    assert ip.fingerprint(swapped, ['npi']) != ip.fingerprint(rows, ['npi'])
    assert ip.fingerprint([rows[0], {**rows[1], 'usd': '7.01'}], ['npi']) != ip.fingerprint(rows, ['npi'])
    assert ip.fingerprint(rows[:1], ['npi']) != ip.fingerprint(rows, ['npi'])
    assert set(ip.FINGERPRINTS) == set(ip.TABLES) and all(len(value) == 64 for value in ip.FINGERPRINTS.values())


def test_check_compares_every_table_with_its_fingerprint(tmp_path, monkeypatch):
    built = tables()
    write_tree(tmp_path, built)
    assert 'table_fingerprints' in failing(tmp_path)
    monkeypatch.setattr(ip, 'FINGERPRINTS', {table: ip.fingerprint(read_csv(tmp_path / ip.SNAPSHOT / f'{table}.csv'), ip.KEYS[table]) for table in ip.TABLES})
    assert failing(tmp_path) == SCALE_ONLY - {'table_fingerprints'}
    # Two people without CAR-T records exchange their all-records values: no arithmetic check can see it, the fingerprint does.
    rows = read_csv(tmp_path / ip.SNAPSHOT / f'{ip.RESEARCH}.csv')
    first, second = (next(r for r in rows if r['npi'] == npi) for npi in (REFERRING, UNPAID))
    first['npi'], second['npi'] = UNPAID, REFERRING
    write_csv(tmp_path / ip.SNAPSHOT / f'{ip.RESEARCH}.csv', rows)
    assert failing(tmp_path) == SCALE_ONLY


def test_every_published_column_has_a_format_or_is_free_text():
    kinds = {c['column']: bool(ip.MONEY.search(c['column'])) + bool(ip.COUNT.search(c['column'])) for table in ip.TABLES for c in ip.COLUMNS[table]()}
    assert max(kinds.values()) == 1
    assert {name for name, kind in kinds.items() if 'usd' in name and not kind} == set()
    assert not {'cms_profile_id', 'manufacturer', 'products', 'teaching_hospital_name'} & {name for name, kind in kinds.items() if kind}


def test_check_catches_missing_universe_and_center_rows(tmp_path):
    built = tables()
    built[ip.GENERAL] = built[ip.GENERAL][:-1]
    built[ip.CENTER] = built[ip.CENTER][:-1]
    write_tree(tmp_path, built)
    assert {'general_universe_coverage', 'center_key'} <= failing(tmp_path)


def test_published_tables_pass_every_check_when_present():
    processed = Path(__file__).resolve().parents[1] / 'Data' / 'processed'
    if not all((processed / ip.SNAPSHOT / f'{table}.csv').is_file() for table in ip.TABLES):
        pytest.skip('2026-11-19 industry payment tables are not built')
    assert {name: errors for name, errors in ip.check(processed).items() if errors} == {}
    cells = {(r['rule'], r['division_group'], r['nature_class']): r for r in read_csv(processed / ip.SNAPSHOT / f'{ip.DIVISION}.csv')}
    assert [(cells['cart_only', group, 'services']['direct_npis_paid'], round(float(cells['cart_only', group, 'services']['direct_usd'])))
            for group in ['core', 'affiliates', 'referring']] == [('200', 4944608), ('338', 4475902), ('148', 727655)]
