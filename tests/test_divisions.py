"""Labeling, specialty, and center-matching rules for the HCP divisions."""
from kolproject.divisions import eligible_specialty, label, match_program, similarity
from kolproject.divisions_source import parse_fact

BASE = {'is_core': 0, 'specialty_eligible': 1, 'role': 'physician', 'core_ccn_links': 0, 'core_org_links': 0,
        'center_ccn_links': 0, 'core_building_links': 0, 'marker_count': 0}

def test_core_wins_over_every_other_signal():
    assert label({**BASE, 'is_core': 1, 'core_ccn_links': 2, 'marker_count': 3}) == 'core'

def test_tier_a_needs_two_independent_structural_links():
    assert label({**BASE, 'core_ccn_links': 1, 'core_org_links': 1}) == 'affiliate_A'
    assert label({**BASE, 'core_ccn_links': 1}) == 'affiliate_B'
    assert label({**BASE, 'core_org_links': 1}) == 'affiliate_B'

def test_center_staff_without_core_link_are_not_referrers():
    row = {**BASE, 'center_ccn_links': 1, 'marker_count': 2}
    assert label(row) == 'center_affiliate_unlinked'

def test_building_only_is_tier_c():
    assert label({**BASE, 'core_building_links': 1, 'marker_count': 1}) == 'affiliate_C'

def test_referring_proxy_requires_physician_and_marker_outside_centers():
    assert label({**BASE, 'marker_count': 1}) == 'referring_proxy'
    assert label({**BASE, 'marker_count': 0}) == 'hemonc_other'
    assert label({**BASE, 'role': 'app', 'marker_count': 1}) == 'hemonc_other'
    assert label({**BASE, 'specialty_eligible': 0, 'marker_count': 1}) == 'not_eligible'

def test_specialty_rules_follow_pecos_fields():
    assert eligible_specialty('HEMATOLOGY/ONCOLOGY', '') == 'physician'
    assert eligible_specialty('INTERNAL MEDICINE', 'HEMATOLOGY/ONCOLOGY') == 'physician'
    assert eligible_specialty('NURSE PRACTITIONER', 'MEDICAL ONCOLOGY, INTERNAL MEDICINE') == 'app'
    assert eligible_specialty('PATHOLOGY', 'HEMATOLOGY') == ''
    assert eligible_specialty('INTERNAL MEDICINE', '') == ''
    assert eligible_specialty('RADIATION ONCOLOGY', '') == ''

def test_similarity_ignores_program_boilerplate_but_needs_a_distinctive_token():
    assert similarity('Blood and Marrow Transplant Program at Mayo Clinic Arizona', 'MAYO CLINIC HOSPITAL') >= 0.5
    assert similarity('University Cancer Center Cellular Therapy Program', 'UNIVERSITY MEDICAL CENTER') == 0.0
    assert similarity('UCSF Medical Center', 'UNIVERSITY OF CALIFORNIA SAN FRANCISCO MEDICAL CENTER') >= 0.5

def test_match_prefers_same_city_then_core_evidence():
    hospitals = {'FL': {'100001': {'names': {'ADVENTHEALTH ORLANDO'}, 'city': 'ORLANDO'},
                        '100002': {'names': {'ADVENTHEALTH TAMPA'}, 'city': 'TAMPA'}}}
    program = {'program_name': 'Adult Blood and Marrow Transplant Program at AdventHealth', 'states': ['Florida'], 'cities': ['Orlando']}
    assert match_program(program, hospitals, {})['matched_ccn'] == '100001'
    tie = {'program_name': 'AdventHealth Cellular Therapy', 'states': ['Florida'], 'cities': ['Miami']}
    assert match_program(tie, hospitals, {'100002': 3})['matched_ccn'] == '100002'
    assert match_program({'program_name': 'Nowhere Program', 'states': ['Florida'], 'cities': []}, hospitals, {})['match_method'] == 'unmatched'

def test_fact_parser_reads_entries_and_pagination():
    page = ('Found 2 Results<br /> Page 1 of 1<br /> <div class="in-results">'
            '<div style="padding:30px;"> <h2><a href="https://x/institutions/a/">Program A &#038; B</a></h2>'
            '<p class="institution">Organization Director Name: &nbsp;Jane Doe MD</p>'
            '<p class="institution">Country: <a href="https://x/country/usa/" rel="tag">USA</a> | State(s): '
            '<a href="https://x/state/ohio/" rel="tag">Ohio</a> | City: <a href="https://x/city/columbus/" rel="tag">Columbus</a></p>'
            '<div class="results-inside">Clinical Services: <ul><li><a href="https://x/clinical/iec/" rel="tag">Immune Effector Cellular Therapy</a></ul></div>'
            '<div style="padding:30px;"> <h2><a href="https://x/institutions/b/">Program B</a></h2>'
            '<p class="institution">Organization Director Name: &nbsp;John Roe</p>'
            '<p class="institution">State(s): <a href="https://x/state/texas/" rel="tag">Texas</a></p>'
            '<div class="pagination"></div></div></div></div></div></div></div></div></div></div>')
    entries, found, pages = parse_fact(page)
    assert found == 2 and pages == (1, 1)
    assert [e['program_name'] for e in entries] == ['Program A & B', 'Program B']
    assert entries[0]['organization_director'] == 'Jane Doe MD'
    assert entries[0]['states'] == ['Ohio'] and entries[0]['cities'] == ['Columbus']
    assert entries[0]['clinical_services'] == ['Immune Effector Cellular Therapy']
    assert entries[1]['states'] == ['Texas'] and entries[1]['cities'] == []
