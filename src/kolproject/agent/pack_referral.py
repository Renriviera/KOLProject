"""Referral part of the agent pack: shared Medicare patients between HCPs and CAR-T centers, 2017 to 2022.

Every table derives from CareSet Systems' DocGraph Hop Teaming files, shared under CC BY-NC-SA 4.0 with CareSet's
non-commercial addendum. The counts are Medicare fee-for-service patients shared for any condition: they are not
CAR-T specific and not confirmed referrals. A count is a sum over clinician pairs with at least 11 shared patients,
so a patient can be counted more than once, and a year without a link is not observed (NULL or absent), never zero.
The 2009 to 2015 CMS baseline uses another method and is deliberately not loaded.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from .packbase import SNAPSHOTS, connect, create_table, load_csv, read_rows, register, source
from .paths import PACK

FIRST_YEAR, LAST_YEAR = 2017, 2022
PAIR_FLOOR = 11
YEARS_LABEL = f'{FIRST_YEAR} to {LAST_YEAR}'
CREDIT = '(source: CareSet Systems DocGraph Hop Teaming, CC BY-NC-SA 4.0 with a non-commercial addendum)'
REGION_TABLE = 'ref_region_center_year'
REGION_COLUMNS = [('hrr', 'text'), ('region_city', 'text'), ('region_state', 'text'), ('ccn', 'text'), ('year', 'int'),
                  ('patients', 'int'), ('referrer_pairs', 'int'), ('same_region', 'int')]


def spec(table, src, grain, key, columns, description, rename=None, indexes=(), totals=()):
    """One pack table loaded column for column from a 2026-11-20 published table."""
    return {'table': table, 'source': src, 'grain': grain, 'key': key, 'columns': columns, 'rename': rename or {},
            'indexes': list(indexes), 'totals': list(totals), 'description': f'{description} {CREDIT}.'}


SPECS = [
    spec('ref_link_year', 'shared_patient_npi_center_flows_2017_2022', 'HCP x center x year', ['npi', 'ccn', 'year'],
         [('npi', 'text'), ('ccn', 'text'), ('year', 'int'), ('patients', 'int'), ('patients_allocated', 'real'),
          ('receiving_npis', 'int'), ('mean_day_wait', 'real'), ('region_relation', 'text')],
         'Shared Medicare fee-for-service patients for any condition between an HCP and a CAR-T center in a year, summed '
         'over clinician pairs of at least 11 patients; not CAR-T specific and not confirmed referrals',
         rename={'mean_day_wait': 'mean_day_wait_weighted'}, indexes=[('ccn', 'year'), ('npi', 'year')],
         totals=['patients', 'patients_allocated', 'receiving_npis']),
    spec('ref_hcp', 'shared_patient_npi_longitudinal_2017_2022', 'HCP', ['npi'],
         [('npi', 'text'), ('link_status', 'text'), ('first_year_linked', 'int'), ('last_year_linked', 'int'),
          ('years_linked', 'int'), ('distinct_centers', 'int'), ('primary_center_ccn', 'text'),
          ('primary_center_share', 'real'), ('primary_center_region', 'text')],
         'One row per HCP of the universe: whether it shares Medicare patients (any condition) with a CAR-T center, the '
         'years linked, the number of centers, and the primary center with its share and region relation',
         rename={'distinct_centers': 'distinct_centers_ever'}, indexes=['primary_center_ccn'],
         totals=['years_linked', 'distinct_centers']),
    spec('ref_hcp_change_year', 'shared_patient_npi_link_changes_2017_2022', 'HCP x year', ['npi', 'year'],
         [('npi', 'text'), ('year', 'int'), ('year_status', 'text'), ('links_gained', 'int'), ('links_lost', 'int'),
          ('links_retained', 'int'), ('comparison_status', 'text')],
         'Center links an HCP gained, lost and kept against the previous year in the shared-Medicare-patient data, with '
         'a status that keeps a year without a link (not observed) apart from zero',
         totals=['links_gained', 'links_lost', 'links_retained']),
    spec('ref_center_year', 'shared_patient_center_referrer_base_2017_2022', 'center x year', ['ccn', 'year'],
         [('ccn', 'text'), ('year', 'int'), ('year_status', 'text'), ('referrers', 'int'), ('patients', 'int'),
          ('referrers_referring', 'int'), ('patients_referring', 'int'), ('concentration_index', 'real'),
          ('top_referrer_share', 'real'), ('out_of_region_share', 'real'), ('referrers_without_region', 'int'),
          ('new_referrers', 'int'), ('lost_referrers', 'int'), ('retained_referrers', 'int'), ('comparison_status', 'text')],
         'HCPs of any division sharing Medicare patients (any condition) with each CAR-T center by year: shared-patient '
         'count, concentration, top-HCP share, out-of-region share and HCPs gained, lost and kept; not-observed years stay blank',
         rename={'patients': 'patients_from_referrers', 'referrers_referring': 'referring_proxy_referrers',
                 'patients_referring': 'patients_from_referring_proxy', 'concentration_index': 'referrer_hhi',
                 'out_of_region_share': 'outside_hrr_patient_share', 'referrers_without_region': 'referrers_without_hrr'},
         indexes=['year'], totals=['referrers', 'patients', 'referrers_referring', 'patients_referring', 'lost_referrers']),
    spec('ref_core_links', 'shared_patient_npi_core_hcp_links_2017_2022', 'HCP x year', ['npi', 'year'],
         [('npi', 'text'), ('year', 'int'), ('core_hcps', 'int'), ('patients_to_core', 'int'),
          ('patients_to_core_at_centers', 'int'), ('share_via_core', 'real'), ('mean_day_wait', 'real')],
         'Per sending HCP and year: core HCPs it shares Medicare patients (any condition) with, the shared-patient count, '
         'and the share of its center-bound count that goes through a core HCP',
         rename={'share_via_core': 'share_of_center_patients_via_core', 'mean_day_wait': 'mean_day_wait_weighted'},
         totals=['core_hcps', 'patients_to_core', 'patients_to_core_at_centers']),
    spec('ref_core_senders', 'shared_patient_core_hcp_senders_2017_2022', 'core HCP x year', ['npi', 'year'],
         [('npi', 'text'), ('year', 'int'), ('year_status', 'text'), ('senders', 'int'), ('patients_from_universe', 'int'),
          ('patients_from_core', 'int'), ('patients_from_affiliates', 'int'), ('patients_from_other', 'int'),
          ('senders_core', 'int'), ('senders_affiliates', 'int'), ('senders_other', 'int'), ('out_of_region_share', 'real'),
          ('senders_without_region', 'int'), ('concentration_index', 'real'), ('senders_gained', 'int'),
          ('senders_lost', 'int'), ('comparison_status', 'text')],
         'Per core HCP and year: HCPs sending shared Medicare patients (any condition) and the shared-patient count, in '
         'total and from core, affiliate and other senders; the referring group is the total minus those three',
         rename={'out_of_region_share': 'outside_hrr_patient_share', 'senders_without_region': 'senders_without_hrr',
                 'concentration_index': 'sender_hhi'},
         totals=['senders', 'patients_from_universe', 'patients_from_core', 'patients_from_affiliates', 'patients_from_other']),
    spec('ref_reach_group_year', 'shared_patient_core_reach_by_group_2017_2022', 'year x division group',
         ['year', 'division_group'],
         [('year', 'int'), ('division_group', 'text'), ('npis_in_group', 'int'), ('senders_to_core', 'int'),
          ('sender_share_of_group', 'real'), ('sender_core_pairs', 'int'), ('patients_to_core', 'int')],
         'Per year and division group: HCPs sharing Medicare patients (any condition) with at least one core HCP and '
         'their shared-patient count', totals=['senders_to_core', 'sender_core_pairs', 'patients_to_core']),
    spec('ref_group_year', 'shared_patient_division_group_flows_2017_2022', 'division group x year',
         ['division_group', 'year'],
         [('division_group', 'text'), ('year', 'int'), ('npis_in_group', 'int'), ('npis_linked', 'int'),
          ('center_links', 'int'), ('patients_allocated', 'int'), ('out_of_region_share', 'real'), ('links_gained', 'int'),
          ('links_lost', 'int'), ('comparison_status', 'text')],
         'Per division group and year: HCPs linked to a CAR-T center through shared Medicare patients (any condition), '
         'links, shared-patient count, out-of-region share and links gained and lost',
         rename={'patients_allocated': 'patients_allocated_to_centers', 'out_of_region_share': 'outside_hrr_patient_share'},
         totals=['npis_linked', 'center_links', 'patients_allocated', 'links_gained', 'links_lost']),
]
TABLES = [s['table'] for s in SPECS] + [REGION_TABLE]
KEYS = {**{s['table']: s['key'] for s in SPECS}, REGION_TABLE: ['hrr', 'ccn', 'year']}


def region_names():
    """Hospital referral region number to (city, state), as published with every HCP."""
    return {r['hrr']: (r['hrr_city'], r['hrr_state']) for r in read_rows(source('divisions', 'hcp_divisions')) if r['hrr']}


def region_source_rows():
    """Rows of the six published region-to-center tables (referring-division HCPs only)."""
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for row in read_rows(source('divisions', f'shared_patient_annual_{year}_hrr_center_flows')):
            yield year, row


def region_rows():
    names = region_names()
    for _, r in region_source_rows():
        city, state = names.get(r['hrr'], (None, None))
        yield (r['hrr'], city, state, r['ccn'], int(r['year']), int(r['patients']), int(r['referrer_pairs']), int(r['same_hrr']))


def build(con):
    counts = {}
    for s in SPECS:
        counts[s['table']] = load_csv(con, s['table'], source('extensions', s['source']), s['columns'], rename=s['rename'],
                                      primary_key=s['key'], indexes=s['indexes'])
        register(con, s['table'], s['grain'], [f"{SNAPSHOTS['extensions']}/{s['source']}"], s['description'], YEARS_LABEL)
    counts[REGION_TABLE] = create_table(con, REGION_TABLE, REGION_COLUMNS, region_rows(), primary_key=KEYS[REGION_TABLE],
                                        indexes=[('ccn', 'year')])
    register(con, REGION_TABLE, 'region x center x year',
             [f"{SNAPSHOTS['divisions']}/shared_patient_annual_{year}_hrr_center_flows" for year in range(FIRST_YEAR, LAST_YEAR + 1)]
             + [f"{SNAPSHOTS['divisions']}/hcp_divisions"],
             'Shared Medicare patients (any condition) between the referring-division HCPs of a hospital referral region '
             f'and a CAR-T center by year, with the region named by its city {CREDIT}.', YEARS_LABEL)
    return counts


def plural(n, word):
    return f'{n:,} {word}' if n == 1 else f'{n:,} {word}s'


def percent(share):
    return f'{round(share * 100)}%'


def center_facts(con):
    """Latest observed year and the change since the first observed year, per center."""
    observed = defaultdict(list)
    for ccn, year, hcps, patients, outside in con.execute(
            "select ccn, year, referrers, patients, out_of_region_share from ref_center_year "
            "where year_status = 'observed' order by ccn, year"):
        observed[ccn].append((year, hcps, patients, outside))
    out = {}
    for (ccn,) in con.execute('select distinct ccn from ref_center_year order by ccn'):
        years = observed.get(ccn)
        if not years:
            out[('center', ccn)] = [f'No HCP had shared Medicare patients for any condition with this center at the '
                                    f'{PAIR_FLOOR}-patient reporting floor in {YEARS_LABEL}: not observed, which is not zero.']
            continue
        (year0, hcps0, patients0, _), (year, hcps, patients, outside) = years[0], years[-1]
        when = f'In {year}' if year == LAST_YEAR else f'In {year}, the latest year observed for this center'
        facts = [f'{when}, {plural(hcps, "HCP")} had shared Medicare patients for any condition with this center, a '
                 f'shared-patient count of {patients:,} (summed over HCP pairs, so a patient can be counted more than once).']
        if outside is not None:
            facts.append(f"{percent(outside)} of the {year} shared-patient count came from HCPs outside the center's "
                         'hospital referral region.')
        if year0 != year:
            facts.append(f'Between {year0} and {year} the shared-patient count went from {patients0:,} to {patients:,} '
                         f'and the HCPs sharing patients from {hcps0:,} to {hcps:,}.')
        out[('center', ccn)] = facts
    return out


def hcp_facts(con):
    """Years linked, number of centers and the primary center, for every HCP with a center link."""
    where = {'inside': ", inside the HCP's own hospital referral region", 'outside': ", outside the HCP's own hospital referral region"}
    out = {}
    for npi, first, last, years, centers, share, region, center in con.execute(
            "select h.npi, h.first_year_linked, h.last_year_linked, h.years_linked, h.distinct_centers, "
            "h.primary_center_share, h.primary_center_region, c.name from ref_hcp h "
            "left join dim_center c on c.ccn = h.primary_center_ccn where h.link_status = 'linked' order by h.npi"):
        span = f'in {first} only' if first == last else f'first {first}, last {last}'
        facts = [f'This HCP had shared Medicare patients for any condition with at least one CAR-T center in {years} of '
                 f'the 6 years {YEARS_LABEL} ({span}).']
        if center and centers == 1:
            facts.append(f'Over those years the HCP is linked to 1 center, {center}{where.get(region, "")}.')
        elif center and share is not None:
            facts.append(f'Over those years the HCP is linked to {centers:,} centers; the primary one is {center}, '
                         f"with {percent(share)} of the HCP's shared-patient count{where.get(region, '')}.")
        out[('hcp', npi)] = facts
    return out


def community_facts(con):
    """Centers behind each community and their summed shared-patient counts in the latest and first year."""
    out = {}
    stats = {}
    for cid, year, centers, patients in con.execute(
            "select c.community_id, y.year, count(*), sum(y.patients) from dim_center c join ref_center_year y on y.ccn = c.ccn "
            "where c.community_id is not null and y.year_status = 'observed' and y.year in (?, ?) group by 1, 2",
            (FIRST_YEAR, LAST_YEAR)):
        stats[(cid, year)] = (centers, patients)
    hcps = dict(con.execute('select c.community_id, count(distinct l.npi) from ref_link_year l join dim_center c on c.ccn = l.ccn '
                            'where l.year = ? and c.community_id is not null group by 1', (LAST_YEAR,)).fetchall())
    largest = {}
    for cid, name, patients in con.execute(
            "select c.community_id, c.name, y.patients from dim_center c join ref_center_year y on y.ccn = c.ccn "
            "where y.year = ? and y.year_status = 'observed' and c.community_id is not null order by y.patients, c.ccn desc",
            (LAST_YEAR,)):
        largest[cid] = (name, patients)
    for cid, total in con.execute('select community_id, count(*) from dim_center where community_id is not null group by 1 order by 1'):
        if (cid, LAST_YEAR) not in stats:
            out[('community', str(cid))] = [
                f'No HCP had shared Medicare patients for any condition with the {plural(total, "CAR-T center")} attached to '
                f'this community in {LAST_YEAR} at the {PAIR_FLOOR}-patient reporting floor: not observed, which is not zero.']
            continue
        centers, patients = stats[(cid, LAST_YEAR)]
        which = 'the only CAR-T center' if total == 1 else f'{centers} of the {total} CAR-T centers'
        facts = [f'In {LAST_YEAR}, {plural(hcps.get(cid, 0), "HCP")} had shared Medicare patients for any condition with '
                 f'{which} attached to this community.']
        trend = ''
        if (cid, FIRST_YEAR) in stats:
            centers0, patients0 = stats[(cid, FIRST_YEAR)]
            trend = f'; in {FIRST_YEAR} it was {patients0:,} across {plural(centers0, "center")}'
        facts.append(f'Summed over {plural(centers, "center")}, the {LAST_YEAR} shared-patient count is {patients:,} (a sum '
                     f'over HCP pairs, so a patient can be counted more than once){trend}.')
        name, top = largest[cid]
        if centers > 1:
            facts.append(f'The center with the largest {LAST_YEAR} shared-patient count is {name} ({top:,}).')
        out[('community', str(cid))] = facts
    return out


def card_facts(con):
    """{(kind, id): sentences} for centers, HCPs with a center link, and communities with a center."""
    return {**center_facts(con), **hcp_facts(con), **community_facts(con)}


def table_columns(con, table):
    return {row[1]: row[2] for row in con.execute(f'pragma table_info("{table}")')}


def check_structure(con):
    """Checks that need only the pack: keys, links to the dims, years, the pair floor, status rules and agreement between tables."""
    one = lambda sql: con.execute(sql).fetchone()[0]
    years = f'{FIRST_YEAR} and {LAST_YEAR}'
    out = {}
    for table, key in KEYS.items():
        columns = table_columns(con, table)
        out[f'{table}_duplicate_keys'] = one(f'select count(*) from (select 1 from {table} group by {", ".join(key)} having count(*) > 1)')
        out[f'{table}_blank_keys'] = one(f'select count(*) from {table} where ' + ' or '.join(f'{c} is null' for c in key))
        if 'npi' in columns:
            out[f'{table}_npi_not_in_dim_hcp'] = one(f'select count(*) from {table} where npi not in (select npi from dim_hcp)')
        if 'ccn' in columns:
            out[f'{table}_ccn_not_in_dim_center'] = one(f'select count(*) from {table} where ccn not in (select ccn from dim_center)')
        if 'year' in columns:
            out[f'{table}_year_outside_{FIRST_YEAR}_{LAST_YEAR}'] = one(f'select count(*) from {table} where year not between {years}')
        numeric = [c for c, kind in columns.items() if kind in ('INTEGER', 'REAL')]
        out[f'{table}_negative_values'] = one(f'select count(*) from {table} where ' + ' or '.join(f'{c} < 0' for c in numeric))
    out.update({
        'ref_link_year_patients_under_floor': one(f'select count(*) from ref_link_year where patients < {PAIR_FLOOR} or patients is null'),
        'ref_link_year_region_relation': one("select count(*) from ref_link_year where region_relation not in ('inside', 'outside', 'unknown')"),
        'ref_hcp_primary_center_not_in_dim_center': one('select count(*) from ref_hcp where primary_center_ccn is not null '
                                                        'and primary_center_ccn not in (select ccn from dim_center)'),
        'ref_hcp_year_outside_span': one(f'select count(*) from ref_hcp where first_year_linked not between {years} '
                                         f'or last_year_linked not between {years} or first_year_linked > last_year_linked'),
        'ref_hcp_unlinked_not_blank': one("select count(*) from ref_hcp where (link_status = 'linked') != (years_linked is not null) "
                                          "or (link_status = 'linked') != (primary_center_ccn is not null) "
                                          "or link_status not in ('linked', 'no_center_link', 'no_outgoing_edge')"),
        'ref_hcp_vs_links': one(
            'with l as (select npi, count(distinct year) y, count(distinct ccn) c, min(year) a, max(year) b from ref_link_year group by npi) '
            "select count(*) from ref_hcp h left join l on l.npi = h.npi where (h.link_status = 'linked') != (l.npi is not null) "
            'or (l.npi is not null and (l.y != h.years_linked or l.c != h.distinct_centers or l.a != h.first_year_linked or l.b != h.last_year_linked))'),
        'ref_hcp_change_year_vs_links': one(
            'with l as (select npi, year, count(*) n from ref_link_year group by npi, year) '
            "select (select count(*) from ref_hcp_change_year c left join l on l.npi = c.npi and l.year = c.year "
            "where (c.year_status = 'linked') != (l.n is not null) or (l.n is not null and c.links_gained + c.links_retained != l.n)) "
            '+ (select count(*) from l left join ref_hcp_change_year c on c.npi = l.npi and c.year = l.year where c.npi is null)'),
        'ref_center_year_status_rule': one(
            "select count(*) from ref_center_year where (year_status = 'observed') != (patients is not null) "
            "or (year_status = 'observed') != (referrers is not null) or patients = 0 or referrers = 0 "
            "or year_status not in ('observed', 'not_observed')"),
        'ref_center_year_centers_missing': one('select count(*) from dim_center where ccn not in (select ccn from ref_center_year)'),
        # The center table counts universe HCPs of every division; its referring columns count division referring_proxy.
        'ref_center_year_vs_links_all_divisions': one(
            'with l as (select ccn, year, count(*) n, sum(patients) p from ref_link_year group by ccn, year) '
            'select (select count(*) from ref_center_year c left join l on l.ccn = c.ccn and l.year = c.year '
            "where (c.year_status = 'observed' and (l.n is null or l.n != c.referrers or l.p != c.patients)) "
            "or (c.year_status != 'observed' and l.n is not null)) "
            '+ (select count(*) from l left join ref_center_year c on c.ccn = l.ccn and c.year = l.year where c.ccn is null)'),
        'ref_center_year_vs_links_referring': one(
            'with l as (select l.ccn, l.year, count(*) n, sum(l.patients) p from ref_link_year l join dim_hcp h on h.npi = l.npi '
            "where h.division = 'referring_proxy' group by l.ccn, l.year) "
            'select count(*) from ref_center_year c left join l on l.ccn = c.ccn and l.year = c.year '
            "where c.year_status = 'observed' and (coalesce(l.n, 0) != c.referrers_referring or coalesce(l.p, 0) != c.patients_referring)"),
        # The region table counts referring-division HCPs with a known region, grouped by the HCP's own region.
        'ref_region_center_year_vs_links_referring': one(
            'with l as (select h.hrr, l.ccn, l.year, sum(l.patients) p, sum(l.receiving_npis) n from ref_link_year l '
            "join dim_hcp h on h.npi = l.npi where h.division = 'referring_proxy' and h.hrr is not null group by h.hrr, l.ccn, l.year) "
            'select (select count(*) from ref_region_center_year r left join l on l.hrr = r.hrr and l.ccn = r.ccn and l.year = r.year '
            'where l.p is null or l.p != r.patients or l.n != r.referrer_pairs) '
            '+ (select count(*) from l left join ref_region_center_year r on r.hrr = l.hrr and r.ccn = l.ccn and r.year = l.year where r.hrr is null)'),
        'ref_region_center_year_patients_under_floor': one(f'select count(*) from ref_region_center_year where patients < {PAIR_FLOOR} or patients is null'),
        'ref_region_center_year_unnamed_region': one('select count(*) from ref_region_center_year where region_city is null or region_state is null'),
        'ref_region_center_year_same_region_flag': one('select count(*) from ref_region_center_year r join dim_center c on c.ccn = r.ccn '
                                                       'where r.same_region != (r.hrr = c.hrr)'),
        'ref_group_year_vs_links': one(
            'with l as (select h.division_group g, l.year, count(distinct l.npi) n, count(*) k, sum(l.patients_allocated) p '
            'from ref_link_year l join dim_hcp h on h.npi = l.npi group by h.division_group, l.year) '
            'select count(*) from ref_group_year g left join l on l.g = g.division_group and l.year = g.year '
            'where coalesce(l.n, 0) != g.npis_linked or coalesce(l.k, 0) != g.center_links '
            'or abs(coalesce(l.p, 0) - g.patients_allocated) > 0.51'),
        'ref_core_links_sender_floor': one(f'select count(*) from ref_core_links where patients_to_core < {PAIR_FLOOR} or core_hcps < 1'),
        'ref_core_senders_receiver_not_core': one('select count(*) from ref_core_senders s join dim_hcp h on h.npi = s.npi where h.is_core != 1'),
        'ref_core_senders_status_rule': one(
            "select count(*) from ref_core_senders where (year_status = 'linked') != (patients_from_universe is not null) "
            "or patients_from_universe = 0 or senders = 0 or year_status not in ('linked', 'not_observed')"),
        'ref_core_senders_groups_exceed_total': one(
            'select count(*) from ref_core_senders where patients_from_universe < patients_from_core + patients_from_affiliates + patients_from_other '
            'or senders < senders_core + senders_affiliates + senders_other'),
        'ref_reach_group_year_vs_core_links': one(
            'with l as (select h.division_group g, l.year, count(*) n, sum(l.core_hcps) k, sum(l.patients_to_core) p '
            'from ref_core_links l join dim_hcp h on h.npi = l.npi group by h.division_group, l.year) '
            'select count(*) from ref_reach_group_year r left join l on l.g = r.division_group and l.year = r.year '
            'where coalesce(l.n, 0) != r.senders_to_core or coalesce(l.k, 0) != r.sender_core_pairs or coalesce(l.p, 0) != r.patients_to_core'),
        # Summed over core HCPs, each sender group of ref_core_senders equals the group's reach; referring is the remainder.
        'ref_core_senders_vs_reach_by_group': one(
            'with s as (select year, sum(patients_from_core) core, sum(patients_from_affiliates) affiliates, sum(patients_from_other) other, '
            'sum(patients_from_universe - patients_from_core - patients_from_affiliates - patients_from_other) referring '
            'from ref_core_senders group by year) '
            "select count(*) from ref_reach_group_year r left join s on s.year = r.year where s.year is null or r.patients_to_core != "
            "case r.division_group when 'core' then s.core when 'affiliates' then s.affiliates when 'other' then s.other else s.referring end"),
        'ref_no_baseline_tables': one("select count(*) from sqlite_master where name like 'ref\\_%' escape '\\' "
                                      "and (name like '%baseline%' or name like '%2009%' or name like '%coverage%')"),
    })
    return out


def csv_summary(path, columns, rename):
    """Row count and column totals of a published table, read the way the loader reads it."""
    rows, totals = 0, dict.fromkeys(columns, 0.0)
    for row in read_rows(path):
        rows += 1
        for column in columns:
            totals[column] += float(row[rename.get(column, column)] or 0)
    return rows, totals


def check_sources(con):
    """Row counts and totals against the published tables, and the two source rules the pack tables cannot show alone."""
    one = lambda sql, *args: con.execute(sql, args).fetchone()[0]
    out = {}
    for s in SPECS:
        rows, totals = csv_summary(source('extensions', s['source']), s['totals'], s['rename'])
        out[f"{s['table']}_rows_vs_source"] = abs(rows - one(f"select count(*) from {s['table']}"))
        for column, total in totals.items():
            packed = one(f"select coalesce(sum({column}), 0) from {s['table']}")
            out[f"{s['table']}_{column}_total_vs_source"] = int(abs(packed - total) > 0.0005)
    region = list(region_source_rows())
    out[f'{REGION_TABLE}_rows_vs_source'] = abs(len(region) - one(f'select count(*) from {REGION_TABLE}'))
    for column in ('patients', 'referrer_pairs'):
        out[f'{REGION_TABLE}_{column}_total_vs_source'] = abs(sum(int(r[column]) for _, r in region)
                                                              - one(f'select coalesce(sum({column}), 0) from {REGION_TABLE}'))
    out[f'{REGION_TABLE}_source_year_or_kind'] = sum(int(r['year']) != year or r['kind'] != 'annual' for year, r in region)
    out['ref_core_senders_referring_remainder_vs_yearly_features'] = referring_remainder_errors(con)
    out['ref_no_baseline_sources'] = one("select count(*) from pack_table where name like 'ref\\_%' escape '\\' "
                                         "and (sources like '%2009%' or sources like '%baseline%' or sources like '%coverage%')")
    return out


def referring_remainder_errors(con):
    """Core HCP years where the total minus the three published sender groups differs from the published yearly features.

    The yearly features are read here only to confirm the subtraction rule stated with the source table; they are not loaded.
    """
    published = {}
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for r in read_rows(source('divisions', f'shared_patient_annual_{year}_features')):
            published[(r['npi'], year)] = (int(float(r['in_patients_from_referrers'] or 0)), int(float(r['referrer_sources'] or 0)))
    errors = 0
    for npi, year, patients, senders in con.execute(
            'select npi, year, patients_from_universe - patients_from_core - patients_from_affiliates - patients_from_other, '
            "senders - senders_core - senders_affiliates - senders_other from ref_core_senders where year_status = 'linked'"):
        errors += published.get((npi, year), (0, 0)) != (patients, senders)
    return errors


def check(con):
    """{check_name: error_count}; zero passes."""
    return {**check_structure(con), **check_sources(con)}


def main():
    """Build the referral tables into agent/pack/dev_referral.sqlite, a copy of the core tables, and check them."""
    path = PACK / 'dev_referral.sqlite'
    for suffix in ('', '-wal', '-shm'):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    base = sqlite3.connect(f'file:{PACK / "dims.sqlite"}?mode=ro', uri=True)
    con = connect(path)
    base.backup(con)
    base.close()
    counts = build(con)
    con.commit()
    checks = check(con)
    facts = card_facts(con)
    print('REFERRAL', json.dumps(counts), 'CARD_FACTS', len(facts), 'CHECKS_RUN', len(checks),
          'CHECKS_FAILED', {k: v for k, v in checks.items() if v})
    con.close()


if __name__ == '__main__':
    main()
