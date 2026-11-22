"""Publishing rule, source terms, and the folder-level checks of the 2026-11-19 snapshot."""
import shutil

import pytest

from kolproject.common import ROOT, read_csv
from kolproject.evaluate import evaluate
from kolproject.volume_billing import base, cli, source_terms

SNAPSHOT = ROOT / 'Data' / 'processed' / base.SNAPSHOT


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'OUT', tmp_path / 'processed')
    monkeypatch.setattr(base, 'INTERIM', tmp_path / 'interim')
    monkeypatch.setattr(base, 'RESTRICTED', tmp_path / 'interim' / 'restricted_terms')
    return tmp_path


def columns():
    return [base.column('ccn', 'Hospital key.', 'Never blank.', 'cms'),
            base.column('discharges', 'Published discharges.', 'Blank = suppressed.', 'cms'),
            base.column('on_kite_list', 'Listed by the locator.', 'Never blank.', 'kite'),
            base.column('exact_count', 'Self-reported count.', 'Blank = not reported.', 'bmtinfonet', restricted=True)]


def test_restricted_sources_are_always_kept_local():
    assert base.column('x', 'd', 'm', 'kite')['restricted'] and base.column('x', 'd', 'm', 'bms')['restricted']
    assert not base.column('x', 'd', 'm', 'cms')['restricted']
    with pytest.raises(AssertionError):
        base.column('x', 'd', 'm', 'somewhere_else')
    with pytest.raises(AssertionError):
        base.column('x', 'd', '', 'cms')


def test_publish_splits_tracked_and_local_columns_on_the_same_key(sandbox):
    rows = [{'ccn': '010033', 'discharges': '45', 'on_kite_list': 1, 'exact_count': 60, 'extra': 'never written'},
            {'ccn': '330154', 'discharges': '', 'on_kite_list': 0, 'exact_count': ''}]
    summary = base.publish('demo', rows, columns(), ['ccn'])
    tracked = read_csv(sandbox / 'processed' / 'demo.csv')
    local = read_csv(sandbox / 'interim' / 'restricted_terms' / 'demo__restricted.csv')
    assert list(tracked[0]) == ['ccn', 'discharges'] and tracked[1]['discharges'] == ''
    assert list(local[0]) == ['ccn', 'on_kite_list', 'exact_count'] and local[0]['exact_count'] == '60'
    assert summary == {'table': 'demo', 'rows': 2, 'key': ['ccn'], 'columns': 2,
                       'restricted_columns': ['on_kite_list', 'exact_count']}


def test_publish_rejects_bad_keys_missing_columns_and_restricted_keys(sandbox):
    good = {'ccn': '010033', 'discharges': '45', 'on_kite_list': 1, 'exact_count': 60}
    with pytest.raises(AssertionError, match='duplicate key'):
        base.publish('demo', [good, dict(good)], columns(), ['ccn'])
    with pytest.raises(AssertionError, match='blank key'):
        base.publish('demo', [{**good, 'ccn': ' '}], columns(), ['ccn'])
    with pytest.raises(AssertionError, match='lacks'):
        base.publish('demo', [{'ccn': '010033'}], columns(), ['ccn'])
    with pytest.raises(AssertionError, match='cannot be restricted'):
        base.publish('demo', [good], columns(), ['on_kite_list'])


def test_blank_cells_are_never_read_as_zero():
    assert base.number('') is None and base.number(' 0 ') == 0.0
    assert base.whole('12.0') == '12' and base.whole('') == '' and base.money('3.456') == '3.46' and base.money('') == ''
    with pytest.raises(AssertionError):
        base.whole('1.5')


def test_every_column_source_has_recorded_terms_and_restricted_ones_say_so():
    terms = {row['source_key']: row for row in source_terms.rows()}
    assert set(terms) == base.SOURCES
    assert {key for key, row in terms.items() if row['terms_class'] == 'restricted'} == base.RESTRICTED_SOURCES
    assert all(row['terms_summary'] and row['handling'] and row['terms_read_on'] for row in terms.values())


needs_snapshot = pytest.mark.skipif(not (SNAPSHOT / cli.METADATA).exists(), reason='2026-11-19 tables not built')


@needs_snapshot
def test_published_snapshot_passes_and_tracks_no_restricted_column():
    assert not {name: n for name, n in cli.check(ROOT / 'Data' / 'processed').items() if n}
    held = base.load(SNAPSHOT / cli.METADATA)['restricted_columns']
    assert held and all(table in cli.tables() for table in held)
    dictionary = base.load(ROOT / 'Data' / 'manifests' / base.SNAPSHOT / 'volume_billing_data_dictionary.json')
    assert set(dictionary) == set(cli.tables())
    for table, fields in dictionary.items():
        assert [f['column'] for f in fields] == list(read_csv(SNAPSHOT / f'{table}.csv')[0])
        assert not {f['source'] for f in fields} & base.RESTRICTED_SOURCES
        assert not {f['column'] for f in fields} & set(held.get(table, []))


@needs_snapshot
def test_stray_file_and_leaked_restricted_column_fail_validation(tmp_path):
    shutil.copytree(ROOT / 'Data' / 'processed', tmp_path / 'Data' / 'processed')
    out = tmp_path / 'Data' / 'processed' / base.SNAPSHOT
    (out / 'notes.txt').write_text('left behind')
    path = out / 'cart_site_volume_panel.csv'
    lines = path.read_text(encoding='utf-8').splitlines()
    path.write_text('\n'.join([lines[0] + ',on_kite_atc_list'] + [line + ',1' for line in lines[1:]]) + '\n', encoding='utf-8')
    report = evaluate(tmp_path)
    assert not report['passed']
    assert {'volume_billing_unexpected_or_missing_files', 'volume_billing_restricted_column_in_tracked_table'} <= set(report['failures'])
    assert report['counts']['cart_center_340b_status'] == 173


def test_stage_freezes_once_and_refuses_paths_outside_the_frozen_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'ROOT', tmp_path)
    monkeypatch.setattr(base, 'RAW', tmp_path / 'raw')
    monkeypatch.setattr(base, 'PROBES', tmp_path / 'probes')
    (tmp_path / 'probes').mkdir()
    (tmp_path / 'probes' / 'a.txt').write_text('first')
    assert base.stage('a.txt').read_text() == 'first'
    (tmp_path / 'probes' / 'a.txt').write_text('second')
    assert base.stage('a.txt').read_text() == 'first' and not list((tmp_path / 'raw').glob('*.tmp'))
    with pytest.raises(ValueError):
        base.stage('../a.txt')
    with pytest.raises(FileNotFoundError, match='not possible from a clone'):
        base.stage('absent.txt')


def test_publish_removes_a_stale_companion_and_refuses_an_unmarked_restricted_source(sandbox):
    row = {'ccn': '010033', 'discharges': '45', 'on_kite_list': 1, 'exact_count': 60}
    base.publish('demo', [row], columns(), ['ccn'])
    companion = sandbox / 'interim' / 'restricted_terms' / 'demo__restricted.csv'
    assert companion.exists()
    base.publish('demo', [row], columns()[:2], ['ccn'])
    assert not companion.exists()
    unmarked = {**base.column('on_kite_list', 'd', 'm', 'kite'), 'restricted': False}
    with pytest.raises(ValueError):
        base.publish('demo', [row], columns()[:2] + [unmarked], ['ccn'])
    with pytest.raises(AssertionError, match='key is required'):
        base.publish('demo', [row], columns()[:2], [])


@needs_snapshot
@pytest.mark.parametrize('damage, failing', [
    (lambda out: (out / cli.METADATA).write_text('{}'), 'volume_billing_metadata_unreadable_or_incomplete'),
    (lambda out: (out / 'sub').mkdir() or (out / 'sub' / 'x.csv').write_text('a\n1\n'), 'volume_billing_unexpected_or_missing_files'),
    (lambda out: (out / 'cart_product_orphan_designations.csv').write_text(''), None),  # any failure, no crash
    (lambda out: (out / 'division_relabel_candidates.csv').write_text(
        '\n'.join((out / 'division_relabel_candidates.csv').read_text().splitlines()[:-1]) + '\n'),
     'volume_billing_metadata_differs_from_tables'),
])
def test_damaged_snapshot_fails_without_crashing(tmp_path, damage, failing):
    for folder in ('2026-10-24', '2026-10-15', '2026-11-11', base.SNAPSHOT):
        shutil.copytree(ROOT / 'Data' / 'processed' / folder, tmp_path / folder)
    damage(tmp_path / base.SNAPSHOT)
    result = cli.check(tmp_path)
    assert result[failing] > 0 if failing else any(result.values())


def test_a_clone_without_the_snapshot_folder_fails_validation(tmp_path):
    shutil.copytree(ROOT / 'Data' / 'processed', tmp_path / 'Data' / 'processed',
                    ignore=shutil.ignore_patterns(base.SNAPSHOT))
    report = evaluate(tmp_path)
    assert 'volume_billing_snapshot_missing' in report['failures']
