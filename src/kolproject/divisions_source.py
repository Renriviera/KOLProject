"""Dated public-source collection for the referring and affiliate HCP divisions.

Everything is keyed on NPI or hospital CCN. The 2026-10-09 cohort tables and the
2026-10-15 utilization tables are read, never rewritten. Pinned annual dataset
UUIDs are queried, not the moving "latest" aliases.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from .common import ROOT, now, dump, load, read_csv, plain

SNAPSHOT = '2026-10-24'
RAW = ROOT / 'Data' / 'raw' / SNAPSHOT
INTERIM = ROOT / 'Data' / 'interim' / SNAPSHOT
OUT = ROOT / 'Data' / 'processed' / SNAPSHOT
COHORT = ROOT / 'Data' / 'processed' / '2026-10-09' / 'hcps.csv'
USER_AGENT = 'Columbia-CART-KOL-Academic-Project/0.3 (public research metadata)'
PAGE = 1000

DATASETS = {
    'provider_2024': {
        'title': 'Medicare Physician & Other Practitioners - by Provider : 2024-12-01',
        'version_id': '4d0b2df0-1e99-4db7-a574-a571d99217f1', 'series_id': '8889d81e-2ee7-448f-8713-f071038289b5',
        'modified': '2026-05-21', 'service_year': 2024},
    'provider_service_2024': {
        'title': 'Medicare Physician & Other Practitioners - by Provider and Service : 2024-12-01',
        'version_id': '335e5f35-eca6-482d-87b3-f99883e213e3', 'series_id': '92396110-2aed-4d63-a6a2-5d6207d46a29',
        'modified': '2026-05-21', 'service_year': 2024},
    'inpatient_2024': {
        'title': 'Medicare Inpatient Hospitals - by Provider and Service : 2024-01-01',
        'version_id': 'ca9b5ef0-1386-4759-b2b6-5f9b35116786', 'series_id': '690ddc6c-2767-4618-b277-420ffb2bf27c',
        'modified': '2026-04-23', 'service_year': 2024},
    'part_d_2024': {
        'title': 'Medicare Part D Prescribers - by Provider and Drug : 2024-12-01',
        'version_id': 'd5aa71a8-dcc0-4570-8bcf-bd39deac69fe', 'series_id': '9552739e-3d05-4c1b-8eff-ecabf391e2e5',
        'modified': '2026-05-21', 'service_year': 2024},
}
CLAIMS_SPECIALTIES = ['Hematology-Oncology', 'Medical Oncology', 'Hematology',
                      'Hematopoietic Cell Transplantation and Cellular Therapy']
CART_DRG = '018'
MARKER_CODES = {
    'chemo_administration': ['96413', '96415', '96417'],
    'rituximab_iv': ['J9312', 'Q5115', 'Q5119', 'Q5123'],
    'rituximab_sc': ['J9311'],
    'dlbcl_agents': ['J9309', 'J9349', 'J9359', 'J9321', 'J9286', 'J9350'],
}
# Exact CMS generic-name spellings; salts are separate values in the Part D file.
PART_D_GENERICS = ['Ibrutinib', 'Acalabrutinib', 'Acalabrutinib Maleate', 'Zanubrutinib', 'Pirtobrutinib',
                   'Lenalidomide', 'Venetoclax', 'Tazemetostat Hydrobromide']
BULK_FILES = {
    'Facility_Affiliation.csv': 'https://data.cms.gov/provider-data/sites/default/files/resources/b7c4080ae144663e43353a9c35cd3f53_1790972135/Facility_Affiliation.csv',
    'DAC_NationalDownloadableFile.csv': 'https://data.cms.gov/provider-data/sites/default/files/resources/52c3f098d7e56028a298fd297cb0b38d_1790972133/DAC_NationalDownloadableFile.csv',
    'Hospital_Enrollments_2026.09.29.csv': 'https://data.cms.gov/sites/default/files/2026-10/961708ac-2136-43cb-9587-ac64af23b6ab/Hospital_Enrollments_2026.09.29.csv',
    'PBLCTN_TH_PRFL_SRCH_P06302026_06032026.csv': 'https://download.cms.gov/openpayments/SMRY_RPTS_P06302026_06032026/PBLCTN_TH_PRFL_SRCH_P06302026_06032026.csv',
    'ZipHsaHrr19.csv.zip': 'https://data.dartmouthatlas.org/downloads/geography/ZipHsaHrr19.csv.zip',
}
BULK_RELEASES = {
    'Facility_Affiliation.csv': {'source': 'CMS Doctors and Clinicians Facility Affiliation Data', 'released': '2026-10-15', 'modified': '2026-10-02'},
    'DAC_NationalDownloadableFile.csv': {'source': 'CMS Doctors and Clinicians National Downloadable File', 'released': '2026-10-15', 'modified': '2026-10-02'},
    'Hospital_Enrollments_2026.09.29.csv': {'source': 'CMS Hospital Enrollments', 'released': '2026-10-01', 'modified': '2026-10-19'},
    'PBLCTN_TH_PRFL_SRCH_P06302026_06032026.csv': {'source': 'CMS Open Payments Teaching Hospital profile list', 'released': '2026-06-30', 'modified': '2026-06-04'},
    'ZipHsaHrr19.csv.zip': {'source': 'Dartmouth Atlas ZIP to HSA/HRR crosswalk 2019', 'released': '2021-06-10', 'modified': '2021-06-10'},
}
FACT_URL = 'https://accredited.factglobal.org/?_sft_country=usa&_sft_clinical=immune-effector-cellular-therapy'
_lock = threading.Lock()
_next_request = 0.0

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def api(key):
    return f"https://data.cms.gov/data-api/v1/dataset/{DATASETS[key]['version_id']}"

def _curl(url):
    """Plain curl for hosts that reject urllib clients (FACT's WordPress host); curl's own agent string is sent."""
    header_file = RAW / '.curl_headers.tmp'
    header_file.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['curl', '-sS', '--fail', '--max-time', '120', '-D', str(header_file), url], capture_output=True, check=True)
    headers = dict(re.findall(r'^([\w-]+):\s*(.*?)\s*$', header_file.read_text(errors='replace'), re.M | re.I))
    return result.stdout, next((v for k, v in headers.items() if k.lower() == 'last-modified'), None)

def fetch(url, source_name, suffix='.json', user_agent=USER_AGENT, via_curl=False):
    """Cache an immutable response with its exact UTC retrieval time and hash."""
    global _next_request
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = RAW / source_name / (key + suffix)
    meta_path = path.with_suffix('.meta.json')
    if path.exists() and meta_path.exists():
        meta = load(meta_path)
        if sha(path) != meta['sha256']:
            raise ValueError(f'Cached response hash mismatch: {path}')
        return path.read_bytes(), meta
    for attempt in range(4):
        try:
            with _lock:
                delay = max(0, _next_request - time.monotonic())
                _next_request = max(_next_request, time.monotonic()) + 1.0
            if delay:
                time.sleep(delay)
            if via_curl:
                payload, last_modified = _curl(url)
                observed = now()
            else:
                headers = {'User-Agent': user_agent} if user_agent else {}
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read()
                    observed = now()
                    last_modified = response.headers.get('Last-Modified')
            if suffix == '.json':
                json.loads(payload)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            meta = {'source_id': source_name + ':' + key, 'source_name': source_name,
                    'source_url': url, 'retrieved_at_utc': observed, 'retrieval_time_precision': 'second',
                    'http_last_modified': last_modified, 'sha256': sha(path), 'bytes': len(payload),
                    'local_path': str(path.relative_to(ROOT)), 'client': 'curl' if via_curl else 'urllib'}
            dump(meta_path, meta)
            return payload, meta
        except Exception as exc:
            if attempt == 3:
                raise
            time.sleep(30 if '429' in str(exc) else 2 ** attempt)

def paged(key, filters, source_name):
    """All rows matching exact-value filters, following offsets to a terminal empty page."""
    records, sources, offset = [], [], 0
    while True:
        params = {f'filter[{column}]': value for column, value in filters.items()}
        params.update(size=PAGE, offset=offset)
        payload, meta = fetch(api(key) + '/data?' + urllib.parse.urlencode(params), source_name)
        page = json.loads(payload)
        if not isinstance(page, list):
            raise ValueError('Expected a list of CMS records')
        for row in page:
            for column, value in filters.items():
                if str(row[column]) != str(value):
                    raise ValueError(f'Filter {column}={value} not applied: {row[column]}')
        sources.append(meta['source_id'])
        if not page:
            break
        records.extend(page)
        offset += len(page)
        if offset > 200000:
            raise ValueError('Unexpected query size')
    return records, sources

def freeze_catalog():
    INTERIM.mkdir(parents=True, exist_ok=True)
    baseline_path = INTERIM / 'preserved_inputs.json'
    inputs = {str(p.relative_to(ROOT)): sha(p) for base in ['2026-10-09', '2026-10-15']
              for p in sorted((ROOT / 'Data' / 'processed' / base).glob('*')) if p.is_file()}
    if baseline_path.exists():
        assert load(baseline_path)['files_sha256'] == inputs, 'Preserved input tables changed'
    else:
        dump(baseline_path, {'captured_at_utc': now(), 'files_sha256': inputs})
    payload, meta = fetch('https://data.cms.gov/data.json', 'cms_catalog')
    catalog = {x.get('title'): x for x in json.loads(payload)['dataset']}
    records = {}
    for key, spec in DATASETS.items():
        record = catalog[spec['title']]
        assert record['modified'] == spec['modified'], (key, record['modified'])
        urls = {x.get('accessURL') for x in record['distribution']}
        assert api(key) + '/data' in urls, key
        assert record['temporal'][0]['startDate'] == f"{spec['service_year']}-01-01"
        assert record['temporal'][0]['endDate'] == f"{spec['service_year']}-12-31"
        records[key] = {'record': record, 'pinned_dataset_version_id': spec['version_id'],
                        'series_alias_not_used_for_collection': spec['series_id']}
        try:
            fetch('https://data.cms.gov/data-api/v1/dataset-resources/' + spec['version_id'], 'cms_resources', '.json')
        except Exception as exc:
            print('Dataset resource listing skipped for', key, exc, flush=True)
    dump(INTERIM / 'dataset_catalog_records.json', {'catalog_source': meta, 'datasets': records})

def query(name, key, filters, source_name):
    """Run one filtered pull once; a complete result is reused on rerun."""
    path = INTERIM / 'queries' / (name + '.json')
    if path.exists() and load(path).get('query_status') == 'complete':
        return load(path)
    result = {'name': name, 'dataset': key, 'dataset_version_id': DATASETS[key]['version_id'],
              'source_release_date': DATASETS[key]['modified'], 'filters': filters}
    try:
        records, sources = paged(key, filters, source_name)
        result.update(query_status='complete', records=records, row_count=len(records), sources=sources,
                      query_completed_at_utc=now(), terminal_empty_page=True)
    except Exception as exc:
        result.update(query_status='failed', error=str(exc), failed_at_utc=now())
    dump(path, result)
    print('QUERY', name, result['query_status'], result.get('row_count'), flush=True)
    return result

def collect_api():
    results = {}
    results['inpatient_drg018'] = query('inpatient_drg018', 'inpatient_2024', {'DRG_Cd': CART_DRG}, 'cms_inpatient')
    covered = set()
    for specialty in CLAIMS_SPECIALTIES:
        slug = re.sub(r'[^a-z0-9]+', '_', specialty.lower()).strip('_')
        result = query('provider_' + slug, 'provider_2024', {'Rndrng_Prvdr_Type': specialty}, 'cms_provider')
        results['provider_' + slug] = result
        covered |= {str(r['Rndrng_NPI']) for r in result.get('records', [])}
    for hcp in read_csv(COHORT):
        if hcp['npi'] not in covered:
            results['provider_core_' + hcp['npi']] = query('provider_core_' + hcp['npi'], 'provider_2024',
                                                            {'Rndrng_NPI': hcp['npi']}, 'cms_provider')
    for group, codes in MARKER_CODES.items():
        for code in codes:
            results['service_' + code] = query('service_' + code, 'provider_service_2024', {'HCPCS_Cd': code}, 'cms_provider_service')
    for generic in PART_D_GENERICS:
        slug = generic.lower().replace(' ', '_')
        results['part_d_' + slug] = query('part_d_' + slug, 'part_d_2024', {'Gnrc_Name': generic}, 'cms_part_d')
    return results

ENTRY = re.compile(r'<div style="padding:30px;">(.*?)(?=<div style="padding:30px;">|<div class="pagination">|</div>\s*</div>\s*</div>\s*</div>\s*</div>\s*</div>\s*</div>\s*</div>\s*</div>\s*</div>)', re.S)

def parse_fact(page_html):
    def tags(block, label):
        section = re.search(label + r':\s*<ul>(.*?)</ul>', block, re.S)
        return [plain(x) for x in re.findall(r'<a [^>]*rel="tag">(.*?)</a>', section.group(1))] if section else []
    entries = []
    for block in ENTRY.findall(page_html):
        head = re.search(r'<h2><a href="([^"]+)">(.*?)</a></h2>', block, re.S)
        if not head:
            continue
        director = re.search(r'Organization Director Name:\s*(?:&nbsp;)?\s*(.*?)</p>', block, re.S)
        states = re.findall(r'/state/[^"]+" rel="tag">(.*?)</a>', block)
        cities = re.findall(r'/city/[^"]+" rel="tag">(.*?)</a>', block)
        entries.append({'program_url': html.unescape(head.group(1)), 'program_name': plain(head.group(2)),
                        'organization_director': plain(director.group(1)) if director else '',
                        'states': [plain(s) for s in states], 'cities': [plain(c) for c in cities],
                        'clinical_services': tags(block, 'Clinical Services'),
                        'transplant_types': tags(block, 'Transplant Types')})
    found = re.search(r'Found (\d+) Results', page_html)
    page = re.search(r'Page (\d+) of (\d+)', page_html)
    return entries, (int(found.group(1)) if found else None), (int(page.group(1)), int(page.group(2))) if page else None

def collect_fact():
    path = INTERIM / 'fact_programs.json'
    if path.exists() and load(path).get('status') == 'complete':
        return load(path)
    programs, sources, number = [], [], 1
    while True:
        url = FACT_URL + (f'&sf_paged={number}' if number > 1 else '')
        payload, meta = fetch(url, 'fact_accredited', '.html', via_curl=True)
        text = payload.decode('utf-8', errors='replace')
        entries, found, page = parse_fact(text)
        assert page and page[0] == number, (number, page)
        sources.append(meta['source_id'])
        for entry in entries:
            programs.append({**entry, 'source_id': meta['source_id'], 'listing_page': number})
        if number == 1:
            link = re.search(r'href="([^"]*action=download_institutions[^"]*)"', text)
            if link:
                try:
                    fetch(html.unescape(link.group(1)), 'fact_accredited_download', '.xls', via_curl=True)
                except Exception as exc:
                    print('FACT download skipped:', exc, flush=True)
        if number >= page[1]:
            break
        number += 1
    assert len(programs) == found, (len(programs), found)
    assert len({p['program_url'] for p in programs}) == len(programs), 'Duplicate FACT programs across pages'
    result = {'status': 'complete', 'listing_url': FACT_URL, 'found_results': found, 'pages': number,
              'programs': programs, 'sources': sources, 'completed_at_utc': now()}
    dump(path, result)
    print('FACT PROGRAMS', len(programs), flush=True)
    return result

def register_bulk():
    """Describe pre-downloaded bulk files with the same metadata shape as API responses."""
    metas = {}
    for name, url in BULK_FILES.items():
        path = RAW / 'bulk' / name
        info = load(path.with_name(name + '.download.json'))
        assert info['source_url'] == url and info['curl_exit'] == 0, name
        head = path.open('rb').read(64)
        assert not head.lstrip().upper().startswith(b'<HTML'), f'{name} is an HTML error page, not data'
        headers = dict(re.findall(r'^([\w-]+):\s*(.*?)\s*$', path.with_name(name + '.headers').read_text(errors='replace'), re.M | re.I))
        last_modified = next((v for k, v in headers.items() if k.lower() == 'last-modified'), None)
        digest = sha(path)
        meta = {'source_id': 'bulk:' + digest[:24], 'source_name': 'bulk_' + name.rsplit('.', 1)[0].lower(),
                'source_url': url, 'retrieved_at_utc': info['retrieval_finished_at_utc'],
                'retrieval_started_at_utc': info['retrieval_started_at_utc'], 'retrieval_time_precision': 'second',
                'http_last_modified': last_modified, 'sha256': digest, 'bytes': path.stat().st_size,
                'local_path': str(path.relative_to(ROOT)), **BULK_RELEASES[name]}
        dump(path.with_name(name + '.meta.json'), meta)
        metas[name] = meta
    return metas

def collect():
    freeze_catalog()
    results = collect_api()
    fact = collect_fact()
    failed = [k for k, v in results.items() if v['query_status'] != 'complete']
    dump(INTERIM / 'collection_status.json', {
        'completed_at_utc': now(), 'query_count': len(results), 'failed_queries': failed,
        'row_counts': {k: v.get('row_count') for k, v in results.items()},
        'fact_programs': len(fact['programs'])})
    assert not failed, f'Collection incomplete; rerun to retry: {failed}'
    return results, fact

if __name__ == '__main__':
    collect()
