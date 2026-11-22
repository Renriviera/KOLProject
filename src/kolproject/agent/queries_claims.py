"""Named queries over the claims tables: Medicare Part B and Part D, Medicaid, and Medicare CAR-T hospital stays.

Medicare hides any cell resting on 10 or fewer patients, and such cells are NULL in the pack, so every sum over
HCPs or centers is a floor. Each aggregate therefore carries the number of HCPs or centers with a published
value behind it, and no figure is obtained by subtracting published totals from one another.
"""
from __future__ import annotations

from .pack_claims import ADMINISTRATION, DRUGS, FAMILIES, HOSPITAL_TYPE
from .packbase import scope_filter
from .queries import QueryError, query

SCOPE = {'scope': 'scope', 'id': 'id'}
CLAIM_YEAR = {'type': 'integer', 'minimum': 2017, 'maximum': 2024, 'default': 2024}
STAY_YEAR = {'type': 'integer', 'minimum': 2021, 'maximum': 2024, 'default': 2024}
TOP_N = {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 15}
DRUG = {'type': 'string', 'enum': DRUGS + ['all'], 'default': 'all'}
FAMILY = {'type': 'string', 'enum': FAMILIES + ['all'], 'default': 'all'}
PROGRAM = {'type': 'string', 'enum': ['medicare_part_b', 'medicaid'], 'default': 'medicare_part_b'}
STATE = {'type': 'string', 'default': '', 'description': 'Two-letter state code such as TX; leave out for all states.'}
MEDICARE = ['MEDICARE', 'NOT_PRODUCT_SPECIFIC']
BY_YEAR = {'type': 'line', 'x': 'year', 'markers': 'lymphoma_products'}


def fetch(con, sql, args=()):
    cursor = con.execute(sql, args)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


@query('partd_by_drug_year',
       'Medicare Part D prescribing of seven oral lymphoma drugs by the HCPs of a scope, per drug and year: HCPs with '
       '11 or more claims, their claims and the gross drug cost (the cost of the prescriptions, not money paid to the '
       'prescriber).',
       SCOPE,
       [('year', 'Year', 'year'), ('drug', 'Drug', 'text'), ('drug_class', 'Drug class', 'text'),
        ('prescribers', 'HCPs with 11 or more claims', 'count'), ('claims', 'Part D claims', 'count'),
        ('drug_cost_usd', 'Gross drug cost', 'usd')],
       skills=['prescribing-claims', 'hcp-profile'], default_panel={**BY_YEAR, 'y': ['claims'], 'split_by': 'drug'},
       caveats=MEDICARE + ['LABELS'], tables=['mcr_partd_year', 'dim_hcp'], years='2017 to 2024')
def partd_by_drug_year(con, scope, id):
    return fetch(con, f'''
        select p.year, p.drug, p.drug_class, count(distinct p.npi) prescribers, sum(p.claims) claims,
               round(sum(p.drug_cost_usd), 2) drug_cost_usd
        from mcr_partd_year p join dim_hcp h on h.npi = p.npi
        where {scope_filter(scope)} group by p.year, p.drug order by p.year, p.drug''', (id,))


@query('top_prescribers',
       'HCPs of a community, center or division group ranked by Medicare Part D claims for one oral lymphoma drug, or '
       'for all seven together, in one year. Only HCPs with 11 or more claims for a drug appear.',
       {**SCOPE, 'drug': DRUG, 'year': CLAIM_YEAR, 'n': TOP_N},
       [('name', 'HCP', 'text'), ('npi', 'NPI', 'id'), ('division_group', 'Division group', 'text'),
        ('community', 'Community', 'text'), ('city', 'City', 'text'), ('state', 'State', 'text'),
        ('claims', 'Part D claims', 'count'), ('drug_cost_usd', 'Gross drug cost', 'usd')],
       skills=['prescribing-claims'], default_panel={'type': 'bar', 'x': 'name', 'y': ['claims']},
       caveats=MEDICARE + ['LABELS'], tables=['mcr_partd_year', 'dim_hcp', 'dim_community'], years='2017 to 2024')
def top_prescribers(con, scope, id, drug, year, n):
    if scope == 'hcp':
        raise QueryError('top_prescribers ranks the HCPs of a community, center or division group. '
                         'For one HCP use partd_by_drug_year.')
    return fetch(con, f'''
        select h.name, h.npi, h.division_group, c.short_label community, h.city, h.state, sum(p.claims) claims,
               round(sum(p.drug_cost_usd), 2) drug_cost_usd
        from mcr_partd_year p join dim_hcp h on h.npi = p.npi left join dim_community c on c.community_id = h.community_id
        where {scope_filter(scope)} and p.year = ? and (? = 'all' or p.drug = ?)
        group by h.npi order by claims desc, drug_cost_usd desc, h.npi limit ?''', (id, year, drug, drug, n))


@query('partb_markers_by_year',
       "Medicare Part B billing under the HCP's own NPI for chemotherapy administration and infused lymphoma drugs, per "
       'family and year: HCPs billing, administration services, a floor on patients, and the estimated Medicare '
       'payment with the number of HCPs it rests on (none for 2024 administration). A family or year without a row '
       'has no HCP with 11 or more patients; drug quantities are not shown because billing units changed over time.',
       {**SCOPE, 'family': FAMILY},
       [('year', 'Year', 'year'), ('family', 'Service or drug', 'text'), ('hcps_billing', 'HCPs billing', 'count'),
        ('services', 'Administration services', 'count'),
        ('beneficiaries', 'Medicare patients, at least (counted once per HCP)', 'count'),
        ('hcps_with_payment', 'HCPs with a payment estimate', 'count'),
        ('estimated_payment_usd', 'Estimated Medicare payment', 'usd')],
       skills=['prescribing-claims'], default_panel={**BY_YEAR, 'y': ['hcps_billing'], 'split_by': 'family'},
       caveats=MEDICARE + ['LABELS'], tables=['mcr_marker_year', 'dim_hcp'], years='2017 to 2024')
def partb_markers_by_year(con, scope, id, family):
    return fetch(con, f'''
        with per_hcp as (
            select m.year, m.family, m.npi, sum(m.services) services, max(m.beneficiaries) beneficiaries,
                   sum(m.estimated_payment_usd) paid
            from mcr_marker_year m join dim_hcp h on h.npi = m.npi
            where {scope_filter(scope)} and (? = 'all' or m.family = ?) group by m.year, m.family, m.npi)
        select year, family, count(*) hcps_billing, case when family = ? then round(sum(services)) end services,
               sum(beneficiaries) beneficiaries, count(paid) hcps_with_payment, round(sum(paid), 2) estimated_payment_usd
        from per_hcp group by year, family order by year, family''', (id, family, family, ADMINISTRATION))


@query('partb_payment_by_year',
       'Medicare Part B payment for services rendered by the HCPs of a scope, per year. The total covers every HCP '
       'with a Medicare row; drug and non-drug payment cover only HCPs whose split is published, so they do not add '
       'up to the total. Drug share is drug payment divided by drug plus non-drug payment.',
       SCOPE,
       [('year', 'Year', 'year'), ('hcps_with_row', 'HCPs with a Medicare row', 'count'),
        ('total_payment_usd', 'Medicare Part B payment, all services', 'usd'),
        ('hcps_with_split', 'HCPs with a published drug and non-drug split', 'count'),
        ('drug_payment_usd', 'Drug payment', 'usd'), ('non_drug_payment_usd', 'Non-drug payment', 'usd'),
        ('hcps_split_hidden', 'HCPs whose split is hidden', 'count'),
        ('drug_share', 'Drug share of drug plus non-drug payment', 'share')],
       skills=['prescribing-claims'], default_panel={**BY_YEAR, 'y': ['drug_payment_usd', 'non_drug_payment_usd']},
       caveats=MEDICARE + ['LABELS'], tables=['mcr_partb_year', 'dim_hcp'], years='2017 to 2024')
def partb_payment_by_year(con, scope, id):
    return fetch(con, f'''
        select b.year, count(b.total_payment_usd) hcps_with_row, round(sum(b.total_payment_usd), 2) total_payment_usd,
               count(b.drug_payment_usd) hcps_with_split, round(sum(b.drug_payment_usd), 2) drug_payment_usd,
               round(sum(b.non_drug_payment_usd), 2) non_drug_payment_usd,
               count(case when b.status = 'split_suppressed' then 1 end) hcps_split_hidden,
               case when sum(b.drug_payment_usd) + sum(b.non_drug_payment_usd) > 0 then
                   round(sum(b.drug_payment_usd) / (sum(b.drug_payment_usd) + sum(b.non_drug_payment_usd)), 4) end drug_share
        from mcr_partb_year b join dim_hcp h on h.npi = b.npi
        where {scope_filter(scope)} group by b.year order by b.year''', (id,))


@query('medicaid_by_year',
       'Medicaid paid amounts on released claim lines naming the HCPs of a scope as servicing provider, per year: all '
       'codes, visits and drug administration, and drug codes, each with the number of HCPs behind it. Lower bounds '
       'that depend on the state; not a ranking and not a trend, and 2024 is incomplete.',
       SCOPE,
       [('year', 'Year', 'year'), ('hcps_with_row', 'HCPs with a released row', 'count'),
        ('paid_usd', 'Medicaid paid, all codes', 'usd'),
        ('hcps_with_visit_admin', 'HCPs with visit or administration rows', 'count'),
        ('visit_admin_paid_usd', 'Paid for visits and drug administration', 'usd'),
        ('hcps_with_drug_code', 'HCPs with drug-code rows', 'count'),
        ('drug_code_paid_usd', 'Paid for drug codes', 'usd'), ('data_note', 'Note', 'text')],
       skills=['prescribing-claims'], default_panel={'type': 'table'}, caveats=['MEDICAID', 'LABELS'],
       tables=['mcd_year', 'dim_hcp'], years='2018 to 2024')
def medicaid_by_year(con, scope, id):
    return fetch(con, f'''
        select m.year, count(*) hcps_with_row, round(sum(m.paid_usd), 2) paid_usd,
               count(m.visit_admin_paid_usd) hcps_with_visit_admin, round(sum(m.visit_admin_paid_usd), 2) visit_admin_paid_usd,
               count(m.drug_code_paid_usd) hcps_with_drug_code, round(sum(m.drug_code_paid_usd), 2) drug_code_paid_usd,
               case when m.year = 2024 then 'preliminary: the last months of 2024 are largely missing' else 'full year' end data_note
        from mcd_year m join dim_hcp h on h.npi = m.npi
        where {scope_filter(scope)} group by m.year order by m.year''', (id,))


@query('center_medicare_cart_stays',
       'Medicare fee-for-service inpatient stays in the CAR-T hospital group (all CAR-T products and some other cell '
       'therapies together) at one center, per year 2021 to 2024. A blank count is not zero: the status says why no '
       'count is published.',
       {'ccn': 'ccn'},
       [('year', 'Year', 'year'), ('stays', 'Medicare CAR-T inpatient stays', 'count'), ('status', 'Status', 'text')],
       skills=['center-profile', 'prescribing-claims'], default_panel={**BY_YEAR, 'y': ['stays']}, caveats=MEDICARE,
       tables=['center_medicare_stays'], years='2021 to 2024')
def center_medicare_cart_stays(con, ccn):
    rows = fetch(con, 'select year, stays, status from center_medicare_stays where ccn = ? order by year', (ccn,))
    if not rows and not con.execute('select 1 from dim_center where ccn = ?', (ccn,)).fetchone():
        raise QueryError(f'No center with CCN {ccn}. Call find_entity first.')
    return rows


@query('community_medicare_cart_stays',
       'Medicare fee-for-service CAR-T inpatient stays at the centers attached to a community, per year: how many '
       'centers have a published count, their summed stays, how many attached centers have no published count, and '
       "how many of those are cancer or children's hospitals that Medicare never publishes. The sum is a floor, not "
       "the community's total.",
       {'community_id': 'community_id'},
       [('year', 'Year', 'year'), ('centers_with_count', 'Centers with a published count', 'count'),
        ('stays', 'Medicare CAR-T inpatient stays at those centers', 'count'),
        ('centers_without_count', 'Centers with no published count', 'count'),
        ('centers_never_published', "Of these, cancer or children's hospitals Medicare never publishes", 'count')],
       skills=['prescribing-claims', 'community-view', 'center-profile'], default_panel={**BY_YEAR, 'y': ['stays']},
       caveats=MEDICARE + ['CORE_ONLY'], tables=['center_medicare_stays', 'dim_center'], years='2021 to 2024')
def community_medicare_cart_stays(con, community_id):
    if not con.execute('select 1 from dim_community where community_id = ?', (community_id,)).fetchone():
        raise QueryError(f'No community with id {community_id}. Call find_entity first.')
    return fetch(con, '''
        select s.year, count(s.stays) centers_with_count, sum(s.stays) stays, count(*) - count(s.stays) centers_without_count,
               count(case when s.status = ? then 1 end) centers_never_published
        from center_medicare_stays s join dim_center c on c.ccn = s.ccn
        where c.community_id = ? group by s.year order by s.year''', (HOSPITAL_TYPE, community_id))


@query('top_centers_by_medicare_stays',
       'Centers ranked by published Medicare fee-for-service CAR-T inpatient stays in one year, nationally or in one '
       'state. Centers with no published count (fewer than 11 stays, cancer hospitals paid outside the standard '
       "Medicare system, children's hospitals) cannot appear.",
       {'year': STAY_YEAR, 'state': STATE, 'n': TOP_N},
       [('name', 'Center', 'text'), ('ccn', 'CCN', 'id'), ('city', 'City', 'text'), ('state', 'State', 'text'),
        ('community', 'Community', 'text'), ('stays', 'Medicare CAR-T inpatient stays', 'count')],
       skills=['center-profile', 'prescribing-claims'], default_panel={'type': 'bar', 'x': 'name', 'y': ['stays']},
       caveats=MEDICARE, tables=['center_medicare_stays', 'dim_center', 'dim_community'], years='2021 to 2024')
def top_centers_by_medicare_stays(con, year, state, n):
    state = state.upper()
    if state and not (len(state) == 2 and state.isalpha()):
        raise QueryError('state must be a two-letter code such as TX, or left out for all states.')
    return fetch(con, '''
        select c.name, c.ccn, c.city, c.state, m.short_label community, s.stays
        from center_medicare_stays s join dim_center c on c.ccn = s.ccn left join dim_community m on m.community_id = c.community_id
        where s.year = ? and s.stays is not null and (? = '' or c.state = ?)
        order by s.stays desc, c.ccn limit ?''', (year, state, state, n))


@query('national_spending_by_brand_year',
       'National spending, claims and patients per CAR-T product and year, for Medicare Part B (outpatient only; most '
       'CAR-T is given in hospital and paid under Part A) or Medicaid (all states together, before rebates; no patient '
       'count). The status says whether a blank is a year without a value or a brand missing from the file.',
       {'program': PROGRAM},
       [('year', 'Year', 'year'), ('brand', 'Product', 'text'), ('program', 'Program', 'text'),
        ('spending_usd', 'Program spending', 'usd'), ('claims', 'Claims', 'count'),
        ('beneficiaries', 'Patients', 'count'), ('status', 'Status', 'text')],
       skills=['product-timeline', 'prescribing-claims'],
       default_panel={'type': 'line', 'x': 'year', 'y': ['spending_usd'], 'split_by': 'brand', 'markers': 'all_products'},
       caveats=MEDICARE, tables=['national_spending'], years='2020 to 2024')
def national_spending_by_brand_year(con, program):
    return fetch(con, '''
        select year, brand, case program when 'medicare_part_b' then 'Medicare Part B (outpatient only)'
                                         else 'Medicaid (all states, before rebates)' end program,
               spending_usd, claims, beneficiaries,
               case value_status when 'published' then 'published' when 'no_value_in_file' then 'no value in the file for this year'
                                 else 'brand not in the file' end status
        from national_spending where program = ? order by year, brand''', (program,))


@query('cart_admin_by_state_year',
       'Medicare Part B services for CAR-T cell administration billed by clinicians, national and by state, per year '
       'and place of service. A state appears only with 11 or more patients, so states do not add up to the national '
       'row.',
       {},
       [('year', 'Year', 'year'), ('geography', 'Geography', 'text'), ('state', 'State', 'text'),
        ('place', 'Place of service', 'text'), ('rendering_providers', 'Clinicians billing', 'count'),
        ('beneficiaries', 'Medicare patients', 'count'), ('services', 'Administration services', 'count')],
       skills=['prescribing-claims', 'center-profile'], default_panel={'type': 'table'}, caveats=MEDICARE,
       tables=['mcr_cart_admin_state_year'], years='2019 to 2024')
def cart_admin_by_state_year(con):
    return fetch(con, '''
        select year, geography, state, case place when 'F' then 'hospital or other facility' else 'office' end place,
               rendering_providers, beneficiaries, services
        from mcr_cart_admin_state_year order by year, level = 'state', geography, place''')


@query('prescribing_summary',
       'Headline Medicare and Medicaid numbers for a scope in one year: HCPs prescribing oral lymphoma drugs and their '
       'Part D claims, HCPs billing rituximab and chemotherapy administration under Part B, Part B drug and non-drug '
       'payment with the HCPs behind it, and Medicaid paid. Counts are HCPs with a published value.',
       {**SCOPE, 'year': CLAIM_YEAR},
       [('year', 'Year', 'year'), ('hcps', 'HCPs in scope', 'count'),
        ('partd_prescribers', 'HCPs prescribing oral lymphoma drugs (Part D)', 'count'),
        ('partd_claims', 'Part D claims for oral lymphoma drugs', 'count'),
        ('rituximab_billers', 'HCPs billing rituximab (Part B)', 'count'),
        ('infusion_billers', 'HCPs billing chemotherapy administration (Part B)', 'count'),
        ('partb_hcps_with_split', 'HCPs with a published Part B drug and non-drug split', 'count'),
        ('partb_drug_payment_usd', 'Medicare Part B drug payment', 'usd'),
        ('partb_non_drug_payment_usd', 'Medicare Part B non-drug payment', 'usd'),
        ('medicaid_hcps', 'HCPs with a released Medicaid row', 'count'),
        ('medicaid_paid_usd', 'Medicaid paid, all codes', 'usd')],
       skills=['prescribing-claims', 'hcp-profile', 'community-view', 'center-profile'], default_panel={'type': 'kpis'},
       caveats=MEDICARE + ['MEDICAID', 'LABELS'],
       tables=['mcr_partd_year', 'mcr_marker_year', 'mcr_partb_year', 'mcd_year', 'dim_hcp'], years='2017 to 2024')
def prescribing_summary(con, scope, id, year):
    where = scope_filter(scope)

    def one(select, table, family=None):
        """One row over the scope's HCPs in the year; `family` narrows the Part B billing rows."""
        return con.execute(f'select {select} from {table} t join dim_hcp h on h.npi = t.npi '
                           f'where {where} and t.year = ?' + (' and t.family = ?' if family else ''),
                           (id, year, family) if family else (id, year)).fetchone()
    orals = one('count(distinct t.npi), sum(t.claims)', 'mcr_partd_year')
    partb = one('count(t.drug_payment_usd), round(sum(t.drug_payment_usd), 2), round(sum(t.non_drug_payment_usd), 2)', 'mcr_partb_year')
    medicaid = one('count(*), round(sum(t.paid_usd), 2)', 'mcd_year')
    covered = con.execute('select 1 from mcd_year where year = ? limit 1', (year,)).fetchone()
    return [{
        'year': year, 'hcps': con.execute(f'select count(*) from dim_hcp h where {where}', (id,)).fetchone()[0],
        'partd_prescribers': orals[0], 'partd_claims': orals[1],
        'rituximab_billers': one('count(distinct t.npi)', 'mcr_marker_year', 'rituximab')[0],
        'infusion_billers': one('count(distinct t.npi)', 'mcr_marker_year', ADMINISTRATION)[0],
        'partb_hcps_with_split': partb[0], 'partb_drug_payment_usd': partb[1], 'partb_non_drug_payment_usd': partb[2],
        'medicaid_hcps': medicaid[0] if covered else None, 'medicaid_paid_usd': medicaid[1]}]
