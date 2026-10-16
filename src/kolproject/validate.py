"""Release gates for identities, publication evidence, provenance and joins."""
import collections
import hashlib
import itertools
from datetime import datetime
from .common import ROOT, PROCESSED, INTERIM, load, dump, read_csv, now
from .identity import TAXONOMIES, HOLDOUT, active_oncology
from .deduplicate import fuzzy_pairs

def validate(target=500):
    hcps = read_csv(PROCESSED / 'hcps.csv')
    publications = read_csv(PROCESSED / 'publications.csv')
    authorships = read_csv(PROCESSED / 'authorships.csv')
    edges = read_csv(PROCESSED / 'coauthor_edges.csv')
    payments = read_csv(PROCESSED / 'general_payments_2024.csv')
    status = read_csv(PROCESSED / 'payment_query_status.csv')
    reviews = read_csv(PROCESSED / 'fuzzy_duplicate_review.csv')
    people = load(INTERIM / 'selected_people.json')
    hmap = {h['hcp_id']: h for h in hcps}
    pmap = {p['pmid']: p for p in publications}
    counts = collections.defaultdict(set)
    work_counts = collections.defaultdict(set)
    for a in authorships:
        if a['analysis_include'] == '1':
            counts[a['hcp_id']].add(a['pmid'])
            work_counts[a['hcp_id']].add(pmap[a['pmid']]['doi'].strip().lower() or 'PMID:' + a['pmid'])
    dates_invalid = []
    date_checks = 0
    for name, rows in [('hcps', hcps), ('publications', publications), ('authorships', authorships), ('payments', payments), ('payment_status', status)]:
        for i, row in enumerate(rows):
            for field, value in row.items():
                if not value or not (field.endswith('_date') or field.endswith('_on') or field.endswith('_utc')):
                    continue
                try:
                    if field.endswith('_utc'):
                        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        assert parsed.tzinfo is not None
                    else:
                        datetime.strptime(value, '%Y-%m-%d')
                    date_checks += 1
                except Exception:
                    dates_invalid.append([name, i, field, value])
    checks = {
        'hcp_count': len(hcps), 'expected_hcp_count': target,
        'duplicate_npis': len(hcps) - len({h['npi'] for h in hcps}),
        'duplicate_hcp_ids': len(hcps) - len(hmap),
        'duplicate_normalized_registered_names': len(hcps) - len({' '.join(h['display_name'].lower().split()) for h in hcps}),
        'inactive_or_ineligible_npi_records': sum(not active_oncology(p['accepted']['record']) for p in people),
        'hcps_below_two_distinct_pmids': sum(len(counts[h['hcp_id']]) < 2 for h in hcps),
        'hcps_below_two_distinct_doi_or_pmid_works': sum(len(work_counts[h['hcp_id']]) < 2 for h in hcps),
        'publication_count_mismatches': sum(int(h['publications_2017_2025']) != len(counts[h['hcp_id']]) for h in hcps),
        'supporting_pmid_list_mismatches': sum(set(h['supporting_pmids'].split(';')) != counts[h['hcp_id']] for h in hcps),
        'duplicate_hcp_pmid_links': len(authorships) - len({(a['hcp_id'], a['pmid']) for a in authorships}),
        'authorship_foreign_key_errors': sum(a['hcp_id'] not in hmap or a['pmid'] not in pmap for a in authorships),
        'duplicate_payment_record_ids': len(payments) - len({p['record_id'] for p in payments}),
        'payment_npi_mismatches': sum(p['npi'] != hmap[p['hcp_id']]['npi'] for p in payments),
        'incomplete_payment_queries': sum(s['status'] != 'complete' for s in status),
        'duplicate_undirected_edges': len(edges) - len({tuple(sorted([e['hcp_id_a'], e['hcp_id_b']])) for e in edges}),
        'self_edges': sum(e['hcp_id_a'] == e['hcp_id_b'] for e in edges),
        'guideline_holdout_leakage': sum(HOLDOUT in e['evidence_pmids'].split(';') for e in edges),
        'fuzzy_review_unresolved': sum(r['decision'].startswith('requires_manual_review') for r in reviews),
        'fuzzy_pairs_reviewed': len(reviews),
        'all_selected_identity_pairs_compared': len(hcps) * (len(hcps) - 1) // 2,
        'date_values_checked': date_checks, 'invalid_date_values': len(dates_invalid),
        'missing_npi_retrieval_timestamps': sum(not h['npi_retrieved_at_utc'] for h in hcps),
        'missing_authorship_source_urls': sum(not a['source_url'] for a in authorships),
        'pagerank_sum': sum(float(h['pagerank']) for h in hcps),
        'payment_rows_total_usd': round(sum(float(p['amount_usd']) for p in payments), 2),
        'hcp_payment_totals_usd': round(sum(float(h['general_payments_usd_2024']) for h in hcps if h['general_payments_usd_2024'] != ''), 2),
        'minimum_supported_publications': min(len(counts[h['hcp_id']]) for h in hcps),
        'validated_at_utc': now(),
    }
    zero_fields = ['duplicate_npis', 'duplicate_hcp_ids', 'duplicate_normalized_registered_names',
                   'inactive_or_ineligible_npi_records', 'hcps_below_two_distinct_pmids',
                   'hcps_below_two_distinct_doi_or_pmid_works', 'publication_count_mismatches',
                   'supporting_pmid_list_mismatches', 'duplicate_hcp_pmid_links', 'authorship_foreign_key_errors',
                   'duplicate_payment_record_ids', 'payment_npi_mismatches', 'incomplete_payment_queries',
                   'duplicate_undirected_edges', 'self_edges', 'guideline_holdout_leakage',
                   'fuzzy_review_unresolved', 'invalid_date_values', 'missing_npi_retrieval_timestamps', 'missing_authorship_source_urls']
    failures = [k for k in zero_fields if checks[k] != 0]
    if len(hcps) != target:
        failures.append('hcp_count')
    if abs(checks['pagerank_sum'] - 1) > 1e-8:
        failures.append('pagerank_sum')
    if checks['payment_rows_total_usd'] != checks['hcp_payment_totals_usd']:
        failures.append('payment_reconciliation')
    checks['failures'] = failures
    checks['passed'] = not failures
    dump(ROOT / 'evaluations' / 'validation_report.json', checks)
    print(checks)
    if failures:
        raise AssertionError('Release gates failed: ' + ', '.join(failures))
    return checks

