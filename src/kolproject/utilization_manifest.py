"""New-source inventories and field definitions, separate from the old cohort."""
from .common import ROOT, load, dump, read_csv, write_csv
from .utilization_source import RAW, INTERIM, OUT, SNAPSHOT, sha
from .utilization import GROUPS

DESCRIPTIONS = {
    'hcp_id': 'Existing HCP key; foreign key to the preserved 2026-10-09 HCP table.',
    'npi': 'Exact ten-digit individual NPI, stored as text. No fuzzy claims join.',
    'service_year': 'Year in which services occurred: 2024, not the collection year.',
    'source_dataset_version_id': 'Pinned CMS annual dataset UUID, not the moving latest alias.',
    'source_release_date': 'CMS catalog modified date for this annual release: 2026-05-21.',
    'collection_snapshot_date': 'Collection-run date; exact request timestamps remain authoritative.',
    'cohort_snapshot_date': 'Date of preserved HCP cohort; not a historical 2024 roster.',
    'feature_version': 'Version of aggregation and missingness definitions.',
    'code_list_version': 'Version of year-specific code definitions.',
    'code_list_sha256': 'SHA-256 of sorted-key JSON encoding of the code evidence list.',
    'query_status': 'complete only after all pages and a terminal empty page; failure blocks publication.',
    'utilization_data_status': 'selected_codes_observed, published_services_no_selected_codes, or no_published_services. None implies confirmed zero activity.',
    'has_any_published_service': '1 if any released row exists, otherwise 0. Observation flag, not clinical inactivity.',
    'has_any_selected_code': '1 if any selected code is published, including administration across indications.',
    'has_any_selected_drug_code': '1 if a selected product code is published; excludes administration-only code.',
    'published_service_rows': 'Number of released code-setting rows, not patients or treatment sessions.',
    'published_hcpcs_count': 'Distinct published HCPCS codes for this NPI.',
    'published_facility_rows': 'Number of released rows in F setting; not number of visits.',
    'published_nonfacility_rows': 'Number of released rows in O setting; not number of visits.',
    'selected_codes_observed': 'Semicolon-separated selected codes observed for this NPI.',
    'cms_provider_types_observed': 'Claims-derived specialty descriptions; distinct from current NPPES taxonomy.',
    'all_published_medicare_payment_estimate_usd': 'Sum of units times average Medicare payment over released rows; rounded once to cents. Approximate, not full practice revenue.',
    'all_published_allowed_amount_estimate_usd': 'Sum of units times average allowed amount over released rows; not patient out-of-pocket spending.',
    'selected_drug_medicare_payment_estimate_usd': 'Estimated payment over selected product rows only; blank if unobserved.',
    'selected_drug_facility_payment_share': 'Estimated selected-product payment in F divided by all-setting selected-product payment; blank if denominator absent or zero.',
    'source_request_ids': 'Raw response keys, including the terminal empty-page query.',
    'hcpcs_code': 'CMS service/product code as text, preserving leading zero in 0540T.',
    'hcpcs_description': 'CMS utilization response description; source coding terms apply.',
    'place_of_service': 'F = facility; O = non-facility. Not the original detailed two-digit setting code.',
    'cms_hcpcs_drug_indicator': 'ASP-list-based CMS indicator; not a complete classifier of CAR-T products.',
    'beneficiaries': 'Distinct beneficiaries within this NPI-code-setting cell only; not additive across cells.',
    'services_or_units': 'CMS Tot_Srvcs; drug billing units may differ from treatment sessions.',
    'beneficiary_days': 'CMS Tot_Bene_Day_Srvcs; distinct beneficiary-days within this code-setting cell.',
    'average_submitted_charge_usd': 'Published average submitted charge per service/unit; not amount received.',
    'average_allowed_amount_usd': 'Published average allowed amount per service/unit; not patient copay.',
    'average_medicare_payment_usd': 'Published average Medicare payment per service/unit.',
    'average_standardized_medicare_payment_usd': 'Published average Medicare payment after CMS geographic/payment standardization.',
    'feature_group': 'Selected analytical family, or other for codes outside this narrow list.',
    'selected_code': '1 if code is in the selected 2024 list, otherwise 0.',
    'selected_drug_code': '1 for selected product codes, 0 for all others including administration.',
    'billing_unit_for_selected_code': 'Codebook unit; blank for unselected codes.',
    'estimated_medicare_payment_usd': 'services_or_units times average_medicare_payment_usd, rounded to cents.',
    'estimated_allowed_amount_usd': 'services_or_units times average_allowed_amount_usd, rounded to cents.',
    'source_id': 'Raw-response key linking to utilization_source_requests.csv.',
    'source_url': 'Exact public API request that returned this row.',
}
SUFFIXES = {
    '_observed': '1 if a family row is published; 0 means unobserved, not confirmed zero activity.',
    '_published_rows': 'Count of published family code-setting rows.',
    '_services_or_units_sum': 'Sum of common-unit services within this family; see codebook. Blank when unobserved.',
    '_beneficiary_code_setting_days_sum': 'Sum of cell beneficiary-days; the same patient-day may repeat across codes/settings.',
    '_max_beneficiaries_in_one_cell': 'Largest beneficiary count in one published family cell; not total unique patients.',
    '_medicare_payment_estimate_usd': 'Estimated payment over published family rows, rounded after summing; blank when unobserved.',
}

def make_manifests():
    dest = ROOT / 'Data' / 'manifests' / SNAPSHOT
    dest.mkdir(parents=True, exist_ok=True)
    sources = [load(p) for p in sorted(RAW.rglob('*.meta.json'))]
    write_csv(dest / 'utilization_source_requests.csv', sources)
    paths = [p for base in [RAW, INTERIM, OUT] for p in sorted(base.rglob('*')) if p.is_file() and p.suffix != '.tmp']
    paths += [ROOT / 'configs' / 'utilization.yaml', ROOT / 'configs' / 'utilization_codes_2024.json']
    write_csv(dest / 'utilization_file_checksums.csv', [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in paths])
    dictionary = {}
    for table in ['cms_provider_service', 'hcp_utilization_features']:
        fields = list(read_csv(OUT / (table + '.csv'))[0])
        entries = []
        for field in fields:
            description = DESCRIPTIONS.get(field)
            if description is None:
                for group in GROUPS:
                    if field.startswith(group + '_'):
                        description = group.replace('_', ' ') + ': ' + SUFFIXES[field[len(group):]]
                        break
            if description is None and field.endswith('_at_utc'):
                description = field.replace('_', ' ') + '; ISO 8601 UTC with Z. Retrieval, query completion and feature construction are distinct.'
            if description is None and field.startswith('cms_'):
                description = field.replace('_', ' ') + ' as observed in the 2024 source; not necessarily current employment or credentials.'
            assert description, 'Missing definition: ' + field
            entries.append({'column': field, 'description': description,
                            'missing_value': 'Blank = unavailable or unobserved; zero observation flags/counts do not imply zero clinical activity.'})
        dictionary[table] = entries
    dump(dest / 'utilization_data_dictionary.json', dictionary)
    print('UTILIZATION MANIFESTS', len(sources), 'SOURCE REQUESTS', len(paths), 'FILES')
