"""Shared rules of the 2026-11-20 snapshot: logged retrieval, de-duplication against earlier snapshots, publishing.

Every outside request goes through `fetch`, which freezes the response under `Data/raw/2026-11-20/<module>/`
with its URL, UTC retrieval time and SHA-256, and never repeats a request already frozen. Inputs that are local
files are recorded with `log_local`. `new_rows` drops anything already published under the same key, and
`publish` writes a table from an explicit column list, requires provenance columns on every row, and keeps
columns from sources that restrict reuse in a local companion file that Git ignores.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..common import ROOT, dump, load, now, read_csv, write_csv
from ..volume_billing.base import (CENTERS, DIVISION_GROUPS, LABEL_VERSION, RESTRICTED_SOURCES, RESTRICTED_SUFFIX,
                                   UNIVERSE, UNIVERSE_SNAPSHOT, centers, duplicates, money, number, sha, universe, whole)
from ..volume_billing.base import SOURCES as EARLIER_SOURCES

SNAPSHOT = '2026-11-20'
PROCESSED = ROOT / 'Data' / 'processed'
OUT = PROCESSED / SNAPSHOT
INTERIM = ROOT / 'Data' / 'interim' / SNAPSHOT
RESTRICTED = INTERIM / 'restricted_terms'
RAW = ROOT / 'Data' / 'raw' / SNAPSHOT
MANIFESTS = ROOT / 'Data' / 'manifests' / SNAPSHOT
EARLIER_SNAPSHOTS = ['2026-10-09', '2026-10-15', '2026-10-24', '2026-11-11', '2026-11-19']

# Source keys a column may name. Kite and Bristol Myers Squibb locator content is never tracked.
SOURCES = EARLIER_SOURCES | {'europe_pmc', 'pubmed', 'orcid', 'careset', 'ash', 'nber'}
_lock = threading.Lock()
_next_request = {}


def column(name, description, missing, source, restricted=False):
    """One published column: its meaning, what a blank means, its source key, and whether it stays local."""
    assert source in SOURCES, f'Unknown source for {name}: {source}'
    assert description.strip() and missing.strip(), f'Column {name} needs a description and a blank meaning'
    return {'column': name, 'description': description, 'missing_value': missing, 'source': source,
            'restricted': bool(restricted or source in RESTRICTED_SOURCES)}


def fetch(url, module, pause=1.0, suffix='', headers=None, data=None, force=False):
    """Fetch one public URL once, freeze it with its retrieval record, and return (path, record).

    The response is saved under RAW/<module>/ and never requested again unless `force` is set. Requests to the
    same host are spaced `pause` seconds apart across threads. The client header is the library default.
    """
    key = hashlib.sha256((url + (data.decode() if data else '')).encode()).hexdigest()[:24]
    path = RAW / module / (key + suffix)
    meta_path = RAW / module / (key + '.meta.json')
    if path.exists() and meta_path.exists() and not force:
        return path, load(meta_path)
    host = url.split('/')[2]
    for attempt in range(5):
        with _lock:
            wait = max(0.0, _next_request.get(host, 0.0) - time.monotonic())
            _next_request[host] = max(_next_request.get(host, 0.0), time.monotonic()) + pause
        time.sleep(wait)
        started = now()
        try:
            request = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(request, timeout=120) as response:
                payload, status, response_headers = response.read(), response.status, dict(response.headers)
            break
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(5 * 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(5 * 2 ** attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(payload)
    temporary.replace(path)
    record = {'source_id': f'{module}:{key}', 'module': module, 'source_url': url, 'method': 'POST' if data else 'GET',
              'retrieval_started_at_utc': started, 'retrieved_at_utc': now(), 'http_status': status,
              'http_last_modified': response_headers.get('Last-Modified', ''), 'http_etag': response_headers.get('ETag', ''),
              'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload),
              'local_path': str(path.relative_to(ROOT))}
    dump(meta_path, record)
    return path, record


def log_local(module, name, path, obtained, release='', sha256=None, note=''):
    """Record a local input (a file already on this machine) with its hash, so the request log is complete."""
    path = Path(path)
    record = {'source_id': f'{module}:local:{name}', 'module': module, 'source_url': '', 'method': 'LOCAL',
              'retrieval_started_at_utc': '', 'retrieved_at_utc': obtained, 'http_status': '',
              'http_last_modified': '', 'http_etag': '', 'sha256': sha256 or sha(path), 'bytes': path.stat().st_size,
              'local_path': str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name,
              'release': release, 'note': note}
    dump(INTERIM / 'local_inputs' / module / f'{name}.json', record)
    return record


def request_log(module):
    """Every frozen request and local input of one module, oldest first."""
    records = [load(p) for p in sorted((RAW / module).glob('*.meta.json'))] if (RAW / module).exists() else []
    local = INTERIM / 'local_inputs' / module
    records += [load(p) for p in sorted(local.glob('*.json'))] if local.exists() else []
    return sorted(records, key=lambda r: (r['retrieved_at_utc'], r['source_id']))


def existing_keys(table_paths, key, rename=None):
    """Key tuples already published in earlier tables; `rename` maps this table's key names to theirs."""
    rename = rename or {}
    found = set()
    for path in table_paths:
        for row in read_csv(path):
            found.add(tuple(str(row[rename.get(k, k)]).strip() for k in key))
    return found


def new_rows(rows, key, already):
    """Rows whose key is not already published, and the count dropped; duplicates inside `rows` are dropped too."""
    kept, seen, dropped = [], set(already), 0
    for row in rows:
        value = tuple(str(row[k]).strip() for k in key)
        if value in seen:
            dropped += 1
            continue
        seen.add(value)
        kept.append(row)
    return kept, dropped


def publish(table, rows, columns, key, provenance):
    """Write a tracked table and, when some columns are restricted, its local companion.

    `provenance` names the columns that say where and when each row came from (a source id, release or version,
    and a retrieval time); they must be listed, tracked and filled on every row. The key must be filled and unique.
    """
    rows = list(rows)
    names = [c['column'] for c in columns]
    if any(c['source'] in RESTRICTED_SOURCES and not c['restricted'] for c in columns):
        raise ValueError(f'{table}: a column from a restricted source is not marked restricted')
    assert key, f'{table}: a key is required'
    assert len(names) == len(set(names)), f'{table}: duplicate column names'
    assert set(key) <= set(names), f'{table}: key columns must be listed'
    public = [c['column'] for c in columns if not c['restricted']]
    local = [c['column'] for c in columns if c['restricted']]
    assert provenance and set(provenance) <= set(public), f'{table}: tracked provenance columns are required'
    for row in rows:
        absent = [n for n in names if n not in row]
        assert not absent, f'{table}: row lacks {absent}'
        assert all(str(row[n]).strip() for n in provenance), f'{table}: blank provenance value'
    assert not any(row[k] is None for row in rows for k in key), f'{table}: blank key value'
    keys = [tuple(str(row[k]).strip() for k in key) for row in rows]
    assert all(all(k) for k in keys), f'{table}: blank key value'
    assert len(keys) == len(set(keys)), f'{table}: duplicate key'
    assert not any(c['restricted'] for c in columns if c['column'] in key), f'{table}: key column cannot be restricted'
    write_csv(OUT / f'{table}.csv', [{n: row[n] for n in public} for row in rows], public)
    companion = RESTRICTED / f'{table}{RESTRICTED_SUFFIX}.csv'
    if local:
        write_csv(companion, [{n: row[n] for n in list(key) + local} for row in rows], list(key) + local)
    elif companion.exists():
        companion.unlink()
    summary = {'table': table, 'rows': len(rows), 'key': list(key), 'columns': len(public),
               'restricted_columns': local, 'provenance_columns': list(provenance)}
    dump(INTERIM / 'dictionary' / f'{table}.json', {**summary, 'fields': columns})
    return summary


def write_module_metadata(module, metadata):
    """Save one module's metadata fragment with its request log; the entry point merges the fragments."""
    path = INTERIM / 'metadata' / f'{module}.json'
    fragment = json.loads(json.dumps({'module': module, 'built_at_utc': now(), **metadata,
                                      'requests_logged': len(request_log(module))}))
    if path.exists():  # an unchanged rebuild keeps its first build time, so tracked metadata does not churn
        previous = load(path)
        if {**previous, 'built_at_utc': ''} == {**fragment, 'built_at_utc': ''}:
            fragment['built_at_utc'] = previous['built_at_utc']
    dump(path, fragment)


__all__ = ['CENTERS', 'DIVISION_GROUPS', 'EARLIER_SNAPSHOTS', 'INTERIM', 'LABEL_VERSION', 'MANIFESTS', 'OUT',
           'PROCESSED', 'RAW', 'RESTRICTED', 'RESTRICTED_SOURCES', 'ROOT', 'SNAPSHOT', 'SOURCES', 'UNIVERSE',
           'UNIVERSE_SNAPSHOT', 'centers', 'column', 'dump', 'duplicates', 'existing_keys', 'fetch', 'load',
           'log_local', 'money', 'new_rows', 'now', 'number', 'publish', 'read_csv', 'request_log', 'sha', 'universe',
           'whole', 'write_csv', 'write_module_metadata']
