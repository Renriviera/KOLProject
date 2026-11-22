"""Entity resolution and card search behind the agent tools `find_entity` and `search_cards`.

`build_aliases` writes lower-cased lookup strings for every HCP, center, community, product, manufacturer group
and place into the `alias` table. `find_entity` loads them into memory once per connection and resolves user
text in three tiers: exact alias, prefix, then fuzzy. `search_cards` runs the full-text index over the cards.
The short forms written by hand below are public institution and company names; no person is named in this file.
"""
from __future__ import annotations

import bisect
import json
import re
import sqlite3
import time
import unicodedata
from collections import Counter, defaultdict

from rapidfuzz import fuzz, process

from .cards import CARD_TYPES, ENTITY_TYPES, REFERENCE_TYPES, STATES, build_cards, rows, table_exists, write_cards
from .packbase import connect
from .paths import PACK

KINDS = ('hcp', 'center', 'community', 'product', 'manufacturer', 'place')
SCORE_FLOOR = 60.0        # nothing below this is returned: an empty list is better than a weak guess
KEEP_WINDOW = 15.0        # candidates further than this below the best one are dropped
AMBIGUITY_MARGIN = 5.0    # two candidates this close are "near-equal": the caller should ask the user
LAST_NAME_MIN = 85        # a person is a fuzzy candidate only if a query word is this close to the last name
GIVEN_NAME_MIN = 50       # and, when given names were typed, they must match at least this well
FUZZY_MIN, FUZZY_MIN_SHORT = 80, 88   # similarity needed for other kinds; stricter under six characters
PREFIX_MIN_COVERAGE = 0.5  # a prefix must cover at least half of the alias it completes
DF_MAX = 3                # a word naming more than this many entities of a kind cannot carry a match alone

TITLES = {'dr', 'doctor', 'prof', 'professor', 'mr', 'mrs', 'ms', 'md', 'phd', 'mbbs', 'facp'}
NAME_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}
COMMUNITY_WORDS = ('orbit', 'group', 'network', 'community')
LEGAL = {'inc', 'llc', 'corporation', 'corp', 'authority', 'ltd', 'lp'}
CENTER_GENERIC = LEGAL | {'the', 'of', 'and', 'for', 'at', 'in', 'hospital', 'hospitals', 'medical', 'center', 'centre',
                          'centers', 'clinic', 'clinics', 'cancer', 'health', 'healthcare', 'care', 'system', 'institute',
                          'research', 'campus', 'program'}
# Words too common or too vague to stand alone as a center's short form.
WEAK_TOKENS = {'north', 'south', 'east', 'west', 'new', 'saint', 'general', 'memorial', 'regional', 'university',
               'community', 'national', 'methodist', 'baptist', 'presbyterian', 'childrens', 'children', 'family',
               'state', 'city', 'green', 'good', 'science', 'sciences', 'primary', 'valley', 'park', 'mount', 'long',
               'island', 'college', 'school', 'affiliated', 'with', 'operations', 'clinical', 'inpatient', 'pavilion',
               'allied', 'diseases', 'lutheran', 'jewish', 'mercy', 'womens', 'fair', 'oaks', 'shea', 'metro', 'inc',
               'county', 'summit', 'gateway', 'shore', 'orthopaedic', 'via', 'blank', 'white', 'wood', 'cook', 'ford',
               'arthur', 'henry', 'robert', 'roger', 'ronald', 'thomas', 'kenneth', 'milton', 'fred', 'johns', 'lee',
               'mary', 'beth', 'francis', 'vincent', 'jude', 'davids', 'scott', 'williams', 'johnson', 'jacobs'}
NOISE = {
    'hcp': TITLES | {'the'},
    'center': CENTER_GENERIC,
    'community': set(COMMUNITY_WORDS) | {'the', 'orbits', 'cluster', 'team'},
    'product': {'the', 'car', 't', 'cart', 'cell', 'therapy', 'product', 'drug'},
    'manufacturer': {'the', 'inc', 'pharma', 'pharmaceuticals', 'pharmaceutical', 'company', 'co', 'corp',
                     'therapeutics', 'sciences', 'biotech', 'bio'},
    'place': {'the', 'state', 'of'},
}

# (words found in the center's name, short forms people use, weight). A pattern may match several centers.
HAND_CENTERS = [
    ('memorial hospital for cancer', ['msk', 'mskcc', 'sloan kettering', 'memorial sloan kettering', 'sloan'], 0.95),
    ('m d anderson', ['md anderson', 'm d anderson', 'mdacc', 'mda', 'anderson', 'ut md anderson'], 0.95),
    ('hospital of the university of pennsylvania', ['penn', 'upenn', 'hup', 'penn medicine', 'university of pennsylvania',
                                                    'abramson', 'penn abramson'], 0.95),
    ('massachusetts general', ['mass general', 'mgh'], 0.95),
    ('brigham', ['brigham', 'brigham and womens', 'bwh'], 0.95),
    ('dana farber', ['dana farber', 'dfci'], 0.95),
    ('moffitt', ['moffitt', 'moffitt cancer center', 'h lee moffitt'], 0.95),
    ('city of hope national', ['city of hope', 'coh', 'city of hope duarte'], 0.95),
    ('stanford', ['stanford', 'stanford hospital', 'stanford medicine', 'stanford university'], 0.95),
    ('mayo clinic', ['mayo', 'mayo clinic'], 0.95),
    ('fred hutchinson', ['fred hutch', 'fred hutchinson', 'the hutch', 'hutch', 'fhcc', 'university of washington',
                         'uw medicine', 'seattle cancer care alliance', 'scca'], 0.95),
    ('cleveland clinic', ['cleveland clinic', 'ccf'], 0.95),
    ('arthur g james', ['ohio state', 'ohio state university', 'osu', 'the james', 'james cancer hospital', 'osuccc'], 0.95),
    ('northwestern memorial', ['northwestern', 'northwestern medicine', 'lurie cancer center', 'lurie'], 0.95),
    ('vanderbilt', ['vanderbilt', 'vumc'], 0.95),
    ('barnes jewish', ['barnes jewish', 'barnes', 'bjc', 'washu', 'wash u', 'washington university', 'wustl',
                       'siteman', 'siteman cancer center'], 0.95),
    ('ucsf', ['ucsf', 'uc san francisco', 'university of california san francisco'], 0.95),
    ('ronald reagan ucla', ['ucla', 'uc los angeles', 'university of california los angeles'], 0.95),
    ('santa monica ucla', ['ucla santa monica', 'santa monica ucla'], 0.95),
    ('santa monica ucla', ['ucla'], 0.85),
    ('duke university', ['duke', 'duke health'], 0.95),
    ('emory university', ['emory', 'winship', 'emory winship', 'winship cancer institute'], 0.95),
    ('johns hopkins', ['hopkins', 'johns hopkins', 'jhu', 'jhh'], 0.95),
    ('university of chicago', ['uchicago', 'uchicago medicine'], 0.95),
    ('university of miami', ['sylvester', 'miami sylvester', 'umiami'], 0.95),
    ('university of maryland medical', ['greenebaum', 'maryland greenebaum', 'university of maryland'], 0.95),
    ('froedtert', ['froedtert', 'medical college of wisconsin', 'mcw'], 0.95),
    ('ohsu', ['ohsu', 'knight', 'knight cancer institute', 'oregon health and science university'], 0.95),
    ('unc hospitals', ['unc', 'lineberger', 'unc lineberger', 'university of north carolina'], 0.95),
    ('new york presbyterian', ['nyp', 'weill cornell', 'cornell', 'columbia', 'newyork presbyterian'], 0.95),
    ('nyu langone', ['nyu', 'langone', 'perlmutter'], 0.95),
    ('mount sinai', ['mount sinai', 'mt sinai', 'icahn', 'tisch'], 0.95),
    ('university of colorado hospital', ['university of colorado', 'uchealth', 'cu anschutz', 'anschutz'], 0.95),
    ('nebraska medical center', ['nebraska medicine', 'unmc', 'university of nebraska'], 0.95),
    ('university of kansas hospital', ['ku', 'kumc', 'ku medical center', 'university of kansas'], 0.95),
    ('fairview hospital university', ['university of minnesota', 'umn', 'm health fairview', 'm health'], 0.95),
    ('university of michigan', ['umich', 'michigan medicine', 'rogel'], 0.95),
    ('university of alabama hospital', ['uab', 'university of alabama at birmingham'], 0.95),
    ('carolinas medical center', ['atrium', 'atrium health', 'levine', 'levine cancer institute', 'atrium levine'], 0.95),
    ('hackensack', ['hackensack', 'hackensack meridian', 'hmh', 'john theurer cancer center'], 0.95),
    ('roswell park', ['roswell', 'roswell park'], 0.95),
    ('yale new haven', ['yale', 'smilow'], 0.95),
    ('university of utah', ['huntsman', 'huntsman cancer institute'], 0.95),
    ('university of iowa', ['holden', 'iowa'], 0.95),
    ('university of wisconsin hospitals', ['uw health', 'carbone', 'uw madison'], 0.95),
    ('uh cleveland', ['university hospitals', 'uh', 'seidman', 'seidman cancer center', 'case western'], 0.95),
    ('ut southwestern', ['utsw', 'southwestern', 'simmons cancer center'], 0.95),
    ('uva health', ['uva', 'university of virginia'], 0.95),
    ('virginia commonwealth', ['vcu', 'massey', 'vcu massey'], 0.95),
    ('karmanos', ['karmanos', 'wayne state'], 0.95),
    ('university of rochester', ['wilmot', 'urmc', 'strong memorial'], 0.95),
    ('baylor university medical center', ['baylor dallas', 'bumc'], 0.95),
    ('thomas jefferson', ['jefferson', 'tju'], 0.95),
    ('temple university', ['temple', 'fox chase'], 0.95),
    ('beth israel deaconess', ['bidmc', 'beth israel'], 0.95),
    ('uci health', ['uci', 'uc irvine', 'university of california irvine'], 0.95),
    ('university of california san diego', ['ucsd', 'uc san diego'], 0.95),
    ('university of california davis', ['uc davis', 'ucd'], 0.95),
    ('uf health shands', ['uf', 'uf health', 'shands', 'university of florida'], 0.95),
    ('musc', ['musc', 'medical university of south carolina', 'hollings'], 0.95),
    ('medstar georgetown', ['georgetown', 'lombardi'], 0.95),
    ('indiana university health', ['iu', 'iu health', 'indiana university'], 0.95),
    ('cedars sinai', ['cedars', 'cedars sinai'], 0.95),
    ('north carolina baptist', ['wake forest', 'wake forest baptist', 'atrium wake forest'], 0.95),
    ('childrens hospital of philadelphia', ['chop'], 0.95),
    ('st jude', ['st jude', 'saint jude'], 0.95),
    ('mary hitchcock', ['dartmouth', 'dartmouth hitchcock', 'dartmouth health', 'dhmc'], 0.95),
    ('long island jewish', ['lij', 'northwell'], 0.95),
    ('north shore university', ['north shore', 'northwell'], 0.95),
    ('ou health', ['ou', 'ou health', 'university of oklahoma', 'stephenson'], 0.95),
    ('wvu hospitals', ['wvu', 'west virginia university'], 0.95),
    ('uams', ['uams', 'university of arkansas for medical sciences'], 0.95),
    ('penn state', ['penn state', 'hershey', 'penn state hershey'], 0.95),
    ('upmc presbyterian shadyside', ['upmc', 'hillman', 'upmc hillman', 'shadyside', 'university of pittsburgh'], 0.95),
    ('upmc magee', ['magee'], 0.95),
    ('usc kenneth norris', ['usc', 'norris', 'usc norris', 'keck'], 0.95),
    ('university of kentucky', ['uk healthcare', 'markey'], 0.95),
    ('suny health science center at syracuse', ['upstate', 'suny upstate'], 0.95),
    ('university of mississippi', ['ummc', 'university of mississippi'], 0.95),
    ('montefiore', ['montefiore', 'einstein', 'montefiore einstein'], 0.95),
    ('tristar centennial', ['tristar', 'sarah cannon', 'sarah cannon nashville'], 0.95),
    ('presbyterian st lukes', ['colorado blood cancer institute', 'cbci'], 0.95),
    ('medical college of georgia', ['augusta university', 'mcg', 'georgia cancer center'], 0.95),
    ('robert wood johnson', ['rwj', 'rutgers', 'rutgers cancer institute', 'rwjbarnabas'], 0.95),
    ('florida health sciences center', ['tampa general', 'tgh'], 0.95),
    ('swedish medical center', ['swedish', 'swedish cancer institute'], 0.95),
    ('adventhealth orlando', ['adventhealth', 'advent health'], 0.95),
]
# (words found in the community's short label, extra names for it).
HAND_COMMUNITIES = [
    ('memorial sloan kettering', ['msk', 'mskcc', 'sloan kettering', 'sloan']),
    ('mass general brigham', ['mgh', 'mass general', 'brigham', 'dfci', 'mgb', 'harvard']),
    ('penn', ['upenn', 'hup', 'university of pennsylvania']),
    ('ohio state', ['osu', 'the james']),
    ('md anderson', ['mda', 'mdacc', 'anderson', 'm d anderson']),
    ('mayo clinic', ['mayo']),
    ('city of hope', ['coh']),
    ('fred hutch', ['fred hutchinson', 'hutch', 'university of washington', 'seattle']),
    ('weill cornell', ['cornell', 'weill']),
    ('baylor', ['bcm', 'baylor college of medicine']),
    ('stanford', ['stanford university']),
    ('vanderbilt', ['vumc']),
    ('moffitt', ['h lee moffitt', 'moffitt cancer center']),
]
# (a member company's name, extra names for its group).
HAND_MANUFACTURERS = [
    ('bristol myers squibb', ['bms', 'bristol', 'bristol myers']),
    ('johnson and johnson', ['j&j', 'jnj', 'j j', 'johnson johnson']),
]
USA_NAMES = ['usa', 'us', 'united states', 'united states of america', 'america', 'nationwide', 'national',
             'whole country', 'all states']

STOPWORDS = {'a', 'an', 'the', 'of', 'in', 'at', 'on', 'for', 'to', 'by', 'and', 'or', 'not', 'with', 'from', 'near',
             'is', 'are', 'was', 'were', 'be', 'do', 'does', 'did', 'has', 'have', 'had', 'what', 'which', 'who',
             'whom', 'whose', 'where', 'when', 'how', 'why', 'show', 'me', 'list', 'find', 'give', 'tell', 'about',
             'there', 'any', 'all', 'that', 'this', 'these', 'those', 'it', 'its', 'their', 'our', 'my', 'most',
             'many', 'much', 'please', 'can', 'you', 'i', 'we', 'us', 'as', 'than', 'into', 'based', 'focused',
             'mean', 'means', 'meaning', 'define', 'definition', 'explain', 'describe'}
TYPE_HINTS = {
    'center': 'center', 'centers': 'center', 'hospital': 'center', 'hospitals': 'center',
    'hcp': 'hcp', 'hcps': 'hcp', 'clinician': 'hcp', 'clinicians': 'hcp', 'physician': 'hcp', 'physicians': 'hcp',
    'doctor': 'hcp', 'doctors': 'hcp', 'oncologist': 'hcp', 'oncologists': 'hcp', 'hematologist': 'hcp',
    'hematologists': 'hcp', 'communities': 'community', 'orbits': 'community', 'manufacturer': 'manufacturer',
    'manufacturers': 'manufacturer', 'company': 'manufacturer', 'companies': 'manufacturer', 'paper': 'paper',
    'papers': 'paper', 'publication': 'paper', 'publications': 'paper', 'article': 'paper', 'articles': 'paper',
    'caveat': 'caveat', 'caveats': 'caveat', 'limitation': 'caveat', 'limitations': 'caveat'}
TITLE_WEIGHT, TEXT_LIMIT = 4.0, 400
DEFINITION = re.compile(r"\s*(what\s+is\s+an?|what\s+(does|do)|define|explain|meaning\s+of)\b", re.I)


# ---------------------------------------------------------------- text shaping

def normalize(text):
    """Lower-case lookup form: no accents, no punctuation, single spaces; 'M.D.' -> 'md', '&' -> 'and'."""
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower().replace('&', ' and ')
    text = re.sub(r'\b(dr|prof|mr|mrs|ms)\.', r'\1 ', text)
    text = re.sub(r"['’`]", '', text)
    text = re.sub(r'(?<=[a-z])\.(?=[a-z])', '', text)
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text).split())


def variants(kind, text):
    """Lookup strings to try for one kind, most literal first: as typed, then without that kind's noise words."""
    text = str(text or '')
    if kind in ('hcp', 'community'):
        text = re.sub(r"(\w)['’]s\b", r'\1', text)
    words = normalize(text).split()
    while words and words[0] == 'the':
        words.pop(0)
    out = [] if kind == 'hcp' else [' '.join(words)]
    if kind == 'center':
        trimmed = list(words)
        while trimmed and trimmed[-1] in LEGAL:
            trimmed.pop()
        out.append(' '.join(trimmed))
    out.append(' '.join(w for w in words if w not in NOISE[kind]))
    return [v for v in dict.fromkeys(out) if v]


def clip(text, limit=TEXT_LIMIT):
    """Text cut at a word boundary to at most `limit` characters."""
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'


# ---------------------------------------------------------------- alias table

def hcp_aliases(con):
    for h in rows(con, 'select npi, name, first_name, last_name from dim_hcp'):
        first, full = normalize(h['first_name']), normalize(h['name'])
        last = ' '.join(w for w in normalize(h['last_name']).split() if w not in NAME_SUFFIXES)
        if full:
            yield full, 'hcp', h['npi'], 1.0, 'full_name'
        if first and last:
            yield f'{first} {last}', 'hcp', h['npi'], 1.0, 'first_last'
            yield f'{last} {first}', 'hcp', h['npi'], 1.0, 'last_first'
            if ' ' in first:
                yield f'{first.split()[0]} {last}', 'hcp', h['npi'], 1.0, 'first_last'
        if last:
            yield last, 'hcp', h['npi'], 0.8, 'last_name'
            for part in last.split() if ' ' in last else ():
                if len(part) >= 3:
                    yield part, 'hcp', h['npi'], 0.7, 'last_name_part'


def center_core(name):
    return ' '.join(w for w in name.split() if w not in CENTER_GENERIC)


def center_aliases(con):
    centers = rows(con, 'select ccn, name, city, state from dim_center')
    names = {c['ccn']: normalize(c['name']) for c in centers}
    owners = defaultdict(set)
    for ccn, name in names.items():
        for word in center_core(name).split():
            owners[word].add(ccn)
    places = {w for name in list(STATES.values()) + [c['city'] for c in centers] for w in normalize(name).split()}
    places |= {k.lower() for k in STATES}
    city_names = {normalize(c['city']) for c in centers} | {normalize(v) for v in STATES.values()}
    hand = defaultdict(set)
    for ccn, name in names.items():
        for pattern, forms, weight in HAND_CENTERS:
            if f' {pattern} ' in f' {name} ' or name.startswith(pattern):
                hand[ccn].update((normalize(form), weight) for form in forms)
    taken = {form for forms in hand.values() for form, _ in forms}
    for c in centers:
        ccn, name, city = c['ccn'], names[c['ccn']], normalize(c['city'])
        words = name.split()
        while words and words[-1] in LEGAL:
            words.pop()
        trimmed = ' '.join(words[1:] if words[:1] == ['the'] else words)
        core = center_core(name)
        yield name, 'center', ccn, 1.0, 'name'
        yield trimmed, 'center', ccn, 0.99, 'name'
        yield core, 'center', ccn, 0.7 if core in city_names else 0.9, 'core_name'
        if city:
            yield f'{trimmed} {city}', 'center', ccn, 0.95, 'name_city'
            yield f'{core} {city}', 'center', ccn, 0.9, 'name_city'
            yield city, 'center', ccn, 0.7, 'city'
        for word in core.split():
            if len(word) >= 3 and owners[word] == {ccn} and word not in WEAK_TOKENS | places | taken:
                yield word, 'center', ccn, 0.8, 'distinct_word'
        for form, weight in hand[ccn]:
            yield form, 'center', ccn, weight, 'short_form'


def community_aliases(con):
    communities = rows(con, 'select community_id, label, short_label, kind, top_institution from dim_community')
    for c in communities:
        ident, label, short = str(c['community_id']), normalize(c['label']), normalize(c['short_label'])
        yield label, 'community', ident, 1.0, 'label'
        yield short, 'community', ident, 1.0, 'label'
        if c['kind'] != 'named':
            continue
        base = ' '.join(w for w in short.split() if w not in COMMUNITY_WORDS and w != 'loose')
        named = {base: (0.98, 'institution')}
        parts = re.split(r'\s+[–—-]\s+|/', re.sub(r'\b(orbit|network|loose)\b', ' ', c['short_label'], flags=re.I))
        for part in parts + re.split(r'/', c['top_institution'] or '') + [c['top_institution'] or '']:
            named.setdefault(normalize(part), (0.92, 'institution'))
        for pattern, forms in HAND_COMMUNITIES:
            if f' {pattern} ' in f' {short} ':
                for form in forms:
                    named.setdefault(normalize(form), (0.9, 'short_form'))
        for name, (weight, source) in named.items():
            if not name:
                continue
            yield name, 'community', ident, weight, source
            for word in COMMUNITY_WORDS:
                yield f'{name} {word}', 'community', ident, weight, 'form'
    for h in rows(con, 'select npi, name, first_name, last_name, community_id from dim_hcp where is_leader = 1'):
        ident, first, last = str(h['community_id']), normalize(h['first_name']), normalize(h['last_name'])
        yield normalize(h['name']), 'community', ident, 0.85, 'leader'
        if first and last:
            yield f'{first} {last}', 'community', ident, 0.85, 'leader'
        if last:
            yield last, 'community', ident, 0.75, 'leader'


def product_aliases(con):
    for p in rows(con, 'select brand, generic, short_name, hcpcs_code from dim_product'):
        short, generic = normalize(p['short_name']), normalize(p['generic'])
        yield normalize(p['brand']), 'product', p['brand'], 1.0, 'brand'
        yield generic, 'product', p['brand'], 1.0, 'generic'
        yield short, 'product', p['brand'], 1.0, 'short_name'
        yield short.replace(' ', ''), 'product', p['brand'], 0.98, 'short_name'
        if len(generic.split()) > 1 and len(generic.split()[0]) >= 6:
            yield generic.split()[0], 'product', p['brand'], 0.95, 'generic'
        if p['hcpcs_code']:
            yield normalize(p['hcpcs_code']), 'product', p['brand'], 0.95, 'billing_code'


def company_forms(name):
    """A company name as written and without words such as pharma, biotech or inc."""
    full = normalize(name)
    return {full, ' '.join(w for w in full.split() if w not in NOISE['manufacturer'])} - {''}


def manufacturer_groups(con):
    """Company groups of the products, then the partner companies, which count as groups of their own."""
    found = rows(con, 'select company_group, partner from dim_product order by first_us_approval, brand')
    return list(dict.fromkeys([p['company_group'] for p in found] + [p['partner'] for p in found if p['partner']]))


def manufacturer_aliases(con):
    for group in manufacturer_groups(con):
        yield normalize(group), 'manufacturer', group, 1.0, 'group'
        for member in group.split('/'):
            for form in company_forms(member):
                yield form, 'manufacturer', group, 0.98, 'member_company'
            for pattern, forms in HAND_MANUFACTURERS:
                if normalize(member) == pattern:
                    for form in forms:
                        yield normalize(form), 'manufacturer', group, 0.95, 'short_form'


def place_aliases(con):
    for code, name in STATES.items():
        yield normalize(name), 'place', code, 1.0, 'state'
        yield code.lower(), 'place', code, 1.0, 'state_code'
    for name in USA_NAMES:
        yield name, 'place', 'USA', 1.0, 'country'
    for c in rows(con, "select distinct city, state from dim_center where city is not null and city != '' and state is not null"):
        ident, city = f"{c['city']}, {c['state']}", normalize(c['city'])
        yield city, 'place', ident, 0.9, 'city'
        yield f"{city} {c['state'].lower()}", 'place', ident, 1.0, 'city_state'
        if c['state'] in STATES:
            yield f"{city} {normalize(STATES[c['state']])}", 'place', ident, 1.0, 'city_state'


def build_aliases(con):
    """Write the `alias(alias, kind, id, weight, source)` table and return the number of aliases per kind.

    `weight` (0 to 1) is how surely the string means that entity: 1.0 for a full name, 0.8 for a last name alone,
    0.95 for a hand-written short form. `source` says where the string came from and is echoed in `matched_on`.
    """
    best = {}
    for make in (hcp_aliases, center_aliases, community_aliases, product_aliases, manufacturer_aliases, place_aliases):
        for alias, kind, ident, weight, source in make(con):
            alias = alias[4:] if alias.startswith('the ') else alias
            key = (alias, kind, str(ident))
            if alias and (key not in best or weight > best[key][0]):
                best[key] = (weight, source)
    con.execute('drop table if exists alias')
    con.execute('create table alias (alias text, kind text, id text, weight real, source text, primary key (alias, kind, id))')
    con.executemany('insert into alias values (?, ?, ?, ?, ?)', ((a, k, i, w, s) for (a, k, i), (w, s) in best.items()))
    con.execute('create index ix_alias_kind_alias on alias (kind, alias)')
    con.commit()
    forget(con)
    return dict(Counter(kind for _, kind, _ in best))


# ---------------------------------------------------------------- in-memory index

class Index:
    """Aliases and display facts of one pack, loaded once so a lookup costs well under a millisecond."""

    def __init__(self, con):
        self.info = {kind: {} for kind in KINDS}
        self.exact = {kind: defaultdict(list) for kind in KINDS}
        self.df = {kind: Counter() for kind in KINDS}
        self.by_last, self.given = defaultdict(list), {}
        self.load_info(con)
        words = {kind: defaultdict(set) for kind in KINDS}
        for a in rows(con, 'select alias, kind, id, weight, source from alias') if table_exists(con, 'alias') else ():
            if a['kind'] in self.info and a['id'] in self.info[a['kind']]:
                self.exact[a['kind']][a['alias']].append((a['id'], a['weight'], a['source']))
                words[a['kind']][a['id']].update(a['alias'].split())
        for kind in KINDS:
            self.df[kind].update(w for found in words[kind].values() for w in found)
        self.sorted = {kind: sorted(self.exact[kind]) for kind in KINDS}
        self.last_names = list(self.by_last)
        self.phrases = {alias: {i for i, _, _ in hits} for alias, hits in self.exact['community'].items()
                        if any(source in ('label', 'form') for _, _, source in hits)}

    def load_info(self, con):
        labels = {c['community_id']: c for c in rows(con, 'select * from dim_community')}
        label = lambda i: labels[i]['short_label'] if i in labels else None
        where = lambda city, state: ', '.join(p for p in (city, state) if p) or None
        fact = lambda name, place=None, community=None, division=None, rank=(): {
            'name': name, 'place': place, 'community_id': community, 'community': label(community),
            'division': division, 'rank': rank}
        for h in rows(con, 'select npi, name, first_name, last_name, city, state, community_id, division_group, '
                           'is_core, is_leader, pagerank from dim_hcp'):
            self.info['hcp'][h['npi']] = fact(h['name'], where(h['city'], h['state']), h['community_id'], h['division_group'],
                                              (h['is_leader'] or 0, h['is_core'] or 0, h['pagerank'] or 0.0))
            last = ' '.join(w for w in normalize(h['last_name']).split() if w not in NAME_SUFFIXES)
            if last:
                self.by_last[last].append(h['npi'])
                for part in last.split() if ' ' in last else ():
                    self.by_last[part].append(h['npi'])
            self.given[h['npi']] = [w for w in dict.fromkeys(normalize(f"{h['first_name'] or ''} {h['name'] or ''}").split())
                                    if w not in last.split()]
        for c in rows(con, 'select ccn, name, city, state, community_id, is_main_center, core_hcps, linked_hcps from dim_center'):
            self.info['center'][c['ccn']] = fact(c['name'], where(c['city'], c['state']), c['community_id'], None,
                                                 (c['is_main_center'] or 0, c['core_hcps'] or 0, c['linked_hcps'] or 0))
        for c in labels.values():
            self.info['community'][str(c['community_id'])] = fact(c['short_label'], c['region'], c['community_id'], None,
                                                                  (int(c['kind'] == 'named'), c['core_members'] or 0))
        for p in rows(con, 'select brand, generic, short_name, company_group, lymphoma_product from dim_product'):
            self.info['product'][p['brand']] = fact(f"{p['brand']} ({p['generic']}, {p['short_name']})", None, None, None,
                                                    (p['lymphoma_product'] or 0,))
        for group in manufacturer_groups(con):
            self.info['manufacturer'][group] = fact(group)
        for code, name in STATES.items():
            self.info['place'][code] = fact(name, code, rank=(2,))
        self.info['place']['USA'] = fact('United States', None, rank=(3,))
        for c in rows(con, "select city, state, sum(core_hcps) as core from dim_center where city is not null and city != '' "
                           'and state is not null group by city, state'):
            self.info['place'][f"{c['city']}, {c['state']}"] = fact(c['city'], c['state'], rank=(1, c['core'] or 0))

    # --- tiers; each returns {id: (score, matched_on)}

    def exact_hits(self, kind, texts):
        hits = {}
        for q in texts:
            for ident, weight, source in self.exact[kind].get(q, ()):
                keep(hits, ident, 100 * weight, f'exact {source}: {q}')
        return hits

    def prefix_hits(self, kind, texts):
        hits, aliases = {}, self.sorted[kind]
        for q in texts:
            if len(q) < (4 if kind == 'hcp' else 3):
                continue
            i = bisect.bisect_left(aliases, q)
            while i < len(aliases) and aliases[i].startswith(q):
                alias, i = aliases[i], i + 1
                cover = len(q) / len(alias)
                if alias == q or cover < PREFIX_MIN_COVERAGE or (' ' not in q and ' ' in alias):
                    continue
                for ident, weight, source in self.exact[kind][alias]:
                    keep(hits, ident, 100 * weight * (0.80 + 0.15 * cover), f'prefix {source}: {alias}')
        return hits

    def fuzzy_hits(self, kind, texts):
        hits, aliases = {}, self.sorted[kind]
        for q in texts:
            if len(q) < 4:
                continue
            for alias, base, _ in process.extract(q, aliases, scorer=fuzz.token_set_ratio, score_cutoff=60, limit=None):
                similar = 0.5 * base + 0.5 * max(fuzz.token_sort_ratio(q, alias), fuzz.ratio(q, alias))
                if similar < (FUZZY_MIN if min(len(q), len(alias)) >= 6 else FUZZY_MIN_SHORT):
                    continue
                for ident, weight, source in self.exact[kind][alias]:
                    keep(hits, ident, 0.9 * similar * weight, f'fuzzy {source}: {alias}')
        return hits

    def person_hits(self, texts):
        """Fuzzy tier for people: a typed word must be close to the last name and start with the same letter;
        given names then refine the score."""
        hits = {}
        for q in texts:
            words = q.split()
            spans = [(i, i + n) for n in (1, 2) for i in range(len(words) - n + 1)]
            for start, end in spans:
                last_text = ' '.join(words[start:end])
                if len(last_text) < 3:
                    continue
                rest = words[:start] + words[end:]
                for last, close, _ in process.extract(last_text, self.last_names, scorer=fuzz.ratio,
                                                      score_cutoff=LAST_NAME_MIN, limit=30):
                    if last[0] != last_text[0]:
                        continue
                    for npi in self.by_last[last]:
                        if not rest:
                            keep(hits, npi, 0.8 * close, f'fuzzy last_name: {last}')
                            continue
                        given = given_similarity(rest, self.given[npi])
                        if given >= GIVEN_NAME_MIN:
                            keep(hits, npi, 0.9 * (0.6 * close + 0.4 * given), f'fuzzy name: {" ".join(self.given[npi][:1] + [last])}')
        return hits

    def too_vague(self, kind, texts):
        """True when every word typed names more than DF_MAX entities of this kind or is a generic hospital word."""
        words = {w for q in texts for w in q.split()}
        weak = WEAK_TOKENS | CENTER_GENERIC if kind == 'center' else ()
        return bool(words) and all(self.df[kind].get(w, 0) > DF_MAX or w in weak for w in words)

    def resolve(self, kind, text):
        """Exact aliases if any, else prefix and fuzzy matches together, as [(id, score, matched_on)] best first."""
        texts = variants(kind, text)
        if not texts:
            return []
        hits = self.exact_hits(kind, texts)
        if not hits and (kind == 'hcp' or not self.too_vague(kind, texts[-1:])):
            hits = self.prefix_hits(kind, texts)
            for ident, hit in (self.person_hits(texts) if kind == 'hcp' else self.fuzzy_hits(kind, texts)).items():
                keep(hits, ident, *hit)
        hits = {i: hit for i, hit in hits.items() if hit[0] >= SCORE_FLOOR}
        if hits:
            return self.ordered(kind, hits)
        if kind == 'community':
            via = {}
            for ccn, score, matched in self.resolve('center', texts[-1]):
                community = self.info['center'][ccn]['community_id']
                if community is not None and 0.9 * score >= SCORE_FLOOR:
                    keep(via, str(community), 0.9 * score, f"center {self.info['center'][ccn]['name']} ({matched})")
            return self.ordered(kind, via)
        return []

    def ordered(self, kind, hits):
        """Best score first; ties go to leaders, then core HCPs (or main centers, larger communities), then name."""
        info = self.info[kind]
        found = sorted(hits.items(), key=lambda item: (-round(item[1][0], 1), tuple(-x for x in info[item[0]]['rank']),
                                                        info[item[0]]['name'], item[0]))
        return [(ident, score, matched) for ident, (score, matched) in found if score >= found[0][1][0] - KEEP_WINDOW]

    def result(self, kind, ident, score, matched):
        info = self.info[kind][ident]
        return {'kind': kind, 'id': ident, 'name': info['name'], 'place': info['place'], 'community_id': info['community_id'],
                'community': info['community'], 'division': info['division'], 'score': round(score, 1), 'matched_on': matched}


def keep(hits, ident, score, matched):
    if ident not in hits or score > hits[ident][0]:
        hits[ident] = (score, matched)


def given_similarity(typed, given):
    """How well the given names typed fit a person's given names, 0 to 100; initials and short forms count."""
    if not given:
        return 0.0
    total = 0.0
    for word in typed:
        best = 0.0
        for name in given:
            if word == name:
                best = 100.0
            elif len(word) == 1 and name.startswith(word):
                best = max(best, 80.0)
            elif len(word) >= 3 and name.startswith(word):
                best = max(best, 90.0)
            else:
                best = max(best, fuzz.ratio(word, name))
        total += best
    return total / len(typed)


_INDEXES, _DEFAULT = {}, []


def default_connection():
    """The built pack, opened read-only once."""
    if not _DEFAULT:
        _DEFAULT.append(connect(readonly=True))
    return _DEFAULT[0]


def index_for(con):
    """The in-memory index of a connection, built on first use."""
    if id(con) not in _INDEXES:
        _INDEXES[id(con)] = (con, Index(con))
    return _INDEXES[id(con)][1]


def forget(con=None):
    """Drop the cached index of a connection (or all of them), for example after the aliases were rebuilt."""
    if con is None:
        _INDEXES.clear()
    else:
        _INDEXES.pop(id(con), None)


# ---------------------------------------------------------------- the two tools

def find_entity(text, kind, con=None, limit=5):
    """Resolve a name the user typed to at most `limit` candidates of one kind.

    kind: hcp, center, community, product, manufacturer or place. None searches every kind (people only when the
    text starts with a title such as 'Dr.'), exact matches of any kind before approximate ones.
    Returns [{kind, id, name, place, community_id, community, division, score, matched_on}], best first. Ids are
    strings: NPI, CCN, community id, brand, manufacturer group, state code ('TX'), 'City, ST' or 'USA'. For a
    place, `place` is the state code.

    Order. Titles and noise words are removed first ('Dr.', 'MD', 'the', 'orbit', 'group', 'hospital', ...).
      1. exact alias: score = 100 x alias weight (full name 100, hand-written short form 95, last name alone 80,
         a center's city 70). If any alias matches exactly, nothing else is consulted.
      2. prefix: the text completes an alias and covers at least half of it: 87 to 95 x weight.
      3. fuzzy: token-based similarity of at least 80 (88 for strings under six characters), score = 0.9 x
         similarity x weight. For people a typed word must be within 85 of the last name and share its first
         letter, and typed given names must fit (initials and short forms count), so a title plus a last name
         never drifts to a center or to an unrelated person.
    Prefix and fuzzy candidates are pooled, each keeping its better score, so a prefix beats an equally close
    misspelling. A community lookup also accepts an institution or a leader's name, and as a last step the
    community of a center the text resolves to. Words shared by many entities ('university', 'memorial') never
    match alone.

    Floor and ambiguity. Nothing scoring under SCORE_FLOOR (60) is returned, so an unknown name gives []. Only
    candidates within KEEP_WINDOW (15) of the best are kept. Equal scores are ordered leaders first, then core
    HCPs by PageRank (main centers and larger communities for the other kinds). When the first two candidates are
    within AMBIGUITY_MARGIN (5) they are different entities that fit about equally: `ambiguous(result)` is true
    and the caller should ask the user rather than take the first.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}.")
    index = index_for(con or default_connection())
    kinds = (kind,) if kind else ('hcp',) if normalize(text).split()[:1] in (['dr'], ['doctor'], ['prof'], ['professor']) else KINDS
    found = []
    for one in kinds:
        found += [index.result(one, ident, score, matched) for ident, score, matched in index.resolve(one, text)]
    if kind is None and found:
        exact = lambda r: r['matched_on'].startswith('exact')
        found.sort(key=lambda r: (not exact(r), -r['score']))
        found = [r for r in found if exact(r) == exact(found[0]) and r['score'] >= found[0]['score'] - KEEP_WINDOW]
    return found[:max(int(limit), 0)]


def ambiguous(candidates, margin=AMBIGUITY_MARGIN):
    """True when the best two candidates of `find_entity` score within `margin` of each other."""
    return len(candidates) > 1 and candidates[0]['score'] - candidates[1]['score'] <= margin


def parse_query(query, index=None):
    """Split user text into search words and cheap facets: (words, state code, community id, hinted card types)."""
    raw = str(query or '')
    padded = f' {normalize(raw)} '
    state = community = None
    numbered = re.search(r' community (\d{1,2}) ', padded)
    if numbered:
        community, padded = numbered.group(1), padded.replace(numbered.group(0), ' ', 1)
    elif index is not None:
        found = sorted((p for p in index.phrases if f' {p} ' in padded), key=len, reverse=True)
        if found and len(index.phrases[found[0]]) == 1:
            community, padded = next(iter(index.phrases[found[0]])), padded.replace(f' {found[0]} ', ' ', 1)
    for code, name in sorted(STATES.items(), key=lambda item: -len(item[1])):
        phrase = f' {normalize(name)} '
        at = padded.find(phrase)
        if at >= 0 and not padded[at + len(phrase):].startswith(('university', 'city')):
            state, padded = code, padded.replace(phrase, ' ', 1)
            break
    if state is None:
        coded = re.search(r'(?:\b(?:in|from|near)\s+|,\s*)([A-Z]{2})\b|\b([A-Z]{2})\s*[?.!]*\s*$', raw)
        code = coded and (coded.group(1) or coded.group(2))
        if code in STATES:
            state, padded = code, re.sub(rf' {code.lower()} ', ' ', padded, count=1)
    words = [w for w in padded.split() if w not in STOPWORDS]
    return words, state, community, sorted({TYPE_HINTS[w] for w in words if w in TYPE_HINTS})


def word_forms(word):
    """A word with its plural and, for a plural, its singular."""
    return [word, word + 's'] + ([word[:-1]] if len(word) > 3 and word.endswith('s') else [])


def match_expression(words, joiner, prefix):
    """A safe FTS5 query: every word is quoted, so no operator in user text reaches the engine."""
    groups = []
    for position, word in enumerate(words):
        forms = [f'"{form}"' for form in word_forms(word)]
        if prefix and position == len(words) - 1 and len(word) >= 3:
            forms.append(f'"{word}"*')
        groups.append('(' + ' OR '.join(forms) + ')')
    return f' {joiner} '.join(groups)


def ubiquitous(con, words):
    """True when every word is printed on more than half of all cards, so BM25 cannot rank HCP cards by it."""
    if not table_exists(con, 'card_vocab'):
        return False
    total = con.execute('select count(*) from card').fetchone()[0]
    for word in words:
        forms = word_forms(word)
        most = con.execute(f"select coalesce(max(doc), 0) from card_vocab where term in ({', '.join('?' * len(forms))})", forms).fetchone()[0]
        if most * 2 <= total:
            return False
    return True


def search_cards(query, kinds=None, con=None, limit=6):
    """Full-text search over the cards; returns [{id, type, title, text (at most 400 characters), refs, score}].

    kinds: a card type or a list of them (hcp, center, community, product, product_event, manufacturer, caveat,
    glossary, inventory, paper); None searches all.

    Ranking.
      1. The query is lower-cased, accents and punctuation are removed and each remaining word is quoted, so FTS
         operators and stray quotes in user text are harmless. Question and filler words are dropped. A word
         also matches its plural or singular.
      2. Facets parsed by rule are filters, not search words: a state name, or a two-letter code after 'in' or at
         the end ('centers in TX'), keeps cards of that state; a community named with its kind word ('the Moffitt
         orbit', 'community 11') keeps that community's cards.
      3. Cards form two groups with separate indexes: reference cards (community, product, event, manufacturer,
         caveat, glossary, inventory) and entity cards (HCP, center, paper). Separate indexes let words printed on
         every HCP card (HCP, core, PageRank, community) still find the glossary. Each group is ranked by BM25
         with the title weighted 4 times the text.
      4. Three levels, best first: 2 = every word is on the card; 1 = every word, the last one as a prefix;
         0 = some of the words. A lower level is consulted only while fewer than `limit` cards were found.
      5. Inside a level the reference group comes first but leaves up to two places for the other group. A type word
         in the query (centers, hospitals, HCPs, physicians, papers, ...) is not searched as a word: it restricts
         the entity group to that type and puts it first, unless `kinds` is given. A definition question
         ('what is a ...', 'what does ... mean', 'define ...') ignores type words and is answered from the
         reference group alone when it has a match.
      6. score = level + 0.5 for the group that comes first + BM25 scaled into 0 to 0.5: the list is in score
         order.
      7. Words printed on more than half of all cards cannot rank entity cards. A query made only of such words
         searches the reference group, and inside a parsed state or community it lists that facet's matching
         cards instead (communities, then main centers and centers, then leaders and core HCPs) with score 0.
         The same listing answers when a state or community was parsed and no word matched inside it. Only
         when all of this finds nothing is a query with a type word run again as plain words.
    """
    con = con or default_connection()
    if not table_exists(con, 'card_fts'):
        return []
    index = index_for(con) if table_exists(con, 'alias') else None
    words, state, community, hinted = parse_query(query, index)
    asked = [t for t in ([kinds] if isinstance(kinds, str) else list(kinds or [])) if t in CARD_TYPES]
    defining = not asked and bool(DEFINITION.match(str(query or '')))
    hinted = [] if asked or defining else hinted
    terms = [w for w in words if w not in TYPE_HINTS] if hinted else words
    limit = max(int(limit), 0)
    groups = card_groups(con, asked, hinted, state)
    found = ranked(con, terms, groups[:1] if defining else [], state, community, limit) or ranked(con, terms, groups, state, community, limit)
    if not found and (state or community) and limit:
        listed = facet_listing(con, asked or hinted, state, community, limit, terms) or facet_listing(con, asked or hinted, state, community, limit)
        found = [(ident, 0.0) for ident in listed]
    if not found and hinted:
        found = ranked(con, words, card_groups(con, [], [], state), state, community, limit)
    cards = {c['id']: c for c in rows(con, f"select id, type, title, text, refs_json from card where id in ({', '.join('?' * len(found))})",
                                      [ident for ident, _ in found])} if found else {}
    return [{'id': ident, 'type': cards[ident]['type'], 'title': cards[ident]['title'], 'text': clip(cards[ident]['text']),
             'refs': json.loads(cards[ident]['refs_json']), 'score': round(score, 3)} for ident, score in found if ident in cards]


def card_groups(con, asked, hinted, state):
    """[(index table, card types)] to search, the group that comes first leading."""
    reference = [t for t in (asked or REFERENCE_TYPES) if t in REFERENCE_TYPES]
    entity = [t for t in (asked or ENTITY_TYPES) if t in ENTITY_TYPES]
    if hinted:
        reference = [t for t in hinted if t in REFERENCE_TYPES] or reference
        entity = [t for t in hinted if t in ENTITY_TYPES] or entity
    groups = []
    if reference and state is None and table_exists(con, 'card_fts_ref'):
        groups.append(('card_fts_ref', reference))
    if entity:
        groups.append(('card_fts', entity))
    if hinted and all(t in ENTITY_TYPES for t in hinted):
        groups.reverse()
    return groups


def facet_filter(table, types, state, community):
    clauses, params = [], []
    if types:
        clauses.append(f"{table}.type in ({', '.join('?' * len(types))})")
        params += list(types)
    for column, value in (('state', state), ('community', community)):
        if value is not None:
            clauses.append(f'{table}.{column} = ?')
            params.append(str(value))
    return ''.join(f' and {c}' for c in clauses), params


def ranked(con, words, groups, state, community, limit):
    """[(card id, score)] best first: level by level, the leading group first, with up to two places (a third
    of those still free) left for the other group."""
    groups = [g for g in groups if g[0] != 'card_fts' or not ubiquitous(con, words)] if words else []
    if not groups or not limit:
        return []
    levels = [(2, 'AND', False)]
    if len(words[-1]) >= 3:
        levels.append((1, 'AND', True))
    if len(words) > 1:
        levels.append((0, 'OR', True))
    scores = {}
    for level, joiner, prefix in levels:
        found = []
        for position, (table, kinds) in enumerate(groups):
            clause, params = facet_filter(table, kinds, state, community)
            sql = (f'select id, bm25({table}, {TITLE_WEIGHT}, 1.0) as relevance from {table} where {table} match ?{clause} '
                   'order by relevance limit ?')
            try:
                hits = con.execute(sql, [match_expression(words, joiner, prefix)] + params + [limit + len(scores)]).fetchall()
            except sqlite3.OperationalError:
                hits = []
            lift = 0.5 if position == 0 else 0.0
            found.append([(ident, level + lift + 0.5 * -relevance / (10.0 - relevance)) for ident, relevance in hits if ident not in scores])
        need = limit - len(scores)
        first, other = found[0], [hit for later in found[1:] for hit in later]
        lead = first[:need - min(2, len(other), need // 3)]
        rest = other[:need - len(lead)]
        scores.update(lead + rest + first[len(lead):need - len(rest)])
        if len(scores) >= limit:
            break
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]


def facet_listing(con, types, state, community, limit, words=None):
    """Card ids of a state or community, optionally only those holding every word: curated cards and leaders first."""
    clause, params = facet_filter('card_fts', types, state, community)
    match = 'card_fts match ?' if words else '1 = 1'
    sql = (f"select card_fts.id from card_fts join card c on c.id = card_fts.id where {match}{clause} "
           "order by case card_fts.type when 'community' then 0 when 'center' then 1 when 'hcp' then 2 else 3 end, "
           "coalesce(json_extract(c.facets_json, '$.is_main_center'), 0) desc, "
           "coalesce(json_extract(c.facets_json, '$.core_hcps'), 0) desc, "
           "coalesce(json_extract(c.facets_json, '$.is_leader'), 0) desc, "
           "coalesce(json_extract(c.facets_json, '$.is_core'), 0) desc, "
           "coalesce(json_extract(c.facets_json, '$.rank'), 999999), c.title limit ?")
    try:
        return [r[0] for r in con.execute(sql, ([match_expression(words, 'AND', False)] if words else []) + params + [limit])]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------- dev build

def timed(function, *args, repeat=200, **kwargs):
    """Result and mean milliseconds per call."""
    start = time.perf_counter()
    for _ in range(repeat):
        out = function(*args, **kwargs)
    return out, (time.perf_counter() - start) * 1000 / repeat


def main():
    """Build cards, aliases and the full-text index into agent/pack/dev_search.sqlite from the core tables."""
    path = PACK / 'dev_search.sqlite'
    for stale in (path, path.with_name(path.name + '-wal'), path.with_name(path.name + '-shm')):
        stale.unlink(missing_ok=True)
    source = connect(PACK / 'dims.sqlite', readonly=True)
    con = connect(path)
    source.backup(con)
    source.close()
    start = time.perf_counter()
    cards = build_cards(con)
    print('CARDS', write_cards(con, cards, PACK / 'dev_search_cards.jsonl'), f'{time.perf_counter() - start:.1f}s')
    start = time.perf_counter()
    print('ALIASES', build_aliases(con), f'{time.perf_counter() - start:.1f}s')
    start = time.perf_counter()
    index_for(con)
    print(f'INDEX LOAD {(time.perf_counter() - start) * 1000:.0f} ms')
    leader = rows(con, 'select last_name, first_name from dim_hcp where is_leader = 1 order by pagerank desc limit 1')
    people = [(f"Dr. {leader[0]['last_name']}", 'hcp'), (f"{leader[0]['first_name']} {leader[0]['last_name']}", 'hcp')] if leader else []
    examples = people + [('Moffitt orbit', 'community'), ('the Penn group', 'community'), ('MSK', 'center'),
                         ('MD Anderson', 'center'), ('Mayo Clinic Rochester', 'center'), ('axi-cel', 'product'),
                         ('Yescarda', 'product'), ('BMS', 'manufacturer'), ('TX', 'place'), ('Rochester', 'place')]
    for text, kind in examples:
        found, ms = timed(find_entity, text, kind, con)
        print(f'find_entity({text!r}, {kind!r}) {ms:.2f} ms ->', [(r['id'], r['name'], r['score'], r['matched_on']) for r in found])
    for query in ('myeloma-focused centers in Texas', 'what does a blank Medicare cell mean', 'leaders in the Stanford orbit'):
        found, ms = timed(search_cards, query, None, con, repeat=20)
        print(f'search_cards({query!r}) {ms:.2f} ms ->', [(r['id'], r['score']) for r in found])
    con.close()


__all__ = ['AMBIGUITY_MARGIN', 'KINDS', 'SCORE_FLOOR', 'ambiguous', 'build_aliases', 'find_entity', 'forget',
           'normalize', 'search_cards']


if __name__ == '__main__':
    main()
