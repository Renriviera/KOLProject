"""Build the 2026-11-19 volume and billing snapshot from the frozen search inputs.

Each module publishes its own tables; this entry point runs them, then writes the merged metadata,
the data dictionary, the source-terms table and the local checksum manifest.
"""
from __future__ import annotations

import argparse
import csv
import importlib

from . import source_terms
from .base import (INTERIM, MANIFESTS, OUT, PROBES, RAW, RESTRICTED, RESTRICTED_SOURCES, ROOT, SNAPSHOT, UNIVERSE_SNAPSHOT,
                   LABEL_VERSION, DIVISION_GROUPS, dump, load, now, sha, write_csv)

MODULES = ['center_panel', 'status_340b', 'treater_roster', 'industry_payments', 'medicare_payments',
           'medicaid_payments']
METADATA = 'volume_billing_metadata.json'
# Files the operating system leaves behind; Git ignores them, so they are not counted as stray files.
IGNORED_FILES = {'.DS_Store'}


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
            result = module.check(processed)
        except Exception:  # a broken table must fail validation, not crash it
            result = {f'{name}_check_raised': 1}
        else:
            result[f'{name}_check_raised'] = 0
        assert not set(result) & set(checks), 'Check names must be unique across modules'
        checks.update(result)
    expected = {f'{table}.csv' for table in tables()} | {METADATA}
    present = ({path.relative_to(out).as_posix() for path in out.rglob('*')
                if path.is_file() and path.name not in IGNORED_FILES} if out.exists() else set())
    checks['volume_billing_unexpected_or_missing_files'] = len(expected ^ present)
    try:
        metadata = load(out / METADATA)
        held, summaries = metadata['restricted_columns'], metadata['tables']
        unreadable = int(not held or set(summaries) != set(tables()))
    except Exception:
        held, summaries, unreadable = {}, {}, 1
    checks['volume_billing_metadata_unreadable_or_incomplete'] = unreadable
    # No tracked header may carry a name that is held back anywhere in the snapshot.
    held_names = {name for names in held.values() for name in names}
    headers = {table: header(out / f'{table}.csv') for table in tables()}
    checks['volume_billing_restricted_column_in_tracked_table'] = sum(
        name in held_names for names in headers.values() for name in names)
    checks['volume_billing_metadata_differs_from_tables'] = sum(
        not headers[table] or summary.get('columns') != len(headers[table])
        or summary.get('rows') != row_count(out / f'{table}.csv')
        for table, summary in summaries.items() if table in headers)
    return checks


def make_manifests():
    dictionary, restricted, summaries = {}, {}, {}
    for table in tables():
        entry = load(INTERIM / 'dictionary' / f'{table}.json')
        public = [f for f in entry['fields'] if not f['restricted']]
        assert header(OUT / f'{table}.csv') == [f['column'] for f in public], f'{table}: header differs from its dictionary'
        assert not any(f['source'] in RESTRICTED_SOURCES for f in public), f'{table}: restricted source is tracked'
        rows = row_count(OUT / f'{table}.csv')
        assert rows == entry['rows'], f'{table}: dictionary fragment is stale; rebuild its module'
        dictionary[table] = [{k: f[k] for k in ['column', 'description', 'missing_value', 'source']} for f in public]
        restricted[table] = entry['restricted_columns']
        summaries[table] = {'rows': rows, 'key': entry['key'], 'columns': len(public)}
    fragments = {name: load(INTERIM / 'metadata' / f'{name}.json') for name in MODULES}
    metadata = {
        'built_at_utc': now(), 'collection_snapshot_date': SNAPSHOT,
        'origin': 'Inputs were collected by an ad hoc multi-agent search on 2026-11-19 and frozen under '
                  'Data/raw/2026-11-19/; request times were not captured for every input.',
        'universe': {'path': f'Data/processed/{UNIVERSE_SNAPSHOT}/hcp_divisions.csv', 'label_version': LABEL_VERSION,
                     'division_groups': DIVISION_GROUPS},
        'center_set': f'Data/processed/{UNIVERSE_SNAPSHOT}/cart_centers.csv',
        'tables': summaries,
        'restricted_columns': {t: c for t, c in restricted.items() if c},
        'restricted_columns_location': 'Data/interim/2026-11-19/restricted_terms/ (local only, not in Git)',
        'source_terms': f'Data/manifests/{SNAPSHOT}/volume_billing_source_terms.csv',
        'modules': {name: {k: v for k, v in fragment.items() if k != 'staged_inputs'}
                    for name, fragment in fragments.items()},
    }
    if (OUT / METADATA).exists():  # keep the build time when nothing else changed, so the tracked file does not churn
        previous = load(OUT / METADATA)
        if {**previous, 'built_at_utc': ''} == {**metadata, 'built_at_utc': ''}:
            metadata['built_at_utc'] = previous['built_at_utc']
    dump(OUT / METADATA, metadata)
    dump(MANIFESTS / 'volume_billing_data_dictionary.json', dictionary)
    write_csv(MANIFESTS / 'volume_billing_source_terms.csv', source_terms.rows(), source_terms.FIELDS)
    paths = [p for base in [RAW.parent, INTERIM] if base.exists() for p in sorted(base.rglob('*'))
             if p.is_file() and p.suffix != '.tmp']
    paths += sorted(p for p in OUT.iterdir() if p.is_file() and p.name not in IGNORED_FILES)
    write_csv(MANIFESTS / 'volume_billing_file_checksums.csv',
              [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p),
                'tracked': int(OUT in p.parents)} for p in paths])
    print('VOLUME BILLING MANIFESTS', len(dictionary), 'TABLES', len(paths), 'FILES',
          sum(map(len, restricted.values())), 'RESTRICTED COLUMNS IN', str(RESTRICTED.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+', choices=MODULES, help='Rebuild only these modules.')
    parser.add_argument('--manifests-only', action='store_true', help='Skip the builds and rewrite the manifests.')
    args = parser.parse_args()
    if not (RAW.parent.exists() or PROBES.exists()) or (args.manifests_only and not (INTERIM / 'metadata').exists()):
        raise SystemExit('The frozen inputs under Data/raw/2026-11-19/ and the build fragments under '
                         'Data/interim/2026-11-19/ are not in Git, so this clone cannot rebuild the snapshot. '
                         'Run kolproject-evaluate to validate the published tables.')
    if not args.manifests_only:
        for module in modules(args.only):
            module.build()
    make_manifests()
    failures = {name: value for name, value in check(ROOT / 'Data' / 'processed').items() if value}
    print('VOLUME BILLING CHECKS', 'PASSED' if not failures else f'FAILED {failures}')
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
