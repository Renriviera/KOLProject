"""Entity resolution, text cards and card search on tiny invented core tables. No real person appears here."""
import json
import sqlite3
import time

import pytest

from kolproject.agent import cards, search
from kolproject.agent.packbase import create_table, register
from kolproject.agent.paths import PACK

HCP_COLUMNS = [('npi', 'text'), ('name', 'text'), ('first_name', 'text'), ('last_name', 'text'), ('credentials', 'text'),
               ('city', 'text'), ('state', 'text'), ('zip5', 'text'), ('hrr', 'text'), ('lat', 'real'), ('lon', 'real'),
               ('geo_precision', 'text'), ('division', 'text'), ('division_group', 'text'), ('is_core', 'int'),
               ('community_id', 'int'), ('community_basis', 'text'), ('pagerank', 'real'), ('pagerank_rank', 'int'),
               ('is_leader', 'int'), ('network_role', 'text'), ('institution', 'text'), ('core_publications', 'int'),
               ('primary_center_ccn', 'text'), ('specialty', 'text')]
CENTER_COLUMNS = [('ccn', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('zip5', 'text'), ('hrr', 'text'),
                  ('lat', 'real'), ('lon', 'real'), ('geo_precision', 'text'), ('community_id', 'int'),
                  ('community_basis', 'text'), ('core_hcps', 'int'), ('linked_hcps', 'int'), ('is_main_center', 'int'),
                  ('fact_accredited', 'int'), ('class_340b', 'text'), ('pps_exempt_cancer_hospital', 'int'),
                  ('medicare_file_status_2024', 'text')]
COMMUNITY_COLUMNS = [('community_id', 'int'), ('label', 'text'), ('short_label', 'text'), ('kind', 'text'),
                     ('color_light', 'text'), ('color_dark', 'text'), ('region', 'text'), ('top_institution', 'text'),
                     ('core_members', 'int'), ('attached_hcps', 'int'), ('centers', 'int')]
PRODUCT_COLUMNS = [('brand', 'text'), ('generic', 'text'), ('short_name', 'text'), ('company_group', 'text'),
                   ('partner', 'text'), ('target', 'text'), ('lymphoma_product', 'int'), ('hcpcs_code', 'text'),
                   ('first_us_approval', 'text')]
EVENT_COLUMNS = [('date', 'text'), ('brand', 'text'), ('event_type', 'text'), ('label', 'text'), ('lymphoma', 'int'),
                 ('verified', 'int'), ('source_url', 'text')]


def hcp(npi, first, last, group, community, basis, state='FL', city='Portview', core=0, rank=None, leader=0, center=None,
        name=None, credentials='M.D.', specialty='Hematology/Oncology', institution=None):
    division = {'core': 'core', 'affiliates': 'affiliate_A', 'referring': 'referring_proxy', 'other': 'hemonc_other'}[group]
    return {'npi': npi, 'name': name or f'{first} {last}', 'first_name': first, 'last_name': last, 'credentials': credentials,
            'city': city, 'state': state, 'zip5': None, 'hrr': None, 'lat': None, 'lon': None, 'geo_precision': None,
            'division': division, 'division_group': group, 'is_core': core, 'community_id': community,
            'community_basis': basis, 'pagerank': 0.02 / rank if rank else None, 'pagerank_rank': rank, 'is_leader': leader,
            'network_role': None, 'institution': institution, 'core_publications': 5 if core else None,
            'primary_center_ccn': center, 'specialty': specialty}


def center(ccn, name, city, state, community, core, linked, main=0, fact=1, status='count_published', exempt=0):
    return {'ccn': ccn, 'name': name, 'city': city, 'state': state, 'zip5': None, 'hrr': None, 'lat': None, 'lon': None,
            'geo_precision': None, 'community_id': community, 'community_basis': 'core_majority' if community else 'unassigned',
            'core_hcps': core, 'linked_hcps': linked, 'is_main_center': main, 'fact_accredited': fact, 'class_340b': None,
            'pps_exempt_cancer_hospital': exempt, 'medicare_file_status_2024': status}


HCPS = [
    hcp('9000000001', 'Mara', 'Quillon', 'core', 1, 'core_member', core=1, rank=1, leader=1, center='990001',
        name='Mara Elise Quillon', credentials='M.D., PH.D.', institution='Halden'),
    hcp('9000000002', 'Ilse', 'Brandtner', 'core', 1, 'core_member', core=1, rank=2, center='990001', institution='Halden'),
    hcp('9000000003', 'Devin', 'Quillon', 'referring', 1, 'referral_primary_center', state='GA', city='Marlow', center='990001'),
    hcp('9000000004', 'Oskar', 'Lindqvist-Varga', 'core', 2, 'core_member', state='MN', city='Rochester', core=1, rank=1,
        leader=1, center='990002', institution='Northgate'),
    hcp('9000000005', 'Petra', 'Halden', 'affiliates', 1, 'center', center='990001'),
    hcp('9000000006', 'Rune', 'Aldecott', 'core', 14, 'core_member', state='NY', city='Gotham', core=1, rank=1,
        institution='Lakeshore Institute'),
    hcp('9000000007', 'Brisa', 'Montclair', 'other', None, 'unassigned', state='OH', city=None, credentials=None, specialty=None),
    hcp('9000000008', 'Yara', 'Ostrander', 'affiliates', 2, 'coauthorship', state='MN', city='Rochester', center='990002'),
    hcp('9000000009', 'Tomas', 'Verhoeven', 'referring', 2, 'referral_primary_center', state='MN', city='Rochester', center='990002'),
    hcp('9000000010', 'Tomas', 'Verhoeven', 'other', None, 'unassigned', state='TX', city='Marlow'),
    hcp('9000000011', 'José', 'Núñez-Peralta', 'affiliates', 2, 'center', state='NY', city='Gotham', center='990004'),
    hcp('9000000012', 'Selma', 'Kestrel', 'core', 2, 'core_member', state='NY', city='Gotham', core=1, rank=2, center='990004',
        institution='Riverton'),
]
CENTERS = [
    center('990001', 'Halden Cancer Center And Research Institute, Inc.', 'Portview', 'FL', 1, 2, 4, main=1, status='not_in_file', exempt=1),
    center('990002', 'Northgate University Hospital', 'Rochester', 'MN', 2, 1, 3, main=1),
    center('990003', 'Riverton Memorial Hospital', 'Rochester', 'NY', None, 0, 0, fact=0, status='in_file_no_drg018_row'),
    center('990004', 'Memorial Hospital For Cancer And Allied Diseases', 'Gotham', 'NY', 2, 1, 2),
    center('990005', "Summit Children's Hospital", 'Marlow', 'TX', None, 0, 1, status=None),
    center('990006', 'Quillonby Medical Center', 'Marlow', 'TX', None, 0, 1, status='in_file_no_drg018_row'),
]
COMMUNITIES = [
    (1, 'Halden orbit', 'Halden orbit', 'named', '#111111', '#222222', 'South', 'Halden', 2, 4, 1),
    (2, 'Northgate – Riverton network (Midwest)', 'Northgate – Riverton network', 'named', '#333333', '#444444', 'Midwest', 'Northgate', 2, 5, 2),
    (3, 'Loose Riverton – Eastfield network', 'Riverton – Eastfield network', 'named', '#555555', '#666666', 'Northeast', 'Eastfield', 0, 0, 0),
    (14, 'Outside main network', 'Outside main network', 'isolated', '#777777', '#888888', 'Northeast', 'Lakeshore Institute', 1, 1, 0),
]
PRODUCTS = [
    ('Zelvora', 'zelvocabtagene autoleucel', 'zelvo-cel', 'Arbor/Helix', 'Tidewater Bio', 'CD19', 1, 'Q9001', '2019-03-04'),
    ('Quantra', 'quantacabtagene ciloleucel', 'quanta-cel', 'Bristol Myers Squibb/Celgene/Juno', None, 'BCMA', 0, None, '2022-02-02'),
]
EVENTS = [
    ('2019-03-04', 'Zelvora', 'first_approval', 'Zelvora approved (large B-cell lymphoma)', 1, 1, 'https://example.org/a'),
    ('2020-10', None, 'coding', 'New inpatient payment group for CAR-T', 0, 0, None),
    ('2021-06-15', 'Zelvora', 'new_indication', 'Zelvora: follicular lymphoma', 1, 0, None),
    ('2022-02-02', 'Quantra', 'first_approval', 'Quantra approved (multiple myeloma)', 0, 0, None),
]


def core_tables(con):
    create_table(con, 'dim_community', COMMUNITY_COLUMNS, COMMUNITIES, primary_key=['community_id'])
    create_table(con, 'dim_hcp', HCP_COLUMNS, HCPS, primary_key=['npi'])
    create_table(con, 'dim_center', CENTER_COLUMNS, CENTERS, primary_key=['ccn'])
    create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')], [(h['npi'], h['primary_center_ccn']) for h in HCPS if h['primary_center_ccn']])
    create_table(con, 'dim_product', PRODUCT_COLUMNS, PRODUCTS, primary_key=['brand'])
    create_table(con, 'product_event', EVENT_COLUMNS, EVENTS)
    register(con, 'dim_hcp', 'HCP', ['synthetic/hcps'], 'Every invented HCP with division and community.', '2024 snapshot')
    register(con, 'dim_center', 'center', ['synthetic/centers'], 'The invented CAR-T centers.', '2024 snapshot')
    con.commit()


@pytest.fixture(scope='module')
def pack(tmp_path_factory):
    folder = tmp_path_factory.mktemp('pack')
    con = sqlite3.connect(folder / 'test.sqlite')
    con.row_factory = sqlite3.Row
    core_tables(con)
    built = cards.build_cards(con)
    counts = cards.write_cards(con, built, folder / 'cards.jsonl')
    aliases = search.build_aliases(con)
    yield {'con': con, 'cards': {c['id']: c for c in built}, 'counts': counts, 'aliases': aliases, 'folder': folder}
    search.forget(con)
    con.close()


def ids(found):
    return [r['id'] for r in found]


# ---------------------------------------------------------------- cards

def test_card_types_counts_and_shape(pack):
    assert pack['counts'] == {'hcp': 12, 'center': 6, 'community': 4, 'product': 2, 'manufacturer': 3, 'product_event': 4,
                              'caveat': 8, 'glossary': len(cards.GLOSSARY) + 1, 'inventory': 2}
    assert pack['counts']['glossary'] >= 25
    for card in pack['cards'].values():
        assert set(card) == {'id', 'type', 'title', 'text', 'facets', 'refs', 'sources', 'as_of'}
        assert card['id'].startswith(card['type'] + ':') and card['title'] and card['text'] and card['sources']


def test_hcp_card_wording(pack):
    leader = pack['cards']['hcp:9000000001']['text']
    assert leader.startswith('Mara Elise Quillon, MD, PhD, is an HCP based in Portview, Florida.')
    assert 'Specialty: hematology/oncology.' in leader and 'Division: core HCP,' in leader
    assert 'Community: Halden orbit (community 1), as a core member.' in leader
    assert 'Primary center: Halden Cancer Center And Research Institute, Inc. in Portview, Florida.' in leader
    assert 'PageRank rank: 1 of 2 core members in this community; one of its five leaders by PageRank.' in leader
    second = pack['cards']['hcp:9000000002']['text']
    assert 'PageRank rank: 2 of 2' in second and 'not among its top five' in second and 'leader' not in second
    referring = pack['cards']['hcp:9000000003']['text']
    assert 'Division: referring community HCP,' in referring
    assert 'attached through their main shared-patient center' in referring
    assert 'PageRank: not defined, because PageRank covers core HCPs only.' in referring
    assert 'attached through their center' in pack['cards']['hcp:9000000005']['text']
    assert 'attached through co-authorship' in pack['cards']['hcp:9000000008']['text']
    isolated = pack['cards']['hcp:9000000006']['text']
    assert 'outside the main network (community 14)' in isolated and 'Primary center: none recorded.' in isolated
    assert pack['cards']['hcp:9000000001']['facets'] == {'state': 'FL', 'community': 1, 'division': 'core', 'is_core': 1,
                                                          'is_leader': 1, 'rank': 1}


def test_hcp_card_blanks_are_said_not_guessed(pack):
    blank = pack['cards']['hcp:9000000007']
    assert blank['text'].startswith('Brisa Montclair is an HCP based in Ohio (city not recorded).')
    assert 'Specialty: not recorded.' in blank['text']
    assert 'Community: not attached to any community.' in blank['text'] and 'Primary center: none recorded.' in blank['text']
    assert 'community' not in blank['facets'] and blank['title'] == 'Brisa Montclair, OH (no community)'


def test_hcp_cards_stay_between_40_and_90_words(pack):
    lengths = [len(c['text'].split()) for c in pack['cards'].values() if c['type'] == 'hcp']
    assert min(lengths) >= 40 and max(lengths) <= 90


def test_center_card_blank_versus_zero(pack):
    main = pack['cards']['center:990001']['text']
    assert 'is a CAR-T center in Portview, Florida.' in main and 'It has 2 core HCPs. 4 HCPs are linked to it in total' in main
    assert 'It is one of the main centers of its community.' in main and 'on the FACT list' in main
    assert 'it is not in the Medicare inpatient file' in main and 'paid outside Medicare' in main
    assert 'Medicare publishes a CAR-T stay count for this hospital for 2024.' in pack['cards']['center:990002']['text']
    empty = pack['cards']['center:990003']['text']
    assert 'Community: not attached to any community, because it has no core HCP.' in empty
    assert 'It has no core HCPs. No HCP in the database is linked to it.' in empty
    assert 'Medicare does not publish a CAR-T stay count for this hospital for 2024' in empty
    assert 'The blank means "not shown", not zero.' in empty and 'Accreditation: none recorded' in empty
    assert 'main centers' not in empty
    assert 'Medicare file status: not recorded.' in pack['cards']['center:990005']['text']
    assert '1 HCP is linked to it in total' in pack['cards']['center:990005']['text']
    assert 'not among the three centers' in pack['cards']['center:990004']['text']


def test_community_cards(pack):
    named = pack['cards']['community:1']
    assert named['title'] == 'Halden orbit (community 1)'
    assert 'It has 2 core members. 4 HCPs are attached to it in total, core members included, and it has 1 CAR-T center.' in named['text']
    assert 'Leaders by PageRank: 1. Mara Elise Quillon (Halden).' in named['text']
    assert 'Main centers: Halden Cancer Center And Research Institute, Inc. (Portview FL).' in named['text']
    assert 'Full label: Loose Riverton – Eastfield network.' in pack['cards']['community:3']['text']
    isolated = pack['cards']['community:isolated']
    assert isolated['refs'] == {'community_ids': [14]} and 'covers 1 core HCP not connected' in isolated['text']
    assert 'community:14' not in pack['cards']


def test_product_event_and_manufacturer_cards(pack):
    verified = pack['cards']['product:Zelvora']['text']
    assert 'Zelvora (zelvocabtagene autoleucel; short name zelvo-cel) is a CAR-T product of Arbor/Helix, with partner Tidewater Bio.' in verified
    assert 'It is one of the lymphoma products.' in verified and 'Medicare billing code: Q9001.' in verified
    assert 'First US approval: 4 March 2019.' in verified
    assert 'Zelvora: follicular lymphoma (15 June 2021, date not yet verified)' in verified
    draft = pack['cards']['product:Quantra']['text']
    assert 'First US approval: 2 February 2022 (date not yet verified).' in draft
    assert 'It is not one of the lymphoma products.' in draft and 'Medicare billing code: not recorded.' in draft
    assert 'date not yet verified' not in pack['cards']['product_event:Zelvora:2019-03-04']['text']
    assert 'Date: 15 June 2021 (date not yet verified).' in pack['cards']['product_event:Zelvora:2021-06-15']['text']
    coding = pack['cards']['product_event:class:2020-10']['text']
    assert 'Date: October 2020 (date not yet verified).' in coding and 'applies across CAR-T products' in coding
    maker = pack['cards']['manufacturer:Arbor/Helix']
    assert 'covering the companies Arbor and Helix' in maker['text'] and 'Zelvora (zelvo-cel; target CD19; a lymphoma product' in maker['text']
    assert 'Partner companies: Tidewater Bio on Zelvora.' in maker['text'] and maker['refs']['brands'] == ['Zelvora']
    partner = pack['cards']['manufacturer:Tidewater Bio']
    assert partner['text'] == ('Tidewater Bio is a manufacturer group. It has no CAR-T product of its own in this database. '
                               'It is the partner company on Zelvora, a product of Arbor/Helix.')
    assert partner['refs'] == {'manufacturer': 'Tidewater Bio', 'brands': [], 'partner_brands': ['Zelvora']}


def test_caveat_cards_repeat_the_system_prompt_exactly(pack):
    sentences = cards.caveat_sentences()
    assert set(cards.CAVEAT_TOPICS) <= set(sentences)
    for code in cards.CAVEAT_TOPICS:
        assert pack['cards'][f'caveat:{code}']['text'] == sentences[code]
    assert sentences['MEDICAID'] == 'Medicaid figures are lower bounds from one federal file and cannot be compared across states.'
    with pytest.raises(ValueError):
        cards.caveat_cards({'REFERRAL': 'Only one.'})


def test_glossary_cards_are_short_and_avoid_method_talk(pack):
    glossary = [c for c in pack['cards'].values() if c['type'] == 'glossary']
    for card in glossary:
        assert 2 <= len([s for s in card['text'].split('. ') if s.strip()]) <= 4, card['id']
        assert not any(word in card['text'].lower() for word in ('stability', 'validat', 'backtest', 'robust'))
    assert 'Zelvora' in pack['cards']['glossary:lymphoma-products']['text'] and 'Quantra' in pack['cards']['glossary:lymphoma-products']['text']


def test_inventory_cards_and_absent_paper_table(pack):
    inventory = pack['cards']['inventory:dim_hcp']
    assert 'Every invented HCP' in inventory['text'] and 'One row per HCP.' in inventory['text'] and 'Rows: 12.' in inventory['text']
    assert not any(c['type'] == 'paper' for c in pack['cards'].values())


def test_facts_are_appended_and_paper_cards_appear(tmp_path):
    con = sqlite3.connect(tmp_path / 'facts.sqlite')
    core_tables(con)
    con.execute('create table pub (pmid text primary key, title text, journal text, year integer)')
    con.execute("insert into pub values ('70000001', 'An invented trial of cell therapy', 'Journal of Examples', 2021)")
    con.execute("insert into pub values ('70000002', 'A second invented paper.', null, null)")
    facts = {('hcp', '9000000001'): ['Named on 3 invented papers from 2019 to 2021', 'Received 2 payments in 2023.'],
             ('community', '14'): ['One isolated fact.'], ('center', '990001'): ['Had 12 stays in 2023.'],
             ('product', 'Zelvora'): ['National spending fact.'], ('manufacturer', 'Arbor/Helix'): ['Paid 5 HCPs in 2023.'],
             ('hcp', 'missing'): ['Ignored.'], ('place', 'TX'): ['Ignored too.']}
    built = {c['id']: c for c in cards.build_cards(con, facts, {'hcp': ['pub', 'pay_general_year']})}
    assert built['hcp:9000000001']['text'].endswith('Named on 3 invented papers from 2019 to 2021. Received 2 payments in 2023.')
    assert built['hcp:9000000001']['sources'][-2:] == ['pub', 'pay_general_year']
    assert built['community:isolated']['text'].endswith('One isolated fact.')
    assert built['center:990001']['text'].endswith('Had 12 stays in 2023.')
    assert built['product:Zelvora']['text'].endswith('National spending fact.')
    assert built['manufacturer:Arbor/Helix']['text'].endswith('Paid 5 HCPs in 2023.')
    assert built['paper:70000001']['text'] == 'An invented trial of cell therapy. Paper published in Journal of Examples in 2021.'
    assert built['paper:70000002']['text'] == 'A second invented paper. Journal and year not recorded.'
    con.close()


def test_write_cards_tables_and_jsonl(pack):
    con = pack['con']
    assert con.execute('select count(*) from card').fetchone()[0] == len(pack['cards'])
    assert [r[1] for r in con.execute('pragma table_info(card)')] == ['id', 'type', 'title', 'text', 'facets_json', 'refs_json',
                                                                      'sources_json', 'as_of']
    assert con.execute('select count(*) from card_fts').fetchone()[0] == len(pack['cards'])
    assert con.execute("select count(*) from card_fts_ref where type in ('hcp', 'center')").fetchone()[0] == 0
    assert con.execute("select id from card_fts where card_fts match 'nunez'").fetchall()[0][0] == 'hcp:9000000011'
    lines = (pack['folder'] / 'cards.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(lines) == len(pack['cards']) and json.loads(lines[0])['id'] == 'hcp:9000000001'


# ---------------------------------------------------------------- find_entity

def test_alias_table(pack):
    con = pack['con']
    assert set(pack['aliases']) == set(search.KINDS)
    assert [r[1] for r in con.execute('pragma table_info(alias)')] == ['alias', 'kind', 'id', 'weight', 'source']
    assert con.execute('select count(*) from alias where alias != lower(alias)').fetchone()[0] == 0
    weights = dict(con.execute("select source, max(weight) from alias where kind = 'hcp' group by source"))
    assert weights['full_name'] == 1.0 and weights['last_name'] == 0.8 and weights['last_name_part'] == 0.7


def test_person_exact_forms_and_titles(pack):
    con = pack['con']
    for text in ('Mara Elise Quillon', 'Mara Quillon', 'Quillon, Mara', 'Dr. Mara Quillon, MD', 'dr mara quillon', 'Prof. Mara Quillon PhD'):
        found = search.find_entity(text, 'hcp', con)
        assert ids(found) == ['9000000001'] and found[0]['score'] == 100.0, text
    top = search.find_entity('Mara Quillon', 'hcp', con)[0]
    assert top == {'kind': 'hcp', 'id': '9000000001', 'name': 'Mara Elise Quillon', 'place': 'Portview, FL', 'community_id': 1,
                   'community': 'Halden orbit', 'division': 'core', 'score': 100.0, 'matched_on': 'exact first_last: mara quillon'}


def test_shared_last_name_returns_both_core_first(pack):
    found = search.find_entity('Dr. Quillon', 'hcp', pack['con'])
    assert ids(found) == ['9000000001', '9000000003'] and found[0]['score'] == found[1]['score'] == 80.0
    assert search.ambiguous(found)
    assert not search.ambiguous(search.find_entity('Devin Quillon', 'hcp', pack['con']))


def test_same_full_name_on_two_people_is_ambiguous(pack):
    found = search.find_entity('Tomas Verhoeven', 'hcp', pack['con'])
    assert sorted(ids(found)) == ['9000000009', '9000000010'] and search.ambiguous(found)
    assert {r['place'] for r in found} == {'Rochester, MN', 'Marlow, TX'}


def test_person_prefix_fuzzy_and_parts(pack):
    con = pack['con']
    prefix = search.find_entity('Brandtn', 'hcp', con)
    assert ids(prefix) == ['9000000002'] and prefix[0]['matched_on'].startswith('prefix last_name')
    assert ids(search.find_entity('Ilse Brandt', 'hcp', con)) == ['9000000002']
    fuzzy = search.find_entity('Mara Quilon', 'hcp', con)
    assert ids(fuzzy)[0] == '9000000001' and fuzzy[0]['matched_on'].startswith('fuzzy') and 60 <= fuzzy[0]['score'] < 100
    assert ids(search.find_entity('M Quillon', 'hcp', con))[0] == '9000000001'
    assert ids(search.find_entity('Brandtnar', 'hcp', con)) == ['9000000002']
    assert ids(search.find_entity('Jose Nunez-Peralta', 'hcp', con)) == ['9000000011']
    assert ids(search.find_entity('Nunez', 'hcp', con)) == ['9000000011']
    assert ids(search.find_entity('Varga', 'hcp', con)) == ['9000000004']
    assert ids(search.find_entity('Lindqvist Varga', 'hcp', con)) == ['9000000004']


def test_weak_or_unknown_people_return_nothing(pack):
    con = pack['con']
    for text in ('Zebulon Farnsworth', 'Walter Quillon', 'Mara', 'Qui', 'Dr.', '', '   ', 'Uillon'):
        assert search.find_entity(text, 'hcp', con) == [], text


def test_kind_filter_and_title_keep_people_apart_from_centers(pack):
    con = pack['con']
    assert ids(search.find_entity('Halden', 'hcp', con)) == ['9000000005']
    assert ids(search.find_entity('Halden', 'center', con)) == ['990001']
    assert ids(search.find_entity('Halden', 'community', con)) == ['1']
    assert search.find_entity('Halden', 'product', con) == [] and search.find_entity('Halden', 'place', con) == []
    assert {r['kind'] for r in search.find_entity('Dr. Quillon', None, con)} == {'hcp'}
    anything = search.find_entity('Quillon', None, con)
    assert [r['kind'] for r in anything[:2]] == ['hcp', 'hcp'] and 'center' not in {r['kind'] for r in anything}
    assert ids(search.find_entity('Quillonby', 'center', con)) == ['990006']
    assert [r['kind'] for r in search.find_entity('Halden', None, con)][:2] == ['community', 'center']
    with pytest.raises(ValueError):
        search.find_entity('Halden', 'drug', con)
    assert len(search.find_entity('Quillon', 'hcp', con, limit=1)) == 1


def test_center_names_short_forms_and_noise(pack):
    con = pack['con']
    for text in ('Halden Cancer Center and Research Institute, Inc.', 'Halden Cancer Center And Research Institute',
                 'the Halden hospital', 'Halden Cancer Center', 'Halden Portview'):
        assert ids(search.find_entity(text, 'center', con)) == ['990001'], text
    assert ids(search.find_entity('MSK', 'center', con)) == ['990004']
    assert ids(search.find_entity('Memorial Sloan Kettering', 'center', con)) == ['990004']
    typo = search.find_entity('Sloan Ketering', 'center', con)
    assert ids(typo) == ['990004'] and typo[0]['matched_on'].startswith('fuzzy')
    assert ids(search.find_entity('Northgate', 'center', con)) == ['990002']
    assert ids(search.find_entity('Northg', 'center', con)) == ['990002']
    assert ids(search.find_entity("Summit Childrens", 'center', con)) == ['990005']
    both = search.find_entity('Rochester', 'center', con)
    assert sorted(ids(both)) == ['990002', '990003'] and search.ambiguous(both) and ids(both)[0] == '990002'
    for vague in ('University', 'Memorial', 'the hospital', 'cancer center', 'Zzyzx Clinic'):
        assert search.find_entity(vague, 'center', con) == [], vague


def test_community_by_label_institution_leader_and_center(pack):
    con = pack['con']
    for text in ('Halden orbit', 'the Halden group', 'Halden network', 'the Halden community', 'halden', 'Haldn orbit'):
        assert ids(search.find_entity(text, 'community', con)) == ['1'], text
    assert ids(search.find_entity('Northgate – Riverton network', 'community', con)) == ['2']
    assert ids(search.find_entity('the Northgate group', 'community', con)) == ['2']
    shared = search.find_entity('the Riverton group', 'community', con)
    assert sorted(ids(shared)) == ['2', '3'] and search.ambiguous(shared)
    assert ids(search.find_entity("Quillon's group", 'community', con)) == ['1']
    assert ids(search.find_entity('Mara Quillon', 'community', con)) == ['1']
    via = search.find_entity('the MSK group', 'community', con)
    assert ids(via) == ['2'] and via[0]['matched_on'].startswith('center Memorial Hospital')
    assert search.find_entity('the Quillonby group', 'community', con) == []
    assert search.find_entity('Brandtner', 'community', con) == []
    assert search.find_entity('outside main network', 'community', con)[0]['id'] == '14'


def test_products_short_names_and_misspellings(pack):
    con = pack['con']
    for text in ('Zelvora', 'zelvocabtagene autoleucel', 'zelvo-cel', 'zelvo cel', 'zelvocel', 'Zelvo-Cel', 'zelvocabtagene',
                 'Q9001', 'Zelvora CAR-T', 'Zelvara', 'Zelvra', 'zelvocabtagen autoleucel', 'Zelv'):
        assert ids(search.find_entity(text, 'product', con)) == ['Zelvora'], text
    assert search.find_entity('zelvo-cel', 'product', con)[0]['score'] == 100.0
    assert search.find_entity('Zelvara', 'product', con)[0]['matched_on'] == 'fuzzy brand: zelvora'
    for text in ('cel', 'CAR-T', 'Fakedrugimab', 'autoleucel'):
        assert search.find_entity(text, 'product', con) == [], text


def test_manufacturer_groups_members_and_partners(pack):
    con = pack['con']
    for text in ('Arbor', 'Helix', 'Arbor/Helix', 'Helix Pharmaceuticals', 'the Helix company'):
        assert ids(search.find_entity(text, 'manufacturer', con)) == ['Arbor/Helix'], text
    for text in ('Tidewater', 'Tidewater Bio', 'Tidewatr Bio'):
        assert ids(search.find_entity(text, 'manufacturer', con)) == ['Tidewater Bio'], text
    for text in ('BMS', 'Bristol-Myers Squibb', 'Bristol', 'Celgene', 'Juno Therapeutics', 'Bristol Meyers Squib'):
        assert ids(search.find_entity(text, 'manufacturer', con)) == ['Bristol Myers Squibb/Celgene/Juno'], text
    assert search.find_entity('Tidewater', 'manufacturer', con)[0]['matched_on'] == 'exact member_company: tidewater'
    assert search.find_entity('Acme Pharma', 'manufacturer', con) == []


def test_places(pack):
    con = pack['con']
    for text in ('Texas', 'TX', 'tx', 'state of Texas', 'Texs'):
        found = search.find_entity(text, 'place', con)
        assert (found[0]['id'], found[0]['name'], found[0]['place']) == ('TX', 'Texas', 'TX'), text
    city = search.find_entity('Portview', 'place', con)
    assert [(r['id'], r['name'], r['place']) for r in city] == [('Portview, FL', 'Portview', 'FL')]
    assert ids(search.find_entity('Portview, FL', 'place', con)) == ['Portview, FL']
    assert ids(search.find_entity('Rochester, Minnesota', 'place', con)) == ['Rochester, MN']
    both = search.find_entity('Rochester', 'place', con)
    assert sorted(ids(both)) == ['Rochester, MN', 'Rochester, NY'] and search.ambiguous(both)
    assert ids(search.find_entity('MD', 'place', con)) == ['MD']
    assert ids(search.find_entity('nationwide', 'place', con)) == ['USA']
    assert search.find_entity('Narnia', 'place', con) == []


def test_lookup_is_fast(pack):
    con = pack['con']
    search.find_entity('Mara Quillon', 'hcp', con)
    start = time.perf_counter()
    for _ in range(200):
        search.find_entity('Mara Quilon', 'hcp', con)
        search.find_entity('the Halden group', 'community', con)
    assert (time.perf_counter() - start) / 400 < 0.005


# ---------------------------------------------------------------- search_cards

def test_search_cards_shape_and_reference_first(pack):
    found = search.search_cards('what does PageRank mean', None, pack['con'])
    assert found and all(set(r) == {'id', 'type', 'title', 'text', 'refs', 'score'} for r in found)
    assert 'glossary:pagerank' in ids(found) and all(r['type'] not in ('hcp', 'center') for r in found)
    assert [r['score'] for r in found] == sorted((r['score'] for r in found), reverse=True)
    assert ids(search.search_cards('what is a core HCP', None, pack['con']))[0] == 'glossary:core-hcp'
    assert ids(search.search_cards('why is a Medicare cell blank', None, pack['con']))[0] in ('glossary:suppression-below-11', 'caveat:MEDICARE')
    assert all(len(r['text']) <= 400 for r in search.search_cards('community', None, pack['con'], limit=20))


def test_search_cards_finds_entities_and_prefixes(pack):
    con = pack['con']
    assert ids(search.search_cards('Brandtner', None, con)) == ['hcp:9000000002']
    assert ids(search.search_cards('Núñez', None, con)) == ['hcp:9000000011']
    assert ids(search.search_cards('Brandt', None, con)) == ['hcp:9000000002']
    assert ids(search.search_cards('zelv', None, con))[0].split(':')[1] == 'Zelvora'
    found = search.search_cards('Halden', None, con)
    assert found[0]['id'] == 'community:1' and 'center:990001' in ids(found)
    assert search.search_cards('Halden', None, con, limit=2)[0]['refs'] == {'community_id': 1}


def test_search_cards_kinds_and_facets(pack):
    con = pack['con']
    assert {r['type'] for r in search.search_cards('Halden', ['center'], con)} == {'center'}
    assert {r['type'] for r in search.search_cards('Halden', 'hcp', con)} == {'hcp'}
    assert ids(search.search_cards('centers in Florida', None, con)) == ['center:990001']
    assert ids(search.search_cards('myeloma-focused centers in TX', None, con)) == ['center:990006', 'center:990005']
    assert ids(search.search_cards('hospitals, NY', None, con)) == ['center:990004', 'center:990003']
    leaders = search.search_cards('leaders in the Halden orbit', None, con)
    assert ids(leaders) == ['community:1', 'hcp:9000000001']
    assert ids(search.search_cards('community 2 leaders', None, con)) == ['community:2', 'hcp:9000000004']
    listed = search.search_cards('who is in the Halden orbit', None, con)
    assert ids(listed)[:3] == ['community:1', 'center:990001', 'hcp:9000000001'] and listed[0]['score'] == 0.0
    assert search.parse_query('the Northgate group in Minnesota', search.index_for(con))[1:3] == ('MN', '2')
    assert search.parse_query('Washington University papers')[1] is None and search.parse_query('payments in OR')[1] == 'OR'


@pytest.mark.parametrize('query', [
    '', '   ', '"', '""', "'; drop table card; --", 'NEAR(a b)', 'a AND', 'OR', 'NOT', 'title: x', '^halden', 'halden*', '* *',
    '(((', 'a) OR (b', 'col:"x', 'x" OR "y', 'héllo – wörld…', 'NEAR/3', '{braces}', 'a\x00b', 'AND OR NOT', 'tab\tand\nnewline',
    '100% of "centers" + (hospitals) - [TX]', 'x' * 500])
def test_search_cards_never_raises_on_operators_or_punctuation(pack, query):
    found = search.search_cards(query, None, pack['con'])
    assert isinstance(found, list)
    assert pack['con'].execute('select count(*) from card').fetchone()[0] == len(pack['cards'])


def test_search_cards_without_index_or_matches(pack, tmp_path):
    assert search.search_cards('zzzz qqqq', None, pack['con']) == []
    assert search.search_cards('Halden', None, pack['con'], limit=0) == []
    bare = sqlite3.connect(tmp_path / 'bare.sqlite')
    core_tables(bare)
    assert search.search_cards('Halden', None, bare) == []
    assert search.find_entity('Halden', 'center', bare) == []
    search.forget(bare)
    bare.close()


# ---------------------------------------------------------------- the real dev file, when it exists

def test_real_dev_pack_when_present():
    path = PACK / 'dev_search.sqlite'
    if not path.exists():
        pytest.skip('agent/pack/dev_search.sqlite has not been built')
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        if not cards.table_exists(con, 'alias') or not cards.table_exists(con, 'card_fts_ref'):
            pytest.skip('dev file was built by an older version')
        count = lambda sql: con.execute(sql).fetchone()[0]
        assert count("select count(*) from card where type = 'hcp'") == count('select count(*) from dim_hcp')
        assert count("select count(*) from card where type = 'center'") == count('select count(*) from dim_center')
        assert count("select count(*) from card where type = 'caveat'") == 8
        assert count("select count(*) from card where trim(coalesce(text, '')) = ''") == 0
        expected = {('Moffitt orbit', 'community'): '11', ('the Penn group', 'community'): '4', ('MD Anderson', 'community'): '9',
                    ('Yescarta', 'product'): 'Yescarta', ('axi-cel', 'product'): 'Yescarta', ('liso cel', 'product'): 'Breyanzi',
                    ('tisagenlecleucel', 'product'): 'Kymriah', ('Kite', 'manufacturer'): 'Kite/Gilead', ('Texas', 'place'): 'TX',
                    ('TX', 'place'): 'TX', ('BMS', 'manufacturer'): 'Bristol Myers Squibb/Celgene/Juno'}
        for (text, kind), ident in expected.items():
            assert search.find_entity(text, kind, con)[0]['id'] == ident, text
        for text in ('MSK', 'MD Anderson', 'Stanford', 'City of Hope', 'Mayo Clinic Rochester', 'Brigham'):
            found = search.find_entity(text, 'center', con)
            assert len(found) == 1 and found[0]['score'] >= 90, text
        assert search.find_entity('Zzyzx Qwertyuiop', 'hcp', con) == []
        start = time.perf_counter()
        for text, kind in expected:
            search.find_entity(text, kind, con)
        assert (time.perf_counter() - start) / len(expected) < 0.005
        for query in ('centers in Texas', 'what is a core HCP', 'leaders in the Stanford orbit', '"quoted" AND (broken'):
            assert isinstance(search.search_cards(query, None, con), list)
        assert search.search_cards('what is a core HCP', None, con)[0]['id'] == 'glossary:core-hcp'
        assert {r['type'] for r in search.search_cards('centers in Texas', None, con)} == {'center'}
    finally:
        search.forget(con)
        con.close()
