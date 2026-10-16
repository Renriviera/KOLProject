"""Two separate utilization tables; the original HCP table is never rewritten."""
from __future__ import annotations

import collections
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
import re
import time
from zipfile import ZipFile

import hydra
import pandas as pd
from omegaconf import DictConfig
from .common import ROOT, dump, load, now, read_csv, write_csv
from .utilization_source import COHORT, DATASET_ID, INTERIM, OUT, RAW, RELEASE_DATE, SNAPSHOT, YEAR, collect, sha

os.environ['TZ'] = 'UTC'
if hasattr(time, 'tzset'):
    time.tzset()

CODE_VERSION = '2024-v1'
FEATURE_VERSION = '1.0.0'
GROUPS = {
    'rituximab_iv': ['J9312', 'Q5115', 'Q5119', 'Q5123'],
    'rituximab_sc': ['J9311'],
    'polatuzumab': ['J9309'],
    'tafasitamab': ['J9349'],
    'lymphoma_cart_product': ['Q2041', 'Q2042', 'Q2053', 'Q2054'],
    'cart_administration_all_indications': ['0540T'],
}
UNITS = {'rituximab_iv': '10 mg billed rituximab', 'rituximab_sc': '10 mg billed rituximab with hyaluronidase',
         'polatuzumab': '1 mg billed polatuzumab vedotin-piiq', 'tafasitamab': '2 mg billed tafasitamab-cxix',
         'lymphoma_cart_product': 'product therapeutic dose billing unit',
         'cart_administration_all_indications': 'CAR-T administration service'}
CODE_TO_GROUP = {c: g for g, codes in GROUPS.items() for c in codes}
NUMERIC_MAP = {
    'Tot_Benes': 'beneficiaries', 'Tot_Srvcs': 'services_or_units',
    'Tot_Bene_Day_Srvcs': 'beneficiary_days', 'Avg_Sbmtd_Chrg': 'average_submitted_charge_usd',
    'Avg_Mdcr_Alowd_Amt': 'average_allowed_amount_usd', 'Avg_Mdcr_Pymt_Amt': 'average_medicare_payment_usd',
    'Avg_Mdcr_Stdzd_Amt': 'average_standardized_medicare_payment_usd',
}
TEXT_MAP = {'Rndrng_Prvdr_Last_Org_Name': 'cms_last_name', 'Rndrng_Prvdr_First_Name': 'cms_first_name',
            'Rndrng_Prvdr_MI': 'cms_middle_initial', 'Rndrng_Prvdr_Crdntls': 'cms_credentials',
            'Rndrng_Prvdr_Ent_Cd': 'cms_entity_type', 'Rndrng_Prvdr_City': 'cms_city',
            'Rndrng_Prvdr_State_Abrvtn': 'cms_state', 'Rndrng_Prvdr_Zip5': 'cms_zip5',
            'Rndrng_Prvdr_Cntry': 'cms_country', 'Rndrng_Prvdr_Type': 'cms_provider_type',
            'Rndrng_Prvdr_Mdcr_Prtcptg_Ind': 'cms_medicare_participation_indicator',
            'HCPCS_Cd': 'hcpcs_code', 'HCPCS_Desc': 'hcpcs_description',
            'HCPCS_Drug_Ind': 'cms_hcpcs_drug_indicator', 'Place_Of_Srvc': 'place_of_service'}

def dec(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Non-finite CMS numeric value')
    return number

def money(value):
    return str(dec(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def sum_metric(rows, field):
    return sum((dec(r[field]) for r in rows), Decimal(0))

def estimated_payment(rows, average_field='average_medicare_payment_usd'):
    if not rows:
        return ''
    return money(sum((dec(r['services_or_units']) * dec(r[average_field]) for r in rows), Decimal(0)))

def verify_preservation():
    baseline = load(INTERIM / 'preserved_cohort.json')['files_sha256']
    bad = [path for path, expected in baseline.items() if sha(ROOT / path) != expected]
    if bad:
        raise ValueError('Original tables modified: ' + ', '.join(bad))
    return baseline

def codebook():
    """Check all selected Level II descriptions against all four 2024 releases."""
    observations = collections.defaultdict(list)
    for path in sorted((RAW / 'hcpcs_2024').glob('*.zip')):
        meta = load(path.with_suffix('.meta.json'))
        quarter = next(q for q in ['january', 'april', 'july', 'october'] if q in meta['source_url'])
        descriptions = collections.defaultdict(list)
        with ZipFile(path) as archive:
            files = [n for n in archive.namelist() if n.lower().endswith('.txt') and '_anweb' in n.lower()]
            assert len(files) == 1
            for line in archive.read(files[0]).decode('cp1252').splitlines():
                # CMS 2024 contractor layout: code 1–5, record type 11, long descriptor 12–91.
                if len(line) >= 91 and line[:5] in CODE_TO_GROUP and line[10] in '34':
                    descriptions[line[:5]].append(line[11:91].strip())
        for code, pieces in descriptions.items():
            observations[code].append({'quarter': quarter, 'description': ' '.join(pieces),
                                       'source_url': meta['source_url'], 'source_id': meta['source_id'],
                                       'retrieved_at_utc': meta['retrieved_at_utc']})
    result = []
    for code, group in CODE_TO_GROUP.items():
        if code == '0540T':
            candidates = [load(p) for p in (RAW / 'cms_documentation').glob('*.meta.json')]
            source = next(x for x in candidates if x['source_url'].endswith('clm104c32.pdf'))
            evidence = [{'source_url': source['source_url'], 'source_id': source['source_id'],
                         'retrieved_at_utc': source['retrieved_at_utc'],
                         'section': '400.2 and 400.2.3: 0540T ends 2024-12-31; 38228 starts 2025-01-01'}]
            description = 'Professional CAR-T administration service; all indications (paraphrase)'
        else:
            evidence = observations[code]
            assert {x['quarter'] for x in evidence} == {'january', 'april', 'july', 'october'}, code
            assert len({x['description'] for x in evidence}) == 1, 'Review within-year code changes: ' + code
            description = evidence[0]['description']
        result.append({'hcpcs_code': code, 'feature_group': group, 'description': description,
                       'service_year': YEAR, 'code_list_version': CODE_VERSION,
                       'billing_unit': UNITS[group], 'selected_drug': group != 'cart_administration_all_indications',
                       'interpretation': 'Proxy for product/service activity; no patient diagnosis or indication confirmed',
                       'evidence': evidence})
    return result

def build_detail(hcp, result, built_at):
    rows = []
    for record in result['records']:
        row = {'hcp_id': hcp['hcp_id'], 'npi': hcp['npi'], 'service_year': YEAR}
        row.update({new: record[old] for old, new in TEXT_MAP.items()})
        row.update({new: str(dec(record[old])) for old, new in NUMERIC_MAP.items()})
        group = CODE_TO_GROUP.get(row['hcpcs_code'], 'other')
        row.update(feature_group=group, selected_code=int(group != 'other'),
                   selected_drug_code=int(group not in ['other', 'cart_administration_all_indications']),
                   billing_unit_for_selected_code=UNITS.get(group, ''),
                   code_list_version=CODE_VERSION,
                   estimated_medicare_payment_usd=estimated_payment([row]),
                   estimated_allowed_amount_usd=estimated_payment([row], 'average_allowed_amount_usd'),
                   source_dataset_version_id=DATASET_ID, source_release_date=RELEASE_DATE,
                   source_id=record['_source']['source_id'], source_url=record['_source']['source_url'],
                   retrieved_at_utc=record['_source']['retrieved_at_utc'],
                   collection_snapshot_date=SNAPSHOT, row_assembled_at_utc=built_at)
        rows.append(row)
    return rows

def summarize(hcp, rows, query, built_at, codes_sha):
    if query['query_status'] != 'complete':
        raise ValueError('Incomplete collection cannot be represented as absent utilization')
    selected = [r for r in rows if r['selected_code'] == 1]
    drugs = [r for r in rows if r['selected_drug_code'] == 1]
    row = {'hcp_id': hcp['hcp_id'], 'npi': hcp['npi'], 'service_year': YEAR,
           'source_dataset_version_id': DATASET_ID, 'source_release_date': RELEASE_DATE,
           'collection_snapshot_date': SNAPSHOT, 'cohort_snapshot_date': '2026-10-09',
           'feature_version': FEATURE_VERSION, 'code_list_version': CODE_VERSION, 'code_list_sha256': codes_sha,
           'query_status': 'complete',
           'utilization_data_status': ('selected_codes_observed' if selected else 'published_services_no_selected_codes' if rows else 'no_published_services'),
           'has_any_published_service': int(bool(rows)), 'has_any_selected_code': int(bool(selected)),
           'has_any_selected_drug_code': int(bool(drugs)), 'published_service_rows': len(rows),
           'published_hcpcs_count': len({r['hcpcs_code'] for r in rows}),
           'published_facility_rows': sum(r['place_of_service'] == 'F' for r in rows),
           'published_nonfacility_rows': sum(r['place_of_service'] == 'O' for r in rows),
           'selected_codes_observed': ';'.join(sorted({r['hcpcs_code'] for r in selected})),
           'cms_provider_types_observed': ';'.join(sorted({r['cms_provider_type'] for r in rows})),
           'all_published_medicare_payment_estimate_usd': estimated_payment(rows),
           'all_published_allowed_amount_estimate_usd': estimated_payment(rows, 'average_allowed_amount_usd'),
           'selected_drug_medicare_payment_estimate_usd': estimated_payment(drugs),
           'selected_drug_facility_payment_share': '',
           'source_first_retrieved_at_utc': min(s['retrieved_at_utc'] for s in query['sources']),
           'source_last_retrieved_at_utc': max(s['retrieved_at_utc'] for s in query['sources']),
           'query_completed_at_utc': query['query_completed_at_utc'],
           'source_request_ids': ';'.join(s['source_id'] for s in query['sources']),
           'features_computed_at_utc': built_at}
    if drugs and dec(row['selected_drug_medicare_payment_estimate_usd']) > 0:
        fac = [r for r in drugs if r['place_of_service'] == 'F']
        row['selected_drug_facility_payment_share'] = str((dec(estimated_payment(fac) or 0) / dec(row['selected_drug_medicare_payment_estimate_usd'])).quantize(Decimal('0.000001')))
    for group in GROUPS:
        subset = [r for r in rows if r['feature_group'] == group]
        row.update({group + '_observed': int(bool(subset)), group + '_published_rows': len(subset),
                    group + '_services_or_units_sum': str(sum_metric(subset, 'services_or_units')) if subset else '',
                    group + '_beneficiary_code_setting_days_sum': str(sum_metric(subset, 'beneficiary_days')) if subset else '',
                    group + '_max_beneficiaries_in_one_cell': str(max(dec(r['beneficiaries']) for r in subset)) if subset else '',
                    group + '_medicare_payment_estimate_usd': estimated_payment(subset)})
    return row

def build():
    verify_preservation()
    cohort = read_csv(COHORT)
    codes = codebook()
    codes_sha = hashlib.sha256(json.dumps(codes, sort_keys=True).encode()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    dump(ROOT / 'configs' / 'utilization_codes_2024.json', codes)
    built_at = now()
    detail, features = [], []
    for hcp in cohort:
        query = load(INTERIM / 'npi_queries' / (hcp['npi'] + '.json'))
        assert query['dataset_version_id'] == DATASET_ID and query['service_year'] == YEAR
        rows = build_detail(hcp, query, built_at)
        detail.extend(rows)
        features.append(summarize(hcp, rows, query, built_at, codes_sha))
    detail.sort(key=lambda r: (r['npi'], r['hcpcs_code'], r['place_of_service']))
    features.sort(key=lambda r: r['npi'])
    write_csv(OUT / 'cms_provider_service.csv', detail)
    write_csv(OUT / 'hcp_utilization_features.csv', features)
    dump(OUT / 'utilization_metadata.json', {
        'built_at_utc': built_at, 'collection_snapshot_date': SNAPSHOT, 'service_year': YEAR,
        'source_dataset_version_id': DATASET_ID, 'source_release_date': RELEASE_DATE,
        'cohort_path': str(COHORT.relative_to(ROOT)), 'cohort_sha256': sha(COHORT),
        'feature_version': FEATURE_VERSION, 'code_list_version': CODE_VERSION, 'code_list_sha256': codes_sha,
        'tables': {'cms_provider_service': len(detail), 'hcp_utilization_features': len(features)},
        'hcp_coverage': dict(collections.Counter(r['utilization_data_status'] for r in features)),
        'observed_hcps_by_group': {g: sum(r[g + '_observed'] for r in features) for g in GROUPS},
        'source_observed_from_utc': min(r['source_first_retrieved_at_utc'] for r in features),
        'source_observed_through_utc': max(r['source_last_retrieved_at_utc'] for r in features),
        'detail_key': ['npi', 'service_year', 'hcpcs_code', 'place_of_service', 'source_dataset_version_id'],
        'features_key': ['npi', 'service_year', 'source_dataset_version_id', 'feature_version'],
        'limitations': ['No published row does not imply no activity; suppression reason is unobservable.',
                       'Counts cover published Medicare FFS Part B non-institutional cells only.',
                       'Drug-code activity does not establish DLBCL diagnosis or CAR-T eligibility.',
                       '0540T covers CAR-T administration across indications; it is not lymphoma-specific.',
                       'Drug services are code-specific billing units; no services sum across incompatible families is provided.',
                       'Beneficiary-days summed across codes/settings can repeat the same person-day.',
                       'No unique-patient total is derived by adding beneficiary counts.',
                       'Payment estimates multiply rounded published average payments by units; not exact reimbursement, income, or patient copays.',
                       'Only 2024 is collected; no utilization trend is inferred.',
                       'Original 2026-observed publication/network features are unsuitable as prospective predictors of 2024 outcomes.']})
    print('BUILT CMS UTILIZATION', len(detail), 'SERVICE ROWS', len(features), 'HCP FEATURE ROWS')
    return detail, features

def validate():
    preserved = verify_preservation()
    cohort = read_csv(COHORT)
    detail = read_csv(OUT / 'cms_provider_service.csv')
    features = read_csv(OUT / 'hcp_utilization_features.csv')
    npis = {r['npi'] for r in cohort}
    expected_hcp_ids = {r['npi']: r['hcp_id'] for r in cohort}
    key = lambda r: (r['npi'], r['service_year'], r['hcpcs_code'], r['place_of_service'], r['source_dataset_version_id'])
    failures = []
    def gate(condition, name):
        if not condition:
            failures.append(name)
    gate(len(features) == len({r['npi'] for r in features}) == 500, 'feature_row_count_or_duplicate_npi')
    gate({r['npi'] for r in features} == npis, 'feature_npi_coverage')
    gate(all(r['hcp_id'] == expected_hcp_ids.get(r['npi']) for r in features), 'feature_foreign_keys')
    joined = pd.DataFrame(cohort).merge(pd.DataFrame(features), on=['npi', 'hcp_id'],
                                       how='left', validate='one_to_one', indicator=True)
    gate(len(joined) == 500 and joined['_merge'].eq('both').all(), 'one_to_one_join_coverage')
    gate(len(detail) == len({key(r) for r in detail}), 'duplicate_detailed_key')
    gate(all(r['npi'] in npis and r['hcp_id'] == expected_hcp_ids[r['npi']] for r in detail), 'detail_foreign_keys')
    gate(all(r['service_year'] == str(YEAR) and r['source_dataset_version_id'] == DATASET_ID for r in detail + features), 'year_or_version_mismatch')
    gate(all(r['place_of_service'] in ['F', 'O'] for r in detail), 'place_of_service')
    gate(all(dec(r['beneficiaries']) > 10 for r in detail), 'unexpected_suppressed_cell')
    gate(all(r['cms_entity_type'] == 'I' for r in detail), 'organization_npi_in_individual_cohort')
    gate(all(r['hcpcs_code'] != '38228' for r in detail), 'post_2024_administration_code')
    gate(all(r['feature_group'] == CODE_TO_GROUP.get(r['hcpcs_code'], 'other') for r in detail), 'code_group_mapping')
    gate(all(dec(r[field]) >= 0 for r in detail for field in NUMERIC_MAP.values()), 'negative_source_value')
    indexed = collections.defaultdict(list)
    for r in detail:
        indexed[r['npi']].append(r)
    source_reconciliation_errors, aggregate_errors, missingness_errors = [], [], []
    for feature in features:
        npi = feature['npi']
        rows = indexed[npi]
        query = load(INTERIM / 'npi_queries' / (npi + '.json'))
        gate(query['query_status'] == 'complete' and query['terminal_empty_page'], 'incomplete_query:' + npi)
        source_keys = {(x['HCPCS_Cd'], x['Place_Of_Srvc']): x for x in query['records']}
        if len(rows) != query['row_count']:
            source_reconciliation_errors.append(npi)
        for row in rows:
            raw = source_keys[(row['hcpcs_code'], row['place_of_service'])]
            if any(dec(row[new]) != dec(raw[old]) for old, new in NUMERIC_MAP.items()):
                source_reconciliation_errors.append(npi)
        if int(feature['published_service_rows']) != len(rows) or feature['all_published_medicare_payment_estimate_usd'] != estimated_payment(rows):
            aggregate_errors.append(npi)
        for group in GROUPS:
            subset = [r for r in rows if r['feature_group'] == group]
            if int(feature[group + '_observed']) != int(bool(subset)) or int(feature[group + '_published_rows']) != len(subset):
                aggregate_errors.append(npi)
            fields = ['services_or_units_sum', 'beneficiary_code_setting_days_sum', 'max_beneficiaries_in_one_cell', 'medicare_payment_estimate_usd']
            if not subset and any(feature[group + '_' + f] != '' for f in fields):
                missingness_errors.append(npi)
            if subset:
                comparisons = {'services_or_units_sum': str(sum_metric(subset, 'services_or_units')),
                               'beneficiary_code_setting_days_sum': str(sum_metric(subset, 'beneficiary_days')),
                               'max_beneficiaries_in_one_cell': str(max(dec(r['beneficiaries']) for r in subset)),
                               'medicare_payment_estimate_usd': estimated_payment(subset)}
                if any(dec(feature[group + '_' + k]) != dec(v) for k, v in comparisons.items()):
                    aggregate_errors.append(npi)
    gate(not source_reconciliation_errors, 'source_value_reconciliation')
    gate(not aggregate_errors, 'aggregate_reconciliation')
    gate(not missingness_errors, 'missing_utilization_encoded_as_zero')
    hash_errors = []
    for meta_path in RAW.rglob('*.meta.json'):
        meta = load(meta_path)
        if sha(ROOT / meta['local_path']) != meta['sha256']:
            hash_errors.append(str(meta_path))
    gate(not hash_errors, 'source_hash_mismatch')
    from datetime import datetime
    for row in detail + features:
        for k, v in row.items():
            if k.endswith('_at_utc') and v:
                try:
                    assert v.endswith('Z')
                    datetime.fromisoformat(v.replace('Z', '+00:00'))
                except Exception:
                    failures.append('invalid_utc_timestamp:' + k)
    report = {'validated_at_utc': now(), 'passed': not failures, 'failures': sorted(set(failures)),
              'detail_rows': len(detail), 'feature_rows': len(features), 'distinct_feature_npis': len(npis),
              'duplicate_detail_keys': len(detail) - len({key(r) for r in detail}),
              'original_tables_preserved_byte_for_byte': len(preserved), 'original_hcps_sha256': sha(COHORT),
              'source_responses_hash_checked': len(list(RAW.rglob('*.meta.json'))),
              'source_reconciliation_errors': len(source_reconciliation_errors), 'aggregate_errors': len(aggregate_errors),
              'missingness_errors': len(missingness_errors), 'left_join_rows': len(joined),
              'left_join_unmatched_rows': int(joined['_merge'].ne('both').sum()),
              'coverage': dict(collections.Counter(r['utilization_data_status'] for r in features))}
    dump(ROOT / 'evaluations' / 'utilization_validation_2026-10-15.json', report)
    print(json.dumps(report, indent=2))
    assert not failures, failures
    return report

@hydra.main(version_base='1.3', config_path=str(ROOT / 'configs'), config_name='utilization')
def main(cfg: DictConfig):
    if (str(cfg.snapshot), str(cfg.cohort_snapshot), int(cfg.service_year), str(cfg.dataset_version_id), str(cfg.source_release_date), str(cfg.code_list_version), str(cfg.feature_version)) != (SNAPSHOT, '2026-10-09', YEAR, DATASET_ID, RELEASE_DATE, CODE_VERSION, FEATURE_VERSION):
        raise ValueError('This is a frozen utilization release; create a separately versioned snapshot for changed sources or definitions.')
    stage = str(cfg.stage)
    if stage in ['collect', 'all']:
        collect(int(cfg.workers))
    if stage in ['build', 'rebuild', 'all']:
        build()
    if stage in ['validate', 'rebuild', 'all']:
        validate()
    if stage in ['manifests', 'rebuild', 'all']:
        from .utilization_manifest import make_manifests
        make_manifests()
    if stage not in ['collect', 'build', 'rebuild', 'validate', 'manifests', 'all']:
        raise ValueError('Unknown utilization stage: ' + stage)

if __name__ == '__main__':
    main()
