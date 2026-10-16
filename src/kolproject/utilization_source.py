"""Immutable, dated CMS provider/service collection for the existing NPI cohort."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from .common import ROOT, now, dump, load, read_csv

SNAPSHOT = '2026-10-15'
YEAR = 2024
DATASET_ID = '335e5f35-eca6-482d-87b3-f99883e213e3'
SERIES_ID = '92396110-2aed-4d63-a6a2-5d6207d46a29'
RELEASE_DATE = '2026-05-21'
API = f'https://data.cms.gov/data-api/v1/dataset/{DATASET_ID}'
RAW = ROOT / 'Data' / 'raw' / SNAPSHOT / 'cms_provider_service'
INTERIM = ROOT / 'Data' / 'interim' / SNAPSHOT / 'cms_provider_service'
OUT = ROOT / 'Data' / 'processed' / SNAPSHOT
COHORT = ROOT / 'Data' / 'processed' / '2026-10-09' / 'hcps.csv'
REFERENCE_SOURCES = {
    'hcpcs_2024': [
        'https://www.cms.gov/files/zip/january-2024-alpha-numeric-hcpcs-files.zip',
        'https://www.cms.gov/files/zip/april-2024-alpha-numeric-hcpcs-files.zip',
        'https://www.cms.gov/files/zip/july-2024-alpha-numeric-hcpcs-file.zip',
        'https://www.cms.gov/files/zip/october-2024-alpha-numeric-hcpcs-file.zip',
    ],
    'cms_documentation': [
        'https://data.cms.gov/sites/default/files/2025-03/bbb1e50e-5ba8-42ed-b072-18368b6f37f9/MUP_PHY_RY25_20250312_DD_PRV_SVC_508.pdf',
        'https://data.cms.gov/sites/default/files/2025-04/MUP_PHY_RY25_202350312_Methodology_508.pdf',
        'https://data.cms.gov/sites/default/files/2025-04/MUP_PHY_RY25_20250312Technical%20Specifications_508.pdf',
        'https://www.cms.gov/Regulations-and-Guidance/Guidance/Manuals/Downloads/clm104c32.pdf',
    ],
}
_lock = threading.Lock()
_next_request = 0.0

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def fetch(url, source_name='cms_provider_service', suffix='.json'):
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
                _next_request = max(_next_request, time.monotonic()) + 0.4
            if delay:
                time.sleep(delay)
            request = urllib.request.Request(url, headers={'User-Agent': 'Columbia-CART-KOL-Academic-Project/0.3 (public research metadata)'})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                observed = now()
                last_modified = response.headers.get('Last-Modified')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            meta = {'source_id': source_name + ':' + key, 'source_name': source_name,
                    'source_url': url, 'retrieved_at_utc': observed, 'retrieval_time_precision': 'second',
                    'http_last_modified': last_modified, 'sha256': sha(path), 'bytes': len(payload),
                    'local_path': str(path.relative_to(ROOT))}
            dump(meta_path, meta)
            return payload, meta
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

def freeze_metadata():
    INTERIM.mkdir(parents=True, exist_ok=True)
    baseline_path = INTERIM / 'preserved_cohort.json'
    original_files = {str(p.relative_to(ROOT)): sha(p) for p in sorted(COHORT.parent.glob('*')) if p.is_file()}
    if baseline_path.exists():
        assert load(baseline_path)['files_sha256'] == original_files, 'Original cohort tables changed'
    else:
        dump(baseline_path, {'captured_at_utc': now(), 'files_sha256': original_files})
    payload, meta = fetch('https://data.cms.gov/data.json', 'cms_catalog')
    catalog = json.loads(payload)
    record = next(x for x in catalog['dataset'] if x.get('title') == 'Medicare Physician & Other Practitioners - by Provider and Service : 2024-12-01')
    assert record['modified'] == RELEASE_DATE
    assert any(x.get('accessURL') == API + '/data' for x in record['distribution'])
    assert record['temporal'][0]['startDate'] == '2024-01-01'
    assert record['temporal'][0]['endDate'] == '2024-12-31'
    dump(INTERIM / 'dataset_catalog_record.json', {'record': record, 'source': meta,
          'pinned_dataset_version_id': DATASET_ID, 'series_alias_not_used_for_collection': SERIES_ID})
    for suffix in ['/dictionary']:
        fetch(API + suffix, 'cms_schema')
    fetch('https://data.cms.gov/data-api/v1/dataset-resources/' + DATASET_ID, 'cms_resources')
    for source_name, urls in REFERENCE_SOURCES.items():
        for url in urls:
            fetch(url, source_name, Path(urllib.parse.urlparse(url).path).suffix)

def collect(workers=4):
    freeze_metadata()
    cohort = read_csv(COHORT)
    assert len(cohort) == len({r['npi'] for r in cohort}) == 500
    def one(hcp):
        npi = hcp['npi']
        path = INTERIM / 'npi_queries' / (npi + '.json')
        if path.exists() and load(path).get('query_status') == 'complete':
            return load(path)
        result = {'npi': npi, 'hcp_id': hcp['hcp_id'], 'service_year': YEAR,
                  'dataset_version_id': DATASET_ID, 'source_release_date': RELEASE_DATE}
        records, sources, offset = [], [], 0
        try:
            while True:
                params = {'filter[Rndrng_NPI]': npi, 'size': 500, 'offset': offset}
                url = API + '/data?' + urllib.parse.urlencode(params)
                payload, meta = fetch(url)
                page = json.loads(payload)
                if not isinstance(page, list):
                    raise ValueError('Expected a list of CMS records')
                assert all(str(r['Rndrng_NPI']) == npi for r in page), 'NPI filter was not applied'
                sources.append(meta)
                if not page:
                    break
                records.extend({**r, '_source': meta} for r in page)
                offset += len(page)
                if offset > 100000:
                    raise ValueError('Unexpected query size')
            keys = [(r['Rndrng_NPI'], r['HCPCS_Cd'], r['Place_Of_Srvc']) for r in records]
            assert len(keys) == len(set(keys)), 'Duplicate source keys across pages'
            result.update(query_status='complete', records=records, row_count=len(records), sources=sources,
                          query_completed_at_utc=now(), terminal_empty_page=True)
        except Exception as exc:
            result.update(query_status='failed', error=str(exc), records=records, row_count=len(records), sources=sources,
                          failed_at_utc=now())
        dump(path, result)
        return result
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(one, h) for h in cohort]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            if len(results) % 25 == 0:
                print('CMS UTILIZATION', len(results), '/ 500', 'ROWS', sum(x['row_count'] for x in results),
                      'FAILED', sum(x['query_status'] != 'complete' for x in results), flush=True)
    results.sort(key=lambda x: x['npi'])
    dump(INTERIM / 'collection_status.json', {'completed_at_utc': now(), 'query_count': len(results),
         'complete_count': sum(r['query_status'] == 'complete' for r in results),
         'source_row_count': sum(r['row_count'] for r in results),
         'failed_npis': [r['npi'] for r in results if r['query_status'] != 'complete']})
    assert all(r['query_status'] == 'complete' for r in results), 'Collection incomplete; rerun to retry failed NPIs'
    return results

if __name__ == '__main__':
    collect()
