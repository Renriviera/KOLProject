"""Render the system prompt and the skill texts from the tracked files, so prompt and database cannot disagree."""
from __future__ import annotations

import json
import re

from . import queries
from .pack_dims import product_file
from .paths import MANIFEST, PROMPTS, REFERENCE, SKILLS

CAVEATS = {
    'REFERRAL': 'Referral views show shared Medicare fee-for-service patients between clinicians for any condition, 2017 to 2022. '
                'They are not CAR-T referrals and not confirmed referrals. Counts add up clinician pairs, so a patient can be counted more than once. Source: CareSet Systems DocGraph (CC BY-NC-SA 4.0, non-commercial).',
    'MEDICARE': 'Medicare figures cover fee-for-service only and hide any cell with 10 or fewer patients, so a blank means "not shown", not zero.',
    'MEDICAID': 'Medicaid figures are lower bounds from one federal file and cannot be compared across states.',
    'NOT_PRODUCT_SPECIFIC': 'Physician and hospital claims do not identify the CAR-T product. Only industry payments and national spending are split by product.',
    'PAYMENTS': 'Industry payments are amounts reported by manufacturers (CMS Open Payments, 2019 to 2025). Research amounts are whole-study amounts, not personal income.',
    'TIES': 'Disclosed ties come from conflict-of-interest statements on papers. A missing statement is not evidence of no tie, and a company tie is not a tie to a CAR-T product.',
    'LABELS': 'Division labels (core, affiliate, referring) and locations are 2024 facts applied to earlier years.',
    'CORE_ONLY': 'Communities and PageRank are defined for the 500 core HCPs. Other HCPs and centers are attached to the community they are most linked to.',
}


def front_matter(text):
    """(meta, body) of a skill file with simple `key: value` front matter between --- lines."""
    match = re.match(r'^---\n(.*?)\n---\n(.*)$', text, re.S)
    assert match, 'Skill file needs front matter'
    meta = {}
    for line in match.group(1).splitlines():
        key, _, raw = line.partition(':')
        raw = raw.split('#')[0].strip()
        meta[key.strip()] = [v.strip() for v in raw[1:-1].split(',') if v.strip()] if raw.startswith('[') else raw
    return meta, match.group(2).strip()


def skills():
    """Skill name to {'meta', 'body'}, in a stable order with scope-and-help last."""
    out = {}
    for path in sorted(SKILLS.glob('*.md'), key=lambda p: (p.stem == 'scope-and-help', p.stem)):
        meta, body = front_matter(path.read_text(encoding='utf-8'))
        assert meta['name'] == path.stem, f'{path.name}: name must equal the file name'
        out[path.stem] = {'meta': meta, 'body': body}
    return out


def signature(spec):
    """One line a small model can copy: name(param: kind = default, ...) and what it returns."""
    parts = []
    for name, rule in spec['params'].items():
        kind = ' | '.join(map(str, rule['enum'])) if 'enum' in rule else rule['type']
        parts.append(f'{name}: {kind}' + (f" = {rule['default']}" if 'default' in rule else ''))
    first = spec['description'].split('. ')[0].rstrip('.')  # what it returns, so the model knows what each query can answer
    first = first + '.' if len(first) <= 170 else first[:170].rsplit(' ', 1)[0].rstrip(' ,;:') + ' …'
    return f"- `{spec['name']}({', '.join(parts)})`: {first}"


QUIET_PARAMS = ('n', 'rule', 'year_from', 'year_to')  # optional parameters left out of the short form of a query


def short_form(spec):
    """`name(parameters)` without types or description: enough to call a query whose name says what it shows."""
    return f"`{spec['name']}({', '.join(p for p in spec['params'] if p not in QUIET_PARAMS)})`"


def skill_text(name):
    """What `load_skill` returns: the playbook plus the exact queries this skill may run."""
    skill = skills()[name]
    allowed = queries.catalog(name)
    lines = [skill['body'], '', '## Queries you may run in this skill (use these names and parameters exactly)']
    lines += [signature(spec) for spec in allowed] or ['- none']
    more = queries.extras(name)
    if more:  # a profile skill can also show the topic views, in short form to keep this text quick to read
        lines += ['', '## Also available here, for a question about a topic below (run one only when the question asks for it)']
        for topic, label in queries.TOPICS.items():
            group = [short_form(spec) for spec in more if spec['skills'][0] == topic]
            if group:
                lines.append(f'- {label}: ' + ', '.join(group))
    if skill['meta'].get('caveats'):
        lines += ['', 'The interface prints these caveats with this skill: ' + ', '.join(skill['meta']['caveats']) + '.']
    return '\n'.join(lines)


def community_table():
    rows = json.loads((REFERENCE / 'communities.json').read_text(encoding='utf-8'))['communities']
    lines = ['| id | community | region | leaders by PageRank (top 3) |', '|---|---|---|---|']
    for c in rows:
        if c['kind'] == 'named':
            leaders = ', '.join(l['name'] for l in c['leaders_by_pagerank'][:3])
            lines.append(f"| {c['community_id']} | {c['short_label']} | {c['region']} | {leaders} |")
    lines.append('Ids 14 to 17 are four isolated HCPs shown as "Outside main network". "Orbit", "network" and "group" all mean community.')
    return '\n'.join(lines)


def product_tables():
    """(product table, event list). Unverified events are left out unless only the draft file exists."""
    data, name = product_file()
    draft = name.endswith('.draft.json')
    lines = ['| brand | generic name (short name) | company | target | first US approval | lymphoma product | Medicare code |', '|---|---|---|---|---|---|---|']
    events = []
    for p in data['products']:
        lines.append(f"| {p['brand']} | {p['generic']} ({p['short_name']}) | {p['company_group']} | {p['target']} | {p['first_us_approval']} | "
                     f"{'yes' if p['lymphoma_product'] else 'no'} | {p.get('hcpcs_code') or 'none'} |")
        events += [(e['date'], f"{e['label']}") for e in p['events'] if draft or e.get('verified')]
    events += [(e['date'], e['label']) for e in data.get('class_and_coding_events', []) if draft or e.get('verified')]
    pending = [f"{p['generic']} ({p['short_name']}, {p['company_group']}): {p['note']}" for p in data.get('pending', [])]
    listed = '\n'.join(f'- {date}: {label}' for date, label in sorted(events))
    if pending:
        listed += '\n\nNot approved, do not show as a product: ' + ' '.join(pending)
    return '\n'.join(lines), listed


def product_reference():
    """The dates of the product table as a result the number check can read: an answer given from the prompt's
    own reference tables, with no query run, may state them."""
    data, name = product_file()
    draft = name.endswith('.draft.json')
    dates = [p['first_us_approval'] for p in data['products']] + [p['hcpcs_effective'] for p in data['products'] if p.get('hcpcs_effective')]
    dates += [e['date'] for p in data['products'] for e in p['events'] if draft or e.get('verified')]
    dates += [e['date'] for e in data.get('class_and_coding_events', []) if draft or e.get('verified')]
    return {'name': 'product_reference', 'rows': [{'date': d} for d in sorted(set(dates))], 'row_count': 0}


def data_as_of():
    return json.loads(MANIFEST.read_text(encoding='utf-8')).get('data_as_of', 'unknown') if MANIFEST.exists() else '2026-11-20'


def system_prompt():
    """The prompt text between the two rules of system_prompt.md with every slot filled."""
    text = (PROMPTS / 'system_prompt.md').read_text(encoding='utf-8')
    body = text.split('\n---\n')[1].strip()
    table, events = product_tables()
    skill_list = '\n'.join(f"- `{name}`: {s['meta']['use_when']}" for name, s in skills().items())
    filled = (body.replace('{{COMMUNITY_TABLE}}', community_table()).replace('{{PRODUCT_TABLE}}', table)
              .replace('{{PRODUCT_EVENTS}}', events).replace('{{SKILL_LIST}}', skill_list).replace('{{DATA_AS_OF}}', data_as_of()))
    assert '{{' not in filled, 'Unfilled slot in the system prompt'
    return filled


if __name__ == '__main__':
    prompt = system_prompt()
    print(prompt)
    print(f'\n[{len(prompt)} characters, about {len(prompt) // 4} tokens]')
