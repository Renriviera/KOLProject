"""Screen the complete author candidate pool, with resume-safe source snapshots."""
from __future__ import annotations

import concurrent.futures
import json
import urllib.parse
from .common import ROOT, INTERIM, load, dump, fetch_json, tokens, now
from .identity import active_oncology, supported_authorships, index_authors

def screen_all(workers=6):
    from .corpus import get_corpus
    corpus = get_corpus()
    candidates = load(ROOT / 'Data' / 'raw' / 'legacy_2026-10-08' / 'expansion_candidates.json')
    seeds = load(ROOT / 'Data' / 'raw' / 'legacy_2026-10-08' / 'cohort_v1.json')
    index = index_authors(corpus)
    tasks = [{'candidate_id': f'C{i:04}', 'first': c['first'], 'last': c['last'], 'raw_papers': len(c['pmids']), 'rank': i} for i, c in enumerate(candidates, 1)]
    for seed in seeds:
        tasks.append({'candidate_id': 'SEED_' + seed['hcp_id'], 'first': seed['seed_first'], 'last': seed['seed_last'], 'npi': seed['npi'], 'rank': 0})
    extra_path = INTERIM / 'supplemental_candidates_to_screen.json'
    if extra_path.exists():
        for i, c in enumerate(load(extra_path), 1):
            tasks.append({'candidate_id': c['candidate_id'], 'first': c['first'], 'last': c['last'], 'raw_papers': len(c['pmids']), 'rank': len(candidates) + i})

    def one(task):
        cache = INTERIM / 'screening' / (task['candidate_id'] + '.json')
        if cache.exists() and load(cache).get('status') != 'query_failed':
            return load(cache)
        params = {'version': '2.1', 'number': task['npi']} if task.get('npi') else {'version': '2.1', 'first_name': task['first'] + '*', 'last_name': task['last'], 'limit': 200, 'enumeration_type': 'NPI-1'}
        url = 'https://npiregistry.cms.hhs.gov/api/?' + urllib.parse.urlencode(params)
        out = {**task, 'screened_at_utc': now(), 'npi_search_url': url}
        try:
            data, meta = fetch_json(url, 'nppes')
            out['source'] = meta
            matches = []
            for record in data.get('results', []):
                if not active_oncology(record):
                    continue
                basic = record['basic']
                if not task.get('npi') and (tokens(basic.get('first_name', '')) != tokens(task['first']) or tokens(basic.get('last_name', '')) != tokens(task['last'])):
                    continue
                aliases = [(task['first'], task['last'])]
                if task.get('npi'):
                    aliases.append((basic.get('first_name', ''), basic.get('last_name', '')))
                    if task['last'] == 'Perales':
                        aliases.append(('Miguel-Angel', 'Perales'))
                    if task['last'] == 'Dholaria':
                        aliases.append(('Bhagirathbhai', 'Dholaria'))
                    if task['last'] == 'Palomba':
                        aliases.extend([('Maria', 'Palomba'), ('M', 'Palomba')])
                authorships, withheld = supported_authorships(record, aliases, index)
                count = sum(r['analysis_include'] for r in authorships)
                matches.append({'record': record, 'aliases': aliases, 'authorships': authorships, 'withheld': withheld, 'supported_papers': count})
            passing = [m for m in matches if m['supported_papers'] >= 2]
            out['status'] = 'qualifies' if len(passing) == 1 else ('ambiguous_multiple_qualifying_npis' if len(passing) > 1 else 'not_qualified')
            out['registry_count'] = data.get('result_count', 0)
            out['matches'] = matches
            if len(passing) == 1:
                out['accepted'] = passing[0]
        except Exception as exc:
            out['status'] = 'query_failed'
            out['error'] = str(exc)
        dump(cache, out)
        return out

    results = []
    with concurrent.futures.ThreadPoolExecutor(workers) as executor:
        for i, result in enumerate(executor.map(one, tasks), 1):
            results.append(result)
            if i % 25 == 0:
                n = len({str(r['accepted']['record']['number']) for r in results if r['status'] == 'qualifies'})
                print('SCREENED', i, '/', len(tasks), 'UNIQUE_QUALIFIED', n, flush=True)
                dump(INTERIM / 'screening_results.json', results)
    dump(INTERIM / 'screening_results.json', results)
    passing = {str(r['accepted']['record']['number']) for r in results if r['status'] == 'qualifies'}
    print('COMPLETE', len(results), 'UNIQUE_QUALIFIED', len(passing), flush=True)
    return results

if __name__ == '__main__':
    screen_all()
