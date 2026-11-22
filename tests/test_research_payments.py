"""Matching and expansion rules for Open Payments research records."""
from kolproject.research_payments import cart_products, expand, features, parse_date, YEARS

NPIS = {'1111111111': 'NPI1111111111', '2222222222': 'NPI2222222222'}
SOURCE = {'member': 'OP_DTL_RSRCH_PGYR2024.csv', 'retrieved_at_utc': '2026-11-12T00:00:00Z'}

def record(**overrides):
    row = {'record_id': '9', 'program_year': '2024', 'date_of_payment': '03/05/2024',
           'total_amount_of_payment_usdollars': '1500.50', 'covered_recipient_type': 'Non-covered Recipient Entity',
           'noncovered_recipient_entity_name': 'Example Cancer Center', 'teaching_hospital_name': '',
           'teaching_hospital_ccn': '', 'recipient_state': 'NY', 'covered_recipient_npi': '',
           'covered_recipient_first_name': '', 'covered_recipient_last_name': '',
           'applicable_manufacturer_or_applicable_gpo_making_payment_name': 'Kite Pharma, Inc.',
           'applicable_manufacturer_or_applicable_gpo_making_payment_id': '1',
           'submitting_applicable_manufacturer_or_applicable_gpo_name': 'Gilead Sciences, Inc.',
           'name_of_study': '', 'clinicaltrials_gov_identifier': '', 'preclinical_research_indicator': 'No',
           'context_of_research': '', 'form_of_payment_or_transfer_of_value': 'Cash or cash equivalent',
           'delay_in_publication_indicator': 'No', 'dispute_status_for_publication': 'No', 'change_type': 'NEW',
           'payment_publication_date': '06/30/2026'}
    for k in range(1, 6):
        row.update({f'principal_investigator_{k}_npi': '', f'principal_investigator_{k}_last_name': '',
                    f'name_of_drug_or_biological_or_device_or_medical_supply_{k}': '',
                    f'product_category_or_therapeutic_area_{k}': ''})
    for k in range(1, 7):
        row[f'expenditure_category{k}'] = ''
    return {**row, **overrides}

def test_brand_generic_and_development_names_map_to_one_brand():
    assert cart_products('Yescarta') == ['YESCARTA']
    assert cart_products('AXICABTAGENE CILOLEUCEL', 'KTE-X19') == ['YESCARTA', 'TECARTUS']
    assert cart_products('bb2121 in myeloma') == ['ABECMA']
    assert cart_products('JNJ-68284528', 'cilta-cel') == ['CARVYKTI']
    assert cart_products('Rituximab', '') == []

def test_one_row_per_named_core_hcp_with_slots():
    rows = expand(record(principal_investigator_2_npi='1111111111', principal_investigator_4_npi='2222222222',
                         principal_investigator_1_last_name='OTHER'), NPIS, SOURCE)
    assert [(r['npi'], r['pi_slots']) for r in rows] == [('1111111111', '2'), ('2222222222', '4')]
    assert all(r['record_core_hcp_count'] == 2 and r['record_pi_count'] == 3 and r['amount_usd'] == 1500.5 for r in rows)
    assert rows[0]['payment_date'] == '2024-03-05' and rows[0]['recipient_name'] == 'Example Cancer Center'

def test_non_cohort_record_yields_nothing_and_covered_recipient_matches():
    assert expand(record(principal_investigator_1_npi='3333333333'), NPIS, SOURCE) == []
    rows = expand(record(covered_recipient_npi='1111111111', covered_recipient_type='Covered Recipient Physician'), NPIS, SOURCE)
    assert len(rows) == 1 and rows[0]['is_covered_recipient'] == 1 and rows[0]['pi_slots'] == ''

def test_product_flag_reads_every_slot_and_text_flag_is_separate():
    slot = expand(record(principal_investigator_1_npi='1111111111',
                         name_of_drug_or_biological_or_device_or_medical_supply_4='ABECMA'), NPIS, SOURCE)[0]
    assert (slot['cart_product_mentioned'], slot['lymphoma_cart_product_mentioned'], slot['cart_products']) == (1, 0, 'ABECMA')
    text = expand(record(principal_investigator_1_npi='1111111111',
                         name_of_study='A Study of Axicabtagene Ciloleucel in Lymphoma'), NPIS, SOURCE)[0]
    assert (text['cart_product_mentioned'], text['cart_text_match'], text['cart_products']) == (0, 1, 'YESCARTA')
    plain = expand(record(principal_investigator_1_npi='1111111111', name_of_study='Cartilage repair'), NPIS, SOURCE)[0]
    assert plain['cart_text_match'] == 0

def test_features_keep_zero_counts_and_blank_amounts_for_unobserved_hcps():
    empty = features('NPI1111111111', '1111111111', [], 'now')
    assert empty['research_data_status'] == 'no_published_records'
    assert empty['research_rows_2019_2025'] == 0 and empty['research_record_usd_2019_2025'] == ''
    rows = expand(record(principal_investigator_1_npi='1111111111',
                         name_of_drug_or_biological_or_device_or_medical_supply_1='Yescarta',
                         clinicaltrials_gov_identifier='NCT00000001'), NPIS, SOURCE)
    full = features('NPI1111111111', '1111111111', rows, 'now')
    assert full['research_rows_2024'] == 1 and sum(full[f'research_rows_{y}'] for y in YEARS) == 1
    assert full['cart_research_rows_2019_2025'] == 1 and full['cart_research_nct_ids'] == 'NCT00000001'
    assert full['research_sole_pi_rows'] == 1 and parse_date('') == ''
