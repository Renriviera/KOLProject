"""Re-evaluate cached responses after identity-evidence improvements, without API calls."""
from .common import ROOT, INTERIM, load, dump, now
from .identity import supported_authorships, index_authors

def rescreen():
    from .corpus import get_corpus
    corpus = get_corpus()
    index = index_authors(corpus)
    results = []
    for path in sorted((INTERIM / 'screening').glob('*.json')):
        result = load(path)
        for match in result.get('matches', []):
            links, withheld = supported_authorships(match['record'], match['aliases'], index)
            match.update(authorships=links, withheld=withheld, supported_papers=sum(a['analysis_include'] for a in links))
        passing = [m for m in result.get('matches', []) if m['supported_papers'] >= 2]
        if result['status'] != 'query_failed':
            result['status'] = 'qualifies' if len(passing) == 1 else ('ambiguous_multiple_qualifying_npis' if len(passing) > 1 else 'not_qualified')
        result.pop('accepted', None)
        if len(passing) == 1:
            result['accepted'] = passing[0]
        result['rescreened_at_utc'] = now()
        dump(path, result)
        results.append(result)
    dump(INTERIM / 'screening_results.json', results)
    print('RESCREENED', len(results), 'UNIQUE_QUALIFIED', len({str(r['accepted']['record']['number']) for r in results if r['status'] == 'qualifies'}), flush=True)
    return results

if __name__ == '__main__':
    rescreen()
