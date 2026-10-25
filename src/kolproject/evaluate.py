"""Check published tables without requiring the excluded source-response caches."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
from pathlib import Path

from .common import ROOT, SNAPSHOT, dump, now, read_csv


def evaluate(root=ROOT, target=500):
    root = Path(root)
    cohort = root / 'Data' / 'processed' / SNAPSHOT
    utilization = root / 'Data' / 'processed' / '2026-10-15'
    tables = {
        name: read_csv(cohort / f'{name}.csv')
        for name in ['hcps', 'publications', 'authorships', 'coauthor_edges',
                     'general_payments_2024', 'payment_query_status']
    }
    h = tables['hcps']
    hmap = {row['hcp_id']: row for row in h}
    pmap = {row['pmid']: row for row in tables['publications']}
    identities = {(row['hcp_id'], row['npi']) for row in h}
    detail = read_csv(utilization / 'cms_provider_service.csv')
    features = read_csv(utilization / 'hcp_utilization_features.csv')
    supported = defaultdict(set)
    works = defaultdict(set)
    for row in tables['authorships']:
        if row['analysis_include'] == '1':
            supported[row['hcp_id']].add(row['pmid'])
            publication = pmap.get(row['pmid'], {})
            works[row['hcp_id']].add(publication.get('doi', '').strip().lower()
                                       or 'PMID:' + row['pmid'])

    def duplicates(rows, keys):
        return len(rows) - len({tuple(row[key] for key in keys) for row in rows})

    def invalid_identities(rows):
        return sum((row['hcp_id'], row['npi']) not in identities for row in rows)

    payment_totals = defaultdict(Decimal)
    payment_rows = Counter()
    for row in tables['general_payments_2024']:
        payment_totals[row['hcp_id']] += Decimal(row['amount_usd'])
        payment_rows[row['hcp_id']] += 1
    service_counts = Counter(row['hcp_id'] for row in detail)
    checks = {
        'hcp_count_mismatch': int(len(h) != target),
        'duplicate_hcp_ids': duplicates(h, ['hcp_id']),
        'duplicate_npis': duplicates(h, ['npi']),
        'invalid_npi_keys': sum(not (row['npi'].isascii() and row['npi'].isdigit()
            and len(row['npi']) == 10 and row['hcp_id'] == 'NPI' + row['npi']) for row in h),
        'ineligible_published_registry_fields': sum(row['npi_status'] != 'A'
            or row['npi_type'] != 'NPI-1' or row['npi_country'] != 'US'
            or not row['qualifying_taxonomy_codes'] for row in h),
        'duplicate_pmids': duplicates(tables['publications'], ['pmid']),
        'duplicate_authorships': duplicates(tables['authorships'], ['hcp_id', 'pmid']),
        'authorship_foreign_key_errors': sum(row['hcp_id'] not in hmap
            or row['pmid'] not in pmap for row in tables['authorships']),
        'authorship_identity_errors': invalid_identities(tables['authorships']),
        'ineligible_supporting_publications': sum(row['analysis_include'] == '1'
            and (row['pmid'] == '37419325'
                 or pmap.get(row['pmid'], {}).get('analysis_include') != '1')
            for row in tables['authorships']),
        'publication_evidence_errors': sum(len(supported[row['hcp_id']]) < 2
            or len(works[row['hcp_id']]) < 2
            or int(row['publications_2017_2025']) != len(supported[row['hcp_id']])
            or set(row['supporting_pmids'].split(';')) != supported[row['hcp_id']]
            for row in h),
        'duplicate_payment_ids': duplicates(tables['general_payments_2024'], ['record_id']),
        'payment_identity_errors': invalid_identities(tables['general_payments_2024']),
        'payment_reconciliation_errors': sum(not row['general_payments_usd_2024']
            or Decimal(row['general_payments_usd_2024']) != payment_totals[row['hcp_id']]
            or int(row['general_payment_rows_2024']) != payment_rows[row['hcp_id']] for row in h),
        'incomplete_payment_queries': sum(row['status'] != 'complete'
            for row in tables['payment_query_status']),
        'payment_query_coverage_errors': len(identities.symmetric_difference(
            {(row['hcp_id'], row['npi']) for row in tables['payment_query_status']})),
        'duplicate_payment_queries': duplicates(tables['payment_query_status'], ['hcp_id']),
        'edge_foreign_key_errors': sum(row['hcp_id_a'] not in hmap
            or row['hcp_id_b'] not in hmap for row in tables['coauthor_edges']),
        'self_edges': sum(row['hcp_id_a'] == row['hcp_id_b'] for row in tables['coauthor_edges']),
        'duplicate_undirected_edges': len(tables['coauthor_edges']) - len({
            tuple(sorted((row['hcp_id_a'], row['hcp_id_b']))) for row in tables['coauthor_edges']}),
        'synthetic_edges': sum(row['is_synthetic'].lower() not in {'0', 'false'}
            for row in tables['coauthor_edges']),
        'holdout_edge_leakage': sum('37419325' in row['evidence_pmids'].split(';')
            for row in tables['coauthor_edges']),
        'pagerank_sum_error': int(abs(sum(float(row['pagerank']) for row in h) - 1) > 1e-8),
        'utilization_identity_errors': invalid_identities(detail) + invalid_identities(features),
        'utilization_feature_count_mismatch': int(len(features) != target),
        'duplicate_utilization_hcps': duplicates(features, ['hcp_id']),
        'utilization_feature_coverage_errors': len(identities.symmetric_difference(
            {(row['hcp_id'], row['npi']) for row in features})),
        'duplicate_utilization_features': duplicates(features, ['hcp_id', 'service_year',
            'source_dataset_version_id', 'feature_version']),
        'duplicate_service_keys': duplicates(detail, ['npi', 'service_year', 'hcpcs_code',
            'place_of_service', 'source_dataset_version_id']),
        'unexpected_service_year': sum(row['service_year'] != '2024' for row in detail + features),
        'incomplete_utilization_queries': sum(row['query_status'] != 'complete' for row in features),
        'service_count_errors': sum(int(row['published_service_rows']) != service_counts[row['hcp_id']]
            for row in features),
        'unobserved_activity_errors': sum(not service_counts[row['hcp_id']]
            and (row['utilization_data_status'] != 'no_published_services'
                 or row['has_any_published_service'] != '0'
                 or row['all_published_medicare_payment_estimate_usd'] != '') for row in features),
    }
    divisions = root / 'Data' / 'processed' / '2026-10-24'
    division_counts = {}
    if (divisions / 'hcp_divisions.csv').exists():
        people = read_csv(divisions / 'hcp_divisions.csv')
        centers = read_csv(divisions / 'cart_centers.csv')
        division_counts = {'hcp_divisions': len(people), 'cart_centers': len(centers)}
        checks.update({
            'duplicate_division_npis': duplicates(people, ['npi']),
            'division_core_coverage_errors': len(identities.symmetric_difference(
                {(row['hcp_id'], row['npi']) for row in people if row['is_core'] == '1'})),
            'division_core_label_errors': sum((row['is_core'] == '1') != (row['division'] == 'core') for row in people),
            'referrer_center_contamination': sum(row['division'] == 'referring_proxy'
                and (row['center_ccn_links'] != '0' or row['core_org_links'] != '0' or row['marker_count'] == '0')
                for row in people),
            'affiliate_without_structural_link': sum(row['division'] in {'affiliate_A', 'affiliate_B'}
                and row['core_ccn_links'] == '0' and row['core_org_links'] == '0' for row in people),
            'duplicate_center_ccns': duplicates(centers, ['ccn']),
            'center_without_evidence': sum(not row['evidence'] for row in centers),
        })
    files = sorted((root / 'Data' / 'processed').rglob('*'))
    return {
        'validated_at_utc': now(),
        'scope': 'Published-table consistency only; source responses and registry identities are not reverified.',
        'counts': {**{name: len(rows) for name, rows in tables.items()},
                   'cms_provider_service': len(detail), 'hcp_utilization_features': len(features), **division_counts},
        'checks': checks,
        'failures': [name for name, value in checks.items() if value],
        'passed': not any(checks.values()),
        'processed_files_sha256': {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in files if path.is_file()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluations' / 'processed_validation.json')
    args = parser.parse_args()
    report = evaluate()
    dump(args.output, report)
    print('PROCESSED VALIDATION', 'PASSED' if report['passed'] else 'FAILED', args.output)
    if not report['passed']:
        print('Failed checks:', ', '.join(report['failures']))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
