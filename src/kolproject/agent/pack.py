"""Build the agent data pack: one SQLite file, the text cards and a manifest, from published tables only.

    uv run kolproject-agent-pack            # build
    uv run kolproject-agent-pack --check    # re-run the checks on the built pack

Order: core tables, then each part (people, money, claims, referral), then cards, lookup aliases and the
full-text index. The pack is written beside the old one and swapped in only when every check passes.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
from datetime import datetime, timezone

from . import pack_dims
from .packbase import PROCESSED, SNAPSHOTS, connect
from .paths import CARDS, DB, GEO, MANIFEST, PACK, REFERENCE

PARTS = ['pack_people', 'pack_money', 'pack_claims', 'pack_referral']
# Column names that must never be in the pack: commercial or all-payer data, method diagnostics, restricted text.
FORBIDDEN_PARTS = ('commercial', 'all_payer', 'remit', 'ny_sparcs', 'ca_hcai', 'fl_ahca', 'stability', 'seed_consensus',
                   'jaccard', 'backtest', 'mover_', 'tier_reason', 'community_split', 'coi_statement', 'kite_', 'on_bms',
                   'bmtinfonet_n_', 'terms_of_interest', 'contextual_information', 'third_party_recipient', 'adr_ln', 'address')
FORBIDDEN_EXACT = {'tier', 'consensus', 'temporal', 'street', 'phone', 'email'}


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 22), b''):
            digest.update(block)
    return digest.hexdigest()


def parts():
    out = []
    for name in PARTS:
        try:
            out.append(importlib.import_module(f'{__package__}.{name}'))
        except ModuleNotFoundError as error:
            if name not in str(error):
                raise
            print('PACK part not present yet:', name)
    return out


def forbidden_columns(con):
    """(table, column) pairs whose name says they hold data this tool must not serve."""
    found = []
    for (table,) in con.execute("select name from sqlite_master where type = 'table' and name not like 'card%' and name not like 'sqlite_%'").fetchall():
        for row in con.execute(f'pragma table_info("{table}")'):
            name = row[1].lower()
            if name in FORBIDDEN_EXACT or any(part in name for part in FORBIDDEN_PARTS):
                found.append((table, row[1]))
    return found


def build():
    PACK.mkdir(parents=True, exist_ok=True)
    building = PACK / 'kol.building.sqlite'
    for suffix in ('', '-wal', '-shm'):
        (PACK / f'kol.building.sqlite{suffix}').unlink(missing_ok=True)
    con = connect(building)
    summary = {'dims': pack_dims.build(con)}
    checks = dict(pack_dims.check(con))
    facts = {}
    for module in parts():
        module.build(con)
        con.commit()
        for key, sentences in module.card_facts(con).items():
            facts.setdefault((key[0], str(key[1])), []).extend(sentences)
        checks.update(module.check(con))
    from . import cards, search  # imported here so the core tables can be built before these modules exist
    card_rows = cards.build_cards(con, facts)
    cards.write_cards(con, card_rows, CARDS)
    search.build_aliases(con)
    con.commit()
    bad = forbidden_columns(con)
    checks['pack_forbidden_columns'] = len(bad)
    checks['pack_cards_without_text'] = con.execute("select count(*) from card where trim(coalesce(text, '')) = ''").fetchone()[0]
    tables = [dict(r) for r in con.execute('select name, grain, sources, description, years, rows from pack_table order by name')]
    sources = sorted({s.strip() for t in tables for s in t['sources'].split(';') if s.strip()[:4].isdigit()})
    hashes = {}
    for item in sources:
        path = PROCESSED / (item + '.csv')
        hashes[item] = sha(path) if path.exists() else None
    geo_manifest = json.loads((GEO / 'geo_manifest.json').read_text()) if (GEO / 'geo_manifest.json').exists() else None
    _, product_name = pack_dims.product_file()
    manifest = {
        'built_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'data_as_of': max(SNAPSHOTS.values()), 'snapshots': SNAPSHOTS, 'product_file': product_name,
        'products_verified': not product_name.endswith('.draft.json'),
        'tables': tables, 'source_sha256': hashes, 'cards': len(card_rows), 'summary': summary,
        'geography': {k: geo_manifest[k] for k in geo_manifest if k != 'sources'} if geo_manifest else 'not built: points have no coordinates',
        'excluded_on_purpose': ['commercial-insurance and all-payer data', 'community stability, movers, backtests and composite scores',
                                'the 2009-2015 referral baseline', 'pair-level shared-patient edges', 'content restricted by source terms',
                                'conference data (none held)'],
        'checks': checks, 'forbidden_columns_found': bad,
        'licence_note': 'Academic, non-commercial demonstration. Referral tables derive from CareSet Systems DocGraph (CC BY-NC-SA 4.0 with a non-commercial addendum).',
    }
    con.execute('create table if not exists pack_meta (key text primary key, value text)')
    con.execute('insert or replace into pack_meta values (?, ?)', ('manifest', json.dumps(manifest)))
    con.commit()
    con.execute('pragma wal_checkpoint(truncate)')
    con.close()
    failures = {k: v for k, v in checks.items() if v}
    if failures:
        print('PACK CHECKS FAILED', failures, bad[:5])
        raise SystemExit(1)
    for suffix in ('-wal', '-shm'):
        (PACK / f'kol.building.sqlite{suffix}').unlink(missing_ok=True)
    os.replace(building, DB)
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    size = DB.stat().st_size / 1e6
    print(f'PACK BUILT {len(tables)} tables, {len(card_rows)} cards, {size:.0f} MB, products {product_name}, checks passed: {len(checks)}')
    return manifest


def check():
    """Re-run every check on the built pack without rebuilding it."""
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    checks = dict(pack_dims.check(con))
    for module in parts():
        checks.update(module.check(con))
    checks['pack_forbidden_columns'] = len(forbidden_columns(con))
    con.close()
    return checks


def missing_inputs():
    """Snapshot folders the build reads that hold no table on this machine (the three newest are not in Git)."""
    return [date for date in sorted(set(SNAPSHOTS.values())) if not any((PROCESSED / date).glob('*.csv'))]


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Build the agent data pack from the published tables.')
    parser.add_argument('--check', action='store_true', help='re-run the checks on the pack already built instead of rebuilding it')
    args = parser.parse_args()
    if not args.check:
        missing = missing_inputs()
        if missing:
            raise SystemExit('The pack is built from published tables that are not on this machine: '
                             + ', '.join(f'Data/processed/{date}' for date in missing)
                             + '. See "Get the data" in agent/USAGE.md.')
        build()
        return
    if not DB.exists():
        raise SystemExit('No pack to check. Build it first: uv run kolproject-agent-pack')
    checks = check()
    failures = {k: v for k, v in checks.items() if v}
    print(f'PACK CHECKS {"FAILED " + str(failures) if failures else "passed"}: {len(checks)}')
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
