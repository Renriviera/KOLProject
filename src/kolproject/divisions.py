"""Referring and affiliate HCP divisions around the preserved 500-HCP core.

Builds a CAR-T center set keyed on hospital CCN, a 2024 hem/onc universe keyed
on NPI, structural affiliation edges, treatment markers, and one label per NPI.
Labels are evidence tiers, not measures of referral volume or influence.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import re
import zipfile
from .common import ROOT, dump, load, now, read_csv, write_csv, norm, config_input
from .divisions_source import (SNAPSHOT, RAW, INTERIM, OUT, COHORT, DATASETS, CLAIMS_SPECIALTIES, CART_DRG,
                               MARKER_CODES, PART_D_GENERICS, BULK_FILES, sha, collect, register_bulk)

LABEL_VERSION = '1.0.0'
PHYSICIAN_SPECIALTIES = {'HEMATOLOGY/ONCOLOGY', 'MEDICAL ONCOLOGY', 'HEMATOLOGY',
                         'HEMATOPOIETIC CELL TRANSPLANTATION AND CELLULAR THERAPY'}
BRIDGE_SPECIALTIES = {'INTERNAL MEDICINE', 'PEDIATRIC MEDICINE'}
APP_SPECIALTIES = {'NURSE PRACTITIONER', 'PHYSICIAN ASSISTANT', 'CERTIFIED CLINICAL NURSE SPECIALIST (CNS)'}
BTKI = {'ibrutinib', 'acalabrutinib', 'zanubrutinib', 'pirtobrutinib'}
CODE_GROUP = {code: group for group, codes in MARKER_CODES.items() for code in codes}
STATES = {'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA', 'colorado': 'CO',
          'connecticut': 'CT', 'delaware': 'DE', 'district of columbia': 'DC', 'florida': 'FL', 'georgia': 'GA',
          'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
          'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD', 'massachusetts': 'MA',
          'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT',
          'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM',
          'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
          'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC', 'south dakota': 'SD',
          'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA',
          'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY', 'puerto rico': 'PR'}
ALIASES = {'ucsf': 'university california san francisco', 'ucla': 'university california los angeles',
           'ucsd': 'university california san diego', 'uc': 'university california', 'mskcc': 'memorial sloan kettering',
           'msk': 'memorial sloan kettering', 'unc': 'university north carolina', 'ohsu': 'oregon health science university',
           'uab': 'university alabama birmingham', 'upmc': 'university pittsburgh medical', 'nyu': 'new york university',
           'usc': 'university southern california', 'osu': 'ohio state university', 'vcu': 'virginia commonwealth university',
           'mcw': 'medical college wisconsin', 'uw': 'university washington', 'uva': 'university virginia',
           'umass': 'university massachusetts', 'ut': 'university texas', 'utsw': 'university texas southwestern',
           'uk': 'university kentucky', 'ku': 'university kansas', 'mgh': 'massachusetts general hospital',
           'bwh': 'brigham womens hospital', 'penn': 'pennsylvania', 'hup': 'hospital university pennsylvania',
           'st': 'saint', 'mt': 'mount', 'childrens': 'children'}
STOPWORDS = {'the', 'of', 'at', 'and', 'for', 'in', 'a', 'an', 'program', 'programs', 'blood', 'marrow', 'bone',
             'transplant', 'transplantation', 'cellular', 'cell', 'therapy', 'therapies', 'stem', 'adult', 'pediatric',
             'hematopoietic', 'immune', 'effector', 'bmt', 'hct', 'services', 'service', 'inc', 'llc', 'pc', 'corp',
             'corporation', 'authority', 'association', 'dba', 'foundation', 'trustees', 'board', 'regents',
             'hematology', 'oncology', 'hematologic', 'malignancies', 'malignancy', 'leukemia', 'lymphoma', 'myeloma'}
GENERIC = {'university', 'medical', 'health', 'healthcare', 'system', 'regional', 'hospital', 'hospitals', 'center',
           'centers', 'cancer', 'clinic', 'clinics', 'institute', 'care', 'general', 'memorial', 'community',
           'presbyterian', 'methodist', 'baptist', 'saint', 'medicine', 'college', 'school', 'physicians',
           'children', 'network', 'city', 'county', 'state', 'national', 'american', 'west', 'east', 'north',
           'south', 'central', 'new', 'york'}

def name_tokens(value):
    words = re.findall(r'[a-z0-9]+', norm(value).replace('&', ' and ').replace('-', ' ').replace("'", ''))
    expanded = []
    for word in words:
        expanded.extend(ALIASES.get(word, word).split())
    return [w for w in expanded if w not in STOPWORDS and not w.isdigit()]

def similarity(program, hospital):
    """Share of program tokens found in the hospital name, requiring a distinctive shared token."""
    p, h = set(name_tokens(program)), set(name_tokens(hospital))
    if not p or not h:
        return 0.0
    common = p & h
    if not (common - GENERIC):
        return 0.0
    return len(common) / len(p)

def eligible_specialty(primary, secondary_all):
    """Physician or APP whose PECOS specialties identify hematology or medical oncology practice."""
    secondary = {s.strip() for s in (secondary_all or '').split(',') if s.strip()}
    if primary in PHYSICIAN_SPECIALTIES:
        return 'physician'
    if primary in BRIDGE_SPECIALTIES and secondary & PHYSICIAN_SPECIALTIES:
        return 'physician'
    if primary in APP_SPECIALTIES and secondary & PHYSICIAN_SPECIALTIES:
        return 'app'
    return ''

def label(row):
    """Evidence tier for one NPI; the order of tests defines the divisions."""
    if row['is_core']:
        return 'core'
    if not row['specialty_eligible']:
        return 'not_eligible'
    if row['core_ccn_links'] and row['core_org_links']:
        return 'affiliate_A'
    if row['core_ccn_links'] or row['core_org_links']:
        return 'affiliate_B'
    if row['center_ccn_links']:
        return 'center_affiliate_unlinked'
    if row['core_building_links']:
        return 'affiliate_C'
    if row['role'] == 'physician' and row['marker_count']:
        return 'referring_proxy'
    return 'hemonc_other'

def match_program(program, hospitals_by_state, core_affiliated):
    """Best hospital CCN for a FACT program: same state, prefer same city, then core-HCP evidence."""
    best = None
    for state in program['states']:
        code = STATES.get(state.lower())
        for ccn, hospital in hospitals_by_state.get(code, {}).items():
            score = max((similarity(program['program_name'], name) for name in hospital['names']), default=0.0)
            if score < 0.5:
                continue
            same_city = any(norm(c) == norm(hospital['city']) for c in program['cities'])
            key = (score >= 0.5, same_city, score, core_affiliated.get(ccn, 0))
            if best is None or key > best[0]:
                best = (key, ccn, score, same_city)
    if best is None:
        return {'matched_ccn': '', 'match_method': 'unmatched', 'match_score': ''}
    _, ccn, score, same_city = best
    return {'matched_ccn': ccn, 'match_method': 'name_city' if same_city else 'name_state', 'match_score': f'{score:.2f}'}

def query_rows(name):
    result = load(INTERIM / 'queries' / (name + '.json'))
    assert result['query_status'] == 'complete', name
    return result['records'], result

def bulk_rows(name, encoding='utf-8-sig'):
    path = RAW / 'bulk' / name
    with path.open(newline='', encoding=encoding, errors='replace') as stream:
        yield from csv.DictReader(stream)

def hrr_lookup():
    with zipfile.ZipFile(RAW / 'bulk' / 'ZipHsaHrr19.csv.zip') as archive:
        text = archive.read('ZipHsaHrr19.csv').decode('utf-8-sig')
    return {r['zipcode19']: r for r in csv.DictReader(io.StringIO(text))}

def build():
    built_at = now()
    bulk = register_bulk()
    core = {h['npi']: h for h in read_csv(COHORT)}
    assert len(core) == 500
    status = load(INTERIM / 'collection_status.json')
    assert not status['failed_queries']
    hrr = hrr_lookup()

    # --- hospitals: CCN -> names, city, state, zip (enrollment file + Open Payments aliases)
    hospitals = {}
    for r in bulk_rows('Hospital_Enrollments_2026.09.29.csv', 'latin-1'):
        if not r['CCN'].isdigit():
            continue  # sub-unit identifiers (letters) never appear in Facility Affiliation
        h = hospitals.setdefault(r['CCN'], {'ccn': r['CCN'], 'names': set(), 'organization_name': '', 'dba_name': '',
                                           'city': '', 'state': '', 'zip5': '', 'organization_npi': ''})
        main = r['PRACTICE LOCATION TYPE'] == 'MAIN/PRIMARY HOSPITAL LOCATION'
        if main or not h['organization_name']:
            h.update(organization_name=r['ORGANIZATION NAME'], dba_name=r['DOING BUSINESS AS NAME'], city=r['CITY'],
                     state=r['STATE'], zip5=r['ZIP CODE'][:5], organization_npi=r['NPI'])
        h['names'] |= {r['ORGANIZATION NAME'], r['DOING BUSINESS AS NAME']} - {''}
    for r in bulk_rows('PBLCTN_TH_PRFL_SRCH_P06302026_06032026.csv'):
        h = hospitals.setdefault(r['Teaching_Hospital_CCN'], {'ccn': r['Teaching_Hospital_CCN'], 'names': set(),
                                 'organization_name': r['Teaching_Hospital_NAME'], 'dba_name': '', 'city': r['Teaching_Hospital_CITY'],
                                 'state': r['Teaching_Hospital_STATE'], 'zip5': r['Teaching_Hospital_ZIP_CODE'][:5], 'organization_npi': ''})
        h['names'] |= {r['Teaching_Hospital_NAME']} | {r[f'Teaching_Hospital_Alternate_Name{i}'] for i in range(1, 6)} - {''}
    by_state = collections.defaultdict(dict)
    for ccn, h in hospitals.items():
        by_state[h['state']][ccn] = h

    # --- DRG 018 hospitals
    drg_rows, drg_query = query_rows('inpatient_drg018')
    drg = {}
    for r in drg_rows:
        assert r['DRG_Cd'] == CART_DRG
        drg[r['Rndrng_Prvdr_CCN']] = r
        hospitals.setdefault(r['Rndrng_Prvdr_CCN'], {'ccn': r['Rndrng_Prvdr_CCN'], 'names': set(), 'organization_name': r['Rndrng_Prvdr_Org_Name'],
                             'dba_name': '', 'city': r['Rndrng_Prvdr_City'], 'state': r['Rndrng_Prvdr_State_Abrvtn'],
                             'zip5': r['Rndrng_Prvdr_Zip5'], 'organization_npi': ''})['names'].add(r['Rndrng_Prvdr_Org_Name'])

    # --- universe from claims specialties and core
    providers = {}
    provider_sources = {}
    for name in sorted(p.stem for p in (INTERIM / 'queries').glob('provider_*.json')):
        rows, result = query_rows(name)
        for r in rows:
            providers[str(r['Rndrng_NPI'])] = r
            provider_sources[str(r['Rndrng_NPI'])] = name
    claims_typed = {npi for npi, r in providers.items() if r['Rndrng_Prvdr_Type'] in CLAIMS_SPECIALTIES}
    seed = claims_typed | set(core)

    # --- Facility Affiliation pass 1: core hospital links (needed for FACT matching evidence)
    affiliations = collections.defaultdict(set)
    for r in bulk_rows('Facility_Affiliation.csv'):
        if r['facility_type'] == 'Hospital':
            affiliations[r['NPI']].add(r['Facility Affiliations Certification Number'])
    core_affiliated = collections.Counter(ccn for npi in core for ccn in affiliations.get(npi, ()))

    # --- FACT programs -> CCN
    fact = load(INTERIM / 'fact_programs.json')
    overrides = {r['program_url']: r for r in read_csv(config_input('cart_center_overrides.csv'))}
    fact_rows = []
    for p in fact['programs']:
        match = match_program(p, by_state, core_affiliated)
        override = overrides.get(p['program_url'])
        if override and override['ccn']:
            match = {'matched_ccn': override['ccn'], 'match_method': 'reviewed_override', 'match_score': ''}
        elif override:
            match = {'matched_ccn': '', 'match_method': 'reviewed_no_ccn', 'match_score': ''}
        hospital = hospitals.get(match['matched_ccn'], {})
        fact_rows.append({'program_url': p['program_url'], 'program_name': p['program_name'],
                          'organization_director': p['organization_director'], 'states': ';'.join(p['states']),
                          'cities': ';'.join(p['cities']), 'clinical_services': ';'.join(p['clinical_services']),
                          'transplant_types': ';'.join(p['transplant_types']), **match,
                          'matched_hospital_name': hospital.get('organization_name', ''),
                          'matched_hospital_city': hospital.get('city', ''),
                          'core_hcps_affiliated_with_matched_ccn': core_affiliated.get(match['matched_ccn'], 0) if match['matched_ccn'] else '',
                          'listing_page': p['listing_page'], 'source_id': p['source_id'], 'listing_url': fact['listing_url'],
                          'listing_observed_at_utc': fact['completed_at_utc']})
    fact_by_ccn = collections.defaultdict(list)
    for r in fact_rows:
        if r['matched_ccn']:
            fact_by_ccn[r['matched_ccn']].append(r['program_url'])
    center_set = set(drg) | set(fact_by_ccn)
    centers = []
    for ccn in sorted(center_set):
        h = hospitals[ccn]
        zip5 = h['zip5'] or drg.get(ccn, {}).get('Rndrng_Prvdr_Zip5', '')
        centers.append({'ccn': ccn, 'hospital_name': h['organization_name'], 'dba_name': h['dba_name'],
                        'city': h['city'], 'state': h['state'], 'zip5': zip5, 'hrr': hrr.get(zip5, {}).get('hrrnum', ''),
                        'hrr_city': hrr.get(zip5, {}).get('hrrcity', ''), 'organization_npi': h['organization_npi'],
                        'drg018_discharges_2024': drg[ccn]['Tot_Dschrgs'] if ccn in drg else '',
                        'fact_iec_program_count': len(fact_by_ccn.get(ccn, [])),
                        'fact_program_urls': ';'.join(fact_by_ccn.get(ccn, [])),
                        'evidence': ';'.join(e for e, ok in [('drg018_2024', ccn in drg), ('fact_iec_accredited', ccn in fact_by_ccn)] if ok),
                        'core_hcps_affiliated': core_affiliated.get(ccn, 0),
                        'drg018_source_dataset_version_id': DATASETS['inpatient_2024']['version_id'] if ccn in drg else '',
                        'collection_snapshot_date': SNAPSHOT})
    center_hrrs = {c['hrr'] for c in centers if c['hrr']}

    # --- Doctors and Clinicians NDF pass: group memberships and specialty-eligible NPIs
    groups = {}
    ndf_people = {}
    for r in bulk_rows('DAC_NationalDownloadableFile.csv'):
        npi = r['NPI']
        role = eligible_specialty(r['pri_spec'], r['sec_spec_all'])
        if npi not in seed and not role:
            continue
        person = ndf_people.setdefault(npi, {'npi': npi, 'ndf_last_name': r['Provider Last Name'], 'ndf_first_name': r['Provider First Name'],
                                             'ndf_credential': r['Cred'], 'ndf_primary_specialty': r['pri_spec'],
                                             'ndf_secondary_specialties': r['sec_spec_all'], 'ndf_role': role,
                                             'ndf_graduation_year': r['Grd_yr'], 'ndf_states': set(), 'ndf_first_zip5': r['ZIP Code'][:5]})
        person['ndf_states'].add(r['State'])
        if role and not person['ndf_role']:
            person.update(ndf_role=role, ndf_primary_specialty=r['pri_spec'], ndf_secondary_specialties=r['sec_spec_all'])
        key = (npi, r['org_pac_id'], r['adrs_id'])
        if key not in groups:
            groups[key] = {'npi': npi, 'org_pac_id': r['org_pac_id'], 'group_legal_name': r['Facility Name'],
                           'group_member_count': r['num_org_mem'], 'adrs_id': r['adrs_id'], 'building_key': r['adrs_id'][:-2],
                           'address_line_1': r['adr_ln_1'], 'city': r['City/Town'], 'state': r['State'], 'zip5': r['ZIP Code'][:5],
                           'group_assignment': r['grp_assgn']}
    universe = seed | set(ndf_people)
    core_orgs = {g['org_pac_id'] for g in groups.values() if g['npi'] in core and g['org_pac_id']}
    core_buildings = {g['building_key'] for g in groups.values() if g['npi'] in core and g['building_key']}
    core_ccns = {ccn for ccn, n in core_affiliated.items()} & center_set
    org_core_count = collections.Counter(g['org_pac_id'] for g in groups.values() if g['npi'] in core)

    # --- markers
    marker_rows, marker_flags = [], collections.defaultdict(lambda: collections.Counter())
    for group, codes in MARKER_CODES.items():
        for code in codes:
            rows, result = query_rows('service_' + code)
            for r in rows:
                npi = str(r['Rndrng_NPI'])
                if npi not in universe:
                    continue
                marker_rows.append({'npi': npi, 'hcpcs_code': r['HCPCS_Cd'], 'hcpcs_description': r['HCPCS_Desc'],
                                    'place_of_service': r['Place_Of_Srvc'], 'marker_group': group,
                                    'beneficiaries': r['Tot_Benes'], 'services_or_units': r['Tot_Srvcs'],
                                    'beneficiary_days': r['Tot_Bene_Day_Srvcs'], 'cms_provider_type': r['Rndrng_Prvdr_Type'],
                                    'service_year': 2024, 'source_dataset_version_id': result['dataset_version_id'],
                                    'query_name': result['name']})
                f = marker_flags[npi]
                f[group] += 1
                if r['Place_Of_Srvc'] == 'O':
                    f[group + '_office'] += 1
    partd_rows, partd_flags = [], collections.defaultdict(lambda: collections.Counter())
    for generic in PART_D_GENERICS:
        rows, result = query_rows('part_d_' + generic.lower().replace(' ', '_'))
        family = generic.split()[0].lower()
        for r in rows:
            npi = str(r['Prscrbr_NPI'])
            if npi not in universe:
                continue
            partd_rows.append({'npi': npi, 'generic_name': r['Gnrc_Name'], 'brand_name': r['Brnd_Name'],
                               'total_claims': r['Tot_Clms'], 'total_30day_fills': r['Tot_30day_Fills'],
                               'total_beneficiaries': r['Tot_Benes'], 'total_drug_cost_usd': r['Tot_Drug_Cst'],
                               'prescriber_type': r['Prscrbr_Type'], 'service_year': 2024,
                               'source_dataset_version_id': result['dataset_version_id'], 'query_name': result['name']})
            f = partd_flags[npi]
            f['btki' if family in BTKI else family] += int(r['Tot_Clms'] or 0)
            f['rows'] += 1

    # --- affiliation edges for the universe
    edge_rows = []
    for npi in sorted(universe):
        for ccn in sorted(affiliations.get(npi, ())):
            h = hospitals.get(ccn, {})
            edge_rows.append({'npi': npi, 'ccn': ccn, 'hospital_name': h.get('organization_name', ''), 'state': h.get('state', ''),
                              'in_cart_center_set': int(ccn in center_set), 'core_hcps_affiliated': core_affiliated.get(ccn, 0),
                              'source': 'cms_doctors_and_clinicians_facility_affiliation', 'source_release_date': bulk['Facility_Affiliation.csv']['released']})
    group_rows = [{**g, 'core_hcps_in_group': org_core_count.get(g['org_pac_id'], 0),
                   'source': 'cms_doctors_and_clinicians_national_downloadable_file',
                   'source_release_date': bulk['DAC_NationalDownloadableFile.csv']['released']}
                  for g in sorted(groups.values(), key=lambda g: (g['npi'], g['org_pac_id'], g['adrs_id']))]

    # --- one row per NPI
    people = []
    for npi in sorted(universe):
        p = providers.get(npi, {})
        n = ndf_people.get(npi, {})
        ccns = affiliations.get(npi, set())
        my_groups = [g for g in groups.values() if g['npi'] == npi]
        role = n.get('ndf_role') or ('physician' if p.get('Rndrng_Prvdr_Type') in CLAIMS_SPECIALTIES else '')
        state = p.get('Rndrng_Prvdr_State_Abrvtn') or next(iter(sorted(n.get('ndf_states', []))), '') or core.get(npi, {}).get('npi_state', '')
        zip5 = p.get('Rndrng_Prvdr_Zip5') or n.get('ndf_first_zip5', '')
        flags = marker_flags.get(npi, collections.Counter())
        pd_flags = partd_flags.get(npi, collections.Counter())
        row = {
            'npi': npi, 'hcp_id': core[npi]['hcp_id'] if npi in core else '', 'is_core': int(npi in core),
            'last_name': p.get('Rndrng_Prvdr_Last_Org_Name') or n.get('ndf_last_name', '') or core.get(npi, {}).get('npi_last_name', ''),
            'first_name': p.get('Rndrng_Prvdr_First_Name') or n.get('ndf_first_name', '') or core.get(npi, {}).get('npi_first_name', ''),
            'credentials': p.get('Rndrng_Prvdr_Crdntls') or n.get('ndf_credential', ''),
            'role': role, 'specialty_eligible': int(bool(role)),
            'in_claims_specialty_universe': int(npi in claims_typed), 'in_ndf_specialty_universe': int(bool(n.get('ndf_role'))),
            'cms_provider_type_2024': p.get('Rndrng_Prvdr_Type', ''), 'ndf_primary_specialty': n.get('ndf_primary_specialty', ''),
            'ndf_secondary_specialties': n.get('ndf_secondary_specialties', ''),
            'state': state, 'city': p.get('Rndrng_Prvdr_City', ''), 'zip5': zip5,
            'hrr': hrr.get(zip5, {}).get('hrrnum', ''), 'hrr_city': hrr.get(zip5, {}).get('hrrcity', ''),
            'hrr_state': hrr.get(zip5, {}).get('hrrstate', ''), 'hrr_has_cart_center': int(hrr.get(zip5, {}).get('hrrnum', '') in center_hrrs) if zip5 in hrr else '',
            'ruca': p.get('Rndrng_Prvdr_RUCA', ''),
            'medicare_beneficiaries_2024': p.get('Tot_Benes', ''), 'medicare_services_2024': p.get('Tot_Srvcs', ''),
            'beneficiary_cancer_pct_2024': p.get('Bene_CC_PH_Cancer6_V2_Pct', ''), 'beneficiary_avg_risk_score_2024': p.get('Bene_Avg_Risk_Scre', ''),
            'hospital_ccns': ';'.join(sorted(ccns)), 'hospital_ccn_count': len(ccns),
            'center_ccn_links': len(ccns & center_set), 'core_ccn_links': len(ccns & core_ccns),
            'group_org_pac_ids': ';'.join(sorted({g['org_pac_id'] for g in my_groups if g['org_pac_id']})),
            'core_org_links': len({g['org_pac_id'] for g in my_groups if g['org_pac_id'] in core_orgs}),
            'core_building_links': len({g['building_key'] for g in my_groups if g['building_key'] in core_buildings}),
            'chemo_administration_rows': flags['chemo_administration'], 'chemo_administration_office_rows': flags['chemo_administration_office'],
            'rituximab_rows': flags['rituximab_iv'] + flags['rituximab_sc'], 'rituximab_office_rows': flags['rituximab_iv_office'] + flags['rituximab_sc_office'],
            'dlbcl_agent_rows': flags['dlbcl_agents'], 'dlbcl_agent_office_rows': flags['dlbcl_agents_office'],
            'part_d_lymphoma_oral_rows': pd_flags['rows'], 'part_d_btki_claims': pd_flags['btki'],
            'part_d_lenalidomide_claims': pd_flags['lenalidomide'], 'part_d_venetoclax_claims': pd_flags['venetoclax'],
            'part_d_tazemetostat_claims': pd_flags['tazemetostat'],
        }
        markers = [m for m, ok in [('office_chemo_administration', flags['chemo_administration_office']),
                                   ('rituximab_own_npi', row['rituximab_rows']), ('dlbcl_agent_own_npi', flags['dlbcl_agents']),
                                   ('part_d_lymphoma_oral', pd_flags['rows'])] if ok]
        row['treatment_markers'] = ';'.join(markers)
        row['marker_count'] = len(markers)
        row['division'] = label(row)
        row.update(label_version=LABEL_VERSION, service_year=2024, collection_snapshot_date=SNAPSHOT,
                   cohort_snapshot_date='2026-10-09', row_assembled_at_utc=built_at)
        people.append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / 'cart_centers.csv', centers)
    write_csv(OUT / 'fact_iec_programs.csv', fact_rows)
    write_csv(OUT / 'hcp_divisions.csv', people)
    write_csv(OUT / 'hcp_hospital_affiliations.csv', edge_rows)
    write_csv(OUT / 'hcp_group_memberships.csv', group_rows)
    write_csv(OUT / 'treatment_markers_2024.csv', marker_rows)
    write_csv(OUT / 'part_d_lymphoma_orals_2024.csv', partd_rows)
    divisions = collections.Counter(r['division'] for r in people)
    by_state = collections.Counter((r['division'], r['state']) for r in people)
    by_hrr = collections.Counter((r['division'], r['hrr']) for r in people if r['hrr'])
    core_rows = [r for r in people if r['is_core']]
    metadata = {
        'built_at_utc': built_at, 'collection_snapshot_date': SNAPSHOT, 'label_version': LABEL_VERSION,
        'service_year': 2024, 'cohort_path': str(COHORT.relative_to(ROOT)), 'cohort_sha256': sha(COHORT),
        'datasets': {k: {kk: vv for kk, vv in v.items()} for k, v in DATASETS.items()},
        'bulk_sources': {k: {kk: v[kk] for kk in ['source', 'source_url', 'released', 'retrieved_at_utc', 'http_last_modified', 'sha256', 'bytes']} for k, v in bulk.items()},
        'tables': {'cart_centers': len(centers), 'fact_iec_programs': len(fact_rows), 'hcp_divisions': len(people),
                   'hcp_hospital_affiliations': len(edge_rows), 'hcp_group_memberships': len(group_rows),
                   'treatment_markers_2024': len(marker_rows), 'part_d_lymphoma_orals_2024': len(partd_rows)},
        'center_set': {'drg018_hospitals': len(drg), 'fact_us_iec_programs': len(fact_rows),
                       'fact_programs_matched_to_ccn': sum(bool(r['matched_ccn']) for r in fact_rows),
                       'fact_match_methods': dict(collections.Counter(r['match_method'] for r in fact_rows)),
                       'center_ccns': len(center_set), 'center_ccns_with_core_hcp': len(core_ccns),
                       'center_hrrs': len(center_hrrs)},
        'universe': {'claims_specialty_npis': len(claims_typed), 'ndf_specialty_npis': sum(bool(p['ndf_role']) for p in ndf_people.values()),
                     'core_npis': len(core), 'total_npis': len(people)},
        'core_coverage': {'core_with_hospital_affiliation': sum(bool(r['hospital_ccns']) for r in core_rows),
                          'core_with_center_ccn': sum(bool(r['center_ccn_links']) for r in core_rows),
                          'core_with_group_membership': sum(bool(r['group_org_pac_ids']) for r in core_rows),
                          'core_in_claims_specialty': sum(r['in_claims_specialty_universe'] for r in core_rows),
                          'core_with_2024_provider_row': sum(npi in providers for npi in core)},
        'divisions': dict(divisions),
        'divisions_by_role': {f'{d}|{role}': n for (d, role), n in sorted(collections.Counter((r['division'], r['role']) for r in people).items())},
        'divisions_by_state': {f'{d}|{s}': n for (d, s), n in sorted(by_state.items())},
        'divisions_by_hrr': {f'{d}|{h}': n for (d, h), n in sorted(by_hrr.items())},
        'source_observed_from_utc': min(s['retrieved_at_utc'] for s in all_sources()),
        'source_observed_through_utc': max(s['retrieved_at_utc'] for s in all_sources()),
        'limitations': [
            'Divisions are evidence tiers from public Medicare enrollment and claims files; they are not referral volumes, employment records, or influence.',
            'The CAR-T center set is a floor: DRG 018 hides hospitals with 10 or fewer FFS discharges and FACT programs that could not be matched to a CCN are excluded until reviewed.',
            'Referring labels are proxies: no shared-patient edge is observed in this release.',
            'Treatment markers come from Medicare FFS Part B and Part D with 11-beneficiary suppression; absence is not evidence of no treatment.',
            'BTK inhibitors and lenalidomide skew to CLL, MCL, WM and myeloma; markers identify B-cell malignancy treaters, not DLBCL specifically.',
            'Hospital affiliation reflects a 12-month claims look-back and group membership reflects PECOS enrollment; both lag moves.',
            'Core HCPs absent from the 2024 provider file or the Doctors and Clinicians files cannot anchor affiliates.',
            'HRR assignment uses the 2019 Dartmouth ZIP crosswalk; newer ZIP codes are unassigned.',
        ],
    }
    dump(OUT / 'division_metadata.json', metadata)
    print('DIVISIONS', dict(divisions), 'CENTERS', len(center_set), flush=True)
    return metadata

def all_sources():
    return [load(p) for p in sorted(RAW.rglob('*.meta.json'))]

DESCRIPTIONS = {
    'npi': 'Ten-digit individual NPI as text; the only person key.',
    'hcp_id': 'Core cohort key when the NPI is one of the preserved 500 HCPs; blank otherwise.',
    'is_core': '1 when the NPI is in the preserved 2026-10-09 cohort.',
    'last_name': 'Name as published by CMS (2024 provider file, else Doctors and Clinicians, else NPPES).',
    'first_name': 'Name as published by CMS (2024 provider file, else Doctors and Clinicians, else NPPES).',
    'credentials': 'Credentials string as published by CMS; not verified.',
    'role': 'physician or app from PECOS specialties, or physician when only the 2024 claims specialty is known; blank when not eligible.',
    'specialty_eligible': '1 when the NPI qualifies for a division by specialty: hematology/oncology, medical oncology, hematology, or an APP with those secondary specialties.',
    'in_claims_specialty_universe': '1 when the 2024 Medicare provider file types the NPI as hem/onc, medical oncology, hematology, or HCT and cellular therapy.',
    'in_ndf_specialty_universe': '1 when Doctors and Clinicians specialties qualify the NPI.',
    'cms_provider_type_2024': 'Claims-derived specialty in the 2024 provider file; differs from NPPES taxonomy.',
    'ndf_primary_specialty': 'PECOS primary specialty from Doctors and Clinicians.',
    'ndf_secondary_specialties': 'PECOS secondary specialties, comma separated.',
    'state': 'Practice state: 2024 provider file, else first Doctors and Clinicians state, else NPPES.',
    'city': 'Practice city from the 2024 provider file when present.',
    'zip5': 'Five-digit practice ZIP from the 2024 provider file, else Doctors and Clinicians.',
    'hrr': 'Dartmouth hospital referral region number for zip5 (2019 crosswalk); blank when unassigned.',
    'hrr_city': 'Dartmouth HRR city label.',
    'hrr_state': 'Dartmouth HRR state label.',
    'hrr_has_cart_center': '1 when at least one CAR-T center hospital sits in the same HRR; blank when the ZIP has no HRR.',
    'ruca': 'Rural-urban commuting area code of the practice ZIP from the 2024 provider file.',
    'medicare_beneficiaries_2024': 'Distinct Medicare FFS beneficiaries in the 2024 provider file; suppressed cells are blank.',
    'medicare_services_2024': 'Total Medicare FFS services in the 2024 provider file.',
    'beneficiary_cancer_pct_2024': 'Share of the NPI\'s beneficiaries with a cancer chronic-condition flag (CMS six-cancer definition).',
    'beneficiary_avg_risk_score_2024': 'Average HCC risk score of the NPI\'s beneficiaries.',
    'hospital_ccns': 'Hospital CCNs from Doctors and Clinicians Facility Affiliation, semicolon separated.',
    'hospital_ccn_count': 'Number of hospital CCN affiliations.',
    'center_ccn_links': 'Number of affiliated CCNs in the CAR-T center set.',
    'core_ccn_links': 'Number of affiliated CCNs in the center set that also have at least one core HCP affiliated.',
    'group_org_pac_ids': 'PECOS group practice PAC IDs from Doctors and Clinicians, semicolon separated.',
    'core_org_links': 'Number of group PAC IDs shared with at least one core HCP.',
    'core_building_links': 'Number of Doctors and Clinicians building keys (adrs_id without the last two characters) shared with a core HCP.',
    'chemo_administration_rows': 'Published 2024 code-setting rows for 96413, 96415, 96417 under this NPI.',
    'chemo_administration_office_rows': 'Those rows in the non-facility (office) setting.',
    'rituximab_rows': 'Published 2024 rows for IV or subcutaneous rituximab codes under this NPI.',
    'rituximab_office_rows': 'Those rows in the office setting.',
    'dlbcl_agent_rows': 'Published 2024 rows for polatuzumab, tafasitamab, loncastuximab, epcoritamab, glofitamab, or mosunetuzumab under this NPI.',
    'dlbcl_agent_office_rows': 'Those rows in the office setting.',
    'part_d_lymphoma_oral_rows': 'Published 2024 Part D prescriber-drug rows for the selected lymphoma oral agents.',
    'part_d_btki_claims': 'Sum of published 2024 Part D claims for ibrutinib, acalabrutinib, zanubrutinib, pirtobrutinib.',
    'part_d_lenalidomide_claims': 'Published 2024 Part D claims for lenalidomide; skews to myeloma.',
    'part_d_venetoclax_claims': 'Published 2024 Part D claims for venetoclax; skews to CLL and AML.',
    'part_d_tazemetostat_claims': 'Published 2024 Part D claims for tazemetostat.',
    'treatment_markers': 'Semicolon-separated markers present: office_chemo_administration, rituximab_own_npi, dlbcl_agent_own_npi, part_d_lymphoma_oral.',
    'marker_count': 'Number of distinct markers present.',
    'division': 'core; affiliate_A (shared center CCN and shared group with a core HCP); affiliate_B (one of those); center_affiliate_unlinked (at a center with no core HCP link); affiliate_C (shared building only); referring_proxy (eligible physician outside centers with a treatment marker); hemonc_other; not_eligible.',
    'label_version': 'Version of the labeling rules.',
    'service_year': 'Year of the claims-derived fields: 2024.',
    'collection_snapshot_date': 'Collection-run date; exact request timestamps are in the source request manifest.',
    'cohort_snapshot_date': 'Date of the preserved core cohort.',
    'row_assembled_at_utc': 'When this row was built; distinct from retrieval times.',
    'ccn': 'Six-character CMS Certification Number of the hospital.',
    'hospital_name': 'Legal organization name from CMS Hospital Enrollments (main location), else the Teaching Hospital or inpatient file name.',
    'dba_name': 'Doing-business-as name from Hospital Enrollments.',
    'organization_npi': 'Organization NPI of the enrolled hospital from Hospital Enrollments.',
    'drg018_discharges_2024': 'Medicare FFS discharges for MS-DRG 018 in 2024; blank when the hospital is in the set only through FACT.',
    'fact_iec_program_count': 'FACT-accredited immune effector cell programs matched to this CCN.',
    'fact_program_urls': 'FACT program listing URLs matched to this CCN.',
    'evidence': 'drg018_2024 and/or fact_iec_accredited.',
    'core_hcps_affiliated': 'Number of core HCPs with a hospital affiliation to this CCN.',
    'drg018_source_dataset_version_id': 'Pinned CMS inpatient dataset UUID when DRG 018 evidence is present.',
    'program_url': 'FACT listing URL; the program key.',
    'program_name': 'Program name as listed by FACT.',
    'organization_director': 'Organization director as listed by FACT; a name, not an NPI.',
    'states': 'States listed by FACT, semicolon separated.',
    'cities': 'Cities listed by FACT, semicolon separated.',
    'clinical_services': 'FACT clinical services; all rows include Immune Effector Cellular Therapy.',
    'transplant_types': 'FACT transplant types.',
    'matched_ccn': 'Hospital CCN assigned by name matching or by a reviewed override; blank when unmatched.',
    'match_method': 'name_city, name_state, reviewed_override, reviewed_no_ccn, or unmatched.',
    'match_score': 'Share of distinctive program-name tokens found in the hospital name; blank for overrides.',
    'matched_hospital_name': 'Hospital name for the matched CCN.',
    'matched_hospital_city': 'Hospital city for the matched CCN.',
    'core_hcps_affiliated_with_matched_ccn': 'Core HCPs affiliated with the matched CCN; supporting evidence for the match.',
    'listing_page': 'FACT listing page number at collection.',
    'source_id': 'Raw-response key linking to the source request manifest.',
    'listing_url': 'FACT listing URL with country and clinical-service filters.',
    'listing_observed_at_utc': 'When the FACT listing pass completed.',
    'in_cart_center_set': '1 when the CCN is in the CAR-T center set.',
    'source': 'Source file name.',
    'source_release_date': 'Release date of the source file.',
    'org_pac_id': 'PECOS group practice PAC ID.',
    'group_legal_name': 'Group legal business name from Doctors and Clinicians.',
    'group_member_count': 'Clinicians in the group as published.',
    'adrs_id': 'Doctors and Clinicians normalized practice address identifier.',
    'building_key': 'adrs_id without its last two characters; shared by clinicians at one building.',
    'address_line_1': 'Practice address line 1 as published.',
    'group_assignment': 'Doctors and Clinicians group accepts Medicare assignment flag.',
    'core_hcps_in_group': 'Number of core HCPs with a membership row in this group.',
    'hcpcs_code': 'CMS service or product code as text.',
    'hcpcs_description': 'CMS description of the code.',
    'place_of_service': 'F = facility; O = non-facility (office).',
    'marker_group': 'chemo_administration, rituximab_iv, rituximab_sc, or dlbcl_agents.',
    'beneficiaries': 'Distinct beneficiaries in this NPI-code-setting cell; not additive across cells.',
    'services_or_units': 'CMS Tot_Srvcs for the cell.',
    'beneficiary_days': 'CMS Tot_Bene_Day_Srvcs for the cell.',
    'cms_provider_type': 'Claims-derived specialty on the service row.',
    'source_dataset_version_id': 'Pinned CMS dataset UUID queried.',
    'query_name': 'Name of the filtered query in the interim collection that returned this row.',
    'generic_name': 'Part D generic name.',
    'brand_name': 'Part D brand name.',
    'total_claims': 'Published 2024 Part D claims for this prescriber-drug cell.',
    'total_30day_fills': 'Published 30-day standardized fills.',
    'total_beneficiaries': 'Published beneficiaries; blank when fewer than 11.',
    'total_drug_cost_usd': 'Published total drug cost.',
    'prescriber_type': 'Claims-derived prescriber specialty.',
    'year': 'CareSet DocGraph data year (Medicare FFS claims for that calendar year).',
    'kind': 'CareSet product: annual (full-year patient sharing) or same_day.',
    'out_patients_total': 'Patients this NPI shared with any provider seen later in the sequence, summed over kept edges.',
    'out_patients_to_centers': 'Patients this NPI shared with CAR-T center physicians or center organization NPIs.',
    'distinct_center_partners': 'Number of distinct center CCNs reached through those edges.',
    'mean_day_wait_to_centers': 'Mean of CareSet average_day_wait over edges to centers; small values suggest co-management, large values referral.',
    'in_patients_total': 'Patients shared with this NPI by any provider seen earlier in the sequence.',
    'in_patients_from_referrers': 'Patients shared with this NPI by physicians labeled referring_proxy.',
    'referrer_sources': 'Number of distinct referring_proxy physicians sharing patients with this NPI.',
    'observed_shared_patient_edge': '1 for every row; the feature table lists only NPIs with at least one kept edge.',
    'patients': 'CareSet patient_count summed over referring physicians in the HRR sharing patients with the center.',
    'referrer_pairs': 'Number of referring physician to center-NPI edges aggregated into this flow.',
    'same_hrr': '1 when the center hospital sits in the same HRR as the referring physicians.',
}
TABLES = ['cart_centers', 'fact_iec_programs', 'hcp_divisions', 'hcp_hospital_affiliations',
          'hcp_group_memberships', 'treatment_markers_2024', 'part_d_lymphoma_orals_2024']
OPTIONAL_TABLE_GLOBS = ['shared_patient_*_features', 'shared_patient_*_hrr_center_flows']

def make_manifests():
    dest = ROOT / 'Data' / 'manifests' / SNAPSHOT
    dest.mkdir(parents=True, exist_ok=True)
    sources = all_sources()
    fields = list(dict.fromkeys(k for s in sources for k in s))
    write_csv(dest / 'division_source_requests.csv', sources, fields)
    paths = [p for base in [RAW, INTERIM, OUT] for p in sorted(base.rglob('*')) if p.is_file() and p.suffix != '.tmp']
    paths.append(config_input('cart_center_overrides.csv'))
    write_csv(dest / 'division_file_checksums.csv', [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in paths])
    dictionary = {}
    optional = sorted(p.stem for pattern in OPTIONAL_TABLE_GLOBS for p in OUT.glob(pattern + '.csv'))
    for table in TABLES + optional:
        rows = read_csv(OUT / (table + '.csv'))
        entries = []
        for field in (list(rows[0]) if rows else []):
            description = DESCRIPTIONS.get(field)
            assert description, 'Missing definition: ' + field
            entries.append({'column': field, 'description': description,
                            'missing_value': 'Blank = unavailable, suppressed, or unobserved; zero counts are observation counts, not confirmed absence of activity.'})
        dictionary[table] = entries
    dump(dest / 'division_data_dictionary.json', dictionary)
    print('DIVISION MANIFESTS', len(sources), 'SOURCE REQUESTS', len(paths), 'FILES')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-collect', action='store_true', help='Build from the existing interim collection only.')
    args = parser.parse_args()
    if not args.skip_collect:
        collect()
    build()
    make_manifests()

if __name__ == '__main__':
    main()
