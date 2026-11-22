"""Text cards: short plain-sentence descriptions the agent can find and quote.

One card per HCP, center, community, product, product event, manufacturer group, caveat, glossary term and pack
table, plus one per paper when the `pub` table exists. Cards are built from the core tables alone; the other
pack parts add sentences through their `card_facts`. Cards carry real names, so they live only in the pack
(`card` table, full-text table `card_fts`, `cards.jsonl`) and never in a tracked file.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .packbase import SNAPSHOTS
from .paths import PROMPTS

STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California', 'CO': 'Colorado',
    'CT': 'Connecticut', 'DE': 'Delaware', 'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky',
    'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire',
    'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota',
    'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island',
    'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont',
    'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
    'PR': 'Puerto Rico', 'GU': 'Guam', 'VI': 'U.S. Virgin Islands'}
CARD_TYPES = ('hcp', 'center', 'community', 'product', 'product_event', 'manufacturer', 'caveat', 'glossary',
              'inventory', 'paper')
# Cards about one person, place or paper, and the far fewer reference cards; each group has its own text index.
ENTITY_TYPES = ('hcp', 'center', 'paper')
REFERENCE_TYPES = tuple(t for t in CARD_TYPES if t not in ENTITY_TYPES)
FACT_KINDS = ('hcp', 'center', 'community', 'product', 'manufacturer')
FTS_TOKENIZER = 'unicode61 remove_diacritics 2'
FTS_PREFIX = '2 3 4'
AS_OF = max(SNAPSHOTS.values())
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')

CAVEAT_TOPICS = {
    'REFERRAL': 'referral views and shared patients', 'MEDICARE': 'Medicare figures and blanks',
    'MEDICAID': 'Medicaid figures', 'NOT_PRODUCT_SPECIFIC': 'claims not split by CAR-T product',
    'PAYMENTS': 'industry payments', 'TIES': 'disclosed ties', 'LABELS': 'division labels and locations',
    'CORE_ONLY': 'communities and PageRank'}
DIVISION_WORDS = {
    'core': 'core HCP, one of the 500 clinicians at the centre of the CAR-T lymphoma co-authorship network',
    'affiliates': 'affiliate at a CAR-T center, a hematology-oncology clinician linked to a CAR-T center who is '
                  'not one of the 500 core HCPs',
    'referring': 'referring community HCP, a hematology-oncology physician outside the CAR-T centers who shows '
                 'lymphoma treatment activity in Medicare data',
    'other': 'other hematology-oncology HCP, not linked to a CAR-T center and not in the referring group'}
BASIS_WORDS = {
    'core_member': 'as a core member', 'coauthorship': 'attached through co-authorship with its core members',
    'center': 'attached through their center',
    'referral_primary_center': 'attached through their main shared-patient center'}
MEDICARE_STATUS_WORDS = {
    'count_published': 'Medicare publishes a CAR-T stay count for this hospital for 2024.',
    'in_file_no_drg018_row': 'Medicare does not publish a CAR-T stay count for this hospital for 2024: the '
                             'hospital is in the Medicare file, so its count is 10 or fewer, possibly none. '
                             'The blank means "not shown", not zero.',
    'not_in_file': "Medicare does not publish a CAR-T stay count for this hospital: it is not in the Medicare "
                   "inpatient file, as happens for children's hospitals and for cancer hospitals paid outside "
                   'the standard system. The blank means "not shown", not zero.'}
EVENT_TYPE_WORDS = {
    'first_approval': 'a first US approval', 'new_indication': 'a new approved indication',
    'coding': 'a billing code change', 'coverage': 'a Medicare coverage decision', 'safety': 'a safety notice',
    'regulatory': 'a regulatory change'}

# Each entry: term, other words people use for it, two or three plain sentences. Nothing here describes how
# methods were tested; the agent does not discuss that.
GLOSSARY = [
    ('core HCP', 'core clinician, core member, core 500',
     'A core HCP is one of the 500 clinicians at the centre of this database: authors of CAR-T lymphoma papers '
     'matched to a United States clinician record. Communities and PageRank are defined for core HCPs only.'),
    ('affiliate', 'affiliate HCP, affiliates',
     'An affiliate is a hematology-oncology HCP linked to a CAR-T center, for example through a hospital '
     'affiliation or a practice group shared with a core HCP, who is not a core HCP. Affiliates are attached to '
     'a community through co-authorship or through their center.'),
    ('referring HCP', 'referring community HCP, referrer, community oncologist',
     'A referring HCP is a hematology-oncology physician outside the CAR-T centers who shows lymphoma treatment '
     'activity in Medicare data. The label says where they practise. It does not confirm that they refer '
     'patients for CAR-T.'),
    ('division', 'division group, HCP group',
     'A division is the group an HCP belongs to: core HCP, affiliate at a CAR-T center, referring community '
     'HCP, or other hematology-oncology HCP. Division labels and locations are 2024 facts applied to earlier '
     'years.'),
    ('community', 'co-authorship community, cluster',
     'A community is a fixed group of core HCPs who write CAR-T lymphoma papers with each other more than with '
     'others. There are 13 named communities, each named after its main institutions. Other HCPs and centers '
     'are attached to the community they are most linked to.'),
    ('orbit', 'network, group around an institution',
     'Orbit is the word used for a community centred on one institution, together with the HCPs and centers '
     'attached to it. An HCP outside the core joins an orbit through co-authorship, through their center, or '
     'through their main shared-patient center.'),
    ('PageRank', 'centrality, rank in the network',
     'PageRank is a score of how central a core HCP is in the co-authorship network. It is higher for people '
     'who publish with many well-connected co-authors. It is used here to rank core HCPs inside their '
     'community and says nothing about clinical quality.'),
    ('leader', 'leaders by PageRank, top of a community',
     'A leader is one of the five core HCPs with the highest PageRank in a named community. The word describes '
     'position in the co-authorship network only.'),
    ('CAR-T center', 'treatment center, center, hospital',
     'A CAR-T center is one of the 173 hospitals in the center set: hospitals with an accredited cellular '
     'therapy programme or with CAR-T stays published by Medicare. Centers are drawn as squares on the map.'),
    ('main center', 'anchor center, main centers of a community',
     'A main center is one of the up to three CAR-T centers with the most core HCPs in a community. Main '
     'centers are labelled on the map.'),
    ('co-authorship', 'coauthor link, shared papers, co-author',
     'Co-authorship means two HCPs are authors of the same CAR-T lymphoma paper. On the map a co-authorship '
     'link joins two HCPs and its width shows the number of shared papers. Papers run from 2017 to 2026.'),
    ('shared patients', 'referral link, shared-patient link, patient sharing',
     'Shared patients are Medicare fee-for-service patients seen by two clinicians, for any condition. They '
     'are used here as links from HCPs to centers and to core HCPs for 2017 to 2022. They are not CAR-T '
     'referrals and not confirmed referrals.'),
    ('out-of-region share', 'share from outside the region, catchment',
     "The out-of-region share is the share of a center's shared patients that comes from clinicians located "
     "outside the center's own hospital referral region. It runs from 0 to 1. A higher value means the center "
     'draws patients from further away.'),
    ('hospital referral region', 'HRR, referral region, region',
     'A hospital referral region is one of about 300 areas of the United States that describe where Medicare '
     'patients go for major hospital care. Each HCP and center is placed in one region by ZIP code. Regions '
     "are used to say whether shared patients come from inside or outside a center's area."),
    ('transfer of value', 'general payment, industry payment, consulting, speaking fee',
     'A transfer of value is a payment or benefit that a manufacturer reports giving to a clinician, such as '
     'consulting or speaking fees, meals and travel. The figures here are payments that name a CAR-T product, '
     '2019 to 2025. Amounts are as reported by manufacturers.'),
    ('research payment', 'research funding, study payment, principal investigator',
     'A research payment is money a manufacturer reports paying for a study that names the clinician as an '
     'investigator. The amount is for the whole study and is often paid to the hospital. It is not personal '
     'income.'),
    ('disclosed tie', 'conflict of interest, disclosure, COI',
     'A disclosed tie is a company named with an author in the conflict-of-interest statement of a paper. A '
     'missing statement is not evidence of no tie, and a tie to a company is not a tie to one of its CAR-T '
     'products.'),
    ('ownership interest', 'stock, equity, investment interest',
     'An ownership interest is a stake in a company, such as stock, that a manufacturer reports a physician or '
     'a close family member holds. It is reported by year, 2019 to 2025, and is separate from payments.'),
    ('Medicare Part B', 'Part B, outpatient drugs, infusion billing',
     'Medicare Part B pays for doctor services and for drugs given in a clinic or outpatient department, such '
     'as infusions. The database holds Part B payment totals per HCP and billing of lymphoma drugs and '
     'infusion services, 2017 to 2024.'),
    ('Medicare Part D', 'Part D, prescriptions, oral drugs',
     'Medicare Part D pays for prescription drugs that patients take at home. The database holds Part D '
     'prescribing of oral lymphoma drugs per HCP, 2017 to 2024.'),
    ('Medicaid', 'Medicaid paid amounts',
     'Medicaid is the public health insurance for people with low income, run by each state. The database '
     'holds Medicaid paid amounts per HCP for 2018 to 2024. They are lower bounds from one federal file and '
     'cannot be compared across states.'),
    ('MS-DRG 018', 'DRG 018, CAR-T inpatient stays, hospital CAR-T volume',
     'MS-DRG 018 is the Medicare inpatient payment group for CAR-T hospital stays, in use since October 2020. '
     'Counts of stays in this group are the hospital CAR-T volume shown here, 2021 to 2024. The group covers '
     'all CAR-T products together.'),
    ('fee-for-service', 'FFS, traditional Medicare, Medicare Advantage',
     'Fee-for-service Medicare is the traditional programme in which Medicare pays each claim directly. People '
     'in Medicare Advantage plans are not in these figures, so the counts cover only part of Medicare '
     'patients.'),
    ('suppression below 11', 'suppressed cell, blank cell, small numbers, not shown',
     'Medicare does not publish a count of 1 to 10 patients, to protect privacy. Such a cell is left blank. A '
     'blank means "not shown", not zero.'),
    ('CAR-T', 'CAR T-cell therapy, chimeric antigen receptor, cell therapy',
     "CAR-T is a treatment in which a patient's own T cells are collected, changed in a laboratory to "
     'recognise cancer cells, and given back by infusion. It is given at specialised centers. The approved '
     'CAR-T products are listed in this database with their approval dates.'),
]


def rows(con, sql, params=()):
    """Query rows as dicts, whatever row factory the connection uses."""
    cursor = con.execute(sql, params)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def table_exists(con, name):
    return con.execute("select 1 from sqlite_master where type in ('table', 'view') and name = ?", (name,)).fetchone() is not None


def state_name(code):
    return STATES.get((code or '').upper())


def place_words(city, state):
    """'Tampa, Florida', 'Florida' or None."""
    name = state_name(state) or (state if state and state not in ('XX', 'ZZ') else None)
    return ', '.join(part for part in (city, name) if part) or None


def place_short(city, state):
    """'Tampa FL' for titles."""
    return ' '.join(part for part in (city, state if state not in ('XX', 'ZZ') else None) if part) or None


def tidy_credentials(text):
    """'M.D., PH.D.' -> 'MD, PhD'; blank stays blank."""
    text = re.sub(r'\.', '', (text or '').upper())
    for split, joined in (('M D', 'MD'), ('D O', 'DO'), ('PH D', 'PHD'), ('M B B S', 'MBBS')):
        text = re.sub(rf'\b{split}\b', joined, text)
    parts = [p for p in re.split(r'[,;/\s]+', text) if p]
    seen = list(dict.fromkeys('PhD' if p == 'PHD' else p for p in parts))
    return ', '.join(seen[:4]) or None


def long_date(text):
    """'2017-10-18' -> '18 October 2017'; '2024-01' -> 'January 2024'."""
    parts = (text or '').split('-')
    try:
        if len(parts) == 3:
            return f'{int(parts[2])} {MONTHS[int(parts[1]) - 1]} {parts[0]}'
        if len(parts) == 2:
            return f'{MONTHS[int(parts[1]) - 1]} {parts[0]}'
    except (ValueError, IndexError):
        pass
    return text or 'date not recorded'


def listing(items):
    """'a', 'a and b', 'a, b and c'."""
    items = [str(i) for i in items if i]
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1] if items else ''


def count_words(n, one, many):
    return f'no {many}' if not n else f'{n:,} {one if n == 1 else many}'


def card(kind, key, title, text, facets=None, refs=None, sources=()):
    return {'id': f'{kind}:{key}', 'type': kind, 'title': title, 'text': ' '.join(text.split()),
            'facets': {k: v for k, v in (facets or {}).items() if v is not None}, 'refs': refs or {},
            'sources': list(sources), 'as_of': AS_OF}


def caveat_sentences(path=None):
    """The caveat sentences exactly as the system prompt gives them, keyed by code."""
    text = Path(path or PROMPTS / 'system_prompt.md').read_text(encoding='utf-8')
    found = {}
    for code, body in re.findall(r'^- `([A-Z_]+)`: (.+?)(?=^- `|^\s*$)', text, flags=re.M | re.S):
        found[code] = ' '.join(body.split())
    return found


def community_phrase(h, community):
    """How an HCP or center relates to its community, in words."""
    if community is None:
        return 'Community: not attached to any community.'
    name = f"{community['short_label']} (community {community['community_id']})"
    basis = BASIS_WORDS.get(h['community_basis'], 'attached to it')
    if community['kind'] != 'named':
        tail = 'a core HCP not connected to the 13 named communities' if h['community_basis'] == 'core_member' else basis
        return f'Community: outside the main network (community {community["community_id"]}), {tail}.'
    return f'Community: {name}, {basis}.'


def hcp_cards(con, communities, centers):
    sizes = {c['community_id']: c['core_members'] for c in communities.values()}
    out = []
    for h in rows(con, 'select * from dim_hcp order by npi'):
        community, center = communities.get(h['community_id']), centers.get(h['primary_center_ccn'])
        credentials = tidy_credentials(h['credentials'])
        where = place_words(h['city'], h['state'])
        if where and not h['city']:
            where += ' (city not recorded)'
        text = [f"{h['name']}{', ' + credentials + ',' if credentials else ''} is an HCP based in "
                f"{where or 'a place that is not recorded'}.",
                f"Specialty: {(h['specialty'] or 'not recorded').lower()}.",
                f"Division: {DIVISION_WORDS.get(h['division_group'], 'not recorded')}.",
                community_phrase(h, community)]
        if center:
            text.append(f"Primary center: {center['name']} in {place_words(center['city'], center['state'])}.")
        else:
            text.append('Primary center: none recorded.')
        if h['is_core'] and community and community['kind'] == 'named':
            leader = 'one of its five leaders by PageRank' if h['is_leader'] else 'not among its top five'
            text.append(f"PageRank rank: {h['pagerank_rank']} of {sizes[h['community_id']]} core members in this "
                        f'community; {leader}.')
        elif h['is_core']:
            text.append('PageRank is defined, but a top five is named only in the 13 named communities.')
        else:
            text.append('PageRank: not defined, because PageRank covers core HCPs only.')
        if h['is_core'] and h['institution'] and not h['institution'].lower().startswith('other'):
            text.append(f"Institution in the network: {h['institution']}.")
        tag = community['short_label'] if community else 'no community'
        title = ', '.join(part for part in (h['name'], place_short(h['city'], h['state'])) if part) + f' ({tag})'
        out.append(card('hcp', h['npi'], title, ' '.join(text),
                        {'state': h['state'], 'community': h['community_id'], 'division': h['division_group'],
                         'is_core': h['is_core'], 'is_leader': h['is_leader'], 'rank': h['pagerank_rank']},
                        {'npi': h['npi'], 'ccn': h['primary_center_ccn']}, ['dim_hcp', 'dim_center', 'dim_community']))
    return out


def center_cards(con, communities, centers):
    out = []
    for c in centers.values():
        community = communities.get(c['community_id'])
        text = [f"{c['name']} is a CAR-T center in {place_words(c['city'], c['state']) or 'a place that is not recorded'}."]
        if community and community['kind'] == 'named':
            text.append(f"Community: {community['short_label']} (community {community['community_id']}), the "
                        'community of most of its core HCPs.')
        elif community:
            text.append(f"Community: outside the main network (community {community['community_id']}); its core "
                        'HCPs are not connected to the 13 named communities.')
        else:
            text.append('Community: not attached to any community, because it has no core HCP.')
        linked = count_words(c['linked_hcps'], 'HCP is', 'HCPs are')
        text.append(f"It has {count_words(c['core_hcps'], 'core HCP', 'core HCPs')}. "
                    + ('No HCP in the database is linked to it.' if not c['linked_hcps'] else
                       f'{linked[0].upper() + linked[1:]} linked to it in total, core HCPs included.'))
        if community:
            text.append('It is one of the main centers of its community.' if c['is_main_center'] else
                        'It is not among the three centers with the most core HCPs in its community.')
        text.append('Accreditation: on the FACT list of accredited cellular therapy programmes.' if c['fact_accredited']
                    else 'Accreditation: none recorded on the cellular therapy accreditation list used here.')
        text.append(MEDICARE_STATUS_WORDS.get(c['medicare_file_status_2024'], 'Medicare file status: not recorded.'))
        if c['pps_exempt_cancer_hospital']:
            text.append("It is one of the cancer hospitals paid outside Medicare's standard inpatient system.")
        tag = community['short_label'] if community else 'no community'
        title = ', '.join(part for part in (c['name'], place_short(c['city'], c['state'])) if part) + f' ({tag})'
        out.append(card('center', c['ccn'], title, ' '.join(text),
                        {'state': c['state'], 'community': c['community_id'], 'is_main_center': c['is_main_center'],
                         'core_hcps': c['core_hcps']},
                        {'ccn': c['ccn']}, ['dim_center', 'dim_community']))
    return out


def community_cards(con, communities, centers):
    leaders, main = defaultdict(list), defaultdict(list)
    for h in rows(con, 'select community_id, name, institution, pagerank_rank from dim_hcp where is_leader = 1 '
                       'order by community_id, pagerank_rank'):
        leaders[h['community_id']].append(f"{h['pagerank_rank']}. {h['name']}" + (f" ({h['institution']})" if h['institution'] else ''))
    for c in sorted(centers.values(), key=lambda c: (-c['core_hcps'], c['name'])):
        if c['is_main_center'] and c['community_id'] is not None:
            main[c['community_id']].append(f"{c['name']} ({place_short(c['city'], c['state'])})")
    named = [c for c in communities.values() if c['kind'] == 'named']
    out = []
    for c in named:
        i = c['community_id']
        text = [f"{c['short_label']} is community {i}, one of the {len(named)} named co-authorship communities of the core HCPs.",
                f"Full label: {c['label']}." if c['label'] != c['short_label'] else '',
                f"Region: {c['region']}." if c['region'] else '',
                f"It has {count_words(c['core_members'], 'core member', 'core members')}. "
                f"{c['attached_hcps']:,} HCPs are attached to it in total, core members included, and it has "
                f"{count_words(c['centers'], 'CAR-T center', 'CAR-T centers')}.",
                f"Leaders by PageRank: {'; '.join(leaders[i])}." if leaders[i] else '',
                f"Main centers: {'; '.join(main[i])}." if main[i] else 'Main centers: none recorded.']
        out.append(card('community', i, f"{c['short_label']} (community {i})", ' '.join(t for t in text if t),
                        {'community': i, 'region': c['region']}, {'community_id': i}, ['dim_community', 'dim_hcp', 'dim_center']))
    isolated = [c for c in communities.values() if c['kind'] != 'named']
    if isolated:
        ids = sorted(c['community_id'] for c in isolated)
        places = listing(sorted({c['top_institution'] for c in isolated if c['top_institution']}))
        text = (f"Outside main network covers {count_words(sum(c['core_members'] for c in isolated), 'core HCP', 'core HCPs')} "
                f"not connected to the {len(named)} named communities. They form {len(isolated)} small separate "
                f"groups, with community ids {listing(ids)}. {sum(c['attached_hcps'] for c in isolated):,} HCPs are "
                f"attached to these groups in total, core HCPs included, and they have "
                f"{count_words(sum(c['centers'] for c in isolated), 'CAR-T center', 'CAR-T centers')}. No leaders "
                f"are named for these groups. Institutions: {places}.")
        out.append(card('community', 'isolated', 'Outside main network (isolated core HCPs)', text, {},
                        {'community_ids': ids}, ['dim_community', 'dim_hcp']))
    return out


def product_cards(con):
    events = defaultdict(list)
    for e in rows(con, 'select * from product_event where brand is not null order by date'):
        events[e['brand']].append(e)
    out, groups, partnered = [], defaultdict(list), defaultdict(list)
    for p in rows(con, 'select * from dim_product order by first_us_approval, brand'):
        first = [e for e in events[p['brand']] if e['event_type'] == 'first_approval']
        unverified = not first or not all(e['verified'] for e in first)
        later = [f"{e['label']} ({long_date(e['date'])}{'' if e['verified'] else ', date not yet verified'})"
                 for e in events[p['brand']] if e['event_type'] != 'first_approval']
        text = [f"{p['brand']} ({p['generic']}; short name {p['short_name']}) is a CAR-T product of {p['company_group']}"
                + (f", with partner {p['partner']}." if p['partner'] else '.'),
                f"Target: {p['target']}." if p['target'] else '',
                'It is one of the lymphoma products.' if p['lymphoma_product'] else 'It is not one of the lymphoma products.',
                f"Medicare billing code: {p['hcpcs_code']}." if p['hcpcs_code'] else 'Medicare billing code: not recorded.',
                f"First US approval: {long_date(p['first_us_approval'])}{' (date not yet verified)' if unverified else ''}.",
                f"Later events: {'; '.join(later)}." if later else '']
        out.append(card('product', p['brand'], f"{p['brand']} ({p['generic']}, {p['short_name']})", ' '.join(t for t in text if t),
                        {'brand': p['brand'], 'manufacturer': p['company_group'], 'lymphoma': p['lymphoma_product']},
                        {'brand': p['brand']}, ['dim_product', 'product_event']))
        groups[p['company_group']].append(p)
        if p['partner']:
            partnered[p['partner']].append(p)
    members_of = lambda group: [m.strip() for m in group.split('/') if m.strip()]
    for group, products in groups.items():
        members = members_of(group)
        described = [f"{p['brand']} ({p['short_name']}; target {p['target']}; "
                     f"{'a lymphoma product' if p['lymphoma_product'] else 'not a lymphoma product'}; first US approval "
                     f"{long_date(p['first_us_approval'])})" for p in products]
        partners = [f"{p['partner']} on {p['brand']}" for p in products if p['partner']]
        text = [f'{group} is a manufacturer group' + (f' covering the companies {listing(members)}.' if len(members) > 1 else '.'),
                f"It has {count_words(len(products), 'CAR-T product', 'CAR-T products')} in this database: {'; '.join(described)}.",
                f"Partner companies: {'; '.join(partners)}." if partners else '']
        out.append(card('manufacturer', group, f'{group} (manufacturer)', ' '.join(t for t in text if t),
                        {'manufacturer': group}, {'manufacturer': group, 'brands': [p['brand'] for p in products]}, ['dim_product']))
    for group, products in partnered.items():
        if group in groups:
            continue
        members = members_of(group)
        text = [f'{group} is a manufacturer group' + (f' covering the companies {listing(members)}.' if len(members) > 1 else '.'),
                'It has no CAR-T product of its own in this database.',
                'It is the partner company on ' + listing(f"{p['brand']}, a product of {p['company_group']}" for p in products) + '.']
        out.append(card('manufacturer', group, f'{group} (manufacturer, partner company)', ' '.join(text), {'manufacturer': group},
                        {'manufacturer': group, 'brands': [], 'partner_brands': [p['brand'] for p in products]}, ['dim_product']))
    return out


def event_cards(con):
    out, seen = [], Counter()
    for e in rows(con, 'select * from product_event order by date, brand, label'):
        key = f"{e['brand'] or 'class'}:{e['date']}"
        seen[key] += 1
        key += f':{seen[key]}' if seen[key] > 1 else ''
        kind = EVENT_TYPE_WORDS.get(e['event_type'], 'an event')
        text = [f"{e['label']}.", f"Date: {long_date(e['date'])}{'' if e['verified'] else ' (date not yet verified)'}.",
                f"This is {kind} for {e['brand']}." if e['brand'] else f'This is {kind} that applies across CAR-T products.',
                ('It concerns lymphoma.' if e['lymphoma'] else 'It does not concern lymphoma.') if e['brand'] else '']
        out.append(card('product_event', key, f"{e['label']} ({e['date']})", ' '.join(t for t in text if t),
                        {'brand': e['brand'], 'year': int(e['date'][:4]) if (e['date'] or '')[:4].isdigit() else None,
                         'event_type': e['event_type'], 'verified': e['verified']},
                        {'brand': e['brand'], 'date': e['date']}, ['product_event']))
    return out


def caveat_cards(sentences=None):
    sentences = caveat_sentences() if sentences is None else sentences
    missing = [code for code in CAVEAT_TOPICS if code not in sentences]
    if missing:
        raise ValueError(f'The system prompt has no caveat sentence for: {", ".join(missing)}')
    return [card('caveat', code, f'Caveat on {topic} ({code})', sentences[code], {'code': code}, {'code': code},
                 ['agent/prompts/system_prompt.md']) for code, topic in CAVEAT_TOPICS.items()]


def glossary_cards(con):
    brands = rows(con, 'select brand, lymphoma_product from dim_product order by first_us_approval, brand')
    lymphoma = [b['brand'] for b in brands if b['lymphoma_product']]
    others = [b['brand'] for b in brands if not b['lymphoma_product']]
    entries = list(GLOSSARY)
    if lymphoma:
        entries.append(('lymphoma products', 'lymphoma CAR-T, CD19 lymphoma products',
                        f'The lymphoma products are the CAR-T products approved for lymphoma: {listing(lymphoma)}. '
                        + (f'The other CAR-T products in the database, {listing(others)}, are shown only when the user '
                           'asks about all CAR-T products or names them.' if others else '')))
    return [card('glossary', re.sub(r'[^a-z0-9]+', '-', term.lower()).strip('-'), f'{term} (also: {also})', text, {},
                 {'term': term}, ['agent/PLAN.md']) for term, also, text in entries]


def inventory_cards(con):
    if not table_exists(con, 'pack_table'):
        return []
    out = []
    for t in rows(con, 'select * from pack_table order by name'):
        text = [f"Table {t['name']}: {t['description']}", f"One row per {t['grain']}." if t['grain'] else '',
                f"Years: {t['years']}." if t['years'] else '', f"Rows: {t['rows']:,}." if t['rows'] is not None else '',
                f"Built from: {t['sources']}." if t['sources'] else '']
        out.append(card('inventory', t['name'], f"Data table {t['name']} ({t['grain']})", ' '.join(x for x in text if x),
                        {}, {'table': t['name']}, ['pack_table']))
    return out


def paper_cards(con):
    """One card per paper when the `pub` table exists with a title; otherwise nothing."""
    if not table_exists(con, 'pub'):
        return []
    names = [r['name'] for r in rows(con, 'pragma table_info(pub)')]
    key = next((n for n in ('pmid', 'pub_id', 'paper_id', 'id') if n in names), names[0] if names else None)
    if key is None or 'title' not in names:
        return []
    journal, year = ('journal' if 'journal' in names else 'null'), ('year' if 'year' in names else 'null')
    out = []
    for p in rows(con, f'select "{key}" as k, title, {journal} as journal, {year} as year from pub where title is not null order by 1'):
        where = ' '.join(part for part in (f"in {p['journal']}" if p['journal'] else '', f"in {p['year']}" if p['year'] else '') if part)
        text = f"{p['title'].rstrip('.')}. " + (f'Paper published {where}.' if where else 'Journal and year not recorded.')
        out.append(card('paper', p['k'], p['title'][:200], text, {'year': p['year']}, {key: str(p['k'])}, ['pub']))
    return out


def add_facts(cards, facts, fact_sources=None):
    """Append the other parts' sentences to the matching cards. Isolated community ids share one card."""
    if not facts:
        return 0
    by_id = {c['id']: c for c in cards}
    isolated = {str(i) for i in by_id.get('community:isolated', {}).get('refs', {}).get('community_ids', [])}
    added = 0
    for (kind, ident), sentences in facts.items():
        ident = 'isolated' if kind == 'community' and str(ident) in isolated else str(ident)
        target = by_id.get(f'{kind}:{ident}') if kind in FACT_KINDS else None
        clean = [s.strip() if s.strip().endswith(('.', '!', '?')) else s.strip() + '.' for s in sentences or [] if s and s.strip()]
        if target is None or not clean:
            continue
        target['text'] = ' '.join([target['text']] + clean)
        target['sources'] = list(dict.fromkeys(target['sources'] + list((fact_sources or {}).get(kind, []))))
        added += len(clean)
    return added


def build_cards(con, facts=None, fact_sources=None):
    """All cards as dicts {id, type, title, text, facets, refs, sources, as_of}.

    `facts` is the merged `card_facts` of the other pack parts, {(kind, id): [sentence, ...]}; the sentences are
    appended to the matching card. `fact_sources` optionally maps a kind to the tables those sentences came
    from, so the card can cite them.
    """
    communities = {c['community_id']: c for c in rows(con, 'select * from dim_community')}
    centers = {c['ccn']: c for c in rows(con, 'select * from dim_center order by ccn')}
    cards = (hcp_cards(con, communities, centers) + center_cards(con, communities, centers)
             + community_cards(con, communities, centers) + product_cards(con) + event_cards(con)
             + caveat_cards() + glossary_cards(con) + inventory_cards(con) + paper_cards(con))
    add_facts(cards, facts, fact_sources)
    return cards


def write_cards(con, cards, jsonl_path=None):
    """Store cards in `card`, index title and text in `card_fts`, and write one JSON object per line.

    `card_fts` covers every card; `card_fts_ref` repeats the reference cards alone, so that their ranking is not
    drowned by words printed on every HCP card. Both keep id, type, state and community as unindexed columns
    so a search can filter without a join. `card_vocab` counts the cards each word appears on.
    """
    assert len({c['id'] for c in cards}) == len(cards), 'Duplicate card id'
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    con.execute('drop table if exists card')
    con.execute('create table card (id text primary key, type text, title text, text text, facets_json text, '
                'refs_json text, sources_json text, as_of text)')
    con.executemany('insert into card values (?, ?, ?, ?, ?, ?, ?, ?)',
                    ((c['id'], c['type'], c['title'], c['text'], dump(c['facets']), dump(c['refs']), dump(c['sources']), c['as_of'])
                     for c in cards))
    for table, chosen in (('card_fts', cards), ('card_fts_ref', [c for c in cards if c['type'] in REFERENCE_TYPES])):
        con.execute(f'drop table if exists {table}')
        con.execute(f"create virtual table {table} using fts5(title, text, id unindexed, type unindexed, state unindexed, "
                    f"community unindexed, tokenize = '{FTS_TOKENIZER}', prefix = '{FTS_PREFIX}')")
        con.executemany(f'insert into {table} (title, text, id, type, state, community) values (?, ?, ?, ?, ?, ?)',
                        ((c['title'], c['text'], c['id'], c['type'], c['facets'].get('state'),
                          None if c['facets'].get('community') is None else str(c['facets']['community'])) for c in chosen))
    con.execute('drop table if exists card_vocab')
    con.execute("create virtual table card_vocab using fts5vocab(card_fts, 'row')")
    con.execute('create index ix_card_type on card (type)')
    con.commit()
    if jsonl_path:
        path = Path(jsonl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as stream:
            for c in cards:
                stream.write(dump(c) + '\n')
    return dict(Counter(c['type'] for c in cards))


__all__ = ['AS_OF', 'CARD_TYPES', 'ENTITY_TYPES', 'REFERENCE_TYPES', 'STATES', 'add_facts', 'build_cards', 'caveat_sentences', 'rows', 'state_name',
           'table_exists', 'write_cards']
