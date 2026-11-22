"""The agent turn with a scripted model: step order, fallbacks, caveats and the number check (no model, no pack)."""
import json
import sqlite3

import jsonschema
import pytest

from kolproject.agent import loop, queries
from kolproject.agent.packbase import create_table
from kolproject.agent.paths import SCHEMAS

SCHEMA = json.loads((SCHEMAS / 'view_spec.schema.json').read_text(encoding='utf-8'))
ALPHA = {'kind': 'community', 'id': '1', 'name': 'Alpha orbit', 'score': 100}
SMITHS = [{'kind': 'hcp', 'id': '1000000001', 'name': 'Ada Smith', 'division': 'referring', 'score': 90},
          {'kind': 'hcp', 'id': '1000000002', 'name': 'Ben Smith', 'division': 'referring', 'score': 90}]


def call(tool, /, **args):
    return {'function': {'name': tool, 'arguments': args}}


def says(*calls, content=''):
    return {'content': content, 'tool_calls': list(calls)}


@pytest.fixture
def con():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    create_table(con, 'dim_community', [('community_id', 'int'), ('label', 'text'), ('short_label', 'text'), ('color_light', 'text'), ('color_dark', 'text')],
                 [(1, 'Alpha orbit', 'Alpha orbit', '#111111', '#222222')])
    create_table(con, 'dim_hcp', [(c, k) for c, k in [('npi', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('lat', 'real'), ('lon', 'real'),
                                                      ('division_group', 'text'), ('is_core', 'int'), ('community_id', 'int'), ('community_basis', 'text'),
                                                      ('pagerank', 'real'), ('pagerank_rank', 'int'), ('is_leader', 'int'), ('primary_center_ccn', 'text')]], [
        ('1000000001', 'Ada Smith', 'Tampa', 'FL', 27.95, -82.46, 'core', 1, 1, 'core_member', 0.02, 1, 1, '100001'),
        ('1000000002', 'Ben Smith', 'Tampa', 'FL', 27.95, -82.46, 'core', 1, 1, 'core_member', 0.01, 2, 0, '100001')])
    create_table(con, 'dim_center', [('ccn', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('lat', 'real'), ('lon', 'real'), ('community_id', 'int'),
                                     ('core_hcps', 'int'), ('linked_hcps', 'int'), ('is_main_center', 'int')],
                 [('100001', 'Alpha Cancer Center', 'Tampa', 'FL', 27.96, -82.45, 1, 2, 40, 1)])
    create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')], [('1000000001', '100001'), ('1000000002', '100001')])
    create_table(con, 'coauthor_pair', [('npi_a', 'text'), ('npi_b', 'text'), ('shared_publications', 'int')], [('1000000001', '1000000002', 4)])
    return con


@pytest.fixture
def world(monkeypatch):
    """Two demo queries in the payments skill and a name lookup that knows one community and two Smiths."""
    monkeypatch.setattr(queries, 'REGISTRY', {})
    monkeypatch.setattr(queries, 'PARTS', [])
    shared = dict(skills=['industry-payments'], caveats=['PAYMENTS'], tables=['pay_general'], years='2019 to 2025')

    @queries.query('demo_by_year', 'Demo dollars by year.', {'scope': 'scope', 'id': 'id', 'brand': {'type': 'string', 'enum': list(queries.BRANDS), 'default': None}},
                   [('year', 'Year', 'year'), ('usd', 'Dollars', 'usd'), ('hcps', 'HCPs paid', 'count')], default_panel={'type': 'line', 'x': 'year', 'y': ['usd']}, **shared)
    def by_year(con, scope, id, brand):
        return [{'year': 2023, 'usd': 4944607.6, 'hcps': 200}, {'year': 2024, 'usd': 727654.8, 'hcps': 148}]

    @queries.query('demo_summary', 'Demo totals in one row.', {'scope': 'scope', 'id': 'id'},
                   [('usd', 'Dollars', 'usd'), ('hcps', 'HCPs paid', 'count')], default_panel={'type': 'kpis'}, **shared)
    def summary(con, scope, id):
        return [{'usd': 5672262.4, 'hcps': 348}]

    known = {'alpha': [ALPHA], 'smith': SMITHS}
    monkeypatch.setattr(loop.search, 'find_entity', lambda text, kind, con=None, limit=5: known.get(text.lower().split()[-1], []))


def run(monkeypatch, con, replies, question='Payments to the Alpha orbit by year.', memory=None):
    """Play scripted model replies through one turn. Returns (final event, step labels, messages the model saw last)."""
    script, seen, heat = list(replies), [], []

    def chat_events(messages, tools, model=None, temperature=None):
        seen[:] = messages
        heat.append(temperature)
        return loop.as_events(script.pop(0) if script else says())
    monkeypatch.setattr(loop, 'chat_events', chat_events)
    events = list(loop.events(question, con=con, memory=memory))
    final = events[-1]
    assert final['type'] == 'final'
    jsonschema.validate(final['view'], SCHEMA)
    final['drawn'] = [e for e in events[:-1] if e['type'] != 'step']  # what reached the interface before the answer
    final['heat'] = heat  # the temperature asked for on each model call; None is the usual low one
    return final, [e['label'] for e in events[:-1] if e['type'] == 'step'], seen


OPENING = [says(call('load_skill', name='industry-payments')), says(call('find_entity', text='Alpha', kind='community'))]
QUERY = call('run_query', name='demo_by_year', params={'scope': 'community', 'id': '1'})


def test_a_full_turn_gives_a_view_built_from_handles_with_caveats_from_skill_and_query(monkeypatch, con, world):
    final, labels, _ = run(monkeypatch, con, OPENING + [
        says(QUERY, call('set_map', focus={'kind': 'community', 'ids': ['1']}, edges='payments', highlight='leaders'),
             call('add_panel', type='line', title='Dollars by year', handle='demo_by_year', options={'x': 'year', 'y': ['usd', 'made_up_column']})),
        says(call('final_answer', headline='Payments fell from $4.9 million to $728 thousand.', text='In 2023 there were 200 HCPs paid, and 148 in 2024.', caveats=['LABELS', 'NOT_A_CODE']))])
    view = final['view']
    assert labels == ['Choosing how to answer', 'Looking up Alpha', 'Reading demo by year', 'Setting the map', 'Redrawing Dollars by year', 'Writing the answer']
    assert view['map'] == {'focus': {'kind': 'community', 'ids': ['1']}, 'show': 'focus_only', 'edges': 'payments', 'highlight': 'leaders'}
    assert view['panels'] == [{'type': 'line', 'title': 'Dollars by year', 'handle': 'demo_by_year', 'options': {'x': 'year', 'y': ['usd']}}]
    assert view['answer'] == {'headline': 'Payments fell from $4.9 million to $728 thousand.', 'text': 'In 2023 there were 200 HCPs paid, and 148 in 2024.',
                              'caveats': ['LABELS', 'PAYMENTS']}
    assert final['skill'] == 'industry-payments' and final['results']['demo_by_year']['row_count'] == 2
    assert {p['id'] for p in final['map_data']['points']} == {'hcp:1000000001', 'hcp:1000000002', 'center:100001'}
    assert final['memory'] == {'focus': 'community 1 (Alpha orbit)'}
    # The interface was sent each piece when its tool ran, before the model wrote the answer: the map as soon
    # as the skill and the community were known, the query's standard panel with the rows, then the panel as
    # the model redrew it. The model's own set_map asked for the map already shown, so it was not sent twice.
    assert [e['type'] for e in final['drawn']] == ['map', 'panels', 'panels']
    moved, first, redrawn = final['drawn']
    assert moved['early'] is True and moved['map_data']['spec'] == view['map']
    assert first['panels'] == [{'type': 'line', 'title': 'Demo by year', 'handle': 'demo_by_year', 'options': {'x': 'year', 'y': ['usd']}}]
    assert first['results']['demo_by_year']['rows'][0]['year'] == 2023 and redrawn['results'] == {} and redrawn['panels'] == view['panels']


def test_a_model_that_only_runs_a_query_still_gets_a_map_panels_and_an_honest_text(monkeypatch, con, world):
    final, _, _ = run(monkeypatch, con, OPENING + [says(QUERY), says(call('final_answer', headline='', text='', caveats=[]))])
    view = final['view']
    assert view['map'] == {'focus': {'kind': 'community', 'ids': ['1']}, 'show': 'focus_only', 'edges': 'payments', 'highlight': 'leaders'}
    assert [(p['type'], p['handle'], p['options']) for p in view['panels']] == [('line', 'demo_by_year', {'x': 'year', 'y': ['usd']})]
    assert view['answer']['headline'] == 'Demo by year: Alpha orbit (core members and attached HCPs)' and view['answer']['text'].startswith('The panels below show')
    assert view['answer']['caveats'] == ['PAYMENTS']


def test_numbers_no_result_supports_never_reach_the_user(monkeypatch, con, world):
    final, _, _ = run(monkeypatch, con, OPENING + [says(QUERY), says(call(
        'final_answer', headline='Payments reached $9.9 million.', text='In 2023 there were 200 HCPs paid. Another 1,250 HCPs were paid abroad.', caveats=[]))])
    answer = final['view']['answer']
    assert answer['text'] == 'In 2023 there were 200 HCPs paid.' and '9.9' not in answer['headline']
    assert final['trace'][-1] == {'step': 'assemble', 'dropped_sentences': ['Another 1,250 HCPs were paid abroad.']}


def test_a_plain_text_reply_is_taken_as_the_answer(monkeypatch, con, world):
    final, _, _ = run(monkeypatch, con, OPENING + [says(QUERY), says(content='**Payments fell in 2024.** There were 148 HCPs paid.\nCaveats: PAYMENTS')])
    assert final['view']['answer']['headline'] == 'Payments fell in 2024.' and final['view']['answer']['text'] == 'There were 148 HCPs paid.'


def test_an_unreadable_tool_call_or_an_empty_reply_is_retried_not_fatal(monkeypatch, con, world):
    final, labels, _ = run(monkeypatch, con, [{'content': '', 'tool_calls': [], 'server_error': 'could not parse tool call'}, says()] + OPENING + [
        says(QUERY), says(call('final_answer', headline='Two years of payments.', text='', caveats=[]))])
    assert labels[-2:] == ['Reading demo by year', 'Writing the answer'] and final['view']['answer']['headline'] == 'Two years of payments.'
    assert final['trace'][0]['server_error'] == 'could not parse tool call'
    assert final['heat'][:3] == [None, 0.4, None]  # the retry is written with more variety, then the usual setting returns
    broken = {'content': '', 'tool_calls': [], 'server_error': 'could not parse tool call'}
    final, labels, _ = run(monkeypatch, con, [broken] * 9)
    assert final['heat'] == [None, 0.4, 0.7] and labels == []  # three unreadable replies in a row end the turn
    assert final['view']['answer']['headline'] == 'The model could not write a readable reply this time.' and final['view']['panels'] == []
    final, _, _ = run(monkeypatch, con, OPENING + [says(QUERY)] + [broken] * 9)
    assert len(final['view']['panels']) == 1 and 'readable reply' not in final['view']['answer']['headline']  # what was already read is still shown


def test_a_query_called_as_if_it_were_a_tool_is_run(monkeypatch, con, world):
    final, labels, _ = run(monkeypatch, con, OPENING + [says(call('demo_by_year', scope='community', id='1')),
                                                        says(call('demo_summary', params={'scope': 'community', 'id': '1'})),
                                                        says(call('no_such_thing', id='1')), says(call('final_answer', headline='Two years.', text=''))])
    assert labels[2:5] == ['Reading demo by year', 'Reading demo summary', 'no_such_thing'] and sorted(final['results']) == ['demo_by_year', 'demo_summary']
    assert [t['error'] for t in final['trace'] if t.get('error')] == ['There is no tool named no_such_thing.']


def test_the_map_follows_the_queries_when_the_model_sets_none(monkeypatch, con, world):
    # A follow-up names nothing to look up: the map goes to what the query was run for, when its panel arrives.
    final, _, _ = run(monkeypatch, con, [says(call('load_skill', name='industry-payments')), says(QUERY), says(call('final_answer', headline='Two years.', text=''))],
                      question='And their payments?', memory={'focus': 'community 1 (Alpha orbit)'})
    assert [e['type'] for e in final['drawn']] == ['map', 'panels'] and final['view']['map']['focus'] == {'kind': 'community', 'ids': ['1']}
    turn = loop.Turn(con)
    turn.skill = 'hcp-profile'
    turn.entities = [{'kind': 'center', 'id': '100001', 'name': 'Alpha Cancer Center'}]
    turn.results = {'a': {'params': {'npi': '1000000001'}, 'caveats': ['REFERRAL', 'MEDICARE']}, 'b': {'params': {'ccn': '100001', 'year': 2019}, 'caveats': ['REFERRAL']}}
    # What was queried wins over what was looked up; a referral query switches the links and gives the year.
    assert loop.default_map(turn) == {'focus': {'kind': 'hcp', 'ids': ['1000000001']}, 'show': 'focus_and_linked', 'edges': 'referral',
                                      'highlight': ['1000000001'], 'year': 2019}
    # A profile skill that ran a payment query draws payment links; its own queries keep its own links.
    turn.skill, turn.entities = 'community-view', []
    turn.results = {'a': {'name': 'community_summary', 'params': {'community_id': 1}, 'caveats': []},
                    'b': {'name': 'payments_by_year', 'params': {'scope': 'community', 'id': '1'}, 'caveats': ['PAYMENTS']}}
    monkeypatch.setattr(queries, 'REGISTRY', {'payments_by_year': {'skills': ['industry-payments', 'hcp-profile']}, 'community_summary': {'skills': ['community-view']}})
    assert loop.default_map(turn) == {'focus': {'kind': 'community', 'ids': ['1']}, 'show': 'focus_only', 'edges': 'payments', 'highlight': 'leaders'}
    del turn.results['b']
    assert loop.default_map(turn)['edges'] == 'coauthor'
    turn.skill, turn.entities = 'center-profile', []
    turn.results = {'a': {'params': {'year': 2023, 'state': 'tx', 'n': 15}, 'caveats': ['MEDICARE']}}
    assert loop.default_map(turn) == {'focus': {'kind': 'state', 'ids': ['TX']}, 'show': 'focus_only', 'edges': 'none', 'highlight': 'none'}
    turn.results = {'a': {'params': {'scope': 'division_group', 'id': 'core'}, 'caveats': []}}
    assert loop.default_map(turn) == {'focus': {'kind': 'usa', 'ids': []}, 'show': 'all', 'edges': 'none', 'highlight': 'none'}  # a national answer
    turn.results = {}
    assert loop.default_map(turn) is None
    turn.skill, turn.results = 'scope-and-help', {'a': {'params': {}, 'caveats': []}}
    assert loop.default_map(turn) is None  # the help table leaves the map alone


def test_queries_need_a_loaded_skill_and_must_belong_to_it(monkeypatch, con, world):
    final, _, seen = run(monkeypatch, con, [says(QUERY), says(call('load_skill', name='referral-flow')), says(QUERY),
                                            says(call('run_query', name='drop_tables', params={})), says(call('nope')),
                                            says(call('final_answer', headline='That is not in the database.', text='', caveats=[]))])
    errors = [json.loads(m['content']).get('error', '') for m in seen if m['role'] == 'tool']
    assert 'Call load_skill first' in errors[0] and 'not part of the skill' in errors[2] and 'no query named' in errors[3] and 'no tool named nope' in errors[4]
    assert final['results'] == {} and final['view']['panels'] == [] and final['view']['map']['unchanged'] is True
    assert final['view']['answer']['caveats'] == []  # a skill's caveats are printed only once its data is shown


def test_panels_tolerate_invented_handles_and_wrong_types_but_not_duplicates_or_floods(monkeypatch, con, world):
    turn = loop.Turn(con)
    turn.load_skill('industry-payments')
    assert 'Drawn on the canvas as a line panel' in turn.run_query('demo_by_year', {'scope': 'community', 'id': '1'})['note']
    turn.run_query('demo_summary', {'scope': 'community', 'id': '1'})
    # Each query drew its own standard panel, the fact box first although it was run second.
    assert turn.panels == [{'type': 'kpis', 'title': 'Demo summary', 'handle': 'demo_summary', 'options': {}},
                           {'type': 'line', 'title': 'Demo by year', 'handle': 'demo_by_year', 'options': {'x': 'year', 'y': ['usd']}}]
    assert 'already ran this' in turn.run_query('demo_by_year', {'scope': 'community', 'id': '1'})['note'] and len(turn.results) == 2 and len(turn.panels) == 2
    assert 'Unknown handle' in turn.add_panel('bar', 'Nothing', 'payments_table')['error'] and 'type must be one of' in turn.add_panel('pie', 'Pie', 'demo_summary')['error']
    # The model's own panel for a result replaces the standard one where it stands.
    assert turn.add_panel('kpis', 'Facts', 'demo_summary_11')['ok'] and turn.panels[0] == {'type': 'kpis', 'title': 'Facts', 'handle': 'demo_summary', 'options': {}}
    assert turn.add_panel('kpis', 'Years', 'demo_by_year')['ok'] and turn.panels[1]['type'] == 'line' and turn.panels[1]['title'] == 'Years'  # a fact box shows one row
    assert 'already on the canvas' in turn.add_panel('line', 'Again', 'demo_by_year')['note'] and len(turn.panels) == 2
    assert turn.add_panel('bar', handle='demo_by_year')['ok'] and turn.panels.pop()['title'] == 'Demo by year'  # a panel without a title takes the query's
    for kind in ('table', 'bar', 'ranked_list', 'timeline'):
        assert turn.add_panel(kind, kind, 'demo_by_year')['ok']
    assert 'At most 6 panels' in turn.add_panel('table', 'Seventh', 'demo_summary')['error']
    assert turn.add_panel('text', 'Note', options={'body': 'x' * 900})['error']  # the limit also covers text panels


def test_a_one_product_time_chart_carries_that_products_approval_dates(monkeypatch, con, world):
    turn = loop.Turn(con)
    turn.load_skill('industry-payments')
    turn.run_query('demo_by_year', {'scope': 'community', 'id': '1', 'brand': 'Yescarta'})
    turn.add_panel('line', 'Yescarta dollars', 'demo_by_year')
    turn.add_panel('table', 'Rows', 'demo_by_year', {'markers': ['Not a product']})
    assert turn.panels[0]['options'] == {'x': 'year', 'y': ['usd'], 'markers': ['Yescarta']} and 'markers' not in turn.panels[1]['options']


def test_equally_good_name_matches_end_the_turn_with_a_question(monkeypatch, con, world):
    final, labels, seen = run(monkeypatch, con, [says(call('load_skill', name='hcp-profile')), says(call('find_entity', text='Dr. Smith', kind='hcp')),
                                                 says(call('ask_user', question='Which Smith?', choices=['Ada Smith, Tampa', 'Ben Smith, Tampa']))],
                              question='Show me Dr. Smith.')
    assert json.loads(seen[-1]['content'])['ambiguous'] is True
    view = final['view']
    assert view['ask'] == {'question': 'Which Smith?', 'choices': ['Ada Smith, Tampa', 'Ben Smith, Tampa']} and labels[-1] == 'Asking a question'
    assert view['answer'] == {'headline': 'Which Smith?', 'text': '', 'caveats': []} and view['panels'] == []
    assert view['map']['unchanged'] is True and final['map_data'] is None  # nobody is shown until the user has chosen


def test_a_model_that_never_finishes_is_stopped_and_the_follow_up_context_is_passed(monkeypatch, con, world):
    final, labels, seen = run(monkeypatch, con, [], question='And their payments?', memory={'focus': 'community 1 (Alpha orbit)'})
    assert labels == [] and final['view']['answer']['headline'] == 'That is not in the database.' and final['memory'] == {'focus': 'community 1 (Alpha orbit)'}
    assert 'the user was looking at: community 1 (Alpha orbit)' in seen[1]['content'] and 'If the question names a person, a center' in seen[1]['content']
    assert sum(m['role'] == 'assistant' for m in seen) <= loop.MAX_STEPS


def test_a_plain_reply_keeps_its_list_loses_its_caveat_line_and_names_the_codes():
    final, codes = loop.split_plain('## I can show three things.\n\n- Payments by year\n- **Leaders** of a community\n\nCaveats: PAYMENTS, LABELS.')
    assert final == {'headline': 'I can show three things.', 'text': '- Payments by year\n- Leaders of a community'} and codes == ['PAYMENTS', 'LABELS']
    final, codes = loop.split_plain('Stays rose from 24 to 53. The chart marks approvals. Caveats: MEDICARE, NOT_PRODUCT_SPECIFIC')
    assert final == {'headline': 'Stays rose from 24 to 53.', 'text': 'The chart marks approvals.'} and codes == ['MEDICARE', 'NOT_PRODUCT_SPECIFIC']
    long = 'A first sentence that runs on ' + 'and on ' * 25 + 'to the end. Then a second one.'
    final, _ = loop.split_plain(long)
    assert final['headline'] == '' and final['text'].startswith('A first sentence') and final['text'].endswith('Then a second one.')
    assert loop.split_plain('Caveats: PAYMENTS') == ({'headline': '', 'text': ''}, ['PAYMENTS'])
    initial, _ = loop.split_plain('He works at H. Lee Moffitt Cancer Center with Dr. Jain. He leads the orbit.')
    assert initial == {'headline': 'He works at H. Lee Moffitt Cancer Center with Dr. Jain.', 'text': 'He leads the orbit.'}  # an initial is not a sentence end
    assert loop.answer_so_far('He works at H. Lee', []) is None and loop.answer_so_far('Sattva S. Neelapu leads. He', [])['headline'] == 'Sattva S. Neelapu leads.'
    labelled, _ = loop.split_plain('Headline: Payments total $208,859.\nText: The highest year was 2025.')
    assert labelled == {'headline': 'Payments total $208,859.', 'text': 'The highest year was 2025.'}


def test_text_is_cut_at_a_sentence_or_line_end():
    text = 'One short sentence. ' * 30
    assert loop.clip(text, 100).endswith('sentence.') and len(loop.clip(text, 100)) <= 100 and loop.clip('Short.', 100) == 'Short.'
    assert loop.clip('word ' * 60, 101).endswith('…') and ' …' not in loop.clip('word ' * 60, 101)
    assert loop.clip('Here is the list:\n- one\n- two\nAbout referrals:\n- three', 48) == 'Here is the list:\n- one\n- two'  # no heading left hanging


def series(periods, names=(None,), column='year'):
    columns = [{'name': column, 'unit': 'year'}, {'name': 'drug', 'unit': 'text'}, {'name': 'usd', 'unit': 'usd'}, {'name': 'hcps', 'unit': 'count'}]
    rows = [{column: p, 'drug': n, 'usd': 100.4 * (i + 1), 'hcps': 3} for n in names for i, p in enumerate(periods)]
    return {'columns': columns, 'rows': rows, 'row_count': len(rows)}


def test_the_model_reads_both_ends_of_a_long_series_and_dollar_totals_it_need_not_add():
    short = series(range(2019, 2026))
    assert loop.preview_rows(short) == (short['rows'], '') and loop.dollar_totals(short) == {'usd': 2811}
    split = series(range(2017, 2025), names=('ibrutinib', 'venetoclax', 'lenalidomide'))
    rows, note = loop.preview_rows(split)
    assert {r['year'] for r in rows} == {2017, 2024} and len(rows) == 6 and 'only the rows for 2017 and 2024' in note and 'Do not say a value is missing' in note
    monthly = series([f'{2019 + k // 12}-{1 + k % 12:02d}' for k in range(84)], column='month')
    monthly['rows'][40]['usd'] = 99999.0
    rows, note = loop.preview_rows(monthly)
    assert [r['month'] for r in rows] == ['2019-01', '2019-02', '2019-03', '2019-04', '2019-05', '2019-06', '2019-07', '2022-05',
                                          '2025-06', '2025-07', '2025-08', '2025-09', '2025-10', '2025-11', '2025-12']
    assert 'the row with the largest usd' in note
    ranked = {'columns': [{'name': 'name', 'unit': 'text'}, {'name': 'rank', 'unit': 'rank'}], 'rows': [{'name': f'n{k}', 'rank': k} for k in range(40)], 'row_count': 40}
    rows, note = loop.preview_rows(ranked)
    assert len(rows) == loop.PREVIEW_FOR_MODEL and 'These are the first 14' in note and loop.dollar_totals(ranked) == {}
    assert loop.dollar_totals(series([2024])) == {}  # one row needs no total


def test_one_call_runs_several_queries_that_share_their_parameters(monkeypatch, con, world):
    turn = loop.Turn(con)
    turn.load_skill('industry-payments')
    shared = {'scope': 'community', 'id': '1', 'brand': 'Yescarta'}
    out = turn.run_query(names=['demo_summary', 'demo_by_year', 'no_such_query'], params=shared)
    assert [r.get('handle') or r['error'] for r in out['results']] == ['demo_summary', 'demo_by_year', 'There is no query named no_such_query.']
    assert turn.results['demo_by_year']['params']['brand'] == 'Yescarta' and 'brand' not in turn.results['demo_summary']['params']  # each took what it has
    assert [p['type'] for p in turn.panels] == ['kpis', 'line'] and out['results'][0]['preview'] == {'usd': 5672262, 'hcps': 348}
    assert 'at most 50 words' in out['next'] and 'next' not in turn.run_query(names=['no_such_query'], params={})  # the reminder comes with results only
    assert out['results'][1]['preview'] == 'year, usd, hcps\n2023, 4944608, 200\n2024, 727655, 148' and out['results'][1]['column_totals'] == {'usd': 5672262}
    # One query alone is still told about a parameter it does not take, and the old single-name form still works.
    assert 'Unknown parameter brand' in turn.run_query(names=['demo_summary'], params=shared)['error']
    assert turn.run_query(name='demo_summary', params={'scope': 'community', 'id': '1'})['handle'] == 'demo_summary'
    assert 'Give the query names' in turn.run_query(params=shared)['error']
    final, labels, _ = run(monkeypatch, con, OPENING + [says(call('run_query', names=['demo_summary', 'demo_by_year', 'nope'], params={'scope': 'community', 'id': '1'})),
                                                        says(call('final_answer', headline='Two years.', text=''))])
    assert labels[2] == 'Reading demo summary, demo by year, nope' and len(final['view']['panels']) == 2
    assert [t['error'] for t in final['trace'] if t.get('tool') == 'run_query'] == ['There is no query named nope.']


def test_the_map_moves_when_the_entity_is_known_unless_the_name_is_ambiguous(monkeypatch, con, world):
    final, _, _ = run(monkeypatch, con, [says(call('find_entity', text='Alpha', kind='community'), call('load_skill', name='industry-payments'),
                                              call('find_entity', text='Alpha', kind='community')), says(QUERY), says(call('final_answer', headline='Two years.', text=''))])
    maps = [e for e in final['drawn'] if e['type'] == 'map']
    assert len(maps) == 1 and maps[0]['early'] and final['view']['map'] == maps[0]['map_data']['spec']  # the model set no map: the early one stands
    assert final['view']['map'] == {'focus': {'kind': 'community', 'ids': ['1']}, 'show': 'focus_only', 'edges': 'payments', 'highlight': 'leaders'}
    turn = loop.Turn(con)
    turn.skill, turn.entities = 'referral-flow', [{'kind': 'center', 'id': '100001', 'name': 'Alpha Cancer Center'}]
    assert loop.default_map(turn) == {'focus': {'kind': 'center', 'ids': ['100001']}, 'show': 'focus_and_linked', 'edges': 'referral',
                                      'highlight': 'main_centers', 'year': 2022}
    turn.skill, turn.entities = 'hcp-profile', [{'kind': 'product', 'id': 'Yescarta'}, {'kind': 'hcp', 'id': '1000000001'}, {'kind': 'hcp', 'id': '1000000002'}]
    assert loop.default_map(turn) == {'focus': {'kind': 'hcp', 'ids': ['1000000001', '1000000002']}, 'show': 'focus_and_linked', 'edges': 'coauthor',
                                      'highlight': ['1000000001', '1000000002']}
    asked, _, _ = run(monkeypatch, con, [says(call('load_skill', name='hcp-profile'), call('find_entity', text='Dr. Smith', kind='hcp')),
                                         says(call('ask_user', question='Which Dr. Smith?', choices=['Ada Smith', 'Ben Smith']))])
    assert asked['drawn'] == [] and asked['view']['ask']['choices'] == ['Ada Smith', 'Ben Smith']


def test_a_refusal_leaves_the_map_alone_and_a_wrong_focus_kind_is_an_error_not_a_crash(monkeypatch, con, world):
    final, _, _ = run(monkeypatch, con, OPENING + [says(call('set_map', focus={'kind': 'division_group', 'ids': ['core']}, edges='none')),
                                                   says(call('final_answer', headline='That is not in the database.', text=''))])
    assert 'focus.kind cannot be division_group' in [t['error'] for t in final['trace'] if t.get('tool') == 'set_map'][0]
    # The map had moved early to the community; with nothing shown the answer leaves it where it was before.
    assert [e['type'] for e in final['drawn']] == ['map'] and final['view']['map']['unchanged'] is True and final['map_data'] is None
    assert final['view']['answer'] == {'headline': 'That is not in the database.', 'text': '', 'caveats': []}


def test_a_tool_that_fails_tells_the_model_and_does_not_end_the_turn(monkeypatch, con, world):
    final, labels, seen = run(monkeypatch, con, [says(call('load_skill', name='industry-payments'), call('find_entity', text='affiliates', kind='division_group')),
                                                 says(call('find_entity', text='Alpha', kind='community')),
                                                 says(QUERY), says(call('final_answer', headline='Two years of payments.', text=''))])
    errors = [t['error'] for t in final['trace'] if t.get('error')]
    assert len(errors) == 1 and 'kind must be one of' in errors[0] and 'division_group' in errors[0]
    monkeypatch.setattr(loop.search, 'find_entity', lambda *a, **k: 1 / 0)
    final, _, _ = run(monkeypatch, con, OPENING + [says(QUERY), says(call('final_answer', headline='Two years of payments.', text=''))])
    assert [t['error'] for t in final['trace'] if t.get('error')] == ['find_entity could not run: division by zero'] and len(final['view']['panels']) == 1


def test_the_answer_reaches_the_page_sentence_by_sentence_and_only_what_the_number_check_accepts(monkeypatch, con, world):
    pieces = ['Payments fell ', 'from $4.9 million to $728 thousand.\n', 'In 2023 there were 200 HCPs paid. ', 'It was 9,999 the year ', 'before. And 148 in 2024.']
    script = OPENING + [says(QUERY)]

    def chat_events(messages, tools, model=None, temperature=None):
        if script:
            return loop.as_events(script.pop(0))
        return iter([('text', piece) for piece in pieces] + [('reply', {'content': ''.join(pieces), 'tool_calls': []})])
    monkeypatch.setattr(loop, 'chat_events', chat_events)
    events = list(loop.events('Payments to the Alpha orbit by year.', con=con))
    answers = [{k: e[k] for k in ('headline', 'text')} for e in events if e['type'] == 'answer']
    # The headline when its line is complete; the next sentence once the one after it has begun; the sentence
    # with a number no result supports never; the last sentence with the final answer.
    assert answers == [{'headline': 'Payments fell from $4.9 million to $728 thousand.', 'text': ''},
                       {'headline': 'Payments fell from $4.9 million to $728 thousand.', 'text': 'In 2023 there were 200 HCPs paid.'}]
    final = events[-1]
    assert final['view']['answer']['text'] == 'In 2023 there were 200 HCPs paid. And 148 in 2024.' and final['view']['answer']['headline'] == answers[0]['headline']
    assert final['trace'][-1]['dropped_sentences'] == ['It was 9,999 the year before.'] and not any('9,999' in str(e) for e in events if e['type'] == 'answer')
    assert [e['label'] for e in events if e['type'] == 'step'][-1] == 'Writing the answer'
    assert [e['type'] for e in events].index('answer') > [e['type'] for e in events].index('panels')  # the panel was on screen first


def test_what_may_be_shown_of_an_answer_still_being_written(monkeypatch, con, world):
    turn = loop.Turn(con)
    turn.load_skill('industry-payments')
    turn.run_query(names=['demo_by_year'], params={'scope': 'community', 'id': '1'})
    results = list(turn.results.values())
    assert loop.answer_so_far('Payments fell to $728 thousand', results) is None  # no sentence is complete yet
    assert loop.answer_so_far('Payments fell to $728 thousand. In', results) == {'headline': 'Payments fell to $728 thousand.', 'text': ''}
    # A headline with a number no result supports is held back, while a checked sentence after it is shown.
    assert loop.answer_so_far('Headline: Payments were $90 million.\nThere were 148 HCPs paid in 2024. More', results) == {
        'headline': '', 'text': 'There were 148 HCPs paid in 2024.'}
    assert loop.answer_so_far('Payments were $90 million. Then', results) is None  # nothing that may be shown
    # Text written before any query has run is not an answer in progress: a refusal arrives whole.
    refusal, labels, _ = run(monkeypatch, con, [says(call('load_skill', name='scope-and-help')), says(content='Commercial claims are not held. Medicare claims are.')])
    assert refusal['drawn'] == [] and labels == ['Choosing how to answer'] and refusal['view']['answer']['headline'] == 'Commercial claims are not held.'


def test_words_alone_are_not_an_answer_until_a_query_has_run_or_the_model_has_been_pushed_once(monkeypatch, con, world):
    early = says(content='Payments to the Alpha orbit were high. They rose every year.')
    # A skill is loaded and the community found, but no query has run: the model is told once, then runs it.
    final, labels, seen = run(monkeypatch, con, OPENING + [early, says(QUERY), says(content='Payments fell.\nThere were 148 HCPs paid in 2024.')])
    assert [t['pushed'] for t in final['trace'] if 'pushed' in t] == ['run a query'] and 'You have not run a query' in seen[7]['content']
    assert final['view']['answer']['headline'] == 'Payments fell.' and len(final['view']['panels']) == 1
    # No skill at all: told once to load one. The same words a second time are taken as the answer (a refusal).
    final, labels, seen = run(monkeypatch, con, [says(content='The weather is not in the database.')] * 2)
    assert [t['pushed'] for t in final['trace'] if 'pushed' in t] == ['load a skill'] and 'You have not loaded a skill' in seen[3]['content']
    assert final['view']['answer']['headline'] == 'The weather is not in the database.' and final['view']['panels'] == [] and labels == []
    # In the help skill words are the answer: no push.
    final, _, _ = run(monkeypatch, con, [says(call('load_skill', name='scope-and-help')), says(content='Method checks are not part of this tool.')])
    assert not any('pushed' in t for t in final['trace']) and final['view']['answer']['headline'] == 'Method checks are not part of this tool.'


def test_a_model_that_writes_about_an_ambiguous_name_instead_of_asking_still_ends_in_a_question_with_the_choices(monkeypatch, con, world):
    final, labels, _ = run(monkeypatch, con, [says(call('load_skill', name='hcp-profile'), call('find_entity', text='Dr. Smith', kind='hcp')),
                                              says(content='There are several HCPs named Smith in the database.')])
    assert final['view']['ask'] == {'question': 'Several entries match "Dr. Smith". Which one do you mean?', 'choices': ['Ada Smith', 'Ben Smith']}
    assert final['view']['panels'] == [] and final['view']['map']['unchanged'] is True and final['trace'][-2] == {'step': 1, 'asked_by_code': True}
    assert loop.ask_from_candidates('Mayo', [{'name': 'Mayo Clinic Hospital', 'place': 'Phoenix, AZ'}, {'name': 'Mayo Clinic Jacksonville', 'place': 'Jacksonville, FL'}])['choices'] == [
        'Mayo Clinic Hospital (Phoenix, AZ)', 'Mayo Clinic Jacksonville (Jacksonville, FL)']


def test_the_model_reads_several_rows_as_one_table_and_one_row_as_named_values():
    result = series([2023, 2024], names=('ibrutinib', 'Smith, Ada'))
    result['rows'][1]['usd'] = None
    assert loop.preview_table(result, result['rows']) == ('year, drug, usd, hcps\n2023, ibrutinib, 100.4, 3\n2024, ibrutinib, , 3\n'
                                                           '2023, "Smith, Ada", 100.4, 3\n2024, "Smith, Ada", 200.8, 3')
    assert loop.preview_table(result, result['rows'][:1]) == {'year': 2023, 'drug': 'ibrutinib', 'usd': 100.4, 'hcps': 3}
    assert loop.preview_table(result, []) == {}
    long = {'columns': [{'name': 'title'}, {'name': 'year'}], 'rows': [{'title': 'A long paper title, ' + 'word ' * 30, 'year': 2024}] * 2}
    line = loop.preview_table(long, long['rows']).splitlines()[1]
    assert line.startswith('"A long paper title, word') and line.endswith('…", 2024') and len(line) < 85  # the panel shows the whole title


def test_an_answer_from_the_prompts_product_table_may_state_its_dates_but_nothing_else(monkeypatch, con, world):
    said = 'Yescarta was first approved on 18 October 2017. It has 4,321 treated patients.'
    final, _, _ = run(monkeypatch, con, [says(content='Yescarta is a CD19 product. ' + said)] * 2)  # said again after the push to load a skill
    assert final['view']['answer'] == {'headline': 'Yescarta is a CD19 product.', 'text': 'Yescarta was first approved on 18 October 2017.', 'caveats': []}
    assert final['trace'][-1]['dropped_sentences'] == ['It has 4,321 treated patients.'] and final['skill'] is None
    # Once a query has run, its rows are the only backing: a day of the month from the product table no longer passes.
    final, _, _ = run(monkeypatch, con, OPENING + [says(QUERY), says(call('final_answer', headline='Two years of payments.', text='Yescarta was approved on 18 October 2017.'))])
    assert final['trace'][-1]['dropped_sentences'] == ['Yescarta was approved on 18 October 2017.']


def test_tool_calls_run_as_the_model_writes_them_and_a_failed_call_keeps_what_already_ran(monkeypatch, con, world):
    turn_replies = [says(call('load_skill', name='industry-payments'), call('find_entity', text='Alpha', kind='community')),
                    {**says(QUERY), 'server_error': 'the stream broke'},
                    says(call('final_answer', headline='Two years of payments.', text=''))]
    final, labels, seen = run(monkeypatch, con, turn_replies)
    assert labels == ['Choosing how to answer', 'Looking up Alpha', 'Reading demo by year', 'Writing the answer']
    assert [m['role'] for m in seen] == ['system', 'user', 'assistant', 'tool', 'tool', 'assistant', 'tool', 'user']  # the query's result stayed in the conversation
    assert final['view']['panels'][0]['handle'] == 'demo_by_year' and final['trace'][-1]['step'] == 'assemble'
    monkeypatch.setattr(loop, 'OLLAMA', 'http://127.0.0.1:9/api/chat')
    assert loop.warm() is None  # no model server: the page still starts


def test_the_map_tool_reads_a_highlight_list_written_as_text_and_a_broken_map_does_not_lose_the_answer(monkeypatch, con, world):
    turn = loop.Turn(con)
    assert turn.set_map({'kind': 'hcp', 'ids': ['1000000001']}, 'coauthor', highlight='["1000000001"]', year='2031')['ok']
    assert turn.map['highlight'] == ['1000000001'] and turn.map['year'] == 2026
    assert [loop.clean_highlight(v) for v in ('leaders', 'main_centers', None, 'everyone', [], [1000000001], '[1000000001, oops')] == [
        'leaders', 'main_centers', 'none', 'none', 'none', ['1000000001'], ['1000000001', 'oops']]
    final, _, _ = run(monkeypatch, con, OPENING + [says(QUERY), says(call('final_answer', headline='Two years of payments.', text='', caveats=[]))])
    turn = loop.Turn(con)
    turn.load_skill('industry-payments')
    turn.run_query('demo_by_year', {'scope': 'community', 'id': '1'})
    turn.map = {'focus': {'kind': 'community', 'ids': ['1']}, 'show': 'focus_only', 'edges': 'payments', 'highlight': 'everything'}
    turn.final = {'headline': 'Two years of payments.', 'text': ''}
    view = loop.assemble(turn)
    assert view['map']['unchanged'] is True and view['answer']['headline'] == 'Two years of payments.' and len(view['panels']) == 1
    assert turn.trace[-1]['repaired'][0]['part'] == 'map'
