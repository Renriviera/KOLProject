"""Frozen original publications plus a separately dated, focused search extension."""
import concurrent.futures
import urllib.parse
from .common import ROOT, INTERIM, load, dump, fetch_json

SUPPLEMENT_QUERY = '(TITLE:"CAR-T" OR TITLE:"CAR T" OR TITLE:"chimeric antigen receptor" OR TITLE:axicabtagene OR TITLE:tisagenlecleucel OR TITLE:lisocabtagene OR TITLE:brexucabtagene) AND (TITLE_ABS:lymphoma OR TITLE_ABS:DLBCL OR TITLE_ABS:LBCL) AND SRC:MED'

def get_corpus():
    path = INTERIM / 'corpus_combined.json'
    return load(path) if path.exists() else load(ROOT / 'Data/raw/legacy_2026-10-08/corpus.json')

def publication_observation(p):
    return p.get('_source_observation', {
        'source_id': 'europe_pmc_med_2017_2025_2026-10-08',
        'retrieved_on': '2026-10-08', 'retrieved_at_utc': '', 'retrieval_time_precision': 'day',
        'query_family': 'original_lymphoma_in_title',
    })

def extend_corpus(workers=3):
    def year_query(year):
        query = f'{SUPPLEMENT_QUERY} AND FIRST_PDATE:[{year}-01-01 TO {year}-12-31]'
        cursor = '*'
        rows = []
        sources = []
        while True:
            params = {'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': 500, 'cursorMark': cursor}
            url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + urllib.parse.urlencode(params)
            data, meta = fetch_json(url, 'europe_pmc')
            page = data.get('resultList', {}).get('result', [])
            for p in page:
                p['_source_observation'] = {
                    'source_id': meta['source_id'], 'retrieved_on': meta['retrieved_at_utc'][:10],
                    'retrieved_at_utc': meta['retrieved_at_utc'], 'retrieval_time_precision': 'second',
                    'query_family': 'supplement_cart_in_title_lymphoma_in_title_or_abstract',
                }
            rows.extend(page)
            sources.append(meta)
            if len(rows) >= int(data['hitCount']):
                break
            if not page or not data.get('nextCursorMark'):
                raise ValueError('Incomplete Europe PMC pagination')
            cursor = data['nextCursorMark']
        print('SUPPLEMENT_YEAR', year, 'RECORDS', len(rows), flush=True)
        return rows, sources
    original = load(ROOT / 'Data/raw/legacy_2026-10-08/corpus.json')
    merged = {p['id']: p for p in original}
    sources = []
    with concurrent.futures.ThreadPoolExecutor(workers) as executor:
        for rows, observation_sources in executor.map(year_query, range(2017, 2026)):
            sources.extend(observation_sources)
            for p in rows:
                # Preserve the original observation for already archived PMIDs.
                merged.setdefault(p['id'], p)
    combined = list(merged.values())
    dump(INTERIM / 'corpus_combined.json', combined)
    dump(INTERIM / 'supplement_query_manifest.json', {
        'query': SUPPLEMENT_QUERY, 'years': [2017, 2025], 'original_records': len(original),
        'additional_distinct_records': len(combined) - len(original), 'combined_records': len(combined),
        'sources': sources,
    })
    print('COMBINED_CORPUS', len(combined), 'ADDED', len(combined) - len(original), flush=True)
    return combined

if __name__ == '__main__':
    extend_corpus()
