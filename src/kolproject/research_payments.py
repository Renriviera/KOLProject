"""CMS Open Payments Research records that name a core HCP as principal investigator.

The seven live program-year archives of the 2026-06-30 publication are downloaded
once, hashed, and scanned locally. A record is kept when a cohort NPI appears in any
of the five principal-investigator slots or as the covered recipient. The 2026-10-09
cohort tables are read, never rewritten.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import io
import re
import subprocess
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .common import ROOT, dump, load, now, read_csv, write_csv

SNAPSHOT = '2026-11-11'
PUBLICATION = 'P06302026_06032026'
RELEASE_DATE = '2026-06-30'
YEARS = list(range(2019, 2026))
FEATURE_VERSION = '1.0.0'
# Datastore identifiers of the same tables on openpaymentsdata.cms.gov, kept for cross-reference.
DATASET_IDS = {
    2019: '713a6016-1930-4a0c-b7c2-3e3de4f244c5', 2020: '9c248e7e-7c7f-478b-ab84-ce0919d72c1c',
    2021: 'ce1d28dd-0094-5060-a036-580329439600', 2022: 'fdc3c773-018a-412c-8a81-d7b8a13a037b',
    2023: 'ec9521bf-9d97-4603-814c-f4132d34bc4f', 2024: '2f15cb85-8887-4dcc-a318-1f8ec1d815b3',
    2025: 'f0d1de67-6852-4093-a036-c9328c256a05',
}
RAW = ROOT / 'Data' / 'raw' / SNAPSHOT / 'open_payments_bulk'
INTERIM = ROOT / 'Data' / 'interim' / SNAPSHOT / 'open_payments_research'
OUT = ROOT / 'Data' / 'processed' / SNAPSHOT
COHORT = ROOT / 'Data' / 'processed' / '2026-10-09' / 'hcps.csv'
PI_SLOTS = range(1, 6)
PRODUCT_SLOTS = range(1, 6)

# Brand, generic, and development names of the seven FDA-approved CAR-T products.
CART_PRODUCTS = {
    'YESCARTA': r'yescarta|axicabtagene|axi[- ]?cel|kte[- ]?c19',
    'TECARTUS': r'tecartus|brexucabtagene|brexu[- ]?cel|kte[- ]?x19',
    'KYMRIAH': r'kymriah|tisagenlecleucel|tisa[- ]?cel|ctl[- ]?019',
    'BREYANZI': r'breyanzi|lisocabtagene|liso[- ]?cel|jcar[- ]?017',
    'ABECMA': r'abecma|idecabtagene|ide[- ]?cel|bb[- ]?2121',
    'CARVYKTI': r'carvykti|ciltacabtagene|cilta[- ]?cel|jnj[- ]?(6828)?4528|lcar[- ]?b38m',
    'AUCATZYL': r'aucatzyl|obecabtagene|obe[- ]?cel|auto1\b',
}
LYMPHOMA_CART = {'YESCARTA', 'TECARTUS', 'KYMRIAH', 'BREYANZI'}
CART_PATTERNS = {brand: re.compile(pattern, re.I) for brand, pattern in CART_PRODUCTS.items()}
# Generic CAR-T wording in free-text study fields; a text heuristic, not a product tag.
CART_TEXT = re.compile(r'chimeric antigen receptor|\bcar[- ]?t\b|\bcart\b|\bcar t[- ]cell', re.I)

DETAIL_FIELDS = [
    'hcp_id', 'npi', 'record_id', 'program_year', 'payment_date', 'amount_usd', 'pi_slots',
    'is_covered_recipient', 'record_pi_count', 'record_core_hcp_count', 'recipient_type', 'recipient_name',
    'teaching_hospital_ccn', 'recipient_state', 'manufacturer', 'manufacturer_id', 'submitting_manufacturer',
    'products', 'product_categories', 'cart_products', 'cart_product_mentioned',
    'lymphoma_cart_product_mentioned', 'cart_text_match', 'study_name', 'nct_id',
    'preclinical_research_indicator', 'context_of_research', 'payment_form', 'expenditure_categories',
    'delay_in_publication_indicator', 'dispute_status', 'change_type', 'payment_publication_date',
    'source_release_date', 'source_file', 'source_url', 'retrieved_at_utc',
]

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 22), b''):
            digest.update(block)
    return digest.hexdigest()

def archive_name(year):
    return f'PGYR{year}_{PUBLICATION}.zip'

def archive_url(year):
    return 'https://download.cms.gov/openpayments/' + archive_name(year)

def download(year):
    """One program-year archive with retrieval time, validators, and hash; curl's own agent string is sent."""
    path = RAW / archive_name(year)
    meta_path = path.with_suffix('.meta.json')
    if path.exists() and meta_path.exists():
        meta = load(meta_path)
        if path.stat().st_size != meta['bytes']:
            raise ValueError(f'Cached archive size mismatch: {path}')
        return meta
    RAW.mkdir(parents=True, exist_ok=True)
    header_file = RAW / f'.headers_{year}.tmp'
    partial = path.with_suffix('.zip.part')
    subprocess.run(['curl', '-sS', '--fail', '--location', '--retry', '3', '-C', '-', '-D', str(header_file),
                    '-o', str(partial), archive_url(year)], check=True)
    observed = now()
    headers = {k.lower(): v for k, v in re.findall(r'^([\w-]+):\s*(.*?)\s*$', header_file.read_text(errors='replace'), re.M)}
    header_file.unlink()
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip() is not None:
            raise ValueError(f'Corrupt archive member in {partial}')
    partial.replace(path)
    meta = {'source_id': 'open_payments_bulk:' + archive_name(year), 'source_name': 'CMS Open Payments program-year archive',
            'program_year': year, 'source_url': archive_url(year), 'source_release_date': RELEASE_DATE,
            'retrieved_at_utc': observed, 'retrieval_time_precision': 'second',
            'http_last_modified': headers.get('last-modified'), 'http_etag': headers.get('etag'),
            'sha256': sha(path), 'bytes': path.stat().st_size, 'local_path': str(path.relative_to(ROOT)), 'client': 'curl'}
    dump(meta_path, meta)
    return meta

def collect(workers=3):
    with concurrent.futures.ThreadPoolExecutor(workers) as executor:
        metas = list(executor.map(download, YEARS))
    print('RESEARCH ARCHIVES', len(metas), 'BYTES', sum(m['bytes'] for m in metas), flush=True)
    return metas

def cohort_npis():
    cohort = read_csv(COHORT)
    assert len(cohort) == len({r['npi'] for r in cohort}) == 500
    return {r['npi']: r['hcp_id'] for r in cohort}

def scan(year, npis):
    """Stream the research detail file and keep every full row naming a cohort NPI."""
    out_path = INTERIM / f'research_rows_{year}.csv'
    summary_path = INTERIM / f'scan_{year}.json'
    meta = load((RAW / archive_name(year)).with_suffix('.meta.json'))
    if (out_path.exists() and summary_path.exists() and load(summary_path)['archive_sha256'] == meta['sha256']
            and 'pi_entries' in load(summary_path)):
        return load(summary_path)
    INTERIM.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(RAW / archive_name(year)) as archive:
        members = [m for m in archive.infolist() if re.search(r'OP_DTL_RSRCH_PGYR%d_.*\.csv$' % year, m.filename)]
        assert len(members) == 1, f'Expected one research detail file for {year}: {[m.filename for m in archive.infolist()]}'
        member = members[0]
        with archive.open(member) as binary, out_path.with_suffix('.csv.tmp').open('w', newline='', encoding='utf-8') as sink:
            reader = csv.reader(io.TextIOWrapper(binary, encoding='utf-8-sig', newline=''))
            header = [column.strip().lower() for column in next(reader)]
            index = {column: i for i, column in enumerate(header)}
            keys = [index[f'principal_investigator_{k}_npi'] for k in PI_SLOTS] + [index['covered_recipient_npi']]
            year_at = index['program_year']
            pi_pairs = [(index[f'principal_investigator_{k}_npi'], index[f'principal_investigator_{k}_last_name']) for k in PI_SLOTS]
            writer = csv.writer(sink)
            writer.writerow(header)
            scanned = kept = named = unkeyed = 0
            for row in reader:
                scanned += 1
                for npi_at, name_at in pi_pairs:
                    if row[name_at] or row[npi_at]:
                        named += 1
                        unkeyed += not row[npi_at]
                if any(row[i] in npis for i in keys):
                    assert row[year_at] == str(year), f'Unexpected program year {row[year_at]} in {member.filename}'
                    writer.writerow(row)
                    kept += 1
    out_path.with_suffix('.csv.tmp').replace(out_path)
    summary = {'program_year': year, 'archive': archive_name(year), 'archive_sha256': meta['sha256'],
               'member': member.filename, 'member_bytes': member.file_size, 'member_crc32': f'{member.CRC:08x}',
               'columns': len(header), 'rows_scanned': scanned, 'rows_kept': kept,
               'pi_entries': named, 'pi_entries_without_npi': unkeyed,
               'rows_sha256': sha(out_path), 'scanned_at_utc': now(), 'retrieved_at_utc': meta['retrieved_at_utc']}
    dump(summary_path, summary)
    print('RESEARCH SCAN', year, 'ROWS', scanned, 'KEPT', kept, flush=True)
    return summary

def parse_date(value):
    return datetime.strptime(value, '%m/%d/%Y').date().isoformat() if value else ''

def joined(values):
    return ';'.join(dict.fromkeys(v.strip() for v in values if v and v.strip()))

def cart_products(*texts):
    """Canonical CAR-T brands whose brand, generic, or development name appears in any text."""
    blob = ' | '.join(t for t in texts if t)
    return [brand for brand, pattern in CART_PATTERNS.items() if pattern.search(blob)]

def expand(record, npis, source):
    """One output row per cohort HCP named on a research record."""
    slots = defaultdict(list)
    for k in PI_SLOTS:
        npi = record[f'principal_investigator_{k}_npi']
        if npi in npis:
            slots[npi].append(str(k))
    recipient = record['covered_recipient_npi']
    matched = list(dict.fromkeys(list(slots) + ([recipient] if recipient in npis else [])))
    pi_count = sum(bool(record[f'principal_investigator_{k}_npi'] or record[f'principal_investigator_{k}_last_name'])
                   for k in PI_SLOTS)
    names = [record[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] for k in PRODUCT_SLOTS]
    brands = cart_products(*names)
    text_brands = cart_products(record['name_of_study'], record['context_of_research'])
    physician = ' '.join(x for x in [record['covered_recipient_first_name'], record['covered_recipient_last_name']] if x)
    base = {
        'record_id': record['record_id'], 'program_year': int(record['program_year']),
        'payment_date': parse_date(record['date_of_payment']),
        'amount_usd': float(record['total_amount_of_payment_usdollars']),
        'record_pi_count': pi_count, 'record_core_hcp_count': len(matched),
        'recipient_type': record['covered_recipient_type'],
        'recipient_name': record['noncovered_recipient_entity_name'] or record['teaching_hospital_name'] or physician,
        'teaching_hospital_ccn': record['teaching_hospital_ccn'], 'recipient_state': record['recipient_state'],
        'manufacturer': record['applicable_manufacturer_or_applicable_gpo_making_payment_name'],
        'manufacturer_id': record['applicable_manufacturer_or_applicable_gpo_making_payment_id'],
        'submitting_manufacturer': record['submitting_applicable_manufacturer_or_applicable_gpo_name'],
        'products': joined(names),
        'product_categories': joined(record[f'product_category_or_therapeutic_area_{k}'] for k in PRODUCT_SLOTS),
        'cart_products': ';'.join(dict.fromkeys(brands + text_brands)),
        'cart_product_mentioned': int(bool(brands)),
        'lymphoma_cart_product_mentioned': int(bool(LYMPHOMA_CART.intersection(brands))),
        'cart_text_match': int(bool(text_brands) or bool(CART_TEXT.search(record['name_of_study'] + ' ' + record['context_of_research']))),
        'study_name': record['name_of_study'], 'nct_id': record['clinicaltrials_gov_identifier'],
        'preclinical_research_indicator': record['preclinical_research_indicator'],
        'context_of_research': record['context_of_research'],
        'payment_form': record['form_of_payment_or_transfer_of_value'],
        'expenditure_categories': joined(record[f'expenditure_category{k}'] for k in range(1, 7)),
        'delay_in_publication_indicator': record['delay_in_publication_indicator'],
        'dispute_status': record['dispute_status_for_publication'], 'change_type': record['change_type'],
        'payment_publication_date': parse_date(record['payment_publication_date']),
        'source_release_date': RELEASE_DATE, 'source_file': source['member'],
        'source_url': archive_url(int(record['program_year'])), 'retrieved_at_utc': source['retrieved_at_utc'],
    }
    return [{'hcp_id': npis[npi], 'npi': npi, 'pi_slots': ';'.join(slots.get(npi, [])),
             'is_covered_recipient': int(npi == recipient), **base} for npi in matched]

def features(hcp_id, npi, rows, built_at):
    cart = [r for r in rows if r['cart_product_mentioned'] or r['cart_text_match']]
    out = {'hcp_id': hcp_id, 'npi': npi,
           'research_data_status': 'published_records' if rows else 'no_published_records',
           'research_rows_2019_2025': len(rows),
           'research_record_usd_2019_2025': round(sum(r['amount_usd'] for r in rows), 2) if rows else ''}
    for year in YEARS:
        out[f'research_rows_{year}'] = sum(r['program_year'] == year for r in rows)
    out.update({
        'research_active_years': len({r['program_year'] for r in rows}),
        'research_manufacturers': len({r['manufacturer'] for r in rows}),
        'research_nct_ids': len({r['nct_id'] for r in rows if r['nct_id']}),
        'research_sole_pi_rows': sum(r['record_pi_count'] == 1 and bool(r['pi_slots']) for r in rows),
        'research_covered_recipient_rows': sum(r['is_covered_recipient'] for r in rows),
        'research_preclinical_rows': sum(r['preclinical_research_indicator'] == 'Yes' for r in rows),
        'cart_research_rows_2019_2025': len(cart),
        'cart_research_record_usd_2019_2025': round(sum(r['amount_usd'] for r in cart), 2) if cart else '',
        'cart_research_products': joined(p for r in cart for p in r['cart_products'].split(';')),
        'cart_research_manufacturers': joined(sorted({r['manufacturer'] for r in cart})),
        'cart_research_nct_ids': joined(sorted({r['nct_id'] for r in cart if r['nct_id']})),
        'first_research_payment_date': min((r['payment_date'] for r in rows if r['payment_date']), default=''),
        'last_research_payment_date': max((r['payment_date'] for r in rows if r['payment_date']), default=''),
        'program_years_scanned': f'{YEARS[0]}-{YEARS[-1]}', 'source_release_date': RELEASE_DATE,
        'feature_version': FEATURE_VERSION, 'collection_snapshot_date': SNAPSHOT, 'built_at_utc': built_at,
    })
    return out

def build():
    npis = cohort_npis()
    built_at = now()
    scans = [scan(year, npis) for year in YEARS]
    detail = []
    for summary in scans:
        for record in read_csv(INTERIM / f"research_rows_{summary['program_year']}.csv"):
            detail.extend(expand(record, npis, summary))
    detail.sort(key=lambda r: (r['hcp_id'], r['program_year'], r['payment_date'], r['record_id']))
    assert len(detail) == len({(r['hcp_id'], r['record_id']) for r in detail}), 'Duplicate HCP-record rows'
    by_hcp = defaultdict(list)
    for row in detail:
        by_hcp[row['hcp_id']].append(row)
    feature_rows = [features(hcp_id, npi, by_hcp[hcp_id], built_at) for npi, hcp_id in sorted(npis.items(), key=lambda x: x[1])]
    write_csv(OUT / 'research_payments_2019_2025.csv', detail, DETAIL_FIELDS)
    write_csv(OUT / 'hcp_research_payment_features.csv', feature_rows)
    records = {r['record_id']: r for r in detail}
    cart_records = {k: r for k, r in records.items() if r['cart_product_mentioned'] or r['cart_text_match']}
    metas = [load((RAW / archive_name(year)).with_suffix('.meta.json')) for year in YEARS]
    dump(OUT / 'research_payments_metadata.json', {
        'built_at_utc': built_at, 'collection_snapshot_date': SNAPSHOT, 'feature_version': FEATURE_VERSION,
        'source': 'CMS Open Payments Research Payment detail files', 'source_release_date': RELEASE_DATE,
        'program_years': YEARS, 'cohort_path': str(COHORT.relative_to(ROOT)), 'cohort_sha256': sha(COHORT),
        'match_rule': 'Cohort NPI equals principal_investigator_{1..5}_npi or covered_recipient_npi; exact string match.',
        'archives': [{k: m[k] for k in ['program_year', 'source_url', 'retrieved_at_utc', 'http_last_modified',
                                         'http_etag', 'sha256', 'bytes']} for m in metas],
        'datastore_dataset_ids': {str(y): DATASET_IDS[y] for y in YEARS},
        'scans': [{k: s[k] for k in ['program_year', 'member', 'member_bytes', 'member_crc32', 'columns',
                                      'rows_scanned', 'rows_kept', 'pi_entries', 'pi_entries_without_npi',
                                      'rows_sha256']} for s in scans],
        'tables': {'research_payments_2019_2025': len(detail), 'hcp_research_payment_features': len(feature_rows)},
        'distinct_records': len(records),
        'distinct_record_usd': round(sum(r['amount_usd'] for r in records.values()), 2),
        'hcps_with_records': sum(bool(by_hcp[h]) for h in npis.values()),
        'rows_by_program_year': dict(sorted(Counter(str(r['program_year']) for r in detail).items())),
        'records_by_recipient_type': dict(Counter(r['recipient_type'] for r in records.values()).most_common()),
        'cart': {'distinct_records': len(cart_records),
                 'distinct_record_usd': round(sum(r['amount_usd'] for r in cart_records.values()), 2),
                 'hcps_with_records': len({r['hcp_id'] for r in detail if r['cart_product_mentioned'] or r['cart_text_match']}),
                 'records_by_product': dict(Counter(p for r in cart_records.values() for p in r['cart_products'].split(';') if p).most_common()),
                 'product_patterns': CART_PRODUCTS, 'text_pattern': CART_TEXT.pattern},
        'limitations': [
            'Research payments are transfers of value reported by manufacturers for research on which the HCP is a named principal investigator; most are paid to institutions, not to the physician.',
            'amount_usd is the whole record. It is repeated on every HCP row of a multi-investigator record and must not be summed across HCPs or read as personal income.',
            'CMS allows publication of research payments to be delayed up to four years for products in development, so recent program years are a floor and zero rows is not evidence of no research relationship.',
            'Records naming an investigator without an NPI cannot be matched; fewer than 0.05% of investigator entries lack an NPI in each scanned year (counts under scans).',
            'cart_product_mentioned reads the five product-name slots; cart_text_match is a text heuristic over study name and research context. Neither confirms the trial intervention.',
            'Open Payments does not observe Medical Science Liaison contact. These rows evidence a sponsor-investigator relationship, not scientific-exchange visits.',
            'The source is the 2026-06-30 publication; CMS revises records at each refresh.',
        ],
    })
    print('RESEARCH BUILT', len(detail), 'HCP-RECORD ROWS', len(records), 'RECORDS', flush=True)

DESCRIPTIONS = {
    'hcp_id': 'Stable cohort key: NPI followed by the ten-digit NPI.',
    'npi': 'Ten-digit individual NPI of the core HCP.',
    'record_id': 'CMS Open Payments research record identifier; unique within the publication.',
    'program_year': 'Open Payments program year of the record.',
    'payment_date': 'Date of payment reported by the manufacturer (ISO).',
    'amount_usd': 'Total amount of the research record in US dollars; a record-level value repeated on each HCP row.',
    'pi_slots': 'Principal-investigator slot numbers (1-5) in which the HCP NPI appears; blank when matched only as covered recipient.',
    'is_covered_recipient': '1 when the HCP NPI is the covered recipient of the record.',
    'record_pi_count': 'Number of populated principal-investigator slots on the record.',
    'record_core_hcp_count': 'Number of distinct core HCPs named on the record.',
    'recipient_type': 'CMS covered-recipient type of the record (non-covered entity, teaching hospital, physician, ...).',
    'recipient_name': 'Non-covered recipient entity name, else teaching hospital name, else covered physician name.',
    'teaching_hospital_ccn': 'CCN of the teaching hospital recipient when reported.',
    'recipient_state': 'Recipient primary business state.',
    'manufacturer': 'Applicable manufacturer or GPO making the payment.',
    'manufacturer_id': 'CMS identifier of the manufacturer making the payment.',
    'submitting_manufacturer': 'Applicable manufacturer or GPO that submitted the record.',
    'products': 'Distinct product names from the five associated-product slots, semicolon-separated.',
    'product_categories': 'Distinct manufacturer-assigned product categories or therapeutic areas.',
    'cart_products': 'Canonical CAR-T brands matched in product slots, study name, or research context.',
    'cart_product_mentioned': '1 when a CAR-T brand, generic, or development name appears in a product slot.',
    'lymphoma_cart_product_mentioned': '1 when the product-slot match is Yescarta, Tecartus, Kymriah, or Breyanzi.',
    'cart_text_match': '1 when study name or research context names a CAR-T product or generic CAR-T wording.',
    'study_name': 'Name of study reported by the manufacturer.',
    'nct_id': 'ClinicalTrials.gov identifier reported by the manufacturer.',
    'preclinical_research_indicator': 'CMS preclinical research indicator (Yes/No).',
    'context_of_research': 'Free-text research context reported by the manufacturer.',
    'payment_form': 'Form of payment or transfer of value.',
    'expenditure_categories': 'Distinct expenditure categories reported on the record.',
    'delay_in_publication_indicator': 'CMS delay-in-publication indicator on the published record.',
    'dispute_status': 'Dispute status for publication.',
    'change_type': 'CMS change type of the record in this publication (NEW, ADD, CHANGED, UNCHANGED).',
    'payment_publication_date': 'Date CMS published the record (ISO).',
    'source_release_date': 'Open Payments publication date of the source archive.',
    'source_file': 'Research detail member file inside the program-year archive.',
    'source_url': 'URL of the program-year archive the record was read from.',
    'retrieved_at_utc': 'UTC time the archive was downloaded.',
    'research_data_status': 'published_records when at least one record names the HCP, else no_published_records.',
    'research_rows_2019_2025': 'Research records naming the HCP across program years 2019-2025.',
    'research_record_usd_2019_2025': 'Sum of record totals on which the HCP is named; not personal income and not additive across HCPs.',
    'research_active_years': 'Program years with at least one record naming the HCP.',
    'research_manufacturers': 'Distinct manufacturers making research payments naming the HCP.',
    'research_nct_ids': 'Distinct ClinicalTrials.gov identifiers on records naming the HCP.',
    'research_sole_pi_rows': 'Records on which the HCP is the only listed principal investigator.',
    'research_covered_recipient_rows': 'Records paid directly to the HCP as covered recipient.',
    'research_preclinical_rows': 'Records flagged as preclinical research.',
    'cart_research_rows_2019_2025': 'Records naming the HCP with a CAR-T product-slot or text match.',
    'cart_research_record_usd_2019_2025': 'Sum of record totals for the CAR-T matched records.',
    'cart_research_products': 'Canonical CAR-T brands matched on the HCP records.',
    'cart_research_manufacturers': 'Manufacturers on the CAR-T matched records.',
    'cart_research_nct_ids': 'ClinicalTrials.gov identifiers on the CAR-T matched records.',
    'first_research_payment_date': 'Earliest payment date on records naming the HCP.',
    'last_research_payment_date': 'Latest payment date on records naming the HCP.',
    'program_years_scanned': 'Program-year range scanned in full.',
    'feature_version': 'Version of the feature definitions.',
    'collection_snapshot_date': 'Collection-run date; exact retrieval times are in the metadata file.',
    'built_at_utc': 'UTC time the table was assembled.',
    **{f'research_rows_{year}': f'Research records naming the HCP in program year {year}.' for year in YEARS},
}
TABLES = ['research_payments_2019_2025', 'hcp_research_payment_features']
MISSING = 'Blank = unavailable, suppressed, or unobserved; zero counts are observation counts, not confirmed absence of activity.'

def make_manifests():
    dest = ROOT / 'Data' / 'manifests' / SNAPSHOT
    dictionary = {}
    for table in TABLES:
        with (OUT / (table + '.csv')).open(newline='', encoding='utf-8') as stream:
            header = next(csv.reader(stream))
        missing = [field for field in header if field not in DESCRIPTIONS]
        assert not missing, 'Missing definition: ' + ', '.join(missing)
        dictionary[table] = [{'column': field, 'description': DESCRIPTIONS[field], 'missing_value': MISSING} for field in header]
    dump(dest / 'research_payments_data_dictionary.json', dictionary)
    paths = [p for base in [RAW, INTERIM, OUT] for p in sorted(base.rglob('*')) if p.is_file() and p.suffix not in {'.tmp', '.part'}]
    write_csv(dest / 'research_payments_file_checksums.csv',
              [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in paths])
    print('RESEARCH MANIFESTS', len(paths), 'FILES')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-collect', action='store_true', help='Build from the already downloaded archives only.')
    args = parser.parse_args()
    if not args.skip_collect:
        collect()
    build()
    make_manifests()

if __name__ == '__main__':
    main()
