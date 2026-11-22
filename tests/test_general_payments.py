"""Shaping, grouping, and aggregation rules for multi-year Open Payments general records."""
from kolproject.general_payments import GROUPS, nature_group, shape, summary_row, year_row

NPIS = {'1111111111': 'NPI1111111111'}
SOURCE = {'member': 'OP_DTL_GNRL_PGYR2022.csv', 'retrieved_at_utc': '2026-11-12T00:00:00Z'}
SPEAKING = ('Compensation for services other than consulting, including serving as faculty or as a speaker '
            'at a venue other than a continuing education program')

def record(**overrides):
    row = {'record_id': '5', 'program_year': '2022', 'date_of_payment': '07/04/2022',
           'total_amount_of_payment_usdollars': '2500.00', 'number_of_payments_included_in_total_amount': '1',
           'covered_recipient_npi': '1111111111', 'covered_recipient_profile_id': '77',
           'applicable_manufacturer_or_applicable_gpo_making_payment_name': 'Kite Pharma, Inc.',
           'applicable_manufacturer_or_applicable_gpo_making_payment_id': '1',
           'submitting_applicable_manufacturer_or_applicable_gpo_name': 'Gilead Sciences, Inc.',
           'nature_of_payment_or_transfer_of_value': 'Consulting Fee',
           'form_of_payment_or_transfer_of_value': 'Cash or cash equivalent',
           'name_of_third_party_entity_receiving_payment_or_transfer_of_value': '', 'contextual_information': '',
           'delay_in_publication_indicator': 'No', 'dispute_status_for_publication': 'No', 'change_type': 'UNCHANGED',
           'payment_publication_date': '06/30/2026'}
    for k in range(1, 6):
        row.update({f'name_of_drug_or_biological_or_device_or_medical_supply_{k}': '',
                    f'product_category_or_therapeutic_area_{k}': ''})
    return {**row, **overrides}

def test_nature_groups_separate_advisory_from_promotional_speaking_and_meals():
    assert nature_group('Consulting Fee') == 'consulting'
    assert nature_group(SPEAKING) == 'speaking_non_cme'
    assert nature_group('Compensation for serving as faculty or as a speaker for a medical education program') == 'medical_education'
    assert nature_group('Food and Beverage') == 'hospitality'
    assert nature_group('Royalty or License') == 'other'
    assert set(GROUPS) >= {nature_group(x) for x in ['Honoraria', 'Grant', 'Education', 'Unknown future category']}

def test_shape_reads_all_product_slots_and_keeps_legacy_lymphoma_flag():
    carvykti = shape(record(name_of_drug_or_biological_or_device_or_medical_supply_3='CARVYKTI'), NPIS, SOURCE)
    assert (carvykti['cart_product_mentioned'], carvykti['lymphoma_cart_product_mentioned'], carvykti['cart_products']) == (1, 0, 'CARVYKTI')
    yescarta = shape(record(name_of_drug_or_biological_or_device_or_medical_supply_1='Yescarta'), NPIS, SOURCE)
    assert (yescarta['cart_product_mentioned'], yescarta['lymphoma_cart_product_mentioned']) == (1, 1)
    assert yescarta['payment_date'] == '2022-07-04' and yescarta['amount_usd'] == 2500.0 and yescarta['hcp_id'] == 'NPI1111111111'
    assert shape(record(), NPIS, SOURCE)['cart_product_mentioned'] == 0

def test_year_and_summary_rows_reconcile_and_zero_fill_scanned_years():
    rows = [shape(record(record_id='1', name_of_drug_or_biological_or_device_or_medical_supply_1='Yescarta'), NPIS, SOURCE),
            shape(record(record_id='2', total_amount_of_payment_usdollars='40', nature_of_payment_or_transfer_of_value='Food and Beverage'), NPIS, SOURCE),
            shape(record(record_id='3', total_amount_of_payment_usdollars='1000', nature_of_payment_or_transfer_of_value=SPEAKING), NPIS, SOURCE)]
    year = year_row('NPI1111111111', '1111111111', 2022, rows)
    assert year['general_payment_rows'] == 3 and year['general_payments_usd'] == 3540.0
    assert year['advisory_usd'] == 2500.0 and year['speaking_non_cme_usd'] == 1000.0 and year['hospitality_usd'] == 40.0
    assert abs(sum(year[f'{g}_usd'] for g in GROUPS) - year['general_payments_usd']) < 0.005
    assert year['cart_product_rows'] == 1 and year['cart_advisory_usd'] == 2500.0 and year['cart_products'] == 'YESCARTA'
    empty = year_row('NPI1111111111', '1111111111', 2019, [])
    assert empty['general_payment_rows'] == 0 and empty['general_payments_usd'] == 0
    summary = summary_row('NPI1111111111', '1111111111', rows, 'now')
    assert summary['general_payment_rows_2019_2025'] == 3 and summary['first_cart_payment_year'] == 2022
    assert summary['cart_advisory_manufacturers_2019_2025'] == 'Kite Pharma, Inc.'
    none = summary_row('NPI1111111111', '1111111111', [], 'now')
    assert none['general_data_status'] == 'no_published_records' and none['first_cart_payment_year'] == ''
