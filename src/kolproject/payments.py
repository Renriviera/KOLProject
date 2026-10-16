"""Exact-NPI, fully paginated CMS general-payment snapshots."""
import concurrent.futures
import urllib.parse
from .common import INTERIM, load, dump, fetch_json, now

DATASET = 'e6b17c6a-2534-4207-a4a1-6746a14911ff'

def collect_payments(people=None, workers=6):
    people = people or load(INTERIM / 'selected_people.json')
    def one(person):
        npi = str(person['accepted']['record']['number'])
        path = INTERIM / 'payments' / (npi + '.json')
        if path.exists() and load(path).get('status') == 'complete':
            return load(path)
        records, sources = [], []
        offset = 0
        out = {'npi': npi, 'program_year': 2024, 'dataset_id': DATASET, 'source_release_date': '2026-06-30'}
        try:
            while True:
                params = {'limit': 500, 'offset': offset,
                          'conditions[0][property]': 'covered_recipient_npi',
                          'conditions[0][value]': npi, 'conditions[0][operator]': '='}
                url = f'https://openpaymentsdata.cms.gov/api/1/datastore/query/{DATASET}/0?' + urllib.parse.urlencode(params)
                data, meta = fetch_json(url, 'cms_open_payments')
                page = data.get('results', [])
                assert all(str(r['covered_recipient_npi']) == npi for r in page)
                records.extend(page)
                sources.append(meta)
                if len(records) >= int(data['count']):
                    assert len(records) == int(data['count'])
                    break
                if not page:
                    raise ValueError('Pagination ended before advertised count')
                offset += len(page)
            out.update(status='complete', records=records, count=len(records), sources=sources, retrieved_at_utc=max(s['retrieved_at_utc'] for s in sources))
        except Exception as exc:
            out.update(status='failed', error=str(exc), records=records, sources=sources, failed_at_utc=now())
        dump(path, out)
        return out
    results = []
    with concurrent.futures.ThreadPoolExecutor(workers) as executor:
        for i, result in enumerate(executor.map(one, people), 1):
            results.append(result)
            if i % 25 == 0:
                print('PAYMENTS', i, '/', len(people), 'ROWS', sum(len(r['records']) for r in results), flush=True)
    dump(INTERIM / 'payments_results.json', results)
    return results

if __name__ == '__main__':
    collect_payments()
