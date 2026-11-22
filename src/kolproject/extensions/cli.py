"""Build the 2026-11-20 snapshot: extensions of the four dataset categories.

Each module publishes its own tables; this entry point runs them, then writes the merged metadata, the data
dictionary, the tracked source log and the local request log.
"""
from __future__ import annotations

import argparse
import csv
import importlib

from .base import (DIVISION_GROUPS, INTERIM, LABEL_VERSION, MANIFESTS, OUT, RAW, RESTRICTED, RESTRICTED_SOURCES, ROOT,
                   SNAPSHOT, UNIVERSE_SNAPSHOT, dump, load, now, request_log, sha, write_csv)

MODULES = ['publications', 'payer', 'industry', 'referral']
CATEGORIES = {'publications': 'Publication history and co-authorship', 'payer': 'Payer payments and claims',
              'industry': 'Industry payments and disclosed ties', 'referral': 'Referral and patient-flow data'}
METADATA = 'extensions_metadata.json'
IGNORED_FILES = {'.DS_Store'}
SOURCE_FIELDS = ['module', 'category', 'name', 'publisher', 'url', 'release_or_version', 'retrieved_from_utc',
                 'retrieved_to_utc', 'how_obtained', 'requests', 'terms_class']
REQUEST_FIELDS = ['source_id', 'module', 'method', 'source_url', 'retrieval_started_at_utc', 'retrieved_at_utc',
                  'http_status', 'http_last_modified', 'http_etag', 'sha256', 'bytes', 'local_path', 'release', 'note']


def modules(names=None):
    return [importlib.import_module(f'{__package__}.{name}') for name in (names or MODULES)]


def tables():
    return [table for module in modules() for table in module.TABLES]


def header(path):
    """Column names of a table; an empty list for a missing or empty file."""
    if not path.exists():
        return []
    with path.open(newline='', encoding='utf-8') as stream:
        return next(csv.reader(stream), [])


def row_count(path):
    with path.open(newline='', encoding='utf-8') as stream:
        return max(sum(1 for _ in csv.reader(stream)) - 1, 0)


def check(processed):
    """Every module's checks on a Data/processed directory, plus the folder-level ones.

    A module check that raises on a malformed table is counted as a failure instead of stopping validation.
    """
    out = processed / SNAPSHOT
    checks = {}
    for name, module in zip(MODULES, modules()):
        try:
            result = dict(module.check(processed))
            result[f'{name}_check_raised'] = 0
        except Exception:  # a broken table must fail validation, not crash it
            result = {f'{name}_check_raised': 1}
        assert not set(result) & set(checks), 'Check names must be unique across modules'
        checks.update(result)
    expected = {f'{table}.csv' for table in tables()} | {METADATA}
    present = ({path.relative_to(out).as_posix() for path in out.rglob('*')
                if path.is_file() and path.name not in IGNORED_FILES} if out.exists() else set())
    checks['extensions_unexpected_or_missing_files'] = len(expected ^ present)
    try:
        metadata = load(out / METADATA)
        held, summaries = metadata['restricted_columns'], metadata['tables']
        unreadable = int(set(summaries) != set(tables()))
    except Exception:
        held, summaries, unreadable = {}, {}, 1
    checks['extensions_metadata_unreadable_or_incomplete'] = unreadable
    held_names = {name for names in held.values() for name in names}
    headers = {table: header(out / f'{table}.csv') for table in tables()}
    checks['extensions_restricted_column_in_tracked_table'] = sum(
        name in held_names for names in headers.values() for name in names)
    checks['extensions_metadata_differs_from_tables'] = sum(
        not headers[table] or summary.get('columns') != len(headers[table])
        or summary.get('rows') != row_count(out / f'{table}.csv')
        for table, summary in summaries.items() if table in headers)
    # Every row of every table says where and when it came from.
    blank = 0
    for table, summary in summaries.items():
        path = out / f'{table}.csv'
        names = summary.get('provenance_columns') or []
        if not names or not set(names) <= set(headers.get(table, [])):
            blank += 1
            continue
        with path.open(newline='', encoding='utf-8') as stream:
            blank += sum(not all(row[name].strip() for name in names) for row in csv.DictReader(stream))
    checks['extensions_rows_without_provenance'] = blank
    return checks


def make_manifests():
    dictionary, restricted, summaries = {}, {}, {}
    owner = {table: name for name, module in zip(MODULES, modules()) for table in module.TABLES}
    for table in tables():
        entry = load(INTERIM / 'dictionary' / f'{table}.json')
        public = [f for f in entry['fields'] if not f['restricted']]
        assert header(OUT / f'{table}.csv') == [f['column'] for f in public], f'{table}: header differs from its dictionary'
        assert not any(f['source'] in RESTRICTED_SOURCES for f in public), f'{table}: restricted source is tracked'
        rows = row_count(OUT / f'{table}.csv')
        assert rows == entry['rows'], f'{table}: dictionary fragment is stale; rebuild its module'
        dictionary[table] = [{k: f[k] for k in ['column', 'description', 'missing_value', 'source']} for f in public]
        restricted[table] = entry['restricted_columns']
        summaries[table] = {'module': owner[table], 'category': CATEGORIES[owner[table]], 'rows': rows,
                            'key': entry['key'], 'columns': len(public), 'provenance_columns': entry['provenance_columns']}
    fragments = {name: load(INTERIM / 'metadata' / f'{name}.json') for name in MODULES}
    metadata = {
        'built_at_utc': now(), 'collection_snapshot_date': SNAPSHOT,
        'purpose': 'Extends the four dataset categories of the project. No entry repeats one published in an earlier '
                   'snapshot; each module records what it dropped under "dedup".',
        'earlier_snapshots_unchanged': True,
        'universe': {'path': f'Data/processed/{UNIVERSE_SNAPSHOT}/hcp_divisions.csv', 'label_version': LABEL_VERSION,
                     'division_groups': DIVISION_GROUPS},
        'tables': summaries,
        'restricted_columns': {t: c for t, c in restricted.items() if c},
        'restricted_columns_location': f'Data/interim/{SNAPSHOT}/restricted_terms/ (local only, not in Git)',
        'source_log': f'Data/manifests/{SNAPSHOT}/extension_sources.csv',
        'modules': fragments,
    }
    if (OUT / METADATA).exists():  # keep the build time when nothing else changed, so the tracked file does not churn
        previous = load(OUT / METADATA)
        if {**previous, 'built_at_utc': ''} == {**metadata, 'built_at_utc': ''}:
            metadata['built_at_utc'] = previous['built_at_utc']
    dump(OUT / METADATA, metadata)
    dump(MANIFESTS / 'extensions_data_dictionary.json', dictionary)
    sources = [{**{field: source.get(field, '') for field in SOURCE_FIELDS}, 'module': name, 'category': CATEGORIES[name]}
               for name, fragment in fragments.items() for source in fragment.get('sources', [])]
    write_csv(MANIFESTS / 'extension_sources.csv', sources, SOURCE_FIELDS)
    requests = [{field: record.get(field, '') for field in REQUEST_FIELDS} for name in MODULES for record in request_log(name)]
    write_csv(MANIFESTS / 'extension_request_log.csv', requests, REQUEST_FIELDS)
    paths = [p for base in [RAW, INTERIM] if base.exists() for p in sorted(base.rglob('*')) if p.is_file() and p.suffix != '.tmp']
    paths += sorted(p for p in OUT.iterdir() if p.is_file() and p.name not in IGNORED_FILES)
    write_csv(MANIFESTS / 'extension_file_checksums.csv',
              [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p), 'tracked': int(OUT in p.parents)}
               for p in paths])
    print('EXTENSIONS MANIFESTS', len(dictionary), 'TABLES', len(sources), 'SOURCES', len(requests), 'LOGGED REQUESTS',
          sum(map(len, restricted.values())), 'RESTRICTED COLUMNS IN', str(RESTRICTED.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+', choices=MODULES, help='Rebuild only these modules.')
    parser.add_argument('--manifests-only', action='store_true', help='Skip the builds and rewrite the manifests.')
    args = parser.parse_args()
    if not RAW.exists() or not (INTERIM / 'metadata').exists() and args.manifests_only:
        raise SystemExit(f'The frozen inputs under Data/raw/{SNAPSHOT}/ and the build fragments under '
                         f'Data/interim/{SNAPSHOT}/ are not in Git, so this clone cannot rebuild the snapshot. '
                         'Run kolproject-evaluate to validate the published tables.')
    if not args.manifests_only:
        for module in modules(args.only):
            module.build()
    make_manifests()
    failures = {name: value for name, value in check(ROOT / 'Data' / 'processed').items() if value}
    print('EXTENSIONS CHECKS', 'PASSED' if not failures else f'FAILED {failures}')
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
