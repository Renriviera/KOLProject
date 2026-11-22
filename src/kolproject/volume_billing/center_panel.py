"""One table of CAR-T treating sites with the volume measures each public source shows for them.

The search table has 267 sites. The tracked `cart_site_volume_panel` keeps the 240 of them that a public
source supports: the 173 hospitals of the 2026-10-24 CAR-T center set, 45 other hospitals and 22 sites
without a CMS Certification Number (physician practices, one federal hospital, Canadian centers) that are
on the BMT InfoNet directory or in a state discharge file. All 267 rows are rebuilt from the rawest inputs
the 2026-11-19 search saved (matched list records, Medicare MS-DRG 018 responses, New York discharge rows,
the California extract, hospital price-file rows) and compared cell by cell with the table the search
produced before the split.

Publication rule: nothing derived from the two manufacturer center locators is written to the tracked
table. That covers their columns, values computed with them, and the 27 rows that are in the search table
only because a locator lists the site; those rows go whole to a local file. BMT InfoNet yearly counts are
published as a 2024 size band only. Hand notes that mention the manufacturer lists are read from the staged
search table, never typed here, because this file is public as well.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict

from .base import (INTERIM, OUT, RAW, RESTRICTED, RESTRICTED_SUFFIX, ROOT, SNAPSHOT, UNIVERSE_SNAPSHOT, centers, column,
                   dump, load, money, now, publish, read_csv, stage, write_csv, write_module_metadata)

MODULE = 'center_panel'
TABLE = 'cart_site_volume_panel'
TABLES = [TABLE]
# Rows whose only basis is a manufacturer locator: every column, local only.
HELD_ROWS_FILE = f'{TABLE}_manufacturer_list_only{RESTRICTED_SUFFIX}.csv'
FOLDER = 'gap-center-set-reconciliation-allpayer-panel'
SEARCH_PANEL = f'{FOLDER}/all_payer_center_panel.csv'
# Saved from the search session's scratch folder on 2026-11-19; the notes folder holds no copy.
MATCHED = f'{FOLDER}/session_intermediates/matched.json'
PRESERVED = [f'{FOLDER}/session_intermediates/{name}' for name in ('lists.json', 'manual.json', 'aux.json', 'dac_hits.csv')]
DRG_RESPONSE = 'cms-hospital-public/inpatient_by_provider_service/drg018_{year}.json'
SPARCS_ROWS = 'state-discharge-apcd/sparcs_{year}_aprdrg011_rows.json'
HCAI_EXTRACT = 'state-discharge-apcd/ca_hcai_top25_msdrg_by_hospital_drg018.txt'
PRICE_ROWS = 'price-transparency/allowed_amount_counts_all_hospitals.csv'
PRICE_ROWS_MGB = {'220071': 'price-transparency/verify/mgh_cart_rows_verify.txt',
                  '220110': 'price-transparency/verify/bwh_cart_rows_verify.txt'}
RESEARCH = 'local-industry-payments-by-division/teaching_hospital_research_payments_x_centers.csv'
ENROLLMENT = ROOT / 'Data' / 'raw' / '2026-10-24' / 'bulk' / 'Hospital_Enrollments_2026.09.29.csv'

# Medicare Inpatient Hospitals by Provider and Service, data year 2024, pinned dataset version (the same
# version the 2026-10-24 center set was built from).
CMS_VERSION = 'ca9b5ef0-1386-4759-b2b6-5f9b35116786'
CMS_API = f'https://data.cms.gov/data-api/v1/dataset/{CMS_VERSION}/data'
CMS_RAW = RAW.parent / 'cms_inpatient_2024'
CMS_PAGE = 5000
CMS_PAUSE_SECONDS = 10  # crawl delay asked by data.cms.gov/robots.txt

MEDICARE_YEARS = ['2021', '2022', '2023', '2024']
BMT_YEARS = ['2021', '2022', '2023', '2024', '2025']
BANDS = [(0, '0'), (10, '1-10'), (25, '11-25'), (50, '26-50'), (100, '51-100'), (200, '101-200')]
BAND_LABELS = [label for _, label in BANDS] + ['201+']
SITE_TYPES = {'P': ('physician_practice', 'practice'), 'F': ('federal_hospital', 'federal'),
              'H': ('hospital_ccn_unresolved', 'hospital-unresolved'), 'X': ('non_us_center', 'non-us')}

# Seven hospital names written by this project where the center table or the enrollment file names the wrong entity.
NAME_OVERRIDES = {
    '050025': 'UNIVERSITY OF CALIFORNIA SAN DIEGO MEDICAL CENTER (HILLCREST AND JACOBS)', '140088': 'THE UNIVERSITY OF CHICAGO MEDICAL CENTER',
    '100088': 'BAPTIST MEDICAL CENTER JACKSONVILLE (incl. WOLFSON CHILDRENS)',
    '150169': 'COMMUNITY HOSPITAL NORTH (COMMUNITY HEALTH NETWORK MD ANDERSON)', '050026': 'SHARP GROSSMONT HOSPITAL',
    '100022': 'JACKSON MEMORIAL HOSPITAL', '490063': 'INOVA FAIRFAX HOSPITAL (INOVA SCHAR CANCER INSTITUTE)'}
# Canadian centers by BMT InfoNet directory id, with the name and city written by this project.
CANADIAN_CENTERS = {
    '16783': ("BC Children's Hospital", 'Vancouver'), '23202': ('Princess Margaret Cancer Centre', 'Toronto'),
    '29699': ('Cross Cancer Institute', 'Edmonton'), '29842': ('CHU Sainte-Justine', 'Montreal'),
    '29968': ('McGill University Health Centre', 'Montreal'), '30358': ('The Ottawa Hospital, General Campus', 'Ottawa'),
    '42502': ('CancerCare Manitoba', 'Winnipeg'), '42529': ('Hamilton Health Sciences', 'Hamilton'),
    '42531': ('Hopital Maisonneuve-Rosemont', 'Montreal'), '42633': ('The Hospital for Sick Children', 'Toronto'),
    '42665': ('Vancouver General Hospital', 'Vancouver')}

# United States sites without a CCN, by BMT InfoNet directory id, with the name and city written by this project:
# the organisation's ordinary business name and the city of its directory record. Nothing here is taken from
# the name typed in the search table, which was written with the manufacturer lists in view.
US_SITES_WITHOUT_CCN = {
    '20285': ('Tennessee Oncology', 'Nashville'), '29956': ('Mary Bird Perkins Cancer Center', 'Baton Rouge'),
    '30636': ('Virginia Oncology Associates', 'Norfolk'), '30658': ('West Cancer Center', 'Germantown'),
    '42666': ('VA Tennessee Valley Healthcare System, Nashville', 'Nashville'),
    '95478': ('Texas Oncology - Deke Slayton Cancer Center', 'Webster'),
    '108472': ('The Center for Cancer and Blood Disorders (American Oncology Network)', 'Bethesda'),
    '110066': ('Epic Care', 'Emeryville'), '110073': ('Astera Cancer Care', 'East Brunswick'),
    '110097': ('The Center for Cancer and Blood Disorders', 'Fort Worth'),
    '110106': ('Virginia Cancer Specialists', 'Fairfax')}

# New York SPARCS facility name to CCN, and California HCAI facility number to CCN (both mapped by hand).
NY_FACILITIES = {
    'MEMORIAL HOSPITAL FOR CANCER AND ALLIED DISEASES': '330154', 'MOUNT SINAI HOSPITAL': '330024',
    'MONTEFIORE MEDICAL CENTER - HENRY & LUCY MOSES DIV': '330059', 'ROSWELL PARK CANCER INSTITUTE': '330354',
    'STRONG MEMORIAL HOSPITAL': '330285', 'NEW YORK-PRESBYTERIAN HOSPITAL - NEW YORK WEILL CORNELL CENTER': '330101',
    'NEW YORK-PRESBYTERIAN HOSPITAL - COLUMBIA PRESBYTERIAN CENTER': '330101', 'NYU LANGONE HOSPITALS': '330214',
    'NORTH SHORE UNIVERSITY HOSPITAL': '330106', 'WESTCHESTER MEDICAL CENTER': '330234',
    "COHEN'S CHILDREN MEDICAL CENTER W/ LIJ": '330195', "COHEN CHILDREN'S MEDICAL CENTER": '330195'}
NY_PAYER_GROUPS = {'Medicare': 'medicare', 'Medicaid': 'medicaid', 'Private Health Insurance': 'commercial',
                   'Blue Cross/Blue Shield': 'commercial'}
NY_GROUPS = ['medicare', 'medicaid', 'commercial', 'other']
CA_FACILITIES = {
    '106430905': '050441', '106190176': '050146', '106381154': '050454', '106370782': '050025', '106190687': '050112',
    '106190796': '050262', '106341006': '050599', '106191216': '050660', '106190555': '050625', '106301279': '050348',
    '106190170': '053302', '106300032': '053304', '106361246': '050327', '106370673': '053303', '106364502': '050778',
    '106371256': '050424', '106014326': '050075', '106100005': '050492', '106301205': '050224'}
PRICE_CLASSES = ['medicare_ffs', 'ma', 'commercial', 'medicaid']
MEDICAID_WORDS = ['MEDICAID', 'MEDI-CAL', 'MASSHEALTH', 'WELLSENSE', 'COUNTYCARE', 'MERIDIAN', 'CARE OREGON', 'CCO ',
                  'OHP ', 'INTERCOMMUNITY', 'COMMUNITY SOLUTIONS']

# Rows that must be read with another row. Wording uses the FACT listing, the BMT InfoNet directory and
# federal or state files only.
VOA = 'practice-virginia-oncology-associates-norfolk-va'
TENNESSEE_ONCOLOGY = 'practice-tennessee-oncology-nashville-tn'
AT = 'program_at_related_site'
HOSTS = 'hosts_program_accredited_under_related_ccn'
PARTLY = 'program_partly_at_related_site'
SHARES = 'shares_program_accredited_under_related_ccn'
SPLIT = 'one_program_split_across_ccns'
ALONE = 'accreditation_only'
RELATIONS = {AT: HOSTS, HOSTS: AT, PARTLY: SHARES, SHARES: PARTLY, SPLIT: SPLIT}
RELATED = {
    '340166': (AT, ['340113'], 'The FACT accreditation attached to this CCN is the Levine Cancer Institute program; its BMT InfoNet directory record is matched to Carolinas Medical Center (340113).'),
    '340113': (HOSTS, ['340166'], 'Site of the Levine Cancer Institute program whose FACT accreditation the center set also attaches to Atrium Health University City (340166).'),
    '490101': (AT, ['490063'], 'The FACT accreditation attached to this CCN is the Inova cell therapy program; its BMT InfoNet directory record is matched to Inova Fairfax Hospital (490063), which is not in the center set.'),
    '490063': (HOSTS, ['490101'], 'Site of the Inova cell therapy program whose FACT accreditation the center set attaches to Inova Fair Oaks Hospital (490101).'),
    '390114': (AT, ['390164'], 'The FACT accreditation attached to this CCN is the UPMC adult transplant and cell therapy program; its BMT InfoNet directory record is matched to UPMC Presbyterian Shadyside (390164).'),
    '390164': (HOSTS, ['390114'], 'Site of the UPMC adult cell therapy program whose FACT accreditation the center set attaches to UPMC Magee-Womens Hospital (390114).'),
    '380004': (AT, ['380061'], 'The FACT accreditation attached to this CCN is a Providence transplant program; the Providence BMT InfoNet directory record is matched to Providence Portland Medical Center (380061), which is not in the center set.'),
    '380061': (HOSTS, ['380004'], 'Site of the Providence cell therapy program; the center set attaches the Providence FACT accreditation to Providence St Vincent Medical Center (380004).'),
    '490007': (AT, [VOA], 'The FACT accreditation attached to this CCN is a joint program with Virginia Oncology Associates; the BMT InfoNet directory record is at that physician practice, which has no CCN.'),
    VOA: (HOSTS, ['490007'], 'Practice site of the joint cell therapy program whose FACT accreditation the center set attaches to Sentara Norfolk General Hospital (490007).'),
    '390050': (PARTLY, ['390090'], 'The FACT accreditation attached to this CCN names West Penn Hospital (390090), which is not in the center set and holds the BMT InfoNet directory record of the same health system program.'),
    '390090': (SHARES, ['390050'], 'Named by the FACT accreditation that the center set attaches to Allegheny General Hospital (390050); holds the BMT InfoNet directory record of that program.'),
    '340014': (PARTLY, ['340053', '340085'], 'One FACT-accredited Novant Health program covers Forsyth (this CCN), Presbyterian in Charlotte (340053) and Thomasville (340085); only Forsyth is in the center set.'),
    '340053': (SHARES, ['340014'], 'Covered by the Novant Health FACT accreditation that the center set attaches to Forsyth Medical Center (340014).'),
    '340085': (SHARES, ['340014'], 'Covered by the Novant Health FACT accreditation that the center set attaches to Forsyth Medical Center (340014).'),
    '440161': (PARTLY, [TENNESSEE_ONCOLOGY], 'A second FACT accreditation attached to this CCN belongs to Tennessee Oncology, a physician practice with its own BMT InfoNet directory record and no CCN.'),
    TENNESSEE_ONCOLOGY: (SHARES, ['440161'], 'Its FACT accreditation is attached in the center set to TriStar Centennial Medical Center (440161).'),
    '220110': (SPLIT, ['220162'], "Bills Medicare for adult inpatient CAR-T of the Dana-Farber Brigham program, whose FACT accreditation and BMT InfoNet directory record sit on the Dana-Farber row (220162). Read the two rows together."),
    '220162': (SPLIT, ['220110'], "Adult inpatient CAR-T of this program is billed to Medicare by Brigham and Women's Hospital (220110). Read the two rows together."),
    '050262': (SPLIT, ['050112'], 'UCLA Health runs one program; Medicare and California discharge files split its stays between Ronald Reagan (050262) and Santa Monica (050112). Read the two rows together.'),
    '050112': (SPLIT, ['050262'], 'UCLA Health runs one program; Medicare and California discharge files split its stays between Ronald Reagan (050262) and Santa Monica (050112). Read the two rows together.'),
    '363303': (ALONE, [], 'In the center set through a pediatric FACT accreditation only; it has no BMT InfoNet directory record.'),
}
# BMT InfoNet records whose assignment to a row was a judgement call.
BMT_MATCH_REVIEW = {
    '390027': 'The directory record of the Fox Chase-Temple program was assigned to Temple University Hospital by hand; Fox Chase Cancer Center (CCN 390196) has no row.',
    '223302': "A pediatric directory record listed at the Dana-Farber address was assigned to Boston Children's Hospital by name.",
    '260105': "A pediatric directory record of Cardinal Glennon Children's Hospital was matched to this adult hospital by name similarity (score 0.80); it may belong to a separate hospital.",
    '050026': "The directory record is a physician group at the hospital's address; it was matched to the hospital by address.",
}


def slug(text):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', text.lower().replace("'", '')).strip('-')


def clean_name(text):
    """Repair the two text faults of CMS enrollment names: an apostrophe stored as '¿' and a pipe."""
    return re.sub(r'\s*\|\s*', ' - ', str(text).replace('¿', "'")).strip()


def count(value):
    """Whole number shown on a directory or price-file cell; None for '-', '-0', masked and blank cells."""
    match = re.fullmatch(r'\s*(\d[\d,]*)\s*', '' if value is None else str(value))
    return int(match.group(1).replace(',', '')) if match else None


def band(total):
    """Public size band of a yearly count; blank stays blank and zero stays '0'."""
    if total == '' or total is None:
        return ''
    return next((label for upper, label in BANDS if int(total) <= upper), '201+')


def childrens_ccn(ccn):
    """CMS numbers children's hospitals 3300 to 3399 in the last four digits of the CCN."""
    return int(bool(ccn) and 3300 <= int(ccn[2:]) <= 3399)


def describe_site(source_id, center, enrollment, records):
    """Key, CCN, name and place of one site; `source_id` is a CCN or the search table's letter:name|city|state."""
    if source_id.isdigit():
        name = NAME_OVERRIDES.get(source_id) or (
            (center['dba_name'] or center['hospital_name']) if center
            else (enrollment['DOING BUSINESS AS NAME'] or enrollment['ORGANIZATION NAME']))
        place = (enrollment['CITY'], enrollment['STATE']) if enrollment else (center['city'], center['state'])
        return {'site_key': source_id, 'ccn': source_id, 'site_name': clean_name(name), 'city': place[0],
                'state': place[1], 'country': 'USA', 'site_type': 'hospital'}
    letter, rest = source_id.split(':', 1)
    name, city, state = rest.split('|')
    site_type, prefix = SITE_TYPES[letter]
    country = 'USA'
    ids = sorted({r['id'] for r in records if r['list'] == 'bmt'})
    name, city = next((US_SITES_WITHOUT_CCN[i] for i in ids if i in US_SITES_WITHOUT_CCN), (name, city))
    if letter == 'X':
        assert len(ids) == 1, f'Expected one directory record for {source_id}'
        (name, city), state, country = CANADIAN_CENTERS[ids[0]], '', 'CAN'
    key = '-'.join(part for part in (prefix, slug(name), slug(city), slug(state or country)) if part)
    return {'site_key': key, 'ccn': '', 'site_name': clean_name(name), 'city': city, 'state': state,
            'country': country, 'site_type': site_type}


def bmtinfonet_fields(records):
    """Flags, program counts and yearly sums from the BMT InfoNet directory records matched to one site."""
    programs = [r for r in records if r['list'] == 'bmt']
    out = {'on_bmtinfonet_directory': int(bool(programs)), 'bmtinfonet_program_count': len(programs),
           'bmtinfonet_directory_ids': ';'.join(sorted(r['id'] for r in programs)),
           'bmtinfonet_fact_approved': int(any((r.get('fact') or '').lower().startswith('yes') for r in programs)),
           'bmtinfonet_match_methods': ';'.join(sorted({r['how'] for r in programs})),
           'bmtinfonet_program_types': ';'.join(sorted({r.get('ptype') or 'unspecified' for r in programs}))}
    for year in BMT_YEARS:
        shown = [n for n in (count((r.get('vol') or {}).get(year)) for r in programs) if n is not None]
        out[f'bmtinfonet_n_{year}'] = sum(shown) if shown else ''
        out[f'bmtinfonet_programs_reporting_{year}'] = len(shown)
    adult = [n for n in (count((r.get('vol') or {}).get('2024')) for r in programs
                         if r.get('ptype') != 'Pediatric Program') if n is not None]
    out['bmtinfonet_n_2024_nonpediatric_programs'] = sum(adult) if adult else ''
    out['bmtinfonet_n_2024_band'] = band(out['bmtinfonet_n_2024'])
    out['bmtinfonet_n_2024_nonpediatric_band'] = band(out['bmtinfonet_n_2024_nonpediatric_programs'])
    return out


def list_fields(ccn, records):
    """Columns that depend on the manufacturer locators; every one of them stays in the local companion."""
    kite = [r for r in records if r['list'] == 'kite']
    bms = [r for r in records if r['list'] == 'bms' and r.get('active') != 'No']
    on_bmt = any(r['list'] == 'bmt' for r in records)
    pediatric = any('Pediatric' in (r.get('ptype') or '') for r in records) or bool(childrens_ccn(ccn))
    adult = any((r['list'] == 'bmt' and 'Adult' in (r.get('ptype') or '')) or r['list'] in ('kite', 'bms') for r in records)
    return {'on_kite_atc_list': int(bool(kite)), 'on_bms_center_list': int(bool(bms)),
            'n_lists': int(bool(kite)) + int(bool(bms)) + int(on_bmt),
            'population': 'adult+pediatric' if adult and pediatric else 'pediatric' if pediatric else 'adult' if adult else '',
            'match_methods': ';'.join(sorted({r['how'] for r in records})),
            'list_records_matched_by_address_only': sum(r['how'] == 'exact_addr' and float(r['score']) == 0 for r in records)}


def duplicate_count_suspects(records, sites):
    """Site pairs whose directory records show the same number in all five years (five-year total 20 or more)."""
    seen = defaultdict(set)
    for r in records:
        shown = [count((r.get('vol') or {}).get(year)) for year in BMT_YEARS]
        if r['list'] == 'bmt' and r.get('ccn') and None not in shown and sum(shown) >= 20:
            seen[tuple(shown)].add(sites[r['ccn']])
    out = {}
    for keys in seen.values():
        if len(keys) > 1:
            for key in keys:
                out[key] = ';'.join(sorted(keys - {key}))
    return out


def medicare_file_status(ccn, discharges, in_file):
    """Why the 2024 Medicare cell is filled or blank."""
    if not ccn:
        return 'no_ccn'
    if discharges != '':
        return 'count_published'
    return 'in_file_no_drg018_row' if ccn in in_file else 'not_in_file'


def sparcs_counts(rows):
    """CCN to payer-group counts for one year of New York de-identified APR-DRG 011 discharge rows."""
    out = defaultdict(Counter)
    for row in rows:
        ccn = NY_FACILITIES[row['facility_name'].upper()]
        out[ccn]['all'] += 1
        out[ccn][NY_PAYER_GROUPS.get(row['payment_typology_1'], 'other')] += 1
    return out


def new_york_fields(site, by_year):
    out = {f'ny_sparcs_aprdrg011_all_payer_{year}': by_year[year][site['ccn']]['all'] or '' if site['ccn'] in by_year[year] else ''
           for year in ('2023', '2024')}
    split = by_year['2024'].get(site['ccn'])
    out.update({f'ny_sparcs_aprdrg011_2024_{group}': split[group] if split else '' for group in NY_GROUPS})
    listed = any(out[f'ny_sparcs_aprdrg011_all_payer_{year}'] != '' for year in ('2023', '2024'))
    out['ny_sparcs_status'] = ('outside_new_york' if site['state'] != 'NY' else 'in_state_no_ccn' if not site['ccn']
                               else 'discharges_in_file' if listed else 'in_state_none_in_file')
    return out


def hcai_counts(lines):
    """(CCN to {year: discharges}, year to statewide discharges) from the saved California MS-DRG 018 extract."""
    by_ccn, statewide, year = defaultdict(dict), {}, None
    for line in lines:
        header = re.match(r'== (\d{4})', line)
        if header:
            year = header.group(1)
            continue
        entry = re.match(r'(\d+) .*? \| discharges (\d+)', line)
        if entry and entry.group(1) == '000000':
            statewide[year] = int(entry.group(2))
        elif entry:
            by_ccn[CA_FACILITIES[entry.group(1)]][year] = int(entry.group(2))
    return by_ccn, statewide


def california_fields(site, by_ccn):
    out = {f'ca_hcai_msdrg018_all_payer_{year}': by_ccn.get(site['ccn'], {}).get(year, '') for year in ('2024', '2025')}
    listed = any(value != '' for value in out.values())
    out['ca_hcai_status'] = ('outside_california' if site['state'] != 'CA' else 'in_state_no_ccn' if not site['ccn']
                             else 'drg018_in_top25_table' if listed else 'in_state_drg018_not_in_top25_table')
    return out


def payer_class(payer, plan):
    """Payer class of one price-file row by keyword; traditional Medicare is known only by one payer code."""
    text = f'{payer} {plan}'.upper()
    if re.match(r'MEDICARE \[2001\]', payer.upper()):
        return 'medicare_ffs'
    if 'MEDICARE ALTERNATE' in text:
        return 'medicare_other'
    if any(word in text for word in MEDICAID_WORDS) and 'MEDICARE' not in text:
        return 'medicaid'
    if 'MEDICARE' in text or 'COMMONWEALTH CARE ALLIA' in text or 'CAREADVANTAGE' in text:
        return 'ma'
    return 'commercial'


def add_remittances(tally, payer, plan, shown):
    """Add one price-file row: an exact count joins the sum, a masked count adds one row and no remittances."""
    cell = tally[payer_class(payer, plan)]
    exact = count(shown)
    if exact is not None:
        cell[0] += exact
    elif 'through' in str(shown):
        cell[1] += 1


def price_counts(table_rows, mgb_lines):
    """CCN to {payer class: [sum of exact remittance counts, number of masked rows]} for inpatient DRG rows."""
    out = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in table_rows:
        if 'DRG' in row['codes']:
            add_remittances(out[re.search(r'CCN (\d{6})', row['hospital']).group(1)], row['payer'], row['plan'], row['count'])
    for ccn, lines in mgb_lines.items():
        for line in lines:
            part = line.rstrip('\n').split('|')
            if len(part) >= 12 and part[1] in ('018', '011') and part[2] == 'inpatient':
                shown = re.match(r'n=(.*)', part[11])
                add_remittances(out[ccn], part[4], part[5], shown.group(1) if shown else '')
    return out


def price_fields(site, tally):
    """Remittance columns of one site. Masked rows are counted as rows; a sum with no exact row stays blank."""
    parsed = tally.get(site['ccn']) if site['ccn'] else None
    out = {'pt_remit_status': 'no_ccn' if not site['ccn'] else 'file_parsed' if parsed is not None else 'file_not_parsed'}
    for name in PRICE_CLASSES:
        exact, masked = parsed[name] if parsed is not None and name in parsed else (0, 0)
        out[f'pt_remit_{name}_exact_n'] = exact or ''
        out[f'pt_remit_{name}_masked_rows'] = masked if parsed is not None else ''
    return out


def research_fields(site, research):
    row = research.get(site['ccn']) if site['ccn'] else None
    return {'open_payments_cart_product_research_usd_2019_2025': money(row['cart_product_research_usd']) if row else '',
            'open_payments_cart_product_or_text_research_usd_2019_2025': money(row['cart_product_or_text_research_usd']) if row else '',
            'open_payments_research_status': ('no_ccn' if not site['ccn'] else 'published_records' if row
                                              else 'no_published_records')}


def has_public_all_payer_count(row):
    """1 when a state discharge file or a parsed hospital price file shows something for the site."""
    return int(any(row[name] != '' for name in STATE_COUNT_COLUMNS) or row['pt_remit_status'] == 'file_parsed')


def public_basis(row):
    """Public reasons for a row to be in the tracked table; none means only a manufacturer locator lists the site."""
    return [name for name, holds in (
        ('center_set', str(row['in_cart_center_set']) == '1'),
        ('bmtinfonet_directory', str(row['on_bmtinfonet_directory']) == '1'),
        ('state_discharge_file', any(row[name] != '' for name in STATE_COUNT_COLUMNS))) if holds]


def split_rows(rows):
    """(rows for the tracked table, rows kept local because no public source supports them)."""
    return [r for r in rows if public_basis(r)], [r for r in rows if not public_basis(r)]


def build_rows(center_rows, enrollment, records, drg, in_file, ny, ca, price, research, notes):
    """Assemble every column of every site from parsed inputs; all arguments are plain dicts and lists."""
    by_source = defaultdict(list)
    for record in records:
        if record.get('ccn'):
            by_source[record['ccn']].append(record)
    sources = list(dict.fromkeys(
        list(center_rows) + list(by_source) + [c for year in MEDICARE_YEARS for c in drg[year]]
        + [c for year in ny for c in ny[year]] + list(ca)))
    sites = {s: describe_site(s, center_rows.get(s), enrollment.get(s), by_source[s]) for s in sources}
    suspects = duplicate_count_suspects(records, {s: site['site_key'] for s, site in sites.items()})
    rows = []
    for source, site in sites.items():
        center, row = center_rows.get(source), dict(site)
        row.update(in_cart_center_set=int(bool(center)), set_evidence=center['evidence'] if center else '',
                   core_hcps_affiliated=center['core_hcps_affiliated'] if center else '',
                   childrens_hospital_ccn_range=childrens_ccn(site['ccn']))
        relation, related, note = RELATED.get(site['site_key'], ('', [], ''))
        row.update(related_center_ccn=';'.join(k for k in related if k.isdigit()), related_site_key=';'.join(related),
                   related_site_relation=relation, related_site_note=note)
        row.update(bmtinfonet_fields(by_source[source]))
        row['bmtinfonet_duplicate_suspect_site_key'] = suspects.get(site['site_key'], '')
        row['bmtinfonet_match_review_note'] = BMT_MATCH_REVIEW.get(site['site_key'], '') if row['on_bmtinfonet_directory'] else ''
        for year in MEDICARE_YEARS:
            row[f'medicare_ffs_drg018_{year}'] = drg[year].get(site['ccn'], '') if site['ccn'] else ''
        row['medicare_file_status_2024'] = medicare_file_status(site['ccn'], row['medicare_ffs_drg018_2024'], in_file)
        row.update(new_york_fields(site, ny))
        row.update(california_fields(site, ca))
        row.update(price_fields(site, price))
        row.update(research_fields(site, research))
        row['has_public_all_payer_count'] = has_public_all_payer_count(row)
        row.update(list_fields(site['ccn'], by_source[source]))
        row['has_any_all_payer_value'] = int(bool(row['has_public_all_payer_count'])
                                             or any(row[f'bmtinfonet_n_{year}'] != '' for year in BMT_YEARS))
        row['linked_site_note'], row['set_registration_note'] = notes.get(source, ('', ''))
        row['search_site_id'] = '' if source.isdigit() else source
        rows.append(row)
    return sorted(rows, key=lambda r: r['site_key'])


STATE_COUNT_COLUMNS = ['ny_sparcs_aprdrg011_all_payer_2023', 'ny_sparcs_aprdrg011_all_payer_2024',
                       'ca_hcai_msdrg018_all_payer_2024', 'ca_hcai_msdrg018_all_payer_2025']
PRICE_COLUMNS = ['pt_remit_medicare_ffs_exact_n', 'pt_remit_ma_exact_n', 'pt_remit_ma_masked_rows',
                 'pt_remit_commercial_exact_n', 'pt_remit_commercial_masked_rows', 'pt_remit_medicaid_masked_rows']
# Built but empty or meaningless on every row, so not published: the classifier reads traditional Medicare
# from one payer code only, and no parsed hospital shows an exact Medicaid count.
PRICE_COLUMNS_NOT_PUBLISHED = ['pt_remit_medicare_ffs_masked_rows', 'pt_remit_medicaid_exact_n']
BMT_COUNT = ('Self-reported CAR-T therapies performed in {year}, summed over the BMT InfoNet directory records matched '
             'to the site, as displayed on 2026-11-19 (unaudited, all indications, definition not stated by the site).')
BMT_BLANK = 'No matched record shows a number for the year (not in the directory, or the year is shown as a dash). 0 means the reported values sum to zero.'
MEDICARE_BLANK = ('No MS-DRG 018 row for the CCN in that year of the CMS file: fewer than 11 discharges (suppressed) or none, '
                  'hospital absent from the file, or no CCN. 0 never appears.')
NY_BLANK = 'No New York discharge record is mapped to the site; ny_sparcs_status says whether the site is outside New York. 0 never appears.'
NY_SPLIT_BLANK = 'Site has no 2024 New York record (see ny_sparcs_status). 0 means a hospital with 2024 records and none in this payer group.'
CA_BLANK = 'Site outside California, or MS-DRG 018 is in none of the hospital\'s top-25 tables that year (not evidence of zero); see ca_hcai_status. 0 never appears.'
PRICE_EXACT_BLANK = 'Price file not parsed (see pt_remit_status), or the parsed file has no row of this payer class with an exact count. A blank is not zero volume.'
PRICE_MASKED_BLANK = 'Price file not parsed (see pt_remit_status). 0 means a parsed file with no masked row of this payer class.'
PAYMENTS_BLANK = 'The CCN is the teaching hospital on no Open Payments research record in 2019 to 2025, or the site has no CCN; see open_payments_research_status. 0.00 means it has research records and none match.'

COLUMNS = [
    column('site_key', 'Identifier of the site, filled on every row: the CMS Certification Number (CCN) for the 218 hospital rows, otherwise a lowercase slug of site type, name, city and state or country written by this project (for example practice-tennessee-oncology-nashville-tn).', 'Never blank.', 'project'),
    column('ccn', 'CCN of the Medicare-enrolled hospital the row stands for (six digits, leading zeros kept; load as text). It is the join key to cart_centers of 2026-10-24 and to cart_center_340b_status of this snapshot, which carries 340B drug discount program status; no 340B column is repeated here.', 'The site is not a United States hospital with a CCN (22 rows); site_type says which kind.', 'cms'),
    column('site_name', 'Name of the site: for center-set hospitals the doing-business-as name, else the legal name, of cart_centers; for other hospitals the same from the Centers for Medicare & Medicaid Services (CMS) Hospital Enrollments file of 2026-09-29; seven hospital names and all names of sites without a CCN are written by this project. Two hospitals share the name METHODIST HOSPITAL (450388, 280040).', 'Never blank.', 'project'),
    column('city', 'City of the site: the enrollment address city of the CMS Hospital Enrollments file for hospital rows (for example EDMOND for 370093), a city written by this project for the other rows.', 'Never blank.', 'cms'),
    column('state', 'Two-letter United States state or territory code of the site (PR included).', 'The site is outside the United States (11 Canadian rows).', 'cms'),
    column('country', 'Country of the site as a three-letter code: USA or CAN.', 'Never blank.', 'project'),
    column('site_type', 'Kind of site: hospital (has a CCN, 218 rows), physician_practice (practice or freestanding clinic without a hospital CCN, 10), federal_hospital (Veterans Affairs hospital, which has no CCN, 1), non_us_center (Canadian center, 11).', 'Never blank.', 'project'),
    column('in_cart_center_set', '1 when the CCN is one of the 173 CAR-T (chimeric antigen receptor T-cell) centers of cart_centers (snapshot 2026-10-24), else 0.', 'Never blank.', 'project'),
    column('set_evidence', 'Why the CCN is in the center set, copied from cart_centers: drg018_2024 (a published 2024 Medicare count), fact_iec_accredited (an immune effector cell accreditation of the Foundation for the Accreditation of Cellular Therapy, FACT, attached to the CCN), or both joined by a semicolon.', 'Not in the center set (67 rows).', 'project'),
    column('core_hcps_affiliated', 'Number of health care professionals of the core division of hcp_divisions (500 people, snapshot 2026-10-24) affiliated with the center, copied from cart_centers.', 'Not in the center set (67 rows). 0 means a center with no core professional.', 'project'),
    column('childrens_hospital_ccn_range', '1 when the last four digits of the CCN are 3300 to 3399, the range CMS gives children\'s hospitals, else 0 (36 rows are 1). All 36 are absent from the 2024 Medicare inpatient file.', 'Never blank; 0 also covers rows without a CCN.', 'cms'),
    column('related_center_ccn', 'CCN, or CCNs joined by a semicolon, of other rows of this table that must be read together with this row; the hospital subset of related_site_key.', 'No related hospital row.', 'project'),
    column('related_site_key', 'site_key values, joined by a semicolon, of the rows this row must be read with; every pointer is returned by the row it names.', 'No related row.', 'project'),
    column('related_site_relation', 'How the related rows connect: program_at_related_site (the accreditation that puts this CCN in the center set belongs to a program at the related site), hosts_program_accredited_under_related_ccn (the reverse), program_partly_at_related_site, shares_program_accredited_under_related_ccn (the reverse), one_program_split_across_ccns, or accreditation_only (in the set by accreditation, no directory record, no related row).', 'No note applies to the row (218 rows).', 'project'),
    column('related_site_note', 'One sentence by this project explaining the relation, based on the FACT listing, the BMT InfoNet directory and Medicare or state files.', 'No note applies to the row.', 'project'),
    column('on_bmtinfonet_directory', '1 when at least one record of the CAR T-cell Center Directory of BMT InfoNet (Blood & Marrow Transplant Information Network; 277 records retrieved 2026-11-19) was matched to the site, else 0. Presence on a list, not proof of treatment.', 'Never blank.', 'bmtinfonet'),
    column('bmtinfonet_program_count', 'Number of BMT InfoNet directory records (programs) matched to the site, 0 to 3; adult and pediatric programs of one hospital are separate records.', 'Never blank; 0 means not in the directory.', 'bmtinfonet'),
    column('bmtinfonet_directory_ids', 'Directory record numbers of the matched BMT InfoNet programs, joined by a semicolon; each is the last part of the address of the program page on bmtinfonet.org.', 'Not in the directory.', 'bmtinfonet'),
    column('bmtinfonet_fact_approved', '1 when a matched BMT InfoNet record showed FACT approval as Yes on 2026-11-19, else 0. The source is the directory page, not the FACT listing, and the two disagree for some centers.', 'Never blank; 0 covers a No, an empty field and sites not in the directory.', 'bmtinfonet'),
    column('bmtinfonet_match_methods', 'How the BMT InfoNet records were matched to the site, distinct values joined by a semicolon: exact_addr (same ZIP code, street number and first street word as a CMS enrollment address), fuzzy (best name and place score within the state), manual (set by hand), foreign (record with no United States state).', 'Not in the directory.', 'project'),
    column('bmtinfonet_match_review_note', 'Note by this project on a BMT InfoNet record whose assignment to this row was a judgement call and should be reviewed before the row is relied on.', 'No such note (236 rows).', 'project'),
    column('bmtinfonet_programs_reporting_2024', 'Number of matched BMT InfoNet programs that show a number of CAR-T therapies for 2024. When it is above 0 and below bmtinfonet_program_count, the 2024 band is a partial sum.', 'Never blank; 0 means no matched program shows a 2024 number.', 'bmtinfonet'),
    column('bmtinfonet_n_2024_band', 'Size band of the self-reported CAR-T therapies performed in 2024, summed over the matched BMT InfoNet programs as displayed on 2026-11-19: 0, 1-10, 11-25, 26-50, 51-100, 101-200 or 201+. Unaudited, all indications, definition not stated by the site; exact counts are not published.', 'No matched program shows a 2024 number (see bmtinfonet_programs_reporting_2024). The band 0 means a reported zero.', 'bmtinfonet'),
    column('bmtinfonet_n_2024_nonpediatric_band', 'Same size band for 2024, summed only over matched BMT InfoNet programs that are not pediatric programs.', 'No non-pediatric matched program shows a 2024 number.', 'bmtinfonet'),
    column('bmtinfonet_duplicate_suspect_site_key', 'site_key of another row whose BMT InfoNet record shows the same number in each of the five years 2021 to 2025 (five-year total 20 or more), which suggests one program posted on two directory pages and counted twice. Not confirmed with the source.', 'No such pair (238 rows).', 'project'),
    column('medicare_ffs_drg018_2021', 'Medicare fee-for-service inpatient discharges in Medicare Severity Diagnosis Related Group (MS-DRG) 018, the CAR-T and other immunotherapy group, at the CCN in 2021, from CMS Medicare Inpatient Hospitals by Provider and Service. Not lymphoma-specific; no Medicare Advantage, no outpatient infusions.', MEDICARE_BLANK, 'cms'),
    column('medicare_ffs_drg018_2022', 'Medicare fee-for-service inpatient discharges in MS-DRG 018 at the CCN in 2022, from CMS Medicare Inpatient Hospitals by Provider and Service.', MEDICARE_BLANK, 'cms'),
    column('medicare_ffs_drg018_2023', 'Medicare fee-for-service inpatient discharges in MS-DRG 018 at the CCN in 2023, from CMS Medicare Inpatient Hospitals by Provider and Service.', MEDICARE_BLANK, 'cms'),
    column('medicare_ffs_drg018_2024', 'Medicare fee-for-service inpatient discharges in MS-DRG 018 at the CCN in 2024, from CMS Medicare Inpatient Hospitals by Provider and Service; equal to drg018_discharges_2024 of cart_centers for the 173 centers.', 'See medicare_file_status_2024, which separates the three reasons for a blank. 0 never appears.', 'cms'),
    column('medicare_file_status_2024', 'Why the 2024 Medicare cell is filled or blank, from a check of every CCN against the full 2024 file: count_published (11 or more discharges), in_file_no_drg018_row (the hospital has rows for other groups, so its MS-DRG 018 count is below 11 or zero), not_in_file (the hospital has no row for any group: 36 children\'s hospitals, 10 cancer hospitals paid outside the prospective payment system, and 050778 and 400016), no_ccn.', 'Never blank.', 'cms'),
    column('ny_sparcs_aprdrg011_all_payer_2023', 'All-payer inpatient discharges in All Patient Refined Diagnosis Related Group (APR-DRG) 011, the CAR-T and other immunotherapy group, in 2023 in the de-identified public file of the New York Statewide Planning and Research Cooperative System (SPARCS), counted as records and summed over the facilities mapped by hand to the CCN. Not lymphoma-specific; values below 11 are shown as the file gives them.', NY_BLANK, 'ny_sparcs'),
    column('ny_sparcs_aprdrg011_all_payer_2024', 'All-payer inpatient discharges in APR-DRG 011 in 2024 in the New York SPARCS de-identified public file, summed over the facilities mapped by hand to the CCN (both NewYork-Presbyterian campuses go to 330101; Cohen Children\'s goes to 330195).', NY_BLANK, 'ny_sparcs'),
    column('ny_sparcs_aprdrg011_2024_medicare', 'Number of the 2024 New York APR-DRG 011 discharges whose first payment type is Medicare; the file does not separate traditional Medicare from Medicare Advantage. In the four payer columns values below 11 are shown as counted from the public record-level file.', NY_SPLIT_BLANK, 'ny_sparcs'),
    column('ny_sparcs_aprdrg011_2024_medicaid', 'Number of the 2024 New York APR-DRG 011 discharges whose first payment type is Medicaid.', NY_SPLIT_BLANK, 'ny_sparcs'),
    column('ny_sparcs_aprdrg011_2024_commercial', 'Number of the 2024 New York APR-DRG 011 discharges whose first payment type is Private Health Insurance or Blue Cross/Blue Shield.', NY_SPLIT_BLANK, 'ny_sparcs'),
    column('ny_sparcs_aprdrg011_2024_other', 'Number of the 2024 New York APR-DRG 011 discharges with any other first payment type (federal, state, local or Veterans Affairs; unspecified managed care; self-pay), counted from the records. The four payer columns sum to the 2024 total on every row.', NY_SPLIT_BLANK, 'ny_sparcs'),
    column('ny_sparcs_status', 'Coverage of the row by the New York file: discharges_in_file (a count in 2023 or 2024), in_state_none_in_file (New York hospital with no APR-DRG 011 record mapped to it), in_state_no_ccn (New York site without a CCN; none in this snapshot), outside_new_york (the file cannot show this site).', 'Never blank.', 'ny_sparcs'),
    column('ca_hcai_msdrg018_all_payer_2024', 'All-payer inpatient discharges in MS-DRG 018 in 2024 at the California licensed facility mapped by hand to the CCN, from the Top 25 MS-DRGs Individual Hospital profile of the California Department of Health Care Access and Information (HCAI). A hospital is listed only when MS-DRG 018 is in one of its top-25 tables; values as low as 1 are shown.', CA_BLANK, 'ca_hcai'),
    column('ca_hcai_msdrg018_all_payer_2025', 'All-payer inpatient discharges in MS-DRG 018 in 2025 at the California licensed facility mapped by hand to the CCN, from the same HCAI profile.', CA_BLANK, 'ca_hcai'),
    column('ca_hcai_status', 'Coverage of the row by the California file: drg018_in_top25_table (a count in 2024 or 2025), in_state_drg018_not_in_top25_table (California hospital not listed in either year, which is not evidence of zero), in_state_no_ccn (California site without a CCN, so no facility could be mapped), outside_california (the file cannot show this site).', 'Never blank.', 'ca_hcai'),
    column('pt_remit_medicare_ffs_exact_n', 'Sum of exact remittance counts (11 or more each) on traditional Medicare payer rows for MS-DRG 018 or APR-DRG 011 inpatient stays in the hospital\'s own price transparency file. Remittances in the 12 to 15 months before the file was posted (files dated 23 March to 1 April 2026 for five hospitals; the Stanford and Emory file dates were not recorded), not patients. The payer rule recognises traditional Medicare in the two Mass General Brigham files only.', PRICE_EXACT_BLANK, 'hospital_price_file'),
    column('pt_remit_ma_exact_n', 'Sum of exact remittance counts on payer-plan rows classed Medicare Advantage by keyword, same files, groups and window. Remittances, not patients.', PRICE_EXACT_BLANK, 'hospital_price_file'),
    column('pt_remit_ma_masked_rows', 'Number of Medicare Advantage payer-plan rows whose remittance count the file masks as 1 through 10. A count of file rows, each standing for 1 to 10 remittances; never add it to a count of cases.', PRICE_MASKED_BLANK, 'hospital_price_file'),
    column('pt_remit_commercial_exact_n', 'Sum of exact remittance counts on payer-plan rows not classed Medicare, Medicare Advantage or Medicaid (transplant carve-out contracts included), same files, groups and window. Remittances include partial and adjustment payments.', PRICE_EXACT_BLANK, 'hospital_price_file'),
    column('pt_remit_commercial_masked_rows', 'Number of commercial payer-plan rows whose remittance count the file masks as 1 through 10 (for APR-DRG 011 one row per plan and severity level). A count of file rows, not of cases.', PRICE_MASKED_BLANK, 'hospital_price_file'),
    column('pt_remit_medicaid_masked_rows', 'Number of Medicaid payer-plan rows (state fee-for-service and managed care together, classed by keyword) whose remittance count the file masks as 1 through 10. A count of file rows, not of cases; no parsed file shows an exact Medicaid count.', PRICE_MASKED_BLANK, 'hospital_price_file'),
    column('pt_remit_status', 'Whether the hospital\'s price transparency file was parsed for the remittance columns: file_parsed (7 hospitals), file_not_parsed (hospital whose file was not read for this table, which says nothing about its volume), no_ccn.', 'Never blank.', 'hospital_price_file'),
    column('open_payments_cart_product_research_usd_2019_2025', 'United States dollars on CMS Open Payments research records, program years 2019 to 2025 (publication of 2026-06-30), whose teaching hospital is this CCN and whose product fields name one of seven approved CAR-T products (Yescarta, Tecartus, Kymriah, Breyanzi, Abecma, Carvykti, Aucatzyl). As reported by manufacturers; institutional payments, not lymphoma-specific.', PAYMENTS_BLANK, 'open_payments'),
    column('open_payments_cart_product_or_text_research_usd_2019_2025', 'United States dollars on the same Open Payments research records that name a CAR-T product in a product field or whose study name or research context matches a CAR-T product name or phrase; a text rule of this project, never below the product-only amount.', PAYMENTS_BLANK, 'open_payments'),
    column('open_payments_research_status', 'Why the two Open Payments cells are filled or blank: published_records (the CCN is the teaching hospital on at least one research record in 2019 to 2025), no_published_records, no_ccn. Research paid to a university or institute instead of the hospital is not counted.', 'Never blank.', 'open_payments'),
    column('has_public_all_payer_count', '1 when the New York file or the California file shows a count for the site, or its price transparency file was parsed (masked rows alone also count), else 0. Computed from the state and price-file columns of this table only; the BMT InfoNet directory plays no part.', 'Never blank.', 'project'),
    # Local companion only: manufacturer locators, values computed with them, exact directory counts, copied text.
    column('on_kite_atc_list', '1 when a Kite authorized treatment center record (locator file retrieved 2026-11-19; collection-only records excluded) was matched to the site, else 0.', 'Never blank.', 'kite'),
    column('on_bms_center_list', '1 when a Bristol Myers Squibb CAR-T treatment center record not flagged inactive (file retrieved 2026-11-19; lymphoma and myeloma centers) was matched to the site, else 0.', 'Never blank.', 'bms'),
    column('n_lists', 'Number of the three lists (Kite, Bristol Myers Squibb, BMT InfoNet) on which the site appears, 0 to 3.', 'Never blank.', 'project', restricted=True),
    column('population', 'Patient population inferred by rule from the three lists and the CCN range: adult, pediatric or adult+pediatric. Any manufacturer record yields adult, so the value is unreliable at children\'s hospitals.', 'No rule fired (10 rows).', 'project', restricted=True),
    column('match_methods', 'How the records of all three lists were matched to the site, distinct values joined by a semicolon (exact_addr, fuzzy, manual, foreign).', 'On none of the three lists.', 'project', restricted=True),
    column('list_records_matched_by_address_only', 'Number of list records attached to the site by an exact address match with no shared name word (score 0); such a record may belong to a practice at the hospital address.', 'Never blank.', 'project', restricted=True),
    column('has_any_all_payer_value', 'The search table\'s flag: 1 when the row has any BMT InfoNet yearly value, a New York or California count, or a parsed price file, else 0.', 'Never blank.', 'project', restricted=True),
    column('bmtinfonet_program_types', 'Program types of the matched BMT InfoNet records as the site writes them, joined by a semicolon; unspecified when the page gives none.', 'Not in the directory.', 'bmtinfonet', restricted=True),
    *[column(f'bmtinfonet_n_{year}', BMT_COUNT.format(year=year), BMT_BLANK, 'bmtinfonet', restricted=True) for year in BMT_YEARS],
    column('bmtinfonet_n_2024_nonpediatric_programs', 'Self-reported 2024 CAR-T therapies summed only over matched BMT InfoNet records that are not pediatric programs, as displayed on 2026-11-19.', BMT_BLANK, 'bmtinfonet', restricted=True),
    *[column(f'bmtinfonet_programs_reporting_{year}', f'Number of matched BMT InfoNet programs that show a number for {year}; below bmtinfonet_program_count the yearly sum is partial.', 'Never blank.', 'bmtinfonet', restricted=True)
      for year in BMT_YEARS if year != '2024'],
    column('linked_site_note', 'Hand note of the search on rows whose counts must be read with another row; it cites the manufacturer lists.', 'No note.', 'project', restricted=True),
    column('set_registration_note', 'Hand note of the search on nine center-set CCNs whose accreditation appears to belong to another site; it cites the manufacturer lists.', 'No note.', 'project', restricted=True),
    column('search_site_id', 'Identifier the search table gave the 34 rows without a CCN (letter, name, city, state); the key of the list crosswalk kept in the notes folder.', 'Hospital row; the CCN is the identifier.', 'project', restricted=True),
]
PUBLIC = [c['column'] for c in COLUMNS if not c['restricted']]
RESTRICTED_COLUMNS = [c['column'] for c in COLUMNS if c['restricted']]
# Search-table columns that must never reach the tracked table under any name used so far.
FORBIDDEN = set(RESTRICTED_COLUMNS) | {
    'site_id', 'name', 'in_173_set', 'fact_iec_in_set', 'fact_per_bmtinfonet', 'kite_atc', 'bms_center', 'bmtinfonet',
    'bmtinfonet_programs', 'list_names', 'any_all_payer_count', 'opais_340b_status', 'opais_340b_entity_type',
    'set_registration_flag', 'pt_note', 'exempt_cancer_hospital', 'pps_exempt_cancer_hospital'}


def fetch(url, path, tries=3):
    """Save one response with its hash and UTC retrieval time, using the default client header."""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                payload = response.read()
            break
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(15 * (attempt + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    dump(path.with_suffix('.meta.json'), {'source_url': url, 'retrieved_at_utc': now(), 'bytes': len(payload),
                                          'sha256': hashlib.sha256(payload).hexdigest(), 'dataset_version_id': CMS_VERSION})
    return json.loads(payload)


def collect_inpatient_2024():
    """Pull CCN, group and discharges of every row of the pinned 2024 Medicare inpatient file, paced, once."""
    done = CMS_RAW / 'complete.json'
    if done.exists():
        return load(done)
    total = fetch(CMS_API + '/stats', CMS_RAW / 'stats.json')['found_rows']
    for offset in range(0, total, CMS_PAGE):
        path = CMS_RAW / f'page_{offset:06d}.json'
        if not (path.exists() and path.with_suffix('.meta.json').exists()):
            time.sleep(CMS_PAUSE_SECONDS)
            fetch(f'{CMS_API}?column=Rndrng_Prvdr_CCN,DRG_Cd,Tot_Dschrgs&size={CMS_PAGE}&offset={offset}', path)
            print('CMS INPATIENT 2024 PAGE', offset, flush=True)
    rows = [row for path in sorted(CMS_RAW.glob('page_*.json')) if not path.name.endswith('.meta.json') for row in load(path)]
    pairs = {(row['Rndrng_Prvdr_CCN'], row['DRG_Cd']) for row in rows}
    assert len(rows) == total == len(pairs), f'Incomplete or repeated pages: {len(rows)} rows, {len(pairs)} distinct, {total} expected'
    summary = {'dataset_version_id': CMS_VERSION, 'rows': total, 'pages': len(list(CMS_RAW.glob('page_*.meta.json'))),
               'distinct_ccn': len({ccn for ccn, _ in pairs}), 'completed_at_utc': now()}
    dump(done, summary)
    return summary


def inpatient_2024():
    """(CCNs with any row in the 2024 file, CCN to MS-DRG 018 discharges) plus a per-CCN file under interim."""
    summary = collect_inpatient_2024()
    groups, cart = Counter(), {}
    for path in sorted(CMS_RAW.glob('page_*.json')):
        if path.name.endswith('.meta.json'):
            continue
        for row in load(path):
            groups[row['Rndrng_Prvdr_CCN']] += 1
            if row['DRG_Cd'] == '018':
                cart[row['Rndrng_Prvdr_CCN']] = int(row['Tot_Dschrgs'])
    write_csv(INTERIM / MODULE / 'medicare_inpatient_2024_by_ccn.csv',
              [{'ccn': ccn, 'drg_rows': n, 'drg018_discharges': cart.get(ccn, '')} for ccn, n in sorted(groups.items())])
    return set(groups), cart, summary


def reconcile(rows, search_rows):
    """Count cells where the rebuilt table differs from the table the search produced (expected: none)."""
    pairs = {'city': 'city', 'in_cart_center_set': 'in_173_set', 'set_evidence': 'set_evidence',
             'core_hcps_affiliated': 'core_hcps_affiliated', 'bmtinfonet_fact_approved': 'fact_per_bmtinfonet',
             'on_kite_atc_list': 'kite_atc', 'on_bms_center_list': 'bms_center', 'on_bmtinfonet_directory': 'bmtinfonet',
             'n_lists': 'n_lists', 'population': 'population', 'bmtinfonet_program_count': 'bmtinfonet_programs',
             'bmtinfonet_program_types': 'bmtinfonet_program_types', 'match_methods': 'match_methods',
             'bmtinfonet_n_2024_nonpediatric_programs': 'bmtinfonet_n_2024_nonpediatric_programs',
             'has_any_all_payer_value': 'any_all_payer_count', 'ny_sparcs_aprdrg011_2024_medicare': 'ny_2024_medicare',
             'ny_sparcs_aprdrg011_2024_medicaid': 'ny_2024_medicaid', 'ny_sparcs_aprdrg011_2024_commercial': 'ny_2024_commercial',
             'ny_sparcs_aprdrg011_2024_other': 'ny_2024_other',
             **{f'bmtinfonet_n_{y}': f'bmtinfonet_n_{y}' for y in BMT_YEARS},
             **{f'medicare_ffs_drg018_{y}': f'medicare_ffs_drg018_{y}' for y in MEDICARE_YEARS},
             **{name: name for name in STATE_COUNT_COLUMNS},
             **{f'pt_remit_{c}_exact_n': f'pt_drg018_{c}_exact_n' for c in PRICE_CLASSES}}
    search = {row['site_id'] or row['ccn']: row for row in search_rows}
    differences = Counter()
    if set(search) != {row['search_site_id'] or row['ccn'] for row in rows}:
        differences['site_set'] += 1
    for row in rows:
        old = search.get(row['search_site_id'] or row['ccn'])
        if old is None:
            continue
        hospital, canadian = bool(row['ccn']), row['country'] == 'CAN'
        for new_name, old_name in pairs.items():
            if new_name == 'city' and not hospital:  # cities of sites without a CCN are project-written
                continue
            differences[new_name] += str(row[new_name]) != old[old_name]
        differences['ccn'] += (row['ccn'] or 'no CCN') != old['ccn']
        differences['state'] += (row['state'] or 'Canada') != old['state']
        differences['site_name'] += hospital and row['ccn'] not in NAME_OVERRIDES and row['site_name'] != clean_name(old['name'])  # other names are project-written
        for name in PRICE_CLASSES:  # the search table wrote zero masked rows as blank
            differences[f'pt_remit_{name}_masked_rows'] += str(row[f'pt_remit_{name}_masked_rows'] or '') != old[f'pt_drg018_{name}_rows_banded_1to10']
        for name in ('open_payments_cart_product_research_usd_2019_2025', 'open_payments_cart_product_or_text_research_usd_2019_2025'):
            differences[name] += row[name] != money(old[name])
        differences['hospital_site_type'] += hospital != (old['site_type'] == 'hospital')
    return {name: n for name, n in differences.items() if n}


def integer(value):
    return int(value) if str(value).strip() else 0


def headline(rows):
    """Numbers a README can quote, from the published columns only."""
    filled = lambda name: [r for r in rows if r[name] != '']
    in_set = [r for r in rows if str(r['in_cart_center_set']) == '1']
    out = {'sites': len(rows), 'center_set_hospitals': len(in_set),
           'other_hospitals': sum(r['site_type'] == 'hospital' for r in rows) - len(in_set),
           'site_types': dict(Counter(r['site_type'] for r in rows)),
           'medicare_file_status_2024': dict(Counter(r['medicare_file_status_2024'] for r in rows)),
           'medicare_file_status_2024_center_set': dict(Counter(r['medicare_file_status_2024'] for r in in_set)),
           'hospitals_with_medicare_count_any_year': sum(any(r[f'medicare_ffs_drg018_{y}'] != '' for y in MEDICARE_YEARS) for r in rows),
           'bmtinfonet_sites': sum(integer(r['on_bmtinfonet_directory']) for r in rows),
           'bmtinfonet_programs_matched': sum(integer(r['bmtinfonet_program_count']) for r in rows),
           'bmtinfonet_center_set_sites': sum(integer(r['on_bmtinfonet_directory']) for r in in_set),
           'bmtinfonet_2024_band_sites': dict(Counter(r['bmtinfonet_n_2024_band'] for r in filled('bmtinfonet_n_2024_band'))),
           'bmtinfonet_2024_band_center_set_sites': sum(r['bmtinfonet_n_2024_band'] != '' for r in in_set),
           'bmtinfonet_2024_partial_sum_sites': sum(0 < integer(r['bmtinfonet_programs_reporting_2024']) < integer(r['bmtinfonet_program_count']) for r in rows),
           'ny_sparcs_status': dict(Counter(r['ny_sparcs_status'] for r in rows)),
           'ca_hcai_status': dict(Counter(r['ca_hcai_status'] for r in rows)),
           'price_files_parsed': sum(r['pt_remit_status'] == 'file_parsed' for r in rows),
           'open_payments_research_status': dict(Counter(r['open_payments_research_status'] for r in rows)),
           'has_public_all_payer_count_sites': sum(integer(r['has_public_all_payer_count']) for r in rows),
           'has_public_all_payer_count_center_set': sum(integer(r['has_public_all_payer_count']) for r in in_set),
           'rows_with_related_site': sum(r['related_site_key'] != '' for r in rows),
           'rows_with_related_site_relation': sum(r['related_site_relation'] != '' for r in rows),
           'rows_with_ccn': sum(r['ccn'] != '' for r in rows),
           'other_hospitals_on_bmtinfonet_directory': sum(r['site_type'] == 'hospital' and str(r['in_cart_center_set']) != '1'
                                                          and str(r['on_bmtinfonet_directory']) == '1' for r in rows),
           'medicare_not_in_file_childrens_ccn_range': sum(r['medicare_file_status_2024'] == 'not_in_file'
                                                           and str(r['childrens_hospital_ccn_range']) == '1' for r in rows),
           'medicare_min_published_value': min(integer(r[f'medicare_ffs_drg018_{y}']) for r in rows for y in MEDICARE_YEARS
                                               if r[f'medicare_ffs_drg018_{y}'] != ''),
           'bmtinfonet_match_review_notes': sum(r['bmtinfonet_match_review_note'] != '' for r in rows),
           'bmtinfonet_duplicate_suspect_rows': sum(r['bmtinfonet_duplicate_suspect_site_key'] != '' for r in rows)}
    for name in ([f'medicare_ffs_drg018_{y}' for y in MEDICARE_YEARS] + STATE_COUNT_COLUMNS
                 + [f'ny_sparcs_aprdrg011_2024_{g}' for g in NY_GROUPS] + PRICE_COLUMNS):
        out[name] = {'sites': len(filled(name)), 'sum': sum(integer(r[name]) for r in rows)}
    for name in ('open_payments_cart_product_research_usd_2019_2025', 'open_payments_cart_product_or_text_research_usd_2019_2025'):
        cents = [round(float(r[name]) * 100) for r in filled(name)]
        out[name] = {'sites': len(cents), 'sites_above_zero': sum(c > 0 for c in cents), 'sum_usd': f'{sum(cents) / 100:.2f}'}
    return out


def local_numbers(rows):
    """Numbers that rest on restricted columns; for local use, not for a tracked README."""
    in_set = [r for r in rows if r['in_cart_center_set'] == 1]
    us = [r for r in rows if r['country'] == 'USA']
    both = [r for r in rows if r['bmtinfonet_n_2024'] != '' and r['medicare_ffs_drg018_2024'] != '']
    return {'kite_sites': sum(r['on_kite_atc_list'] for r in rows), 'bms_sites': sum(r['on_bms_center_list'] for r in rows),
            'sites_on_a_list': sum(r['n_lists'] > 0 for r in rows), 'center_set_on_a_list': sum(r['n_lists'] > 0 for r in in_set),
            'center_set_on_kite': sum(r['on_kite_atc_list'] for r in in_set), 'center_set_on_bms': sum(r['on_bms_center_list'] for r in in_set),
            'us_list_sites_outside_center_set': sum(r['n_lists'] > 0 and not r['in_cart_center_set'] for r in us),
            'rows_present_only_through_a_manufacturer_list': sum(
                not r['in_cart_center_set'] and not r['on_bmtinfonet_directory'] and r['ca_hcai_status'] != 'drg018_in_top25_table'
                and r['n_lists'] > 0 for r in rows),
            'population': dict(Counter(r['population'] for r in rows)),
            'has_any_all_payer_value_sites': sum(r['has_any_all_payer_value'] for r in rows),
            'has_any_all_payer_value_center_set': sum(r['has_any_all_payer_value'] for r in in_set),
            'bmtinfonet_sum_by_year_all_sites': {y: sum(integer(r[f'bmtinfonet_n_{y}']) for r in rows) for y in BMT_YEARS},
            'bmtinfonet_2024_us_sites': {'sites': sum(r['bmtinfonet_n_2024'] != '' for r in us), 'sum': sum(integer(r['bmtinfonet_n_2024']) for r in us)},
            'bmtinfonet_2024_center_set': {'sites': sum(r['bmtinfonet_n_2024'] != '' for r in in_set), 'sum': sum(integer(r['bmtinfonet_n_2024']) for r in in_set)},
            'medicare_2024_against_bmtinfonet_2024': {'sites': len(both), 'medicare': sum(integer(r['medicare_ffs_drg018_2024']) for r in both),
                                                      'bmtinfonet': sum(integer(r['bmtinfonet_n_2024']) for r in both)}}


SOURCES = [
    {'name': 'CAR-T center set (cart_centers)', 'publisher': 'this project', 'url': 'Data/processed/2026-10-24/cart_centers.csv', 'release_or_version': UNIVERSE_SNAPSHOT, 'retrieved': UNIVERSE_SNAPSHOT, 'how_obtained': 'tracked table of an earlier snapshot', 'terms_class': 'project'},
    {'name': 'Hospital Enrollments', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': 'https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/hospital-enrollments', 'release_or_version': '2026-09-29', 'retrieved': '2026-10-24', 'how_obtained': 'file download saved under Data/raw/2026-10-24/bulk', 'terms_class': 'a: federal public domain, attribution appreciated'},
    {'name': 'Medicare Inpatient Hospitals by Provider and Service, MS-DRG 018 rows', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': 'https://data.cms.gov/provider-summary-by-type-of-service/medicare-inpatient-hospitals/medicare-inpatient-hospitals-by-provider-and-service', 'release_or_version': 'data years 2021 to 2024', 'retrieved': '2026-11-19', 'how_obtained': 'API responses saved by the search, one per year', 'terms_class': 'a: federal public domain, attribution appreciated'},
    {'name': 'Medicare Inpatient Hospitals by Provider and Service, all rows of data year 2024', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': CMS_API, 'release_or_version': f'dataset version {CMS_VERSION} (modified 2026-04-23)', 'retrieved': '2026-11-19', 'how_obtained': 'paced API pull of three columns in pages of 5,000, raw pages saved under Data/raw/2026-11-19/cms_inpatient_2024', 'terms_class': 'a: federal public domain, attribution appreciated'},
    {'name': 'Hospital Inpatient Discharges (SPARCS De-Identified), APR-DRG 011 records', 'publisher': 'New York State Department of Health', 'url': 'https://health.data.ny.gov/resource/sf4k-39ay.json (2024) and https://health.data.ny.gov/resource/46xm-urtu.json (2023)', 'release_or_version': 'discharge years 2023 and 2024', 'retrieved': '2026-11-19', 'how_obtained': 'open data API, record-level rows saved by the search', 'terms_class': 'a: Open NY terms of use dated 2013-03-08, revocable licence'},
    {'name': 'Top 25 MS-DRGs Individual Hospital pivot profile, MS-DRG 018 lines', 'publisher': 'California Department of Health Care Access and Information', 'url': 'https://data.chhs.ca.gov/dataset/top-25-ms-drgs-individual-hospital-pivot-profile', 'release_or_version': '2024 and 2025 workbooks', 'retrieved': '2026-11-19', 'how_obtained': 'text extract of the workbook lines saved by the search; only the derived extract exists locally', 'terms_class': 'a: Creative Commons Attribution per the state catalog; portal terms page not read'},
    {'name': 'Hospital price transparency machine-readable files (7 hospitals)', 'publisher': 'each hospital, under 45 CFR 180.50', 'url': 'https://github.com/CMSgov/hospital-price-transparency (file template); files posted on each hospital site', 'release_or_version': 'CMS template 3.0.0, files dated 2026-03-23 to 2026-04-01 where recorded', 'retrieved': '2026-11-19', 'how_obtained': 'rows extracted by the first search pass; only the extracts exist locally', 'terms_class': 'b: mandated public files, no licence; derived counts only'},
    {'name': 'Open Payments research payments to teaching hospitals', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': 'https://openpaymentsdata.cms.gov', 'release_or_version': 'program years 2019 to 2025, publication 2026-06-30', 'retrieved': '2026-11-12', 'how_obtained': 'per-hospital table derived by the search from the bulk archives; only the derived table is read here', 'terms_class': 'a: federal public domain, attribution appreciated'},
    {'name': 'CAR T-cell Center Directory', 'publisher': 'BMT InfoNet', 'url': 'https://bmtinfonet.org/car-t-cell-therapy-center-directory-search', 'release_or_version': '277 records, no update date shown', 'retrieved': '2026-11-19', 'how_obtained': 'directory pages read by the search; matched records saved from the session', 'terms_class': 'b: all rights reserved, silent on reuse; flags, program counts, record numbers and a 2024 size band are tracked, exact counts and copied text stay local'},
    {'name': 'FACT accredited institutions listing', 'publisher': 'Foundation for the Accreditation of Cellular Therapy', 'url': 'https://accredited.factglobal.org/', 'release_or_version': 'as attached to cart_centers 2026-10-24', 'retrieved': '2026-10-24', 'how_obtained': 'through cart_centers; no new pull', 'terms_class': 'b: all rights reserved, silent on reuse; accreditation flags only'},
    {'name': 'Kite authorized treatment center locator file', 'publisher': 'Kite Pharma', 'url': 'https://www.kitepharma.com/-/media/atc-qac/getgaimatcdetails.json', 'release_or_version': 'not published', 'retrieved': '2026-11-19', 'how_obtained': 'file read by the search; matched records saved from the session', 'terms_class': 'c: terms limit reuse to own non-commercial use; nothing derived from it is tracked'},
    {'name': 'Bristol Myers Squibb CAR-T treatment center file', 'publisher': 'Bristol Myers Squibb', 'url': 'https://www.breyanzi.com/content/dam/commercial/treatmentdata.json', 'release_or_version': 'not published', 'retrieved': '2026-11-19', 'how_obtained': 'file read by the search; matched records saved from the session', 'terms_class': 'c: legal notice forbids public or commercial reuse; nothing derived from it is tracked'},
]
ROW_DEFINITION = ('One CAR-T treating site that a public source supports: a Medicare-enrolled hospital identified by its CCN (the '
                  '173 centers of cart_centers 2026-10-24, 43 other hospitals on the BMT InfoNet directory, and 2 California '
                  'hospitals that appear only in the state discharge file) or a site without a CCN that is on the BMT InfoNet '
                  'directory (10 physician practices, 1 federal hospital, 11 Canadian centers).')
LIMITATIONS = [
    'No column is lymphoma-specific: MS-DRG 018 and APR-DRG 011 include myeloma and leukemia CAR-T and other cell therapies, BMT InfoNet counts cover all indications, and Open Payments amounts include myeloma products.',
    'Columns come from different sources, years and definitions and overlap; do not add them across sources.',
    'Medicare columns are fee-for-service inpatient stays only: no Medicare Advantage, no outpatient infusions, nothing below 11, and nothing for hospitals absent from the file. Only the 2024 blanks are split by reason.',
    'State columns are inpatient stays by licensed facility mapped to a CCN by hand. California lists a hospital only when MS-DRG 018 is in one of its top-25 tables. The New York Medicare payer group includes Medicare Advantage. New York cells below 11 are published as the public record-level file gives them.',
    'Price-file columns count remittances over the 12 to 15 months before each file was posted (about January 2025 to March 2026), include partial and adjustment payments, and cover 7 hospitals parsed in the first search pass with a keyword payer rule. A masked row stands for 1 to 10 remittances; masked rows are counted as rows and never added to cases.',
    'BMT InfoNet counts are self-reported, unaudited and undefined (trial or commercial, infusions or patients). The tracked table carries a 2024 size band only; a band is a partial sum when fewer programs report than are matched.',
    'Directory membership records presence on a public list on 2026-11-19, not proof of treatment. Only BMT InfoNet membership is tracked. The search also reconciled two manufacturer locators; their flags stay local, and so do the rows of sites that only a locator lists, so the table is not a complete list of treating sites outside the center set.',
    'Rows are CCNs, not programs: the related_site columns name the rows that must be read together, and children\'s programs that share a hospital CCN are summed into that hospital row.',
    'Record-to-site matching used address, name similarity and hand overrides, checked by spot checks only; name similarity scores favour hospitals already in the center set. bmtinfonet_match_methods shows the method for each site.',
    'The 173-center set is unchanged. Eight set CCNs carry an accreditation that belongs wholly or partly to another site; they are flagged, not relabelled.',
]


def build():
    """Stage inputs, rebuild the panel, compare it with the search table, publish and write metadata."""
    staged = {name: stage(name) for name in [SEARCH_PANEL, MATCHED, HCAI_EXTRACT, PRICE_ROWS, RESEARCH, *PRESERVED,
                                             *PRICE_ROWS_MGB.values(),
                                             *[DRG_RESPONSE.format(year=y) for y in MEDICARE_YEARS],
                                             *[SPARCS_ROWS.format(year=y) for y in ('2023', '2024')]]}
    center_rows = centers()
    with ENROLLMENT.open(newline='', encoding='cp1252') as stream:
        enrollment = {row['CCN'].strip(): row for row in csv.DictReader(stream) if re.fullmatch(r'\d{6}', row['CCN'].strip())}
    records = load(staged[MATCHED])
    drg = {y: {row['Rndrng_Prvdr_CCN']: int(row['Tot_Dschrgs']) for row in load(staged[DRG_RESPONSE.format(year=y)])}
           for y in MEDICARE_YEARS}
    in_file, cart_2024, pull = inpatient_2024()
    assert cart_2024 == drg['2024'], 'The 2024 MS-DRG 018 rows of the full file differ from the saved response'
    ny = {y: sparcs_counts(load(staged[SPARCS_ROWS.format(year=y)])) for y in ('2023', '2024')}
    ca, ca_statewide = hcai_counts(staged[HCAI_EXTRACT].read_text(encoding='utf-8').splitlines())
    price = price_counts(read_csv(staged[PRICE_ROWS]),
                         {ccn: staged[name].read_text(encoding='utf-8').splitlines() for ccn, name in PRICE_ROWS_MGB.items()})
    research = {row['ccn']: row for row in read_csv(staged[RESEARCH])}
    search_rows = read_csv(staged[SEARCH_PANEL])
    notes = {row['site_id'] or row['ccn']: (row['linked_site_note'], row['set_registration_flag']) for row in search_rows
             if row['linked_site_note'] or row['set_registration_flag']}
    rows = build_rows(center_rows, enrollment, records, drg, in_file, ny, ca, price, research, notes)

    differences = reconcile(rows, search_rows)
    assert not differences, f'Rebuilt table differs from the search table: {differences}'
    for name in PRICE_COLUMNS_NOT_PUBLISHED:
        assert not any(row.pop(name) for row in rows), f'{name} is no longer empty'
    tracked, held = split_rows(rows)
    assert not [k for k in list(RELATED) + list(BMT_MATCH_REVIEW) if k not in {row['site_key'] for row in tracked}]
    # The tracked table must come out the same when no manufacturer record is read at all.
    blind = build_rows(center_rows, enrollment, [r for r in records if r['list'] == 'bmt'], drg, in_file, ny, ca, price, research, {})
    assert [{n: r[n] for n in PUBLIC} for r in blind] == [{n: r[n] for n in PUBLIC} for r in tracked], 'Tracked values depend on a manufacturer list'
    summary = publish(TABLE, tracked, COLUMNS, ['site_key'])
    write_csv(RESTRICTED / HELD_ROWS_FILE, held, [c['column'] for c in COLUMNS])
    dump(INTERIM / MODULE / 'local_only_numbers.json', local_numbers(rows))
    errors = {name: n for name, n in check(OUT.parent).items() if n}
    assert not errors, f'Checks failed: {errors}; fingerprints of the table just written: {fingerprints(read_csv(OUT / f"{TABLE}.csv"))}'

    published = read_csv(OUT / f'{TABLE}.csv')
    bmt_methods = dict(Counter(r['how'] for r in records if r['list'] == 'bmt'))
    renamed = sum(clean_name(row['name']) != row['name'] for row in search_rows)
    medicare_other = {ccn: tally['medicare_other'][0] for ccn, tally in price.items() if tally['medicare_other'][0]}
    metadata = {
        'tables': [summary], 'row_definition': ROW_DEFINITION, 'sources': SOURCES, 'headline': headline(published), 'limitations': LIMITATIONS,
        'bmtinfonet_records_by_match_method': bmt_methods,
        'center_set_snapshot': UNIVERSE_SNAPSHOT,
        'california_statewide_msdrg018_discharges': ca_statewide,
        'medicare_inpatient_2024_pull': pull,
        'reconciliation_with_search_table': {'cells_different': 0, 'note': 'Every rebuilt column was compared with all_payer_center_panel.csv on all 267 rows of the search table before renaming, before the fixes listed under defects_fixed and before rows were held back.'},
        'rows_removed': [{'reason': f'The site is in the search table only because a manufacturer center locator lists it (not in the center set, not on the BMT InfoNet directory, not in a state discharge file). The locator terms restrict reuse, so the whole row is written to Data/interim/{SNAPSHOT}/restricted_terms/{HELD_ROWS_FILE} and to no tracked file.', 'rows': len(held)}],
        'tracked_table_unchanged_without_manufacturer_records': True,
        'inputs_read_as_derived_tables_only': [RESEARCH, HCAI_EXTRACT, PRICE_ROWS, *PRICE_ROWS_MGB.values(), MATCHED,
                                               f'{SEARCH_PANEL} (two hand-note columns)'],
        'price_file_secondary_medicare_remittances_not_in_any_column': medicare_other,
        'defects_fixed': [
            'One filled site_key on every row: the CCN for hospitals, a slug for the other rows; ccn is blank, not the text "no CCN", on those rows.',
            f'{len(held)} rows that exist only because a manufacturer locator lists the site are kept out of the tracked table; the tracked table is rebuilt without any manufacturer record and must come out identical.',
            'Numbers that rest on restricted columns are written to a local file, not to the metadata that is tracked.',
            'state no longer holds the word Canada: it is blank on the 11 Canadian rows and a country column is added; the one blank Canadian city is filled (Toronto) and Canadian names are written by this project.',
            f'{renamed} hospital names repaired (an apostrophe stored as an inverted question mark for 363302, a pipe for 190064).',
            'site_type values are single tokens without slashes or parentheses.',
            'medicare_file_status_2024 added from a pull of the whole 2024 file, so a blank is no longer ambiguous between suppression and absence from the file.',
            'Status columns added for the New York, California, price-file and Open Payments blanks.',
            'Sibling pointers added (related_center_ccn, related_site_key, related_site_relation, related_site_note) for the eight misattached set CCNs, the two split programs and their counterparts.',
            'The suspected double count of one Novant Health program on two directory pages is flagged on both rows (bmtinfonet_duplicate_suspect_site_key).',
            'bmtinfonet_programs_reporting_2024 added so a partial directory sum can be told from a complete one.',
            'Price-file columns renamed as remittance counts; masked-row columns are 0, not blank, at a parsed hospital with no such row; the two columns empty on every row and the one-value note column are dropped.',
            'The New York other-payer column is counted from the records instead of by subtraction.',
            'Columns dropped as the specification asks: fact_iec_in_set (redundant), list_names (cut at 400 characters), and the 340B columns (wrong on 18 rows; published corrected in cart_center_340b_status).',
            'Open Payments dollars are written to cents.',
        ],
        'defects_open': [
            'Eight center-set CCNs are attached to the wrong or a partial sibling site (340166, 490101, 390114, 380004, 490007, 390050, 340014, 440161). They are flagged through the related_site columns; the set and the division labels are not changed here.',
            'The BMT InfoNet records of 340014 and 340053 show identical counts in all five years and may be one program counted twice; flagged, not confirmed with the source.',
            'BMT InfoNet sums are partial where one matched program reports and another does not; bmtinfonet_programs_reporting_2024 shows it for 2024.',
            f'Matching: four BMT InfoNet assignments are judgement calls (bmtinfonet_match_review_note). Of the 277 directory records, {bmt_methods.get("fuzzy", 0)} were matched by name similarity and {bmt_methods.get("manual", 0)} by hand, verified by spot checks only.',
            'Price-file columns come from the first search pass (7 hospitals) with a keyword payer rule that knows traditional Medicare from one payer code only; the later 48-hospital sweep uses other payer classes and is not merged. The Stanford value was not re-read after the hospital re-posted its file on 2026-11-11.',
            'Medicare blanks of 2021 to 2023 are not split by reason; only 2024 was checked against the whole file.',
            'city is the enrollment address city of the CMS file (370093 shows EDMOND); two hospitals share the name METHODIST HOSPITAL (450388, 280040).',
            'The inferred population column (local only) is unreliable: any manufacturer record yields adult, including at children\'s hospitals.',
            'cart_centers 2026-10-24 carries wrong doing-business-as names for 050025 and 140088; this table overrides them, the center table is unchanged.',
            'The matched list records are a session intermediate frozen under Data/raw/2026-11-19; the matching itself (scripts in the notes folder) was not rerun.',
        ],
    }
    inputs = list(staged.values()) + [ENROLLMENT, CMS_RAW / 'complete.json', CMS_RAW / 'stats.json',
                                      *sorted(p for p in CMS_RAW.glob('page_*.json'))]
    write_module_metadata(MODULE, metadata, inputs)
    print('CENTER PANEL', summary['rows'], 'ROWS', summary['columns'], 'PUBLIC COLUMNS', len(summary['restricted_columns']),
          'LOCAL COLUMNS', len(held), 'ROWS KEPT LOCAL', 'MEDICARE 2024', metadata['headline']['medicare_file_status_2024'], flush=True)


FINGERPRINT_PREFIXES = ['related_', 'bmtinfonet_', 'medicare_', 'ny_sparcs_', 'ca_hcai_', 'pt_remit_', 'open_payments_']


def fingerprints(rows):
    """Short digest of each column group over site_key and values, so a value moved to another row is noticed."""
    groups = defaultdict(list)
    for name in PUBLIC:
        group = next((prefix.rstrip('_') for prefix in FINGERPRINT_PREFIXES if name.startswith(prefix)),
                     'bmtinfonet' if name == 'on_bmtinfonet_directory' else 'site')
        groups[group].append(name)
    ordered = sorted(rows, key=lambda r: r['site_key'])
    return {group: hashlib.sha256(json.dumps([[r['site_key']] + [str(r[n]) for n in names] for r in ordered],
                                             ensure_ascii=False).encode()).hexdigest()[:16]
            for group, names in groups.items()}


# Totals and digests of the table as published; a refresh of any input needs them updated on purpose.
EXPECTED = {
    'rows': 240,
    'site_types': {'hospital': 218, 'physician_practice': 10, 'federal_hospital': 1, 'non_us_center': 11},
    'medicare': {'2021': (23, 544), '2022': (37, 981), '2023': (46, 1303), '2024': (49, 1360)},
    'medicare_status': {'count_published': 49, 'in_file_no_drg018_row': 121, 'not_in_file': 48, 'no_ccn': 22},
    'new_york': {'ny_sparcs_aprdrg011_all_payer_2023': (10, 483), 'ny_sparcs_aprdrg011_all_payer_2024': (10, 557),
                 'ny_sparcs_aprdrg011_2024_medicare': (10, 258), 'ny_sparcs_aprdrg011_2024_medicaid': (10, 66),
                 'ny_sparcs_aprdrg011_2024_commercial': (10, 219), 'ny_sparcs_aprdrg011_2024_other': (10, 14)},
    'california': {'ca_hcai_msdrg018_all_payer_2024': (16, 738), 'ca_hcai_msdrg018_all_payer_2025': (19, 921)},
    'price': {'pt_remit_medicare_ffs_exact_n': (2, 91), 'pt_remit_ma_exact_n': (1, 24), 'pt_remit_commercial_exact_n': (3, 59),
              'pt_remit_ma_masked_rows': (7, 27), 'pt_remit_commercial_masked_rows': (7, 42), 'pt_remit_medicaid_masked_rows': (7, 17)},
    'price_files_parsed': 7,
    # filled rows, rows above zero, dollars in cents
    'open_payments': {'open_payments_cart_product_research_usd_2019_2025': (197, 66, 9538396176),
                      'open_payments_cart_product_or_text_research_usd_2019_2025': (197, 95, 24951907335)},
    'public_all_payer_flag': (34, 31),
    'bmtinfonet': {'sites': 229, 'programs': 277, 'band_sites': 179,
                   'bands': {'0': 15, '1-10': 53, '11-25': 33, '26-50': 26, '51-100': 36, '101-200': 13, '201+': 3}},
    'fingerprints': {'site': '4e9e18fdc021bc83', 'related': '7be7fdb0af533f92', 'bmtinfonet': '7201d30442007cbd',
                     'medicare': '9f2a063c0ce92ea9', 'ny_sparcs': '039d58584119b744', 'ca_hcai': '2dd7deba6fc35a1c',
                     'pt_remit': '036c7912a87a1554', 'open_payments': '71f1cde904a8cf28'},
}
PRICE_STATUSES = {'file_parsed', 'file_not_parsed', 'no_ccn'}
PAYMENT_STATUSES = {'published_records', 'no_published_records', 'no_ccn'}


def count_sum(rows, name):
    return sum(r[name] != '' for r in rows), sum(integer(r[name]) for r in rows)


def check(processed):
    """Error counts for the published table; reads only files under `processed`."""
    path = processed / '2026-11-19' / f'{TABLE}.csv'
    if not path.exists():
        return {f'{MODULE}_table_missing': 1}
    rows = read_csv(path)
    header = list(rows[0]) if rows else []
    rows = [{**dict.fromkeys(PUBLIC, ''), **row} for row in rows]  # a missing column is counted once, below
    if not (processed / UNIVERSE_SNAPSHOT / 'cart_centers.csv').exists():
        return {f'{MODULE}_center_table_missing': 1}
    center_rows = {r['ccn']: r for r in read_csv(processed / UNIVERSE_SNAPSHOT / 'cart_centers.csv')}
    keys = [r['site_key'] for r in rows]
    by_key = {r['site_key']: r for r in rows}
    in_set = [r for r in rows if r['in_cart_center_set'] == '1']
    out = {'table_missing': 0, 'center_table_missing': 0}
    out['row_count'] = int(len(rows) != EXPECTED['rows'])
    out['rows_without_public_basis'] = sum(not public_basis(r) for r in rows)
    out['missing_columns'] = len([name for name in PUBLIC if name not in header])
    out['restricted_or_unknown_columns_present'] = len([name for name in header if name in FORBIDDEN or name not in PUBLIC
                                                        or re.search(r'kite|bms|340b', name)])
    out['key_errors'] = sum(not k.strip() for k in keys) + len(keys) - len(set(keys))
    out['identifier_errors'] = sum(
        (r['site_type'] == 'hospital') != bool(re.fullmatch(r'\d{6}', r['ccn']))
        or (r['ccn'] != '' and r['ccn'] != r['site_key'])
        or (r['ccn'] == '' and not re.fullmatch(r'[a-z]+(-[a-z0-9]+)+', r['site_key']))
        or r['site_type'] not in EXPECTED['site_types']
        or (r['site_type'] == 'non_us_center') != (r['country'] != 'USA')
        or (r['country'] == 'USA') != bool(re.fullmatch(r'[A-Z]{2}', r['state']))
        or r['childrens_hospital_ccn_range'] != str(childrens_ccn(r['ccn']))
        for r in rows)
    out['site_type_counts'] = int(dict(Counter(r['site_type'] for r in rows)) != EXPECTED['site_types'])
    out['blank_required_values'] = sum(r[name] == '' for r in rows for name in (
        'site_name', 'city', 'country', 'site_type', 'in_cart_center_set', 'on_bmtinfonet_directory', 'bmtinfonet_program_count',
        'bmtinfonet_fact_approved', 'bmtinfonet_programs_reporting_2024', 'medicare_file_status_2024', 'ny_sparcs_status',
        'ca_hcai_status', 'pt_remit_status', 'open_payments_research_status', 'has_public_all_payer_count'))
    out['text_errors'] = sum(bool(re.search(r'\||¿|@', value)) or value in ('no CCN', 'Canada') for r in rows for value in r.values())
    out['center_set_mismatch'] = len({r['ccn'] for r in in_set} ^ set(center_rows)) + sum(r['site_type'] != 'hospital' for r in in_set)
    out['center_fields_mismatch'] = sum(
        (r['set_evidence'], r['core_hcps_affiliated']) != ((center_rows[r['ccn']]['evidence'], center_rows[r['ccn']]['core_hcps_affiliated'])
                                                           if r['ccn'] in center_rows else ('', ''))
        or (r['in_cart_center_set'] == '1') != (r['ccn'] in center_rows) for r in rows)
    out['medicare_2024_vs_center_table'] = sum(
        r['medicare_ffs_drg018_2024'] != center_rows[r['ccn']]['drg018_discharges_2024']
        or (r['medicare_ffs_drg018_2024'] != '') != ('drg018_2024' in r['set_evidence'].split(';'))
        for r in rows if r['ccn'] in center_rows)
    out['medicare_cell_errors'] = sum(
        r[f'medicare_ffs_drg018_{y}'] != '' and (integer(r[f'medicare_ffs_drg018_{y}']) < 11 or r['in_cart_center_set'] != '1')
        for r in rows for y in MEDICARE_YEARS)
    out['medicare_totals'] = sum(count_sum(rows, f'medicare_ffs_drg018_{y}') != expected for y, expected in EXPECTED['medicare'].items())
    out['medicare_file_status_errors'] = sum(
        (r['medicare_file_status_2024'] == 'count_published') != (r['medicare_ffs_drg018_2024'] != '')
        or (r['medicare_file_status_2024'] == 'no_ccn') != (r['ccn'] == '')
        or r['medicare_file_status_2024'] not in EXPECTED['medicare_status'] for r in rows)
    out['medicare_file_status_counts'] = int(dict(Counter(r['medicare_file_status_2024'] for r in rows)) != EXPECTED['medicare_status'])
    split = [f'ny_sparcs_aprdrg011_2024_{g}' for g in NY_GROUPS]
    out['new_york_errors'] = sum(
        (r['ny_sparcs_aprdrg011_all_payer_2024'] != '') != all(r[name] != '' for name in split)
        or any(r[name] != '' for name in split) and sum(integer(r[name]) for name in split) != integer(r['ny_sparcs_aprdrg011_all_payer_2024'])
        or (r['ny_sparcs_status'] == 'discharges_in_file') != any(r[f'ny_sparcs_aprdrg011_all_payer_{y}'] != '' for y in ('2023', '2024'))
        or (r['ny_sparcs_status'] == 'outside_new_york') != (r['state'] != 'NY')
        or (r['ny_sparcs_status'] == 'in_state_no_ccn') != (r['state'] == 'NY' and r['ccn'] == '')
        or r['ny_sparcs_aprdrg011_all_payer_2023'] == '0' or r['ny_sparcs_aprdrg011_all_payer_2024'] == '0' for r in rows)
    out['new_york_totals'] = sum(count_sum(rows, name) != expected for name, expected in EXPECTED['new_york'].items())
    out['california_errors'] = sum(
        (r['ca_hcai_status'] == 'drg018_in_top25_table') != any(r[f'ca_hcai_msdrg018_all_payer_{y}'] != '' for y in ('2024', '2025'))
        or (r['ca_hcai_status'] == 'outside_california') != (r['state'] != 'CA')
        or (r['ca_hcai_status'] == 'in_state_no_ccn') != (r['state'] == 'CA' and r['ccn'] == '')
        or r['ca_hcai_msdrg018_all_payer_2024'] == '0' or r['ca_hcai_msdrg018_all_payer_2025'] == '0' for r in rows)
    out['california_totals'] = sum(count_sum(rows, name) != expected for name, expected in EXPECTED['california'].items())
    masked = [name for name in PRICE_COLUMNS if name.endswith('masked_rows')]
    exact = [name for name in PRICE_COLUMNS if name.endswith('exact_n')]
    out['price_file_errors'] = sum(
        (r['pt_remit_status'] == 'file_parsed') != all(r[name] != '' for name in masked)
        or r['pt_remit_status'] != 'file_parsed' and any(r[name] != '' for name in exact)
        or any(r[name] != '' and integer(r[name]) < 11 for name in exact)
        or (r['pt_remit_status'] == 'no_ccn') != (r['ccn'] == '') or r['pt_remit_status'] not in PRICE_STATUSES for r in rows)
    out['price_file_totals'] = (sum(count_sum(rows, name) != expected for name, expected in EXPECTED['price'].items())
                                + int(sum(r['pt_remit_status'] == 'file_parsed' for r in rows) != EXPECTED['price_files_parsed']))
    product, text = 'open_payments_cart_product_research_usd_2019_2025', 'open_payments_cart_product_or_text_research_usd_2019_2025'
    out['open_payments_errors'] = sum(
        (r[product] == '') != (r[text] == '') or (r['open_payments_research_status'] == 'published_records') != (r[product] != '')
        or (r['open_payments_research_status'] == 'no_ccn') != (r['ccn'] == '')
        or r['open_payments_research_status'] not in PAYMENT_STATUSES
        or any(r[name] != '' and not re.fullmatch(r'\d+\.\d\d', r[name]) for name in (product, text))
        or r[product] != '' and r[text] != '' and not 0 <= float(r[product]) <= float(r[text]) for r in rows)
    out['open_payments_totals'] = sum(
        (sum(r[name] != '' for r in rows), sum(r[name] != '' and float(r[name]) > 0 for r in rows),
         sum(round(float(r[name]) * 100) for r in rows if r[name] != '')) != expected
        for name, expected in EXPECTED['open_payments'].items())
    out['bmtinfonet_errors'] = sum(
        (r['on_bmtinfonet_directory'] == '1') != (integer(r['bmtinfonet_program_count']) > 0)
        or len([i for i in r['bmtinfonet_directory_ids'].split(';') if i]) != integer(r['bmtinfonet_program_count'])
        or integer(r['bmtinfonet_programs_reporting_2024']) > integer(r['bmtinfonet_program_count'])
        or (r['bmtinfonet_n_2024_band'] != '') != (integer(r['bmtinfonet_programs_reporting_2024']) > 0)
        or r['bmtinfonet_n_2024_band'] not in BAND_LABELS + [''] or r['bmtinfonet_n_2024_nonpediatric_band'] not in BAND_LABELS + ['']
        or r['bmtinfonet_n_2024_nonpediatric_band'] != '' and (
            r['bmtinfonet_n_2024_band'] == ''
            or BAND_LABELS.index(r['bmtinfonet_n_2024_nonpediatric_band']) > BAND_LABELS.index(r['bmtinfonet_n_2024_band']))
        or r['on_bmtinfonet_directory'] != '1' and (r['bmtinfonet_fact_approved'] == '1' or r['bmtinfonet_match_methods'] != ''
                                                    or r['bmtinfonet_match_review_note'] != '')
        or r['bmtinfonet_duplicate_suspect_site_key'] != '' and by_key.get(r['bmtinfonet_duplicate_suspect_site_key'], {}).get(
            'bmtinfonet_duplicate_suspect_site_key') != r['site_key']
        for r in rows)
    ids = [i for r in rows for i in r['bmtinfonet_directory_ids'].split(';') if i]
    out['bmtinfonet_totals'] = int({
        'sites': sum(r['on_bmtinfonet_directory'] == '1' for r in rows), 'programs': len(set(ids)) if len(ids) == len(set(ids)) else -1,
        'band_sites': sum(r['bmtinfonet_n_2024_band'] != '' for r in rows),
        'bands': dict(Counter(r['bmtinfonet_n_2024_band'] for r in rows if r['bmtinfonet_n_2024_band'] != ''))} != EXPECTED['bmtinfonet'])
    out['public_all_payer_flag_errors'] = sum(r['has_public_all_payer_count'] != str(has_public_all_payer_count(r)) for r in rows)
    out['public_all_payer_flag_totals'] = int((sum(r['has_public_all_payer_count'] == '1' for r in rows),
                                               sum(r['has_public_all_payer_count'] == '1' for r in in_set)) != EXPECTED['public_all_payer_flag'])
    out['related_site_errors'] = sum(related_errors(r, by_key) for r in rows)
    other = processed / '2026-11-19' / 'cart_center_340b_status.csv'
    other_rows = read_csv(other) if other.exists() else []
    out['340b_table_ccns_missing_from_panel'] = sum(r['ccn'] not in by_key for r in other_rows if 'ccn' in r)
    # Same Open Payments amounts as the teaching-hospital table of this snapshot, which zero-fills where this table is blank.
    other = processed / '2026-11-19' / 'cart_center_teaching_hospital_payments_2019_2025.csv'
    out['open_payments_vs_teaching_hospital_table'] = sum(
        payments_differ(by_key[t['ccn']], t) for t in (read_csv(other) if other.exists() else []) if t.get('ccn') in by_key)
    written = {**US_SITES_WITHOUT_CCN, **CANADIAN_CENTERS}
    out['site_without_ccn_not_named_by_project'] = sum(
        (r['site_name'], r['city']) != written.get(r['bmtinfonet_directory_ids']) for r in rows if r['ccn'] == '')
    found = fingerprints(rows)
    out['column_fingerprint_mismatch'] = sum(found[group] != EXPECTED['fingerprints'].get(group) for group in found)
    return {f'{MODULE}_{name}': int(n) for name, n in out.items()}


def payments_differ(row, other):
    """1 when a panel row and the teaching-hospital payments row of the same CCN disagree on status or dollars."""
    pairs = [('open_payments_cart_product_research_usd_2019_2025', 'cart_product_research_usd_2019_2025'),
             ('open_payments_cart_product_or_text_research_usd_2019_2025', 'cart_product_or_text_research_usd_2019_2025')]
    if 'research_data_status' not in other or any(theirs not in other for _, theirs in pairs):
        return 0
    if row['open_payments_research_status'] != other['research_data_status']:
        return 1
    return int(any(row[ours] != '' and round(float(row[ours]) * 100) != round(float(other[theirs] or 0) * 100) for ours, theirs in pairs))


def related_errors(row, by_key):
    """1 when a row's pointers to related rows are malformed, dangling or not returned."""
    keys = [k for k in row['related_site_key'].split(';') if k]
    relation = row['related_site_relation']
    return int(
        (relation == '') != (row['related_site_note'] == '') or relation not in set(RELATIONS) | {ALONE, ''}
        or bool(keys) != (relation in RELATIONS)
        or row['related_center_ccn'] != ';'.join(k for k in keys if k.isdigit())
        or any(k not in by_key or row['site_key'] not in by_key[k]['related_site_key'].split(';')
               or by_key[k]['related_site_relation'] != RELATIONS.get(relation) for k in keys))


if __name__ == '__main__':
    build()
