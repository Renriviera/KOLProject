"""Evidence rules, restricted-source handling and tracked-file checks of the CAR-T treater evidence table."""
from collections import defaultdict

import pytest

from kolproject.common import ROOT, write_csv
from kolproject.volume_billing import treater_roster as t


def npi(nine):
    return next(nine + d for d in '0123456789' if t.luhn_ok(nine + d))


CORE, REFERRING, OTHER, KITE_ONLY, THERAPIST, NURSE, FALSE = (npi(f'10000000{k}') for k in range(1, 8))
MEMBERS = {CORE: {'npi': CORE, 'is_core': '1', 'division': 'core'},
           REFERRING: {'npi': REFERRING, 'is_core': '0', 'division': 'referring_proxy'},
           OTHER: {'npi': OTHER, 'is_core': '0', 'division': 'hemonc_other'},
           KITE_ONLY: {'npi': KITE_ONLY, 'is_core': '0', 'division': 'affiliate_A'}}
NCT1, NCT2, NCT3 = 'NCT00000001', 'NCT00000002', 'NCT00000003'
TRIALS = {NCT1: 'cart_product_trial', NCT2: 'cart_context_trial', NCT3: 'cart_product_trial'}


def mention(source, src_id, who, status='exact_first_last_state', role='physician', state='NY', org='Center'):
    return {'source': source, 'src_id': src_id, 'role': role, 'states': state, 'org': org, 'match_status': status, 'npi': who}


AUDIT = [
    mention('bmt', '10', CORE, role='medical_director;physician', org='Adult Center'),
    mention('bmt', '10', CORE, status='nppes_in_universe_national', org='Adult Center'),
    mention('kite', 'k1', CORE, org='Kite Center'),
    mention('ctgov_overall', NCT1, CORE, status='fuzzy_first_last_state', role='PRINCIPAL_INVESTIGATOR'),
    mention('ctgov_site', NCT1, CORE, role='PRINCIPAL_INVESTIGATOR'),
    mention('ctgov_site', NCT3, CORE, role='SUB_INVESTIGATOR'),
    mention('fact', '330101', CORE, role='program_director', org='Program'),
    mention('bmt', '20', REFERRING, org='Children Center'),
    mention('kite', 'k1', OTHER, org='Kite Center'),
    mention('kite', 'k1', KITE_ONLY, org='Kite Center'),
    mention('ctgov_site', NCT2, THERAPIST, status='nppes_outside_universe', role='SUB_INVESTIGATOR'),
    mention('kite', 'k1', NURSE, status='nppes_outside_universe', org='Kite Center'),
    mention('ctgov_site', NCT1, FALSE, status='nppes_outside_universe'),
    {**mention('bmt', '10', ''), 'match_status': 'unmatched_nppes_none'},
]


def inputs():
    registry = {THERAPIST: {'npi': THERAPIST, 'first': 'ANN', 'last': 'LEE', 'state': 'IL', 'tax': [['235Z00000X', 'Speech-Language Pathologist,  ', True]]},
                NURSE: {'npi': NURSE, 'first': 'KIM', 'last': 'ROE', 'state': 'TN', 'tax': [['363L00000X', 'Nurse Practitioner', True]]}}
    return {
        'programs': {'10': 'Adult Program', '20': 'Pediatric Program'}, 'positive_counts': {'10': True, '20': False},
        'research': t.research_pi([{'cart_products': 'YESCARTA', 'pi_npis': CORE, 'total_amount_of_payment_usdollars': '100.50',
                                    'clinicaltrials_gov_identifier': NCT1, 'name_of_study': 'S'}]),
        'tier': {CORE: {'lymphoma_only_services_usd': '12000'}, OTHER: {'lymphoma_only_services_usd': '0'}},
        'recipients': {NURSE: {'cart_only_services_usd': '500', 'primary_type': 'Nurse Practitioner', 'specialty': 'x|Nurse Practitioner'}},
        'company_years': t.company_service_years({2023: [['legend biotech', 'other', 'services', 'no_product', 1, 9.0, [OTHER]]]}),
        'medicare_0540t': defaultdict(list, {CORE: ['2023:11']}), 'medicare_specialty': defaultdict(list, {REFERRING: ['2022', '2021']}),
        'registry': registry, 'by_number': {},
    }


def build_rows():
    linked, dropped = t.link_mentions(AUDIT, TRIALS, {FALSE})
    rows = t.assemble(MEMBERS, linked, inputs())
    return {row['npi']: row for row in rows}, dropped


def test_npi_check_digit():
    assert t.luhn_ok('1234567893') and not t.luhn_ok('1234567890') and not t.luhn_ok('123456789')


def test_link_confidence_uses_best_status_per_source_and_separates_npi_keyed_rows():
    assert t.weak('nppes_in_universe_national') and t.weak('fuzzy_first_last_state') and t.weak('exact_name_unique_no_state')
    assert not t.weak('nppes_outside_universe') and not t.weak('exact_first_last_state_city_tiebreak')
    assert t.best_status(['nppes_in_universe_national', 'exact_first_last_state', 'fuzzy_first_last_state']) == 'exact_first_last_state'
    assert t.link_confidence({}) == ([], 'npi_keyed_only')
    assert t.link_confidence({'bmt': 'exact_first_last_state'}) == ([], 'high')
    assert t.link_confidence({'bmt': 'exact_first_last_state', 'fact': 'fuzzy_first_last_state'}) == (['fact'], 'mixed')
    assert t.link_confidence({'ctgov_site': 'nppes_outside_universe_national'}) == (['ctgov_site'], 'low_name_based')


def test_company_service_years_count_distinct_years_not_product_naming_groups():
    groups = {2023: [['kite', 'core', 'services', 'cart_named', 2, 9.0, ['1']], ['kite', 'core', 'services', 'no_product', 1, 5.0, ['1']],
                     ['kite', 'core', 'in_kind_other', 'cart_named', 3, 1.0, ['1', '2']]],
              2024: [['legend biotech', 'core', 'services', 'no_product', 1, 5.0, ['1']]]}
    years = t.company_service_years(groups)
    assert {c: sorted(y) for c, y in years['1'].items()} == {'kite': [2023], 'legend_biotech': [2024]} and '2' not in years


def test_research_pi_counts_a_record_once_per_listed_investigator():
    records = [{'cart_products': 'CARVYKTI;YESCARTA', 'pi_npis': '1;2', 'total_amount_of_payment_usdollars': '10', 'clinicaltrials_gov_identifier': 'NCT9', 'name_of_study': 'A'},
               {'cart_products': 'CARVYKTI', 'pi_npis': '1', 'total_amount_of_payment_usdollars': '5', 'clinicaltrials_gov_identifier': '', 'name_of_study': 'B' * 60},
               {'cart_products': '', 'pi_npis': '1', 'total_amount_of_payment_usdollars': '99', 'clinicaltrials_gov_identifier': '', 'name_of_study': 'C'}]
    out = t.research_pi(records)
    assert (out['1']['records'], out['1']['lymphoma'], out['1']['usd'], out['1']['studies']) == (2, 1, 15.0, {'NCT9', 'B' * 40})
    assert out['2']['records'] == 1 and out['2']['brands'] == {'CARVYKTI', 'YESCARTA'}


def test_taxonomy_class_fixes():
    assert t.taxonomy_class([['235Z', 'Speech-Language Pathologist,  ', True]]) == 'other:Speech-Language Pathologist'
    assert t.taxonomy_class([['207Z', 'Pathology, Clinical Pathology', True]]) == 'pathology'
    assert t.taxonomy_class([['2085R0001X', None, True]]) == 'other:code 2085R0001X'
    assert t.taxonomy_class([['208D', 'General Practice', True], ['207R', 'Internal Medicine, Hematology & Oncology', False]]) == 'adult_hematology_oncology'
    assert t.taxonomy_class([['363L', 'Nurse Practitioner', True], ['207R', 'Internal Medicine, Medical Oncology', False]]) == 'nurse_practitioner'
    assert t.taxonomy_class([]) == 'unknown' and t.primary_class('Pharmacist') == 'pharmacist'
    assert t.open_payments_class('Medical Doctor', 'Allopathic|Internal Medicine|Hematology & Oncology') == 'adult_hematology_oncology'
    assert t.open_payments_class('Medical Doctor', 'Allopathic|Obstetrics & Gynecology') == 'other:Obstetrics & Gynecology'
    assert t.open_payments_class('Medical Doctor', '') == 'unknown'


def test_open_payments_class_uses_the_registry_labels():
    physicians = 'Allopathic & Osteopathic Physicians'
    for specialty, registry_wording in [('Internal Medicine', 'Internal Medicine'), ('Internal Medicine|Nephrology', 'Internal Medicine, Nephrology'),
                                        ('Pediatrics', 'Pediatrics'), ('Radiology|Diagnostic Radiology', 'Radiology, Diagnostic Radiology'),
                                        ('Pathology|Anatomic Pathology', 'Pathology, Anatomic Pathology')]:
        assert t.open_payments_class('Medical Doctor', f'{physicians}|{specialty}') == t.primary_class(registry_wording)
    assert t.open_payments_class('Medical Doctor', f'{physicians}|Internal Medicine') == 'internal_medicine_other'
    assert t.open_payments_class('Medical Doctor', f'{physicians}|Radiology|Diagnostic Radiology') == 'other:Radiology, Diagnostic Radiology'
    assert t.open_payments_class('Nurse Practitioner', 'Physician Assistants & Advanced Practice Nursing Providers|Clinical Nurse Specialist|Oncology') == 'nurse_practitioner'
    assert t.open_payments_class('Medical Doctor', f'{physicians}|Pediatrics|Pediatric Hematology-Oncology') == 'pediatric_hematology_oncology'


def test_false_links_are_dropped_and_unmatched_mentions_ignored():
    rows, dropped = build_rows()
    assert FALSE not in rows and dropped == {FALSE: 1} and '' not in rows
    assert FALSE in {row['npi'] for row in t.assemble(MEMBERS, t.link_mentions(AUDIT, TRIALS, set())[0], inputs())}


def test_public_counts_and_score_leave_kite_out_and_kite_only_rows_are_never_tracked():
    rows, _ = build_rows()
    core = rows[CORE]
    assert (core['ev_kite_team'], core['n_evidence_types'], core['n_evidence_types_public']) == ('1', '7', '6')
    assert core['n_evidence_types_excl_services_public'] == '5'
    # public sort aid: roster 3, director 3, trials 2*1+1, research 2, dollars 1+1, 0540T 3; Kite (+3) and the role point stay local
    assert core['evidence_score_public'] == '16' and core['evidence_score'] == '20'
    assert core['bmt_match_status'] == 'exact_first_last_state' and core['ctgov_overall_official_match_status'] == 'fuzzy_first_last_state'
    assert (core['name_link_weak_sources_public'], core['npi_link_confidence_public']) == ('ctgov_overall', 'mixed')
    assert core['n_evidence_types_excl_services_strict_links_public'] == '5'
    assert (core['ctgov_product_trials_overall_official'], core['ctgov_product_trials_site_investigator'], core['ctgov_product_trials_any_role']) == ('1', '2', '2')
    assert core['needs_review_reasons'] == 'mixed_name_link' and core['fact_program_ccns'] == '330101'
    tracked, local = t.split_kite_only(list(rows.values()))
    assert [row['npi'] for row in local] == [KITE_ONLY] and KITE_ONLY not in {row['npi'] for row in tracked}
    public = [c['column'] for c in t.columns() if not c['restricted']]
    linked = t.link_mentions(AUDIT, TRIALS, {FALSE})[0]
    assert t.kite_dependent(tracked, MEMBERS, linked, inputs(), public) == []
    assert t.kite_dependent(tracked, MEMBERS, linked, inputs(), public + ['n_evidence_types']) == sorted([CORE, NURSE, OTHER])
    assert t.kite_dependent(tracked + local, MEMBERS, linked, inputs(), public) == [KITE_ONLY]
    restricted = {c['column'] for c in t.columns() if c['restricted']}
    assert {'ev_kite_team', 'kite_centers', 'kite_match_status', 'n_evidence_types', 'evidence_score', 'npi_link_confidence',
            'bmt_centers', 'bmt_roles', 'fact_programs'} <= restricted
    assert all(c['restricted'] for c in t.columns() if c['source'] == 'kite')


def test_blank_rules_identity_and_over_inclusion_flags():
    rows, _ = build_rows()
    other, referring, therapist, nurse = rows[OTHER], rows[REFERRING], rows[THERAPIST], rows[NURSE]
    assert (other['op_lymphoma_only_cart_services_usd_2019_2025'], other['op_cart_only_services_usd_2019_2025_outside_universe']) == ('0.00', '')
    assert (other['ev_op_cart_services'], other['flag_services_company_arm_only'], other['flag_services_legend_autolus_only']) == ('1', '1', '1')
    assert other['op_kite_legend_autolus_service_years'] == 'legend_biotech:1' and other['npi_link_confidence_public'] == 'npi_keyed_only'
    assert (referring['flag_bmt_pediatric_program_only'], referring['flag_medicare_specialty_only_public']) == ('1', '0')
    assert referring['medicare_hct_specialty_years'] == '2021;2022' and referring['identity_source'] == 'universe_table'
    assert not any(referring[c] for c in ['nppes_last_name', 'nppes_first_name', 'nppes_state', 'nppes_primary_taxonomy'])
    assert (therapist['identity_source'], therapist['nppes_last_name'], therapist['division']) == ('nppes_name_query', 'LEE', 'outside_universe')
    assert (therapist['flag_ctgov_context_only'], therapist['flag_non_physician_taxonomy_class']) == ('1', '1')
    assert therapist['needs_review_reasons'] == 'outside_universe_name_link_not_hematology_class'
    assert (therapist['op_lymphoma_only_cart_services_usd_2019_2025'], therapist['op_cart_only_services_usd_2019_2025_outside_universe']) == ('', '0.00')
    # the registry record of this NPI was reached only through a restricted source, so its name stays out
    assert (nurse['identity_source'], nurse['nppes_last_name'], nurse['taxonomy_class']) == ('no_registry_lookup', '', 'nurse_practitioner')
    assert nurse['ev_op_cart_services'] == '1' and nurse['n_evidence_types_public'] == '1' and nurse['n_evidence_types'] == '2'


def test_relabel_candidates_follow_the_public_rule_and_order():
    rows, _ = build_rows()
    tracked, _ = t.split_kite_only(list(rows.values()))
    candidates = t.relabel_candidates(tracked)
    # OTHER has only service payments in public evidence; its Kite listing does not make it a published candidate
    assert [row['npi'] for row in candidates] == [REFERRING]
    assert candidates[0]['evidence_flags'] == 'bmt_roster+medicare_cell_therapy' and 'division' not in candidates[0]
    assert list(candidates[0]) == [c['column'] for c in t.relabel_columns()]
    assert not any(c['restricted'] for c in t.relabel_columns())


def write_tree(folder, rows, candidates):
    public = [c['column'] for c in t.columns() if not c['restricted']]
    write_csv(folder / t.UNIVERSE_SNAPSHOT / 'hcp_divisions.csv', MEMBERS.values())
    write_csv(folder / t.UNIVERSE_SNAPSHOT / 'cart_centers.csv', [{'ccn': '330101'}])
    write_csv(folder / t.SNAPSHOT / f'{t.TABLES[0]}.csv', [{c: row[c] for c in public} for row in rows], public)
    write_csv(folder / t.SNAPSHOT / f'{t.TABLES[1]}.csv', candidates, [c['column'] for c in t.relabel_columns()])


def failures(folder, expected=None):
    return {label for label, count in t.check(folder, expected).items() if count}


def test_check_passes_on_a_clean_tree_and_catches_broken_tables(tmp_path):
    rows, _ = build_rows()
    tracked, _ = t.split_kite_only(list(rows.values()))
    candidates = t.relabel_candidates(tracked)
    write_tree(tmp_path / 'ok', tracked, candidates)
    assert failures(tmp_path / 'ok') == set()
    assert failures(tmp_path / 'ok', {'rows': len(tracked), 'relabel_candidates': 1, 'relabel_by_division': {'referring_proxy': 1}}) == set()
    assert failures(tmp_path / 'ok', {'rows': 99}) == {'treater_roster_headline_totals_differ'}
    snapshot = {**t.headline(tracked, candidates), 'fingerprints': t.fingerprints(tracked, candidates)}
    assert failures(tmp_path / 'ok', snapshot) == set()
    assert snapshot['totals']['op_cart_research_pi_record_usd_2019_2025'] == '100.50' and snapshot['totals']['medicare_0540t_services'] == 11

    def broken(name, change, relabel=None):
        copies = [dict(row) for row in tracked]
        change({row['npi']: row for row in copies}, copies)
        write_tree(tmp_path / name, copies, candidates if relabel is None else relabel)
        return failures(tmp_path / name)

    assert broken('flag', lambda by, _: by[CORE].update(ev_bmt_roster='0')) >= {'treater_roster_derived_value_differs_from_detail_columns'}
    assert broken('score', lambda by, _: by[CORE].update(evidence_score_public='19')) == {'treater_roster_derived_value_differs_from_detail_columns'}
    assert broken('name', lambda by, _: by[REFERRING].update(nppes_last_name='DOE')) == {'treater_roster_universe_join_or_identity_rule_broken'}
    assert broken('division', lambda by, _: by[OTHER].update(division='core')) >= {'treater_roster_universe_join_or_identity_rule_broken'}
    assert broken('hint', lambda by, _: by[NURSE].update(identity_source='nppes_name_query', nppes_last_name='ROE', nppes_first_name='KIM', nppes_state='TN')) == {'treater_roster_universe_join_or_identity_rule_broken'}
    assert broken('years', lambda by, _: by[OTHER].update(op_kite_legend_autolus_service_years='legend_biotech:9')) == {'treater_roster_value_format_invalid'}
    # a required count turned into a blank is counted, not raised
    assert broken('blank_count', lambda by, _: by[OTHER].update(ctgov_product_trials_overall_official='')) == {'treater_roster_value_format_invalid'}
    assert broken('blank_dollars', lambda by, _: by[OTHER].update(op_cart_research_pi_record_usd_2019_2025='')) == {'treater_roster_value_format_invalid'}
    assert broken('zero_list', lambda by, _: by[OTHER].update(medicare_0540t_year_services='0')) == {'treater_roster_value_format_invalid'}
    assert broken('twice', lambda by, _: by[OTHER].update(op_kite_legend_autolus_service_years='legend_biotech:1;legend_biotech:1')) == {'treater_roster_detail_columns_inconsistent'}
    assert broken('taxonomy', lambda by, _: by[NURSE].update(nppes_primary_taxonomy='0')) == {'treater_roster_universe_join_or_identity_rule_broken'}
    assert broken('class', lambda by, _: by[THERAPIST].update(taxonomy_class='pathology')) >= {'treater_roster_universe_join_or_identity_rule_broken'}
    assert broken('zero', lambda by, _: by[THERAPIST].update(op_lymphoma_only_cart_services_usd_2019_2025='0.00')) == {'treater_roster_detail_columns_inconsistent'}
    assert broken('ccn', lambda by, _: by[CORE].update(fact_program_ccns='999999')) == {'treater_roster_fact_ccn_not_in_cart_centers'}
    assert broken('review', lambda by, _: by[CORE].update(needs_review='0', needs_review_reasons='')) == {'treater_roster_needs_review_inconsistent'}
    assert broken('npi', lambda by, _: by[CORE].update(npi=CORE[:-1] + str((int(CORE[-1]) + 1) % 10))) >= {'treater_roster_npi_not_unique_or_invalid'}
    assert broken('false', lambda _, copies: copies.append({**copies[-1], 'npi': '1760815682'})) >= {'treater_roster_known_false_link_present'}
    assert broken('relabel_missing', lambda by, _: None, relabel=[]) == {'treater_roster_relabel_rows_differ_from_rule'}
    assert broken('relabel_value', lambda by, _: None, relabel=[{**candidates[0], 'evidence_score_public': '99'}]) == {'treater_roster_relabel_values_differ_from_evidence_table'}
    assert t.check(tmp_path / 'absent') == {'treater_roster_table_missing_or_empty': 2}

    # the directory identifiers join to the site panel of the same snapshot when that table is present
    panel = tmp_path / 'ok' / t.SNAPSHOT / f'{t.SITE_PANEL[0]}.csv'
    write_csv(panel, [{'site_key': 'a', t.SITE_PANEL[1]: '10;11'}, {'site_key': 'b', t.SITE_PANEL[1]: ''}])
    assert failures(tmp_path / 'ok') == {'treater_roster_bmt_center_id_not_in_site_panel'}
    write_csv(panel, [{'site_key': 'a', t.SITE_PANEL[1]: '10;11'}, {'site_key': 'b', t.SITE_PANEL[1]: '20'}])
    assert failures(tmp_path / 'ok') == set()

    # changes that no rule inside the table can see are caught by the snapshot totals or the fingerprint
    def drifted(name, change):
        copies = [dict(row) for row in tracked]
        change({row['npi']: row for row in copies})
        write_tree(tmp_path / name, copies, t.relabel_candidates(copies))
        return failures(tmp_path / name, snapshot)

    both = {'treater_roster_headline_totals_differ', 'treater_roster_content_differs_from_snapshot_fingerprint'}
    assert drifted('cent', lambda by: by[CORE].update(op_cart_research_pi_record_usd_2019_2025='100.51')) == both
    assert drifted('services', lambda by: by[CORE].update(medicare_0540t_year_services='2023:12')) == both
    assert drifted('center', lambda by: by[REFERRING].update(bmt_center_ids='21')) == {'treater_roster_content_differs_from_snapshot_fingerprint'}
    assert drifted('surname', lambda by: by[THERAPIST].update(nppes_last_name='LEA')) == {'treater_roster_content_differs_from_snapshot_fingerprint'}

    def swap(by):
        by[REFERRING]['npi'], by[OTHER]['npi'] = OTHER, REFERRING

    assert 'treater_roster_content_differs_from_snapshot_fingerprint' in drifted('swap', swap)
    assert t.fingerprint(tracked, ['npi']) != t.fingerprint(tracked[::-1], ['npi'])


def test_kite_columns_in_a_tracked_header_are_counted(tmp_path):
    rows, _ = build_rows()
    tracked, _ = t.split_kite_only(list(rows.values()))
    write_tree(tmp_path, tracked, t.relabel_candidates(tracked))
    path = tmp_path / t.SNAPSHOT / f'{t.TABLES[0]}.csv'
    text = path.read_text(encoding='utf-8').splitlines()
    path.write_text('\n'.join([text[0] + ',kite_centers'] + [line + ',x' for line in text[1:]]) + '\n', encoding='utf-8')
    result = t.check(tmp_path, None)
    assert result['treater_roster_restricted_or_dropped_column_tracked'] == 1 and result['treater_roster_columns_differ_from_dictionary'] == 1


def test_published_tables_pass_when_present():
    processed = ROOT / 'Data' / 'processed'
    if not (processed / t.SNAPSHOT / f'{t.TABLES[0]}.csv').exists():
        pytest.skip('2026-11-19 tables not built here')
    assert {label: count for label, count in t.check(processed).items() if count} == {}
