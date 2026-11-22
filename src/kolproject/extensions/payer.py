"""Payer tables of the 2026-11-20 snapshot: Medicare Part B treatment markers and Part D oral lymphoma drugs for
service years 2017-2023, the validity by year of each lymphoma product code, a national reference of CAR-T product
spending by brand and year, state all-payer CAR-T hospital stays (California 2021-2023, Florida 2023-2025) and
state-level Medicare CAR-T administration (0540T) for 2019-2024.

Every request goes to a pinned data.cms.gov dataset version (one per service year, read from the catalog
https://data.cms.gov/data.json and never through the series alias) and is frozen by `fetch`. Part B rows are
national per-code queries filtered here to the 17,510-NPI universe; Part D rows are national per-generic queries.
CMS drops a by-Provider-and-Service or Part D row resting on 10 or fewer beneficiaries, and blanks a Part D
beneficiary count of 1 to 10, so a blank is never a zero and every blank count carries a status in words.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .base import (DIVISION_GROUPS, OUT, PROCESSED, RAW, ROOT, SNAPSHOT, column, fetch, log_local, new_rows,
                   now, publish, read_csv, request_log, universe, write_module_metadata)

MODULE = 'payer'
MARKERS, ORALS, SPENDING = 'hcp_medicare_partb_treatment_markers_2017_2023', 'hcp_medicare_partd_lymphoma_orals_2017_2023', \
    'cart_product_program_spending_by_year'
TABLES = [MARKERS, ORALS, SPENDING]
YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023]
API = 'https://data.cms.gov/data-api/v1/dataset/{version}/data'
CATALOG = 'https://data.cms.gov/data.json'
PAGE, PACE = 5000, 10.0   # data.cms.gov rate-limits bursts; robots.txt asks for ten seconds between requests

# Pinned per-service-year dataset versions, read from the catalog of 2026-11-20 (each year's only API distribution).
SERVICE_VERSIONS = {2017: '85bf3c9c-2244-490d-ad7d-c34e4c28f8ea', 2018: 'fb6d9fe8-38c1-4d24-83d4-0b7b291000b2',
                    2019: '867b8ac7-ccb7-4cc9-873d-b24340d89e32', 2020: 'c957b49e-1323-49e7-8678-c09da387551d',
                    2021: '31dc2c47-f297-4948-bfb4-075e1bec3a02', 2022: 'e650987d-01b7-4f09-b75e-b0b075afbf98',
                    2023: '0e9f2f2b-7bf9-451a-912c-e02e654dd725'}
SERVICE_TITLE = 'Medicare Physician & Other Practitioners - by Provider and Service'
PART_D_VERSIONS = {2017: '05f108dd-76c4-49f4-9fdc-788d8f4251ec', 2018: '802fe556-311f-4962-8d75-d5f4ff405884',
                   2019: '2a6705e6-7a1e-460c-ba22-35249a531918', 2020: '7795fe20-e80e-435a-a9ed-d2d65e05feeb',
                   2021: 'f68114ed-f854-4ffc-9c6e-ed78b5e2f8d0', 2022: 'b101b457-ffa4-49bb-8fd9-27c1266086e2',
                   2023: 'e54db557-cd82-4e91-a0fe-61aad5865d69'}
PART_D_TITLE = 'Medicare Part D Prescribers - by Provider and Drug'
# Spending-by-drug files: the pinned version of the 2024 release, which carries the calendar years 2020-2024.
PARTB_SPENDING_VERSION, MEDICAID_SPENDING_VERSION = 'f48ced88-541a-4c51-947b-149197e5eaf3', '98aa468b-130b-4c3d-897a-552a94f85750'
PARTB_SPENDING_TITLE, MEDICAID_SPENDING_TITLE = 'Medicare Part B Spending by Drug', 'Medicaid Spending by Drug'

# Project family labels; code numbers only, never CMS descriptor text.
CODE_FAMILIES = {
    '96413': 'chemo_administration', '96415': 'chemo_administration', '96417': 'chemo_administration',
    'J9312': 'rituximab_intravenous', 'Q5115': 'rituximab_intravenous', 'Q5119': 'rituximab_intravenous',
    'Q5123': 'rituximab_intravenous', 'J9311': 'rituximab_subcutaneous', 'J9309': 'polatuzumab_vedotin',
    'J9349': 'tafasitamab', 'J9350': 'mosunetuzumab', 'J9286': 'glofitamab', 'J9321': 'epcoritamab',
    'J9359': 'loncastuximab_tesirine', 'J9301': 'obinutuzumab', 'J9033': 'bendamustine', 'J9034': 'bendamustine',
    'J9036': 'bendamustine', 'J9056': 'bendamustine', 'J9058': 'bendamustine', 'J9059': 'bendamustine',
    # Product-specific temporary codes billed before the permanent code (dates from the CMS HCPCS files).
    'J9310': 'rituximab_intravenous', 'C9467': 'rituximab_subcutaneous',
    'C9042': 'bendamustine', 'C9070': 'tafasitamab', 'C9084': 'loncastuximab_tesirine', 'C9155': 'epcoritamab',
}
# The marker groups of treatment_markers_2024.csv, for the codes that table holds.
MARKER_GROUPS = {'chemo_administration': 'chemo_administration', 'rituximab_intravenous': 'rituximab_iv',
                 'rituximab_subcutaneous': 'rituximab_sc', 'polatuzumab_vedotin': 'dlbcl_agents',
                 'tafasitamab': 'dlbcl_agents', 'loncastuximab_tesirine': 'dlbcl_agents', 'epcoritamab': 'dlbcl_agents',
                 'glofitamab': 'dlbcl_agents', 'mosunetuzumab': 'dlbcl_agents'}
SERVICE_FIELDS = ['Rndrng_NPI', 'Rndrng_Prvdr_Type', 'HCPCS_Cd', 'HCPCS_Drug_Ind', 'Place_Of_Srvc', 'Tot_Benes',
                  'Tot_Srvcs', 'Tot_Bene_Day_Srvcs', 'Avg_Mdcr_Alowd_Amt', 'Avg_Mdcr_Pymt_Amt']
# Exact CMS generic-name spellings as in part_d_lymphoma_orals_2024.csv; salts are separate values.
PART_D_GENERICS = ['Ibrutinib', 'Acalabrutinib', 'Acalabrutinib Maleate', 'Zanubrutinib', 'Pirtobrutinib',
                   'Lenalidomide', 'Venetoclax', 'Tazemetostat Hydrobromide']
PART_D_FIELDS = ['Prscrbr_NPI', 'Prscrbr_Type', 'Brnd_Name', 'Gnrc_Name', 'Tot_Clms', 'Tot_30day_Fills',
                 'Tot_Day_Suply', 'Tot_Drug_Cst', 'Tot_Benes']
DRUG_CLASSES = {'Ibrutinib': 'btk_inhibitor', 'Acalabrutinib': 'btk_inhibitor', 'Acalabrutinib Maleate': 'btk_inhibitor',
                'Zanubrutinib': 'btk_inhibitor', 'Pirtobrutinib': 'btk_inhibitor', 'Lenalidomide': 'lenalidomide',
                'Venetoclax': 'venetoclax', 'Tazemetostat Hydrobromide': 'tazemetostat'}
# CAR-T brands (upper case as matched); the Part B file names a product by its brand.
CART_BRANDS = ['YESCARTA', 'KYMRIAH', 'TECARTUS', 'BREYANZI', 'ABECMA', 'CARVYKTI', 'AUCATZYL']
SPENDING_YEARS = [2020, 2021, 2022, 2023, 2024]

EARLIER_MARKERS = PROCESSED / '2026-10-24' / 'treatment_markers_2024.csv'
EARLIER_DRUG_ROWS = PROCESSED / '2026-11-19' / 'medicare_partb_drug_rows_2024.csv'
EARLIER_ORALS = PROCESSED / '2026-10-24' / 'part_d_lymphoma_orals_2024.csv'
MARKER_KEY = ['npi', 'service_year', 'hcpcs_code', 'place_of_service']
ORAL_KEY = ['npi', 'service_year', 'generic_name', 'brand_name']
SPENDING_KEY = ['brand_name', 'program', 'calendar_year']
ORAL_STATUSES = ['beneficiaries_published', 'beneficiaries_suppressed_1_to_10']
SPENDING_STATUSES = ['published', 'no_value_in_file', 'brand_not_in_file']


def to_cents(value):
    value = str(value).strip()
    return int((Decimal(value) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if value else None


def estimate(average, units):
    """Estimated payment on one released row: CMS average payment times services, rounded to cents."""
    return str((Decimal(average) * Decimal(units)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def count(value):
    """A CMS count as an integer text ('12.0' becomes '12'); blank stays blank."""
    value = str(value).strip()
    if not value:
        return ''
    number = Decimal(value)
    assert number == number.to_integral_value(), f'Not a whole count: {value}'
    return str(int(number))


def plain(value):
    """A CMS decimal as text, blank kept blank; no rounding."""
    return str(value).strip()


# ---------- collection ----------

def paged(version, filters, fields):
    """All rows of one exact-value query on a pinned version, paging until a short page; (rows, records)."""
    rows, records, offset = [], [], 0
    while True:
        params = {f'filter[{k}]': v for k, v in filters.items()}
        params.update(column=','.join(fields), size=PAGE, offset=offset)
        url = API.format(version=version) + '?' + urllib.parse.urlencode(params, safe=',[]')
        path, record = fetch(url, MODULE, pause=PACE, suffix='.json')
        page = json.loads(path.read_text())
        assert isinstance(page, list), f'Expected a list from {url}'
        for row in page:
            for k, v in filters.items():
                assert str(row[k]) == str(v), f'Filter {k}={v} not applied: {row[k]}'
        records.append(record)
        rows.extend(page)
        offset += len(page)
        if len(page) < PAGE:
            return rows, records
        assert offset < 400000, 'Unexpected query size'


def collect_service(log=print):
    out = {}
    for year in YEARS:
        for code in CODE_FAMILIES:
            out[(year, code)] = paged(SERVICE_VERSIONS[year], {'HCPCS_Cd': code}, SERVICE_FIELDS)
            log(f'service {year} {code}: {len(out[(year, code)][0])} national rows')
    return out


def collect_part_d(log=print):
    out = {}
    for year in YEARS:
        for generic in PART_D_GENERICS:
            out[(year, generic)] = paged(PART_D_VERSIONS[year], {'Gnrc_Name': generic}, PART_D_FIELDS)
            log(f'part_d {year} {generic}: {len(out[(year, generic)][0])} national rows')
    return out


def collect_spending():
    """Whole Part B and Medicaid spending-by-drug files (each a few thousand rows) from their pinned versions."""
    out = {}
    for program, version in (('medicare_part_b', PARTB_SPENDING_VERSION), ('medicaid', MEDICAID_SPENDING_VERSION)):
        rows, records, offset = [], [], 0
        while True:
            url = API.format(version=version) + f'?size={PAGE}&offset={offset}'
            path, record = fetch(url, MODULE, pause=PACE, suffix='.json')
            page = json.loads(path.read_text())
            records.append(record)
            rows.extend(page)
            offset += len(page)
            if len(page) < PAGE:
                break
        out[program] = (rows, records)
    return out


def collect_catalog():
    return fetch(CATALOG, MODULE, pause=PACE, suffix='.json')


def collect_all(log=print):
    collect_catalog()
    spending = collect_spending()
    log('spending: ' + ', '.join(f'{k} {len(v[0])}' for k, v in spending.items()))
    part_d = collect_part_d(log)
    service = collect_service(log)
    geo = collect_geo(log)
    return service, part_d, spending, geo


def unsign(record, portal_url, redirect_read_at, location=''):
    """Replace the signed storage address in a frozen record by the stable portal URL (also on disk)."""
    unsigned = (location or record.get('source_url', '')).split('?')[0]
    record = {k: v for k, v in record.items() if k not in ('portal_url', 'source_url_unsigned')}
    record.update(source_url=portal_url, storage_object_url_unsigned=unsigned.replace('s3.amazonaws.com:443', 's3.amazonaws.com'),
                  redirect_read_at_utc=redirect_read_at)
    meta = RAW / MODULE / (Path(record['local_path']).stem + '.meta.json')
    if meta.exists():
        meta.write_text(json.dumps(record, indent=1, sort_keys=True))
    return record


def unsign_index():
    """Rewrite the portal index and frozen records of earlier runs so no signed address stays on disk."""
    index_path = RAW / MODULE / 'portal_redirect_index.json'
    if not index_path.exists():
        return
    index = json.loads(index_path.read_text())
    index = {url: unsign(rec, url, rec.get('redirect_read_at_utc', ''),
                         rec.get('storage_object_url_unsigned') or rec.get('source_url_unsigned') or rec['source_url'])
             for url, rec in index.items()}
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_portal_file(url, suffix):
    """Fetch one California portal download through `fetch` and return (path, record).

    The portal answers with a redirect to a signed S3 address whose explicit ':443' port breaks the signature for
    the standard library client, and the signature changes on every visit. So the redirect is read once without
    following it (that one request is logged in the index below with its time), the port is dropped, and the file
    is fetched and frozen; the index maps the stable portal URL to the frozen record so a rebuild never asks again.
    """
    index_path = RAW / MODULE / 'portal_redirect_index.json'
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    if url in index and (ROOT / index[url]['local_path']).exists():
        return ROOT / index[url]['local_path'], index[url]
    started = now()
    try:
        urllib.request.build_opener(_NoRedirect).open(url, timeout=120)
        raise ValueError(f'Expected a redirect from {url}')
    except urllib.error.HTTPError as error:
        assert error.code in (301, 302, 303, 307), f'{url}: HTTP {error.code}'
        location = error.headers['Location'].replace('s3.amazonaws.com:443', 's3.amazonaws.com')
    path, record = fetch(location, MODULE, pause=3, suffix=suffix)
    record = unsign(record, url, started, location)
    index[url] = record
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True))
    return path, record


# ---------- state all-payer hospital stays ----------

STATE = 'state_cart_hospital_stays_by_payer'
TABLES.append(STATE)
STATE_KEY = ['ccn', 'year', 'source', 'payer_group']
CA_PORTAL = 'https://data.chhs.ca.gov/dataset/0fa47cc2-284e-45a2-83b6-a4524fd84fb2/resource/{resource}/download/{name}'
CA_FILES = {2021: ('e0e070f3-79e8-43d5-9d3d-5880191208fe', '2021top25msdrg_pivot_20220705.xlsm', '2022-09-02'),
            2022: ('91f93719-307c-464f-9852-5b892525fea2', '2022top25msdrg_pivot_20230701.xlsm', '2023-10-02'),
            2023: ('a6ed2963-df4a-4668-9ead-6d79310a7686', '2023top25msdrg_pivot_202407018.xlsm', '2024-10-01')}
CA_PACKAGE = 'https://data.chhs.ca.gov/api/3/action/package_show?id=top-25-ms-drgs-individual-hospital-pivot-profile'
CA_SOURCE = 'ca_hcai_top25_msdrg_hospital_profile'
FL_SOURCE = 'fl_ahca_inpatient_query_tool'
FL_TOOL = 'https://quality.healthfinder.fl.gov/QueryTool/QTResults'
FL_PROBES = ROOT / 'research_notes' / 'CAR-T physician and hospital volume data' / 'probes' / \
    'gap-state-allpayer-remaining-and-npi-terms' / 'florida'
FL_YEARS = [2023, 2024, 2025]
# Florida AHCA file number to CCN, mapped by hand from the hospital names and cities of cart_centers.csv.
FL_FACILITIES = {'110009': '100271', '100151': '100151', '100079': '100079', '100007': '100007', '111527': '100281'}
FL_PAYERS = {'Medicare': 'medicare_ffs', 'Medicare Managed Care': 'medicare_managed_care',
             'Commercial Health Insurance': 'commercial', 'Medicaid': 'medicaid',
             'Medicaid Managed Care': 'medicaid_managed_care'}
FL_THRESHOLD = 30   # the site disclaimer: fewer than 30 cases shown as N/A, zero cases excluded
FL_DISCLAIMER = 'https://quality.healthfinder.fl.gov/compare-care/Disclaimer'
FL_DISCLAIMER_WORDING = 'facilities with fewer than 30 cases will be denoted with'
STATE_STATUSES = ['shown', 'not_shown_below_display_threshold', 'drg018_not_in_top25_tables']
PANEL = PROCESSED / '2026-11-19' / 'cart_site_volume_panel.csv'
# (state, year) cells the 2026-11-19 panel already holds; New York before 2023 pools CAR-T with transplant.
PANEL_CELLS = {('CA', 2024), ('CA', 2025), ('NY', 2023), ('NY', 2024)}
SOURCE_STATE = {CA_SOURCE: 'CA', FL_SOURCE: 'FL'}


def xlsx_rows(path, sheet):
    """Rows of one worksheet of an .xlsx/.xlsm file as {column letter: text}, standard library only."""
    import html
    import re
    import zipfile
    with zipfile.ZipFile(path) as book:
        strings = [html.unescape(''.join(re.findall(r'<t[^>]*>([^<]*)</t>', item)))
                   for item in re.findall(r'<si>(.*?)</si>', book.read('xl/sharedStrings.xml').decode(), re.S)]
        names = re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', book.read('xl/workbook.xml').decode())
        rels = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', book.read('xl/_rels/workbook.xml.rels').decode()))
        rels.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="([^"]+)"',
                                                   book.read('xl/_rels/workbook.xml.rels').decode())})
        target = rels[dict(names)[sheet]].lstrip('/')
        data = book.read(target if target.startswith('xl/') else 'xl/' + target).decode()
    for row in re.finditer(r'<row [^>]*>(.*?)</row>', data, re.S):
        out = {}
        for cell in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', row.group(1), re.S):
            letter, attrs, inner = cell.groups()
            value = re.search(r'<v>([^<]*)</v>', inner or '')
            text = value.group(1) if value else ''
            if 't="s"' in attrs and value:
                text = strings[int(text)]
            out[letter] = html.unescape(text)
        yield out


def ca_drg018(rows):
    """Facility number to MS-DRG 018 discharges from the 'All Data' rows; one facility appears once per rank table."""
    rows = iter(rows)
    header = next(rows)
    names = {v: k for k, v in header.items()}
    found = {}
    for row in rows:
        if row.get(names['DRGCode'], '').strip() != '018':
            continue
        facility, discharges = row[names['FacilityNumber']].strip(), count(row[names['Discharges']])
        assert found.get(facility, discharges) == discharges, f'Facility {facility} has two MS-DRG 018 counts'
        found[facility] = discharges
    return found


def fl_shown(lines_by_year, payer_lines):
    """{(file number, year, payer group): discharges} of shown Florida cells, all-payer and by payer."""
    import csv
    out = {}
    for year, lines in lines_by_year.items():
        for row in csv.DictReader(lines):
            if row['File Number'].strip():
                out[(row['File Number'].strip(), year, 'all_payer')] = count(row['Discharges'])
    for row in csv.DictReader(payer_lines):
        if row['file_number'].strip():
            out[(row['file_number'].strip(), int(row['year']), FL_PAYERS[row['payer']])] = count(row['discharges'])
    return out


def page_text(page):
    """Visible text of an HTML page, whitespace collapsed."""
    import html
    import re
    page = re.sub(r'(?s)<(script|style).*?</\1>', '', page)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', page)))


def shown_count(row):
    """The state count of a state-table row, from the column of its own source."""
    return row['ca_hcai_discharges'] if row['source'] == CA_SOURCE else row['fl_ahca_discharges']


def state_row(ccn, year, source, payer, discharges, facility, centers_set, release, retrieved, query):
    shown = discharges != ''
    status = 'shown' if shown else ('not_shown_below_display_threshold' if source == FL_SOURCE
                                    else 'drg018_not_in_top25_tables')
    return {'ccn': ccn, 'year': str(year), 'source': source, 'payer_group': payer, 'state': SOURCE_STATE[source],
            'ca_hcai_discharges': discharges if source == CA_SOURCE else '',
            'fl_ahca_discharges': discharges if source == FL_SOURCE else '', 'cell_status': status, 'state_facility_number': facility,
            'in_cart_center_set': '1' if ccn in centers_set else '0', 'query_parameters': query,
            'source_release': release, 'retrieved_at_utc': retrieved,
            'attribution': ('California Department of Health Care Access and Information (HCAI), Top 25 MS-DRGs '
                            'Individual Hospital Pivot Profile, CC BY' if source == CA_SOURCE else
                            'Florida Agency for Health Care Administration (AHCA), FloridaHealthFinder inpatient query '
                            'tool; counts as displayed by the tool, no affiliation claimed')}


def build_state(log):
    from ..volume_billing.center_panel import CA_FACILITIES
    centers = {c['ccn']: c for c in read_csv(ROOT / 'Data' / 'processed' / '2026-10-24' / 'cart_centers.csv')}
    rows, unmapped = [], {}
    package, package_record = fetch(CA_PACKAGE, MODULE, pause=3, suffix='.json')
    ca_centers = sorted(c for c, r in centers.items() if r['state'] == 'CA')
    ca_statewide, ca_records = {}, []
    for year, (resource, name, release) in CA_FILES.items():
        path, record = fetch_portal_file(CA_PORTAL.format(resource=resource, name=name), '.xlsm')
        ca_records.append(record)
        found = ca_drg018(xlsx_rows(path, 'All Data'))
        ca_statewide[year] = found.pop('000000', '')
        by_ccn = {}
        for facility, discharges in found.items():
            if facility not in CA_FACILITIES:
                unmapped[f'{year}:{facility}'] = discharges
                continue
            ccn = CA_FACILITIES[facility]
            assert ccn not in by_ccn, f'Two facilities map to {ccn} in {year}'
            by_ccn[ccn] = (facility, discharges)
        for ccn in sorted(set(by_ccn) | set(ca_centers)):
            facility, discharges = by_ccn.get(ccn, ('', ''))
            rows.append(state_row(ccn, year, CA_SOURCE, 'all_payer', discharges, facility, centers, release,
                                  record['retrieved_at_utc'], f'workbook {name}, sheet All Data, DRGCode 018'))
    # Florida: the query tool's shown cells, saved by the 2026-11-19 search as eight small CSV files.
    files = sorted(FL_PROBES.glob('*.csv'))
    fl_records = []
    for path in files:
        obtained = __import__('datetime').datetime.fromtimestamp(path.stat().st_mtime, __import__('datetime').timezone.utc)
        fl_records.append(log_local(MODULE, path.stem, path, obtained.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                    release='query tool data as served on 2026-11-19',
                                    note='Output of the public Florida AHCA inpatient query tool saved by the search'))
    fl_saved = {Path(r['local_path']).name: r['retrieved_at_utc'] for r in fl_records}
    file_for = lambda year, payer: (f'fl_querytool_inpatient_drg018_{year}_by_Hospital_Name.csv' if payer == 'all_payer'
                                    else 'fl_querytool_inpatient_drg018_hospital_by_payer_2023_2025.csv')
    disclaimer, disclaimer_record = fetch(FL_DISCLAIMER, MODULE, pause=3, suffix='.html')
    assert FL_DISCLAIMER_WORDING in page_text(disclaimer.read_text(errors='ignore')), 'Florida disclaimer wording changed'
    by_year = {y: (FL_PROBES / f'fl_querytool_inpatient_drg018_{y}_by_Hospital_Name.csv').read_text().splitlines()
               for y in FL_YEARS}
    shown = fl_shown(by_year, (FL_PROBES / 'fl_querytool_inpatient_drg018_hospital_by_payer_2023_2025.csv')
                     .read_text().splitlines())
    unknown = {k for k in shown if k[0] not in FL_FACILITIES}
    assert not unknown, f'Florida file numbers without a CCN: {unknown}'
    fl_centers = sorted(c for c, r in centers.items() if r['state'] == 'FL')
    reverse = {v: k for k, v in FL_FACILITIES.items()}
    for year in FL_YEARS:
        for payer in ['all_payer', *FL_PAYERS.values()]:
            for ccn in sorted(set(fl_centers) | set(FL_FACILITIES.values())):
                facility = reverse.get(ccn, '')
                discharges = shown.get((facility, year, payer), '')
                query = (f'query made by the 2026-11-19 search: source Inpatient; MS-DRG 018; year {year}; aggregated by '
                         'Hospital Name' + ('' if payer == 'all_payer' else f'; principal payer {payer}'))
                rows.append(state_row(ccn, year, FL_SOURCE, payer, discharges, facility, centers,
                                      'query tool data as served on 2026-11-19', fl_saved[file_for(year, payer)], query))
    fl_statewide = {}
    for line in (FL_PROBES / 'fl_querytool_inpatient_drg018_by_year.csv').read_text().splitlines()[1:]:
        parts = line.split(',')
        if parts[0].isdigit():
            fl_statewide[int(parts[0])] = int(parts[1])
    meta = {'ca_records': ca_records + [package_record], 'fl_records': fl_records, 'fl_disclaimer': disclaimer_record, 'unmapped_ca': unmapped,
            'ca_statewide': ca_statewide, 'fl_statewide': fl_statewide,
            'ca_license': json.loads(package.read_text())['result'].get('license_title') or ''}
    return rows, meta


# ---------- shaping of CMS rows ----------

SERVICE_RELEASES = {2017: '2025-03-26', 2018: '2025-03-26', 2019: '2025-03-26', 2020: '2025-03-26', 2021: '2025-03-26', 2022: '2025-03-26', 2023: '2025-10-28'}
PART_D_RELEASES = {2017: '2024-06-04', 2018: '2024-06-04', 2019: '2024-06-04', 2020: '2024-06-04', 2021: '2024-06-04', 2022: '2024-06-04', 2023: '2025-09-09'}
SPENDING_RELEASE = '2026-06-25'


def marker_row(record, year, retrieved):
    """One released NPI x code x place-of-service row of a by-Provider-and-Service year; no descriptor text."""
    code = record['HCPCS_Cd'].strip()
    return {'npi': record['Rndrng_NPI'].strip(), 'service_year': str(year), 'hcpcs_code': code,
            'place_of_service': record['Place_Of_Srvc'].strip(), 'code_family': CODE_FAMILIES[code],
            'hcpcs_drug_indicator': record['HCPCS_Drug_Ind'].strip(),
            'beneficiaries': count(record['Tot_Benes']), 'services_or_units': plain(record['Tot_Srvcs']),
            'beneficiary_days': count(record['Tot_Bene_Day_Srvcs']),
            'average_medicare_allowed_usd': plain(record['Avg_Mdcr_Alowd_Amt']),
            'average_medicare_payment_usd': plain(record['Avg_Mdcr_Pymt_Amt']),
            'estimated_medicare_payment_usd': estimate(record['Avg_Mdcr_Pymt_Amt'], record['Tot_Srvcs']),
            'cms_provider_type': record['Rndrng_Prvdr_Type'].strip(),
            'source_dataset_version_id': SERVICE_VERSIONS[year], 'source_release': SERVICE_RELEASES[year],
            'retrieved_at_utc': retrieved}


def oral_row(record, year, retrieved):
    """One NPI x drug row of a Part D year; a beneficiary count of 1 to 10 is blank in the CMS file."""
    beneficiaries = count(record['Tot_Benes'])
    generic = record['Gnrc_Name'].strip()
    return {'npi': record['Prscrbr_NPI'].strip(), 'service_year': str(year), 'generic_name': generic,
            'brand_name': record['Brnd_Name'].strip(), 'drug_class': DRUG_CLASSES[generic],
            'total_claims': count(record['Tot_Clms']), 'total_30day_fills': plain(record['Tot_30day_Fills']),
            'total_day_supply': plain(record['Tot_Day_Suply']), 'total_drug_cost_usd': plain(record['Tot_Drug_Cst']),
            'total_beneficiaries': beneficiaries,
            'beneficiaries_status': 'beneficiaries_published' if beneficiaries else 'beneficiaries_suppressed_1_to_10',
            'prescriber_type': record['Prscrbr_Type'].strip(),
            'source_dataset_version_id': PART_D_VERSIONS[year], 'source_release': PART_D_RELEASES[year],
            'retrieved_at_utc': retrieved}


def spending_rows(program, records, retrieved, version):
    """Brand x calendar year rows of one spending-by-drug file for the CAR-T brands (all-manufacturer rows only)."""
    found = defaultdict(list)
    for record in records:
        brand = record['Brnd_Name'].strip().upper()
        if brand in CART_BRANDS and (program == 'medicare_part_b' or record.get('Mftr_Name', '').strip() == 'Overall'):
            found[brand].append(record)
    out = []
    for brand in CART_BRANDS:
        assert len(found[brand]) <= 1, f'{program}: {len(found[brand])} rows for {brand}'
        record = found[brand][0] if found[brand] else None
        for year in SPENDING_YEARS:
            value = lambda field: plain(record.get(f'{field}_{year}', '')) if record else ''
            spending = value('Tot_Spndng')
            status = 'brand_not_in_file' if record is None else 'published' if spending else 'no_value_in_file'
            benes = value('Tot_Benes')
            out.append({'brand_name': brand.title(), 'program': program, 'calendar_year': str(year),
                        'hcpcs_code': record.get('HCPCS_Cd', '').strip() if record else '',
                        'total_spending_usd': spending, 'total_claims': count(value('Tot_Clms')),
                        'total_dosage_units': value('Tot_Dsg_Unts'),
                        'total_beneficiaries': count(benes) if program == 'medicare_part_b' else '',
                        'beneficiaries_status': ('not_reported_by_program' if program == 'medicaid' else
                                                 'published' if benes else 'blank_in_file'),
                        'outlier_flag': value('Outlier_Flag'), 'value_status': status,
                        'source_dataset_version_id': version, 'source_release': SPENDING_RELEASE,
                        'retrieved_at_utc': retrieved})
    return out


# ---------- columns ----------

PROVENANCE = ['source_dataset_version_id', 'source_release', 'retrieved_at_utc']


def provenance_columns(what):
    return [
        column('source_dataset_version_id', f'Pinned data.cms.gov dataset version id the row was read from ({what}); never the series alias.', 'Never blank.', 'cms'),
        column('source_release', 'Date the pinned version was last modified according to the data.cms.gov catalog (https://data.cms.gov/data.json) read on 2026-11-20.', 'Never blank.', 'cms'),
        column('retrieved_at_utc', 'UTC time the response page holding the row was retrieved and frozen.', 'Never blank.', 'project')]


MARKER_COLUMNS = [
    column('npi', 'National Provider Identifier (NPI) of the rendering clinician; one of the 17,510 NPIs of hcp_divisions.csv (2026-10-24), joined on npi for names and divisions.', 'Never blank.', 'cms'),
    column('service_year', 'Medicare service year of the row, 2017 to 2023 (2024 is published in treatment_markers_2024.csv and medicare_partb_drug_rows_2024.csv).', 'Never blank.', 'cms'),
    column('hcpcs_code', 'Healthcare Common Procedure Coding System (HCPCS) code number billed; descriptor text is deliberately not published.', 'Never blank.', 'cms'),
    column('place_of_service', 'CMS place-of-service flag: F facility (hospital outpatient and similar), O office or other non-facility setting.', 'Never blank.', 'cms'),
    column('code_family', 'Project label of the code: chemo_administration (96413, 96415, 96417), rituximab_intravenous, rituximab_subcutaneous, polatuzumab_vedotin, tafasitamab, mosunetuzumab, glofitamab, epcoritamab, loncastuximab_tesirine, obinutuzumab or bendamustine. None is specific to lymphoma.', 'Never blank.', 'project'),
    column('hcpcs_drug_indicator', 'CMS flag: Y when the code is a drug billed in Part B, N otherwise.', 'Never blank.', 'cms'),
    column('beneficiaries', 'Distinct Medicare fee-for-service beneficiaries with the service from this clinician in the year. CMS releases a row only when it rests on 11 or more beneficiaries.', 'Never blank; rows under 11 beneficiaries are absent from the CMS file, not zero.', 'cms'),
    column('services_or_units', 'Number of services, or billing units for a drug code, in the year, as CMS gives it (may have decimals).', 'Never blank.', 'cms'),
    column('beneficiary_days', 'Distinct beneficiary-days of service in the year.', 'Never blank.', 'cms'),
    column('average_medicare_allowed_usd', 'CMS average Medicare allowed amount per service or unit, US dollars, unrounded as published.', 'Never blank.', 'cms'),
    column('average_medicare_payment_usd', 'CMS average Medicare payment per service or unit after deductible and coinsurance, US dollars, unrounded as published.', 'Never blank.', 'cms'),
    column('estimated_medicare_payment_usd', 'average_medicare_payment_usd times services_or_units, rounded half up to cents; an estimate of the Medicare payment on the row, not a CMS figure.', 'Never blank.', 'project'),
    column('cms_provider_type', 'CMS provider type derived from the clinician\'s claims in that service year (can change between years).', 'Never blank.', 'cms'),
    *provenance_columns('Medicare Physician & Other Practitioners - by Provider and Service, one version per service year')]
ORAL_COLUMNS = [
    column('npi', 'National Provider Identifier (NPI) of the prescriber; one of the 17,510 NPIs of hcp_divisions.csv (2026-10-24), joined on npi for names and divisions.', 'Never blank.', 'cms'),
    column('service_year', 'Medicare Part D service year, 2017 to 2023 (2024 is published in part_d_lymphoma_orals_2024.csv).', 'Never blank.', 'cms'),
    column('generic_name', 'CMS generic name exactly as spelled in the file; salts are separate names (Acalabrutinib capsule and Acalabrutinib Maleate tablet).', 'Never blank.', 'cms'),
    column('brand_name', 'CMS brand name as spelled in the file; generic lenalidomide appears under its generic name once generics were sold.', 'Never blank.', 'cms'),
    column('drug_class', 'Project label: btk_inhibitor (Bruton tyrosine kinase inhibitors ibrutinib, acalabrutinib, zanubrutinib, pirtobrutinib), lenalidomide, venetoclax or tazemetostat. None is specific to lymphoma.', 'Never blank.', 'project'),
    column('total_claims', 'Part D claims including refills for the drug from this prescriber in the year. CMS releases a row only with 11 or more claims.', 'Never blank.', 'cms'),
    column('total_30day_fills', 'Claims standardized to 30-day fills, as CMS gives it (decimals).', 'Never blank.', 'cms'),
    column('total_day_supply', 'Total days of supply on the claims.', 'Never blank.', 'cms'),
    column('total_drug_cost_usd', 'Total gross drug cost paid by the plan, beneficiary and others, US dollars, as published (before rebates).', 'Never blank.', 'cms'),
    column('total_beneficiaries', 'Distinct beneficiaries with a claim for the drug from this prescriber in the year.', 'Blank when CMS suppresses a count of 1 to 10 (beneficiaries_status says so); never a zero.', 'cms'),
    column('beneficiaries_status', 'beneficiaries_published, or beneficiaries_suppressed_1_to_10 when CMS blanks the count.', 'Never blank.', 'project'),
    column('prescriber_type', 'CMS prescriber type for the year.', 'Never blank.', 'cms'),
    *provenance_columns('Medicare Part D Prescribers - by Provider and Drug, one version per service year')]
SPENDING_COLUMNS = [
    column('brand_name', 'CAR-T (chimeric antigen receptor T-cell) product brand: Yescarta, Kymriah, Tecartus, Breyanzi, Abecma, Carvykti or Aucatzyl.', 'Never blank.', 'cms'),
    column('program', 'medicare_part_b (Medicare Part B Spending by Drug, outpatient and physician claims; inpatient CAR-T stays are paid under Part A and are not in it) or medicaid (Medicaid Spending by Drug, state drug utilization, all manufacturers row).', 'Never blank.', 'cms'),
    column('calendar_year', 'Calendar year of the spending, 2020 to 2024 (the years carried by the 2024 release).', 'Never blank.', 'cms'),
    column('hcpcs_code', 'HCPCS code of the product in the Part B file.', 'Blank for medicaid rows (the Medicaid file is by drug name) and for brands not in the Part B file.', 'cms'),
    column('total_spending_usd', 'Total program spending on the product in the year, US dollars, as published (before rebates).', 'Blank when the file has no value for the year or does not list the brand; value_status says which. Never a zero for a missing value.', 'cms'),
    column('total_claims', 'Claims for the product in the year.', 'Blank under the same rule as total_spending_usd.', 'cms'),
    column('total_dosage_units', 'Dosage units in the year, as published (decimals).', 'Blank under the same rule as total_spending_usd.', 'cms'),
    column('total_beneficiaries', 'Beneficiaries with a claim for the product in the year (Part B only).', 'Blank for medicaid rows (not reported), or when the Part B file has no value; beneficiaries_status says which.', 'cms'),
    column('beneficiaries_status', 'published, blank_in_file (Part B file gives no count for the year) or not_reported_by_program (Medicaid file has no beneficiary count).', 'Never blank.', 'project'),
    column('outlier_flag', 'CMS outlier flag for the year as published (1 = flagged by CMS).', 'Blank when the file has no value for the year.', 'cms'),
    column('value_status', 'published (spending shown), no_value_in_file (brand listed, year blank, e.g. before approval or below the reporting rule) or brand_not_in_file.', 'Never blank.', 'project'),
    *provenance_columns('Medicare Part B Spending by Drug or Medicaid Spending by Drug, 2024 release')]
STATE_COLUMNS = [
    column('ccn', 'CMS Certification Number (CCN) of the hospital, six characters as text; mapped by hand from the state facility number.', 'Never blank.', 'project'),
    column('year', 'Discharge year: California 2021 to 2023 (2024-2025 are in cart_site_volume_panel.csv), Florida 2023 to 2025.', 'Never blank.', 'project'),
    column('source', f'{CA_SOURCE} (California HCAI Top 25 MS-DRGs Individual Hospital Pivot Profile) or {FL_SOURCE} (Florida AHCA FloridaHealthFinder inpatient query tool).', 'Never blank.', 'project'),
    column('payer_group', 'all_payer, or for Florida the principal payer group: medicare_ffs (traditional Medicare), medicare_managed_care, commercial, medicaid, medicaid_managed_care.', 'Never blank.', 'project'),
    column('state', 'State of the source file (CA or FL).', 'Never blank.', 'project'),
    column('ca_hcai_discharges', 'California rows: all-payer inpatient discharges in Medicare Severity Diagnosis Related Group (MS-DRG) 018 (CAR-T and other immunotherapies) at the licensed facility in the year, from the California Department of Health Care Access and Information (HCAI) Top 25 MS-DRGs Individual Hospital Pivot Profile; a hospital is listed only when MS-DRG 018 is in one of its top-25 tables.', 'Blank on every Florida row, and on a California row whose hospital is not listed (cell_status drg018_not_in_top25_tables; not evidence of zero).', 'ca_hcai'),
    column('fl_ahca_discharges', 'Florida rows: MS-DRG 018 inpatient discharges at the hospital in the year for the payer group, as displayed by the Florida Agency for Health Care Administration (AHCA) FloridaHealthFinder inpatient query tool. The site disclaimer says facilities with fewer than 30 cases are shown as N/A and facilities with zero cases are excluded.', 'Blank on every California row, and on a Florida row the tool did not display (cell_status not_shown_below_display_threshold: 0 to 29 cases); never filled by subtraction.', 'fl_ahca'),
    column('cell_status', 'shown, not_shown_below_display_threshold (Florida: the site disclaimer says facilities with fewer than 30 cases are shown as N/A and those with zero cases are excluded, so the value is 0 to 29) or drg018_not_in_top25_tables (California hospital not listed that year).', 'Never blank.', 'project'),
    column('state_facility_number', 'State facility number of the listed hospital (California HCAI facility number or Florida AHCA file number).', 'Blank for a center the source did not list that year.', 'project'),
    column('in_cart_center_set', '1 when the CCN is one of the 173 hospitals of cart_centers.csv (2026-10-24), else 0.', 'Never blank.', 'project'),
    column('query_parameters', 'Workbook and sheet (California) or query tool parameters (Florida) that produced the cell.', 'Never blank.', 'project'),
    column('source_release', 'Portal date of the California workbook, or for Florida the tool data as served when the earlier search queried it (2026-11-19).', 'Never blank.', 'project'),
    column('retrieved_at_utc', 'California: UTC time the workbook was retrieved on 2026-11-20. Florida: UTC time on 2026-11-19 at which the earlier search saved the query tool output this row comes from; the tool was not queried again on 2026-11-20 (file hashes are in the module metadata).', 'Never blank.', 'project'),
    column('attribution', 'Required attribution for the source; California data are Creative Commons Attribution, Florida data are shown counts quoted with attribution and no affiliation.', 'Never blank.', 'project')]


# ---------- state-level CAR-T administration (goal 5) ----------

GEO = 'state_medicare_cart_administration_by_year'
TABLES.append(GEO)
GEO_KEY = ['geography_code', 'service_year', 'hcpcs_code', 'place_of_service']
GEO_TITLE = 'Medicare Physician & Other Practitioners - by Geography and Service'
GEO_VERSIONS = {2019: '673030ae-ceed-4561-8fca-b1275395a86a', 2020: '31eb3018-43e0-4259-a0d8-e7a1112ffd08',
                2021: 'f00f462f-bb61-48b1-a321-1c50d78ce175', 2022: '87304f15-9ed0-41dc-a141-6141a0327453',
                2023: 'ddee9e22-7889-4bef-975a-7853e4cd0fbb', 2024: '0c75b0b3-b40f-4007-a5ac-f9f2fed95862'}
GEO_RELEASES = {2019: '2023-05-10', 2020: '2023-05-10', 2021: '2024-03-08', 2022: '2024-06-04', 2023: '2025-10-28',
                2024: '2026-05-21'}
# Category III codes of the CAR-T episode: 0537T harvest, 0538T preparation, 0539T receipt, 0540T administration.
GEO_CODES = {'0537T': 'cart_cell_harvest', '0538T': 'cart_cell_preparation', '0539T': 'cart_cell_receipt',
             '0540T': 'cart_cell_administration'}
GEO_FIELDS = ['Rndrng_Prvdr_Geo_Lvl', 'Rndrng_Prvdr_Geo_Cd', 'Rndrng_Prvdr_Geo_Desc', 'HCPCS_Cd', 'Place_Of_Srvc',
              'Tot_Rndrng_Prvdrs', 'Tot_Benes', 'Tot_Srvcs', 'Tot_Bene_Day_Srvcs', 'Avg_Mdcr_Pymt_Amt']


def collect_geo(log=print):
    out = {}
    for year, version in GEO_VERSIONS.items():
        for code in GEO_CODES:
            out[(year, code)] = paged(version, {'HCPCS_Cd': code}, GEO_FIELDS)
            log(f'geo {year} {code}: {len(out[(year, code)][0])} rows')
    return out


def geo_row(record, year, retrieved):
    """One national or state row; the geography description is a state name, kept so readers need no lookup."""
    level = record['Rndrng_Prvdr_Geo_Lvl'].strip()
    code = record['HCPCS_Cd'].strip()
    return {'geography_level': level.lower(), 'geography_code': 'US' if level == 'National' else record['Rndrng_Prvdr_Geo_Cd'].strip(),
            'geography_name': record['Rndrng_Prvdr_Geo_Desc'].strip(), 'service_year': str(year), 'hcpcs_code': code,
            'code_step': GEO_CODES[code], 'place_of_service': record['Place_Of_Srvc'].strip(),
            'rendering_providers': count(record['Tot_Rndrng_Prvdrs']), 'beneficiaries': count(record['Tot_Benes']),
            'services': plain(record['Tot_Srvcs']), 'beneficiary_days': count(record['Tot_Bene_Day_Srvcs']),
            'average_medicare_payment_usd': plain(record['Avg_Mdcr_Pymt_Amt']),
            'source_dataset_version_id': GEO_VERSIONS[year], 'source_release': GEO_RELEASES[year], 'retrieved_at_utc': retrieved}


GEO_COLUMNS = [
    column('geography_level', 'national or state, as the CMS file gives the rendering provider geography.', 'Never blank.', 'cms'),
    column('geography_code', 'US for the national row, else the two-digit state Federal Information Processing Standards (FIPS) code as CMS gives it.', 'Never blank.', 'cms'),
    column('geography_name', 'State name (or National) of the rendering providers.', 'Never blank.', 'cms'),
    column('service_year', 'Medicare service year, 2019 to 2024.', 'Never blank.', 'cms'),
    column('hcpcs_code', 'Category III code number of a CAR-T episode step (0537T to 0540T); descriptor text is not published.', 'Never blank.', 'cms'),
    column('code_step', 'Project label of the step: cart_cell_harvest (0537T), cart_cell_preparation (0538T), cart_cell_receipt (0539T), cart_cell_administration (0540T).', 'Never blank.', 'project'),
    column('place_of_service', 'F facility or O office/non-facility.', 'Never blank.', 'cms'),
    column('rendering_providers', 'Distinct rendering providers billing the code in the geography and year.', 'Never blank.', 'cms'),
    column('beneficiaries', 'Distinct Medicare fee-for-service beneficiaries; CMS releases a geography row only with 11 or more.', 'Never blank; rows under 11 are absent from the file, not zero.', 'cms'),
    column('services', 'Services billed, as published.', 'Never blank.', 'cms'),
    column('beneficiary_days', 'Distinct beneficiary-days of service.', 'Never blank.', 'cms'),
    column('average_medicare_payment_usd', 'CMS average Medicare payment per service, US dollars, unrounded as published.', 'Never blank.', 'cms'),
    *provenance_columns('Medicare Physician & Other Practitioners - by Geography and Service, one version per service year')]


# ---------- product code validity (which code a product was billed under, by year) ----------

VALIDITY = 'lymphoma_product_code_validity'
TABLES.append(VALIDITY)
VALIDITY_KEY = ['hcpcs_code', 'service_year']
HCPCS_FILES = {2021: 'https://www.cms.gov/files/zip/january-2021-alpha-numeric-hcpcs-file.zip',
               2022: 'https://www.cms.gov/files/zip/january-2022-alpha-numeric-hcpcs-file.zip',
               2023: 'https://www.cms.gov/files/zip/january-2023-alpha-numeric-hcpcs-file.zip',
               2024: 'https://www.cms.gov/files/zip/january-2024-alpha-numeric-hcpcs-files.zip',
               2025: 'https://www.cms.gov/files/zip/january-2025-alpha-numeric-hcpcs-file.zip'}
HCPCS_PAGE = 'https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update'
ADMIN_CODES = ['96413', '96415', '96417']   # Level I (CPT) codes: not in the Level II files, so not in this table
VALIDITY_STATUSES = ['valid_all_year', 'valid_part_of_year', 'not_valid']


def hcpcs_dates(paths):
    """Code to (added date, termination date, file year) from the latest January Level II file listing the code."""
    import zipfile
    out = {}
    for year, path in sorted(paths.items()):
        with zipfile.ZipFile(path) as book:
            name = [n for n in book.namelist() if n.endswith('.txt') and 'ANWEB' in n.upper()][0]
            for line in book.read(name).decode('latin1').splitlines():
                code = line[:5].strip()
                if len(line) >= 293 and code in CODE_FAMILIES and line[10] == '3':
                    out[code] = (line[268:276].strip(), line[284:292].strip(), year)
    return out


def iso(date):
    return f'{date[:4]}-{date[4:6]}-{date[6:]}' if date else ''


def validity_status(added, terminated, year):
    """Whether a code was valid in a service year, from its added and termination dates (YYYYMMDD)."""
    start, end = f'{year}0101', f'{year}1231'
    if added > end or (terminated and terminated < start):
        return 'not_valid'
    return 'valid_all_year' if added <= start and (not terminated or terminated >= end) else 'valid_part_of_year'


def validity_rows(dates, national, markers, retrieved):
    universe_rows = Counter((r['hcpcs_code'], r['service_year']) for r in markers)
    out = []
    for code in sorted(c for c in CODE_FAMILIES if c not in ADMIN_CODES):
        added, terminated, file_year = dates[code]
        for year in YEARS:
            out.append({'hcpcs_code': code, 'service_year': str(year), 'code_family': CODE_FAMILIES[code],
                        'code_kind': 'temporary_product_specific' if code.startswith('C') else 'permanent_or_biosimilar',
                        'valid_from_date': iso(added), 'valid_to_date': iso(terminated),
                        'validity_in_year': validity_status(added, terminated, year),
                        'hcpcs_file_read': f'January {file_year} alpha-numeric HCPCS file',
                        'national_rows_in_pinned_file': str(national[(year, code)]),
                        'universe_rows': str(universe_rows[(code, str(year))]),
                        'source_dataset_version_id': SERVICE_VERSIONS[year], 'source_release': SERVICE_RELEASES[year],
                        'retrieved_at_utc': retrieved[(year, code)]})
    return out


VALIDITY_COLUMNS = [
    column('hcpcs_code', 'Healthcare Common Procedure Coding System (HCPCS) Level II code number of a lymphoma product (no descriptor text). Miscellaneous codes (J9999, J3490, J3590, C9399) are not attributed to any product and are not listed.', 'Never blank.', 'cms'),
    column('service_year', 'Medicare service year, 2017 to 2023.', 'Never blank.', 'cms'),
    column('code_family', 'Project product label, as in hcp_medicare_partb_treatment_markers_2017_2023.csv.', 'Never blank.', 'project'),
    column('code_kind', 'temporary_product_specific (a C code used for hospital outpatient billing before the permanent code) or permanent_or_biosimilar.', 'Never blank.', 'project'),
    column('valid_from_date', 'Date the code was added, from the CMS Level II alpha-numeric HCPCS file (ISO date).', 'Never blank.', 'cms'),
    column('valid_to_date', 'Termination date of the code in the same file (ISO date).', 'Blank when the code had not been terminated as of the January 2025 file.', 'cms'),
    column('validity_in_year', 'valid_all_year, valid_part_of_year or not_valid, computed from valid_from_date and valid_to_date for the service year.', 'Never blank.', 'project'),
    column('hcpcs_file_read', 'The January HCPCS file (2021 to 2025) whose record gave the dates: the latest file listing the code.', 'Never blank.', 'cms'),
    column('national_rows_in_pinned_file', 'Rows of the code in the pinned by-Provider-and-Service file of the year, all clinicians nationally; 0 means CMS released no clinician row (fewer than 11 beneficiaries everywhere, or the code was not valid).', 'Never blank.', 'cms'),
    column('universe_rows', 'Rows of the code and year in hcp_medicare_partb_treatment_markers_2017_2023.csv (universe NPIs only).', 'Never blank.', 'project'),
    *provenance_columns('Medicare Physician & Other Practitioners - by Provider and Service of the year')]


# ---------- Part B payments per NPI, 2017-2018 (by Provider) ----------

PAYMENTS = 'hcp_medicare_partb_payments_by_year_2017_2018'
PAYMENT_YEARS = [2017, 2018]
PAYMENT_KEY = ['npi', 'service_year']
BY_PROVIDER_TITLE = 'Medicare Physician & Other Practitioners - by Provider'
BY_PROVIDER_VERSIONS = {2017: '44ea2789-993f-4d55-ac44-ed7f160b58fa', 2018: '900850df-c9a9-47ce-a9e0-d0ceae5a811f'}
BY_PROVIDER_RELEASES = {2017: '2025-03-26', 2018: '2025-03-26'}
BY_PROVIDER_FIELDS = ['Rndrng_NPI', 'Rndrng_Prvdr_State_Abrvtn', 'Rndrng_Prvdr_Type', 'Tot_Benes', 'Tot_Srvcs',
                      'Tot_Mdcr_Pymt_Amt', 'Drug_Sprsn_Ind', 'Drug_Tot_Benes', 'Drug_Mdcr_Pymt_Amt', 'Med_Sprsn_Ind',
                      'Med_Tot_Benes', 'Med_Mdcr_Pymt_Amt']
TYPE_QUERIES = ['Hematology-Oncology', 'Medical Oncology', 'Hematology']
NPI_BATCH = 100
PAYMENT_STATUSES = ['published_split', 'split_suppressed', 'no_row']
MONEY = re.compile(r'-?\d+\.\d\d')
EARLIER_PAYMENTS = PROCESSED / '2026-11-19' / 'hcp_medicare_partb_payments_by_year.csv'


def in_filter(npis):
    text = 'filter[a][condition][path]=Rndrng_NPI&filter[a][condition][operator]=IN'
    return text + ''.join(f'&filter[a][condition][value][{i}]={npi}' for i, npi in enumerate(npis))


def collect_by_provider(npis, log=print):
    """year -> (NPI -> record, page records): oncology provider-type queries, then 100-NPI batches for the rest.

    Every universe NPI is covered by one of the two query kinds, so an NPI without a record has no CMS row.
    """
    out = {}
    for year in PAYMENT_YEARS:
        version, found, pages = BY_PROVIDER_VERSIONS[year], {}, []
        for kind in TYPE_QUERIES:
            rows, records = paged(version, {'Rndrng_Prvdr_Type': kind}, BY_PROVIDER_FIELDS)
            pages += records
            found.update({r['Rndrng_NPI']: r for r in rows if r['Rndrng_NPI'] in npis})
        rest = sorted(set(npis) - set(found))
        for start in range(0, len(rest), NPI_BATCH):
            batch = rest[start:start + NPI_BATCH]
            url = (API.format(version=version) + '?' + in_filter(batch) + '&column=' + ','.join(BY_PROVIDER_FIELDS)
                   + f'&size={PAGE}')
            path, record = fetch(url, MODULE, pause=PACE, suffix='.json')
            rows = json.loads(path.read_text())
            assert {r['Rndrng_NPI'] for r in rows} <= set(batch), 'row outside the requested NPIs'
            pages.append(record)
            found.update({r['Rndrng_NPI']: r for r in rows})
        log(f'by_provider {year}: {len(found)} universe NPIs with a row, {len(pages)} requests')
        out[year] = (found, pages)
    return out


def provider_status(record):
    """Status in words of one NPI x year cell, as in hcp_medicare_partb_payments_by_year.csv (2026-11-19)."""
    if record is None:
        return 'no_row'
    suppressed = bool(record['Drug_Sprsn_Ind'].strip() or record['Med_Sprsn_Ind'].strip())
    filled = [bool(record[f].strip()) for f in ('Drug_Mdcr_Pymt_Amt', 'Med_Mdcr_Pymt_Amt')]
    assert filled == [not suppressed] * 2, f"Split and suppression indicators disagree for {record['Rndrng_NPI']}"
    return 'split_suppressed' if suppressed else 'published_split'


def money2(value):
    value = str(value).strip()
    return f'{Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}' if value else ''


def payment_row(npi, year, record, retrieved):
    row = {'npi': npi, 'service_year': str(year), 'by_provider_status': provider_status(record)}
    record = record or {}
    row.update({
        'cms_provider_type': record.get('Rndrng_Prvdr_Type', '').strip(),
        'total_medicare_payment_usd': money2(record.get('Tot_Mdcr_Pymt_Amt', '')),
        'drug_medicare_payment_usd': money2(record.get('Drug_Mdcr_Pymt_Amt', '')),
        'non_drug_medicare_payment_usd': money2(record.get('Med_Mdcr_Pymt_Amt', '')),
        'drug_suppression_indicator': record.get('Drug_Sprsn_Ind', '').strip(),
        'non_drug_suppression_indicator': record.get('Med_Sprsn_Ind', '').strip(),
        'total_beneficiaries': count(record.get('Tot_Benes', '')),
        'drug_beneficiaries': count(record.get('Drug_Tot_Benes', '')),
        'non_drug_beneficiaries': count(record.get('Med_Tot_Benes', '')),
        'source_dataset_version_id': BY_PROVIDER_VERSIONS[year], 'source_release': BY_PROVIDER_RELEASES[year],
        'retrieved_at_utc': retrieved})
    return row


_NO_ROW = 'Blank when by_provider_status is no_row (CMS released no row: 10 or fewer beneficiaries or no Part B claims)'
PAYMENT_COLUMNS = [
    column('npi', 'National Provider Identifier (NPI); every one of the 17,510 NPIs of hcp_divisions.csv (2026-10-24) has one row per year.', 'Never blank.', 'cms'),
    column('service_year', 'Medicare service year, 2017 or 2018 (2019-2024 are in hcp_medicare_partb_payments_by_year.csv of 2026-11-19).', 'Never blank.', 'cms'),
    column('by_provider_status', 'published_split (CMS row with the drug and non-drug split), split_suppressed (row released but the split hidden because one side rests on 1 to 10 beneficiaries) or no_row (no CMS row).', 'Never blank.', 'project'),
    column('cms_provider_type', 'CMS provider type of the NPI in the year.', _NO_ROW + '.', 'cms'),
    column('total_medicare_payment_usd', 'Total Medicare fee-for-service Part B payment to the NPI in the year, US dollars rounded to cents.', _NO_ROW + '.', 'cms'),
    column('drug_medicare_payment_usd', 'Medicare payment for Part B drug codes, US dollars rounded to cents.', _NO_ROW + ', or when split_suppressed.', 'cms'),
    column('non_drug_medicare_payment_usd', 'Medicare payment for all other codes, US dollars rounded to cents.', _NO_ROW + ', or when split_suppressed.', 'cms'),
    column('drug_suppression_indicator', 'CMS indicator on the drug side: * (1 to 10 beneficiaries) or # (hidden so the other side cannot be derived).', 'Blank when CMS sets no indicator or there is no row.', 'cms'),
    column('non_drug_suppression_indicator', 'CMS indicator on the non-drug side, same codes.', 'Blank when CMS sets no indicator or there is no row.', 'cms'),
    column('total_beneficiaries', 'Distinct Medicare fee-for-service beneficiaries of the NPI in the year.', _NO_ROW + '.', 'cms'),
    column('drug_beneficiaries', 'Beneficiaries with a Part B drug code.', _NO_ROW + ', or when split_suppressed.', 'cms'),
    column('non_drug_beneficiaries', 'Beneficiaries with any other code.', _NO_ROW + ', or when split_suppressed.', 'cms'),
    *provenance_columns('Medicare Physician & Other Practitioners - by Provider, one version per service year')]


# ---------- earlier-snapshot keys ----------

def earlier_marker_keys(processed):
    """(npi, year, code, place) already published: treatment_markers_2024.csv and the 2024 drug rows (year 2024)."""
    keys = set()
    for row in read_csv(processed / '2026-10-24' / 'treatment_markers_2024.csv'):
        keys.add((row['npi'], row['service_year'], row['hcpcs_code'], row['place_of_service']))
    for row in read_csv(processed / '2026-11-19' / 'medicare_partb_drug_rows_2024.csv'):
        keys.add((row['npi'], '2024', row['hcpcs_code'], row['place_of_service']))
    return keys


def earlier_oral_keys(processed):
    return {(r['npi'], r['service_year'], r['generic_name'], r['brand_name'])
            for r in read_csv(processed / '2026-10-24' / 'part_d_lymphoma_orals_2024.csv')}


def panel_state_cells(processed):
    """(ccn, year, state) cells of cart_site_volume_panel.csv: every panel CCN for each (state, year) it covers."""
    ccns = {r['ccn'] for r in read_csv(processed / '2026-11-19' / 'cart_site_volume_panel.csv') if r['ccn']}
    return {(ccn, str(year), state) for ccn in ccns for state, year in PANEL_CELLS}


# ---------- headline ----------

def fingerprint(rows):
    """sha256 over a table's cells with column names, rows sorted; any changed, moved or dropped cell shows."""
    import hashlib
    digest = hashlib.sha256()
    for line in sorted('\x1f'.join(f'{k}={v}' for k, v in row.items()) for row in rows):
        digest.update(line.encode() + b'\n')
    return digest.hexdigest()


def cents_total(rows, field):
    """Summed cents of the filled cells; None when no cell is filled (a sum over nothing is not a zero)."""
    values = [to_cents(r[field]) for r in rows if r[field] != '']
    return sum(values) if values else None


def dollars(amount):
    return None if amount is None else f'{Decimal(amount) / 100:.2f}'


def headline(markers, orals, spending, state, groups, geo):
    by_year = {}
    for year in map(str, YEARS):
        m = [r for r in markers if r['service_year'] == year]
        o = [r for r in orals if r['service_year'] == year]
        by_year[year] = {
            'marker_rows': len(m), 'marker_npis': len({r['npi'] for r in m}),
            'marker_estimated_payment_usd': dollars(cents_total(m, 'estimated_medicare_payment_usd')),
            'marker_rows_by_family': dict(sorted(Counter(r['code_family'] for r in m).items())),
            'oral_rows': len(o), 'oral_npis': len({r['npi'] for r in o}),
            'oral_drug_cost_usd': dollars(cents_total(o, 'total_drug_cost_usd')),
            'oral_rows_by_class': dict(sorted(Counter(r['drug_class'] for r in o).items()))}
    division = defaultdict(lambda: defaultdict(int))
    for r in markers:
        division[groups[r['npi']]][r['service_year']] += 1
    return {
        'marker_rows': len(markers), 'oral_rows': len(orals), 'spending_rows': len(spending), 'state_rows': len(state),
        'by_service_year': by_year,
        'marker_rows_by_division_group_and_year': {g: dict(sorted(v.items())) for g, v in sorted(division.items())},
        'oral_beneficiaries_suppressed_rows': sum(r['beneficiaries_status'] != 'beneficiaries_published' for r in orals),
        'spending_published_usd_by_program_year': {
            f"{p}:{y}": dollars(cents_total([r for r in spending if r['program'] == p and r['calendar_year'] == y],
                                            'total_spending_usd'))
            for p in ('medicare_part_b', 'medicaid') for y in map(str, SPENDING_YEARS)},
        'state_shown_discharges_by_source_year_payer': {
            f"{s}:{y}:{p}": sum(int(shown_count(r)) for r in state if (r['source'], r['year'], r['payer_group']) == (s, y, p)
                                and shown_count(r) != '') or None
            for s, y, p in sorted({(r['source'], r['year'], r['payer_group']) for r in state})},
        'state_cells_by_status': dict(sorted(Counter(r['cell_status'] for r in state).items())),
        'geo_rows': len(geo),
        'geo_national_beneficiaries_by_year_code_place': {
            f"{r['service_year']}:{r['hcpcs_code']}:{r['place_of_service']}": int(r['beneficiaries'])
            for r in geo if r['geography_level'] == 'national'},
        'geo_states_with_rows_by_year_code': {
            f"{y}:{c}": len({r['geography_code'] for r in geo if (r['service_year'], r['hcpcs_code']) == (y, c)
                             and r['geography_level'] != 'national'})
            for y, c in sorted({(r['service_year'], r['hcpcs_code']) for r in geo})},
    }


def payment_headline(rows):
    out = {}
    for year in map(str, PAYMENT_YEARS):
        y = [r for r in rows if r['service_year'] == year]
        out[year] = {'rows': len(y), 'by_status': dict(sorted(Counter(r['by_provider_status'] for r in y).items())),
                     **{f: dollars(cents_total(y, f)) for f in ('total_medicare_payment_usd', 'drug_medicare_payment_usd',
                                                                 'non_drug_medicare_payment_usd')}}
    return out


def division_groups(processed):
    return {r['npi']: DIVISION_GROUPS[r['division']] for r in read_csv(processed / '2026-10-24' / 'hcp_divisions.csv')}


# ---------- build ----------

SUPERSEDED = ['hcp_medicare_partb_treatment_markers_2019_2023', 'hcp_medicare_partd_lymphoma_orals_2019_2023']


def remove_superseded():
    """Delete this module's own tables of an earlier build of this snapshot that a renamed table replaces."""
    from .base import INTERIM
    for table in SUPERSEDED:
        for path in (OUT / f'{table}.csv', INTERIM / 'dictionary' / f'{table}.json'):
            if path.exists():
                path.unlink()


def build():
    unsign_index()
    remove_superseded()
    catalog_path, catalog_record = collect_catalog()
    versions = {}
    for dataset in json.loads(catalog_path.read_text())['dataset']:
        for dist in dataset.get('distribution', []):
            if 'data-api/v1/dataset/' in dist.get('accessURL', ''):
                versions[dist['accessURL'].split('/')[-2]] = (dataset['title'], dist.get('modified', ''))
    for year in YEARS:   # the pinned ids and release dates are those of the catalog read on 2026-11-20
        assert versions[SERVICE_VERSIONS[year]] == (f'{SERVICE_TITLE} : {year}-12-{"31" if year == 2023 else "02"}',
                                                     SERVICE_RELEASES[year]), year
        assert versions[PART_D_VERSIONS[year]][0].startswith(PART_D_TITLE) and \
            versions[PART_D_VERSIONS[year]][1] == PART_D_RELEASES[year], year
    for version in (PARTB_SPENDING_VERSION, MEDICAID_SPENDING_VERSION):
        assert versions[version][1] == SPENDING_RELEASE
    for year, version in BY_PROVIDER_VERSIONS.items():
        assert versions[version][0].startswith(f'{BY_PROVIDER_TITLE} : {year}-') and \
            versions[version][1] == BY_PROVIDER_RELEASES[year], year
    hcps = universe()
    service, part_d, spending_raw = collect_service(lambda _: None), collect_part_d(lambda _: None), collect_spending()
    code_years, marker_candidates = {}, []
    for (year, code), (records, pages) in service.items():
        code_years[f'{year}:{code}'] = len(records)
        retrieved = max(p['retrieved_at_utc'] for p in pages)
        marker_candidates += [marker_row(r, year, retrieved) for r in records if r['Rndrng_NPI'] in hcps]
    drug_years, oral_candidates = {}, []
    for (year, generic), (records, pages) in part_d.items():
        drug_years[f'{year}:{generic}'] = len(records)
        retrieved = max(p['retrieved_at_utc'] for p in pages)
        oral_candidates += [oral_row(r, year, retrieved) for r in records if r['Prscrbr_NPI'] in hcps]
    spending_candidates = []
    for program, version in (('medicare_part_b', PARTB_SPENDING_VERSION), ('medicaid', MEDICAID_SPENDING_VERSION)):
        records, pages = spending_raw[program]
        spending_candidates += spending_rows(program, records, max(p['retrieved_at_utc'] for p in pages), version)
    state_candidates, state_meta = build_state(print)
    geo_candidates, geo_years = [], {}
    for (year, code), (records, pages) in collect_geo(lambda _: None).items():
        geo_years[f'{year}:{code}'] = len(records)
        retrieved = max(p['retrieved_at_utc'] for p in pages)
        geo_candidates += [geo_row(r, year, retrieved) for r in records]
    geo, g_drop = new_rows(geo_candidates, GEO_KEY, set())
    fetch(HCPCS_PAGE, MODULE, pause=3, suffix='.html')
    hcpcs_paths = {year: fetch(url, MODULE, pause=3, suffix='.zip')[0] for year, url in HCPCS_FILES.items()}
    national = {key: len(records) for key, (records, _) in service.items()}
    retrieved_by = {key: max(p['retrieved_at_utc'] for p in pages) for key, (_, pages) in service.items()}
    earlier_m, earlier_o = earlier_marker_keys(PROCESSED), earlier_oral_keys(PROCESSED)
    panel = panel_state_cells(PROCESSED)
    markers, m_drop = new_rows(marker_candidates, MARKER_KEY, earlier_m)
    m_earlier = sum(tuple(r[k] for k in MARKER_KEY) in earlier_m for r in marker_candidates)
    orals, o_drop = new_rows(oral_candidates, ORAL_KEY, earlier_o)
    o_earlier = sum(tuple(r[k] for k in ORAL_KEY) in earlier_o for r in oral_candidates)
    spending, s_drop = new_rows(spending_candidates, SPENDING_KEY, set())
    state_kept = [r for r in state_candidates if (r['ccn'], r['year'], r['state']) not in panel]
    st_earlier = len(state_candidates) - len(state_kept)
    state, st_drop = new_rows(state_kept, STATE_KEY, set())
    validity_candidates = validity_rows(hcpcs_dates(hcpcs_paths), national, markers, retrieved_by)
    validity, v_drop = new_rows(validity_candidates, VALIDITY_KEY, set())
    payments, p_candidates, p_earlier, p_drop = [], [], 0, 0
    if INCLUDE_PAYMENTS:
        for year, (found, pages) in collect_by_provider(set(hcps), lambda _: None).items():
            retrieved = max(p['retrieved_at_utc'] for p in pages)
            p_candidates += [payment_row(npi, year, found.get(npi), retrieved) for npi in sorted(hcps)]
        earlier_p = {(r['npi'], r['service_year']) for r in read_csv(EARLIER_PAYMENTS)}
        p_earlier = sum((r['npi'], r['service_year']) in earlier_p for r in p_candidates)
        payments, p_drop = new_rows(p_candidates, PAYMENT_KEY, earlier_p)
    tables = [publish(MARKERS, sorted(markers, key=lambda r: tuple(r[k] for k in MARKER_KEY)), MARKER_COLUMNS, MARKER_KEY, PROVENANCE),
              publish(ORALS, sorted(orals, key=lambda r: tuple(r[k] for k in ORAL_KEY)), ORAL_COLUMNS, ORAL_KEY, PROVENANCE),
              publish(SPENDING, spending, SPENDING_COLUMNS, SPENDING_KEY, PROVENANCE),
              publish(STATE, sorted(state, key=lambda r: tuple(r[k] for k in STATE_KEY)), STATE_COLUMNS, STATE_KEY,
                      ['source', 'source_release', 'retrieved_at_utc']),
              publish(GEO, sorted(geo, key=lambda r: tuple(r[k] for k in GEO_KEY)), GEO_COLUMNS, GEO_KEY, PROVENANCE),
              publish(VALIDITY, validity, VALIDITY_COLUMNS, VALIDITY_KEY, PROVENANCE)]
    if INCLUDE_PAYMENTS:
        tables.append(publish(PAYMENTS, payments, PAYMENT_COLUMNS, PAYMENT_KEY, PROVENANCE))
    log = request_log(MODULE)
    times = lambda pred: sorted(r['retrieved_at_utc'] for r in log if pred(r))
    window = lambda pred: {'retrieved_from_utc': (times(pred) or [''])[0], 'retrieved_to_utc': (times(pred) or [''])[-1],
                           'requests': len(times(pred))}
    cms = lambda version: (lambda r: version in r.get('source_url', ''))
    sources = [
        {'name': f'{SERVICE_TITLE} {y}', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': API.format(version=v),
         'release_or_version': f'{v} (modified {SERVICE_RELEASES[y]})', 'how_obtained': 'national per-code exact-value queries, 5,000-row pages, ten seconds apart; filtered to the universe here',
         'terms_class': 'a: US government public data', **window(cms(v))} for y, v in SERVICE_VERSIONS.items()] + [
        {'name': f'{PART_D_TITLE} {y}', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': API.format(version=v),
         'release_or_version': f'{v} (modified {PART_D_RELEASES[y]})', 'how_obtained': 'national per-generic exact-value queries; filtered to the universe here',
         'terms_class': 'a: US government public data', **window(cms(v))} for y, v in PART_D_VERSIONS.items()] + [
        {'name': f'{GEO_TITLE} {y}', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': API.format(version=v),
         'release_or_version': f'{v} (modified {GEO_RELEASES[y]})', 'how_obtained': 'per-code exact-value queries (national and state rows)',
         'terms_class': 'a: US government public data', **window(cms(v))} for y, v in GEO_VERSIONS.items()] + [
        {'name': t, 'publisher': 'Centers for Medicare & Medicaid Services', 'url': API.format(version=v),
         'release_or_version': f'{v} (modified {SPENDING_RELEASE})', 'how_obtained': 'whole file in 5,000-row pages',
         'terms_class': 'a: US government public data', **window(cms(v))}
        for t, v in ((PARTB_SPENDING_TITLE, PARTB_SPENDING_VERSION), (MEDICAID_SPENDING_TITLE, MEDICAID_SPENDING_VERSION))] + [
        {'name': 'Alpha-numeric HCPCS files, January 2021-2025 (Level II code added and termination dates)',
         'publisher': 'Centers for Medicare & Medicaid Services', 'url': HCPCS_PAGE,
         'release_or_version': '; '.join(HCPCS_FILES.values()), 'how_obtained': 'quarterly-update page plus five January zip files; only code numbers and dates are used',
         'terms_class': 'a: US government public data (Level I CPT codes are not read)',
         **window(lambda r: r.get('source_url') in set(HCPCS_FILES.values()) | {HCPCS_PAGE})},
        *[{'name': f'{BY_PROVIDER_TITLE} {y}', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': API.format(version=v),
           'release_or_version': f'{v} (modified {BY_PROVIDER_RELEASES[y]})',
           'how_obtained': 'provider-type queries (Hematology-Oncology, Medical Oncology, Hematology) plus 100-NPI IN-filter batches for the other universe NPIs',
           'terms_class': 'a: US government public data', **window(cms(v))} for y, v in BY_PROVIDER_VERSIONS.items() if INCLUDE_PAYMENTS],
        {'name': 'data.cms.gov catalog', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': CATALOG,
         'release_or_version': catalog_record['retrieved_at_utc'], 'how_obtained': 'one GET; used to pin version ids',
         'terms_class': 'a: US government public data', **window(lambda r: r.get('source_url') == CATALOG)},
        {'name': 'Top 25 MS-DRGs Individual Hospital Pivot Profile 2021-2023', 'publisher': 'California Department of Health Care Access and Information',
         'url': 'https://data.chhs.ca.gov/dataset/top-25-ms-drgs-individual-hospital-pivot-profile',
         'release_or_version': '; '.join(f'{y}: {n} ({d})' for y, (_, n, d) in CA_FILES.items()),
         'how_obtained': 'portal package listing plus three workbook downloads (redirect to signed storage read without following, then fetched)',
         'terms_class': 'a: Creative Commons Attribution per the portal; attribution column on every row',
         'retrieved_from_utc': min(r['retrieved_at_utc'] for r in state_meta['ca_records']),
         'retrieved_to_utc': max(r['retrieved_at_utc'] for r in state_meta['ca_records']), 'requests': len(state_meta['ca_records']) + 3},
        {'name': 'FloridaHealthFinder inpatient query tool, MS-DRG 018, 2023-2025', 'publisher': 'Florida Agency for Health Care Administration',
         'url': FL_TOOL, 'release_or_version': 'tool data as served on 2026-11-19',
         'how_obtained': ('queries to the public tool were made by the earlier search on 2026-11-19, which saved eight CSV outputs '
                          '(research_notes probes, gap-state-allpayer-remaining-and-npi-terms/florida); they are logged here as local '
                          'inputs with their hashes and were not re-queried on 2026-11-20. The display rule was read from the site '
                          f'disclaimer {FL_DISCLAIMER} on 2026-11-20 (one request): facilities with "fewer than 30 cases will be '
                          'denoted with N/A", and facilities with zero cases are excluded from results.'),
         'saved_files_sha256': {Path(r['local_path']).name: r['sha256'] for r in state_meta['fl_records']},
         'disclaimer_retrieved_at_utc': state_meta['fl_disclaimer']['retrieved_at_utc'],
         'terms_class': 'c: copyright notice and no reuse grant; only shown counts with attribution and the query parameters are published',
         'retrieved_from_utc': min(r['retrieved_at_utc'] for r in state_meta['fl_records']),
         'retrieved_to_utc': max(r['retrieved_at_utc'] for r in state_meta['fl_records']), 'requests': len(state_meta['fl_records'])}]
    head = headline(markers, orals, spending, state, division_groups(PROCESSED), geo)
    if INCLUDE_PAYMENTS:
        head['payments'] = payment_headline(payments)
    head.update({'national_rows_by_year_and_code': code_years, 'national_rows_by_year_and_generic': drug_years,
                 'california_statewide_msdrg018': state_meta['ca_statewide'], 'florida_statewide_msdrg018': state_meta['fl_statewide'],
                 'california_facilities_with_drg018_not_mapped_to_ccn': state_meta['unmapped_ca'],
                 'geography_rows_by_year_and_code': geo_years})
    write_module_metadata(MODULE, {
        'tables': tables, 'sources': sources, 'headline': head,
        'dedup': {MARKERS: {'candidate_rows': len(marker_candidates), 'dropped_already_published': m_earlier,
                            'dropped_internal_duplicates': m_drop - m_earlier},
                  ORALS: {'candidate_rows': len(oral_candidates), 'dropped_already_published': o_earlier,
                          'dropped_internal_duplicates': o_drop - o_earlier},
                  SPENDING: {'candidate_rows': len(spending_candidates), 'dropped_already_published': 0,
                             'dropped_internal_duplicates': s_drop},
                  STATE: {'candidate_rows': len(state_candidates), 'dropped_already_published': st_earlier,
                          'dropped_internal_duplicates': st_drop},
                  GEO: {'candidate_rows': len(geo_candidates), 'dropped_already_published': 0,
                        'dropped_internal_duplicates': g_drop},
                  VALIDITY: {'candidate_rows': len(validity_candidates), 'dropped_already_published': 0,
                             'dropped_internal_duplicates': v_drop},
                  **({PAYMENTS: {'candidate_rows': len(p_candidates), 'dropped_already_published': p_earlier,
                                 'dropped_internal_duplicates': p_drop - p_earlier}} if INCLUDE_PAYMENTS else {})},
        'fingerprints': {t: fingerprint(read_csv(OUT / f'{t}.csv')) for t in TABLES},
        'limitations': LIMITATIONS, 'defects_open': DEFECTS,
        'helpers_outside_scaffold': ['fetch_portal_file: reads the California portal redirect without following it, drops the explicit :443 port of the signed storage address, then fetches through base.fetch',
                                     'xlsx_rows: standard-library reader of one worksheet (openpyxl is not installed)']})
    print(f"{MODULE}: {len(markers)} Part B marker rows, {len(orals)} Part D rows, {len(spending)} spending rows, "
          f"{len(state)} state cells, {len(geo)} geography rows, {len(validity)} code-year validity rows; "
          f"{len(log)} requests logged")
    return tables


LIMITATIONS = [
    'CMS releases a by-Provider-and-Service or Part D row only when it rests on 11 or more beneficiaries (Part D: 11 or more claims); absent rows are not zeros.',
    'Code validity by year is in lymphoma_product_code_validity.csv, from the January 2021-2025 CMS Level II HCPCS files (the January 2019 and 2020 files were not found at the CMS address pattern; every queried code is listed in a later file with its added and termination dates). For 2017-2018 rituximab was billed under J9310 (terminated 2018-12-31) and rituximab with hyaluronidase under C9467 (2018-04 to 2018-12); both are queried for 2017-2023. Product-specific temporary C codes found and queried: C9042 (bendamustine, 2019-04 to 2019-06), C9070 (tafasitamab, 2021-01 to 2021-03), C9084 (loncastuximab, 2021-10 to 2022-03), C9155 (epcoritamab, 2023-10 to 2023-12). No product-specific code was found for polatuzumab before J9309 (2020-01-01), for mosunetuzumab before J9350 (2023-07-01) or for glofitamab before J9286 (2024-01-01); those products were billed under miscellaneous codes, which are not attributed to any product. Temporary C codes are hospital outpatient codes and are rarely on clinician claims.',
    'Medicare Part B Spending by Drug covers Part B claims only; CAR-T given in an inpatient stay is paid under Part A and is not in it, so the Part B CAR-T figures are a small outpatient share. The 2024 release carries 2020-2024; 2019 is not included.',
    'Florida cells under 30 are not displayed (the site disclaimer: fewer than 30 cases shown as N/A, zero cases excluded; the disclaimer is written for the facility pages and is read here as also governing the query tool, whose smallest shown cell is 30); the payer split covers five payer groups only, and MS-DRG 018 includes non-CAR-T immunotherapies. Florida values come from the search\'s saved tool output of 2026-11-19, not a fresh query.',
    'Geography and Service: 0537T, 0538T and 0539T have no national or state row in any year 2019-2024 (below the 11-beneficiary rule or not billed separately); 0540T state rows exist only for 5 to 14 states a year, so state rows do not sum to the national row. No 2025 service year is released, so 38225-38228 were not queried.',
    'Spending by drug: Tecartus and Aucatzyl are not in the Part B file; Abecma, Aucatzyl are absent from Medicaid; Medicaid shows no CAR-T spending in 2020.',
    'California lists a hospital only when MS-DRG 018 is in one of its top-25 tables; one facility (106341051) with 1 discharge in 2022 and 2023 has no hand-mapped CCN and is left out.',
]
DEFECTS = []


# ---------- check ----------

def check(processed):
    processed = processed if hasattr(processed, 'joinpath') else __import__('pathlib').Path(processed)
    here = processed / SNAPSHOT
    load = lambda table: read_csv(here / f'{table}.csv')
    markers, orals, spending, state, geo = load(MARKERS), load(ORALS), load(SPENDING), load(STATE), load(GEO)
    validity = load(VALIDITY)
    universe_rows = Counter((r['hcpcs_code'], r['service_year']) for r in markers)
    hcps = {r['npi'] for r in read_csv(processed / '2026-10-24' / 'hcp_divisions.csv')}
    centers_set = {r['ccn'] for r in read_csv(processed / '2026-10-24' / 'cart_centers.csv')}
    dup = lambda rows, key: len(rows) - len({tuple(r[k] for k in key) for r in rows})
    earlier_m, earlier_o, panel = earlier_marker_keys(processed), earlier_oral_keys(processed), panel_state_cells(processed)
    blank = lambda r, cols: any(r[c] == '' for c in cols)
    out = {
        'payer_markers_duplicate_keys': dup(markers, MARKER_KEY),
        'payer_orals_duplicate_keys': dup(orals, ORAL_KEY),
        'payer_spending_duplicate_keys': dup(spending, SPENDING_KEY),
        'payer_state_duplicate_keys': dup(state, STATE_KEY),
        'payer_markers_overlap_earlier_snapshots': sum(tuple(r[k] for k in MARKER_KEY) in earlier_m for r in markers),
        'payer_orals_overlap_earlier_snapshots': sum(tuple(r[k] for k in ORAL_KEY) in earlier_o for r in orals),
        'payer_state_overlap_volume_panel': sum((r['ccn'], r['year'], r['state']) in panel for r in state),
        'payer_markers_npi_not_in_universe': sum(r['npi'] not in hcps for r in markers),
        'payer_orals_npi_not_in_universe': sum(r['npi'] not in hcps for r in orals),
        'payer_markers_year_or_version': sum(r['service_year'] not in map(str, YEARS)
                                             or r['source_dataset_version_id'] != SERVICE_VERSIONS[int(r['service_year'])]
                                             or r['source_release'] != SERVICE_RELEASES[int(r['service_year'])] for r in markers),
        'payer_orals_year_or_version': sum(r['service_year'] not in map(str, YEARS)
                                           or r['source_dataset_version_id'] != PART_D_VERSIONS[int(r['service_year'])]
                                           or r['source_release'] != PART_D_RELEASES[int(r['service_year'])] for r in orals),
        'payer_markers_family_label': sum(CODE_FAMILIES.get(r['hcpcs_code']) != r['code_family'] for r in markers),
        'payer_markers_place_of_service': sum(r['place_of_service'] not in ('F', 'O') for r in markers),
        'payer_markers_beneficiaries_below_11_or_blank': sum(r['beneficiaries'] == '' or int(r['beneficiaries']) < 11 for r in markers),
        'payer_markers_blank_value': sum(blank(r, [c['column'] for c in MARKER_COLUMNS]) for r in markers),
        'payer_markers_estimate_recomputed': sum(estimate(r['average_medicare_payment_usd'], r['services_or_units'])
                                                 != r['estimated_medicare_payment_usd'] for r in markers),
        'payer_orals_drug_class': sum(DRUG_CLASSES.get(r['generic_name']) != r['drug_class'] for r in orals),
        'payer_orals_beneficiaries_below_11': sum(r['total_beneficiaries'] != '' and int(r['total_beneficiaries']) < 11 for r in orals),
        'payer_orals_claims_below_11': sum(int(r['total_claims']) < 11 for r in orals),
        'payer_orals_status_consistency': sum((r['total_beneficiaries'] == '') != (r['beneficiaries_status'] == 'beneficiaries_suppressed_1_to_10')
                                              or r['beneficiaries_status'] not in ORAL_STATUSES for r in orals),
        'payer_orals_blank_value': sum(blank(r, [c['column'] for c in ORAL_COLUMNS if c['column'] != 'total_beneficiaries']) for r in orals),
        'payer_spending_status_consistency': sum(
            r['value_status'] not in SPENDING_STATUSES or (r['total_spending_usd'] != '') != (r['value_status'] == 'published')
            or (r['program'] == 'medicaid') != (r['beneficiaries_status'] == 'not_reported_by_program')
            or (r['total_beneficiaries'] != '') != (r['beneficiaries_status'] == 'published')
            or r['total_spending_usd'] in ('0', '0.00') or r['brand_name'].upper() not in CART_BRANDS for r in spending),
        'payer_spending_beneficiaries_below_11': sum(r['total_beneficiaries'] != '' and int(r['total_beneficiaries']) < 11 for r in spending),
        'payer_spending_grid_complete': abs(len(spending) - len(CART_BRANDS) * 2 * len(SPENDING_YEARS)),
        'payer_state_status_consistency': sum(
            r['cell_status'] not in STATE_STATUSES or (shown_count(r) == '') == (r['cell_status'] == 'shown')
            or shown_count(r) == '0' or (r['ca_hcai_discharges'] if r['source'] == FL_SOURCE else r['fl_ahca_discharges']) != ''
            or (r['cell_status'] == 'not_shown_below_display_threshold') != (shown_count(r) == '' and r['source'] == FL_SOURCE)
            for r in state),
        'payer_state_florida_shown_below_threshold': sum(r['source'] == FL_SOURCE and shown_count(r) != ''
                                                         and int(shown_count(r)) < FL_THRESHOLD for r in state),
        'payer_state_center_flag': sum((r['in_cart_center_set'] == '1') != (r['ccn'] in centers_set) for r in state),
        'payer_state_ccn_format': sum(len(r['ccn']) != 6 for r in state),
        'payer_state_years': sum((r['source'], int(r['year'])) not in {(CA_SOURCE, y) for y in CA_FILES} | {(FL_SOURCE, y) for y in FL_YEARS}
                                 or SOURCE_STATE[r['source']] != r['state'] for r in state),
        'payer_state_florida_grid_complete': sum(
            len([r for r in state if r['source'] == FL_SOURCE and r['year'] == str(y) and r['payer_group'] == p])
            != len([r for r in state if r['source'] == FL_SOURCE and r['year'] == str(y) and r['payer_group'] == 'all_payer'])
            for y in FL_YEARS for p in FL_PAYERS.values()),
        'payer_geo_duplicate_keys': dup(geo, GEO_KEY),
        'payer_geo_year_version_code': sum(r['service_year'] not in map(str, GEO_VERSIONS)
                                           or r['source_dataset_version_id'] != GEO_VERSIONS[int(r['service_year'])]
                                           or r['source_release'] != GEO_RELEASES[int(r['service_year'])]
                                           or GEO_CODES.get(r['hcpcs_code']) != r['code_step'] for r in geo),
        'payer_geo_beneficiaries_below_11_or_blank': sum(r['beneficiaries'] == '' or int(r['beneficiaries']) < 11 for r in geo),
        'payer_geo_blank_value': sum(blank(r, [c['column'] for c in GEO_COLUMNS]) for r in geo),
        'payer_validity_duplicate_keys': dup(validity, VALIDITY_KEY),
        'payer_validity_grid_complete': abs(len(validity) - len([c for c in CODE_FAMILIES if c not in ADMIN_CODES]) * len(YEARS)),
        'payer_validity_recomputed': sum(
            r['validity_in_year'] != validity_status(r['valid_from_date'].replace('-', ''), r['valid_to_date'].replace('-', ''), r['service_year'])
            or CODE_FAMILIES.get(r['hcpcs_code']) != r['code_family'] or r['hcpcs_code'] in ('J9999', 'J3490', 'J3590', 'C9399')
            or r['universe_rows'] != str(universe_rows[(r['hcpcs_code'], r['service_year'])])
            or int(r['universe_rows']) > int(r['national_rows_in_pinned_file'])
            or (r['validity_in_year'] == 'not_valid' and r['national_rows_in_pinned_file'] != '0')
            or r['source_dataset_version_id'] != SERVICE_VERSIONS.get(int(r['service_year'])) for r in validity),
        'payer_markers_code_valid_in_year': sum(
            (r['hcpcs_code'], r['service_year']) in {(v['hcpcs_code'], v['service_year']) for v in validity if v['validity_in_year'] == 'not_valid'}
            for r in markers),
        'payer_no_descriptor_or_name_columns': sum(any(c in rows[0] for c in ('hcpcs_description', 'HCPCS_Desc', 'last_name', 'first_name', 'division'))
                                                    for rows in (markers, orals, spending, state, geo, validity) if rows),
    }
    if INCLUDE_PAYMENTS:
        pay = load(PAYMENTS)
        earlier_p = {(r['npi'], r['service_year']) for r in read_csv(processed / '2026-11-19' / 'hcp_medicare_partb_payments_by_year.csv')}
        money_ok = lambda v: v == '' or (MONEY.fullmatch(v) is not None)
        out.update({
            'payer_payments_duplicate_keys': dup(pay, PAYMENT_KEY),
            'payer_payments_overlap_earlier_snapshots': sum((r['npi'], r['service_year']) in earlier_p for r in pay),
            'payer_payments_grid_complete': sum(len({r['npi'] for r in pay if r['service_year'] == str(y)} ^ hcps) for y in PAYMENT_YEARS)
                                            + abs(len(pay) - len(hcps) * len(PAYMENT_YEARS)),
            'payer_payments_year_or_version': sum(r['service_year'] not in map(str, PAYMENT_YEARS)
                                                  or r['source_dataset_version_id'] != BY_PROVIDER_VERSIONS[int(r['service_year'])] for r in pay),
            'payer_payments_status_consistency': sum(
                r['by_provider_status'] not in PAYMENT_STATUSES
                or (r['by_provider_status'] == 'no_row') != (r['total_medicare_payment_usd'] == '')
                or (r['by_provider_status'] == 'published_split') != (r['drug_medicare_payment_usd'] != '' and r['non_drug_medicare_payment_usd'] != '')
                or (r['by_provider_status'] == 'split_suppressed') != bool(r['drug_suppression_indicator'] or r['non_drug_suppression_indicator'])
                or (r['by_provider_status'] == 'no_row' and any(r[c] for c in ('cms_provider_type', 'total_beneficiaries')))
                or not all(money_ok(r[c]) for c in ('total_medicare_payment_usd', 'drug_medicare_payment_usd', 'non_drug_medicare_payment_usd'))
                for r in pay),
            'payer_payments_split_sums_to_total': sum(
                r['by_provider_status'] == 'published_split' and abs(to_cents(r['drug_medicare_payment_usd']) + to_cents(r['non_drug_medicare_payment_usd'])
                                                                   - to_cents(r['total_medicare_payment_usd'])) > 2 for r in pay),
            'payer_payments_beneficiaries_below_11': sum(r['total_beneficiaries'] != '' and int(r['total_beneficiaries']) < 11 for r in pay)})
        tables_rows = ((PAYMENTS, pay),)
    else:
        tables_rows = ()
    out['payer_table_fingerprints'] = sum(fingerprint(rows) != TABLE_SHA256.get(table) for table, rows in
                                          ((MARKERS, markers), (ORALS, orals), (SPENDING, spending), (STATE, state),
                                           (GEO, geo), (VALIDITY, validity)) + tables_rows) if TABLE_SHA256 else 6
    try:
        actual = headline(markers, orals, spending, state, division_groups(processed), geo)
        if INCLUDE_PAYMENTS:
            actual['payments'] = payment_headline(pay)
        out['payer_headline_recomputed'] = sum(actual.get(k) != v for k, v in EXPECTED_HEADLINE.items()) if EXPECTED_HEADLINE else 1
    except KeyError:
        out['payer_headline_recomputed'] = 1
    return out


# Headline of the first build (2026-11-20), frozen: check() recomputes it from the published rows.
# sha256 of each published table's cells (see `fingerprint`) and the headline, frozen from the build: they catch a
# changed, moved or dropped cell that nothing else can recompute. A rebuild that changes a cell must update them.
TABLE_SHA256 = {
    'hcp_medicare_partb_treatment_markers_2017_2023': 'a607333c422418f89fa579930ed2286d4a35ad4fabb37490ec731f07d30739a0',
    'hcp_medicare_partd_lymphoma_orals_2017_2023': '94ecc881ead59c9726d4b7e71c393d9b4564dd0d281c80e634102df4a5b044d2',
    'cart_product_program_spending_by_year': '79c2790375cdc447139a51c07afa032ca3c6fbadfdfd292cdf2e6dfc9fb2418c',
    'state_cart_hospital_stays_by_payer': '1dec1803e161065648572c9e1fa2141ac08b06f0ee2a75704bebb992ffee9d22',
    'state_medicare_cart_administration_by_year': '97bb10f2f93016477438111dcbe9b3135e3cf5969bcb9074d04b95226ff09de3',
    'lymphoma_product_code_validity': '1885b3562784b49ac6ce17383a2a7184c33df4e7a793789e9d96178756cb598b',
    'hcp_medicare_partb_payments_by_year_2017_2018': 'b0595768bdd1cdfd33a63ed2797d01d54d61e2213f4b8e066f5a77f6efdddc14',
}
EXPECTED_HEADLINE = json.loads(r'''{"by_service_year": {"2017": {"marker_estimated_payment_usd": "419794015.00", "marker_npis": 3584, "marker_rows": 10561, "marker_rows_by_family": {"bendamustine": 61, "chemo_administration": 9336, "obinutuzumab": 4, "rituximab_intravenous": 1160}, "oral_drug_cost_usd": "3203549156.05", "oral_npis": 5529, "oral_rows": 7905, "oral_rows_by_class": {"btk_inhibitor": 3077, "lenalidomide": 4749, "venetoclax": 79}}, "2018": {"marker_estimated_payment_usd": "424138485.32", "marker_npis": 3575, "marker_rows": 10514, "marker_rows_by_family": {"bendamustine": 73, "chemo_administration": 9319, "obinutuzumab": 2, "rituximab_intravenous": 1120}, "oral_drug_cost_usd": "4202178342.97", "oral_npis": 6064, "oral_rows": 9111, "oral_rows_by_class": {"btk_inhibitor": 3705, "lenalidomide": 5124, "venetoclax": 282}}, "2019": {"marker_estimated_payment_usd": "401292491.34", "marker_npis": 3604, "marker_rows": 10493, "marker_rows_by_family": {"bendamustine": 60, "chemo_administration": 9401, "rituximab_intravenous": 984, "rituximab_subcutaneous": 48}, "oral_drug_cost_usd": "5272263067.62", "oral_npis": 6560, "oral_rows": 10814, "oral_rows_by_class": {"btk_inhibitor": 4550, "lenalidomide": 5399, "venetoclax": 865}}, "2020": {"marker_estimated_payment_usd": "277538452.95", "marker_npis": 3559, "marker_rows": 9798, "marker_rows_by_family": {"bendamustine": 36, "chemo_administration": 9155, "obinutuzumab": 4, "rituximab_intravenous": 577, "rituximab_subcutaneous": 26}, "oral_drug_cost_usd": "6545337888.19", "oral_npis": 6974, "oral_rows": 12642, "oral_rows_by_class": {"btk_inhibitor": 5456, "lenalidomide": 5679, "tazemetostat": 1, "venetoclax": 1506}}, "2021": {"marker_estimated_payment_usd": "239918858.03", "marker_npis": 3532, "marker_rows": 9681, "marker_rows_by_family": {"bendamustine": 11, "chemo_administration": 9116, "obinutuzumab": 2, "rituximab_intravenous": 536, "rituximab_subcutaneous": 16}, "oral_drug_cost_usd": "7566880488.46", "oral_npis": 7237, "oral_rows": 14055, "oral_rows_by_class": {"btk_inhibitor": 6447, "lenalidomide": 5777, "tazemetostat": 8, "venetoclax": 1823}}, "2022": {"marker_estimated_payment_usd": "197450016.03", "marker_npis": 3603, "marker_rows": 9703, "marker_rows_by_family": {"bendamustine": 19, "chemo_administration": 9144, "obinutuzumab": 1, "rituximab_intravenous": 533, "rituximab_subcutaneous": 6}, "oral_drug_cost_usd": "7849962233.57", "oral_npis": 7310, "oral_rows": 15412, "oral_rows_by_class": {"btk_inhibitor": 7051, "lenalidomide": 6335, "tazemetostat": 8, "venetoclax": 2018}}, "2023": {"marker_estimated_payment_usd": "157941063.47", "marker_npis": 3672, "marker_rows": 9706, "marker_rows_by_family": {"bendamustine": 8, "chemo_administration": 9246, "obinutuzumab": 1, "rituximab_intravenous": 446, "rituximab_subcutaneous": 5}, "oral_drug_cost_usd": "7214847564.05", "oral_npis": 7223, "oral_rows": 17138, "oral_rows_by_class": {"btk_inhibitor": 7769, "lenalidomide": 7228, "tazemetostat": 14, "venetoclax": 2127}}}, "geo_national_beneficiaries_by_year_code_place": {"2019:0540T:F": 157, "2020:0540T:F": 212, "2021:0540T:F": 234, "2022:0540T:F": 266, "2023:0540T:F": 544, "2023:0540T:O": 17, "2024:0540T:F": 569, "2024:0540T:O": 34}, "geo_rows": 56, "geo_states_with_rows_by_year_code": {"2019:0540T": 5, "2020:0540T": 5, "2021:0540T": 6, "2022:0540T": 7, "2023:0540T": 10, "2024:0540T": 14}, "marker_rows": 70456, "marker_rows_by_division_group_and_year": {"affiliates": {"2017": 3919, "2018": 3902, "2019": 3904, "2020": 3640, "2021": 3571, "2022": 3618, "2023": 3699}, "core": {"2017": 72, "2018": 85, "2019": 87, "2020": 88, "2021": 86, "2022": 92, "2023": 100}, "other": {"2017": 677, "2018": 579, "2019": 509, "2020": 436, "2021": 395, "2022": 299, "2023": 152}, "referring": {"2017": 5893, "2018": 5948, "2019": 5993, "2020": 5634, "2021": 5629, "2022": 5694, "2023": 5755}}, "oral_beneficiaries_suppressed_rows": 79216, "oral_rows": 87077, "payments": {"2017": {"by_status": {"no_row": 6225, "published_split": 10394, "split_suppressed": 891}, "drug_medicare_payment_usd": "4820288800.94", "non_drug_medicare_payment_usd": "1633599125.10", "rows": 17510, "total_medicare_payment_usd": "6532534960.59"}, "2018": {"by_status": {"no_row": 5703, "published_split": 10867, "split_suppressed": 940}, "drug_medicare_payment_usd": "5574144402.58", "non_drug_medicare_payment_usd": "1678798986.88", "rows": 17510, "total_medicare_payment_usd": "7341436017.36"}}, "spending_published_usd_by_program_year": {"medicaid:2020": null, "medicaid:2021": "11270638.89", "medicaid:2022": "20626626.20", "medicaid:2023": "37186202.36", "medicaid:2024": "44892028.35", "medicare_part_b:2020": "14582027.50", "medicare_part_b:2021": "16803463.06", "medicare_part_b:2022": "32965803.68", "medicare_part_b:2023": "84921780.96", "medicare_part_b:2024": "204056714.21"}, "spending_rows": 70, "state_cells_by_status": {"drg018_not_in_top25_tables": 19, "not_shown_below_display_threshold": 155, "shown": 62}, "state_rows": 236, "state_shown_discharges_by_source_year_payer": {"ca_hcai_top25_msdrg_hospital_profile:2021:all_payer": 446, "ca_hcai_top25_msdrg_hospital_profile:2022:all_payer": 667, "ca_hcai_top25_msdrg_hospital_profile:2023:all_payer": 741, "fl_ahca_inpatient_query_tool:2023:all_payer": 387, "fl_ahca_inpatient_query_tool:2023:commercial": 104, "fl_ahca_inpatient_query_tool:2023:medicaid": null, "fl_ahca_inpatient_query_tool:2023:medicaid_managed_care": null, "fl_ahca_inpatient_query_tool:2023:medicare_ffs": 142, "fl_ahca_inpatient_query_tool:2023:medicare_managed_care": 67, "fl_ahca_inpatient_query_tool:2024:all_payer": 394, "fl_ahca_inpatient_query_tool:2024:commercial": 141, "fl_ahca_inpatient_query_tool:2024:medicaid": null, "fl_ahca_inpatient_query_tool:2024:medicaid_managed_care": null, "fl_ahca_inpatient_query_tool:2024:medicare_ffs": 108, "fl_ahca_inpatient_query_tool:2024:medicare_managed_care": 39, "fl_ahca_inpatient_query_tool:2025:all_payer": 430, "fl_ahca_inpatient_query_tool:2025:commercial": 172, "fl_ahca_inpatient_query_tool:2025:medicaid": null, "fl_ahca_inpatient_query_tool:2025:medicaid_managed_care": null, "fl_ahca_inpatient_query_tool:2025:medicare_ffs": 114, "fl_ahca_inpatient_query_tool:2025:medicare_managed_care": null}}''')
INCLUDE_PAYMENTS = True   # set once every 2017-2018 by-Provider request is frozen; never a partial table
if INCLUDE_PAYMENTS:
    TABLES.append(PAYMENTS)


if __name__ == '__main__':
    if sys.argv[1:] == ['collect']:
        collect_all(lambda line: print(line, flush=True))
    else:
        build()
