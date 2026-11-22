"""Registry matching, remedy parsing, orphan-list fixes and table checks for the 340B status module."""
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from kolproject.common import ROOT, read_csv, write_csv
from kolproject.volume_billing import status_340b as m

REAL = ROOT / 'Data' / 'processed'


def rec(ident, ccn, kind='DSH', parent='', participating=True, start='2015-01-01', end='', site=''):
    return {'id': ident, 'type': kind, 'ccn': ccn, 'parent_id': parent, 'participating': participating,
            'start': start, 'end': end, 'end_reason': '', 'name': 'HOSPITAL', 'site_name': site}


def manual(route, evidence='', named='', note='no record'):
    return {'determination': route, 'evidence_340b_id': evidence, 'sibling_site_names_cell_therapy': named,
            'status_note': note}


def test_slim_keeps_matching_fields_and_leaves_contacts_and_addresses_behind():
    raw = {'id340B': 'DSh450054BK', 'entityType': 'DSH', 'medicareProviderNumber': '450054', 'parentId340B': 'DSH450054',
           'participating': 'TRUE', 'participatingStartDate': '2014-07-01T00:00:00', 'name': 'HOSPITAL',
           'subName': 'Clinic', 'streetAddress': {'addressLine1': '1 Main St'}, 'primaryContact': {'name': 'A Person'}}
    out = m.slim(raw)
    assert out == rec('DSh450054BK', '450054', parent='DSH450054', start='2014-07-01', site='Clinic')
    ended = m.slim({**raw, 'participating': 'FALSE', 'terminationDate': '2019-01-01T00:00:00', 'terminationReason': 'Other'})
    assert (ended['participating'], ended['end'], ended['end_reason']) == (False, '2019-01-01', 'Other')


def test_direct_center_is_matched_on_provider_number_not_on_the_id_text():
    records = [rec('DSH450054', '450054'), rec('DSh450054BK', '450054', parent='DSH450054'),
               rec('DSH450054A', '450054', parent='DSH450054', participating=False, end='2020-01-01')]
    row = m.position('450054', records, None, {})
    assert (row['registry_status_at_ccn'], row['route_340b'], row['class_340b']) == ('active', 'direct', 'A')
    assert (row['evidence_340b_id'], row['evidence_ccn'], row['evidence_start_date']) == ('DSH450054', '450054', '2015-01-01')
    assert (row['n_registry_records_active'], row['n_registry_records_total']) == (2, 3)
    assert row['scheduled_registry_change'] == '' and row['status_note'] == '' and row['sibling_site_names_cell_therapy'] == ''
    with pytest.raises(AssertionError):
        m.position('450054', records, manual('outside'), {})


def test_scheduled_changes_name_endings_successors_and_ignore_void_records():
    ending = rec('DSH310001', '310001', end='2026-07-01')
    assert m.position('310001', [ending], None, {})['scheduled_registry_change'] == '2026-07-01: DSH record ends; no successor record'
    switch = [rec('DSH520037', '520037', end='2026-07-01'),
              rec('SCH520037-00', '520037', kind='SCH', participating=False, start='2026-07-01')]
    row = m.position('520037', switch, None, {})
    assert row['scheduled_registry_change'] == '2026-07-01: DSH record ends; SCH record starts'
    assert row['registry_entity_types_ever'] == 'DSH|SCH' and row['class_340b'] == 'A'
    void = [rec('DSH030122', '030122'),
            rec('RRC030122-00', '030122', kind='RRC', participating=False, start='2026-07-01', end='2026-07-01')]
    assert m.pending_parents(void) == [] and m.position('030122', void, None, {})['scheduled_registry_change'] == ''


def test_pending_record_is_found_by_rule_and_must_agree_with_the_manual_row():
    records = [rec('DSH170122', '170122', participating=False, start='2012-01-01', end='2026-04-01'),
               rec('RRC170122-00', '170122', kind='RRC', participating=False, start='2026-07-01')]
    row = m.position('170122', records, manual('pending_direct', 'RRC170122-00', note='RRC record, start 2026-07-01'), {})
    assert (row['registry_status_at_ccn'], row['route_340b'], row['entity_type_340b'], row['class_340b']) == (
        'inactive', 'pending_direct', 'RRC', 'B')
    assert row['evidence_start_date'] == '2026-07-01' and row['scheduled_registry_change'] == '2026-07-01: RRC record starts'
    with pytest.raises(AssertionError):  # an outside determination cannot stand beside a future-dated parent record
        m.position('170122', records, manual('outside'), {})
    with pytest.raises(AssertionError):  # a center without a participating parent needs a manual row
        m.position('170122', records, None, {})


def test_sibling_and_outside_rows_come_from_the_manual_file_and_are_checked_against_the_registry():
    parent, site = rec('DSH110078', '110078'), rec('DSH110078AE', '110078', parent='DSH110078', start='2015-04-01', site='BMT Clinic')
    by_id = {r['id']: r for r in (parent, site)}
    row = m.position('110010', [], manual('sibling', 'DSH110078AE', '1', 'other hospital, start 2015-04-01'), by_id)
    assert (row['registry_status_at_ccn'], row['route_340b'], row['class_340b']) == ('not_registered', 'sibling', 'A')
    assert (row['evidence_ccn'], row['evidence_start_date'], row['sibling_site_names_cell_therapy']) == ('110078', '2015-04-01', '1')
    assert row['registry_entity_types_ever'] == '' and row['n_registry_records_total'] == 0
    outside = m.position('100271', [], manual('outside'), by_id)
    assert (outside['route_340b'], outside['entity_type_340b'], outside['class_340b'], outside['evidence_340b_id']) == (
        'outside', '', 'C', '')
    assert outside['status_note'] == 'no record'
    by_id['DSH110078AE'] = {**site, 'participating': False}
    with pytest.raises(AssertionError):
        m.position('110010', [], manual('sibling', 'DSH110078AE', '1', 'x'), by_id)
    with pytest.raises(AssertionError):  # the flag belongs to sibling rows only
        m.position('100271', [], manual('outside', named='0'), by_id)


def test_class_follows_entity_type_and_alternative_as_of_rules():
    assert [m.class_340b(t) for t in ('DSH', 'PED', 'CAN', 'RRC', 'SCH', 'CAH', '')] == list('AABBBBC')
    base = {'class_340b': 'A', 'route_340b': 'direct', 'scheduled_registry_change': ''}
    leaving = {**base, 'scheduled_registry_change': '2026-07-01: DSH record ends; no successor record'}
    switching = {**base, 'scheduled_registry_change': '2026-07-01: DSH record ends; SCH record starts'}
    pending = {'class_340b': 'B', 'route_340b': 'pending_direct', 'scheduled_registry_change': '2026-07-01: RRC record starts'}
    sibling = {**base, 'route_340b': 'sibling'}
    table = {row_name: [m.class_under(row, rule) for rule in m.AS_OF_RULES] for row_name, row in
             [('plain', base), ('leaving', leaving), ('switching', switching), ('pending', pending), ('sibling', sibling)]}
    assert table == {'plain': list('AAAA'), 'leaving': list('AACA'), 'switching': list('AABA'),
                     'pending': list('BCBC'), 'sibling': list('AAAC')}


SHEET = ['\t"Addendum AAA\n\n* note"\t\t\t\t\t\t\t\t\r\n',
         '\t'.join(m.SHEET_HEADER) + '\r\n',
         '\tTotal:\t"$1,000.00"\t$0.00\t$0.00\t$0.00\t$5.50\t"$1,005.50"\t$20.00\t$14.50\r\n',
         '010001\tA HOSPITAL\t"$1,000.00"\t$0.00\t$0.00\t$0.00\t$5.50\t"$1,005.50"\t$20.00\t$14.50\r\n',
         '010006\tB HOSPITAL\t$0.00\t$0.00\t$0.00\t$0.00\t$0.00\t$0.00\t$0.00\t$0.00\r\n']


def test_remedy_sheet_reads_amounts_by_header_position_and_keeps_zero_apart_from_blank():
    providers, total = m.remedy_sheet(SHEET)
    assert set(providers) == {'010001', '010006'} and total['remedy_total_2018_2022_usd'] == Decimal('1005.50')
    listed = m.remedy_columns(providers['010001'])
    assert listed == {'in_cms_340b_remedy_file': 1, 'remedy_2018_usd': '1000.00', 'remedy_2019_usd': '0.00',
                      'remedy_2020_usd': '0.00', 'remedy_2021_usd': '0.00', 'remedy_2022_withheld_usd': '20.00',
                      'remedy_2022_net_usd': '5.50', 'remedy_total_2018_2022_usd': '1005.50'}
    assert m.remedy_columns(providers['010006'])['remedy_total_2018_2022_usd'] == '0.00'
    absent = m.remedy_columns(None)
    assert absent['in_cms_340b_remedy_file'] == 0 and all(absent[name] == '' for name in m.REMEDY_COLUMNS)
    assert m.dollars('.') is None and m.cents(None) == '' and m.cents(Decimal('0')) == '0.00'
    with pytest.raises(AssertionError):  # provider rows before the expected header mean the layout changed
        m.remedy_sheet(SHEET[3:])


def test_exempt_cancer_hospital_ccns_are_read_from_the_page_table():
    page = ('<tr><th>05-0146</th><td>City of Hope</td></tr><tr><th>&nbsp;&nbsp;36-0242&nbsp;</th><td>James</td></tr>'
            '<p>P.L. 98-21 and 101-239</p><td>10-0079</td>')
    assert m.pps_exempt_ccns(page) == ['050146', '100079', '360242']


COMPILED = [
    {'product': 'Breyanzi', 'generic': 'lisocabtagene maraleucel', 'designated_indication': 'DLBCL',
     'designation_date': '2016-04-27', 'evidence_document': 'FDA SBRA 2021', 'evidence_url': 'https://www.fda.gov/media/1/download',
     'evidence_quote': 'DLBCL on 04/27/2016, follicular lymphoma on 09/07/2017'},
    {'product': 'Breyanzi', 'generic': 'lisocabtagene maraleucel', 'designated_indication': 'Follicular lymphoma',
     'designation_date': '2017-09-07', 'evidence_document': 'FDA SBRA 2021; approval letter',
     'evidence_url': 'https://www.fda.gov/media/2/download', 'evidence_quote': 'same sentence; letter repeats orphan statement'},
    {'product': 'Tecartus', 'generic': 'brexucabtagene autoleucel', 'designated_indication': 'Mantle cell lymphoma',
     'designation_date': '2016-04', 'evidence_document': 'FDA SBRA 2020-07-23', 'evidence_url': 'https://www.fda.gov/media/3/download',
     'evidence_quote': 'granted in April 2016'},
    {'product': 'Abecma', 'generic': 'idecabtagene vicleucel', 'designated_indication': 'Multiple myeloma',
     'designation_date': 'not stated', 'evidence_document': 'FDA press release', 'evidence_url': 'https://www.fda.gov/news/x',
     'evidence_quote': 'Abecma was granted Orphan Drug and Breakthrough Therapy designations'},
]


def test_orphan_rows_fill_placeholders_apply_corrections_and_type_the_date():
    first, follicular, tecartus, abecma = m.orphan_rows(COMPILED)
    assert follicular['evidence_quote'] == first['evidence_quote']
    assert follicular['evidence_url'] == 'https://www.fda.gov/media/1/download | https://www.fda.gov/media/2/download'
    assert (tecartus['designation_date'], tecartus['designation_date_precision']) == ('2016-04-28', 'day')
    assert '28 April 2016' in tecartus['evidence_quote']
    assert (abecma['designation_date'], abecma['designation_date_precision']) == ('', 'not_stated')
    assert first['generic_name'] == 'lisocabtagene maraleucel' and 'generic' not in first
    assert m.orphan_rows(COMPILED[2:3], corrections={})[0]['designation_date_precision'] == 'month'
    with pytest.raises(AssertionError):
        m.date_precision('April 2016')


def test_quotes_are_looked_up_in_saved_documents_of_the_same_product():
    rows = m.orphan_rows(COMPILED[3:])
    texts = {'abecma_press_release.html': m.plain('<p>Abecma was granted Orphan&nbsp;Drug\n and Breakthrough Therapy designations.</p>'),
             'tecartus_2020_sbra.txt': m.plain('unrelated')}
    assert m.verify_quotes(rows, texts) == []
    assert m.verify_quotes(rows, {'tecartus_2020_sbra.txt': texts['abecma_press_release.html']}) == ['Abecma / Multiple myeloma']


def tiny_tree(tmp_path, change=None):
    """A three-center processed tree that is well formed but far smaller than the real table."""
    parent, site = rec('DSH110078', '110078'), rec('DSH110078AE', '110078', parent='DSH110078', start='2015-04-01')
    by_id = {r['id']: r for r in (parent, site)}
    providers, _ = m.remedy_sheet(SHEET)
    built = [('010001', m.position('010001', [rec('DSH010001', '010001')], None, by_id), providers['010001']),
             ('110010', m.position('110010', [], manual('sibling', 'DSH110078AE', '1', 'site, start 2015-04-01'), by_id), None),
             ('100271', m.position('100271', [], manual('outside'), by_id), providers['010006'])]
    rows = [{'ccn': ccn, **registry, 'pps_exempt_cancer_hospital': int(ccn == '100271'), **m.remedy_columns(entry),
             'registry_export_date': m.EXPORT_DATE, 'collection_snapshot_date': m.SNAPSHOT} for ccn, registry, entry in built]
    for row in rows:
        (change or (lambda r: None))(row)
    write_csv(tmp_path / m.SNAPSHOT / f'{m.STATUS_TABLE}.csv', rows, [c['column'] for c in m.status_columns()])
    write_csv(tmp_path / m.SNAPSHOT / f'{m.ORPHAN_TABLE}.csv', m.orphan_rows(COMPILED))
    write_csv(tmp_path / m.CENTER_SNAPSHOT / 'cart_centers.csv',
              [{'ccn': '010001', 'drg018_discharges_2024': '45'}, {'ccn': '110010', 'drg018_discharges_2024': '37'},
               {'ccn': '100271', 'drg018_discharges_2024': ''}])
    return m.check(tmp_path)


HEADLINE = ('_rows', '_counts', '_totals', '_sums', 'remedy_flag', 'remedy_centers', 'pps_exempt_flag',
            'scheduled_change_rows', 'orphan_rows_by_product', 'orphan_date_range', 'anchor_', 'fingerprint_')


def test_check_passes_structure_of_a_well_formed_tiny_table_and_flags_only_headline_totals(tmp_path):
    result = tiny_tree(tmp_path)
    assert all(name.startswith('status_340b_') for name in result)
    structural = {k: v for k, v in result.items() if not any(part in k for part in HEADLINE) and 'by_class' not in k}
    assert structural and not any(structural.values()), {k: v for k, v in structural.items() if v}
    assert result['status_340b_status_rows'] == 1 and result['status_340b_class_counts_reported'] > 0


@pytest.mark.parametrize('check_name, change', [
    ('class_follows_entity_type', lambda r: r.update(class_340b='B') if r['ccn'] == '010001' else None),
    ('entity_type_blank_iff_outside', lambda r: r.update(entity_type_340b='DSH', class_340b='A') if r['ccn'] == '100271' else None),
    ('evidence_ccn_matches_route', lambda r: r.update(evidence_ccn='010001') if r['ccn'] == '110010' else None),
    ('sibling_flag_iff_sibling', lambda r: r.update(sibling_site_names_cell_therapy='') if r['ccn'] == '110010' else None),
    ('status_note_presence', lambda r: r.update(status_note='') if r['ccn'] == '100271' else None),
    ('status_note_no_address_or_contact', lambda r: r.update(status_note='child site at 123 Example Rd NE') if r['ccn'] == '100271' else None),
    ('status_note_no_address_or_contact', lambda r: r.update(status_note='no child site at 900 Westlake') if r['ccn'] == '100271' else None),
    ('status_note_no_address_or_contact', lambda r: r.update(status_note='site in Anytown, VA 00000') if r['ccn'] == '100271' else None),
    ('status_note_no_address_or_contact', lambda r: r.update(status_note='ask 507-555-0100') if r['ccn'] == '100271' else None),
    ('remedy_blank_iff_not_listed', lambda r: r.update(remedy_2018_usd='0.00') if r['ccn'] == '110010' else None),
    ('remedy_year_sum_equals_total', lambda r: r.update(remedy_total_2018_2022_usd='2000.00') if r['ccn'] == '010001' else None),
    ('remedy_net_not_above_withheld', lambda r: r.update(remedy_2022_withheld_usd='1.00') if r['ccn'] == '010001' else None),
    ('record_counts', lambda r: r.update(n_registry_records_active='9') if r['ccn'] == '010001' else None),
    ('constant_dates', lambda r: r.update(registry_export_date='2026-07-01') if r['ccn'] == '010001' else None),
])
def test_check_catches_a_broken_cell(tmp_path, check_name, change):
    assert tiny_tree(tmp_path)[f'status_340b_{check_name}'] == 0
    assert tiny_tree(tmp_path, change)[f'status_340b_{check_name}'] > 0


def test_address_rule_lets_dates_ccns_ids_and_years_through():
    kept = ('Hospital (CCN 030014) registers the child site "BMT Outpatient", start 2023-06-12; site DSH110078A dates '
            'from 2007. RRC record terminated 2019-04-01 after 2018 and before 2020 rules')
    assert not (m.STREET.search(kept) or m.CONTACT.search(kept) or m.HOUSE_NUMBER.search(kept))
    for leaked in ('4500 N 1st St #300', '900 Westlake', 'Building B, Suite 4', 'Anytown GA 00000'):
        assert m.STREET.search(leaked) or m.HOUSE_NUMBER.search(leaked), leaked


def test_fingerprint_changes_when_two_rows_trade_values_but_totals_stay():
    rows = [{'ccn': '010001', 'usd': '5.00'}, {'ccn': '010006', 'usd': '7.00'}]
    traded = [{'ccn': '010001', 'usd': '7.00'}, {'ccn': '010006', 'usd': '5.00'}]
    assert m.fingerprint(rows, ['ccn', 'usd']) != m.fingerprint(traded, ['ccn', 'usd'])
    assert m.fingerprint(rows, ['ccn', 'usd']) == m.fingerprint([dict(r, other='x') for r in rows], ['ccn', 'usd'])
    assert m.fingerprint([{'a': '1', 'b': ''}], ['a', 'b']) != m.fingerprint([{'a': '', 'b': '1'}], ['a', 'b'])
    assert len(m.fingerprint(rows, ['ccn'])) == 16


def test_check_reports_missing_tables_and_wrong_columns(tmp_path):
    assert m.check(tmp_path) == {'status_340b_tables_present': 3}
    tiny_tree(tmp_path)
    path = tmp_path / m.SNAPSHOT / f'{m.STATUS_TABLE}.csv'
    write_csv(path, [{k: v for k, v in row.items() if k != 'status_note'} for row in read_csv(path)])
    assert m.check(tmp_path)['status_340b_status_columns'] == 1


@pytest.mark.skipif(not (REAL / m.SNAPSHOT / f'{m.STATUS_TABLE}.csv').is_file(), reason='published tables not built')
def test_published_tables_pass_every_check_and_a_changed_class_is_caught(tmp_path):
    result = m.check(REAL)
    assert len(result) > 40 and not any(result.values()), {k: v for k, v in result.items() if v}
    for folder in (m.SNAPSHOT, m.CENTER_SNAPSHOT):
        (tmp_path / folder).mkdir()
    for name in m.TABLES:
        shutil.copy(REAL / m.SNAPSHOT / f'{name}.csv', tmp_path / m.SNAPSHOT)
    shutil.copy(REAL / m.CENTER_SNAPSHOT / 'cart_centers.csv', tmp_path / m.CENTER_SNAPSHOT)
    path = Path(tmp_path / m.SNAPSHOT / f'{m.STATUS_TABLE}.csv')
    rows = read_csv(path)
    hackensack = next(r for r in rows if r['ccn'] == '310001')
    hackensack.update(route_340b='outside', entity_type_340b='', class_340b='C', evidence_340b_id='', evidence_ccn='',
                      evidence_start_date='', status_note='record ended')
    write_csv(path, rows)
    broken = {k for k, v in m.check(tmp_path).items() if v}
    assert {'status_340b_class_counts_reported', 'status_340b_drg018_by_class_reported', 'status_340b_route_counts',
            'status_340b_status_matches_route'} <= broken


def _real_copy(tmp_path):
    for folder in (m.SNAPSHOT, m.CENTER_SNAPSHOT):
        (tmp_path / folder).mkdir()
    for name in m.TABLES:
        shutil.copy(REAL / m.SNAPSHOT / f'{name}.csv', tmp_path / m.SNAPSHOT)
    shutil.copy(REAL / m.CENTER_SNAPSHOT / 'cart_centers.csv', tmp_path / m.CENTER_SNAPSHOT)
    return tmp_path / m.SNAPSHOT / f'{m.STATUS_TABLE}.csv', tmp_path / m.SNAPSHOT / f'{m.ORPHAN_TABLE}.csv'


def _trade(rows, first, second, names):
    a, b = (next(r for r in rows if r['ccn'] == ccn) for ccn in (first, second))
    for name in names:
        a[name], b[name] = b[name], a[name]


def _cents(rows, ccn, name, delta):
    row = next(r for r in rows if r['ccn'] == ccn)
    row[name] = f'{Decimal(row[name]) + Decimal(delta):.2f}'


SILENT_EDITS = [  # edits to the published table that leave every count and total unchanged
    ('fingerprint_remedy', lambda rows: _trade(rows, '010033', '030064', m.REMEDY_COLUMNS)),
    ('fingerprint_remedy', lambda rows: (_cents(rows, '010033', 'remedy_2018_usd', '0.01'),
                                         _cents(rows, '030064', 'remedy_2018_usd', '-0.01'))),
    ('fingerprint_registry', lambda rows: _trade(rows, '010033', '030064', ['n_registry_records_active', 'n_registry_records_total'])),
    ('fingerprint_registry', lambda rows: _trade(rows, '010033', '030064', ['evidence_start_date'])),
    ('fingerprint_notes', lambda rows: _trade(rows, '030103', '450044', ['status_note'])),
    ('anchor_pps_exempt_ccns', lambda rows: _trade(rows, '100271', '050224', ['pps_exempt_cancer_hospital'])),
    ('anchor_non_direct_evidence', lambda rows: _trade(rows, '030087', '110010', ['evidence_340b_id', 'evidence_ccn', 'evidence_start_date', 'status_note'])),
    ('types_ever_counts', lambda rows: next(r for r in rows if r['ccn'] == '010033').update(registry_entity_types_ever='DSH|RRC')),
]


@pytest.mark.skipif(not (REAL / m.SNAPSHOT / f'{m.STATUS_TABLE}.csv').is_file(), reason='published tables not built')
@pytest.mark.parametrize('check_name, edit', SILENT_EDITS)
def test_edits_that_keep_every_total_are_still_caught_on_the_published_table(tmp_path, check_name, edit):
    path, _ = _real_copy(tmp_path)
    rows = read_csv(path)
    edit(rows)
    write_csv(path, rows)
    assert m.check(tmp_path)[f'status_340b_{check_name}'] > 0


@pytest.mark.skipif(not (REAL / m.SNAPSHOT / f'{m.ORPHAN_TABLE}.csv').is_file(), reason='published tables not built')
def test_a_changed_orphan_date_inside_the_range_is_caught(tmp_path):
    _, path = _real_copy(tmp_path)
    rows = read_csv(path)
    next(r for r in rows if r['product'] == 'Kymriah' and r['designated_indication'] == 'DLBCL')['designation_date'] = '2017-08-28'
    write_csv(path, rows)
    broken = {k for k, v in m.check(tmp_path).items() if v}
    assert broken == {'status_340b_fingerprint_orphan'}
