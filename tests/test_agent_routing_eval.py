"""The routing test set and its scorer: the set agrees with the queries, and each check passes and fails when it should."""
from kolproject.agent import queries, routing_eval
from kolproject.agent.routing_eval import check_set, load_set, report, score, summary


def final(skill='referral-flow', results=(), focus=None, edges='none', caveats=(), ask=None, headline='', text=''):
    """A finished answer as the loop returns it, with only the parts the scorer reads."""
    view = {'map': {'focus': focus, 'edges': edges} if focus else {'focus': {'kind': 'usa', 'ids': []}, 'edges': 'none', 'unchanged': True},
            'panels': [], 'answer': {'headline': headline, 'text': text, 'caveats': list(caveats)}}
    if ask:
        view['ask'] = ask
    return {'view': view, 'skill': skill, 'trace': [],
            'results': {f'{name}_{i}': {'name': name, 'params': params} for i, (name, params) in enumerate(results)}}


CENTER = {'id': 'x', 'group': 'referral', 'question': 'Who referred to the center in 2021?', 'skill': ['referral-flow'], 'ids': ['450076'],
          'queries': ['center_top_referrers'], 'queries_any': ['center_referral_summary', 'center_referrers_by_region'], 'params': {'year': '2021'},
          'focus': 'center', 'edges': ['referral'], 'caveats': ['REFERRAL']}
GOOD = dict(results=[('center_top_referrers', {'ccn': '450076', 'year': 2021, 'n': 15}), ('center_referral_summary', {'ccn': '450076', 'year': 2021})],
            focus={'kind': 'center', 'ids': ['450076']}, edges='referral', caveats=['MEDICARE', 'REFERRAL'])


def test_the_tracked_set_names_only_skills_queries_parameters_and_caveats_that_exist():
    items = load_set()
    assert check_set(items) == [] and len(items) >= 80
    assert {i['group'] for i in items} >= {'community', 'hcp', 'center', 'payments', 'ties', 'prescribing', 'referral', 'product', 'scope', 'ambiguity',
                                           'follow-up', 'new-topic'}
    switched = [i for i in items if i['group'] == 'new-topic']  # a new entity named after another answer must not be answered about the old one
    assert len(switched) >= 3 and all(i['memory'] and not any(ident in i['memory'] for entry in i['ids'] for ident in routing_eval.any_of(entry)) for i in switched)
    assert sum(1 for i in items if i.get('outcome') in ('decline', 'ask')) >= 10  # refusals and questions back are tested too
    held_out = load_set(routing_eval.SETS['holdout'])
    assert check_set(held_out) == [] and len(held_out) >= 30
    assert not {i['question'] for i in held_out} & {i['question'] for i in items}  # no question is in both sets


def test_a_set_with_a_wrong_name_is_reported():
    queries.load_parts()
    broken = [{'id': 'a', 'group': 'g', 'question': 'q', 'skill': ['no-such-skill'], 'queries': ['no_such_query']},
              {'id': 'a', 'group': 'g', 'question': 'q', 'skill': ['center-profile'], 'queries': ['payments_by_year'], 'params': {'ccn': '1'},
               'caveats': ['NOPE'], 'outcome': 'shrug'}]
    problems = check_set(broken)
    assert problems == ['a: unknown skill no-such-skill', 'a: unknown query no_such_query', 'a: duplicate id', 'a: unknown outcome shrug',
                        'a: no accepted skill may run payments_by_year', 'a: none of its queries takes the parameter ccn', 'a: unknown caveat NOPE']


def test_a_correct_answer_passes_every_check_and_each_mistake_fails_its_own():
    checks = score(CENTER, final(**GOOD))
    assert all(checks[c] is True for c in ('outcome', 'skill', 'entity', 'queries', 'params', 'map', 'caveats')) and checks['text'] is None
    # Another skill that ran the required queries showed the right view, so it counts; without them it does not.
    assert score(CENTER, final(**{**GOOD, 'skill': 'center-profile'}))['skill'] is True
    wrong_view = score(CENTER, final(**dict(GOOD, skill='center-profile', results=[('center_profile', {'ccn': '450076'})])))
    assert wrong_view['skill'] is False and wrong_view['queries'] is False and wrong_view['entity'] is True
    other_center = dict(GOOD, results=[(n, {**p, 'ccn': '100271'}) for n, p in GOOD['results']], focus={'kind': 'center', 'ids': ['100271']})
    wrong = score(CENTER, final(**other_center))
    assert wrong['entity'] is False and wrong['map'] is False and wrong['queries'] is True
    assert score(CENTER, final(**dict(GOOD, results=GOOD['results'][1:])))['queries'] is False  # the required query was not run
    assert score(CENTER, final(**dict(GOOD, results=GOOD['results'][:1])))['queries'] is False  # none of the either-or queries
    assert score(CENTER, final(**dict(GOOD, results=[(n, {**p, 'year': 2022}) for n, p in GOOD['results']])))['params'] is False
    assert score(CENTER, final(**dict(GOOD, edges='coauthor')))['map'] is False
    assert score(CENTER, final(**dict(GOOD, focus={'kind': 'community', 'ids': ['450076']})))['map'] is False
    assert score(CENTER, final(**dict(GOOD, caveats=['MEDICARE'])))['caveats'] is False
    assert score(CENTER, final(skill='referral-flow', ask={'question': 'Which one?', 'choices': ['a', 'b']}))['outcome'] is False
    assert score(CENTER, final())['outcome'] is False  # nothing shown


def test_questions_back_refusals_and_alternatives_are_scored():
    ask = {'id': 'x', 'group': 'ambiguity', 'question': 'Dr. Smith?', 'skill': ['hcp-profile'], 'outcome': 'ask'}
    assert score(ask, final(skill='hcp-profile', ask={'question': 'Which Smith?', 'choices': ['a', 'b']}))['outcome'] is True
    assert score(ask, final(skill='hcp-profile', results=[('hcp_profile', {'npi': '1'})]))['outcome'] is False  # picked one silently
    assert score(ask, final(skill=None, ask={'question': 'Which Smith?', 'choices': ['a', 'b']}))['skill'] is None  # no skill is needed to ask back
    decline = {'id': 'x', 'group': 'scope', 'question': 'Commercial claims?', 'skill': ['scope-and-help'], 'outcome': 'decline',
               'text_has': ['not in the database', 'does not hold']}
    said = score(decline, final(skill=None, headline='The database does not hold commercial claims.'))
    assert said['outcome'] is True and said['text'] is True and said['map'] is True and said['skill'] is None
    shown = score(decline, final(skill='prescribing-claims', results=[('partd_by_drug_year', {'scope': 'center', 'id': '100271'})],
                                 focus={'kind': 'center', 'ids': ['100271']}, text='Here are Medicare claims.'))
    assert shown == {**shown, 'outcome': False, 'skill': None, 'text': False, 'map': False}
    help_ = {'id': 'x', 'group': 'scope', 'question': 'What can I ask?', 'skill': ['scope-and-help'], 'outcome': 'help'}
    assert score(help_, final(skill='scope-and-help', results=[('data_inventory', {})]))['outcome'] is True  # the inventory is not data about an entity
    either = {'id': 'x', 'group': 'payments', 'question': 'Studies at City of Hope?', 'skill': ['industry-payments'], 'ids': [['050146', '12']],
              'params': {'scope': ['center', 'community'], 'state': 'CA'}, 'focus': 'state'}
    by_center = final(skill='industry-payments', results=[('research_studies', {'scope': 'center', 'id': '050146', 'state': 'CA'})], focus={'kind': 'state', 'ids': ['CA']})
    assert score(either, by_center) == {**score(either, by_center), 'entity': True, 'params': True, 'map': True}


def test_the_summary_counts_by_group_and_reports_times(monkeypatch):
    def record(ident, group, checks, seconds, first):
        full = dict.fromkeys(routing_eval.CHECKS) | checks
        return {'id': ident, 'group': group, 'question': f'Question {ident}', 'checks': full, 'skill': 'referral-flow',
                'routed': all(full[c] is not False for c in routing_eval.ROUTING), 'all_checks': all(v is not False for v in full.values()),
                'failed': [c for c, v in full.items() if v is False], 'seconds': seconds, 'first_view_seconds': first, 'model_calls': 3,
                'output_tokens': 200, 'tool_errors': 0, 'plain_text_reply': False, 'dropped_sentences': 0}
    records = [record('a', 'referral', {'outcome': True, 'skill': True, 'entity': True, 'map': True}, 8.0, 3.0),
               record('b', 'referral', {'outcome': True, 'skill': True, 'entity': True, 'map': False}, 12.0, 4.0),
               record('c', 'scope', {'outcome': True, 'skill': False}, 2.0, None)]
    s = summary(records)
    assert s['overall'] == {**s['overall'], 'questions': 3, 'routed': 0.667, 'all_checks': 0.333, 'skill': 0.667, 'entity': 1.0, 'map': 0.5, 'caveats': None}
    assert s['by_group']['referral']['routed'] == 1.0 and s['by_group']['scope']['routed'] == 0.0
    assert s['seconds'] == {'median': 8.0, 'p90': 12.0, 'max': 12.0, 'share_within_10s': 0.667} and s['first_view_seconds']['median'] == 3.5
    text = report({'label': 'test', 'model': 'scripted', 'summary': s, 'records': records})
    assert 'ROUTING SET test: 3 questions' in text and 'within 10 s 67%' in text and 'b       map' in text and 'c       skill' in text
    # A run asks each question once and counts an answer that raises as failed, not as a crash of the run.
    monkeypatch.setattr(routing_eval, 'ask_one', lambda item, con, model=None: (_ for _ in ()).throw(RuntimeError('model gone')) if item['id'] == 'b' else records[0])
    result = routing_eval.run([{'id': 'a', 'group': 'referral', 'question': 'q'}, {'id': 'b', 'group': 'referral', 'question': 'q'}], con=None, echo=lambda line: None)
    assert [r['failed'] for r in result['records']] == [[], ['error']] and result['summary']['overall']['routed'] == 0.5
