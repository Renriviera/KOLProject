"""Load a CareSet DocGraph Hop Teaming file into the division snapshot.

CareSet releases the file only after a non-commercial attestation that the
project owner submits on careset.com; the download link arrives by email. Put
the delivered CSV (optionally .gz) anywhere local and run:

    uv run kolproject-careset <path> --year 2024 --kind annual

The raw file stays local. Filtered directed edges touching the division
universe or a CAR-T center organization NPI are written to Data/interim by
default; pass --publish-edges to write them to Data/processed once CareSet has
confirmed that non-commercial redistribution of the filtered table is allowed.
Per-NPI flow features and HRR-to-center aggregates are always published.
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import io
import shutil
import zipfile
from pathlib import Path
from .common import ROOT, dump, now, read_csv, write_csv
from .divisions_source import SNAPSHOT, RAW, INTERIM, OUT

FIELDS = ['FROM_NPI', 'TO_NPI', 'PATIENT_COUNT', 'TRANSACTION_COUNT', 'AVERAGE_DAY_WAIT', 'STD_DAY_WAIT']
LICENSE = ('CC BY-NC-SA 4.0 with the CareSet Noncommercial Data License Addendum: attribution to CareSet Systems, '
           'non-commercial use, derived tables shared under the same license, no re-identification of individuals')

def open_text(path: Path):
    """Text stream over a CSV, .gz, or single-member .zip."""
    path = Path(path)
    if path.suffix == '.gz':
        return io.TextIOWrapper(gzip.open(path, 'rb'), encoding='utf-8', newline='')
    if path.suffix == '.zip':
        archive = zipfile.ZipFile(path)
        members = [m for m in archive.namelist() if not m.endswith('/') and not m.startswith('__MACOSX')]
        assert len(members) == 1, f'Expected one file in {path.name}, found {members}'
        return io.TextIOWrapper(archive.open(members[0]), encoding='utf-8', newline='')
    return path.open(newline='', encoding='utf-8')

def normalize_header(header):
    mapping = {}
    for name in header:
        key = name.strip().strip('"').upper().replace(' ', '_')
        if key in FIELDS:
            mapping[name] = key
    missing = set(FIELDS) - set(mapping.values())
    if missing:
        raise ValueError(f'CareSet file is missing columns {sorted(missing)}; header was {list(header)}')
    return mapping

def load_context(processed: Path = OUT):
    people = read_csv(processed / 'hcp_divisions.csv')
    centers = read_csv(processed / 'cart_centers.csv')
    division = {r['npi']: r['division'] for r in people}
    hrr = {r['npi']: r['hrr'] for r in people}
    center_ccns_by_npi = collections.defaultdict(set)
    for r in people:
        for ccn in r['hospital_ccns'].split(';'):
            if ccn and ccn in {c['ccn'] for c in centers}:
                center_ccns_by_npi[r['npi']].add(ccn)
    org_npi_to_ccn = {c['organization_npi']: c['ccn'] for c in centers if c['organization_npi']}
    return {'division': division, 'hrr': hrr, 'center_ccns': center_ccns_by_npi, 'org_to_ccn': org_npi_to_ccn,
            'center_hrr': {c['ccn']: c['hrr'] for c in centers}}

def filter_edges(rows, ctx):
    """Keep directed edges where either end is in the universe or is a center organization NPI."""
    division, org = ctx['division'], ctx['org_to_ccn']
    for r in rows:
        a, b = r['FROM_NPI'], r['TO_NPI']
        if a in division or b in division or a in org or b in org:
            yield {'from_npi': a, 'to_npi': b, 'patient_count': r['PATIENT_COUNT'], 'transaction_count': r['TRANSACTION_COUNT'],
                   'average_day_wait': r['AVERAGE_DAY_WAIT'], 'std_day_wait': r['STD_DAY_WAIT'],
                   'from_division': division.get(a, 'organization' if a in org else 'outside_universe'),
                   'to_division': division.get(b, 'organization' if b in org else 'outside_universe')}

def stream_edges(reader, mapping, ctx, counter):
    """Fast path over csv.reader rows: positional lookups, no per-row dict until a row is kept."""
    header = next(reader)
    index = {mapping[name]: i for i, name in enumerate(header) if name in mapping}
    fa, ta, pc, tc, aw, sw = (index[k] for k in FIELDS)
    division, org = ctx['division'], ctx['org_to_ccn']
    for row in reader:
        counter[0] += 1
        a, b = row[fa], row[ta]
        if a in division or b in division or a in org or b in org:
            yield {'from_npi': a, 'to_npi': b, 'patient_count': row[pc], 'transaction_count': row[tc],
                   'average_day_wait': row[aw], 'std_day_wait': row[sw],
                   'from_division': division.get(a, 'organization' if a in org else 'outside_universe'),
                   'to_division': division.get(b, 'organization' if b in org else 'outside_universe')}

def target_centers(npi, ctx):
    """Center CCNs a flow reaches: the organization's own CCN, or the physician's center affiliations."""
    if npi in ctx['org_to_ccn']:
        return {ctx['org_to_ccn'][npi]}
    return ctx['center_ccns'].get(npi, set())

def aggregate(edges, ctx, year, kind):
    """Per-NPI flow features and HRR-to-center patient flows."""
    features = collections.defaultdict(lambda: collections.Counter())
    partners = collections.defaultdict(set)
    waits = collections.defaultdict(list)
    flows = collections.Counter()
    flow_pairs = collections.Counter()
    for e in edges:
        a, b = e['from_npi'], e['to_npi']
        patients = int(float(e['patient_count'] or 0))
        centers = target_centers(b, ctx)
        if a in ctx['division']:
            features[a]['out_patients_total'] += patients
            if centers:
                features[a]['out_patients_to_centers'] += patients
                partners[a] |= centers
                waits[a].append(float(e['average_day_wait'] or 0))
                hrr = ctx['hrr'].get(a, '')
                if hrr and ctx['division'][a] == 'referring_proxy':
                    for ccn in centers:
                        flows[(hrr, ccn)] += patients
                        flow_pairs[(hrr, ccn)] += 1
        if b in ctx['division']:
            features[b]['in_patients_total'] += patients
            if ctx['division'].get(a) == 'referring_proxy':
                features[b]['in_patients_from_referrers'] += patients
                features[b]['referrer_sources'] += 1
    rows = []
    for npi in sorted(set(features) | set(partners)):
        f = features[npi]
        rows.append({'npi': npi, 'division': ctx['division'][npi], 'year': year, 'kind': kind,
                     'out_patients_total': f['out_patients_total'], 'out_patients_to_centers': f['out_patients_to_centers'],
                     'distinct_center_partners': len(partners[npi]),
                     'mean_day_wait_to_centers': round(sum(waits[npi]) / len(waits[npi]), 1) if waits[npi] else '',
                     'in_patients_total': f['in_patients_total'], 'in_patients_from_referrers': f['in_patients_from_referrers'],
                     'referrer_sources': f['referrer_sources'], 'observed_shared_patient_edge': 1})
    flow_rows = [{'hrr': h, 'ccn': c, 'year': year, 'kind': kind, 'patients': n, 'referrer_pairs': flow_pairs[(h, c)],
                  'same_hrr': int(ctx['center_hrr'].get(c, '') == h)} for (h, c), n in sorted(flows.items())]
    return rows, flow_rows

def ingest(path, year, kind='annual', publish_edges=False, processed: Path = OUT, interim: Path = INTERIM, raw: Path = RAW):
    """Stream the delivered file once: filtered edges go straight to disk while aggregates accumulate."""
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 24), b''):
            digest.update(chunk)
    digest = digest.hexdigest()
    ctx = load_context(processed)
    stem = f'shared_patient_{kind}_{year}'
    edge_dir = processed if publish_edges else interim / 'careset'
    edge_dir.mkdir(parents=True, exist_ok=True)
    edge_path = edge_dir / f'{stem}_edges.csv'
    counter = [0]
    kept = 0
    edge_fields = ['from_npi', 'to_npi', 'patient_count', 'transaction_count', 'average_day_wait', 'std_day_wait',
                   'from_division', 'to_division', 'year', 'kind', 'source_sha256']
    with open_text(path) as stream, edge_path.open('w', newline='', encoding='utf-8') as out:
        header = next(csv.reader(stream))
        mapping = normalize_header(header)
        stream.seek(0)
        writer = csv.DictWriter(out, fieldnames=edge_fields)
        writer.writeheader()

        def edges():
            nonlocal kept
            for e in stream_edges(csv.reader(stream), mapping, ctx, counter):
                kept += 1
                writer.writerow({**e, 'year': year, 'kind': kind, 'source_sha256': digest[:16]})
                if kept % 500000 == 0:
                    print('CARESET', stem, 'rows', counter[0], 'kept', kept, flush=True)
                yield e
        features, flows = aggregate(edges(), ctx, year, kind)
    write_csv(processed / f'{stem}_features.csv', features)
    write_csv(processed / f'{stem}_hrr_center_flows.csv', flows)
    meta_dir = raw / 'careset'
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta = {'source': 'CareSet DocGraph Hop Teaming', 'kind': kind, 'year': year, 'license': LICENSE,
            'local_file': str(path), 'sha256': digest, 'bytes': path.stat().st_size, 'ingested_at_utc': now(),
            'rows_total': counter[0], 'rows_kept': kept, 'feature_rows': len(features), 'flow_rows': len(flows),
            'edges_published': bool(publish_edges),
            'edge_table': str(edge_path.relative_to(ROOT)) if edge_path.is_relative_to(ROOT) else str(edge_path)}
    dump(meta_dir / f'{stem}.meta.json', meta)
    dump(processed / f'{stem}_metadata.json', {k: v for k, v in meta.items() if k != 'local_file'})
    print('CARESET', stem, 'rows', counter[0], 'kept', kept, 'features', len(features), 'flows', len(flows), flush=True)
    return meta

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('path', help='CareSet CSV, .gz, or .zip as delivered')
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--kind', choices=['annual', 'same_day'], default='annual')
    parser.add_argument('--publish-edges', action='store_true')
    args = parser.parse_args(argv)
    ingest(args.path, args.year, args.kind, args.publish_edges)

if __name__ == '__main__':
    main()
