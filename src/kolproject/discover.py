"""Discover additional author candidates without relaxing NPI/publication gates."""
import collections
import re
from .common import ROOT, INTERIM, load, dump, tokens
from .corpus import get_corpus
from .identity import eligible, author_affiliation

US = re.compile(r'\b(USA|United States|U\.S\.A\.|New York|Houston|Boston|Seattle|Chicago|Philadelphia|Pittsburgh|Cleveland|Milwaukee|Nashville|Durham|Tampa|Rochester|Baltimore|Los Angeles|San Francisco|San Diego|Stanford|Duarte|Columbus|Columbia University|North Carolina|Minnesota|Moffitt|MD Anderson|M\.D\. Anderson|Dana.Farber|Memorial Sloan|Vanderbilt|Fred Hutch|Mayo Clinic|Wisconsin|Nebraska|Michigan|Colorado|California|Florida|Pennsylvania|Texas|Ohio|Missouri|Massachusetts|Washington University)\b', re.I)

def discover(limit=200):
    old = load(ROOT / 'Data/raw/legacy_2026-10-08/expansion_candidates.json')
    seeds = load(ROOT / 'Data/raw/legacy_2026-10-08/cohort_v1.json')
    keys = {(tuple(tokens(r['first'])), tuple(tokens(r['last']))) for r in old}
    keys |= {(tuple(tokens(r['seed_first'])), tuple(tokens(r['seed_last']))) for r in seeds}
    groups = {}
    for p in get_corpus():
        if not eligible(p):
            continue
        for a in p.get('authorList', {}).get('author', []):
            given = a.get('firstName', '').split()
            last = a.get('lastName', '')
            if not given or len(given[0]) < 2 or not last:
                continue
            key = (tuple(tokens(given[0])), tuple(tokens(last)))
            if key in keys:
                continue
            group = groups.setdefault(key, {'first': given[0], 'last': last, 'pmids': set(), 'us_pmids': set()})
            group['pmids'].add(p['id'])
            if US.search(author_affiliation(a)):
                group['us_pmids'].add(p['id'])
    candidates = [r for r in groups.values() if len(r['pmids']) >= 2 and r['us_pmids']]
    candidates.sort(key=lambda r: (-len(r['pmids']), r['last'], r['first']))
    rows = [{**r, 'pmids': sorted(r['pmids']), 'us_pmids': sorted(r['us_pmids']),
             'candidate_id': f'D{i:04}', 'screen_this_release': i <= limit}
            for i, r in enumerate(candidates, 1)]
    dump(INTERIM / 'supplemental_candidates_all.json', rows)
    dump(INTERIM / 'supplemental_candidates_to_screen.json', rows[:limit])
    print('SUPPLEMENTAL_DISCOVERY', len(rows), 'SCREEN_LIMIT', min(limit, len(rows)), flush=True)
    return rows

if __name__ == '__main__':
    discover()
