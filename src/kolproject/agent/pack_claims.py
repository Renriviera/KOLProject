"""Claims tables of the agent pack: Medicare Part B and Part D, Medicaid, and Medicare CAR-T hospital stays.

Medicare and Medicaid only. No state all-payer table, no hospital price-file column and no payer split is read
here, so the pack holds nothing about commercial insurance. Every table is a published snapshot table reshaped:
blank cells stay NULL (Medicare hides cells resting on 10 or fewer patients), and nothing is recovered by
subtraction. Choices a reader of the queries should know:

- `mcr_partb_year` keeps the `no_row` HCP-years with NULL amounts, so every HCP has all eight years.
- `mcr_marker_year` takes 2024 from two tables that overlap on the drug codes; the overlap is loaded once. The
  2024 administration rows carry no payment in their source, so their estimated payment is NULL.
- `mcr_partd_year` keeps one row per CMS generic and brand name and adds one tidy `drug` name per generic.
- `mcd_year` holds the servicing side only; HCP-years with rows on the billing side alone are left out.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .packbase import connect, create_table, load_csv, read_rows, register, source, value
from .paths import PACK

TABLES = ['mcr_partb_year', 'mcr_marker_year', 'mcr_partd_year', 'mcd_year', 'center_medicare_stays',
          'national_spending', 'mcr_cart_admin_state_year']
SOURCES = {
    'partb_2017_2018': ('extensions', 'hcp_medicare_partb_payments_by_year_2017_2018'),
    'partb_2019_2024': ('volume', 'hcp_medicare_partb_payments_by_year'),
    'markers_2017_2023': ('extensions', 'hcp_medicare_partb_treatment_markers_2017_2023'),
    'markers_2024': ('divisions', 'treatment_markers_2024'),
    'drug_rows_2024': ('volume', 'medicare_partb_drug_rows_2024'),
    'code_validity': ('extensions', 'lymphoma_product_code_validity'),
    'partd_2017_2023': ('extensions', 'hcp_medicare_partd_lymphoma_orals_2017_2023'),
    'partd_2024': ('divisions', 'part_d_lymphoma_orals_2024'),
    'medicaid': ('volume', 'hcp_medicaid_payments_by_year_2018_2024'),
    'panel': ('volume', 'cart_site_volume_panel'),
    'spending': ('extensions', 'cart_product_program_spending_by_year'),
    'cart_admin': ('extensions', 'state_medicare_cart_administration_by_year'),
}
YEARS = {'mcr_partb_year': (2017, 2024), 'mcr_marker_year': (2017, 2024), 'mcr_partd_year': (2017, 2024),
         'mcd_year': (2018, 2024), 'center_medicare_stays': (2021, 2024), 'national_spending': (2020, 2024),
         'mcr_cart_admin_state_year': (2019, 2024)}
KEYS = {'mcr_partb_year': ('npi', 'year'), 'mcr_marker_year': ('npi', 'year', 'code', 'place'),
        'mcr_partd_year': ('npi', 'year', 'cms_generic_name', 'brand_name'), 'mcd_year': ('npi', 'year'),
        'center_medicare_stays': ('ccn', 'year'), 'national_spending': ('brand', 'program', 'year'),
        'mcr_cart_admin_state_year': ('geography_code', 'year', 'code', 'place')}
HCP_TABLES = ('mcr_partb_year', 'mcr_marker_year', 'mcr_partd_year', 'mcd_year')
PARTB_STATUSES = ('published_split', 'split_suppressed', 'no_row')
SMALLEST_PUBLISHED = 11

ADMINISTRATION = 'chemotherapy administration'
ADMINISTRATION_CODES = ('96413', '96415', '96417')
# Family labels of the source tables, in plain words. The 2024 label `dlbcl_agents` groups several products and
# is resolved by code.
FAMILY_WORDS = {'chemo_administration': ADMINISTRATION, 'rituximab_intravenous': 'rituximab',
                'rituximab_subcutaneous': 'rituximab', 'rituximab_iv': 'rituximab', 'rituximab_sc': 'rituximab',
                'bendamustine': 'bendamustine', 'obinutuzumab': 'obinutuzumab', 'polatuzumab_vedotin': 'polatuzumab',
                'tafasitamab': 'tafasitamab', 'loncastuximab_tesirine': 'loncastuximab',
                'mosunetuzumab': 'bispecific antibody', 'glofitamab': 'bispecific antibody',
                'epcoritamab': 'bispecific antibody'}
FAMILIES = [ADMINISTRATION, 'rituximab', 'bendamustine', 'obinutuzumab', 'polatuzumab', 'tafasitamab', 'loncastuximab',
            'bispecific antibody']
DRUG_CLASSES = {'ibrutinib': 'BTK inhibitor', 'acalabrutinib': 'BTK inhibitor', 'zanubrutinib': 'BTK inhibitor',
                'pirtobrutinib': 'BTK inhibitor', 'lenalidomide': 'immunomodulator', 'venetoclax': 'BCL-2 inhibitor',
                'tazemetostat': 'EZH2 inhibitor'}
DRUGS = list(DRUG_CLASSES)
SOURCE_DRUG_CLASSES = {'btk_inhibitor': 'BTK inhibitor', 'lenalidomide': 'immunomodulator', 'venetoclax': 'BCL-2 inhibitor',
                       'tazemetostat': 'EZH2 inhibitor'}

STAY_YEARS = (2021, 2022, 2023, 2024)
PUBLISHED = 'published'
BELOW_THRESHOLD = 'not published, fewer than 11 or none'
HOSPITAL_TYPE = 'not published for this hospital type'
NOT_IN_FILE = 'not published, hospital not in the Medicare file'
STAY_STATUSES = (PUBLISHED, BELOW_THRESHOLD, HOSPITAL_TYPE, NOT_IN_FILE)

STATE_CODES = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA', 'Colorado': 'CO',
    'Connecticut': 'CT', 'Delaware': 'DE', 'District of Columbia': 'DC', 'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI',
    'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA',
    'Maine': 'ME', 'Maryland': 'MD', 'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
    'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ',
    'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
    'Oregon': 'OR', 'Pennsylvania': 'PA', 'Puerto Rico': 'PR', 'Rhode Island': 'RI', 'South Carolina': 'SC',
    'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT', 'Virginia': 'VA',
    'Washington': 'WA', 'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY'}
# Name fragments that would mean a commercial, all-payer or payer-split column or source slipped in.
FORBIDDEN = ('commercial', 'payer', 'sparcs', 'hcai', 'ahca', 'remit', 'aprdrg', 'private', 'insur')

PARTB_COLUMNS = [('npi', 'text'), ('year', 'int'), ('status', 'text'), ('provider_type', 'text'),
                 ('total_payment_usd', 'real'), ('drug_payment_usd', 'real'), ('non_drug_payment_usd', 'real'),
                 ('beneficiaries', 'int')]
PARTB_RENAME = {'year': 'service_year', 'status': 'by_provider_status', 'provider_type': 'cms_provider_type',
                'total_payment_usd': 'total_medicare_payment_usd', 'drug_payment_usd': 'drug_medicare_payment_usd',
                'non_drug_payment_usd': 'non_drug_medicare_payment_usd', 'beneficiaries': 'total_beneficiaries'}
MARKER_COLUMNS = [('npi', 'text'), ('year', 'int'), ('code', 'text'), ('place', 'text'), ('family', 'text'),
                  ('beneficiaries', 'int'), ('services', 'real'), ('estimated_payment_usd', 'real'), ('source', 'text')]
PARTD_COLUMNS = [('npi', 'text'), ('year', 'int'), ('drug', 'text'), ('drug_class', 'text'), ('cms_generic_name', 'text'),
                 ('brand_name', 'text'), ('claims', 'int'), ('fills_30day', 'real'), ('drug_cost_usd', 'real'),
                 ('beneficiaries', 'int')]
MEDICAID_COLUMNS = [('npi', 'text'), ('year', 'int'), ('paid_usd', 'real'), ('visit_admin_paid_usd', 'real'),
                    ('drug_code_paid_usd', 'real')]
MEDICAID_RENAME = {'paid_usd': 'medicaid_servicing_paid_usd', 'visit_admin_paid_usd': 'medicaid_servicing_visit_admin_paid_usd',
                   'drug_code_paid_usd': 'medicaid_servicing_drug_code_paid_usd'}
SPENDING_COLUMNS = [('brand', 'text'), ('program', 'text'), ('year', 'int'), ('spending_usd', 'real'), ('claims', 'int'),
                    ('dosage_units', 'real'), ('beneficiaries', 'int'), ('value_status', 'text')]
SPENDING_RENAME = {'brand': 'brand_name', 'year': 'calendar_year', 'spending_usd': 'total_spending_usd', 'claims': 'total_claims',
                   'dosage_units': 'total_dosage_units', 'beneficiaries': 'total_beneficiaries'}
ADMIN_COLUMNS = [('level', 'text'), ('geography_code', 'text'), ('geography', 'text'), ('state', 'text'), ('year', 'int'),
                 ('code', 'text'), ('place', 'text'), ('rendering_providers', 'int'), ('beneficiaries', 'int'),
                 ('services', 'int')]


def path(name):
    """Published CSV behind a short source name."""
    return source(*SOURCES[name])


def cited(*names):
    """Source tables as `snapshot/table`, for the pack inventory."""
    return [str(path(name).parent.name) + '/' + path(name).stem for name in names]


def code_families():
    """Billing code -> family in plain words: the product-code table plus the three administration codes."""
    out = {code: ADMINISTRATION for code in ADMINISTRATION_CODES}
    out.update({row['hcpcs_code']: FAMILY_WORDS[row['code_family']] for row in read_rows(path('code_validity'))})
    return out


def place_key(row):
    return row['npi'], row['hcpcs_code'], row['place_of_service']


def family_drug_rows(family):
    """2024 drug rows whose code belongs to one of the families, keyed by NPI, code and place of service."""
    return {place_key(row): row for row in read_rows(path('drug_rows_2024')) if row['hcpcs_code'] in family}


def marker_rows():
    """Part B rows for 2017 to 2024. A 2024 drug row present in both 2024 tables is loaded once, with the payment
    estimate that only the drug-row table carries."""
    family = code_families()
    for row in read_rows(path('markers_2017_2023')):
        yield (row['npi'], int(row['service_year']), row['hcpcs_code'], row['place_of_service'], family[row['hcpcs_code']],
               value(row['beneficiaries'], 'int'), value(row['services_or_units'], 'real'),
               value(row['estimated_medicare_payment_usd'], 'real'), 'markers_2017_2023')
    drug, seen = family_drug_rows(family), set()
    for row in read_rows(path('markers_2024')):
        seen.add(place_key(row))
        paid = drug.get(place_key(row), {}).get('estimated_medicare_payment_usd')
        yield (row['npi'], 2024, row['hcpcs_code'], row['place_of_service'], family[row['hcpcs_code']],
               value(row['beneficiaries'], 'int'), value(row['services_or_units'], 'real'), value(paid, 'real'), 'markers_2024')
    for key, row in drug.items():
        if key not in seen:
            yield (row['npi'], 2024, row['hcpcs_code'], row['place_of_service'], family[row['hcpcs_code']],
                   value(row['beneficiaries'], 'int'), value(row['services_or_units'], 'real'),
                   value(row['estimated_medicare_payment_usd'], 'real'), 'drug_rows_2024')


def tidy_drug(generic_name):
    """One name per generic: the first word of the CMS generic name, so salt forms and brand variants add up."""
    drug = generic_name.split()[0].lower()
    if drug not in DRUG_CLASSES:
        raise ValueError(f'Part D row for a drug outside the seven oral lymphoma drugs: {generic_name}')
    return drug


def partd_rows():
    for name in ('partd_2017_2023', 'partd_2024'):
        for row in read_rows(path(name)):
            drug = tidy_drug(row['generic_name'])
            yield (row['npi'], int(row['service_year']), drug, DRUG_CLASSES[drug], row['generic_name'], row['brand_name'],
                   value(row['total_claims'], 'int'), value(row['total_30day_fills'], 'real'),
                   value(row['total_drug_cost_usd'], 'real'), value(row['total_beneficiaries'], 'int'))


def childrens_hospital(ccn):
    """CMS numbers children's hospitals 3300 to 3399 in the last four characters of the CCN."""
    return ccn[2:].isdigit() and 3300 <= int(ccn[2:]) <= 3399


def stay_status(stays, ccn, exempt_cancer_hospital, file_status_2024):
    """Why a center-year has a count or not, in words. Only 2024 was checked against the whole Medicare file, so
    for earlier years the hospital type and the 2024 file status are used."""
    if stays is not None:
        return PUBLISHED
    if file_status_2024 == 'not_in_file':
        return HOSPITAL_TYPE if exempt_cancer_hospital or childrens_hospital(ccn) else NOT_IN_FILE
    return BELOW_THRESHOLD


def stay_rows(con):
    """Long form of the four Medicare stay columns of the site panel; no other panel column is read."""
    panel = {row['ccn']: row for row in read_rows(path('panel')) if row['ccn']}
    for ccn, exempt, file_status in con.execute(
            'select ccn, pps_exempt_cancer_hospital, medicare_file_status_2024 from dim_center order by ccn').fetchall():
        for year in STAY_YEARS:
            if ccn in panel:
                stays = value(panel[ccn][f'medicare_ffs_drg018_{year}'], 'int')
                yield ccn, year, stays, stay_status(stays, ccn, exempt, file_status)


def admin_rows():
    for row in read_rows(path('cart_admin')):
        yield (row['geography_level'], row['geography_code'], row['geography_name'], STATE_CODES.get(row['geography_name']),
               int(row['service_year']), row['hcpcs_code'], row['place_of_service'], value(row['rendering_providers'], 'int'),
               value(row['beneficiaries'], 'int'), value(row['services'], 'int'))


def partb_rows():
    for name in ('partb_2017_2018', 'partb_2019_2024'):
        for row in read_rows(path(name)):
            yield [value(row[PARTB_RENAME.get(c, c)], kind) for c, kind in PARTB_COLUMNS]


def build(con):
    counts = {
        'mcr_partb_year': create_table(con, 'mcr_partb_year', PARTB_COLUMNS, partb_rows(), KEYS['mcr_partb_year'], ['year']),
        'mcr_marker_year': create_table(con, 'mcr_marker_year', MARKER_COLUMNS, marker_rows(), KEYS['mcr_marker_year'],
                                        [('year', 'family')]),
        'mcr_partd_year': create_table(con, 'mcr_partd_year', PARTD_COLUMNS, partd_rows(), KEYS['mcr_partd_year'],
                                       [('year', 'drug')]),
        'mcd_year': load_csv(con, 'mcd_year', path('medicaid'), MEDICAID_COLUMNS, MEDICAID_RENAME,
                             lambda row: row['medicaid_row_sides'] != 'billing', KEYS['mcd_year'], ['year']),
        'center_medicare_stays': create_table(con, 'center_medicare_stays', [('ccn', 'text'), ('year', 'int'), ('stays', 'int'),
                                                                             ('status', 'text')],
                                              stay_rows(con), KEYS['center_medicare_stays'], ['year']),
        'national_spending': load_csv(con, 'national_spending', path('spending'), SPENDING_COLUMNS, SPENDING_RENAME,
                                      primary_key=KEYS['national_spending']),
        'mcr_cart_admin_state_year': create_table(con, 'mcr_cart_admin_state_year', ADMIN_COLUMNS, admin_rows(),
                                                  KEYS['mcr_cart_admin_state_year'], ['year']),
    }
    register(con, 'mcr_partb_year', 'HCP x year', cited('partb_2017_2018', 'partb_2019_2024'),
             'Medicare Part B payment per HCP and service year, split into drugs and everything else; years with no '
             'Medicare row are kept with blank amounts.', '2017 to 2024')
    register(con, 'mcr_marker_year', 'HCP x year x billing code x place of service',
             cited('markers_2017_2023', 'markers_2024', 'drug_rows_2024', 'code_validity'),
             'Medicare Part B billing rows for chemotherapy administration and infused lymphoma drugs, each with a family '
             'in plain words; 2024 administration rows have no payment estimate.', '2017 to 2024')
    register(con, 'mcr_partd_year', 'HCP x year x drug (CMS generic and brand name)', cited('partd_2017_2023', 'partd_2024'),
             'Medicare Part D claims and gross drug cost for seven oral lymphoma drugs per prescriber and year; '
             'beneficiaries are blank when 10 or fewer.', '2017 to 2024')
    register(con, 'mcd_year', 'HCP x year', cited('medicaid'),
             'Medicaid paid amounts on released claim lines naming the HCP as servicing provider: all codes, visits and '
             'drug administration, and drug codes; lower bounds, 2024 incomplete.', '2018 to 2024')
    register(con, 'center_medicare_stays', 'center x year', cited('panel'),
             'Medicare fee-for-service inpatient stays in the CAR-T hospital group (MS-DRG 018) per center and year, with '
             'the reason in words when no count is published.', '2021 to 2024')
    register(con, 'national_spending', 'brand x program x year', cited('spending'),
             'National Medicare Part B (outpatient only) and Medicaid spending, claims and dosage units per CAR-T product '
             'and year.', '2020 to 2024')
    register(con, 'mcr_cart_admin_state_year', 'geography x year x place of service', cited('cart_admin'),
             'Medicare Part B services for CAR-T cell administration (code 0540T), national and by state; states appear '
             'only at 11 or more patients, so they do not add up to the national row.', '2019 to 2024')
    return counts


def usd(amount):
    if amount >= 1e9:
        return f'${amount / 1e9:.2f} billion'
    if amount >= 1e6:
        return f'${amount / 1e6:.1f} million'
    return f'${amount:,.0f}'


def listed(words):
    return words[0] if len(words) == 1 else ', '.join(words[:-1]) + ' and ' + words[-1]


def latest_year_rows(con, sql):
    """Rows of each HCP's latest year, grouped by NPI. `sql` selects npi, year and more, ordered by NPI."""
    out = defaultdict(list)
    for row in con.execute(sql):
        out[row[0]].append(row)
    return out


def hcp_facts(con):
    facts = defaultdict(list)
    orals = latest_year_rows(con, '''
        select p.npi, p.year, p.drug, sum(p.claims) claims from mcr_partd_year p
        join (select npi, max(year) year from mcr_partd_year group by npi) l on l.npi = p.npi and l.year = p.year
        group by p.npi, p.drug order by p.npi, claims desc, p.drug''')
    for npi, rows in orals.items():
        (_, year, drug, claims), total = rows[0], sum(row[3] for row in rows)
        facts[npi].append(
            f'Medicare Part D, {year}: {claims:,} claims for {drug}, the only oral lymphoma drug with a published count '
            'for this HCP.' if len(rows) == 1 else
            f'Medicare Part D, {year}: {total:,} claims for oral lymphoma drugs, most for {drug} ({claims:,} claims).')
    billed = latest_year_rows(con, '''
        select m.npi, m.year, m.family, sum(m.services) services from mcr_marker_year m
        join (select npi, max(year) year from mcr_marker_year group by npi) l on l.npi = m.npi and l.year = m.year
        group by m.npi, m.family order by m.npi, m.family''')
    for npi, rows in billed.items():
        year = rows[0][1]
        parts = [f'{ADMINISTRATION} ({services:,.0f} services)' for _, _, family, services in rows if family == ADMINISTRATION]
        parts += [family for _, _, family, _ in rows if family != ADMINISTRATION]
        facts[npi].append(f"Medicare Part B, {year}: billed {listed(parts)} under this HCP's own NPI.")
    paid = con.execute('''
        select b.npi, b.year, b.total_payment_usd, b.drug_payment_usd, b.non_drug_payment_usd from mcr_partb_year b
        join (select npi, max(year) year from mcr_partb_year where status != 'no_row' group by npi) l
          on l.npi = b.npi and l.year = b.year
        where b.status = 'published_split' and b.drug_payment_usd + b.non_drug_payment_usd > 0''')
    for npi, year, total, drug, other in paid:
        share = f'{drug / (drug + other):.0%} of it for drugs' if drug > 0 else 'none of it for drugs'
        facts[npi].append(f"Medicare Part B paid {usd(total)} in {year} for services under this HCP's NPI, {share}.")
    return {('hcp', npi): sentences for npi, sentences in facts.items()}


def center_facts(con):
    facts, by_center = {}, defaultdict(list)
    for ccn, year, stays, status in con.execute('select ccn, year, stays, status from center_medicare_stays order by ccn, year'):
        by_center[ccn].append((year, stays, status))
    for ccn, rows in by_center.items():
        shown = [f'{stays:,} in {year}' for year, stays, _ in rows if stays is not None]
        hidden = [str(year) for year, stays, _ in rows if stays is None]
        span = f'{rows[0][0]} to {rows[-1][0]}'
        if shown:
            sentences = [f'Medicare fee-for-service CAR-T inpatient stays, all products together: {listed(shown)}.']
            if hidden:
                sentences.append(f'No count is published for {listed(hidden)}, which means fewer than 11 stays, or none.')
        elif rows[0][2] == HOSPITAL_TYPE:
            kind = "children's hospital" if childrens_hospital(ccn) else 'cancer hospital paid outside the standard Medicare system'
            sentences = [f'Medicare publishes no CAR-T inpatient stay count for this hospital, a {kind}.']
        elif rows[0][2] == NOT_IN_FILE:
            sentences = ['Medicare publishes no CAR-T inpatient stay count for this hospital, which is not in the Medicare '
                         'inpatient file.']
        else:
            sentences = [f'Medicare published no CAR-T inpatient stay count for this center in {span}, which means fewer '
                         'than 11 stays a year, or none.']
        facts[('center', ccn)] = sentences
    return facts


def community_facts(con):
    facts = defaultdict(list)
    stays = defaultdict(list)
    for row in con.execute('''
            select c.community_id, s.year, count(*) centers, count(s.stays) with_count, sum(s.stays) stays,
                   count(case when s.status = ? then 1 end) never
            from center_medicare_stays s join dim_center c on c.ccn = s.ccn where c.community_id is not null
            group by c.community_id, s.year order by c.community_id, s.year''', (HOSPITAL_TYPE,)):
        stays[row[0]].append(row[1:])
    for community, rows in stays.items():
        centers, never = rows[0][1], rows[0][4]
        shown = [f'{total:,} in {year} ({with_count} of {centers} centers)' for year, _, with_count, total, _ in rows if with_count]
        never_text = f"{never} cancer or children's hospital{'' if never == 1 else 's'} that Medicare never publishes"
        if shown:
            facts[community] += [f'Published Medicare CAR-T inpatient stays at the attached centers: {listed(shown)}.',
                                 'These sums leave out centers without a published count'
                                 + (f', including {never_text}.' if never else '.')]
        else:
            facts[community].append(f'None of the {centers} centers attached to this community has a published Medicare CAR-T '
                                    f'inpatient stay count for {rows[0][0]} to {rows[-1][0]}'
                                    + (f'; they include {never_text}.' if never else '.'))
    orals = defaultdict(list)
    for community, year, drug, claims in con.execute('''
            select h.community_id, p.year, p.drug, sum(p.claims) claims from mcr_partd_year p join dim_hcp h on h.npi = p.npi
            where h.community_id is not null and p.year = (select max(year) from mcr_partd_year)
            group by h.community_id, p.drug order by h.community_id, claims desc, p.drug'''):
        orals[community].append((year, drug, claims))
    for community, rows in orals.items():
        top = [f'{drug} ({claims:,} claims)' for _, drug, claims in rows[:3]]
        facts[community].append(f'Oral lymphoma drugs with the most Medicare Part D claims from attached HCPs in {rows[0][0]}: '
                                f'{listed(top)}.')
    return {('community', str(community)): sentences for community, sentences in facts.items()}


def card_facts(con):
    """Up to three sentences per HCP, center and community that has Medicare claims facts."""
    return {**hcp_facts(con), **center_facts(con), **community_facts(con)}


def scaled(text, scale):
    return int((Decimal(text.strip()) * scale).to_integral_value())


def differences(con, table, rows, pairs, where='1 = 1'):
    """Differences between pack columns and source columns over `rows`: the count of filled cells and the exact sum.

    `pairs` is (pack column, source column, scale); sums are compared as whole numbers after scaling (100 for cents).
    """
    errors = 0
    for column, source_column, scale in pairs:
        filled = [row[source_column] for row in rows if row[source_column].strip() != '']
        have = con.execute(f'select count("{column}"), coalesce(sum(cast(round("{column}" * {scale}) as integer)), 0) '
                           f'from "{table}" where {where}').fetchone()
        errors += abs(have[0] - len(filled)) + abs(have[1] - sum(scaled(text, scale) for text in filled))
    return errors


def marker_checks(con, one):
    """The three Part B billing sources against the pack; a 2024 drug row present in both 2024 tables must be there once."""
    family = code_families()
    early, late = list(read_rows(path('markers_2017_2023'))), list(read_rows(path('markers_2024')))
    drug = family_drug_rows(family)
    late_keys = {place_key(row) for row in late}
    overlap = [row for row in late if place_key(row) in drug]
    added = [row for key, row in drug.items() if key not in late_keys]
    labelled = {(row['npi'], int(row['service_year']), row['hcpcs_code'], row['place_of_service']): FAMILY_WORDS.get(row[label])
                for rows, label in ((early, 'code_family'), (late, 'marker_group')) for row in rows}
    labelled.update({(key[0], 2024, key[1], key[2]): FAMILY_WORDS.get(row['lymphoma_product_family']) for key, row in drug.items()})
    stored = {tuple(row[:4]): row[4] for row in con.execute('select npi, year, code, place, family from mcr_marker_year')}
    counts = ('beneficiaries', 'beneficiaries', 1), ('services', 'services_or_units', 1000)
    payment = (('estimated_payment_usd', 'estimated_medicare_payment_usd', 100),)
    repeated = one('select count(*) from mcr_marker_year a join mcr_marker_year b on a.npi = b.npi and a.year = b.year '
                   'and a.code = b.code and a.place = b.place and a.rowid < b.rowid where a.year = 2024')
    return {
        'claims_marker_rows': abs(len(stored) - len(early) - len(late) - len(added)),
        'claims_marker_totals_2017_2023': differences(con, 'mcr_marker_year', early, counts + payment, "source = 'markers_2017_2023'"),
        'claims_marker_totals_2024': differences(con, 'mcr_marker_year', late, counts, "source = 'markers_2024'")
        + differences(con, 'mcr_marker_year', added, counts, "source = 'drug_rows_2024'")
        + differences(con, 'mcr_marker_year', list(drug.values()), payment, 'year = 2024'),
        'claims_marker_2024_counted_twice': repeated + abs(one('select count(*) from mcr_marker_year where year = 2024')
                                                           - len(late_keys | set(drug))),
        'claims_marker_2024_sources_disagree': sum(
            1 for row in overlap if row['beneficiaries'] != drug[place_key(row)]['beneficiaries']
            or Decimal(row['services_or_units']) != Decimal(drug[place_key(row)]['services_or_units'])),
        'claims_marker_family': sum(1 for key, name in stored.items() if name not in FAMILIES or name != family.get(key[2])
                                    or labelled.get(key, name) not in (None, name)),
        'claims_marker_drug_row_outside_families': sum(1 for row in read_rows(path('drug_rows_2024'))
                                                       if bool(row['lymphoma_product_family']) != (row['hcpcs_code'] in family)),
    }


def partd_checks(con, one):
    rows = [row for name in ('partd_2017_2023', 'partd_2024') for row in read_rows(path(name))]
    classes = {tidy_drug(row['generic_name']): SOURCE_DRUG_CLASSES.get(row['drug_class']) for row in rows if row.get('drug_class')}
    return {
        'claims_partd_rows': abs(one('select count(*) from mcr_partd_year') - len(rows)),
        'claims_partd_totals': differences(con, 'mcr_partd_year', rows, (
            ('claims', 'total_claims', 1), ('fills_30day', 'total_30day_fills', 1000), ('drug_cost_usd', 'total_drug_cost_usd', 100),
            ('beneficiaries', 'total_beneficiaries', 1))),
        'claims_partd_drug_names': sum(1 for drug, name in con.execute('select drug, drug_class from mcr_partd_year group by 1, 2')
                                       if DRUG_CLASSES.get(drug) != name) + sum(1 for drug, name in classes.items()
                                                                               if DRUG_CLASSES[drug] != name),
    }


def stay_checks(con, one):
    centers = {row[0] for row in con.execute('select ccn from dim_center')}
    panel = [row for row in read_rows(path('panel')) if row['ccn'] in centers]
    totals = sum(differences(con, 'center_medicare_stays', panel, (('stays', f'medicare_ffs_drg018_{year}', 1),), f'year = {year}')
                 for year in STAY_YEARS)
    marks = ', '.join('?' for _ in STAY_STATUSES)
    return {
        'claims_stays_rows': abs(one('select count(*) from center_medicare_stays') - len(STAY_YEARS) * len(centers)),
        'claims_stays_totals': totals,
        'claims_stays_status': con.execute(f"select count(*) from center_medicare_stays where status not in ({marks}) "
                                           f"or (stays is null) = (status = '{PUBLISHED}')", STAY_STATUSES).fetchone()[0],
    }


def structure_checks(con, one):
    out = {}
    for table, key in KEYS.items():
        columns = ', '.join(f'"{c}"' for c in key)
        low, high = YEARS[table]
        out[f'claims_duplicate_key_{table}'] = one(f'select count(*) from (select 1 from "{table}" group by {columns} having count(*) > 1)')
        out[f'claims_years_{table}'] = one(f'select count(*) from "{table}" where year is null or year < {low} or year > {high}')
    out['claims_npi_not_in_dim_hcp'] = sum(one(f'select count(*) from "{t}" where npi not in (select npi from dim_hcp)') for t in HCP_TABLES)
    out['claims_ccn_not_in_dim_center'] = one('select count(*) from center_medicare_stays where ccn not in (select ccn from dim_center)')
    small = [('mcr_partb_year', 'beneficiaries'), ('mcr_marker_year', 'beneficiaries'), ('mcr_partd_year', 'beneficiaries'),
             ('mcr_partd_year', 'claims'), ('center_medicare_stays', 'stays'), ('national_spending', 'beneficiaries'),
             ('mcr_cart_admin_state_year', 'beneficiaries')]
    out['claims_published_count_under_11'] = sum(one(f'select count(*) from "{t}" where "{c}" < {SMALLEST_PUBLISHED}') for t, c in small)
    amounts = [('mcr_partb_year', 'total_payment_usd'), ('mcr_partb_year', 'drug_payment_usd'), ('mcr_partb_year', 'non_drug_payment_usd'),
               ('mcr_marker_year', 'services'), ('mcr_marker_year', 'estimated_payment_usd'), ('mcr_partd_year', 'drug_cost_usd'),
               ('mcr_partd_year', 'fills_30day'), ('mcd_year', 'paid_usd'), ('mcd_year', 'visit_admin_paid_usd'),
               ('mcd_year', 'drug_code_paid_usd'), ('national_spending', 'spending_usd'), ('national_spending', 'claims'),
               ('mcr_cart_admin_state_year', 'services'), ('mcr_cart_admin_state_year', 'rendering_providers')]
    out['claims_negative_value'] = sum(one(f'select count(*) from "{t}" where "{c}" < 0') for t, c in amounts)
    names = [row[1].lower() for table in TABLES for row in con.execute(f'pragma table_info("{table}")')]
    marks = ', '.join('?' for _ in TABLES)
    names += [row[0].lower() for row in con.execute(f'select sources from pack_table where name in ({marks})', TABLES)]
    out['claims_commercial_or_payer_split'] = sum(1 for name in names for word in FORBIDDEN if word in name)
    return out


def check(con):
    """Row counts and exact totals against every source table, keys, joins to the dims, thresholds; zero passes."""
    one = lambda sql: con.execute(sql).fetchone()[0]
    partb = [row for name in ('partb_2017_2018', 'partb_2019_2024') for row in read_rows(path(name))]
    medicaid = [row for row in read_rows(path('medicaid')) if row['medicaid_row_sides'] != 'billing']
    spending, admin = list(read_rows(path('spending'))), list(read_rows(path('cart_admin')))
    statuses = ', '.join(f"'{s}'" for s in PARTB_STATUSES)
    out = {
        'claims_partb_rows': abs(one('select count(*) from mcr_partb_year') - len(partb)),
        'claims_partb_totals': differences(con, 'mcr_partb_year', partb, [(c, PARTB_RENAME[c], 100 if c.endswith('_usd') else 1)
                                                                          for c, kind in PARTB_COLUMNS if kind != 'text' and c != 'year']),
        'claims_partb_status': one(f"select count(*) from mcr_partb_year where status not in ({statuses}) "
                                   "or (status = 'no_row') != (total_payment_usd is null) "
                                   "or (status = 'published_split') != (drug_payment_usd is not null) "
                                   "or (drug_payment_usd is null) != (non_drug_payment_usd is null)"),
        'claims_medicaid_rows': abs(one('select count(*) from mcd_year') - len(medicaid)),
        'claims_medicaid_totals': differences(con, 'mcd_year', medicaid, [(c, s, 100) for c, s in MEDICAID_RENAME.items()]),
        'claims_spending_rows': abs(one('select count(*) from national_spending') - len(spending)),
        'claims_spending_totals': differences(con, 'national_spending', spending, (
            ('spending_usd', 'total_spending_usd', 100), ('claims', 'total_claims', 1), ('dosage_units', 'total_dosage_units', 1000),
            ('beneficiaries', 'total_beneficiaries', 1))),
        'claims_spending_status': one("select count(*) from national_spending where (value_status = 'published') != (spending_usd is not null)"),
        'claims_cart_admin_rows': abs(one('select count(*) from mcr_cart_admin_state_year') - len(admin)),
        'claims_cart_admin_totals': differences(con, 'mcr_cart_admin_state_year', admin, (
            ('rendering_providers', 'rendering_providers', 1), ('beneficiaries', 'beneficiaries', 1), ('services', 'services', 1))),
        'claims_cart_admin_state_code': one("select count(*) from mcr_cart_admin_state_year where (level = 'state') != (state is not null)"),
    }
    out.update(marker_checks(con, one))
    out.update(partd_checks(con, one))
    out.update(stay_checks(con, one))
    out.update(structure_checks(con, one))
    return out


def main():
    """Build the claims tables into agent/pack/dev_claims.sqlite, a copy of the core tables, and check them."""
    target = PACK / 'dev_claims.sqlite'
    for suffix in ('', '-wal', '-shm'):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    base, con = connect(PACK / 'dims.sqlite', readonly=True), connect(target)
    base.backup(con)
    base.close()
    counts = build(con)
    con.commit()
    checks = check(con)
    facts = card_facts(con)
    print('CLAIMS', counts)
    print('CARD FACTS', {kind: sum(1 for k, _ in facts if k == kind) for kind in ('hcp', 'center', 'community')})
    print('CHECKS', len(checks), 'run;', 'failing:', {name: count for name, count in checks.items() if count})
    con.close()


if __name__ == '__main__':
    main()
