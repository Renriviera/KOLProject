"""CareSet DocGraph ingestion: schema check, universe filter, flow aggregation."""
import gzip
import pytest
from kolproject.careset import normalize_header, filter_edges, aggregate, ingest
from kolproject.common import write_csv, read_csv

CTX = {
    'division': {'1000000001': 'referring_proxy', '1000000002': 'core', '1000000003': 'affiliate_A', '1000000004': 'hemonc_other'},
    'hrr': {'1000000001': '301', '1000000002': '303', '1000000003': '303', '1000000004': '301'},
    'center_ccns': {'1000000002': {'330154'}, '1000000003': {'330154'}},
    'org_to_ccn': {'1999999999': '050441'},
    'center_hrr': {'330154': '303', '050441': '050'},
}

def edge(a, b, patients, wait='12.0'):
    return {'FROM_NPI': a, 'TO_NPI': b, 'PATIENT_COUNT': str(patients), 'TRANSACTION_COUNT': str(patients * 2),
            'AVERAGE_DAY_WAIT': wait, 'STD_DAY_WAIT': '3.0'}

def test_header_accepts_case_and_spacing_but_requires_all_fields():
    m = normalize_header(['from_npi', 'TO_NPI', 'Patient Count', 'TRANSACTION_COUNT', 'AVERAGE_DAY_WAIT', 'STD_DAY_WAIT', 'extra'])
    assert m['Patient Count'] == 'PATIENT_COUNT' and 'extra' not in m
    with pytest.raises(ValueError, match='missing columns'):
        normalize_header(['FROM_NPI', 'TO_NPI'])

def test_filter_keeps_edges_touching_universe_or_center_organizations():
    rows = [edge('1000000001', '1000000002', 15), edge('5555555555', '6666666666', 40), edge('5555555555', '1999999999', 12)]
    kept = list(filter_edges(rows, CTX))
    assert [(e['from_npi'], e['to_npi']) for e in kept] == [('1000000001', '1000000002'), ('5555555555', '1999999999')]
    assert kept[1]['from_division'] == 'outside_universe' and kept[1]['to_division'] == 'organization'

def test_aggregate_credits_referrer_flows_to_center_ccns_and_hrr_pairs():
    rows = [edge('1000000001', '1000000002', 15), edge('1000000001', '1999999999', 11, '30.0'),
            edge('1000000004', '1000000002', 20), edge('1000000002', '1000000003', 50)]
    kept = list(filter_edges(rows, CTX))
    features, flows = aggregate(kept, CTX, 2024, 'annual')
    by = {f['npi']: f for f in features}
    assert by['1000000001']['out_patients_to_centers'] == 26
    assert by['1000000001']['distinct_center_partners'] == 2
    assert by['1000000001']['mean_day_wait_to_centers'] == 21.0
    assert by['1000000002']['in_patients_from_referrers'] == 15  # hemonc_other is not a referrer
    assert by['1000000002']['in_patients_total'] == 35
    assert by['1000000003']['in_patients_total'] == 50
    assert {(f['hrr'], f['ccn'], f['patients'], f['same_hrr']) for f in flows} == {('301', '330154', 15, 0), ('301', '050441', 11, 0)}

def test_ingest_writes_features_and_keeps_edges_local_by_default(tmp_path, monkeypatch):
    processed = tmp_path / 'processed'; processed.mkdir()
    write_csv(processed / 'hcp_divisions.csv', [
        {'npi': '1000000001', 'division': 'referring_proxy', 'hrr': '301', 'hospital_ccns': ''},
        {'npi': '1000000002', 'division': 'core', 'hrr': '303', 'hospital_ccns': '330154'}])
    write_csv(processed / 'cart_centers.csv', [{'ccn': '330154', 'organization_npi': '1888888888', 'hrr': '303'}])
    path = tmp_path / 'docgraph.csv.gz'
    with gzip.open(path, 'wt', newline='') as f:
        f.write('FROM_NPI,TO_NPI,PATIENT_COUNT,TRANSACTION_COUNT,AVERAGE_DAY_WAIT,STD_DAY_WAIT\n')
        f.write('1000000001,1000000002,15,30,12.0,3.0\n7777777777,8888888888,99,99,1.0,1.0\n')
    meta = ingest(path, 2024, 'annual', False, processed=processed, interim=tmp_path / 'interim', raw=tmp_path / 'raw')
    assert meta['rows_total'] == 2 and meta['rows_kept'] == 1 and not meta['edges_published']
    assert (tmp_path / 'interim' / 'careset' / 'shared_patient_annual_2024_edges.csv').exists()
    assert not (processed / 'shared_patient_annual_2024_edges.csv').exists()
    features = read_csv(processed / 'shared_patient_annual_2024_features.csv')
    assert {f['npi'] for f in features} == {'1000000001', '1000000002'}
    assert read_csv(processed / 'shared_patient_annual_2024_hrr_center_flows.csv')[0]['patients'] == '15'
