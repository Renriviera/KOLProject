"""340B registry position and 2018-2022 remedy amounts of the CAR-T centers, and FDA orphan designations.

`cart_center_340b_status` is rebuilt from three inputs: the covered-entity export of the federal 340B
registry dated 2026-06-23 (matched on Medicare provider number and parent 340B ID, never on the text of
the 340B ID), Addendum AAA of the CMS 340B remedy final rule, and a reviewed file of the manual findings
for the 34 centers with no participating parent record at their own CCN. `cart_product_orphan_designations`
is the hand-compiled FDA reference list with its placeholder quotes filled in.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal

from .base import (CENTERS, INTERIM, OUT, PROBES, RAW, ROOT, SNAPSHOT, column, dump, load, publish, read_csv, stage,
                   write_csv, write_module_metadata)

MODULE = 'status_340b'
TABLES = ['cart_center_340b_status', 'cart_product_orphan_designations']
STATUS_TABLE, ORPHAN_TABLE = TABLES
CENTER_SNAPSHOT = '2026-10-24'
EXPORT_DATE = '2026-06-23'
WORK = INTERIM / MODULE
COLLECTED = RAW.parent  # Data/raw/2026-11-19: pages fetched for this module sit beside the staged search inputs

# Search inputs, relative to the probes folder.
REGISTRY_ZIP = '340b/verify/mirror/CE_Daily_Report_20260623.json.zip'
MIRROR_CSV = '340b/verify/mirror/covered_entities_20260624.csv'
ADDENDUM_ZIP = '340b/cms_340b_remedy/addendum_aaa.zip'
ORPHAN_CSV = 'gap-340b-cart-closeout/cart_orphan_designation_fda_primary.csv'
FDA_LETTERS = 'gap-340b-cart-closeout/fda_letters'
SEARCH_TABLES = {'first_pass': '340b/cart_centers_x_opais_340b_status_20260624.csv',
                 'corrected': 'gap-340b-cart-closeout/cart_centers_340b_corrected_status.csv',
                 'remedy': '340b/cart_centers_x_cms_340b_remedy_addendum_aaa.csv'}
EVIDENCE_LISTINGS = ['gap-340b-cart-closeout/opais_20260623_address_keyword_matches_34_centers.csv',
                     '340b/verify/opais_official_20260623_cart_center_bmt_celltherapy_child_sites.csv']
# Inputs that never sat in the probes folder. They were frozen on 2026-11-19 from a temporary working folder
# (the two search scripts, the saved CMS page, the text extracted from the FDA files) or written for this load
# (the reviewed manual determinations), so they are read from the frozen folder by name.
FROZEN_ONLY = {'match_opais': '340b_scripts/match_opais.py', 'match_remedy': '340b_scripts/match_remedy.py',
               'pps_exempt_page': 'cms_pps_exempt_cancer_hospitals/pch.html',
               'manual': '340b_manual/center_determinations.csv',
               'manual_maker': '340b_manual/make_center_determinations.py'}
FDA_TEXT = 'fda_letters_text'
FDA_PAGES = COLLECTED / 'fda'
REPORTS_PAGE = COLLECTED / 'hrsa_opais' / 'reports_page.html'

HOSPITAL_TYPES = ('DSH', 'PED', 'RRC', 'CAN', 'SCH', 'CAH')
CLASS_OF_TYPE = {'DSH': 'A', 'PED': 'A', 'CAN': 'B', 'RRC': 'B', 'SCH': 'B', 'CAH': 'B', '': 'C'}
ROUTES = ('direct', 'sibling', 'pending_direct', 'outside')
STATUSES = ('active', 'inactive', 'not_registered')
AS_OF_RULES = ('reported', 'export_date', 'after_scheduled_changes', 'own_ccn_only')
YEAR_COLUMNS = ['remedy_2018_usd', 'remedy_2019_usd', 'remedy_2020_usd', 'remedy_2021_usd']
# Amount cells of the Addendum AAA payment sheet in file order (columns 3 to 10).
SHEET_CELLS = YEAR_COLUMNS + ['remedy_2022_net_usd', 'remedy_total_2018_2022_usd', 'remedy_2022_withheld_usd',
                              'remedy_2022_already_paid_usd']
SHEET_HEADER = ['Provider Number', 'Provider Name', '2018 340B Drugs Impact', '2019 340B Drugs Impact',
                '2020 340B Drugs Impact', '2021 340B Drugs Impact', '2022 340B Drugs Impact',
                'Total 340B Drugs Impact', 'CY 2022 (January 1 to September 27) 340B Drugs Payment Withheld',
                'CY 2022 (January 1 to December 31) 340B Remedy Payment Already Paid*']
REMEDY_COLUMNS = YEAR_COLUMNS + ['remedy_2022_withheld_usd', 'remedy_2022_net_usd', 'remedy_total_2018_2022_usd']
# Cells covered by each fingerprint that `check` compares; the status table is hashed in file order.
FINGERPRINT_COLUMNS = {
    'registry': ['ccn', 'registry_status_at_ccn', 'route_340b', 'entity_type_340b', 'class_340b', 'evidence_340b_id',
                 'evidence_ccn', 'evidence_start_date', 'sibling_site_names_cell_therapy', 'scheduled_registry_change',
                 'registry_entity_types_ever', 'n_registry_records_active', 'n_registry_records_total',
                 'pps_exempt_cancer_hospital'],
    'notes': ['ccn', 'status_note'],
    'remedy': ['ccn', 'in_cms_340b_remedy_file'] + REMEDY_COLUMNS,
}
STREET = re.compile(r'\b\d{1,5}\s+(?:[NSEW]\.?\s+)?(?:\w+\.?\s+){1,3}(?:st|street|rd|road|ave|avenue|dr|drive|ln|lane|'
                    r'blvd|boulevard|way|pike|hwy|highway|pkwy|parkway)\b', re.I)
CONTACT = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+|\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b')
# Wider net for addresses with no street word: a bare house number before a word, a state and ZIP code, a unit.
# Dates, CCNs and 340B IDs in the notes do not match because their digits touch a hyphen, a letter or a sixth digit;
# a bare year is let through.
HOUSE_NUMBER = re.compile(r'(?<![\w/-])(?!(?:19|20)\d{2}\b)\d{1,5}\s+[A-Za-z]|\b[A-Z]{2}\s+\d{5}\b|#\s*\d|\b(?:suite|ste|bldg|floor)\b', re.I)
PLACEHOLDER = 'same sentence'
DATE_FORMS = {'day': r'\d{4}-\d{2}-\d{2}', 'month': r'\d{4}-\d{2}', 'not_stated': ''}
# Hand corrections to the compiled FDA list, each read in the saved FDA document it cites.
ORPHAN_CORRECTIONS = {
    ('Tecartus', 'Mantle cell lymphoma'): {
        'designation_date': '2016-04-28',
        'evidence_quote': 'TECARTUS received orphan drug designation (ODD) on 28 April 2016 for the treatment of '
                          'mantle cell lymphoma'},
    ('Breyanzi', 'CLL/SLL (2024-03-21); MCL (2024-05-30); 2025-12-04 supplement'): {
        'designated_indication': 'CLL/SLL (2024-03-21); MCL (2024-05-30); marginal zone lymphoma (2025-12-04)'},
    ('Yescarta', 'Follicular lymphoma'): {
        'evidence_url': 'https://www.fda.gov/media/108788/download | https://www.fda.gov/media/148510/download'},
}

# Totals the 2026-11-19 search reported and its review confirmed; `check` recomputes each from the tracked tables.
EXPECTED = {
    'centers': 173,
    'registry_status': {'active': 139, 'inactive': 5, 'not_registered': 29},
    'route': {'direct': 139, 'sibling': 7, 'pending_direct': 2, 'outside': 25},
    'entity_type': {'DSH': 114, 'PED': 22, 'RRC': 9, 'CAN': 3, '': 25},
    'classes': {'reported': {'A': 136, 'B': 12, 'C': 25}, 'export_date': {'A': 135, 'B': 11, 'C': 27},
                'after_scheduled_changes': {'A': 134, 'B': 13, 'C': 26}, 'own_ccn_only': {'A': 128, 'B': 11, 'C': 34}},
    'drg018': {'reported': {'A': 1086, 'B': 141, 'C': 133}, 'export_date': {'A': 1086, 'B': 141, 'C': 133},
               'after_scheduled_changes': {'A': 1042, 'B': 141, 'C': 177},
               'own_ccn_only': {'A': 1049, 'B': 141, 'C': 170}},
    'scheduled_changes': ['170122', '310001', '410004', '520037'],
    'sibling_named': {'1': 4, '0': 3},
    'records_active': 11746, 'records_total': 16034,
    'pps_exempt': 10, 'pps_exempt_by_class': {'B': 3, 'C': 7},
    'remedy_listed': 172, 'remedy_positive': 121, 'remedy_positive_with_discharges': 41,
    'remedy_by_class': {'A': ('3600363684.62', 108), 'B': ('84326352.64', 8), 'C': ('5832366.03', 5)},
    'remedy_sums': {'remedy_2018_usd': '770471146.88', 'remedy_2019_usd': '905679023.76',
                    'remedy_2020_usd': '947135755.51', 'remedy_2021_usd': '1017270166.31',
                    'remedy_2022_withheld_usd': '762616451.99', 'remedy_2022_net_usd': '49966310.82',
                    'remedy_total_2018_2022_usd': '3690522403.29'},
    'orphan_rows': 16,
    'orphan_by_product': {'Yescarta': 4, 'Breyanzi': 4, 'Kymriah': 3, 'Tecartus': 2, 'Abecma': 1, 'Carvykti': 1,
                          'Aucatzyl': 1},
    'orphan_precision': {'day': 7, 'month': 3, 'not_stated': 6},
    'orphan_date_range': ['2014-01-31', '2019-02-01'],
    # Named rows behind the figures most often quoted, so a moved value is reported by name.
    'types_ever': {'DSH': 102, '': 29, 'PED': 22, 'DSH|RRC': 9, 'RRC': 6, 'CAN': 4, 'DSH|SCH': 1},
    'pps_exempt_ccns': ['050146', '050660', '100079', '100271', '220162', '330154', '330354', '360242', '450076',
                        '500138'],
    'non_direct_evidence': {'030087': 'DSH030014AG', '110010': 'DSH110078AE', '140010': 'DSH140114N',
                            '360006': 'DSH360017AR', '360016': 'DSH360236P', '390050': 'DSH390090C',
                            '490101': 'DSH490063BA', '170122': 'RRC170122-00', '410004': 'DSH410004'},
    'remedy_not_listed': ['443302'],
    'remedy_largest': [('330214', '122254829.14'), ('170040', '99680151.91'), ('110161', '97206355.04'),
                       ('050441', '90515932.27'), ('070022', '90465805.43'), ('390111', '85689413.90')],
    # Fingerprints of the published cells (see `fingerprint`). Validation has no raw files, so only these notice two
    # centers' values being swapped or two edits that cancel in every total. After a rebuild that changes a table
    # on purpose, copy the new values from `fingerprints` in the module metadata.
    'fingerprints': {'registry': '551aacbcdf16e343', 'notes': '4c52e10f9a8d4e50', 'remedy': 'c375efa0c22287ed',
                     'orphan': '5bc2959354dd4d94'},
}


# ---- registry ----

def slim(record):
    """The fields of one registry record this module uses; contact persons and street addresses stay behind."""
    return {'id': record['id340B'], 'type': record['entityType'], 'ccn': record.get('medicareProviderNumber') or '',
            'parent_id': record.get('parentId340B') or '', 'participating': record['participating'] == 'TRUE',
            'start': (record.get('participatingStartDate') or '')[:10],
            'end': (record.get('terminationDate') or '')[:10], 'end_reason': record.get('terminationReason') or '',
            'name': record.get('name') or '', 'site_name': record.get('subName') or ''}


def read_registry(path):
    """Hospital records of the export in slim form, an index of every record, and facts about the file."""
    with zipfile.ZipFile(path) as archive:
        member, = archive.infolist()
        text = archive.read(member).decode('utf-8-sig')
    assert text.startswith('{"coveredEntities":['), 'Unexpected registry export layout'
    decoder, at = json.JSONDecoder(), text.index('[') + 1
    hospital, index, latest = [], {}, ''
    while True:
        while text[at] in ' ,\r\n\t':
            at += 1
        if text[at] == ']':
            break
        record, at = decoder.raw_decode(text, at)
        assert record['id340B'] not in index, 'Duplicate 340B ID in the export'
        index[record['id340B']] = (record['entityType'], record['participating'],
                                   (record.get('participatingStartDate') or '')[:10])
        latest = max(latest, record.get('editDate') or '')
        if record['entityType'] in HOSPITAL_TYPES:
            hospital.append(slim(record))
    facts = {'member': member.filename, 'member_bytes': member.file_size, 'member_crc32': f'{member.CRC:08x}',
             'member_timestamp': '%04d-%02d-%02dT%02d:%02d:%02d' % member.date_time, 'records': len(index),
             'hospital_records': len(hospital), 'hospital_provider_numbers': len({r['ccn'] for r in hospital}),
             'latest_edit': latest}
    return hospital, index, facts


def parents(records):
    return [r for r in records if not r['parent_id']]


def registry_status(records):
    if any(r['participating'] for r in parents(records)):
        return 'active'
    return 'inactive' if records else 'not_registered'


def pending_parents(records, export_date=EXPORT_DATE):
    """Parent records loaded in the export that start after it; a record ending no later than it starts is void."""
    return [r for r in parents(records) if not r['participating'] and r['start'] > export_date
            and not (r['end'] and r['end'] <= r['start'])]


def scheduled_change(evidence, records, export_date=EXPORT_DATE):
    """Net change to the center's own parent records that the export schedules for a later date, in words."""
    if evidence is None:
        return ''
    starts = [r for r in pending_parents(records, export_date) if r is not evidence]
    if not evidence['participating']:
        return f"{evidence['start']}: {evidence['type']} record starts"
    if evidence['end'] > export_date:
        tail = '; '.join(f"{r['type']} record starts" + ('' if r['start'] == evidence['end'] else f" {r['start']}")
                         for r in starts) or 'no successor record'
        return f"{evidence['end']}: {evidence['type']} record ends; {tail}"
    return '; '.join(f"{r['start']}: {r['type']} record starts beside the continuing {evidence['type']} record"
                     for r in starts)


def class_340b(entity_type):
    return CLASS_OF_TYPE[entity_type]


def position(ccn, records, manual, by_id, export_date=EXPORT_DATE):
    """Registry columns of one center from the hospital records at its own CCN and its manual determination."""
    status = registry_status(records)
    if status == 'active':
        assert manual is None, f'{ccn}: manual determination on a center with a participating parent record'
        evidence, = [r for r in parents(records) if r['participating']]
        route, named, note = 'direct', '', ''
    else:
        assert manual is not None, f'{ccn}: no participating parent record and no manual determination'
        route, named, note = manual['determination'], manual['sibling_site_names_cell_therapy'], manual['status_note']
        pending = pending_parents(records, export_date)
        assert route in ROUTES[1:] and note.strip(), f'{ccn}: incomplete manual determination'
        assert named in (('0', '1') if route == 'sibling' else ('',)), f'{ccn}: sibling flag does not fit the route'
        if route == 'pending_direct':
            evidence, = pending
        else:
            assert not pending, f'{ccn}: a future-dated parent record exists'
            evidence = by_id[manual['evidence_340b_id']] if route == 'sibling' else None
        if route == 'sibling':
            parent = by_id[evidence['parent_id']]
            assert evidence['ccn'] != ccn and evidence['participating'] and parent['participating'], ccn
            assert not evidence['end'] and not parent['end'], f'{ccn}: sibling site or its parent is ending'
        assert manual['evidence_340b_id'] == (evidence['id'] if evidence else ''), f'{ccn}: evidence record differs'
    entity_type = evidence['type'] if evidence else ''
    return {
        'registry_status_at_ccn': status, 'route_340b': route, 'entity_type_340b': entity_type,
        'class_340b': class_340b(entity_type), 'evidence_340b_id': evidence['id'] if evidence else '',
        'evidence_ccn': evidence['ccn'] if evidence else '', 'evidence_start_date': evidence['start'] if evidence else '',
        'sibling_site_names_cell_therapy': named,
        'scheduled_registry_change': scheduled_change(evidence, records, export_date),
        'registry_entity_types_ever': '|'.join(sorted({r['type'] for r in records})),
        'n_registry_records_active': sum(r['participating'] for r in records), 'n_registry_records_total': len(records),
        'status_note': note,
    }


def class_under(row, rule):
    """Class of a published row under another as-of rule; `reported` is the published class_340b."""
    change = row['scheduled_registry_change']
    if rule == 'own_ccn_only':
        return row['class_340b'] if row['route_340b'] == 'direct' else 'C'
    if rule == 'export_date':
        return 'C' if row['route_340b'] == 'pending_direct' else row['class_340b']
    if rule == 'after_scheduled_changes' and 'record ends' in change:
        successor = re.search(r'; ([A-Z]{3}) record starts', change)
        return class_340b(successor.group(1)) if successor else 'C'
    assert rule in AS_OF_RULES, rule
    return row['class_340b']


# ---- CMS remedy file and cancer hospital list ----

def dollars(cell):
    """Decimal amount of a cell such as "$1,234.50"; None when the cell holds no number."""
    cell = cell.replace('$', '').replace(',', '').replace('"', '').strip()
    return Decimal(cell) if re.fullmatch(r'-?\d+(\.\d+)?', cell) else None


def cents(amount):
    """Dollar text with two decimals; a missing amount stays blank and a zero stays 0.00."""
    return '' if amount is None else f'{amount:.2f}'


def remedy_sheet(lines):
    """Provider number to the eight amounts of the Addendum AAA payment sheet, plus the file's Total row."""
    providers, total, header_seen = {}, None, False
    for cells in csv.reader(lines, delimiter='\t'):
        cells = [c.strip() for c in cells]
        if cells[:len(SHEET_HEADER)] == SHEET_HEADER:
            header_seen = True
        elif len(cells) >= 10 and re.fullmatch(r'[0-9A-Z]{6}', cells[0]):
            assert header_seen and cells[0] not in providers, f'Unexpected provider row {cells[0]}'
            providers[cells[0]] = dict(zip(SHEET_CELLS, map(dollars, cells[2:10])))
        elif len(cells) >= 10 and cells[1] == 'Total:':
            total = dict(zip(SHEET_CELLS, map(dollars, cells[2:10])))
    return providers, total


def read_addendum(path):
    with zipfile.ZipFile(path) as archive:
        member, = [m for m in archive.infolist() if 'Remedy Payment FR' in m.filename and m.filename.endswith('.txt')]
        with archive.open(member) as binary:
            providers, total = remedy_sheet(io.TextIOWrapper(binary, encoding='latin-1', newline=''))
    return providers, total, member.filename


def remedy_columns(entry):
    """Published remedy cells of one center; `entry` is None when the CCN is not a provider row of the file."""
    out = {'in_cms_340b_remedy_file': int(entry is not None)}
    out.update({name: cents(entry[name]) if entry else '' for name in REMEDY_COLUMNS})
    return out


def pps_exempt_ccns(page):
    """CCNs in the table of the CMS page listing the cancer hospitals exempt from inpatient prospective payment."""
    return sorted({a + b for a, b in re.findall(r'<t[hd][^>]*>\s*(\d{2})-(\d{4})\s*</t[hd]>', html.unescape(page))})


# ---- FDA orphan designations ----

def date_precision(value):
    value = '' if value == 'not stated' else value
    found = [name for name, form in DATE_FORMS.items() if re.fullmatch(form, value)]
    assert found, f'Unexpected designation date: {value}'
    return found[0]


def orphan_rows(compiled, corrections=ORPHAN_CORRECTIONS):
    """Published rows from the compiled list: placeholders filled, hand corrections applied, dates typed.

    A quote written as 'same sentence' points at the first quoted row of the same product; the row receives that
    quote and, when it does not already cite it, the address of the document the quote comes from.
    """
    quoted, out = {}, []
    for source in compiled:
        row = dict(source)
        if row['evidence_quote'].startswith(PLACEHOLDER):
            origin = quoted[row['product']]
            row['evidence_quote'] = origin['evidence_quote']
            cited = row['evidence_url'].split(' | ')
            row['evidence_url'] = ' | '.join([u for u in origin['evidence_url'].split(' | ') if u not in cited] + cited)
        else:
            quoted.setdefault(row['product'], dict(row))
        row.update(corrections.get((source['product'], source['designated_indication']), {}))
        precision = date_precision(row['designation_date'])
        out.append({'product': row['product'], 'generic_name': row['generic'],
                    'designated_indication': row['designated_indication'],
                    'designation_date': '' if precision == 'not_stated' else row['designation_date'],
                    'designation_date_precision': precision, 'evidence_document': row['evidence_document'],
                    'evidence_url': row['evidence_url'], 'evidence_quote': row['evidence_quote'],
                    'collection_snapshot_date': SNAPSHOT})
    return out


def plain(text):
    """Lower-case text with markup removed and white space collapsed, for finding a quote in a saved document."""
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    return re.sub(r'\s+', ' ', text.replace('’', "'")).strip().lower()


# ---- dictionaries ----

def status_columns():
    registry = 'the Health Resources and Services Administration (HRSA) 340B registry export of 2026-06-23'
    amount = ('CMS estimate, in US dollars, of the payment the hospital did not receive (Medicare payment plus the '
              'beneficiary copayment share) for separately payable outpatient drugs bought under 340B on calendar-year '
              '{year} claims, because they were paid at average sales price (ASP) minus 22.5% instead of ASP plus 6%; '
              'all drugs, no CAR-T breakout.')
    not_listed = 'The CCN is not a provider row of Addendum AAA (in_cms_340b_remedy_file = 0); '
    return [
        column('ccn', 'Six-character Centers for Medicare & Medicaid Services (CMS) Certification Number (CCN) of the '
               'hospital that is a chimeric antigen receptor T-cell (CAR-T) therapy center, zero-padded text. Key of '
               'the table and of cart_centers.csv in the 2026-10-24 snapshot, which holds the hospital name, state and '
               'Medicare discharge count.', 'Never blank.', 'cms'),
        column('registry_status_at_ccn', f"Result of matching the center's own CCN to hospital records in {registry}: "
               'active = a parent record at this CCN is participating (a parent record is the registration of the '
               'hospital itself; its off-site clinics are registered as child sites under it); inactive = records '
               'exist at this CCN but no parent record is participating on that date, whether terminated or not yet '
               'started; not_registered = no hospital record carries this CCN.', 'Never blank.', 'hrsa'),
        column('route_340b', 'How the center reaches the 340B drug discount program in the 2026-06-23 export: direct = '
               'participating parent record at its own CCN; sibling = no participating record at its own CCN, but a '
               "participating child site of another hospital in the same health system sits at or beside the center's "
               'address or names its transplant or cell-therapy clinic (found by manual search); pending_direct = a '
               'parent record at its own CCN that starts on 2026-07-01 and is not yet participating; outside = none of '
               'these.', 'Never blank.', 'project'),
        column('entity_type_340b', 'HRSA hospital entity type of the evidence record: DSH = disproportionate share '
               "hospital, PED = children's hospital, RRC = rural referral center, CAN = free-standing cancer hospital, "
               'SCH = sole community hospital, CAH = critical access hospital. Only DSH, PED, RRC and CAN occur.',
               'route_340b is outside: there is no evidence record.', 'hrsa'),
        column('class_340b', 'What the entity type allows in law for an outpatient CAR-T dose: A = DSH or PED, a 340B '
               'price is legally possible; B = CAN, RRC, SCH or CAH, types for which drugs with an orphan designation '
               '(every CAR-T product) are excluded from 340B pricing unless the manufacturer offers it voluntarily; C = '
               'outside 340B. It does not show that any CAR-T dose was bought at a 340B price, and it counts the two '
               'records that start on 2026-07-01 but not the two that end on that date (see scheduled_registry_change).',
               'Never blank.', 'project'),
        column('evidence_340b_id', "HRSA 340B ID of the registry record that supports route_340b: the center's own "
               "parent record for direct and pending_direct rows, the other hospital's child-site record for sibling "
               'rows.', 'route_340b is outside.', 'hrsa'),
        column('evidence_ccn', 'Medicare provider number on the evidence record. It equals ccn for direct and '
               "pending_direct rows; for sibling rows it is another hospital's CCN, which is not one of the 173 "
               'centers.', 'route_340b is outside.', 'hrsa'),
        column('evidence_start_date', 'Participating start date (YYYY-MM-DD) of the evidence record: start of the '
               'current registration under the current entity type for direct rows, start of the child site for '
               "sibling rows, and the future start for pending_direct rows. It is not the hospital's first 340B date, "
               'because a new registration resets it.', 'route_340b is outside.', 'hrsa'),
        column('sibling_site_names_cell_therapy', "For sibling rows only: 1 when the name of the other hospital's "
               'child site names a bone marrow transplant or cell-therapy clinic, 0 when it is a general oncology, '
               "infusion or outpatient site at or next door to the center's address, which is weaker evidence.",
               'route_340b is not sibling.', 'project'),
        column('scheduled_registry_change', "Change to the center's own parent records that the 2026-06-23 export "
               'already holds for a later date, written as the date (YYYY-MM-DD), a colon and what starts or ends, for '
               'example "2026-07-01: DSH record ends; SCH record starts". Four centers have one, all dated 2026-07-01. '
               'class_340b applies the starts but not the endings.', 'No such change. A future-dated record that ends '
               'on the day it starts is void and is not counted.', 'hrsa'),
        column('registry_entity_types_ever', "Every HRSA hospital entity type found on any record at the center's own "
               'CCN in the 2026-06-23 export, whether participating, terminated or future-dated, sorted and separated '
               'by |.', 'No hospital record carries this CCN.', 'hrsa'),
        column('n_registry_records_active', "Number of registry records at the center's own CCN (parent and child "
               'sites) that are participating on 2026-06-23.', 'Never blank; 0 means none.', 'hrsa'),
        column('n_registry_records_total', "Number of registry records at the center's own CCN (parent and child "
               'sites) in the 2026-06-23 export, whether participating, terminated or future-dated.',
               'Never blank; 0 means none.', 'hrsa'),
        column('pps_exempt_cancer_hospital', '1 when the CCN is one of the 11 cancer hospitals that CMS exempts from '
               'the Medicare inpatient prospective payment system (PPS), from the CMS list last modified 2026-03-17, '
               'else 0. Ten of the 11 are centers. They have no 2024 discharge count for Medicare Severity Diagnosis '
               'Related Group (MS-DRG) 018, the inpatient payment group for CAR-T, in cart_centers.csv, and they were '
               'exempt from the 2018 to 2022 cut in Medicare payment for 340B drugs.', 'Never blank.', 'cms'),
        column('status_note', 'Evidence in words, written by hand for this project: for sibling and pending_direct '
               'rows the other hospital or record, the child-site name as the registry gives it and its start date; '
               'for outside rows why no route was found. BMT = bone marrow transplant; entity type codes as in '
               'entity_type_340b.', 'route_340b is direct: the evidence record needs no note.', 'project'),
        column('in_cms_340b_remedy_file', '1 when the CCN is a provider row in Addendum AAA of the CMS final rule on '
               'the remedy for 340B-acquired drug payment (CMS-1793-F, files dated 2023-10-27), else 0. Being listed '
               'does not mean a non-zero amount.', 'Never blank.', 'cms'),
        *[column(name, amount.format(year=name[7:11]), not_listed + '0.00 means listed with no affected payment '
                 'that year.', 'cms') for name in YEAR_COLUMNS],
        column('remedy_2022_withheld_usd', 'Payment withheld on 340B-acquired outpatient drug claims from 2022-01-01 '
               'to 2022-09-27, in US dollars, before subtracting the remedy already paid: the part-year continuation '
               'of the 2018 to 2021 columns.', not_listed + '0.00 means listed with nothing withheld.', 'cms'),
        column('remedy_2022_net_usd', 'Amount still owed for claims from 2022-01-01 to 2022-09-27, in US dollars: '
               'remedy_2022_withheld_usd minus the remedy CMS had already paid by reprocessing 2022 claims. This net '
               'figure, not the withheld amount, enters the total, and it is not comparable with the 2018 to 2021 '
               'columns.', not_listed + '0.00 means nothing still owed.', 'cms'),
        column('remedy_total_2018_2022_usd', 'Lump-sum remedy payment CMS scheduled for the hospital, in US dollars, '
               'for 340B-acquired outpatient drug claims from 2018-01-01 to 2022-09-27: the four year columns plus '
               'remedy_2022_net_usd as CMS publishes the total, which can differ from that sum by up to $0.02.',
               not_listed + '0.00 means listed with no remedy: expected for hospital types exempt from the payment '
               'cut and for hospitals not in 340B in those years, so it does not by itself show which of the two.',
               'cms'),
        column('registry_export_date', 'Date of the HRSA 340B registry export that every registry column describes.',
               'Never blank.', 'hrsa'),
        column('collection_snapshot_date', 'Date of the collection run that assembled this table.', 'Never blank.',
               'project'),
    ]


def orphan_columns():
    return [
        column('product', 'Brand name of the chimeric antigen receptor T-cell (CAR-T) product: Yescarta, Kymriah, '
               'Tecartus, Breyanzi, Abecma, Carvykti or Aucatzyl.', 'Never blank.', 'fda'),
        column('generic_name', 'Nonproprietary name of the product.', 'Never blank.', 'fda'),
        column('designated_indication', 'Indication for which the cited Food and Drug Administration (FDA) document '
               'states an orphan-drug designation, in short form: DLBCL = diffuse large B-cell lymphoma, PMBCL = '
               'primary mediastinal large B-cell lymphoma, ALL = acute lymphoblastic leukemia, CLL/SLL = chronic '
               'lymphocytic leukemia or small lymphocytic lymphoma, MCL = mantle cell lymphoma, 2L = second line, '
               'r/r = relapsed or refractory. A '
               'date in brackets is the approval date of a supplemental biologics license application (sBLA) whose '
               'letter states that the product has an orphan designation for that indication; one Breyanzi row '
               'groups three such supplements.', 'Never blank.', 'fda'),
        column('designation_date', 'Date the orphan designation was granted as the FDA document states it: YYYY-MM-DD, '
               'or YYYY-MM when the document gives only the month.', 'The document confirms the designation without a '
               'date (designation_date_precision = not_stated).', 'fda'),
        column('designation_date_precision', 'How precisely the document dates the designation: day, month, or '
               'not_stated.', 'Never blank.', 'project'),
        column('evidence_document', 'Short label of the FDA document or documents relied on, separated by "; ", with '
               'the date or year of the document where the compiled list recorded it. SBRA = summary basis for '
               'regulatory action; FL = follicular lymphoma.', 'Never blank.', 'fda'),
        column('evidence_url', 'Address on fda.gov of each document named in evidence_document, in the same order, '
               'separated by " | ". Two exceptions: the Carvykti row names a second document (the 2024 approval '
               'letter) whose address is not listed, and the Breyanzi row of three supplements lists its three '
               'letters.', 'Never blank.', 'fda'),
        column('evidence_quote', 'Short extract copied word for word from one of the FDA documents at evidence_url '
               'that states the designation. On the Yescarta follicular lymphoma row the quote is from the approval '
               'letter and the date from the summary document.', 'Never blank.', 'fda'),
        column('collection_snapshot_date', 'Date of the collection run that assembled this table.', 'Never blank.',
               'project'),
    ]


# ---- summaries and checks ----

def fingerprint(rows, names):
    """First 16 hexadecimal digits of the SHA-256 of the named cells, row by row in the order given."""
    text = '\n'.join('\x1f'.join(str(row.get(name, '')) for name in names) for row in rows)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def fingerprints(status, orphan):
    """Fingerprint of each column group of the status table in file order, and of the orphan table sorted by key."""
    out = {group: fingerprint(status, names) for group, names in FINGERPRINT_COLUMNS.items()}
    names = [c['column'] for c in orphan_columns()]
    out['orphan'] = fingerprint(sorted(orphan, key=lambda r: (r.get('product', ''), r.get('designated_indication', ''))), names)
    return out


def money_sum(rows, name):
    return sum((Decimal(r[name]) for r in rows if str(r[name]).strip()), Decimal(0))


def by_class(rows, discharges, rule):
    """Centers, centers with a discharge count, and 2024 MS-DRG 018 discharges per class under an as-of rule."""
    out = {k: {'centers': 0, 'centers_with_discharges': 0, 'drg018_discharges_2024': 0} for k in 'ABC'}
    for row in rows:
        cell, count = out[class_under(row, rule)], str(discharges.get(row['ccn'], '')).strip()
        cell['centers'] += 1
        cell['centers_with_discharges'] += bool(count)
        cell['drg018_discharges_2024'] += int(count or 0)
    return out


def headline(status_rows, center_rows, orphan):
    """Numbers a reader can quote, each computed from the published rows and cart_centers.csv."""
    rows = [{k: str(v) for k, v in r.items()} for r in status_rows]
    discharges = {r['ccn']: r['drg018_discharges_2024'] for r in center_rows}
    total_discharges = sum(int(v) for v in discharges.values() if v.strip())
    classes = {rule: by_class(rows, discharges, rule) for rule in AS_OF_RULES}
    for cells in classes.values():
        for cell in cells.values():
            cell['drg018_share_pct'] = (round(100 * cell['drg018_discharges_2024'] / total_discharges, 1)
                                        if total_discharges else None)
    positive = [r for r in rows if r['remedy_total_2018_2022_usd'] and Decimal(r['remedy_total_2018_2022_usd']) > 0]
    direct = [r for r in rows if r['route_340b'] == 'direct']
    dated = sorted((r['designation_date'], r['product'], r['designated_indication']) for r in orphan if r['designation_date'])
    return {
        'centers': len(rows), 'registry_export_date': EXPORT_DATE,
        'registry_status_at_ccn': dict(Counter(r['registry_status_at_ccn'] for r in rows)),
        'route_340b': dict(Counter(r['route_340b'] for r in rows)),
        'entity_type_of_direct_rows': dict(Counter(r['entity_type_340b'] for r in direct)),
        'entity_type_340b': dict(Counter(r['entity_type_340b'] or 'blank' for r in rows)),
        'class_340b_reported': classes['reported'], 'class_340b_strictly_on_export_date': classes['export_date'],
        'class_340b_after_all_scheduled_changes': classes['after_scheduled_changes'],
        'class_340b_own_ccn_records_only': classes['own_ccn_only'],
        'drg018_discharges_2024_total': total_discharges,
        'centers_with_drg018_discharges_2024': sum(bool(v.strip()) for v in discharges.values()),
        'scheduled_registry_changes': {r['ccn']: r['scheduled_registry_change'] for r in rows if r['scheduled_registry_change']},
        'sibling_site_names_cell_therapy': dict(Counter(r['sibling_site_names_cell_therapy'] for r in rows if r['route_340b'] == 'sibling')),
        'sibling_evidence_ccns': sorted(r['evidence_ccn'] for r in rows if r['route_340b'] == 'sibling'),
        'registry_records_active': sum(int(r['n_registry_records_active']) for r in rows),
        'registry_records_total': sum(int(r['n_registry_records_total']) for r in rows),
        'registry_records_active_max': max((int(r['n_registry_records_active']) for r in rows), default=0),
        'pps_exempt_cancer_hospitals': sum(r['pps_exempt_cancer_hospital'] == '1' for r in rows),
        'pps_exempt_cancer_hospitals_by_class': dict(Counter(r['class_340b'] for r in rows if r['pps_exempt_cancer_hospital'] == '1')),
        'remedy_centers_listed': sum(r['in_cms_340b_remedy_file'] == '1' for r in rows),
        'remedy_centers_not_listed': [r['ccn'] for r in rows if r['in_cms_340b_remedy_file'] != '1'],
        'remedy_centers_above_zero': len(positive),
        'remedy_centers_listed_at_zero': sum(r['remedy_total_2018_2022_usd'] != ''
                                             and Decimal(r['remedy_total_2018_2022_usd']) == 0 for r in rows),
        'remedy_usd': {name: f'{money_sum(rows, name):.2f}' for name in REMEDY_COLUMNS},
        'remedy_total_usd_by_class': {
            k: f"{money_sum([r for r in rows if r['class_340b'] == k], 'remedy_total_2018_2022_usd'):.2f}" for k in 'ABC'},
        'remedy_centers_above_zero_by_class': {k: sum(r['class_340b'] == k for r in positive) for k in 'ABC'},
        'remedy_centers_with_discharges_above_zero': sum(bool(discharges.get(r['ccn'], '').strip()) for r in positive),
        'remedy_largest': [{'ccn': r['ccn'], 'remedy_total_2018_2022_usd': r['remedy_total_2018_2022_usd']} for r in
                           sorted(positive, key=lambda r: -Decimal(r['remedy_total_2018_2022_usd']))[:6]],
        'orphan_rows': len(orphan), 'orphan_products': len({r['product'] for r in orphan}),
        'orphan_rows_by_product': dict(Counter(r['product'] for r in orphan)),
        'orphan_date_precision': dict(Counter(r['designation_date_precision'] for r in orphan)),
        'orphan_earliest_dated': dated[0] if dated else None, 'orphan_latest_dated': dated[-1] if dated else None,
    }


def _table(path):
    return read_csv(path) if path.is_file() else None


def check(processed):
    """Error counts per invariant, read from tracked tables only; zero means the invariant holds."""
    status = _table(processed / SNAPSHOT / f'{STATUS_TABLE}.csv')
    orphan = _table(processed / SNAPSHOT / f'{ORPHAN_TABLE}.csv')
    centers = _table(processed / CENTER_SNAPSHOT / 'cart_centers.csv')
    out = {f'{MODULE}_tables_present': sum(t is None for t in (status, orphan, centers))}
    if out[f'{MODULE}_tables_present']:
        return out

    def note(name, errors):
        out[f'{MODULE}_{name}'] = int(errors)

    def differs(counter, expected):
        return sum(counter.get(k, 0) != expected.get(k, 0) for k in set(counter) | set(expected))

    names = [c['column'] for c in status_columns()]
    note('status_columns', list(status[0].keys()) != names if status else 1)
    note('status_rows', len(status) != EXPECTED['centers'])
    note('status_ccn_equals_cart_centers', [r['ccn'] for r in status] != [r['ccn'] for r in centers])
    note('status_ccn_format', sum(not re.fullmatch(r'[0-9A-Z]{6}', r['ccn']) for r in status)
         + len(status) - len({r['ccn'] for r in status}))
    if out[f'{MODULE}_status_columns']:
        return out
    center_set = {r['ccn'] for r in centers}
    discharges = {r['ccn']: r['drg018_discharges_2024'] for r in centers}
    note('status_values', sum(r['registry_status_at_ccn'] not in STATUSES or r['route_340b'] not in ROUTES
                              or r['entity_type_340b'] not in CLASS_OF_TYPE for r in status))
    note('registry_status_counts', differs(Counter(r['registry_status_at_ccn'] for r in status), EXPECTED['registry_status']))
    note('route_counts', differs(Counter(r['route_340b'] for r in status), EXPECTED['route']))
    note('entity_type_counts', differs(Counter(r['entity_type_340b'] for r in status), EXPECTED['entity_type']))
    note('class_follows_entity_type', sum(CLASS_OF_TYPE.get(r['entity_type_340b']) != r['class_340b'] for r in status))
    note('entity_type_blank_iff_outside', sum((r['entity_type_340b'] == '') != (r['route_340b'] == 'outside') for r in status))
    note('status_matches_route', sum((r['registry_status_at_ccn'] == 'active') != (r['route_340b'] == 'direct')
                                     or (r['route_340b'] == 'pending_direct' and r['registry_status_at_ccn'] != 'inactive')
                                     for r in status))
    note('evidence_ccn_matches_route', sum(
        (r['evidence_ccn'] == r['ccn']) != (r['route_340b'] in ('direct', 'pending_direct'))
        or (r['route_340b'] == 'sibling' and (not r['evidence_ccn'] or r['evidence_ccn'] in center_set))
        or (r['route_340b'] == 'outside' and bool(r['evidence_ccn'] or r['evidence_340b_id'] or r['evidence_start_date']))
        for r in status))
    note('evidence_id_carries_type_and_ccn', sum(
        r['route_340b'] != 'outside' and not r['evidence_340b_id'].upper().startswith(r['entity_type_340b'] + r['evidence_ccn'])
        for r in status))
    note('evidence_start_date', sum(
        r['route_340b'] != 'outside' and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', r['evidence_start_date'])
                                          or (r['evidence_start_date'] > r['registry_export_date']) != (r['route_340b'] == 'pending_direct'))
        for r in status))
    note('children_hospitals_are_ped', sum((r['ccn'][2:4] == '33') != (r['entity_type_340b'] == 'PED') for r in status))
    note('sibling_flag_iff_sibling', sum((r['sibling_site_names_cell_therapy'] in ('0', '1')) != (r['route_340b'] == 'sibling')
                                         or r['sibling_site_names_cell_therapy'] not in ('', '0', '1') for r in status))
    note('sibling_flag_counts', differs(Counter(r['sibling_site_names_cell_therapy'] for r in status if r['route_340b'] == 'sibling'),
                                        EXPECTED['sibling_named']))
    changed = [r for r in status if r['scheduled_registry_change']]
    note('scheduled_change_rows', sorted(r['ccn'] for r in changed) != EXPECTED['scheduled_changes'])
    note('scheduled_change_format', sum(
        not re.fullmatch(r'\d{4}-\d{2}-\d{2}: [A-Z]{3} record (starts|ends; (no successor record|[A-Z]{3} record starts.*))',
                         r['scheduled_registry_change'])
        or r['scheduled_registry_change'][:10] <= r['registry_export_date'] for r in changed)
         + sum(r['route_340b'] == 'pending_direct' and not r['scheduled_registry_change'] for r in status))
    note('types_ever', sum((r['registry_entity_types_ever'] == '') != (r['registry_status_at_ccn'] == 'not_registered')
                           or (r['route_340b'] in ('direct', 'pending_direct')
                               and r['entity_type_340b'] not in r['registry_entity_types_ever'].split('|'))
                           or not set(filter(None, r['registry_entity_types_ever'].split('|'))) <= set(HOSPITAL_TYPES)
                           for r in status))
    note('types_ever_counts', differs(Counter(r['registry_entity_types_ever'] for r in status), EXPECTED['types_ever']))
    counts_ok = all(r['n_registry_records_active'].isdigit() and r['n_registry_records_total'].isdigit() for r in status)
    note('record_counts', not counts_ok or sum(
        int(r['n_registry_records_active']) > int(r['n_registry_records_total'])
        or (int(r['n_registry_records_total']) == 0) != (r['registry_status_at_ccn'] == 'not_registered')
        or (r['registry_status_at_ccn'] == 'active' and int(r['n_registry_records_active']) < 1) for r in status))
    note('record_count_totals', not counts_ok
         or sum(int(r['n_registry_records_active']) for r in status) != EXPECTED['records_active']
         or sum(int(r['n_registry_records_total']) for r in status) != EXPECTED['records_total'])
    note('pps_exempt_flag', sum(r['pps_exempt_cancer_hospital'] not in ('0', '1') for r in status)
         + (sum(r['pps_exempt_cancer_hospital'] == '1' for r in status) != EXPECTED['pps_exempt'])
         + differs(Counter(r['class_340b'] for r in status if r['pps_exempt_cancer_hospital'] == '1'),
                   EXPECTED['pps_exempt_by_class'])
         + sum(r['pps_exempt_cancer_hospital'] == '1' and discharges.get(r['ccn'], '').strip() != '' for r in status))
    note('anchor_pps_exempt_ccns', sorted(r['ccn'] for r in status if r['pps_exempt_cancer_hospital'] == '1') != EXPECTED['pps_exempt_ccns'])
    note('anchor_non_direct_evidence', {r['ccn']: r['evidence_340b_id'] for r in status
                                        if r['route_340b'] in ('sibling', 'pending_direct')} != EXPECTED['non_direct_evidence'])
    note('status_note_presence', sum((r['status_note'].strip() == '') != (r['route_340b'] == 'direct') for r in status))
    note('status_note_no_address_or_contact', sum(bool(STREET.search(r['status_note']) or CONTACT.search(r['status_note'])
                                                       or HOUSE_NUMBER.search(r['status_note'])) for r in status))
    note('sibling_note_states_start_date', sum(r['route_340b'] in ('sibling', 'pending_direct')
                                               and f"start {r['evidence_start_date']}" not in r['status_note'] for r in status))
    for rule in AS_OF_RULES:
        try:
            cells = by_class(status, discharges, rule)
        except (KeyError, ValueError):
            cells = {}
        note(f'class_counts_{rule}', differs({k: v['centers'] for k, v in cells.items()}, EXPECTED['classes'][rule]))
        note(f'drg018_by_class_{rule}', differs({k: v['drg018_discharges_2024'] for k, v in cells.items()}, EXPECTED['drg018'][rule]))
    amount = re.compile(r'\d+\.\d{2}')
    listed = [r for r in status if r['in_cms_340b_remedy_file'] == '1']
    note('remedy_flag', sum(r['in_cms_340b_remedy_file'] not in ('0', '1') for r in status)
         + (len(listed) != EXPECTED['remedy_listed']))
    note('remedy_blank_iff_not_listed', sum(any((r[name] == '') != (r['in_cms_340b_remedy_file'] != '1') for name in REMEDY_COLUMNS)
                                            for r in status))
    remedy_ok = all(amount.fullmatch(r[name]) for r in listed for name in REMEDY_COLUMNS)
    note('remedy_amount_format', not remedy_ok)
    if remedy_ok:
        note('remedy_year_sum_equals_total', sum(
            abs(sum(Decimal(r[name]) for name in YEAR_COLUMNS + ['remedy_2022_net_usd'])
                - Decimal(r['remedy_total_2018_2022_usd'])) > Decimal('0.02') for r in listed))
        note('remedy_net_not_above_withheld', sum(Decimal(r['remedy_2022_net_usd']) > Decimal(r['remedy_2022_withheld_usd']) for r in listed))
        note('remedy_centers_above_zero', sum(Decimal(r['remedy_total_2018_2022_usd']) > 0 for r in listed) != EXPECTED['remedy_positive'])
        note('remedy_sums', sum(f'{money_sum(listed, name):.2f}' != EXPECTED['remedy_sums'][name] for name in REMEDY_COLUMNS))
        positive = [r for r in listed if Decimal(r['remedy_total_2018_2022_usd']) > 0]
        note('remedy_centers_with_discharges_above_zero',
             sum(discharges.get(r['ccn'], '').strip() != '' for r in positive) != EXPECTED['remedy_positive_with_discharges'])
        note('remedy_by_class', sum(
            (f"{money_sum([r for r in listed if r['class_340b'] == k], 'remedy_total_2018_2022_usd'):.2f}",
             sum(r['class_340b'] == k for r in positive)) != EXPECTED['remedy_by_class'][k] for k in 'ABC'))
        largest = sorted(positive, key=lambda r: -Decimal(r['remedy_total_2018_2022_usd']))[:len(EXPECTED['remedy_largest'])]
        note('anchor_remedy_largest_and_not_listed',
             ([(r['ccn'], r['remedy_total_2018_2022_usd']) for r in largest] != EXPECTED['remedy_largest'])
             + ([r['ccn'] for r in status if r['in_cms_340b_remedy_file'] != '1'] != EXPECTED['remedy_not_listed']))
    note('constant_dates', sum(r['registry_export_date'] != EXPORT_DATE or r['collection_snapshot_date'] != SNAPSHOT for r in status))

    orphan_names = [c['column'] for c in orphan_columns()]
    note('orphan_columns', list(orphan[0].keys()) != orphan_names if orphan else 1)
    note('orphan_rows', len(orphan) != EXPECTED['orphan_rows'])
    if out[f'{MODULE}_orphan_columns']:
        return out
    note('orphan_key_unique', len(orphan) - len({(r['product'], r['designated_indication']) for r in orphan}))
    note('orphan_rows_by_product', differs(Counter(r['product'] for r in orphan), EXPECTED['orphan_by_product']))
    note('orphan_no_blank_or_placeholder', sum(
        any(not r[name].strip() for name in orphan_names if name != 'designation_date')
        or PLACEHOLDER in r['evidence_quote'].lower() or 'not stated' in r['designation_date'] for r in orphan))
    note('orphan_date_matches_precision', sum(
        r['designation_date_precision'] not in DATE_FORMS
        or not re.fullmatch(DATE_FORMS[r['designation_date_precision']], r['designation_date']) for r in orphan))
    note('orphan_date_precision_counts', differs(Counter(r['designation_date_precision'] for r in orphan), EXPECTED['orphan_precision']))
    dated = sorted(r['designation_date'] for r in orphan if r['designation_date'])
    note('orphan_date_range', [dated[0], dated[-1]] != EXPECTED['orphan_date_range'] if dated else 1)
    note('orphan_urls_are_fda', sum(not all(re.fullmatch(r'https://www\.fda\.gov/\S+', u) for u in r['evidence_url'].split(' | '))
                                    for r in orphan))
    note('orphan_snapshot_date', sum(r['collection_snapshot_date'] != SNAPSHOT for r in orphan))
    for group, value in fingerprints(status, orphan).items():
        note(f'fingerprint_{group}', value != EXPECTED['fingerprints'][group])
    return out


# ---- build ----

def frozen(name):
    """Path of an input that exists only in the frozen folder."""
    path = RAW / name
    assert path.is_file(), f'Frozen input missing and not recoverable from the notes folder: {path}'
    return path


def manual_determinations(path):
    rows = read_csv(path)
    assert len(rows) == len({r['ccn'] for r in rows}), 'Duplicate CCN in the manual determinations'
    return {r['ccn']: r for r in rows}


def reconcile(rows, search, index, mirror):
    """Differences between this rebuild and the tables the search wrote, and between the mirror's two files."""
    first = {r['ccn']: r for r in search['first_pass']}
    corrected = {r['ccn']: r for r in search['corrected']}
    remedy = {r['ccn']: r for r in search['remedy']}
    status_words = {'active': 'active', 'terminated/inactive': 'inactive', 'not registered': 'not_registered'}
    pairs = [('remedy_2018_usd', 'remedy_2018'), ('remedy_2019_usd', 'remedy_2019'), ('remedy_2020_usd', 'remedy_2020'),
             ('remedy_2021_usd', 'remedy_2021'), ('remedy_2022_net_usd', 'remedy_2022'),
             ('remedy_total_2018_2022_usd', 'remedy_total_2018_2022')]

    def differing(test):
        return sorted(r['ccn'] for r in rows if test(r))

    def amounts_differ(row):
        theirs = remedy[row['ccn']]
        return any((row[ours] == '') != (theirs[old] == '') or (row[ours] != '' and Decimal(row[ours]) != Decimal(theirs[old]))
                   for ours, old in pairs)
    mirror_ids = {r['id340B']: (r['entity_type'], r['participating'], r['participating_start_date'][:10]) for r in mirror}
    return {
        'registry_status_differs': differing(lambda r: status_words[first[r['ccn']]['opais_status']] != r['registry_status_at_ccn']),
        'route_differs': differing(lambda r: corrected[r['ccn']]['corrected_route'].replace('-', '_') != r['route_340b']),
        'class_differs': differing(lambda r: corrected[r['ccn']]['class_340b'][0] != r['class_340b']),
        'entity_type_differs': differing(lambda r: corrected[r['ccn']]['entity_type'] != r['entity_type_340b']),
        'evidence_id_differs': differing(lambda r: corrected[r['ccn']]['id340B_evidence'] != r['evidence_340b_id']),
        'types_ever_differs': differing(lambda r: first[r['ccn']]['opais_entity_types_ever'] != r['registry_entity_types_ever']),
        'record_counts_differ': differing(lambda r: (first[r['ccn']]['n_active_sites'], first[r['ccn']]['n_total_records'])
                                          != (str(r['n_registry_records_active']), str(r['n_registry_records_total']))),
        'remedy_amounts_differ': differing(amounts_differ),
        'mirror_csv_records': len(mirror_ids), 'mirror_json_records': len(index),
        'mirror_ids_only_in_one_file': len(set(mirror_ids) ^ set(index)),
        'mirror_field_differences': sum(mirror_ids[k] != index[k] for k in set(mirror_ids) & set(index)),
    }


def verify_quotes(orphan, texts):
    """Rows whose quote is not found word for word in a saved document of the same product."""
    return [f"{r['product']} / {r['designated_indication']}" for r in orphan
            if not any(plain(r['evidence_quote']) in text for name, text in texts.items()
                       if name.startswith(r['product'].lower()))]


def build():
    inputs = {key: stage(path) for key, path in [('registry', REGISTRY_ZIP), ('mirror_csv', MIRROR_CSV),
                                                  ('addendum', ADDENDUM_ZIP), ('orphan', ORPHAN_CSV),
                                                  ('corrected_script', 'gap-340b-cart-closeout/corrected_340b_status.py')]}
    inputs.update({key: stage(path) for key, path in SEARCH_TABLES.items()})
    inputs.update({key: frozen(name) for key, name in FROZEN_ONLY.items()})
    evidence = [stage(path) for path in EVIDENCE_LISTINGS]
    letter_folder = PROBES / FDA_LETTERS if (PROBES / FDA_LETTERS).is_dir() else RAW / FDA_LETTERS
    letters = [stage(f'{FDA_LETTERS}/{p.name}') for p in sorted(letter_folder.glob('*.pdf'))]
    letter_texts = [frozen(f'{FDA_TEXT}/{p.stem}.txt') for p in letters]
    pages = sorted(FDA_PAGES.glob('*.html'))
    collected = [p for page in pages + [REPORTS_PAGE] for p in (page, page.with_suffix('.meta.json')) if p.is_file()]

    center_rows = read_csv(CENTERS)
    manual = manual_determinations(inputs['manual'])
    hospital, index, facts = read_registry(inputs['registry'])
    assert facts['member_timestamp'][:10] == EXPORT_DATE and facts['latest_edit'][:10] <= EXPORT_DATE, facts
    by_id, by_ccn = {r['id']: r for r in hospital}, defaultdict(list)
    for record in hospital:
        by_ccn[record['ccn']].append(record)
    providers, total, sheet_name = read_addendum(inputs['addendum'])
    assert sum((p['remedy_total_2018_2022_usd'] for p in providers.values()), Decimal(0)) == total['remedy_total_2018_2022_usd']
    assert all(abs(p['remedy_2022_withheld_usd'] - p['remedy_2022_already_paid_usd'] - p['remedy_2022_net_usd']) <= Decimal('0.01')
               for p in providers.values()), 'Net 2022 amount is not withheld minus already paid'
    assert all(v is not None and v >= 0 for p in providers.values() for v in p.values()), 'Missing or negative remedy amount'
    exempt = pps_exempt_ccns(inputs['pps_exempt_page'].read_text(encoding='utf-8', errors='replace'))
    assert len(exempt) == 11, f'Expected the 11 exempt cancer hospitals, found {len(exempt)}'
    non_active = {r['ccn'] for r in center_rows if registry_status(by_ccn.get(r['ccn'], [])) != 'active'}
    assert non_active == set(manual), f'Manual determinations and non-active centers differ: {sorted(non_active ^ set(manual))}'
    for ccn, entry in manual.items():  # a termination date quoted in a note must be on a parent record of that CCN
        quoted = set(re.findall(r'terminated (\d{4}-\d{2}-\d{2})', entry['status_note']))
        assert quoted <= {r['end'] for r in parents(by_ccn.get(ccn, []))}, f'{ccn}: termination date not in the registry'

    rows = []
    for center in center_rows:
        ccn = center['ccn']
        rows.append({'ccn': ccn, **position(ccn, by_ccn.get(ccn, []), manual.get(ccn), by_id),
                     'pps_exempt_cancer_hospital': int(ccn in exempt), **remedy_columns(providers.get(ccn)),
                     'registry_export_date': EXPORT_DATE, 'collection_snapshot_date': SNAPSHOT})
    orphan = orphan_rows(read_csv(inputs['orphan']))
    texts = {p.name: plain(p.read_text(encoding='utf-8', errors='replace')) for p in letter_texts + pages}
    unverified = verify_quotes(orphan, texts)
    assert 'marginal zone lymphoma' in texts.get('breyanzi_2025-12-04.txt', ''), 'Breyanzi 2025-12-04 letter does not name marginal zone lymphoma'

    summaries = [publish(STATUS_TABLE, rows, status_columns(), ['ccn']),
                 publish(ORPHAN_TABLE, orphan, orphan_columns(), ['product', 'designated_indication'])]
    published = read_csv(OUT / f'{STATUS_TABLE}.csv'), read_csv(OUT / f'{ORPHAN_TABLE}.csv')
    numbers = headline(published[0], center_rows, published[1])
    checks = check(OUT.parent)
    failing = sorted(name for name, errors in checks.items() if errors)
    national_total = total['remedy_total_2018_2022_usd']
    numbers['remedy_share_of_national_total_pct'] = round(100 * float(Decimal(numbers['remedy_usd']['remedy_total_2018_2022_usd']) / national_total), 1)
    centers_used = {r['ccn'] for r in center_rows} | {r['evidence_ccn'] for r in rows if r['evidence_ccn']}
    write_csv(WORK / 'registry_records_at_centers.csv', [r for r in hospital if r['ccn'] in centers_used])
    search = {key: read_csv(inputs[key]) for key in SEARCH_TABLES}
    reconciliation = reconcile(rows, search, index, read_csv(inputs['mirror_csv']))
    dump(WORK / 'reconciliation.json', reconciliation)
    reports_meta = load(REPORTS_PAGE.with_suffix('.meta.json')) if REPORTS_PAGE.with_suffix('.meta.json').is_file() else {}

    write_module_metadata(MODULE, {
        'tables': summaries,
        'sources': [
            {'name': 'HRSA 340B Office of Pharmacy Affairs Information System (OPAIS) covered-entity daily export, JSON',
             'publisher': 'Health Resources and Services Administration, Office of Pharmacy Affairs',
             'url': 'https://340bopais.hrsa.gov/Reports',
             'release_or_version': f"export of {EXPORT_DATE} (file time {facts['member_timestamp']}, latest record edit {facts['latest_edit'][:19]})",
             'retrieved': '2026-11-19',
             'how_obtained': 'Copy of the daily export of 2026-06-23 taken from a third-party GitHub mirror '
                             'whose repository states no licence. The official Reports page answered HTTP 200 to plain curl at '
                             f"{reports_meta.get('retrieved_at_utc', 'an unrecorded time')}, but it hands out the export through an "
                             'interactive button with no file address, so the official file was not fetched and the mirror copy is '
                             'not confirmed against it. The mirror files stay local; only per-center values are published.',
             'terms_class': 'a: US federal government data, public with attribution (17 U.S.C. 105); mirror copy not redistributed'},
            {'name': 'Addendum AAA, Final Rule Lump Sum Remedy Payments for 340B-Acquired Drugs to 340B Providers (CMS-1793-F)',
             'publisher': 'Centers for Medicare & Medicaid Services',
             'url': 'https://www.cms.gov/files/zip/nfrm-opps-remedy-340b-acquired-drug-payment-addendum-aaa.zip',
             'release_or_version': f'files dated 2023-10-27; payment sheet "{sheet_name}"', 'retrieved': '2026-11-19',
             'how_obtained': 'Zip saved by the search; the tab-delimited payment sheet is read from inside the zip.',
             'terms_class': 'a: US federal government work, public with attribution'},
            {'name': 'PPS-Exempt Cancer Hospitals (PCHs) page', 'publisher': 'Centers for Medicare & Medicaid Services',
             'url': 'https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/pps-exempt-cancer-hospitals-pchs',
             'release_or_version': 'page last modified 2026-03-17', 'retrieved': '2026-11-19',
             'how_obtained': 'Page saved during the review of the search; the 11 CCNs are read from its table.',
             'terms_class': 'a: US federal government work, public with attribution'},
            {'name': 'FDA approval letters, summary basis for regulatory action documents and two FDA announcements',
             'publisher': 'US Food and Drug Administration', 'url': 'https://www.fda.gov (addresses in evidence_url)',
             'release_or_version': 'documents dated 2017-08-30 to 2025-12-04', 'retrieved': '2026-11-19',
             'how_obtained': f'{len(letters)} PDF files saved by the search and their text extracted with Ghostscript during '
                             f'the review; the {len(pages)} announcement pages fetched once each with curl on 2026-11-19. The list '
                             'was compiled by hand and is not an extract of the FDA orphan-designation database.',
             'terms_class': 'a: US federal government work, public with attribution'},
            {'name': 'cart_centers.csv', 'publisher': 'this project', 'url': 'Data/processed/2026-10-24/cart_centers.csv',
             'release_or_version': '2026-10-24 snapshot', 'retrieved': '2026-10-24',
             'how_obtained': 'Tracked table; gives the 173 CCNs, their order and the 2024 MS-DRG 018 discharge counts.',
             'terms_class': 'project table built from CMS and FACT sources'},
            {'name': 'Reviewed manual determinations for the 34 centers without a participating parent record',
             'publisher': 'this project', 'url': str(inputs['manual'].relative_to(ROOT)), 'release_or_version': 'reviewed 2026-11-19',
             'retrieved': '2026-11-19',
             'how_obtained': 'Hand-entered SIB and OUT_REASON dictionaries of corrected_340b_status.py moved into a file, one '
                             'row per center, with a public note rewritten without street addresses. The build checks every '
                             'row against the registry: the evidence record must exist and participate, pending records must '
                             'be found by rule, and outside centers must have no future-dated parent record.',
             'terms_class': 'project judgement on federal registry data'},
        ],
        'headline': numbers,
        'source_file_facts': {
            'registry_export': facts,
            'remedy_file': {'provider_rows': len(providers), 'providers_above_zero': sum(p['remedy_total_2018_2022_usd'] > 0 for p in providers.values()),
                            'national_total_usd': f'{national_total:.2f}',
                            'providers_where_years_do_not_sum_to_total_within_1_cent': sum(
                                abs(sum(p[n] for n in YEAR_COLUMNS + ['remedy_2022_net_usd']) - p['remedy_total_2018_2022_usd']) > Decimal('0.01')
                                for p in providers.values())},
            'pps_exempt_cancer_hospital_ccns': exempt,
            'pps_exempt_not_in_center_set': sorted(set(exempt) - {r['ccn'] for r in center_rows}),
        },
        'rows_removed': {STATUS_TABLE: 0, ORPHAN_TABLE: 0},
        'reconciliation_with_search_tables': reconciliation,
        'orphan_quotes': {'rows': len(orphan), 'found_word_for_word_in_saved_documents': len(orphan) - len(unverified),
                          'not_found': unverified},
        'fingerprints': fingerprints(*published),
        'checks': {'run': len(checks), 'failing': failing},
        'as_of_rule': 'class_340b keeps the counts the 2026-11-19 search reported (A 136, B 12, C 25): it applies the two '
                      'records that start on 2026-07-01 and not the two that end on that date. scheduled_registry_change '
                      'names all four so a reader can apply either consistent rule.',
        'limitations': [
            'Mixed as-of date, kept by decision. class_340b counts 136 A, 12 B and 25 C centers as the search reported them. '
            'Strictly on the export date of 2026-06-23 the counts are 135, 11 and 27 (the two pending centers are outside); '
            'with all four changes scheduled for 2026-07-01 they are 134, 13 and 26, and the 2024 Medicare discharges move '
            'from 1,086, 141 and 133 to 1,042, 141 and 177. Counting only records at the center\'s own CCN gives 128, 11 and 34.',
            'Class A means that an outpatient 340B price is legally possible for the hospital type. No source shows that any '
            'CAR-T dose was bought at a 340B price at any center.',
            '340B covers outpatient drugs only, and most CAR-T infusions are inpatient stays, so the discharge shares by class '
            'describe where Medicare inpatient volume sits, not volume eligible for 340B.',
            'Class B does not mean no discount: a manufacturer may offer 340B pricing on an orphan drug voluntarily, and '
            'whether any CAR-T manufacturer does is unknown.',
            'The registry copy is five months older than this snapshot and came through a third-party mirror. Changes HRSA '
            'applied on 2026-07-01 and 2026-10-01 are not reflected, and the copy was not confirmed against the official file.',
            'The sibling and outside determinations were made by hand. Evidence was saved for 20 of the 34 centers without '
            'a participating parent record; for the other 14 nothing matched. Three of the seven sibling rows rest on a '
            'general site at the center address (sibling_site_names_cell_therapy = 0).',
            'In every sibling row the 340B registration belongs to another hospital that is not among the 173 centers; '
            'an outpatient CAR-T dose bought through it would be billed under that hospital\'s CCN.',
            'Registry columns describe one day. evidence_start_date is the start of the current registration, not the first '
            'date the hospital joined 340B, and the status is not valid for earlier years.',
            'Remedy amounts are CMS estimates for all 340B-acquired outpatient drugs in 2018-2022, including the beneficiary '
            'copayment share; they are not purchases or savings and have no CAR-T breakout. Zero is expected for hospital '
            'types exempt from the payment cut and for hospitals not in 340B in those years, so a zero does not by itself '
            'mean the hospital was outside 340B.',
            'The registry half describes 2026 and the remedy half describes 2018-2022 claims; they are different periods.',
            'Medicare discharge shares use the 49 centers with a published 2024 count. The ten exempt cancer hospitals and '
            'all 22 children\'s hospitals have no count, so the class B and C shares of true volume are understated.',
            'The orphan table shows that every product carries an orphan designation for at least one labelled indication. '
            'It is not a complete list of designations or approved indications, and rows for supplemental approvals are not '
            'separate designation events.',
        ],
        'defects_fixed': [
            'Matching uses the Medicare provider number and parent 340B ID of the registry JSON instead of the text of the '
            '340B ID; CCN 450054 gains one record that the pattern missed (ID with a lower-case letter).',
            'The first-pass label terminated/inactive, wrong for CCN 410004 whose only record has never been terminated, is '
            'recoded to inactive; values are active, inactive and not_registered.',
            'Route, class and booleans recoded (pending_direct, A/B/C, 1/0); hospital name, state and discharge count dropped '
            'because cart_centers.csv holds them.',
            'New columns derived from the registry: evidence_ccn, evidence_start_date for sibling and pending records, and '
            'scheduled_registry_change, which ignores the void record of CCN 030122 (start and end on the same day).',
            'The bracketed tag of the hand-written note became sibling_site_names_cell_therapy; street addresses were removed '
            'from the notes.',
            'The hand-entered dictionaries of the search script became a reviewed input file, and the build checks each row '
            'against the registry.',
            'Four notes in the reviewed file were corrected against the sources: CCN 240010 now says that Mayo has no '
            'hospital record in Rochester (Mayo Clinic Health System hospitals elsewhere in Minnesota are registered), and '
            'CCNs 030138, 110233 and 140100 no longer assert a current for-profit status that no saved source supports.',
            'remedy_2022_withheld_usd added from the payment sheet so 2022 has a figure comparable with 2018-2021; the net '
            'column is named as net. Bed count, teaching category and the patient percentage of the characteristics sheet '
            'are not carried.',
            'The scripts match_opais.py and match_remedy.py, which existed only in a temporary folder, are frozen with the '
            'other inputs.',
            'Orphan table: three placeholder quotes replaced by the quoted sentence, with the address of the document that '
            'holds it; marginal zone lymphoma named on the Breyanzi supplement of 2025-12-04; the Tecartus mantle cell '
            'lymphoma date set to 2016-04-28 from the same FDA document; the text "not stated" replaced by a blank date and '
            'the column designation_date_precision; the Abecma and Aucatzyl announcement pages saved locally.',
        ],
        'defects_open': [
            'class_340b mixes two dates (see limitations); scheduled_registry_change carries the four affected centers.',
            'CCN 490101 (Inova Fair Oaks) is probably a crosswalk error for 490063 (Inova Fairfax), whose own record would '
            'make the row direct. The center set is not changed here; status_note says so.',
            'Three sibling rows (140010, 360016, 390050) rest on a general site with no cell-therapy clinic named; flagged by '
            'sibling_site_names_cell_therapy = 0.',
            'CCN 500138 (Fred Hutchinson) is enrolled at the address of the participating UW Medical Center parent record and '
            'is left outside by judgement; status_note says so.',
            'The registry copy comes from a mirror and is dated 2026-06-23; a current official export would settle the pending '
            'and ending records. The official page does not serve the file to a plain request.',
            'For CCNs 050348 and 170040 the year columns exceed the published total by $0.02, as in the CMS file.',
            'Hospital types exempt from the payment cut still show small remedy amounts as CMS published them (for example '
            '360242, 050660, 500138, 453300, 193300); reason unknown.',
            'The orphan table is hand-compiled. Abecma and Aucatzyl rest on FDA announcements without a designation date, and '
            'one Breyanzi row groups three supplemental approvals.',
            'The search did not record which saved FDA file came from which address, so each quote is confirmed in a saved '
            'document of the same product, not at a specific address in evidence_url. The Carvykti row names a 2024 '
            'approval letter whose address is not listed.',
        ],
    }, list(inputs.values()) + evidence + letters + letter_texts + collected)
    reported = numbers['class_340b_reported']
    print('STATUS_340B BUILT', len(rows), 'CENTERS', 'A/B/C', '/'.join(str(reported[k]['centers']) for k in 'ABC'),
          'REMEDY USD', numbers['remedy_usd']['remedy_total_2018_2022_usd'], 'ORPHAN ROWS', len(orphan),
          'QUOTES NOT FOUND', len(unverified), 'CHECKS FAILING', len(failing), '/', len(checks), flush=True)


if __name__ == '__main__':
    build()
