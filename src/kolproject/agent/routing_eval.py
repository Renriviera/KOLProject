"""Score the agent on the routing test set.

    uv run kolproject-agent-eval                    # every question, with the local model
    uv run kolproject-agent-eval --only referral    # one group, or one question id
    uv run kolproject-agent-eval --set holdout      # the held-out questions instead
    uv run kolproject-agent-eval --check            # only check that both sets agree with the queries and the pack

Each question in `agent/eval/routing_set.json` names what a correct answer must use: the skill, the entity ids,
the queries, the map focus and links, and the caveats. The run asks every question once, compares, and reports
the share that passed, by group, with the time each answer took. It reads the pack and talks to the local
model; nothing leaves this machine.

The skill is counted right when it is one of those the question lists, or when the answer ran the queries the
question requires (a profile skill may also run the payment, prescribing, ties and referral queries).

There are two sets. `routing_set.json` is the working set: the prompt and the skills were adjusted against its
failures, so its score flatters the agent. `routing_holdout.json` was written before the agent saw it and is
never tuned against; its score is the one to quote for questions the agent was not adjusted for.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from datetime import date

from . import loop, prompt, queries
from .packbase import connect
from .paths import AGENT, DB
from ..common import ROOT

SETS = {'working': AGENT / 'eval' / 'routing_set.json', 'holdout': AGENT / 'eval' / 'routing_holdout.json'}
SET = SETS['working']
OUT = {'working': ROOT / 'evaluations' / 'agent_routing.json', 'holdout': ROOT / 'evaluations' / 'agent_routing_holdout.json'}
OUTCOMES = ('view', 'ask', 'decline', 'help')
ROUTING = ('outcome', 'skill', 'entity')  # the three checks that make up "routed correctly"
CHECKS = ROUTING + ('queries', 'params', 'map', 'caveats', 'text')
ID_TABLES = {'community': ('dim_community', 'community_id'), 'center': ('dim_center', 'ccn'), 'hcp': ('dim_hcp', 'npi')}


def load_set(path=SET):
    return json.loads(path.read_text(encoding='utf-8'))['questions']


def any_of(entry):
    """An expected value written alone or as a list of alternatives."""
    return [str(v) for v in entry] if isinstance(entry, list) else [str(entry)]


def check_set(items, con=None):
    """Problems in the test set itself: unknown skills or queries, parameters no query takes, ids not in the pack."""
    registry, skills, problems = queries.load_parts(), prompt.skills(), []
    seen = set()
    for item in items:
        where = item.get('id', '?')
        if where in seen:
            problems.append(f'{where}: duplicate id')
        seen.add(where)
        if item.get('outcome', 'view') not in OUTCOMES:
            problems.append(f"{where}: unknown outcome {item['outcome']}")
        for skill in item.get('skill', []):
            if skill not in skills:
                problems.append(f'{where}: unknown skill {skill}')
        named = item.get('queries', []) + item.get('queries_any', [])
        for name in named:
            if name not in registry:
                problems.append(f'{where}: unknown query {name}')
            elif not set(registry[name]['skills']) & set(item.get('skill', [])):
                problems.append(f'{where}: no accepted skill may run {name}')
        for key in item.get('params', {}):
            if named and not any(key in registry[n]['params'] for n in named if n in registry):
                problems.append(f'{where}: none of its queries takes the parameter {key}')
        for code in item.get('caveats', []):
            if code not in prompt.CAVEATS:
                problems.append(f'{where}: unknown caveat {code}')
        if con is not None:
            for entry in item.get('ids', []):
                for ident in any_of(entry):
                    if not any(con.execute(f'select 1 from {table} where {key} = ?', (ident,)).fetchone()
                               for table, key in ID_TABLES.values()):
                        problems.append(f'{where}: id {ident} is not a community, center or HCP in the pack')
    return problems


def score(item, final):
    """{check: True, False, or None when the question does not test it} for one answered question."""
    view, results = final['view'], final['results']
    outcome = item.get('outcome', 'view')
    data = {h: r for h, r in results.items() if r['name'] != 'data_inventory'}
    names = {r['name'] for r in results.values()}
    focus = view['map'].get('focus', {})
    moved = not view['map'].get('unchanged')
    used = {str(v) for r in results.values() for v in r['params'].values()} | ({str(i) for i in focus.get('ids', [])} if moved else set())
    text = (view['answer']['headline'] + ' ' + view['answer']['text']).lower()
    checks = dict.fromkeys(CHECKS)
    asked = bool(view.get('ask'))
    checks['outcome'] = {'view': not asked and bool(data), 'ask': asked, 'decline': not asked and not data,
                         'help': not asked and not data}[outcome]
    if item.get('ids'):
        checks['entity'] = all(set(any_of(entry)) & used for entry in item['ids'])
    if item.get('queries') or item.get('queries_any'):
        checks['queries'] = (all(q in names for q in item.get('queries', []))
                             and (not item.get('queries_any') or bool(names & set(item['queries_any']))))
    if outcome in ('view', 'help'):  # a question back or a refusal is right whichever skill was open, or none
        # The skill is right if it is one of those listed, or if it ran the queries the question requires: a
        # profile skill may run the topic queries, and a skill that shows the right view is a right choice.
        checks['skill'] = final.get('skill') in item['skill'] or checks['queries'] is True
    if item.get('params'):
        checks['params'] = all(any(str(r['params'].get(key)) in any_of(wanted) for r in results.values())
                               for key, wanted in item['params'].items())
    if outcome == 'decline':
        checks['map'] = not moved
    elif item.get('focus') or item.get('edges'):
        expected = {i for entry in item.get('ids', []) for i in any_of(entry)} | set(any_of(item.get('params', {}).get('state', [])))
        right_place = (not item.get('focus') or (moved and focus.get('kind') == item['focus']
                                                 and (not expected or bool(expected & {str(i) for i in focus.get('ids', [])}))))
        checks['map'] = right_place and (not item.get('edges') or (moved and view['map'].get('edges') in item['edges']))
    if item.get('caveats'):
        checks['caveats'] = all(code in view['answer']['caveats'] for code in item['caveats'])
    if item.get('text_has'):
        checks['text'] = any(phrase.lower() in text for phrase in any_of(item['text_has']))
    return checks


def ask_one(item, con, model=loop.MODEL):
    """Answer one question and return its record: what the agent chose, each check, and how long it took."""
    memory = {'focus': item['memory']} if item.get('memory') else None
    start = time.time()
    first = text = None
    final = None
    for event in loop.events(item['question'], con=con, memory=memory, model=model):
        if first is None and event['type'] in ('map', 'panels', 'answer', 'final'):
            first = time.time() - start  # the first thing the user sees change
        if text is None and event['type'] in ('answer', 'final'):
            text = time.time() - start  # the first words of the answer
        if event['type'] == 'final':
            final = event
    seconds = time.time() - start
    checks = score(item, final)
    trace = final['trace']
    calls = [t['model_call'] for t in trace if 'model_call' in t]
    record = {'id': item['id'], 'group': item['group'], 'question': item['question'],
              'routed': all(checks[c] is not False for c in ROUTING), 'all_checks': all(v is not False for v in checks.values()),
              'failed': [c for c, v in checks.items() if v is False], 'checks': checks,
              'skill': final.get('skill'), 'queries': sorted({r['name'] for r in final['results'].values()}),
              'map': {k: final['view']['map'].get(k) for k in ('focus', 'edges', 'unchanged') if k in final['view']['map']},
              'asked': bool(final['view'].get('ask')), 'headline': final['view']['answer']['headline'],
              'seconds': round(seconds, 1), 'first_view_seconds': round(first, 1) if first is not None else None,
              'first_text_seconds': round(text, 1) if text is not None else None,
              'model_calls': len(calls), 'output_tokens': sum(c['output_tokens'] for c in calls),
              'tool_errors': sum(1 for t in trace if t.get('error')),
              'plain_text_reply': any(t.get('plain_text_reply') for t in trace),
              'dropped_sentences': sum(len(t.get('dropped_sentences', [])) for t in trace)}
    return record


def rate(records, key):
    """Share of the records that test `key` and pass it; None when none tests it."""
    tested = [r['checks'][key] for r in records if r['checks'].get(key) is not None] if key in CHECKS else [r[key] for r in records]
    return round(sum(tested) / len(tested), 3) if tested else None


def summary(records):
    """Pass rates overall and by group, and answer times."""
    seconds = sorted(r['seconds'] for r in records)
    firsts = sorted(r['first_view_seconds'] for r in records if r['first_view_seconds'] is not None)
    texts = sorted(r['first_text_seconds'] for r in records if r.get('first_text_seconds') is not None)
    groups = defaultdict(list)
    for record in records:
        groups[record['group']].append(record)

    def block(rows):
        return {'questions': len(rows), 'routed': rate(rows, 'routed'), 'all_checks': rate(rows, 'all_checks'),
                **{check: rate(rows, check) for check in CHECKS}}

    def spread(values):
        if not values:
            return None
        return {'median': round(statistics.median(values), 1), 'p90': round(values[min(len(values) - 1, int(0.9 * len(values)))], 1),
                'max': round(values[-1], 1), 'share_within_10s': round(sum(v <= 10 for v in values) / len(values), 3)}

    return {'overall': block(records), 'by_group': {name: block(rows) for name, rows in groups.items()},
            'seconds': spread(seconds), 'first_view_seconds': spread(firsts), 'first_text_seconds': spread(texts),
            'model_calls_median': statistics.median(r['model_calls'] for r in records),
            'output_tokens_median': statistics.median(r['output_tokens'] for r in records),
            'plain_text_replies': sum(r['plain_text_reply'] for r in records),
            'answers_with_a_dropped_sentence': sum(r['dropped_sentences'] > 0 for r in records),
            'answers_with_a_tool_error': sum(r['tool_errors'] > 0 for r in records)}


def report(result):
    """The summary as lines of text."""
    s = result['summary']
    lines = [f"ROUTING SET {result['label']}: {s['overall']['questions']} questions, model {result['model']}",
             f"{'group':<12}{'n':>4}{'routed':>8}{'all':>7}" + ''.join(f'{c:>9}' for c in CHECKS)]
    for name, row in list(s['by_group'].items()) + [('ALL', s['overall'])]:
        cells = ''.join(f"{'-' if row[c] is None else format(row[c], '.0%'):>9}" for c in CHECKS)
        lines.append(f"{name:<12}{row['questions']:>4}{row['routed']:>8.0%}{row['all_checks']:>7.0%}{cells}")
    t = s['seconds']
    lines.append(f"seconds per answer: median {t['median']}, 90th percentile {t['p90']}, slowest {t['max']}, within 10 s {t['share_within_10s']:.0%}")
    if s['first_view_seconds']:
        f = s['first_view_seconds']
        lines.append(f"seconds to the first map or panel: median {f['median']}, 90th percentile {f['p90']}")
    if s.get('first_text_seconds'):
        f = s['first_text_seconds']
        lines.append(f"seconds to the first words of the answer: median {f['median']}, 90th percentile {f['p90']}")
    lines.append(f"model calls per answer (median) {s['model_calls_median']}, output tokens (median) {s['output_tokens_median']}, "
                 f"answers written as plain text {s['plain_text_replies']}, answers with a dropped sentence {s['answers_with_a_dropped_sentence']}, "
                 f"with a tool error {s['answers_with_a_tool_error']}")
    failed = [r for r in result['records'] if not r['all_checks']]
    if failed:
        lines.append('not passing every check:')
        lines += [f"  {r['id']:<8}{','.join(r['failed']):<28} skill {r['skill']}; {r['question'][:70]}" for r in failed]
    return '\n'.join(lines)


def run(items, con, model=loop.MODEL, label='run', out=None, echo=print):
    records = []
    for index, item in enumerate(items, 1):
        try:
            record = ask_one(item, con, model)
        except Exception as error:  # one broken answer must not stop the run; it counts as failed
            record = {'id': item['id'], 'group': item['group'], 'question': item['question'], 'routed': False, 'all_checks': False,
                      'failed': ['error'], 'checks': dict.fromkeys(CHECKS), 'skill': None, 'queries': [], 'map': {}, 'asked': False,
                      'headline': f'{type(error).__name__}: {error}'[:160], 'seconds': 0.0, 'first_view_seconds': None,
                      'first_text_seconds': None, 'model_calls': 0,
                      'output_tokens': 0, 'tool_errors': 0, 'plain_text_reply': False, 'dropped_sentences': 0}
        records.append(record)
        echo(f"{index:>3}/{len(items)} {record['id']:<8}{'ok' if record['all_checks'] else 'FAIL ' + ','.join(record['failed']):<30}"
             f"{record['seconds']:>6.1f}s  {record['skill']}")
        result = {'label': label, 'date': date.today().isoformat(), 'model': model, 'questions_in_set': len(items),
                  'summary': summary(records), 'records': records}
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--only', help='a group name or a question id; several separated by commas')
    parser.add_argument('--limit', type=int, help='stop after this many questions')
    parser.add_argument('--label', default='latest')
    parser.add_argument('--set', choices=list(SETS), default='working', help='which questions to ask (default: the working set)')
    parser.add_argument('--out', help='where to write the result (default: evaluations/agent_routing.json, or '
                                      'agent_routing_holdout.json for the held-out set, for a full run)')
    parser.add_argument('--check', action='store_true', help='check both sets against the queries and the pack, ask nothing')
    args = parser.parse_args()
    if not DB.exists():
        raise SystemExit('The data pack is missing. Build it first: uv run kolproject-agent-pack')
    con = connect(readonly=True)
    for name, path in SETS.items():
        problems = check_set(load_set(path), con)
        if problems:
            raise SystemExit(f'The {name} set has problems:\n' + '\n'.join(problems))
    if args.check:
        print('ROUTING SETS OK: ' + ', '.join(f'{len(load_set(path))} {name} questions' for name, path in SETS.items()))
        return
    items = load_set(SETS[args.set])
    full = not (args.only or args.limit)
    if args.only:
        wanted = set(args.only.split(','))
        items = [i for i in items if i['group'] in wanted or i['id'] in wanted]
    items = items[:args.limit] if args.limit else items
    from pathlib import Path
    out = Path(args.out) if args.out else (OUT[args.set] if full else None)
    result = run(items, con, label=args.label, out=out)
    print(report(result))


if __name__ == '__main__':
    main()
