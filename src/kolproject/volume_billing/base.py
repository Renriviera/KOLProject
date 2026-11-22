"""Shared paths, staging of search inputs, and the publishing rule for the 2026-11-19 snapshot.

The inputs were collected by an ad hoc search on 2026-11-19 and saved under `research_notes/`.
`stage` freezes each input under `Data/raw/2026-11-19/search_probes/` on first use and never
refreshes it, so a rebuild does not depend on the notes folder; hashes of the frozen files are written
to each module's metadata and to the local checksum manifest. `publish` writes one tracked table from an explicit column
list and moves every column whose source restricts reuse into a local companion file under
`Data/interim/2026-11-19/restricted_terms/`, which Git ignores.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from ..common import ROOT, dump, load, now, read_csv, write_csv

SNAPSHOT = '2026-11-19'
OUT = ROOT / 'Data' / 'processed' / SNAPSHOT
INTERIM = ROOT / 'Data' / 'interim' / SNAPSHOT
RESTRICTED = INTERIM / 'restricted_terms'
RAW = ROOT / 'Data' / 'raw' / SNAPSHOT / 'search_probes'
MANIFESTS = ROOT / 'Data' / 'manifests' / SNAPSHOT
NOTES = ROOT / 'research_notes' / 'CAR-T physician and hospital volume data'
PROBES = NOTES / 'probes'
UNIVERSE = ROOT / 'Data' / 'processed' / '2026-10-24' / 'hcp_divisions.csv'
CENTERS = ROOT / 'Data' / 'processed' / '2026-10-24' / 'cart_centers.csv'
UNIVERSE_SNAPSHOT = '2026-10-24'
LABEL_VERSION = '1.0.0'

# Division labels of hcp_divisions.csv (label version 1.0.0) grouped the way the search report quotes them.
DIVISION_GROUPS = {
    'core': 'core',
    'affiliate_A': 'affiliates', 'affiliate_B': 'affiliates', 'affiliate_C': 'affiliates',
    'center_affiliate_unlinked': 'affiliates',
    'referring_proxy': 'referring',
    'hemonc_other': 'other',
}

# Where a column's values come from. Kite and Bristol Myers Squibb site terms limit reuse to personal,
# non-commercial use, so nothing derived from them is written to a tracked table.
SOURCES = {'cms', 'hhs', 'hrsa', 'fda', 'ctgov', 'nppes', 'open_payments', 'ny_sparcs', 'ca_hcai', 'fl_ahca',
           'hospital_price_file', 'ahrq', 'bmtinfonet', 'fact', 'kite', 'bms', 'project'}
RESTRICTED_SOURCES = {'kite', 'bms'}
RESTRICTED_SUFFIX = '__restricted'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 22), b''):
            digest.update(block)
    return digest.hexdigest()


def stage(source, name=None):
    """Freeze one search input under RAW and return the frozen path.

    `source` is a path relative to PROBES, or an absolute path for inputs saved elsewhere, in which case
    `name` gives the relative path to store it under. An input already frozen is never overwritten.
    """
    source = Path(source)
    relative = Path(name) if name else source
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError(f'Give a relative name inside the frozen folder for {source}')
    target = RAW / relative
    if not target.exists():
        origin = source if source.is_absolute() else PROBES / source
        if not origin.is_file():
            raise FileNotFoundError(
                f'Frozen input {target.relative_to(ROOT)} is not on this machine and its search copy {origin} is '
                'missing too. Data/raw is not in Git, so a rebuild is not possible from a clone; '
                'kolproject-evaluate validates the published tables instead.')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.tmp')
        shutil.copy2(origin, temporary)
        temporary.replace(target)
    return target


def staged_inputs(paths):
    return [{'path': str(Path(p).relative_to(ROOT)), 'bytes': Path(p).stat().st_size, 'sha256': sha(p)}
            for p in sorted(set(map(Path, paths)))]


def column(name, description, missing, source, restricted=False):
    """One published column: its meaning, what a blank means, its source key, and whether it stays local."""
    assert source in SOURCES, f'Unknown source for {name}: {source}'
    assert description.strip() and missing.strip(), f'Column {name} needs a description and a blank meaning'
    return {'column': name, 'description': description, 'missing_value': missing, 'source': source,
            'restricted': bool(restricted or source in RESTRICTED_SOURCES)}


def publish(table, rows, columns, key):
    """Write a tracked table and, when some columns are restricted, its local companion.

    Rows must carry every listed column and nothing else is written. The key must be filled and unique.
    """
    rows = list(rows)
    names = [c['column'] for c in columns]
    if any(c['source'] in RESTRICTED_SOURCES and not c['restricted'] for c in columns):
        raise ValueError(f'{table}: a column from a restricted source is not marked restricted')
    assert key, f'{table}: a key is required'
    assert len(names) == len(set(names)), f'{table}: duplicate column names'
    assert set(key) <= set(names), f'{table}: key columns must be listed'
    for row in rows:
        absent = [n for n in names if n not in row]
        assert not absent, f'{table}: row lacks {absent}'
    assert not any(row[k] is None for row in rows for k in key), f'{table}: blank key value'
    keys = [tuple(str(row[k]).strip() for k in key) for row in rows]
    assert all(all(k) for k in keys), f'{table}: blank key value'
    assert len(keys) == len(set(keys)), f'{table}: duplicate key'
    assert not any(c['restricted'] for c in columns if c['column'] in key), f'{table}: key column cannot be restricted'
    public = [c['column'] for c in columns if not c['restricted']]
    local = [c['column'] for c in columns if c['restricted']]
    write_csv(OUT / f'{table}.csv', [{n: row[n] for n in public} for row in rows], public)
    companion = RESTRICTED / f'{table}{RESTRICTED_SUFFIX}.csv'
    if local:
        write_csv(companion, [{n: row[n] for n in list(key) + local} for row in rows], list(key) + local)
    elif companion.exists():
        companion.unlink()
    summary = {'table': table, 'rows': len(rows), 'key': list(key), 'columns': len(public),
               'restricted_columns': local}
    dump(INTERIM / 'dictionary' / f'{table}.json', {**summary, 'fields': columns})
    return summary


def write_module_metadata(module, metadata, inputs):
    """Save one module's metadata fragment; the command-line entry point merges the fragments."""
    path = INTERIM / 'metadata' / f'{module}.json'
    fragment = {'module': module, 'built_at_utc': now(), **metadata, 'staged_inputs': staged_inputs(inputs)}
    if path.exists():  # an unchanged rebuild keeps its first build time, so tracked metadata does not churn
        previous = load(path)
        if {**previous, 'built_at_utc': ''} == {**json.loads(json.dumps(fragment)), 'built_at_utc': ''}:
            fragment['built_at_utc'] = previous['built_at_utc']
    dump(path, fragment)


def universe():
    """NPI to row of the 2026-10-24 universe table."""
    return {row['npi']: row for row in read_csv(UNIVERSE)}


def centers():
    """CCN to row of the 2026-10-24 CAR-T center table."""
    return {row['ccn']: row for row in read_csv(CENTERS)}


def number(value):
    """Float for a filled cell, None for a blank one; a blank is never read as zero."""
    value = str(value).strip()
    return float(value) if value else None


def whole(value):
    """Integer text for counts stored as floats such as '12.0'; blank stays blank."""
    value = str(value).strip()
    if not value:
        return ''
    assert float(value) == int(float(value)), f'Not a whole number: {value}'
    return str(int(float(value)))


def money(value):
    """Dollar text rounded to cents; blank stays blank."""
    value = str(value).strip()
    return f'{float(value):.2f}' if value else ''


def duplicates(rows, key):
    return len(rows) - len({tuple(row[k] for k in key) for row in rows})


__all__ = ['CENTERS', 'DIVISION_GROUPS', 'INTERIM', 'LABEL_VERSION', 'MANIFESTS', 'NOTES', 'OUT', 'PROBES', 'RAW',
           'RESTRICTED', 'RESTRICTED_SOURCES', 'RESTRICTED_SUFFIX', 'ROOT', 'SNAPSHOT', 'UNIVERSE',
           'UNIVERSE_SNAPSHOT', 'centers', 'column', 'dump', 'duplicates', 'load', 'money', 'now', 'number',
           'publish', 'read_csv', 'sha', 'stage', 'staged_inputs', 'universe', 'whole', 'write_csv',
           'write_module_metadata']
