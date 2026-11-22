"""CMS Open Payments General records for the core HCPs, program years 2019-2025.

Reads the General detail file from each program-year archive already collected by
`research_payments` (2026-06-30 publication) and keeps every record whose covered
recipient is a cohort NPI. Program year 2024 is included so the series is uniform;
the 2026-10-09 API table for 2024 is read for reconciliation, never rewritten.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import re
import zipfile
from collections import Counter, defaultdict

from .common import ROOT, dump, load, now, read_csv, write_csv
from .research_payments import (COHORT, CART_PATTERNS, CART_PRODUCTS, LYMPHOMA_CART, MISSING, OUT, PRODUCT_SLOTS, RAW,
                                RELEASE_DATE, SNAPSHOT, YEARS, archive_name, archive_url, cart_products, cohort_npis,
                                collect, joined, parse_date, sha)

FEATURE_VERSION = '1.0.0'
INTERIM = ROOT / 'Data' / 'interim' / SNAPSHOT / 'open_payments_general'
API_2024 = ROOT / 'Data' / 'processed' / '2026-10-09' / 'general_payments_2024.csv'
# The lymphoma flag published with the 2026-10-09 table; kept identical for comparability.
LEGACY_LYMPHOMA = re.compile('yescarta|kymriah|breyanzi|tecartus|axicabtagene|tisagenlecleucel|lisocabtagene|brexucabtagene', re.I)

# CMS nature-of-payment categories grouped by the kind of engagement they evidence.
# The grouping is this project's reading; CMS does not label payments as medical or commercial.
NATURE_GROUPS = {
    'Consulting Fee': 'consulting',
    'Honoraria': 'honoraria',
    'Compensation for services other than consulting, including serving as faculty or as a speaker at a venue other than a continuing education program': 'speaking_non_cme',
    'Compensation for serving as faculty or as a speaker for a medical education program': 'medical_education',
    'Compensation for serving as faculty or as a speaker for a non-accredited and noncertified continuing education program': 'medical_education',
    'Compensation for serving as faculty or as a speaker for an accredited or certified continuing education program': 'medical_education',
    'Education': 'medical_education',
    'Grant': 'grant',
    'Food and Beverage': 'hospitality',
    'Travel and Lodging': 'hospitality',
    'Gift': 'hospitality',
    'Entertainment': 'hospitality',
}
GROUPS = ['consulting', 'honoraria', 'speaking_non_cme', 'medical_education', 'grant', 'hospitality', 'other']
# Groups that most plausibly follow from medical-affairs engagement rather than promotion or meals.
ADVISORY_GROUPS = {'consulting', 'honoraria', 'medical_education', 'grant'}

DETAIL_FIELDS = [
    'hcp_id', 'npi', 'record_id', 'cms_profile_id', 'program_year', 'payment_date', 'amount_usd',
    'number_of_payments', 'manufacturer', 'manufacturer_id', 'submitting_manufacturer', 'payment_nature',
    'nature_group', 'payment_form', 'products', 'product_categories', 'cart_products', 'cart_product_mentioned',
    'lymphoma_cart_product_mentioned', 'third_party_recipient', 'contextual_information',
    'delay_in_publication_indicator', 'dispute_status', 'change_type', 'payment_publication_date',
    'source_release_date', 'source_file', 'source_url', 'retrieved_at_utc',
]

def scan(year, npis):
    """Stream one General detail file and keep every full row paid to a cohort NPI."""
    out_path = INTERIM / f'general_rows_{year}.csv'
    summary_path = INTERIM / f'scan_{year}.json'
    meta = load((RAW / archive_name(year)).with_suffix('.meta.json'))
    if out_path.exists() and summary_path.exists() and load(summary_path)['archive_sha256'] == meta['sha256']:
        return load(summary_path)
    INTERIM.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(RAW / archive_name(year)) as archive:
        members = [m for m in archive.infolist() if re.search(r'OP_DTL_GNRL_PGYR%d_.*\.csv$' % year, m.filename)]
        assert len(members) == 1, f'Expected one General detail file for {year}'
        member = members[0]
        with archive.open(member) as binary, out_path.with_suffix('.csv.tmp').open('w', newline='', encoding='utf-8') as sink:
            reader = csv.reader(io.TextIOWrapper(binary, encoding='utf-8-sig', newline=''))
            header = [column.strip().lower() for column in next(reader)]
            npi_at, year_at = header.index('covered_recipient_npi'), header.index('program_year')
            writer = csv.writer(sink)
            writer.writerow(header)
            scanned = kept = 0
            for row in reader:
                scanned += 1
                if row[npi_at] in npis:
                    assert row[year_at] == str(year), f'Unexpected program year {row[year_at]} in {member.filename}'
                    writer.writerow(row)
                    kept += 1
    out_path.with_suffix('.csv.tmp').replace(out_path)
    summary = {'program_year': year, 'archive': archive_name(year), 'archive_sha256': meta['sha256'],
               'member': member.filename, 'member_bytes': member.file_size, 'member_crc32': f'{member.CRC:08x}',
               'columns': len(header), 'rows_scanned': scanned, 'rows_kept': kept, 'rows_sha256': sha(out_path),
               'scanned_at_utc': now(), 'retrieved_at_utc': meta['retrieved_at_utc']}
    dump(summary_path, summary)
    print('GENERAL SCAN', year, 'ROWS', scanned, 'KEPT', kept, flush=True)
    return summary

def nature_group(nature):
    return NATURE_GROUPS.get(nature, 'other')

def shape(record, npis, source):
    """One published row from a General record, with product slots read in full."""
    names = [record[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] for k in PRODUCT_SLOTS]
    brands = cart_products(*names)
    npi = record['covered_recipient_npi']
    return {
        'hcp_id': npis[npi], 'npi': npi, 'record_id': record['record_id'],
        'cms_profile_id': record['covered_recipient_profile_id'], 'program_year': int(record['program_year']),
        'payment_date': parse_date(record['date_of_payment']),
        'amount_usd': float(record['total_amount_of_payment_usdollars']),
        'number_of_payments': int(record['number_of_payments_included_in_total_amount']),
        'manufacturer': record['applicable_manufacturer_or_applicable_gpo_making_payment_name'],
        'manufacturer_id': record['applicable_manufacturer_or_applicable_gpo_making_payment_id'],
        'submitting_manufacturer': record['submitting_applicable_manufacturer_or_applicable_gpo_name'],
        'payment_nature': record['nature_of_payment_or_transfer_of_value'],
        'nature_group': nature_group(record['nature_of_payment_or_transfer_of_value']),
        'payment_form': record['form_of_payment_or_transfer_of_value'],
        'products': ';'.join(x for x in names if x),
        'product_categories': joined(record[f'product_category_or_therapeutic_area_{k}'] for k in PRODUCT_SLOTS),
        'cart_products': ';'.join(brands), 'cart_product_mentioned': int(bool(brands)),
        'lymphoma_cart_product_mentioned': int(any(LEGACY_LYMPHOMA.search(x) for x in names)),
        'third_party_recipient': record['name_of_third_party_entity_receiving_payment_or_transfer_of_value'],
        'contextual_information': record['contextual_information'],
        'delay_in_publication_indicator': record['delay_in_publication_indicator'],
        'dispute_status': record['dispute_status_for_publication'], 'change_type': record['change_type'],
        'payment_publication_date': parse_date(record['payment_publication_date']),
        'source_release_date': RELEASE_DATE, 'source_file': source['member'],
        'source_url': archive_url(int(record['program_year'])), 'retrieved_at_utc': source['retrieved_at_utc'],
    }

def usd(rows):
    return round(sum(r['amount_usd'] for r in rows), 2)

def year_row(hcp_id, npi, year, rows):
    """HCP x program year; the file was scanned in full, so zero is an observed count."""
    cart = [r for r in rows if r['cart_product_mentioned']]
    out = {'hcp_id': hcp_id, 'npi': npi, 'program_year': year, 'general_payment_rows': len(rows),
           'general_payments_usd': usd(rows), 'manufacturers': len({r['manufacturer'] for r in rows})}
    for group in GROUPS:
        out[f'{group}_usd'] = usd([r for r in rows if r['nature_group'] == group])
    out.update({'advisory_usd': usd([r for r in rows if r['nature_group'] in ADVISORY_GROUPS]),
                'cart_product_rows': len(cart), 'cart_product_usd': usd(cart),
                'cart_advisory_usd': usd([r for r in cart if r['nature_group'] in ADVISORY_GROUPS]),
                'cart_products': joined(p for r in cart for p in r['cart_products'].split(';')),
                'cart_manufacturers': joined(sorted({r['manufacturer'] for r in cart}))})
    return out

def summary_row(hcp_id, npi, rows, built_at):
    cart = [r for r in rows if r['cart_product_mentioned']]
    advisory = [r for r in rows if r['nature_group'] in ADVISORY_GROUPS]
    cart_advisory = [r for r in cart if r['nature_group'] in ADVISORY_GROUPS]
    return {
        'hcp_id': hcp_id, 'npi': npi,
        'general_data_status': 'published_records' if rows else 'no_published_records',
        'general_payment_rows_2019_2025': len(rows), 'general_payments_usd_2019_2025': usd(rows),
        'general_active_years': len({r['program_year'] for r in rows}),
        'general_manufacturers_2019_2025': len({r['manufacturer'] for r in rows}),
        'advisory_usd_2019_2025': usd(advisory), 'advisory_active_years': len({r['program_year'] for r in advisory}),
        'consulting_usd_2019_2025': usd([r for r in rows if r['nature_group'] == 'consulting']),
        'speaking_non_cme_usd_2019_2025': usd([r for r in rows if r['nature_group'] == 'speaking_non_cme']),
        'hospitality_usd_2019_2025': usd([r for r in rows if r['nature_group'] == 'hospitality']),
        'cart_product_rows_2019_2025': len(cart), 'cart_product_usd_2019_2025': usd(cart),
        'cart_advisory_usd_2019_2025': usd(cart_advisory),
        'cart_active_years': len({r['program_year'] for r in cart}),
        'first_cart_payment_year': min((r['program_year'] for r in cart), default=''),
        'last_cart_payment_year': max((r['program_year'] for r in cart), default=''),
        'cart_products_2019_2025': joined(p for r in cart for p in r['cart_products'].split(';')),
        'cart_manufacturers_2019_2025': joined(sorted({r['manufacturer'] for r in cart})),
        'cart_advisory_manufacturers_2019_2025': joined(sorted({r['manufacturer'] for r in cart_advisory})),
        'program_years_scanned': f'{YEARS[0]}-{YEARS[-1]}', 'source_release_date': RELEASE_DATE,
        'feature_version': FEATURE_VERSION, 'collection_snapshot_date': SNAPSHOT, 'built_at_utc': built_at,
    }

def reconcile_2024(detail):
    """Compare the bulk 2024 rows with the 2026-10-09 API table of the same publication."""
    api = {r['record_id']: r for r in read_csv(API_2024)}
    bulk = {r['record_id']: r for r in detail if r['program_year'] == 2024}
    shared = set(api) & set(bulk)
    fields = ['npi', 'payment_date', 'manufacturer', 'payment_nature', 'payment_form', 'products']
    return {'api_table': str(API_2024.relative_to(ROOT)), 'api_rows': len(api), 'bulk_rows': len(bulk),
            'shared_record_ids': len(shared), 'only_in_api': len(set(api) - set(bulk)),
            'only_in_bulk': len(set(bulk) - set(api)),
            'amount_mismatches': sum(abs(float(api[k]['amount_usd']) - bulk[k]['amount_usd']) > 0.005 for k in shared),
            'field_mismatches': {f: sum(str(api[k][f]) != str(bulk[k][f]) for k in shared) for f in fields},
            'legacy_lymphoma_flag_mismatches': sum(int(api[k]['lymphoma_cart_product_mentioned'])
                                                   != bulk[k]['lymphoma_cart_product_mentioned'] for k in shared)}

def build(workers=4):
    npis = cohort_npis()
    built_at = now()
    with concurrent.futures.ProcessPoolExecutor(workers) as executor:
        scans = list(executor.map(scan, YEARS, [npis] * len(YEARS)))
    detail = []
    for summary in scans:
        for record in read_csv(INTERIM / f"general_rows_{summary['program_year']}.csv"):
            detail.append(shape(record, npis, summary))
    detail.sort(key=lambda r: (r['hcp_id'], r['program_year'], r['payment_date'], r['record_id']))
    assert len(detail) == len({r['record_id'] for r in detail}), 'Duplicate General record ids'
    by_hcp, by_year = defaultdict(list), defaultdict(list)
    for row in detail:
        by_hcp[row['hcp_id']].append(row)
        by_year[row['hcp_id'], row['program_year']].append(row)
    people = sorted(npis.items(), key=lambda x: x[1])
    year_rows = [year_row(hcp_id, npi, year, by_year[hcp_id, year]) for npi, hcp_id in people for year in YEARS]
    summary_rows = [summary_row(hcp_id, npi, by_hcp[hcp_id], built_at) for npi, hcp_id in people]
    write_csv(OUT / 'general_payments_2019_2025.csv', detail, DETAIL_FIELDS)
    write_csv(OUT / 'hcp_general_payments_by_year.csv', year_rows)
    write_csv(OUT / 'hcp_general_payment_features.csv', summary_rows)
    cart = [r for r in detail if r['cart_product_mentioned']]
    metas = [load((RAW / archive_name(year)).with_suffix('.meta.json')) for year in YEARS]
    def by(rows, key):
        return {str(k): {'rows': len(v), 'usd': usd(v)} for k, v in sorted(_group(rows, key).items())}
    dump(OUT / 'general_payments_metadata.json', {
        'built_at_utc': built_at, 'collection_snapshot_date': SNAPSHOT, 'feature_version': FEATURE_VERSION,
        'source': 'CMS Open Payments General Payment detail files', 'source_release_date': RELEASE_DATE,
        'program_years': YEARS, 'cohort_path': str(COHORT.relative_to(ROOT)), 'cohort_sha256': sha(COHORT),
        'match_rule': 'Cohort NPI equals covered_recipient_npi; exact string match.',
        'archives': [{k: m[k] for k in ['program_year', 'source_url', 'retrieved_at_utc', 'http_last_modified',
                                         'http_etag', 'sha256', 'bytes']} for m in metas],
        'scans': [{k: s[k] for k in ['program_year', 'member', 'member_bytes', 'member_crc32', 'columns',
                                      'rows_scanned', 'rows_kept', 'rows_sha256']} for s in scans],
        'tables': {'general_payments_2019_2025': len(detail), 'hcp_general_payments_by_year': len(year_rows),
                   'hcp_general_payment_features': len(summary_rows)},
        'total_usd': usd(detail), 'hcps_with_records': sum(bool(by_hcp[h]) for h in npis.values()),
        'by_program_year': by(detail, 'program_year'), 'by_nature_group': by(detail, 'nature_group'),
        'nature_groups': NATURE_GROUPS, 'advisory_groups': sorted(ADVISORY_GROUPS),
        'ungrouped_natures': dict(Counter(r['payment_nature'] for r in detail if r['nature_group'] == 'other').most_common()),
        'cart': {'rows': len(cart), 'usd': usd(cart), 'hcps_with_records': len({r['hcp_id'] for r in cart}),
                 'by_program_year': by(cart, 'program_year'), 'by_nature_group': by(cart, 'nature_group'),
                 'rows_by_product': dict(Counter(p for r in cart for p in r['cart_products'].split(';')).most_common()),
                 'by_manufacturer': by(cart, 'manufacturer'), 'product_patterns': CART_PRODUCTS,
                 'legacy_lymphoma_pattern': LEGACY_LYMPHOMA.pattern},
        'reconciliation_2024': reconcile_2024(detail),
        'limitations': [
            'General payments are transfers of value reported by manufacturers. They do not record Medical Science Liaison contact, which usually involves no transfer of value beyond a meal.',
            'nature_group and the advisory grouping are this project\'s reading of CMS categories; CMS does not label a payment as medical-affairs or commercial.',
            'Speaking compensation outside continuing education is kept apart from the advisory groups because it covers promotional speaker programs.',
            'cart_product_mentioned reads the five product-name slots. Manufacturers often leave products blank on consulting fees, so CAR-T totals are a floor.',
            'lymphoma_cart_product_mentioned keeps the 2026-10-09 pattern (brand and generic names of four lymphoma products) for comparability with general_payments_2024.csv.',
            'Zero rows in a scanned year is an observed count for this publication, not proof of no relationship; CMS revises records at each refresh.',
            'CMS category names changed in program year 2021 when the two continuing-education categories were merged; nature_group maps old and new names together.',
        ],
    })
    print('GENERAL BUILT', len(detail), 'ROWS', usd(detail), 'USD', flush=True)

def _group(rows, key):
    out = defaultdict(list)
    for row in rows:
        out[row[key]].append(row)
    return out

DESCRIPTIONS = {
    'hcp_id': 'Stable cohort key: NPI followed by the ten-digit NPI.',
    'npi': 'Ten-digit individual NPI of the core HCP.',
    'record_id': 'CMS Open Payments general record identifier; unique within the publication.',
    'cms_profile_id': 'Open Payments covered-recipient profile identifier on the record.',
    'program_year': 'Open Payments program year.',
    'payment_date': 'Date of payment reported by the manufacturer (ISO).',
    'amount_usd': 'Total amount of the transfer of value in US dollars.',
    'number_of_payments': 'Number of payments included in the total amount.',
    'manufacturer': 'Applicable manufacturer or GPO making the payment.',
    'manufacturer_id': 'CMS identifier of the manufacturer making the payment.',
    'submitting_manufacturer': 'Applicable manufacturer or GPO that submitted the record.',
    'payment_nature': 'CMS nature of payment or transfer of value, as published.',
    'nature_group': 'Project grouping of payment_nature: consulting, honoraria, speaking_non_cme, medical_education, grant, hospitality, or other.',
    'payment_form': 'Form of payment or transfer of value.',
    'products': 'Product names from the five associated-product slots, semicolon-separated.',
    'product_categories': 'Distinct manufacturer-assigned product categories or therapeutic areas.',
    'cart_products': 'Canonical CAR-T brands matched in the product slots.',
    'cart_product_mentioned': '1 when a brand, generic, or development name of one of seven approved CAR-T products appears in a product slot.',
    'lymphoma_cart_product_mentioned': '1 under the 2026-10-09 pattern: brand or generic name of Yescarta, Kymriah, Breyanzi, or Tecartus in a product slot.',
    'third_party_recipient': 'Third-party entity that received the payment on the HCP\'s behalf, when reported.',
    'contextual_information': 'Free-text context reported by the manufacturer.',
    'delay_in_publication_indicator': 'CMS delay-in-publication indicator on the published record.',
    'dispute_status': 'Dispute status for publication.',
    'change_type': 'CMS change type of the record in this publication.',
    'payment_publication_date': 'Date CMS published the record (ISO).',
    'source_release_date': 'Open Payments publication date of the source archive.',
    'source_file': 'General detail member file inside the program-year archive.',
    'source_url': 'URL of the program-year archive the record was read from.',
    'retrieved_at_utc': 'UTC time the archive was downloaded.',
    'general_payment_rows': 'General records paid to the HCP in the program year.',
    'general_payments_usd': 'Sum of general payments to the HCP in the program year.',
    'manufacturers': 'Distinct manufacturers paying the HCP in the program year.',
    'advisory_usd': 'Sum over the consulting, honoraria, medical_education, and grant groups in the program year.',
    'cart_product_rows': 'Records naming a CAR-T product in the program year.',
    'cart_product_usd': 'Sum of records naming a CAR-T product in the program year.',
    'cart_advisory_usd': 'Advisory-group sum restricted to records naming a CAR-T product.',
    'cart_manufacturers': 'Manufacturers on records naming a CAR-T product in the program year.',
    'general_data_status': 'published_records when at least one record is paid to the HCP in 2019-2025, else no_published_records.',
    'general_payment_rows_2019_2025': 'General records paid to the HCP across program years 2019-2025.',
    'general_payments_usd_2019_2025': 'Sum of general payments to the HCP across 2019-2025.',
    'general_active_years': 'Program years with at least one general record.',
    'general_manufacturers_2019_2025': 'Distinct manufacturers paying the HCP across 2019-2025.',
    'advisory_usd_2019_2025': 'Sum over the consulting, honoraria, medical_education, and grant groups across 2019-2025.',
    'advisory_active_years': 'Program years with at least one advisory-group record.',
    'consulting_usd_2019_2025': 'Sum of Consulting Fee records across 2019-2025.',
    'speaking_non_cme_usd_2019_2025': 'Sum of speaking or faculty compensation outside continuing education across 2019-2025.',
    'hospitality_usd_2019_2025': 'Sum of food, beverage, travel, lodging, gift, and entertainment records across 2019-2025.',
    'cart_product_rows_2019_2025': 'Records naming a CAR-T product across 2019-2025.',
    'cart_product_usd_2019_2025': 'Sum of records naming a CAR-T product across 2019-2025.',
    'cart_advisory_usd_2019_2025': 'Advisory-group sum on records naming a CAR-T product across 2019-2025.',
    'cart_active_years': 'Program years with at least one record naming a CAR-T product.',
    'first_cart_payment_year': 'First program year with a record naming a CAR-T product.',
    'last_cart_payment_year': 'Last program year with a record naming a CAR-T product.',
    'cart_products_2019_2025': 'Canonical CAR-T brands named on the HCP records across 2019-2025.',
    'cart_manufacturers_2019_2025': 'Manufacturers on records naming a CAR-T product across 2019-2025.',
    'cart_advisory_manufacturers_2019_2025': 'Manufacturers on advisory-group records naming a CAR-T product.',
    'program_years_scanned': 'Program-year range scanned in full.',
    'feature_version': 'Version of the feature definitions.',
    'collection_snapshot_date': 'Collection-run date; exact retrieval times are in the metadata file.',
    'built_at_utc': 'UTC time the table was assembled.',
    **{f'{group}_usd': f'Sum of records in the {group} nature group in the program year.' for group in GROUPS},
}
TABLES = ['general_payments_2019_2025', 'hcp_general_payments_by_year', 'hcp_general_payment_features']

def make_manifests():
    dest = ROOT / 'Data' / 'manifests' / SNAPSHOT
    dictionary = {}
    for table in TABLES:
        with (OUT / (table + '.csv')).open(newline='', encoding='utf-8') as stream:
            header = next(csv.reader(stream))
        missing = [field for field in header if field not in DESCRIPTIONS]
        assert not missing, 'Missing definition: ' + ', '.join(missing)
        dictionary[table] = [{'column': field, 'description': DESCRIPTIONS[field], 'missing_value': MISSING} for field in header]
    dump(dest / 'general_payments_data_dictionary.json', dictionary)
    paths = [p for base in [INTERIM] for p in sorted(base.rglob('*')) if p.is_file() and p.suffix != '.tmp']
    paths += [OUT / (table + '.csv') for table in TABLES] + [OUT / 'general_payments_metadata.json']
    write_csv(dest / 'general_payments_file_checksums.csv',
              [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in paths])
    print('GENERAL MANIFESTS', len(paths), 'FILES')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-collect', action='store_true', help='Build from the already downloaded archives only.')
    parser.add_argument('--workers', type=int, default=4, help='Program-year files scanned in parallel.')
    args = parser.parse_args()
    if not args.skip_collect:
        collect()
    build(args.workers)
    make_manifests()

if __name__ == '__main__':
    main()
