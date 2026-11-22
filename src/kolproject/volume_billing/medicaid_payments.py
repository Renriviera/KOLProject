"""Medicaid paid amounts naming universe HCPs as servicing or billing provider, 2018-2024.

Source: the U.S. Department of Health and Human Services (HHS) Open Data file 'Medicaid Provider
Spending by HCPCS', release 2026-02-09, one row per billing NPI x servicing NPI x procedure code x
month, released only when the cell has at least 12 claim lines and at least 12 beneficiaries. The
search of 2026-11-19 filtered that file remotely to rows naming one of the 17,510 universe NPIs on
either side; this module freezes the month-level extract, sums it in whole cents, and publishes an
NPI x year table, an NPI feature table, a division x year summary and physician-to-center billing
links. A blank always means that no released row exists; it is never written as zero.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import os
import re
import statistics
import urllib.request
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .base import (DIVISION_GROUPS, INTERIM, LABEL_VERSION, OUT, RAW, ROOT, SNAPSHOT, UNIVERSE_SNAPSHOT, centers, column,
                   dump, duplicates, load, now, publish, read_csv, sha, stage, universe, write_module_metadata)

MODULE = 'medicaid_payments'
TABLES = ['hcp_medicaid_payments_by_year_2018_2024', 'hcp_medicaid_payment_features_2018_2024',
          'medicaid_payments_by_division_year_2018_2024', 'hcp_medicaid_center_billing_links_2018_2024']
YEARS = [str(year) for year in range(2018, 2025)]
SIDES = {'servicing': 'billing', 'billing': 'servicing'}  # side an NPI is read on -> the other NPI of the row
HERE = INTERIM / MODULE
HEADERS = RAW.parent / 'hhs_medicaid'
PROBE = Path('gap-medicaid-and-multiyear-payer-payments-by-division')
# Folder of the second-pass re-pull (month-level spending rows, uncollapsed enrollment segments, saved dataset
# pages). It is read only the first time, to freeze those files under Data/raw; later builds use the frozen copies.
SECOND_PASS = os.environ.get('KOL_MEDICAID_SECOND_PASS', '')
SPENDING_URL = ('https://stopendataprod.blob.core.windows.net/datasets/medicaid-provider-spending/2026-02-09/'
                'dataset/medicaid-provider-spending.parquet')
ENROLLMENT_URL = ('https://stopendataprod.blob.core.windows.net/datasets/medicaid-provider-enrollment-segments/'
                  '2026-07-24/dataset/medicaid-provider-enrollment-segments.parquet')
# What the search recorded for the two source files on 2026-11-19; build() compares its own header read to these.
RECORDED = {
    'spending': {'release': '2026-02-09', 'http_etag': '0x8DE93E6CBECD570', 'bytes': 3072545478,
                 'http_last_modified': 'Mon, 06 Apr 2026 14:14:20 GMT', 'source_rows': 238015729,
                 'publisher_stated_sha256': 'f646d426126638dd0177cb14a86bfe7d7e9ed6b081a3e1c270bfe5070fb3fb94'},
    'enrollment': {'release': '2026-07-24', 'http_etag': '0x8DEE9A30127E58E', 'bytes': 195945074,
                   'http_last_modified': 'Fri, 24 Jul 2026 16:45:44 GMT', 'source_rows': 65970112,
                   'publisher_stated_sha256': 'c763c53946e5f8822e46db47d2cbd0de09c153c6b7045b57504c461afa2ce92a'},
}

SOURCE_FACTS = {
    'spending': [
        'One row per billing NPI x servicing NPI x procedure code x month, January 2018 to December 2024; the month is the claim line\'s through date, or its from date when there is none.',
        'Built from finalized claims paid within the reporting period: outpatient and professional claim lines with a populated code. Inpatient claims are not in the file.',
        'Medicaid and CHIP, fee-for-service claims and managed-care encounters together, with no flag to separate them.',
        'The paid amount is the sum of claim-line paid amounts, with a missing line amount counted as $0; it is not billed charges.',
        'A row is released only with at least 12 claim lines and at least 12 unique beneficiaries.',
        'There is no state column. 2024 is stated to be preliminary. Whether denied claim lines are included is not stated.',
    ],
    'enrollment': [
        'One row per provider-state enrollment segment overlapping January 2018 to December 2024, with no guaranteed unique key.',
        'The NPI is one current NPI chosen per provider and state, blank when none qualifies; every New Mexico and Oregon row has a blank NPI.',
        'A blank end date means open-ended; end dates include far-future years and start dates go back to 0001-01-01.',
    ],
}

# Code classes exactly as the search defined them, so its 2024 figures reproduce. The first constant is the
# search's rule as written (DuckDB SQL over the code column); code_subclass() below is the same rule in Python.
CODE_SUBCLASS_RULE_SQL = """case when hcpcs in ('99202','99203','99204','99205','99211','99212','99213','99214','99215') then 'em_office'
     when hcpcs in ('99221','99222','99223','99231','99232','99233') then 'em_hospital'
     when (hcpcs between '96360' and '96379' and length(hcpcs)=5) or (hcpcs between '96401' and '96417' and length(hcpcs)=5) then 'admin'
     when hcpcs in ('J9312','Q5115','Q5119','Q5123','J9311','J9301','J9033','J9034','J9036','J9042','J9309','J9349','J9359','J9321','J9286','J9350','J9271','J9299','J9144','J9145','J0897') then 'drug_listed'
     when hcpcs in ('J3490','J3590','J9999','C9399') then 'drug_unclassified'
     when regexp_matches(hcpcs,'^(J[0-9]{4}|Q5[0-9]{3}|Q20[0-9]{2}|Q013[89]|Q008[1-5]|C9[0-9]{3}|S0[0-9]{3})$') and hcpcs not in ('Q0081','Q0083','Q0084','Q0085') then 'drug_other_jq'
     when hcpcs='38241' then 'transplant_38241'
     else 'other' end"""
CODE_CLASS_RULE_SQL = """case when cls in ('em_office','em_hospital','admin') then 'service'
     when cls in ('drug_listed','drug_unclassified','drug_other_jq') then 'drug' else 'other' end"""
CLASS_RULES = {
    'visit_admin': 'A procedure code is in the visit_admin class when it is one of 99202, 99203, 99204, 99205, 99211, '
                   '99212, 99213, 99214, 99215 (office visits), 99221, 99222, 99223, 99231, 99232, 99233 (hospital '
                   'visits), or a five-character code that sorts from 96360 to 96379 or from 96401 to 96417 '
                   '(infusion, injection and chemotherapy administration). The search called this class service.',
    'drug_code': 'A procedure code that is not in the visit_admin class is in the drug_code class when it matches '
                 'J plus four digits, Q5 plus three digits, Q20 plus two digits, C9 plus three digits, S0 plus three '
                 'digits, or is Q0138, Q0139 or Q0082. Q0081, Q0083, Q0084 and Q0085 are excluded. The search called '
                 'this class drug.',
    'other': 'Every other value of the code field, including code 38241.',
}
OFFICE = {'99202', '99203', '99204', '99205', '99211', '99212', '99213', '99214', '99215'}
HOSPITAL = {'99221', '99222', '99223', '99231', '99232', '99233'}
LISTED = {'J9312', 'Q5115', 'Q5119', 'Q5123', 'J9311', 'J9301', 'J9033', 'J9034', 'J9036', 'J9042', 'J9309', 'J9349',
          'J9359', 'J9321', 'J9286', 'J9350', 'J9271', 'J9299', 'J9144', 'J9145', 'J0897'}
UNCLASSIFIED = {'J3490', 'J3590', 'J9999', 'C9399'}
DRUG_PATTERN = re.compile(r'J[0-9]{4}|Q5[0-9]{3}|Q20[0-9]{2}|Q013[89]|Q008[1-5]|C9[0-9]{3}|S0[0-9]{3}')
NOT_DRUG = {'Q0081', 'Q0083', 'Q0084', 'Q0085'}
CLASS_OF = {'em_office': 'visit_admin', 'em_hospital': 'visit_admin', 'admin': 'visit_admin',
            'drug_listed': 'drug_code', 'drug_unclassified': 'drug_code', 'drug_other_jq': 'drug_code',
            'transplant_38241': 'other', 'other': 'other'}
ACTIVE_FROM = '2024-01-01'
STATE_PLANS = {'MEDICAID', 'CHIP'}

# Every headline number, as recomputed by headline() from the published rows on 2026-11-19; they include the figures the
# search report quotes. check() fails when the published rows stop reproducing any of them.
EXPECTED = {
    'universe_npis': 17510, 'npi_year_rows': 54745, 'npis_observed': 11602, 'servicing_npis_any_year': 11586,
    'servicing_npis_2024': 8454, 'billing_npis_any_year': 1877, 'billing_npis_2024': 1043,
    'self_billed_npis_any_year': 1847, 'billing_only_npis': 16, 'servicing_npis_2024_paid_exactly_zero': 69,
    'servicing_npis_any_year_paid_exactly_zero': 59, 'npis_observed_not_in_enrollment_file': 18,
    'servicing_paid_usd_2018_2024': '1593841130.66', 'servicing_visit_admin_paid_usd_2018_2024': '1003630100.88',
    'servicing_drug_code_paid_usd_2018_2024': '209447168.63', 'billing_paid_usd_2018_2024': '114882488.00',
    'self_billed_paid_usd_2018_2024': '109915335.19', 'servicing_source_rows_2018_2024': 1591435,
    'servicing_claim_lines_2018_2024': 55150053,
    'servicing_npis_by_year': {'2018': 6275, '2019': 6885, '2020': 7289, '2021': 8217, '2022': 8664, '2023': 8876, '2024': 8454},
    'servicing_paid_usd_by_year': {'2018': '158372079.70', '2019': '216441404.32', '2020': '228950200.08', '2021': '246991878.84', '2022': '265347058.36', '2023': '266150065.67', '2024': '211588443.69'},
    'billing_source_rows_2018_2024': 121510, 'billing_claim_lines_2018_2024': 4252830,
    'billing_npis_by_year': {'2018': 803, '2019': 929, '2020': 991, '2021': 1071, '2022': 1100, '2023': 1109, '2024': 1043},
    'billing_paid_usd_by_year': {'2018': '12059496.04', '2019': '15746919.04', '2020': '16227172.52', '2021': '18110044.35', '2022': '18260243.47', '2023': '18764734.91', '2024': '15713877.67'},
    'affiliates_2024_npis_in_group': 9548, 'affiliates_2024_npis_present': 3895,
    'affiliates_2024_npis_with_visit_admin_rows': 3416, 'affiliates_2024_npis_with_visit_admin_paid': 3396,
    'affiliates_2024_npis_with_drug_code_rows': 360, 'affiliates_2024_npis_with_drug_code_paid': 315,
    'affiliates_2024_paid_total_whole_usd': 70004014, 'affiliates_2024_paid_visit_admin_whole_usd': 49070242,
    'affiliates_2024_paid_drug_code_whole_usd': 4194695, 'affiliates_2024_median_paid_total_whole_usd': 6527,
    'core_2024_npis_in_group': 500, 'core_2024_npis_present': 93, 'core_2024_npis_with_visit_admin_rows': 81,
    'core_2024_npis_with_visit_admin_paid': 81, 'core_2024_npis_with_drug_code_rows': 1,
    'core_2024_npis_with_drug_code_paid': 1, 'core_2024_paid_total_whole_usd': 754844,
    'core_2024_paid_visit_admin_whole_usd': 592225, 'core_2024_paid_drug_code_whole_usd': 32,
    'core_2024_median_paid_total_whole_usd': 3220, 'other_2024_npis_in_group': 2830, 'other_2024_npis_present': 1102,
    'other_2024_npis_with_visit_admin_rows': 976, 'other_2024_npis_with_visit_admin_paid': 972,
    'other_2024_npis_with_drug_code_rows': 73, 'other_2024_npis_with_drug_code_paid': 60,
    'other_2024_paid_total_whole_usd': 30513331, 'other_2024_paid_visit_admin_whole_usd': 21101456,
    'other_2024_paid_drug_code_whole_usd': 691579, 'other_2024_median_paid_total_whole_usd': 8233,
    'referring_2024_npis_in_group': 4632, 'referring_2024_npis_present': 3364,
    'referring_2024_npis_with_visit_admin_rows': 3071, 'referring_2024_npis_with_visit_admin_paid': 3062,
    'referring_2024_npis_with_drug_code_rows': 681, 'referring_2024_npis_with_drug_code_paid': 633,
    'referring_2024_paid_total_whole_usd': 110316254, 'referring_2024_paid_visit_admin_whole_usd': 68985815,
    'referring_2024_paid_drug_code_whole_usd': 18154478, 'referring_2024_median_paid_total_whole_usd': 10153,
    'affiliates_2024_billing_npis_present': 576, 'affiliates_2024_billing_paid_total_whole_usd': 5990992,
    'core_2024_billing_npis_present': 10, 'core_2024_billing_paid_total_whole_usd': 28217,
    'other_2024_billing_npis_present': 124, 'other_2024_billing_paid_total_whole_usd': 2457620,
    'referring_2024_billing_npis_present': 333, 'referring_2024_billing_paid_total_whole_usd': 7237049,
    'affiliates_npis_with_servicing_row_any_year': 5643, 'affiliates_npis_in_enrollment_file': 9334,
    'core_npis_with_servicing_row_any_year': 223, 'core_npis_in_enrollment_file': 493,
    'other_npis_with_servicing_row_any_year': 1750, 'other_npis_in_enrollment_file': 2678,
    'referring_npis_with_servicing_row_any_year': 3970, 'referring_npis_in_enrollment_file': 4612,
    'center_link_pairs': 1816, 'center_link_npis': 1772, 'center_link_centers': 99,
    'center_link_npis_with_more_than_one_center': 44, 'center_link_source_rows': 104472,
    'center_link_claim_lines': 2900037, 'center_link_paid_usd_2018_2024': '73162855.17',
    'center_link_visit_admin_paid_usd_2018_2024': '23586230.20',
    'center_link_drug_code_paid_usd_2018_2024': '10795345.78', 'center_link_paid_whole_usd': 73162855,
    'center_link_pairs_paid_exactly_zero': 12, 'center_link_pairs_with_gap_years': 196,
    'center_link_pairs_in_hcp_hospital_ccns': 1309, 'center_link_pairs_seen_2024': 879, 'enrollment_npis_in_file': 17117,
    'enrollment_npis_active_2024_any_state': 16974, 'enrollment_npis_medicaid_or_chip_plan': 16003,
    'enrollment_npis_active_2024_medicaid_or_chip_plan': 15569,
}
# Digest of each published table of the 2026-11-19 build (see fingerprint); build() writes the current values to the
# metadata fragment under table_fingerprints, to copy here after a deliberate change of inputs or rules.
FINGERPRINTS = dict(zip(TABLES, (43858620882610520, 23031100167464372, 20489258761354482, 63767241680559798)))


def code_subclass(code):
    """The search's eight-way class of a procedure code, with its tests in their original order."""
    if code in OFFICE:
        return 'em_office'
    if code in HOSPITAL:
        return 'em_hospital'
    if len(code) == 5 and ('96360' <= code <= '96379' or '96401' <= code <= '96417'):
        return 'admin'
    if code in LISTED:
        return 'drug_listed'
    if code in UNCLASSIFIED:
        return 'drug_unclassified'
    if DRUG_PATTERN.fullmatch(code) and code not in NOT_DRUG:
        return 'drug_other_jq'
    return 'transplant_38241' if code == '38241' else 'other'


def code_class(code):
    """visit_admin, drug_code or other."""
    return CLASS_OF[code_subclass(code)]


def usd(cents):
    """Dollar text with two decimals from a whole number of cents; None stays blank."""
    return '' if cents is None else f'{Decimal(cents) / 100:.2f}'


def to_cents(text):
    """Whole cents from published dollar text; a blank cell is None, never zero."""
    return None if text == '' else int((Decimal(text) * 100).to_integral_value(ROUND_HALF_UP))


def whole_usd(cents):
    """Whole dollars, halves rounded up, the way the report quotes amounts."""
    return int((Decimal(cents) / 100).to_integral_value(ROUND_HALF_UP))


def median_usd(values):
    """Median of amounts in cents as dollar text, a half cent rounded up; blank when there is no amount."""
    if not values:
        return ''
    return str((Decimal(statistics.median(values)) / 100).quantize(Decimal('0.01'), ROUND_HALF_UP))


def share(part, whole):
    """Share to three decimals; blank when the denominator is not positive."""
    return f'{part / whole:.3f}' if whole > 0 else ''


def new_tally():
    return {'rows': 0, 'months': set(), 'others': set(), 'lines': 0, 'patients': 0, 'max_patients': 0, 'paid': 0,
            'same_rows': 0, 'same_paid': 0, 'other_blank_rows': 0,
            'class_rows': Counter(), 'class_lines': Counter(), 'class_paid': Counter()}


def add(tally, code, month, patients, lines, cents, own, other):
    """Add one released row to a tally; `own` is the NPI the tally belongs to, `other` the NPI on the other side."""
    tally['rows'] += 1
    tally['months'].add(month)
    tally['lines'] += lines
    tally['patients'] += patients
    tally['max_patients'] = max(tally['max_patients'], patients)
    tally['paid'] += cents
    if other:
        tally['others'].add(other)
    else:
        tally['other_blank_rows'] += 1
    if other == own:
        tally['same_rows'] += 1
        tally['same_paid'] += cents
    sub = code_subclass(code)
    for key in {CLASS_OF[sub], sub} & {'visit_admin', 'drug_code', 'other', 'drug_listed', 'drug_unclassified'}:
        tally['class_rows'][key] += 1
        tally['class_lines'][key] += lines
        tally['class_paid'][key] += cents


def merged(tallies):
    """One tally holding the sum of several."""
    out = new_tally()
    for tally in tallies:
        for key in ('rows', 'lines', 'patients', 'paid', 'same_rows', 'same_paid', 'other_blank_rows'):
            out[key] += tally[key]
        out['max_patients'] = max(out['max_patients'], tally['max_patients'])
        out['months'] |= tally['months']
        out['others'] |= tally['others']
        for key in ('class_rows', 'class_lines', 'class_paid'):
            out[key].update(tally[key])
    return out


def tally_rows(rows, npis, center_npis=()):
    """Tallies per side keyed (NPI, year), and link tallies keyed (servicing NPI, billing center NPI).

    Each row is (billing NPI, servicing NPI, code, month 'YYYY-MM', beneficiaries, claim lines, paid cents); a
    blank NPI is ''. A row naming universe NPIs on both sides is counted once on each side.
    """
    sides = {'servicing': defaultdict(new_tally), 'billing': defaultdict(new_tally)}
    links = defaultdict(new_tally)
    for bill, serv, code, month, patients, lines, cents in rows:
        if serv in npis:
            add(sides['servicing'][serv, month[:4]], code, month, patients, lines, cents, serv, bill)
            if bill in center_npis:
                add(links[serv, bill], code, month, patients, lines, cents, serv, bill)
        if bill in npis:
            add(sides['billing'][bill, month[:4]], code, month, patients, lines, cents, bill, serv)
    return {side: dict(found) for side, found in sides.items()}, dict(links)


def paid(tally, cls=None):
    """Dollar text for all codes or one class; blank when the tally has no released row of that kind."""
    if tally is None or not (tally['class_rows'][cls] if cls else tally['rows']):
        return ''
    return usd(tally['class_paid'][cls] if cls else tally['paid'])


def lines(tally, cls):
    return tally['class_lines'][cls] if tally['class_rows'][cls] else ''


def side_names(side):
    stem = f'medicaid_{side}'
    return [f'{stem}_months_present', f'{stem}_source_rows', f'{stem}_distinct_{SIDES[side]}_npis',
            f'{stem}_claim_lines', f'{stem}_visit_admin_claim_lines', f'{stem}_drug_code_claim_lines',
            f'{stem}_patient_counts_summed', f'{stem}_max_patients_in_one_row', f'{stem}_paid_usd',
            f'{stem}_visit_admin_paid_usd', f'{stem}_drug_code_paid_usd']


def side_cells(side, tally):
    """The by-year cells of one side; every cell is blank when the NPI has no released row on that side."""
    names = side_names(side)
    if tally is None:
        return dict.fromkeys(names, '')
    values = [len(tally['months']), tally['rows'], len(tally['others']), tally['lines'], lines(tally, 'visit_admin'),
              lines(tally, 'drug_code'), tally['patients'], tally['max_patients'], paid(tally),
              paid(tally, 'visit_admin'), paid(tally, 'drug_code')]
    return dict(zip(names, values))


def observed_sides(servicing, billing):
    return '_and_'.join(side for side, seen in (('servicing', servicing), ('billing', billing)) if seen)


def year_row(npi, year, servicing, billing):
    """One NPI x year row from that NPI's servicing-side and billing-side tallies (None when absent)."""
    own = servicing if servicing and servicing['same_rows'] else None
    return {'npi': npi, 'year': year, 'medicaid_row_sides': observed_sides(servicing, billing),
            **side_cells('servicing', servicing), **side_cells('billing', billing),
            'medicaid_self_billed_source_rows': own['same_rows'] if own else '',
            'medicaid_self_billed_paid_usd': usd(own['same_paid']) if own else ''}


def enrollment_flags(segments):
    """Enrollment columns of one NPI, each segment judged on its own status, plan and end date."""
    active = [s for s in segments if s['status'].startswith('ACTIVE') and (not s['end'] or s['end'] >= ACTIVE_FROM)]
    return {'in_medicaid_enrollment_file': int(bool(segments)),
            'medicaid_enrollment_states': ';'.join(sorted({s['state'] for s in segments})),
            'medicaid_enrollment_medicaid_or_chip_plan': int(any(s['plan'] in STATE_PLANS for s in segments)),
            'medicaid_enrollment_active_2024_any_state': int(bool(active)),
            'medicaid_enrollment_active_2024_medicaid_or_chip_plan': int(any(s['plan'] in STATE_PLANS for s in active))}


def feature_row(npi, servicing, billing, segments):
    """One universe NPI: `servicing` and `billing` map year to that NPI's tally on each side."""
    out = {'npi': npi, 'medicaid_data_status': 'observed' if servicing or billing else 'not_observed',
           'medicaid_observed_sides': observed_sides(servicing, billing) or 'neither'}
    for side, found in (('servicing', servicing), ('billing', billing)):
        stem = f'medicaid_{side}'
        for label, tally in (('2024', found.get('2024')), ('2018_2024', merged(found.values()) if found else None)):
            out[f'{stem}_paid_usd_{label}'] = paid(tally)
            out[f'{stem}_visit_admin_paid_usd_{label}'] = paid(tally, 'visit_admin')
            out[f'{stem}_drug_code_paid_usd_{label}'] = paid(tally, 'drug_code')
        out[f'{stem}_years_present'] = len(found) or ''
        if side == 'servicing':
            out[f'{stem}_distinct_billing_npis'] = len(merged(found.values())['others']) if found else ''
    for label, tallies in (('2024', [servicing[y] for y in servicing if y == '2024']), ('2018_2024', servicing.values())):
        own = merged(tallies)
        out[f'medicaid_self_billed_paid_usd_{label}'] = usd(own['same_paid']) if own['same_rows'] else ''
    return {**out, **enrollment_flags(segments)}


def division_row(side, level, label, year, size, tallies):
    """Summary of one division (or division group) x year from the tallies of its NPIs present that year."""
    total = merged(tallies)
    amounts = {cls: [t['class_paid'][cls] for t in tallies if t['class_rows'][cls]] for cls in ('visit_admin', 'drug_code')}
    seen = bool(tallies)
    return {
        'join_side': side, 'division_level': level, 'division': label, 'year': year, 'label_version': LABEL_VERSION,
        'npis_in_division': size, 'npis_present': len(tallies),
        'npis_with_visit_admin_rows': len(amounts['visit_admin']),
        'npis_with_visit_admin_paid': sum(v > 0 for v in amounts['visit_admin']),
        'npis_with_drug_code_rows': len(amounts['drug_code']),
        'npis_with_drug_code_paid': sum(v > 0 for v in amounts['drug_code']),
        'source_rows': total['rows'], 'claim_lines': total['lines'],
        'claim_lines_visit_admin': total['class_lines']['visit_admin'],
        'claim_lines_drug_code': total['class_lines']['drug_code'],
        'paid_total_usd': paid(total), 'paid_visit_admin_usd': paid(total, 'visit_admin'),
        'paid_drug_code_usd': paid(total, 'drug_code'),
        'paid_listed_oncology_drug_usd': paid(total, 'drug_listed'),
        'paid_unclassified_drug_usd': paid(total, 'drug_unclassified'), 'paid_other_usd': paid(total, 'other'),
        'median_paid_total_usd_per_present_npi': median_usd([t['paid'] for t in tallies]),
        'median_visit_admin_usd_per_npi_with_any': median_usd([v for v in amounts['visit_admin'] if v > 0]),
        'median_drug_code_usd_per_npi_with_any': median_usd([v for v in amounts['drug_code'] if v > 0]),
        'share_rows_servicing_equals_billing': share(total['same_rows'], total['rows']) if seen else '',
        'share_rows_other_npi_blank': share(total['other_blank_rows'], total['rows']) if seen else '',
        'share_paid_servicing_equals_billing': share(total['same_paid'], total['paid']) if seen else '',
    }


def division_rows(side, tallies, divisions):
    """All division x year and division-group x year rows of one side; `divisions` maps NPI to its label."""
    out = []
    for level, label_of in (('division', lambda d: d), ('division_group', DIVISION_GROUPS.__getitem__)):
        sizes = Counter(label_of(d) for d in divisions.values())
        cells = defaultdict(list)
        for (npi, year), tally in tallies.items():
            cells[label_of(divisions[npi]), year].append(tally)
        out += [division_row(side, level, label, year, sizes[label], cells[label, year])
                for label in sorted(sizes) for year in YEARS]
    return out


def link_row(npi, ccn, organization_npi, tally, hospital_ccns):
    """One universe NPI x CAR-T center pair with released rows naming the center's NPI as billing provider."""
    years = sorted({month[:4] for month in tally['months']})
    return {'npi': npi, 'ccn': ccn, 'center_organization_npi': organization_npi, 'first_year': years[0],
            'last_year': years[-1], 'years_present': len(years), 'source_rows': tally['rows'],
            'claim_lines': tally['lines'], 'paid_usd_2018_2024': paid(tally),
            'visit_admin_paid_usd_2018_2024': paid(tally, 'visit_admin'),
            'drug_code_paid_usd_2018_2024': paid(tally, 'drug_code'),
            'center_in_hcp_hospital_ccns': int(ccn in hospital_ccns)}


def tables(rows, people, center_rows, segments):
    """The four published tables from monthly rows, universe rows, center rows and enrollment segments by NPI."""
    by_center_npi = {row['organization_npi']: row['ccn'] for row in center_rows if row['organization_npi']}
    sides, links = tally_rows(rows, people, by_center_npi)
    by_npi = {side: defaultdict(dict) for side in sides}
    for side, found in sides.items():
        for (npi, year), tally in found.items():
            by_npi[side][npi][year] = tally
    year_rows = [year_row(npi, year, sides['servicing'].get((npi, year)), sides['billing'].get((npi, year)))
                 for npi in sorted(people) for year in YEARS
                 if (npi, year) in sides['servicing'] or (npi, year) in sides['billing']]
    feature_rows = [feature_row(npi, by_npi['servicing'].get(npi, {}), by_npi['billing'].get(npi, {}),
                                segments.get(npi, [])) for npi in sorted(people)]
    divisions = {npi: row['division'] for npi, row in people.items()}
    summary = [row for side in SIDES for row in division_rows(side, sides[side], divisions)]
    link_rows = [link_row(npi, by_center_npi[org], org, tally, set(people[npi]['hospital_ccns'].split(';')))
                 for (npi, org), tally in sorted(links.items())]
    return dict(zip(TABLES, (year_rows, feature_rows, summary, link_rows)))


# ---- inputs -------------------------------------------------------------------------------------------------

def second_pass(file):
    """Frozen copy of a second-pass file; KOL_MEDICAID_SECOND_PASS names its folder the first time only."""
    name = Path('second_pass') / 'medicaid-payer' / Path(file).name
    return stage(Path(SECOND_PASS) / file if SECOND_PASS else name, name)


def staged_inputs():
    probe = {key: stage(PROBE / file) for key, file in {
        'year_extract': 'medicaid/universe_rows_by_year.parquet', 'pull_script': 'mcaid_pull.py',
        'pull_log': 'mcaid_pull.log', 'enrollment_pull_script': 'enroll_pull.py',
        'analysis_script': 'mcaid_analyze.py', 'link_script': 'mcaid_extra.py',
        'search_npi_table': 'out/npi_medicaid_services_vs_products_2018_2024.csv',
        'search_servicing_groups': 'out/medicaid_servicing_join_by_group_year.csv',
        'search_billing_groups': 'out/medicaid_billing_join_by_group_year.csv',
        'search_links': 'out/medicaid_universe_npi_x_cart_centre_billing_npi.csv'}.items()}
    later = {key: second_pass(file) for key, file in {
        'monthly_rows': 'medicaid-payer-skeptic/monthly_universe_rows.parquet',
        'monthly_pull_script': 'medicaid-payer-skeptic/r1.py', 'monthly_pull_log': 'medicaid-payer-skeptic/r1.log',
        'enrollment_segments': 'medicaid-payer-skeptic/enroll_segments_universe_full.parquet',
        'enrollment_pull_script_full': 'medicaid-payer-skeptic/r2.py',
        'spending_page': 'medicaid-payer/web/medicaid-provider-spending.html',
        'enrollment_page': 'medicaid-payer/web/medicaid-provider-enrollment-segments.html'}.items()}
    return {**probe, **later}


MONTHLY_SQL = """select coalesce(bill, '') bill, coalesce(serv, '') serv, hcpcs, mon, patients, claim_lines,
       cast(round(paid * 100) as bigint) paid_cents, abs(paid * 100 - round(paid * 100)) cent_error
from read_parquet(?) order by 1, 2, 3, 4"""
YEAR_SQL = """select coalesce(bill, '') bill, coalesce(serv, '') serv, hcpcs, yr, n_month_rows, cast(patient_months as bigint),
       cast(claim_lines as bigint), cast(round(paid * 100) as bigint) paid_cents, abs(paid * 100 - round(paid * 100)) cent_error
from read_parquet(?) order by 1, 2, 3, 4"""
SEGMENT_SQL = """select NPI, STATE_CD, coalesce(cast(ENRL_START_DT as varchar), ''), coalesce(cast(ENRL_END_DT as varchar), ''),
       ENRL_STATUS_CD, ENRL_STATUS_DESC, ENRL_PLAN_CD, ENRL_PLAN_DESC, coalesce(PRVDR_TYPE, '')
from read_parquet(?) order by all"""


def extract(source, name, sql, fields):
    """Rewrite a frozen parquet extract as a gzip CSV under Data/interim once, with both hashes recorded.

    The project environment has no parquet reader, so this one step needs DuckDB; later builds read the CSV.
    """
    target, meta = HERE / f'{name}.csv.gz', HERE / f'{name}.meta.json'
    if target.exists() and meta.exists() and (load(meta)['source_sha256'], load(meta)['sha256']) == (sha(source), sha(target)):
        return target
    try:
        import duckdb
    except ImportError as error:
        raise SystemExit('Reading the frozen parquet extracts needs DuckDB once: '
                         f'uv run --with duckdb python -m kolproject.volume_billing.{MODULE}') from error
    found = duckdb.connect().execute(sql, [str(source)]).fetchall()
    if len(found[0]) > len(fields):  # the last selected value is the distance of the paid amount from whole cents
        assert max(row[-1] for row in found) < 1e-4, 'A paid amount is not a whole number of cents'
        found = [row[:-1] for row in found]
    HERE.mkdir(parents=True, exist_ok=True)
    with target.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as packed:
        with io.TextIOWrapper(packed, encoding='utf-8', newline='') as text:
            writer = csv.writer(text)
            writer.writerow(fields)
            writer.writerows(found)
    dump(meta, {'path': str(target.relative_to(ROOT)), 'rows': len(found), 'fields': fields, 'sha256': sha(target),
                'bytes': target.stat().st_size, 'source_path': str(Path(source).relative_to(ROOT)),
                'source_sha256': sha(source), 'query': sql, 'written_at_utc': now()})
    return target


def monthly_rows(path):
    """Rows of the month-level interim extract as tuples with whole-number counts and cents."""
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as stream:
        reader = csv.reader(stream)
        next(reader)
        for bill, serv, code, month, patients, claim_lines, cents in reader:
            yield bill, serv, code, month, int(patients), int(claim_lines), int(cents)


def segments_by_npi(path):
    out = defaultdict(list)
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            out[row['npi']].append(row)
    return out


def read_headers(key, url):
    """Save the HTTP response headers of a source file once; the file itself is not downloaded."""
    path, meta = HEADERS / f'{key}_parquet.headers.txt', HEADERS / f'{key}_parquet.headers.meta.json'
    if not meta.exists():
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60) as response:
                headers, observed = response.headers, now()
        except OSError as error:
            return {'source_url': url, 'header_read': f'failed: {error}'}
        HEADERS.mkdir(parents=True, exist_ok=True)
        path.write_text(str(headers), encoding='utf-8')
        dump(meta, {'source_url': url, 'method': 'HEAD', 'retrieved_at_utc': observed, 'header_read': 'ok',
                    'http_last_modified': headers.get('Last-Modified'), 'http_etag': headers.get('ETag'),
                    'bytes': int(headers.get('Content-Length')), 'http_content_md5': headers.get('Content-MD5'),
                    'publisher_stated_sha256_header': headers.get('x-ms-meta-sha256'),
                    'local_path': str(path.relative_to(ROOT)), 'sha256': sha(path)})
    return load(meta)


# ---- columns ------------------------------------------------------------------------------------------------

ROW = ('A released row is one billing NPI x servicing NPI x procedure code x month cell of the HHS Medicaid Provider '
       'Spending file; a cell is released only with at least 12 claim lines and at least 12 beneficiaries.')
VISIT = 'visit and drug-administration codes (99202-99205, 99211-99215, 99221-99223, 99231-99233, 96360-96379, 96401-96417)'
DRUG = ('drug-pattern procedure codes (J, Q5, Q20, C9 or S0 followed by digits to five characters, plus Q0138, Q0139 '
        'and Q0082)')
NOT_PRODUCT = 'These are mostly supportive-care drugs and fluids, not lymphoma or chimeric antigen receptor T-cell (CAR-T) product dollars.'
CHIP = 'Children\'s Health Insurance Program (CHIP)'
NPI_COLUMN = ('Ten-digit individual National Provider Identifier (NPI) of a health care professional (HCP) in the 2026-10-24 universe '
              '(hcp_divisions.csv); join on it for name, state, specialty and division.')
ROLE = {'servicing': 'servicing provider', 'billing': 'billing provider'}


def no_row(side, what='row', when='that year'):
    return f'No released {what} names this NPI as {ROLE[side]} in {when}; unobserved, not zero.'


def side_columns(side):
    stem, role, other = f'medicaid_{side}', ROLE[side], SIDES[side]
    rows = f'released rows naming this NPI as {role} in the year'
    none, texts = no_row(side), {
        'months_present': f'Number of calendar months of the year (1 to 12) with at least one released row naming this NPI as {role}.',
        'source_rows': f'Number of released rows naming this NPI as {role} in the year, all procedure codes.',
        f'distinct_{other}_npis': f'Number of distinct non-blank {other} NPIs on the {rows}, the NPI itself included when it is on both sides; 0 means every such row has a blank {other} NPI.',
        'claim_lines': f'Sum of claim lines (lines, not claims, including lines paid $0) on released rows naming this NPI as {role} in the year.',
        'visit_admin_claim_lines': f'Claim lines on the {rows} whose procedure code is one of the {VISIT}.',
        'drug_code_claim_lines': f'Claim lines on the {rows} whose procedure code is one of the {DRUG}.',
        'patient_counts_summed': f'Sum of the per-row beneficiary counts on the {rows}. A patient is counted once for every code, month and billing-servicing pair, so this is not a number of distinct patients.',
        'max_patients_in_one_row': f'Largest beneficiary count on any single one of the {rows}; a lower bound on the distinct Medicaid patients of the year.',
        'paid_usd': f'Medicaid and {CHIP if side == "servicing" else "CHIP"} paid amount in US dollars on released rows naming this NPI as {role} in the year, all procedure codes; fee-for-service and managed-care encounters together. '
                    + ('The money was paid to the billing NPI, usually a group or hospital.' if side == 'servicing' else 'Rows where the NPI is also the servicing provider are inside the servicing columns too, so the two sides must not be added.'),
        'visit_admin_paid_usd': f'Part of the year\'s {side}-side paid amount, in US dollars, on {VISIT}.',
        'drug_code_paid_usd': f'Part of the year\'s {side}-side paid amount, in US dollars, on {DRUG}. '
                              + (NOT_PRODUCT if side == 'servicing' else 'Not lymphoma or CAR-T product dollars.'),
    }
    blanks = {'visit_admin_claim_lines': no_row(side, 'visit or administration row'), 'drug_code_claim_lines': no_row(side, 'drug-pattern row'),
              'visit_admin_paid_usd': no_row(side, 'visit or administration row') + ' 0.00 means such rows exist and paid sums to exactly $0.',
              'drug_code_paid_usd': no_row(side, 'drug-pattern row') + ' 0.00 means such rows exist and paid sums to exactly $0.',
              'paid_usd': none + ' 0.00 means released rows exist and paid sums to exactly $0.'}
    return [column(f'{stem}_{name}', text, blanks.get(name, none), 'hhs') for name, text in texts.items()]


YEAR_COLUMNS = [
    column('npi', NPI_COLUMN, 'Never blank.', 'project'),
    column('year', 'Calendar year, 2018 to 2024, of the claim-line month in the U.S. Department of Health and Human Services (HHS) Medicaid Provider Spending file, release 2026-02-09. 2024 is preliminary and its last months are largely missing.', 'Never blank.', 'hhs'),
    column('medicaid_row_sides', f'Side on which the NPI has released rows that year: servicing, billing, or servicing_and_billing. {ROW} An NPI x year without a released row on either side has no row in this table.', 'Never blank.', 'hhs'),
    *side_columns('servicing'), *side_columns('billing'),
    column('medicaid_self_billed_source_rows', 'Number of released rows of the year on which this NPI is both the servicing and the billing provider; these rows are counted in both sides\' columns.', 'No released row of the year has this NPI on both sides.', 'hhs'),
    column('medicaid_self_billed_paid_usd', 'Paid amount in US dollars on the released rows of the year on which this NPI is both servicing and billing provider. Servicing paid plus billing paid minus this amount is the paid amount on rows naming the NPI on either side.', 'No released row of the year has this NPI on both sides; unobserved, not zero.', 'hhs'),
]


def feature_side_columns(side):
    stem, role, out = f'medicaid_{side}', ROLE[side], []
    for label, period in (('2024', 'calendar 2024 (preliminary, last months largely missing)'), ('2018_2024', '2018 to 2024')):
        when = '2024' if label == '2024' else 'any year from 2018 to 2024'
        out += [
            column(f'{stem}_paid_usd_{label}', f'Medicaid and {CHIP if (side, label) == ("servicing", "2024") else "CHIP"} paid amount in US dollars on released rows naming this NPI as {role} with a claim-line month in {period}, all procedure codes; a lower bound. '
                   + ('The money was paid to the billing NPI, usually a group or hospital, not to the HCP.' if side == 'servicing' else
                      'Paid to the HCP\'s own NPI; rows where the HCP is also the servicing provider are inside the servicing amount too, so the two must not be added.'), no_row(side, when=when) + ' 0.00 means released rows exist and paid sums to exactly $0.', 'hhs'),
            column(f'{stem}_visit_admin_paid_usd_{label}', f'Part of the {period} {side}-side paid amount, in US dollars, on {VISIT}.', no_row(side, 'visit or administration row', when) + ' 0.00 means such rows exist and paid sums to exactly $0.', 'hhs'),
            column(f'{stem}_drug_code_paid_usd_{label}', f'Part of the {period} {side}-side paid amount, in US dollars, on {DRUG}. '
                   + (NOT_PRODUCT if (side, label) == ('servicing', '2024') else 'Not lymphoma or CAR-T product dollars.'), no_row(side, 'drug-pattern row', when) + ' 0.00 means such rows exist and paid sums to exactly $0.', 'hhs'),
        ]
    out.append(column(f'{stem}_years_present', f'Number of calendar years from 2018 to 2024 (1 to 7) with at least one released row naming this NPI as {role}.', no_row(side, when='any year from 2018 to 2024'), 'hhs'))
    return out


FEATURE_COLUMNS = [
    column('npi', NPI_COLUMN + ' Every universe NPI has one row.', 'Never blank.', 'project'),
    column('medicaid_data_status', f'observed when at least one released row of the U.S. Department of Health and Human Services (HHS) Medicaid Provider Spending file (release 2026-02-09, January 2018 to December 2024) names this NPI as servicing or billing provider, otherwise not_observed. {ROW} not_observed does not mean the HCP sees no Medicaid patients.', 'Never blank.', 'hhs'),
    column('medicaid_observed_sides', 'Side on which released rows name the NPI in 2018 to 2024: servicing_and_billing, servicing, billing, or neither.', 'Never blank.', 'hhs'),
    *feature_side_columns('servicing'),
    column('medicaid_servicing_distinct_billing_npis', 'Number of distinct non-blank billing NPIs on released 2018 to 2024 rows naming this NPI as servicing provider, the NPI itself included when it bills under its own number; 0 means every such row has a blank billing NPI.', no_row('servicing', when='any year from 2018 to 2024'), 'hhs'),
    *feature_side_columns('billing'),
    column('medicaid_self_billed_paid_usd_2024', 'Paid amount in US dollars on released 2024 rows where this NPI is both servicing and billing provider; it is inside both the servicing and the billing 2024 amounts.', 'No released 2024 row has this NPI on both sides; unobserved, not zero.', 'hhs'),
    column('medicaid_self_billed_paid_usd_2018_2024', 'Paid amount in US dollars on released 2018 to 2024 rows where this NPI is both servicing and billing provider; it is inside both the servicing and the billing seven-year amounts.', 'No released row has this NPI on both sides; unobserved, not zero.', 'hhs'),
    column('in_medicaid_enrollment_file', '1 when the NPI is on at least one row of the HHS Medicaid Provider Enrollment Segments file (release 2026-07-24, segments overlapping 2018 to 2024) in any state, with any status and any plan; otherwise 0. 1 does not mean active participation, and 0 does not mean the HCP never treated Medicaid patients.', 'Never blank.', 'hhs'),
    column('medicaid_enrollment_states', 'Semicolon-separated, sorted postal codes of the states and territories that reported at least one enrollment segment carrying this NPI, of any plan and status. New Mexico and Oregon never appear because their source rows carry no NPI.', 'The NPI has no row in the enrollment file (in_medicaid_enrollment_file is 0).', 'hhs'),
    column('medicaid_enrollment_medicaid_or_chip_plan', '1 when at least one of the NPI\'s enrollment segments has the plan MEDICAID or CHIP, with any status; otherwise 0, which includes NPIs absent from the file.', 'Never blank.', 'hhs'),
    column('medicaid_enrollment_active_2024_any_state', '1 when at least one enrollment segment of the NPI, in any state and under any plan, has a status beginning with ACTIVE (which includes do-not-pay and encounter-only statuses) and an end date that is blank or on or after 2024-01-01; otherwise 0, which includes NPIs absent from the file.', 'Never blank.', 'hhs'),
    column('medicaid_enrollment_active_2024_medicaid_or_chip_plan', 'Same rule as medicaid_enrollment_active_2024_any_state, counting only segments whose plan is MEDICAID or CHIP.', 'Never blank.', 'hhs'),
]

COUNTED = 'NPIs of the division on this join side'
DIVISION_COLUMNS = [
    column('join_side', 'servicing when the rows summarised name a National Provider Identifier (NPI) of the division as servicing provider, billing when they name it as billing provider. The two sides overlap and must not be added.', 'Never blank.', 'project'),
    column('division_level', 'division for one of the seven labels of hcp_divisions.csv (2026-10-24); division_group for the four groups core, affiliates (affiliate_A, affiliate_B, affiliate_C, center_affiliate_unlinked), referring (referring_proxy) and other (hemonc_other).', 'Never blank.', 'project'),
    column('division', 'Division label or division group name, as division_level says.', 'Never blank.', 'project'),
    column('year', 'Calendar year, 2018 to 2024, of the claim-line month in the U.S. Department of Health and Human Services (HHS) Medicaid Provider Spending file, release 2026-02-09. 2024 is preliminary and its last months are largely missing, so years are not a trend series.', 'Never blank.', 'hhs'),
    column('label_version', 'Version of the division labels the row was built with (label_version column of hcp_divisions.csv).', 'Never blank.', 'project'),
    column('npis_in_division', 'Number of NPIs in hcp_divisions.csv (2026-10-24, 17,510 in all) carrying the label or in the group; the same in every year.', 'Never blank.', 'project'),
    column('npis_present', f'Number of {COUNTED} with at least one released row that year, on any procedure code and including rows paid $0. {ROW}', 'Never blank.', 'hhs'),
    column('npis_with_visit_admin_rows', f'Number of {COUNTED} with at least one released row that year on {VISIT}.', 'Never blank.', 'hhs'),
    column('npis_with_visit_admin_paid', f'Number of {COUNTED} whose paid amount on visit and drug-administration codes that year is above $0.', 'Never blank.', 'hhs'),
    column('npis_with_drug_code_rows', f'Number of {COUNTED} with at least one released row that year on {DRUG}.', 'Never blank.', 'hhs'),
    column('npis_with_drug_code_paid', f'Number of {COUNTED} whose paid amount on drug-pattern codes that year is above $0.', 'Never blank.', 'hhs'),
    column('source_rows', 'Number of released rows that year naming an NPI of the division on this join side, all procedure codes; the other columns summarise these rows.', 'Never blank.', 'hhs'),
    column('claim_lines', 'Sum of claim lines (lines, not claims, including lines paid $0) on the rows counted in source_rows.', 'Never blank.', 'hhs'),
    column('claim_lines_visit_admin', 'Part of claim_lines on visit and drug-administration codes; 0 when no such row was released.', 'Never blank.', 'hhs'),
    column('claim_lines_drug_code', 'Part of claim_lines on drug-pattern codes; 0 when no such row was released.', 'Never blank.', 'hhs'),
    column('paid_total_usd', f'Medicaid and {CHIP} paid amount in US dollars on the rows counted in source_rows, all procedure codes; fee-for-service and managed-care encounters together. A lower bound; on the servicing side the money went to the billing NPI, usually a group or hospital, not to the health care professional (HCP).', 'No row was released for the division on this side that year.', 'hhs'),
    column('paid_visit_admin_usd', 'Part of paid_total_usd on visit and drug-administration codes.', 'No such row was released (npis_with_visit_admin_rows is 0); unobserved, not zero.', 'hhs'),
    column('paid_drug_code_usd', f'Part of paid_total_usd on drug-pattern codes. {NOT_PRODUCT}', 'No such row was released (npis_with_drug_code_rows is 0); unobserved, not zero.', 'hhs'),
    column('paid_listed_oncology_drug_usd', 'Part of paid_drug_code_usd on 21 listed codes: J9312, Q5115, Q5119, Q5123, J9311, J9301, J9033, J9034, J9036, J9042, J9309, J9349, J9359, J9321, J9286, J9350, J9271, J9299, J9144, J9145 and J0897. The list is not lymphoma-specific.', 'No row on a listed code was released; unobserved, not zero.', 'hhs'),
    column('paid_unclassified_drug_usd', 'Part of paid_drug_code_usd on the unclassified-drug codes J3490, J3590, J9999 and C9399, where the product cannot be identified.', 'No row on these codes was released; unobserved, not zero.', 'hhs'),
    column('paid_other_usd', 'Part of paid_total_usd on every code that is neither a visit and drug-administration code nor a drug-pattern code, such as laboratory codes, hospital clinic visit code G0463, clinic encounter code T1015 and chemotherapy administration code Q0084.', 'No such row was released; unobserved, not zero.', 'hhs'),
    column('median_paid_total_usd_per_present_npi', 'Median, in US dollars, of the per-NPI paid amount that year across the NPIs counted in npis_present; NPIs without a released row are left out, not counted as zero.', 'npis_present is 0.', 'hhs'),
    column('median_visit_admin_usd_per_npi_with_any', 'Median, in US dollars, of the per-NPI paid amount on visit and drug-administration codes across the NPIs counted in npis_with_visit_admin_paid.', 'npis_with_visit_admin_paid is 0.', 'hhs'),
    column('median_drug_code_usd_per_npi_with_any', 'Median, in US dollars, of the per-NPI paid amount on drug-pattern codes across the NPIs counted in npis_with_drug_code_paid.', 'npis_with_drug_code_paid is 0.', 'hhs'),
    column('share_rows_servicing_equals_billing', 'Share, to three decimals, of the rows counted in source_rows on which the servicing NPI equals the billing NPI, meaning the HCP billed under their own number.', 'npis_present is 0.', 'hhs'),
    column('share_rows_other_npi_blank', 'Share, to three decimals, of the rows counted in source_rows whose other NPI is blank: the billing NPI on the servicing side, the servicing NPI on the billing side.', 'npis_present is 0.', 'hhs'),
    column('share_paid_servicing_equals_billing', 'Share, to three decimals, of paid_total_usd on rows where the servicing NPI equals the billing NPI.', 'npis_present is 0 or paid_total_usd is not above $0.', 'hhs'),
]

LINK_COLUMNS = [
    column('npi', NPI_COLUMN + ' Here it is the servicing provider on the released rows of the pair.', 'Never blank.', 'project'),
    column('ccn', 'Centers for Medicare & Medicaid Services (CMS) Certification Number (CCN) of the chimeric antigen receptor T-cell (CAR-T) center in cart_centers.csv (2026-10-24) whose organization NPI is the billing provider on the released rows of the pair.', 'Never blank.', 'project'),
    column('center_organization_npi', 'The organization NPI recorded for that center in cart_centers.csv, one per CCN; a hospital\'s other billing NPIs, such as a faculty practice plan, are not matched.', 'Never blank.', 'project'),
    column('first_year', f'Earliest calendar year, 2018 to 2024, with a released row for the pair in the U.S. Department of Health and Human Services (HHS) Medicaid Provider Spending file, release 2026-02-09. {ROW}', 'Never blank.', 'hhs'),
    column('last_year', 'Latest calendar year with a released row for the pair. 2024 is preliminary and incomplete, so an earlier last year does not show that the relationship ended.', 'Never blank.', 'hhs'),
    column('years_present', 'Number of calendar years from first_year to last_year with at least one released row for the pair; less than the span when there are gap years.', 'Never blank.', 'hhs'),
    column('source_rows', 'Number of released rows for the pair in 2018 to 2024.', 'Never blank.', 'hhs'),
    column('claim_lines', 'Sum of claim lines (lines, not claims) on the released rows of the pair in 2018 to 2024, all procedure codes.', 'Never blank.', 'hhs'),
    column('paid_usd_2018_2024', f'Medicaid and {CHIP} paid amount in US dollars to the center\'s billing NPI on the released rows of the pair in 2018 to 2024, all procedure codes; 0.00 means the rows exist and paid sums to exactly $0.', 'Never blank.', 'hhs'),
    column('visit_admin_paid_usd_2018_2024', f'Part of the pair\'s paid amount, in US dollars, on {VISIT}.', 'No such row was released for the pair; unobserved, not zero.', 'hhs'),
    column('drug_code_paid_usd_2018_2024', f'Part of the pair\'s paid amount, in US dollars, on {DRUG}. Mostly supportive-care drugs and fluids, not lymphoma or CAR-T product dollars.', 'No such row was released for the pair; unobserved, not zero.', 'hhs'),
    column('center_in_hcp_hospital_ccns', '1 when the center\'s CCN is among the HCP\'s hospital_ccns in hcp_divisions.csv, otherwise 0. A 0 may be a real secondary site or attribution noise; it was not checked.', 'Never blank.', 'project'),
]
COLUMNS = dict(zip(TABLES, (YEAR_COLUMNS, FEATURE_COLUMNS, DIVISION_COLUMNS, LINK_COLUMNS)))
KEYS = dict(zip(TABLES, (['npi', 'year'], ['npi'], ['join_side', 'division_level', 'division', 'year'], ['npi', 'ccn'])))


# ---- figures and checks -------------------------------------------------------------------------------------

def total(rows, field):
    """Sum of a dollar column in whole cents, blanks left out."""
    return sum(to_cents(row[field]) for row in rows if row[field] != '')


def headline(year_rows, feature_rows, summary, link_rows, people):
    """Numbers a README can quote, computed from the published rows and the universe's division labels."""
    out = {
        'universe_npis': len(feature_rows), 'npi_year_rows': len(year_rows),
        'npis_observed': sum(r['medicaid_data_status'] == 'observed' for r in feature_rows),
        'servicing_npis_any_year': sum(r['medicaid_servicing_paid_usd_2018_2024'] != '' for r in feature_rows),
        'servicing_npis_2024': sum(r['medicaid_servicing_paid_usd_2024'] != '' for r in feature_rows),
        'billing_npis_any_year': sum(r['medicaid_billing_paid_usd_2018_2024'] != '' for r in feature_rows),
        'billing_npis_2024': sum(r['medicaid_billing_paid_usd_2024'] != '' for r in feature_rows),
        'self_billed_npis_any_year': sum(r['medicaid_self_billed_paid_usd_2018_2024'] != '' for r in feature_rows),
        'billing_only_npis': sum(r['medicaid_observed_sides'] == 'billing' for r in feature_rows),
        'servicing_npis_2024_paid_exactly_zero': sum(r['medicaid_servicing_paid_usd_2024'] == '0.00' for r in feature_rows),
        'servicing_npis_any_year_paid_exactly_zero': sum(r['medicaid_servicing_paid_usd_2018_2024'] == '0.00' for r in feature_rows),
        'npis_observed_not_in_enrollment_file': sum(r['medicaid_data_status'] == 'observed' and str(r['in_medicaid_enrollment_file']) == '0' for r in feature_rows),
    }
    for name in ('servicing_paid_usd', 'servicing_visit_admin_paid_usd', 'servicing_drug_code_paid_usd',
                 'billing_paid_usd', 'self_billed_paid_usd'):
        out[f'{name}_2018_2024'] = usd(total(year_rows, f'medicaid_{name}'))
    for side in SIDES:
        seen = [r for r in year_rows if r[f'medicaid_{side}_paid_usd'] != '']
        out[f'{side}_source_rows_2018_2024'] = sum(int(r[f'medicaid_{side}_source_rows'] or 0) for r in seen)
        out[f'{side}_claim_lines_2018_2024'] = sum(int(r[f'medicaid_{side}_claim_lines'] or 0) for r in seen)
        out[f'{side}_npis_by_year'] = {year: sum(str(r['year']) == year for r in seen) for year in YEARS}
        out[f'{side}_paid_usd_by_year'] = {year: usd(total([r for r in seen if str(r['year']) == year], f'medicaid_{side}_paid_usd'))
                                           for year in YEARS}
    for row in summary:
        if (row['division_level'], str(row['year'])) != ('division_group', '2024'):
            continue
        group = row['division']
        if row['join_side'] == 'billing':
            out[f'{group}_2024_billing_npis_present'] = int(row['npis_present'])
            out[f'{group}_2024_billing_paid_total_whole_usd'] = '' if row['paid_total_usd'] == '' else whole_usd(to_cents(row['paid_total_usd']))
        else:
            out[f'{group}_2024_npis_in_group'] = int(row['npis_in_division'])
            out[f'{group}_2024_npis_present'] = int(row['npis_present'])
            for name in ('visit_admin_rows', 'visit_admin_paid', 'drug_code_rows', 'drug_code_paid'):
                out[f'{group}_2024_npis_with_{name}'] = int(row[f'npis_with_{name}'])
            for name in ('paid_total', 'paid_visit_admin', 'paid_drug_code', 'median_paid_total'):
                field = f'{name}_usd_per_present_npi' if name.startswith('median') else f'{name}_usd'
                out[f'{group}_2024_{name}_whole_usd'] = '' if row[field] == '' else whole_usd(to_cents(row[field]))
    group_of = lambda r: DIVISION_GROUPS.get(people[r['npi']]['division'], '') if r['npi'] in people else ''
    groups = Counter(group_of(r) for r in feature_rows if r['medicaid_servicing_paid_usd_2018_2024'] != '')
    enrolled = Counter(group_of(r) for r in feature_rows if str(r['in_medicaid_enrollment_file']) == '1')
    for group in sorted(set(DIVISION_GROUPS.values())):
        out[f'{group}_npis_with_servicing_row_any_year'] = groups[group]
        out[f'{group}_npis_in_enrollment_file'] = enrolled[group]
    span = lambda r: int(r['last_year']) - int(r['first_year']) + 1
    out.update({
        'center_link_pairs': len(link_rows), 'center_link_npis': len({r['npi'] for r in link_rows}),
        'center_link_centers': len({r['ccn'] for r in link_rows}),
        'center_link_npis_with_more_than_one_center': sum(n > 1 for n in Counter(r['npi'] for r in link_rows).values()),
        'center_link_source_rows': sum(int(r['source_rows']) for r in link_rows),
        'center_link_claim_lines': sum(int(r['claim_lines']) for r in link_rows),
        'center_link_paid_usd_2018_2024': usd(total(link_rows, 'paid_usd_2018_2024')),
        'center_link_visit_admin_paid_usd_2018_2024': usd(total(link_rows, 'visit_admin_paid_usd_2018_2024')),
        'center_link_drug_code_paid_usd_2018_2024': usd(total(link_rows, 'drug_code_paid_usd_2018_2024')),
        'center_link_paid_whole_usd': whole_usd(total(link_rows, 'paid_usd_2018_2024')),
        'center_link_pairs_paid_exactly_zero': sum(r['paid_usd_2018_2024'] == '0.00' for r in link_rows),
        'center_link_pairs_with_gap_years': sum(int(r['years_present']) < span(r) for r in link_rows),
        'center_link_pairs_in_hcp_hospital_ccns': sum(str(r['center_in_hcp_hospital_ccns']) == '1' for r in link_rows),
        'center_link_pairs_seen_2024': sum(str(r['last_year']) == '2024' for r in link_rows),
        'enrollment_npis_in_file': sum(str(r['in_medicaid_enrollment_file']) == '1' for r in feature_rows),
        'enrollment_npis_active_2024_any_state': sum(str(r['medicaid_enrollment_active_2024_any_state']) == '1' for r in feature_rows),
        'enrollment_npis_medicaid_or_chip_plan': sum(str(r['medicaid_enrollment_medicaid_or_chip_plan']) == '1' for r in feature_rows),
        'enrollment_npis_active_2024_medicaid_or_chip_plan': sum(str(r['medicaid_enrollment_active_2024_medicaid_or_chip_plan']) == '1' for r in feature_rows),
    })
    return out


def group_by(rows, *fields):
    out = defaultdict(list)
    for row in rows:
        out[tuple(row[f] for f in fields)].append(row)
    return out


def check_years(year_rows, people):
    """Errors inside the NPI x year table."""
    errors = Counter(key=duplicates(year_rows, ['npi', 'year']), joins=0, sides=0, classes=0, release_threshold=0)
    for row in year_rows:
        errors['joins'] += row['npi'] not in people or row['year'] not in YEARS
        filled = {side: [row[name] != '' for name in side_names(side)] for side in SIDES}
        seen = {side: row[f'medicaid_{side}_paid_usd'] != '' for side in SIDES}
        always = lambda side: [row[f'medicaid_{side}_{name}'] != '' for name in
                               ('months_present', 'source_rows', f'distinct_{SIDES[side]}_npis', 'claim_lines',
                                'patient_counts_summed', 'max_patients_in_one_row', 'paid_usd')]
        errors['sides'] += (row['medicaid_row_sides'] != observed_sides(seen['servicing'], seen['billing'])
                            or not any(seen.values())
                            or any(all(always(side)) != seen[side] or (not seen[side] and any(filled[side])) for side in SIDES)
                            or (row['medicaid_self_billed_paid_usd'] != '') != (row['medicaid_self_billed_source_rows'] != '')
                            or (row['medicaid_self_billed_paid_usd'] != '' and not all(seen.values())))
        for side in SIDES:
            stem = f'medicaid_{side}'
            if not (seen[side] and all(always(side))):  # an incomplete side is already counted under sides
                continue
            rows_, lines_, paid_ = int(row[f'{stem}_source_rows']), int(row[f'{stem}_claim_lines']), to_cents(row[f'{stem}_paid_usd'])
            parts = [(row[f'{stem}_{cls}_claim_lines'], row[f'{stem}_{cls}_paid_usd']) for cls in ('visit_admin', 'drug_code')]
            errors['classes'] += (any((a == '') != (b == '') for a, b in parts)
                                  or sum(int(a) for a, _ in parts if a) > lines_
                                  or paid_ < 0 or any(to_cents(b) < 0 for _, b in parts if b)
                                  or sum(to_cents(b) for _, b in parts if b) > paid_)
            errors['release_threshold'] += (lines_ < 12 * rows_ or int(row[f'{stem}_patient_counts_summed']) < 12 * rows_
                                            or int(row[f'{stem}_max_patients_in_one_row']) < 12
                                            or not 1 <= int(row[f'{stem}_months_present']) <= min(12, rows_)
                                            or any(a and int(a) < 12 for a, _ in parts))
            if row['medicaid_self_billed_source_rows'] and row['medicaid_self_billed_paid_usd']:
                errors['sides'] += int(row['medicaid_self_billed_source_rows']) > rows_ or to_cents(row['medicaid_self_billed_paid_usd']) > paid_
    return errors


def expected_feature(npi, rows):
    """The dollar, count and status cells of a feature row recomputed from that NPI's published year rows."""
    seen = {side: [r for r in rows if r[f'medicaid_{side}_paid_usd'] != ''] for side in SIDES}
    out = {'medicaid_data_status': 'observed' if rows else 'not_observed',
           'medicaid_observed_sides': observed_sides(seen['servicing'], seen['billing']) or 'neither'}
    for side in SIDES:
        stem = f'medicaid_{side}'
        for label, part in (('2024', [r for r in seen[side] if r['year'] == '2024']), ('2018_2024', seen[side])):
            for cls in ('', 'visit_admin_', 'drug_code_'):
                cells = [r[f'{stem}_{cls}paid_usd'] for r in part if r[f'{stem}_{cls}paid_usd'] != '']
                out[f'{stem}_{cls}paid_usd_{label}'] = usd(sum(map(to_cents, cells))) if cells else ''
        out[f'{stem}_years_present'] = str(len(seen[side]) or '')
    for label, part in (('2024', [r for r in rows if r['year'] == '2024']), ('2018_2024', rows)):
        cells = [r['medicaid_self_billed_paid_usd'] for r in part if r['medicaid_self_billed_paid_usd'] != '']
        out[f'medicaid_self_billed_paid_usd_{label}'] = usd(sum(map(to_cents, cells))) if cells else ''
    return out


def check_features(feature_rows, year_rows, people):
    errors = Counter(universe=len(set(people) ^ {r['npi'] for r in feature_rows}) + duplicates(feature_rows, ['npi']),
                     match_by_year=0, blank_when_unobserved=0, enrollment=0)
    by_npi = group_by(year_rows, 'npi')
    measures = [c['column'] for c in FEATURE_COLUMNS if c['column'].startswith('medicaid_') and 'enrollment' not in c['column']
                and c['column'] not in ('medicaid_data_status', 'medicaid_observed_sides')]
    for row in feature_rows:
        rows = by_npi.get((row['npi'],), [])
        want = expected_feature(row['npi'], rows)
        errors['match_by_year'] += any(row[name] != value for name, value in want.items())
        errors['blank_when_unobserved'] += row['medicaid_data_status'] == 'not_observed' and any(row[name] != '' for name in measures)
        known = [int(r['medicaid_servicing_distinct_billing_npis'] or 0) for r in rows if r['medicaid_servicing_paid_usd'] != '']
        distinct = row['medicaid_servicing_distinct_billing_npis']
        errors['match_by_year'] += (distinct != '') != bool(known) or (bool(known) and not max(known) <= int(distinct) <= sum(known))
        flags = [row[name] for name in ('in_medicaid_enrollment_file', 'medicaid_enrollment_medicaid_or_chip_plan',
                                        'medicaid_enrollment_active_2024_any_state',
                                        'medicaid_enrollment_active_2024_medicaid_or_chip_plan')]
        states = row['medicaid_enrollment_states']
        in_file, plan, active, active_plan = flags
        errors['enrollment'] += (any(flag not in ('0', '1') for flag in flags) or (in_file == '0' and '1' in flags[1:])
                                 or (active_plan == '1' and (active != '1' or plan != '1'))
                                 or (states == '') != (in_file == '0')
                                 or not re.fullmatch(r'([A-Z]{2}(;[A-Z]{2})*)?', states)
                                 or states.split(';') != sorted(set(states.split(';')))
                                 or bool({'NM', 'OR'} & set(states.split(';'))))
    return errors


def expected_division(side, level, label, year, size, rows):
    """Division cells that the published NPI x year rows determine, recomputed from those rows."""
    stem = f'medicaid_{side}'
    rows = [r for r in rows if r[f'{stem}_paid_usd'] != '']
    cents = lambda name: [to_cents(r[f'{stem}_{name}']) for r in rows if r[f'{stem}_{name}'] != '']
    count = lambda name: sum(int(r[name]) for r in rows if r[name] != '')
    visit, drug, paid_total = cents('visit_admin_paid_usd'), cents('drug_code_paid_usd'), sum(cents('paid_usd'))
    same_rows, same_paid = count('medicaid_self_billed_source_rows'), sum(to_cents(r['medicaid_self_billed_paid_usd']) for r in rows if r['medicaid_self_billed_paid_usd'] != '')
    return {
        'label_version': LABEL_VERSION, 'npis_in_division': str(size), 'npis_present': str(len(rows)),
        'npis_with_visit_admin_rows': str(len(visit)), 'npis_with_visit_admin_paid': str(sum(v > 0 for v in visit)),
        'npis_with_drug_code_rows': str(len(drug)), 'npis_with_drug_code_paid': str(sum(v > 0 for v in drug)),
        'source_rows': str(count(f'{stem}_source_rows')), 'claim_lines': str(count(f'{stem}_claim_lines')),
        'claim_lines_visit_admin': str(count(f'{stem}_visit_admin_claim_lines')),
        'claim_lines_drug_code': str(count(f'{stem}_drug_code_claim_lines')),
        'paid_total_usd': usd(paid_total) if rows else '', 'paid_visit_admin_usd': usd(sum(visit)) if visit else '',
        'paid_drug_code_usd': usd(sum(drug)) if drug else '',
        'median_paid_total_usd_per_present_npi': median_usd(cents('paid_usd')),
        'median_visit_admin_usd_per_npi_with_any': median_usd([v for v in visit if v > 0]),
        'median_drug_code_usd_per_npi_with_any': median_usd([v for v in drug if v > 0]),
        'share_rows_servicing_equals_billing': share(same_rows, count(f'{stem}_source_rows')) if rows else '',
        'share_paid_servicing_equals_billing': share(same_paid, paid_total) if rows else '',
    }


def check_divisions(summary, year_rows, people):
    errors = Counter(key=duplicates(summary, KEYS[TABLES[2]]), match_by_year=0, denominators=0, levels_agree=0, class_sums=0)
    labels = {'division': {npi: row['division'] for npi, row in people.items()},
              'division_group': {npi: DIVISION_GROUPS.get(row['division'], '') for npi, row in people.items()}}
    sizes = {level: Counter(found.values()) for level, found in labels.items()}
    wanted = {(side, level, label, year) for side in SIDES for level in sizes for label in sizes[level] for year in YEARS}
    errors['key'] += len(wanted ^ {tuple(row[k] for k in KEYS[TABLES[2]]) for row in summary})
    cells = {level: group_by([{**r, 'label': found.get(r['npi'], '')} for r in year_rows], 'label', 'year')
             for level, found in labels.items()}
    additive = ['npis_in_division', 'npis_present', 'npis_with_visit_admin_rows', 'npis_with_visit_admin_paid',
                'npis_with_drug_code_rows', 'npis_with_drug_code_paid', 'source_rows', 'claim_lines',
                'claim_lines_visit_admin', 'claim_lines_drug_code']
    money = ['paid_total_usd', 'paid_visit_admin_usd', 'paid_drug_code_usd', 'paid_listed_oncology_drug_usd',
             'paid_unclassified_drug_usd', 'paid_other_usd']
    for row in summary:
        side, level, label, year = (row[k] for k in KEYS[TABLES[2]])
        if level not in sizes or side not in SIDES:
            continue
        want = expected_division(side, level, label, year, sizes[level][label], cells[level].get((label, year), []))
        errors['match_by_year'] += any(row[name] != value for name, value in want.items())
        counts = {name: int(row[name]) for name in additive}
        errors['denominators'] += (counts['npis_present'] > counts['npis_in_division']
                                   or counts['npis_with_visit_admin_rows'] > counts['npis_present']
                                   or counts['npis_with_visit_admin_paid'] > counts['npis_with_visit_admin_rows']
                                   or counts['npis_with_drug_code_rows'] > counts['npis_present']
                                   or counts['npis_with_drug_code_paid'] > counts['npis_with_drug_code_rows']
                                   or counts['claim_lines_visit_admin'] + counts['claim_lines_drug_code'] > counts['claim_lines']
                                   or any(row[name] != '' and not 0 <= float(row[name]) <= 1 for name in row if name.startswith('share_'))
                                   or any((row[name] == '') != (counts['npis_present'] == 0)
                                          for name in ('share_rows_servicing_equals_billing', 'share_rows_other_npi_blank'))
                                   or (row['share_paid_servicing_equals_billing'] == '') != ((to_cents(row['paid_total_usd']) or 0) <= 0))
        parts = {name: to_cents(row[name]) or 0 for name in money}
        errors['class_sums'] += (parts['paid_visit_admin_usd'] + parts['paid_drug_code_usd'] + parts['paid_other_usd'] != parts['paid_total_usd']
                                 or parts['paid_listed_oncology_drug_usd'] + parts['paid_unclassified_drug_usd'] > parts['paid_drug_code_usd']
                                 or (row['paid_visit_admin_usd'] == '') != (counts['npis_with_visit_admin_rows'] == 0)
                                 or (row['paid_drug_code_usd'] == '') != (counts['npis_with_drug_code_rows'] == 0)
                                 or (row['paid_total_usd'] == '') != (counts['npis_present'] == 0))
    for (side, level, year), rows in group_by(summary, 'join_side', 'division_level', 'year').items():
        errors['denominators'] += sum(int(r['npis_in_division']) for r in rows) != len(people)
    fine = group_by([r for r in summary if r['division_level'] == 'division'], 'join_side', 'year')
    for row in (r for r in summary if r['division_level'] == 'division_group'):
        members = [r for r in fine.get((row['join_side'], row['year']), []) if DIVISION_GROUPS.get(r['division']) == row['division']]
        errors['levels_agree'] += (any(sum(int(r[name]) for r in members) != int(row[name]) for name in additive)
                                   or any(sum(to_cents(r[name]) or 0 for r in members) != (to_cents(row[name]) or 0) for name in money))
    return errors


def check_links(link_rows, year_rows, people, center_rows):
    errors = Counter(key=duplicates(link_rows, ['npi', 'ccn']), joins=0, values=0)
    by_ccn = {row['ccn']: row['organization_npi'] for row in center_rows}
    errors['joins'] += (len(set(by_ccn.values())) != len(by_ccn)) + len(set(people) & set(by_ccn.values()))
    seen = {(r['npi'], r['year']): r for r in year_rows}
    for row in link_rows:
        errors['joins'] += (row['npi'] not in people or by_ccn.get(row['ccn'], '') != row['center_organization_npi']
                            or row['center_organization_npi'] == '')
        if row['npi'] not in people:
            continue
        span = int(row['last_year']) - int(row['first_year']) + 1
        ends = [seen.get((row['npi'], row[name]), {}).get('medicaid_servicing_paid_usd', '') for name in ('first_year', 'last_year')]
        mine = [seen[row['npi'], year] for year in YEARS if (row['npi'], year) in seen and row['first_year'] <= year <= row['last_year']]
        parts = [to_cents(row[name]) for name in ('visit_admin_paid_usd_2018_2024', 'drug_code_paid_usd_2018_2024') if row[name] != '']
        errors['values'] += (row['first_year'] not in YEARS or row['last_year'] not in YEARS or span < 1
                             or not min(span, 2) <= int(row['years_present']) <= min(span, len(mine))
                             or '' in ends or row['paid_usd_2018_2024'] == '' or to_cents(row['paid_usd_2018_2024']) < 0
                             or int(row['claim_lines']) < 12 * int(row['source_rows'])
                             or int(row['claim_lines']) > sum(int(r['medicaid_servicing_claim_lines'] or 0) for r in mine)
                             or int(row['source_rows']) > sum(int(r['medicaid_servicing_source_rows'] or 0) for r in mine)
                             or sum(parts) > to_cents(row['paid_usd_2018_2024'])
                             or row['center_in_hcp_hospital_ccns'] != str(int(row['ccn'] in people[row['npi']]['hospital_ccns'].split(';'))))
    return errors


FORMATS = [  # column name pattern -> pattern of a filled cell; the first match applies
    (r'npi|center_organization_npi', r'\d{10}'), (r'ccn', r'[0-9A-Z]{6}'), (r'(first_|last_)?year', r'20(1[89]|2[0-4])'),
    (r'.*_usd.*', r'-?\d+\.\d\d'), (r'share_.*', r'[01]\.\d{3}'), (r'label_version', r'\d+\.\d+\.\d+'),
    (r'medicaid_row_sides|join_side|medicaid_observed_sides', r'servicing|billing|servicing_and_billing|neither'),
    (r'medicaid_data_status', r'observed|not_observed'), (r'division_level', r'division|division_group'),
    (r'division', r'[A-Za-z_]+'), (r'medicaid_enrollment_states', r'[A-Z]{2}(;[A-Z]{2})*'),
    (r'in_medicaid_enrollment_file|medicaid_enrollment_.*|center_in_hcp_hospital_ccns', r'[01]'), (r'.*', r'\d+'),
]


def well_formed(table, rows):
    """Rows whose every cell has its column's format, and the number of cells that do not.

    A blank is accepted only where the dictionary does not say 'Never blank'. The other checks read numbers from the
    well-formed rows only, so a damaged cell is counted here instead of stopping the validation.
    """
    rules = [(c['column'], re.compile(next(cell for name, cell in FORMATS if re.fullmatch(name, c['column']))),
              not c['missing_value'].startswith('Never blank')) for c in COLUMNS[table]]
    if not rows or list(rows[0]) != [name for name, _, _ in rules]:
        return [], len(rows)
    good, bad = [], 0
    for row in rows:
        wrong = sum(not (cell.fullmatch(row[name] or '') or (blank_allowed and row[name] == '')) for name, cell, blank_allowed in rules)
        bad += wrong
        if not wrong:
            good.append(row)
    return good, bad


def fingerprint(rows, names):
    """Order-free digest of a table: it changes when any cell is changed, moved to another row, added or dropped."""
    return sum(int.from_bytes(hashlib.sha256('\x1f'.join(str(row.get(name, '')) for name in names).encode()).digest()[:7], 'big')
               for row in rows) % (1 << 56)


def check(processed):
    """Error counts for the four published tables, read only from a Data/processed directory.

    Three layers: cell formats; rules that tie the tables to each other, to the universe and to the center table;
    and the totals and table digests of the 2026-11-19 build, which guard the cells no rule can rebuild (per-NPI
    enrollment columns, per-pair link amounts, months and beneficiary counts).
    """
    processed = Path(processed)
    read = lambda path: read_csv(path) if path.exists() else []
    people = {row['npi']: row for row in read(processed / UNIVERSE_SNAPSHOT / 'hcp_divisions.csv')}
    center_rows = read(processed / UNIVERSE_SNAPSHOT / 'cart_centers.csv')
    raw = {table: read(processed / SNAPSHOT / f'{table}.csv') for table in TABLES}
    clean = {table: well_formed(table, rows) for table, rows in raw.items()}
    year_rows, feature_rows, summary, link_rows = (clean[table][0] for table in TABLES)
    out = {f'{MODULE}.cell_formats': sum(bad for _, bad in clean.values())}
    for name, errors in (('by_year', check_years(year_rows, people)), ('features', check_features(feature_rows, year_rows, people)),
                         ('division_year', check_divisions(summary, year_rows, people)),
                         ('center_links', check_links(link_rows, year_rows, people, center_rows))):
        out.update({f'{MODULE}.{name}_{key}': int(count) for key, count in errors.items()})
    found = headline(year_rows, feature_rows, summary, link_rows, people)
    out[f'{MODULE}.headline_totals'] = sum(found.get(key) != value for key, value in EXPECTED.items()) + len(set(found) - set(EXPECTED))
    out[f'{MODULE}.table_fingerprints'] = sum(fingerprint(raw[table], [c['column'] for c in COLUMNS[table]]) != FINGERPRINTS[table]
                                              for table in TABLES)
    out[f'{MODULE}.label_version'] = sum(row['label_version'] != LABEL_VERSION for row in people.values())
    banned = re.compile(r'(^|_)(service|services|product|products|serv|bill|mcaid)(_|$)')
    out[f'{MODULE}.columns'] = sum([c['column'] for c in COLUMNS[table]] != list(raw[table][0]) if raw[table] else 1 for table in TABLES)
    out[f'{MODULE}.class_names'] = sum(bool(banned.search(name)) for table in TABLES for name in (raw[table][0] if raw[table] else []))
    return out


# ---- build --------------------------------------------------------------------------------------------------

def within_a_dollar(mine, theirs):
    """Whether two-decimal text of this build and a whole-dollar cell of the search agree; blank must meet blank."""
    if mine == '' or theirs == '':
        return mine == theirs
    return abs(Decimal(mine) - Decimal(theirs)) <= 1


def reconcile(built, inputs):
    """Differences between this build and the tables the search wrote, which rounded float sums to whole dollars."""
    pairs = {'mcaid_serv_paid_2024': 'medicaid_servicing_paid_usd_2024',
             'mcaid_serv_service_paid_2024': 'medicaid_servicing_visit_admin_paid_usd_2024',
             'mcaid_serv_drug_paid_2024': 'medicaid_servicing_drug_code_paid_usd_2024',
             'mcaid_serv_paid_2018_2024': 'medicaid_servicing_paid_usd_2018_2024',
             'mcaid_serv_service_paid_2018_2024': 'medicaid_servicing_visit_admin_paid_usd_2018_2024',
             'mcaid_serv_drug_paid_2018_2024': 'medicaid_servicing_drug_code_paid_usd_2018_2024',
             'mcaid_bill_paid_2024': 'medicaid_billing_paid_usd_2024',
             'mcaid_bill_paid_2018_2024': 'medicaid_billing_paid_usd_2018_2024',
             'mcaid_bill_drug_paid_2018_2024': 'medicaid_billing_drug_code_paid_usd_2018_2024'}
    mine = {row['npi']: row for row in built[TABLES[1]]}
    theirs = read_csv(inputs['search_npi_table'])
    out = {'npi_table_rows': len(theirs), 'npi_table_npis_missing': len(set(mine) ^ {r['npi'] for r in theirs}),
           'npi_table_dollar_cells_off_by_more_than_1_usd': sum(not within_a_dollar(str(mine[r['npi']][new]), r[old]) for r in theirs for old, new in pairs.items()),
           'npi_table_whole_dollar_cells_different_from_cents_rounded_half_up': sum(
               '' not in (r[old], mine[r['npi']][new]) and Decimal(r[old]) != whole_usd(to_cents(mine[r['npi']][new]))
               for r in theirs for old, new in pairs.items()),
           'npi_table_count_cells_different': sum(str(mine[r['npi']][new]) != (r[old] and str(int(float(r[old])))) for r in theirs for old, new in
                                                  (('mcaid_serv_years_present', 'medicaid_servicing_years_present'), ('mcaid_distinct_billing_npis', 'medicaid_servicing_distinct_billing_npis'))),
           'npi_table_in_enrollment_file_different': sum(str(mine[r['npi']]['in_medicaid_enrollment_file']) != r['in_medicaid_enrollment_file'] for r in theirs),
           'npi_table_enrollment_states_different': sum(mine[r['npi']]['medicaid_enrollment_states'] != r['medicaid_enroll_states'] for r in theirs),
           'active_flag_0_in_search_1_here': sum((r['active_segment_reaching_2024_or_open'], str(mine[r['npi']]['medicaid_enrollment_active_2024_any_state'])) == ('0', '1') for r in theirs),
           'active_flag_1_in_search_0_here': sum((r['active_segment_reaching_2024_or_open'], str(mine[r['npi']]['medicaid_enrollment_active_2024_any_state'])) == ('1', '0') for r in theirs)}
    groups = {'core': 'core', 'affiliate_all': 'affiliates', 'referring_proxy': 'referring', 'hemonc_other': 'other'}
    fields = {'npis_in_division': 'npis_in_division', 'npis_present': 'npis_present', 'npis_with_service_rows': 'npis_with_visit_admin_paid',
              'npis_with_drug_rows': 'npis_with_drug_code_paid', 'claim_lines': 'claim_lines', 'claim_lines_service': 'claim_lines_visit_admin',
              'claim_lines_drug': 'claim_lines_drug_code'}
    dollars = {'paid_total': 'paid_total_usd', 'paid_service_em_admin': 'paid_visit_admin_usd', 'paid_drug_all': 'paid_drug_code_usd',
               'paid_drug_listed': 'paid_listed_oncology_drug_usd', 'paid_drug_unclassified': 'paid_unclassified_drug_usd', 'paid_other': 'paid_other_usd',
               'median_total_per_present_npi': 'median_paid_total_usd_per_present_npi',
               'median_service_per_npi_with_service': 'median_visit_admin_usd_per_npi_with_any',
               'median_drug_per_npi_with_drug': 'median_drug_code_usd_per_npi_with_any'}
    summary = {(r['join_side'], r['division'], r['year']): r for r in built[TABLES[2]] if r['division_level'] == 'division_group'}
    counts = money = 0
    for side in SIDES:
        for row in read_csv(inputs[f'search_{side}_groups']):
            new = summary[side, groups[row['division']], row['year']]
            counts += sum(str(new[b]) != row[a] for a, b in fields.items())
            # The search wrote 0 where no row of a class was released; this build leaves those cells blank.
            money += sum(not within_a_dollar(str(new[b]) or '0', row[a] or '0') for a, b in dollars.items())
    out.update({'group_year_count_cells_different': counts, 'group_year_dollar_cells_off_by_more_than_1_usd': money})
    links = {(r['npi'], r['center_organization_npi']): r for r in built[TABLES[3]]}
    search_links = read_csv(inputs['search_links'])
    out.update({'link_rows': len(search_links), 'link_pairs_missing': len(set(links) ^ {(r['npi'], r['centre_org_npi']) for r in search_links}),
                'link_cells_different': sum((r['first_year'], r['last_year'], r['claim_lines']) != tuple(str(new[k]) for k in ('first_year', 'last_year', 'claim_lines'))
                                            or not within_a_dollar(new['paid_usd_2018_2024'], r['paid'])
                                            for r in search_links for new in [links.get((r['npi'], r['centre_org_npi']))] if new)})
    return out


def source_checks(rows, people, year_extract):
    """Facts about the month-level extract that the published tables no longer show."""
    months, collapsed, least = Counter(), defaultdict(lambda: [0, 0, 0, 0]), [10 ** 9, 10 ** 9]
    count = cents = repeats = negative = zero = lowest = 0
    seen = set()
    for bill, serv, code, month, patients, claim_lines, paid_cents in rows:
        count += 1
        cents += paid_cents
        negative += paid_cents < 0
        zero += paid_cents == 0
        lowest = min(lowest, paid_cents)
        repeats += (bill, serv, code, month) in seen
        seen.add((bill, serv, code, month))
        least = [min(least[0], patients), min(least[1], claim_lines)]
        cell = collapsed[bill, serv, code, month[:4]]
        cell[:] = [cell[0] + 1, cell[1] + patients, cell[2] + claim_lines, cell[3] + paid_cents]
        if serv in people:
            months[month] += paid_cents
    with gzip.open(year_extract, 'rt', encoding='utf-8', newline='') as stream:
        search = {tuple(row[:4]): [int(v) for v in row[4:]] for row in list(csv.reader(stream))[1:]}
    return {'monthly_rows': count, 'paid_usd': usd(cents), 'duplicate_billing_servicing_code_month_keys': repeats,
            'minimum_beneficiaries_on_a_row': least[0], 'minimum_claim_lines_on_a_row': least[1],
            'rows_with_negative_paid': negative, 'lowest_paid_usd_on_a_row': usd(lowest), 'rows_with_paid_exactly_zero': zero,
            'first_month': min(months), 'last_month': max(months),
            'year_rows_after_collapsing_months': len(collapsed), 'search_year_extract_rows': len(search),
            'year_rows_different_from_search_year_extract': len(set(collapsed) ^ set(search))
                                                            + sum(collapsed[key] != search[key] for key in set(collapsed) & set(search)),
            'servicing_paid_usd_by_month': {month: usd(value) for month, value in sorted(months.items())}}


def state_coverage(year_rows, people):
    """NPIs with a servicing-side row by the HCP's practice state and year; the source itself has no state column."""
    sizes = Counter(row['state'] for row in people.values())
    seen, dollars = defaultdict(set), Counter()
    for row in year_rows:
        if row['medicaid_servicing_paid_usd'] != '':
            state = people[row['npi']]['state']
            seen[state, row['year']].add(row['npi'])
            seen[state, 'any'].add(row['npi'])
            dollars[state, row['year']] += to_cents(row['medicaid_servicing_paid_usd'])
    return {state: {'universe_npis': size, 'npis_with_servicing_row_any_year': len(seen[state, 'any']),
                    'npis_with_servicing_row': {year: len(seen[state, year]) for year in YEARS},
                    'servicing_paid_usd': {year: usd(dollars[state, year]) for year in YEARS}}
            for state, size in sorted(sizes.items())}


def limitations(head, coverage, facts):
    mi, ut, fl, tx = (coverage[state] for state in ('MI', 'UT', 'FL', 'TX'))
    months = facts['servicing_paid_usd_by_month']
    seen = lambda found, year: found['npis_with_servicing_row'][year]
    millions = lambda text: f'${Decimal(text) / 1000000:.2f} million'
    return [
        'The tables cannot support any statement about CAR-T: the source has no row for any CAR-T product or administration code, nationally or for these NPIs, and it holds outpatient and professional claim lines only, so inpatient CAR-T stays are absent.',
        'They do not measure lymphoma product dollars. The drug_code class is a pattern over code numbers that is mostly supportive-care drugs and fluids and also catches a few non-drug codes; the search found lymphoma-specific codes for one NPI in seven years.',
        'They are not a payer mix. Medicaid rows are released per billing-servicing pair, code and month above a threshold, while Medicare totals are annual per NPI, so the two levels must not be divided or compared.',
        'They are not physician income. The paid amount went to the billing NPI, which is a group or hospital for most rows; the self-billed columns show the part billed under the HCP\'s own number.',
        'They cannot rank physicians, divisions, centers or states. Whether an HCP is visible depends on monthly volume per code, on whether the practice writes the HCP\'s NPI on the claim line, and on what the state reports.',
        f'They are not a trend. Years differ in state reporting, and 2024 is preliminary and cut off: servicing-side dollars for December 2024 are {millions(months["2024-12"])} against {millions(months["2023-12"])} for December 2023, and for November {millions(months["2024-11"])} against {millions(months["2023-11"])}.',
        f'Fee-for-service claims, managed-care encounters and CHIP are mixed in every amount and cannot be separated. For encounters the amount is whatever the state reported the plan paid, and a missing line amount is counted as $0 by the publisher: {facts["rows_with_paid_exactly_zero"]:,} of the {facts["monthly_rows"]:,} monthly rows in the extract have a paid amount of exactly $0, and {facts["rows_with_negative_paid"]} have a negative one (lowest {facts["lowest_paid_usd_on_a_row"]} US dollars), which the sums include as they are.',
        'Every amount is a lower bound: rows under 12 claim lines or 12 beneficiaries per month are not released, rows with a blank servicing NPI or the organization\'s NPI in the servicing field cannot be attributed to an HCP, and the visit_admin class leaves out visit-equivalent codes such as G0463, T1015 and Q0084.',
        f'States are missing or unreliable. The source has no state column, so this was measured by the HCP\'s practice state: Michigan-based NPIs have a servicing-side row for {min(mi["npis_with_servicing_row"].values())} to {max(mi["npis_with_servicing_row"].values())} of {mi["universe_npis"]} NPIs a year; Utah falls from {seen(ut, "2022")} NPIs in 2022 to {seen(ut, "2023")} in 2023 and {seen(ut, "2024")} in 2024; Florida dollars move from {millions(fl["servicing_paid_usd"]["2018"])} in 2018 to {millions(fl["servicing_paid_usd"]["2019"])} in 2019 and {millions(fl["servicing_paid_usd"]["2021"])} in 2021; Texas has {seen(tx, "2018")} NPIs in 2018, {max(tx["npis_with_servicing_row"].values())} at its peak and {seen(tx, "2024")} in 2024. The causes are unknown.',
        f'Blank means unobserved, not zero: {head["universe_npis"] - head["npis_observed"]:,} of {head["universe_npis"]:,} universe NPIs have no released row on either side, which does not show that they see no Medicaid patients. 0.00 means released rows exist and their paid amounts sum to exactly $0.',
        'Servicing-side and billing-side amounts overlap on rows where the HCP is both and must not be added; subtract the self-billed amount to get the union.',
        'The division summary copies division labels of version 1.0.0; medians describe only NPIs with a released row, and small cells (core, affiliate_C, the billing side) rest on a handful of NPIs.',
        'A center billing link shows that a center\'s organization NPI billed Medicaid with the HCP as servicing provider on at least one released row. It does not show employment, CAR-T involvement or referral, only one organization NPI per center is matched, and a missing pair is unobserved rather than absent.',
        'The enrollment columns do not discriminate between divisions and are not proof of participation: almost every universe NPI is in the file, New Mexico and Oregon rows carry no NPI, and the search measured 35 to 72 percent of rows without an NPI in Illinois, Minnesota, Michigan, Wisconsin, California, Washington, Nebraska and Texas.',
        'Unknown: whether denied claim lines are included (the documentation is silent), how much of the visit dollars is Medicaid paying Medicare cost-sharing for patients in both programs (an unverified hypothesis), and the licence of the file (none is posted).',
    ]


def sources(inputs, headers, extracts):
    pages = {key: RECORDED[key]['publisher_stated_sha256'] in Path(inputs[f'{key}_page']).read_text(encoding='utf-8', errors='replace')
             for key in RECORDED}
    urls = {'spending': SPENDING_URL, 'enrollment': ENROLLMENT_URL}
    names = {'spending': 'Medicaid Provider Spending by HCPCS', 'enrollment': 'Medicaid Provider Enrollment Segments'}
    landing = {'spending': 'https://opendata.hhs.gov/datasets/medicaid-provider-spending/',
               'enrollment': 'https://opendata.hhs.gov/datasets/medicaid-provider-enrollment-segments/'}
    how = {'spending': 'Remote query over the published parquet file by the search on 2026-11-19 (HTTP range requests, no full download): rows whose servicing or billing NPI is a universe NPI, kept at billing NPI x servicing NPI x code x month. This module read the frozen extract and made one HEAD request for the headers.',
           'enrollment': 'Remote query over the published parquet file by the search on 2026-11-19: every segment whose NPI is a universe NPI, uncollapsed. This module read the frozen extract and made one HEAD request for the headers.'}
    out = []
    for key in RECORDED:
        head = headers[key]
        out.append({
            'name': names[key], 'publisher': 'U.S. Department of Health and Human Services, HHS Open Data',
            'url': urls[key], 'landing_page': landing[key], 'release_or_version': RECORDED[key]['release'],
            'retrieved': '2026-11-19', 'how_obtained': how[key], 'terms_class': 'a',
            'terms_note': 'No licence or terms statement is posted; treated as a work of the United States Government (17 U.S.C. 105). Attribute to HHS with the release date and file URL.',
            'source_rows_per_search': RECORDED[key]['source_rows'],
            'http_headers_recorded_by_search': {k: RECORDED[key][k] for k in ('http_last_modified', 'http_etag', 'bytes')},
            'http_headers_read_by_this_module': head,
            'http_etag_unchanged_since_search': head.get('http_etag') == RECORDED[key]['http_etag'] if 'http_etag' in head else 'not re-read',
            'publisher_stated_sha256': RECORDED[key]['publisher_stated_sha256'],
            'publisher_stated_sha256_status': 'unverified: the file was never downloaded whole, so the hash was not computed from its bytes',
            'publisher_stated_sha256_found_on_saved_dataset_page': pages[key],
            'release_label_note': 'The file under the release path was last modified after the release date; both are recorded.' if key == 'spending' else '',
            'publisher_documentation_as_read_by_search': SOURCE_FACTS[key],
            'interim_extract': load(extracts[key].with_name(extracts[key].name.replace('.csv.gz', '.meta.json'))),
        })
    out.append({'name': 'HCP universe and CAR-T center tables', 'publisher': 'this project',
                'url': 'Data/processed/2026-10-24/hcp_divisions.csv; Data/processed/2026-10-24/cart_centers.csv',
                'release_or_version': f'snapshot {UNIVERSE_SNAPSHOT}, label version {LABEL_VERSION}', 'retrieved': 'local',
                'how_obtained': 'Read from the tracked tables.', 'terms_class': 'project'})
    return out


def build():
    inputs = staged_inputs()
    extracts = {'spending': extract(inputs['monthly_rows'], 'monthly_universe_rows', MONTHLY_SQL,
                                    ['billing_npi', 'servicing_npi', 'procedure_code', 'month', 'beneficiaries', 'claim_lines', 'paid_cents']),
                'enrollment': extract(inputs['enrollment_segments'], 'enrollment_segments_universe', SEGMENT_SQL,
                                      ['npi', 'state', 'start', 'end', 'status_code', 'status', 'plan_code', 'plan', 'provider_type'])}
    year_extract = extract(inputs['year_extract'], 'search_year_extract', YEAR_SQL,
                           ['billing_npi', 'servicing_npi', 'procedure_code', 'year', 'month_rows', 'beneficiaries', 'claim_lines', 'paid_cents'])
    headers = {'spending': read_headers('spending', SPENDING_URL), 'enrollment': read_headers('enrollment', ENROLLMENT_URL)}
    people, center_rows = universe(), list(centers().values())
    rows = list(monthly_rows(extracts['spending']))
    foreign = sum(serv not in people and bill not in people for bill, serv, *_ in rows)
    assert not foreign, 'Extract rows that name no universe NPI'
    built = tables(rows, people, center_rows, segments_by_npi(extracts['enrollment']))
    summaries = [publish(table, built[table], COLUMNS[table], KEYS[table]) for table in TABLES]
    published = [read_csv(OUT / f'{table}.csv') for table in TABLES]
    head = headline(*published, people)
    facts = source_checks(rows, people, year_extract)
    coverage = state_coverage(published[0], people)
    paths = list(inputs.values()) + [p for p in HEADERS.glob('*') if p.is_file()]
    write_module_metadata(MODULE, {
        'tables': summaries,
        'sources': sources(inputs, headers, extracts),
        'headline': head,
        'limitations': limitations(head, coverage, facts),
        'defects_fixed': [
            'Dollars are summed in whole cents and published with two decimals. The search rounded float sums to whole dollars, which made 40 cells depend on summation order.',
            'The active-enrollment flag is computed per segment (status begins with ACTIVE and end date blank or on or after 2024-01-01). The search judged collapsed groups by their latest non-blank end date, which set 145 NPIs to 0 that have an open-ended active segment.',
            'A flag for a MEDICAID or CHIP plan segment, computed by the search but not written, is published, with a second active flag limited to those plans.',
            'Code classes are named visit_admin and drug_code instead of service and drug, and no column is called a product column.',
            'Counts of NPIs with rows of a class are published beside counts of NPIs with paid above $0, which the search had labelled as rows.',
            'The four summary files are merged into one table keyed by join side, division level, division and year, with a label_version column; the drug share, the task-list sum and the constant-zero share columns are not published.',
            'Per-year values are published for every year, not only 2024, with months present, source rows and claim lines.',
            'The center link table uses the house spelling, carries the center CCN and a count of years present, and drops the copied division label and hospital name.',
            'A status column in words (medicaid_data_status) separates unobserved NPIs from observed ones; class dollars are blank, not 0, where no row of the class was released, including in the division summary.',
            'The copied division label is not repeated in the per-NPI tables.',
            'The month-level extract and the uncollapsed enrollment segments are frozen under Data/raw and rewritten under Data/interim with hashes; the HTTP Last-Modified and ETag of both source files are recorded.',
        ],
        'defects_open': [
            'The visit_admin class is the search\'s fixed code list and leaves out G0463, T1015, Q0084, other 96xxx administration codes, consultations, discharge, emergency and critical-care codes and 99201; it was kept unchanged so the report figures reproduce.',
            'The drug_code class is a pattern, not a drug list: it includes Q5001-Q5003, C9803, a few S0 codes and intravenous fluids. Kept unchanged for the same reason.',
            'The code field holds values that are not procedure codes (four-digit revenue-code-like values and state-local codes); they fall in the other class.',
            '2024 is preliminary and its last months are largely missing; medicaid_servicing_months_present shows the months each NPI has, but no column corrects for it.',
            'State reporting gaps (Michigan, Utah, Florida, Texas, Massachusetts) are inside every figure and are not flagged per row because the source has no state column.',
            'A few NPIs hold a large part of the drug_code dollars (the search names three California NPIs with $88.6 million of the referring division\'s seven-year total); no outlier flag is published.',
            'Only one organization NPI per center is matched in the link table, so 74 of 173 centers have no pair; center_in_hcp_hospital_ccns marks the pairs whose center is not among the HCP\'s hospital CCNs, which were not checked further.',
            'The enrollment flags count any state; medicaid_enrollment_active_2024_any_state also counts any plan and do-not-pay statuses. Far-future end dates behave as open-ended. Which variant to use is left to the reader.',
            'The publisher-stated SHA-256 of each source file was not verified, and the spending file was modified after its release date.',
            'Part of the office-visit dollars may be Medicaid paying Medicare cost-sharing; this is a hypothesis of the search and was not verified.',
            f'{facts["rows_with_negative_paid"]} monthly source rows carry a negative paid amount (lowest {facts["lowest_paid_usd_on_a_row"]} US dollars) and are summed as they are; no NPI x year amount is negative, and no flag marks the rows.',
            'medicaid_enrollment_states lists states per NPI from the enrollment file. It is a federal open-data fact and not a copy of the practice state in hcp_divisions.csv, but it usually contains that state; whether it belongs in a per-NPI table is left to the maintainer.',
        ],
        'code_class_rules': {**CLASS_RULES, 'search_subclass_rule_sql_verbatim': CODE_SUBCLASS_RULE_SQL,
                             'search_class_rule_sql_verbatim': CODE_CLASS_RULE_SQL,
                             'search_class_names': {'service': 'visit_admin', 'drug': 'drug_code', 'other': 'other'}},
        'division_groups': DIVISION_GROUPS, 'label_version': LABEL_VERSION,
        'rounding_rule': 'Amounts are summed as whole cents and written with two decimals. Whole-dollar figures and medians round a half up.',
        'rows_removed': {'count': 0, 'note': 'No extract row is dropped. A row naming universe NPIs on both sides is counted once on each side.'},
        'source_checks': facts,
        'table_fingerprints': {table: fingerprint(rows, [c['column'] for c in COLUMNS[table]]) for table, rows in zip(TABLES, published)},
        'table_fingerprints_note': 'Order-free digest of every cell of each published table; check() compares it with the FINGERPRINTS constant of the module.',
        'reconciliation_with_search_tables': reconcile(built, inputs),
        'coverage_by_practice_state': coverage,
        'release_rule': 'A row is released only when it has at least 12 claim lines and at least 12 beneficiaries (publisher documentation; the minimum of both is 12 on the extract).',
        'input_note': 'The month-level spending rows and the uncollapsed enrollment segments are the second-pass re-pull of 2026-11-19, made against the same ETag as the search extract; the search\'s year-collapsed extract is frozen for comparison only.',
    }, paths)
    if dict(zip(TABLES, (fingerprint(rows, [c['column'] for c in COLUMNS[table]]) for table, rows in zip(TABLES, published)))) != FINGERPRINTS:
        print('MEDICAID TABLES DIFFER FROM THE RECORDED DIGESTS; see table_fingerprints in the metadata fragment', flush=True)
    print(f'MEDICAID BUILT {len(built[TABLES[0]])} NPI-YEAR ROWS, {head["npis_observed"]} OF {head["universe_npis"]} NPIS OBSERVED, '
          f'{head["servicing_paid_usd_2018_2024"]} USD SERVICING, {len(built[TABLES[3]])} CENTER LINKS', flush=True)


if __name__ == '__main__':
    build()
