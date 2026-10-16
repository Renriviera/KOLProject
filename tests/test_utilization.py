"""Check the interpretation risks in aggregated claims features."""
from decimal import Decimal
import pytest
from kolproject.utilization import summarize, estimated_payment, build_detail

HCP = {'npi': '1234567890', 'hcp_id': 'NPI1234567890'}
QUERY = {'query_status': 'complete', 'query_completed_at_utc': '2026-10-15T23:40:00Z',
         'sources': [{'retrieved_at_utc': '2026-10-15T23:39:59Z', 'source_id': 'test-source'}]}

def row(code='J9312', group='rituximab_iv', units='100', beneficiaries='15', days='20', payment='12.345'):
    return {'hcpcs_code': code, 'feature_group': group, 'selected_code': 1, 'selected_drug_code': 1,
            'place_of_service': 'F', 'cms_provider_type': 'Hematology-Oncology',
            'services_or_units': units, 'beneficiaries': beneficiaries, 'beneficiary_days': days,
            'average_medicare_payment_usd': payment, 'average_allowed_amount_usd': '15.00'}

def test_absent_published_records_are_not_zero_utilization():
    f = summarize(HCP, [], QUERY, '2026-10-15T23:41:00Z', 'test-hash')
    assert f['utilization_data_status'] == 'no_published_services'
    assert f['has_any_published_service'] == 0
    assert f['published_service_rows'] == 0  # Count of released records, not patient activity.
    assert f['all_published_medicare_payment_estimate_usd'] == ''
    assert f['rituximab_iv_services_or_units_sum'] == ''

def test_incomplete_query_cannot_be_reported_as_absence():
    with pytest.raises(ValueError, match='Incomplete'):
        summarize(HCP, [], {**QUERY, 'query_status': 'failed'}, '2026-10-15T23:41:00Z', 'test')

def test_patient_counts_are_not_added_as_unique_patients():
    rows = [row(), row(code='Q5115', beneficiaries='18', units='200', days='25')]
    f = summarize(HCP, rows, QUERY, '2026-10-15T23:41:00Z', 'test')
    assert f['rituximab_iv_max_beneficiaries_in_one_cell'] == '18'
    assert f['rituximab_iv_beneficiary_code_setting_days_sum'] == '45'
    assert 'total_unique_beneficiaries' not in f
    assert f['rituximab_iv_services_or_units_sum'] == '300'

def test_incompatible_drug_units_stay_separate():
    rows = [row(), row(code='J9349', group='tafasitamab', units='250')]
    f = summarize(HCP, rows, QUERY, '2026-10-15T23:41:00Z', 'test')
    assert f['rituximab_iv_services_or_units_sum'] == '100'
    assert f['tafasitamab_services_or_units_sum'] == '250'
    assert 'all_drug_services_sum' not in f

def test_payment_estimate_uses_units_and_retains_observed_zero():
    assert estimated_payment([row(units='2.5', payment='1.234')]) == '3.09'
    assert estimated_payment([row(payment='0')]) == '0.00'
    assert estimated_payment([]) == ''
