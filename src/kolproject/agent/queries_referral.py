"""Named queries over the referral tables: shared Medicare patients between HCPs and CAR-T centers, 2017 to 2022.

The data is CareSet Systems' DocGraph (CC BY-NC-SA 4.0 with a non-commercial addendum): Medicare fee-for-service
patients shared for any condition. It is not CAR-T specific and not confirmed referrals, so every query carries the
REFERRAL caveat and every label says "shared patients". A shared-patient count is a sum over HCP pairs with at least
11 shared patients, so a patient can be counted more than once. A year with no link is not observed, never zero.
"""
from __future__ import annotations

from .queries import QueryError, query

SKILL = 'referral-flow'
CAVEATS = ('REFERRAL', 'MEDICARE', 'LABELS')
YEARS = '2017 to 2022'
NOTE = ('Shared-patient counts are sums over HCP pairs with at least 11 shared Medicare patients (any condition), so a '
        'patient can be counted more than once; a year with no link is not observed, never zero.')
YEAR = {'type': 'integer', 'minimum': 2017, 'maximum': 2022, 'default': 2022}
TOP = {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 15}
STATE = {'type': 'string', 'default': ''}
OBSERVED = "case when {0} in ('observed', 'linked') then 'observed' else 'not observed' end"
GROUP_ORDER = "case {0} when 'core' then 1 when 'affiliates' then 2 when 'referring' then 3 else 4 end"

# Column declarations shared by several queries: (name, label, unit).
YEAR_COL = ('year', 'Year', 'year')
STATUS_COL = ('year_status', 'Observed in the year (not observed is not zero)', 'text')
PATIENTS_COL = ('shared_patients', 'Shared-patient count', 'count')
CENTER_HCPS_COL = ('hcps', 'HCPs sharing patients with the center', 'count')
OUTSIDE_COL = ('out_of_region_share', "Share of the count from HCPs outside the center's region", 'share')
RELATION_COL = ('region_relation', "Center inside or outside the HCP's own region", 'text')
WAIT_COL = ('mean_day_wait', 'Mean days between the two visits', 'days')
GROUP_COL = ('division_group', 'Division group of the HCPs', 'text')
PLACE_COLS = [('city', 'City', 'text'), ('state', 'State', 'text')]
CENTER_LIST = [('name', 'Center', 'text'), ('ccn', 'CCN', 'id'), *PLACE_COLS]
BY_YEAR = {'type': 'line', 'x': 'year', 'y': ['shared_patients'], 'markers': 'lymphoma_products'}


def fetch(con, sql, params=()):
    """Rows as dicts, whatever row factory the connection uses."""
    cursor = con.execute(sql, params)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def center_id(con, ccn):
    ccn = str(ccn).strip().zfill(6)
    if not con.execute('select 1 from dim_center where ccn = ?', (ccn,)).fetchone():
        raise QueryError(f'No center with CCN {ccn}. Call find_entity first.')
    return ccn


def hcp_id(con, npi):
    npi = str(npi).strip()
    if not con.execute('select 1 from dim_hcp where npi = ?', (npi,)).fetchone():
        raise QueryError(f'No HCP with NPI {npi}. Call find_entity first.')
    return npi


def community(con, community_id):
    if not con.execute('select 1 from dim_community where community_id = ?', (community_id,)).fetchone():
        raise QueryError(f'No community with id {community_id}. Call find_entity first.')
    return community_id


@query('center_referrer_base_by_year',
       'Per year 2017 to 2022 for one center: HCPs (any division) sharing patients with it, the shared-patient count, how '
       'concentrated the count is among those HCPs, the largest HCP share, the share from outside the center\'s region, '
       f'and HCPs gained, lost and kept against the previous year. {NOTE}',
       {'ccn': 'ccn'},
       [YEAR_COL, CENTER_HCPS_COL, PATIENTS_COL,
        ('concentration_index', 'Concentration of the count among HCPs (0 spread out, 1 a single HCP)', 'index'),
        ('top_hcp_share', 'Share of the count from the largest HCP', 'share'), OUTSIDE_COL,
        ('hcps_gained', 'HCPs new against the previous year', 'count'),
        ('hcps_lost', 'HCPs of the previous year no longer sharing patients', 'count'),
        ('hcps_kept', 'HCPs present in both years', 'count'), STATUS_COL],
       [SKILL], default_panel=BY_YEAR, caveats=CAVEATS, tables=['ref_center_year'], years=YEARS)
def center_referrer_base_by_year(con, ccn):
    return fetch(con, f"""
        select year, referrers as hcps, patients as shared_patients, concentration_index, top_referrer_share as top_hcp_share,
               out_of_region_share, new_referrers as hcps_gained, lost_referrers as hcps_lost, retained_referrers as hcps_kept,
               {OBSERVED.format('year_status')} as year_status
        from ref_center_year where ccn = ? order by year""", (center_id(con, ccn),))


@query('center_referral_summary',
       'One row for one center and year, for a fact box: HCPs (any division) sharing patients with it, the shared-patient '
       'count, the part of both from referring-division HCPs, the share from outside the center\'s region and the largest '
       f'HCP share. Blank with status not observed when the center has no link that year. {NOTE}',
       {'ccn': 'ccn', 'year': YEAR},
       [YEAR_COL, CENTER_HCPS_COL, PATIENTS_COL, ('referring_hcps', 'Of those HCPs, referring-division HCPs', 'count'),
        ('referring_shared_patients', 'Shared-patient count from referring-division HCPs', 'count'), OUTSIDE_COL,
        ('top_hcp_share', 'Share of the count from the largest HCP', 'share'), STATUS_COL],
       [SKILL], default_panel={'type': 'kpis'}, caveats=CAVEATS, tables=['ref_center_year'], years=YEARS)
def center_referral_summary(con, ccn, year):
    return fetch(con, f"""
        select year, referrers as hcps, patients as shared_patients, referrers_referring as referring_hcps,
               patients_referring as referring_shared_patients, out_of_region_share, top_referrer_share as top_hcp_share,
               {OBSERVED.format('year_status')} as year_status
        from ref_center_year where ccn = ? and year = ?""", (center_id(con, ccn), year))


@query('center_top_referrers',
       'HCPs ranked by their shared-patient count with one center in one year, with division group, community, place, '
       f'mean days between the two visits and whether the center is inside or outside the HCP\'s own region. {NOTE}',
       {'ccn': 'ccn', 'year': YEAR, 'n': TOP},
       [('name', 'HCP', 'text'), ('npi', 'NPI', 'id'), ('division_group', 'Division group', 'text'),
        ('community', 'Community', 'text'), ('city', 'City', 'text'), ('state', 'State', 'text'), PATIENTS_COL, WAIT_COL,
        RELATION_COL],
       [SKILL], default_panel={'type': 'table'}, caveats=CAVEATS,
       tables=['ref_link_year', 'dim_hcp', 'dim_community'], years=YEARS)
def center_top_referrers(con, ccn, year, n):
    return fetch(con, """
        select h.name, l.npi, h.division_group, m.short_label as community, h.city, h.state, l.patients as shared_patients,
               l.mean_day_wait, l.region_relation
        from ref_link_year l join dim_hcp h on h.npi = l.npi left join dim_community m on m.community_id = h.community_id
        where l.ccn = ? and l.year = ? order by l.patients desc, h.name, l.npi limit ?""", (center_id(con, ccn), year, n))


@query('center_referrers_by_region',
       'Hospital referral regions ranked by the shared-patient count between their referring-division HCPs and one center '
       'in one year, with the region name and whether it is the center\'s own region. This table counts referring-division '
       f'HCPs only, so it is smaller than the center total. {NOTE}',
       {'ccn': 'ccn', 'year': YEAR, 'n': TOP},
       [('region', 'Region of the referring-division HCPs', 'text'),
        ('region_relation', "The center's own region (inside) or another (outside)", 'text'),
        ('hcps', 'Referring-division HCPs sharing patients with the center', 'count'), PATIENTS_COL],
       [SKILL], default_panel={'type': 'bar', 'x': 'region', 'y': ['shared_patients']}, caveats=CAVEATS,
       tables=['ref_region_center_year', 'ref_link_year', 'dim_hcp'], years=YEARS)
def center_referrers_by_region(con, ccn, year, n):
    return fetch(con, """
        select r.region_city || ', ' || r.region_state || ' region' as region,
               case r.same_region when 1 then 'inside' else 'outside' end as region_relation,
               (select count(*) from ref_link_year l join dim_hcp h on h.npi = l.npi
                where l.ccn = r.ccn and l.year = r.year and h.division = 'referring_proxy' and h.hrr = r.hrr) as hcps,
               r.patients as shared_patients
        from ref_region_center_year r where r.ccn = ? and r.year = ?
        order by r.patients desc, region limit ?""", (center_id(con, ccn), year, n))


@query('center_referrers_by_division',
       'For one center and year: HCPs sharing patients with it and the shared-patient count by division group of the HCP '
       f'(core, affiliates, referring, other), with each group\'s share of the center\'s count. {NOTE}',
       {'ccn': 'ccn', 'year': YEAR},
       [GROUP_COL, CENTER_HCPS_COL, PATIENTS_COL, ('share_of_center', "Share of the center's count", 'share')],
       [SKILL], default_panel={'type': 'bar', 'x': 'division_group', 'y': ['shared_patients']}, caveats=CAVEATS,
       tables=['ref_link_year', 'dim_hcp'], years=YEARS)
def center_referrers_by_division(con, ccn, year):
    return fetch(con, f"""
        select h.division_group, count(*) as hcps, sum(l.patients) as shared_patients,
               round(sum(l.patients) * 1.0 / (select sum(patients) from ref_link_year where ccn = :ccn and year = :year), 3) as share_of_center
        from ref_link_year l join dim_hcp h on h.npi = l.npi where l.ccn = :ccn and l.year = :year
        group by h.division_group order by {GROUP_ORDER.format('h.division_group')}""", {'ccn': center_id(con, ccn), 'year': year})


@query('center_core_hcp_senders',
       'For each core HCP linked to one center, in one year: HCPs sending them shared patients and the shared-patient count '
       'received, in total (all HCPs of the universe) and from core, affiliate, referring and other senders. The source '
       'publishes the total and the core, affiliate and other parts; its referring part sits in yearly features that are not '
       'loaded, so the referring column is the total minus the other three. Counts follow the core HCP wherever they '
       f'practise, not only at this center. {NOTE}',
       {'ccn': 'ccn', 'year': YEAR},
       [('name', 'Core HCP', 'text'), ('npi', 'NPI', 'id'), ('sending_hcps', 'HCPs sending shared patients', 'count'),
        ('shared_patients', 'Shared-patient count received, all senders', 'count'),
        ('from_core', 'Count from core HCPs', 'count'), ('from_affiliates', 'Count from affiliate HCPs', 'count'),
        ('from_referring', 'Count from referring HCPs (total minus the other three)', 'count'),
        ('from_other', 'Count from other hematology-oncology HCPs', 'count'),
        ('out_of_region_share', "Share of the count from senders outside the core HCP's region", 'share'), STATUS_COL],
       [SKILL], default_panel={'type': 'table'}, caveats=CAVEATS,
       tables=['ref_core_senders', 'hcp_center', 'dim_hcp'], years=YEARS)
def center_core_hcp_senders(con, ccn, year):
    return fetch(con, f"""
        select h.name, h.npi, s.senders as sending_hcps, s.patients_from_universe as shared_patients,
               s.patients_from_core as from_core, s.patients_from_affiliates as from_affiliates,
               s.patients_from_universe - s.patients_from_core - s.patients_from_affiliates - s.patients_from_other as from_referring,
               s.patients_from_other as from_other, s.out_of_region_share, {OBSERVED.format('s.year_status')} as year_status
        from hcp_center c join dim_hcp h on h.npi = c.npi and h.is_core = 1
        left join ref_core_senders s on s.npi = h.npi and s.year = :year
        where c.ccn = :ccn order by s.patients_from_universe is null, s.patients_from_universe desc, h.name, h.npi""",
                 {'ccn': center_id(con, ccn), 'year': year})


@query('hcp_referral_links',
       'For one HCP, per center and year: the shared-patient count, the receivers at the center behind it (clinicians or '
       'the hospital itself), mean days between the two visits and whether the center is inside or outside the HCP\'s own '
       f'region. A center and year without a row is not observed. {NOTE}',
       {'npi': 'npi'},
       [YEAR_COL, ('center', 'Center', 'text'), ('ccn', 'CCN', 'id'), *PLACE_COLS, PATIENTS_COL,
        ('receivers', 'Receivers at the center (clinicians or the hospital)', 'count'),
        WAIT_COL, RELATION_COL],
       [SKILL, 'hcp-profile'], default_panel={'type': 'table'}, caveats=CAVEATS,
       tables=['ref_link_year', 'dim_center'], years=YEARS)
def hcp_referral_links(con, npi):
    return fetch(con, """
        select l.year, c.name as center, l.ccn, c.city, c.state, l.patients as shared_patients, l.receiving_npis as receivers,
               l.mean_day_wait, l.region_relation
        from ref_link_year l join dim_center c on c.ccn = l.ccn where l.npi = ?
        order by l.year, l.patients desc, c.name, l.ccn""", (hcp_id(con, npi),))


@query('hcp_referral_summary',
       'One row for one HCP: whether it shares Medicare patients (any condition) with a CAR-T center in 2017 to 2022, the '
       'first and last year and number of years linked, the number of centers, and the primary center with its share of '
       'the HCP\'s shared-patient count and whether it lies inside or outside the HCP\'s own region. Blank when never linked.',
       {'npi': 'npi'},
       [('link_status', 'Shared-patient link to a CAR-T center', 'text'), ('first_year_linked', 'First year linked', 'year'),
        ('last_year_linked', 'Last year linked', 'year'), ('years_linked', 'Years linked, of 6', 'count'),
        ('centers', 'Centers linked in any year', 'count'), ('primary_center', 'Primary center', 'text'),
        ('ccn', 'CCN of the primary center', 'id'),
        ('primary_center_share', "Primary center's share of the HCP's shared-patient count", 'share'),
        ('primary_center_region', "Primary center inside or outside the HCP's own region", 'text')],
       [SKILL], default_panel={'type': 'kpis'}, caveats=CAVEATS, tables=['ref_hcp', 'dim_center'], years=YEARS)
def hcp_referral_summary(con, npi):
    return fetch(con, """
        select case r.link_status when 'linked' then 'linked to a CAR-T center'
                                  when 'no_center_link' then 'shares patients, none with a CAR-T center'
                                  else 'no shared-patient pair observed' end as link_status,
               r.first_year_linked, r.last_year_linked, r.years_linked, r.distinct_centers as centers, c.name as primary_center,
               r.primary_center_ccn as ccn, r.primary_center_share, r.primary_center_region
        from ref_hcp r left join dim_center c on c.ccn = r.primary_center_ccn where r.npi = ?""", (hcp_id(con, npi),))


@query('hcp_referral_by_year',
       'For one HCP, per year: centers it shares patients with, the shared-patient count across them (each HCP pair counted '
       'once, split across the receiver\'s centers), and centers gained, lost and kept against the previous year. A year '
       f'without a row is not observed with no change. {NOTE}',
       {'npi': 'npi'},
       [YEAR_COL, ('centers', 'Centers sharing patients with the HCP', 'count'), PATIENTS_COL,
        ('centers_gained', 'Centers new against the previous year', 'count'),
        ('centers_lost', 'Centers of the previous year no longer linked', 'count'),
        ('centers_kept', 'Centers linked in both years', 'count'), STATUS_COL],
       [SKILL], default_panel={'type': 'bar', 'x': 'year', 'y': ['shared_patients']}, caveats=CAVEATS,
       tables=['ref_hcp_change_year', 'ref_link_year'], years=YEARS)
def hcp_referral_by_year(con, npi):
    return fetch(con, f"""
        select c.year, l.centers, l.shared_patients, c.links_gained as centers_gained, c.links_lost as centers_lost,
               c.links_retained as centers_kept, {OBSERVED.format('c.year_status')} as year_status
        from ref_hcp_change_year c
        left join (select year, count(*) as centers, cast(round(sum(patients_allocated)) as integer) as shared_patients
                   from ref_link_year where npi = :npi group by year) l on l.year = c.year
        where c.npi = :npi order by c.year""", {'npi': hcp_id(con, npi)})


@query('hcp_core_links',
       'For one HCP, per year: core HCPs it shares patients with as the earlier provider, the shared-patient count with '
       'them, and the share of the HCP\'s center-bound count that goes through a core HCP rather than another clinician or '
       f'the hospital itself. A year without a row is not observed. {NOTE}',
       {'npi': 'npi'},
       [YEAR_COL, ('core_hcps', 'Core HCPs sharing patients with the HCP', 'count'), PATIENTS_COL,
        ('share_via_core', 'Share of the center-bound count going through a core HCP', 'share')],
       [SKILL], default_panel={'type': 'bar', 'x': 'year', 'y': ['shared_patients']}, caveats=CAVEATS,
       tables=['ref_core_links'], years=YEARS)
def hcp_core_links(con, npi):
    return fetch(con, 'select year, core_hcps, patients_to_core as shared_patients, share_via_core from ref_core_links '
                      'where npi = ? order by year', (hcp_id(con, npi),))


@query('community_referral_flows',
       'Centers attached to one community, ranked by shared-patient count in one year, with the HCPs (any division) sharing '
       f'patients with each and the share from outside the center\'s region. {NOTE}',
       {'community_id': 'community_id', 'year': YEAR, 'n': TOP},
       [*CENTER_LIST, CENTER_HCPS_COL, PATIENTS_COL, OUTSIDE_COL, STATUS_COL],
       [SKILL, 'community-view'], default_panel={'type': 'bar', 'x': 'name', 'y': ['shared_patients']}, caveats=CAVEATS,
       tables=['ref_center_year', 'dim_center'], years=YEARS)
def community_referral_flows(con, community_id, year, n):
    return fetch(con, f"""
        select c.name, c.ccn, c.city, c.state, y.referrers as hcps, y.patients as shared_patients,
               y.out_of_region_share, {OBSERVED.format('y.year_status')} as year_status
        from dim_center c left join ref_center_year y on y.ccn = c.ccn and y.year = :year
        where c.community_id = :id order by y.patients is null, y.patients desc, c.name, c.ccn limit :n""",
                 {'id': community(con, community_id), 'year': year, 'n': n})


@query('community_referral_by_year',
       'Per year 2017 to 2022 for one community: the shared-patient counts of its centers added together, the HCPs sharing '
       'patients with at least one of those centers (each HCP once), and how many of the community\'s centers are behind '
       f'the sum. An HCP pair whose receiver works at several of the centers counts at each. {NOTE}',
       {'community_id': 'community_id'},
       [YEAR_COL, ('shared_patients', "Shared-patient count, summed over the community's centers", 'count'),
        ('hcps', 'HCPs sharing patients with at least one of the centers', 'count'),
        ('centers_observed', 'Centers behind the sum', 'count'), ('centers', 'Centers attached to the community', 'count')],
       [SKILL], default_panel=BY_YEAR, caveats=CAVEATS, tables=['ref_center_year', 'ref_link_year', 'dim_center'], years=YEARS)
def community_referral_by_year(con, community_id):
    return fetch(con, """
        select y.year, sum(y.patients) as shared_patients,
               nullif((select count(distinct l.npi) from ref_link_year l join dim_center d on d.ccn = l.ccn
                       where d.community_id = :id and l.year = y.year), 0) as hcps,
               sum(y.year_status = 'observed') as centers_observed, count(*) as centers
        from dim_center c join ref_center_year y on y.ccn = c.ccn where c.community_id = :id
        group by y.year order by y.year""", {'id': community(con, community_id)})


@query('referral_reach_by_group',
       'Per year 2017 to 2022 and division group: HCPs of the group sharing patients with at least one core HCP, their '
       f'share of the group, and the shared-patient count with core HCPs. {NOTE}',
       {},
       [YEAR_COL, GROUP_COL, ('hcps', 'HCPs sharing patients with at least one core HCP', 'count'),
        ('share_of_group', 'Share of the HCPs in the group', 'share'),
        ('shared_patients', 'Shared-patient count with core HCPs', 'count'), ('hcps_in_group', 'HCPs in the group', 'count')],
       [SKILL], default_panel={'type': 'line', 'x': 'year', 'y': ['hcps'], 'split_by': 'division_group'}, caveats=CAVEATS,
       tables=['ref_reach_group_year'], years=YEARS)
def referral_reach_by_group(con):
    return fetch(con, f"""
        select year, division_group, senders_to_core as hcps, sender_share_of_group as share_of_group,
               patients_to_core as shared_patients, npis_in_group as hcps_in_group
        from ref_reach_group_year order by year, {GROUP_ORDER.format('division_group')}""")


@query('referral_flows_by_group',
       'Per year 2017 to 2022 and division group: HCPs of the group sharing patients with at least one CAR-T center, their '
       'HCP-to-center links, the shared-patient count (each HCP pair counted once), the share going to centers outside the '
       f'HCP\'s own region, and links gained and lost against the previous year. {NOTE}',
       {},
       [YEAR_COL, GROUP_COL, ('hcps', 'HCPs sharing patients with at least one center', 'count'),
        ('center_links', 'HCP-to-center links', 'count'), PATIENTS_COL,
        ('out_of_region_share', "Share of the count going to centers outside the HCP's region", 'share'),
        ('links_gained', 'Links new against the previous year', 'count'),
        ('links_lost', 'Links of the previous year no longer present', 'count'), ('hcps_in_group', 'HCPs in the group', 'count')],
       [SKILL], default_panel={'type': 'line', 'x': 'year', 'y': ['shared_patients'], 'split_by': 'division_group'},
       caveats=CAVEATS, tables=['ref_group_year'], years=YEARS)
def referral_flows_by_group(con):
    return fetch(con, f"""
        select year, division_group, npis_linked as hcps, center_links, patients_allocated as shared_patients,
               out_of_region_share, links_gained, links_lost, npis_in_group as hcps_in_group
        from ref_group_year order by year, {GROUP_ORDER.format('division_group')}""")


@query('top_centers_by_shared_patients',
       'Centers ranked by shared-patient count in one year, nationally or in one state (two-letter code), with the HCPs '
       f'(any division) sharing patients with each, the share from outside the center\'s region and the community. {NOTE}',
       {'year': YEAR, 'state': STATE, 'n': TOP},
       [*CENTER_LIST, ('community', 'Community', 'text'), CENTER_HCPS_COL, PATIENTS_COL, OUTSIDE_COL],
       [SKILL], default_panel={'type': 'bar', 'x': 'name', 'y': ['shared_patients']}, caveats=CAVEATS,
       tables=['ref_center_year', 'dim_center', 'dim_community'], years=YEARS)
def top_centers_by_shared_patients(con, year, state, n):
    state = state.upper()
    if state and not (len(state) == 2 and state.isalpha()):
        raise QueryError('state must be a two-letter code such as TX, or left out for the whole country.')
    return fetch(con, """
        select c.name, c.ccn, c.city, c.state, m.short_label as community, y.referrers as hcps,
               y.patients as shared_patients, y.out_of_region_share
        from ref_center_year y join dim_center c on c.ccn = y.ccn left join dim_community m on m.community_id = c.community_id
        where y.year = :year and y.year_status = 'observed' and (:state = '' or c.state = :state)
        order by y.patients desc, c.name, c.ccn limit :n""", {'year': year, 'state': state, 'n': n})
