"""Per-NPI public evidence of CAR-T involvement, and the division review list built from it (2026-11-19).

`npi_cart_treater_evidence` has one row per individual NPI named in at least one public, non-claims source:
a BMT InfoNet center page, a FACT program director field, a ClinicalTrials.gov investigator entry, an Open
Payments Research principal-investigator slot, Open Payments General service payments tied to CAR-T, or a
Medicare cell-therapy marker. It is a screen of who is named, not proof of who infuses and not a volume.

The table is rebuilt from the saved search intermediates: the name-match record (the only saved outcome of
the name matching), the trial classification, the Open Payments scan extracts, two Medicare extracts and
the NPPES registry caches. The ad hoc table of the search is read only to reconcile the rebuild.

Kite treatment-team evidence is restricted by the Kite site terms. It never reaches a tracked file: Kite
columns and every value that counts Kite evidence go to the local companion file, and NPIs whose only
evidence is a Kite team list are written to a separate local file. Published counts and the sort aid are
recomputed without Kite and carry the suffix `_public`.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import re
from collections import Counter, defaultdict

from .base import (OUT, RESTRICTED, RESTRICTED_SUFFIX, SNAPSHOT, UNIVERSE_SNAPSHOT, column, duplicates, load, publish,
                   read_csv, stage, universe, write_csv, write_module_metadata)

MODULE = 'treater_roster'
TABLES = ['npi_cart_treater_evidence', 'division_relabel_candidates']
KITE_ONLY_FILE = f'npi_cart_treater_evidence_kite_only{RESTRICTED_SUFFIX}.csv'
YEARS = list(range(2019, 2026))
SEARCH = 'gap-npi-treater-roster-public-evidence'
PAYMENTS = 'local-industry-payments-by-division'
MEDICARE = 'cms-physician-national'

FLAGS = ['ev_bmt_roster', 'ev_kite_team', 'ev_fact_director', 'ev_ctgov_investigator', 'ev_op_cart_research_pi',
         'ev_op_cart_services', 'ev_medicare_cell_therapy']
PUBLIC_FLAGS = [flag for flag in FLAGS if flag != 'ev_kite_team']
SOURCE_FLAG = {'bmt': 'ev_bmt_roster', 'kite': 'ev_kite_team', 'fact': 'ev_fact_director',
               'ctgov_overall': 'ev_ctgov_investigator', 'ctgov_site': 'ev_ctgov_investigator'}
STATUS_COLUMN = {'bmt': 'bmt_match_status', 'kite': 'kite_match_status', 'fact': 'fact_match_status',
                 'ctgov_overall': 'ctgov_overall_official_match_status',
                 'ctgov_site': 'ctgov_site_investigator_match_status'}
PUBLIC_SOURCES = [source for source in STATUS_COLUMN if source != 'kite']
# Accepted name-link outcomes, strongest first. The last five are the weak ones.
STATUS_ORDER = ['exact_first_last_state', 'exact_first_last_state_city_tiebreak', 'nppes_in_universe',
                'nppes_outside_universe', 'fuzzy_first_last_state', 'exact_name_unique_state_differs',
                'exact_name_unique_no_state', 'nppes_in_universe_national', 'nppes_outside_universe_national']
LYMPHOMA_BRANDS = {'YESCARTA', 'TECARTUS', 'KYMRIAH', 'BREYANZI'}
COMPANIES = {'kite': 'kite', 'legend biotech': 'legend_biotech', 'autolus': 'autolus'}
RELABEL_DIVISIONS = ('referring_proxy', 'hemonc_other')
HEMATOLOGY_CLASSES = {'adult_hematology_oncology', 'pediatric_hematology_oncology'}
NON_PHYSICIAN_CLASSES = {'nurse_practitioner', 'physician_assistant', 'registered_nurse_or_cns', 'pharmacist',
                         'student_or_trainee'}
NON_PHYSICIAN_TEXT = re.compile('Speech-Language|Behavior Analyst', re.I)

# Name links judged false when the search was checked: the registry record belongs to another person with
# the same name. Their name mentions are dropped before the table is assembled.
FALSE_LINKS = {'1619344165', '1760815682'}
# Probable errors that could not be settled from saved inputs; kept and marked for a hand check.
HAND_CHECK = {'1437785383': 'probable_false_name_link', '1184795098': 'probable_false_name_link',
              '1245279231': 'probable_false_name_link', '1386844934': 'probable_false_name_link',
              '1962707554': 'probable_false_name_link', '1841292117': 'possible_recipient_mixup'}

# Every headline number of the tracked tables after the fixes (the 2,413 NPIs of the search, less 2 false links
# and 45 NPIs whose only evidence is restricted), and a fingerprint of each table. check() compares the tracked
# files with them; after a deliberate change, copy the new values from the metadata written by build().
EXPECTED = {
    'rows': 2366,
    'in_universe': 1846,
    'outside_universe': 520,
    'by_division': {
        'affiliate_A': 761, 'affiliate_B': 152, 'affiliate_C': 5, 'center_affiliate_unlinked': 264, 'core': 381,
        'hemonc_other': 91, 'outside_universe': 520, 'referring_proxy': 192,
    },
    'npis_by_flag': {
        'ev_bmt_roster': 1562, 'ev_fact_director': 180, 'ev_ctgov_investigator': 316, 'ev_op_cart_research_pi': 493,
        'ev_op_cart_services': 850, 'ev_medicare_cell_therapy': 215,
    },
    'npis_with_only_this_flag': {
        'ev_bmt_roster': 900, 'ev_fact_director': 8, 'ev_ctgov_investigator': 62, 'ev_op_cart_research_pi': 120,
        'ev_op_cart_services': 433, 'ev_medicare_cell_therapy': 73,
    },
    'evidence_types_at_least': {1: 2366, 2: 770, 3: 329, 4: 117, 5: 32},
    'evidence_types_excl_services_at_least': {1: 1933, 2: 606, 3: 183, 4: 42},
    'strict_links_at_least': {1: 1851, 2: 573, 3: 171},
    'evidence_excl_services_by_division': {
        'affiliate_A': 682, 'affiliate_B': 121, 'affiliate_C': 5, 'center_affiliate_unlinked': 230, 'core': 347,
        'hemonc_other': 78, 'outside_universe': 401, 'referring_proxy': 69,
    },
    'services_only_by_division': {
        'affiliate_A': 79, 'affiliate_B': 31, 'center_affiliate_unlinked': 34, 'core': 34, 'hemonc_other': 13,
        'outside_universe': 119, 'referring_proxy': 123,
    },
    'core_present': 381,
    'core_absent': 119,
    'core_with_evidence_excl_services': 347,
    'core_with_two_or_more_excl_services': 206,
    'core_under_strict_links': 340,
    'npi_link_confidence_public': {'high': 1571, 'low_name_based': 105, 'mixed': 10, 'npi_keyed_only': 680},
    'flags': {
        'flag_bmt_pediatric_program_only': 355, 'flag_bmt_centers_in_two_states': 4,
        'flag_medicare_specialty_only_public': 80, 'flag_ctgov_context_only': 52,
        'flag_services_company_arm_only': 120, 'flag_services_legend_autolus_only': 75,
        'flag_non_physician_taxonomy_class': 67, 'needs_review': 177,
    },
    'core_pediatric_program_only': 20,
    'research_pi_without_lymphoma_brand': 153,
    'outside_universe_by_class': {
        'pediatric_hematology_oncology': 250, 'adult_hematology_oncology': 93, 'other': 58, 'nurse_practitioner': 53,
        'internal_medicine_other': 29, 'pediatrics_other': 17, 'physician_assistant': 10, 'pathology': 5,
        'registered_nurse_or_cns': 2, 'unknown': 2, 'student_or_trainee': 1,
    },
    'outside_universe_by_identity_source': {
        'no_registry_lookup': 130, 'nppes_name_query': 310, 'nppes_number_lookup': 78, 'nppes_number_lookup_empty': 2,
    },
    'outside_universe_by_flag': {
        'ev_bmt_roster': 281, 'ev_fact_director': 23, 'ev_ctgov_investigator': 33, 'ev_op_cart_research_pi': 96,
        'ev_op_cart_services': 146, 'ev_medicare_cell_therapy': 25,
    },
    'needs_review_by_reason': {
        'low_name_based_name_link': 105, 'outside_universe_name_link_not_hematology_class': 54, 'mixed_name_link': 10,
        'probable_false_name_link': 5, 'bmt_centers_in_two_states': 4, 'registry_lookup_empty': 2,
        'possible_recipient_mixup': 1,
    },
    'relabel_candidates': 147,
    'relabel_by_division': {'hemonc_other': 78, 'referring_proxy': 69},
    'relabel_under_strict_links': 143,
    'relabel_by_evidence_types_excl_services': {'1': 123, '2': 20, '3': 4},
    'relabel_pediatric_program_only': 56,
    'relabel_medicare_specialty_only': 10,
    'relabel_needs_review': 6,
    'relabel_by_link_confidence': {'high': 121, 'low_name_based': 6, 'npi_keyed_only': 20},
    'totals': {
        'bmt_center_listings': 1617, 'fact_program_links': 180, 'ctgov_trial_links': 467,
        'op_cart_research_pi_records_2019_2025': 12055, 'op_lymphoma_cart_research_pi_records_2019_2025': 7948,
        'op_cart_research_pi_record_usd_2019_2025': '224385782.08',
        'op_lymphoma_only_cart_services_usd_2019_2025': '9774513.85',
        'op_cart_only_services_usd_2019_2025_outside_universe': '880157.17',
        'npis_with_kite_legend_autolus_service_years': 618, 'kite_legend_autolus_company_years': 1248,
        'medicare_0540t_services': 69, 'medicare_hct_specialty_npi_years': 980,
    },
    'fingerprints': {
        'npi_cart_treater_evidence': '33431bfd4e387126d8ebbad9f75af636563f409f318728d456323e3bee0f867e',
        'division_relabel_candidates': '43b00e2fea1737c6311b9469c2d1ad75a814bc1104e9b3d8f418a27de75905aa',
    },
}


def weak(status):
    """A link that rests on a nickname or prefix first name, a state mismatch, or a name-only registry query."""
    return 'national' in status or status.startswith(('fuzzy', 'exact_name_unique'))


def best_status(statuses):
    """Strongest outcome among several mentions of one person in one source."""
    return min(statuses, key=STATUS_ORDER.index)


def link_confidence(statuses):
    """Weak sources and a confidence label from the best status of each name-based source."""
    weak_sources = sorted(source for source, status in statuses.items() if weak(status))
    if not statuses:
        return weak_sources, 'npi_keyed_only'
    if not weak_sources:
        return weak_sources, 'high'
    return weak_sources, 'mixed' if len(weak_sources) < len(statuses) else 'low_name_based'


def luhn_ok(npi):
    """NPI check digit: Luhn over the ten digits with the card-issuer prefix 80840."""
    if not re.fullmatch(r'\d{10}', npi):
        return False
    digits = [int(d) for d in '80840' + npi]
    total = sum(d if k % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for k, d in enumerate(reversed(digits)))
    return total % 10 == 0


def joined(values, separator=';'):
    return separator.join(sorted(set(values)))


def split(value, separator=';'):
    return [part for part in value.split(separator) if part]


def amount(value):
    """Dollar value of a cell for arithmetic; a blank cell means the amount was not computed for the row."""
    return float(value) if value else 0.0


def link_mentions(audit, trial_class, false_links=FALSE_LINKS):
    """Accepted name mentions by NPI and source, and the count of mentions dropped as known false links."""
    linked, dropped = defaultdict(lambda: defaultdict(list)), Counter()
    for mention in audit:
        npi = mention['npi']
        if not npi:
            continue
        if npi in false_links:
            dropped[npi] += 1
            continue
        linked[npi][mention['source']].append({**mention, 'trial_class': trial_class.get(mention['src_id'], '')})
    return linked, dropped


def research_pi(records):
    """NPI to totals over Open Payments Research records that name a CAR-T product.

    A record counts in full for every principal-investigator slot it lists.
    """
    out = defaultdict(lambda: {'records': 0, 'lymphoma': 0, 'usd': 0.0, 'brands': set(), 'studies': set()})
    for record in records:
        brands = set(split(record['cart_products']))
        if not brands:
            continue
        for npi in split(record['pi_npis']):
            entry = out[npi]
            entry['records'] += 1
            entry['lymphoma'] += bool(brands & LYMPHOMA_BRANDS)
            entry['usd'] += float(record['total_amount_of_payment_usdollars'] or 0)
            entry['brands'] |= brands
            entry['studies'].add(record['clinicaltrials_gov_identifier'] or record['name_of_study'][:40])
    return out


def company_service_years(groups_by_year):
    """NPI to company to the program years with a service-type payment from Kite, Legend Biotech or Autolus.

    Each group is (company, division group, nature class, product naming, rows, dollars, NPIs). The search
    counted groups, which overstated years; a year is counted once here whatever product was named.
    """
    out = defaultdict(lambda: defaultdict(set))
    for year, groups in groups_by_year.items():
        for company, _, nature, _, _, _, npis in groups:
            if nature == 'services':
                for npi in npis:
                    out[npi][COMPANIES[company]].add(int(year))
    return out


def primary_class(primary):
    """Coarse class of one primary taxonomy description."""
    for pattern, label in [('Nurse Practitioner', 'nurse_practitioner'), ('Physician Assistant', 'physician_assistant'),
                           ('Registered Nurse|Clinical Nurse Specialist', 'registered_nurse_or_cns'),
                           ('Pediatrics', 'pediatrics_other'), ('Internal Medicine', 'internal_medicine_other'),
                           ('^Pathology', 'pathology'), ('Pharmacist', 'pharmacist'),
                           ('Student|Resident', 'student_or_trainee')]:
        if re.search(pattern, primary):
            return label
    return 'other:' + primary if primary else 'unknown'


def taxonomy_class(taxonomies):
    """Coarse class from NPPES taxonomies given as (code, description, is_primary).

    Hematology-oncology is read from any taxonomy, every other class from the primary one. A taxonomy the
    registry returns without a description is shown by its code.
    """
    labelled = [(description or (f'code {code}' if code else ''), main) for code, description, main in taxonomies]
    labelled = [(description.strip(' ,'), main) for description, main in labelled]
    primary = next((description for description, main in labelled if main), labelled[0][0] if labelled else '')
    every = ' | '.join(description for description, _ in labelled)
    if 'Pediatric Hematology' in every:
        return 'pediatric_hematology_oncology'
    adult = re.search(r'Hematology & Oncology|Internal Medicine, Hematology|Medical Oncology|Hematology$', every)
    if adult and not re.search('Nurse|Physician Assistant', primary):
        return 'adult_hematology_oncology'
    return primary_class(primary)


def open_payments_class(recipient_type, specialty):
    """Coarse class of a recipient known only from Open Payments, from its recipient type and specialty.

    The specialty reads group|classification|specialization. Its last two parts are worded like a registry
    taxonomy, so the class is assigned by the same rule and carries the same labels.
    """
    both = recipient_type + specialty
    if 'Nurse Practitioner' in both:
        return 'nurse_practitioner'
    if 'Physician Assistant' in both:
        return 'physician_assistant'
    if 'Nurse' in both:
        return 'registered_nurse_or_cns'
    if 'Pediatric Hematology' in specialty:
        return 'pediatric_hematology_oncology'
    if re.search('Hematology|Medical Oncology', specialty):
        return 'adult_hematology_oncology'
    parts = [part.strip() for part in specialty.split('|') if part.strip()]
    return primary_class(', '.join(parts[1:] or parts))


def registry_index(lookup_cache):
    """NPI to the NPPES record seen in any cached name query."""
    out = {}
    for records in lookup_cache.values():
        for record in records or []:
            out.setdefault(record['npi'], record)
    return out


def identity(npi, member, registry, by_number, recipient, named=True):
    """Identity columns. Universe NPIs carry no name here; outside-universe names come from NPPES only.

    `recipient` is the Open Payments recipient row of an outside-universe NPI paid for services, if any.
    A record from a name query is used only when `named` says a publishable name link reached the NPI, so
    that the presence of a registry name never hints at a link held in a restricted source.
    """
    blank = {'nppes_last_name': '', 'nppes_first_name': '', 'nppes_state': '', 'nppes_primary_taxonomy': ''}
    if member:
        return {**blank, 'identity_source': 'universe_table', 'taxonomy_class': 'in_universe'}
    if named and npi in registry:
        record = registry[npi]
        primary = [d for _, d, main in record['tax'] if main] or [d for _, d, _ in record['tax'][:1]]
        return {'identity_source': 'nppes_name_query', 'nppes_last_name': record['last'], 'nppes_first_name': record['first'],
                'nppes_state': record['state'], 'nppes_primary_taxonomy': ((primary[0] if primary else '') or '').strip(' ,'),
                'taxonomy_class': taxonomy_class(record['tax'])}
    if by_number.get(npi):
        record = by_number[npi]
        primary = (record.get('tax') or [''])[0].strip(' ,')
        saved = record['tax_class']
        # The by-number cache keeps only the primary description and the class assigned from all taxonomies.
        label = saved if saved in HEMATOLOGY_CLASSES else primary_class(primary)
        return {'identity_source': 'nppes_number_lookup', 'nppes_last_name': record['last'], 'nppes_first_name': record['first'],
                'nppes_state': record['state'], 'nppes_primary_taxonomy': primary, 'taxonomy_class': label}
    if npi in by_number:
        return {**blank, 'identity_source': 'nppes_number_lookup_empty', 'taxonomy_class': 'unknown'}
    label = open_payments_class(recipient['primary_type'], recipient['specialty']) if recipient else 'unknown'
    return {**blank, 'identity_source': 'no_registry_lookup', 'taxonomy_class': label}


def name_columns(mentions, programs, counts):
    """Detail columns of the four name-based sources for one NPI, public and restricted alike."""
    bmt, kite, fact = mentions.get('bmt', []), mentions.get('kite', []), mentions.get('fact', [])
    overall, site = mentions.get('ctgov_overall', []), mentions.get('ctgov_site', [])
    trials = {(role, kind): {m['src_id'] for m in rows if m['trial_class'] == f'cart_{kind}_trial'}
              for role, rows in (('overall', overall), ('site', site)) for kind in ('product', 'context')}
    product = trials['overall', 'product'] | trials['site', 'product']
    context = trials['overall', 'context'] | trials['site', 'context']
    ids = sorted({m['src_id'] for m in bmt})
    out = {
        'bmt_center_ids': ';'.join(ids), 'bmt_centers': joined((m['org'] for m in bmt), ' | '),
        'bmt_roles': joined(role for m in bmt for role in split(m['role'])),
        'bmt_states': joined(state for m in bmt for state in split(m['states'])),
        'flag_bmt_pediatric_program_only': str(int(bool(ids) and all(programs.get(k) == 'Pediatric Program' for k in ids))),
        'flag_bmt_no_positive_count_2021_2025': str(int(bool(ids) and not any(counts.get(k) for k in ids))),
        'kite_centers': joined((m['org'] for m in kite), ' | '),
        'fact_program_ccns': joined(m['src_id'] for m in fact), 'fact_programs': joined((m['org'] for m in fact), ' | '),
        'ctgov_product_trials_overall_official': str(len(trials['overall', 'product'])),
        'ctgov_product_trials_site_investigator': str(len(trials['site', 'product'])),
        'ctgov_product_trials_any_role': str(len(product)), 'ctgov_context_trials_any_role': str(len(context)),
        'ctgov_nct_ids': ';'.join(sorted(product | context)),
    }
    for source, name in STATUS_COLUMN.items():
        out[name] = best_status([m['match_status'] for m in mentions[source]]) if mentions.get(source) else ''
    return out


def payment_columns(npi, member, research, tier, recipient, company_years):
    """Open Payments detail columns. A dollar column is blank where the search did not compute it."""
    pi = research.get(npi)
    lymphoma = tier.get(npi, {}).get('lymphoma_only_services_usd') or 0
    outside = (recipient or {}).get('cart_only_services_usd') or 0
    companies = company_years.get(npi, {}) if member else {}
    return {
        'op_cart_research_pi_records_2019_2025': str(pi['records'] if pi else 0),
        'op_lymphoma_cart_research_pi_records_2019_2025': str(pi['lymphoma'] if pi else 0),
        'op_cart_research_studies': str(len(pi['studies']) if pi else 0),
        'op_cart_research_brands': joined(pi['brands']) if pi else '',
        'op_cart_research_pi_record_usd_2019_2025': f"{pi['usd'] if pi else 0:.2f}",
        'op_lymphoma_only_cart_services_usd_2019_2025': f'{float(lymphoma):.2f}' if member else '',
        'op_cart_only_services_usd_2019_2025_outside_universe': '' if member else f'{float(outside):.2f}',
        'op_kite_legend_autolus_service_years': ';'.join(f'{c}:{len(y)}' for c, y in sorted(companies.items())),
    }


def score(row, flags):
    """Additive sort aid over the evidence flags given; the weights are arbitrary and it measures nothing."""
    overall, either = int(row['ctgov_product_trials_overall_official']), int(row['ctgov_product_trials_any_role'])
    records = int(row['op_cart_research_pi_records_2019_2025'])
    dollars = max(amount(row['op_lymphoma_only_cart_services_usd_2019_2025']),
                  amount(row['op_cart_only_services_usd_2019_2025_outside_universe']))
    return (3 * flags['ev_bmt_roster'] + 3 * flags['ev_fact_director'] + 3 * flags.get('ev_kite_team', 0)
            + min(6, 2 * overall + either - overall) + min(2, int(row['ctgov_context_trials_any_role']))
            + (2 + (records >= 10) + (records >= 50) if records else 0)
            + (dollars > 0) + (dollars >= 10000) + bool(row['op_kite_legend_autolus_service_years'])
            + 3 * bool(row['medicare_0540t_year_services']) + 2 * bool(row['medicare_hct_specialty_years']))


def service_basis(row):
    """Whether service dollars on CAR-T product records exist, and the companies of the company arm."""
    dollars = (amount(row['op_lymphoma_only_cart_services_usd_2019_2025']) > 0
               or amount(row['op_cart_only_services_usd_2019_2025_outside_universe']) > 0)
    return dollars, {part.split(':')[0] for part in split(row['op_kite_legend_autolus_service_years'])}


def evidence_flags(row, sources=PUBLIC_SOURCES):
    """Each evidence flag is 1 exactly when its detail columns hold something."""
    services, companies = service_basis(row)
    flags = {
        'ev_bmt_roster': int(bool(row['bmt_center_ids'])), 'ev_fact_director': int(bool(row['fact_program_ccns'])),
        'ev_ctgov_investigator': int(bool(row['ctgov_nct_ids'])),
        'ev_op_cart_research_pi': int(int(row['op_cart_research_pi_records_2019_2025']) > 0),
        'ev_op_cart_services': int(services or bool(companies)),
        'ev_medicare_cell_therapy': int(bool(row['medicare_0540t_year_services'] or row['medicare_hct_specialty_years'])),
    }
    if 'kite' in sources:
        flags['ev_kite_team'] = int(bool(row['kite_centers']))
    return flags


def derived(row, sources=PUBLIC_SOURCES):
    """Flags, counts, sort aid and link confidence that follow from the detail columns of one row.

    With the default sources Kite is left out and the result is what the tracked table publishes.
    """
    services, companies = service_basis(row)
    flags = evidence_flags(row, sources)
    statuses = {source: row[STATUS_COLUMN[source]] for source in sources if row[STATUS_COLUMN[source]]}
    weak_sources, confidence = link_confidence(statuses)
    strict = ({SOURCE_FLAG[source] for source, status in statuses.items() if not weak(status)}
              | {flag for flag in ('ev_op_cart_research_pi', 'ev_medicare_cell_therapy') if flags[flag]})
    total = sum(flags.values())
    listed = total - flags['ev_op_cart_services'] - flags['ev_medicare_cell_therapy']
    return flags, {
        'n_evidence_types': total, 'n_evidence_types_excl_services': total - flags['ev_op_cart_services'],
        'n_evidence_types_excl_services_strict_links': len(strict), 'evidence_score': score(row, flags),
        'name_link_weak_sources': ';'.join(weak_sources), 'npi_link_confidence': confidence,
        'flag_medicare_specialty_only': int(bool(row['medicare_hct_specialty_years']) and listed == 0
                                            and not row['medicare_0540t_year_services']),
        'flag_ctgov_context_only': int(flags['ev_ctgov_investigator'] and row['ctgov_product_trials_any_role'] == '0'),
        'flag_services_company_arm_only': int(bool(companies) and not services),
        'flag_services_legend_autolus_only': int(bool(companies) and not services and 'kite' not in companies),
        'flag_non_physician_taxonomy_class': int(row['taxonomy_class'] in NON_PHYSICIAN_CLASSES
                                                 or bool(NON_PHYSICIAN_TEXT.search(row['taxonomy_class']))),
    }


PUBLIC_SUFFIXED = ['n_evidence_types', 'n_evidence_types_excl_services', 'n_evidence_types_excl_services_strict_links',
                   'evidence_score', 'name_link_weak_sources', 'npi_link_confidence', 'flag_medicare_specialty_only']
PLAIN_FLAGS = ['flag_ctgov_context_only', 'flag_services_company_arm_only', 'flag_services_legend_autolus_only',
               'flag_non_physician_taxonomy_class']


def public_values(row):
    """Every tracked value that is recomputable from other tracked columns of the same row, as text."""
    flags, values = derived(row)
    out = {flag: str(value) for flag, value in flags.items()}
    out.update({f'{name}_public': str(values[name]) for name in PUBLIC_SUFFIXED})
    out.update({name: str(values[name]) for name in PLAIN_FLAGS})
    return out


def review_reasons(row):
    """Why a row needs a hand check before it is used to relabel anyone; uses tracked columns only."""
    reasons = [HAND_CHECK[row['npi']]] if row['npi'] in HAND_CHECK else []
    if row['npi_link_confidence_public'] in ('low_name_based', 'mixed'):
        reasons.append(row['npi_link_confidence_public'] + '_name_link')
    named = any(row[STATUS_COLUMN[source]] for source in PUBLIC_SOURCES)
    if row['in_universe'] == '0' and named and row['taxonomy_class'] not in HEMATOLOGY_CLASSES:
        reasons.append('outside_universe_name_link_not_hematology_class')
    if row['identity_source'] == 'nppes_number_lookup_empty':
        reasons.append('registry_lookup_empty')
    if row['flag_bmt_centers_in_two_states'] == '1':
        reasons.append('bmt_centers_in_two_states')
    return ';'.join(reasons)


def assemble(members, linked, inputs):
    """One row per NPI with any evidence, Kite included; `inputs` holds the NPI-keyed sources."""
    research, tier, recipients = inputs['research'], inputs['tier'], inputs['recipients']
    paid_outside = {npi for npi, r in recipients.items() if npi not in members and float(r['cart_only_services_usd'] or 0) > 0}
    paid_inside = {npi for npi, r in tier.items() if float(r['lymphoma_only_services_usd'] or 0) > 0}
    npis = (set(linked) | set(research) | paid_inside | set(inputs['company_years']) | paid_outside
            | set(inputs['medicare_0540t']) | set(inputs['medicare_specialty']))
    rows = []
    for npi in sorted(npis):
        member = members.get(npi)
        row = {'npi': npi, 'in_universe': str(int(bool(member))), 'is_core': member['is_core'] if member else '0',
               'division': member['division'] if member else 'outside_universe'}
        recipient = recipients[npi] if npi in paid_outside else None
        row.update(name_columns(linked.get(npi, {}), inputs['programs'], inputs['positive_counts']))
        row.update(payment_columns(npi, bool(member), research, tier, recipient, inputs['company_years']))
        row['medicare_0540t_year_services'] = ';'.join(inputs['medicare_0540t'].get(npi, []))
        row['medicare_hct_specialty_years'] = joined(inputs['medicare_specialty'].get(npi, []))
        row['flag_bmt_centers_in_two_states'] = str(int(len(split(row.pop('bmt_states'))) > 1))
        # A row with no publishable evidence is never tracked, so its registry record may be used freely.
        named = any(row[STATUS_COLUMN[source]] for source in PUBLIC_SOURCES) or not any(evidence_flags(row).values())
        row.update(identity(npi, member, inputs['registry'], inputs['by_number'], recipient, named))
        flags, with_kite = derived(row, list(STATUS_COLUMN))
        director = bool(set(split(row['bmt_roles'])) & {'medical_director', 'program_director'})
        row.update({name: str(value) for name, value in with_kite.items() if name in PUBLIC_SUFFIXED})
        row['evidence_score'] = str(with_kite['evidence_score'] + director)
        row['ev_kite_team'] = str(flags['ev_kite_team'])
        row.update(public_values(row))
        reasons = review_reasons(row)
        row.update({'needs_review': str(int(bool(reasons))), 'needs_review_reasons': reasons})
        rows.append(row)
    return rows


def split_kite_only(rows):
    """Rows with public evidence, and rows whose only evidence is a Kite team list (never tracked)."""
    tracked = [row for row in rows if int(row['n_evidence_types_public']) > 0]
    return tracked, [row for row in rows if int(row['n_evidence_types_public']) == 0]


RELABEL_COPIED = ['n_evidence_types_excl_services_public', 'n_evidence_types_excl_services_strict_links_public',
                  'evidence_score_public', 'bmt_center_ids', 'fact_program_ccns', 'ctgov_nct_ids',
                  'op_cart_research_pi_records_2019_2025', 'medicare_hct_specialty_years', 'npi_link_confidence_public',
                  'name_link_weak_sources_public', 'flag_bmt_pediatric_program_only', 'flag_medicare_specialty_only_public',
                  'flag_ctgov_context_only', 'needs_review', 'needs_review_reasons']


def kite_dependent(rows, members, linked, inputs, names):
    """Tracked rows whose published values would change if Kite evidence did not exist; must be empty."""
    without = {npi: {source: found for source, found in sources.items() if source != 'kite'} for npi, sources in linked.items()}
    blind = {row['npi']: row for row in assemble(members, {npi: sources for npi, sources in without.items() if sources}, inputs)}
    tracked = {row['npi']: row for row in rows}
    return sorted(npi for npi in set(blind) | set(tracked)
                  if npi not in blind or npi not in tracked or any(blind[npi][n] != tracked[npi][n] for n in names))


def relabel_candidates(rows):
    """Universe NPIs labelled referring_proxy or hemonc_other with evidence other than service payments."""
    out = []
    for row in rows:
        if row['division'] in RELABEL_DIVISIONS and int(row['n_evidence_types_excl_services_public']) >= 1:
            flags = '+'.join(flag[3:] for flag in PUBLIC_FLAGS if row[flag] == '1')
            out.append({'npi': row['npi'], 'evidence_flags': flags, **{name: row[name] for name in RELABEL_COPIED}})
    return sorted(out, key=lambda r: (-int(r['n_evidence_types_excl_services_public']), -int(r['evidence_score_public']), r['npi']))


def reconcile(rows, probe, members):
    """Differences between the rebuild before fixes and the ad hoc table of the search, by column.

    Columns the fixes change on purpose are counted apart so that every other count is expected to be zero.
    """
    old = {row['npi']: row for row in probe}
    new = {row['npi']: row for row in rows}
    shared = sorted(set(old) & set(new))
    same = {'bmt_center_ids': 'bmt_center_ids', 'bmt_centers': 'bmt_centers', 'bmt_roles': 'bmt_roles',
            'kite_centers': 'kite_centers', 'fact_programs': 'fact_programs', 'ctgov_nct_ids': 'ctgov_nct_ids',
            'op_cart_research_brands': 'op_cart_research_brands', 'medicare_0540T': 'medicare_0540t_year_services',
            'medicare_hct_specialty_years': 'medicare_hct_specialty_years', 'division': 'division',
            'in_universe': 'in_universe', 'is_core': 'is_core'}
    numeric = {**{flag: flag for flag in FLAGS}, 'n_evidence_types': 'n_evidence_types',
               'n_delivery_evidence_types': 'n_evidence_types_excl_services', 'evidence_score': 'evidence_score',
               'ctgov_product_trials_overall_official': 'ctgov_product_trials_overall_official',
               'ctgov_product_trials_site_investigator': 'ctgov_product_trials_site_investigator',
               'ctgov_context_trials_any_role': 'ctgov_context_trials_any_role',
               'op_cart_research_pi_records_2019_2025': 'op_cart_research_pi_records_2019_2025',
               'op_lymphoma_cart_research_pi_records': 'op_lymphoma_cart_research_pi_records_2019_2025',
               'op_cart_research_studies': 'op_cart_research_studies',
               'op_cart_research_record_usd': 'op_cart_research_pi_record_usd_2019_2025',
               'op_lymphoma_only_cart_services_usd_2019_2025': 'op_lymphoma_only_cart_services_usd_2019_2025',
               'op_cart_only_services_usd_outside_universe': 'op_cart_only_services_usd_2019_2025_outside_universe'}
    unexpected = {'npis_only_in_search_table': len(set(old) - set(new)), 'npis_only_in_rebuild': len(set(new) - set(old))}
    for before, after in same.items():
        unexpected[before] = sum(old[n][before] != new[n][after] for n in shared)
    for before, after in numeric.items():
        unexpected[before] = sum(abs(amount(old[n][before]) - amount(new[n][after])) > 0.005 for n in shared)
    years = lambda value: {part.split(':')[0].replace(' ', '_'): int(part.split(':')[1]) for part in split(value)}
    before_after = [(years(old[n]['op_pure_play_service_payer_years']), years(new[n]['op_kite_legend_autolus_service_years']))
                    for n in shared]
    unexpected['company_year_count_above_search_group_count'] = sum(
        set(a) != set(b) or any(b[c] > a[c] for c in b) for a, b in before_after)
    confidence = Counter((old[n]['npi_link_confidence'], new[n]['npi_link_confidence']) for n in shared)
    allowed = {('high', 'high'), ('mixed', 'mixed'), ('low_name_based', 'low_name_based'), ('high', 'npi_keyed_only'),
               ('low_name_based', 'high'), ('mixed', 'high'), ('low_name_based', 'mixed')}
    unexpected['npi_link_confidence_other_change'] = sum(v for k, v in confidence.items() if k not in allowed)
    status_pairs = [('bmt_match_status', ['bmt_match_status']), ('kite_match_status', ['kite_match_status']),
                    ('fact_match_status', ['fact_match_status']),
                    ('ctgov_match_status', ['ctgov_overall_official_match_status', 'ctgov_site_investigator_match_status'])]
    changed_by_fix = {
        'company_year_counts_lowered': sum(a != b for a, b in before_after),
        'company_entries_above_7_in_search_table': sum(v > 7 for a, _ in before_after for v in a.values()),
        'npi_link_confidence_raised_by_best_status': sum(v for (a, b), v in confidence.items()
                                                         if a != b and b != 'npi_keyed_only'),
        'match_status_now_best_instead_of_last': sum(old[n][before] not in [new[n][c] for c in after]
                                                     for n in shared for before, after in status_pairs if old[n][before]),
        'strict_link_count_changed': sum(int(old[n]['n_delivery_evidence_types_strict_links'])
                                         != int(new[n]['n_evidence_types_excl_services_strict_links']) for n in shared),
        'taxonomy_class_changed': sum(old[n]['taxonomy_class'] != new[n]['taxonomy_class'] for n in shared),
        'lymphoma_services_zero_now_blank_outside_universe': sum(n not in members for n in shared),
    }
    return {'search_table_rows': len(old), 'rebuild_rows_before_fixes': len(new), 'unexpected_differences': unexpected,
            'changed_by_fix': changed_by_fix}


def read_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def collect():
    """Stage every search input and return the parsed sources with the list of frozen paths."""
    paths = []

    def frozen(relative):
        paths.append(stage(relative))
        return paths[-1]

    audit = read_csv(frozen(f'{SEARCH}/name_match_audit.csv'))
    trial_class = {r['nct_id']: r['classification'] for r in read_csv(frozen(f'{SEARCH}/ctgov/ctgov_trial_classification.csv'))}
    center_rows = read_csv(frozen('rwd-ehr-registries/verify/bmtinfonet_center_volumes.csv'))
    probe = read_csv(frozen(f'{SEARCH}/npi_cart_treater_evidence.csv'))
    medicare_0540t, medicare_specialty = defaultdict(list), defaultdict(list)
    for r in read_csv(frozen(f'{MEDICARE}/provider_service_cart_rows_2013_2024.csv')):
        assert r['HCPCS_Cd'] == '0540T', 'Unexpected procedure code in the Medicare service extract'
        medicare_0540t[r['Rndrng_NPI']].append(f"{r['service_year']}:{r['Tot_Srvcs']}")
    for r in read_csv(frozen(f'{MEDICARE}/by_provider_specialty_HCT_cellular_therapy_2019_2024.csv')):
        medicare_specialty[r['Rndrng_NPI']].append(r['service_year'])
    lookups = load(frozen(f'{SEARCH}/nppes_cache/nppes_lookup_cache_2026-11-19.json'))
    inputs = {
        'programs': {r['center_id']: r['program_type'] for r in center_rows},
        'positive_counts': {r['center_id']: any(r[f'n_{y}'].isdigit() and int(r[f'n_{y}']) > 0 for y in range(2021, 2026))
                            for r in center_rows},
        'research': research_pi(r for y in YEARS for r in read_gz(frozen(f'{PAYMENTS}/scan/research_cart_rows_{y}.csv.gz'))),
        'tier': {r['npi']: r for r in read_csv(frozen(f'{PAYMENTS}/npi_cart_tier_summary_2019_2025.csv'))},
        'recipients': {r['npi']: r for r in read_csv(frozen(f'{PAYMENTS}/outside_universe_cart_only_recipients.csv'))},
        'company_years': company_service_years({y: load(frozen(f'{PAYMENTS}/verify/verify_general_{y}.json'))['pure']
                                                for y in YEARS}),
        'medicare_0540t': medicare_0540t, 'medicare_specialty': medicare_specialty,
        'registry': registry_index(lookups), 'queries': len(lookups),
        'queries_at_cap': sum(len(records or []) == 200 for records in lookups.values()),
        'by_number': load(frozen(f'{SEARCH}/nppes_cache/nppes_number_lookup_cache_2026-11-19.json')),
    }
    return audit, trial_class, probe, inputs, paths


CTGOV = 'ClinicalTrials.gov study records processed on 2026-11-19'
RESEARCH = 'Open Payments Research records, program years 2019-2025 (publication of 2026-06-30)'
GENERAL = 'Open Payments General records, program years 2019-2025 (publication of 2026-06-30)'
STATUS_WORDS = ('exact_first_last_state = one universe NPI with the same full first name, last name and state '
                '(_city_tiebreak when the city settled it); nppes_in_universe or nppes_outside_universe = found by a '
                'state-scoped NPPES registry query, inside or outside the universe; the weak values are '
                'fuzzy_first_last_state (nickname, prefix or middle-name first name, or one part of a compound '
                'surname), exact_name_unique_state_differs and exact_name_unique_no_state (one universe NPI with '
                'that full name, state different or not given) and any value ending _national (registry query on '
                'the name alone).')
NEVER = 'Never blank.'


def columns():
    """Published columns of the evidence table, tracked first and then those kept local."""
    public = [
        column('npi', 'Ten-digit individual National Provider Identifier (NPI); join key to hcp_divisions.csv.', NEVER, 'project'),
        column('in_universe', f'1 if the NPI is one of the 17,510 NPIs of hcp_divisions.csv ({UNIVERSE_SNAPSHOT}), else 0.', NEVER, 'project'),
        column('is_core', f'1 if the NPI is one of the 500 core healthcare professionals (HCPs) of hcp_divisions.csv ({UNIVERSE_SNAPSHOT}), else 0.', NEVER, 'project'),
        column('division', f'Division label of hcp_divisions.csv ({UNIVERSE_SNAPSHOT}, label version 1.0.0), or outside_universe when the NPI is not in that table.', NEVER, 'project'),
        column('identity_source', 'Where identity fields come from: universe_table (join hcp_divisions.csv on npi; no name is repeated here), nppes_name_query (record returned by the name query to the National Plan and Provider Enumeration System (NPPES) registry behind a published name link, 2026-11-19), nppes_number_lookup (registry lookup by NPI on 2026-11-19), nppes_number_lookup_empty (that lookup returned nothing), or no_registry_lookup (no registry record is attached: the NPI was reached through payment or Medicare records that carry the NPI, and no registry lookup by number was saved).', NEVER, 'project'),
        column('nppes_last_name', 'Last name in the NPPES registry record, upper case as the registry returns it; outside-universe NPIs only.', 'Universe NPI, or no registry record attached; identity_source says which.', 'nppes'),
        column('nppes_first_name', 'First name in the NPPES registry record, upper case as the registry returns it; outside-universe NPIs only.', 'Universe NPI, or no registry record attached; identity_source says which.', 'nppes'),
        column('nppes_state', 'State of the first practice-location address in the NPPES registry record, as a two-letter code; outside-universe NPIs only. One row holds a state of Mexico written in full.', 'Universe NPI, or no registry record attached; identity_source says which.', 'nppes'),
        column('nppes_primary_taxonomy', 'Description of the primary taxonomy in the NPPES registry record; outside-universe NPIs only.', 'Universe NPI, no registry record attached, or the registry returned the taxonomy without a description.', 'nppes'),
        column('taxonomy_class', 'Coarse class. in_universe for every universe NPI (not classified further). For outside-universe NPIs with a registry record: pediatric_hematology_oncology or adult_hematology_oncology if any registry taxonomy says so, otherwise from the primary taxonomy nurse_practitioner, physician_assistant, registered_nurse_or_cns (registered nurse or clinical nurse specialist), pediatrics_other, internal_medicine_other, pathology, pharmacist, student_or_trainee or other:<primary taxonomy>, written other:code <taxonomy code> when the registry gives no description. When identity_source is no_registry_lookup the same labels are assigned from the Open Payments recipient type and specialty. unknown when nothing is available.', NEVER, 'project'),
        column('ev_bmt_roster', '1 if a person listed as program director, medical director or physician on a US center page of the BMT InfoNet (Blood & Marrow Transplant Information Network) directory of centers giving chimeric antigen receptor T-cell (CAR-T) therapy, read on 2026-11-19, was matched by name to this NPI. Not limited to adult or lymphoma programs.', 'Never blank; 0 means no accepted name match, not absence from every roster.', 'bmtinfonet'),
        column('ev_fact_director', '1 if this NPI was matched by name to a director of a program accredited for immune effector cell therapy by the Foundation for the Accreditation of Cellular Therapy (FACT), from Data/processed/2026-10-24/fact_iec_programs.csv (listing observed 2026-10-25).', 'Never blank; 0 means no accepted name match.', 'fact'),
        column('ev_ctgov_investigator', f'1 if this NPI was matched by name to an overall official or to a US site principal or sub-investigator on one of 337 trials kept as CAR-T lymphoma trials among the 402 {CTGOV}. The trial filter and the name matching are this project\'s modifications of the registry data.', 'Never blank; 0 means no accepted name match. Site names exist for only 74 of the 337 trials.', 'ctgov'),
        column('ev_op_cart_research_pi', f'1 if this NPI holds a principal-investigator (PI) slot on at least one of the Centers for Medicare & Medicaid Services (CMS) {RESEARCH} that names a commercial CAR-T product of any indication.', NEVER, 'open_payments'),
        column('ev_op_cart_services', f'1 if, in {GENERAL}, a universe NPI received more than 0 US dollars of service-type payments (consulting, honoraria, speaker or faculty compensation) on records naming only CAR-T products with at least one lymphoma product, or any service-type payment from a payer named Kite, Legend Biotech or Autolus; or an outside-universe NPI received more than 0 US dollars of service-type payments on records naming only CAR-T products of any indication.', NEVER, 'open_payments'),
        column('ev_medicare_cell_therapy', '1 if the NPI has a row for procedure code 0540T (CAR-T administration) in the CMS Medicare Physician & Other Practitioners by Provider and Service file (service years 2013-2024 searched), or provider type Hematopoietic Cell Transplantation and Cellular Therapy in the by Provider file in any service year 2019-2024. The provider type also covers transplant-only physicians.', NEVER, 'cms'),
        column('n_evidence_types_public', 'Number of the six ev_ flags of this table equal to 1 (1 to 6). Evidence held in restricted columns is not counted.', NEVER, 'project'),
        column('n_evidence_types_excl_services_public', 'n_evidence_types_public minus ev_op_cart_services. It counts listing, trial, research and Medicare markers, not treatment delivery.', 'Never blank; 0 means the only published evidence is industry service payments.', 'project'),
        column('n_evidence_types_excl_services_strict_links_public', 'Evidence types other than service payments that remain when name-based sources with a weak link are discarded (0 to 5): ev_bmt_roster, ev_fact_director and ev_ctgov_investigator count only if a match status of that source is not weak, ev_op_cart_research_pi and ev_medicare_cell_therapy always count. Weak values are listed under bmt_match_status.', 'Never blank; 0 means no such evidence survives.', 'project'),
        column('evidence_score_public', 'Arbitrary additive sort aid recomputable from this table: 3 if ev_bmt_roster, 3 if ev_fact_director, min(6, 2 x product trials as overall official + 1 x further product trials as site investigator only), min(2, context trials), 2 if a research PI slot (+1 at 10 records, +1 at 50), 1 if either service-dollar column of this table is above 0 (+1 at 10,000 US dollars), 1 if any Kite, Legend Biotech or Autolus service year, 3 if a 0540T row, 2 if the Medicare cell-therapy provider type. Not a measure of anything.', NEVER, 'project'),
        column('bmt_center_ids', 'Semicolon-separated BMT InfoNet directory center identifiers (the number at the end of the center page address) at which the NPI is listed.', 'ev_bmt_roster is 0.', 'bmtinfonet'),
        column('bmt_match_status', 'How the BMT InfoNet name was linked to this NPI, best outcome over its mentions: ' + STATUS_WORDS, 'ev_bmt_roster is 0.', 'bmtinfonet'),
        column('fact_program_ccns', f'Semicolon-separated CMS Certification Numbers (CCN) of the hospitals in cart_centers.csv ({UNIVERSE_SNAPSHOT}) whose FACT-accredited program lists the NPI as a director.', 'ev_fact_director is 0.', 'fact'),
        column('fact_match_status', 'How the FACT director name was linked to this NPI, best outcome over its mentions; same values as bmt_match_status.', 'ev_fact_director is 0.', 'fact'),
        column('ctgov_product_trials_overall_official', 'Number of distinct trials classified by this project as CAR-T product trials (lymphoma condition or title and a CAR-T term in the intervention name, or one of 21 trials added by hand) on which the NPI is an overall official.', 'Never blank; 0 means none matched.', 'ctgov'),
        column('ctgov_product_trials_site_investigator', 'Number of distinct CAR-T product trials on which the NPI is a US site principal or sub-investigator; can overlap the overall-official count.', 'Never blank; 0 means none matched. Site contacts are published only while a site recruits.', 'ctgov'),
        column('ctgov_product_trials_any_role', 'Number of distinct CAR-T product trials on which the NPI holds either role.', 'Never blank; 0 means none matched.', 'ctgov'),
        column('ctgov_context_trials_any_role', 'Number of distinct context trials (lymphoma trials that mention CAR-T in the title or intervention description without a CAR-T term in the intervention name, for example treatments given after CAR-T) on which the NPI holds either role.', 'Never blank; 0 means none matched.', 'ctgov'),
        column('ctgov_nct_ids', 'Semicolon-separated ClinicalTrials.gov identifiers of the product and context trials counted in the ctgov_ columns.', 'ev_ctgov_investigator is 0.', 'ctgov'),
        column('ctgov_overall_official_match_status', 'How the overall-official name was linked to this NPI, best outcome over its mentions; same values as bmt_match_status, where state means any US site state of the trial (used only when the trial has six or fewer such states).', 'No overall-official mention matched.', 'ctgov'),
        column('ctgov_site_investigator_match_status', 'How the site-investigator name was linked to this NPI, best outcome over its mentions; same values as bmt_match_status.', 'No site-investigator mention matched.', 'ctgov'),
        column('op_cart_research_pi_records_2019_2025', f'Number of {RESEARCH} that name a commercial CAR-T product and list this NPI in a principal-investigator slot.', 'Never blank; 0 means none.', 'open_payments'),
        column('op_lymphoma_cart_research_pi_records_2019_2025', 'Subset of op_cart_research_pi_records_2019_2025 naming Yescarta, Tecartus, Kymriah or Breyanzi.', 'Never blank; 0 means none (myeloma or leukemia products only, or no record).', 'open_payments'),
        column('op_cart_research_studies', 'Number of distinct studies behind the records counted in op_cart_research_pi_records_2019_2025, by ClinicalTrials.gov identifier or, when the record has none, the first 40 characters of the study name; a study can count twice when only some of its records carry the identifier.', 'Never blank; 0 means none.', 'open_payments'),
        column('op_cart_research_brands', 'Semicolon-separated CAR-T brand names, in capitals, found on the records counted in op_cart_research_pi_records_2019_2025.', 'ev_op_cart_research_pi is 0.', 'open_payments'),
        column('op_cart_research_pi_record_usd_2019_2025', 'Sum in US dollars of the full amount of every record counted in op_cart_research_pi_records_2019_2025, program years 2019-2025. Mostly paid to institutions, not the physician; a record with several principal investigators counts in full for each.', 'Never blank; 0.00 means no such record.', 'open_payments'),
        column('op_lymphoma_only_cart_services_usd_2019_2025', f'US dollars of service-type payments in {GENERAL} to a universe NPI on records where every named product is a CAR-T product and at least one is Yescarta, Tecartus, Kymriah or Breyanzi. Amounts are as reported by manufacturers.', 'Outside-universe NPI: not computed. 0.00 is an observed zero.', 'open_payments'),
        column('op_cart_only_services_usd_2019_2025_outside_universe', f'For outside-universe NPIs, US dollars of service-type payments in {GENERAL} on records where every named product is a CAR-T product of any indication.', 'Universe NPI: this column does not apply. 0.00 is an observed zero.', 'open_payments'),
        column('op_kite_legend_autolus_service_years', f'For universe NPIs, semicolon-separated company:count pairs (kite, legend_biotech, autolus) giving the number of program years 2019-2025 (1 to 7) with a service-type payment in {GENERAL} from a payer named Kite, Legend Biotech or Autolus, whatever product was named.', 'No such payment, or outside-universe NPI (not computed); in_universe says which.', 'open_payments'),
        column('medicare_0540t_year_services', 'Semicolon-separated service_year:services pairs for procedure code 0540T (CAR-T administration) rows of this NPI in the Medicare Physician & Other Practitioners by Provider and Service file, service years 2013-2024.', 'No row published; CMS withholds rows with 10 or fewer beneficiaries, so blank is not zero.', 'cms'),
        column('medicare_hct_specialty_years', 'Semicolon-separated service years 2019-2024 in which the NPI has provider type Hematopoietic Cell Transplantation and Cellular Therapy in the Medicare by Provider file.', 'Not listed under that provider type in any of those years.', 'cms'),
        column('name_link_weak_sources_public', 'Semicolon-separated name-based sources (bmt, fact, ctgov_overall, ctgov_site) whose match status in this table is one of the weak values listed under bmt_match_status.', 'No weak link among the published name-based sources, or no name-based source at all.', 'project'),
        column('npi_link_confidence_public', 'Confidence that the published name-based evidence belongs to this NPI: high (no weak source), mixed (weak and strong sources), low_name_based (every source weak), npi_keyed_only (no name matching involved). high is not verified identity.', NEVER, 'project'),
        column('flag_bmt_pediatric_program_only', '1 if every BMT InfoNet center listing the NPI is described by the directory as a pediatric program.', 'Never blank; 0 also when ev_bmt_roster is 0.', 'bmtinfonet'),
        column('flag_bmt_centers_in_two_states', '1 if the BMT InfoNet centers listing the NPI are in more than one state: a move not yet reflected on one roster, or two people merged.', 'Never blank; 0 also when ev_bmt_roster is 0.', 'bmtinfonet'),
        column('flag_medicare_specialty_only_public', '1 if the Medicare cell-therapy provider type is the only published evidence other than service payments (no 0540T row, roster, director, trial or research evidence).', NEVER, 'project'),
        column('flag_ctgov_context_only', '1 if ev_ctgov_investigator is 1 and every matched trial is a context trial.', NEVER, 'project'),
        column('flag_services_company_arm_only', '1 if ev_op_cart_services rests only on payments from Kite, Legend Biotech or Autolus, with no service dollars on lymphoma CAR-T product records.', 'Never blank; always 0 for outside-universe NPIs.', 'project'),
        column('flag_services_legend_autolus_only', '1 if flag_services_company_arm_only is 1 and none of the payers is Kite, so the payments come from a company whose CAR-T product treats myeloma or leukemia.', 'Never blank; always 0 for outside-universe NPIs.', 'project'),
        column('flag_non_physician_taxonomy_class', '1 if taxonomy_class is nurse_practitioner, physician_assistant, registered_nurse_or_cns, pharmacist or student_or_trainee, or names a speech-language pathologist or behavior analyst.', 'Never blank; always 0 for universe NPIs.', 'project'),
        column('needs_review', '1 if the row should be checked by hand before it is used to relabel anyone; needs_review_reasons says why.', NEVER, 'project'),
        column('needs_review_reasons', 'Semicolon-separated reasons: probable_false_name_link, possible_recipient_mixup (an outside-universe NPI paid for CAR-T services that shares name and state with a core healthcare professional), low_name_based_name_link, mixed_name_link, outside_universe_name_link_not_hematology_class, registry_lookup_empty, bmt_centers_in_two_states.', 'needs_review is 0.', 'project'),
    ]
    held = 'Kept local because it counts Kite treatment-team evidence.'
    local = [
        column('ev_kite_team', '1 if a name on the team list of a Kite authorized treatment center record (2026-11-19) was matched to this NPI.', NEVER, 'kite'),
        column('kite_centers', 'Kite authorized treatment centers whose team list names the NPI.', 'ev_kite_team is 0.', 'kite'),
        column('kite_match_status', 'How the Kite team name was linked to this NPI, best outcome over its mentions.', 'ev_kite_team is 0.', 'kite'),
        column('n_evidence_types', f'Number of the seven evidence flags equal to 1, Kite included. {held}', NEVER, 'project', True),
        column('n_evidence_types_excl_services', f'n_evidence_types minus ev_op_cart_services. {held}', NEVER, 'project', True),
        column('n_evidence_types_excl_services_strict_links', f'Strict-link count with Kite included. {held}', NEVER, 'project', True),
        column('evidence_score', f'Sort aid of the search: evidence_score_public plus 3 for a Kite team listing and 1 for a director role at a BMT InfoNet center. {held}', NEVER, 'project', True),
        column('name_link_weak_sources', f'Weak name-based sources with Kite included. {held}', 'No weak link.', 'project', True),
        column('npi_link_confidence', f'Link confidence with Kite included. {held}', NEVER, 'project', True),
        column('flag_medicare_specialty_only', f'Medicare specialty-only flag with Kite counted as other evidence. {held}', NEVER, 'project', True),
        column('bmt_centers', 'Names of the BMT InfoNet centers listing the NPI, as the directory writes them.', 'ev_bmt_roster is 0.', 'bmtinfonet', True),
        column('bmt_roles', 'Roles under which the directory lists the NPI.', 'ev_bmt_roster is 0.', 'bmtinfonet', True),
        column('flag_bmt_no_positive_count_2021_2025', '1 if no center listing the NPI reports a positive yearly count for 2021-2025; derived from exact center counts, which stay local.', NEVER, 'bmtinfonet', True),
        column('fact_programs', 'Names of the FACT programs as the listing writes them.', 'ev_fact_director is 0.', 'fact', True),
    ]
    return public + local


def relabel_columns():
    """Columns of the review list; the descriptions do not lean on columns that only the evidence table has."""
    by_name = {c['column']: c for c in columns()}
    own = [
        column('npi', f'Ten-digit National Provider Identifier (NPI) of a healthcare professional whose division in hcp_divisions.csv ({UNIVERSE_SNAPSHOT}) is referring_proxy or hemonc_other and who has public evidence, other than industry service payments, of involvement with chimeric antigen receptor T-cell (CAR-T) therapy. Join hcp_divisions.csv or npi_cart_treater_evidence.csv on npi for the label and the full evidence.', NEVER, 'project'),
        column('evidence_flags', 'Evidence types held by the NPI, joined by +: bmt_roster (listed at a center of the Blood & Marrow Transplant Information Network, BMT InfoNet, CAR-T directory), fact_director (director of a program accredited by the Foundation for the Accreditation of Cellular Therapy, FACT), ctgov_investigator (ClinicalTrials.gov), op_cart_research_pi and op_cart_services (Open Payments research principal investigator and service payments), medicare_cell_therapy.', NEVER, 'project'),
        column('n_evidence_types_excl_services_public', 'Number of evidence types in evidence_flags other than op_cart_services (1 to 3 in this table). It counts listings, trial roles, research slots and Medicare markers, not treatment delivery.', NEVER, 'project'),
        column('n_evidence_types_excl_services_strict_links_public', 'The same count when evidence that rests on a weak name link is discarded (0 to 3 in this table). Weak links are defined under bmt_match_status in npi_cart_treater_evidence.csv.', 'Never blank; 0 means all such evidence rests on weak name links.', 'project'),
        column('evidence_score_public', 'Arbitrary additive sort aid copied from npi_cart_treater_evidence.csv, where the formula is given; used here only to order rows with the same number of evidence types.', NEVER, 'project'),
        column('bmt_center_ids', 'Semicolon-separated BMT InfoNet directory center identifiers (the number at the end of the center page address) at which the NPI is listed.', 'Not matched to a BMT InfoNet center listing.', 'bmtinfonet'),
        column('fact_program_ccns', f'Semicolon-separated Centers for Medicare & Medicaid Services (CMS) Certification Numbers (CCN) of the hospitals in cart_centers.csv ({UNIVERSE_SNAPSHOT}) whose FACT-accredited program lists the NPI as a director.', 'Not matched to a FACT program director.', 'fact'),
        column('ctgov_nct_ids', f'Semicolon-separated ClinicalTrials.gov identifiers of the CAR-T lymphoma trials on which the NPI was matched as an overall official or US site investigator ({CTGOV}).', 'No matched trial.', 'ctgov'),
        column('op_cart_research_pi_records_2019_2025', f'Number of CMS {RESEARCH} that name a commercial CAR-T product and list this NPI in a principal-investigator slot.', 'Never blank; 0 means none.', 'open_payments'),
        column('medicare_hct_specialty_years', 'Semicolon-separated service years 2019-2024 in which the NPI has provider type Hematopoietic Cell Transplantation and Cellular Therapy in the CMS Medicare Physician & Other Practitioners by Provider file.', 'Not listed under that provider type in any of those years.', 'cms'),
        column('npi_link_confidence_public', 'Confidence that the name-based evidence (bmt_roster, fact_director, ctgov_investigator) belongs to this NPI: high (no weak name link), mixed (weak and strong links), low_name_based (every link weak), npi_keyed_only (no name matching involved). high is not verified identity.', NEVER, 'project'),
        column('name_link_weak_sources_public', 'Semicolon-separated name-based sources (bmt, fact, ctgov_overall, ctgov_site) whose link to this NPI is weak.', 'No weak name link, or no name-based source at all.', 'project'),
        column('flag_bmt_pediatric_program_only', '1 if every BMT InfoNet center listing the NPI is described by the directory as a pediatric program; such a listing does not point to adult lymphoma practice.', 'Never blank; 0 also when the NPI is on no center listing.', 'bmtinfonet'),
        column('flag_medicare_specialty_only_public', '1 if the Medicare provider type in medicare_hct_specialty_years is the only evidence other than service payments. The provider type also covers transplant-only physicians.', NEVER, 'project'),
        column('flag_ctgov_context_only', '1 if every trial in ctgov_nct_ids is a context trial: a lymphoma trial that mentions CAR-T in its title or intervention description without giving a CAR-T product.', 'Never blank; 0 also when ctgov_nct_ids is blank.', 'project'),
        by_name['needs_review'], by_name['needs_review_reasons'],
    ]
    assert [c['column'] for c in own] == ['npi', 'evidence_flags'] + RELABEL_COPIED
    return own


def tally(rows, key):
    return dict(sorted(Counter(row[key] for row in rows).items()))


def headline(rows, candidates):
    """Numbers a README can quote, all counted from the published rows."""
    listed = {row['npi'] for row in candidates}
    reviewed = [row for row in rows if row['npi'] in listed]
    at_least = lambda name, k: sum(int(row[name]) >= k for row in rows)
    core = [row for row in rows if row['division'] == 'core']
    outside = [row for row in rows if row['in_universe'] == '0']
    flagged = lambda name, subset=rows: sum(row[name] == '1' for row in subset)
    return {
        'rows': len(rows), 'in_universe': len(rows) - len(outside), 'outside_universe': len(outside),
        'by_division': tally(rows, 'division'),
        'npis_by_flag': {flag: flagged(flag) for flag in PUBLIC_FLAGS},
        'npis_with_only_this_flag': {flag: sum(row[flag] == '1' and row['n_evidence_types_public'] == '1' for row in rows)
                                     for flag in PUBLIC_FLAGS},
        'evidence_types_at_least': {k: at_least('n_evidence_types_public', k) for k in range(1, 6)},
        'evidence_types_excl_services_at_least': {k: at_least('n_evidence_types_excl_services_public', k) for k in range(1, 5)},
        'strict_links_at_least': {k: at_least('n_evidence_types_excl_services_strict_links_public', k) for k in range(1, 4)},
        'evidence_excl_services_by_division': tally([r for r in rows if int(r['n_evidence_types_excl_services_public'])], 'division'),
        'services_only_by_division': tally([r for r in rows if r['n_evidence_types_excl_services_public'] == '0'], 'division'),
        'core_present': len(core), 'core_absent': 500 - len(core),
        'core_with_evidence_excl_services': sum(int(r['n_evidence_types_excl_services_public']) >= 1 for r in core),
        'core_with_two_or_more_excl_services': sum(int(r['n_evidence_types_excl_services_public']) >= 2 for r in core),
        'core_under_strict_links': sum(int(r['n_evidence_types_excl_services_strict_links_public']) >= 1 for r in core),
        'npi_link_confidence_public': tally(rows, 'npi_link_confidence_public'),
        'flags': {name: flagged(name) for name in ['flag_bmt_pediatric_program_only', 'flag_bmt_centers_in_two_states',
                                                   'flag_medicare_specialty_only_public', 'flag_ctgov_context_only',
                                                   'flag_services_company_arm_only', 'flag_services_legend_autolus_only',
                                                   'flag_non_physician_taxonomy_class', 'needs_review']},
        'core_pediatric_program_only': flagged('flag_bmt_pediatric_program_only', core),
        'research_pi_without_lymphoma_brand': sum(r['ev_op_cart_research_pi'] == '1' and r['op_lymphoma_cart_research_pi_records_2019_2025'] == '0' for r in rows),
        'outside_universe_by_class': dict(Counter(r['taxonomy_class'].split(':')[0] for r in outside).most_common()),
        'outside_universe_by_identity_source': tally(outside, 'identity_source'),
        'outside_universe_by_flag': {flag: flagged(flag, outside) for flag in PUBLIC_FLAGS},
        'needs_review_by_reason': dict(Counter(reason for r in rows for reason in split(r['needs_review_reasons'])).most_common()),
        'relabel_candidates': len(candidates), 'relabel_by_division': tally(reviewed, 'division'),
        'relabel_under_strict_links': sum(int(r['n_evidence_types_excl_services_strict_links_public']) >= 1 for r in candidates),
        'relabel_by_evidence_types_excl_services': tally(candidates, 'n_evidence_types_excl_services_public'),
        'relabel_pediatric_program_only': flagged('flag_bmt_pediatric_program_only', candidates),
        'relabel_medicare_specialty_only': flagged('flag_medicare_specialty_only_public', candidates),
        'relabel_needs_review': flagged('needs_review', candidates),
        'relabel_by_link_confidence': tally(candidates, 'npi_link_confidence_public'),
        'totals': totals(rows),
    }


def totals(rows):
    """Column sums of the detail columns; dollar sums are exact to the cent and given as text."""
    count = lambda name: sum(int(row[name]) for row in rows)
    items = lambda name: sum(len(split(row[name])) for row in rows)
    cents = lambda name: sum(round(amount(row[name]) * 100) for row in rows)
    dollars = lambda name: f'{cents(name) // 100}.{cents(name) % 100:02d}'
    return {
        'bmt_center_listings': items('bmt_center_ids'), 'fact_program_links': items('fact_program_ccns'),
        'ctgov_trial_links': items('ctgov_nct_ids'),
        'op_cart_research_pi_records_2019_2025': count('op_cart_research_pi_records_2019_2025'),
        'op_lymphoma_cart_research_pi_records_2019_2025': count('op_lymphoma_cart_research_pi_records_2019_2025'),
        'op_cart_research_pi_record_usd_2019_2025': dollars('op_cart_research_pi_record_usd_2019_2025'),
        'op_lymphoma_only_cart_services_usd_2019_2025': dollars('op_lymphoma_only_cart_services_usd_2019_2025'),
        'op_cart_only_services_usd_2019_2025_outside_universe': dollars('op_cart_only_services_usd_2019_2025_outside_universe'),
        'npis_with_kite_legend_autolus_service_years': sum(bool(row['op_kite_legend_autolus_service_years']) for row in rows),
        'kite_legend_autolus_company_years': sum(int(part.split(':')[1]) for row in rows
                                                 for part in split(row['op_kite_legend_autolus_service_years'])),
        'medicare_0540t_services': sum(int(part.split(':')[1]) for row in rows for part in split(row['medicare_0540t_year_services'])),
        'medicare_hct_specialty_npi_years': items('medicare_hct_specialty_years'),
    }


def fingerprint(rows, names):
    """SHA-256 over the header and every cell of a table, whatever the line endings or quoting of the file."""
    digest = hashlib.sha256()
    for line in [names] + [[row[name] for name in names] for row in rows]:
        digest.update('\x1f'.join(line).encode('utf-8') + b'\x1e')
    return digest.hexdigest()


def fingerprints(rows, candidates):
    return {TABLES[0]: fingerprint(rows, [c['column'] for c in columns() if not c['restricted']]),
            TABLES[1]: fingerprint(candidates, [c['column'] for c in relabel_columns()])}


def build():
    audit, trial_class, probe, inputs, paths = collect()
    members = universe()
    # Mentions of the published name-based sources at US centers or trials that no NPI could be tied to.
    unlinked = Counter(m['source'] for m in audit if not m['npi'] and m['match_status'] != 'skipped_non_us_center'
                       and m['source'] != 'kite')
    comparison = reconcile(assemble(members, link_mentions(audit, trial_class, set())[0], inputs), probe, members)
    assert not any(comparison['unexpected_differences'].values()), comparison['unexpected_differences']
    linked, dropped = link_mentions(audit, trial_class)
    everything = assemble(members, linked, inputs)
    rows, kite_only = split_kite_only(everything)
    removed = sorted({row['npi'] for row in probe} - {row['npi'] for row in everything})
    assert set(removed) == FALSE_LINKS, f'Rows removed beyond the known false links: {removed}'
    assert all(k in inputs['programs'] for row in everything for k in split(row['bmt_center_ids'])), 'Unknown BMT InfoNet center id'
    fields = columns()
    names = [c['column'] for c in fields]
    leaks = kite_dependent(rows, members, linked, inputs, [c['column'] for c in fields if not c['restricted']])
    assert not leaks, f'Tracked values depend on restricted Kite evidence for {len(leaks)} NPIs'
    summary = publish(TABLES[0], rows, fields, ['npi'])
    write_csv(RESTRICTED / KITE_ONLY_FILE, kite_only, names)
    candidates = relabel_candidates(rows)
    summaries = [summary, publish(TABLES[1], candidates, relabel_columns(), ['npi'])]
    held_candidates = sum(row['division'] in RELABEL_DIVISIONS and row['n_evidence_types_excl_services_public'] == '0'
                          and int(row['n_evidence_types_excl_services']) >= 1 for row in everything)
    write_module_metadata(MODULE, {
        'tables': summaries,
        'sources': SOURCES,
        'headline': headline(rows, candidates),
        'content_fingerprints': fingerprints(rows, candidates),
        'name_mentions_not_linked': {'note': 'Mentions in the published name-based sources (BMT InfoNet, FACT, ClinicalTrials.gov) at US centers or trials that could not be tied to an NPI.', 'mentions': sum(unlinked.values()), 'by_source': dict(sorted(unlinked.items()))},
        'rows_removed': [{'reason': 'Name link judged false: the registry record belongs to another person with the same name. The link was the only evidence, so the row is gone.', 'rows': len(removed), 'npis': removed, 'name_mentions_dropped': sum(dropped.values())},
                         {'reason': f'Only evidence is a Kite treatment-team listing, which the Kite site terms restrict; written to Data/interim/{SNAPSHOT}/restricted_terms/{KITE_ONLY_FILE} and to no tracked file.', 'rows': len(kite_only)}],
        'relabel_candidates_not_published': {'reason': 'Only evidence other than service payments is a Kite team listing; derivable locally from the restricted companion.', 'rows': held_candidates},
        'reconciliation_with_search_table': comparison,
        'inputs_note': 'Name-based evidence is loaded from the saved name-match record of the search (name_match_audit.csv): the scripts that matched names wrote their intermediate files to a session folder that was not kept, so the match itself cannot be re-run from raw pages. Open Payments evidence is rebuilt from the scan extracts of the 2026-06-30 publication, not from the bulk archives. The ad hoc table npi_cart_treater_evidence.csv is read only for reconciliation.',
        'nppes_name_queries': {'cached': inputs['queries'], 'at_200_record_cap': inputs['queries_at_cap']},
        'limitations': [text.format(unlinked=sum(unlinked.values())) for text in LIMITATIONS],
        'defects_fixed': DEFECTS_FIXED, 'defects_open': DEFECTS_OPEN,
    }, paths)
    failing = sorted(label for label, count in check(OUT.parent).items() if count)
    print('TREATER ROSTER BUILT', len(rows), 'NPIS', len(candidates), 'RELABEL CANDIDATES', len(kite_only), 'KITE-ONLY ROWS KEPT LOCAL',
          len(removed), 'FALSE LINKS REMOVED', 'CHECKS FAILING', failing or 0, flush=True)


SOURCES = [
    {'name': 'BMT InfoNet CAR T-cell Center Directory (center pages: listed directors and physicians, program type)', 'publisher': 'Blood & Marrow Transplant Information Network', 'url': 'https://bmtinfonet.org/car-t-cell-therapy-center-directory-search', 'release_or_version': 'No update date on the site', 'retrieved': '2026-11-19', 'how_obtained': 'Center pages read by script during the search; names matched to NPIs by name and state', 'terms_class': 'b: no terms page, all rights reserved, silent on reuse; flags and directory identifiers published, names and role titles kept local'},
    {'name': 'FACT accredited immune effector cell programs (director field)', 'publisher': 'Foundation for the Accreditation of Cellular Therapy', 'url': 'https://accredited.factglobal.org/', 'release_or_version': f'Data/processed/{UNIVERSE_SNAPSHOT}/fact_iec_programs.csv, listing observed 2026-10-25', 'retrieved': '2026-10-25', 'how_obtained': 'Repository table; director names matched to NPIs by name and state', 'terms_class': 'b: no terms page, all rights reserved, silent on reuse; flag and hospital CCN published'},
    {'name': 'ClinicalTrials.gov study records (402 trials: condition lymphoma, CAR-T intervention terms, United States)', 'publisher': 'U.S. National Library of Medicine', 'url': 'https://clinicaltrials.gov/api/v2/studies', 'release_or_version': 'Registry as processed by ClinicalTrials.gov on 2026-11-19', 'retrieved': '2026-11-19', 'how_obtained': 'Public API; modified by this project: 337 trials kept by a CAR-T and lymphoma text filter, investigator names matched to NPIs', 'terms_class': 'a: open with attribution, processing date and statement of modifications'},
    {'name': 'CMS Open Payments Research and General detail files, program years 2019-2025', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': 'https://openpaymentsdata.cms.gov', 'release_or_version': '2026-06-30 publication', 'retrieved': '2026-11-11', 'how_obtained': 'Bulk archives already collected by the repository, scanned during the search; loaded here from the scan extracts', 'terms_class': 'a: US Government work'},
    {'name': 'Medicare Physician & Other Practitioners by Provider and Service (code 0540T rows) and by Provider (cell-therapy provider type)', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': 'https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners', 'release_or_version': 'Service years 2013-2024 and 2019-2024', 'retrieved': '2026-11-19', 'how_obtained': 'data.cms.gov API extracts saved by the search', 'terms_class': 'a: US Government work; procedure descriptor text not copied'},
    {'name': 'NPPES NPI Registry API v2.1 (name queries and lookups by number)', 'publisher': 'Centers for Medicare & Medicaid Services', 'url': 'https://npiregistry.cms.hhs.gov/api/?version=2.1', 'release_or_version': 'Live registry on the retrieval date', 'retrieved': '2026-11-19', 'how_obtained': 'Responses cached by the search; no new request made by this build', 'terms_class': 'a: US Government work'},
    {'name': 'Kite authorized treatment center records (team lists)', 'publisher': 'Kite Pharma', 'url': 'https://www.kitepharma.com/-/media/atc-qac/getgaimatcdetails.json', 'release_or_version': 'Records as of 2026-11-19', 'retrieved': '2026-11-19', 'how_obtained': 'Saved by the search; used only for local restricted files', 'terms_class': 'c: site terms limit reuse to own non-commercial use; nothing derived from it is tracked'},
]
LIMITATIONS = [
    'The table is a screen of who is named at a CAR-T program, in a CAR-T trial, payment or specialty record. It does not show that a person infuses CAR-T and it is not a measure of volume.',
    'Absence from the table is not evidence of non-involvement: some centers list few or no physicians, pivotal-trial investigators are not in the registry extract, and {unlinked} name mentions in the published sources (BMT InfoNet, FACT, ClinicalTrials.gov) could not be tied to an NPI.',
    'BMT InfoNet and FACT listings and the Medicare markers cover all indications, adult and pediatric; only the Open Payments lymphoma-brand fields and the trial filter are lymphoma-specific.',
    'Name-based flags rest on name and state matching. high in npi_link_confidence_public is not verified identity; rows with needs_review = 1 should be checked by hand before any label change.',
    'evidence_score_public is an arbitrary additive index that mixes program listing with industry payments; use it to sort, not to measure.',
    'BMT InfoNet rosters are self-reported, undated and stale in places.',
    'Evidence from Kite treatment-team lists is not published; counts ending in _public leave it out, and NPIs whose only evidence was a Kite listing are absent.',
    'Research and service flags skew toward Kite and Johnson & Johnson relationships because those products dominate the CAR-T records in Open Payments.',
    'Outside-universe rows are not HCPs of the cohort; many are pediatric hematology-oncology physicians or non-physicians. Their names are given only where an NPPES registry record is attached (identity_source).',
    'ev_op_cart_services uses a narrower rule inside the universe (lymphoma brands, or a payer named Kite, Legend Biotech or Autolus) than outside it (any CAR-T brand), and the payer arm counts a payment whatever product it names; the two flag_services_ columns mark rows that rest on that arm alone.',
    'taxonomy_class reads hematology-oncology from any registry taxonomy, not only the primary one, and other: classes can include non-physician types that flag_non_physician_taxonomy_class does not name.',
    'division_relabel_candidates is a review queue, not a relabel decision.',
]
DEFECTS_FIXED = [
    'Five allocation columns (proxy_2024_equal_share, proxy_2024_evidence_weighted, proxy_basis, proxy_thin_roster_flag, proxy_for_rank) dropped: a center total divided across listed names is not a per-physician volume.',
    'n_delivery_evidence_types and n_delivery_evidence_types_strict_links renamed to n_evidence_types_excl_services and n_evidence_types_excl_services_strict_links; the counted flags do not show treatment delivery.',
    'Match status and link confidence now use the best outcome per source over all mentions instead of the last mention processed; rows with no name-based source get npi_keyed_only instead of high.',
    'Kite, Legend Biotech and Autolus service counts recomputed as distinct program years per company (1 to 7) from the per-year NPI lists; the search counted year-by-product-naming groups.',
    'Two rows removed whose only evidence was a name link judged false (NPIs 1619344165 and 1760815682).',
    'Over-inclusion flags added: pediatric-program-only roster, Medicare specialty as the only non-service evidence, context-only trials, service flag resting only on the company arm (and on Legend Biotech or Autolus alone), non-physician taxonomy class.',
    'needs_review and needs_review_reasons added for the hand-check NPIs, low or mixed link confidence, outside-universe name links to a non-hematology class, empty registry lookups and rosters in two states.',
    'Names, city, state and specialty of universe NPIs are no longer repeated; outside-universe names and states come from saved NPPES records only, in registry casing, and are blank where no record is attached.',
    'Service-dollar columns are blank where the search did not compute them (lymphoma-only for outside-universe NPIs, CAR-T-only for universe NPIs) instead of 0.',
    'taxonomy_class no longer files a speech-language pathologist under pathology, no longer truncates other: labels at 40 characters, and shows a taxonomy without a registry description by its code instead of an empty label.',
    'taxonomy_class read from Open Payments now uses the same labels as the class read from the registry: the search wrote other:Internal Medicine, other:Pediatrics or other:Diagnostic Radiology where registry rows say internal_medicine_other, pediatrics_other or other:Radiology, Diagnostic Radiology.',
    'ctgov_match_status split into one column per role, and ctgov_product_trials_any_role added, so that link confidence and the sort aid are recomputable from the table.',
    'The relabel list now carries link confidence, weak sources, over-inclusion flags and needs_review in place of a single-source match status; its two renamed columns match the main table.',
    'Three column names now state the year span (op_lymphoma_cart_research_pi_records_2019_2025, op_cart_research_pi_record_usd_2019_2025, op_cart_only_services_usd_2019_2025_outside_universe); medicare_0540T became medicare_0540t_year_services.',
]
DEFECTS_OPEN = [
    'Five NPIs are probable false name links that saved inputs cannot settle (1437785383, 1184795098, 1245279231, 1386844934, 1962707554); they stay in the table with needs_review = 1.',
    'Outside-universe NPI 1841292117 carries CAR-T-only service payments and shares first name, last name and state with a core HCP; possible recipient mix-up in Open Payments, not confirmed; needs_review = 1.',
    'State-scoped registry matches count as strong even when the first name matched at nickname or prefix level, and whatever the specialty of the matched record; outside-universe name links to a non-hematology class are marked needs_review but not removed.',
    '17 of 1,442 cached NPPES name queries returned the 200-record maximum, so some candidate lists were cut off and the right record may have been missed.',
    'Two outside-universe NPIs (1760773683, 1912969726) have no retrievable registry identity; their only evidence is a research principal-investigator slot.',
    'Outside-universe NPIs whose published evidence is keyed by NPI in Open Payments have no registry lookup by number saved, so their name, state and taxonomy are blank; a lookup by number would fill them.',
    'The name matching cannot be re-run: the search kept only its outcome (name_match_audit.csv), not the parsed mentions.',
    'Trial evidence is uneven: pivotal trials list no investigators in the registry, 61 NPIs carry NCT06500273 (an investigational trial run in community practices), and the product versus context classification has errors in both directions.',
    'A study can be counted twice in op_cart_research_studies when only some of its records carry a ClinicalTrials.gov identifier.',
    'A flag for NPIs listed only at BMT InfoNet centers that report no positive yearly count for 2021-2025 exists only in the local companion file, because it derives from exact center counts; the tracked table cannot separate those listings.',
    'ev_op_cart_services follows a narrower rule inside the universe than outside it, and its payer arm counts a payment from Kite, Legend Biotech or Autolus whatever product it names; rows resting on that arm alone are flagged, not removed.',
    'Research and service evidence is loaded from the scan extracts of the search, not re-read from the Open Payments bulk archives.',
    'The best match per source hides a weak link to one center or trial when the same source also has a strong link to another; the bmt_centers_in_two_states reason marks the four known cases.',
]


def table(processed, name):
    path = processed / SNAPSHOT / f'{name}.csv'
    return read_csv(path) if path.exists() else None


# Tracked table of the center panel module, and its column of BMT InfoNet directory identifiers.
SITE_PANEL = ('cart_site_volume_panel', 'bmtinfonet_directory_ids')


def directory_ids(processed):
    """Directory identifiers listed in the tracked site panel, or None where that table is not present."""
    panel = table(processed, SITE_PANEL[0])
    if not panel or SITE_PANEL[1] not in panel[0]:
        return None
    return {part for row in panel for part in split(row[SITE_PANEL[1]])}


REGISTRY_SOURCES = {'nppes_name_query', 'nppes_number_lookup'}
OUTSIDE_SOURCES = REGISTRY_SOURCES | {'nppes_number_lookup_empty', 'no_registry_lookup'}
NOT_TRACKED = re.compile('kite_team|kite_centers|kite_match|^bmt_centers$|bmt_roles|fact_programs|proxy_|delivery|'
                         '^last_name|^first_name|^city|^state')


def listing(item):
    return f'({item}(;{item})*)?'


# Full-match patterns of the cells that later checks read as numbers, lists or labels.
FORMATS = {
    'n_evidence_types_public': '[0-6]', 'n_evidence_types_excl_services_public': '[0-5]',
    'n_evidence_types_excl_services_strict_links_public': '[0-5]', 'evidence_score_public': r'\d+',
    'bmt_center_ids': listing(r'\d+'), 'fact_program_ccns': listing(r'\d{6}'), 'ctgov_nct_ids': listing(r'NCT\d{8}'),
    'ctgov_product_trials_overall_official': r'\d+', 'ctgov_product_trials_site_investigator': r'\d+',
    'ctgov_product_trials_any_role': r'\d+', 'ctgov_context_trials_any_role': r'\d+',
    'op_cart_research_pi_records_2019_2025': r'\d+', 'op_lymphoma_cart_research_pi_records_2019_2025': r'\d+',
    'op_cart_research_studies': r'\d+', 'op_cart_research_brands': listing('[A-Z]+'),
    'op_cart_research_pi_record_usd_2019_2025': r'\d+\.\d\d', 'op_lymphoma_only_cart_services_usd_2019_2025': r'(\d+\.\d\d)?',
    'op_cart_only_services_usd_2019_2025_outside_universe': r'(\d+\.\d\d)?',
    'op_kite_legend_autolus_service_years': listing('(kite|legend_biotech|autolus):[1-7]'),
    'medicare_0540t_year_services': listing(r'20(1[3-9]|2[0-4]):[1-9]\d*'),
    'medicare_hct_specialty_years': listing('20(19|2[0-4])'),
    'name_link_weak_sources_public': listing('(bmt|fact|ctgov_overall|ctgov_site)'),
    'npi_link_confidence_public': 'high|mixed|low_name_based|npi_keyed_only',
    'nppes_state': '([A-Z][A-Z ]*[A-Z])?',
}


def format_errors(row):
    """Cells that do not have the stated form, a blank where a value is required included."""
    return sum(not re.fullmatch(pattern, row[name]) for name, pattern in FORMATS.items())


def class_errors(row):
    """taxonomy_class against the registry taxonomy published beside it, for outside-universe rows."""
    label, primary, source = row['taxonomy_class'], row['nppes_primary_taxonomy'], row['identity_source']
    if source not in REGISTRY_SOURCES:
        return int(bool(primary) or (source == 'nppes_number_lookup_empty' and label != 'unknown')
                   or label == 'other:' or label.startswith('other:code '))
    if label in HEMATOLOGY_CLASSES:
        return int(not primary)
    return int(label != primary_class(primary) if primary else not re.fullmatch(r'other:code \w+', label))


def identity_errors(row, members):
    """Join to the universe table, and the rule that only outside-universe rows carry registry identity."""
    member = members.get(row['npi'])
    named = [row[c] for c in ['nppes_last_name', 'nppes_first_name', 'nppes_state']]
    if member:
        ok = (row['in_universe'] == '1' and row['division'] == member['division'] and row['is_core'] == member['is_core']
              and row['identity_source'] == 'universe_table' and row['taxonomy_class'] == 'in_universe'
              and not any(named) and not row['nppes_primary_taxonomy'])
    else:
        ok = (row['in_universe'] == '0' and row['division'] == 'outside_universe' and row['is_core'] == '0'
              and row['identity_source'] in OUTSIDE_SOURCES and row['taxonomy_class'] not in ('', 'in_universe')
              and all(named) == (row['identity_source'] in REGISTRY_SOURCES) == any(named) and not class_errors(row)
              and (row['identity_source'] != 'nppes_name_query' or any(row[STATUS_COLUMN[s]] for s in PUBLIC_SOURCES)))
    return int(not ok)


def detail_errors(row):
    """Detail columns that contradict each other or break their stated format or blank rule."""
    overall, site = int(row['ctgov_product_trials_overall_official']), int(row['ctgov_product_trials_site_investigator'])
    either, context = int(row['ctgov_product_trials_any_role']), int(row['ctgov_context_trials_any_role'])
    records, member = int(row['op_cart_research_pi_records_2019_2025']), row['in_universe'] == '1'
    statuses = [row[STATUS_COLUMN[source]] for source in PUBLIC_SOURCES]
    return sum([
        any(status and status not in STATUS_ORDER for status in statuses),
        bool(statuses[0]) != bool(row['bmt_center_ids']), bool(statuses[1]) != bool(row['fact_program_ccns']),
        bool(statuses[2] or statuses[3]) != bool(row['ctgov_nct_ids']),
        bool(statuses[2]) and overall + context == 0, bool(statuses[3]) and site + context == 0,
        len(set(split(row['bmt_center_ids']))) != len(split(row['bmt_center_ids'])),
        not max(overall, site) <= either <= overall + site, len(split(row['ctgov_nct_ids'])) != either + context,
        int(row['op_lymphoma_cart_research_pi_records_2019_2025']) > records,
        (records > 0) != bool(row['op_cart_research_brands']), (records > 0) != (int(row['op_cart_research_studies']) > 0),
        float(row['op_cart_research_pi_record_usd_2019_2025']) < 0 or (records == 0 and float(row['op_cart_research_pi_record_usd_2019_2025']) != 0),
        bool(row['op_lymphoma_only_cart_services_usd_2019_2025']) != member,
        bool(row['op_cart_only_services_usd_2019_2025_outside_universe']) == member,
        not member and bool(row['op_kite_legend_autolus_service_years']),
        len({part.split(':')[0] for part in split(row['op_kite_legend_autolus_service_years'])})
        != len(split(row['op_kite_legend_autolus_service_years'])),
        split(row['medicare_hct_specialty_years']) != sorted(set(split(row['medicare_hct_specialty_years']))),
        row['flag_bmt_pediatric_program_only'] == '1' and not row['bmt_center_ids'],
        row['flag_bmt_centers_in_two_states'] == '1' and len(split(row['bmt_center_ids'])) < 2,
    ])


def check(processed, expected=EXPECTED):
    """Error counts on the tracked files only; zero means pass. `expected` holds the snapshot totals."""
    rows, candidates = table(processed, TABLES[0]), table(processed, TABLES[1])
    if not rows or candidates is None:
        return {f'{MODULE}_table_missing_or_empty': int(not rows) + int(candidates is None)}
    members = {row['npi']: row for row in read_csv(processed / UNIVERSE_SNAPSHOT / 'hcp_divisions.csv')}
    ccns = {row['ccn'] for row in read_csv(processed / UNIVERSE_SNAPSHOT / 'cart_centers.csv')}
    public = [c['column'] for c in columns() if not c['restricted']]
    relabel = [c['column'] for c in relabel_columns()]
    headers = [list(rows[0]), list(candidates[0]) if candidates else relabel]
    out = {'columns_differ_from_dictionary': int(headers[0] != public) + int(headers[1] != relabel),
           'restricted_or_dropped_column_tracked': sum(bool(NOT_TRACKED.search(c)) for header in headers for c in header)}
    if out['columns_differ_from_dictionary']:
        return {f'{MODULE}_{label}': value for label, value in out.items()}
    # The checks below read cells as numbers, so a malformed cell is counted here and stops the run.
    out['value_format_invalid'] = sum(format_errors(row) for row in rows)
    if out['value_format_invalid']:
        return {f'{MODULE}_{label}': value for label, value in out.items()}
    binary = PUBLIC_FLAGS + [c for c in public if c.startswith('flag_')] + ['in_universe', 'is_core', 'needs_review']
    by_npi = {row['npi']: row for row in rows}
    known = directory_ids(processed)
    rule = relabel_candidates(rows)
    by_rule = {row['npi']: row for row in rule}
    out.update({
        'npi_not_unique_or_invalid': duplicates(rows, ['npi']) + sum(not luhn_ok(row['npi']) for row in rows),
        'flag_not_0_or_1': sum(row[c] not in ('0', '1') for row in rows for c in binary),
        'universe_join_or_identity_rule_broken': sum(identity_errors(row, members) for row in rows),
        'detail_columns_inconsistent': sum(detail_errors(row) for row in rows),
        'derived_value_differs_from_detail_columns': sum(row[c] != value for row in rows for c, value in public_values(row).items()),
        'row_without_published_evidence': sum(row['n_evidence_types_public'] not in list('123456') for row in rows),
        'needs_review_inconsistent': sum(row['needs_review_reasons'] != review_reasons(row)
                                         or row['needs_review'] != str(int(bool(row['needs_review_reasons']))) for row in rows),
        'fact_ccn_not_in_cart_centers': sum(ccn not in ccns for row in rows for ccn in split(row['fact_program_ccns'])),
        'bmt_center_id_not_in_site_panel': 0 if known is None else sum(k not in known for row in rows for k in split(row['bmt_center_ids'])),
        'known_false_link_present': sum(npi in by_npi for npi in FALSE_LINKS),
        'relabel_key_not_unique': duplicates(candidates, ['npi']),
        'relabel_rows_differ_from_rule': len({row['npi'] for row in candidates} ^ set(by_rule)),
        'relabel_values_differ_from_evidence_table': sum(row != by_rule[row['npi']] for row in candidates if row['npi'] in by_rule),
        'relabel_not_sorted': int([row['npi'] for row in candidates] != [row['npi'] for row in rule if row['npi'] in {r['npi'] for r in candidates}]),
        'relabel_npi_not_in_reviewed_division': sum(members.get(row['npi'], {}).get('division') not in RELABEL_DIVISIONS for row in candidates),
    })
    if expected:
        got = headline(rows, candidates)
        out['headline_totals_differ'] = sum(got[label] != total for label, total in expected.items() if label != 'fingerprints')
        found = fingerprints(rows, candidates)
        out['content_differs_from_snapshot_fingerprint'] = sum(found[name] != value
                                                               for name, value in expected.get('fingerprints', {}).items())
    return {f'{MODULE}_{label}': int(value) for label, value in out.items()}


if __name__ == '__main__':
    build()
