"""Shared helpers for building and reading the agent data pack (one SQLite file).

Every pack table is built from published snapshot tables only. Part modules (`pack_people`, `pack_money`,
`pack_claims`, `pack_referral`) each expose `build(con)`, `card_facts(con)` and `check(con)`; `pack.py` runs
them in order after `pack_dims` and writes the cards and the manifest.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .paths import DB, PACK, PROCESSED

SNAPSHOTS = {'cohort': '2026-10-09', 'utilization': '2026-10-15', 'divisions': '2026-10-24', 'payments': '2026-11-11',
             'volume': '2026-11-19', 'extensions': '2026-11-20'}
SCOPES = ('hcp', 'community', 'community_core', 'center', 'division_group')
DIVISION_GROUP_NAMES = ('core', 'affiliates', 'referring', 'other')


def source(snapshot, table):
    """Path of a published table, by the short snapshot name in SNAPSHOTS."""
    return PROCESSED / SNAPSHOTS[snapshot] / f'{table}.csv'


def connect(path=None, readonly=False):
    """Open the pack. Readers open it read-only so a running server can never change it."""
    path = Path(path or DB)
    if readonly:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.execute('pragma journal_mode = wal')
    con.row_factory = sqlite3.Row
    return con


def read_rows(path):
    """Rows of a published CSV as dicts of strings."""
    with Path(path).open(newline='', encoding='utf-8') as stream:
        yield from csv.DictReader(stream)


def value(text, kind):
    """A CSV cell as a typed SQL value; a blank cell is NULL, never zero."""
    text = '' if text is None else str(text).strip()
    if text == '':
        return None
    if kind == 'int':
        return int(float(text))
    if kind == 'real':
        return float(text)
    return text


def create_table(con, name, columns, rows, primary_key=None, indexes=()):
    """(Re)create a pack table. `columns` is a list of (name, 'text' | 'int' | 'real'); `rows` are dicts or tuples."""
    names = [c for c, _ in columns]
    kinds = {'text': 'TEXT', 'int': 'INTEGER', 'real': 'REAL'}
    spec = ', '.join(f'"{c}" {kinds[k]}' for c, k in columns)
    if primary_key:
        spec += ', PRIMARY KEY (' + ', '.join(f'"{c}"' for c in primary_key) + ')'
    con.execute(f'drop table if exists "{name}"')
    con.execute(f'create table "{name}" ({spec})')
    marks = ', '.join('?' for _ in names)
    con.executemany(f'insert into "{name}" values ({marks})',
                    ([row[c] for c in names] if isinstance(row, dict) else list(row) for row in rows))
    for index in indexes:
        cols = [index] if isinstance(index, str) else list(index)
        con.execute(f'create index "ix_{name}_{"_".join(cols)}" on "{name}" (' + ', '.join(f'"{c}"' for c in cols) + ')')
    return con.execute(f'select count(*) from "{name}"').fetchone()[0]


def load_csv(con, name, path, columns, rename=None, keep=None, primary_key=None, indexes=()):
    """Load a published CSV into a pack table.

    `columns` is a list of (pack column, kind); `rename` maps pack column to source column when they differ;
    `keep` is an optional row filter on the source row.
    """
    rename = rename or {}

    def shaped():
        for row in read_rows(path):
            if keep is None or keep(row):
                yield [value(row[rename.get(c, c)], k) for c, k in columns]
    return create_table(con, name, columns, shaped(), primary_key, indexes)


def register(con, table, grain, sources, description, years=''):
    """Record what a pack table is and where it came from; the data inventory and the manifest read this."""
    con.execute('create table if not exists pack_table (name text primary key, grain text, sources text, '
                'description text, years text, rows integer)')
    rows = con.execute(f'select count(*) from "{table}"').fetchone()[0]
    con.execute('insert or replace into pack_table values (?, ?, ?, ?, ?, ?)',
                (table, grain, '; '.join(sources), description, years, rows))


def scope_filter(scope, alias='h'):
    """SQL condition on dim_hcp (aliased) selecting the HCPs of a scope, with one `?` for the id.

    hcp: that NPI. community: every HCP attached to the community, core members and orbit. community_core: only
    the community's core members. center: HCPs whose hospital links include the center. division_group: core,
    affiliates, referring or other.
    """
    if scope == 'hcp':
        return f'{alias}.npi = ?'
    if scope == 'community':
        return f'{alias}.community_id = cast(? as integer)'
    if scope == 'community_core':
        return f'{alias}.community_id = cast(? as integer) and {alias}.is_core = 1'
    if scope == 'center':
        return f'{alias}.npi in (select npi from hcp_center where ccn = ?)'
    if scope == 'division_group':
        return f'{alias}.division_group = ?'
    raise ValueError(f'Unknown scope {scope}')


def scope_label(con, scope, ident):
    """Plain name of a scope for titles, or None when the id does not exist."""
    if scope == 'hcp':
        row = con.execute('select name from dim_hcp where npi = ?', (str(ident),)).fetchone()
    elif scope in ('community', 'community_core'):
        row = con.execute('select short_label from dim_community where community_id = cast(? as integer)', (ident,)).fetchone()
        suffix = ' (core members only)' if scope == 'community_core' else ' (core members and attached HCPs)'
        return row[0] + suffix if row else None
    elif scope == 'center':
        row = con.execute('select name from dim_center where ccn = ?', (str(ident),)).fetchone()
    elif scope == 'division_group':
        return {'core': 'Core HCPs', 'affiliates': 'Affiliate HCPs', 'referring': 'Referring HCPs',
                'other': 'Other hematology-oncology HCPs'}.get(str(ident))
    else:
        return None
    return row[0] if row else None


__all__ = ['DB', 'DIVISION_GROUP_NAMES', 'PACK', 'SCOPES', 'SNAPSHOTS', 'connect', 'create_table', 'load_csv',
           'read_rows', 'register', 'scope_filter', 'scope_label', 'source', 'value']
