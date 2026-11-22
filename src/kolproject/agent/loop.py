"""The agent turn: Qwen3.5-9B picks a skill, calls typed tools, and the code assembles a validated view.

The model never emits data values. It refers to query results by handle; charts and tables read the rows
from the result. Code, not the model, enforces the order of steps, attaches caveats, draws each query result
as its standard panel, supplies a default map when the model leaves it out, and drops sentences whose numbers
no result supports.

Most of an answer's time is the model writing, at about 40 tokens a second, so the loop keeps what the model
has to write short and acts on each tool call as soon as the model has written it: the map and the panels
reach the interface while the model is still working on the rest. The answer itself is written as plain text
and passed on sentence by sentence, each one only after the number check has accepted it.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import jsonschema

from . import mapdata, prompt, queries, search, verify
from .packbase import connect
from .paths import SCHEMAS

MODEL = 'qwen3.5:9b'
OLLAMA = 'http://localhost:11434/api/chat'
MAX_STEPS = 9
MAX_UNREADABLE = 3  # replies in a row the model server could not parse before the turn gives up
KEEP_ALIVE = '2h'  # how long the model stays loaded after a question
PREVIEW_FOR_MODEL = 14
TEXT_CELL = 70  # characters of a text cell the model reads in a preview
TEXT_LIMIT = 1200  # characters of answer text; the view schema allows the same
# num_predict bounds one reply: the longest useful one is under 400 tokens, and a model that starts to repeat
# itself would otherwise write until the context is full, for minutes.
OPTIONS = {'temperature': 0.1, 'top_p': 0.9, 'num_ctx': 16384, 'presence_penalty': 0.0, 'num_predict': 800}
TIME_COLUMNS = ('year', 'program_year', 'month', 'publication_year', 'service_year')
KINDS = ['hcp', 'center', 'community', 'product', 'manufacturer', 'place']
PANEL_TYPES = ['kpis', 'table', 'bar', 'line', 'timeline', 'ranked_list', 'text']
EDGES = ['coauthor', 'referral', 'payments', 'none']
NEXT_AFTER_QUERY = ('If the question needs nothing more, write the answer now as plain text: first a headline of at most 20 words on its own '
                    'line, then one or two sentences, at most 50 words. Do not list the rows; the panel shows them.')


def tool(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
                                             'parameters': {'type': 'object', 'properties': properties, 'required': required}}}


def tool_specs():
    names = list(prompt.skills())
    return [
        tool('load_skill', 'Load the one skill that fits the question. Call this first, together with find_entity for each name in the question.',
             {'name': {'type': 'string', 'enum': names}}, ['name']),
        tool('find_entity', 'Find a person, center, community, product, manufacturer or place the user named. Returns candidates with ids.',
             {'text': {'type': 'string'}, 'kind': {'type': 'string', 'enum': KINDS}}, ['text', 'kind']),
        tool('search_cards', 'Search short fact cards when the user describes something without naming it.',
             {'query': {'type': 'string'}, 'kinds': {'type': 'array', 'items': {'type': 'string'}}}, ['query']),
        tool('run_query', 'Run named queries from the loaded skill, several at once. Each result is drawn on the canvas as a panel. '
             '`params` is shared: each query takes the parameters it has. Returns a handle, the row count and a preview for each.',
             {'names': {'type': 'array', 'items': {'type': 'string'}}, 'params': {'type': 'object'}}, ['names', 'params']),
        tool('set_map', 'Optional: the map already follows what you look up and query. Use this only to change which links are drawn, what is highlighted or what stays visible.',
             {'focus': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['community', 'center', 'hcp', 'state', 'usa']},
                                                         'ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['kind', 'ids']},
              'show': {'type': 'string', 'enum': ['focus_only', 'focus_and_linked', 'all']},
              'edges': {'type': 'string', 'enum': EDGES},
              'highlight': {'description': '"leaders", "main_centers", "none", or a list of ids'},
              'year': {'type': 'integer'}}, ['focus', 'edges']),
        tool('add_panel', 'Optional. Draw a query result another way than its standard panel, or add a second chart of it.',
             {'type': {'type': 'string', 'enum': PANEL_TYPES}, 'handle': {'type': 'string'}, 'title': {'type': 'string'},
              'options': {'type': 'object'}}, ['type', 'handle']),
        tool('ask_user', 'Ask the user one short question when a name matches several things.',
             {'question': {'type': 'string'}, 'choices': {'type': 'array', 'items': {'type': 'string'}}}, ['question', 'choices']),
    ]  # There is no tool for the answer: it is written as plain text, so it can be shown while it is written.


def chat_events(messages, tools, model=MODEL, temperature=None):
    """One model call through the local Ollama server, thinking off, read as it is written.

    Yields ('text', piece) as the model writes plain text and ('call', tool_call) as soon as each tool call is
    complete, then ('reply', message) once with the whole reply: its text, every tool call, where the time went,
    and 'server_error' if the call failed. `temperature` overrides the usual low setting for a retry.
    """
    options = OPTIONS if temperature is None else {**OPTIONS, 'temperature': temperature}
    body = {'model': model, 'stream': True, 'think': False, 'options': options, 'messages': messages, 'tools': tools,
            'keep_alive': KEEP_ALIVE}
    request = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    content, calls, last, problem = [], [], {}, None
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            for line in response:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get('error'):
                    problem = str(chunk['error'])[:300]
                    break
                message = chunk.get('message') or {}
                if message.get('content'):
                    content.append(message['content'])
                    yield 'text', message['content']
                for call in message.get('tool_calls') or []:
                    calls.append(call)
                    yield 'call', call
                if chunk.get('done'):
                    last = chunk
    except urllib.error.HTTPError as error:
        # The model server answers 500 when the model writes a tool call it cannot parse. Report it as a
        # reply the loop can recover from instead of failing the whole turn.
        problem = error.read().decode('utf-8', 'replace')[:300]
    reply = {'content': ''.join(content), 'tool_calls': calls, 'stats': call_stats(last)}
    if problem:
        reply['server_error'] = problem
    yield 'reply', reply


def as_events(message):
    """A whole reply as the events `chat_events` yields; for a scripted model in tests."""
    if message.get('content'):
        yield 'text', message['content']
    for call in message.get('tool_calls') or []:
        yield 'call', call
    yield 'reply', message


def warm(model=MODEL):
    """Load the model and have it read the system prompt and the tools once.

    The model server keeps what it has read, so every later question only pays for its own words. Returns the
    seconds it took, or None when the model server is not running.
    """
    body = {'model': model, 'stream': False, 'think': False, 'options': {**OPTIONS, 'num_predict': 1}, 'tools': tool_specs(),
            'messages': [{'role': 'system', 'content': prompt.system_prompt()}, {'role': 'user', 'content': 'Hello.'}],
            'keep_alive': KEEP_ALIVE}
    request = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return round(json.load(response).get('total_duration', 0) / 1e9, 1)
    except (urllib.error.URLError, OSError):
        return None


def call_stats(data):
    """Where one model call spent its time, in seconds, from the counters the model server reports."""
    second = 1e9
    return {'prompt_tokens': data.get('prompt_eval_count', 0), 'output_tokens': data.get('eval_count', 0),
            'load_s': round(data.get('load_duration', 0) / second, 2), 'prompt_s': round(data.get('prompt_eval_duration', 0) / second, 2),
            'output_s': round(data.get('eval_duration', 0) / second, 2), 'total_s': round(data.get('total_duration', 0) / second, 2)}


def brief(value):
    """Round floats so previews stay short for the model."""
    return round(value, 3) if isinstance(value, float) and abs(value) < 1000 else round(value) if isinstance(value, float) else value


def preview_rows(result, limit=PREVIEW_FOR_MODEL):
    """(rows the model reads, a note saying which rows they are).

    A long time series is sampled at both ends, so the model sees where it starts and where it ends instead of
    only its first rows; otherwise it reports the years it was not shown as missing.
    """
    rows = result['rows']
    if len(rows) <= limit:
        return rows, ''
    rest = 'The panel will show every row. Do not say a value is missing because it is not listed here.'
    time = next((c['name'] for c in result['columns'] if c['name'] in TIME_COLUMNS), None)
    if not time:
        return rows[:limit], f'{len(rows)} rows in all, in the order the table shows them. These are the first {limit}. {rest}'
    periods = sorted({r[time] for r in rows if r[time] is not None})
    ends = [r for r in rows if r[time] in (periods[0], periods[-1])]
    if len(periods) > 1 and 4 <= len(ends) <= 2 * limit:  # several series: every series at the first and the last period
        return ends, f'{len(rows)} rows in all. These are only the rows for {periods[0]} and {periods[-1]}, the first and the last period. {rest}'
    half = limit // 2
    picked = rows[:half] + rows[-half:]
    measure = next((c['name'] for c in result['columns'] if c['unit'] in ('usd', 'count') and c['name'] != time), None)
    peak = max(rows, key=lambda r: r[measure] or 0) if measure else None
    if peak is not None and peak not in picked:
        picked = rows[:half] + [peak] + rows[-half:]
    return picked, (f'{len(rows)} rows in all. These are the first {half} and the last {half}'
                    + (f', and the row with the largest {measure}' if len(picked) > 2 * half else '') + f'. {rest}')


def cell(value):
    """One value of a preview table: blank when the source has none, quoted when the text holds a comma."""
    if value is None:
        return ''
    text = str(brief(value))
    if len(text) > TEXT_CELL:  # a paper or study title: the panel shows it whole, the model needs only its start
        text = text[:TEXT_CELL].rsplit(' ', 1)[0] + '…'
    return f'"{text}"' if ',' in text else text


def preview_table(result, rows):
    """The rows the model reads. One row is written as name: value pairs; several rows as a table with one header
    line, which is less than half the length of repeating every column name on every row."""
    names = [c['name'] for c in result['columns']]
    if len(rows) <= 1:
        return {k: brief(v) for k, v in rows[0].items()} if rows else {}
    return '\n'.join([', '.join(names)] + [', '.join(cell(row[name]) for name in names) for row in rows])


def dollar_totals(result):
    """Column totals of the dollar columns, so the model does not add a long column in its head."""
    if result['row_count'] < 2:
        return {}
    return {c['name']: round(sum(r[c['name']] or 0 for r in result['rows'])) for c in result['columns'] if c['unit'] == 'usd'}


class Turn:
    """State of one question: the loaded skill, query results, and the view being assembled."""

    def __init__(self, con, memory=None):
        self.con = con
        self.memory = memory or {}
        self.skill = None
        self.results = {}
        self.entities = []
        self.map = None
        self.panels = []
        self.standard = set()  # handles whose panel is still the query's standard one
        self.early_map = None  # the map drawn from the skill's default before the model set one
        self.unsure = False  # a name matched several things: no map until the user has chosen
        self.choices = None  # (the name asked for, the candidates) of that lookup
        self.pushed = False  # the model was told once to run its queries instead of answering in words alone
        self.caveats = set()
        self.skill_caveats = set()
        self.final = None
        self.ask = None
        self.trace = []

    # Each tool returns what the model reads next.
    def load_skill(self, name):
        if name not in prompt.skills():
            return {'error': f'No skill named {name}.'}
        self.skill = name
        self.skill_caveats = {c for c in prompt.skills()[name]['meta'].get('caveats', []) if c in prompt.CAVEATS}
        return {'skill': name, 'instructions': prompt.skill_text(name)}

    def find_entity(self, text, kind):
        if kind not in KINDS:
            return {'error': f"kind must be one of: {', '.join(KINDS)}. A division group (core, affiliates, referring, other) needs no "
                             'lookup: pass scope "division_group" with that word as the id.'}
        found = search.find_entity(text, kind, con=self.con)
        if not found:
            return {'candidates': [], 'note': f'Nothing in the database matches "{text}" as a {kind}. Try another kind or tell the user.'}
        if search.ambiguous(found):
            core = [c for c in found if c.get('division') == 'core' or c.get('kind') != 'hcp']
            if kind == 'hcp' and len([c for c in found if c.get('division') == 'core']) == 1:
                found = [c for c in found if c.get('division') == 'core'] + [c for c in found if c.get('division') != 'core']
            else:  # several equally good matches: the user must choose
                self.unsure = True
                self.choices = (str(text), found[:5])
                self.entities += found[:1]
                return {'candidates': found, 'ambiguous': True,
                        'note': 'Several entries match equally well. Call ask_user with these names and places as choices, unless the question makes clear which one is meant.'}
        self.entities += found[:1]
        return {'candidates': found, 'note': 'Candidates are listed best first. Use the id of the first one unless it is clearly wrong.'}

    def search_cards(self, query, kinds=None):
        out = {'cards': search.search_cards(query, kinds, con=self.con)}
        if not self.skill:
            out['note'] = 'Cards alone draw nothing for the user. Call load_skill now and run its queries.'
        return out

    def run_query(self, names=None, params=None, name=None):
        """Run one query, or several that share their parameters. The model saves most of its writing by naming
        the queries once and the parameters once."""
        if not self.skill:
            return {'error': 'Call load_skill first.'}
        wanted = names if names is not None else name
        wanted = [wanted] if isinstance(wanted, str) else [str(n) for n in (wanted or [])]
        if not wanted:
            return {'error': 'Give the query names as a list in `names`.'}
        if not isinstance(params, dict):
            params = {}
        outs = [self.run_one(n, params, shared=len(wanted) > 1) for n in wanted[:6]]
        out = outs[0] if len(outs) == 1 else {'results': outs}
        if any('handle' in o for o in outs):  # read just before the model writes its answer, so it is said here
            out['next'] = NEXT_AFTER_QUERY
        return out

    def run_one(self, name, params, shared=False):
        spec = queries.load_parts().get(name)
        if shared and spec:  # parameters given for several queries: each takes the ones it has
            params = {k: v for k, v in params.items() if k in spec['params']}
        try:
            result = queries.run(name, params, con=self.con, skill=self.skill)
        except queries.QueryError as error:
            return {'query': name, 'error': str(error)}
        again = next((h for h, r in self.results.items() if r['name'] == name and r['params'] == result['params']), None)
        handle = again or (name if name not in self.results else f'{name}_{len(self.results) + 1}')
        self.results[handle] = result
        self.caveats.update(result['caveats'])
        drawn = None if again else self.place_standard(handle, result)
        rows, sampled = preview_rows(result)
        if not result['row_count']:
            note = 'No rows: say that this is not in the database for the entity or period asked.'
        elif again:
            note = 'You already ran this. Go on to the next step.'
        elif drawn:
            note = f'Drawn on the canvas as a {drawn} panel.'
        else:
            note = f'Not drawn: the canvas is full. Use handle "{handle}" in add_panel to replace a panel.'
        out = {'handle': handle, 'row_count': result['row_count'], 'preview': preview_table(result, rows),
               'note': ' '.join(filter(None, [note, sampled]))}
        totals = dollar_totals(result)
        if totals:
            out['column_totals'] = totals
        return out

    def place_standard(self, handle, result):
        """Draw a result as its query's standard panel, fact boxes first. Returns the panel type, or None if not drawn."""
        if not result['row_count'] or len(self.panels) >= 6:
            return None
        default = result.get('default_panel') or {'type': 'table'}
        kind = default.get('type', 'table')
        kind = 'table' if kind == 'kpis' and result['row_count'] > 1 else kind
        panel = clean_panel(kind, result['title'], handle, default, result)
        boxes = sum(1 for p in self.panels if p['type'] == 'kpis')
        self.panels.insert(boxes if kind == 'kpis' else len(self.panels), panel)
        self.standard.add(handle)
        return kind

    def set_map(self, focus, edges, show='focus_only', highlight='none', year=None):
        if edges not in EDGES:
            return {'error': f'edges must be one of {EDGES}.'}
        ids = [str(i) for i in (focus or {}).get('ids', [])][:6]
        spec = {'focus': {'kind': (focus or {}).get('kind', 'usa'), 'ids': ids}, 'show': show if show in ('focus_only', 'focus_and_linked', 'all') else 'focus_only',
                'edges': edges, 'highlight': clean_highlight(highlight)}
        if isinstance(year, (int, float)) or str(year or '').isdigit():
            spec['year'] = min(2026, max(2017, int(year)))
        problem = mapdata.validate(self.con, spec)
        if problem:
            return {'error': problem}
        self.map = spec
        if edges == 'referral':
            self.caveats.add('REFERRAL')
        return {'ok': True, 'shown': mapdata.counts(self.con, spec)}

    def add_panel(self, type, title=None, handle=None, options=None):
        if type not in PANEL_TYPES:
            return {'error': f'type must be one of {PANEL_TYPES}.'}
        handle = self.resolve(handle)
        if type != 'text' and handle is None:
            return {'error': f'Unknown handle. Handles so far: {list(self.results) or "none; call run_query first"}.'}
        result = self.results.get(handle)
        if type == 'kpis' and result and result['row_count'] > 1:  # a fact box shows one row; several rows are a chart or a table
            type = (result.get('default_panel') or {}).get('type', 'table')
            type = 'table' if type == 'kpis' else type
        panel = clean_panel(type, title or (result or {}).get('title') or 'Note', handle, options or {}, result)
        if handle in self.standard:  # the model draws this result its own way: replace the standard panel where it stands
            self.standard.discard(handle)
            index = next(i for i, p in enumerate(self.panels) if p.get('handle') == handle)
            self.panels[index] = panel
            return {'ok': True, 'panels': len(self.panels), 'next': 'When every panel is as you want it, write the answer.'}
        if any(p['type'] == panel['type'] and p.get('handle') == handle for p in self.panels if handle):
            return {'ok': True, 'panels': len(self.panels), 'note': 'That panel is already on the canvas.'}
        if len(self.panels) >= 6:
            return {'error': 'At most 6 panels. Write the answer now.'}
        self.panels.append(panel)
        return {'ok': True, 'panels': len(self.panels), 'next': 'When every panel is as you want it, write the answer.'}

    def resolve(self, handle):
        """The handle the model means: exact, or the latest result whose query name the given text starts with."""
        if handle in self.results:
            return handle
        text = str(handle or '')
        matches = [h for h, r in self.results.items() if text and (text.startswith(r['name']) or r['name'].startswith(text))]
        return matches[-1] if matches else None

    def ask_user(self, question, choices):
        self.ask = {'question': str(question)[:200], 'choices': [str(c)[:80] for c in choices][:5]}
        return {'ok': True}

    def final_answer(self, headline, text, caveats=()):
        """The answer given through a tool call. The model is asked to write it as plain text; this is kept so that
        a model that calls the tool anyway is still understood."""
        self.caveats.update(c for c in caveats if c in prompt.CAVEATS)
        self.final = {'headline': clip(str(headline), 160), 'text': clip(str(text), TEXT_LIMIT)}
        return {'ok': True}


def clean_highlight(value):
    """What to ring and name on the map: a mode, or a list of ids. The model sometimes writes the list as text."""
    if isinstance(value, str) and value.strip().startswith('['):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = re.findall(r'[A-Za-z0-9]+', value)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int))][:25] or 'none'
    return value if value in ('leaders', 'main_centers') else 'none'


def valid_markers(value):
    return value in ('lymphoma_products', 'all_products', 'none') or (isinstance(value, list) and bool(value) and all(m in queries.BRANDS for m in value))


def clean_panel(kind, title, handle, options, result):
    """A panel whose options only name columns that exist; anything else falls back to the query's default."""
    panel = {'type': kind, 'title': str(title)[:90]}
    if kind == 'text':
        panel['options'] = {'body': str(options.get('body', ''))[:600]}
        return panel
    panel['handle'] = handle
    names = {c['name'] for c in result['columns']}
    default = result.get('default_panel') or {}
    out = {}
    x = options.get('x') if options.get('x') in names else default.get('x')
    y = [c for c in (options.get('y') or []) if c in names] or [c for c in default.get('y', []) if c in names]
    if x:
        out['x'] = x
    if y:
        out['y'] = y[:4]
    split = options.get('split_by') or default.get('split_by')
    if split in names:
        out['split_by'] = split
    brand = result.get('params', {}).get('brand')
    # A one-product time chart carries that product's approval dates; any other chart, what its query suggests.
    usual = [brand] if kind == 'line' and brand in queries.BRANDS else default.get('markers')
    markers = options.get('markers') if options is not default and valid_markers(options.get('markers')) else usual
    if valid_markers(markers):
        out['markers'] = markers
    if isinstance(options.get('top_n'), int):
        out['top_n'] = max(3, min(50, options['top_n']))
    if options.get('span') in ('full', 'half', 'third'):
        panel['span'] = options['span']
    panel['options'] = out
    return panel


def clip(text, limit):
    """Text cut at the last line or sentence end that fits, so nothing stops in the middle of a word."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind('\n'), cut.rfind('. ') + 1)
    if stop <= limit // 2:
        return cut.rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
    lines = cut[:stop].rstrip().split('\n')
    while len(lines) > 1 and lines[-1].rstrip().endswith(':'):  # a list heading with nothing left under it
        lines.pop()
    return '\n'.join(lines)


CAVEAT_LINE = re.compile(r'(?im)^[\s*_#-]*caveats?(?: codes?)?\s*:.*$|\bcaveats?(?: codes?)?\s*:\s*(?:[A-Z_]{4,}[\s,.;and]*)+')


def split_plain(content):
    """(headline and text, caveat codes named) from a reply the model wrote as plain text instead of calling final_answer."""
    codes = [code for code in prompt.CAVEATS if re.search(rf'\b{code}\b', content)]
    text = CAVEAT_LINE.sub('', content).replace('**', '').replace('__', '')
    for code in prompt.CAVEATS:
        text = re.sub(rf'\s*\(?\b{code}\b\)?', '', text)
    lines = [re.sub(r'^#+\s*', '', ' '.join(line.split())) for line in text.splitlines()]
    lines = [re.sub(r'^(headline|text|answer)\s*:\s*', '', line, flags=re.I) for line in lines]  # the tool's field names written as labels
    lines = [line for line in lines if line.strip(' ,.;:-')]
    if not lines:
        return {'headline': '', 'text': ''}, codes
    first, *rest = verify.SENTENCE.split(lines[0], maxsplit=1)  # the headline is the first sentence of the first line
    lines = rest + lines[1:]
    if len(first) > 158:  # too long for a headline: keep it as the first line of the text
        return {'headline': '', 'text': clip('\n'.join([first] + lines), TEXT_LIMIT)}, codes
    return {'headline': first, 'text': clip('\n'.join(lines), TEXT_LIMIT)}, codes


# A sentence is complete once the next one has begun, or the line has ended.
PUSH_NO_SKILL = ('You have not loaded a skill, so nothing is shown to the user. Call load_skill now, with find_entity for any name in the '
                 'question, then run its queries and answer after that. If the question is outside the database, load scope-and-help and '
                 'say so in one sentence.')
PUSH_NO_QUERY = ('You have not run a query, so nothing is shown and your answer has no figures behind it. If the database can answer this, call '
                 'run_query now with queries the skill lists, and answer after that. If the question asks for something the database does not '
                 'hold, say so in one sentence.')


def ask_from_candidates(text, candidates):
    """The question back to the user when a name matched several entries, with each entry as a choice."""
    choices = [f"{c['name']} ({c['place']})" if c.get('place') else str(c['name']) for c in candidates]
    return {'question': f'Several entries match "{text[:60]}". Which one do you mean?', 'choices': [c[:80] for c in choices][:5]}


COMPLETE = re.compile(r'(?<=[.!?])' + verify.NOT_AN_END + r'(?=\s+["(A-Z0-9])|\n')


def answer_so_far(content, results):
    """The headline and text the interface may show while the model is still writing: only sentences that are
    complete, and of those only the ones the number check accepts. None until a first sentence is complete."""
    cut = max((match.end() for match in COMPLETE.finditer(content)), default=0)
    if not cut:
        return None
    written, _ = split_plain(content[:cut])
    backing = results or [prompt.product_reference()]
    text, _ = verify.clean_text(written['text'], backing)
    headline = written['headline'] if not verify.unsupported(written['headline'], backing) else ''
    return {'headline': headline[:160], 'text': text[:TEXT_LIMIT]} if headline or text else None


def default_panels(turn):
    """One panel per query result, drawn the way the query itself suggests."""
    panels = []
    for handle, result in list(turn.results.items())[:6]:
        default = result.get('default_panel') or {'type': 'table'}
        panels.append(clean_panel(default.get('type', 'table'), result['title'], handle, default, result))
    return panels


def focus_of(turn):
    """(kind, ids, year of referral queries) of what the answer is about: what its queries were run for, or,
    before any has run, what was looked up."""
    found, year = [], None
    for result in turn.results.values():
        params = result['params']
        scope = params.get('scope')
        if scope in ('community', 'community_core'):
            found.append(('community', str(params['id'])))
        elif scope in ('hcp', 'center'):
            found.append((scope, str(params['id'])))
        for key, kind in (('community_id', 'community'), ('other_community_id', 'community'), ('ccn', 'center'), ('npi', 'hcp')):
            if params.get(key) not in (None, ''):
                found.append((kind, str(params[key])))
        if params.get('state'):
            found.append(('state', str(params['state']).upper()))
        if year is None and 'REFERRAL' in result['caveats'] and isinstance(params.get('year'), int):
            year = params['year']
    if not found:
        found = [('state' if e['kind'] == 'place' else e['kind'], str(e['id'])) for e in turn.entities
                 if e['kind'] in ('community', 'center', 'hcp', 'place')]
    if not found:
        return None, [], year
    kind = found[0][0]
    return kind, list(dict.fromkeys(i for k, i in found if k == kind))[:6], year  # two communities to compare: both


def default_map(turn):
    """The map the model gets without asking: the skill's usual links, on what the answer is about.

    Links follow the topic of the queries that ran (research dollars after a payment query from a profile skill,
    shared patients after a referral query, with that query's year), so the map and the panels show the same
    thing. A national answer shows the whole country. None when there is nothing to show.
    """
    if not turn.skill or turn.skill == 'scope-and-help':
        return None
    known = prompt.skills()
    edges = known[turn.skill]['meta'].get('default_edges', 'none')
    results = list(turn.results.values())
    # A profile skill that ran a topic query (payments, shared patients) draws that topic's links.
    topic = next((queries.home(r['name']) for r in results if r.get('name') in queries.ALSO.get(turn.skill, ())
                  and queries.home(r['name']) not in (None, turn.skill)), None)
    if topic in known:
        edges = known[topic]['meta'].get('default_edges', 'none')
    edges = edges if edges in EDGES else 'none'
    if edges in ('coauthor', 'none') and any('REFERRAL' in r['caveats'] for r in results):
        edges = 'referral'
    kind, ids, year = focus_of(turn)
    if not kind:
        return {'focus': {'kind': 'usa', 'ids': []}, 'show': 'all', 'edges': 'none', 'highlight': 'none'} if results else None
    spec = {'focus': {'kind': kind, 'ids': ids}, 'show': 'focus_and_linked' if kind in ('center', 'hcp') or edges == 'referral' else 'focus_only',
            'edges': edges, 'highlight': 'leaders' if kind == 'community' else 'main_centers' if kind == 'center' else ids if kind == 'hcp' else 'none'}
    if edges == 'referral':
        spec['year'] = year if year and 2017 <= year <= 2022 else 2022  # 2022 is the latest year held
    return spec


def assemble(turn):
    """The validated ViewSpec for the interface, with caveats and fallbacks applied."""
    if turn.ask:  # a question back to the user is the whole answer: no panels, no caveats, and the map stays put
        view = {'map': {'focus': {'kind': 'usa', 'ids': []}, 'show': 'all', 'edges': 'none', 'unchanged': True}, 'panels': [],
                'answer': {'headline': turn.ask['question'][:160], 'text': '', 'caveats': []}, 'ask': turn.ask}
        jsonschema.validate(view, json.loads((SCHEMAS / 'view_spec.schema.json').read_text(encoding='utf-8')))
        turn.trace.append({'step': 'assemble', 'dropped_sentences': []})
        return view
    results = list(turn.results.values())
    final = turn.final or {'headline': '', 'text': ''}
    backing = results or [prompt.product_reference()]  # with no query run, only the prompt's own product dates may be stated
    text, dropped = verify.clean_text(final['text'], backing)
    headline = final['headline'] if not verify.unsupported(final['headline'], backing) else ''
    panels = turn.panels or default_panels(turn)
    caveats = turn.caveats | (turn.skill_caveats if results else set())  # a skill's caveats apply once its data is shown
    if not text and not headline and panels:  # the model wrote nothing usable: say what is on the canvas
        text = 'The panels below show ' + '; '.join(p['title'] for p in panels[:4]) + '.'
    if not headline and panels:  # no usable headline from the model: the first panel's heading and who it is about
        about = (turn.results.get(panels[0].get('handle')) or {}).get('scope_label')
        headline = panels[0]['title'] + (f': {about}' if about else '')
    if not headline:
        headline = 'That is not in the database.'
    # With nothing to show from the database the map stays where it was, unless the model itself set it.
    view = {'map': turn.map or (default_map(turn) if results else None) or {'focus': {'kind': 'usa', 'ids': []}, 'show': 'all', 'edges': 'none', 'unchanged': True},
            'panels': panels[:6],
            'answer': {'headline': headline[:160], 'text': text[:TEXT_LIMIT], 'caveats': sorted(caveats)}}
    schema = json.loads((SCHEMAS / 'view_spec.schema.json').read_text(encoding='utf-8'))
    repaired = []
    for part, fallback in (('map', {'focus': {'kind': 'usa', 'ids': []}, 'show': 'all', 'edges': 'none', 'unchanged': True}), ('panels', default_panels(turn))):
        errors = [e for e in jsonschema.Draft202012Validator(schema).iter_errors(view) if list(e.absolute_path)[:1] == [part]]
        if errors:  # keep the answer: swap the part the model got wrong for the safe default
            view[part] = fallback
            repaired.append({'part': part, 'problem': errors[0].message[:200]})
    jsonschema.validate(view, schema)
    turn.trace.append({'step': 'assemble', 'dropped_sentences': dropped, **({'repaired': repaired} if repaired else {})})
    return view


STEP_LABELS = {'load_skill': 'Choosing how to answer', 'find_entity': 'Looking up {text}', 'search_cards': 'Searching for {query}',
               'run_query': 'Reading {names}', 'set_map': 'Setting the map', 'add_panel': 'Redrawing {handle}',
               'ask_user': 'Asking a question', 'final_answer': 'Writing the answer'}


def step_label(name, args):
    """What the interface shows while a tool runs, in plain words."""
    words = {k: str(v)[:60] for k, v in args.items()}
    if name == 'run_query':
        wanted = args.get('names') if args.get('names') is not None else args.get('name')
        wanted = [wanted] if isinstance(wanted, str) else [str(n) for n in (wanted or [])]
        words['names'] = ', '.join(n.replace('_', ' ') for n in wanted)[:90]
    if name == 'add_panel':
        words['handle'] = str(args.get('title') or args.get('handle') or 'a panel').replace('_', ' ')[:60]
    try:
        return STEP_LABELS.get(name, name).format(**words)
    except (KeyError, IndexError):
        return STEP_LABELS.get(name, name).split(' {')[0]


EXPORT = ('name', 'params', 'scope_label', 'title', 'columns', 'rows', 'row_count', 'source', 'caveats')


def export(result):
    """A query result as the interface receives it."""
    return {key: result[key] for key in EXPORT}


def run_call(turn, step, call, outputs, sent):
    """Run one tool call. Appends what the model reads next to `outputs` and yields what the interface shows:
    the step, then the map or the panels if the call changed them."""
    name, args = call['function']['name'], call['function'].get('arguments') or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    if name not in STEP_LABELS and name in queries.load_parts():  # a query called as if it were a tool: run it
        name, args = 'run_query', {'names': [name], 'params': args.get('params', args) if isinstance(args, dict) else {}}
    handler = getattr(turn, name, None) if name in STEP_LABELS else None
    before = json.dumps(turn.panels, sort_keys=True)
    try:
        output = handler(**args) if handler else {'error': f'There is no tool named {name}.'}
    except TypeError as error:
        output = {'error': f'Wrong arguments for {name}: {error}'}
    except Exception as error:  # a tool that fails gives the model something to act on; it never ends the turn
        output = {'error': f'{name} could not run: {error}'}
    failed = None
    if isinstance(output, dict):
        failed = output.get('error') or '; '.join(r['error'] for r in output.get('results', []) if 'error' in r) or None
    turn.trace.append({'step': step, 'tool': name, 'args': args, 'error': failed})
    outputs.append({'role': 'tool', 'tool_name': name, 'content': json.dumps(output, ensure_ascii=False, default=str)})
    yield {'type': 'step', 'label': step_label(name, args), 'tool': name}
    if name == 'set_map' and not failed:
        if turn.map != turn.early_map:  # the map is already there when the model asks for the skill's usual view
            yield {'type': 'map', 'map_data': mapdata.view(turn.con, turn.map)}
    elif name in ('load_skill', 'find_entity', 'run_query') and not turn.map and not turn.unsure:
        # Move the map as soon as it is known what the answer is about: first what was looked up, then what
        # the queries were run for. The model's own set_map, if it calls it, replaces this.
        early = default_map(turn)
        if early and early != turn.early_map and not mapdata.validate(turn.con, early):
            turn.early_map = early
            yield {'type': 'map', 'map_data': mapdata.view(turn.con, early), 'early': True}
    if json.dumps(turn.panels, sort_keys=True) != before:
        new = {h: export(r) for h, r in turn.results.items() if h not in sent}
        sent.update(new)
        yield {'type': 'panels', 'panels': [dict(p) for p in turn.panels], 'results': new}


def events(question, con=None, memory=None, model=MODEL):
    """Run one turn. Yields {'type': 'step'}, {'type': 'map'} and {'type': 'panels'} as tools run, then
    {'type': 'answer'} with the checked sentences so far as the model writes them, and finally
    {'type': 'final', 'view', 'results', 'trace'}, which is the whole checked answer and replaces anything drawn
    before it."""
    own = con is None
    con = con or connect(readonly=True)
    turn = Turn(con, memory)
    tools = tool_specs()
    context = ''
    if memory and memory.get('focus'):
        context = (f"\n\n(Before this question the user was looking at: {memory['focus']}. If the question names a person, a center, a "
                   'community or a place itself, it is about that one: look it up and ignore this note. Only if the question names none and says '
                   '"it", "its", "their" or "this" does it mean the one in this note: then use its kind and id as given here.)')
    messages = [{'role': 'system', 'content': prompt.system_prompt()}, {'role': 'user', 'content': question + context}]
    sent, unreadable = set(), 0
    try:
        for step in range(MAX_STEPS):
            outputs, reply = [], {}
            # After a reply that could not be read, the next one is written with more variety: at the usual low
            # setting the model repeats the same malformed call.
            retry = {'temperature': min(0.9, 0.1 + 0.3 * unreadable)} if unreadable else {}
            said, shown = '', None
            for kind, value in chat_events(messages, tools, model, **retry):
                if kind == 'reply':
                    reply = value
                elif kind == 'text':
                    # Text after the queries have run is the answer. Each sentence is passed on once it is
                    # complete and the number check has accepted it, so nothing unchecked is ever shown.
                    said += value
                    if turn.results and not outputs and not (turn.final or turn.ask):
                        if shown is None:
                            shown = {}
                            yield {'type': 'step', 'label': 'Writing the answer', 'tool': 'answer'}
                        now = answer_so_far(said, list(turn.results.values()))
                        if now and now != shown:
                            shown = now
                            yield {'type': 'answer', **now}
                elif not (turn.final or turn.ask):  # nothing runs after the answer or a question back to the user
                    yield from run_call(turn, step, value, outputs, sent)
            calls = (reply.get('tool_calls') or [])[:len(outputs)]
            if reply.get('stats'):
                turn.trace.append({'step': step, 'model_call': reply['stats']})
            if calls or not reply.get('server_error'):
                messages.append({'role': 'assistant', 'content': reply.get('content', ''), 'tool_calls': calls})
                messages += outputs
            if turn.final or turn.ask:
                break
            if reply.get('server_error'):
                unreadable += 1
                turn.trace.append({'step': step, 'server_error': reply['server_error']})
                if unreadable >= MAX_UNREADABLE:
                    if not turn.results:
                        turn.final = {'headline': 'The model could not write a readable reply this time.', 'text': 'Please ask again, or put the question another way.'}
                    break
                messages.append({'role': 'user', 'content': 'Your last reply could not be read. Write the tool calls again, each one complete and well formed.'})
                continue
            unreadable = 0
            if not calls:
                if not reply.get('content', '').strip():
                    messages.append({'role': 'user', 'content': 'Continue with a tool call, or write the answer.'})
                    continue
                if turn.unsure and not turn.results:
                    # The name matched several things and the model wrote about that instead of asking: ask for it.
                    turn.ask = ask_from_candidates(*turn.choices)
                    turn.trace.append({'step': step, 'asked_by_code': True})
                    break
                push = PUSH_NO_SKILL if not turn.skill else PUSH_NO_QUERY if turn.skill != 'scope-and-help' and not turn.results else None
                if push and not turn.pushed:
                    # Words alone show the user nothing, and an answer given before any query has run is often
                    # wrong. Say so once; a second reply in words is taken as a refusal and accepted.
                    turn.pushed = True
                    turn.trace.append({'step': step, 'pushed': 'load a skill' if not turn.skill else 'run a query'})
                    messages.append({'role': 'user', 'content': push})
                    continue
                turn.final, named = split_plain(reply['content'])
                turn.caveats.update(named)
                turn.trace.append({'step': step, 'plain_text_reply': True})
                break
        view = assemble(turn)
        focus = view['map'].get('focus', {})
        entity = next((e for e in turn.entities if str(e['id']) in focus.get('ids', [])), None)
        yield {'type': 'final', 'view': view, 'skill': turn.skill, 'trace': turn.trace,
               'results': {h: export(r) for h, r in turn.results.items()},
               'map_data': None if view['map'].get('unchanged') else mapdata.view(con, view['map']),
               'memory': {'focus': f"{entity['kind']} {entity['id']} ({entity['name']})" if entity else (memory or {}).get('focus')}}
    finally:
        if own:
            con.close()


def answer(question, con=None, memory=None, model=MODEL):
    """Run one turn to the end and return the final event."""
    final = None
    for event in events(question, con, memory, model):
        if event['type'] == 'final':
            final = event
    return final
