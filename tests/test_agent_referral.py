"""Referral pack tables, checks, card facts and named queries on a small synthetic pack (invented ids and names)."""
import csv
from collections import defaultdict

import pytest

from kolproject.agent import pack_referral as p
from kolproject.agent import packbase, queries, queries_referral
from kolproject.agent.paths import PACK

A, B, C, D, E, F, G = (f'100000000{i}' for i in range(1, 8))
ALPHA, BETA, CHILD, LATE = '990001', '990002', '990003', '990004'
YEARS = range(2017, 2023)
GROUPS = ('core', 'affiliates', 'referring', 'other')
HCPS = [  # npi, name, city, state, hrr, division, division_group, is_core, community_id
    (A, 'Referring One', 'Alphaville', 'AA', '10', 'referring_proxy', 'referring', 0, 1),
    (B, 'Affiliate Two', 'Betatown', 'BB', '20', 'affiliate_A', 'affiliates', 0, 1),
    (C, 'Core Three', 'Alphaville', 'AA', '10', 'core', 'core', 1, 1),
    (D, 'Other Four', None, None, None, 'hemonc_other', 'other', 0, None),
    (E, 'Referring Five', 'Gammatown', 'CC', '30', 'referring_proxy', 'referring', 0, None),
    (F, 'Core Six', 'Betatown', 'BB', '20', 'core', 'core', 1, 1),
    (G, 'Referring Seven', 'Alphaville', 'AA', '10', 'referring_proxy', 'referring', 0, 1)]
CENTERS = [  # ccn, name, city, state, hrr, community_id
    (ALPHA, 'Alpha Hospital', 'Alphaville', 'AA', '10', 1), (BETA, 'Beta Medical Center', 'Betatown', 'BB', '20', 1),
    (CHILD, 'Gamma Childrens Hospital', 'Alphaville', 'AA', '10', 2), (LATE, 'Delta Clinic', 'Betatown', 'BB', '20', None)]
LINKS = [  # npi, ccn, year, patients, patients_allocated, receiving_npis, mean_day_wait, region_relation
    (A, ALPHA, 2017, 20, 20.0, 1, 10.0, 'inside'), (B, ALPHA, 2017, 30, 15.0, 2, 20.0, 'outside'),
    (B, BETA, 2017, 30, 15.0, 1, 20.0, 'inside'), (A, ALPHA, 2018, 25, 25.0, 1, 12.0, 'inside'),
    (A, LATE, 2021, 15, 15.0, 1, 9.0, 'outside'), (A, ALPHA, 2022, 40, 40.0, 2, 8.5, 'inside'),
    (B, ALPHA, 2022, 60, 60.0, 1, 5.0, 'outside'), (C, ALPHA, 2022, 50, 50.0, 1, 3.0, 'inside'),
    (D, ALPHA, 2022, 11, 11.0, 1, 30.0, 'unknown'), (E, BETA, 2022, 12, 12.0, 1, 40.0, 'outside')]
CORE_LINKS = [  # sender, year, core HCPs, patients to core, of which at centers, share via core, mean wait
    (B, 2017, 1, 12, 12, None, 9.0), (A, 2022, 1, 14, 14, 0.35, 7.0), (B, 2022, 1, 20, 20, 0.333, 6.0), (D, 2022, 1, 11, 11, 1.0, 4.0)]
CORE_SENDERS = [
    {'npi': F, 'year': 2017, 'year_status': 'linked', 'senders': 1, 'patients_from_universe': 12, 'patients_from_core': 0,
     'patients_from_affiliates': 12, 'patients_from_other': 0, 'senders_core': 0, 'senders_affiliates': 1, 'senders_other': 0,
     'out_of_region_share': 0.0, 'senders_without_region': 0, 'concentration_index': 1.0, 'comparison_status': 'first_year'},
    {'npi': F, 'year': 2018, 'year_status': 'not_observed', 'senders_lost': 1, 'comparison_status': 'compared'},
    {'npi': C, 'year': 2022, 'year_status': 'linked', 'senders': 3, 'patients_from_universe': 45, 'patients_from_core': 0,
     'patients_from_affiliates': 20, 'patients_from_other': 11, 'senders_core': 0, 'senders_affiliates': 1, 'senders_other': 1,
     'out_of_region_share': 0.588, 'senders_without_region': 1, 'concentration_index': 0.3541, 'senders_gained': 3,
     'senders_lost': 0, 'comparison_status': 'compared'}]
REACH = {(2017, 'affiliates'): (1, 1, 12), (2022, 'affiliates'): (1, 1, 20), (2022, 'referring'): (1, 1, 14), (2022, 'other'): (1, 1, 11)}
HRR = {h[0]: h[4] for h in HCPS}
DIVISION = {h[0]: h[5] for h in HCPS}
GROUP = {h[0]: h[6] for h in HCPS}
CENTER_HRR = {c[0]: c[4] for c in CENTERS}
REGION_NAMES = {'10': ('Alphaville', 'AA'), '20': ('Betatown', 'BB'), '30': ('Gammatown', 'CC')}


def compared(year):
    return 'first_year' if year == 2017 else 'compared'


def center_year_rows():
    """The center table as the source rule builds it: HCPs of every division, blank when the year is not observed."""
    rows = []
    for ccn in CENTER_HRR:
        before = {}
        for year in YEARS:
            now = {l[0]: l[3] for l in LINKS if l[1] == ccn and l[2] == year}
            row = {'ccn': ccn, 'year': year, 'year_status': 'observed' if now else 'not_observed', 'comparison_status': compared(year)}
            if year > 2017:
                row['lost_referrers'] = len(set(before) - set(now))
            if now:
                total, known = sum(now.values()), {n: v for n, v in now.items() if HRR[n]}
                outside = sum(v for n, v in known.items() if HRR[n] != CENTER_HRR[ccn])
                row.update(referrers=len(now), patients=total, referrers_referring=sum(DIVISION[n] == 'referring_proxy' for n in now),
                           patients_referring=sum(v for n, v in now.items() if DIVISION[n] == 'referring_proxy'),
                           concentration_index=round(sum((v / total) ** 2 for v in now.values()), 4),
                           top_referrer_share=round(max(now.values()) / total, 3),
                           out_of_region_share=round(outside / sum(known.values()), 3) if known else None,
                           referrers_without_region=len(now) - len(known))
                if year > 2017:
                    row.update(new_referrers=len(set(now) - set(before)), retained_referrers=len(set(now) & set(before)))
            rows.append(row)
            before = now
    return rows


def hcp_rows():
    rows = []
    for npi in HRR:
        mine = [l for l in LINKS if l[0] == npi]
        if not mine:
            rows.append({'npi': npi, 'link_status': 'no_center_link' if npi == F else 'no_outgoing_edge'})
            continue
        allocated = defaultdict(float)
        for link in mine:
            allocated[link[1]] += link[4]
        primary, years = max(allocated, key=allocated.get), {l[2] for l in mine}
        rows.append({'npi': npi, 'link_status': 'linked', 'first_year_linked': min(years), 'last_year_linked': max(years),
                     'years_linked': len(years), 'distinct_centers': len(allocated), 'primary_center_ccn': primary,
                     'primary_center_share': round(allocated[primary] / sum(allocated.values()), 3),
                     'primary_center_region': 'unknown' if not HRR[npi] else 'inside' if HRR[npi] == CENTER_HRR[primary] else 'outside'})
    return rows


def change_rows():
    rows = []
    for npi in HRR:
        before = set()
        for year in YEARS:
            now = {l[1] for l in LINKS if l[0] == npi and l[2] == year}
            if now or before:
                row = {'npi': npi, 'year': year, 'year_status': 'linked' if now else 'not_observed', 'comparison_status': compared(year)}
                if year > 2017:
                    row.update(links_gained=len(now - before), links_lost=len(before - now), links_retained=len(now & before))
                rows.append(row)
            before = now
    return rows


def group_rows():
    rows = []
    for year in YEARS:
        for group in GROUPS:
            mine = [l for l in LINKS if l[2] == year and GROUP[l[0]] == group]
            rows.append({'division_group': group, 'year': year, 'npis_in_group': sum(g == group for g in GROUP.values()),
                         'npis_linked': len({l[0] for l in mine}), 'center_links': len(mine),
                         'patients_allocated': round(sum(l[4] for l in mine)), 'comparison_status': compared(year)})
    return rows


def reach_rows():
    return [{'year': year, 'division_group': group, 'npis_in_group': sum(g == group for g in GROUP.values()),
             'senders_to_core': REACH.get((year, group), (0, 0, 0))[0], 'sender_share_of_group': 0.0,
             'sender_core_pairs': REACH.get((year, group), (0, 0, 0))[1], 'patients_to_core': REACH.get((year, group), (0, 0, 0))[2]}
            for year in (2017, 2022) for group in GROUPS]


def region_rows(year):
    """A published region-to-center table: referring-division HCPs with a known region only."""
    flows = defaultdict(lambda: [0, 0])
    for npi, ccn, link_year, patients, _, receivers, *_ in LINKS:
        if link_year == year and DIVISION[npi] == 'referring_proxy' and HRR[npi]:
            flows[(HRR[npi], ccn)][0] += patients
            flows[(HRR[npi], ccn)][1] += receivers
    return [{'hrr': hrr, 'ccn': ccn, 'year': year, 'kind': 'annual', 'patients': patients, 'referrer_pairs': pairs,
             'same_hrr': int(hrr == CENTER_HRR[ccn])} for (hrr, ccn), (patients, pairs) in flows.items()]


def named(table, rows):
    names = [c for c, _ in next(s for s in p.SPECS if s['table'] == table)['columns']]
    return [dict(zip(names, row)) for row in rows]


def write(path, rows, header):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, header)
        writer.writeheader()
        writer.writerows({k: '' if row.get(k) is None else row[k] for k in header} for row in rows)


def write_sources():
    """Tiny published tables with the source column names; None becomes a blank cell."""
    tables = {'ref_link_year': named('ref_link_year', LINKS), 'ref_hcp': hcp_rows(), 'ref_hcp_change_year': change_rows(),
              'ref_center_year': center_year_rows(), 'ref_core_links': named('ref_core_links', CORE_LINKS),
              'ref_core_senders': CORE_SENDERS, 'ref_reach_group_year': reach_rows(), 'ref_group_year': group_rows()}
    for s in p.SPECS:
        source = [s['rename'].get(c, c) for c, _ in s['columns']]
        write(packbase.source('extensions', s['source']), [dict(zip(source, (row.get(c) for c, _ in s['columns'])))
                                                           for row in tables[s['table']]], source)
    for year in YEARS:
        write(packbase.source('divisions', f'shared_patient_annual_{year}_hrr_center_flows'), region_rows(year),
              ['hrr', 'ccn', 'year', 'kind', 'patients', 'referrer_pairs', 'same_hrr'])
        referring = [{'npi': C, 'in_patients_from_referrers': 14, 'referrer_sources': 1}] if year == 2022 else []
        write(packbase.source('divisions', f'shared_patient_annual_{year}_features'), referring,
              ['npi', 'in_patients_from_referrers', 'referrer_sources'])
    write(packbase.source('divisions', 'hcp_divisions'),
          [{'npi': npi, 'hrr': hrr, 'hrr_city': REGION_NAMES[hrr][0], 'hrr_state': REGION_NAMES[hrr][1]} for npi, hrr in HRR.items() if hrr],
          ['npi', 'hrr', 'hrr_city', 'hrr_state'])


def dims(con):
    packbase.create_table(con, 'dim_hcp', [('npi', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('hrr', 'text'),
                                           ('division', 'text'), ('division_group', 'text'), ('is_core', 'int'), ('community_id', 'int')], HCPS)
    packbase.create_table(con, 'dim_center', [('ccn', 'text'), ('name', 'text'), ('city', 'text'), ('state', 'text'), ('hrr', 'text'),
                                              ('community_id', 'int')], CENTERS)
    packbase.create_table(con, 'dim_community', [('community_id', 'int'), ('short_label', 'text')],
                          [(1, 'Alpha orbit'), (2, 'Beta orbit'), (3, 'Empty orbit')])
    packbase.create_table(con, 'hcp_center', [('npi', 'text'), ('ccn', 'text')], [(C, ALPHA), (F, ALPHA), (B, ALPHA)])


@pytest.fixture
def pack(tmp_path, monkeypatch):
    monkeypatch.setattr(packbase, 'PROCESSED', tmp_path / 'processed')
    write_sources()
    con = packbase.connect(tmp_path / 'pack.sqlite')
    dims(con)
    p.build(con)
    con.commit()
    yield con
    con.close()


def rows(con, name, **params):
    return queries.run(name, params, con=con)['rows']


def test_build_loads_every_table_and_all_checks_pass(pack):
    counts = {t: pack.execute(f'select count(*) from {t}').fetchone()[0] for t in p.TABLES}
    assert counts == {'ref_link_year': 10, 'ref_hcp': 7, 'ref_hcp_change_year': 11,'ref_center_year': 24, 'ref_core_links': 4,
                      'ref_core_senders': 3, 'ref_reach_group_year': 8, 'ref_group_year': 24, 'ref_region_center_year': 5}
    assert [c[1] for c in pack.execute('pragma table_info(ref_link_year)')] == [
        'npi', 'ccn', 'year', 'patients', 'patients_allocated', 'receiving_npis', 'mean_day_wait', 'region_relation']
    assert {k: v for k, v in p.check(pack).items() if v} == {}
    described = pack.execute("select count(*), sum(description like '%CareSet Systems%') from pack_table where name like 'ref_%'").fetchone()
    assert tuple(described) == (9, 9)


def test_blank_cells_stay_null_and_never_become_zero(pack):
    blank = pack.execute("select referrers, patients, out_of_region_share, new_referrers, lost_referrers from ref_center_year "
                         "where ccn = ? and year = 2019", (ALPHA,)).fetchone()
    assert tuple(blank) == (None, None, None, None, 1)
    assert pack.execute('select count(*) from ref_center_year where patients = 0 or referrers = 0').fetchone()[0] == 0
    assert tuple(pack.execute('select years_linked, primary_center_ccn from ref_hcp where npi = ?', (G,)).fetchone()) == (None, None)
    assert pack.execute('select share_via_core from ref_core_links where npi = ? and year = 2017', (B,)).fetchone()[0] is None
    assert tuple(pack.execute("select region_city, region_state, same_region from ref_region_center_year where hrr = '30'").fetchone()) == ('Gammatown', 'CC', 0)


@pytest.mark.parametrize('sql, failing', [
    ('update ref_link_year set patients = 10 where npi = "1000000004"', 'ref_link_year_patients_under_floor'),
    ('update ref_link_year set year = 2015 where npi = "1000000005"', 'ref_link_year_year_outside_2017_2022'),
    ('update ref_link_year set npi = "1999999999" where npi = "1000000005"', 'ref_link_year_npi_not_in_dim_hcp'),
    ('update ref_link_year set ccn = "000000" where npi = "1000000005"', 'ref_link_year_ccn_not_in_dim_center'),
    ('update ref_center_year set patients = 0, referrers = 0 where year_status = "not_observed"', 'ref_center_year_status_rule'),
    ('update ref_center_year set patients = patients + 1 where ccn = "990001" and year = 2022', 'ref_center_year_vs_links_all_divisions'),
    ('update ref_center_year set patients_referring = 0 where ccn = "990001" and year = 2022', 'ref_center_year_vs_links_referring'),
    ('delete from ref_center_year where ccn = "990003"', 'ref_center_year_centers_missing'),
    ('update ref_region_center_year set patients = patients + 1 where hrr = "30"', 'ref_region_center_year_vs_links_referring'),
    ('update ref_region_center_year set region_city = null where hrr = "30"', 'ref_region_center_year_unnamed_region'),
    ('update ref_hcp set years_linked = 0, distinct_centers = 0 where link_status != "linked"', 'ref_hcp_unlinked_not_blank'),
    ('update ref_hcp set distinct_centers = 5 where npi = "1000000001"', 'ref_hcp_vs_links'),
    ('update ref_hcp_change_year set year_status = "linked" where year_status = "not_observed"', 'ref_hcp_change_year_vs_links'),
    ('update ref_group_year set patients_allocated = patients_allocated + 2 where year = 2022', 'ref_group_year_vs_links'),
    ('update ref_core_senders set patients_from_universe = 0 where year_status = "not_observed"', 'ref_core_senders_status_rule'),
    ('update ref_core_senders set patients_from_affiliates = 99 where year = 2022', 'ref_core_senders_groups_exceed_total'),
    ('update ref_core_senders set patients_from_universe = 50 where year = 2022', 'ref_core_senders_referring_remainder_vs_yearly_features'),
    ('update ref_reach_group_year set patients_to_core = 15 where year = 2022 and division_group = "referring"', 'ref_core_senders_vs_reach_by_group'),
    ('update ref_center_year set lost_referrers = -1 where year = 2020', 'ref_center_year_negative_values'),
    ('create table ref_link_baseline_2009_2015 as select * from ref_link_year', 'ref_no_baseline_tables'),
    ('delete from ref_link_year where npi = "1000000003"', 'ref_link_year_rows_vs_source'),
    ('update ref_link_year set patients = patients + 1, patients_allocated = patients_allocated + 1', 'ref_link_year_patients_total_vs_source')])
def test_check_catches_a_broken_table(pack, sql, failing):
    pack.execute(sql.replace('"', "'"))
    assert p.check(pack)[failing] > 0


def test_check_catches_duplicate_keys(pack):
    pack.executescript('create table copy as select * from ref_group_year; drop table ref_group_year; '
                       'alter table copy rename to ref_group_year; insert into ref_group_year select * from ref_group_year where year = 2017;')
    assert p.check_structure(pack)['ref_group_year_duplicate_keys'] == 4


def test_center_referrer_base_by_year_keeps_unobserved_years_blank(pack):
    out = rows(pack, 'center_referrer_base_by_year', ccn=ALPHA)
    assert [r['year'] for r in out] == list(YEARS)
    assert out[0] == {'year': 2017, 'hcps': 2, 'shared_patients': 50, 'concentration_index': 0.52, 'top_hcp_share': 0.6,
                      'out_of_region_share': 0.6, 'hcps_gained': None, 'hcps_lost': None, 'hcps_kept': None, 'year_status': 'observed'}
    assert (out[1]['hcps_gained'], out[1]['hcps_lost'], out[1]['hcps_kept']) == (0, 1, 1)
    assert out[2] == {'year': 2019, 'hcps': None, 'shared_patients': None, 'concentration_index': None, 'top_hcp_share': None,
                      'out_of_region_share': None, 'hcps_gained': None, 'hcps_lost': 1, 'hcps_kept': None, 'year_status': 'not observed'}
    assert (out[5]['hcps'], out[5]['shared_patients'], out[5]['out_of_region_share']) == (4, 161, 0.4)
    assert all(r['year_status'] == 'not observed' and r['shared_patients'] is None for r in rows(pack, 'center_referrer_base_by_year', ccn=CHILD))
    assert rows(pack, 'center_referrer_base_by_year', ccn='990001 ')[5]['shared_patients'] == 161


def test_center_referral_summary_is_one_row_for_a_fact_box(pack):
    assert rows(pack, 'center_referral_summary', ccn=ALPHA) == [
        {'year': 2022, 'hcps': 4, 'shared_patients': 161, 'referring_hcps': 1, 'referring_shared_patients': 40,
         'out_of_region_share': 0.4, 'top_hcp_share': 0.373, 'year_status': 'observed'}]
    assert rows(pack, 'center_referral_summary', ccn=ALPHA, year=2020) == [
        {'year': 2020, 'hcps': None, 'shared_patients': None, 'referring_hcps': None, 'referring_shared_patients': None,
         'out_of_region_share': None, 'top_hcp_share': None, 'year_status': 'not observed'}]


def test_center_top_referrers_ranks_by_shared_patients(pack):
    out = rows(pack, 'center_top_referrers', ccn=ALPHA)
    assert [(r['npi'], r['shared_patients']) for r in out] == [(B, 60), (C, 50), (A, 40), (D, 11)]
    assert out[0] == {'name': 'Affiliate Two', 'npi': B, 'division_group': 'affiliates', 'community': 'Alpha orbit', 'city': 'Betatown',
                      'state': 'BB', 'shared_patients': 60, 'mean_day_wait': 5.0, 'region_relation': 'outside'}
    assert (out[3]['community'], out[3]['city'], out[3]['region_relation']) == (None, None, 'unknown')
    assert [r['npi'] for r in rows(pack, 'center_top_referrers', ccn=ALPHA, n=2)] == [B, C]
    assert [r['npi'] for r in rows(pack, 'center_top_referrers', ccn=ALPHA, year=2018)] == [A]
    assert rows(pack, 'center_top_referrers', ccn=ALPHA, year=2019) == []


def test_center_referrers_by_region_counts_referring_division_only(pack):
    assert rows(pack, 'center_referrers_by_region', ccn=ALPHA) == [
        {'region': 'Alphaville, AA region', 'region_relation': 'inside', 'hcps': 1, 'shared_patients': 40}]
    assert rows(pack, 'center_referrers_by_region', ccn=BETA) == [
        {'region': 'Gammatown, CC region', 'region_relation': 'outside', 'hcps': 1, 'shared_patients': 12}]
    assert rows(pack, 'center_referrers_by_region', ccn=BETA, year=2017) == []


def test_center_referrers_by_division_shares_add_to_one(pack):
    out = rows(pack, 'center_referrers_by_division', ccn=ALPHA)
    assert [(r['division_group'], r['hcps'], r['shared_patients'], r['share_of_center']) for r in out] == [
        ('core', 1, 50, 0.311), ('affiliates', 1, 60, 0.373), ('referring', 1, 40, 0.248), ('other', 1, 11, 0.068)]
    assert rows(pack, 'center_referrers_by_division', ccn=ALPHA, year=2020) == []


def test_center_core_hcp_senders_derives_the_referring_part(pack):
    out = rows(pack, 'center_core_hcp_senders', ccn=ALPHA)
    assert out[0] == {'name': 'Core Three', 'npi': C, 'sending_hcps': 3, 'shared_patients': 45, 'from_core': 0, 'from_affiliates': 20,
                      'from_referring': 14, 'from_other': 11, 'out_of_region_share': 0.588, 'year_status': 'observed'}
    assert out[1] == {'name': 'Core Six', 'npi': F, 'sending_hcps': None, 'shared_patients': None, 'from_core': None,
                      'from_affiliates': None, 'from_referring': None, 'from_other': None, 'out_of_region_share': None,
                      'year_status': 'not observed'}
    assert [(r['npi'], r['year_status'], r['shared_patients']) for r in rows(pack, 'center_core_hcp_senders', ccn=ALPHA, year=2018)] == [
        (F, 'not observed', None), (C, 'not observed', None)]  # both blank, so ordered by name
    assert rows(pack, 'center_core_hcp_senders', ccn=BETA) == []


def test_hcp_referral_links_orders_by_year_then_patients(pack):
    out = rows(pack, 'hcp_referral_links', npi=A)
    assert [(r['year'], r['ccn'], r['center'], r['shared_patients']) for r in out] == [
        (2017, ALPHA, 'Alpha Hospital', 20), (2018, ALPHA, 'Alpha Hospital', 25), (2021, LATE, 'Delta Clinic', 15), (2022, ALPHA, 'Alpha Hospital', 40)]
    assert out[3] == {'year': 2022, 'center': 'Alpha Hospital', 'ccn': ALPHA, 'city': 'Alphaville', 'state': 'AA', 'shared_patients': 40,
                      'receivers': 2, 'mean_day_wait': 8.5, 'region_relation': 'inside'}
    assert [(r['year'], r['center']) for r in rows(pack, 'hcp_referral_links', npi=B)] == [
        (2017, 'Alpha Hospital'), (2017, 'Beta Medical Center'), (2022, 'Alpha Hospital')]
    assert rows(pack, 'hcp_referral_links', npi=G) == []


def test_hcp_referral_summary_is_one_row_and_blank_when_never_linked(pack):
    assert rows(pack, 'hcp_referral_summary', npi=A) == [
        {'link_status': 'linked to a CAR-T center', 'first_year_linked': 2017, 'last_year_linked': 2022, 'years_linked': 4, 'centers': 2,
         'primary_center': 'Alpha Hospital', 'ccn': ALPHA, 'primary_center_share': 0.85, 'primary_center_region': 'inside'}]
    never = rows(pack, 'hcp_referral_summary', npi=G)[0]
    assert never['link_status'] == 'no shared-patient pair observed' and set(list(never.values())[1:]) == {None}
    assert rows(pack, 'hcp_referral_summary', npi=F)[0]['link_status'] == 'shares patients, none with a CAR-T center'


def test_hcp_referral_by_year_counts_each_pair_once(pack):
    out = rows(pack, 'hcp_referral_by_year', npi=A)
    assert [(r['year'], r['centers'], r['shared_patients'], r['centers_gained'], r['centers_lost'], r['centers_kept'], r['year_status']) for r in out] == [
        (2017, 1, 20, None, None, None, 'observed'), (2018, 1, 25, 0, 0, 1, 'observed'), (2019, None, None, 0, 1, 0, 'not observed'),
        (2021, 1, 15, 1, 0, 0, 'observed'), (2022, 1, 40, 1, 1, 0, 'observed')]
    first = rows(pack, 'hcp_referral_by_year', npi=B)[0]
    assert (first['centers'], first['shared_patients']) == (2, 30)
    assert rows(pack, 'hcp_referral_by_year', npi=G) == []


def test_hcp_core_links_keeps_a_blank_share(pack):
    assert rows(pack, 'hcp_core_links', npi=B) == [
        {'year': 2017, 'core_hcps': 1, 'shared_patients': 12, 'share_via_core': None},
        {'year': 2022, 'core_hcps': 1, 'shared_patients': 20, 'share_via_core': 0.333}]
    assert rows(pack, 'hcp_core_links', npi=G) == []


def test_community_queries_sum_centers_and_count_each_hcp_once(pack):
    flows = rows(pack, 'community_referral_flows', community_id=1)
    assert [(r['ccn'], r['hcps'], r['shared_patients'], r['out_of_region_share'], r['year_status']) for r in flows] == [
        (ALPHA, 4, 161, 0.4, 'observed'), (BETA, 1, 12, 1.0, 'observed')]
    assert [(r['ccn'], r['shared_patients'], r['year_status']) for r in rows(pack, 'community_referral_flows', community_id=1, year=2018)] == [
        (ALPHA, 25, 'observed'), (BETA, None, 'not observed')]
    assert [(r['name'], r['hcps'], r['year_status']) for r in rows(pack, 'community_referral_flows', community_id=2)] == [
        ('Gamma Childrens Hospital', None, 'not observed')]
    assert rows(pack, 'community_referral_flows', community_id=3) == []
    by_year = {r['year']: r for r in rows(pack, 'community_referral_by_year', community_id=1)}
    assert by_year[2017] == {'year': 2017, 'shared_patients': 80, 'hcps': 2, 'centers_observed': 2, 'centers': 2}
    assert by_year[2018] == {'year': 2018, 'shared_patients': 25, 'hcps': 1, 'centers_observed': 1, 'centers': 2}
    assert by_year[2019] == {'year': 2019, 'shared_patients': None, 'hcps': None, 'centers_observed': 0, 'centers': 2}
    assert by_year[2022] == {'year': 2022, 'shared_patients': 173, 'hcps': 5, 'centers_observed': 2, 'centers': 2}
    assert rows(pack, 'community_referral_by_year', community_id=3) == []


def test_group_queries_follow_the_published_tables(pack):
    reach = rows(pack, 'referral_reach_by_group')
    assert [(r['year'], r['division_group']) for r in reach] == [(y, g) for y in (2017, 2022) for g in GROUPS]
    assert reach[6] == {'year': 2022, 'division_group': 'referring', 'hcps': 1, 'share_of_group': 0.0, 'shared_patients': 14, 'hcps_in_group': 3}
    flows = rows(pack, 'referral_flows_by_group')
    assert len(flows) == 24 and flows[21] == {
        'year': 2022, 'division_group': 'affiliates', 'hcps': 1, 'center_links': 1, 'shared_patients': 60, 'out_of_region_share': None,
        'links_gained': None, 'links_lost': None, 'hcps_in_group': 1}


def test_top_centers_by_shared_patients_filters_by_year_and_state(pack):
    assert [(r['ccn'], r['community'], r['hcps'], r['shared_patients']) for r in rows(pack, 'top_centers_by_shared_patients')] == [
        (ALPHA, 'Alpha orbit', 4, 161), (BETA, 'Alpha orbit', 1, 12)]
    assert [r['ccn'] for r in rows(pack, 'top_centers_by_shared_patients', state='bb')] == [BETA]
    assert [(r['ccn'], r['community']) for r in rows(pack, 'top_centers_by_shared_patients', year=2021)] == [(LATE, None)]
    assert [r['ccn'] for r in rows(pack, 'top_centers_by_shared_patients', n=1)] == [ALPHA]
    assert rows(pack, 'top_centers_by_shared_patients', state='ZZ') == []


@pytest.mark.parametrize('name, params, message', [
    ('center_top_referrers', {'ccn': '123456'}, 'No center with CCN 123456'),
    ('center_referrer_base_by_year', {}, 'needs the parameter ccn'),
    ('center_top_referrers', {'ccn': ALPHA, 'year': 2016}, 'year must be between 2017 and 2022'),
    ('center_top_referrers', {'ccn': ALPHA, 'n': 0}, 'n must be between 1 and 50'),
    ('center_top_referrers', {'ccn': ALPHA, 'top': 5}, 'Unknown parameter top'),
    ('hcp_referral_links', {'npi': '1999999999'}, 'No HCP with NPI 1999999999'),
    ('hcp_referral_summary', {'npi': 'someone'}, 'No HCP with NPI someone'),
    ('community_referral_flows', {'community_id': 99}, 'No community with id 99'),
    ('community_referral_by_year', {'community_id': 'nine'}, 'community_id must be a whole number'),
    ('top_centers_by_shared_patients', {'state': 'Texas'}, 'two-letter code'),
    ('center_referrers_by_region', {'ccn': ALPHA, 'year': 'latest'}, 'year must be a whole number')])
def test_parameter_validation_gives_a_usable_message(pack, name, params, message):
    with pytest.raises(queries.QueryError, match=message):
        queries.run(name, params, con=pack)


def test_skill_gate_and_registration_rules(pack):
    mine = {n: s for n, s in queries.REGISTRY.items() if s['function'].__module__ == queries_referral.__name__}
    assert len(mine) == 15
    assert queries.run('hcp_referral_links', {'npi': A}, con=pack, skill='hcp-profile')['row_count'] == 4
    assert queries.run('community_referral_flows', {'community_id': 1}, con=pack, skill='community-view')['row_count'] == 2
    with pytest.raises(queries.QueryError, match='not part of the skill'):
        queries.run('center_top_referrers', {'ccn': ALPHA}, con=pack, skill='hcp-profile')
    for name, spec in mine.items():
        assert {'REFERRAL', 'MEDICARE', 'LABELS'} <= set(spec['caveats']) and 'referral-flow' in spec['skills'], name
        assert spec['default_panel']['type'] in ('kpis', 'table', 'bar', 'line') and spec['years'] == '2017 to 2022', name
        assert (spec['default_panel']['type'] == 'kpis') == name.endswith('_summary'), name
        assert 'counted more than once' in spec['description'] or name == 'hcp_referral_summary', name
        names = {c['name'] for c in spec['columns']}
        assert set(spec['default_panel'].get('y', [])) | ({spec['default_panel']['x']} if 'x' in spec['default_panel'] else set()) <= names, name
        for column in spec['columns']:
            label = column['label'].lower()
            assert 'referral' not in label and 'referrer' not in label and 'car-t patient' not in label, (name, label)
            assert column['name'] != 'shared_patients' or label.startswith('shared-patient count'), (name, label)


def test_card_facts_are_short_and_name_no_person(pack):
    facts = p.card_facts(pack)
    assert all(1 <= len(v) <= 3 and 'shared Medicare patients for any condition' in v[0] for v in facts.values())
    assert not any(h[1] in sentence for v in facts.values() for sentence in v for h in HCPS)
    assert {k for k in facts if k[0] == 'hcp'} == {('hcp', npi) for npi in (A, B, C, D, E)}
    assert facts[('center', ALPHA)] == [
        'In 2022, 4 HCPs had shared Medicare patients for any condition with this center, a shared-patient count of 161 '
        '(summed over HCP pairs, so a patient can be counted more than once).',
        "40% of the 2022 shared-patient count came from HCPs outside the center's hospital referral region.",
        'Between 2017 and 2022 the shared-patient count went from 50 to 161 and the HCPs sharing patients from 2 to 4.']
    assert len(facts[('center', CHILD)]) == 1 and 'not observed, which is not zero' in facts[('center', CHILD)][0]
    assert facts[('center', LATE)][0].startswith('In 2021, the latest year observed for this center, 1 HCP had shared')
    assert facts[('hcp', A)] == [
        'This HCP had shared Medicare patients for any condition with at least one CAR-T center in 4 of the 6 years 2017 to 2022 '
        '(first 2017, last 2022).',
        "Over those years the HCP is linked to 2 centers; the primary one is Alpha Hospital, with 85% of the HCP's shared-patient "
        "count, inside the HCP's own hospital referral region."]
    assert facts[('hcp', E)][1] == "Over those years the HCP is linked to 1 center, Beta Medical Center, outside the HCP's own hospital referral region."
    assert facts[('community', '1')] == [
        'In 2022, 5 HCPs had shared Medicare patients for any condition with 2 of the 2 CAR-T centers attached to this community.',
        'Summed over 2 centers, the 2022 shared-patient count is 173 (a sum over HCP pairs, so a patient can be counted more than '
        'once); in 2017 it was 80 across 2 centers.',
        'The center with the largest 2022 shared-patient count is Alpha Hospital (161).']
    assert facts[('community', '2')][0].startswith('No HCP had shared Medicare patients for any condition with the 1 CAR-T center')
    assert ('community', '3') not in facts


@pytest.mark.skipif(not (PACK / 'dev_referral.sqlite').exists(), reason='the local dev pack has not been built')
def test_every_query_runs_on_the_real_dev_pack():
    con = packbase.connect(PACK / 'dev_referral.sqlite', readonly=True)
    try:
        assert {k: v for k, v in p.check(con).items() if v} == {}
        ccn, community = con.execute("select y.ccn, c.community_id from ref_center_year y join dim_center c on c.ccn = y.ccn "
                                     "where y.year = 2022 and c.community_id is not null order by y.patients desc").fetchone()
        npi = con.execute('select npi from ref_core_links where year = 2022 order by core_hcps desc, npi').fetchone()[0]
        given = {'ccn': ccn, 'npi': npi, 'community_id': community}
        for name, spec in queries.REGISTRY.items():
            if spec['function'].__module__ != queries_referral.__name__:
                continue
            result = queries.run(name, {k: given[k] for k in spec['params'] if k in given}, con=con, skill='referral-flow')
            assert 0 < result['row_count'] <= queries.MAX_ROWS, name
            assert all(r.get('shared_patients') is None or r['shared_patients'] >= p.PAIR_FLOOR for r in result['rows']), name
        assert len(queries.run('center_referrer_base_by_year', {'ccn': ccn}, con=con)['rows']) == 6
    finally:
        con.close()
