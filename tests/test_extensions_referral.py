"""Link rule, gained and lost links, concentration, de-duplication, and check() on a small synthetic processed tree."""
from collections import defaultdict

from kolproject import careset
from kolproject.common import write_csv
from kolproject.extensions import referral as r

A, B, C, D, E = '1000000001', '1000000002', '1000000003', '1000000004', '1000000005'
ORG1, ORG2, OUTSIDE = '1900000001', '1900000002', '1555555555'
PEOPLE = [{'npi': A, 'division': 'referring_proxy', 'hrr': '10', 'hospital_ccns': ''},
          {'npi': B, 'division': 'affiliate_A', 'hrr': '20', 'hospital_ccns': '990001;990002;123456'},
          {'npi': C, 'division': 'core', 'hrr': '10', 'hospital_ccns': '990001'},
          {'npi': D, 'division': 'hemonc_other', 'hrr': '', 'hospital_ccns': ''},
          {'npi': E, 'division': 'referring_proxy', 'hrr': '30', 'hospital_ccns': ''}]
CENTERS = [{'ccn': '990001', 'hrr': '10', 'organization_npi': ORG1},
           {'ccn': '990002', 'hrr': '20', 'organization_npi': ORG2}]
META = {y: {'sha256': 'ab' * 32, 'ingested_at_utc': '2026-01-01T00:00:00Z'} for y in r.YEARS}


def edge(a, b, patients, wait='10.0'):
    return {'from_npi': a, 'to_npi': b, 'patient_count': str(patients), 'average_day_wait': wait}


EDGES = {2017: [edge(A, ORG1, 20, '10.0'), edge(A, B, 30, '20.0'), edge(D, C, 12), edge(A, OUTSIDE, 50)],
         2018: [edge(A, ORG1, 25), edge(A, ORG2, 11)],
         2019: [edge(D, C, 15), edge(A, OUTSIDE, 40)],
         2020: [edge(A, B, 40)], 2021: [edge(A, B, 40), edge(E, C, 11)], 2022: [edge(A, B, 40)]}
PAIRS = [{'npi1': A, 'npi2': ORG1, 'paircount': '100', 'benecount': '20', 'samedaycount': '2'},
         {'npi1': A, 'npi2': OUTSIDE, 'paircount': '90', 'benecount': '30', 'samedaycount': '0'}]
RECORD = {'sha256': 'cd' * 32, 'retrieved_at_utc': '2026-11-20T00:00:00Z'}


def tree(tmp_path):
    """Write a 2026-10-24 tree (features and flows from careset.aggregate) and the module's tables built on it."""
    earlier = tmp_path / r.UNIVERSE_SNAPSHOT
    write_csv(earlier / 'hcp_divisions.csv', PEOPLE)
    write_csv(earlier / 'cart_centers.csv', CENTERS)
    ctx = r.context(earlier)
    panel, recon = defaultdict(lambda: [0, 0.0, 0, 0.0]), defaultdict(lambda: [0, set(), []])
    sends, core = set(), {}
    for year, edges in EDGES.items():
        features, flows = careset.aggregate(edges, ctx, year, 'annual')
        write_csv(earlier / f'shared_patient_annual_{year}_features.csv', features,
                  ['npi', 'division', 'year', 'kind', 'out_patients_total', 'out_patients_to_centers',
                   'distinct_center_partners', 'mean_day_wait_to_centers', 'in_patients_total',
                   'in_patients_from_referrers', 'referrer_sources', 'observed_shared_patient_edge'])
        write_csv(earlier / f'shared_patient_annual_{year}_hrr_center_flows.csv', flows,
                  ['hrr', 'ccn', 'year', 'kind', 'patients', 'referrer_pairs', 'same_hrr'])
        sends |= {f['npi'] for f in features if f['out_patients_total']}
        r.accumulate(r.core_pairs(edges, ctx, year, core), ctx, year, panel, recon)
    center_hrr = {c['ccn']: c['hrr'] for c in CENTERS}
    tables = r.derive(panel, ctx, center_hrr, sends, META)
    linked_2017 = {(x['npi'], x['ccn']) for x in tables[r.PANEL] if x['year'] == '2017'}
    early, late = r.baseline_rows(PAIRS, ctx, linked_2017, RECORD, 2014), r.baseline_rows(PAIRS[:1], ctx, linked_2017, RECORD, 2015)
    tables[r.BASELINE] = early + late
    tables[r.COVERAGE] = (r.coverage_rows(2014, ctx, {A, D}, {A}, early, RECORD)
                          + r.coverage_rows(2015, ctx, None, {A}, late, RECORD))
    prov = {'source': 'careset_hop_test', 'source_sha256_prefix': 'abababab', 'source_ingested_at_utc': 'x'}
    links, received, reach = r.core_tables(core, ctx, recon)
    tables[r.CORE_LINKS], tables[r.CORE_SENDERS], tables[r.CORE_REACH] = (
        [{**x, **prov} for x in rows] for rows in (links, received, reach))
    for table, rows in tables.items():
        write(tmp_path, table, rows)
    return tables, ctx, recon


def write(processed, table, rows):
    write_csv(processed / r.SNAPSHOT / f'{table}.csv', rows, [c['column'] for c in r.SPECS[table][0]])


def test_link_rule_matches_careset_features_and_splits_multi_center_receivers(tmp_path):
    tables, ctx, recon = tree(tmp_path)
    assert r.centers_of(ORG1, ctx) == {'990001'} and r.centers_of(B, ctx) == {'990001', '990002'}
    assert r.centers_of(OUTSIDE, ctx) == set() and r.centers_of(D, ctx) == set()
    cell = {(x['npi'], x['ccn'], x['year']): x for x in tables[r.PANEL]}
    first = cell[(A, '990001', '2017')]
    assert (first['patients'], first['patients_allocated'], first['receiving_npis']) == ('50', '35', '2')
    assert first['mean_day_wait_weighted'] == '16.0' and first['region_relation'] == 'inside'
    assert first['source'] == 'careset_hop_2017' and first['source_sha256_prefix'] == 'ab' * 4
    assert 'licence' not in first
    assert cell[(A, '990002', '2017')]['patients'] == '30' and cell[(A, '990002', '2017')]['patients_allocated'] == '15'
    assert r.compact(10 / 3) == '3.333' and r.compact(15.5) == '15.5' and r.compact(0.0001) == '0'
    assert cell[(D, '990001', '2017')]['region_relation'] == r.REGION[None]
    assert (A, '990001', '2019') not in cell   # the 2019 edge goes outside the centers: not observed, no zero row
    assert recon[(A, 2017)][0] == 50 and recon[(A, 2017)][1] == {'990001', '990002'}


def test_gained_lost_and_status_words():
    delta = r.changes({2017: {'x', 'y'}, 2018: {'x'}, 2020: {'z'}})
    assert delta[2017] is None and delta[2018] == (0, 1, 1) and delta[2019] == (0, 1, 0) and delta[2020] == (1, 0, 0)
    assert delta[2021] == (0, 1, 0) and delta[2022] == (0, 0, 0)
    rows = {x['year']: x for x in r.change_rows(A, {2017: {'x'}, 2019: {'x'}})}
    assert rows['2017']['links_gained'] == '' and rows['2017']['comparison_status'] == 'first_year'
    assert rows['2018']['year_status'] == 'not_observed' and rows['2018']['links_lost'] == '1'
    assert rows['2019']['links_gained'] == '1' and rows['2019']['year_status'] == 'linked'
    assert rows['2020']['links_lost'] == '1' and '2021' not in rows and '2022' not in rows   # no change: not repeated
    late = {x['year'] for x in r.change_rows(A, {2019: {'x'}})}
    assert late == {'2019', '2020'}   # 2017-2018 unlinked without loss are implied by first_year_linked


def test_concentration_index():
    assert r.hhi([5, 5]) == 0.5 and r.hhi([30]) == 1.0 and r.hhi([]) is None
    assert r.hhi([60, 20, 20]) == round(0.36 + 0.04 + 0.04, 4)


def test_summary_and_center_tables(tmp_path):
    tables, ctx, _ = tree(tmp_path)
    summary = {x['npi']: x for x in tables[r.NPI_SUMMARY]}
    a = summary[A]
    assert (a['first_year_linked'], a['last_year_linked'], a['years_linked']) == ('2017', '2022', '5')
    assert a['persistence_in_span'] == '0.833' and a['distinct_centers_ever'] == '2'
    assert a['primary_center_ccn'] == '990001' and a['primary_center_region'] == 'inside' and a['link_status'] == 'linked'
    assert summary[E]['first_year_linked'] == '2021'
    assert summary[B]['link_status'] == r.NPI_STATUSES[2] and summary[B]['years_linked'] == ''
    base = {(x['ccn'], x['year']): x for x in tables[r.CENTER_BASE]}
    assert base[('990002', '2019')]['year_status'] == r.CENTER_NOT_OBSERVED
    assert base[('990002', '2019')]['referrers'] == '' and base[('990002', '2019')]['lost_referrers'] == '1'
    assert base[('990001', '2021')]['referrers'] == '2' and base[('990001', '2021')]['referrer_hhi'] == '0.6617'
    assert base[('990001', '2021')]['new_referrers'] == '1' and base[('990001', '2021')]['outside_hrr_patient_share'] == '0.216'
    baseline = {(x['npi'], x['ccn'], x['year']): x for x in tables[r.BASELINE]}
    assert set(baseline) == {(A, '990001', '2014'), (A, '990001', '2015')}
    assert baseline[(A, '990001', '2014')]['careset_2017_link'] == 'linked_2017'
    assert baseline[(A, '990001', '2015')]['source'] == 'cms_pspp_2015_180'
    coverage = {(x['year'], x['division_group']): x for x in tables[r.COVERAGE]}
    referring = coverage[('2014', 'referring')]
    assert (referring['npis_in_group'], referring['npis_as_npi1'], referring['npis_linked']) == ('2', '1', '1')
    assert referring['linked_share_of_covered'] == '1.000' and referring['coverage_status'] == 'measured'
    assert coverage[('2014', 'other')]['npis_as_npi1'] == '1' and coverage[('2014', 'other')]['npis_linked'] == '0'
    unmeasured = coverage[('2015', 'referring')]
    assert unmeasured['coverage_status'] == 'not_measured' and unmeasured['npis_as_npi1'] == ''
    assert unmeasured['linked_share_of_covered'] == '' and unmeasured['npis_as_npi1_kept'] == '1'


def test_deduplicate_drops_published_and_internal_duplicates(tmp_path):
    rows = [{'npi': A, 'ccn': '990001', 'year': '2017'}, {'npi': A, 'ccn': '990001', 'year': '2017'},
            {'npi': B, 'ccn': '990001', 'year': '2017'}]
    write_csv(tmp_path / r.EARLIER_SNAPSHOTS[-1] / f'{r.PANEL}.csv', [rows[2]])
    kept, counts = r.deduplicate(r.PANEL, rows, processed=tmp_path)
    assert kept == [rows[0]] and counts == {'candidate_rows': 3, 'dropped_already_published': 1,
                                            'dropped_internal_duplicates': 1}


def test_check_passes_and_catches_broken_tables(tmp_path, monkeypatch):
    tables, ctx, _ = tree(tmp_path)
    monkeypatch.setattr(r, 'NBER_YEARS', [2014, 2015])
    monkeypatch.setattr(r, 'EXPECTED_HEADLINE', r.headline(tables, ctx))
    monkeypatch.setattr(r, 'FINGERPRINTS', {t: r.sha(tmp_path / r.SNAPSHOT / f'{t}.csv') for t in r.TABLES})
    result = r.check(tmp_path)
    assert result and all(k.startswith('referral_') for k in result)
    assert not any(result.values()), {k: v for k, v in result.items() if v}
    broken = [dict(x) for x in tables[r.PANEL]]
    broken[0]['patients'] = '5'
    write(tmp_path, r.PANEL, broken + [broken[1]])
    result = r.check(tmp_path)
    assert result['referral_duplicate_keys'] == 1 and result['referral_below_floor'] >= 1
    assert result['referral_hrr_flows_patients'] >= 1 and result['referral_headline'] >= 1
    assert result['referral_fingerprint'] == 1
    write(tmp_path, r.PANEL, tables[r.PANEL])
    summary = [dict(x) for x in tables[r.NPI_SUMMARY]]
    summary[0]['primary_center_share'] = '0.100'
    summary[1]['source'] = ''
    write(tmp_path, r.NPI_SUMMARY, summary)
    result = r.check(tmp_path)
    assert result['referral_summary_recompute'] >= 1 and result['referral_blank_provenance'] == 1
    coverage = [dict(x) for x in tables[r.COVERAGE]]
    coverage[0]['npis_as_npi1'] = '0' if coverage[0]['npis_as_npi1'] == '' else ''
    write(tmp_path, r.COVERAGE, coverage)
    assert r.check(tmp_path)['referral_coverage_recompute'] >= 1
    (tmp_path / r.SNAPSHOT / f'{r.GROUPS}.csv').unlink()
    assert r.check(tmp_path)['referral_tables_missing'] == 1


def test_check_flags_identifying_columns_and_wrong_region(tmp_path, monkeypatch):
    tables, ctx, _ = tree(tmp_path)
    monkeypatch.setattr(r, 'NBER_YEARS', [2014, 2015])
    monkeypatch.setattr(r, 'EXPECTED_HEADLINE', r.headline(tables, ctx))
    monkeypatch.setattr(r, 'FINGERPRINTS', {t: r.sha(tmp_path / r.SNAPSHOT / f'{t}.csv') for t in r.TABLES})
    panel = [{**x, 'city': 'Springfield'} for x in tables[r.PANEL]]
    panel[0]['region_relation'] = 'outside' if panel[0]['region_relation'] != 'outside' else 'inside'
    write_csv(tmp_path / r.SNAPSHOT / f'{r.PANEL}.csv', panel)
    result = r.check(tmp_path)
    assert result['referral_identifying_columns'] == 1 and result['referral_panel_region_recompute'] == 1


def test_core_hcp_tables_and_their_agreement_with_features(tmp_path, monkeypatch):
    tables, ctx, recon = tree(tmp_path)
    links = {(x['npi'], x['year']): x for x in tables[r.CORE_LINKS]}
    assert set(links) == {(D, '2017'), (D, '2019'), (E, '2021')}
    assert links[(D, '2017')]['patients_to_core_at_centers'] == '12'
    assert links[(D, '2017')]['share_of_center_patients_via_core'] == '1.000'
    received = {(x['npi'], x['year']): x for x in tables[r.CORE_SENDERS]}
    assert set(received) == {(C, '2017'), (C, '2018'), (C, '2019'), (C, '2020'), (C, '2021'), (C, '2022')}
    assert received[(C, '2018')]['year_status'] == 'not_observed' and received[(C, '2018')]['senders_lost'] == '1'
    assert received[(C, '2018')]['patients_from_universe'] == ''
    e2021 = received[(C, '2021')]
    assert (e2021['senders'], e2021['patients_from_universe'], e2021['patients_from_other']) == ('1', '11', '0')
    assert e2021['outside_hrr_patient_share'] == '1.000' and e2021['sender_hhi'] == '1.0000'
    reach = {(x['year'], x['division_group']): x for x in tables[r.CORE_REACH]}
    assert reach[('2017', 'other')]['senders_to_core'] == '1' and reach[('2021', 'referring')]['patients_to_core'] == '11'
    monkeypatch.setattr(r, 'NBER_YEARS', [2014, 2015])
    monkeypatch.setattr(r, 'EXPECTED_HEADLINE', r.headline(tables, ctx))
    monkeypatch.setattr(r, 'FINGERPRINTS', {t: r.sha(tmp_path / r.SNAPSHOT / f'{t}.csv') for t in r.TABLES})
    assert not any(r.check(tmp_path).values())
    broken = [dict(x) for x in tables[r.CORE_SENDERS]]
    row = next(x for x in broken if x['year'] == '2017')
    row['patients_from_other'] = str(int(row['patients_from_other']) - 1)   # moves one patient to the referring group
    write(tmp_path, r.CORE_SENDERS, broken)
    result = r.check(tmp_path)
    assert result['referral_core_vs_features'] >= 1 and result['referral_fingerprint'] == 1
