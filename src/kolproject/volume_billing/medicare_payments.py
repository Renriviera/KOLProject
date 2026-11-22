"""Medicare fee-for-service Part B payments to the 17,510-NPI universe, service years 2019-2024.

Three tables are built from data.cms.gov responses (Medicare Physician & Other Practitioners files):
a complete NPI x service year grid of by-Provider totals with the drug and non-drug split, the 2024
NPI x code drug rows of the by-Provider-and-Service file, and one row per NPI with four 2024 sums.
A blank is never a zero here: CMS drops a clinician with 10 or fewer beneficiaries and hides either
side of the split that rests on 1 to 10, so every table says in words which case applies.

Service year 2024 is read from the pinned version only. The search had pulled it through the series
alias, which will point at 2025 once CMS releases it; the alias pull is kept as a cross-check.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from .base import (DIVISION_GROUPS, OUT, RAW, ROOT, SNAPSHOT, UNIVERSE, UNIVERSE_SNAPSHOT, column, dump, duplicates,
                   load, money, now, publish, read_csv, sha, stage, universe, whole, write_module_metadata)

MODULE = 'medicare_payments'
BY_YEAR, FEATURES, DRUG_ROWS = ('hcp_medicare_partb_payments_by_year', 'hcp_medicare_service_product_features_2024',
                                'medicare_partb_drug_rows_2024')
TABLES = [BY_YEAR, FEATURES, DRUG_ROWS]
YEARS = [2019, 2020, 2021, 2022, 2023, 2024]
# Pinned data.cms.gov version of the by-Provider file for each service year (never the series alias).
BY_PROVIDER_VERSIONS = {
    2019: '6a53afe5-1cbc-4b33-9dc8-926ee532dc66', 2020: '29d799aa-c660-44fe-a51a-72c4b3e661ac',
    2021: '44e0a489-666c-4ea4-a1a2-360b6cdc19db', 2022: '21555c17-ec1b-4e74-b2c6-925c6cbb3147',
    2023: '8ba584c6-a43a-4b0b-a35a-eb9a59e3a571', 2024: '4d0b2df0-1e99-4db7-a574-a571d99217f1',
}
BY_PROVIDER_ALIAS = '8889d81e-2ee7-448f-8713-f071038289b5'
SERVICE_VERSION = '335e5f35-eca6-482d-87b3-f99883e213e3'   # by Provider and Service, 2024
PART_D_VERSION = 'd5aa71a8-dcc0-4570-8bcf-bd39deac69fe'    # Part D by Provider and Drug, 2024
API = 'https://data.cms.gov/data-api/v1/dataset/{version}/data'
BY_PROVIDER_FIELDS = ['Rndrng_NPI', 'Rndrng_Prvdr_State_Abrvtn', 'Rndrng_Prvdr_Type', 'Tot_Benes', 'Tot_Srvcs',
                      'Tot_Mdcr_Pymt_Amt', 'Drug_Sprsn_Ind', 'Drug_Tot_Benes', 'Drug_Mdcr_Pymt_Amt', 'Med_Sprsn_Ind',
                      'Med_Tot_Benes', 'Med_Mdcr_Pymt_Amt']
# No descriptor text is requested: published tables carry code numbers and this project's own labels.
SERVICE_FIELDS = ['Rndrng_NPI', 'Rndrng_Prvdr_Type', 'HCPCS_Cd', 'HCPCS_Drug_Ind', 'Place_Of_Srvc', 'Tot_Benes',
                  'Tot_Srvcs', 'Tot_Bene_Day_Srvcs', 'Avg_Mdcr_Alowd_Amt', 'Avg_Mdcr_Pymt_Amt']
ROW_FIELDS = ['Rndrng_NPI', 'HCPCS_Cd', 'Place_Of_Srvc', 'Tot_Benes', 'Tot_Srvcs', 'Avg_Mdcr_Pymt_Amt']

GAP = 'gap-medicaid-and-multiyear-payer-payments-by-division/medicare/by_provider_{year}.json'
DRUG_ROWS_INPUT = 'local-payer-payments-by-division/api/provider_service2024_drug_rows_universe.json'
QUERIES = ROOT / 'Data' / 'interim' / UNIVERSE_SNAPSHOT / 'queries'   # pinned-version pulls of 2026-10-25
PART_D = ROOT / 'Data' / 'processed' / UNIVERSE_SNAPSHOT / 'part_d_lymphoma_orals_2024.csv'
# sha256 of the search's response files as recorded when the load specification was checked.
INPUT_SHA256 = {
    GAP.format(year=2019): 'a7ef183bcd799a8ef9a8f734aaaa7436b75e44dae3adce5148735969f8f884f4',
    GAP.format(year=2020): 'aa4610e024faa9cec62c40e92f9726ecf1cd0dda5b9ceaa181835e32b807f02b',
    GAP.format(year=2021): '2c58bc1898e0c7d9b06a758aa7fd8c5edb133165a807d0d182cab4feff4bc993',
    GAP.format(year=2022): '21823c3e10289c976fdd43af75e4ea85878c1ed8d0bbb0ff0ff823384f2d2424',
    GAP.format(year=2023): 'dbec137636595ce4d40349dbeb2029de773ba1b5f7aa4ff5abf339c931ab339a',
    GAP.format(year=2024): '4cbac2bbb14772916deed9eba7765d508709e3b0a7a330e5d5809d7f5f834a75',
    DRUG_ROWS_INPUT: '6e2bd0315811c5ba48ff39d8eacfd553dd99ef51b19ecc389a7f983ff052ef17',
}
BY_PROVIDER_PULL = RAW.parent / 'cms_by_provider_2024'
SERVICE_PULL = RAW.parent / 'cms_provider_service_2024'
BATCH, PAGE, PACE = 100, 5000, 10.0    # data.cms.gov robots.txt asks for ten seconds between requests

# Project labels for a fixed list of 25 products used in lymphoma; none is specific to lymphoma.
PRODUCT_FAMILIES = {
    'J9312': 'rituximab_intravenous', 'Q5115': 'rituximab_intravenous', 'Q5119': 'rituximab_intravenous',
    'Q5123': 'rituximab_intravenous', 'J9311': 'rituximab_subcutaneous', 'J9301': 'obinutuzumab',
    'J9033': 'bendamustine', 'J9034': 'bendamustine', 'J9036': 'bendamustine', 'J9056': 'bendamustine',
    'J9058': 'bendamustine', 'J9059': 'bendamustine', 'J9042': 'brentuximab_vedotin',
    'J9309': 'polatuzumab_vedotin', 'J9349': 'tafasitamab', 'J9359': 'loncastuximab_tesirine',
    'J9321': 'epcoritamab', 'J9286': 'glofitamab', 'J9350': 'mosunetuzumab',
    'Q2041': 'car_t_product', 'Q2042': 'car_t_product', 'Q2053': 'car_t_product', 'Q2054': 'car_t_product',
    'Q2055': 'car_t_product', 'Q2056': 'car_t_product',
}
# Codes with a national code query saved on 2026-10-25, and codes the search pulled without a script.
LOCAL_CODES = ['J9312', 'Q5115', 'Q5119', 'Q5123', 'J9311', 'J9309', 'J9349', 'J9359', 'J9321', 'J9286', 'J9350']
PULLED_CODES = [code for code in PRODUCT_FAMILIES if code not in LOCAL_CODES]
ADMIN_CODES = ['96413', '96415', '96417']   # chemotherapy infusion administration

STATUSES = ['published_split', 'split_suppressed', 'no_row']
DRUG_ROW_STATUSES = ['released_rows', 'no_released_rows', 'drug_payment_zero', 'split_suppressed',
                     'no_by_provider_row']
SUMS = ['partb_drug_rows_medicare_payment_estimate_usd', 'office_lymphoma_product_medicare_payment_estimate_usd',
        'office_chemo_admin_medicare_payment_estimate_usd', 'part_d_lymphoma_oral_total_drug_cost_usd']
GROUPS = ['core', 'affiliates', 'referring', 'other']

# Figures the search report quotes; `check` recomputes each from the published rows.
EXPECTED_ROWS_WITH_RECORD = {2019: 12337, 2020: 12814, 2021: 13331, 2022: 13811, 2023: 14342, 2024: 14915}
EXPECTED_SUPPRESSED = {2019: 984, 2020: 1021, 2021: 1090, 2022: 1129, 2023: 1080, 2024: 1145}
EXPECTED_2024_TOTAL_MILLION = {'referring': '5560.2', 'affiliates': '3582.4', 'core': '71.6'}
EXPECTED_2024_PUBLISHED_DRUG_BILLERS = {'referring': 2490, 'affiliates': 1759, 'core': 58}
EXPECTED_FEATURE_NPIS = dict(zip(SUMS, [4011, 390, 3669, 7828]))
EXPECTED_FEATURE_MILLION = dict(zip(SUMS, ['2705.5', '35.7', '119.2', '9365.5']))
EXPECTED_DRUG_ROWS = 46191
SAVED_PROVIDER_QUERIES = 119   # pinned 2024 by-Provider query files saved on 2026-10-25
# Drug and non-drug suppression codes that CMS publishes together on one row.
INDICATOR_PAIRS = {('', ''), ('*', '#'), ('*', '*'), ('#', '*')}
MONEY, COUNT = re.compile(r'\d+\.\d\d'), re.compile(r'\d+')
# Figures the README quotes, to the cent. They were computed from the raw CMS responses by a script that does
# not use this module; `check` recomputes each from the published rows through `headline`.
_YEAR = ('npis_with_row', 'npis_split_suppressed', 'total_usd', 'drug_usd', 'non_drug_usd')
_GROUP = ('npis', 'npis_with_row', 'npis_split_suppressed', 'total_usd', 'drug_usd', 'non_drug_usd',
          'npis_published_drug_payment_above_zero', 'npis_drug_side_suppressed_1_to_10_beneficiaries')
_EARLY = ('total_usd', 'drug_usd', 'npis_published_drug_payment_above_zero')
_PANEL = ('npis_with_row', 'total_usd', 'drug_usd')
EXPECTED_HEADLINE = {
    'by_year_rows': 105060, 'npi_years_with_cms_row': 81550, 'npis_with_row_in_all_six_years': 11427,
    'npis_with_no_row_in_any_year': 2054, 'provider_type_changed_2019_to_2024_in_balanced_panel': 1264,
    'universe_by_year': {
        2019: dict(zip(_YEAR, (12337, 984, '8018912682.00', '6291260443.90', '1641951474.25'))),
        2020: dict(zip(_YEAR, (12814, 1021, '8232703218.35', '6508988650.21', '1639790706.88'))),
        2021: dict(zip(_YEAR, (13331, 1090, '8501304398.74', '6724512423.81', '1676683988.95'))),
        2022: dict(zip(_YEAR, (13811, 1129, '8466522427.14', '6847721107.67', '1518695151.61'))),
        2023: dict(zip(_YEAR, (14342, 1080, '8710992083.27', '7138302975.41', '1475398234.93'))),
        2024: dict(zip(_YEAR, (14915, 1145, '9324736271.75', '7733400758.22', '1494757597.81')))},
    'division_groups_by_year': {
        2019: {'core': dict(zip(_EARLY, ('45494488.94', '26925279.14', 39))),
               'affiliates': dict(zip(_EARLY, ('2856913395.20', '2165136082.17', 1692))),
               'referring': dict(zip(_EARLY, ('4699891734.44', '3815241582.97', 2316))),
               'other': dict(zip(_EARLY, ('416613063.42', '283957499.62', 371)))},
        2024: {'core': dict(zip(_GROUP, (500, 414, 13, '71646226.92', '50035463.76', '19857140.85', 58, 13))),
               'affiliates': dict(zip(_GROUP, (9548, 8301, 473, '3582370627.94', '2884818672.16', '651849397.46',
                                               1759, 473))),
               'referring': dict(zip(_GROUP, (4632, 4504, 417, '5560156207.08', '4766650122.17', '761410091.95',
                                              2490, 416))),
               'other': dict(zip(_GROUP, (2830, 1696, 242, '110563209.81', '31896500.13', '61640967.55',
                                          189, 242)))}},
    'balanced_panel_2019_and_2024': {
        'core': {2019: dict(zip(_PANEL, (334, '43708838.28', '26417489.54'))),
                 2024: dict(zip(_PANEL, (334, '57812309.37', '39570065.63')))},
        'affiliates': {2019: dict(zip(_PANEL, (6141, '2813260561.83', '2139938754.24'))),
                       2024: dict(zip(_PANEL, (6141, '3204907216.03', '2605457920.75')))},
        'referring': {2019: dict(zip(_PANEL, (3839, '4667631224.11', '3791182470.54'))),
                      2024: dict(zip(_PANEL, (3839, '5079383352.45', '4360049452.85')))},
        'other': {2019: dict(zip(_PANEL, (1113, '335789959.97', '227761511.95'))),
                  2024: dict(zip(_PANEL, (1113, '86812706.70', '24088267.48')))}},
    'drug_rows': {'rows': 46191, 'npis': 4011, 'codes': 221, 'place_of_service': {'O': 46191},
                  'product_family_rows': {'rituximab_intravenous': 393, 'rituximab_subcutaneous': 7,
                                          'obinutuzumab': 6, 'bendamustine': 10}},
    'partb_drug_rows_status': {'released_rows': 4011, 'no_released_rows': 485, 'drug_payment_zero': 9274,
                               'split_suppressed': 1145, 'no_by_provider_row': 2595},
    'features': {SUMS[0]: {'npis': 4011, 'usd': '2705450242.91'}, SUMS[1]: {'npis': 390, 'usd': '35724204.87'},
                 SUMS[2]: {'npis': 3669, 'usd': '119170831.88'}, SUMS[3]: {'npis': 7828, 'usd': '9365532028.42'}},
}
# sha256 of each published table's cells (see `fingerprint`). The tables are a frozen snapshot: a rebuild that
# changes any cell must be explained and these values updated, which is how a swapped or edited cell is caught
# where no tracked table holds a second copy of the value.
TABLE_SHA256 = {BY_YEAR: '4191475307fbb39c7c79ceb9671e111ca57cbc15504c1af9a30dbfbe7f94161d',
                FEATURES: 'bb6fa8ec3a66994208759c42b6a954dd5ba4e8b6336bda0a1b3c2b4df79e2cea',
                DRUG_ROWS: '1c49f4aa9d3a77982868a7726a2a05480221300c0f1379a0f9f88c1dc14debf4'}


def cents(value):
    """Whole cents of a dollar text; None for a blank, which is never read as zero."""
    value = str(value).strip()
    return int((Decimal(value) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if value else None


def dollars(amount):
    return f'{Decimal(amount) / 100:.2f}'


def million(amount):
    """Cents as millions of dollars to one decimal, the precision the report quotes."""
    return str((Decimal(amount) / Decimal(10 ** 8)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def estimate(average, units):
    """Estimated payment on one released row: CMS average payment times services, rounded to cents."""
    return str((Decimal(average) * Decimal(units)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def fingerprint(rows):
    """sha256 over a table's cells with their column names, rows in sorted order; any changed or swapped cell shows."""
    digest = hashlib.sha256()
    for line in sorted('\x1f'.join(f'{name}={value}' for name, value in row.items()) for row in rows):
        digest.update(line.encode() + b'\n')
    return digest.hexdigest()


def figures_differing(expected, actual):
    """Number of expected figures that a recomputed headline does not reproduce."""
    if isinstance(expected, dict):
        return sum(figures_differing(value, actual.get(key) if isinstance(actual, dict) else None)
                   for key, value in expected.items())
    return int(expected != actual)


def provider_status(record):
    """Status in words of one NPI x year cell; `record` is the CMS by-Provider row, or None."""
    if record is None:
        return 'no_row'
    suppressed = bool(record['Drug_Sprsn_Ind'].strip() or record['Med_Sprsn_Ind'].strip())
    filled = [bool(record[field].strip()) for field in ('Drug_Mdcr_Pymt_Amt', 'Med_Mdcr_Pymt_Amt')]
    assert filled == [not suppressed] * 2, f"Split and suppression indicators disagree for {record['Rndrng_NPI']}"
    return 'split_suppressed' if suppressed else 'published_split'


def year_row(npi, year, record):
    """One row of the NPI x service year grid; cells stay blank when CMS has no row or hides the split."""
    row = {'npi': npi, 'service_year': year, 'by_provider_status': provider_status(record),
           'source_dataset_version_id': BY_PROVIDER_VERSIONS[year]}
    record = record or {}
    row.update({
        'cms_provider_type': record.get('Rndrng_Prvdr_Type', ''),
        'total_medicare_payment_usd': money(record.get('Tot_Mdcr_Pymt_Amt', '')),
        'drug_medicare_payment_usd': money(record.get('Drug_Mdcr_Pymt_Amt', '')),
        'non_drug_medicare_payment_usd': money(record.get('Med_Mdcr_Pymt_Amt', '')),
        'drug_suppression_indicator': record.get('Drug_Sprsn_Ind', '').strip(),
        'non_drug_suppression_indicator': record.get('Med_Sprsn_Ind', '').strip(),
        'total_beneficiaries': whole(record.get('Tot_Benes', '')),
        'drug_beneficiaries': whole(record.get('Drug_Tot_Benes', '')),
        'non_drug_beneficiaries': whole(record.get('Med_Tot_Benes', '')),
    })
    return row


def drug_row(record):
    """One published NPI x code x place-of-service drug row of the 2024 by-Provider-and-Service file."""
    assert record['HCPCS_Drug_Ind'] == 'Y', 'Not a drug row'
    return {'npi': record['Rndrng_NPI'], 'hcpcs_code': record['HCPCS_Cd'], 'place_of_service': record['Place_Of_Srvc'],
            'lymphoma_product_family': PRODUCT_FAMILIES.get(record['HCPCS_Cd'], ''),
            'beneficiaries': whole(record['Tot_Benes']), 'services_or_units': record['Tot_Srvcs'].strip(),
            'average_medicare_payment_usd': record['Avg_Mdcr_Pymt_Amt'].strip(),
            'estimated_medicare_payment_usd': estimate(record['Avg_Mdcr_Pymt_Amt'], record['Tot_Srvcs']),
            'source_dataset_version_id': SERVICE_VERSION}


def total_by_npi(rows, field):
    """NPI to summed cents over the given rows; an NPI with no row is absent, not zero."""
    out = defaultdict(int)
    for row in rows:
        out[row['npi']] += cents(row[field])
    return dict(out)


def is_office_product(row):
    return bool(row['lymphoma_product_family']) and row['place_of_service'] == 'O'


def drug_rows_status(row_2024, has_rows):
    """Why the drug-row estimate is filled or blank, read from the NPI's 2024 by-Provider cell."""
    status = row_2024['by_provider_status']
    if has_rows:
        assert status == 'published_split' and cents(row_2024['drug_medicare_payment_usd']) > 0, \
            f"Released drug rows without a published drug payment: {row_2024['npi']}"
        return 'released_rows'
    if status == 'no_row':
        return 'no_by_provider_row'
    if status == 'split_suppressed':
        return 'split_suppressed'
    return 'no_released_rows' if cents(row_2024['drug_medicare_payment_usd']) > 0 else 'drug_payment_zero'


def feature_row(npi, row_2024, sums):
    """One NPI of the 2024 feature table; `sums` maps each sum name to NPI -> cents."""
    values = {name: dollars(sums[name][npi]) if npi in sums[name] else '' for name in SUMS}
    return {'npi': npi,
            'service_product_data_status': 'released_cells' if any(values.values()) else 'no_released_cells',
            'partb_drug_rows_status': drug_rows_status(row_2024, bool(values[SUMS[0]])), **values}


# ---------------------------------------------------------------------------------------------------------------
# Collection: the two slices the search left without a pinned, scripted pull.

def in_filter(npis):
    text = 'filter[a][condition][path]=Rndrng_NPI&filter[a][condition][operator]=IN'
    return text + ''.join(f'&filter[a][condition][value][{i}]={npi}' for i, npi in enumerate(npis))


def fetch(url, path, version, request):
    """One paced API request with curl's default user agent; the raw response is kept with hash and UTC time."""
    meta_path = path.with_suffix('.meta.json')
    if path.exists() and meta_path.exists():
        return json.loads(path.read_bytes())
    delay = 20
    for attempt in range(5):
        done = subprocess.run(['curl', '-s', '-g', '-w', '\n%{http_code}', url], capture_output=True, timeout=180)
        body, _, code = done.stdout.rpartition(b'\n')
        observed = now()
        try:
            rows = json.loads(body) if code == b'200' else None
        except ValueError:
            rows = None
        if isinstance(rows, list):
            break
        assert attempt < 4, f'CMS request failed with HTTP {code.decode()}: {url[:160]}'
        time.sleep(delay)
        delay *= 2
    assert len(rows) < PAGE, 'Response filled a page; add paging before trusting it'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    dump(meta_path, {'source_url': url, 'dataset_version_id': version, 'request': request, 'http_status': 200,
                     'retrieved_at_utc': observed, 'rows': len(rows), 'bytes': len(body),
                     'sha256': hashlib.sha256(body).hexdigest(), 'client': 'curl, default user agent',
                     'local_path': str(path.relative_to(ROOT))})
    print('MEDICARE PULL', path.name, len(rows), 'ROWS', flush=True)
    time.sleep(PACE)
    return rows


def batches(npis):
    npis = sorted(npis)
    return [npis[i:i + BATCH] for i in range(0, len(npis), BATCH)]


def pull_by_provider_2024(npis):
    """Pinned 2024 by-Provider rows for the universe NPIs that have no row in the saved pinned queries."""
    version, rows, paths = BY_PROVIDER_VERSIONS[2024], [], []
    for index, batch in enumerate(batches(npis)):
        url = f"{API.format(version=version)}?{in_filter(batch)}&column={','.join(BY_PROVIDER_FIELDS)}&size={PAGE}"
        path = BY_PROVIDER_PULL / f'batch_{index:03d}.json'
        page = fetch(url, path, version, {'Rndrng_NPI': batch})
        assert load(path.with_suffix('.meta.json'))['request']['Rndrng_NPI'] == batch, f'{path.name}: other NPIs'
        assert {r['Rndrng_NPI'] for r in page} <= set(batch), f'{path.name}: row outside the requested NPIs'
        rows += page
        paths += [path, path.with_suffix('.meta.json')]
    return rows, paths


def pull_product_codes():
    """Pinned 2024 national code queries for the product codes the search had pulled without a script."""
    rows, paths = [], []
    for code in PULLED_CODES:
        url = (f"{API.format(version=SERVICE_VERSION)}?filter[HCPCS_Cd]={code}"
               f"&column={','.join(SERVICE_FIELDS)}&size={PAGE}")
        path = SERVICE_PULL / f'code_{code}.json'
        page = fetch(url, path, SERVICE_VERSION, {'HCPCS_Cd': code})
        assert all(r['HCPCS_Cd'] == code for r in page), f'{path.name}: other code returned'
        rows += page
        paths += [path, path.with_suffix('.meta.json')]
    return rows, paths


# ---------------------------------------------------------------------------------------------------------------
# Inputs

def staged_probe(name, paths):
    """Freeze one response file of the search and confirm it is the file the specification checked."""
    path = stage(name)
    assert sha(path) == INPUT_SHA256[name], f'{name}: not the response file the specification checked'
    paths.append(path)
    return path


def staged_query(name, version, paths):
    """Records of one pinned-version query saved by the 2026-10-25 collection, frozen with this snapshot."""
    path = stage(QUERIES / name, f'cms_queries_{UNIVERSE_SNAPSHOT}/{name}')
    paths.append(path)
    query = load(path)
    assert query['dataset_version_id'] == version and query['query_status'] == 'complete', f'{name}: wrong query'
    return query['records'], query['query_completed_at_utc']


def unique_by_npi(rows, label):
    out = {}
    for row in rows:
        npi = row['Rndrng_NPI']
        same = npi not in out or all(out[npi][f] == row[f] for f in BY_PROVIDER_FIELDS)
        assert same, f'{label}: conflicting rows for one NPI'
        out[npi] = row
    return out


def saved_provider_queries():
    """File names of the saved pinned 2024 by-Provider queries, from the 2026-10-24 folder or their frozen copy."""
    folders = [QUERIES, RAW / f'cms_queries_{UNIVERSE_SNAPSHOT}']
    names = sorted({path.name for folder in folders if folder.is_dir() for path in folder.glob('provider_*.json')})
    assert len(names) == SAVED_PROVIDER_QUERIES, f'{len(names)} saved by-Provider queries found'
    return names


def by_provider_2024(people, paths):
    """NPI to pinned 2024 by-Provider row: saved pinned queries first, then a pinned pull for the rest."""
    times, local = [], []
    for name in saved_provider_queries():
        records, completed = staged_query(name, BY_PROVIDER_VERSIONS[2024], paths)
        local += [r for r in records if r['Rndrng_NPI'] in people]
        times.append(completed)
    local = unique_by_npi(local, 'saved 2024 queries')
    missing = sorted(set(people) - set(local))
    pulled, pull_paths = pull_by_provider_2024(missing)
    paths += pull_paths
    pulled = unique_by_npi(pulled, 'pinned 2024 pull')
    alias_path = staged_probe(GAP.format(year=2024), paths)
    alias = unique_by_npi(load(alias_path), 'alias 2024 pull')
    records = {**local, **pulled}
    differing = sum(any(records[n][f] != alias[n][f] for f in BY_PROVIDER_FIELDS) for n in set(records) & set(alias))
    metas = [load(p) for p in pull_paths if p.name.endswith('.meta.json')]
    audit = {'saved_pinned_query_files': len(times), 'saved_pinned_rows_in_universe': len(local),
             'saved_pinned_query_completed_utc': [min(times), max(times)],
             'npis_without_saved_row_queried_on_pinned_version': len(missing), 'pinned_pull_rows': len(pulled),
             'pinned_pull_requests': len(metas),
             'pinned_pull_retrieved_utc': [min(m['retrieved_at_utc'] for m in metas),
                                           max(m['retrieved_at_utc'] for m in metas)],
             'alias_pull_rows': len(alias), 'npis_only_in_pinned': len(set(records) - set(alias)),
             'npis_only_in_alias': len(set(alias) - set(records)), 'rows_differing_from_alias_pull': differing}
    assert not (audit['npis_only_in_pinned'] or audit['npis_only_in_alias'] or differing), \
        f'Pinned 2024 rows differ from the alias pull: {audit}'
    return records, audit


def by_provider_records(people, paths):
    """Service year to {NPI: CMS by-Provider row}, with a provenance note per year."""
    records, provenance = {}, {}
    for year in YEARS[:-1]:
        path = staged_probe(GAP.format(year=year), paths)
        rows = load(path)
        records[year] = unique_by_npi(rows, f'by-Provider {year}')
        assert len(records[year]) == len(rows) and set(records[year]) <= set(people), f'{year}: unexpected rows'
        saved = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')
        provenance[year] = {'dataset_version_id': BY_PROVIDER_VERSIONS[year], 'rows': len(rows),
                            'request_times': 'not captured', 'response_file_saved_utc': saved.replace('+00:00', 'Z')}
    records[2024], audit = by_provider_2024(people, paths)
    provenance[2024] = {'dataset_version_id': BY_PROVIDER_VERSIONS[2024], 'rows': len(records[2024]), **audit}
    return records, provenance


def same_cells(left, right):
    """Number of NPI x code x place cells that differ between two sets of by-Provider-and-Service rows."""
    index = [{tuple(r[f] for f in ROW_FIELDS[:3]): tuple(r[f] for f in ROW_FIELDS[3:]) for r in rows}
             for rows in (left, right)]
    return sum(index[0].get(key) != index[1].get(key) for key in set(index[0]) | set(index[1]))


def drug_row_records(people, paths):
    """2024 drug rows of the universe plus the audit of the 25 product codes against pinned code queries."""
    path = staged_probe(DRUG_ROWS_INPUT, paths)
    records = load(path)
    kept = [r for r in records if r['Rndrng_NPI'] in people]
    code_rows, times = [], []
    for code in LOCAL_CODES:
        saved_rows, completed = staged_query(f'service_{code}.json', SERVICE_VERSION, paths)
        code_rows += saved_rows
        times.append(completed)
    pulled, pull_paths = pull_product_codes()
    paths += pull_paths
    national = code_rows + pulled
    listed = [r for r in kept if r['HCPCS_Cd'] in PRODUCT_FAMILIES]
    in_universe = [r for r in national if r['Rndrng_NPI'] in people]
    metas = [load(p) for p in pull_paths if p.name.endswith('.meta.json')]
    saved = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')
    audit = {'input_rows': len(records), 'rows_outside_universe_not_published': len(records) - len(kept),
             'request_times': 'not captured', 'response_file_saved_utc': saved.replace('+00:00', 'Z'),
             'product_code_rows_national': len(national), 'product_code_rows_in_universe': len(in_universe),
             'product_code_rows_in_drug_rows': len(listed),
             'product_code_cells_differing': same_cells(listed, in_universe),
             'product_codes_with_national_rows': dict(sorted(Counter(r['HCPCS_Cd'] for r in national).items())),
             'product_codes_with_no_national_row': sorted(set(PRODUCT_FAMILIES) - {r['HCPCS_Cd'] for r in national}),
             'product_code_place_of_service': dict(Counter(r['Place_Of_Srvc'] for r in national)),
             'codes_from_saved_pinned_queries': LOCAL_CODES, 'saved_query_completed_utc': [min(times), max(times)],
             'codes_repulled_on_pinned_version': PULLED_CODES,
             'repull_retrieved_utc': [min(m['retrieved_at_utc'] for m in metas),
                                      max(m['retrieved_at_utc'] for m in metas)]}
    assert audit['product_code_cells_differing'] == 0, f'Product rows differ from the pinned code queries: {audit}'
    return kept, audit


def admin_rows(people, paths):
    """Released 2024 non-facility chemotherapy administration rows of universe NPIs, with estimates."""
    rows, national, times = [], 0, []
    for code in ADMIN_CODES:
        records, completed = staged_query(f'service_{code}.json', SERVICE_VERSION, paths)
        national += len(records)
        times.append(completed)
        rows += [{'npi': r['Rndrng_NPI'], 'hcpcs_code': code,
                  'estimated_medicare_payment_usd': estimate(r['Avg_Mdcr_Pymt_Amt'], r['Tot_Srvcs'])}
                 for r in records if r['Rndrng_NPI'] in people and r['Place_Of_Srvc'] == 'O']
    return rows, {'national_rows': national, 'universe_non_facility_rows': len(rows),
                  'query_completed_utc': [min(times), max(times)]}


# ---------------------------------------------------------------------------------------------------------------
# Published columns

BLANK_NO_ROW = 'No row for the NPI in that year\'s file (by_provider_status = no_row); not zero.'
BLANK_SPLIT = ('No row that year, or the split is hidden by CMS; by_provider_status says which. Never zero.')


def by_year_columns():
    return [
        column('npi', 'National Provider Identifier (NPI) of the clinician, ten digits. Every NPI of the 2026-10-24 '
               'universe table hcp_divisions.csv has one row per service year.', 'Never blank.', 'project'),
        column('service_year', 'Calendar year in which the services were rendered, 2019 to 2024.', 'Never blank.',
               'cms'),
        column('by_provider_status', 'What the Centers for Medicare & Medicaid Services (CMS) file Medicare Physician '
               '& Other Practitioners - by Provider holds for the NPI in that year: published_split (a row with drug '
               'and non-drug payment), split_suppressed (a row with the total only, because one side rests on 1 to 10 '
               'beneficiaries), or no_row (no row, which happens with 10 or fewer beneficiaries or no claims as '
               'rendering provider).', 'Never blank.', 'cms'),
        column('cms_provider_type', 'Provider type on the CMS row for that year: the specialty reported on the '
               'NPI\'s claims and, when several were reported, the one with the most services that year. It is set per '
               'year and can change between years; it is a claims attribute, not a board certification.',
               BLANK_NO_ROW, 'cms'),
        column('total_medicare_payment_usd', 'US dollars that Medicare fee-for-service Part B paid in the service '
               'year, after deductible and coinsurance, for all non-institutional services and drugs on which the NPI '
               'was the rendering provider. The money goes to the billing practice, not necessarily to the clinician.',
               BLANK_NO_ROW, 'cms'),
        column('drug_medicare_payment_usd', 'Part of total_medicare_payment_usd paid for billing codes on that '
               'year\'s Medicare Part B drug Average Sales Price (ASP) file. A 0 is published by CMS and means no '
               'beneficiary received such a drug from the NPI.', BLANK_SPLIT + ' A hidden split with '
               'drug_suppression_indicator * means some drug billing, for 1 to 10 beneficiaries.', 'cms'),
        column('non_drug_medicare_payment_usd', 'Part of total_medicare_payment_usd paid for everything not on the '
               'ASP file: visits, drug administration, laboratory, imaging, and drugs billed under codes absent from '
               'that file. CMS calls this side medical services.', BLANK_SPLIT, 'cms'),
        column('drug_suppression_indicator', 'CMS code on the drug side: * = hidden because 1 to 10 beneficiaries '
               'received a drug on the ASP file from the NPI; # = hidden only so that the hidden non-drug side cannot '
               'be recomputed from the total.',
               'Drug side published, or no row that year; by_provider_status says which.', 'cms'),
        column('non_drug_suppression_indicator', 'CMS code on the non-drug side: * = hidden because 1 to 10 '
               'beneficiaries received non-drug services from the NPI; # = hidden only so that the hidden drug side '
               'cannot be recomputed from the total.',
               'Non-drug side published, or no row that year; by_provider_status says which.', 'cms'),
        column('total_beneficiaries', 'Medicare fee-for-service beneficiaries who received any Part B service from the '
               'NPI in the service year; at least 11 on every CMS row. A patient seen by several NPIs is counted once '
               'for each, so sums across NPIs are not patient counts.', BLANK_NO_ROW, 'cms'),
        column('drug_beneficiaries', 'Beneficiaries who received a drug on the ASP file from the NPI in the service '
               'year; 0 or at least 11 when published.', BLANK_SPLIT, 'cms'),
        column('non_drug_beneficiaries', 'Beneficiaries who received a service not on the ASP file from the NPI in '
               'the service year; at least 11 when published.', BLANK_SPLIT, 'cms'),
        column('source_dataset_version_id', 'data.cms.gov version identifier of the by-Provider file for that service '
               'year. It is filled on no_row rows too, where it names the file that was searched.', 'Never blank.',
               'cms'),
    ]


def feature_columns():
    floor = ('The Centers for Medicare & Medicaid Services (CMS) releases an NPI x code x place-of-service cell only '
             'when it has 11 or more beneficiaries')
    return [
        column('npi', 'National Provider Identifier (NPI) of the clinician, ten digits; one row for every NPI of the '
               '2026-10-24 universe table hcp_divisions.csv.', 'Never blank.', 'project'),
        column('service_product_data_status', 'released_cells when at least one of the four sums on the row is filled, '
               'no_released_cells when all four are blank.', 'Never blank.', 'project'),
        column('partb_drug_rows_status', 'Why partb_drug_rows_medicare_payment_estimate_usd is filled or blank, read '
               'with the NPI\'s 2024 row of hcp_medicare_partb_payments_by_year: released_rows; no_released_rows (drug '
               'payment published above zero but every code cell is under 11 beneficiaries); drug_payment_zero '
               '(published zero); split_suppressed (drug payment hidden); no_by_provider_row. For the last three the '
               'blank follows from the release rule, since no code cell can then reach 11 beneficiaries; most of '
               'those NPIs were not queried for drug rows.', 'Never blank.', 'project'),
        column(SUMS[0], 'Estimated 2024 Medicare fee-for-service Part B payment in US dollars on the NPI\'s released '
               'drug rows: the sum of estimated_medicare_payment_usd over its rows in medicare_partb_drug_rows_2024. '
               f'{floor}, so this is a floor under the NPI\'s 2024 drug_medicare_payment_usd in '
               'hcp_medicare_partb_payments_by_year.',
               'No released drug row for the NPI; partb_drug_rows_status gives the reason. Not zero.', 'cms'),
        column(SUMS[1], 'Estimated 2024 Medicare fee-for-service Part B payment in US dollars on the NPI\'s released '
               'rows for 25 product codes used in lymphoma, in the non-facility setting (place_of_service O): the sum '
               'of estimated_medicare_payment_usd over its rows of medicare_partb_drug_rows_2024 with a '
               'lymphoma_product_family. Only rituximab, obinutuzumab and bendamustine codes have released rows, and '
               'all three are also used outside lymphoma.',
               'No released row for any listed code; not zero and not evidence that the NPI does not treat lymphoma.',
               'cms'),
        column(SUMS[2], 'Estimated 2024 Medicare payment in US dollars for chemotherapy infusion administration, '
               'codes 96413, 96415 and 96417, on the NPI\'s released non-facility rows of the CMS by-Provider-and-'
               'Service file: average Medicare payment times services, rounded to cents per row and summed.',
               'No released row for any of the three codes (cell under 11 beneficiaries, or the administration is '
               'billed by a hospital or under another NPI); not zero.', 'cms'),
        column(SUMS[3], 'Sum of total_drug_cost_usd over the NPI\'s 2024 rows of part_d_lymphoma_orals_2024.csv '
               '(2026-10-24 snapshot; Medicare Part D prescriptions for seven oral agents). It is the gross cost of '
               'prescriptions filled at pharmacies, not a payment to the prescriber, and must not be added to the '
               'Part B columns.', 'No published Part D row for any of the seven agents (CMS drops a prescriber x drug '
               'row with fewer than 11 claims); not zero.', 'cms'),
    ]


def drug_row_columns():
    return [
        column('npi', 'National Provider Identifier (NPI) of the rendering clinician, ten digits; always in the '
               '2026-10-24 universe table hcp_divisions.csv.', 'Never blank.', 'cms'),
        column('hcpcs_code', 'Healthcare Common Procedure Coding System (HCPCS) code of the drug as billed, code '
               'number only. Every row is a code that the Centers for Medicare & Medicaid Services (CMS) flags as a '
               'drug on the Part B Average Sales Price file.', 'Never blank.', 'cms'),
        column('place_of_service', 'CMS place of service: O = non-facility (office or other non-facility setting), '
               'F = facility. Every row of this table is O.', 'Never blank.', 'cms'),
        column('lymphoma_product_family', 'This project\'s label when the code is one of 25 product codes used in '
               'lymphoma: rituximab_intravenous (originator and biosimilars), rituximab_subcutaneous, obinutuzumab or '
               'bendamustine on the rows present. None of these products is specific to lymphoma.',
               'The code is not on the project\'s list.', 'project'),
        column('beneficiaries', 'Medicare fee-for-service beneficiaries who received the drug from the NPI in that '
               'place of service in 2024; at least 11, because CMS does not release smaller cells.', 'Never blank.',
               'cms'),
        column('services_or_units', 'Billing units of the drug paid in 2024, as CMS counts services for the code. '
               'For a drug code this is a quantity of dosage units, not a number of administrations, and it can be '
               'fractional.', 'Never blank.', 'cms'),
        column('average_medicare_payment_usd', 'Average US dollars Medicare paid per unit after deductible and '
               'coinsurance, as CMS publishes it.', 'Never blank.', 'cms'),
        column('estimated_medicare_payment_usd', 'average_medicare_payment_usd times services_or_units, rounded to '
               'cents. It is an estimate of Medicare\'s payment on the row, since CMS publishes the average, not the '
               'sum.', 'Never blank.', 'cms'),
        column('source_dataset_version_id', 'data.cms.gov version identifier of the 2024 Medicare Physician & Other '
               'Practitioners - by Provider and Service file.', 'Never blank.', 'cms'),
    ]


# ---------------------------------------------------------------------------------------------------------------
# Headline numbers, recomputed from published rows

def group_of(people):
    return {npi: DIVISION_GROUPS[row['division']] for npi, row in people.items()}


def split_summary(rows):
    """Totals over a set of by-year rows of one service year; cents are summed only where CMS published them."""
    with_row = [r for r in rows if r['by_provider_status'] != 'no_row']
    split = [r for r in with_row if r['by_provider_status'] == 'published_split']
    total = sum(cents(r['total_medicare_payment_usd']) for r in with_row)
    drug = sum(cents(r['drug_medicare_payment_usd']) for r in split)
    non_drug = sum(cents(r['non_drug_medicare_payment_usd']) for r in split)
    return {'npis': len(rows), 'npis_with_row': len(with_row), 'npis_split_suppressed': len(with_row) - len(split),
            'npis_published_drug_payment_above_zero': sum(cents(r['drug_medicare_payment_usd']) > 0 for r in split),
            'npis_drug_side_suppressed_1_to_10_beneficiaries':
                sum(r['drug_suppression_indicator'] == '*' for r in with_row),
            'total_usd': dollars(total), 'drug_usd': dollars(drug), 'non_drug_usd': dollars(non_drug),
            'total_on_split_suppressed_rows_usd':
                dollars(sum(cents(r['total_medicare_payment_usd']) for r in with_row
                            if r['by_provider_status'] == 'split_suppressed')),
            'total_million_usd': million(total), 'drug_million_usd': million(drug),
            'non_drug_million_usd': million(non_drug),
            'drug_share_of_published_split': f'{drug / (drug + non_drug):.3f}' if drug + non_drug else '',
            'drug_share_of_total': f'{drug / total:.3f}' if total else ''}


def headline(year_rows, features, drug_rows, groups):
    by_year = defaultdict(list)
    for row in year_rows:
        by_year[int(row['service_year'])].append(row)
    present = Counter(r['npi'] for r in year_rows if r['by_provider_status'] != 'no_row')
    balanced = {npi for npi, years in present.items() if years == len(YEARS)}
    type_2024 = {r['npi']: r['cms_provider_type'] for r in by_year[2024]}
    out = {
        'universe_npis': len(groups), 'division_group_npis': dict(Counter(groups.values())),
        'by_year_rows': len(year_rows), 'npi_years_with_cms_row': sum(present.values()),
        'npis_with_row_in_all_six_years': len(balanced), 'npis_with_no_row_in_any_year': len(groups) - len(present),
        'status_counts_by_year': {y: dict(Counter(r['by_provider_status'] for r in by_year[y])) for y in YEARS},
        'drug_indicator_hash_by_year': {y: sum(r['drug_suppression_indicator'] == '#' for r in by_year[y])
                                        for y in YEARS},
        'universe_by_year': {y: split_summary(by_year[y]) for y in YEARS},
        'division_groups_by_year': {y: {g: split_summary([r for r in by_year[y] if groups[r['npi']] == g])
                                        for g in GROUPS} for y in YEARS},
        'balanced_panel_2019_and_2024': {
            g: {y: split_summary([r for r in by_year[y] if r['npi'] in balanced and groups[r['npi']] == g])
                for y in (2019, 2024)} for g in GROUPS},
        'provider_type_changed_2019_to_2024_in_balanced_panel': sum(
            r['npi'] in balanced and r['cms_provider_type'] != type_2024[r['npi']] for r in by_year[2019]),
        'drug_rows': {'rows': len(drug_rows), 'npis': len({r['npi'] for r in drug_rows}),
                      'codes': len({r['hcpcs_code'] for r in drug_rows}),
                      'place_of_service': dict(Counter(r['place_of_service'] for r in drug_rows)),
                      'product_family_rows': dict(Counter(r['lymphoma_product_family'] for r in drug_rows
                                                          if r['lymphoma_product_family']))},
        'partb_drug_rows_status': dict(Counter(r['partb_drug_rows_status'] for r in features)),
        'service_product_data_status': dict(Counter(r['service_product_data_status'] for r in features)),
        'features': {},
    }
    for name in SUMS:
        filled = [r for r in features if r[name]]
        out['features'][name] = {
            'npis': len(filled), 'usd': dollars(sum(cents(r[name]) for r in filled)),
            'million_usd': million(sum(cents(r[name]) for r in filled)),
            'by_division_group': {g: {'npis': sum(groups[r['npi']] == g for r in filled),
                                      'million_usd': million(sum(cents(r[name]) for r in filled
                                                                 if groups[r['npi']] == g))} for g in GROUPS}}
    drug_2024 = sum(cents(r['drug_medicare_payment_usd']) or 0 for r in by_year[2024])
    visible = sum(cents(r[SUMS[0]]) for r in features if r[SUMS[0]])
    out['share_of_2024_drug_payment_visible_by_code'] = f'{visible / drug_2024:.3f}' if drug_2024 else ''
    return out


LIMITATIONS = [
    'Medicare fee-for-service Part B non-institutional claims only. Medicare Advantage, Medicaid, commercial and '
    'uninsured patients, hospital facility billing and durable medical equipment are absent. The share of '
    'beneficiaries in Medicare Advantage rose over 2019-2024, which alone lowers fee-for-service totals.',
    'Dollars follow the rendering NPI but are paid to the billing practice. A hospital-employed physician\'s drugs '
    'are billed by the hospital and show as a published zero here, so differences between divisions in drug dollars '
    'show who bills drugs under their own NPI, not who treats more patients.',
    'Amounts are Medicare\'s payment after deductible and coinsurance in nominal dollars: not allowed amounts, not '
    'practice revenue or margin, and not adjusted for inflation or fee-schedule changes. Whether they reflect the '
    'sequestration pause of May 2020 to March 2022 is not stated in the CMS methodology on file.',
    'Division labels and the universe itself are a 2024 snapshot (hcp_divisions.csv, label version 1.0.0) applied to '
    'every year. A 2019 row of a referring NPI is what a physician who met the 2024 definition was paid in 2019. Rows '
    'with a CMS record rise from 12,337 in 2019 to 14,915 in 2024 because younger physicians enter and physicians who '
    'left before 2024 are absent, so unbalanced totals overstate growth; use the NPIs with a row in all six years for '
    'trend statements. The fall of the group "other" over time is an effect of the labelling, not a finding.',
    'The referring label (division referring_proxy) is defined by the same 2024 markers that feed three of the four '
    'sums: non-facility chemotherapy administration, rituximab billed under the NPI, and Part D lymphoma orals. Every '
    'referring NPI has at least one of those sums and no NPI of the group "other" has any, by construction.',
    'Drug means billing codes on the Medicare Part B Average Sales Price file of that year, which changes yearly and '
    'which CMS says is not a complete list of Part B drugs; non-drug is everything else. The drug share the search '
    'report quotes (86%, 82%, 72% for referring, affiliates, core in 2024) is drug divided by drug plus non-drug on '
    'rows with a published split; drug divided by total payment is 86%, 81% and 70%.',
    'A count of NPIs with published drug payment above zero (2024: referring 2,490, affiliates 1,759, core 58) is not '
    'a count of NPIs billing any Part B drug. NPIs whose drug side is hidden with * also billed drugs, for 1 to 10 '
    'beneficiaries: 416 referring, 473 affiliates and 13 core in 2024, plus one referring NPI with the # code, for '
    'which it is unknown.',
    'Blank is never zero. No row means 10 or fewer beneficiaries or no claims; a hidden split means 1 to 10 '
    'beneficiaries on one side. Sums of drug and non-drug payment understate the total by the amount on rows with a '
    'hidden split ($96.6 million in 2024). No hidden value is recovered by subtraction anywhere in these tables.',
    'The code-level sums see only NPI x code x place-of-service cells with 11 or more beneficiaries, so they are '
    'floors and ranks partly reflect clearing that threshold: 35% of the universe\'s 2024 drug payment is visible '
    'by code. They use CMS-rounded averages, so they differ from true payments by rounding.',
    'CMS provider type is assigned per year from claims; do not carry the 2024 type back to earlier years.',
    'The 2021 file includes COVID-19 vaccination claims for Medicare Advantage enrollees billed under '
    'fee-for-service, by CMS\'s own note. The effect on this oncology universe was not measured.',
    'No column identifies lymphoma or CAR-T activity. Rituximab, obinutuzumab and bendamustine are used outside '
    'lymphoma; lenalidomide, about half of the Part D sum, is mostly a myeloma drug; CAR-T product codes and '
    'bispecific antibody codes have no NPI-level row nationally in 2024. Core figures rest on 37 to 58 NPIs with '
    'published drug payment per year.',
    'Part D cost is the gross drug cost at the pharmacy, not money to the prescriber.',
    'Released drug rows for NPIs after the first 2,480 in sorted order were requested only when the 2024 by-Provider '
    'drug payment was published and above zero. Among the 2,480 NPIs queried in full, none with no row, a zero drug '
    'payment or a * code had a drug row, and the single # NPI has no rows at all in the file.',
    'The 2019-2022 files are CMS re-releases (catalog modified 2025-03-26) and 2023 was modified 2025-09-09; CMS may '
    'revise them again, so each row carries the version identifier it was read from.',
]


def sources(provenance, drug_audit, admin_audit):
    terms = 'a: US Government work, public domain, attribution appreciated (https://data.cms.gov/about)'
    name = 'CMS Medicare Physician & Other Practitioners - by Provider'
    out = [{'name': f'{name}, service year {year}', 'publisher': 'Centers for Medicare & Medicaid Services',
            'url': API.format(version=BY_PROVIDER_VERSIONS[year]), 'release_or_version': BY_PROVIDER_VERSIONS[year],
            'retrieved': f"2026-11-19; request times not captured; response file saved "
                         f"{provenance[year]['response_file_saved_utc']}",
            'how_obtained': 'API, NPI IN-filter in batches of 80, 12 columns, by the search script mcare_pull.py',
            'terms_class': terms} for year in YEARS[:-1]]
    audit = provenance[2024]
    times = drug_audit['saved_query_completed_utc'] + admin_audit['query_completed_utc']
    saved = [min(times), max(times)]
    out += [
        {'name': f'{name}, service year 2024', 'publisher': 'Centers for Medicare & Medicaid Services',
         'url': API.format(version=BY_PROVIDER_VERSIONS[2024]), 'release_or_version': BY_PROVIDER_VERSIONS[2024],
         'retrieved': f"{audit['saved_pinned_rows_in_universe']} rows from provider-type and per-NPI queries completed "
                      f"{' to '.join(audit['saved_pinned_query_completed_utc'])}; {audit['pinned_pull_rows']} rows "
                      f"from {audit['pinned_pull_requests']} requests made "
                      f"{' to '.join(audit['pinned_pull_retrieved_utc'])}",
         'how_obtained': 'API on the pinned version. Saved 2026-10-25 queries first, then an NPI IN-filter pull by '
                         'this module for every universe NPI without a saved row, each response kept with hash and '
                         'UTC time. The search\'s pull through the series alias '
                         f'{BY_PROVIDER_ALIAS} is used only as a cross-check and agrees on every row.',
         'terms_class': terms},
        {'name': 'CMS Medicare Physician & Other Practitioners - by Provider and Service, service year 2024: drug '
                 'rows of universe NPIs',
         'publisher': 'Centers for Medicare & Medicaid Services', 'url': API.format(version=SERVICE_VERSION),
         'release_or_version': SERVICE_VERSION,
         'retrieved': f"2026-11-19; request times not captured; response file saved "
                      f"{drug_audit['response_file_saved_utc']}",
         'how_obtained': 'API, NPI IN-filter with the CMS drug indicator, by the search scripts fetch_api.py and '
                         'fetch_api2.py; one merged file, no per-request hashes', 'terms_class': terms},
        {'name': 'Same file: national code queries for 25 product codes and 3 administration codes',
         'publisher': 'Centers for Medicare & Medicaid Services', 'url': API.format(version=SERVICE_VERSION),
         'release_or_version': SERVICE_VERSION,
         'retrieved': f"{len(LOCAL_CODES) + len(ADMIN_CODES)} codes from queries completed {saved[0]} to {saved[1]}; "
                      f"{len(PULLED_CODES)} codes requested {' to '.join(drug_audit['repull_retrieved_utc'])}",
         'how_obtained': 'API code filter on the pinned version: saved 2026-10-25 queries, and a pull by this module '
                         'for the codes the search had fetched without a script. Used for the administration sum and '
                         'to confirm the product rows of the drug-row file.', 'terms_class': terms},
        {'name': 'CMS Medicare Part D Prescribers - by Provider and Drug, 2024, as published in '
                 'part_d_lymphoma_orals_2024.csv',
         'publisher': 'Centers for Medicare & Medicaid Services',
         'url': API.format(version=PART_D_VERSION), 'release_or_version': PART_D_VERSION,
         'retrieved': '2026-10-25 (2026-10-24 snapshot)',
         'how_obtained': 'Read from the tracked table of the 2026-10-24 snapshot; not collected again',
         'terms_class': terms},
        {'name': 'HCP universe and division labels, hcp_divisions.csv', 'publisher': 'This project',
         'url': str(UNIVERSE.relative_to(ROOT)), 'release_or_version': f'{UNIVERSE_SNAPSHOT}, label version 1.0.0',
         'retrieved': 'local', 'how_obtained': 'Read from the tracked table', 'terms_class': 'project table'},
    ]
    return out


def build():
    people = universe()
    groups = group_of(people)
    paths = []
    records, provenance = by_provider_records(people, paths)
    year_rows = [year_row(npi, year, records[year].get(npi)) for npi in sorted(people) for year in YEARS]
    raw_drug, drug_audit = drug_row_records(people, paths)
    drug_rows = sorted((drug_row(r) for r in raw_drug), key=lambda r: (r['npi'], r['hcpcs_code'], r['place_of_service']))
    admin, admin_audit = admin_rows(people, paths)
    part_d = read_csv(PART_D)
    assert {r['source_dataset_version_id'] for r in part_d} == {PART_D_VERSION}, 'Unexpected Part D version'
    sums = {SUMS[0]: total_by_npi(drug_rows, 'estimated_medicare_payment_usd'),
            SUMS[1]: total_by_npi(filter(is_office_product, drug_rows), 'estimated_medicare_payment_usd'),
            SUMS[2]: total_by_npi(admin, 'estimated_medicare_payment_usd'),
            SUMS[3]: total_by_npi((r for r in part_d if r['npi'] in people), 'total_drug_cost_usd')}
    cell_2024 = {r['npi']: r for r in year_rows if r['service_year'] == 2024}
    features = [feature_row(npi, cell_2024[npi], sums) for npi in sorted(people)]
    tables = [publish(BY_YEAR, year_rows, by_year_columns(), ['npi', 'service_year']),
              publish(FEATURES, features, feature_columns(), ['npi']),
              publish(DRUG_ROWS, drug_rows, drug_row_columns(), ['npi', 'hcpcs_code', 'place_of_service'])]
    published = {table: read_csv(OUT / f'{table}.csv') for table in TABLES}
    numbers = headline(published[BY_YEAR], published[FEATURES], published[DRUG_ROWS], groups)
    prints = {table: fingerprint(rows) for table, rows in published.items()}
    errors = {name: count for name, count in check(OUT.parent).items() if count}
    assert not errors, f'Checks failed on the published tables: {errors}; table fingerprints are now {prints}'
    write_module_metadata(MODULE, {
        'tables': tables, 'sources': sources(provenance, drug_audit, admin_audit), 'headline': numbers,
        'limitations': LIMITATIONS,
        'division_groups': {'referring': ['referring_proxy'],
                            'affiliates': ['affiliate_A', 'affiliate_B', 'affiliate_C', 'center_affiliate_unlinked'],
                            'core': ['core'], 'other': ['hemonc_other']},
        'by_provider_provenance': provenance, 'drug_row_audit': drug_audit, 'administration_audit': admin_audit,
        'part_d_rows_read': len(part_d), 'part_d_rows_outside_universe': sum(r['npi'] not in people for r in part_d),
        'product_families': PRODUCT_FAMILIES, 'administration_codes': ADMIN_CODES,
        'rows_removed': {'drug_rows_outside_universe': drug_audit['rows_outside_universe_not_published'],
                         'reason': 'The drug-row pull was filtered on universe NPIs, so no row is outside it.'},
        'search_response_sha256': INPUT_SHA256, 'table_cell_fingerprint_sha256': prints,
        'defects_fixed': DEFECTS_FIXED, 'defects_open': DEFECTS_OPEN,
    }, paths + [PART_D, UNIVERSE])
    print('MEDICARE PAYMENTS BUILT', len(year_rows), 'NPI-YEARS', numbers['npi_years_with_cms_row'], 'WITH CMS ROW',
          len(drug_rows), 'DRUG ROWS', len(features), 'NPIS', flush=True)


DEFECTS_FIXED = [
    'Service year 2024 is read from the pinned version 4d0b2df0-1e99-4db7-a574-a571d99217f1 only: saved pinned '
    'queries of 2026-10-25 plus a pinned pull by this module for every universe NPI without a saved row. The '
    'search\'s pull through the series alias 8889d81e-2ee7-448f-8713-f071038289b5 is kept as a cross-check and '
    'agrees on every row and field.',
    'The wide table with one set of columns per year is published as a complete NPI x service_year grid with '
    'by_provider_status in words, so a blank no longer has to be read against two other columns.',
    'The non-drug suppression indicator, the three beneficiary counts and the per-year provider type, dropped by the '
    'search, are carried; the pinned version identifier is on every row.',
    'The 2024 by-Provider columns are stored once, in the by-year table; the feature table keeps only the four sums.',
    'Division, names and state copied from hcp_divisions.csv are dropped; readers join on npi.',
    'Columns renamed to say what they hold: non_drug instead of medical, _medicare_payment_estimate_usd for sums of '
    'average times services, total_drug_cost for the Part D sum.',
    'The lymphoma-product sum is taken from the scripted pinned drug-row pull with an explicit non-facility filter, '
    'and its rows were confirmed cell by cell against pinned national code queries, including a pull by this module '
    'of the 14 codes the search had fetched without a script (obinutuzumab and bendamustine among them).',
    'The 46,191 NPI x code x place-of-service drug rows are published as their own table with code numbers only, so '
    'two of the four sums can be recomputed from published rows.',
    'Requests made by this module are saved one response per file with sha256 and UTC retrieval time.',
    'The drug-row and product sums are defined as the sum of row estimates rounded to cents, so they reproduce '
    'exactly from the published drug rows. All four sums equal the search\'s per-NPI values to the cent on every NPI.',
]
DEFECTS_OPEN = [
    'Request times and per-request hashes were not captured for the 2019-2023 by-Provider pulls and the 2024 '
    'drug-row pull of the search; only the saved response files, their sha256 and their file times exist.',
    'The 2019-2023 pulls and the 2024 pull by this module carry 12 columns, not the full CMS record.',
    'About 10% of 2019-2023 rows (provider types other than the three oncology types) were confirmed only by '
    'batch-level checks and a random batch per year, not by an independent full pull.',
    'hcp_divisions.csv leaves cms_provider_type_2024 and medicare_beneficiaries_2024 blank for 856 NPIs that do have '
    'a 2024 by-Provider row outside the four oncology claims specialties; the by-year table has them. The 2026-10-24 '
    'table is not changed by this module.',
    'The search report labels a column "Drug share of total" although it is drug share of the published split, and '
    'counts NPIs with published drug payment as NPIs billing any Part B drug; the per-patient figures of the gap note '
    'mix two beneficiary denominators. The tables allow each to be recomputed; the notes were not edited.',
    'The Part D rows were not collected again; the sum reproduces from the tracked 2026-10-24 table.',
    'The CMS suppression codes * and # are published as CMS writes them; a reader that treats # as a comment mark '
    'must be told not to. by_provider_status carries the same information in words.',
    'The search requested drug rows for NPIs after the first 2,480 in sorted order only when the 2024 drug payment '
    'was published and above zero; the others were not queried again. Their blank follows from the CMS release rule '
    'and partb_drug_rows_status gives the reason (drug_payment_zero, split_suppressed or no_by_provider_row).',
    'Whether CMS will revise the re-released 2019-2023 files again is unknown; each row carries the version '
    'identifier it was read from.',
    'cms_provider_type is published per NPI and year because it changes between years. Its 2024 value repeats '
    'cms_provider_type_2024 of hcp_divisions.csv for the 14,059 NPIs filled there; check() requires the two to agree.',
]


# ---------------------------------------------------------------------------------------------------------------
# Validation on tracked tables only

CHECKS = ['tables_missing', 'by_year_grid_incomplete', 'by_year_duplicate_keys', 'by_year_npi_not_universe',
          'by_year_status_invalid', 'by_year_version_id_wrong', 'by_year_cell_not_numeric',
          'by_year_no_row_cell_filled', 'by_year_row_cell_blank', 'by_year_status_disagrees_with_indicators',
          'by_year_suppressed_split_filled', 'by_year_published_split_blank', 'by_year_total_not_drug_plus_non_drug',
          'by_year_amount_not_positive', 'by_year_drug_zero_rule_broken', 'by_year_beneficiaries_under_11',
          'by_year_beneficiaries_inconsistent', 'by_year_rows_with_record_changed',
          'by_year_suppressed_count_changed', 'by_year_2024_division_total_changed',
          'by_year_2024_drug_biller_count_changed', 'by_year_2024_disagrees_with_universe_table',
          'drug_rows_duplicate_keys', 'drug_rows_npi_not_universe', 'drug_rows_cell_invalid',
          'drug_rows_estimate_wrong', 'drug_rows_family_wrong', 'drug_rows_count_changed',
          'features_npi_set_not_universe', 'features_duplicate_keys', 'features_cell_not_numeric',
          'features_status_wrong', 'features_drug_rows_sum_wrong', 'features_product_sum_wrong',
          'features_product_above_drug_rows', 'features_drug_rows_above_by_provider_drug',
          'features_admin_above_by_provider_total', 'features_admin_marker_disagrees', 'features_part_d_sum_wrong',
          'features_part_d_marker_disagrees', 'features_core_drug_rows_differ', 'features_headline_changed',
          'headline_figure_changed', 'by_year_content_changed', 'features_content_changed',
          'drug_rows_content_changed']
CONTENT_CHECKS = dict(zip(TABLES, CHECKS[-3:]))


def check(processed):
    """Check name to error count for the three tables, reading only files under the processed directory."""
    out = dict.fromkeys(CHECKS, 0)
    paths = {table: processed / SNAPSHOT / f'{table}.csv' for table in TABLES}
    people_path = processed / UNIVERSE_SNAPSHOT / 'hcp_divisions.csv'
    out['tables_missing'] = sum(not p.is_file() for p in [*paths.values(), people_path])
    if out['tables_missing']:
        return {f'{MODULE}.{name}': count for name, count in out.items()}
    people = {row['npi']: row for row in read_csv(people_path)}
    groups = group_of(people)
    year_rows, features, drug_rows = (read_csv(paths[table]) for table in TABLES)

    # NPI x service year grid
    keys = {(r['npi'], r['service_year']) for r in year_rows}
    grid = {(npi, str(year)) for npi in people for year in YEARS}
    out['by_year_grid_incomplete'] = len(grid ^ keys)
    out['by_year_duplicate_keys'] = duplicates(year_rows, ['npi', 'service_year'])
    out['by_year_npi_not_universe'] = len({r['npi'] for r in year_rows} ^ set(people))
    with_record, suppressed, totals, billers = Counter(), Counter(), Counter(), Counter()
    cell_2024 = {}   # 2024 cells with a valid status and numeric cells; the feature checks read these
    for r in year_rows:
        status, year = r['by_provider_status'], r['service_year']
        if status not in STATUSES or not year.isdigit() or int(year) not in YEARS:
            out['by_year_status_invalid'] += 1
            continue
        year = int(year)
        out['by_year_version_id_wrong'] += r['source_dataset_version_id'] != BY_PROVIDER_VERSIONS[year]
        base_cells = [r['cms_provider_type'], r['total_medicare_payment_usd'], r['total_beneficiaries']]
        split_cells = [r['drug_medicare_payment_usd'], r['non_drug_medicare_payment_usd'], r['drug_beneficiaries'],
                       r['non_drug_beneficiaries']]
        marks = (r['drug_suppression_indicator'], r['non_drug_suppression_indicator'])
        amounts, counts = [base_cells[1], *split_cells[:2]], [base_cells[2], *split_cells[2:]]
        if not (all(MONEY.fullmatch(v) for v in amounts if v) and all(COUNT.fullmatch(v) for v in counts if v)):
            out['by_year_cell_not_numeric'] += 1
            continue
        if year == 2024:
            cell_2024[r['npi']] = r
        if status == 'no_row':
            out['by_year_no_row_cell_filled'] += any(base_cells + split_cells + list(marks))
            continue
        with_record[year] += 1
        out['by_year_row_cell_blank'] += not all(base_cells)
        out['by_year_status_disagrees_with_indicators'] += (
            marks not in INDICATOR_PAIRS or any(marks) != (status == 'split_suppressed'))
        if not all(base_cells):
            continue
        total, total_people = cents(r['total_medicare_payment_usd']), int(r['total_beneficiaries'])
        out['by_year_amount_not_positive'] += total <= 0
        out['by_year_beneficiaries_under_11'] += total_people < 11
        if groups.get(r['npi']) and year == 2024:
            totals[groups[r['npi']]] += total
        if status == 'split_suppressed':
            suppressed[year] += 1
            out['by_year_suppressed_split_filled'] += any(split_cells)
            continue
        if not all(split_cells):
            out['by_year_published_split_blank'] += 1
            continue
        drug, non_drug = cents(r['drug_medicare_payment_usd']), cents(r['non_drug_medicare_payment_usd'])
        out['by_year_total_not_drug_plus_non_drug'] += abs(total - drug - non_drug) > 1
        out['by_year_amount_not_positive'] += drug < 0 or non_drug < 0
        drug_people, non_drug_people = int(r['drug_beneficiaries']), int(r['non_drug_beneficiaries'])
        out['by_year_drug_zero_rule_broken'] += (drug == 0) != (drug_people == 0) or (drug > 0 and drug_people < 11)
        out['by_year_beneficiaries_under_11'] += non_drug_people < 11
        # Each side serves a subset of the NPI's beneficiaries and every beneficiary is on at least one side.
        out['by_year_beneficiaries_inconsistent'] += (max(drug_people, non_drug_people) > total_people
                                                      or total_people > drug_people + non_drug_people)
        if groups.get(r['npi']) and year == 2024 and drug > 0:
            billers[groups[r['npi']]] += 1
    out['by_year_rows_with_record_changed'] = sum(with_record[y] != EXPECTED_ROWS_WITH_RECORD[y] for y in YEARS)
    out['by_year_suppressed_count_changed'] = sum(suppressed[y] != EXPECTED_SUPPRESSED[y] for y in YEARS)
    out['by_year_2024_division_total_changed'] = sum(million(totals[g]) != expected
                                                     for g, expected in EXPECTED_2024_TOTAL_MILLION.items())
    out['by_year_2024_drug_biller_count_changed'] = sum(billers[g] != expected for g, expected
                                                        in EXPECTED_2024_PUBLISHED_DRUG_BILLERS.items())
    # The universe table already holds the 2024 provider type and beneficiary count of some NPIs.
    for npi, person in people.items():
        known_type = person.get('cms_provider_type_2024', '')
        known_people = person.get('medicare_beneficiaries_2024', '')
        cell = cell_2024.get(npi, {})
        out['by_year_2024_disagrees_with_universe_table'] += (
            bool(known_type) and cell.get('cms_provider_type') != known_type
            or bool(known_people) and cell.get('total_beneficiaries') != whole(known_people))

    # Drug rows
    out['drug_rows_duplicate_keys'] = duplicates(drug_rows, ['npi', 'hcpcs_code', 'place_of_service'])
    out['drug_rows_npi_not_universe'] = len({r['npi'] for r in drug_rows} - set(people))
    valid_rows = []
    for r in drug_rows:
        try:
            valid = (len(r['hcpcs_code']) == 5 and r['hcpcs_code'].isalnum() and r['place_of_service'] in ('O', 'F')
                     and int(r['beneficiaries']) >= 11 and Decimal(r['services_or_units']) > 0
                     and Decimal(r['average_medicare_payment_usd']) >= 0
                     and r['source_dataset_version_id'] == SERVICE_VERSION
                     and bool(MONEY.fullmatch(r['estimated_medicare_payment_usd'])))
        except (ArithmeticError, ValueError):
            valid = False
        if not valid:
            out['drug_rows_cell_invalid'] += 1
            continue
        valid_rows.append(r)
        out['drug_rows_estimate_wrong'] += (
            estimate(r['average_medicare_payment_usd'], r['services_or_units']) != r['estimated_medicare_payment_usd'])
        out['drug_rows_family_wrong'] += r['lymphoma_product_family'] != PRODUCT_FAMILIES.get(r['hcpcs_code'], '')
    out['drug_rows_count_changed'] = int(len(drug_rows) != EXPECTED_DRUG_ROWS)

    # One row per NPI with four sums
    out['features_npi_set_not_universe'] = len({r['npi'] for r in features} ^ set(people))
    out['features_duplicate_keys'] = duplicates(features, ['npi'])
    row_sum = total_by_npi(valid_rows, 'estimated_medicare_payment_usd')
    product_sum = total_by_npi(filter(is_office_product, valid_rows), 'estimated_medicare_payment_usd')
    part_d_path = processed / UNIVERSE_SNAPSHOT / 'part_d_lymphoma_orals_2024.csv'
    part_d_sum = total_by_npi(read_csv(part_d_path), 'total_drug_cost_usd') if part_d_path.is_file() else None
    out['features_part_d_sum_wrong'] += part_d_sum is None
    # Core NPIs had every 2024 code row collected one by one on 2026-10-15: an independent copy of their drug rows.
    core_path = processed / '2026-10-15' / 'cms_provider_service.csv'
    core_sum = (total_by_npi((r for r in read_csv(core_path) if r['cms_hcpcs_drug_indicator'] == 'Y'),
                             'estimated_medicare_payment_usd') if core_path.is_file() else None)
    out['features_core_drug_rows_differ'] += core_sum is None
    sums, counts = Counter(), Counter()
    for r in features:
        npi, cell = r['npi'], cell_2024.get(r['npi'])
        if cell is None or npi not in people or (cell['by_provider_status'] == 'published_split'
                                                 and not cell['drug_medicare_payment_usd']):
            continue   # the grid or row rules above already count this NPI
        if not all(MONEY.fullmatch(r[name]) for name in SUMS if r[name]):
            out['features_cell_not_numeric'] += 1
            continue
        values = {name: cents(r[name]) for name in SUMS}
        for name, value in values.items():
            if value is not None:
                sums[name] += value
                counts[name] += 1
        has_rows = values[SUMS[0]] is not None
        drug = cents(cell['drug_medicare_payment_usd'])
        if has_rows and not (cell['by_provider_status'] == 'published_split' and drug > 0):
            out['features_status_wrong'] += 1
        else:
            out['features_status_wrong'] += (
                r['partb_drug_rows_status'] != drug_rows_status(cell, has_rows)
                or r['service_product_data_status'] != ('released_cells' if any(r[name] for name in SUMS)
                                                        else 'no_released_cells'))
        out['features_drug_rows_sum_wrong'] += values[SUMS[0]] != row_sum.get(npi)
        out['features_product_sum_wrong'] += values[SUMS[1]] != product_sum.get(npi)
        out['features_product_above_drug_rows'] += (values[SUMS[1]] or 0) > (values[SUMS[0]] or 0)
        # CMS-rounded averages can overshoot the published payment slightly; the specification allows 0.1%.
        out['features_drug_rows_above_by_provider_drug'] += has_rows and (drug is None
                                                                          or values[SUMS[0]] * 1000 > drug * 1001)
        total = cents(cell['total_medicare_payment_usd'])
        out['features_admin_above_by_provider_total'] += values[SUMS[2]] is not None and (
            total is None or values[SUMS[2]] * 1000 > total * 1001)
        person = people[npi]
        out['features_admin_marker_disagrees'] += (
            (values[SUMS[2]] is not None) != (int(person['chemo_administration_office_rows'] or 0) > 0))
        out['features_part_d_marker_disagrees'] += (
            (values[SUMS[3]] is not None) != (int(person['part_d_lymphoma_oral_rows'] or 0) > 0))
        if part_d_sum is not None:
            out['features_part_d_sum_wrong'] += values[SUMS[3]] != part_d_sum.get(npi)
        if groups[npi] == 'core' and core_sum is not None:
            out['features_core_drug_rows_differ'] += values[SUMS[0]] != core_sum.get(npi)
    out['features_headline_changed'] = sum(
        counts[name] != EXPECTED_FEATURE_NPIS[name] or million(sums[name]) != EXPECTED_FEATURE_MILLION[name]
        for name in SUMS)

    # Quoted figures to the cent, on tables that passed every rule above (a broken table is already counted).
    if not any(out.values()):
        out['headline_figure_changed'] = figures_differing(EXPECTED_HEADLINE,
                                                           headline(year_rows, features, drug_rows, groups))
    # Any cell that differs from the snapshot as built, which is the only guard against values swapped between NPIs.
    for table, rows in zip(TABLES, (year_rows, features, drug_rows)):
        out[CONTENT_CHECKS[table]] = int(fingerprint(rows) != TABLE_SHA256[table])
    return {f'{MODULE}.{name}': int(count) for name, count in out.items()}


if __name__ == '__main__':
    build()
