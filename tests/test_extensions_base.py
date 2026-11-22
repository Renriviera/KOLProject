"""Logging, de-duplication and publishing rules shared by the 2026-11-20 extension modules."""
import shutil

import pytest

from kolproject.common import ROOT, read_csv
from kolproject.extensions import base, cli

SNAPSHOT = ROOT / 'Data' / 'processed' / base.SNAPSHOT


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(base, 'ROOT', tmp_path)
    monkeypatch.setattr(base, 'OUT', tmp_path / 'processed')
    monkeypatch.setattr(base, 'INTERIM', tmp_path / 'interim')
    monkeypatch.setattr(base, 'RESTRICTED', tmp_path / 'interim' / 'restricted_terms')
    monkeypatch.setattr(base, 'RAW', tmp_path / 'raw')
    return tmp_path


def columns():
    return [base.column('pmid', 'Paper identifier.', 'Never blank.', 'europe_pmc'),
            base.column('title', 'Paper title.', 'Never blank.', 'europe_pmc'),
            base.column('statement', 'Publisher text.', 'Blank = none.', 'pubmed', restricted=True),
            base.column('source_id', 'Frozen response the row came from.', 'Never blank.', 'project'),
            base.column('retrieved_at_utc', 'UTC time of retrieval.', 'Never blank.', 'project')]


def row(pmid, **extra):
    return {'pmid': pmid, 'title': 't', 'statement': 's', 'source_id': 'demo:1', 'retrieved_at_utc': '2026-11-20T00:00:00Z', **extra}


def test_new_rows_drops_entries_already_published_and_internal_repeats(tmp_path):
    earlier = tmp_path / 'earlier.csv'
    earlier.write_text('paper,x\n100,a\n200,b\n')
    held = base.existing_keys([earlier], ['pmid'], rename={'pmid': 'paper'})
    kept, dropped = base.new_rows([row('100'), row('300'), row(' 300 '), row('400')], ['pmid'], held)
    assert [r['pmid'] for r in kept] == ['300', '400'] and dropped == 2


def test_publish_requires_filled_tracked_provenance_and_keeps_restricted_columns_local(sandbox):
    summary = base.publish('demo', [row('1'), row('2')], columns(), ['pmid'], ['source_id', 'retrieved_at_utc'])
    assert list(read_csv(sandbox / 'processed' / 'demo.csv')[0]) == ['pmid', 'title', 'source_id', 'retrieved_at_utc']
    assert list(read_csv(sandbox / 'interim' / 'restricted_terms' / 'demo__restricted.csv')[0]) == ['pmid', 'statement']
    assert summary['provenance_columns'] == ['source_id', 'retrieved_at_utc'] and summary['restricted_columns'] == ['statement']
    with pytest.raises(AssertionError, match='blank provenance'):
        base.publish('demo', [row('1', retrieved_at_utc=' ')], columns(), ['pmid'], ['source_id', 'retrieved_at_utc'])
    with pytest.raises(AssertionError, match='provenance columns are required'):
        base.publish('demo', [row('1')], columns(), ['pmid'], [])
    with pytest.raises(AssertionError, match='provenance columns are required'):
        base.publish('demo', [row('1')], columns(), ['pmid'], ['statement'])
    with pytest.raises(AssertionError, match='duplicate key'):
        base.publish('demo', [row('1'), row('1')], columns(), ['pmid'], ['source_id'])


def test_fetch_freezes_one_response_with_its_record_and_never_repeats_it(sandbox, monkeypatch):
    calls = []

    class Response:
        status, headers = 200, {'Last-Modified': 'Fri, 20 Nov 2026 00:00:00 GMT'}

        def read(self):
            return b'{"n": 1}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(base.urllib.request, 'urlopen', lambda request, timeout: calls.append(request.full_url) or Response())
    path, record = base.fetch('https://example.org/data?x=1', 'demo', pause=0, suffix='.json')
    again, same = base.fetch('https://example.org/data?x=1', 'demo', pause=0, suffix='.json')
    assert calls == ['https://example.org/data?x=1'] and path == again and record == same
    assert path.read_bytes() == b'{"n": 1}' and record['bytes'] == 8 and len(record['sha256']) == 64
    assert record['retrieved_at_utc'].endswith('Z') and record['http_last_modified'].startswith('Fri')
    assert [r['source_id'] for r in base.request_log('demo')] == [record['source_id']]


def test_local_inputs_join_the_request_log(sandbox):
    (sandbox / 'file.csv').write_text('a\n1\n')
    record = base.log_local('demo', 'file', sandbox / 'file.csv', '2026-10-25T00:00:00Z', release='2022')
    assert record['method'] == 'LOCAL' and record['local_path'] == 'file.csv' and record['bytes'] == 4
    assert base.request_log('demo')[0]['release'] == '2022'


needs_snapshot = pytest.mark.skipif(not (SNAPSHOT / cli.METADATA).exists(), reason='2026-11-20 tables not built')


@needs_snapshot
def test_published_extension_snapshot_passes_with_provenance_on_every_row():
    assert not {name: n for name, n in cli.check(ROOT / 'Data' / 'processed').items() if n}
    metadata = base.load(SNAPSHOT / cli.METADATA)
    assert set(metadata['tables']) == set(cli.tables())
    assert all(summary['provenance_columns'] for summary in metadata['tables'].values())
    sources = read_csv(ROOT / 'Data' / 'manifests' / base.SNAPSHOT / 'extension_sources.csv')
    assert {s['module'] for s in sources} == set(cli.MODULES)
    assert all(s['name'] and s['publisher'] and (s['retrieved_from_utc'] or s['release_or_version']) for s in sources)


@needs_snapshot
def test_stray_file_blank_provenance_and_leaked_column_fail_validation(tmp_path):
    shutil.copytree(ROOT / 'Data' / 'processed', tmp_path / 'processed')
    out = tmp_path / 'processed' / base.SNAPSHOT
    (out / 'notes.txt').write_text('left behind')
    metadata = base.load(out / cli.METADATA)
    table, summary = next((t, s) for t, s in metadata['tables'].items() if s['rows'])
    path = out / f'{table}.csv'
    lines = path.read_text(encoding='utf-8').splitlines()
    held = next(iter(next(iter(metadata['restricted_columns'].values()), ['held_back_column'])))
    metadata['restricted_columns'].setdefault(table, []).append(held)
    base.dump(out / cli.METADATA, metadata)
    path.write_text('\n'.join([lines[0] + ',' + held] + [line + ',x' for line in lines[1:]]) + '\n', encoding='utf-8')
    result = cli.check(tmp_path / 'processed')
    assert result['extensions_unexpected_or_missing_files'] > 0
    assert result['extensions_restricted_column_in_tracked_table'] > 0
    assert result['extensions_metadata_differs_from_tables'] > 0
