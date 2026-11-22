"""Referral and patient-flow extension: longitudinal CareSet links 2017-2022 and a 2015 CMS baseline.

Six tables of the 2026-11-20 snapshot. Four are derived from the six local CareSet DocGraph Hop Teaming edge tables
of the 2026-10-24 snapshot (Medicare fee-for-service shared-patient pairs, 2017-2022, kept local): the panel of
universe NPI x CAR-T center x year links, a per-NPI longitudinal summary, per-NPI yearly link changes, and a
per-center yearly referrer base. One is a 2015 baseline from the CMS Physician Shared Patient Patterns file
(180-day window) streamed once from the NBER mirror, of which only a filtered extract is kept locally. The last is
a year-over-year summary by division group.

A link to a center is the rule of `careset.aggregate` for "to centers": a directed edge from a universe NPI to a
receiver that is a center organization NPI (that center's CCN) or a universe NPI whose hospital CCNs include
center CCNs (each of those centers). Nothing is requested from CareSet.
"""
from __future__ import annotations

import csv
import hashlib
import struct
import urllib.request
import zlib
from collections import Counter, defaultdict
from pathlib import Path

from .. import careset
from .base import (DIVISION_GROUPS, EARLIER_SNAPSHOTS, INTERIM, OUT, PROCESSED, RAW, ROOT, SNAPSHOT, UNIVERSE_SNAPSHOT,
                   column, dump, duplicates, write_csv, existing_keys, fetch, load, log_local, new_rows, now, publish, read_csv, sha,
                   write_module_metadata)

MODULE = 'referral'
PANEL, NPI_SUMMARY, NPI_CHANGES, CENTER_BASE, BASELINE, GROUPS, COVERAGE, CORE_LINKS, CORE_SENDERS, CORE_REACH = (
    'shared_patient_npi_center_flows_2017_2022', 'shared_patient_npi_longitudinal_2017_2022',
    'shared_patient_npi_link_changes_2017_2022', 'shared_patient_center_referrer_base_2017_2022',
    'shared_patient_npi_center_baseline_2009_2015', 'shared_patient_division_group_flows_2017_2022',
    'shared_patient_baseline_coverage_2009_2015', 'shared_patient_npi_core_hcp_links_2017_2022',
    'shared_patient_core_hcp_senders_2017_2022', 'shared_patient_core_reach_by_group_2017_2022')
TABLES = [PANEL, NPI_SUMMARY, NPI_CHANGES, CENTER_BASE, BASELINE, GROUPS, COVERAGE, CORE_LINKS, CORE_SENDERS, CORE_REACH]
YEARS = [2017, 2018, 2019, 2020, 2021, 2022]
FLOOR = 11   # CareSet and CMS both drop provider pairs with fewer than 11 shared patients
EDGES = ROOT / 'Data' / 'interim' / UNIVERSE_SNAPSHOT / 'careset'
EARLIER = PROCESSED / UNIVERSE_SNAPSHOT
DOCGRAPH = ROOT.parent / 'DocGraph_{year}_NonCommercial' / 'DocGraph_Hop_Teaming_{year}.csv'
LICENCE = ('Derived from CareSet DocGraph Hop Teaming (CareSet Systems, Inc.) under CC BY-NC-SA 4.0 and the CareSet '
           'Noncommercial Data License Addendum; shared under the same licence, non-commercial use only, no '
           're-identification of any individual')
PREFIX = 8   # hexadecimal characters of a source SHA-256 kept on each row; the full hash is in the metadata
NBER_YEARS = [2009, 2010, 2011, 2012, 2013, 2014, 2015]
NBER_URL = 'https://data.nber.org/physician-shared-patient-patterns/{year}/pspp{year}_180.zip'
NBER_PAGE = 'https://www.nber.org/research/data/physician-shared-patient-patterns-data'
NBER_DESC = 'https://data.nber.org/physician-shared-patient-patterns/2015/desc/pspp2015_180/desc.txt'
NBER_TECH = 'https://data.nber.org/physician-shared-patient-patterns/docs/physician_shared_patient_patterns_technical_requirements.pdf'
CMS_FAQ = 'https://www.cms.gov/Regulations-and-Guidance/Legislation/FOIA/Referral-Data-FAQs.html'
# Date range and record count of each 180-day file, from the CMS technical requirements (NBER docs folder).
NBER_RANGES = {2009: '01/01/2009 - 07/01/2010', 2010: '01/01/2010 - 07/01/2011', 2011: '01/01/2011 - 07/01/2012',
               2012: '01/01/2012 - 07/01/2013', 2013: '01/01/2013 - 07/01/2014', 2014: '01/01/2014 - 07/01/2015',
               2015: '01/01/2015 - 10/01/2015'}
CMS_RECORDS = {2009: 119270681, 2010: 124844963, 2011: 129968445, 2012: 133042645, 2013: 135071996,
               2014: 138614191, 2015: 65739582}
# The npi1 coverage count was added after 2014 and 2015 had first been streamed; both were streamed a second time.
COVERAGE_MEASURED, COVERAGE_NOT_MEASURED = 'measured', 'not_measured'
DIRECTION = ('Directed. The CMS technical requirements (NBER docs folder) say "A shared relationship is presumed from '
             'physician A to physician B" when B\'s claim follows A\'s; output is Initial Physician NPI (NPI 1), '
             'Secondary Physician NPI (NPI 2). npi1 is therefore the earlier provider, read here as the sender. '
             'Same-day relationships are assigned to the lesser of the two NPIs, so their direction is arbitrary.')
NBER_SUM = 'https://data.nber.org/physician-shared-patient-patterns/2015/desc/pspp2015_180/sum.txt'
NBER_EXTRACT = str(INTERIM / MODULE / 'pspp{year}_180_universe_extract.csv')
NBER_COVERAGE = str(INTERIM / MODULE / 'pspp{year}_180_universe_npi1_pairs.csv')
NBER_REJECTED = str(INTERIM / MODULE / 'pspp{year}_180_lines_not_data.txt')
NBER_RECORD = str(RAW / MODULE / 'pspp{year}_180_stream.meta.json')
NBER_SOURCE = 'cms_pspp_{year}_180'
# Compressed sizes in bytes from HEAD requests of 2026-11-20; only the 2015 180-day file was transferred.
NBER_SIZES = {'2009_180': 1199696142, '2010_180': 1255329761, '2011_180': 1305967119, '2012_180': 1336217767,
              '2013_180': 1356763297, '2014_180': 1391141659, '2015_180': 664105312, '2015_90': 566928093,
              '2015_60': 500078949, '2015_30': 355555833}
# Headline figures quoted in the README; `check` recomputes each from the published rows.
EXPECTED_HEADLINE = {
    'panel_rows': 147226,
    'panel_npis': 13802,
    'panel_centers': 161,
    'panel_rows_by_year': {'2017': 22861, '2018': 24620, '2019': 25597, '2020': 23726, '2021': 25143, '2022': 25279},
    'npis_by_link_status': {'linked': 13802, 'no_center_link': 1093, 'no_outgoing_edge': 2615},
    'npis_linked_all_six_years': 8580,
    'npis_primary_center_outside_own_hrr': 5920,
    'links_gained_by_year': {'2018': 5937, '2019': 5679, '2020': 4284, '2021': 5602, '2022': 5098},
    'links_lost_by_year': {'2018': 4178, '2019': 4702, '2020': 6155, '2021': 4185, '2022': 4962},
    'centers_observed_by_year': {'2017': 158, '2018': 159, '2019': 158, '2020': 158, '2021': 159, '2022': 159},
    'baseline_by_year': {'2009': [16023, 6847, 5157, 1807755], '2010': [17243, 7248, 5558, 1959688], '2011': [19086, 7716, 6038, 2187099], '2012': [20581, 8198, 6553, 2390758], '2013': [21821, 8717, 7132, 2536346], '2014': [23270, 9240, 7794, 2736979], '2015': [15593, 8363, 7413, 1446485]},
    'coverage': {'2009 affiliates': ['3857', 3663, 3559], '2009 core': ['204', 195, 193], '2009 other': ['1040', 915, 824], '2009 referring': ['2767', 2533, 2271], '2010 affiliates': ['4116', 3892, 3787], '2010 core': ['224', 215, 214], '2010 other': ['1086', 962, 861], '2010 referring': ['2916', 2684, 2386], '2011 affiliates': ['4388', 4185, 4069], '2011 core': ['238', 230, 228], '2011 other': ['1121', 989, 886], '2011 referring': ['3042', 2810, 2533], '2012 affiliates': ['4672', 4479, 4381], '2012 core': ['248', 243, 240], '2012 other': ['1169', 1034, 922], '2012 referring': ['3175', 2922, 2655], '2013 affiliates': ['4999', 4789, 4688], '2013 core': ['273', 266, 264], '2013 other': ['1220', 1075, 965], '2013 referring': ['3335', 3074, 2800], '2014 affiliates': ['5311', 5104, 5020], '2014 core': ['291', 283, 282], '2014 other': ['1266', 1098, 992], '2014 referring': ['3479', 3205, 2946], '2015 affiliates': ['5211', 4931, 4754], '2015 core': ['275', 270, 270], '2015 other': ['1197', 962, 794], '2015 referring': ['3384', 3014, 2545]},
    'core_links_rows': 6597,
    'core_sender_rows': 2079,
    'core_hcps_receiving_by_year': {'2017': 291, '2018': 319, '2019': 329, '2020': 335, '2021': 353, '2022': 355},
    'core_reach': {'2017 core': [219, 21179], '2017 affiliates': [699, 56195], '2017 referring': [50, 3318], '2017 other': [42, 3824], '2022 core': [298, 34711], '2022 affiliates': [806, 78211], '2022 referring': [43, 2955], '2022 other': [17, 758]},
    'change_rows_by_status': {'linked': 68001, 'not_observed': 2699},
    'panel_patients_by_year': {'2017': 2622608, '2018': 2862561, '2019': 3073482, '2020': 2831682, '2021': 3100494, '2022': 3231762},
    'groups': {'affiliates 2017': [5707, '1782124', '0.271'], 'affiliates 2022': [7365, '2342776', '0.202'], 'core 2017': [342, '128818', '0.176'], 'core 2022': [408, '187293', '0.071'], 'other 2017': [986, '159935', '0.505'], 'other 2022': [1075, '110758', '0.489'], 'referring 2017': [3104, '405227', '0.546'], 'referring 2022': [3547, '439559', '0.490']},
}
# SHA-256 of each published table as built; `check` fails on any changed byte.
FINGERPRINTS = {
    PANEL: '906278ed82a4d9a299fbfce76b47b2bb9cb364ce536388e1e8bf24351f8f67ed',
    NPI_SUMMARY: 'e70e0184a5cee6747e1b7eb8fedb3021f920a2fd4bc075744aaacef83728773e',
    NPI_CHANGES: 'ced8fafaec09dbf12bb23e299f99342d1cd0bcbaf0cd91c204241d5d4651827e',
    CENTER_BASE: '0f7c77949dd8506012d4764b656f6e224765bbd9725813a2b738f700a5e4e3b2',
    BASELINE: '8717eb93c85079d23d6f54cfe15def5d4868aea87954dbb8db86ec88b0209378',
    GROUPS: '934c52f85e91c3f4582e38db78886dfa2b1e237a2240222d3a62cc83d5fd1854',
    COVERAGE: '191255bd4564f2996a812a8f1c1554fed5b7b270258f9e1596396ab1a980cc0c',
    CORE_LINKS: 'b49c3084b85dfbe702c8b5871e22a97af9a7a8c82a3ef3e6152c231bd0952b74',
    CORE_SENDERS: '3da7635511f18b30924c8a64360b6528528d6517811ed1540f5f21d675900740',
    CORE_REACH: '216fec2848b25fc8b00cdf1018f08650de53f6a9fd7623135ddab408ede4b0b1',
}
# Short status tokens; each column description spells them out.
GAINED_BLANK, COMPARED = 'first_year', 'compared'
LINKED, NOT_OBSERVED = 'linked', 'not_observed'
CENTER_OBSERVED, CENTER_NOT_OBSERVED = 'observed', 'not_observed'
NPI_STATUSES = ['linked', 'no_center_link', 'no_outgoing_edge']
BASELINE_LINK = {True: 'linked_2017', False: 'not_linked_2017'}
LICENCE_NOTE = ('Licence of every row: CC BY-NC-SA 4.0 with the CareSet Noncommercial Data License Addendum, '
                'attribution to CareSet Systems, Inc., non-commercial use only. ')
# Columns a per-NPI or per-center table of this module must not carry (readers join on npi or ccn instead).
IDENTIFYING = {'last_name', 'first_name', 'hospital_name', 'dba_name', 'city', 'state', 'zip5', 'address', 'email',
               'phone', 'division', 'hrr', 'hrr_city'}
REGION = {True: 'outside', False: 'inside', None: 'unknown'}


# ---------------------------------------------------------------- small pure functions

def context(processed=EARLIER):
    """The careset.py context (divisions, HRRs, center CCNs of universe NPIs, organization NPI to CCN)."""
    return careset.load_context(Path(processed))


def centers_of(receiver, ctx):
    """CCNs an edge reaches: careset.target_centers, the rule behind the published "to centers" features."""
    return careset.target_centers(receiver, ctx)


def accumulate(edges, ctx, year, panel, recon):
    """Add one year's directed edges to the panel cells and to the per-NPI reconciliation totals."""
    for e in edges:
        a = e['from_npi']
        if a not in ctx['division']:
            continue
        targets = centers_of(e['to_npi'], ctx)
        if not targets:
            continue
        patients, wait = int(float(e['patient_count'] or 0)), float(e['average_day_wait'] or 0)
        total = recon[(a, year)]
        total[0] += patients
        total[1] |= targets
        total[2].append(wait)
        for ccn in targets:
            cell = panel[(a, ccn, year)]
            cell[0] += patients
            cell[1] += patients / len(targets)
            cell[2] += 1
            cell[3] += wait * patients


def region(npi_hrr, center_hrr):
    if not npi_hrr or not center_hrr:
        return None
    return npi_hrr != center_hrr


def hhi(values):
    """Herfindahl index of shares (0 to 1) of positive values; None when nothing is positive."""
    total = sum(values)
    return round(sum((v / total) ** 2 for v in values), 4) if total else None


def changes(links):
    """Per year: (gained, lost, retained) center links against the previous year, None in the first year."""
    out = {}
    for year in YEARS:
        if year == YEARS[0]:
            out[year] = None
            continue
        now_, before = links.get(year, set()), links.get(year - 1, set())
        out[year] = (len(now_ - before), len(before - now_), len(now_ & before))
    return out


def fmt(value, places=None):
    if value is None:
        return ''
    return f'{value:.{places}f}' if places is not None else str(value)


def compact(value):
    """Three decimals without trailing zeros: 26.000 -> 26, 15.500 -> 15.5."""
    text = f'{value:.3f}'.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


CORE_PAIRS = INTERIM / MODULE / 'core_hcp_pairs_2017_2022.csv'   # pair-level, local only
GROUP_ORDER = ['core', 'affiliates', 'referring', 'other']
CAVEAT = ('Counts are sums of pair counts, each of at least 11 Medicare fee-for-service patients shared in the year, '
          'not distinct patients; a shared patient is not a referral, and nothing here is specific to CAR-T. ')


def core_pairs(edges, ctx, year, out):
    """Directed pairs from a universe NPI to a core HCP: (sender, core, year) -> [patients, wait, at a center]."""
    for e in edges:
        a, b = e['from_npi'], e['to_npi']
        if a in ctx['division'] and ctx['division'].get(b) == 'core' and a != b:
            out[(a, b, year)] = [int(float(e['patient_count'] or 0)), float(e['average_day_wait'] or 0),
                                 bool(ctx['center_ccns'].get(b))]
        yield e


def core_tables(pairs, ctx, recon):
    """Per sender-year, per core-year and per group-year aggregates of the core pairs."""
    group = {n: DIVISION_GROUPS[d] for n, d in ctx['division'].items()}
    senders, receivers = defaultdict(list), defaultdict(dict)
    for (a, b, y), v in pairs.items():
        senders[(a, y)].append((b, v))
        receivers[(b, y)][a] = v[0]
    links = []
    for (a, y), rows in sorted(senders.items()):
        patients = sum(v[0] for _, v in rows)
        at_centers = sum(v[0] for _, v in rows if v[2])
        to_centers = recon.get((a, y), [0])[0]
        links.append({'npi': a, 'year': str(y), 'core_hcps': str(len(rows)), 'patients_to_core': str(patients),
                      'patients_to_core_at_centers': str(at_centers),
                      'share_of_center_patients_via_core': fmt(at_centers / to_centers, 3) if to_centers else '',
                      'mean_day_wait_weighted': fmt(sum(v[0] * v[1] for _, v in rows) / patients, 1)})
    core = sorted(n for n, d in ctx['division'].items() if d == 'core')
    received = []
    for b in core:
        for y in YEARS:
            now_, before = receivers.get((b, y), {}), receivers.get((b, y - 1), {}) if y > YEARS[0] else None
            if not now_ and not (before and set(before)):
                continue   # a year with no sender and no lost sender carries no information
            row = {'npi': b, 'year': str(y), 'year_status': LINKED if now_ else NOT_OBSERVED,
                   'comparison_status': GAINED_BLANK if before is None else COMPARED}
            for name in ['senders', 'patients_from_universe', 'patients_from_core', 'patients_from_affiliates',
                         'patients_from_other', 'senders_core', 'senders_affiliates', 'senders_other',
                         'outside_hrr_patient_share', 'senders_without_hrr', 'sender_hhi', 'senders_gained',
                         'senders_lost']:
                row[name] = ''
            if before is not None:
                row['senders_lost'] = str(len(set(before) - set(now_)))
                row['senders_gained'] = str(len(set(now_) - set(before))) if now_ else ''
            if now_:
                total = sum(now_.values())
                by = Counter()
                count = Counter()
                for a, n in now_.items():
                    by[group[a]] += n
                    count[group[a]] += 1
                known = {a: n for a, n in now_.items() if region(ctx['hrr'].get(a, ''), ctx['hrr'].get(b, '')) is not None}
                outside = sum(n for a, n in known.items() if region(ctx['hrr'][a], ctx['hrr'][b]))
                row.update({'senders': str(len(now_)), 'patients_from_universe': str(total),
                            'patients_from_core': str(by['core']), 'patients_from_affiliates': str(by['affiliates']),
                            'patients_from_other': str(by['other']), 'senders_core': str(count['core']),
                            'senders_affiliates': str(count['affiliates']), 'senders_other': str(count['other']),
                            'outside_hrr_patient_share': fmt(outside / sum(known.values()), 3) if known else '',
                            'senders_without_hrr': str(len(now_) - len(known)),
                            'sender_hhi': fmt(hhi(list(now_.values())), 4)})
            received.append(row)
    return links, received, reach_rows(links, ctx)


def reach_rows(links, ctx):
    """Per year and sender division group: senders linked to at least one core HCP and their patients."""
    group = {n: DIVISION_GROUPS[d] for n, d in ctx['division'].items()}
    sizes = Counter(group.values())
    senders, patients, pairs = Counter(), Counter(), Counter()
    for r in links:
        key = (r['year'], group[r['npi']])
        senders[key] += 1
        patients[key] += int(r['patients_to_core'])
        pairs[key] += int(r['core_hcps'])
    return [{'year': str(y), 'division_group': g, 'npis_in_group': str(sizes[g]),
             'senders_to_core': str(senders[(str(y), g)]), 'sender_share_of_group': fmt(senders[(str(y), g)] / sizes[g], 3),
             'sender_core_pairs': str(pairs[(str(y), g)]), 'patients_to_core': str(patients[(str(y), g)])}
            for y in YEARS for g in GROUP_ORDER]


# ---------------------------------------------------------------- derived tables from published panel rows

def links_by_npi(panel_rows):
    links = defaultdict(lambda: defaultdict(set))
    for r in panel_rows:
        links[r['npi']][int(r['year'])].add(r['ccn'])
    return links


def npi_summary_row(npi, rows, ctx, center_hrr, sends):
    """One NPI's longitudinal summary from its panel rows (strings as published)."""
    base = {'npi': npi, 'link_status': '', 'first_year_linked': '', 'last_year_linked': '', 'years_linked': '',
            'persistence_in_span': '', 'link_retention_rate': '', 'distinct_centers_ever': '',
            'link_years_total': '', 'primary_center_ccn': '', 'primary_center_share': '',
            'primary_center_years': '', 'primary_center_region': ''}
    if not rows:
        base['link_status'] = NPI_STATUSES[1] if npi in sends else NPI_STATUSES[2]
        return base
    years = sorted({int(r['year']) for r in rows})
    by_year = defaultdict(set)
    allocated, center_years = Counter(), defaultdict(set)
    for r in rows:
        by_year[int(r['year'])].add(r['ccn'])
        allocated[r['ccn']] += float(r['patients_allocated'])
        center_years[r['ccn']].add(int(r['year']))
    retained = sum(len(by_year[y] & by_year[y - 1]) for y in YEARS[1:])
    exposed = sum(len(by_year[y - 1]) for y in YEARS[1:])
    primary = min(allocated, key=lambda c: (-round(allocated[c], 3), -len(center_years[c]), c))
    total = sum(allocated.values())
    base.update({'link_status': NPI_STATUSES[0], 'first_year_linked': str(years[0]), 'last_year_linked': str(years[-1]),
                 'years_linked': str(len(years)),
                 'persistence_in_span': fmt(len(years) / (years[-1] - years[0] + 1), 3),
                 'link_retention_rate': fmt(retained / exposed, 3) if exposed else '',
                 'distinct_centers_ever': str(len(allocated)), 'link_years_total': str(len(rows)),
                 'primary_center_ccn': primary, 'primary_center_share': fmt(allocated[primary] / total, 3),
                 'primary_center_years': str(len(center_years[primary])),
                 'primary_center_region': REGION[region(ctx['hrr'].get(npi, ''), center_hrr.get(primary, ''))]})
    return base


def change_rows(npi, links):
    out = []
    for year, delta in changes(links).items():
        linked = bool(links.get(year))
        row = {'npi': npi, 'year': str(year), 'year_status': LINKED if linked else NOT_OBSERVED,
               'links_gained': '', 'links_lost': '', 'links_retained': '',
               'comparison_status': GAINED_BLANK if delta is None else COMPARED}
        if delta is not None:
            row.update(dict(zip(['links_gained', 'links_lost', 'links_retained'], map(str, delta))))
        if linked or (delta is not None and delta[1]):
            out.append(row)   # an unlinked year without lost links carries no information
    return out


def center_rows(panel_rows, ctx, center_hrr):
    """Per center and year: referrer base, concentration, out-of-region share, referrer turnover."""
    cells = defaultdict(dict)
    for r in panel_rows:
        cells[(r['ccn'], int(r['year']))][r['npi']] = int(r['patients'])
    out = []
    for ccn in sorted(center_hrr):
        for year in YEARS:
            referrers = cells.get((ccn, year), {})
            before = set(cells.get((ccn, year - 1), {})) if year > YEARS[0] else None
            row = dict.fromkeys(CENTER_COLUMNS, '')
            row.update({'ccn': ccn, 'year': str(year),
                        'year_status': CENTER_OBSERVED if referrers else CENTER_NOT_OBSERVED,
                        'comparison_status': GAINED_BLANK if before is None else COMPARED})
            if before is not None:
                row['lost_referrers'] = str(len(before - set(referrers)))
                row['new_referrers'] = str(len(set(referrers) - before)) if referrers else ''
                row['retained_referrers'] = str(len(set(referrers) & before)) if referrers else ''
            if referrers:
                proxies = {n: p for n, p in referrers.items() if ctx['division'].get(n) == 'referring_proxy'}
                known = {n: p for n, p in referrers.items() if region(ctx['hrr'].get(n, ''), center_hrr[ccn]) is not None}
                outside = sum(p for n, p in known.items() if region(ctx['hrr'][n], center_hrr[ccn]))
                row.update({'referrers': str(len(referrers)), 'patients_from_referrers': str(sum(referrers.values())),
                            'referring_proxy_referrers': str(len(proxies)),
                            'patients_from_referring_proxy': str(sum(proxies.values())),
                            'referrer_hhi': fmt(hhi(list(referrers.values())), 4),
                            'top_referrer_share': fmt(max(referrers.values()) / sum(referrers.values()), 3),
                            'outside_hrr_patient_share': fmt(outside / sum(known.values()), 3) if known else '',
                            'referrers_without_hrr': str(len(referrers) - len(known))})
            out.append(row)
    return out


def group_rows(panel_rows, change_table, ctx, center_hrr):
    """Per division group and year: linked referrers, links, allocated patients, out-of-region share, turnover."""
    group = {n: DIVISION_GROUPS[d] for n, d in ctx['division'].items()}
    sizes = Counter(group.values())
    linked, links, allocated, outside, known = (defaultdict(set), Counter(), Counter(), Counter(), Counter())
    for r in panel_rows:
        key = (group[r['npi']], int(r['year']))
        linked[key].add(r['npi'])
        links[key] += 1
        value = float(r['patients_allocated'])
        allocated[key] += value
        side = region(ctx['hrr'].get(r['npi'], ''), center_hrr.get(r['ccn'], ''))
        if side is not None:
            known[key] += value
            outside[key] += value if side else 0
    gained, lost = Counter(), Counter()
    for r in change_table:
        if r['links_gained']:
            gained[(group[r['npi']], int(r['year']))] += int(r['links_gained'])
            lost[(group[r['npi']], int(r['year']))] += int(r['links_lost'])
    out = []
    for name in sorted(sizes):
        for year in YEARS:
            key = (name, year)
            out.append({'division_group': name, 'year': str(year), 'npis_in_group': str(sizes[name]),
                        'npis_linked': str(len(linked[key])), 'center_links': str(links[key]),
                        'patients_allocated_to_centers': str(round(allocated[key])),
                        'outside_hrr_patient_share': fmt(outside[key] / known[key], 3) if known[key] else '',
                        'links_gained': str(gained[key]) if year > YEARS[0] else '',
                        'links_lost': str(lost[key]) if year > YEARS[0] else '',
                        'comparison_status': GAINED_BLANK if year == YEARS[0] else COMPARED})
    return out


def headline(tables, ctx):
    """Headline numbers recomputed from the published rows."""
    panel, summary, changes_, base, baseline, groups, coverage, core_links, core_senders, reach = (tables[t] for t in TABLES)
    linked = [r for r in summary if r['link_status'] == NPI_STATUSES[0]]
    out = {'panel_rows': len(panel), 'panel_npis': len({r['npi'] for r in panel}),
           'panel_centers': len({r['ccn'] for r in panel}),
           'panel_rows_by_year': {str(y): sum(r['year'] == str(y) for r in panel) for y in YEARS},
           'npis_by_link_status': dict(sorted(Counter(r['link_status'] for r in summary).items())),
           'npis_linked_all_six_years': sum(r['years_linked'] == '6' for r in linked),
           'npis_primary_center_outside_own_hrr': sum(r['primary_center_region'] == REGION[True] for r in linked),
           'links_gained_by_year': {str(y): sum(int(r['links_gained']) for r in changes_ if r['year'] == str(y))
                                    for y in YEARS[1:]},
           'links_lost_by_year': {str(y): sum(int(r['links_lost']) for r in changes_ if r['year'] == str(y))
                                  for y in YEARS[1:]},
           'centers_observed_by_year': {str(y): sum(r['year'] == str(y) and r['year_status'] == CENTER_OBSERVED
                                                    for r in base) for y in YEARS},
           'baseline_by_year': {str(y): [sum(r['year'] == str(y) for r in baseline),
                                         len({r['npi'] for r in baseline if r['year'] == str(y)}),
                                         len({r['npi'] for r in baseline if r['year'] == str(y)
                                              and r['careset_2017_link'] == BASELINE_LINK[True]}),
                                         sum(int(r['beneficiaries']) for r in baseline if r['year'] == str(y))]
                                for y in NBER_YEARS},
           'coverage': {f"{r['year']} {r['division_group']}": [r['npis_as_npi1'], int(r['npis_as_npi1_kept']),
                                                               int(r['npis_linked'])] for r in coverage},
           'core_links_rows': len(core_links), 'core_sender_rows': len(core_senders),
           'core_hcps_receiving_by_year': {str(y): sum(r['year'] == str(y) and r['year_status'] == LINKED
                                                       for r in core_senders) for y in YEARS},
           'core_reach': {f"{r['year']} {r['division_group']}": [int(r['senders_to_core']), int(r['patients_to_core'])]
                          for r in reach if r['year'] in ('2017', '2022')},
           'change_rows_by_status': dict(sorted(Counter(r['year_status'] for r in changes_).items())),
           'panel_patients_by_year': {str(y): sum(int(r['patients']) for r in panel if r['year'] == str(y))
                                      for y in YEARS},
           'groups': {f"{r['division_group']} {r['year']}": [int(r['npis_linked']), r['patients_allocated_to_centers'],
                                                            r['outside_hrr_patient_share']]
                      for r in groups if r['year'] in ('2017', '2022')}}
    return out


# ---------------------------------------------------------------- columns

def _c(name, description, missing='Never blank.', source='careset'):
    return column(name, description, missing, source)


PROVENANCE = ['source', 'source_sha256_prefix', 'source_ingested_at_utc']
CARESET_PROVENANCE = [
    _c('source', 'Source id of the row: careset_hop_<year> is the CareSet DocGraph Hop Teaming annual file (Medicare '
                 'fee-for-service shared-patient provider pairs) of that year; careset_hop_<first>_<last> means the '
                 'row derives from the years first to last.'),
    _c('source_sha256_prefix', 'First 8 hexadecimal characters of the SHA-256 of each CareSet file the row derives '
                               'from, as recorded at ingestion in the 2026-10-24 metadata, joined by semicolons in '
                               'year order; full hashes are in the module metadata.'),
    _c('source_ingested_at_utc', 'UTC time the CareSet file was ingested into the 2026-10-24 edge tables (the latest '
                                 'ingestion time when the row derives from several years).'),
]
PANEL_COLUMNS = [
    _c('npi', LICENCE_NOTE + 'National Provider Identifier (NPI) of the sending universe clinician (hcp_divisions.csv, 2026-10-24); '
              'join there for division and region.'),
    _c('ccn', 'CMS Certification Number (CCN, six characters) of the CAR-T center reached (cart_centers.csv).'),
    _c('year', 'CareSet calendar year of the shared-patient pairs, 2017 to 2022.'),
    _c('patients', 'Sum of PATIENT_COUNT over the directed pairs from this NPI to receivers that map to this center '
                   'in the year; a sum of pair counts, not distinct patients, and a pair whose receiver maps to several '
                   'centers counts in full at each. At least 11, the source pair floor.'),
    _c('patients_allocated', 'Same sum with each pair split equally across the centers its receiver maps to, to three '
                             'decimals; an apportionment, not a count. Summed over centers it equals '
                             'out_patients_to_centers of the 2026-10-24 yearly features.'),
    _c('receiving_npis', 'Number of directed pairs (distinct receiving NPIs: the center organization NPI or universe '
                         'clinicians affiliated with the center) behind the row.'),
    _c('mean_day_wait_weighted', 'Patient-weighted mean of AVERAGE_DAY_WAIT, the mean days between the sender\'s and '
                                 'the receiver\'s services, over the pairs behind the row, in days to one decimal.'),
    _c('region_relation', 'Where the center lies against the sending NPI\'s hospital referral region (HRR, Dartmouth '
                          '2019 crosswalk): inside (same HRR), outside (another HRR) or unknown (either HRR blank).'),
] + CARESET_PROVENANCE
SUMMARY_COLUMNS = [
    _c('npi', LICENCE_NOTE + 'National Provider Identifier (NPI) of a universe clinician; every one of the 17,510 NPIs of '
              'hcp_divisions.csv (2026-10-24) has one row.'),
    _c('link_status', 'linked: a link to a CAR-T center in at least one CareSet year 2017-2022; no_center_link: '
                      'outgoing pairs in some year but none to a center; no_outgoing_edge: no outgoing pair at or '
                      'above the 11-patient floor in any year.'),
    _c('first_year_linked', 'First CareSet year with a center link.', 'Blank when the NPI was never linked.'),
    _c('last_year_linked', 'Last CareSet year with a center link.', 'Blank when the NPI was never linked.'),
    _c('years_linked', 'Number of CareSet years 2017-2022 with at least one center link (1 to 6).',
       'Blank when the NPI was never linked.'),
    _c('persistence_in_span', 'years_linked divided by the years from first to last linked year inclusive, to three '
                              'decimals; 1.000 means no gap.', 'Blank when the NPI was never linked.'),
    _c('link_retention_rate', 'Share of the NPI\'s center links in one year that recur the next year, pooled over '
                              '2017-2018 to 2021-2022, to three decimals.',
       'Blank when never linked, or linked only in 2022 so there is no next year to compare.'),
    _c('distinct_centers_ever', 'Number of distinct CAR-T centers (CCN) linked in any year 2017-2022.',
       'Blank when the NPI was never linked.'),
    _c('link_years_total', 'Number of NPI x center x year rows of the panel for this NPI.',
       'Blank when the NPI was never linked.'),
    _c('primary_center_ccn', 'CCN of the center with the largest patients_allocated summed over 2017-2022 (ties: '
                             'more years linked, then lower CCN).', 'Blank when the NPI was never linked.'),
    _c('primary_center_share', 'The primary center\'s share of the NPI\'s patients_allocated over 2017-2022, to '
                               'three decimals.', 'Blank when the NPI was never linked.'),
    _c('primary_center_years', 'Number of years the primary center was linked.', 'Blank when the NPI was never linked.'),
    _c('primary_center_region', 'Primary center against the NPI\'s own hospital referral region (HRR): inside, '
                                'outside, or unknown when either HRR is blank.', 'Blank when the NPI was never linked.'),
] + CARESET_PROVENANCE
CHANGE_COLUMNS = [
    _c('npi', LICENCE_NOTE + 'National Provider Identifier (NPI) of a universe clinician linked to a center in at '
              'least one year. A row is published for each year the NPI is linked and for each year it is not linked '
              'but lost links against the previous year; any other NPI-year is not observed with no change, which '
              'follows from the longitudinal table and is not repeated.'),
    _c('year', 'CareSet calendar year, 2017 to 2022.'),
    _c('year_status', 'linked, or not_observed when no pair to a center reached the 11-patient floor in the year '
                      '(not observed is not zero patients). Patients per year are in the 2026-10-24 yearly features.'),
    _c('links_gained', 'Centers linked this year that were not linked the previous year.',
       'Blank in 2017, the first panel year.'),
    _c('links_lost', 'Centers linked the previous year that are not linked this year.',
       'Blank in 2017, the first panel year.'),
    _c('links_retained', 'Centers linked both this year and the previous year.', 'Blank in 2017, the first panel year.'),
    _c('comparison_status', 'first_year: 2017, no earlier year, so the change columns are blank; compared: '
                            'compared with the previous year.'),
] + CARESET_PROVENANCE
CENTER_FIELDS = [
    _c('ccn', LICENCE_NOTE + 'CMS Certification Number (CCN) of a CAR-T center of cart_centers.csv (2026-10-24); all 173 centers.'),
    _c('year', 'CareSet calendar year, 2017 to 2022.'),
    _c('year_status', 'observed, or not_observed when no universe NPI had a pair to the center at or above the '
                      '11-patient floor in the year.'),
    _c('referrers', 'Distinct universe NPIs (any division) with a panel row to the center in the year.',
       'Blank when the year is not observed.'),
    _c('patients_from_referrers', 'Sum of panel patients over those NPIs: pair counts, not distinct patients.',
       'Blank when the year is not observed.'),
    _c('referring_proxy_referrers', 'Referrers whose 2024 division is referring_proxy.',
       'Blank when the year is not observed.'),
    _c('patients_from_referring_proxy', 'Panel patients from referring_proxy referrers.',
       'Blank when the year is not observed.'),
    _c('referrer_hhi', 'Herfindahl-Hirschman index of referrers\' shares of patients_from_referrers, 0 to 1, four '
                       'decimals; 1 means a single referrer.', 'Blank when the year is not observed.'),
    _c('top_referrer_share', 'Largest single referrer\'s share of patients_from_referrers, three decimals.',
       'Blank when the year is not observed.'),
    _c('outside_hrr_patient_share', 'Share of patients from referrers whose hospital referral region (HRR) differs '
                                    'from the center\'s, among referrers with a known HRR, three decimals.',
       'Blank when the year is not observed or no referrer has an HRR.'),
    _c('referrers_without_hrr', 'Referrers left out of outside_hrr_patient_share because their HRR is blank.',
       'Blank when the year is not observed.'),
    _c('new_referrers', 'Referrers this year that had no row to the center the previous year.',
       'Blank in 2017 and when the year is not observed.'),
    _c('lost_referrers', 'Referrers of the previous year with no row to the center this year.', 'Blank in 2017.'),
    _c('retained_referrers', 'Referrers present this year and the previous year.',
       'Blank in 2017 and when the year is not observed.'),
    _c('comparison_status', 'first_year: 2017, no earlier year, so the turnover columns are blank; compared: '
                            'compared with the previous year.'),
] + CARESET_PROVENANCE
CENTER_COLUMNS = [c['column'] for c in CENTER_FIELDS]
GROUP_COLUMNS = [
    _c('division_group', LICENCE_NOTE + 'Division group of base.DIVISION_GROUPS: core, affiliates, referring or other (2024 '
                         'labels, version 1.0.0).'),
    _c('year', 'CareSet calendar year, 2017 to 2022.'),
    _c('npis_in_group', 'Universe NPIs in the group (fixed across years: labels are from 2024).'),
    _c('npis_linked', 'NPIs of the group with at least one center link in the year.'),
    _c('center_links', 'NPI x center rows of the panel for the group in the year.'),
    _c('patients_allocated_to_centers', 'Sum of patients_allocated for the group in the year, rounded to a whole '
                                        'number; equals the group sum of out_patients_to_centers of the 2026-10-24 '
                                        'yearly features (pair counts, not distinct patients).'),
    _c('outside_hrr_patient_share', 'Share of patients_allocated going to centers outside the sender\'s hospital '
                                    'referral region (HRR), among rows with both HRRs known.',
       'Blank when no row of the group has both HRRs known.'),
    _c('links_gained', 'Sum of links_gained over the group\'s NPIs.', 'Blank in 2017.'),
    _c('links_lost', 'Sum of links_lost over the group\'s NPIs.', 'Blank in 2017.'),
    _c('comparison_status', 'first_year: 2017, no earlier year, so the change columns are blank; compared: '
                            'compared with the previous year.'),
] + CARESET_PROVENANCE
BASELINE_COLUMNS = [
    _c('npi', 'National Provider Identifier (NPI) of the initial NPI of the pair (npi1, the provider whose claim came '
              'first), a universe clinician. CMS data, public domain; a pre-CAR-T baseline, not part of the CareSet '
              '2017-2022 series. Divisions, hospital affiliations, the universe and the center set are 2024 facts applied backwards, so early years undercount clinicians who moved, retired or changed NPI; read with the coverage table. ', source='cms'),
    _c('ccn', 'CCN of the CAR-T center reached, by the same rule as the CareSet panel applied to npi2 (the secondary '
              'provider whose claim followed within 180 days).', source='cms'),
    _c('year', 'CMS file year, 2009 to 2015: initial claims in that calendar year with follow-ups up to 1 July of '
               'the next year; 2015 covers 2015-01-01 to 2015-10-01 only, a partial year.', source='cms'),
    _c('beneficiaries', 'Sum of Bene Count (distinct beneficiaries of each pair, at least 11) over the pairs from this '
                        'NPI to receivers mapped to the center; pair sums, not distinct beneficiaries.', source='cms'),
    _c('beneficiaries_allocated', 'Same sum with each pair split equally across its receiver\'s centers, up to three '
                                  'decimals; an apportionment, not a count.', source='cms'),
    _c('pair_count', 'Sum of Pair Count (shared count: follow-on claim events of each pair) over the same pairs.',
       source='cms'),
    _c('same_day_count', 'Sum of Same Day Count over the same pairs; CMS assigns a same-day pair to the lesser of '
                         'the two NPIs, so its direction is arbitrary.', source='cms'),
    _c('receiving_npis', 'Number of pairs (distinct receiving NPIs) behind the row.', source='cms'),
    _c('careset_2017_link', 'linked_2017 when the same NPI x center has a 2017 CareSet panel row, else '
                            'not_linked_2017. Different method: not one series.'),
    _c('source', 'Source id: cms_pspp_<year>_180 is the CMS Physician Shared Patient Patterns file of that year, '
                 '180-day window, as mirrored by the National Bureau of Economic Research (NBER); public domain.',
       source='nber'),
    _c('source_sha256_prefix', 'First 8 hexadecimal characters of the SHA-256 of the whole streamed zip file.',
       source='nber'),
    _c('source_retrieved_at_utc', 'UTC time the stream of the zip file ended.', source='nber'),
]
COVERAGE_COLUMNS = [
    _c('year', 'CMS Physician Shared Patient Patterns file year, 2009 to 2015 (180-day window); a pre-CAR-T baseline, '
               'not part of the CareSet 2017-2022 series. Divisions, hospital affiliations, the universe and the center set are 2024 facts applied backwards, so early years undercount clinicians who moved, retired or changed NPI; read with the coverage table. ', source='cms'),
    _c('division_group', 'Division group of base.DIVISION_GROUPS (2024 labels, version 1.0.0).', source='cms'),
    _c('npis_in_group', 'Universe NPIs in the group (fixed: labels are from 2024).', source='cms'),
    _c('coverage_status', 'measured: npis_as_npi1 was counted while streaming the whole file (2014 and 2015 on a '
                          'second streaming whose SHA-256 and line count equal the first); not_measured: not counted.',
       source='cms'),
    _c('npis_as_npi1', 'Universe NPIs of the group appearing as npi1 in any pair of the file, with any partner: how '
                       'many of the 2024 universe the CMS year sees at all.',
       'Blank when coverage_status is not_measured.', source='cms'),
    _c('npis_as_npi1_kept', 'Universe NPIs of the group appearing as npi1 in the kept extract (pairs whose other end is '
                            'a universe NPI or a center organization NPI).', source='cms'),
    _c('npis_linked', 'Universe NPIs of the group with at least one baseline row (a link to a center) in the year.',
       source='cms'),
    _c('linked_share_of_covered', 'npis_linked divided by npis_as_npi1, three decimals.',
       'Blank when coverage_status is not_measured.', source='cms'),
    _c('source', 'Source id cms_pspp_<year>_180 (NBER mirror of the CMS file); public domain.', source='nber'),
    _c('source_sha256_prefix', 'First 8 hexadecimal characters of the SHA-256 of the whole streamed zip file.',
       source='nber'),
    _c('source_retrieved_at_utc', 'UTC time the stream of the zip file ended.', source='nber'),
]
CORE_LINK_COLUMNS = [
    _c('npi', LICENCE_NOTE + 'National Provider Identifier (NPI) of a sending universe clinician; a row exists only for '
              'years with at least one pair to a core HCP (division core, 2024 label). ' + CAVEAT),
    _c('year', 'CareSet calendar year, 2017 to 2022.'),
    _c('core_hcps', 'Distinct core HCPs the NPI shares patients with as the earlier provider in the year.'),
    _c('patients_to_core', 'Sum of PATIENT_COUNT over those pairs; at least 11.'),
    _c('patients_to_core_at_centers', 'Part of patients_to_core going to core HCPs affiliated with a CAR-T center; '
                                      'these pairs are also counted in out_patients_to_centers of the 2026-10-24 '
                                      'yearly features.'),
    _c('share_of_center_patients_via_core', 'patients_to_core_at_centers divided by the NPI\'s out_patients_to_centers '
                                            '(2026-10-24 yearly features) for the year, three decimals: the part of '
                                            'its center-linked flow that goes through a core HCP rather than a '
                                            'non-core center clinician or the center organization.',
       'Blank when the NPI has no center-linked flow in the year.'),
    _c('mean_day_wait_weighted', 'Patient-weighted mean of AVERAGE_DAY_WAIT over the pairs to core HCPs, in days.'),
] + CARESET_PROVENANCE
CORE_SENDER_COLUMNS = [
    _c('npi', LICENCE_NOTE + 'NPI of a core HCP (division core, 2024 label) as receiver. A row exists for each year '
              'with at least one universe sender, and for a year without one that lost senders against the '
              'previous year. ' + CAVEAT + 'Patients and senders of the referring group are not repeated here: '
              'they are in_patients_from_referrers and referrer_sources of the 2026-10-24 yearly features, and equal '
              'patients_from_universe minus the three published groups.'),
    _c('year', 'CareSet calendar year, 2017 to 2022.'),
    _c('year_status', 'linked: at least one universe sender in the year; not_observed: none at or above the 11-patient '
                      'pair floor (not zero patients).'),
    _c('senders', 'Distinct universe NPIs sending to this core HCP.', 'Blank when not_observed.'),
    _c('patients_from_universe', 'Sum of PATIENT_COUNT over pairs from universe NPIs.', 'Blank when not_observed.'),
    _c('patients_from_core', 'Part of patients_from_universe from core senders.', 'Blank when not_observed.'),
    _c('patients_from_affiliates', 'Part from affiliate senders (affiliate_A, B, C and center_affiliate_unlinked).',
       'Blank when not_observed.'),
    _c('patients_from_other', 'Part from hemonc_other senders.', 'Blank when not_observed.'),
    _c('senders_core', 'Core senders.', 'Blank when not_observed.'),
    _c('senders_affiliates', 'Affiliate senders.', 'Blank when not_observed.'),
    _c('senders_other', 'hemonc_other senders.', 'Blank when not_observed.'),
    _c('outside_hrr_patient_share', 'Share of patients_from_universe from senders whose hospital referral region (HRR) '
                                    'differs from the core HCP\'s own, among senders with a known HRR, three decimals.',
       'Blank when not_observed, or when the core HCP or every sender has no HRR.'),
    _c('senders_without_hrr', 'Senders left out of outside_hrr_patient_share.', 'Blank when not_observed.'),
    _c('sender_hhi', 'Herfindahl-Hirschman index of senders\' shares of patients_from_universe, 0 to 1, four decimals.',
       'Blank when not_observed.'),
    _c('senders_gained', 'Senders this year without a pair the previous year.', 'Blank in 2017 and when not_observed.'),
    _c('senders_lost', 'Senders of the previous year without a pair this year.', 'Blank in 2017.'),
    _c('comparison_status', 'first_year: 2017, change columns blank; compared: compared with the previous year.'),
] + CARESET_PROVENANCE
CORE_REACH_COLUMNS = [
    _c('year', LICENCE_NOTE + 'CareSet calendar year, 2017 to 2022. ' + CAVEAT),
    _c('division_group', 'Sender division group of base.DIVISION_GROUPS (2024 labels).'),
    _c('npis_in_group', 'Universe NPIs in the group.'),
    _c('senders_to_core', 'NPIs of the group with a pair to at least one core HCP in the year.'),
    _c('sender_share_of_group', 'senders_to_core divided by npis_in_group, three decimals.'),
    _c('sender_core_pairs', 'Sender x core HCP pairs of the group in the year.'),
    _c('patients_to_core', 'Sum of PATIENT_COUNT over those pairs.'),
] + CARESET_PROVENANCE
BASELINE_PROVENANCE = ['source', 'source_sha256_prefix', 'source_retrieved_at_utc']


# ---------------------------------------------------------------- inputs

def stream_zip_member(url, handle_line, chunk=1 << 20):
    """Stream the first member of a remote zip once, inflate it on the fly and pass each text line on.

    The scaffold's fetch keeps whole responses, so large files use this. The record holds the URL, start and end
    UTC time, bytes transferred, SHA-256 of the full compressed stream and the HTTP Last-Modified header.
    """
    started, digest, transferred = now(), hashlib.sha256(), 0
    with urllib.request.urlopen(urllib.request.Request(url), timeout=300) as response:
        status, headers = response.status, dict(response.headers)
        head = response.read(30)
        digest.update(head)
        transferred += len(head)
        signature, _, flags, method, _, _, _, _, _, name_length, extra_length = struct.unpack('<IHHHHHIIIHH', head)
        assert signature == 0x04034b50 and method == 8, 'Expected a deflated zip member'
        name = response.read(name_length + extra_length)
        digest.update(name)
        transferred += len(name)
        inflater, rest, lines = zlib.decompressobj(-15), b'', 0
        for block in iter(lambda: response.read(chunk), b''):
            digest.update(block)
            transferred += len(block)
            if inflater.eof:
                continue
            rest += inflater.decompress(block)
            *complete, rest = rest.split(b'\n')
            for line in complete:
                lines += 1
                handle_line(line.decode('ascii', 'replace').strip())
        if rest.strip():
            lines += 1
            handle_line(rest.decode('ascii', 'replace').strip())
    return {'source_id': f'{MODULE}:stream:' + url.rsplit('/', 1)[-1], 'module': MODULE, 'source_url': url,
            'member': name[:name_length].decode(), 'method': 'GET (streamed)',
            'retrieval_started_at_utc': started, 'retrieved_at_utc': now(), 'http_status': status,
            'http_last_modified': headers.get('Last-Modified', ''), 'http_content_length': headers.get('Content-Length', ''),
            'bytes': transferred, 'sha256': digest.hexdigest(), 'lines_read': lines}


def nber_extract(year, force=False, second=False):
    """Stream one 180-day file once and keep pairs with both NPIs in the universe or one a center organization.

    `second` re-streams a file already transferred, to add the npi1 coverage sidecar: its extract and record get a
    `_second` name and the first record is left as it was.
    """
    from .base import centers, universe
    url, tag = NBER_URL.format(year=year), '_second' if second else ''
    extract = Path(NBER_EXTRACT.format(year=year).replace('.csv', f'{tag}.csv'))
    record_path = Path(NBER_RECORD.format(year=year).replace('.meta.json', f'{tag}.meta.json'))
    rejected_path = Path(NBER_REJECTED.format(year=year))
    if extract.exists() and record_path.exists() and not force:
        return extract, load(record_path)
    people = set(universe())
    organizations = {c['organization_npi'] for c in centers().values() if c['organization_npi']}
    extract.parent.mkdir(parents=True, exist_ok=True)
    temporary = extract.with_name(extract.name + '.tmp')
    counts, as_first = {'kept': 0, 'malformed': 0}, Counter()   # pairs per universe NPI as npi1, any partner
    rejected = []
    with temporary.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['npi1', 'npi2', 'paircount', 'benecount', 'samedaycount'])

        def keep(line):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 5 or not parts[0].isdigit():
                counts['malformed'] += bool(line)
                if line:
                    rejected.append(line)
                return
            a, b = parts[0], parts[1]
            if a in people:
                as_first[a] += 1
            if (a in people and b in people) or a in organizations or b in organizations:
                writer.writerow(parts[:5])
                counts['kept'] += 1
        record = stream_zip_member(url, keep)
    temporary.replace(extract)
    rejected_path.write_text(''.join(line + '\n' for line in rejected), encoding='utf-8')
    coverage = Path(NBER_COVERAGE.format(year=year))
    with coverage.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['npi', 'pairs_as_npi1'])
        writer.writerows(sorted(as_first.items()))
    record.update({'lines_not_data_path': str(rejected_path.relative_to(ROOT)), 'lines_not_data': rejected[:50],
                   'universe_npis_as_npi1': len(as_first), 'coverage_path': str(coverage.relative_to(ROOT)),
                   'coverage_sha256': sha(coverage)})
    record.update({'rows_kept': counts['kept'], 'lines_skipped_not_data': counts['malformed'],
                   'filter': 'both NPIs in hcp_divisions.csv (2026-10-24) or either NPI a cart_centers.csv organization NPI',
                   'extract_path': str(extract.relative_to(ROOT)), 'extract_sha256': sha(extract),
                   'extract_bytes': extract.stat().st_size})
    dump(record_path, record)
    return extract, record


def read_edges(path):
    with Path(path).open(newline='', encoding='utf-8') as stream:
        reader = csv.reader(stream)
        header = next(reader)
        i = {name: header.index(name) for name in ('from_npi', 'to_npi', 'patient_count', 'average_day_wait')}
        for row in reader:
            yield {name: row[k] for name, k in i.items()}


def coverage_rows(year, ctx, first_npis, kept_npis, baseline, record):
    """Per division group: universe NPIs seen as npi1 (None when not measured), seen in the extract, and linked."""
    group = {n: DIVISION_GROUPS[d] for n, d in ctx['division'].items()}
    linked = {r['npi'] for r in baseline if r['year'] == str(year)}
    out = []
    for name in sorted(set(group.values())):
        members = {n for n, g in group.items() if g == name}
        seen = None if first_npis is None else len(members & first_npis)
        hit = len(members & linked)
        out.append({'year': str(year), 'division_group': name, 'npis_in_group': str(len(members)),
                    'coverage_status': COVERAGE_NOT_MEASURED if seen is None else COVERAGE_MEASURED,
                    'npis_as_npi1': fmt(seen), 'npis_as_npi1_kept': str(len(members & kept_npis)),
                    'npis_linked': str(hit), 'linked_share_of_covered': fmt(hit / seen, 3) if seen else '',
                    'source': NBER_SOURCE.format(year=year), 'source_sha256_prefix': record['sha256'][:PREFIX],
                    'source_retrieved_at_utc': record['retrieved_at_utc']})
    return out


def baseline_rows(pairs, ctx, panel_keys_2017, record, year):
    """NPI x center rows of one CMS file: npi1 is the initial provider, npi2 the one whose claim followed."""
    cells = defaultdict(lambda: [0, 0.0, 0, 0, 0])
    for p in pairs:
        a = p['npi1']
        if a not in ctx['division']:
            continue
        targets = centers_of(p['npi2'], ctx)
        for ccn in targets:
            cell = cells[(a, ccn)]
            cell[0] += int(p['benecount'])
            cell[1] += int(p['benecount']) / len(targets)
            cell[2] += int(p['paircount'])
            cell[3] += int(p['samedaycount'])
            cell[4] += 1
    return [{'npi': a, 'ccn': c, 'year': str(year), 'beneficiaries': str(v[0]), 'beneficiaries_allocated': compact(v[1]),
             'pair_count': str(v[2]), 'same_day_count': str(v[3]), 'receiving_npis': str(v[4]),
             'careset_2017_link': BASELINE_LINK[(a, c) in panel_keys_2017],
             'source': NBER_SOURCE.format(year=year), 'source_sha256_prefix': record['sha256'][:PREFIX],
             'source_retrieved_at_utc': record['retrieved_at_utc']} for (a, c), v in sorted(cells.items())]


# ---------------------------------------------------------------- derive

def derive(panel, ctx, center_hrr, sends, meta):
    """All CareSet tables from accumulated panel cells; `meta` maps year to its 2026-10-24 metadata."""
    def provenance(years):
        return {'source': f'careset_hop_{years[0]}' + (f'_{years[-1]}' if len(years) > 1 else ''),
                'source_sha256_prefix': ';'.join(meta[y]['sha256'][:PREFIX] for y in years),
                'source_ingested_at_utc': max(meta[y]['ingested_at_utc'] for y in years)}

    def compared(year):   # a change or turnover row derives from its year and the previous one
        return provenance([year]) if year == YEARS[0] else provenance([year - 1, year])

    panel_rows = []
    for (npi, ccn, y), (patients, allocated, receivers, wait) in sorted(panel.items()):
        panel_rows.append({'npi': npi, 'ccn': ccn, 'year': str(y), 'patients': str(patients),
                           'patients_allocated': compact(allocated), 'receiving_npis': str(receivers),
                           'mean_day_wait_weighted': fmt(wait / patients, 1),
                           'region_relation': REGION[region(ctx['hrr'].get(npi, ''), center_hrr.get(ccn, ''))],
                           **provenance([y])})
    by_npi = defaultdict(list)
    for r in panel_rows:
        by_npi[r['npi']].append(r)
    every = provenance(YEARS)
    summary_rows = [{**npi_summary_row(n, by_npi.get(n, []), ctx, center_hrr, sends), **every}
                    for n in sorted(ctx['division'])]
    links = links_by_npi(panel_rows)
    change_table = [{**r, **compared(int(r['year']))} for n in sorted(links) for r in change_rows(n, links[n])]
    centers_table = [{**r, **compared(int(r['year']))} for r in center_rows(panel_rows, ctx, center_hrr)]
    groups_table = [{**r, **compared(int(r['year']))} for r in group_rows(panel_rows, change_table, ctx, center_hrr)]
    return {PANEL: panel_rows, NPI_SUMMARY: summary_rows, NPI_CHANGES: change_table, CENTER_BASE: centers_table,
            GROUPS: groups_table}


SPECS = {PANEL: (PANEL_COLUMNS, ['npi', 'ccn', 'year'], PROVENANCE), NPI_SUMMARY: (SUMMARY_COLUMNS, ['npi'], PROVENANCE),
         NPI_CHANGES: (CHANGE_COLUMNS, ['npi', 'year'], PROVENANCE), CENTER_BASE: (CENTER_FIELDS, ['ccn', 'year'], PROVENANCE),
         BASELINE: (BASELINE_COLUMNS, ['npi', 'ccn', 'year'], BASELINE_PROVENANCE),
         GROUPS: (GROUP_COLUMNS, ['division_group', 'year'], PROVENANCE),
         COVERAGE: (COVERAGE_COLUMNS, ['year', 'division_group'], BASELINE_PROVENANCE),
         CORE_LINKS: (CORE_LINK_COLUMNS, ['npi', 'year'], PROVENANCE),
         CORE_SENDERS: (CORE_SENDER_COLUMNS, ['npi', 'year'], PROVENANCE),
         CORE_REACH: (CORE_REACH_COLUMNS, ['year', 'division_group'], PROVENANCE)}


def deduplicate(table, rows, processed=PROCESSED):
    """Rows of `table` not already published in an earlier snapshot, and the counts dropped."""
    key = SPECS[table][1]
    earlier = [processed / s / f'{table}.csv' for s in EARLIER_SNAPSHOTS if (processed / s / f'{table}.csv').exists()]
    kept, dropped = new_rows(rows, key, existing_keys(earlier, key))
    _, inside = new_rows(rows, key, set())
    return kept, {'candidate_rows': len(rows), 'dropped_already_published': dropped - inside,
                  'dropped_internal_duplicates': inside}


# ---------------------------------------------------------------- build

def build():
    ctx = context()
    center_hrr = {c['ccn']: c['hrr'] for c in read_csv(EARLIER / 'cart_centers.csv')}
    meta = {y: load(EARLIER / f'shared_patient_annual_{y}_metadata.json') for y in YEARS}
    for y in YEARS:
        raw = Path(str(DOCGRAPH).format(year=y))
        if raw.exists():
            log_local(MODULE, f'docgraph_hop_teaming_{y}', raw, meta[y]['ingested_at_utc'], release=f'{y} annual',
                      sha256=meta[y]['sha256'], note='Hash reused from the 2026-10-24 metadata; date received from '
                      'CareSet not recorded in the project.')
    core = {}
    panel, recon, edge_hashes = defaultdict(lambda: [0, 0.0, 0, 0.0]), defaultdict(lambda: [0, set(), []]), {}
    for y in YEARS:
        path = EDGES / f'shared_patient_annual_{y}_edges.csv'
        edge_hashes[y] = log_local(MODULE, f'careset_edges_{y}', path, meta[y]['ingested_at_utc'],
                                   release=f'{y} annual, filtered at 2026-10-24')['sha256']
        accumulate(core_pairs(read_edges(path), ctx, y, core), ctx, y, panel, recon)
        print('REFERRAL edges', y, 'done', flush=True)

    # Reconciliation of the link rule against the published 2026-10-24 yearly features (all years, every NPI).
    reconciliation = {}
    for y in YEARS:
        published = {r['npi']: r for r in read_csv(EARLIER / f'shared_patient_annual_{y}_features.csv')
                     if int(r['out_patients_to_centers'])}
        mine = {n: v for (n, yy), v in recon.items() if yy == y}
        reconciliation[str(y)] = {
            'npis_published': len(published), 'npis_rebuilt': len(mine),
            'npi_set_mismatch': len(set(published) ^ set(mine)),
            'patients_mismatch': sum(int(published[n]['out_patients_to_centers']) != v[0]
                                     for n, v in mine.items() if n in published),
            'partners_mismatch': sum(int(published[n]['distinct_center_partners']) != len(v[1])
                                     for n, v in mine.items() if n in published),
            'mean_wait_mismatch': sum(published[n]['mean_day_wait_to_centers'] != fmt(round(sum(v[2]) / len(v[2]), 1))
                                      for n, v in mine.items() if n in published)}

    sends = {r['npi'] for y in YEARS for r in read_csv(EARLIER / f'shared_patient_annual_{y}_features.csv')
             if int(r['out_patients_total'])}
    tables = derive(panel, ctx, center_hrr, sends, meta)
    panel_rows = tables[PANEL]
    documents = [fetch(url, MODULE, suffix=suffix)[1] for url, suffix in
                 ((NBER_PAGE, '.html'), (NBER_DESC, '.txt'), (NBER_SUM, '.txt'), (NBER_TECH, '.pdf'),
                  (CMS_FAQ, '.html'))]
    linked_2017 = {(r['npi'], r['ccn']) for r in panel_rows if r['year'] == '2017'}
    records, baseline, coverage = {}, [], []
    for year in NBER_YEARS:
        extract, records[year] = nber_extract(year)
        assert records[year]['lines_read'] == CMS_RECORDS[year], f'{year}: line count differs from the CMS count'
        again = Path(NBER_RECORD.format(year=year).replace('.meta.json', '_second.meta.json'))
        if again.exists():   # second transfer for the coverage sidecar: must be the same bytes and lines
            second = load(again)
            assert (second['sha256'], second['lines_read'], second['extract_sha256']) == (
                records[year]['sha256'], records[year]['lines_read'], records[year]['extract_sha256']), year
            records[year] = {**records[year], 'second_transfer': {k: second[k] for k in (
                'retrieval_started_at_utc', 'retrieved_at_utc', 'bytes', 'sha256', 'http_last_modified', 'lines_read',
                'lines_skipped_not_data', 'lines_not_data', 'universe_npis_as_npi1', 'coverage_sha256')}}
        pairs = read_csv(extract)
        rows_ = baseline_rows(pairs, ctx, linked_2017, records[year], year)
        baseline += rows_
        sidecar = Path(NBER_COVERAGE.format(year=year))
        first_npis = {r['npi'] for r in read_csv(sidecar)} if sidecar.exists() else None
        kept_npis = {p['npi1'] for p in pairs if p['npi1'] in ctx['division']}
        coverage += coverage_rows(year, ctx, first_npis, kept_npis, rows_, records[year])
    tables[BASELINE], tables[COVERAGE] = baseline, coverage
    write_csv(CORE_PAIRS, [{'from_npi': a, 'to_core_npi': b, 'year': y, 'patient_count': v[0], 'average_day_wait': v[1],
                            'core_at_center': int(v[2])} for (a, b, y), v in sorted(core.items())])
    links, received, reach = core_tables(core, ctx, recon)
    def provenance(years):
        return {'source': f'careset_hop_{years[0]}' + (f'_{years[-1]}' if len(years) > 1 else ''),
                'source_sha256_prefix': ';'.join(meta[y]['sha256'][:PREFIX] for y in years),
                'source_ingested_at_utc': max(meta[y]['ingested_at_utc'] for y in years)}
    def compared(year):
        return provenance([year]) if year == YEARS[0] else provenance([year - 1, year])
    tables[CORE_LINKS] = [{**r, **provenance([int(r['year'])])} for r in links]
    tables[CORE_SENDERS] = [{**r, **compared(int(r['year']))} for r in received]
    tables[CORE_REACH] = [{**r, **provenance([int(r['year'])])} for r in reach]
    for table in TABLES:   # earlier tables of this snapshot must stay byte-identical
        path = OUT / f'{table}.csv'
        if path.exists():
            columns = [c['column'] for c in SPECS[table][0]]
            assert read_csv(path) == [{c: str(r[c]) for c in columns} for r in tables[table]], f'{table} changed'
    summaries, dedup, published = [], {}, {}
    for table in TABLES:
        columns, key, prov = SPECS[table]
        published[table], dedup[table] = deduplicate(table, tables[table])
        summaries.append(publish(table, published[table], columns, key, prov))
    for table, digest in FINGERPRINTS.items():
        assert digest == '' or table in (CORE_LINKS, CORE_SENDERS, CORE_REACH) or sha(OUT / f'{table}.csv') == digest, table
    numbers = headline(published, ctx)
    write_module_metadata(MODULE, {
        'tables': summaries,
        'sources': [
            {'name': 'CareSet DocGraph Hop Teaming annual 2017-2022 (filtered edge tables of the 2026-10-24 snapshot)',
             'publisher': 'CareSet Systems, Inc.', 'url': 'https://careset.com/data-store/',
             'release_or_version': '; '.join(f"{y}: sha256 {meta[y]['sha256'][:16]}" for y in YEARS),
             'retrieved_from_utc': min(meta[y]['ingested_at_utc'] for y in YEARS),
             'retrieved_to_utc': max(meta[y]['ingested_at_utc'] for y in YEARS),
             'how_obtained': 'Local files delivered under the CareSet non-commercial licence before this snapshot; '
                             'read from Data/interim/2026-10-24/careset (edge table hashes in the local input log); '
                             'nothing requested from CareSet', 'requests': 0, 'terms_class': 'CC BY-NC-SA 4.0 + addendum',
             'edge_table_sha256': {str(y): h for y, h in edge_hashes.items()}},
            *[{'name': f'CMS Physician Shared Patient Patterns {year}, 180-day window (NBER mirror)',
               'source_id': NBER_SOURCE.format(year=year),
               'publisher': 'Centers for Medicare & Medicaid Services; mirror by NBER', 'url': NBER_URL.format(year=year),
               'release_or_version': f"HTTP Last-Modified {r['http_last_modified']}; member {r['member']}; "
                                     f"sha256 {r['sha256']}; CMS date range {NBER_RANGES[year]}",
               'retrieved_from_utc': r['retrieval_started_at_utc'], 'retrieved_to_utc': r['retrieved_at_utc'],
               'how_obtained': 'One streamed GET of the zip, inflated in memory; only the filtered extract was saved '
                               f"({r['extract_path']}, {r['rows_kept']} rows, sha256 {r['extract_sha256'][:16]})",
               'requests': 1 + bool(r.get('second_transfer')), 'bytes_transferred': r['bytes'],
               'lines_read': r['lines_read'], 'cms_record_count': CMS_RECORDS[year],
               'lines_not_data': r.get('second_transfer', r).get('lines_not_data', 'not saved'),
               'lines_not_data_count': r['lines_skipped_not_data'],
               'second_transfer': r.get('second_transfer', ''),
               'terms_class': 'public domain (US government work)'} for year, r in records.items()],
            {'name': 'NBER and CMS documentation of the Physician Shared Patient Patterns files',
             'publisher': 'NBER; Centers for Medicare & Medicaid Services', 'url': NBER_PAGE,
             'release_or_version': 'landing page last updated 2018-02-13; CMS technical requirements PDF',
             'retrieved_from_utc': min(d['retrieved_at_utc'] for d in documents),
             'retrieved_to_utc': max(d['retrieved_at_utc'] for d in documents),
             'how_obtained': 'fetch (frozen under Data/raw/2026-11-20/referral)', 'requests': len(documents),
             'documents': [{k: d[k] for k in ('source_url', 'retrieved_at_utc', 'sha256')} for d in documents],
             'terms_class': 'public domain (US government work)',
             'other_file_sizes_bytes_not_transferred': {k: v for k, v in NBER_SIZES.items()
                                                        if not k.endswith('_180')}}],
        'transfer_bytes_total': sum(r['bytes'] for r in records.values()),
        'cms_direction': DIRECTION,
        'licence': LICENCE,
        'grain_decision': 'NPI x center x year panel published: the CC BY-NC-SA 4.0 licence grants the right to '
                          '"Share the Licensed Material, in whole or in part, for NonCommercial purposes only" '
                          '(Section 2(a)(1)); the addendum forbids re-identification of individuals, and every cell '
                          'aggregates pairs of at least 11 patients. Pair-level edges stay local (size, as before).',
        'link_rule': 'careset.target_centers: a directed edge from a universe NPI to a center organization NPI (that '
                     'CCN) or to a universe NPI whose hospital_ccns include center CCNs (each such CCN).',
        'reconciliation_with_2026_10_24_features': reconciliation,
        'headline': numbers, 'dedup': dedup,
        'limitations': [
            'Patients are sums of pair-level PATIENT_COUNT, each pair at least 11 Medicare fee-for-service '
            'patients, not distinct patients; a patient seen by several physicians counts once per pair. A shared '
            'patient is not a referral, and nothing in these tables is specific to CAR-T.',
            'Core HCP links count pairs to the 500 core NPIs as receivers; most center-linked flow lands on center '
            'organization NPIs or on affiliates, so the core tables describe a small part of the flow.',
            'Hospital affiliations and divisions are 2024/2026 labels applied backwards to 2017-2022.',
            'Medicare fee-for-service only; disease-agnostic shared patients, not lymphoma referrals.',
            'The CMS baseline differs from CareSet Hop Teaming: a 180-day follow-on window, beneficiary counts, no '
            'wait time, claim types that include hospice, home health, skilled nursing and Medicare Advantage '
            'no-pay claims, and same-day pairs assigned to the lesser NPI; 2014 is a full year of initial claims '
            '(follow-ups to 2015-07-01) and 2015 stops at 2015-10-01. The two are not one series.',
            'All seven 180-day CMS files 2009-2015 were transferred, each streamed once (about 8.5 GB in total); '
            'the 30, 60 and 90-day files were not.',
            'The universe, divisions, hospital affiliations and the center set are 2024 facts applied backwards up '
            'to fifteen years, so a CMS link to a center describes where the receiving clinician is affiliated '
            'now, and early years miss clinicians who were not yet in practice or used other NPIs; the coverage '
            'table gives, per year and division group, how many universe NPIs the file sees at all.',
            'The 2009-2015 baseline and the 2017-2022 CareSet panel are different sources and methods and are '
            'never combined into one series.',
            'Twelve of the 173 centers never receive a universe link 2017-2022; all twelve are children\'s '
            'hospitals (CCN 3300-3399 series), which Medicare fee-for-service data barely reach.'],
        'defects_open': ['The lines that failed the data test in 2011 (4), 2012 (6) and 2013 (5) were dropped '
                         'without being saved, so their content is unknown; 2014 and 2015 were re-streamed and '
                         'their such lines are listed under sources (lines_not_data).',
                         'The NBER landing page and file sizes were first read with curl (HEAD requests) before the '
                         'module froze the landing page through fetch; the sizes are recorded in sources.']})
    print('REFERRAL', ' '.join(f"{s['table']}={s['rows']}" for s in summaries), flush=True)
    return summaries


# ---------------------------------------------------------------- check

def check(processed):
    """Check name to error count, reading only files under the processed directory."""
    processed = Path(processed)
    names = ['tables_missing', 'duplicate_keys', 'npi_not_in_universe', 'ccn_not_in_centers', 'below_floor',
             'blank_provenance', 'features_npi_year_set', 'features_partners', 'features_patients_allocated',
             'features_patients_floor', 'hrr_flows_patients', 'hrr_flows_pairs', 'summary_recompute',
             'changes_recompute', 'centers_recompute', 'groups_recompute', 'groups_vs_features', 'status_words',
             'baseline_floor', 'baseline_link_recompute', 'panel_region_recompute', 'identifying_columns', 'coverage_recompute', 'core_floor', 'core_join', 'core_vs_features', 'core_cross_table', 'core_reach_recompute', 'headline', 'fingerprint']
    out = dict.fromkeys(names, 0)
    paths = {t: processed / SNAPSHOT / f'{t}.csv' for t in TABLES}
    earlier = processed / UNIVERSE_SNAPSHOT
    needed = [*paths.values(), earlier / 'hcp_divisions.csv', earlier / 'cart_centers.csv',
              *[earlier / f'shared_patient_annual_{y}_features.csv' for y in YEARS],
              *[earlier / f'shared_patient_annual_{y}_hrr_center_flows.csv' for y in YEARS]]
    out['tables_missing'] = sum(not p.is_file() for p in needed)
    if out['tables_missing']:
        return {f'referral_{k}': v for k, v in out.items()}
    tables = {t: read_csv(p) for t, p in paths.items()}
    ctx = context(earlier)
    center_hrr = {c['ccn']: c['hrr'] for c in read_csv(earlier / 'cart_centers.csv')}
    for t, (_, key, prov) in SPECS.items():
        out['duplicate_keys'] += duplicates(tables[t], key)
        out['blank_provenance'] += sum(not r[p].strip() for r in tables[t] for p in prov)
        if 'npi' in key:
            out['npi_not_in_universe'] += sum(r['npi'] not in ctx['division'] for r in tables[t])
        if 'ccn' in key:
            out['ccn_not_in_centers'] += sum(r['ccn'] not in center_hrr for r in tables[t])
    panel = tables[PANEL]
    out['panel_region_recompute'] = sum(
        r['region_relation'] != REGION[region(ctx['hrr'].get(r['npi'], ''), center_hrr.get(r['ccn'], ''))] for r in panel)
    for t, p in paths.items():
        with p.open(newline='', encoding='utf-8') as stream:
            out['identifying_columns'] += len(set(next(csv.reader(stream))) & IDENTIFYING)
    out['below_floor'] = sum(int(r['patients']) < FLOOR for r in panel) + sum(
        int(r['patients_from_referrers']) < FLOOR for r in tables[CENTER_BASE] if r['year_status'] == CENTER_OBSERVED)
    out['baseline_floor'] = sum(int(r['beneficiaries']) < FLOOR for r in tables[BASELINE])
    linked_2017 = {(r['npi'], r['ccn']) for r in panel if r['year'] == '2017'}
    group = {n: DIVISION_GROUPS[d] for n, d in ctx['division'].items()}
    linked_by = Counter((r['year'], group[r['npi']]) for r in {(r['year'], r['npi']): r for r in tables[BASELINE]}.values())
    sizes = Counter(group.values())
    coverage = tables[COVERAGE]
    out['coverage_recompute'] = (len({(r['year'], r['division_group']) for r in coverage}
                                     ^ {(str(y), g) for y in NBER_YEARS for g in sizes})
                                 + sum(bool(int(r['npis_linked']) != linked_by[(r['year'], r['division_group'])]
                                       or int(r['npis_in_group']) != sizes[r['division_group']]
                                       or int(r['npis_linked']) > int(r['npis_as_npi1_kept'])
                                       or (r['coverage_status'] == COVERAGE_MEASURED) != bool(r['npis_as_npi1'])
                                       or r['coverage_status'] not in (COVERAGE_MEASURED, COVERAGE_NOT_MEASURED)
                                       or (r['npis_as_npi1'] and (int(r['npis_as_npi1_kept']) > int(r['npis_as_npi1'])
                                           or r['linked_share_of_covered'] != (fmt(int(r['npis_linked'])
                                                                                   / int(r['npis_as_npi1']), 3)
                                                                               if int(r['npis_as_npi1']) else ''))))
                                       for r in coverage))
    out['baseline_link_recompute'] = sum(r['careset_2017_link'] != BASELINE_LINK[(r['npi'], r['ccn']) in linked_2017]
                                         or r['year'] not in map(str, NBER_YEARS)
                                         or r['source'] != NBER_SOURCE.format(year=r['year']) for r in tables[BASELINE])
    out['summary_recompute'] += len(tables[NPI_SUMMARY]) != len(ctx['division'])

    # Agreement with the 2026-10-24 yearly features and HRR x center flows.
    cells = defaultdict(lambda: [set(), 0.0])
    flows, pairs = Counter(), Counter()
    for r in panel:
        cell = cells[(r['npi'], r['year'])]
        cell[0].add(r['ccn'])
        cell[1] += float(r['patients_allocated'])
        if ctx['division'].get(r['npi']) == 'referring_proxy' and ctx['hrr'].get(r['npi']):
            flows[(ctx['hrr'][r['npi']], r['ccn'], r['year'])] += int(r['patients'])
            pairs[(ctx['hrr'][r['npi']], r['ccn'], r['year'])] += int(r['receiving_npis'])
    sends, group_features = set(), Counter()
    for y in YEARS:
        features = {r['npi']: r for r in read_csv(earlier / f'shared_patient_annual_{y}_features.csv')}
        sends |= {n for n, r in features.items() if int(r['out_patients_total'])}
        linked = {n for n, r in features.items() if int(r['out_patients_to_centers'])}
        mine = {n for (n, yy) in cells if yy == str(y)}
        out['features_npi_year_set'] += len(linked ^ mine)
        for n in linked & mine:
            f, cell = features[n], cells[(n, str(y))]
            out['features_partners'] += int(f['distinct_center_partners']) != len(cell[0])
            out['features_patients_allocated'] += abs(int(f['out_patients_to_centers']) - cell[1]) > 0.0005 * len(cell[0]) + 1e-6
            out['features_patients_floor'] += int(f['out_patients_to_centers']) < FLOOR
            group_features[(DIVISION_GROUPS[ctx['division'][n]], str(y))] += int(f['out_patients_to_centers'])
        published = {(r['hrr'], r['ccn'], r['year']): r for r in
                     read_csv(earlier / f'shared_patient_annual_{y}_hrr_center_flows.csv')}
        mine_flows = {k for k in flows if k[2] == str(y)}
        out['hrr_flows_patients'] += len(set(published) ^ mine_flows) + sum(
            int(published[k]['patients']) != flows[k] for k in mine_flows & set(published))
        out['hrr_flows_pairs'] += sum(int(published[k]['referrer_pairs']) != pairs[k] for k in mine_flows & set(published))

    # Every derived table recomputed from the published panel.
    by_npi = defaultdict(list)
    for r in panel:
        by_npi[r['npi']].append(r)
    fields = [c['column'] for c in SUMMARY_COLUMNS if c['column'] not in PROVENANCE]
    for r in tables[NPI_SUMMARY]:
        again = npi_summary_row(r['npi'], by_npi.get(r['npi'], []), ctx, center_hrr, sends)
        out['summary_recompute'] += any(again[f] != r[f] for f in fields)
    links = links_by_npi(panel)
    again = {(r['npi'], r['year']): r for n in links for r in change_rows(n, links[n])}
    published = {(r['npi'], r['year']): r for r in tables[NPI_CHANGES]}
    fields = [c['column'] for c in CHANGE_COLUMNS if c['column'] not in PROVENANCE]
    out['changes_recompute'] = len(set(again) ^ set(published)) + sum(
        any(again[k][f] != published[k][f] for f in fields) for k in set(again) & set(published))
    again = {(r['ccn'], r['year']): r for r in center_rows(panel, ctx, center_hrr)}
    published = {(r['ccn'], r['year']): r for r in tables[CENTER_BASE]}
    fields = [f for f in CENTER_COLUMNS if f not in PROVENANCE]
    out['centers_recompute'] = len(set(again) ^ set(published)) + sum(
        any(again[k][f] != published[k][f] for f in fields) for k in set(again) & set(published))
    again = {(r['division_group'], r['year']): r for r in group_rows(panel, tables[NPI_CHANGES], ctx, center_hrr)}
    published = {(r['division_group'], r['year']): r for r in tables[GROUPS]}
    fields = [c['column'] for c in GROUP_COLUMNS if c['column'] not in PROVENANCE]
    out['groups_recompute'] = len(set(again) ^ set(published)) + sum(
        any(again[k][f] != published[k][f] for f in fields) for k in set(again) & set(published))
    out['groups_vs_features'] = sum(int(r['patients_allocated_to_centers']) !=
                                    group_features[(r['division_group'], r['year'])] for r in tables[GROUPS])
    out['status_words'] = (sum(r['link_status'] not in NPI_STATUSES for r in tables[NPI_SUMMARY])
                           + sum(r['year_status'] not in (LINKED, NOT_OBSERVED) for r in tables[NPI_CHANGES])
                           + sum(r['year_status'] not in (CENTER_OBSERVED, CENTER_NOT_OBSERVED)
                                 for r in tables[CENTER_BASE])
                           + sum(r['region_relation'] not in REGION.values() for r in panel)
                           + sum(r['comparison_status'] not in (GAINED_BLANK, COMPARED)
                                 for t in (NPI_CHANGES, CENTER_BASE, GROUPS) for r in tables[t]))
    # Core HCP tables: floor, joins, agreement with the yearly features, and with each other.
    links, senders_ = tables[CORE_LINKS], tables[CORE_SENDERS]
    out['core_floor'] = (sum(int(r['patients_to_core']) < FLOOR for r in links)
                         + sum(int(r['patients_from_universe']) < FLOOR for r in senders_ if r['year_status'] == LINKED))
    out['core_join'] = sum(ctx['division'].get(r['npi']) != 'core' for r in senders_)
    linked_years = {(r['npi'], r['year']) for r in panel}
    features = {y: {r['npi']: r for r in read_csv(earlier / f'shared_patient_annual_{y}_features.csv')} for y in YEARS}
    for r in links:
        f = features[int(r['year'])].get(r['npi'])
        to_centers = int(f['out_patients_to_centers']) if f else 0
        at = int(r['patients_to_core_at_centers'])
        out['core_vs_features'] += (at > to_centers or at > int(r['patients_to_core'])
                                    or (at > 0) != ((r['npi'], r['year']) in linked_years and at > 0)
                                    or r['share_of_center_patients_via_core'] != (fmt(at / to_centers, 3)
                                                                                  if to_centers else ''))
    seen = set()
    for r in senders_:
        f = features[int(r['year'])].get(r['npi'])
        seen.add((r['npi'], r['year']))
        from_referrers = int(f['in_patients_from_referrers']) if f else 0
        sources = int(f['referrer_sources']) if f else 0
        if r['year_status'] == LINKED:
            parts = sum(int(r[k]) for k in ('patients_from_core', 'patients_from_affiliates', 'patients_from_other'))
            groups_ = sum(int(r[k]) for k in ('senders_core', 'senders_affiliates', 'senders_other'))
            out['core_vs_features'] += (int(r['patients_from_universe']) - parts != from_referrers
                                        or int(r['senders']) - groups_ != sources
                                        or int(r['patients_from_universe']) > int(f['in_patients_total']))
        else:
            out['core_vs_features'] += bool(from_referrers or any(r[k] for k in ('senders', 'patients_from_universe')))
    out['core_vs_features'] += sum((n, str(y)) not in seen for y in YEARS for n, f in features[y].items()
                                   if ctx['division'].get(n) == 'core' and int(f['in_patients_from_referrers']))
    for y in map(str, YEARS):
        sent = [r for r in links if r['year'] == y]
        got = [r for r in senders_ if r['year'] == y and r['year_status'] == LINKED]
        out['core_cross_table'] += (sum(int(r['patients_to_core']) for r in sent)
                                    != sum(int(r['patients_from_universe']) for r in got)
                                    or sum(int(r['core_hcps']) for r in sent) != sum(int(r['senders']) for r in got))
    again = {(r['year'], r['division_group']): r for r in reach_rows(links, ctx)}
    published = {(r['year'], r['division_group']): r for r in tables[CORE_REACH]}
    fields = [c['column'] for c in CORE_REACH_COLUMNS if c['column'] not in PROVENANCE]
    out['core_reach_recompute'] = len(set(again) ^ set(published)) + sum(
        any(again[k][f] != published[k][f] for f in fields) for k in set(again) & set(published))
    out['status_words'] += sum(r['year_status'] not in (LINKED, NOT_OBSERVED)
                               or r['comparison_status'] not in (GAINED_BLANK, COMPARED) for r in senders_)
    # Cells nothing else can recompute (the panel's mean waits, receiving NPIs of most rows) are held by the hash.
    out['fingerprint'] = sum(sha(p) != FINGERPRINTS.get(t) for t, p in paths.items())
    numbers = headline(tables, ctx)
    out['headline'] = sum(EXPECTED_HEADLINE.get(k) != v for k, v in numbers.items()) + len(set(EXPECTED_HEADLINE) - set(numbers))
    return {f'referral_{k}': v for k, v in out.items()}

if __name__ == '__main__':
    build()
