"""CMS Open Payments records for the 17,510-NPI universe and the 173 CAR-T centers, program years 2019-2025.

The seven program-year archives of the 2026-06-30 publication, already collected for the 2026-11-11
snapshot, are streamed once each. The General file gives every record paid to a universe NPI or to a
teaching hospital; the Research file gives every record naming a universe NPI as investigator or
recipient, or paid to a teaching hospital. Per-year extracts are kept under
`Data/interim/2026-11-19/industry_payments/` so a rebuild skips the scan. The 2026-11-11 tables for the
500 core HCPs are read for comparison, never rewritten.
"""
from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import io
import math
import os
import re
import statistics
import zipfile
from collections import Counter, defaultdict

from ..general_payments import nature_group, shape
from ..research_payments import (CART_PRODUCTS, CART_TEXT, LYMPHOMA_CART, PI_SLOTS, PRODUCT_SLOTS, RELEASE_DATE, YEARS,
                                 archive_name, cart_products)
from ..research_payments import RAW as ARCHIVES
from .base import (CENTERS, DIVISION_GROUPS, INTERIM, LABEL_VERSION, OUT, ROOT, SNAPSHOT, UNIVERSE, UNIVERSE_SNAPSHOT,
                   centers, column, dump, load, now, publish, read_csv, sha, stage, universe, write_csv,
                   write_module_metadata)

MODULE = 'industry_payments'
WORK = INTERIM / MODULE
SCAN_VERSION = '1.0.0'
PERIOD = f'{YEARS[0]}_{YEARS[-1]}'
RECENT = f'{YEARS[-2]}_{YEARS[-1]}'
CORE_SNAPSHOT = '2026-11-11'
TABLES = ['universe_cart_general_payments_2019_2025', 'hcp_universe_general_payment_features',
          'hcp_universe_research_payment_features', 'cart_industry_payments_by_division_2019_2025',
          'cart_center_teaching_hospital_payments_2019_2025']
DETAIL, GENERAL, RESEARCH, DIVISION, CENTER = TABLES

# Any brand, generic or development name of the seven approved CAR-T products; same patterns as the 2026-11-11 tables.
ANY_CART = re.compile('|'.join(f'(?:{pattern})' for pattern in CART_PRODUCTS.values()), re.I)
# Brand and generic names only. A record matched by ANY_CART but not by this is a development-code record.
NAMED_CART = re.compile('yescarta|axicabtagene|axi[- ]?cel|tecartus|brexucabtagene|brexu[- ]?cel|kymriah|tisagenlecleucel|'
                        'tisa[- ]?cel|breyanzi|lisocabtagene|liso[- ]?cel|abecma|idecabtagene|ide[- ]?cel|carvykti|'
                        'ciltacabtagene|cilta[- ]?cel|aucatzyl|obecabtagene|obe[- ]?cel', re.I)
# Companies whose marketed products are CAR-T therapies; matched on the name of the entity making the payment.
PURE_PLAY = re.compile(r'\bkite\b|legend biotech|autolus|\bjuno\b|2seventy', re.I)
DIRECT = 'No Third Party Payment'
HOSPITALITY_EDUCATION = {'Food and Beverage', 'Travel and Lodging', 'Education', 'Gift', 'Entertainment'}
CLASSES = ['services', 'hospitality_education', 'other_financial']
# Record rules of the division table, read from the flags of the row-level table.
RULES = {
    'any_slot': lambda r: r['any_slot'] == '1',
    'cart_only': lambda r: r['cart_only'] == '1',
    'cart_only_named_product': lambda r: r['cart_only'] == '1' and r['development_name_only'] == '0',
    'lymphoma_only': lambda r: r['lymphoma_only'] == '1',
    'pure_play_payer': lambda r: r['pure_play_payer'] == '1',
    'cart_only_or_pure_play_payer': lambda r: r['cart_only'] == '1' or r['pure_play_payer'] == '1',
}
# Column prefix in the per-NPI table for each rule published there.
FEATURE_RULES = {'cart_any_slot': 'any_slot', 'cart_only': 'cart_only', 'lymphoma_only': 'lymphoma_only',
                 'pure_play_payer': 'pure_play_payer', 'cart_only_or_pure_play': 'cart_only_or_pure_play_payer'}
GROUPS = list(dict.fromkeys(DIVISION_GROUPS.values()))
# CMS nature-of-payment categories given their own column in the teaching-hospital table.
HOSPITAL_NATURES = {
    'Royalty or License': 'royalty_license',
    'Grant': 'grant',
    'Space rental or facility fees (teaching hospital only)': 'space_rental',
    'Consulting Fee': 'consulting',
    'Compensation for services other than consulting, including serving as faculty or as a speaker at a venue other than a continuing education program': 'other_services_compensation',
    'Education': 'education',
}
HOSPITAL_PARTS = list(HOSPITAL_NATURES.values()) + ['other_nature']
# Tables of the 2026-11-19 search, staged only to report how the rebuild compares with them.
SEARCH_TABLES = {
    'general': 'local-industry-payments-by-division/npi_general_payment_summary_2019_2025.csv',
    'tier': 'local-industry-payments-by-division/npi_cart_tier_summary_2019_2025.csv',
    'research': 'local-industry-payments-by-division/npi_research_summary_2019_2025.csv',
    'hospital_general': 'local-industry-payments-by-division/teaching_hospital_general_payments_x_centers.csv',
    'hospital_research': 'local-industry-payments-by-division/teaching_hospital_research_payments_x_centers.csv',
    'division': 'gap-center-to-community-money-traces/partD/final_cart_industry_payment_table_2019_2025.csv',
}
# Totals of the 2026-06-30 publication for this universe and center set, asserted by check().
EXPECTED = {
    'universe_npis': 17510, 'npis_with_general_records': 16317, 'general_rows': 2583452, 'general_usd': 692851202.81,
    'detail_rows': 39655, 'any_slot_rows': 29750, 'any_slot_usd': 17803679.45, 'cart_only_rows': 23563,
    'cart_only_usd': 13215749.36, 'lymphoma_only_rows': 16725, 'lymphoma_only_usd': 11348829.12,
    'pure_play_payer_rows': 24860, 'pure_play_payer_usd': 11534540.33, 'cart_only_or_pure_play_rows': 33468,
    'cart_only_or_pure_play_usd': 15612193.08, 'development_name_only_rows': 471,
    'development_name_only_usd': 447824.08, 'npis_with_research_records': 8072, 'research_rows': 1142639,
    'research_investigator_rows': 1118148, 'research_recipient_only_rows': 24491,
    'npis_with_cart_product_research': 419, 'cart_product_research_rows': 11587,
    'centers': 173, 'centers_with_general_records': 160, 'center_general_rows': 124749,
    'center_general_usd': 5693414283.12, 'centers_with_research_records': 158, 'center_research_rows': 625224,
    'center_research_usd': 9626684901.84, 'center_cart_product_research_rows': 4400,
    'center_cart_product_research_usd': 95229117.73,
    'services_usd': 552189467.77, 'npis_with_services': 7294, 'hospitality_education_usd': 121571816.59,
    'other_financial_usd': 19089918.45, 'general_direct_usd': 637764121.92, 'general_third_party_rows': 31152,
    'any_slot_npis': 5082, 'cart_only_npis': 4638, 'lymphoma_only_npis': 3648, 'pure_play_payer_npis': 4455,
    'cart_only_or_pure_play_npis': 5301, 'any_slot_third_party_rows': 474, 'cart_only_third_party_rows': 268,
    'any_slot_co_listed_rows': 6187, 'pure_play_payer_rows_without_product': 9903,
    'pure_play_payer_rows_naming_only_other_products': 2, 'npis_named_only_as_recipient': 121,
    'npis_with_cart_product_research_as_investigator': 397, 'research_record_usd_summed_over_npis': 16761183705.47,
    'cart_product_research_record_usd_summed_over_npis': 215361200.99, 'center_general_usd_2024_2025': 1475119127.43,
    'center_royalty_license_usd': 4561879121.11, 'center_grant_usd': 412492808.82, 'center_space_rental_usd': 109824376.83,
    'center_consulting_usd': 106124247.80, 'center_education_usd': 56671332.21, 'center_cart_any_slot_rows': 172,
    'center_cart_any_slot_usd': 1287275.00, 'center_cart_only_usd': 1013275.00, 'center_lymphoma_only_usd': 874625.00,
    'centers_with_cart_product_research': 62, 'center_cart_product_or_text_research_rows': 7565,
    'center_cart_product_or_text_research_usd': 245347808.49,
}
# sha256 of every cell of each published table (see fingerprint). The all-records, research and center values cannot be
# recomputed from other published rows, so this is what tells a clone that two rows were swapped or a cell was edited.
# After a rebuild on a later publication, replace with the 'fingerprints' object of the module metadata.
FINGERPRINTS = {
    'universe_cart_general_payments_2019_2025': 'bc3c77c5448b2cb6b138172ed9ea871d96577cd47233a4299dac2abeb6b17593',
    'hcp_universe_general_payment_features': '26cfdfae64e9136754f8eaae7889cbd0301e717b1fa913fc9c404d252381050b',
    'hcp_universe_research_payment_features': 'f30d02f8442d1b8fb45f22fc9869de5f73bca2414a17f533bcfdabbba7e1c51f',
    'cart_industry_payments_by_division_2019_2025': '8e92247e49a0604dac53180be6ae4eefd9d91f17827c703d3672af729fed186e',
    'cart_center_teaching_hospital_payments_2019_2025': '7a119c198e038580e16f19600c4d2e4e119ada6b1953222a8e13fe8f498559ef',
}
# Direct CAR-T-only services figures quoted in the 2026-11-19 search report: division group -> (NPIs paid, US dollars).
REPORT_HEADLINE = {'core': (200, 4944608), 'affiliates': (338, 4475902), 'referring': (148, 727655)}


def to_cents(value):
    """Whole cents of a dollar amount, so sums are exact."""
    return round(float(value) * 100)


def usd(cents):
    return f'{cents / 100:.2f}'


def median_usd(cents):
    """Median of whole-cent amounts as dollar text; half a cent rounds up."""
    return usd(math.floor(statistics.median(cents) + 0.5))


def fingerprint(rows, key):
    """sha256 over every cell of a table read with read_csv, rows taken in key order."""
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: tuple(r[k] for k in key)):
        digest.update('\x1f'.join(f'{name}={value}' for name, value in row.items()).encode('utf-8') + b'\n')
    return digest.hexdigest()


def nature_class(nature):
    """Project class of a CMS nature-of-payment category."""
    if nature in ('Consulting Fee', 'Honoraria') or nature.startswith('Compensation for serv'):
        return 'services'
    return 'hospitality_education' if nature in HOSPITALITY_EDUCATION else 'other_financial'


def product_flags(names):
    """CAR-T flags of one record from its product-name fields, read field by field."""
    named = [name for name in names if name and name.strip()]
    brands = cart_products(*named)
    cart_only = bool(brands) and all(ANY_CART.search(name) for name in named)
    return {'cart_products': ';'.join(brands), 'any_slot': int(bool(brands)), 'cart_only': int(cart_only),
            'lymphoma_only': int(cart_only and bool(LYMPHOMA_CART.intersection(brands))),
            'development_name_only': int(bool(brands) and not any(NAMED_CART.search(name) for name in named))}


NO_PRODUCT = product_flags([])


def pure_play(manufacturer):
    return int(bool(PURE_PLAY.search(manufacturer)))


def research_links(investigators, recipient, npis):
    """(NPI, role) for each universe NPI on a research record; a recipient who is also an investigator counts once."""
    links = [(npi, 'investigator') for npi in dict.fromkeys(investigators) if npi in npis]
    if recipient in npis and recipient not in investigators:
        links.append((recipient, 'recipient_only'))
    return links


def research_text_match(study, context):
    """Study name or research context names a CAR-T product or generic CAR-T wording (rule of the 2026-11-11 table)."""
    return int(bool(cart_products(study, context)) or bool(CART_TEXT.search(study + ' ' + context)))


def tally_general(header, rows, npis):
    """One pass over General detail rows: candidate records, per-NPI cells and teaching-hospital cells."""
    at = {name: i for i, name in enumerate(header)}
    npi_at, ccn_at, hospital_at = at['covered_recipient_npi'], at['teaching_hospital_ccn'], at['teaching_hospital_name']
    amount_at, nature_at = at['total_amount_of_payment_usdollars'], at['nature_of_payment_or_transfer_of_value']
    payer_at = at['applicable_manufacturer_or_applicable_gpo_making_payment_name']
    third_at, year_at = at['third_party_payment_recipient_indicator'], at['program_year']
    slots = [at[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] for k in PRODUCT_SLOTS]
    people, hospitals = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    candidates, payers, years, counts = [], {}, set(), Counter()
    scanned = 0
    for row in rows:
        scanned += 1
        npi, ccn = row[npi_at], row[ccn_at]
        hit = npi in npis
        if not hit and not ccn:
            continue
        cents = to_cents(row[amount_at])
        counts['amounts_not_in_whole_cents'] += abs(float(row[amount_at]) * 100 - cents) > 1e-3
        names = [row[i] for i in slots]
        flags = product_flags(names) if ANY_CART.search('|'.join(names)) else NO_PRODUCT
        years.add(row[year_at])
        if hit:
            counts['universe_rows'] += 1
            cell = people[npi, row[nature_at], row[third_at]]
            cell[0] += 1
            cell[1] += cents
            payer = row[payer_at]
            if payer not in payers:
                payers[payer] = pure_play(payer)
            if flags['any_slot'] or payers[payer]:
                candidates.append(row)
        if ccn:
            counts['teaching_hospital_rows'] += 1
            counts['teaching_hospital_rows_with_npi'] += bool(npi)
            cell = hospitals[ccn, row[hospital_at], row[nature_at], flags['any_slot'], flags['cart_only'], flags['lymphoma_only']]
            cell[0] += 1
            cell[1] += cents
    return {'candidates': candidates, 'people': people, 'hospitals': hospitals, 'years': years,
            'counts': {'rows_scanned': scanned, **counts}}


def tally_research(header, rows, npis):
    """One pass over Research detail rows: per-NPI cells by role and teaching-hospital cells."""
    at = {name: i for i, name in enumerate(header)}
    pi_at = [at[f'principal_investigator_{k}_npi'] for k in PI_SLOTS]
    npi_at, ccn_at, hospital_at = at['covered_recipient_npi'], at['teaching_hospital_ccn'], at['teaching_hospital_name']
    amount_at, type_at, year_at = at['total_amount_of_payment_usdollars'], at['covered_recipient_type'], at['program_year']
    study_at, context_at = at['name_of_study'], at['context_of_research']
    slots = [at[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] for k in PRODUCT_SLOTS]
    people, hospitals, by_type = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    years, counts = set(), Counter()
    scanned = 0
    for row in rows:
        scanned += 1
        names = [row[i] for i in slots]
        brands = cart_products(*names) if ANY_CART.search('|'.join(names)) else []
        if brands:
            cell = by_type[row[type_at]]
            cell[0] += 1
            cell[1] += to_cents(row[amount_at])
        links = research_links([row[i] for i in pi_at if row[i]], row[npi_at], npis)
        ccn = row[ccn_at]
        if not links and not ccn:
            continue
        cents = to_cents(row[amount_at])
        text = research_text_match(row[study_at], row[context_at])
        years.add(row[year_at])
        counts['records_naming_universe_npi'] += bool(links)
        for npi, role in links:
            cell = people[npi, role, ';'.join(brands), text]
            cell[0] += 1
            cell[1] += cents
        if ccn:
            counts['teaching_hospital_rows'] += 1
            cell = hospitals[ccn, row[hospital_at], int(bool(brands)), text]
            cell[0] += 1
            cell[1] += cents
    return {'people': people, 'hospitals': hospitals, 'years': years, 'counts': {'rows_scanned': scanned, **counts},
            'cart_product_records_by_recipient_type': {k: {'rows': v[0], 'usd': v[1] / 100} for k, v in sorted(by_type.items())}}


CELL_FIELDS = {
    'general_people': ['npi', 'payment_nature', 'third_party_payment_indicator'],
    'general_hospitals': ['ccn', 'teaching_hospital_name', 'payment_nature', 'any_slot', 'cart_only', 'lymphoma_only'],
    'research_people': ['npi', 'role', 'cart_products', 'cart_text_match'],
    'research_hospitals': ['ccn', 'teaching_hospital_name', 'cart_product_mentioned', 'cart_text_match'],
}


def cell_rows(kind, part, cells):
    """Tally cells as text rows, the form they are saved in and read back."""
    fields = CELL_FIELDS[f'{kind}_{part}']
    return [{name: str(value) for name, value in [*zip(fields, key), ('rows', cell[0]), ('cents', cell[1])]}
            for key, cell in sorted(cells.items())]


def scan(job):
    """Stream one detail file of one program-year archive and save its extracts; skipped when already done."""
    kind, year = job
    summary_path = WORK / f'{kind}_scan_{year}.json'
    meta = load((ARCHIVES / archive_name(year)).with_suffix('.meta.json'))
    stamp = {'scan_version': SCAN_VERSION, 'archive_sha256': meta['sha256'], 'universe_sha256': sha(UNIVERSE)}
    if summary_path.exists():
        done = load(summary_path)
        if all(done.get(k) == v for k, v in stamp.items()) and all((WORK / name).exists() for name in done['outputs']):
            return done
    WORK.mkdir(parents=True, exist_ok=True)
    npis = set(universe())
    archive_path = ARCHIVES / archive_name(year)
    assert sha(archive_path) == meta['sha256'], f'Archive differs from its recorded hash: {archive_path}'
    code = {'general': 'GNRL', 'research': 'RSRCH'}[kind]
    with zipfile.ZipFile(archive_path) as archive:
        members = [m for m in archive.infolist() if re.search(r'OP_DTL_%s_PGYR%d_.*\.csv$' % (code, year), m.filename)]
        assert len(members) == 1, f'Expected one {kind} detail file for {year}'
        member = members[0]
        with archive.open(member) as binary:
            reader = csv.reader(io.TextIOWrapper(binary, encoding='utf-8-sig', newline=''))
            header = [name.strip().lower() for name in next(reader)]
            result = (tally_general if kind == 'general' else tally_research)(header, reader, npis)
    assert result['years'] <= {str(year)}, f'Unexpected program year in {member.filename}'
    outputs = {}
    if kind == 'general':
        path = WORK / f'general_candidates_{year}.csv'
        with path.open('w', newline='', encoding='utf-8') as sink:
            csv.writer(sink).writerows([header] + result['candidates'])
        outputs[path.name] = sha(path)
    for part in ['people', 'hospitals']:
        path = WORK / f'{kind}_{part}_{year}.csv'
        write_csv(path, cell_rows(kind, part, result[part]), CELL_FIELDS[f'{kind}_{part}'] + ['rows', 'cents'])
        outputs[path.name] = sha(path)
    summary = {**stamp, 'kind': kind, 'program_year': year, 'archive': archive_name(year), 'member': member.filename,
               'member_bytes': member.file_size, 'member_crc32': f'{member.CRC:08x}', 'columns': len(header),
               **result['counts'], 'candidate_rows': len(result.get('candidates', [])), 'outputs': outputs,
               'cart_product_records_by_recipient_type': result.get('cart_product_records_by_recipient_type', {}),
               'scanned_at_utc': now(), 'retrieved_at_utc': meta['retrieved_at_utc']}
    dump(summary_path, summary)
    print(MODULE.upper(), 'SCAN', kind, year, 'ROWS', summary['rows_scanned'], flush=True)
    return summary


def detail_row(record, source):
    """One published row of the row-level table from a General record."""
    row = shape(record, {record['covered_recipient_npi']: ''}, source)
    flags = product_flags([record[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] for k in PRODUCT_SLOTS])
    indicator = record['third_party_payment_recipient_indicator']
    row.update({**flags, 'covered_recipient_type': record['covered_recipient_type'],
                'amount_usd': usd(to_cents(record['total_amount_of_payment_usdollars'])),
                'nature_class': nature_class(record['nature_of_payment_or_transfer_of_value']),
                'pure_play_payer': pure_play(record['applicable_manufacturer_or_applicable_gpo_making_payment_name']),
                'third_party_payment_indicator': indicator, 'direct_payment': int(indicator == DIRECT)})
    return {name: str(value) for name, value in row.items()}


def spread(rows, prefix, years=True):
    """Rows, dollars and services dollars of a set of row-level records, in total, direct and third-party."""
    direct = [r for r in rows if r['direct_payment'] == '1']
    def total(part, services=False):
        return usd(sum(to_cents(r['amount_usd']) for r in part if not services or r['nature_class'] == 'services'))
    out = {f'{prefix}_rows_{PERIOD}': len(rows), f'{prefix}_usd_{PERIOD}': total(rows),
           f'{prefix}_services_usd_{PERIOD}': total(rows, True),
           f'{prefix}_direct_rows_{PERIOD}': len(direct), f'{prefix}_direct_usd_{PERIOD}': total(direct),
           f'{prefix}_direct_services_usd_{PERIOD}': total(direct, True),
           f'{prefix}_third_party_rows_{PERIOD}': len(rows) - len(direct),
           f'{prefix}_third_party_usd_{PERIOD}': total([r for r in rows if r['direct_payment'] != '1'])}
    if years:
        out[f'{prefix}_active_years'] = len({r['program_year'] for r in rows})
    return out


def general_features(npi, cells, detail):
    """One zero-filled row of the per-NPI General table from the NPI's scan cells and row-level records."""
    def tally(part):
        return sum(int(c['rows']) for c in part), sum(int(c['cents']) for c in part)
    direct = [c for c in cells if c['third_party_payment_indicator'] == DIRECT]
    rows, cents = tally(cells)
    out = {'npi': npi, 'general_data_status': 'published_records' if rows else 'no_published_records',
           f'general_payment_rows_{PERIOD}': rows, f'general_payments_usd_{PERIOD}': usd(cents)}
    for label in CLASSES:
        part = tally([c for c in cells if nature_class(c['payment_nature']) == label])
        out.update({f'{label}_rows_{PERIOD}': part[0], f'{label}_usd_{PERIOD}': usd(part[1])})
    part = tally(direct)
    out.update({f'general_direct_rows_{PERIOD}': part[0], f'general_direct_usd_{PERIOD}': usd(part[1])})
    for label in CLASSES:
        part = tally([c for c in direct if nature_class(c['payment_nature']) == label])
        out.update({f'{label}_direct_rows_{PERIOD}': part[0], f'{label}_direct_usd_{PERIOD}': usd(part[1])})
    out.update({f'general_third_party_rows_{PERIOD}': rows - tally(direct)[0],
                f'general_third_party_usd_{PERIOD}': usd(cents - tally(direct)[1]),
                'general_active_years': len({c['program_year'] for c in cells})})
    for prefix, rule in FEATURE_RULES.items():
        out.update(spread([r for r in detail if RULES[rule](r)], prefix))
    out['cart_any_slot_brands'] = ';'.join(sorted({b for r in detail for b in r['cart_products'].split(';') if b}))
    return {**out, 'program_years_scanned': f'{YEARS[0]}-{YEARS[-1]}', 'source_release_date': RELEASE_DATE}


def research_features(npi, cells):
    """One row of the per-NPI Research table; counts are zero-filled, dollar sums stay blank without a record."""
    def tally(part):
        return sum(int(c['rows']) for c in part), sum(int(c['cents']) for c in part)
    def by_role(part, role):
        return tally([c for c in part if c['role'] == role])[0]
    product = [c for c in cells if c['cart_products']]
    either = [c for c in cells if c['cart_products'] or c['cart_text_match'] == '1']
    rows, cents = tally(cells)
    return {
        'npi': npi, 'research_data_status': 'published_records' if rows else 'no_published_records',
        f'research_rows_{PERIOD}': rows, f'research_investigator_rows_{PERIOD}': by_role(cells, 'investigator'),
        f'research_recipient_only_rows_{PERIOD}': by_role(cells, 'recipient_only'),
        f'research_record_usd_{PERIOD}': usd(cents) if rows else '',
        'research_active_years': len({c['program_year'] for c in cells}),
        f'cart_product_research_rows_{PERIOD}': tally(product)[0],
        f'cart_product_research_investigator_rows_{PERIOD}': by_role(product, 'investigator'),
        f'cart_product_research_recipient_only_rows_{PERIOD}': by_role(product, 'recipient_only'),
        f'cart_product_research_record_usd_{PERIOD}': usd(tally(product)[1]) if product else '',
        'cart_product_research_brands': ';'.join(sorted({b for c in product for b in c['cart_products'].split(';')})),
        f'cart_product_or_text_research_rows_{PERIOD}': tally(either)[0],
        f'cart_product_or_text_research_record_usd_{PERIOD}': usd(tally(either)[1]) if either else '',
        'program_years_scanned': f'{YEARS[0]}-{YEARS[-1]}', 'source_release_date': RELEASE_DATE,
    }


def division_table(detail, divisions):
    """Rule x division group x nature class summary of the row-level table; divisions maps NPI to division label."""
    sizes = Counter(DIVISION_GROUPS[label] for label in divisions.values())
    out = []
    for rule, test in RULES.items():
        matched = [r for r in detail if test(r)]
        for group in GROUPS:
            in_group = [r for r in matched if DIVISION_GROUPS[divisions[r['npi']]] == group]
            for label in ['all'] + CLASSES:
                rows = [r for r in in_group if label == 'all' or r['nature_class'] == label]
                direct, third = defaultdict(int), defaultdict(int)
                for r in rows:
                    (direct if r['direct_payment'] == '1' else third)[r['npi']] += to_cents(r['amount_usd'])
                direct_rows = sum(r['direct_payment'] == '1' for r in rows)
                out.append({
                    'rule': rule, 'division_group': group, 'nature_class': label, 'npis_in_division_group': sizes[group],
                    'npis_paid': len(set(direct) | set(third)), 'rows': len(rows),
                    'usd': usd(sum(direct.values()) + sum(third.values())),
                    'direct_npis_paid': len(direct), 'direct_rows': direct_rows, 'direct_usd': usd(sum(direct.values())),
                    'direct_median_usd_per_paid_npi': median_usd(direct.values()) if direct else '',
                    'third_party_npis_paid': len(third), 'third_party_rows': len(rows) - direct_rows,
                    'third_party_usd': usd(sum(third.values())),
                    'universe_snapshot_date': UNIVERSE_SNAPSHOT, 'division_label_version': LABEL_VERSION,
                    'program_years_scanned': f'{YEARS[0]}-{YEARS[-1]}', 'source_release_date': RELEASE_DATE,
                })
    return [{name: str(value) for name, value in row.items()} for row in out]


def center_row(ccn, general, research):
    """One zero-filled row of the center table from the CCN's General and Research teaching-hospital cells."""
    def tally(part):
        return sum(int(c['rows']) for c in part), sum(int(c['cents']) for c in part)
    names = Counter()
    for cell in general or research:
        names[cell['teaching_hospital_name']] += int(cell['rows'])
    rows, cents = tally(general)
    out = {'ccn': ccn, 'teaching_hospital_name': min(names, key=lambda n: (-names[n], n)) if names else '',
           'general_data_status': 'published_records' if general else 'no_published_records',
           f'general_payment_rows_{PERIOD}': rows, f'general_payments_usd_{PERIOD}': usd(cents),
           f'general_payments_usd_{RECENT}': usd(tally([c for c in general if c['program_year'] in RECENT.split('_')])[1]),
           'general_active_years': len({c['program_year'] for c in general})}
    for part in HOSPITAL_PARTS:
        kept = [c for c in general if HOSPITAL_NATURES.get(c['payment_nature'], 'other_nature') == part]
        out[f'{part}_usd_{PERIOD}'] = usd(tally(kept)[1])
    for prefix, flag in [('cart_any_slot', 'any_slot'), ('cart_only', 'cart_only'), ('lymphoma_only', 'lymphoma_only')]:
        part = tally([c for c in general if c[flag] == '1'])
        out.update({f'{prefix}_rows_{PERIOD}': part[0], f'{prefix}_usd_{PERIOD}': usd(part[1])})
    product = tally([c for c in research if c['cart_product_mentioned'] == '1'])
    either = tally([c for c in research if c['cart_product_mentioned'] == '1' or c['cart_text_match'] == '1'])
    rows, cents = tally(research)
    out.update({'research_data_status': 'published_records' if research else 'no_published_records',
                f'research_rows_{PERIOD}': rows, f'research_payments_usd_{PERIOD}': usd(cents),
                'research_active_years': len({c['program_year'] for c in research}),
                f'cart_product_research_rows_{PERIOD}': product[0], f'cart_product_research_usd_{PERIOD}': usd(product[1]),
                f'cart_product_or_text_research_rows_{PERIOD}': either[0],
                f'cart_product_or_text_research_usd_{PERIOD}': usd(either[1]),
                'program_years_scanned': f'{YEARS[0]}-{YEARS[-1]}', 'source_release_date': RELEASE_DATE})
    return out


def headline(tables, divisions=None):
    """Numbers a README can quote, recomputed from the published rows (values may be strings or numbers).

    With `divisions` (NPI to division label) the per-division counts are added.
    """
    def total(rows, name):
        return sum(to_cents(r[name] or 0) for r in rows) / 100
    def count(rows, name):
        return sum(int(r[name]) > 0 for r in rows)
    detail, general, research, division, center = (tables[name] for name in TABLES)
    out = {'universe_npis': len(general), 'npis_with_general_records': count(general, f'general_payment_rows_{PERIOD}'),
           'general_rows': int(total(general, f'general_payment_rows_{PERIOD}')),
           'general_usd': total(general, f'general_payments_usd_{PERIOD}'),
           'general_direct_usd': total(general, f'general_direct_usd_{PERIOD}'),
           'general_third_party_rows': int(total(general, f'general_third_party_rows_{PERIOD}')),
           'detail_rows': len(detail)}
    for label in CLASSES:
        out[f'{label}_usd'] = total(general, f'{label}_usd_{PERIOD}')
        out[f'npis_with_{label}'] = count(general, f'{label}_rows_{PERIOD}')
    for prefix, rule in [('any_slot', 'any_slot'), ('cart_only', 'cart_only'), ('lymphoma_only', 'lymphoma_only'),
                         ('pure_play_payer', 'pure_play_payer'), ('cart_only_or_pure_play', 'cart_only_or_pure_play_payer'),
                         ('development_name_only', None)]:
        rows = [r for r in detail if (RULES[rule](r) if rule else str(r['development_name_only']) == '1')]
        out.update({f'{prefix}_rows': len(rows), f'{prefix}_usd': total(rows, 'amount_usd'),
                    f'{prefix}_npis': len({r['npi'] for r in rows}),
                    f'{prefix}_third_party_rows': sum(r['direct_payment'] != '1' for r in rows)})
    co_listed = [r for r in detail if r['any_slot'] == '1' and r['cart_only'] == '0']
    out.update({'any_slot_co_listed_rows': len(co_listed), 'any_slot_co_listed_usd': total(co_listed, 'amount_usd'),
                'any_slot_co_listed_rows_by_manufacturer': dict(Counter(r['manufacturer'] for r in co_listed).most_common()),
                'any_slot_co_listed_rows_by_program_year': dict(sorted(Counter(r['program_year'] for r in co_listed).items())),
                'development_name_only_rows_by_product': dict(Counter(r['products'] for r in detail if r['development_name_only'] == '1').most_common()),
                'pure_play_payer_rows_without_product': sum(r['pure_play_payer'] == '1' and not r['products'] for r in detail),
                'pure_play_payer_rows_naming_only_other_products': sum(r['pure_play_payer'] == '1' and r['any_slot'] == '0' and bool(r['products']) for r in detail),
                'pure_play_payer_rows_without_product_by_payer': dict(Counter(r['manufacturer'] for r in detail if r['pure_play_payer'] == '1' and not r['products']).most_common()),
                'cart_only_services_usd_by_manufacturer': {k: round(v / 100, 2) for k, v in sorted(_sum_by(
                    [r for r in detail if r['cart_only'] == '1' and r['nature_class'] == 'services'], 'manufacturer').items(), key=lambda x: -x[1])},
                'pure_play_payers': dict(Counter(r['manufacturer'] for r in detail if r['pure_play_payer'] == '1').most_common()),
                'any_slot_rows_by_program_year': dict(sorted(Counter(r['program_year'] for r in detail if r['any_slot'] == '1').items()))})
    for row in division:
        if row['rule'] in ('cart_only', 'cart_only_named_product', 'lymphoma_only', 'pure_play_payer') and row['nature_class'] == 'services':
            out[f"{row['rule']}_services_direct_{row['division_group']}"] = {
                'npis': int(row['direct_npis_paid']), 'usd': float(row['direct_usd']),
                'median_usd_per_paid_npi': float(row['direct_median_usd_per_paid_npi']) if row['direct_median_usd_per_paid_npi'] else None,
                'third_party_usd': float(row['third_party_usd'])}
    out.update({'npis_with_research_records': count(research, f'research_rows_{PERIOD}'),
                'research_rows': int(total(research, f'research_rows_{PERIOD}')),
                'research_investigator_rows': int(total(research, f'research_investigator_rows_{PERIOD}')),
                'research_recipient_only_rows': int(total(research, f'research_recipient_only_rows_{PERIOD}')),
                'npis_named_only_as_recipient': sum(int(r[f'research_rows_{PERIOD}']) > 0 and int(r[f'research_investigator_rows_{PERIOD}']) == 0 for r in research),
                'npis_with_cart_product_research': count(research, f'cart_product_research_rows_{PERIOD}'),
                'npis_with_cart_product_research_as_investigator': count(research, f'cart_product_research_investigator_rows_{PERIOD}'),
                'cart_product_research_rows': int(total(research, f'cart_product_research_rows_{PERIOD}')),
                # Whole-record amounts repeated for every investigator: a checksum of the column, not a dollar total.
                'research_record_usd_summed_over_npis': total(research, f'research_record_usd_{PERIOD}'),
                'cart_product_research_record_usd_summed_over_npis': total(research, f'cart_product_research_record_usd_{PERIOD}'),
                'centers': len(center), 'centers_with_general_records': count(center, f'general_payment_rows_{PERIOD}'),
                'centers_with_general_records_in_all_years': sum(int(r['general_active_years']) == len(YEARS) for r in center),
                'center_general_rows': int(total(center, f'general_payment_rows_{PERIOD}')),
                'center_general_usd': total(center, f'general_payments_usd_{PERIOD}'),
                'center_general_usd_2024_2025': total(center, f'general_payments_usd_{RECENT}'),
                **{f'center_{part}_usd': total(center, f'{part}_usd_{PERIOD}') for part in HOSPITAL_PARTS},
                'centers_with_cart_any_slot_general': count(center, f'cart_any_slot_rows_{PERIOD}'),
                'center_cart_any_slot_rows': int(total(center, f'cart_any_slot_rows_{PERIOD}')),
                'center_cart_any_slot_usd': total(center, f'cart_any_slot_usd_{PERIOD}'),
                'center_cart_only_usd': total(center, f'cart_only_usd_{PERIOD}'),
                'center_lymphoma_only_usd': total(center, f'lymphoma_only_usd_{PERIOD}'),
                'centers_with_research_records': count(center, f'research_rows_{PERIOD}'),
                'center_research_rows': int(total(center, f'research_rows_{PERIOD}')),
                'center_research_usd': total(center, f'research_payments_usd_{PERIOD}'),
                'centers_with_cart_product_research': count(center, f'cart_product_research_rows_{PERIOD}'),
                'center_cart_product_research_rows': int(total(center, f'cart_product_research_rows_{PERIOD}')),
                'center_cart_product_research_usd': total(center, f'cart_product_research_usd_{PERIOD}'),
                'center_cart_product_or_text_research_rows': int(total(center, f'cart_product_or_text_research_rows_{PERIOD}')),
                'center_cart_product_or_text_research_usd': total(center, f'cart_product_or_text_research_usd_{PERIOD}'),
                'centers_without_general_records': sorted(r['ccn'] for r in center if r['general_data_status'] == 'no_published_records'),
                'centers_without_research_records': sorted(r['ccn'] for r in center if r['research_data_status'] == 'no_published_records')})
    if divisions:
        by_label = {}
        for label in dict.fromkeys(divisions.values()):
            mine, studies = ([r for r in table if divisions.get(r['npi']) == label] for table in (general, research))
            by_label[label] = {
                'npis': len(mine), 'npis_with_general_records': count(mine, f'general_payment_rows_{PERIOD}'),
                'general_usd': total(mine, f'general_payments_usd_{PERIOD}'),
                'npis_with_services': count(mine, f'services_rows_{PERIOD}'), 'services_usd': total(mine, f'services_usd_{PERIOD}'),
                'npis_with_cart_any_slot': count(mine, f'cart_any_slot_rows_{PERIOD}'),
                'cart_any_slot_rows': int(total(mine, f'cart_any_slot_rows_{PERIOD}')),
                'cart_any_slot_usd': total(mine, f'cart_any_slot_usd_{PERIOD}'),
                'npis_with_cart_only': count(mine, f'cart_only_rows_{PERIOD}'), 'cart_only_usd': total(mine, f'cart_only_usd_{PERIOD}'),
                'npis_with_research_records': count(studies, f'research_rows_{PERIOD}'),
                'npis_with_cart_product_research': count(studies, f'cart_product_research_rows_{PERIOD}'),
                'npis_with_cart_product_research_as_investigator': count(studies, f'cart_product_research_investigator_rows_{PERIOD}'),
                'cart_product_research_rows': int(total(studies, f'cart_product_research_rows_{PERIOD}'))}
        out['by_division'] = dict(sorted(by_label.items()))
    return out


def _sum_by(rows, key):
    out = defaultdict(int)
    for row in rows:
        out[row[key]] += to_cents(row['amount_usd'])
    return out


# ---- published columns -------------------------------------------------------------------------------------------

NEVER = 'Never blank.'
MONEY = re.compile('usd')
COUNT = re.compile('rows|active_years|npis|number_of_payments')
FLAGS = ['any_slot', 'cart_only', 'lymphoma_only', 'development_name_only', 'pure_play_payer', 'direct_payment']
ISO_DATES = ['payment_date', 'payment_publication_date', 'source_release_date', 'universe_snapshot_date']
STATUSES = ('published_records', 'no_published_records')
ZERO = 'Never blank; 0 means the full scan of the 2026-06-30 publication found no such record.'
SPAN = f'program years {YEARS[0]}-{YEARS[-1]}'


def op(name, description, missing=NEVER):
    return column(name, description, missing, 'open_payments')


def provenance_columns():
    return [op('program_years_scanned', 'Range of Open Payments program years scanned in full.'),
            op('source_release_date', 'Date of the Open Payments publication the values were read from.')]


RULE_TEXT = {
    'cart_any_slot': 'that name a CAR-T product in at least one product-name field, other products allowed on the same record',
    'cart_only': 'on which every named product is a CAR-T product',
    'lymphoma_only': 'on which every named product is a CAR-T product and one is Yescarta, Tecartus, Kymriah or Breyanzi',
    'pure_play_payer': 'paid by Kite Pharma, Legend Biotech or Autolus, whatever the product fields say',
    'cart_only_or_pure_play': 'that satisfy the cart_only rule or the pure_play_payer rule, each record counted once',
}


def spread_columns(prefix, what, years=True):
    rest = '' if 'pure_play' in prefix else f' The rest of {prefix}_usd_{PERIOD} is hospitality_education: no record under this rule has an other_financial nature.'
    out = [op(f'{prefix}_rows_{PERIOD}', f'Number of General Payment records to this NPI, {SPAN}, {what}.', ZERO),
           op(f'{prefix}_usd_{PERIOD}', f'US dollars on the records counted in {prefix}_rows_{PERIOD}, all payment natures, payments to third parties included.', ZERO),
           op(f'{prefix}_services_usd_{PERIOD}', f'Part of {prefix}_usd_{PERIOD} whose nature class is services (consulting, honoraria, speaking and faculty compensation).' + rest, ZERO),
           op(f'{prefix}_direct_rows_{PERIOD}', f'Records counted in {prefix}_rows_{PERIOD} that were paid to the recipient directly (no third-party payee).', ZERO),
           op(f'{prefix}_direct_usd_{PERIOD}', f'US dollars on the records counted in {prefix}_direct_rows_{PERIOD}.', ZERO),
           op(f'{prefix}_direct_services_usd_{PERIOD}', f'Part of {prefix}_direct_usd_{PERIOD} whose nature class is services.', ZERO),
           op(f'{prefix}_third_party_rows_{PERIOD}', f'Records counted in {prefix}_rows_{PERIOD} that were paid to a third party on the recipient\'s behalf; direct plus third-party equals the total.', ZERO),
           op(f'{prefix}_third_party_usd_{PERIOD}', f'US dollars on the records counted in {prefix}_third_party_rows_{PERIOD}.', ZERO)]
    if years:
        out.append(op(f'{prefix}_active_years', f'Number of program years of {YEARS[0]}-{YEARS[-1]} (0 to {len(YEARS)}) with at least one record counted in {prefix}_rows_{PERIOD}.', ZERO))
    return out


CLASS_TEXT = {
    'services': 'services (CMS natures Consulting Fee, Honoraria, compensation for services other than consulting, and compensation for serving as faculty or speaker)',
    'hospitality_education': 'hospitality_education (CMS natures Food and Beverage, Travel and Lodging, Education, Gift and Entertainment)',
    'other_financial': 'other_financial (every other CMS nature, such as Grant, Royalty or License, Acquisitions or ownership interest)',
}


def detail_columns():
    return [
        column('npi', 'National Provider Identifier (NPI) of the covered recipient; always one of the 17,510 NPIs of the 2026-10-24 universe table hcp_divisions.csv, matched by exact equality.', NEVER, 'project'),
        op('record_id', 'Identifier of the General Payment record in the Open Payments publication of 2026-06-30 from the Centers for Medicare & Medicaid Services (CMS); unique across program years.'),
        op('cms_profile_id', 'Open Payments profile identifier of the covered recipient on the record.'),
        op('covered_recipient_type', 'Open Payments covered-recipient type on the record, as published: Covered Recipient Physician or Covered Recipient Non-Physician Practitioner. It describes the record, not the specialty of the person.'),
        op('program_year', 'Open Payments program year of the record, 2019 to 2025.'),
        op('payment_date', 'Date of payment reported by the manufacturer, in ISO format (YYYY-MM-DD); always a date within program_year.'),
        op('amount_usd', 'Total amount of the payment or transfer of value in US dollars, as reported by the manufacturer; whole cents, always above zero in this table.'),
        op('number_of_payments', 'Number of payments included in the total amount.'),
        op('manufacturer', 'Name of the applicable manufacturer or group purchasing organization making the payment, as published.'),
        op('manufacturer_id', 'CMS identifier of the manufacturer making the payment.'),
        op('submitting_manufacturer', 'Name of the manufacturer or group purchasing organization that submitted the record.'),
        op('payment_nature', 'CMS nature of payment or transfer of value, as published.'),
        op('nature_class', 'Project class of payment_nature: ' + '; '.join(CLASS_TEXT.values()) + '.'),
        op('nature_group', 'Grouping of payment_nature used in general_payments_2019_2025.csv of the 2026-11-11 snapshot (consulting, honoraria, speaking_non_cme, medical_education, grant, hospitality, other), kept so the two snapshots can be compared.'),
        op('payment_form', 'CMS form of payment or transfer of value, as published.'),
        op('products', 'Product names from the five product-name fields of the record, in field order, separated by semicolons.', 'No product is named on the record.'),
        op('product_categories', 'Distinct product categories or therapeutic areas the manufacturer assigned to the named products, separated by semicolons.', 'No category is reported on the record.'),
        op('cart_products', 'Brands of the chimeric antigen receptor T-cell (CAR-T) products matched in the product-name fields by brand, generic or development name: YESCARTA, TECARTUS, KYMRIAH, BREYANZI, ABECMA, CARVYKTI or AUCATZYL, separated by semicolons.', 'No CAR-T product is named; the record is in the table because of its payer.'),
        op('any_slot', '1 when at least one product-name field names a CAR-T product, other products allowed on the same record; else 0.'),
        op('cart_only', '1 when at least one product is named and every named product is a CAR-T product; else 0. Records with all product fields blank are 0.'),
        op('lymphoma_only', '1 when cart_only is 1 and a named product is Yescarta, Tecartus, Kymriah or Breyanzi; else 0. A product rule, not an indication rule: these products are also used outside lymphoma.'),
        op('development_name_only', '1 when any_slot is 1 and no brand or generic name appears, only a development code such as JCAR017 or BB-2121 (payments made before approval); else 0.'),
        op('pure_play_payer', '1 when the name of the manufacturer making the payment matches Kite, Legend Biotech, Autolus, Juno or 2seventy, companies whose marketed products are CAR-T therapies, whatever the product fields say; else 0. Only Kite Pharma, Inc., Legend Biotech USA Inc. and Autolus Inc occur.'),
        op('third_party_payment_indicator', 'CMS third-party payment recipient indicator as published. No Third Party Payment and Entity occur in this table; CMS also uses Individual, which no record here carries. The name of the third party is not published.'),
        op('direct_payment', '1 when third_party_payment_indicator is No Third Party Payment, that is, paid to the recipient directly; 0 when paid to a third party on the recipient\'s behalf.'),
        op('delay_in_publication_indicator', 'CMS delay-in-publication indicator on the published record.'),
        op('dispute_status', 'CMS dispute status for publication.'),
        op('change_type', 'CMS change type of the record in this publication (NEW, ADD, CHANGED or UNCHANGED).'),
        op('payment_publication_date', 'Payment publication date on the CMS record, in ISO format (YYYY-MM-DD); 2026-06-30 on every record of this publication.'),
        op('source_release_date', 'Date of the Open Payments publication the record was read from.'),
        op('source_file', 'General Payment detail file inside the program-year archive the record was read from.'),
    ]


def general_columns():
    out = [
        column('npi', 'National Provider Identifier (NPI) from the 2026-10-24 universe table hcp_divisions.csv; every universe NPI has exactly one row. Join that table on npi for name, place and division.', NEVER, 'project'),
        op('general_data_status', 'published_records when at least one General Payment record of the Centers for Medicare & Medicaid Services (CMS) Open Payments publication of 2026-06-30 names this NPI as covered recipient in ' + SPAN + ', else no_published_records.'),
        op(f'general_payment_rows_{PERIOD}', f'Number of General Payment records whose covered-recipient NPI equals this NPI, {SPAN}; every nature, manufacturer and product, payments to third parties included.', ZERO),
        op(f'general_payments_usd_{PERIOD}', f'US dollars on the records counted in general_payment_rows_{PERIOD}, as reported by manufacturers.', ZERO),
    ]
    for label in CLASSES:
        out += [op(f'{label}_rows_{PERIOD}', f'Number of General Payment records to this NPI, {SPAN}, in nature class {CLASS_TEXT[label]}.', ZERO),
                op(f'{label}_usd_{PERIOD}', f'US dollars on the records counted in {label}_rows_{PERIOD}. The three class columns sum to general_payments_usd_{PERIOD}.', ZERO)]
    out += [op(f'general_direct_rows_{PERIOD}', f'Records counted in general_payment_rows_{PERIOD} whose CMS third-party payment indicator is No Third Party Payment, that is, paid to the recipient directly.', ZERO),
            op(f'general_direct_usd_{PERIOD}', f'US dollars on the records counted in general_direct_rows_{PERIOD}.', ZERO)]
    for label in CLASSES:
        out += [op(f'{label}_direct_rows_{PERIOD}', f'Records counted in {label}_rows_{PERIOD} that were paid to the recipient directly.', ZERO),
                op(f'{label}_direct_usd_{PERIOD}', f'US dollars on the records counted in {label}_direct_rows_{PERIOD}.', ZERO)]
    out += [op(f'general_third_party_rows_{PERIOD}', f'Records counted in general_payment_rows_{PERIOD} paid to a third-party entity or individual on the recipient\'s behalf; direct plus third-party equals the total.', ZERO),
            op(f'general_third_party_usd_{PERIOD}', f'US dollars on the records counted in general_third_party_rows_{PERIOD}.', ZERO),
            op('general_active_years', f'Number of program years of {YEARS[0]}-{YEARS[-1]} (0 to {len(YEARS)}) with at least one General Payment record to this NPI.', ZERO)]
    for prefix in FEATURE_RULES:
        text = RULE_TEXT[prefix]
        if prefix == 'cart_any_slot':
            text = 'that name a chimeric antigen receptor T-cell (CAR-T) product (brand, generic or development name of Yescarta, Tecartus, Kymriah, Breyanzi, Abecma, Carvykti or Aucatzyl) in at least one product-name field, other products allowed on the same record'
        out += spread_columns(prefix, text)
    out.append(op('cart_any_slot_brands', 'CAR-T brands matched on the records counted in cart_any_slot_rows_2019_2025, sorted and separated by semicolons.', 'No record to this NPI names a CAR-T product.'))
    return out + provenance_columns()


def research_columns():
    return [
        column('npi', 'National Provider Identifier (NPI) from the 2026-10-24 universe table hcp_divisions.csv; every universe NPI has exactly one row.', NEVER, 'project'),
        op('research_data_status', 'published_records when at least one Research Payment record of the Centers for Medicare & Medicaid Services (CMS) Open Payments publication of 2026-06-30 names this NPI in ' + SPAN + ', else no_published_records.'),
        op(f'research_rows_{PERIOD}', f'Number of Research Payment records, {SPAN}, naming this NPI in one of the five principal-investigator fields or as covered recipient; each record counted once per NPI.', ZERO),
        op(f'research_investigator_rows_{PERIOD}', f'Records counted in research_rows_{PERIOD} on which the NPI is a listed principal investigator.', ZERO),
        op(f'research_recipient_only_rows_{PERIOD}', f'Records counted in research_rows_{PERIOD} on which the NPI is the covered recipient without being a listed principal investigator.', ZERO),
        op(f'research_record_usd_{PERIOD}', f'Sum in US dollars of the full record amounts over the records counted in research_rows_{PERIOD}. The whole amount is repeated for every investigator and is mostly paid to institutions: not personal income and not additive across NPIs.', 'No research record names this NPI (research_rows_2019_2025 is 0).'),
        op('research_active_years', f'Number of program years of {YEARS[0]}-{YEARS[-1]} (0 to {len(YEARS)}) with at least one research record naming this NPI.', ZERO),
        op(f'cart_product_research_rows_{PERIOD}', f'Records counted in research_rows_{PERIOD} that name a chimeric antigen receptor T-cell (CAR-T) product (brand, generic or development name of seven approved products) in a product-name field. Study titles are not read, so this is narrower than cart_research_rows_2019_2025 in hcp_research_payment_features.csv of the 2026-11-11 snapshot.', ZERO),
        op(f'cart_product_research_investigator_rows_{PERIOD}', f'Records counted in cart_product_research_rows_{PERIOD} on which the NPI is a listed principal investigator.', ZERO),
        op(f'cart_product_research_recipient_only_rows_{PERIOD}', f'Records counted in cart_product_research_rows_{PERIOD} on which the NPI is only the covered recipient.', ZERO),
        op(f'cart_product_research_record_usd_{PERIOD}', f'Sum in US dollars of the full record amounts over the records counted in cart_product_research_rows_{PERIOD}; not personal income and not additive across NPIs.', 'No research record naming this NPI names a CAR-T product in a product field.'),
        op('cart_product_research_brands', f'CAR-T brands matched in the product-name fields of the records counted in cart_product_research_rows_{PERIOD}, sorted and separated by semicolons.', 'No research record naming this NPI names a CAR-T product in a product field.'),
        op(f'cart_product_or_text_research_rows_{PERIOD}', f'Records counted in research_rows_{PERIOD} that name a CAR-T product in a product field, or whose study name or research context names one or uses generic CAR-T wording (a text heuristic); the rule of cart_research_rows_2019_2025 in hcp_research_payment_features.csv of the 2026-11-11 snapshot.', ZERO),
        op(f'cart_product_or_text_research_record_usd_{PERIOD}', f'Sum in US dollars of the full record amounts over the records counted in cart_product_or_text_research_rows_{PERIOD}; not personal income and not additive across NPIs.', 'No research record naming this NPI matches the product-or-text rule.'),
    ] + provenance_columns()


def division_columns():
    return [
        op('rule', 'Rule selecting the Open Payments General Payment records to universe NPIs that the cell counts: any_slot (a chimeric antigen receptor T-cell, CAR-T, product named in any product field); cart_only (every named product is a CAR-T product); cart_only_named_product (cart_only without development-code records); lymphoma_only (cart_only naming Yescarta, Tecartus, Kymriah or Breyanzi); pure_play_payer (paid by Kite Pharma, Legend Biotech or Autolus); cart_only_or_pure_play_payer (either, each record once).'),
        column('division_group', 'Group of division labels of hcp_divisions.csv (2026-10-24, label version 1.0.0): core; affiliates (affiliate_A, affiliate_B, affiliate_C and center_affiliate_unlinked); referring (referring_proxy); other (hemonc_other).', NEVER, 'project'),
        op('nature_class', 'all, or one project class of the CMS (Centers for Medicare & Medicaid Services) nature of payment: ' + '; '.join(CLASS_TEXT.values()) + '.'),
        column('npis_in_division_group', 'Number of universe NPIs (National Provider Identifiers) in the division group; the denominator for shares.', NEVER, 'project'),
        op('npis_paid', f'Number of NPIs in the group with at least one matching General Payment record, {SPAN}, direct or through a third party.', ZERO),
        op('rows', f'Number of matching General Payment records, {SPAN}; equals direct_rows plus third_party_rows.', ZERO),
        op('usd', 'US dollars on the records counted in rows; equals direct_usd plus third_party_usd.', ZERO),
        op('direct_npis_paid', 'Number of NPIs in the group with at least one matching record paid to the recipient directly (CMS third-party payment indicator No Third Party Payment).', ZERO),
        op('direct_rows', 'Number of matching records paid to the recipient directly.', ZERO),
        op('direct_usd', 'US dollars on the records counted in direct_rows. The headline figures of this snapshot for CAR-T-only services are this column for rule cart_only and nature class services.', ZERO),
        op('direct_median_usd_per_paid_npi', f'Median, over the NPIs counted in direct_npis_paid, of each NPI\'s direct dollars in the cell summed over {SPAN}; half a cent rounds up.', 'No NPI in the cell has a direct payment.'),
        op('third_party_npis_paid', 'Number of NPIs in the group with at least one matching record paid to a third party on the recipient\'s behalf; an NPI can also be counted in direct_npis_paid.', ZERO),
        op('third_party_rows', 'Number of matching records paid to a third party on the recipient\'s behalf.', ZERO),
        op('third_party_usd', 'US dollars on the records counted in third_party_rows; not included in direct_usd.', ZERO),
        column('universe_snapshot_date', 'Snapshot date of the hcp_divisions.csv table whose division labels were used.', NEVER, 'project'),
        column('division_label_version', 'Version of the division labels in hcp_divisions.csv that the groups were built from.', NEVER, 'project'),
    ] + provenance_columns()


def center_columns():
    nature_text = {'royalty_license': 'Royalty or License', 'grant': 'Grant', 'space_rental': 'Space rental or facility fees',
                   'consulting': 'Consulting Fee', 'other_services_compensation': 'compensation for services other than consulting',
                   'education': 'Education'}
    out = [
        column('ccn', 'Centers for Medicare & Medicaid Services (CMS) Certification Number (CCN) of a center in the 2026-10-24 table cart_centers.csv; every one of the 173 centers has exactly one row. Six characters, to be read as text.', NEVER, 'project'),
        op('teaching_hospital_name', f'Teaching hospital name most often written on Open Payments records carrying this CCN, {SPAN} (General records, else Research records); spellings vary between records and can differ from the name in cart_centers.csv.', 'No Open Payments record carries this CCN as teaching hospital.'),
        op('general_data_status', 'published_records when at least one General Payment record of the CMS Open Payments publication of 2026-06-30 carries this CCN as teaching hospital in ' + SPAN + ', else no_published_records. The files do not say whether a center without records is absent from the CMS teaching-hospital list or listed and unpaid.'),
        op(f'general_payment_rows_{PERIOD}', f'Number of General Payment records paid to this teaching hospital (teaching-hospital CCN equals ccn), {SPAN}; all natures, manufacturers and products.', ZERO),
        op(f'general_payments_usd_{PERIOD}', f'US dollars on the records counted in general_payment_rows_{PERIOD}; payments to the institution, not to its physicians.', ZERO),
        op(f'general_payments_usd_{RECENT}', f'Part of general_payments_usd_{PERIOD} on records of program years {YEARS[-2]} and {YEARS[-1]} only, in US dollars.', ZERO),
        op('general_active_years', f'Number of program years of {YEARS[0]}-{YEARS[-1]} (0 to {len(YEARS)}) with at least one General Payment record to this hospital.', ZERO),
    ]
    for part, text in nature_text.items():
        out.append(op(f'{part}_usd_{PERIOD}', f'US dollars, {SPAN}, on General Payment records to this hospital whose CMS nature of payment is {text}.', ZERO))
    out.append(op(f'other_nature_usd_{PERIOD}', f'US dollars, {SPAN}, on General Payment records to this hospital in every other CMS nature; the seven nature columns sum to general_payments_usd_{PERIOD}.', ZERO))
    out += [
        op(f'cart_any_slot_rows_{PERIOD}', f'General Payment records to this hospital, {SPAN}, that name a chimeric antigen receptor T-cell (CAR-T) product (brand, generic or development name of seven approved products) in at least one product-name field.', ZERO),
        op(f'cart_any_slot_usd_{PERIOD}', f'US dollars on the records counted in cart_any_slot_rows_{PERIOD}.', ZERO),
        op(f'cart_only_rows_{PERIOD}', f'General Payment records to this hospital, {SPAN}, on which every named product is a CAR-T product.', ZERO),
        op(f'cart_only_usd_{PERIOD}', f'US dollars on the records counted in cart_only_rows_{PERIOD}.', ZERO),
        op(f'lymphoma_only_rows_{PERIOD}', f'Records counted in cart_only_rows_{PERIOD} that name Yescarta, Tecartus, Kymriah or Breyanzi.', ZERO),
        op(f'lymphoma_only_usd_{PERIOD}', f'US dollars on the records counted in lymphoma_only_rows_{PERIOD}.', ZERO),
        op('research_data_status', 'published_records when at least one Research Payment record of the same publication carries this CCN as teaching hospital in ' + SPAN + ', else no_published_records.'),
        op(f'research_rows_{PERIOD}', f'Number of Research Payment records on which this teaching hospital is the recipient, {SPAN}. Research paid to a university, research institute or other non-covered entity is not included.', ZERO),
        op(f'research_payments_usd_{PERIOD}', f'US dollars on the records counted in research_rows_{PERIOD}; each record counted once.', ZERO),
        op('research_active_years', f'Number of program years of {YEARS[0]}-{YEARS[-1]} (0 to {len(YEARS)}) with at least one Research Payment record to this hospital.', ZERO),
        op(f'cart_product_research_rows_{PERIOD}', f'Records counted in research_rows_{PERIOD} that name a CAR-T product in a product-name field.', ZERO),
        op(f'cart_product_research_usd_{PERIOD}', f'US dollars on the records counted in cart_product_research_rows_{PERIOD}.', ZERO),
        op(f'cart_product_or_text_research_rows_{PERIOD}', f'Records counted in research_rows_{PERIOD} that name a CAR-T product in a product field, or whose study name or research context names one or uses generic CAR-T wording; a text heuristic that also catches trials of unapproved CAR-T products.', ZERO),
        op(f'cart_product_or_text_research_usd_{PERIOD}', f'US dollars on the records counted in cart_product_or_text_research_rows_{PERIOD}.', ZERO),
    ]
    return out + provenance_columns()


COLUMNS = {DETAIL: detail_columns, GENERAL: general_columns, RESEARCH: research_columns, DIVISION: division_columns,
           CENTER: center_columns}
KEYS = {DETAIL: ['record_id'], GENERAL: ['npi'], RESEARCH: ['npi'], DIVISION: ['rule', 'division_group', 'nature_class'],
        CENTER: ['ccn']}


# ---- build -------------------------------------------------------------------------------------------------------

def load_extracts(sources):
    """Row-level records and scan cells of all program years, each cell tagged with its program year."""
    detail = [detail_row(record, sources[year]) for year in YEARS for record in read_csv(WORK / f'general_candidates_{year}.csv')]
    cells = {name: [{**cell, 'program_year': str(year)} for year in YEARS for cell in read_csv(WORK / f'{name}_{year}.csv')]
             for name in CELL_FIELDS}
    return detail, cells


def assemble(people, center_ccns, detail, cells):
    """All five tables from row-level records and scan cells; `people` maps universe NPI to division label."""
    def grouped(rows, key):
        out = defaultdict(list)
        for row in rows:
            out[row[key]].append(row)
        return out
    detail = sorted(detail, key=lambda r: (r['npi'], r['program_year'], r['payment_date'], r['record_id']))
    by_npi, general, research = grouped(detail, 'npi'), grouped(cells['general_people'], 'npi'), grouped(cells['research_people'], 'npi')
    hospital_general, hospital_research = grouped(cells['general_hospitals'], 'ccn'), grouped(cells['research_hospitals'], 'ccn')
    return {
        DETAIL: detail,
        GENERAL: [general_features(npi, general.get(npi, []), by_npi.get(npi, [])) for npi in people],
        RESEARCH: [research_features(npi, research.get(npi, [])) for npi in people],
        DIVISION: division_table(detail, people),
        CENTER: [center_row(ccn, hospital_general.get(ccn, []), hospital_research.get(ccn, [])) for ccn in center_ccns],
    }


def compare_search_tables(tables, staged):
    """Count cells where the rebuild differs from the tables the search produced (half-cent tolerance on dollars)."""
    def differ(mine, theirs):
        return abs(float(mine or 0) - float(theirs or 0)) > 0.005
    def misses(mine, theirs, pairs, absent_is_zero=True):
        count = 0
        for key, row in mine.items():
            other = theirs.get(key)
            if other is None:
                count += any(differ(row[a], 0) for a, _ in pairs) if absent_is_zero else 1
            else:
                count += any(differ(row[a], other[b]) for a, b in pairs)
        return count + len(set(theirs) - set(mine))
    general = {r['npi']: r for r in tables[GENERAL]}
    research = {r['npi']: r for r in tables[RESEARCH]}
    center = {r['ccn']: r for r in tables[CENTER]}
    in_centers = lambda path: {r['ccn']: r for r in read_csv(path) if r['ccn'] in center}
    suffix = f'_{PERIOD}'
    division_map = {'affiliate': 'affiliates', 'hemonc_other': 'other'}
    rule_map = {'payer_name': 'pure_play_payer', 'union_cart_only_or_payer': 'cart_only_or_pure_play_payer'}
    class_map = {'in_kind': 'hospitality_education'}
    theirs = {(rule_map.get(r['rule'], r['rule']), division_map.get(r['division'], r['division']), class_map.get(r['nature_class'], r['nature_class'])): r
              for r in read_csv(staged['division']) if r['scenario'] == 'as_labelled'}
    mine = {(r['rule'], r['division_group'], r['nature_class']): r for r in tables[DIVISION] if (r['rule'], r['division_group'], r['nature_class']) in theirs}
    return {
        'general_summary_rows_differing': misses(general, {r['npi']: r for r in read_csv(staged['general'])}, [
            ('general_payment_rows' + suffix, 'general_rows' + suffix), ('general_payments_usd' + suffix, 'general_usd' + suffix),
            ('services_usd' + suffix, 'services_usd' + suffix), ('hospitality_education_usd' + suffix, 'in_kind_other_usd' + suffix),
            ('other_financial_usd' + suffix, 'other_financial_usd' + suffix), ('cart_any_slot_rows' + suffix, 'cart_linked_rows' + suffix),
            ('cart_any_slot_usd' + suffix, 'cart_linked_usd' + suffix), ('cart_any_slot_services_usd' + suffix, 'cart_linked_services_usd' + suffix)]),
        'tier_summary_rows_differing': misses(general, {r['npi']: r for r in read_csv(staged['tier'])}, [
            ('cart_only_rows' + suffix, 'cart_only_rows'), ('cart_only_usd' + suffix, 'cart_only_usd'),
            ('cart_only_services_usd' + suffix, 'cart_only_services_usd'), ('lymphoma_only_rows' + suffix, 'lymphoma_only_rows'),
            ('lymphoma_only_usd' + suffix, 'lymphoma_only_usd'), ('lymphoma_only_services_usd' + suffix, 'lymphoma_only_services_usd')]),
        'research_summary_rows_differing': misses(research, {r['npi']: r for r in read_csv(staged['research'])}, [
            ('research_rows' + suffix, 'research_records' + suffix), ('research_record_usd' + suffix, 'research_record_usd' + suffix),
            ('cart_product_research_rows' + suffix, 'cart_research_records' + suffix),
            ('cart_product_research_record_usd' + suffix, 'cart_research_record_usd' + suffix)]),
        'center_general_rows_differing': misses(center, in_centers(staged['hospital_general']), [
            ('general_payment_rows' + suffix, 'general_rows' + suffix), ('general_payments_usd' + suffix, 'general_usd' + suffix),
            (f'general_payments_usd_{RECENT}', f'general_usd_{RECENT}'), ('royalty_license_usd' + suffix, 'royalty_license_usd'), ('grant_usd' + suffix, 'grant_usd'),
            ('space_rental_usd' + suffix, 'space_rental_usd'), ('consulting_usd' + suffix, 'consulting_usd'),
            ('education_usd' + suffix, 'education_usd'), ('cart_any_slot_rows' + suffix, 'cart_linked_rows'),
            ('cart_any_slot_usd' + suffix, 'cart_linked_usd')]),
        'center_research_rows_differing': misses(center, in_centers(staged['hospital_research']), [
            ('research_rows' + suffix, 'research_records' + suffix), ('research_payments_usd' + suffix, 'research_usd' + suffix),
            ('cart_product_research_rows' + suffix, 'cart_product_research_records'),
            ('cart_product_research_usd' + suffix, 'cart_product_research_usd'),
            ('cart_product_or_text_research_usd' + suffix, 'cart_product_or_text_research_usd')]),
        'division_cells_compared': len(mine),
        'division_cells_differing': misses(mine, theirs, [
            ('npis_in_division_group', 'npis_in_division'), ('direct_npis_paid', 'direct_npis_paid'), ('direct_rows', 'direct_rows'),
            ('direct_usd', 'direct_usd'), ('direct_median_usd_per_paid_npi', 'direct_median_usd_per_paid_npi'),
            ('third_party_rows', 'third_party_payee_rows'), ('third_party_npis_paid', 'third_party_payee_npis'),
            ('third_party_usd', 'third_party_payee_usd')], absent_is_zero=False),
    }


def build(workers=None):
    people = {npi: row['division'] for npi, row in universe().items()}
    center_ccns = list(centers())
    built_at = now()
    staged = {name: stage(path) for name, path in SEARCH_TABLES.items()}
    jobs = [(kind, year) for kind in ['general', 'research'] for year in sorted(YEARS, reverse=True)]
    with concurrent.futures.ProcessPoolExecutor(workers or min(len(jobs), os.cpu_count() or 4)) as executor:
        scans = list(executor.map(scan, jobs))
    general_scans = {s['program_year']: s for s in scans if s['kind'] == 'general'}
    research_scans = {s['program_year']: s for s in scans if s['kind'] == 'research'}
    earlier = {kind: {s['program_year']: s['rows_scanned'] for s in load(ROOT / 'Data' / 'processed' / CORE_SNAPSHOT / f'{kind}_payments_metadata.json')['scans']}
               for kind in ['general', 'research']}
    assert earlier['general'] == {y: s['rows_scanned'] for y, s in general_scans.items()}, 'General rows scanned differ from the 2026-11-11 scan'
    assert earlier['research'] == {y: s['rows_scanned'] for y, s in research_scans.items()}, 'Research rows scanned differ from the 2026-11-11 scan'
    detail, cells = load_extracts(general_scans)
    tables = assemble(people, center_ccns, detail, cells)
    detail = tables[DETAIL]
    observed_payers = {r['manufacturer'] for r in detail if r['pure_play_payer'] == '1'}
    summaries = [publish(name, tables[name], COLUMNS[name](), KEYS[name]) for name in TABLES]
    numbers = headline(tables, people)
    metas = [load((ARCHIVES / archive_name(year)).with_suffix('.meta.json')) for year in YEARS]
    def hospital_total(part):
        return {'ccns': len({c['ccn'] for c in part}), 'rows': sum(int(c['rows']) for c in part),
                'usd': sum(int(c['cents']) for c in part) / 100}
    write_module_metadata(MODULE, {
        'tables': summaries,
        'sources': [{
            'name': 'CMS Open Payments General Payment and Research Payment detail files, program years 2019-2025',
            'publisher': 'Centers for Medicare & Medicaid Services', 'url': 'https://download.cms.gov/openpayments/',
            'release_or_version': f'publication of {RELEASE_DATE} (file stamp P06302026_06032026)',
            'retrieved': f"{min(m['retrieved_at_utc'] for m in metas)} to {max(m['retrieved_at_utc'] for m in metas)}",
            'how_obtained': 'Seven program-year archives downloaded with curl for the 2026-11-11 snapshot and read from Data/raw/2026-11-11/open_payments_bulk/; no new request was made.',
            'terms_class': 'a (United States Government work, public domain; attribution to CMS Open Payments)',
            'archives': [{k: m[k] for k in ['program_year', 'source_url', 'retrieved_at_utc', 'http_last_modified', 'sha256', 'bytes']} for m in metas],
        }, {
            'name': 'HCP division universe and CAR-T center set', 'publisher': 'this project',
            'url': 'Data/processed/2026-10-24/hcp_divisions.csv; Data/processed/2026-10-24/cart_centers.csv',
            'release_or_version': f'snapshot {UNIVERSE_SNAPSHOT}, label version {LABEL_VERSION}', 'retrieved': 'not applicable (repository tables)',
            'how_obtained': 'Read from the repository; NPIs and CCNs are matched to Open Payments by exact equality.',
            'terms_class': 'project-derived', 'sha256': {'hcp_divisions.csv': sha(UNIVERSE), 'cart_centers.csv': sha(CENTERS)},
        }],
        'headline': numbers,
        'fingerprints': {name: fingerprint(read_csv(OUT / f'{name}.csv'), KEYS[name]) for name in TABLES},
        'scans': [{k: s.get(k) for k in ['kind', 'program_year', 'member', 'member_bytes', 'member_crc32', 'columns', 'rows_scanned',
                                         'universe_rows', 'records_naming_universe_npi', 'candidate_rows', 'teaching_hospital_rows',
                                         'teaching_hospital_rows_with_npi', 'amounts_not_in_whole_cents']} for s in scans],
        'rows_scanned': {'general': sum(s['rows_scanned'] for s in general_scans.values()),
                         'research': sum(s['rows_scanned'] for s in research_scans.values()),
                         'equal_to_2026_11_11_scans': True},
        'scan_context': {
            'note': 'Counts read from the scan extracts, not from the published tables.',
            'all_teaching_hospitals_general': hospital_total(cells['general_hospitals']),
            'all_teaching_hospitals_research': hospital_total(cells['research_hospitals']),
            'all_teaching_hospitals_general_cart_any_slot': hospital_total([c for c in cells['general_hospitals'] if c['any_slot'] == '1']),
            'all_teaching_hospitals_research_cart_product': hospital_total([c for c in cells['research_hospitals'] if c['cart_product_mentioned'] == '1']),
            'teaching_hospital_ccns_in_either_file': len({c['ccn'] for c in cells['general_hospitals'] + cells['research_hospitals']}),
            'whole_file_cart_product_research_records_by_recipient_type': {
                kind: {'rows': sum(s['cart_product_records_by_recipient_type'].get(kind, {}).get('rows', 0) for s in research_scans.values()),
                       'usd': round(sum(s['cart_product_records_by_recipient_type'].get(kind, {}).get('usd', 0) for s in research_scans.values()), 2)}
                for kind in sorted({k for s in research_scans.values() for k in s['cart_product_records_by_recipient_type']})},
        },
        'rules': {'cart_product_patterns': CART_PRODUCTS, 'brand_and_generic_pattern': NAMED_CART.pattern,
                  'pure_play_payer_pattern': PURE_PLAY.pattern, 'pure_play_payers_observed': sorted(observed_payers),
                  'direct_payment': f"Third_Party_Payment_Recipient_Indicator equals '{DIRECT}'",
                  'third_party_indicator_values_in_row_level_table': dict(Counter(r['third_party_payment_indicator'] for r in detail)),
                  'nature_class': {nature: nature_class(nature) for nature in sorted({r['payment_nature'] for r in detail})},
                  'division_groups': DIVISION_GROUPS, 'match_rule': 'Universe NPI equals covered_recipient_npi (General) or a principal-investigator NPI or covered_recipient_npi (Research); center CCN equals teaching_hospital_ccn; exact string equality.'},
        'comparison_with_search_tables': compare_search_tables(tables, staged),
        'rows_removed': {'count': 0, 'reason': 'No record is dropped: the row-level table holds every General record to a universe NPI that names a CAR-T product or is paid by a pure-play payer; all other records are counted in the per-NPI table.'},
        'columns_not_published': {
            'contextual_information': 'Free text written by manufacturers, mostly internal reference codes on these records; it cannot be screened row by row for names of people without an NPI.',
            'third_party_recipient': 'Name of the third-party payee; a payee can be a practice or company named after a person. The CMS indicator and the direct_payment flag are published instead.',
            'names_city_state_specialty_division': 'Attributes of universe NPIs are read by joining hcp_divisions.csv on npi.',
            'manufacturer_groups_and_dominant_nature': 'The search tables carried project company groups with embedded dollar amounts and a dominant-nature label; the row-level table gives the CMS manufacturer name and nature of every record instead.',
        },
        'limitations': LIMITATIONS, 'defects_fixed': DEFECTS_FIXED, 'defects_open': DEFECTS_OPEN,
    }, list(staged.values()) + [ARCHIVES / archive_name(year) for year in YEARS]
       + [(ARCHIVES / archive_name(year)).with_suffix('.meta.json') for year in YEARS])
    errors = check(OUT.parent)
    print(MODULE.upper(), 'BUILT', {s['table']: s['rows'] for s in summaries}, 'CHECK ERRORS', sum(errors.values()), 'AT', built_at, flush=True)
    return summaries


LIMITATIONS = [
    'Open Payments records are transfers of value reported and attested by manufacturers; CMS does not verify them. They are not payer reimbursement, product volume, discounts, or evidence of Medical Science Liaison contact.',
    'Every CAR-T figure is a floor. Manufacturers often leave the product fields blank: Kite Pharma, Inc. on about four in ten of its records to universe NPIs and Autolus Inc on all of them. The pure_play_payer rule recovers those records but cannot see CAR-T payments by Bristol Myers Squibb, Novartis or Janssen that name no product.',
    'The any_slot rule counts records that name a CAR-T product beside other products, mostly Janssen records listing Carvykti with a non-CAR-T product in 2022-2025. Use cart_only or lymphoma_only for CAR-T totals.',
    'lymphoma_only is a product rule (Yescarta, Tecartus, Kymriah or Breyanzi named), not an indication rule; these products are also used for leukemia and other diseases.',
    'Records matched only by a development code (JCAR017, BB-2121), all in program year 2019, are payments made before approval; they carry development_name_only = 1 and the division table gives the rule cart_only_named_product without them.',
    'Direct figures exclude payments routed to a third party on the recipient\'s behalf; the third-party columns hold them. The headline figures for CAR-T-only services use direct payments.',
    'Division labels are proxies. Some referring_proxy NPIs practise at treating sites, so referring totals include treating-site physicians.',
    'Research amounts are whole-record amounts, mostly paid to institutions and repeated for every investigator on a record; they are not personal income and must not be summed across NPIs. CMS lets manufacturers delay publication of research payments for up to four years, so recent years are a floor.',
    'The center table counts only payments made to the hospital as a covered teaching hospital under the exact CCN of cart_centers.csv. Research routed to a university, research institute or cancer-center corporation is a non-covered-entity record and is not in it; a center registered with CMS under a sibling CCN looks unpaid.',
    'Teaching-hospital General totals are dominated by royalty and licence payments at a few hospitals.',
    'Zero is an observed count for the 2026-06-30 publication only; CMS revises records at each refresh.',
    'A CAR-T flag asserts a financial relationship about an identified person under this project\'s own product and payer rules; an error in a rule or in a manufacturer\'s product field carries through.',
    'Kite Pharma, Inc. alone pays about two thirds of the cart_only services dollars (see cart_only_services_usd_by_manufacturer in the headline); Bristol Myers Squibb and Novartis name the product less often, so they are under-counted in the product rules and, on research records, their trial sites are largely invisible.',
    'lymphoma_only is not the lymphoma_cart_product_mentioned flag of general_payments_2019_2025.csv (2026-11-11): it also counts the development code JCAR017 and it leaves out records that name another product beside the CAR-T product.',
    'nature_class services is not advisory_usd of the 2026-11-11 tables; the two groupings differ and must not be compared.',
    'Matching is by exact NPI and CCN. Records filed under the recipient type Non-Physician Practitioner are counted like physician records; investigators listed on a research record without an NPI cannot be matched.',
    'All-records dollar totals are dominated by a few very large records (acquisitions, royalties); prefer the nature-class columns to general_payments_usd_2019_2025 when comparing people.',
    'In the division table, medians for nature class all are driven by meals; compare the services medians across division groups.',
    'Program year 2020 is low in the whole Open Payments file, not only for this universe.',
]
DEFECTS_FIXED = [
    'Row counts written as floats in the search tables are integers here.',
    'The search tables folded third-party-payee records into every dollar column; every rule now has direct and third-party columns, and the headline of the search report (direct CAR-T-only services) is reproduced from the row-level table.',
    'The per-NPI CAR-T table was not zero-filled; all 17,510 universe NPIs now have a row with general_data_status.',
    'The general summary and the tier summary are merged into one per-NPI table with one copy of the any_slot columns, named for the rule (cart_any_slot_*).',
    'cart_mfr_any_product_* (any product of seven large company groups, not CAR-T-specific) is dropped and replaced with pure_play_payer_* columns read from the paying-entity name.',
    'in_kind / in_kind_other is renamed hospitality_education in every table; it is a nature-of-payment class, not the CMS form of payment.',
    'Development-code records are flagged (development_name_only) and the division table has a rule without them.',
    'Manufacturer strings with embedded rounded dollars are dropped; the row-level table gives the CMS manufacturer name per record.',
    'The research table is zero-filled with research_data_status, separates investigator from recipient-only records, and names the product-field columns cart_product_* so they cannot be confused with the product-or-text columns of the 2026-11-11 table; the product-or-text variant is published beside them.',
    'The teaching-hospital table covers all 173 centers with status columns, adds cart_only and lymphoma_only values rebuilt from the records, rebuilds space rental from the records (the companion file of the search had 0 on every row), adds the compensation-for-services nature and a remainder column so the nature columns sum to the total, and adds a record count for the product-or-text research rule.',
    'The division table is generated from the row-level table and holds the as-labelled scenario only; the two leakage scenarios of the search, which depend on Kite, Bristol Myers Squibb and BMT InfoNet pages, are not built.',
    'Identity copies (names, city, state, credentials, specialty, division) and center attribute copies are not repeated; join hcp_divisions.csv on npi or cart_centers.csv on ccn.',
]
DEFECTS_OPEN = [
    'Blank product fields cannot be repaired: product-rule totals remain floors and payers that seldom name the product (Bristol Myers Squibb, Novartis) are under-counted.',
    'For centers with no_published_records the files cannot tell a hospital absent from the CMS teaching-hospital list from one listed and unpaid, or from one registered under a sibling CCN.',
    'teaching_hospital_name is one of several spellings per CCN (the most frequent one).',
    'The pure_play_payer rule also counts the few Kite Pharma records that name a non-CAR-T product.',
    'No NPI x program-year grid is published; per-year values can be rebuilt from the row-level table for CAR-T rules only.',
    'any_slot still counts records that name a CAR-T product beside another product (mostly Janssen records naming Carvykti); they are flagged in the row-level table by any_slot = 1 with cart_only = 0, and the cart_only and lymphoma_only columns leave them out.',
    'Development-code records of 2019 (JCAR017, BB-2121) stay in cart_only and lymphoma_only; they are flagged by development_name_only and removed only in the division rule cart_only_named_product.',
    'Research dollar sums repeat the whole record amount for every investigator and cannot be added across NPIs; no record-level research table is published for the universe.',
    'The division group affiliates pools four labels (affiliate_A, affiliate_B, affiliate_C, center_affiliate_unlinked), and division labels are proxies: some referring_proxy NPIs practise at treating sites. No relabelled scenario is published.',
    'Third-party payee names and the manufacturer free-text context are not published; they stay in the local scan extracts.',
    'Open Payments reuse terms are recorded in the module metadata as public domain with attribution; the access policy asks automated clients for a contact in the User-Agent, which matters for a future refresh, not for this build (no request was made).',
]


# ---- validation --------------------------------------------------------------------------------------------------

def close(a, b, tolerance=0.005):
    return abs(float(a or 0) - float(b or 0)) <= tolerance


def check(processed):
    """Error counts for the published tables, read only from a Data/processed directory; zero means pass."""
    name = lambda label: f'{MODULE}.{label}'
    here = processed / SNAPSHOT
    needed = {table: here / f'{table}.csv' for table in TABLES}
    needed.update({'universe': processed / UNIVERSE_SNAPSHOT / 'hcp_divisions.csv', 'centers': processed / UNIVERSE_SNAPSHOT / 'cart_centers.csv',
                   'core_general': processed / CORE_SNAPSHOT / 'hcp_general_payment_features.csv',
                   'core_general_rows': processed / CORE_SNAPSHOT / 'general_payments_2019_2025.csv',
                   'core_research': processed / CORE_SNAPSHOT / 'hcp_research_payment_features.csv',
                   'core_research_rows': processed / CORE_SNAPSHOT / 'research_payments_2019_2025.csv'})
    out = {name('files_present'): sum(not path.is_file() for path in needed.values())}
    if out[name('files_present')]:
        return out
    data = {key: read_csv(path) for key, path in needed.items()}
    detail, general, research, division, center = (data[table] for table in TABLES)
    divisions = {r['npi']: r['division'] for r in data['universe']}
    P = PERIOD

    # Published columns are exactly the declared public columns.
    out[name('columns_as_declared')] = sum(list(data[table][0]) != [c['column'] for c in COLUMNS[table]() if not c['restricted']]
                                           for table in TABLES if data[table])
    out[name('tables_not_empty')] = sum(not data[table] for table in TABLES)
    if out[name('tables_not_empty')] or out[name('columns_as_declared')]:
        return out
    # A cell is blank only where the dictionary gives blank a meaning. Dollars are written with two decimals, counts as
    # whole numbers, flags as 0 or 1, dates as YYYY-MM-DD, NPIs as ten digits and program years as 2019 to 2025.
    def pattern(column):
        if MONEY.search(column):
            return r'\d+\.\d{2}'
        if COUNT.search(column):
            return r'\d+'
        if column in FLAGS:
            return '[01]'
        if column in ISO_DATES:
            return r'20\d\d-[01]\d-[0-3]\d'
        return {'npi': r'\d{10}', 'program_year': '20(19|2[0-5])', 'record_id': r'\d+'}.get(column)
    def malformed(table):
        errors = 0
        for spec in COLUMNS[table]():
            shape_of, required = pattern(spec['column']), spec['missing_value'] in (NEVER, ZERO)
            for r in data[table]:
                value = r[spec['column']]
                errors += (not value.strip() and required) or value != value.strip() or bool(value and shape_of and not re.fullmatch(shape_of, value))
        return errors
    out[name('cells_well_formed')] = sum(malformed(table) for table in TABLES)
    if out[name('cells_well_formed')]:
        return out

    # Row-level table: key, scope and flags recomputed from the published fields.
    ids = [r['record_id'] for r in detail]
    out[name('detail_key')] = len(ids) - len(set(ids)) + sum(not i for i in ids) + sum(r['npi'] not in divisions for r in detail)
    out[name('detail_scope')] = sum(r['any_slot'] != '1' and r['pure_play_payer'] != '1' for r in detail)
    def flags_wrong(r):
        expected = {k: str(v) for k, v in product_flags(r['products'].split(';')).items()}
        expected.update({'nature_class': nature_class(r['payment_nature']), 'pure_play_payer': str(pure_play(r['manufacturer'])),
                         'direct_payment': str(int(r['third_party_payment_indicator'] == DIRECT))})
        return any(r[k] != v for k, v in expected.items())
    out[name('detail_flags_recomputed')] = sum(flags_wrong(r) for r in detail)
    out[name('detail_rules_nested')] = sum(not (r['any_slot'] >= r['cart_only'] >= r['lymphoma_only']) or r['development_name_only'] > r['any_slot']
                                           or float(r['amount_usd']) <= 0 or not re.fullmatch(r'20(19|2[0-5])', r['program_year'])
                                           or (r['any_slot'] == '1' and r['nature_class'] == 'other_financial') for r in detail)
    # Fields that restate one another: the program year in the payment date and file name, the 2026-11-11 nature grouping,
    # one CMS profile per NPI, and the values the dictionary says occur.
    profiles = defaultdict(set)
    for r in detail:
        profiles[r['npi']].add(r['cms_profile_id'])
    out[name('detail_fields_consistent')] = sum(
        (r['payment_date'][:4] != r['program_year']) + (f"OP_DTL_GNRL_PGYR{r['program_year']}_" not in r['source_file'])
        + (r['nature_group'] != nature_group(r['payment_nature'])) + (r['source_release_date'] != RELEASE_DATE)
        + (r['third_party_payment_indicator'] not in (DIRECT, 'Entity', 'Individual'))
        + (r['covered_recipient_type'] not in ('Covered Recipient Physician', 'Covered Recipient Non-Physician Practitioner'))
        + (int(r['number_of_payments']) < 1) for r in detail) + sum(len(found) != 1 for found in profiles.values())

    # Per-NPI General table: coverage, arithmetic, and equality with the row-level table.
    npis = [r['npi'] for r in general]
    out[name('general_universe_coverage')] = len(set(npis) ^ set(divisions)) + len(npis) - len(set(npis))
    def general_arithmetic(r):
        n, f = (lambda c: int(r[c])), (lambda c: float(r[c]))
        errors = n(f'general_payment_rows_{P}') != sum(n(f'{c}_rows_{P}') for c in CLASSES)
        errors += not close(f(f'general_payments_usd_{P}'), sum(f(f'{c}_usd_{P}') for c in CLASSES), 0.011)
        errors += n(f'general_direct_rows_{P}') != sum(n(f'{c}_direct_rows_{P}') for c in CLASSES)
        errors += not close(f(f'general_direct_usd_{P}'), sum(f(f'{c}_direct_usd_{P}') for c in CLASSES), 0.011)
        errors += any(n(f'{c}_direct_rows_{P}') > n(f'{c}_rows_{P}') or f(f'{c}_direct_usd_{P}') > f(f'{c}_usd_{P}') + 0.005 for c in CLASSES)
        errors += (r['general_data_status'] == 'published_records') != (n(f'general_payment_rows_{P}') > 0)
        errors += r['general_data_status'] not in STATUSES
        errors += (n(f'general_payment_rows_{P}') > 0) != (n('general_active_years') > 0) or n('general_active_years') > len(YEARS)
        return int(errors)
    out[name('general_class_sums')] = sum(general_arithmetic(r) for r in general)
    # In this publication a record count is zero exactly when its dollar sum is zero, for every pair of columns.
    pairs = [(f'general_payment_rows_{P}', f'general_payments_usd_{P}')]
    pairs += [(f'{stem}_rows_{P}', f'{stem}_usd_{P}') for stem in
              ['general_direct', 'general_third_party'] + CLASSES + [f'{c}_direct' for c in CLASSES]
              + [f'{prefix}{part}' for prefix in FEATURE_RULES for part in ('', '_direct', '_third_party')]]
    out[name('general_rows_and_dollars_agree')] = sum((int(r[rows]) > 0) != (float(r[dollars]) > 0) for r in general for rows, dollars in pairs)
    def direct_plus_third(r, prefix, total_rows, total_usd):
        return (int(r[total_rows]) != int(r[f'{prefix}_direct_rows_{P}']) + int(r[f'{prefix}_third_party_rows_{P}'])
                or not close(r[total_usd], float(r[f'{prefix}_direct_usd_{P}']) + float(r[f'{prefix}_third_party_usd_{P}']), 0.011))
    out[name('general_direct_plus_third_party')] = sum(
        direct_plus_third(r, 'general', f'general_payment_rows_{P}', f'general_payments_usd_{P}')
        + sum(direct_plus_third(r, prefix, f'{prefix}_rows_{P}', f'{prefix}_usd_{P}') for prefix in FEATURE_RULES) for r in general)
    def nested(r):
        errors = 0
        for part in ['rows', 'usd', 'services_usd', 'direct_rows', 'direct_usd', 'direct_services_usd']:
            a, b, c, d, e = (float(r[f'{prefix}_{part}_{P}']) for prefix in ['cart_any_slot', 'cart_only', 'lymphoma_only', 'pure_play_payer', 'cart_only_or_pure_play'])
            errors += not (a + 0.005 >= b >= c - 0.005) or e + 0.005 < max(b, d) or e > b + d + 0.005
        for prefix in FEATURE_RULES:
            errors += float(r[f'{prefix}_services_usd_{P}']) > float(r[f'{prefix}_usd_{P}']) + 0.005
            errors += int(r[f'{prefix}_rows_{P}']) > int(r[f'general_payment_rows_{P}']) or float(r[f'{prefix}_usd_{P}']) > float(r[f'general_payments_usd_{P}']) + 0.005
            errors += float(r[f'{prefix}_services_usd_{P}']) > float(r[f'services_usd_{P}']) + 0.005
        return errors
    out[name('general_rules_nested')] = sum(nested(r) for r in general)
    by_npi = defaultdict(list)
    for r in detail:
        by_npi[r['npi']].append(r)
    def from_detail(r):
        rows = by_npi.get(r['npi'], [])
        expected = {'cart_any_slot_brands': ';'.join(sorted({b for x in rows for b in x['cart_products'].split(';') if b}))}
        for prefix, rule in FEATURE_RULES.items():
            expected.update(spread([x for x in rows if RULES[rule](x)], prefix))
        return sum(str(v) != r[k] for k, v in expected.items())
    out[name('general_equals_row_level_table')] = sum(from_detail(r) for r in general)

    # Core NPIs against the 2026-11-11 tables.
    mine = {r['npi']: r for r in general}
    def core_general(old):
        new = mine.get(old['npi'])
        if new is None:
            return 1
        return int(new[f'general_payment_rows_{P}'] != old['general_payment_rows_2019_2025']
                   or not close(new[f'general_payments_usd_{P}'], old['general_payments_usd_2019_2025'])
                   or new[f'cart_any_slot_rows_{P}'] != old['cart_product_rows_2019_2025']
                   or not close(new[f'cart_any_slot_usd_{P}'], old['cart_product_usd_2019_2025'])
                   or new['general_active_years'] != old['general_active_years']
                   or new['cart_any_slot_active_years'] != old['cart_active_years']
                   or set(filter(None, new['cart_any_slot_brands'].split(';'))) != set(filter(None, old['cart_products_2019_2025'].split(';')))
                   or new['general_data_status'] != old['general_data_status'])
    out[name('core_general_equals_2026_11_11')] = sum(core_general(old) for old in data['core_general']) + (len(data['core_general']) != 500)
    # Nature classes of the core NPIs recomputed from every core record of the 2026-11-11 row-level table. That table
    # names third-party entities only, so its count is a lower bound for the third-party rows here (Individual has no name).
    old_cells = defaultdict(Counter)
    for r in data['core_general_rows']:
        label = nature_class(r['payment_nature'])
        old_cells[r['npi']].update({f'{label}_rows': 1, f'{label}_cents': to_cents(r['amount_usd']), 'named_third_party': bool(r['third_party_recipient'])})
    def core_classes(old):
        new, cells = mine.get(old['npi']), old_cells[old['npi']]
        if new is None:
            return 1
        return int(any(int(new[f'{c}_rows_{P}']) != cells[f'{c}_rows'] or to_cents(new[f'{c}_usd_{P}']) != cells[f'{c}_cents'] for c in CLASSES)
                   or int(new[f'general_third_party_rows_{P}']) < cells['named_third_party'])
    out[name('core_classes_equal_2026_11_11')] = sum(core_classes(old) for old in data['core_general'])
    core = {r['npi'] for r in data['core_general']}
    out[name('core_is_core_division')] = len(core ^ {npi for npi, label in divisions.items() if label == 'core'})
    old_rows = {r['record_id']: r for r in data['core_general_rows'] if r['cart_product_mentioned'] == '1' or pure_play(r['manufacturer'])}
    new_rows = {r['record_id']: r for r in detail if r['npi'] in core}
    out[name('core_row_level_equals_2026_11_11')] = len(set(old_rows) ^ set(new_rows)) + sum(
        new_rows[k]['npi'] != old_rows[k]['npi'] or not close(new_rows[k]['amount_usd'], old_rows[k]['amount_usd'])
        or new_rows[k]['any_slot'] != old_rows[k]['cart_product_mentioned'] or new_rows[k]['products'] != old_rows[k]['products']
        or int(new_rows[k]['direct_payment'] == '1') == int(bool(old_rows[k]['third_party_recipient'])) for k in set(old_rows) & set(new_rows))

    # Per-NPI Research table.
    npis = [r['npi'] for r in research]
    out[name('research_universe_coverage')] = len(set(npis) ^ set(divisions)) + len(npis) - len(set(npis))
    def research_arithmetic(r):
        n = lambda c: int(r[c])
        rows, product, either = n(f'research_rows_{P}'), n(f'cart_product_research_rows_{P}'), n(f'cart_product_or_text_research_rows_{P}')
        errors = rows != n(f'research_investigator_rows_{P}') + n(f'research_recipient_only_rows_{P}')
        errors += product != n(f'cart_product_research_investigator_rows_{P}') + n(f'cart_product_research_recipient_only_rows_{P}')
        errors += not (rows >= either >= product) or n(f'cart_product_research_investigator_rows_{P}') > n(f'research_investigator_rows_{P}')
        errors += (r['research_data_status'] == 'published_records') != (rows > 0) or (rows > 0) != (n('research_active_years') > 0)
        errors += r['research_data_status'] not in STATUSES or n('research_active_years') > len(YEARS)
        errors += (rows > 0) != bool(r[f'research_record_usd_{P}']) or (product > 0) != bool(r[f'cart_product_research_record_usd_{P}'])
        errors += (product > 0) != bool(r['cart_product_research_brands']) or (either > 0) != bool(r[f'cart_product_or_text_research_record_usd_{P}'])
        errors += float(r[f'cart_product_research_record_usd_{P}'] or 0) > float(r[f'cart_product_or_text_research_record_usd_{P}'] or 0) + 0.005
        errors += float(r[f'cart_product_or_text_research_record_usd_{P}'] or 0) > float(r[f'research_record_usd_{P}'] or 0) + 0.005
        return int(errors)
    out[name('research_arithmetic')] = sum(research_arithmetic(r) for r in research)
    mine = {r['npi']: r for r in research}
    roles = defaultdict(Counter)
    for r in data['core_research_rows']:
        roles[r['npi']]['investigator' if r['pi_slots'] else 'recipient_only'] += 1
        roles[r['npi']]['product'] += r['cart_product_mentioned'] == '1'
        roles[r['npi']]['product_cents'] += to_cents(r['amount_usd']) * (r['cart_product_mentioned'] == '1')
    def core_research(old):
        new = mine.get(old['npi'])
        if new is None:
            return 1
        role = roles[old['npi']]
        return int(new[f'research_rows_{P}'] != old['research_rows_2019_2025']
                   or bool(new[f'research_record_usd_{P}']) != bool(old['research_record_usd_2019_2025'])
                   or not close(new[f'research_record_usd_{P}'], old['research_record_usd_2019_2025'])
                   or new['research_active_years'] != old['research_active_years']
                   or new['research_data_status'] != old['research_data_status']
                   or new[f'cart_product_or_text_research_rows_{P}'] != old['cart_research_rows_2019_2025']
                   or not close(new[f'cart_product_or_text_research_record_usd_{P}'], old['cart_research_record_usd_2019_2025'])
                   or int(new[f'research_investigator_rows_{P}']) != role['investigator']
                   or int(new[f'research_recipient_only_rows_{P}']) != role['recipient_only']
                   or int(new[f'cart_product_research_rows_{P}']) != role['product']
                   or not close(new[f'cart_product_research_record_usd_{P}'], role['product_cents'] / 100))
    out[name('core_research_equals_2026_11_11')] = sum(core_research(old) for old in data['core_research']) + (len(data['core_research']) != 500)

    # Division table: recomputed from the row-level table and the universe labels.
    known = [r for r in detail if r['npi'] in divisions]
    expected = {(r['rule'], r['division_group'], r['nature_class']): r for r in division_table(known, divisions)}
    found = {(r['rule'], r['division_group'], r['nature_class']): r for r in division}
    out[name('division_equals_row_level_table')] = (len(set(expected) ^ set(found)) + len(division) - len(found)
                                                    + sum(found[k] != expected[k] for k in set(expected) & set(found)))
    def division_arithmetic(r):
        errors = int(r['rows']) != int(r['direct_rows']) + int(r['third_party_rows'])
        errors += not close(r['usd'], float(r['direct_usd']) + float(r['third_party_usd']), 0.011)
        errors += int(r['npis_paid']) > int(r['npis_in_division_group']) or int(r['npis_paid']) < max(int(r['direct_npis_paid']), int(r['third_party_npis_paid']))
        if r['nature_class'] == 'all':
            parts = [found.get((r['rule'], r['division_group'], c)) for c in CLASSES]
            errors += any(p is None for p in parts) or any(
                not close(r[c], sum(float(p[c]) for p in parts if p), 0.011) for c in ['rows', 'usd', 'direct_rows', 'direct_usd', 'third_party_rows', 'third_party_usd'])
        return int(errors)
    out[name('division_direct_plus_third_party')] = sum(division_arithmetic(r) for r in division)
    sizes = Counter(DIVISION_GROUPS.get(label, 'unknown') for label in divisions.values())
    out[name('division_group_sizes')] = (sum(int(r['npis_in_division_group']) != sizes[r['division_group']] for r in division) + ('unknown' in sizes)
                                         + sum(r['label_version'] != LABEL_VERSION for r in data['universe'])
                                         + sum(r['division_label_version'] != LABEL_VERSION or r['universe_snapshot_date'] != UNIVERSE_SNAPSHOT for r in division))
    out[name('report_headline_direct_cart_only_services')] = sum(
        (row := found.get(('cart_only', group, 'services'))) is None or int(row['direct_npis_paid']) != people
        or abs(float(row['direct_usd']) - dollars) > 1 for group, (people, dollars) in REPORT_HEADLINE.items())

    # Center table.
    ccns = [r['ccn'] for r in center]
    out[name('center_key')] = (len(set(ccns) ^ {r['ccn'] for r in data['centers']}) + len(ccns) - len(set(ccns))
                               + sum(not re.fullmatch(r'\d{6}', c) for c in ccns))
    def center_arithmetic(r):
        n, f = (lambda c: int(r[c])), (lambda c: float(r[c]))
        errors = not close(f(f'general_payments_usd_{P}'), sum(f(f'{part}_usd_{P}') for part in HOSPITAL_PARTS), 0.011)
        errors += not (n(f'general_payment_rows_{P}') >= n(f'cart_any_slot_rows_{P}') >= n(f'cart_only_rows_{P}') >= n(f'lymphoma_only_rows_{P}'))
        errors += not (f(f'general_payments_usd_{P}') + 0.005 >= f(f'cart_any_slot_usd_{P}') >= f(f'cart_only_usd_{P}') - 0.005 >= f(f'lymphoma_only_usd_{P}') - 0.01)
        errors += not (n(f'research_rows_{P}') >= n(f'cart_product_or_text_research_rows_{P}') >= n(f'cart_product_research_rows_{P}'))
        errors += not (f(f'research_payments_usd_{P}') + 0.005 >= f(f'cart_product_or_text_research_usd_{P}') >= f(f'cart_product_research_usd_{P}') - 0.005)
        errors += (r['general_data_status'] == 'published_records') != (n(f'general_payment_rows_{P}') > 0) or (n(f'general_payment_rows_{P}') > 0) != (n('general_active_years') > 0)
        errors += (r['research_data_status'] == 'published_records') != (n(f'research_rows_{P}') > 0) or (n(f'research_rows_{P}') > 0) != (n('research_active_years') > 0)
        errors += bool(r['teaching_hospital_name']) != (n(f'general_payment_rows_{P}') + n(f'research_rows_{P}') > 0)
        errors += f(f'general_payments_usd_{RECENT}') > f(f'general_payments_usd_{P}') + 0.005 or (f(f'general_payments_usd_{RECENT}') > 0 and n('general_active_years') == 0)
        errors += r['general_data_status'] not in STATUSES or r['research_data_status'] not in STATUSES
        errors += max(n('general_active_years'), n('research_active_years')) > len(YEARS)
        errors += (n(f'general_payment_rows_{P}') > 0) != (f(f'general_payments_usd_{P}') > 0) or (n(f'research_rows_{P}') > 0) != (f(f'research_payments_usd_{P}') > 0)
        return int(errors)
    out[name('center_arithmetic')] = sum(center_arithmetic(r) for r in center)

    # Headline totals of the 2026-06-30 publication.
    numbers = headline({table: data[table] for table in TABLES})
    out[name('headline_totals')] = sum(not close(numbers[key], value) for key, value in EXPECTED.items())
    # Every cell against the fingerprints recorded at build time.
    out[name('table_fingerprints')] = sum(fingerprint(data[table], KEYS[table]) != FINGERPRINTS[table] for table in TABLES)
    return out


if __name__ == '__main__':
    build()
