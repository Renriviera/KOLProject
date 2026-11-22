"""Number checking, prompt rendering, query validation and map selection for the agent tool (no model, no pack)."""
import sqlite3

import pytest

from kolproject.agent import mapdata, prompt, queries, verify
from kolproject.agent.packbase import create_table

RESULT = {'row_count': 3, 'rows': [
    {'year': 2023, 'usd': 4944607.6, 'hcps': 200, 'share': 0.546, 'name': 'Group A'},
    {'year': 2024, 'usd': 727654.8, 'hcps': 148, 'share': 0.49, 'name': 'Group B'},
    {'year': 2025, 'usd': 1200000.0, 'hcps': 338, 'share': None, 'name': 'Group C'}]}


@pytest.mark.parametrize('sentence', [
    'Group A received $4.9 million across 200 people.',
    'That is 54.6% of the total, up from 49%.',
    'In total about $6.9 million was reported.',
    'Group C has 190 more people than Group B.',
    'The three rows cover 2023 to 2025.',
    'The 13 named communities hold 500 core HCPs.',
])
def test_numbers_backed_by_results_are_accepted(sentence):
    assert verify.unsupported(sentence, [RESULT]) == []


@pytest.mark.parametrize('sentence, bad', [
    ('Payments reached $9.9 million in 2024.', '$9.9 million'),
    ('Roughly 1,250 people were paid.', '1,250'),
    ('The group received $48,000 on average.', '$48,000'),
])
def test_numbers_no_result_supports_are_flagged(sentence, bad):
    assert verify.unsupported(sentence, [RESULT]) == [bad]


def test_clean_text_drops_only_the_unsupported_sentence_and_handles_no_results():
    text, dropped = verify.clean_text('Group A received $4.9 million. Another $12.3 million went elsewhere. See the table.', [RESULT])
    assert text == 'Group A received $4.9 million. See the table.' and dropped == ['Another $12.3 million went elsewhere.']
    text, dropped = verify.clean_text('There were 4,321 payments.', [])
    assert text == '' and len(dropped) == 1
    assert verify.clean_text('That is not in the database.', []) == ('That is not in the database.', [])
    listed, dropped = verify.clean_text('Three groups:\n- Group A received $4.9 million.\n- Group Z received $77 million.\n- Group B had 148 people.', [RESULT])
    assert listed == 'Three groups:\n- Group A received $4.9 million.\n- Group B had 148 people.' and dropped == ['- Group Z received $77 million.']


def test_a_date_in_a_result_backs_the_day_and_month_written_from_it():
    events = {'row_count': 2, 'rows': [{'date': '2022-06-24', 'label': 'Second line'}, {'date': '2024-03-14', 'label': 'Third line'}]}
    assert verify.unsupported('Approved on June 24, 2022 and again on March 14, 2024.', [events]) == []
    assert verify.unsupported('Approved on June 27, 2022.', [events]) == ['27']


def test_system_prompt_fills_every_slot_from_the_reference_files():
    text = prompt.system_prompt()
    assert '{{' not in text
    for brand in queries.BRANDS:
        assert brand in text
    for label in ('Moffitt orbit', 'Penn / Abramson orbit', 'Baylor orbit'):
        assert label in text
    for skill in prompt.skills():
        assert f'`{skill}`' in text
    assert 'conferences' not in prompt.skills() and len(prompt.skills()) == 9
    assert 'not CAR-T referrals' in text and 'commercial-insurance' in text
    assert 'stability' in text and 'Method checks are not part of this tool' in text  # the refusal rule, not the data


def test_only_verified_product_events_reach_the_prompt(monkeypatch):
    data = {'products': [{'brand': 'Kymriah', 'generic': 'g', 'short_name': 's', 'company_group': 'c', 'target': 't', 'lymphoma_product': True,
                          'hcpcs_code': 'Q1', 'first_us_approval': '2017-08-30',
                          'events': [{'date': '2017-08-30', 'type': 'first_approval', 'label': 'Kymriah approved', 'verified': True},
                                     {'date': '2030-01-01', 'type': 'new_indication', 'label': 'Unconfirmed event', 'verified': False}]}],
            'class_and_coding_events': [], 'pending': []}
    monkeypatch.setattr(prompt, 'product_file', lambda: (data, 'cart_products.json'))
    _, events = prompt.product_tables()
    assert 'Kymriah approved' in events and 'Unconfirmed' not in events
    monkeypatch.setattr(prompt, 'product_file', lambda: (data, 'cart_products.draft.json'))
    assert 'Unconfirmed' in prompt.product_tables()[1]  # a draft is shown whole so development can proceed


def test_every_skill_names_only_known_caveats_and_default_links():
    for name, skill in prompt.skills().items():
        assert set(skill['meta'].get('caveats', [])) <= set(prompt.CAVEATS), name
        assert skill['meta'].get('default_edges') in ('coauthor', 'referral', 'payments', 'none'), name


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(queries, 'REGISTRY', {})
    monkeypatch.setattr(queries, 'PARTS', [])

    @queries.query('demo_by_year', 'Demo rows by year.', {'scope': 'scope', 'id': 'id', 'rule': {'type': 'string', 'enum': ['a', 'b'], 'default': 'a'},
                                                          'n': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 5}},
                   [('year', 'Year', 'year'), ('usd', 'Dollars', 'usd')], skills=['industry-payments'], default_panel={'type': 'line', 'x': 'year', 'y': ['usd']},
                   caveats=['PAYMENTS'], tables=['demo'], years='2019 to 2025')
    def demo(con, scope, id, rule, n):
        return [{'year': 2024, 'usd': 10.0, 'extra': 'dropped'}][:n]
    return queries.REGISTRY


def connection():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    create_table(con, 'dim_community', [('community_id', 'int'), ('label', 'text'), ('short_label', 'text'), ('color_light', 'text'), ('color_dark', 'text')],
                 [(1, 'Alpha orbit', 'Alpha orbit', '#111111', '#222222'), (2, 'Beta orbit', 'Beta orbit', '#333333', '#444444')])
    create_table(con, 'dim_hcp', [(c, k) for c, k in [('npi', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('lat', 'real'), ('lon', 'real'),
                                                      ('division_group', 'text'), ('is_core', 'int'), ('community_id', 'int'), ('community_basis', 'text'),
                                                      ('pagerank', 'real'), ('pagerank_rank', 'int'), ('is_leader', 'int'), ('primary_center_ccn', 'text')]], [
        ('1000000001', 'Ada Example', 'Tampa', 'FL', 27.95, -82.46, 'core', 1, 1, 'core_member', 0.02, 1, 1, '100001'),
        ('1000000002', 'Ben Example', 'Tampa', 'FL', 27.95, -82.46, 'core', 1, 1, 'core_member', 0.01, 2, 0, '100001'),
        ('1000000003', 'Cy Example', 'Boston', 'MA', 42.36, -71.06, 'core', 1, 2, 'core_member', 0.03, 1, 1, '220001'),
        ('1000000004', 'Di Example', 'Orlando', 'FL', 28.54, -81.38, 'referring', 0, 1, 'referral_primary_center', None, None, 0, '100001'),
        ('1000000005', 'Ed Example', 'Miami', 'FL', None, None, 'referring', 0, None, 'unassigned', None, None, 0, None)])
    create_table(con, 'dim_center', [('ccn', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('lat', 'real'), ('lon', 'real'), ('community_id', 'int'),
                                     ('core_hcps', 'int'), ('linked_hcps', 'int'), ('is_main_center', 'int')],
                 [('100001', 'Alpha Cancer Center', 'Tampa', 'FL', 27.96, -82.45, 1, 2, 40, 1), ('220001', 'Beta Hospital', 'Boston', 'MA', 42.34, -71.1, 2, 1, 30, 1)])
    create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')], [('1000000001', '100001'), ('1000000002', '100001'), ('1000000003', '220001')])
    create_table(con, 'coauthor_pair', [('npi_a', 'text'), ('npi_b', 'text'), ('shared_publications', 'int')],
                 [('1000000001', '1000000002', 4), ('1000000001', '1000000003', 1)])
    create_table(con, 'ref_link_year', [('npi', 'text'), ('ccn', 'text'), ('year', 'int'), ('patients', 'int')],
                 [('1000000004', '100001', 2022, 40), ('1000000005', '100001', 2022, 15), ('1000000004', '100001', 2019, 22)])
    return con


def test_query_parameters_are_validated_with_messages_the_model_can_use(registry):
    con = connection()
    result = queries.run('demo_by_year', {'scope': 'community', 'id': '1'}, con=con, skill='industry-payments')
    assert result['params'] == {'scope': 'community', 'id': '1', 'rule': 'a', 'n': 5}
    assert result['rows'] == [{'year': 2024, 'usd': 10.0}] and result['caveats'] == ['PAYMENTS']
    assert result['scope_label'] == 'Alpha orbit (core members and attached HCPs)'
    core = queries.run('demo_by_year', {'scope': 'community_core', 'id': '1'}, con=con)
    assert core['scope_label'] == 'Alpha orbit (core members only)'
    for params, words in [({'scope': 'community'}, 'needs the parameter id'), ({'scope': 'galaxy', 'id': '1'}, 'must be one of'),
                          ({'scope': 'community', 'id': '1', 'n': 99}, 'between 1 and 50'), ({'scope': 'community', 'id': '1', 'colour': 'x'}, 'Unknown parameter'),
                          ({'scope': 'community', 'id': '77'}, 'Call find_entity first')]:
        with pytest.raises(queries.QueryError, match=words):
            queries.run('demo_by_year', params, con=con)
    with pytest.raises(queries.QueryError, match='not part of the skill'):
        queries.run('demo_by_year', {'scope': 'community', 'id': '1'}, con=con, skill='referral-flow')
    with pytest.raises(queries.QueryError, match='no query named'):
        queries.run('drop_tables', {}, con=con)


def spec(kind, ids, edges='none', show='focus_only', **extra):
    return {'focus': {'kind': kind, 'ids': ids}, 'show': show, 'edges': edges, 'highlight': 'none', **extra}


def test_a_community_focus_keeps_only_its_core_members_and_centers():
    con = connection()
    data = mapdata.view(con, spec('community', ['1'], 'coauthor', highlight='leaders'))
    assert {p['id'] for p in data['points']} == {'hcp:1000000001', 'hcp:1000000002', 'center:100001'}
    assert data['links'] == [{'source': 'hcp:1000000001', 'target': 'hcp:1000000002', 'weight': 4}]
    assert [p['id'] for p in data['points'] if p['label']] == ['hcp:1000000001']
    assert [c['community_id'] for c in data['legend']['communities']] == [1]
    assert data['bbox'][0] < -82.46 < data['bbox'][2] and data['bbox'][3] < 30  # the view is centred on Tampa, not the country


def test_referral_links_bring_in_linked_hcps_say_what_they_are_and_drop_unplaced_points():
    con = connection()
    data = mapdata.view(con, spec('center', ['100001'], 'referral', 'focus_and_linked', year=2022))
    ids = {p['id'] for p in data['points']}
    assert 'hcp:1000000004' in ids and 'hcp:1000000005' not in ids and data['counts']['not_placed'] == 1
    assert data['links'] == [{'source': 'hcp:1000000004', 'target': 'center:100001', 'weight': 40}]
    assert 'Not CAR-T referrals' in data['legend']['note'] and '2022' in data['legend']['links']['label']
    assert mapdata.view(con, spec('center', ['100001'], 'referral', year=2019))['links'][0]['weight'] == 22


def test_unknown_focus_ids_are_reported_and_a_missing_link_table_draws_no_links():
    con = connection()
    assert 'No community with id 9' in mapdata.validate(con, spec('community', ['9']))
    assert 'focus.ids is empty' in mapdata.validate(con, spec('center', []))
    assert mapdata.validate(con, spec('usa', [])) is None
    data = mapdata.view(con, spec('community', ['1'], 'payments'))
    assert data['links'] == [] and data['legend']['links']['kind'] == 'payments'
    everything = mapdata.view(con, spec('usa', [], show='all'))
    assert everything['counts'] == {'hcps': 3, 'centers': 2, 'links': 0, 'not_placed': 0} and everything['bbox'] == [-125.0, 24.0, -66.5, 49.5]


def test_a_profile_skill_may_also_run_the_topic_queries_and_no_other_skill_gains_any():
    registry = queries.load_parts()
    assert all(name in registry for names in queries.ALSO.values() for name in names)
    assert queries.allowed('payments_by_year', 'community-view') and queries.allowed('center_top_referrers', 'center-profile')
    assert queries.allowed('research_studies', 'hcp-profile') and not queries.allowed('center_top_referrers', 'community-view')
    assert not queries.allowed('payments_by_year', 'referral-flow') and not queries.allowed('community_leaders', 'center-profile')
    assert queries.extras('industry-payments') == [] and queries.home('payments_by_year') == 'industry-payments'
    assert 'payments_by_year' not in {s['name'] for s in queries.extras('hcp-profile')}  # already its own, so listed in full
    text = prompt.skill_text('center-profile')
    assert '## Also available here' in text and '`partb_payment_by_year(scope, id)`' in text and '`center_top_referrers(ccn, year)`' in text
    assert '## Also available here' not in prompt.skill_text('referral-flow')
    with pytest.raises(queries.QueryError, match='is not part of the skill referral-flow'):
        queries.run('payments_by_year', {'scope': 'community', 'id': '1'}, con=sqlite3.connect(':memory:'), skill='referral-flow')


def test_an_initial_or_an_abbreviation_does_not_end_a_sentence():
    text, dropped = verify.clean_text('He works at H. Lee Moffitt Cancer Center, INC. in Tampa. It has 99,999 beds. Dr. Jain is there.', [RESULT])
    assert text == 'He works at H. Lee Moffitt Cancer Center, INC. in Tampa. Dr. Jain is there.' and dropped == ['It has 99,999 beds.']


def test_program_names_with_digits_are_not_read_as_figures():
    assert verify.unsupported('The hospital is FACT accredited and outside the 340B program.', [RESULT]) == []
    assert verify.unsupported('Stays are counted in MS-DRG 018, and DRG 18 covers other cell therapies too.', [RESULT]) == []
    assert verify.unsupported('It received 340 billion dollars and 18 awards.', [RESULT]) == ['340 billion', '18']  # real figures are still checked


def test_every_query_has_a_panel_heading_that_fills_from_its_parameters():
    registry = queries.load_parts()
    assert set(queries.TITLES) == set(registry)
    sample = {'scope': 'community', 'id': '1', 'community_id': 1, 'other_community_id': 2, 'ccn': '100001', 'npi': '1000000001', 'state': 'TX'}
    for name, spec in registry.items():
        params = {k: rule.get('default', sample.get(k)) for k, rule in spec['params'].items()}
        title = queries.panel_title(name, params)
        fallback = name.replace('_', ' ').capitalize()  # what a query without a heading would get
        assert '{' not in title and (title != fallback or queries.TITLES[name] == fallback) and len(title) <= 70, (name, title)
    assert queries.panel_title('payments_by_product', {'brand': 'Yescarta'}) == 'Payments naming Yescarta by year'
    assert queries.panel_title('payments_by_product', {'brand': None}) == 'Payments by product and year'
    assert queries.panel_title('top_centers_by_medicare_stays', {'year': 2023, 'state': ''}) == 'Centers by Medicare CAR-T stays, 2023'
    assert queries.panel_title('community_members', {'division_group': 'affiliates'}) == 'Affiliates of the community'
    assert queries.panel_title('a_query_added_later', {}) == 'A query added later'


def test_the_pack_build_names_the_table_folders_that_are_missing_instead_of_failing_midway(tmp_path, monkeypatch):
    from kolproject.agent import pack
    monkeypatch.setattr(pack, 'PROCESSED', tmp_path)
    for date in ('2026-10-09', '2026-10-15', '2026-10-24'):
        (tmp_path / date).mkdir()
        (tmp_path / date / 'table.csv').write_text('a\n1\n')
    (tmp_path / '2026-11-19').mkdir()  # a folder with no table in it is still missing
    assert pack.missing_inputs() == ['2026-11-11', '2026-11-19', '2026-11-20']
    monkeypatch.setattr('sys.argv', ['kolproject-agent-pack'])
    with pytest.raises(SystemExit) as stop:
        pack.main()
    assert 'Data/processed/2026-11-11, Data/processed/2026-11-19, Data/processed/2026-11-20' in str(stop.value) and 'agent/USAGE.md' in str(stop.value)
