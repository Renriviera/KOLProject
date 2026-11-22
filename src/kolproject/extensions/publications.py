"""Publication history and co-authorship for the 17,510-NPI universe, snapshot 2026-11-20.

The 3,363 held CAR-T lymphoma papers of 2026-10-09 carry 34,449 author slots; only the 4,991 links of the 500 core
HCPs were published. This module links further slots to non-core universe NPIs on name plus evidence (hospital or
group name in the affiliation, practice or center city with state, or an ORCID already anchored by such a link),
grades every link, builds the co-author pairs that the new links add, re-runs the 2026-10-09 Europe PMC query to
add papers not yet held, and links core and universe NPIs on those papers with the same rules.

Identity rule (a slot is one author position on one paper):
  A  name + institution, or an ORCID iD anchored by an A or B link of the same NPI; institution evidence may also
     separate same-name candidates when exactly one of them has any evidence.
  B  name + city and state of the NPI's practice or of a CAR-T center it is affiliated with; name unique in the
     universe.
  C  name only, unique in the universe, affiliation neither outside the United States nor naming only US states
     where the NPI has no practice, hospital or group address. Tier C links, and A or B links without institution
     evidence whose name another hematology-oncology clinician shares nationally (NPPES), are written only to a local
     review file under Data/interim and are never tracked.
Institution words that fit several hospitals or groups of the universe (a multi-site system) also need the state; a
name with one strong word, or only state words, must appear as a phrase.
Names with two or more universe candidates are not linked unless institution evidence separates them; a slot that a
2026-10-09 core link (published or withheld) holds is never relinked; an NPI matched twice in one paper is withheld.
"""
from __future__ import annotations

import collections
import itertools
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone

from ..common import ROOT, clean_affiliation, norm, plain, tokens
from ..identity import author_affiliation, eligible
from .base import (DIVISION_GROUPS, OUT, RAW, SNAPSHOT, UNIVERSE, UNIVERSE_SNAPSHOT, column, duplicates, existing_keys, fetch, load,
                   log_local, new_rows, now, publish, read_csv, request_log, sha, write_module_metadata, dump, INTERIM)

MODULE = 'publications'
UA, UE, NP, NA = 'universe_authorships', 'universe_coauthor_edges', 'new_publications', 'new_authorships'
FT, NE = 'hcp_universe_publication_features', 'new_paper_coauthor_edges'
CE, HC, CC = 'corpus_citation_edges', 'hcp_citation_links', 'publication_citation_counts_2026_11_20'
PY, HY, NY = 'coauthor_pairs_by_year', 'hcp_publications_by_year', 'network_summary_by_year'
TABLES = [UA, UE, NP, NA, NE, CE, HC, CC, FT, PY, HY, NY]
PINNED = [UA, UE, NP, NA, NE, CE, HC, CC, FT]   # verified on 2026-11-20; a rebuild must leave them byte-identical
GROUP_ORDER = ['core', 'affiliates', 'referring', 'other']
REVIEW = INTERIM / 'publications' / 'review_links_not_tracked.csv'
CORE = '2026-10-09'
CORPUS = ROOT / 'Data' / 'interim' / CORE / 'corpus_combined.json'
QUERY = ('(TITLE:"CAR-T" OR TITLE:"CAR T" OR TITLE:"chimeric antigen receptor" OR TITLE:axicabtagene OR '
         'TITLE:tisagenlecleucel OR TITLE:lisocabtagene OR TITLE:brexucabtagene) AND (TITLE_ABS:lymphoma OR '
         'TITLE_ABS:DLBCL OR TITLE_ABS:LBCL) AND SRC:MED')
# The 2026-10-09 corpus is the union of two queries. The first (lymphoma in title, CAR-T term in title or abstract)
# is the 245-HCP project query, recorded in Data/legacy/cohort_245/scripts/collect.py and its README; corpus.py
# holds only the second. Families are named as in publications.csv.
ORIGINAL_QUERY = ('(TITLE_ABS:"CAR-T" OR TITLE_ABS:"CAR T" OR TITLE_ABS:"chimeric antigen receptor" OR '
                  'TITLE_ABS:axicabtagene OR TITLE_ABS:tisagenlecleucel OR TITLE_ABS:lisocabtagene OR '
                  'TITLE_ABS:brexucabtagene) AND (TITLE:lymphoma OR TITLE:DLBCL OR TITLE:LBCL) AND SRC:MED')
FAMILIES = {'original_lymphoma_in_title': ORIGINAL_QUERY,
            'supplement_cart_in_title_lymphoma_in_title_or_abstract': QUERY}
WINDOW = ('2017-01-01', SNAPSHOT)
INSTITUTION_RULES = {'name_institution', 'name_institution_disambiguated'}
EPMC = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?'
TIERS = {'A', 'B', 'C'}
RULES = {'A': {'name_institution', 'name_institution_disambiguated', 'name_orcid_anchored'},
         'B': {'name_city_state_unique'}, 'C': {'name_only_unique'}}

STOP = set('''the of and at for in on inc llc lp llp pa pc pllc ltd corp corporation co company hospital hospitals
medical medicine center centers centre health healthcare system systems services service clinic clinics physician
physicians group associates association care cancer oncology hematology haematology institute institutes regional
community general campus dba foundation trust partners network practice practices specialists specialty specialties
department dept division unit school sciences science research faculty ii iii dr md lymphoma myeloma leukemia
transplant transplantation blood marrow bone cellular cell therapy therapies immunotherapy comprehensive'''.split())
WEAK = set('''university univ college state saint st north south east west central northern southern eastern western
county city new national american united valley mount mt lake san santa los el de la memorial methodist baptist
presbyterian lutheran catholic christian mercy providence trinity sacred heart children childrens womens'''.split())
STATES = {'AL': 'alabama', 'AK': 'alaska', 'AZ': 'arizona', 'AR': 'arkansas', 'CA': 'california', 'CO': 'colorado',
          'CT': 'connecticut', 'DE': 'delaware', 'DC': 'district of columbia', 'FL': 'florida', 'GA': 'georgia',
          'HI': 'hawaii', 'ID': 'idaho', 'IL': 'illinois', 'IN': 'indiana', 'IA': 'iowa', 'KS': 'kansas',
          'KY': 'kentucky', 'LA': 'louisiana', 'ME': 'maine', 'MD': 'maryland', 'MA': 'massachusetts',
          'MI': 'michigan', 'MN': 'minnesota', 'MS': 'mississippi', 'MO': 'missouri', 'MT': 'montana',
          'NE': 'nebraska', 'NV': 'nevada', 'NH': 'new hampshire', 'NJ': 'new jersey', 'NM': 'new mexico',
          'NY': 'new york', 'NC': 'north carolina', 'ND': 'north dakota', 'OH': 'ohio', 'OK': 'oklahoma',
          'OR': 'oregon', 'PA': 'pennsylvania', 'RI': 'rhode island', 'SC': 'south carolina', 'SD': 'south dakota',
          'TN': 'tennessee', 'TX': 'texas', 'UT': 'utah', 'VT': 'vermont', 'VA': 'virginia', 'WA': 'washington',
          'WV': 'west virginia', 'WI': 'wisconsin', 'WY': 'wyoming', 'PR': 'puerto rico'}
ABROAD = re.compile(r'\b(china|japan|germany|france|italy|spain|korea|united kingdom|england|scotland|wales|uk|'
                    r'canada|australia|netherlands|belgium|switzerland|austria|sweden|norway|denmark|finland|israel|'
                    r'india|brazil|mexico|argentina|turkey|iran|poland|czech|greece|portugal|ireland|taiwan|'
                    r'singapore|egypt|saudi|russia|hungary|chile|colombia|new zealand|thailand|vietnam|pakistan)\b')
US = re.compile(r'\b(usa|u\.s\.a|united states)\b')


# ---------------------------------------------------------------- name and evidence rules (pure functions)

def name_key(first, last):
    """Lower-case token key of the first given name and the full surname; None when the given name is an initial."""
    given = str(first or '').split()
    head, surname = tuple(tokens(given[0])) if given else (), tuple(tokens(last or ''))
    if not head or not surname or len(''.join(head)) < 2:
        return None
    return head, surname


STATE_WORDS = {w for name in STATES.values() for w in name.split()}
TRAILING = set('''hospital hospitals medical medicine center centers centre health healthcare system systems services clinic
clinics group physicians physician associates foundation institute network practice partners care'''.split())
LEGAL = {'the', 'inc', 'llc', 'lp', 'llp', 'pa', 'pc', 'pllc', 'ltd', 'corp', 'corporation', 'co', 'company'}


def distinctive(name):
    """(tokens, strong count, phrase) of a hospital or group name that can identify it, or None when too generic.

    When there is one strong word, or every strong word is part of a state name (Texas Oncology, University of Utah),
    the words alone could be a place name in any affiliation (Tampa General Medical Group in "Moffitt, Tampa"), so the
    phrase must appear in order: the name without legal suffixes, and for a non-state word also without trailing
    generic words ("tampa general", "mayo", "texas oncology").
    """
    found = [t for t in tokens(name) if len(t) > 1 and t not in STOP]
    strong = [t for t in found if t not in WEAK]
    if not strong:
        return None
    words = [t for t in tokens(name) if t not in LEGAL]
    state_only = all(t in STATE_WORDS for t in strong)
    while words and words[-1] in TRAILING and not state_only:
        words.pop()
    phrase = ' '.join(words) if len(strong) == 1 or state_only else ''
    return frozenset(found), len(strong), phrase


def state_named(text, state):
    """True when the affiliation names the US state by its full name or its upper-case postal code."""
    return bool(state) and (bool(re.search(r'\b' + STATES.get(state, '#') + r'\b', norm(text)))
                            or bool(re.search(r'\b' + state + r'\b', text)))


def city_named(text, city):
    city = norm(city).replace('st.', 'st').replace('saint ', 'st ')
    body = norm(text).replace('st.', 'st').replace('saint ', 'st ')
    return bool(city) and bool(re.search(r'\b' + re.escape(city) + r'\b', body))


STREET = re.compile(r'\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z.\'-]*\s+){1,4}(?:Avenue|Ave|Street|St|Road|Rd|'
                    r'Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Place|Pl|Parkway|Pkwy|Court|Ct|Circle|Highway|Hwy|Plaza|'
                    r'Terrace|Square|Pike)\b\.?,?\s*')
PHONE = re.compile(r'(?:\b(?:tel|phone|fax)\.?:?\s*)?\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b', re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')


def scrub(affiliation):
    """Affiliation text for a tracked table: email addresses, street addresses and phone numbers removed."""
    return re.sub(r'\s{2,}', ' ', PHONE.sub('', STREET.sub('', clean_affiliation(affiliation)))).strip(' ,;.')


def usable(affiliation):
    """Affiliations too long or listing several institutions are not used as identity evidence (as on 2026-10-09)."""
    return bool(affiliation) and len(affiliation) <= 700 and len(re.findall(r'\([A-Z][A-Z., ]+\)', affiliation)) < 2


def abroad(affiliation):
    text = norm(affiliation)
    return bool(ABROAD.search(text)) and not US.search(text)


def institution_hit(affiliation, places):
    """First (label, ccn, multi) whose distinctive tokens all occur in the affiliation, with state when needed.

    The state must be named when only one word is strong, or when the words fit several hospitals or groups of the
    universe (a multi-site system, where a namesake may work at a sister hospital). When the one strong word is itself
    part of the state name (University of Washington), the postal code is needed.
    """
    words, text = set(tokens(affiliation)), ' '.join(tokens(affiliation))
    for label, ccn, state, (need, strong, phrase), multi in places:
        if not need <= words or (phrase and not re.search(r'\b' + phrase + r'\b', text)):
            continue
        if multi and not state_named(affiliation, state):
            continue
        if strong >= 2:
            return label, ccn, multi
        word = next(t for t in need if t not in WEAK)
        named = (bool(re.search(r'\b' + state + r'\b', affiliation)) if state and word in STATES.get(state, '').split()
                 else state_named(affiliation, state))
        if named:
            return label, ccn, multi
    return None


def states_in(affiliation):
    """US postal codes the affiliation names, by upper-case code or full state name."""
    text = norm(affiliation)
    return {c for c, name in STATES.items() if re.search(r'\b' + c + r'\b', affiliation) or re.search(r'\b' + name + r'\b', text)}


def city_hit(affiliation, cities):
    for city, state in cities:
        if city_named(affiliation, city) and state_named(affiliation, state):
            return city, state
    return None


def grade(candidates, evidence, affiliation, states=None):
    """(npi, tier, rule) for one slot or (None, '', reason). evidence maps npi -> {'institution': ..., 'city': ...}."""
    with_inst = [n for n in candidates if evidence.get(n, {}).get('institution')]
    with_any = [n for n in candidates if evidence.get(n, {}).get('institution') or evidence.get(n, {}).get('city')]
    if len(candidates) == 1:
        npi = candidates[0]
        if with_inst:
            return npi, 'A', 'name_institution'
        if with_any:
            return npi, 'B', 'name_city_state_unique'
        if abroad(affiliation):
            return None, '', 'affiliation_outside_us'
        named = states_in(affiliation)
        if named and states is not None and not named & states.get(npi, set()):
            return None, '', 'affiliation_state_disagrees'
        return npi, 'C', 'name_only_unique'
    if len(with_inst) == 1 and len(with_any) == 1:
        return with_inst[0], 'A', 'name_institution_disambiguated'
    return None, '', 'ambiguous_name_multiple_universe_npis'


def anchor_orcids(links):
    """ORCID -> NPI for ORCIDs carried by A or B links of exactly one NPI."""
    owners = collections.defaultdict(set)
    for r in links:
        if r['orcid'] and r['confidence_tier'] in ('A', 'B'):
            owners[r['orcid']].add(r['npi'])
    return {o: next(iter(n)) for o, n in owners.items() if len(n) == 1}


def withhold_repeats(links):
    """Drop links where one NPI is matched to two slots of the same paper; return (kept, withheld)."""
    count = collections.Counter((r['npi'], r['pmid']) for r in links)
    return ([r for r in links if count[r['npi'], r['pmid']] == 1], [r for r in links if count[r['npi'], r['pmid']] > 1])


def norm_title(title):
    return ' '.join(tokens(plain(title)))


def norm_doi(doi):
    return str(doi or '').strip().lower().removeprefix('https://doi.org/')


def edges(links, author_counts, dates, observed, core, core_pairs=False):
    """Unordered NPI pairs sharing included papers; fractional weight sums 1/(authors - 1) over shared papers.

    Core-core pairs are skipped unless `core_pairs` is set (they are in coauthor_edges.csv for held papers).
    """
    by_paper = collections.defaultdict(set)
    for r in links:
        by_paper[r['pmid']].add(r['npi'])
    pairs = collections.defaultdict(list)
    for pmid, npis in by_paper.items():
        for a, b in itertools.combinations(sorted(npis), 2):
            if a in core and b in core and not core_pairs:
                continue
            pairs[a, b].append(pmid)
    rows = []
    for (a, b), pmids in sorted(pairs.items()):
        pmids = sorted(pmids)
        rows.append({'npi_a': a, 'npi_b': b, 'pair_type': pair_type(a, b, core), 'shared_publications': len(pmids),
                     'fractional_weight': repr(sum(1 / max(int(author_counts[p]) - 1, 1) for p in pmids)),
                     'evidence_pmids': ';'.join(pmids),
                     'first_shared_publication_date': min(dates[p] for p in pmids),
                     'last_shared_publication_date': max(dates[p] for p in pmids),
                     'evidence_metadata_first_observed_on': min(observed[p] for p in pmids),
                     'evidence_metadata_last_observed_on': max(observed[p] for p in pmids)})
    return rows


def features(people, core_links, ua, na, ue, old_edges, ne=(), hc=(), stamp=''):
    """One row per universe NPI from analysis-included links (2026-10-09 core links, universe and new links)."""
    core = {n for n, p in people.items() if p['is_core'] == '1'}
    held, new = collections.defaultdict(dict), collections.defaultdict(dict)
    for r in core_links:
        if str(r['analysis_include']) == '1':
            held[r['npi']][r['pmid']] = r
    for r in ua:
        if str(r['analysis_include']) == '1':
            held[r['npi']][r['pmid']] = r
    for r in na:
        if str(r['analysis_include']) == '1':
            new[r['npi']][r['pmid']] = r
    partners = collections.defaultdict(set)
    for e in ue:
        partners[e['npi_a']].add(e['npi_b'])
        partners[e['npi_b']].add(e['npi_a'])
    for e in old_edges:
        a, b = e['hcp_id_a'].removeprefix('NPI'), e['hcp_id_b'].removeprefix('NPI')
        partners[a].add(b)
        partners[b].add(a)
    later = collections.defaultdict(set)
    for e in ne:
        later[e['npi_a']].add(e['npi_b'])
        later[e['npi_b']].add(e['npi_a'])
    received, citers, received_apart = collections.Counter(), collections.defaultdict(set), collections.Counter()
    for r in hc:
        received[r['cited_npi']] += int(r['citation_pairs'])
        received_apart[r['cited_npi']] += int(r['citation_pairs_not_coauthors'])
        citers[r['cited_npi']].add(r['citing_npi'])
    rows = []
    for npi in sorted(people):
        links = list(held[npi].values()) + list(new[npi].values())
        years = sorted(r['publication_date'][:4] for r in links)
        lead = sum(str(r['author_position']) == '1' or str(r['author_position']) == str(r['author_count']) for r in links)
        ccns = sorted({r.get('evidence_ccn', '') for r in links} - {''})
        tiers = sorted({r.get('confidence_tier', '') for r in links} - {''})
        rows.append({'npi': npi, 'is_core': int(npi in core),
                     'link_status': 'core' if npi in core else ('linked' if links else 'no_included_link'),
                     'held_publications_2017_2025': len(held[npi]), 'new_publications_2026': len(new[npi]),
                     'first_publication_year': years[0] if years else '', 'last_publication_year': years[-1] if years else '',
                     'first_or_last_author_publications': lead,
                     'distinct_core_coauthors': len(partners[npi] & core),
                     'distinct_universe_coauthors': len(partners[npi] - core),
                     'new_paper_distinct_core_coauthors': len(later[npi] & core),
                     'new_paper_distinct_universe_coauthors': len(later[npi] - core),
                     'corpus_citation_links_received': received[npi],
                     'corpus_citation_links_received_not_coauthors': received_apart[npi],
                     'distinct_citing_npis': len(citers[npi]),
                     'best_link_tier': 'core_2026_10_09' if npi in core and held[npi] else (tiers[0] if tiers else ''),
                     'institution_ccns': ';'.join(ccns),
                     'source_id': f'{UA}+{NA}+{UE}+{NE}+{HC}@{SNAPSHOT};authorships+coauthor_edges@{CORE}',
                     'source_release': SNAPSHOT, 'computed_at_utc': stamp})
    return rows


def hold_back(rows):
    """Split links into (tracked, review): tier C, and A or B without institution evidence whose name another
    hematology-oncology clinician shares nationally, go to the local review file only."""
    tracked, review = [], []
    for r in rows:
        if r['confidence_tier'] == 'C':
            review.append({**r, 'review_reason': 'tier_c_name_only'})
        elif int(r['nppes_hemonc_name_matches_national']) > 1 and r['match_rule'] not in INSTITUTION_RULES:
            review.append({**r, 'review_reason': 'oncologist_namesake_nationally_without_institution_evidence'})
        else:
            tracked.append(r)
    return tracked, review


def citation_edges(pulled, corpus):
    """(citing, cited) pairs with both PMIDs in the corpus, from citation pages keyed by cited PMID; no self-pairs.

    `corpus` maps PMID -> {'date': first publication date, 'status': 'held' or 'new'}.
    """
    rows, seen = [], set()
    for cited, (citing, _pages, _hits) in sorted(pulled.items()):
        for c in citing:
            key = (str(c.get('id', '')), cited)
            if c.get('source') != 'MED' or key[0] not in corpus or key[0] == cited or key in seen:
                continue
            seen.add(key)
            rows.append(citation_row(key[0], cited, corpus, c['_record']))
    return sorted(rows, key=lambda r: (r['citing_pmid'], r['cited_pmid']))


def citation_row(citing, cited, corpus, record):
    a, b = corpus[citing], corpus[cited]
    return {'citing_pmid': citing, 'cited_pmid': cited, 'citing_publication_date': a['date'],
            'cited_publication_date': b['date'], 'citing_corpus_status': a['status'], 'cited_corpus_status': b['status'],
            'citing_precedes_cited': int(a['date'] < b['date']),
            'source_id': record['source_id'], 'source_release': 'Europe PMC REST citations, live index',
            'retrieved_at_utc': record['retrieved_at_utc']}


def hcp_citations(edges_rows, links):
    """Directed (citing NPI, cited NPI) totals over corpus citation pairs through included authorship links.

    `links` are included (npi, pmid) rows. The same person on both papers is not a pair. A pair counts as shared when
    the two NPIs are co-authors on the citing or on the cited paper.
    """
    authors = collections.defaultdict(set)
    for r in links:
        authors[r['pmid']].add(r['npi'])
    out = {}
    for e in edges_rows:
        left, right = authors.get(e['citing_pmid'], set()), authors.get(e['cited_pmid'], set())
        for a in left:
            for b in right:
                if a == b:
                    continue
                r = out.setdefault((a, b), {'citing_npi': a, 'cited_npi': b, 'citation_pairs': 0,
                                            'citation_pairs_coauthors_on_either_paper': 0, 'citation_pairs_not_coauthors': 0,
                                            'first_citing_date': e['citing_publication_date'],
                                            'last_citing_date': e['citing_publication_date']})
                shared = b in left or a in right
                r['citation_pairs'] += 1
                r['citation_pairs_coauthors_on_either_paper' if shared else 'citation_pairs_not_coauthors'] += 1
                r['first_citing_date'] = min(r['first_citing_date'], e['citing_publication_date'])
                r['last_citing_date'] = max(r['last_citing_date'], e['citing_publication_date'])
    return [out[k] for k in sorted(out)]


def citation_counts(corpus, fresh, held_rows, pull_status):
    """One row per corpus PMID: the 2026-11-20 cited-by count from the frozen search record, beside the earlier one."""
    rows = []
    for pmid in sorted(corpus):
        p, old = fresh.get(pmid), held_rows.get(pmid)
        now_count = str(p.get('citedByCount', 0)) if p else ''
        before = old['citation_count_epmc'] if old else ''
        rows.append({'pmid': pmid, 'corpus_status': corpus[pmid]['status'],
                     'citation_count_epmc_2026_11_20': now_count,
                     'observed_at_utc': p['_record']['retrieved_at_utc'] if p else '',
                     'observation_status': 'observed' if p else 'not_returned_by_2026_11_20_queries',
                     'citation_count_epmc_2026_10_09': before,
                     'observed_on_2026_10_09': old['citation_observed_on'] if old else '',
                     'citation_count_change': str(int(now_count) - int(before)) if now_count and before else '',
                     'citation_pull_status': pull_status.get(pmid, 'not_pulled'),
                     'source_id': p['_record']['source_id'] if p else old['source_snapshot_id'],
                     'source_release': 'Europe PMC REST search, live index' if p else 'publications.csv of 2026-10-09',
                     'retrieved_at_utc': p['_record']['retrieved_at_utc'] if p else (old['metadata_retrieved_at_utc'] or old['metadata_retrieved_on'])})
    return rows


def pull_status_of(counts, pulled):
    """complete, no_citations (cited-by count zero, no request needed) or incomplete for each PMID with a count."""
    out = {}
    for pmid, n in counts.items():
        if not int(n or 0):
            out[pmid] = 'no_citations'
        elif pmid in pulled:
            rows, _pages, hits = pulled[pmid]
            out[pmid] = 'complete' if len(rows) >= hits else 'incomplete'
        else:
            out[pmid] = 'not_pulled'
    return out


def paper_years(pubs, new):
    """PMID -> (publication year, author count, included) for held and new papers, from the published tables."""
    out = {p: (r['publication_year'], int(r['author_count']), r['analysis_include'] == '1') for p, r in pubs.items()}
    out.update({p: (r['publication_year'], int(r['author_count']), r['analysis_include'] == '1') for p, r in new.items()})
    return out


def included_links(core_links, ua, na):
    """Included (npi, pmid) links: 2026-10-09 core links and tracked links of this snapshot, analysis-included only."""
    return [r for r in core_links if str(r['analysis_include']) == '1'] + [r for r in list(ua) + list(na) if str(r['analysis_include']) == '1']


def pairs_by_year(links, years, core):
    """Rows (npi_a, npi_b, year) with shared papers and fractional weight 1/(authors - 1) summed in that year."""
    by_paper = collections.defaultdict(set)
    for r in links:
        by_paper[r['pmid']].add(r['npi'])
    cells = collections.defaultdict(list)
    for pmid, npis in by_paper.items():
        for a, b in itertools.combinations(sorted(npis), 2):
            cells[a, b, years[pmid][0]].append(pmid)
    return [{'npi_a': a, 'npi_b': b, 'publication_year': y, 'pair_type': pair_type(a, b, core),
             'shared_publications': len(ps), 'fractional_weight': repr(sum(1 / max(years[p][1] - 1, 1) for p in ps)),
             'evidence_pmids': ';'.join(sorted(ps))} for (a, b, y), ps in sorted(cells.items())]


def npi_years(links, years, core, citation_rows):
    """Rows (npi, year): papers, first and last author papers, co-authors, first-time co-authors, citations received."""
    papers = collections.defaultdict(dict)
    authors = collections.defaultdict(set)
    for r in links:
        papers[r['npi']][r['pmid']] = r
        authors[r['pmid']].add(r['npi'])
    cited_by = collections.defaultdict(set)
    for e in citation_rows:
        cited_by[e['cited_pmid']].add(e['citing_pmid'])
    rows = []
    for npi in sorted(papers):
        seen = set()
        by_year = collections.defaultdict(list)
        for pmid, r in papers[npi].items():
            by_year[years[pmid][0]].append(r)
        for year in sorted(by_year):
            mine = by_year[year]
            partners = set().union(*(authors[r['pmid']] for r in mine)) - {npi}
            received = sum(1 for r in mine for c in cited_by[r['pmid']] if npi not in authors.get(c, set()))
            rows.append({'npi': npi, 'publication_year': year, 'is_core': int(npi in core), 'papers': len(mine),
                         'first_author_papers': sum(str(r['author_position']) == '1' for r in mine),
                         'last_author_papers': sum(str(r['author_position']) == str(r['author_count']) and str(r['author_count']) != '1' for r in mine),
                         'distinct_core_coauthors': len(partners & core), 'distinct_noncore_coauthors': len(partners - core),
                         'new_coauthors': len(partners - seen), 'citations_received_from_other_papers': received})
            seen |= partners
    return rows


def network_by_year(pair_rows, links, years, groups):
    """One row per year: included papers, linked NPIs by division group, pairs, first-seen pairs, cross-group pairs."""
    combos = [(a, b) for i, a in enumerate(GROUP_ORDER) for b in GROUP_ORDER[i:]]
    first = {}
    for r in sorted(pair_rows, key=lambda r: r['publication_year']):
        first.setdefault((r['npi_a'], r['npi_b']), r['publication_year'])
    npis = collections.defaultdict(set)
    for r in links:
        npis[years[r['pmid']][0]].add(r['npi'])
    out = []
    for year in sorted({y for y, _n, inc in years.values() if inc}):
        rows = [r for r in pair_rows if r['publication_year'] == year]
        row = {'publication_year': year, 'included_papers': sum(y == year and inc for y, _n, inc in years.values())}
        for g in GROUP_ORDER:
            row[f'linked_npis_{g}'] = sum(groups.get(n) == g for n in npis[year])
        row['pairs'] = len(rows)
        row['new_pairs'] = sum(first[r['npi_a'], r['npi_b']] == year for r in rows)
        kinds = collections.Counter(tuple(sorted((groups.get(r['npi_a']), groups.get(r['npi_b'])), key=GROUP_ORDER.index)) for r in rows)
        for a, b in combos:
            row[f'pairs_{a}_{b}'] = kinds[a, b]
        cross = sum(n for (a, b), n in kinds.items() if a != b)
        row['cross_group_pairs'] = cross
        row['cross_group_share'] = f'{cross / len(rows):.4f}' if rows else ''
        out.append(row)
    return out


def prior_status(pair, old, universe_pairs):
    if pair in old:
        return 'in_coauthor_edges_2026_10_09'
    return 'in_universe_coauthor_edges' if pair in universe_pairs else 'new_pair'


def feature_columns():
    return [
        column('npi', 'National Provider Identifier (NPI); one row for each of the 17,510 NPIs of hcp_divisions.csv of 2026-10-24.', 'Never blank.', 'nppes'),
        column('is_core', '1 for the 500 core HCPs of 2026-10-09, else 0.', 'Never blank.', 'project'),
        column('link_status', 'core, linked (non-core NPI with at least one analysis-included link) or no_included_link (no A or B link found; this is not evidence of no publications).', 'Never blank.', 'project'),
        column('held_publications_2017_2025', 'Analysis-included held CAR-T lymphoma papers (2017-2025) linked to the NPI: core links of 2026-10-09 plus tier A and B universe links.', 'Never blank; 0 means no included link was found.', 'project'),
        column('new_publications_2026', 'Analysis-included links of the NPI on new_publications (all first published in 2026).', 'Never blank; 0 means no included link was found.', 'project'),
        column('first_publication_year', 'Earliest publication year among the counted papers.', 'Blank when no paper is counted.', 'project'),
        column('last_publication_year', 'Latest publication year among the counted papers.', 'Blank when no paper is counted.', 'project'),
        column('first_or_last_author_publications', 'Counted papers on which the NPI is first or last author.', 'Never blank.', 'project'),
        column('distinct_core_coauthors', 'Distinct core HCPs sharing at least one held paper with the NPI (from coauthor_edges.csv of 2026-10-09 and universe_coauthor_edges).', 'Never blank.', 'project'),
        column('distinct_universe_coauthors', 'Distinct non-core universe NPIs sharing at least one held paper with the NPI (from universe_coauthor_edges).', 'Never blank.', 'project'),
        column('new_paper_distinct_core_coauthors', 'Distinct core HCPs sharing at least one new 2026 paper with the NPI (from new_paper_coauthor_edges); counted apart from the held-paper columns.', 'Never blank.', 'project'),
        column('new_paper_distinct_universe_coauthors', 'Distinct non-core universe NPIs sharing at least one new 2026 paper with the NPI (from new_paper_coauthor_edges).', 'Never blank.', 'project'),
        column('corpus_citation_links_received', 'Citing-paper to cited-paper pairs inside the corpus in which another universe NPI, linked to the citing paper, cites a paper linked to this NPI (sum of citation_pairs in hcp_citation_links where this NPI is cited_npi).', 'Never blank; 0 means none found.', 'project'),
        column('corpus_citation_links_received_not_coauthors', 'The part of corpus_citation_links_received in which the two NPIs share neither the citing nor the cited paper.', 'Never blank.', 'project'),
        column('distinct_citing_npis', 'Distinct universe NPIs whose linked papers cite a paper linked to this NPI inside the corpus.', 'Never blank.', 'project'),
        column('best_link_tier', 'Best identity tier among the counted links (A or B), or core_2026_10_09 for core links.', 'Blank when no paper is counted.', 'project'),
        column('institution_ccns', 'Semicolon-separated CMS Certification Numbers (CCNs) of hospitals whose names matched the affiliation on counted tier A links.', 'Blank when no counted link rests on a hospital name.', 'cms'),
        column('source_id', 'Tables the row is computed from.', 'Never blank.', 'project'),
        column('source_release', 'Snapshot date of the computation.', 'Never blank.', 'project'),
        column('computed_at_utc', 'UTC time the row was computed.', 'Never blank.', 'project'),
    ]


def pair_type(a, b, core):
    if a in core and b in core:
        return 'core_core'
    return 'core_universe' if (a in core) != (b in core) else 'universe_universe'


# ---------------------------------------------------------------- inputs

def processed(snapshot, name):
    return ROOT / 'Data' / 'processed' / snapshot / name


def multi_site(entities):
    """Distinctive token set -> True when two or more distinct hospitals or groups have names containing all its words.

    `entities` maps an entity id (CCN or group PAC ID) to the distinctive token sets of its names.
    """
    index = collections.defaultdict(set)
    for entity, needs in entities.items():
        for need in needs:
            for t in need:
                index[t].add(entity)
    out = {}
    for needs in entities.values():
        for need in needs:
            if need not in out:
                out[need] = len(set.intersection(*(index[t] for t in need))) > 1
    return out


def places_of(people):
    """NPI -> list of (label, ccn, state, distinctive tokens, multi-site) from hospitals, CAR-T centers and groups."""
    centers = {r['ccn']: r for r in read_csv(processed(UNIVERSE_SNAPSHOT, 'cart_centers.csv'))}
    raw, entities = collections.defaultdict(list), collections.defaultdict(set)
    for r in read_csv(processed(UNIVERSE_SNAPSHOT, 'hcp_hospital_affiliations.csv')):
        names = [r['hospital_name']] + ([centers[r['ccn']]['dba_name']] if r['ccn'] in centers else [])
        for name in names:
            need = distinctive(name)
            if need:
                entities['ccn:' + r['ccn']].add(need[0])
                if r['npi'] in people:
                    raw[r['npi']].append((name, r['ccn'], r['state'], need))
    seen = set()
    for r in read_csv(processed(UNIVERSE_SNAPSHOT, 'hcp_group_memberships.csv')):
        need = distinctive(r['group_legal_name'])
        if need:
            entities['group:' + r['org_pac_id']].add(need[0])
            if r['npi'] in people and (r['npi'], r['org_pac_id']) not in seen:
                seen.add((r['npi'], r['org_pac_id']))
                raw[r['npi']].append((r['group_legal_name'], '', r['state'], need))
    multi = multi_site(entities)
    return {npi: [(*place, multi[place[3][0]]) for place in items] for npi, items in raw.items()}


def cities_of(people):
    centers = {r['ccn']: r for r in read_csv(processed(UNIVERSE_SNAPSHOT, 'cart_centers.csv'))}
    out = {}
    for npi, p in people.items():
        found = [(p['city'], p['state'])] if p['city'] else []
        found += [(centers[c]['city'], centers[c]['state']) for c in p['hospital_ccns'].split(';') if c in centers]
        out[npi] = list(dict.fromkeys(found))
    return out


def slots(corpus):
    """Every author slot of the corpus as a plain dict."""
    for p in corpus:
        authors = p.get('authorList', {}).get('author', [])
        for position, a in enumerate(authors, 1):
            orcid = a.get('authorId', {}).get('value', '') if a.get('authorId', {}).get('type') == 'ORCID' else ''
            yield {'pmid': p['id'], 'position': position, 'author_count': len(authors), 'first': a.get('firstName', ''),
                   'last': a.get('lastName', ''), 'full': a.get('fullName', ''), 'orcid': orcid,
                   'affiliation': author_affiliation(a), 'date': p.get('firstPublicationDate', '')}


def match(corpus, people, places, cities, claimed, core_orcids):
    """Link every unclaimed slot of the corpus to universe NPIs (core included); returns (links, audit, withheld)."""
    by_name = collections.defaultdict(list)
    states = {n: {st for c, st in cities.get(n, [])} | {pl[2] for pl in places.get(n, [])} | {p['state']}
              for n, p in people.items()}
    for npi, p in people.items():
        key = name_key(p['first_name'], p['last_name'])
        if key:
            by_name[key].append(npi)
    audit, links, pending = collections.Counter(), [], []
    for s in slots(corpus):
        audit['slots'] += 1
        if (s['pmid'], s['position']) in claimed:
            audit['claimed_by_core_link_2026_10_09'] += 1
            continue
        key = name_key(s['first'], s['last'])
        candidates = sorted(by_name.get(key, [])) if key else []
        if not candidates:
            audit['no_universe_name' if key else 'initial_only_or_blank_name'] += 1
            continue
        evidence = {}
        if usable(s['affiliation']):
            for n in candidates:
                evidence[n] = {'institution': institution_hit(s['affiliation'], places.get(n, [])),
                               'city': city_hit(s['affiliation'], cities.get(n, []))}
        npi, tier, rule = grade(candidates, evidence, s['affiliation'], states)
        if s['orcid'] in core_orcids and core_orcids[s['orcid']] != npi:
            audit['orcid_belongs_to_other_core_hcp'] += 1
            continue
        if not npi:
            audit[rule] += 1
            pending.append((s, candidates))
            continue
        e = evidence.get(npi, {})
        detail = e.get('institution') or e.get('city') or ('', '')
        links.append(link(s, npi, tier, rule, len(candidates), detail, bool(e.get('institution'))))
    anchors = anchor_orcids(links)
    rescued = []
    for s, candidates in pending:
        owner = anchors.get(s['orcid'])
        if owner in candidates:
            rescued.append(link(s, owner, 'A', 'name_orcid_anchored', len(candidates), (s['orcid'], ''), False))
    upgraded = []
    for r in links:
        owner = anchors.get(r['orcid'])
        if r['orcid'] and owner and owner != r['npi']:
            audit['orcid_anchored_to_other_npi'] += 1
            continue
        if r['confidence_tier'] == 'C' and owner == r['npi']:
            r = {**r, 'confidence_tier': 'A', 'match_rule': 'name_orcid_anchored', 'evidence_detail': r['orcid'],
                 'evidence_ccn': '', 'institution_multi_site': 0}
        upgraded.append(r)
    audit['rescued_by_anchored_orcid'] = len(rescued)
    kept, withheld = withhold_repeats(upgraded + rescued)
    audit['withheld_same_npi_twice_in_paper'] = len(withheld)
    return kept, audit, withheld


def link(s, npi, tier, rule, candidates, detail, institution):
    return {'npi': npi, 'pmid': s['pmid'], 'author_position': s['position'], 'author_count': s['author_count'],
            'author_name_as_indexed': s['full'] or f"{s['first']} {s['last']}".strip(),
            'first_name_as_indexed': s['first'], 'last_name_as_indexed': s['last'], 'orcid': s['orcid'],
            'affiliation_at_publication': scrub(s['affiliation']), 'publication_date': s['date'],
            'confidence_tier': tier, 'match_rule': rule, 'universe_name_candidates': candidates,
            'evidence_detail': ', '.join(x for x in detail if x) if not institution else detail[0],
            'evidence_ccn': detail[1] if institution else '',
            'institution_multi_site': int(bool(institution and detail[2]))}


# ---------------------------------------------------------------- national namesakes

NPPES = 'https://npiregistry.cms.hhs.gov/api/?'
HEMONC = {'207RH0003X', '207RH0000X', '207RX0202X', '2080P0207X', '207RB0002X'}


def namesake_url(first, last):
    return NPPES + urllib.parse.urlencode({'version': '2.1', 'first_name': first, 'last_name': last, 'limit': 200,
                                           'enumeration_type': 'NPI-1'})


def namesakes(people, npis):
    """NPI -> (active individual NPIs nationally with the same first and last name, of them hematology-oncology)."""
    out, records = {}, []
    for npi in sorted(npis):
        p = people[npi]
        path, record = fetch(namesake_url(p['first_name'].split()[0] if p['first_name'] else '', p['last_name']),
                             MODULE, pause=0.5, suffix='.json')
        records.append(record)
        found = json.loads(path.read_text(encoding='utf-8')).get('results', [])
        found = [r for r in found if r.get('basic', {}).get('status') == 'A'
                 and name_key(r['basic'].get('first_name', ''), r['basic'].get('last_name', '')) ==
                 name_key(p['first_name'], p['last_name'])]
        onc = [r for r in found if any(t.get('code') in HEMONC for t in r.get('taxonomies', []))]
        out[npi] = (len(found), len(onc))
    return out, records


# ---------------------------------------------------------------- citations inside the corpus

CITATIONS = 'https://www.ebi.ac.uk/europepmc/webservices/rest/MED/{pmid}/citations?'


def frozen(url):
    """True when fetch already holds this URL (same key rule as base.fetch), so no request would be made."""
    import hashlib
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    return (RAW / MODULE / (key + '.json')).exists() and (RAW / MODULE / (key + '.meta.json')).exists()


def citation_pages(pmid, expected, cached_only=False):
    """Every page (1,000 per page) of the Europe PMC citations of one MED paper; returns (citing records, pages, hits).

    With `cached_only`, a page not yet frozen is not requested and None is returned (the paper counts as not pulled).
    """
    rows, pages, page = [], [], 1
    while True:
        url = CITATIONS.format(pmid=pmid) + urllib.parse.urlencode({'page': page, 'pageSize': 1000, 'format': 'json'})
        if cached_only and not frozen(url):
            return None
        path, record = fetch(url, MODULE, pause=0.2, suffix='.json')
        data = json.loads(path.read_text(encoding='utf-8'))
        got = data.get('citationList', {}).get('citation', [])
        rows += [{**c, '_record': record} for c in got]
        pages.append({**record, 'hit_count': int(data.get('hitCount', 0))})
        if not got or len(rows) >= int(data.get('hitCount', 0)):
            return rows, pages, int(data.get('hitCount', 0))
        page += 1


def pull_citations(counts, workers=5, cached_only=False):
    """Citations of every corpus paper whose fresh record has a cited-by count above zero, at most five requests a second.

    Returns pmid -> (citing records, pages, hit count); papers with a count of zero need no request. With
    `cached_only` (environment KOL_CITATIONS_CACHED_ONLY=1) only frozen responses are read and unfrozen papers are left
    out, so their status is not_pulled and the pull is reported as incomplete.
    """
    import concurrent.futures
    todo = sorted(p for p, n in counts.items() if n and int(n) > 0)
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        got = dict(zip(todo, pool.map(lambda p: citation_pages(p, counts[p], cached_only), todo)))
    return {p: v for p, v in got.items() if v is not None}


# ---------------------------------------------------------------- new papers

def search(query):
    """All Europe PMC core records of one query, through cursor pages of 1,000, each page frozen by fetch."""
    cursor, rows, pages = '*', [], []
    while True:
        params = {'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': 1000, 'cursorMark': cursor}
        path, record = fetch(EPMC + urllib.parse.urlencode(params), MODULE, pause=1.0, suffix='.json')
        data = json.loads(path.read_text(encoding='utf-8'))
        page = data.get('resultList', {}).get('result', [])
        for p in page:
            p['_record'] = record
        rows += page
        pages.append(record)
        if len(rows) >= int(data['hitCount']) or not page:
            break
        cursor = data['nextCursorMark']
    assert len(rows) == int(data['hitCount']), 'Incomplete Europe PMC pagination'
    return rows, pages


def screened(p):
    """The 2026-10-09 eligibility rule with the window carried to this snapshot: no retraction, correction or erratum."""
    types = [t.lower() for t in p.get('pubTypeList', {}).get('pubType', [])]
    return (WINDOW[0] <= p.get('firstPublicationDate', '') <= WINDOW[1]
            and not re.search(r'^(retraction|correction|erratum|corrigendum)', p.get('title', ''), re.I)
            and not any(t in {'retracted publication', 'retraction of publication', 'published erratum'} for t in types))


def new_papers(records, held):
    """Records not held on 2026-10-09 by PMID, DOI or normalised title; returns (kept, audit)."""
    pmids, dois, titles = held
    audit, kept, seen = collections.Counter(candidates=len(records)), [], set()
    for p in records:
        doi, title = norm_doi(p.get('doi')), norm_title(p.get('title', ''))
        if p.get('source') != 'MED' or not p.get('id'):
            audit['dropped_not_medline'] += 1
        elif p['id'] in pmids:
            audit['dropped_pmid_held'] += 1
        elif doi and doi in dois:
            audit['dropped_doi_held'] += 1
        elif title and title in titles:
            audit['dropped_title_held'] += 1
        elif p['id'] in seen or (doi and ('doi', doi) in seen) or ('title', title) in seen:
            audit['dropped_internal_duplicate'] += 1
        else:
            seen.update({p['id'], ('title', title)} | ({('doi', doi)} if doi else set()))
            kept.append(p)
    audit['kept'] = len(kept)
    return kept, dict(audit)


ACCESS = re.compile(r'\b(access|disparit\w*|referr\w*|rural|socioeconomic|geographic\w*|out.of.pocket|insurance|'
                    r'affordab\w*|travel|financial toxicity|barriers)\b', re.I)


def publication_row(p):
    record, include = p['_record'], screened(p)
    date = p.get('firstPublicationDate', '')
    reason = '' if include else ('outside_window' if not WINDOW[0] <= date <= WINDOW[1] else 'correction_or_retraction')
    return {'pmid': p['id'], 'doi': p.get('doi', ''), 'title': plain(p.get('title', '')),
            'first_publication_date': date, 'publication_year': date[:4],
            'first_index_date': p.get('firstIndexDate', ''),
            'journal': p.get('journalInfo', {}).get('journal', {}).get('title', ''),
            'author_count': len(p.get('authorList', {}).get('author', [])),
            'citation_count_epmc': p.get('citedByCount', 0),
            'publication_types': ';'.join(p.get('pubTypeList', {}).get('pubType', [])),
            'analysis_include': int(include), 'exclusion_reason': reason,
            'in_2017_2025_window': int('2017-01-01' <= date <= '2025-12-31'),
            'access_keyword_flag': int(bool(ACCESS.search(plain(p.get('title', '')) + ' ' + plain(p.get('abstractText', ''))))),
            'source_url': f'https://europepmc.org/article/MED/{p["id"]}', 'pubmed_url': f'https://pubmed.ncbi.nlm.nih.gov/{p["id"]}/',
            'source_id': record['source_id'], 'source_release': 'Europe PMC REST search, live index',
            'retrieved_at_utc': record['retrieved_at_utc'], 'query_family': p['_family']}


# ---------------------------------------------------------------- columns

def link_columns(new=False):
    paper = 'new_publications.csv of this snapshot' if new else 'publications.csv of 2026-10-09'
    cols = [
        column('npi', 'National Provider Identifier (NPI) of the linked clinician; joins hcp_divisions.csv of 2026-10-24.', 'Never blank.', 'nppes'),
        column('pmid', f'PubMed identifier (PMID) of the paper; joins {paper}.', 'Never blank.', 'pubmed'),
        column('author_position', 'Position of the linked author in the Europe PMC author list, 1 = first author.', 'Never blank.', 'europe_pmc'),
        column('author_count', 'Number of authors Europe PMC lists for the paper.', 'Never blank.', 'europe_pmc'),
        column('author_name_as_indexed', 'Author name as Europe PMC indexes it (surname and initials).', 'Blank when Europe PMC gives no full name.', 'europe_pmc'),
        column('first_name_as_indexed', 'Given names or initials as Europe PMC indexes them.', 'Never blank for a linked slot.', 'europe_pmc'),
        column('last_name_as_indexed', 'Surname as Europe PMC indexes it.', 'Never blank for a linked slot.', 'europe_pmc'),
        column('orcid', 'Open Researcher and Contributor ID (ORCID) iD that Europe PMC attaches to this author slot.', 'Blank when the slot has no ORCID iD.', 'europe_pmc'),
        column('affiliation_at_publication', 'Affiliation text of the slot as indexed, with email addresses, street addresses and phone numbers removed.', 'Blank when the slot has no affiliation.', 'europe_pmc'),
        column('publication_date', 'First publication date of the paper in Europe PMC (YYYY-MM-DD).', 'Never blank.', 'europe_pmc'),
        column('confidence_tier', 'Identity grade: A = name plus institution or an anchored ORCID iD; B = name plus city and state, name unique in the universe. Name-only links (tier C) and A or B links without institution evidence whose name another oncologist shares nationally are kept in a local review file and are not in this table.', 'Never blank.', 'project'),
        column('match_rule', 'Rule that made the link: name_institution, name_institution_disambiguated (institution separated same-name candidates), name_orcid_anchored, name_city_state_unique or name_only_unique.', 'Never blank.', 'project'),
        column('universe_name_candidates', 'Number of NPIs of the 17,510-NPI universe (core included) whose first given name and surname match the slot.', 'Never blank.', 'project'),
        column('nppes_name_matches_national', 'Active individual NPIs in the national NPPES (National Plan and Provider Enumeration System) registry with the same first given name and surname as the linked NPI, queried 2026-11-20; 1 means the name is unique nationally, 0 that the query did not return the NPI itself under the universe spelling of its name.', 'Never blank.', 'nppes'),
        column('nppes_hemonc_name_matches_national', 'Of those, NPIs with a hematology, hematology-oncology, medical oncology, bone marrow transplant or pediatric hematology-oncology taxonomy; above 1 means a same-name oncologist exists, inside or outside the universe.', 'Never blank.', 'nppes'),
        column('evidence_detail', 'The evidence matched in the affiliation: the hospital or group name (tier A institution), the city and state (tier B), or the anchoring ORCID iD.', 'Blank for tier C rows, which rest on the name only.', 'project'),
        column('evidence_ccn', 'CMS Certification Number (CCN) of the hospital whose name matched the affiliation.', 'Blank when the match was a group name, a city or an ORCID iD.', 'cms'),
        column('institution_multi_site', '1 when the matched hospital or group words fit two or more hospitals or groups of the universe (a multi-site system); such a match also required the state of the hospital or group to be named in the affiliation. 0 otherwise.', 'Never blank.', 'project'),
        column('analysis_include', '1 when the paper is analysis-included, else 0 (every tracked link is tier A or B).', 'Never blank.', 'project'),
        column('source_id', 'Identifier of the frozen Europe PMC response the slot was read from.', 'Never blank.', 'europe_pmc'),
        column('metadata_retrieved_on', 'Date (UTC) the Europe PMC record of the paper was retrieved.', 'Never blank.', 'europe_pmc'),
        column('retrieved_at_utc', 'UTC time the Europe PMC response was retrieved, or, for held papers whose 2026-10-09 record has only a retrieval day, the UTC time the frozen 2026-10-09 corpus file was written.', 'Never blank.', 'project'),
        column('matched_at_utc', 'UTC time this module made the link.', 'Never blank.', 'project'),
    ]
    if new:
        cols.insert(1, column('is_core', '1 when the NPI is one of the 500 core HCPs of 2026-10-09, else 0.', 'Never blank.', 'project'))
    return cols


def edge_columns():
    return [
        column('npi_a', 'Lower NPI of the unordered pair.', 'Never blank.', 'nppes'),
        column('npi_b', 'Higher NPI of the unordered pair.', 'Never blank.', 'nppes'),
        column('pair_type', 'core_universe when exactly one NPI is a core HCP, universe_universe when neither is; core-core pairs are in coauthor_edges.csv of 2026-10-09.', 'Never blank.', 'project'),
        column('shared_publications', 'Number of analysis-included held papers (2017-2025) on which both NPIs have an analysis-included link.', 'Never blank.', 'project'),
        column('fractional_weight', 'Sum over shared papers of 1 / (author count - 1), as in coauthor_edges.csv.', 'Never blank.', 'project'),
        column('evidence_pmids', 'Semicolon-separated PMIDs of the shared papers, ascending.', 'Never blank.', 'pubmed'),
        column('first_shared_publication_date', 'Earliest first publication date among the shared papers.', 'Never blank.', 'europe_pmc'),
        column('last_shared_publication_date', 'Latest first publication date among the shared papers.', 'Never blank.', 'europe_pmc'),
        column('edge_type', 'Always observed_coauthorship.', 'Never blank.', 'project'),
        column('min_link_tier', 'Lowest identity tier among the links of this snapshot behind the pair (A or B), or core when every link is a 2026-10-09 core link.', 'Never blank.', 'project'),
        column('evidence_metadata_first_observed_on', 'Earliest Europe PMC retrieval date among the shared papers.', 'Never blank.', 'europe_pmc'),
        column('evidence_metadata_last_observed_on', 'Latest Europe PMC retrieval date among the shared papers.', 'Never blank.', 'europe_pmc'),
        column('source_id', 'Source of the links: universe_authorships of 2026-11-20 and authorships.csv of 2026-10-09.', 'Never blank.', 'project'),
        column('computed_at_utc', 'UTC time the pair was computed.', 'Never blank.', 'project'),
    ]


def new_edge_columns():
    cols = []
    for c in edge_columns():
        if c['column'] == 'pair_type':
            c = column('pair_type', 'core_core, core_universe or universe_universe by whether each NPI is a core HCP.', 'Never blank.', 'project')
        elif c['column'] == 'shared_publications':
            c = column('shared_publications', 'Number of analysis-included new papers (new_publications, all 2026) on which both NPIs have an analysis-included link in new_authorships; held papers are not counted here.', 'Never blank.', 'project')
        elif c['column'] == 'source_id':
            c = column('source_id', 'Source of the links: new_authorships and new_publications of 2026-11-20.', 'Never blank.', 'project')
        cols.append(c)
        if c['column'] == 'pair_type':
            cols.append(column('prior_pair_status', 'Whether the pair already has a held-paper row: in_coauthor_edges_2026_10_09, in_universe_coauthor_edges, or new_pair. Counts here are new-paper counts only, so rows add to the earlier row without double counting.', 'Never blank.', 'project'))
    return cols


def citation_edge_columns():
    return [
        column('citing_pmid', 'PubMed identifier (PMID) of the citing paper; in publications.csv of 2026-10-09 or new_publications.', 'Never blank.', 'pubmed'),
        column('cited_pmid', 'PMID of the cited paper; in publications.csv of 2026-10-09 or new_publications; never equal to citing_pmid.', 'Never blank.', 'pubmed'),
        column('citing_publication_date', 'First publication date of the citing paper (YYYY-MM-DD) from the corpus tables.', 'Never blank.', 'europe_pmc'),
        column('cited_publication_date', 'First publication date of the cited paper (YYYY-MM-DD) from the corpus tables.', 'Never blank.', 'europe_pmc'),
        column('citing_corpus_status', 'held (publications.csv of 2026-10-09) or new (new_publications of 2026-11-20).', 'Never blank.', 'project'),
        column('cited_corpus_status', 'held or new, as for the citing paper.', 'Never blank.', 'project'),
        column('citing_precedes_cited', '1 when the citing paper was first published before the cited one (possible with online-first and print dates, or preprint versions); kept and counted, not dropped.', 'Never blank.', 'project'),
        column('source_id', 'Identifier of the frozen Europe PMC citations response listing the citing paper.', 'Never blank.', 'europe_pmc'),
        column('source_release', 'Release of the source: the live Europe PMC index on the retrieval date.', 'Never blank.', 'europe_pmc'),
        column('retrieved_at_utc', 'UTC time the citations response was retrieved.', 'Never blank.', 'europe_pmc'),
    ]


def hcp_citation_columns():
    return [
        column('citing_npi', 'NPI linked (included link) to the citing paper; joins hcp_divisions.csv of 2026-10-24.', 'Never blank.', 'nppes'),
        column('cited_npi', 'NPI linked (included link) to the cited paper; never equal to citing_npi.', 'Never blank.', 'nppes'),
        column('citation_pairs', 'Number of corpus_citation_edges rows whose citing paper is linked to citing_npi and whose cited paper is linked to cited_npi. Links are the 2026-10-09 core links and the tracked tier A and B links of this snapshot, analysis-included only.', 'Never blank.', 'project'),
        column('citation_pairs_coauthors_on_either_paper', 'The part of citation_pairs in which the two NPIs are both linked to the citing paper or both linked to the cited paper.', 'Never blank.', 'project'),
        column('citation_pairs_not_coauthors', 'The part of citation_pairs in which the two NPIs share neither paper.', 'Never blank.', 'project'),
        column('first_citing_date', 'Earliest citing-paper publication date among the pairs.', 'Never blank.', 'europe_pmc'),
        column('last_citing_date', 'Latest citing-paper publication date among the pairs.', 'Never blank.', 'europe_pmc'),
        column('source_id', 'Tables the row is computed from.', 'Never blank.', 'project'),
        column('source_release', 'Snapshot date of the computation.', 'Never blank.', 'project'),
        column('computed_at_utc', 'UTC time the row was computed.', 'Never blank.', 'project'),
    ]


def citation_count_columns():
    return [
        column('pmid', 'PMID of a corpus paper (held on 2026-10-09 or new).', 'Never blank.', 'pubmed'),
        column('corpus_status', 'held or new.', 'Never blank.', 'project'),
        column('citation_count_epmc_2026_11_20', 'Europe PMC cited-by count in the frozen 2026-11-20 search record of the paper (all citing sources, not only the corpus).', 'Blank when neither 2026-11-20 query returned the paper (see observation_status).', 'europe_pmc'),
        column('observed_at_utc', 'UTC time the search response carrying the count was retrieved.', 'Blank when the count is blank.', 'europe_pmc'),
        column('observation_status', 'observed, or not_returned_by_2026_11_20_queries.', 'Never blank.', 'project'),
        column('citation_count_epmc_2026_10_09', 'The earlier count, citation_count_epmc of publications.csv of 2026-10-09 (that file is not edited).', 'Blank for new papers.', 'europe_pmc'),
        column('observed_on_2026_10_09', 'citation_observed_on of publications.csv for the earlier count.', 'Blank for new papers.', 'europe_pmc'),
        column('citation_count_change', 'citation_count_epmc_2026_11_20 minus citation_count_epmc_2026_10_09; may be negative when Europe PMC revises links.', 'Blank when either count is blank.', 'project'),
        column('citation_pull_status', 'State of the citations pull for this paper: complete (every citing record retrieved), no_citations (count zero, no request needed), incomplete, or not_pulled (no 2026-11-20 count).', 'Never blank.', 'project'),
        column('source_id', 'Identifier of the frozen search response, or for a paper not returned now the 2026-10-09 snapshot id.', 'Never blank.', 'europe_pmc'),
        column('source_release', 'Release of the source.', 'Never blank.', 'europe_pmc'),
        column('retrieved_at_utc', 'UTC retrieval time of the source response, or the 2026-10-09 retrieval day when that is all the earlier record holds.', 'Never blank.', 'europe_pmc'),
    ]


def year_pair_columns():
    return [
        column('npi_a', 'Lower NPI of the unordered pair.', 'Never blank.', 'nppes'),
        column('npi_b', 'Higher NPI of the unordered pair.', 'Never blank.', 'nppes'),
        column('publication_year', 'Publication year of the shared papers (2017-2025 held papers, 2026 new papers).', 'Never blank.', 'europe_pmc'),
        column('pair_type', 'core_core, core_universe or universe_universe.', 'Never blank.', 'project'),
        column('shared_publications', 'Included papers of that year on which both NPIs have an included link. Summed over 2017-2025 it equals coauthor_edges.csv of 2026-10-09 (core_core) or universe_coauthor_edges (other pairs); the 2026 row equals new_paper_coauthor_edges.', 'Never blank.', 'project'),
        column('fractional_weight', 'Sum over that year\'s shared papers of 1 / (author count - 1).', 'Never blank.', 'project'),
        column('evidence_pmids', 'Semicolon-separated PMIDs of that year\'s shared papers, ascending.', 'Never blank.', 'pubmed'),
        column('source_id', 'Tables the row is computed from.', 'Never blank.', 'project'),
        column('source_release', 'Snapshot date of the computation.', 'Never blank.', 'project'),
        column('computed_at_utc', 'UTC time the row was computed.', 'Never blank.', 'project'),
    ]


def npi_year_columns():
    return [
        column('npi', 'NPI with at least one included link; joins hcp_divisions.csv of 2026-10-24.', 'Never blank.', 'nppes'),
        column('publication_year', 'Publication year (2017-2025 held papers, 2026 new papers); only years with a paper are listed.', 'Never blank.', 'europe_pmc'),
        column('is_core', '1 for a core HCP of 2026-10-09, else 0.', 'Never blank.', 'project'),
        column('papers', 'Included papers of that year linked to the NPI. For core HCPs the 2017-2025 sum equals publications_2017_2025 of hcps.csv.', 'Never blank.', 'project'),
        column('first_author_papers', 'Of those, papers with the NPI in author position 1.', 'Never blank.', 'project'),
        column('last_author_papers', 'Of those, papers with the NPI in the last author position (single-author papers not counted).', 'Never blank.', 'project'),
        column('distinct_core_coauthors', 'Distinct core HCPs with an included link on any of that year\'s papers.', 'Never blank.', 'project'),
        column('distinct_noncore_coauthors', 'Distinct non-core universe NPIs with an included link on any of that year\'s papers.', 'Never blank.', 'project'),
        column('new_coauthors', 'Co-authors of that year (core or not) not linked with the NPI in any earlier year listed here.', 'Never blank.', 'project'),
        column('citations_received_from_other_papers', 'corpus_citation_edges rows citing one of that year\'s papers whose citing paper has no included link to this NPI.', 'Never blank.', 'project'),
        column('source_id', 'Tables the row is computed from.', 'Never blank.', 'project'),
        column('source_release', 'Snapshot date of the computation.', 'Never blank.', 'project'),
        column('computed_at_utc', 'UTC time the row was computed.', 'Never blank.', 'project'),
    ]


def network_columns():
    cols = [column('publication_year', 'Publication year (2017-2025 held papers, 2026 new papers).', 'Never blank.', 'europe_pmc'),
            column('included_papers', 'Analysis-included corpus papers first published that year.', 'Never blank.', 'project')]
    cols += [column(f'linked_npis_{g}', f'Distinct NPIs of division group {g} (hcp_divisions.csv, label version 1.0.0) with an included link on a paper of that year.', 'Never blank.', 'project') for g in GROUP_ORDER]
    cols += [column('pairs', 'Rows of coauthor_pairs_by_year for that year.', 'Never blank.', 'project'),
             column('new_pairs', 'Of those, pairs with no shared paper in any earlier year.', 'Never blank.', 'project')]
    cols += [column(f'pairs_{a}_{b}', f'Pairs of that year with one NPI in group {a} and the other in group {b}.', 'Never blank.', 'project')
             for i, a in enumerate(GROUP_ORDER) for b in GROUP_ORDER[i:]]
    cols += [column('cross_group_pairs', 'Pairs of that year whose two NPIs are in different division groups.', 'Never blank.', 'project'),
             column('cross_group_share', 'cross_group_pairs / pairs, four decimals.', 'Blank when the year has no pair.', 'project'),
             column('source_id', 'Tables the row is computed from.', 'Never blank.', 'project'),
             column('source_release', 'Snapshot date of the computation.', 'Never blank.', 'project'),
             column('computed_at_utc', 'UTC time the row was computed.', 'Never blank.', 'project')]
    return cols


def publication_columns():
    return [
        column('pmid', 'PubMed identifier (PMID); not in publications.csv of 2026-10-09.', 'Never blank.', 'pubmed'),
        column('doi', 'Digital Object Identifier (DOI) as indexed.', 'Blank when Europe PMC gives no DOI.', 'europe_pmc'),
        column('title', 'Article title as indexed, markup removed.', 'Never blank.', 'europe_pmc'),
        column('first_publication_date', 'First publication date in Europe PMC (YYYY-MM-DD).', 'Never blank.', 'europe_pmc'),
        column('publication_year', 'Year of first_publication_date.', 'Never blank.', 'europe_pmc'),
        column('first_index_date', 'Date Europe PMC first indexed the record (YYYY-MM-DD).', 'Blank when Europe PMC gives none.', 'europe_pmc'),
        column('journal', 'Journal title as indexed.', 'Blank when Europe PMC gives none.', 'europe_pmc'),
        column('author_count', 'Number of authors Europe PMC lists.', 'Never blank; 0 means no author list.', 'europe_pmc'),
        column('citation_count_epmc', 'Europe PMC cited-by count on the retrieval date.', 'Never blank.', 'europe_pmc'),
        column('publication_types', 'Semicolon-separated publication types as indexed.', 'Blank when none are indexed.', 'europe_pmc'),
        column('analysis_include', '1 when first published 2017-01-01 to 2026-11-20 and not a retraction, correction or erratum (the 2026-10-09 rule with the window carried to this snapshot).', 'Never blank.', 'project'),
        column('exclusion_reason', 'Why analysis_include is 0: outside_window or correction_or_retraction.', 'Blank when analysis_include is 1.', 'project'),
        column('in_2017_2025_window', '1 when first published 2017-2025 (the 2026-10-09 window), else 0.', 'Never blank.', 'project'),
        column('access_keyword_flag', '1 when title or abstract contains an access keyword (the 2026-10-09 list: access, disparity, referral, rural, insurance, travel and others).', 'Never blank.', 'project'),
        column('source_url', 'Europe PMC article page.', 'Never blank.', 'europe_pmc'),
        column('pubmed_url', 'PubMed article page.', 'Never blank.', 'pubmed'),
        column('source_id', 'Identifier of the frozen Europe PMC response page holding the record.', 'Never blank.', 'europe_pmc'),
        column('source_release', 'Release of the source: the live Europe PMC index on the retrieval date.', 'Never blank.', 'europe_pmc'),
        column('retrieved_at_utc', 'UTC time the Europe PMC response page was retrieved.', 'Never blank.', 'europe_pmc'),
        column('query_family', 'Query that returned the record, named as in publications.csv: original_lymphoma_in_title (lymphoma, DLBCL or LBCL in title, CAR-T term in title or abstract) or supplement_cart_in_title_lymphoma_in_title_or_abstract; a record returned by both keeps the first.', 'Never blank.', 'project'),
    ]


# ---------------------------------------------------------------- build

def held_keys():
    pubs = read_csv(processed(CORE, 'publications.csv'))
    return ({r['pmid'] for r in pubs}, {norm_doi(r['doi']) for r in pubs if r['doi']},
            {norm_title(r['title']) for r in pubs if norm_title(r['title'])})


def core_state():
    core_links = read_csv(processed(CORE, 'authorships.csv')) + read_csv(processed(CORE, 'withheld_authorships.csv'))
    claimed = {(r['pmid'], int(r['author_position'])) for r in core_links}
    owners = collections.defaultdict(set)
    for r in core_links:
        if r['orcid']:
            owners[r['orcid']].add(r['npi'])
    return core_links, claimed, {o: next(iter(n)) for o, n in owners.items() if len(n) == 1}


def finish(rows, pubs, stamp, read_at, include_of):
    out = []
    for r in rows:
        p = pubs[r['pmid']]
        out.append({**r, 'analysis_include': int(r['confidence_tier'] in ('A', 'B') and include_of(r['pmid'])),
                    'source_id': p['source_id'], 'metadata_retrieved_on': p['retrieved_on'],
                    'retrieved_at_utc': p['retrieved_at_utc'] or read_at, 'matched_at_utc': stamp})
    return sorted(out, key=lambda r: (r['npi'], r['pmid']))


def accuracy(replay, core_links):
    """Replay the rule with no slot reserved and compare it with the 2026-10-09 core links, slot by slot, per tier.

    The 2026-10-09 links used NPPES middle names and city evidence; a replayed link on a slot they hold either names
    the same NPI (agree) or another one (conflict, a wrong-person link). Recall is the share of core slots recovered.
    """
    owner = {(r['pmid'], int(r['author_position'])): r['npi'] for r in core_links}
    out = {}
    for tier in sorted(TIERS):
        made = [r for r in replay if r['confidence_tier'] == tier and (r['pmid'], r['author_position']) in owner]
        agree = sum(owner[r['pmid'], r['author_position']] == r['npi'] for r in made)
        out[tier] = {'links_on_core_slots': len(made), 'agree': agree, 'conflict': len(made) - agree}
    found = {(r['pmid'], r['author_position']) for r in replay if r['confidence_tier'] in 'AB'}
    out['core_slots'] = len(owner)
    out['core_slots_recovered_tier_a_or_b'] = len(found & set(owner))
    return out


def gather():
    """Both 2026-10-09 queries over 2017-01-01 to the snapshot date; records keyed by PMID with their query family.

    A PMID returned by both queries keeps the first family, as the 2026-10-09 corpus kept the original observation.
    """
    merged, pages, hits = {}, [], {}
    for family, query in FAMILIES.items():
        records, got = search(f'{query} AND FIRST_PDATE:[{WINDOW[0]} TO {WINDOW[1]}]')
        pages += got
        hits[family] = {p['id'] for p in records}
        for p in records:
            if p['id'] not in merged:
                merged[p['id']] = {**p, '_family': family}
    return list(merged.values()), pages, hits


def query_coverage(hits, pub_rows):
    """How the two queries rerun on 2026-11-20 reproduce the held papers of each family."""
    out = {}
    union = set().union(*hits.values())
    for family in FAMILIES:
        held = {p for p, r in pub_rows.items() if r['query_family'] == family}
        out[family] = {'held_papers': len(held), 'returned_now': len(hits[family]),
                       'held_returned_by_own_query': len(held & hits[family]),
                       'held_returned_by_either_query': len(held & union)}
    out['held_not_returned_by_either_query'] = len(set(pub_rows) - union)
    return out


def build():
    stamp = now()
    written = datetime.fromtimestamp(CORPUS.stat().st_mtime, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    corpus_record = log_local(MODULE, 'corpus_combined_2026_10_09', CORPUS, obtained=written, release='2026-10-08/09',
                              note='Frozen Europe PMC corpus of 2026-10-09 (3,363 records, author lists and affiliations).')
    people = {r['npi']: r for r in read_csv(UNIVERSE)}
    core = {n for n, r in people.items() if r['is_core'] == '1'}
    places, cities = places_of(people), cities_of(people)
    core_links, claimed, core_orcids = core_state()
    corpus = load(CORPUS)
    pub_rows = {r['pmid']: r for r in read_csv(processed(CORE, 'publications.csv'))}
    held = {p: {'source_id': r['source_snapshot_id'], 'retrieved_on': r['metadata_retrieved_on'],
                'retrieved_at_utc': r['metadata_retrieved_at_utc']} for p, r in pub_rows.items()}
    links, audit, withheld = match(corpus, people, places, cities, claimed, core_orcids)
    replay, _, _ = match(corpus, people, places, cities, set(), {})
    check_core = accuracy(replay, core_links)
    noncore = [r for r in links if r['npi'] not in core]
    ua_rows = finish(noncore, held, stamp, corpus_record['retrieved_at_utc'], lambda p: pub_rows[p]['analysis_include'] == '1')
    already = existing_keys([processed(CORE, 'authorships.csv')], ['npi', 'pmid'])
    ua_rows, ua_dropped = new_rows(ua_rows, ['npi', 'pmid'], already)

    # papers not yet held, from both 2026-10-09 queries
    records, pages, hits = gather()
    coverage = query_coverage(hits, pub_rows)
    kept, paper_audit = new_papers(records, held_keys())
    np_rows = sorted((publication_row(p) for p in kept), key=lambda r: r['pmid'])
    np_already = existing_keys([processed(CORE, 'publications.csv')], ['pmid'])
    np_rows, np_dropped = new_rows(np_rows, ['pmid'], np_already)
    new_pubs = {p['id']: {'source_id': p['_record']['source_id'], 'retrieved_on': p['_record']['retrieved_at_utc'][:10],
                          'retrieved_at_utc': p['_record']['retrieved_at_utc']} for p in kept}
    include_new = {r['pmid']: r['analysis_include'] == 1 for r in np_rows}
    new_links, new_audit, new_withheld = match(kept, people, places, cities, set(), core_orcids)
    na_rows = finish(new_links, new_pubs, stamp, stamp, lambda p: include_new[p])
    for r in na_rows:
        r['is_core'] = int(r['npi'] in core)
    na_rows, na_dropped = new_rows(na_rows, ['npi', 'pmid'], already)

    # national namesakes, then weak links to the local review file only
    counts_ns, ns_records = namesakes(people, {r['npi'] for r in ua_rows + na_rows})
    for r in ua_rows + na_rows:
        r['nppes_name_matches_national'], r['nppes_hemonc_name_matches_national'] = counts_ns[r['npi']]
    ua_rows, ua_review = hold_back(ua_rows)
    na_rows, na_review = hold_back(na_rows)
    review = [{**r, 'table': UA} for r in ua_review] + [{**r, 'table': NA} for r in na_review]
    write_csv_local(REVIEW, review)

    # co-author pairs on held papers: core links of 2026-10-09 plus tracked included links of this snapshot
    in_use = [r for r in core_links_tracked() if r['analysis_include'] == '1'] + [r for r in ua_rows if r['analysis_include'] == 1]
    counts = {p: r['author_count'] for p, r in pub_rows.items()}
    dates = {p: r['first_publication_date'] for p, r in pub_rows.items()}
    observed = {p: r['metadata_retrieved_on'] for p, r in pub_rows.items()}
    edge_rows = edges(in_use, counts, dates, observed, core)
    tier_of = {(r['npi'], r['pmid']): r['confidence_tier'] for r in ua_rows + na_rows}
    old_pairs = existing_keys([processed(CORE, 'coauthor_edges.csv')], ['hcp_id_a', 'hcp_id_b'])
    old_pairs = {tuple(sorted(k.removeprefix('NPI') for k in pair)) for pair in old_pairs}
    for e in edge_rows:
        e.update(edge_extra(e, tier_of, f'{UA}@{SNAPSHOT};authorships@{CORE}', stamp))
    edge_candidates = len(edge_rows)
    edge_rows, edge_dropped = new_rows(edge_rows, ['npi_a', 'npi_b'], old_pairs)

    # co-author pairs on the new papers only, core-core included, with the status of the pair in earlier tables
    new_meta = {r['pmid']: r for r in np_rows}
    ne_rows = edges([r for r in na_rows if r['analysis_include'] == 1], {p: r['author_count'] for p, r in new_meta.items()},
                    {p: r['first_publication_date'] for p, r in new_meta.items()},
                    {p: r['retrieved_at_utc'][:10] for p, r in new_meta.items()}, core, core_pairs=True)
    universe_pairs = {(e['npi_a'], e['npi_b']) for e in edge_rows}
    for e in ne_rows:
        e.update(edge_extra(e, tier_of, f'{NA}+{NP}@{SNAPSHOT}', stamp))
        e['prior_pair_status'] = prior_status((e['npi_a'], e['npi_b']), old_pairs, universe_pairs)
    ne_rows, ne_dropped = new_rows(ne_rows, ['npi_a', 'npi_b'], set())

    # citations inside the corpus (held and new papers), and refreshed citation counts
    fresh = {p['id']: p for p in records}
    corpus = {p: {'date': r['first_publication_date'], 'status': 'held'} for p, r in pub_rows.items()}
    corpus.update({r['pmid']: {'date': r['first_publication_date'], 'status': 'new'} for r in np_rows})
    counts_now = {p: fresh[p].get('citedByCount', 0) for p in corpus if p in fresh}
    pulled = pull_citations(counts_now, cached_only=os.environ.get('KOL_CITATIONS_CACHED_ONLY') == '1')
    status = pull_status_of(counts_now, pulled)
    ce_rows = citation_edges(pulled, corpus)
    ce_candidates = sum(c.get('source') == 'MED' and str(c.get('id')) in corpus and str(c.get('id')) != cited
                        for cited, (rows, _p, _h) in pulled.items() for c in rows)
    included = ([r for r in core_links_tracked() if r['analysis_include'] == '1']
                + [r for r in ua_rows + na_rows if r['analysis_include'] == 1])
    hc_rows = [{**r, 'source_id': f'{CE}+{UA}+{NA}@{SNAPSHOT};authorships@{CORE}', 'source_release': SNAPSHOT,
                'computed_at_utc': stamp} for r in hcp_citations(ce_rows, included)]
    cc_rows = citation_counts(corpus, fresh, pub_rows, status)
    citation_requests = [page for _rows, pages, _hits in pulled.values() for page in pages]

    ua_rows, na_rows = keep_stamps(ua_rows, UA), keep_stamps(na_rows, NA)
    prov = ['source_id', 'metadata_retrieved_on', 'retrieved_at_utc']
    edge_prov = ['source_id', 'evidence_metadata_last_observed_on', 'computed_at_utc']
    edge_rows = keep_stamps(edge_rows, UE, 'computed_at_utc', ('npi_a', 'npi_b'))
    ne_rows = keep_stamps(ne_rows, NE, 'computed_at_utc', ('npi_a', 'npi_b'))
    hc_rows = keep_stamps(hc_rows, HC, 'computed_at_utc', ('citing_npi', 'cited_npi'))
    tables = [guarded(UA, ua_rows, link_columns(), ['npi', 'pmid'], prov),
              guarded(UE, edge_rows, edge_columns(), ['npi_a', 'npi_b'], edge_prov),
              guarded(NP, np_rows, publication_columns(), ['pmid'], ['source_id', 'source_release', 'retrieved_at_utc']),
              guarded(NA, na_rows, link_columns(new=True), ['npi', 'pmid'], prov),
              guarded(NE, ne_rows, new_edge_columns(), ['npi_a', 'npi_b'], edge_prov),
              guarded(CE, ce_rows, citation_edge_columns(), ['citing_pmid', 'cited_pmid'], ['source_id', 'source_release', 'retrieved_at_utc']),
              guarded(HC, hc_rows, hcp_citation_columns(), ['citing_npi', 'cited_npi'], ['source_id', 'source_release', 'computed_at_utc']),
              guarded(CC, cc_rows, citation_count_columns(), ['pmid'], ['source_id', 'source_release', 'retrieved_at_utc'])]
    ft_rows = features(people, core_links_tracked(), ua_rows, na_rows, edge_rows,
                       read_csv(processed(CORE, 'coauthor_edges.csv')), ne_rows, hc_rows, stamp)
    ft_rows = keep_stamps(ft_rows, FT, 'computed_at_utc', ('npi',))
    tables.append(guarded(FT, ft_rows, feature_columns(), ['npi'], ['source_id', 'source_release', 'computed_at_utc']))

    # time axis of co-authorship, from the published link tables only
    published_now = {t: read_csv(OUT / f'{t}.csv') for t in PINNED}
    years = paper_years(pub_rows, {r['pmid']: r for r in published_now[NP]})
    links_now = included_links(core_links_tracked(), published_now[UA], published_now[NA])
    groups = {n: DIVISION_GROUPS.get(p['division']) for n, p in people.items()}
    derived = f'{UA}+{NA}+{NP}+{CE}@{SNAPSHOT};authorships+publications@{CORE}'
    extra = {'source_id': derived, 'source_release': SNAPSHOT, 'computed_at_utc': stamp}
    py_rows = [{**r, **extra} for r in pairs_by_year(links_now, years, core)]
    hy_rows = [{**r, **extra} for r in npi_years(links_now, years, core, published_now[CE])]
    ny_rows = [{**r, **extra} for r in network_by_year(py_rows, links_now, years, groups)]
    py_rows = keep_stamps(py_rows, PY, 'computed_at_utc', ('npi_a', 'npi_b', 'publication_year'))
    hy_rows = keep_stamps(hy_rows, HY, 'computed_at_utc', ('npi', 'publication_year'))
    ny_rows = keep_stamps(ny_rows, NY, 'computed_at_utc', ('publication_year',))
    stamps = ['source_id', 'source_release', 'computed_at_utc']
    tables += [publish(PY, py_rows, year_pair_columns(), ['npi_a', 'npi_b', 'publication_year'], stamps),
               publish(HY, hy_rows, npi_year_columns(), ['npi', 'publication_year'], stamps),
               publish(NY, ny_rows, network_columns(), ['publication_year'], stamps)]
    dump(INTERIM / MODULE / 'withheld_links.json', {'held_papers': withheld, 'new_papers': new_withheld})
    errors = {k: v for k, v in check(OUT.parent).items() if v}
    assert not errors, f'Checks failed: {errors}'
    published = {t: read_csv(OUT / f'{t}.csv') for t in TABLES}
    numbers = headline(published, people)
    treater = treater_crosscheck(published[UA], people)
    retrieved = [p['retrieved_at_utc'] for p in pages]
    held_back = collections.Counter((r['table'], r['review_reason']) for r in review)
    write_module_metadata(MODULE, {
        'tables': tables, 'headline': numbers,
        'sources': [
            {'name': 'Europe PMC REST search (both CAR-T lymphoma queries of 2026-10-09)', 'publisher': 'EMBL-EBI',
             'url': EPMC.rstrip('?'), 'release_or_version': 'live index', 'retrieved_from_utc': min(retrieved),
             'retrieved_to_utc': max(retrieved), 'how_obtained': 'cursor-paged JSON, pageSize 1000, frozen by fetch',
             'requests': len(pages), 'terms_class': 'open_metadata', 'queries': FAMILIES},
            {'name': 'Europe PMC corpus of 2026-10-09 (frozen, local)', 'publisher': 'EMBL-EBI',
             'url': '', 'release_or_version': '2026-10-08/09', 'retrieved_from_utc': corpus_record['retrieved_at_utc'],
             'retrieved_to_utc': corpus_record['retrieved_at_utc'], 'how_obtained': 'local file, logged with SHA-256',
             'requests': 0, 'terms_class': 'open_metadata'},
            {'name': 'hcp_divisions, hcp_hospital_affiliations, hcp_group_memberships, cart_centers (2026-10-24)',
             'publisher': 'this project (CMS sources)', 'url': '', 'release_or_version': '2026-10-24',
             'retrieved_from_utc': stamp, 'retrieved_to_utc': stamp, 'how_obtained': 'tracked processed tables',
             'requests': 0, 'terms_class': 'public_domain'},
            {'name': 'NPPES NPI Registry API (name queries for linked NPIs)', 'publisher': 'CMS',
             'url': NPPES.rstrip('?'), 'release_or_version': 'API version 2.1, live registry',
             'retrieved_from_utc': min(r['retrieved_at_utc'] for r in ns_records),
             'retrieved_to_utc': max(r['retrieved_at_utc'] for r in ns_records),
             'how_obtained': 'one first-name and last-name query per linked NPI, limit 200, frozen by fetch',
             'requests': len(ns_records), 'terms_class': 'public_domain'},
            {'name': 'Europe PMC REST citations (MED/{pmid}/citations, pageSize 1000)', 'publisher': 'EMBL-EBI',
             'url': CITATIONS.split('MED')[0].rstrip('/'), 'release_or_version': 'live index',
             'retrieved_from_utc': min(r['retrieved_at_utc'] for r in citation_requests),
             'retrieved_to_utc': max(r['retrieved_at_utc'] for r in citation_requests),
             'how_obtained': 'one paged request series per corpus paper with a cited-by count above zero, 5 per second, frozen by fetch',
             'requests': len(citation_requests), 'terms_class': 'open_metadata'}],
        'dedup': {UA: {'candidate_rows': len(ua_rows) + len(ua_review) + ua_dropped, 'dropped_already_published': ua_dropped,
                       'dropped_internal_duplicates': 0, 'held_back_to_local_review': len(ua_review),
                       'withheld_same_npi_twice_in_paper': len(withheld)},
                  UE: {'candidate_rows': edge_candidates, 'dropped_already_published': edge_dropped,
                       'dropped_internal_duplicates': 0},
                  NP: {**paper_audit, 'dropped_already_published_by_publish_step': np_dropped},
                  NA: {'candidate_rows': len(na_rows) + len(na_review) + na_dropped, 'dropped_already_published': na_dropped,
                       'dropped_internal_duplicates': 0, 'held_back_to_local_review': len(na_review),
                       'withheld_same_npi_twice_in_paper': len(new_withheld)},
                  NE: {'candidate_rows': len(ne_rows) + ne_dropped, 'dropped_already_published': 0,
                       'dropped_internal_duplicates': ne_dropped,
                       'note': 'Pairs also in coauthor_edges.csv or universe_coauthor_edges are kept, because this table '
                               'counts only the new papers; prior_pair_status says which, so the tables add without '
                               'double counting.'},
                  CE: {'candidate_rows': ce_candidates, 'dropped_already_published': 0,
                       'dropped_internal_duplicates': ce_candidates - len(ce_rows),
                       'note': 'Candidates are citing records of source MED with both PMIDs in the corpus and not the same paper.'},
                  HC: {'candidate_rows': len(hc_rows), 'dropped_already_published': 0, 'dropped_internal_duplicates': 0},
                  CC: {'candidate_rows': len(cc_rows), 'dropped_already_published': 0, 'dropped_internal_duplicates': 0,
                       'note': 'A dated observation beside publications.csv, which is not edited.'},
                  FT: {'candidate_rows': len(ft_rows), 'dropped_already_published': 0, 'dropped_internal_duplicates': 0}},
        'held_back_links': {'file': str(REVIEW.relative_to(ROOT)), 'rows': len(review),
                            'by_table_and_reason': {f'{t}:{why}': n for (t, why), n in sorted(held_back.items())}},
        'citation_pull': {'status': 'complete' if all(v in ('complete', 'no_citations') for v in status.values()) and len(status) == len(counts_now) else 'incomplete',
                          'coverage_rule': 'corpus_citation_edges holds every in-corpus citing paper of each cited paper whose '
                                           'citation_pull_status is complete; cited papers with not_pulled or incomplete '
                                           'contribute no edge.',
                          'papers_in_corpus': len(corpus), 'papers_with_2026_11_20_count': len(counts_now),
                          'papers_pulled': len(pulled), 'papers_no_citations': sum(v == 'no_citations' for v in status.values()),
                          'papers_incomplete': sum(v == 'incomplete' for v in status.values()),
                          'papers_not_pulled': sum(v == 'not_pulled' for v in status.values()) + len(corpus) - len(status),
                          'pull_order': 'ascending PMID, so a stopped pull covers the lowest (mostly earliest) PMIDs first',
                          'citing_records_retrieved': sum(len(v[0]) for v in pulled.values()),
                          'requests': len(citation_requests)},
        'query_recovery': {'families': FAMILIES, 'coverage_2026_11_20': coverage,
                           'original_query_source': 'Data/legacy/cohort_245/scripts/collect.py (QUERY) and '
                                                    'Data/legacy/cohort_245/README.md, section Publication query'},
        'match_audit_held_papers': dict(audit), 'match_audit_new_papers': dict(new_audit),
        'identity_error_estimate': {'rule_replayed_on_core_npis': check_core, 'treater_crosscheck': treater,
                                    'national_namesakes_by_tier_tracked': namesake_summary(published[UA])},
        'limitations': LIMITATIONS, 'defects_open': DEFECTS_OPEN,
        'scaffold_helpers_added_here': ['norm_doi/norm_title de-duplication keys', 'search() cursor pager over fetch',
                                        'scrub() contact-detail removal', 'namesakes() NPPES name query',
                                        'write_csv_local() for untracked review files'],
    })
    print('PUBLICATIONS BUILT', len(ua_rows), 'UNIVERSE LINKS', len(edge_rows), 'PAIRS', len(np_rows), 'NEW PAPERS',
          len(na_rows), 'NEW LINKS', len(ne_rows), 'NEW-PAPER PAIRS', len(review), 'LINKS HELD BACK', flush=True)


def guarded(table, rows, columns, key, provenance):
    """Publish a table verified on 2026-11-20 only if the new file is byte-identical to the one already published."""
    path = OUT / f'{table}.csv'
    if table in PINNED and path.exists():
        from ..common import write_csv
        staged = INTERIM / MODULE / 'staging' / f'{table}.csv'
        names = [c['column'] for c in columns if not c['restricted']]
        write_csv(staged, [{n: r[n] for n in names} for r in rows], names)
        assert staged.read_bytes() == path.read_bytes(), f'{table} would change; it is pinned and must stay byte-identical'
    return publish(table, rows, columns, key, provenance)


def keep_stamps(rows, table, field='matched_at_utc', key=('npi', 'pmid')):
    """Keep the earlier match time of a row already published under the same key, so an unchanged rebuild leaves
    the file unchanged (other modules pin these tables)."""
    path = OUT / f'{table}.csv'
    if not path.exists():
        return rows
    before = {tuple(r[k] for k in key): r[field] for r in read_csv(path)}
    return [{**r, field: before.get(tuple(str(r[k]) for k in key), r[field])} for r in rows]


def core_links_tracked():
    return read_csv(processed(CORE, 'authorships.csv'))


def write_csv_local(path, rows):
    """A local (git-ignored) review file under Data/interim; never a tracked table."""
    assert INTERIM in path.parents, 'Review files stay under Data/interim'
    from ..common import write_csv
    write_csv(path, rows, list(dict.fromkeys(k for r in rows for k in r)) or ['npi', 'pmid'])


def edge_extra(e, tier_of, source, stamp):
    found = [tier_of.get((n, p)) for n in (e['npi_a'], e['npi_b']) for p in e['evidence_pmids'].split(';')]
    return {'edge_type': 'observed_coauthorship', 'min_link_tier': max([t for t in found if t] or ['core']),
            'source_id': source, 'computed_at_utc': stamp}


def treater_crosscheck(rows, people):
    """Share of linked NPIs with an independent CAR-T research or treater signal, by tier, against the universe."""
    path = processed('2026-11-19', 'npi_cart_treater_evidence.csv')
    ev = {r['npi']: r for r in read_csv(path)}
    signal = lambda n: any(ev.get(n, {}).get(k) == '1' for k in ('ev_op_cart_research_pi', 'ev_ctgov_investigator', 'ev_bmt_roster', 'ev_fact_director'))
    out = {}
    for tier in sorted(TIERS):
        npis = {r['npi'] for r in rows if r['confidence_tier'] == tier}
        out[tier] = {'npis': len(npis), 'with_signal': sum(map(signal, npis))}
    noncore = [n for n, p in people.items() if p['is_core'] != '1']
    out['universe_noncore'] = {'npis': len(noncore), 'with_signal': sum(map(signal, noncore))}
    return out


def namesake_summary(rows):
    out = {}
    for tier in sorted(TIERS):
        mine = [r for r in rows if r['confidence_tier'] == tier]
        out[tier] = {'links': len(mine), 'name_unique_nationally': sum(r['nppes_name_matches_national'] == '1' for r in mine),
                     'other_same_name_oncologist': sum(int(r['nppes_hemonc_name_matches_national'] or 0) > 1 for r in mine)}
    return out


def headline(published, people):
    """Flat name -> integer totals recomputed from the published tables; check() compares them with HEADLINE."""
    ua, ue, np_, na, ne, ce, hc, cc, ft = (published[t] for t in PINNED)
    py, hy, ny = (published.get(t, []) for t in (PY, HY, NY))
    included = [r for r in ua if r['analysis_include'] == '1']
    out = {f'rows_{t}': len(published[t]) for t in TABLES}
    out.update({f'universe_links_tier_{k}': v for k, v in collections.Counter(r['confidence_tier'] for r in ua).items()})
    out.update({'universe_links_analysis_included': len(included),
                'universe_npis_linked': len({r['npi'] for r in ua}),
                'universe_npis_linked_analysis_included': len({r['npi'] for r in included}),
                'universe_links_institution_multi_site': sum(r['institution_multi_site'] == '1' for r in ua)})
    out.update({f'universe_npis_included_{k}': v for k, v in
                collections.Counter(people[n]['division'] for n in {r['npi'] for r in included} if n in people).items()})
    out.update({f'pairs_{k}': v for k, v in collections.Counter(r['pair_type'] for r in ue).items()})
    out['pairs_shared_publications_total'] = sum(int(r['shared_publications']) for r in ue)
    out['new_publications_analysis_included'] = sum(r['analysis_include'] == '1' for r in np_)
    out.update({f'new_publications_{k}': v for k, v in collections.Counter(r['query_family'] for r in np_).items()})
    out.update({f'new_publications_year_{k}': v for k, v in collections.Counter(r['publication_year'] for r in np_).items()})
    out.update({'new_links_core': sum(r['is_core'] == '1' for r in na),
                'new_links_analysis_included': sum(r['analysis_include'] == '1' for r in na)})
    out.update({f'new_links_tier_{k}': v for k, v in collections.Counter(r['confidence_tier'] for r in na).items()})
    out.update({f'new_paper_pairs_{k}': v for k, v in collections.Counter(r['prior_pair_status'] for r in ne).items()})
    out.update({f'new_paper_pairs_type_{k}': v for k, v in collections.Counter(r['pair_type'] for r in ne).items()})
    out.update({f'features_{k}': v for k, v in collections.Counter(r['link_status'] for r in ft).items()})
    out['citation_edges_citing_precedes_cited'] = sum(r['citing_precedes_cited'] == '1' for r in ce)
    out.update({f'citation_edges_{a}_cites_{b}': n for (a, b), n in
                collections.Counter((r['citing_corpus_status'], r['cited_corpus_status']) for r in ce).items()})
    out['hcp_citation_pairs_total'] = sum(int(r['citation_pairs']) for r in hc)
    out['hcp_citation_pairs_not_coauthors_total'] = sum(int(r['citation_pairs_not_coauthors']) for r in hc)
    out.update({f'citation_counts_pull_{k}': v for k, v in collections.Counter(r['citation_pull_status'] for r in cc).items()})
    out['citation_counts_observed'] = sum(r['observation_status'] == 'observed' for r in cc)
    out['citation_counts_change_total'] = sum(int(r['citation_count_change']) for r in cc if r['citation_count_change'])
    out['features_citation_links_received_total'] = sum(int(r['corpus_citation_links_received']) for r in ft)
    out['features_held_publications_total'] = sum(int(r['held_publications_2017_2025']) for r in ft)
    out.update({f'year_pairs_{k}': v for k, v in collections.Counter(r['pair_type'] for r in py).items()})
    out['year_pairs_shared_publications_total'] = sum(int(r['shared_publications']) for r in py)
    out['npi_years_papers_total'] = sum(int(r['papers']) for r in hy)
    out['npi_years_npis'] = len({r['npi'] for r in hy})
    out['npi_years_new_coauthors_total'] = sum(int(r['new_coauthors']) for r in hy)
    out['npi_years_citations_received_total'] = sum(int(r['citations_received_from_other_papers']) for r in hy)
    out['network_new_pairs_total'] = sum(int(r['new_pairs']) for r in ny)
    out['network_cross_group_pairs_total'] = sum(int(r['cross_group_pairs']) for r in ny)
    out['features_new_publications_total'] = sum(int(r['new_publications_2026']) for r in ft)
    return dict(sorted(out.items()))


# Headline totals of this snapshot, recomputed by check() from the published rows (fixed after the 2026-11-20 build).
HEADLINE = {
    'citation_counts_change_total': 2467, 'citation_counts_observed': 4061, 'citation_counts_pull_complete': 3171,
    'citation_counts_pull_no_citations': 890, 'citation_counts_pull_not_pulled': 1,
    'citation_edges_citing_precedes_cited': 132, 'citation_edges_held_cites_held': 28207,
    'citation_edges_held_cites_new': 9, 'citation_edges_new_cites_held': 6410, 'citation_edges_new_cites_new': 150,
    'features_citation_links_received_total': 494144, 'features_core': 500, 'features_held_publications_total':
    5809, 'features_linked': 617, 'features_new_publications_total': 1138, 'features_no_included_link': 16393,
    'hcp_citation_pairs_not_coauthors_total': 401205, 'hcp_citation_pairs_total': 494144,
    'network_cross_group_pairs_total': 7206, 'network_new_pairs_total': 17437, 'new_links_analysis_included': 1138,
    'new_links_core': 843, 'new_links_tier_A': 607, 'new_links_tier_B': 531,
    'new_paper_pairs_in_coauthor_edges_2026_10_09': 1690, 'new_paper_pairs_in_universe_coauthor_edges': 535,
    'new_paper_pairs_new_pair': 3241, 'new_paper_pairs_type_core_core': 2745, 'new_paper_pairs_type_core_universe':
    2167, 'new_paper_pairs_type_universe_universe': 554, 'new_publications_analysis_included': 691,
    'new_publications_original_lymphoma_in_title': 523,
    'new_publications_supplement_cart_in_title_lymphoma_in_title_or_abstract': 176, 'new_publications_year_2026':
    699, 'npi_years_citations_received_total': 179992, 'npi_years_new_coauthors_total': 34874, 'npi_years_npis':
    1117, 'npi_years_papers_total': 6947, 'pairs_core_universe': 4100, 'pairs_shared_publications_total': 6426,
    'pairs_universe_universe': 718, 'rows_coauthor_pairs_by_year': 27018, 'rows_corpus_citation_edges': 34776,
    'rows_hcp_citation_links': 123557, 'rows_hcp_publications_by_year': 3167,
    'rows_hcp_universe_publication_features': 17510, 'rows_network_summary_by_year': 10, 'rows_new_authorships':
    1138, 'rows_new_paper_coauthor_edges': 5466, 'rows_new_publications': 699,
    'rows_publication_citation_counts_2026_11_20': 4062, 'rows_universe_authorships': 855,
    'rows_universe_coauthor_edges': 4818, 'universe_links_analysis_included': 851,
    'universe_links_institution_multi_site': 314, 'universe_links_tier_A': 484, 'universe_links_tier_B': 371,
    'universe_npis_included_affiliate_A': 378, 'universe_npis_included_affiliate_B': 32,
    'universe_npis_included_center_affiliate_unlinked': 43, 'universe_npis_included_hemonc_other': 14,
    'universe_npis_included_referring_proxy': 26, 'universe_npis_linked': 494,
    'universe_npis_linked_analysis_included': 493, 'year_pairs_core_core': 18584, 'year_pairs_core_universe': 7119,
    'year_pairs_shared_publications_total': 34184, 'year_pairs_universe_universe': 1315}

LIMITATIONS = [
    'The universe table carries no middle name, so a middle initial in the indexed name is not checked against the NPI; '
    'tier B rests on the first given name and surname being unique among the 17,510 universe NPIs. A or B links '
    'without institution evidence whose name another oncologist shares nationally (NPPES) are held back.',
    'Institution evidence compares distinctive words of CMS hospital and group names with the affiliation text; where '
    'the words fit several hospitals or groups the state must be named too, but a namesake at a sister hospital in '
    'the same state can still pass.',
    'Tier C links (name only) and namesake-risk links are kept only in a local review file under Data/interim; they are '
    'not tracked and are not used in pairs or features.',
    'New papers come from the live Europe PMC index on the retrieval date; papers of 2026 are outside the 2017-2025 '
    'window of the 2026-10-09 analysis and are marked in_2017_2025_window = 0.',
    'Held-paper and new-paper co-authorship are separate tables; features keep them in separate columns.',
    'Citation edges come from the Europe PMC citations lists of the cited papers, keeping citing records of source MED '
    'whose PMID is in the corpus; a citation Europe PMC has not linked is missing. 132 edges have a citing paper first '
    'published before the cited one (online-first dates, versions); they are kept and flagged.',
    'hcp_citation_links counts citing-paper x cited-paper x author pairs, so one citation between two large author teams '
    'adds to many NPI pairs; citation_pairs_not_coauthors removes pairs who share either paper.',
    'The refreshed citation counts are Europe PMC cited-by counts from all sources on 2026-11-20; PMID 40770877 was not '
    'returned by either query and has no 2026-11-20 count.',
]
DEFECTS_OPEN = []


# ---------------------------------------------------------------- check

TRACKED_RULES = {'A': RULES['A'], 'B': RULES['B']}
CHECKS = ['tables_missing', 'headline_changed',
          'ua_duplicate_keys', 'ua_overlap_core_authorships', 'ua_npi_not_universe', 'ua_npi_is_core', 'ua_pmid_not_held',
          'ua_tier_rule_invalid', 'ua_namesake_rule_broken', 'ua_include_wrong', 'ua_slot_claimed_by_core',
          'ua_slot_linked_twice', 'ua_count_wrong', 'ua_date_wrong', 'ua_provenance_blank', 'ua_contact_present',
          'ua_namesake_invalid', 'ua_multi_site_invalid', 'ua_name_disagrees_with_npi', 'na_name_disagrees_with_npi',
          'ue_duplicate_keys', 'ue_order_or_self', 'ue_overlap_core_edges', 'ue_both_core', 'ue_npi_not_universe',
          'ue_recomputed_mismatch', 'ue_missing_pairs', 'ue_provenance_blank',
          'np_duplicate_keys', 'np_overlap_held_pmid', 'np_overlap_held_doi', 'np_overlap_held_title',
          'np_internal_doi_or_title_duplicate', 'np_include_wrong', 'np_year_wrong', 'np_family_invalid',
          'np_provenance_blank',
          'na_duplicate_keys', 'na_overlap_core_authorships', 'na_pmid_not_new', 'na_npi_not_universe', 'na_is_core_wrong',
          'na_tier_rule_invalid', 'na_namesake_rule_broken', 'na_include_wrong', 'na_slot_linked_twice', 'na_count_wrong',
          'na_date_wrong', 'na_provenance_blank', 'na_contact_present', 'na_namesake_invalid', 'na_multi_site_invalid',
          'ne_duplicate_keys', 'ne_order_or_self', 'ne_npi_not_universe', 'ne_recomputed_mismatch', 'ne_missing_pairs',
          'ne_prior_status_wrong', 'ne_provenance_blank',
          'ce_duplicate_keys', 'ce_self_pair', 'ce_pmid_not_in_corpus', 'ce_date_or_status_wrong',
          'ce_precedes_flag_wrong', 'ce_cited_pull_not_complete', 'ce_provenance_blank',
          'hc_duplicate_keys', 'hc_self_pair', 'hc_npi_not_universe', 'hc_recomputed_mismatch', 'hc_missing_pairs',
          'cc_duplicate_keys', 'cc_pmid_set_not_corpus', 'cc_earlier_count_wrong', 'cc_change_wrong', 'cc_status_invalid',
          'cc_provenance_blank',
          'ft_duplicate_keys', 'ft_npi_set_not_universe', 'ft_recomputed_mismatch',
          'py_duplicate_keys', 'py_order_or_npi', 'py_recomputed_mismatch', 'py_held_sum_not_pair_tables',
          'py_2026_not_new_paper_pairs', 'hy_duplicate_keys', 'hy_npi_not_universe', 'hy_recomputed_mismatch',
          'hy_core_papers_not_hcps_csv', 'ny_duplicate_keys', 'ny_recomputed_mismatch']
CONTACT = lambda text: bool(EMAIL.search(text) or STREET.search(text) or PHONE.search(text))


def check(processed_dir, expected=None):
    """Check name to error count for the nine tables, reading only files under the processed directory.

    `expected` holds the headline totals to compare with (HEADLINE of this snapshot unless given).
    """
    expected = HEADLINE if expected is None else expected
    p = lambda snap, name: processed_dir / snap / name
    out = dict.fromkeys(CHECKS, 0)
    paths = [p(SNAPSHOT, f'{t}.csv') for t in TABLES] + [p(UNIVERSE_SNAPSHOT, 'hcp_divisions.csv')] + [
        p(CORE, n) for n in ('publications.csv', 'authorships.csv', 'withheld_authorships.csv', 'coauthor_edges.csv',
                             'hcps.csv')]
    out['tables_missing'] = sum(not x.is_file() for x in paths)
    if out['tables_missing']:
        return {f'{MODULE}_{k}': v for k, v in out.items()}
    (ua, ue, np_, na, ne, ce, hc, cc, ft, py, hy, ny, people, pubs, core_links, withheld, old_edges,
     hcps) = (read_csv(x) for x in paths)
    published = dict(zip(TABLES, (ua, ue, np_, na, ne, ce, hc, cc, ft, py, hy, ny)))
    people = {r['npi']: r for r in people}
    core = {n for n, r in people.items() if r['is_core'] == '1'}
    pubs = {r['pmid']: r for r in pubs}
    new = {r['pmid']: r for r in np_}
    core_pairs = {(r['npi'], r['pmid']) for r in core_links}
    claimed = {(r['pmid'], r['author_position']) for r in core_links + withheld}

    def links(rows, prefix, papers):
        out[f'{prefix}_duplicate_keys'] += duplicates(rows, ['npi', 'pmid'])
        out[f'{prefix}_slot_linked_twice'] += duplicates(rows, ['pmid', 'author_position'])
        for r in rows:
            paper = papers.get(r['pmid'], {})
            out[f'{prefix}_npi_not_universe'] += r['npi'] not in people
            person = people.get(r['npi'], {})
            out[f'{prefix}_name_disagrees_with_npi'] += (name_key(r['first_name_as_indexed'], r['last_name_as_indexed'])
                                                         != name_key(person.get('first_name', ''), person.get('last_name', '')))
            out[f'{prefix}_overlap_core_authorships'] += (r['npi'], r['pmid']) in core_pairs
            out[f'{prefix}_tier_rule_invalid'] += r['match_rule'] not in TRACKED_RULES.get(r['confidence_tier'], ())
            out[f'{prefix}_include_wrong'] += r['analysis_include'] != str(int(paper.get('analysis_include') == '1'))
            out[f'{prefix}_count_wrong'] += r['author_count'] != paper.get('author_count')
            out[f'{prefix}_date_wrong'] += r['publication_date'] != paper.get('first_publication_date')
            out[f'{prefix}_provenance_blank'] += not all(r[c] for c in ('source_id', 'metadata_retrieved_on', 'retrieved_at_utc'))
            out[f'{prefix}_contact_present'] += CONTACT(r['affiliation_at_publication'])
            n, h = r['nppes_name_matches_national'], r['nppes_hemonc_name_matches_national']
            valid = n.isdigit() and h.isdigit() and int(h) <= int(n)
            out[f'{prefix}_namesake_invalid'] += not valid
            out[f'{prefix}_namesake_rule_broken'] += valid and int(h) > 1 and r['match_rule'] not in INSTITUTION_RULES
            out[f'{prefix}_multi_site_invalid'] += (r['institution_multi_site'] not in ('0', '1')
                                                    or (r['institution_multi_site'] == '1' and r['match_rule'] not in INSTITUTION_RULES))

    links(ua, 'ua', pubs)
    for r in ua:
        out['ua_npi_is_core'] += r['npi'] in core
        out['ua_pmid_not_held'] += r['pmid'] not in pubs
        out['ua_slot_claimed_by_core'] += (r['pmid'], r['author_position']) in claimed
    links(na, 'na', new)
    for r in na:
        out['na_pmid_not_new'] += r['pmid'] not in new
        out['na_is_core_wrong'] += r['is_core'] != str(int(r['npi'] in core))

    def pairs(rows, prefix, redo, extra=()):
        out[f'{prefix}_duplicate_keys'] += duplicates(rows, ['npi_a', 'npi_b'])
        fields = ['pair_type', 'shared_publications', 'evidence_pmids', 'first_shared_publication_date',
                  'last_shared_publication_date', 'evidence_metadata_first_observed_on', 'evidence_metadata_last_observed_on']
        for e in rows:
            out[f'{prefix}_order_or_self'] += not e['npi_a'] < e['npi_b']
            out[f'{prefix}_npi_not_universe'] += e['npi_a'] not in people or e['npi_b'] not in people
            out[f'{prefix}_provenance_blank'] += not all(e[c] for c in ('source_id', 'evidence_metadata_last_observed_on', 'computed_at_utc'))
            r = redo.get((e['npi_a'], e['npi_b']))
            out[f'{prefix}_recomputed_mismatch'] += (r is None or any(str(r[f]) != e[f] for f in fields)
                                                     or abs(float(r['fractional_weight']) - float(e['fractional_weight'])) > 1e-9)
        out[f'{prefix}_missing_pairs'] += len(set(redo) - {(e['npi_a'], e['npi_b']) for e in rows} - set(extra))

    old = {tuple(sorted((r['hcp_id_a'].removeprefix('NPI'), r['hcp_id_b'].removeprefix('NPI')))) for r in old_edges}
    use = [r for r in core_links if r['analysis_include'] == '1'] + [r for r in ua if r['analysis_include'] == '1']
    redo = {(e['npi_a'], e['npi_b']): e for e in edges(use, {k: v['author_count'] for k, v in pubs.items()},
                                                       {k: v['first_publication_date'] for k, v in pubs.items()},
                                                       {k: v['metadata_retrieved_on'] for k, v in pubs.items()}, core)}
    pairs(ue, 'ue', redo, old)
    for e in ue:
        out['ue_overlap_core_edges'] += (e['npi_a'], e['npi_b']) in old
        out['ue_both_core'] += e['npi_a'] in core and e['npi_b'] in core
    redo_new = {(e['npi_a'], e['npi_b']): e for e in edges(
        [r for r in na if r['analysis_include'] == '1'], {k: v['author_count'] for k, v in new.items()},
        {k: v['first_publication_date'] for k, v in new.items()}, {k: v['retrieved_at_utc'][:10] for k, v in new.items()},
        core, core_pairs=True)}
    pairs(ne, 'ne', redo_new)
    universe_pairs = {(e['npi_a'], e['npi_b']) for e in ue}
    out['ne_prior_status_wrong'] = sum(e['prior_pair_status'] != prior_status((e['npi_a'], e['npi_b']), old, universe_pairs)
                                       for e in ne)

    held_dois = {norm_doi(r['doi']) for r in pubs.values() if r['doi']}
    held_titles = {norm_title(r['title']) for r in pubs.values()}
    out['np_duplicate_keys'] = duplicates(np_, ['pmid'])
    dois = [norm_doi(r['doi']) for r in np_ if r['doi']]
    titles = [norm_title(r['title']) for r in np_]
    out['np_internal_doi_or_title_duplicate'] = len(dois) - len(set(dois)) + len(titles) - len(set(titles))
    for r in np_:
        out['np_overlap_held_pmid'] += r['pmid'] in pubs
        out['np_overlap_held_doi'] += bool(r['doi']) and norm_doi(r['doi']) in held_dois
        out['np_overlap_held_title'] += norm_title(r['title']) in held_titles
        inside = WINDOW[0] <= r['first_publication_date'] <= WINDOW[1]
        out['np_include_wrong'] += (r['analysis_include'] == '1') and not inside
        out['np_include_wrong'] += r['in_2017_2025_window'] != str(int('2017-01-01' <= r['first_publication_date'] <= '2025-12-31'))
        out['np_include_wrong'] += (r['analysis_include'] == '1') == bool(r['exclusion_reason'])
        out['np_year_wrong'] += r['publication_year'] != r['first_publication_date'][:4]
        out['np_family_invalid'] += r['query_family'] not in FAMILIES
        out['np_provenance_blank'] += not all(r[c] for c in ('source_id', 'source_release', 'retrieved_at_utc'))

    corpus = {p: {'date': r['first_publication_date'], 'status': 'held'} for p, r in pubs.items()}
    corpus.update({p: {'date': r['first_publication_date'], 'status': 'new'} for p, r in new.items()})
    pull = {r['pmid']: r['citation_pull_status'] for r in cc}
    out['ce_duplicate_keys'] = duplicates(ce, ['citing_pmid', 'cited_pmid'])
    for e in ce:
        a, b = corpus.get(e['citing_pmid']), corpus.get(e['cited_pmid'])
        out['ce_self_pair'] += e['citing_pmid'] == e['cited_pmid']
        out['ce_pmid_not_in_corpus'] += not a or not b
        out['ce_provenance_blank'] += not all(e[c] for c in ('source_id', 'source_release', 'retrieved_at_utc'))
        out['ce_cited_pull_not_complete'] += pull.get(e['cited_pmid']) != 'complete'
        if a and b:
            out['ce_date_or_status_wrong'] += (e['citing_publication_date'], e['cited_publication_date'], e['citing_corpus_status'],
                                               e['cited_corpus_status']) != (a['date'], b['date'], a['status'], b['status'])
            out['ce_precedes_flag_wrong'] += e['citing_precedes_cited'] != str(int(a['date'] < b['date']))
    included = ([r for r in core_links if r['analysis_include'] == '1'] + [r for r in ua + na if r['analysis_include'] == '1'])
    redo_hc = {(r['citing_npi'], r['cited_npi']): r for r in hcp_citations(ce, included)}
    out['hc_duplicate_keys'] = duplicates(hc, ['citing_npi', 'cited_npi'])
    for r in hc:
        out['hc_self_pair'] += r['citing_npi'] == r['cited_npi']
        out['hc_npi_not_universe'] += r['citing_npi'] not in people or r['cited_npi'] not in people
        x = redo_hc.get((r['citing_npi'], r['cited_npi']))
        out['hc_recomputed_mismatch'] += x is None or any(str(x[k]) != r[k] for k in x)
    out['hc_missing_pairs'] = len(set(redo_hc) - {(r['citing_npi'], r['cited_npi']) for r in hc})
    out['cc_duplicate_keys'] = duplicates(cc, ['pmid'])
    out['cc_pmid_set_not_corpus'] = len({r['pmid'] for r in cc} ^ set(corpus))
    statuses = {'complete', 'no_citations', 'incomplete', 'not_pulled'}
    for r in cc:
        old = pubs.get(r['pmid'])
        out['cc_earlier_count_wrong'] += r['citation_count_epmc_2026_10_09'] != (old['citation_count_epmc'] if old else '')
        now_count, before = r['citation_count_epmc_2026_11_20'], r['citation_count_epmc_2026_10_09']
        out['cc_change_wrong'] += r['citation_count_change'] != (str(int(now_count) - int(before)) if now_count and before else '')
        out['cc_status_invalid'] += (r['citation_pull_status'] not in statuses
                                     or (r['observation_status'] == 'observed') != bool(now_count)
                                     or (r['citation_pull_status'] == 'no_citations') != (now_count == '0')
                                     or r['corpus_status'] != corpus.get(r['pmid'], {}).get('status'))
        out['cc_provenance_blank'] += not all(r[c] for c in ('source_id', 'source_release', 'retrieved_at_utc'))
    out['ft_duplicate_keys'] = duplicates(ft, ['npi'])
    out['ft_npi_set_not_universe'] = len({r['npi'] for r in ft} ^ set(people))
    redo_ft = {r['npi']: r for r in features(people, core_links, ua, na, ue, old_edges, ne, hc)}
    out['ft_recomputed_mismatch'] = sum(r['npi'] not in redo_ft or any(str(redo_ft[r['npi']][k]) != r[k]
                                                                       for k in redo_ft[r['npi']] if k != 'computed_at_utc')
                                        for r in ft)
    # time axis: recomputed from the link tables and reconciled with the pair tables
    years = paper_years(pubs, new)
    links_all = included_links(core_links, ua, na)
    out['py_duplicate_keys'] = duplicates(py, ['npi_a', 'npi_b', 'publication_year'])
    out['py_order_or_npi'] = sum(not r['npi_a'] < r['npi_b'] or r['npi_a'] not in people or r['npi_b'] not in people for r in py)
    redo_py = {(r['npi_a'], r['npi_b'], r['publication_year']): r for r in pairs_by_year(links_all, years, core)}
    fields = ['pair_type', 'shared_publications', 'evidence_pmids']
    out['py_recomputed_mismatch'] = len(set(redo_py) ^ {(r['npi_a'], r['npi_b'], r['publication_year']) for r in py}) + sum(
        any(str(redo_py[k][f]) != r[f] for f in fields) or abs(float(redo_py[k]['fractional_weight']) - float(r['fractional_weight'])) > 1e-9
        for r in py for k in [(r['npi_a'], r['npi_b'], r['publication_year'])] if k in redo_py)
    held_sum, new_sum = collections.defaultdict(lambda: [0, 0.0]), collections.defaultdict(lambda: [0, 0.0])
    for r in py:
        cell = new_sum if r['publication_year'] == '2026' else held_sum
        cell[r['npi_a'], r['npi_b']][0] += int(r['shared_publications'])
        cell[r['npi_a'], r['npi_b']][1] += float(r['fractional_weight'])
    targets = {tuple(sorted((r['hcp_id_a'].removeprefix('NPI'), r['hcp_id_b'].removeprefix('NPI')))): r for r in old_edges}
    targets.update({(r['npi_a'], r['npi_b']): r for r in ue})
    out['py_held_sum_not_pair_tables'] = len(set(held_sum) ^ set(targets)) + sum(
        held_sum[k][0] != int(t['shared_publications']) or abs(held_sum[k][1] - float(t['fractional_weight'])) > 1e-9
        for k, t in targets.items() if k in held_sum)
    later = {(r['npi_a'], r['npi_b']): r for r in ne}
    out['py_2026_not_new_paper_pairs'] = len(set(new_sum) ^ set(later)) + sum(
        new_sum[k][0] != int(t['shared_publications']) or abs(new_sum[k][1] - float(t['fractional_weight'])) > 1e-9
        for k, t in later.items() if k in new_sum)
    out['hy_duplicate_keys'] = duplicates(hy, ['npi', 'publication_year'])
    out['hy_npi_not_universe'] = sum(r['npi'] not in people for r in hy)
    redo_hy = {(r['npi'], r['publication_year']): r for r in npi_years(links_all, years, core, ce)}
    out['hy_recomputed_mismatch'] = len(set(redo_hy) ^ {(r['npi'], r['publication_year']) for r in hy}) + sum(
        any(str(redo_hy[k][f]) != r[f] for f in redo_hy[k]) for r in hy for k in [(r['npi'], r['publication_year'])] if k in redo_hy)
    core_papers = collections.Counter()
    for r in hy:
        if r['is_core'] == '1' and r['publication_year'] <= '2025':
            core_papers[r['npi']] += int(r['papers'])
    out['hy_core_papers_not_hcps_csv'] = sum(core_papers[h['npi']] != int(h['publications_2017_2025']) for h in hcps)
    groups = {n: DIVISION_GROUPS.get(r['division']) for n, r in people.items()}
    out['ny_duplicate_keys'] = duplicates(ny, ['publication_year'])
    redo_ny = {r['publication_year']: r for r in network_by_year(py, links_all, years, groups)}
    out['ny_recomputed_mismatch'] = len(set(redo_ny) ^ {r['publication_year'] for r in ny}) + sum(
        any(str(redo_ny[r['publication_year']][f]) != r[f] for f in redo_ny[r['publication_year']])
        for r in ny if r['publication_year'] in redo_ny)
    totals = headline(published, people)
    out['headline_changed'] = len({k for k in set(totals) | set(expected) if totals.get(k) != expected.get(k)}) if expected else 0
    return {f'{MODULE}_{k}': v for k, v in out.items()}


if __name__ == '__main__':
    build()
