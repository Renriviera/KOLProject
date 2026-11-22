"""Keep the model's text honest: every number it writes must be traceable to a query result.

The model sees previews of query results and may count, add, subtract, divide, give a share or a percent
change. `supported` accepts a number when it equals a value in the results (in any common written form:
rounded, in thousands, millions or billions, or as a percentage of a 0-to-1 share), a count of result rows,
a year, or one of those simple calculations on two values of the same result. `clean_text` drops any sentence
with a number that is not supported.
"""
from __future__ import annotations

import re

NUMBER = re.compile(r'(?<![\w.])(\$?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|percent|thousand|million|billion|bn|[KkMmBb]\b)?')
SCALE = {'thousand': 1e3, 'k': 1e3, 'million': 1e6, 'm': 1e6, 'billion': 1e9, 'bn': 1e9, 'b': 1e9}
# A sentence ends at . ! or ? followed by a capital, a quote or a bracket. A full stop after a single capital
# letter is an initial ("H. Lee Moffitt", "Sattva S. Neelapu"), and one after Dr, St, Inc or vs an abbreviation.
NOT_AN_END = r'(?<!\b[A-Z]\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bInc\.)(?<!\bvs\.)'
SENTENCE = re.compile(r'(?<=[.!?])' + NOT_AN_END + r'\s+(?=[A-Z"(])')
# Counts that are part of the tool's own description and always safe to state.
REFERENCE_NUMBERS = {13, 17, 173, 500, 17510, 7, 11}  # 11: Medicare shows a count only from 11 up
SMALL = 10
PAIR_LIMIT = 60
DATE = re.compile(r'(\d{4})-(\d{2})(?:-(\d{2}))?')
# Names that contain digits and are not figures: the 340B drug pricing program and the Medicare hospital group for CAR-T stays.
NAMES_WITH_DIGITS = re.compile(r'\b340B\b|\bMS-DRG\s*0?18\b|\bDRG\s*0?18\b', re.I)


def numbers_in(text):
    """[(value, is_percent, tolerance, raw)] for every number written in the text.

    The tolerance is half a unit of the last written digit ("4.9 million" allows 4.85 to 4.95 million), or a
    full unit for scaled figures, which writers often truncate.
    """
    out = []
    text = NAMES_WITH_DIGITS.sub(' ', text)
    for match in NUMBER.finditer(text):
        _, whole, frac, unit = match.groups()
        unit = (unit or '').lower()
        scale = SCALE.get(unit, 1)
        decimals = len(frac) - 1 if frac else 0
        value = float(whole.replace(',', '') + (frac or '')) * scale
        tolerance = (1.0 if scale > 1 else 0.5) * scale * 10 ** (-decimals) + 1e-9
        out.append((value, unit in ('%', 'percent'), tolerance, match.group(0).strip()))
    return out


def number(cell):
    """A result cell as a float, or None when it is not a number."""
    if isinstance(cell, bool) or cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    text = str(cell).strip()
    return float(text) if len(text) < 12 and re.fullmatch(r'-?\d+(\.\d+)?', text) else None


def result_values(results):
    """Per result: every numeric cell, the cells by column, and the cells by row."""
    groups = []
    for result in results:
        cells, columns, rows = {float(result.get('row_count', 0))}, {}, []
        for row in result.get('rows', []):
            numeric = {name: number(cell) for name, cell in row.items()}
            numeric = {name: v for name, v in numeric.items() if v is not None}
            cells.update(numeric.values())
            for cell in row.values():  # a date backs its own year, month and day: "June 24, 2022" from 2022-06-24
                match = DATE.fullmatch(cell.strip()) if isinstance(cell, str) else None
                if match:
                    cells.update(float(part) for part in match.groups() if part)
            rows.append(list(numeric.values()))
            for name, v in numeric.items():
                columns.setdefault(name, []).append(v)
        groups.append({'cells': cells, 'columns': columns, 'rows': rows})
    return groups


def derived(group):
    """Values one simple calculation away: sum, difference, ratio or relative change of two values in the same
    column (and the column total), or the ratio of two values in the same row."""
    for values in group['columns'].values():
        sample = values[:PAIR_LIMIT]
        total = sum(values)
        yield total
        for a in sample:
            if total:
                yield a / total
            for b in sample:
                if a is b:
                    continue
                yield a + b
                yield a - b
                if b:
                    yield a / b
                    yield (a - b) / abs(b)
    for row in group['rows']:
        for a in row:
            for b in row:
                if b and a is not b:
                    yield a / b


def supported(value, percent, tolerance, groups):
    """Whether a written number is backed by the results."""
    plain = not percent and tolerance < 1
    if plain and (2009 <= value <= 2026 or value <= SMALL or value in REFERENCE_NUMBERS):
        return True
    near = lambda actual: abs(actual - value) <= tolerance
    for group in groups:
        if any(near(actual) or (abs(actual) <= 1 and near(actual * 100)) for actual in group['cells']):
            return True
        if any(near(c) or (percent and near(c * 100)) for c in derived(group)):
            return True
    return False


def unsupported(text, results):
    """The written numbers in `text` that no result backs."""
    groups = result_values(results)
    return [raw for value, percent, tolerance, raw in numbers_in(text) if not supported(value, percent, tolerance, groups)]


def clean_text(text, results):
    """(text without sentences carrying unsupported numbers, the sentences dropped)."""
    lines, dropped = [], []
    for line in text.strip().splitlines():  # line breaks are kept, so a list stays a list
        kept = []
        for sentence in SENTENCE.split(line.strip()):
            if sentence and unsupported(sentence, results):
                dropped.append(sentence)
            elif sentence:
                kept.append(sentence)
        if kept:
            lines.append(' '.join(kept))
    return '\n'.join(lines), dropped
