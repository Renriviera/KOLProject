"""Check published tables without requiring the excluded source-response caches."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
from pathlib import Path

from .common import ROOT, SNAPSHOT, dump, now, read_csv
from .extensions import cli as extensions
from .volume_billing import cli as volume_billing


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
    research = root / 'Data' / 'processed' / '2026-11-11'
    research_counts = {}
    if (research / 'research_payments_2019_2025.csv').exists():
        rows = read_csv(research / 'research_payments_2019_2025.csv')
        research_features = read_csv(research / 'hcp_research_payment_features.csv')
        research_counts = {'research_payments_2019_2025': len(rows), 'hcp_research_payment_features': len(research_features)}
        research_rows = Counter(row['hcp_id'] for row in rows)
        research_totals = defaultdict(Decimal)
        record_amounts = defaultdict(set)
        for row in rows:
            research_totals[row['hcp_id']] += Decimal(row['amount_usd'])
            record_amounts[row['record_id']].add(row['amount_usd'])
        checks.update({
            'research_identity_errors': invalid_identities(rows) + invalid_identities(research_features),
            'duplicate_research_hcp_records': duplicates(rows, ['hcp_id', 'record_id']),
            'research_unmatched_rows': sum(not row['pi_slots'] and row['is_covered_recipient'] != '1' for row in rows),
            'research_unexpected_program_year': sum(not 2019 <= int(row['program_year']) <= 2025 for row in rows),
            'research_record_amount_conflicts': sum(len(values) > 1 for values in record_amounts.values()),
            'research_lymphoma_flag_errors': sum(row['lymphoma_cart_product_mentioned'] == '1'
                and row['cart_product_mentioned'] != '1' for row in rows),
            'research_feature_coverage_errors': len(identities.symmetric_difference(
                {(row['hcp_id'], row['npi']) for row in research_features})),
            'duplicate_research_features': duplicates(research_features, ['hcp_id']),
            'research_reconciliation_errors': sum(
                int(row['research_rows_2019_2025']) != research_rows[row['hcp_id']]
                or sum(int(row[f'research_rows_{year}']) for year in range(2019, 2026)) != research_rows[row['hcp_id']]
                or (row['research_record_usd_2019_2025'] != '') != bool(research_rows[row['hcp_id']])
                or (row['research_record_usd_2019_2025'] != ''
                    and abs(Decimal(row['research_record_usd_2019_2025']) - research_totals[row['hcp_id']]) > Decimal('0.01'))
                or (row['research_data_status'] == 'published_records') != bool(research_rows[row['hcp_id']])
                for row in research_features),
        })
    if (research / 'general_payments_2019_2025.csv').exists():
        general = read_csv(research / 'general_payments_2019_2025.csv')
        general_years = read_csv(research / 'hcp_general_payments_by_year.csv')
        general_features = read_csv(research / 'hcp_general_payment_features.csv')
        research_counts.update({'general_payments_2019_2025': len(general),
                                'hcp_general_payments_by_year': len(general_years),
                                'hcp_general_payment_features': len(general_features)})
        year_rows, year_usd = Counter(), defaultdict(Decimal)
        hcp_rows, hcp_usd = Counter(), defaultdict(Decimal)
        for row in general:
            year_rows[row['hcp_id'], row['program_year']] += 1
            year_usd[row['hcp_id'], row['program_year']] += Decimal(row['amount_usd'])
            hcp_rows[row['hcp_id']] += 1
            hcp_usd[row['hcp_id']] += Decimal(row['amount_usd'])
        groups = ['consulting', 'honoraria', 'speaking_non_cme', 'medical_education', 'grant', 'hospitality', 'other']
        api_2024 = {row['record_id']: row for row in tables['general_payments_2024']}
        bulk_2024 = {row['record_id']: row for row in general if row['program_year'] == '2024'}
        cent = Decimal('0.01')
        checks.update({
            'general_history_identity_errors': invalid_identities(general) + invalid_identities(general_years)
                + invalid_identities(general_features),
            'duplicate_general_history_ids': duplicates(general, ['record_id']),
            'general_history_unexpected_year': sum(not 2019 <= int(row['program_year']) <= 2025 for row in general),
            'general_history_2024_mismatch': len(set(api_2024) ^ set(bulk_2024)) + sum(
                Decimal(api_2024[key]['amount_usd']) != Decimal(bulk_2024[key]['amount_usd'])
                or api_2024[key]['lymphoma_cart_product_mentioned'] != bulk_2024[key]['lymphoma_cart_product_mentioned']
                for key in set(api_2024) & set(bulk_2024)),
            'general_history_flag_errors': sum(row['lymphoma_cart_product_mentioned'] == '1'
                and row['cart_product_mentioned'] != '1' for row in general),
            'general_year_grid_errors': int({(row['hcp_id'], row['npi'], row['program_year']) for row in general_years}
                != {(hcp_id, npi, str(year)) for hcp_id, npi in identities for year in range(2019, 2026)})
                + duplicates(general_years, ['hcp_id', 'program_year']),
            'general_year_reconciliation_errors': sum(
                int(row['general_payment_rows']) != year_rows[row['hcp_id'], row['program_year']]
                or abs(Decimal(row['general_payments_usd']) - year_usd[row['hcp_id'], row['program_year']]) > cent
                or abs(sum(Decimal(row[f'{group}_usd']) for group in groups) - Decimal(row['general_payments_usd'])) > cent * 7
                for row in general_years),
            'general_feature_coverage_errors': len(identities.symmetric_difference(
                {(row['hcp_id'], row['npi']) for row in general_features})) + duplicates(general_features, ['hcp_id']),
            'general_feature_reconciliation_errors': sum(
                int(row['general_payment_rows_2019_2025']) != hcp_rows[row['hcp_id']]
                or abs(Decimal(row['general_payments_usd_2019_2025']) - hcp_usd[row['hcp_id']]) > cent
                or (row['general_data_status'] == 'published_records') != bool(hcp_rows[row['hcp_id']])
                for row in general_features),
        })
    volume_counts = {}
    processed = root / 'Data' / 'processed'
    checks['volume_billing_snapshot_missing'] = int(not (processed / '2026-11-19').exists())
    if (processed / '2026-11-19').exists():
        checks.update(volume_billing.check(processed))
        volume_counts = {table: len(read_csv(processed / '2026-11-19' / f'{table}.csv'))
                         for table in volume_billing.tables() if (processed / '2026-11-19' / f'{table}.csv').exists()}
    extension_counts = {}
    checks['extensions_snapshot_missing'] = int(not (processed / '2026-11-20').exists())
    if (processed / '2026-11-20').exists():
        checks.update(extensions.check(processed))
        extension_counts = {table: extensions.row_count(processed / '2026-11-20' / f'{table}.csv')
                            for table in extensions.tables() if (processed / '2026-11-20' / f'{table}.csv').exists()}
    files = sorted(processed.rglob('*'))
    return {
        'validated_at_utc': now(),
        'scope': 'Published-table consistency only; source responses and registry identities are not reverified.',
        'counts': {**{name: len(rows) for name, rows in tables.items()},
                   'cms_provider_service': len(detail), 'hcp_utilization_features': len(features),
                   **division_counts, **research_counts, **volume_counts, **extension_counts},
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
