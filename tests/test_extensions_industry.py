"""Manufacturer rule, record shaping, statement parsing, de-duplication and validation of the industry-ties tables."""
from kolproject.common import read_csv, write_csv
from kolproject.extensions import industry as ind
from kolproject.extensions.base import new_rows

P = ind.PERIOD
CORE, OTHER, OUTSIDE = '1000000001', '1000000002', '1999999999'
SCAN = {'retrieved_at_utc': '2026-11-12T00:00:00Z',
        'members': {'ownership': {'member': 'OP_DTL_OWNRSHP_TEST.csv'}, 'research': {'member': 'OP_DTL_RSRCH_TEST.csv'}}}
BATCH = {'source_id': 'industry:test', 'retrieved_at_utc': '2026-11-20T00:00:00Z'}


def ownership(record_id, npi, payer, value='100.00', invested='0.00', holder='Physician Covered Recipient', year=2022):
    return {'record_id': record_id, 'physician_npi': npi, 'program_year': str(year),
            'applicable_manufacturer_or_applicable_gpo_making_payment_name': payer,
            'applicable_manufacturer_or_applicable_gpo_making_payment_id': '9' + record_id,
            'submitting_applicable_manufacturer_or_applicable_gpo_name': payer, 'value_of_interest': value,
            'total_amount_invested_usdollars': invested, 'interest_held_by_physician_or_an_immediate_family_member': holder,
            'terms_of_interest': 'Invented terms text', 'dispute_status_for_publication': 'No', 'change_type': 'NEW',
            'payment_publication_date': '06/30/2026'}


def research(record_id, investigators, recipient, amount, products=(), study='', context='', last_names=()):
    row = {'record_id': record_id, 'program_year': '2023', 'date_of_payment': '05/01/2023',
           'total_amount_of_payment_usdollars': amount, 'covered_recipient_npi': recipient,
           'covered_recipient_first_name': '', 'covered_recipient_last_name': '',
           'covered_recipient_type': 'Non-covered Recipient Entity', 'teaching_hospital_ccn': '',
           'applicable_manufacturer_or_applicable_gpo_making_payment_name': 'Kite Pharma, Inc.',
           'applicable_manufacturer_or_applicable_gpo_making_payment_id': '1', 'name_of_study': study,
           'context_of_research': context, 'clinicaltrials_gov_identifier': 'NCT00000001',
           'preclinical_research_indicator': 'No', 'form_of_payment_or_transfer_of_value': 'Cash or cash equivalent',
           'dispute_status_for_publication': 'No', 'change_type': 'NEW', 'payment_publication_date': '06/30/2026'}
    for k in range(1, 6):
        row[f'principal_investigator_{k}_npi'] = investigators[k - 1] if k <= len(investigators) else ''
        row[f'principal_investigator_{k}_first_name'] = ''
        row[f'principal_investigator_{k}_last_name'] = last_names[k - 1] if k <= len(last_names) else ('X' if k <= len(investigators) else '')
        row[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] = products[k - 1] if k <= len(products) else ''
    return row


AUTHORS = [{'last': 'Quill', 'fore': 'Ada B', 'initials': 'AB', 'collective': '', 'affiliations': ['Example University']},
           {'last': 'Marsh', 'fore': 'Cyril', 'initials': 'C', 'collective': '', 'affiliations': ['Kite, a Gilead Company']},
           {'last': 'Tern', 'fore': 'Dora', 'initials': 'D', 'collective': '', 'affiliations': []}]


def test_manufacturer_rule():
    assert ind.manufacturer_group('Kite Pharma, Inc.') == 'Kite/Gilead'
    assert ind.manufacturer_group('E.R. Squibb & Sons, L.L.C.') == 'Bristol Myers Squibb/Celgene/Juno'
    assert ind.manufacturer_group('Janssen Research & Development, LLC') == 'Janssen/Johnson & Johnson'
    assert ind.manufacturer_group('Legend Biotech USA Inc.') == 'Legend Biotech'
    assert ind.manufacturer_group('2seventy bio, Inc.') == 'bluebird bio/2seventy bio'
    assert ind.manufacturer_group('Kitestring Holdings') == '' and ind.manufacturer_group('Legendary Devices') == ''
    assert ind.manufacturers_named('Fees from Novartis and Autolus.') == ['Novartis', 'Autolus']


def test_ownership_row_and_features():
    rows = [ind.ownership_row(ownership('11', OTHER, 'Example GPO LLC', '250.10', '50.00'), SCAN),
            ind.ownership_row(ownership('12', OTHER, 'Kite Pharma, Inc.', '', holder='Immediate family member'), SCAN)]
    assert rows[0]['cart_manufacturer'] == '0' and rows[1]['cart_manufacturer_group'] == 'Kite/Gilead'
    assert rows[1]['value_of_interest_usd'] == '' and rows[0]['amount_invested_usd'] == '50.00'
    assert rows[0]['terms_reported'] == '1' and rows[0]['payment_publication_date'] == '2026-06-30'
    feature = ind.ownership_features(OTHER, rows)
    assert feature[f'ownership_rows_{P}'] == '2' and feature[f'ownership_family_held_rows_{P}'] == '1'
    assert feature[f'ownership_value_of_interest_usd_{P}'] == '250.10'
    assert feature[f'cart_manufacturer_ownership_rows_{P}'] == '1' and feature[f'cart_manufacturer_ownership_value_usd_{P}'] == '0.00'
    empty = ind.ownership_features(CORE, [])
    assert empty[f'ownership_rows_{P}'] == '0' and empty[f'ownership_value_of_interest_usd_{P}'] == ''
    assert empty['ownership_data_status'] == 'no_published_records'


def test_research_rows_roles_and_screen():
    record = research('21', [CORE, OTHER], OTHER, '1000.00', ['Yescarta'], 'Phase 2 study of axi-cel')
    rows = ind.research_rows(record, {OTHER}, SCAN)
    assert [(r['npi'], r['role'], r['pi_slots']) for r in rows] == [(OTHER, 'investigator', '2')]
    assert rows[0]['match_basis'] == 'product_field' and rows[0]['cart_products'] == 'YESCARTA'
    text = ind.research_rows(research('22', [], OTHER, '5.00', ['Other drug'], 'CAR T registry'), {OTHER}, SCAN)
    assert text[0]['role'] == 'recipient_only' and text[0]['match_basis'] == 'study_or_context_text'
    named = ind.research_rows(research('23', [OTHER], '', '5.00', ['Kymriah'], 'Quillfeather CAR-T pilot',
                                       last_names=['Quillfeather']), {OTHER}, SCAN)
    assert named[0]['study_name'] == '' and named[0]['study_name_screen'] == 'failed'
    assert ind.screen_study('Trial at 12 Example Street', []) == 'failed'
    assert ind.screen_study('Dr. Example trial', []) == 'failed'
    assert ind.screen_study('A Phase 1 Study of KTE-X19', ['Ada', 'Quill']) == 'passed'
    assert ind.screen_study('  ', []) == 'blank'


def test_statement_parsing():
    statement = ('A.B.Q. and C.M. received consulting fees from Kite and Novartis; C.M. is employed by Example Co. '
                 'D.T. reports honoraria from Pfizer. The remaining authors declare no competing interests.')
    assert ind.author_tie(statement, AUTHORS, 0) == ('author_named', ['Kite/Gilead', 'Novartis'])
    assert ind.author_tie(statement, AUTHORS, 2) == ('author_named', [])
    run_on = 'ABQ: advisory board for Example Co CM: consultancy for Janssen'
    assert ind.author_tie(run_on, AUTHORS, 0) == ('author_named', [])
    assert ind.author_tie(run_on, AUTHORS, 1) == ('author_named', ['Janssen/Johnson & Johnson'])
    assert ind.author_tie('C.M. reports fees from Celgene.', AUTHORS, 0) == ('author_not_named', [])
    assert ind.author_tie('Some authors consult for Kite.', AUTHORS, 0) == ('not_structured', [])
    assert ind.author_tie('The authors declare no conflicts.', AUTHORS, 0) == ('no_cart_manufacturer', [])
    assert ind.author_tie('', AUTHORS, 0) == ('no_statement', [])
    assert ind.locate(AUTHORS, '2', 'Marsh') == (1, 'position_and_surname')
    assert ind.locate(AUTHORS, '9', 'Tern') == (2, 'unique_surname')


def test_paper_and_author_rows():
    article = {'coi': 'C.M. is an employee of Kite.', 'authors': AUTHORS, 'revised': '2025-01-02'}
    paper = ind.paper_row('100', article, BATCH)
    assert paper['coi_cart_manufacturers'] == 'Kite/Gilead' and paper['industry_coauthorship'] == '1'
    assert paper['industry_affiliation_authors'] == '1' and paper['coi_statement'] == article['coi']
    link = {'npi': CORE, 'pmid': '100', 'author_position': '1', 'last_name_as_indexed': 'Quill'}
    row = ind.author_row(link, article, BATCH)
    assert (row['statement_status'], row['named_in_statement'], row['named_with_cart_manufacturer']) == ('author_not_named', '0', '0')
    missing = ind.paper_row('101', None, BATCH)
    assert missing['pubmed_record_status'] == 'not_returned' and missing['coi_statement_present'] == ''
    assert ind.author_row({**link, 'pmid': '101'}, None, BATCH)['named_with_cart_manufacturer'] == ''


def test_author_name_forms():
    authors = [{'last': 'de Nully Brown', 'fore': 'Ada', 'initials': 'A', 'collective': '', 'affiliations': []},
               {'last': 'Quill', 'fore': 'Bo T', 'initials': 'BT', 'collective': '', 'affiliations': []},
               {'last': 'Myers', 'fore': 'Cy', 'initials': 'C', 'collective': '', 'affiliations': []}]
    statement = 'Quill: honoraria from Kite; Brown: advisory board for Legend. Cy Myers reports none.'
    assert ind.author_tie(statement, authors, 1) == ('author_named', ['Kite/Gilead'])
    assert ind.author_tie(statement, authors, 0) == ('author_named', ['Legend Biotech'])
    assert ind.author_tie('B. Quill consults for Jansssen.', authors, 1) == ('author_named', ['Janssen/Johnson & Johnson'])
    assert ind.author_tie('A.B. consults for Bristol Myers Squibb.', authors, 2) == ('not_structured', [])
    assert ind.manufacturers_named('a legend of the field') == []


def test_universe_and_core_new_summaries():
    links = [{'npi': OTHER, 'pmid': '1', 'link_table': 'universe_authorships', 'statement_status': 'author_named',
              'named_with_cart_manufacturer': '1', 'cart_manufacturers_named_for_author': 'Autolus',
              'own_affiliation_cart_manufacturer': '0', 'retrieved_at_utc': 'T'},
             {'npi': CORE, 'pmid': '2', 'link_table': 'new_authorships', 'statement_status': 'no_statement',
              'named_with_cart_manufacturer': '', 'cart_manufacturers_named_for_author': '',
              'own_affiliation_cart_manufacturer': '0', 'retrieved_at_utc': 'T'}]
    features = [ind.ownership_features(OTHER, []), ind.ownership_features(CORE, [])]
    research = [{'npi': OTHER, 'cart_product_mentioned': '1', 'pi_slots': '1', 'record_pi_count': '1', 'nct_id': 'NCT1',
                 'manufacturer': 'Autolus Inc'}]
    rows = ind.universe_rows(features, research, links, {CORE})
    assert [r['npi'] for r in rows] == [OTHER] and rows[0]['groups_in_both_research_and_disclosure'] == '1'
    assert rows[0][f'cart_product_research_sole_pi_rows_{P}'] == '1'
    new = ind.core_new_rows(links, {CORE})
    assert [(r['npi'], r['new_paper_links'], r['new_paper_undetermined']) for r in new] == [(CORE, '1', '1')]


def test_research_by_year():
    core_row = {'npi': CORE, 'record_id': '1', 'program_year': '2022', 'cart_product_mentioned': '0', 'cart_text_match': '1',
                'pi_slots': '', 'record_pi_count': '2', 'lymphoma_cart_product_mentioned': '0', 'cart_products': 'KYMRIAH',
                'products': 'Other drug', 'amount_usd': '10.5', 'study_name': 'Invented CAR-T registry', 'nct_id': '',
                'manufacturer': 'Novartis Pharmaceuticals Corporation'}
    other = ind.research_rows(research('2', [OTHER], '', '200.00', ['Breyanzi'], 'Invented trial'), {OTHER}, SCAN)
    rows = ind.research_by_year([core_row], other, {CORE})
    assert len(rows) == 14 and {r['source_table'] for r in rows if r['npi'] == CORE} == {ind.CORE_RESEARCH}
    c = next(r for r in rows if r['npi'] == CORE and r['program_year'] == '2022')
    assert (c['text_only_rows'], c['product_field_rows'], c['text_only_record_usd'], c['product_field_brands']) == ('1', '0', '10.50', '')
    o = next(r for r in rows if r['npi'] == OTHER and r['program_year'] == '2023')
    assert (o['product_field_sole_pi_rows'], o['lymphoma_product_rows'], o['manufacturer_groups']) == ('1', '1', 'Kite/Gilead')
    division = ind.research_division_rows(rows, {CORE: 'core', OTHER: 'affiliate_A'})
    assert next(r for r in division if r['division_group'] == 'affiliates' and r['program_year'] == '2023')['npis_with_product_field_record'] == '1'


def test_new_rows_drops_published_and_internal_duplicates():
    rows = [{'npi': OTHER, 'record_id': '1'}, {'npi': OTHER, 'record_id': '1'}, {'npi': OTHER, 'record_id': '2'}]
    kept, dropped = new_rows(rows, ['npi', 'record_id'], {(OTHER, '2')})
    assert kept == [rows[0]] and dropped == 2


def general_record(npi, year, amount, nature, any_slot='1', cart_only='0', pure_play='0', direct='1'):
    return {'npi': npi, 'record_id': npi[-1] + year + amount, 'program_year': year, 'amount_usd': amount,
            'manufacturer_id': '7', 'nature_class': nature, 'any_slot': any_slot, 'cart_only': cart_only,
            'lymphoma_only': cart_only, 'pure_play_payer': pure_play, 'direct_payment': direct}


def test_yearly_tables():
    rows = ind.general_by_year([general_record(OTHER, '2022', '2500.00', 'services', cart_only='1'),
                                general_record(OTHER, '2022', '40.10', 'hospitality_education', direct='0')])
    assert len(rows) == 7 and [r['year_status'] for r in rows].count('records_in_year') == 1
    year = next(r for r in rows if r['program_year'] == '2022')
    assert (year['cart_any_slot_rows'], year['cart_only_usd'], year['cart_only_services_usd']) == ('2', '2500.00', '2500.00')
    assert year['cart_only_direct_services_usd'] == '2500.00' and year['cart_only_hospitality_education_usd'] == '0.00'
    assert next(r for r in rows if r['program_year'] == '2019')['usd'] == '0.00'
    division = ind.division_year_rows(rows, {OTHER: 'affiliate_A'})
    cell = next(r for r in division if r['division_group'] == 'affiliates' and r['program_year'] == '2022')
    assert (cell['cart_only_npis_paid'], cell['cart_only_services_usd']) == ('1', '2500.00')
    links = [{'npi': CORE, 'pmid': '1', 'statement_status': 'author_named', 'named_with_cart_manufacturer': '1',
              'cart_manufacturers_named_for_author': 'Novartis', 'retrieved_at_utc': 'T'},
             {'npi': CORE, 'pmid': '2', 'statement_status': 'no_statement', 'named_with_cart_manufacturer': '',
              'cart_manufacturers_named_for_author': '', 'retrieved_at_utc': 'T'}]
    ties = ind.ties_by_year(links, {'1': '2023', '2': '2023'})
    assert len(ties) == 1 and ties[0]['held_papers'] == '2' and ties[0]['papers_determinable'] == '1'
    assert ties[0]['papers_named_with_novartis'] == '1' and ties[0]['papers_named_with_kite_gilead'] == '0'


def fixture(tmp_path):
    """A Data/processed directory with every input of check() and consistent synthetic tables."""
    root = tmp_path / 'processed'
    write_csv(root / '2026-10-24' / 'hcp_divisions.csv', [{'npi': n, 'division': d} for n, d in [(CORE, 'core'), (OTHER, 'affiliate_A')]])
    write_csv(root / '2026-10-09' / 'hcps.csv', [{'npi': CORE, 'hcp_id': 'NPI' + CORE}])
    write_csv(root / '2026-10-09' / 'publications.csv', [{'pmid': '100', 'publication_year': '2024'}])
    write_csv(root / '2026-10-09' / 'authorships.csv', [{'npi': CORE, 'pmid': '100', 'author_position': '1', 'last_name_as_indexed': 'Quill'}])
    write_csv(root / '2026-11-11' / 'general_payments_2019_2025.csv', [{'record_id': '900'}])
    write_csv(root / '2026-11-19' / 'universe_cart_general_payments_2019_2025.csv', [{'record_id': '901'}])
    write_csv(root / '2026-11-11' / 'research_payments_2019_2025.csv',
              [{'npi': CORE, 'record_id': '20', 'cart_product_mentioned': '1', 'pi_slots': '1', 'record_pi_count': '1',
                'nct_id': 'NCT1', 'manufacturer': 'Kite Pharma, Inc.', 'program_year': '2021', 'amount_usd': '1000.0',
                'cart_text_match': '0', 'lymphoma_cart_product_mentioned': '1', 'cart_products': 'YESCARTA',
                'products': 'Yescarta', 'study_name': 'Invented study'}])
    write_csv(root / '2026-11-11' / 'hcp_research_payment_features.csv',
              [{'npi': CORE, 'cart_research_rows_2019_2025': '1', 'cart_research_record_usd_2019_2025': '1000.0'}])
    own = [ind.ownership_row(ownership('11', OTHER, 'Example GPO LLC'), SCAN)]
    feat = [ind.ownership_features(CORE, []), ind.ownership_features(OTHER, own)]
    res = ind.research_rows(research('21', [OTHER], '', '1000.00', ['Yescarta'], 'Phase 2 study'), {OTHER}, SCAN)
    article = {'coi': 'A.B.Q. consults for Kite.', 'authors': AUTHORS, 'revised': '2025-01-02'}
    papers = [ind.paper_row('100', article, BATCH)]
    links = [ind.author_row(read_csv(root / '2026-10-09' / 'authorships.csv')[0], article, BATCH)]
    core_rows = ind.core_rows({CORE}, feat, read_csv(root / '2026-11-11' / 'research_payments_2019_2025.csv'), links)
    summary = {'npi': OTHER, f'cart_product_research_rows_{P}': '1', f'cart_product_research_investigator_rows_{P}': '1',
               f'cart_product_research_recipient_only_rows_{P}': '0', f'cart_product_research_record_usd_{P}': '1000.00',
               'cart_product_research_brands': 'YESCARTA', f'cart_product_or_text_research_rows_{P}': '1',
               f'cart_product_or_text_research_record_usd_{P}': '1000.00'}
    write_csv(root / '2026-11-19' / 'hcp_universe_research_payment_features.csv', [summary, {**summary, 'npi': CORE}])
    detail = [general_record(OTHER, '2022', '2500.00', 'services', cart_only='1'),
              general_record(OTHER, '2022', '40.10', 'hospitality_education', direct='0'),
              general_record(CORE, '2024', '15.00', 'hospitality_education', any_slot='0', pure_play='1')]
    write_csv(root / '2026-11-19' / 'universe_cart_general_payments_2019_2025.csv', detail, list(detail[0]))
    yearly = ind.general_by_year(detail)
    features = []
    for npi in [CORE, OTHER]:
        rows = [r for r in yearly if r['npi'] == npi]
        f = {'npi': npi}
        for prefix in ind.YEAR_RULES:
            f[f'{prefix}_rows_{P}'] = str(sum(int(r[f'{prefix}_rows']) for r in rows))
            f[f'{prefix}_usd_{P}'] = ind.usd(ind.cents_sum(rows, f'{prefix}_usd'))
        for prefix in ['cart_only', 'lymphoma_only']:
            f[f'{prefix}_services_usd_{P}'] = ind.usd(ind.cents_sum(rows, f'{prefix}_services_usd'))
        f[f'cart_only_direct_services_usd_{P}'] = ind.usd(ind.cents_sum(rows, 'cart_only_direct_services_usd'))
        features.append(f)
    write_csv(root / '2026-11-19' / 'hcp_universe_general_payment_features.csv', features, list(features[0]))
    write_csv(root / '2026-11-11' / 'hcp_general_payments_by_year.csv',
              [{'npi': CORE, 'program_year': str(y), 'cart_product_rows': '0', 'cart_product_usd': '0'} for y in ind.YEARS])
    division = ind.division_year_rows(yearly, {CORE: 'core', OTHER: 'affiliate_A'})
    ties = ind.ties_by_year(links, {'100': '2024'})
    write_csv(root / ind.SNAPSHOT / 'new_publications.csv', [{'pmid': '200'}])
    write_csv(root / ind.SNAPSHOT / 'universe_authorships.csv', [{'npi': OTHER, 'pmid': '100', 'author_position': '2', 'last_name_as_indexed': 'Marsh'}])
    write_csv(root / ind.SNAPSHOT / 'new_authorships.csv', [{'npi': CORE, 'pmid': '200', 'author_position': '1', 'last_name_as_indexed': 'Quill'}])
    new_article = {'coi': 'A.B.Q. consults for Novartis.', 'authors': AUTHORS, 'revised': '2026-03-01'}
    new_papers = [ind.paper_row('200', new_article, BATCH)]
    uni_links = [{**ind.author_row(read_csv(root / ind.SNAPSHOT / 'universe_authorships.csv')[0], article, BATCH),
                  'link_table': 'universe_authorships', 'is_core': '0'},
                 {**ind.author_row(read_csv(root / ind.SNAPSHOT / 'new_authorships.csv')[0], new_article, BATCH),
                  'link_table': 'new_authorships', 'is_core': '1'}]
    uni_summary = ind.universe_rows(feat, res, uni_links, {CORE})
    core_new = ind.core_new_rows(uni_links, {CORE})
    research_year = ind.research_by_year(read_csv(root / '2026-11-11' / 'research_payments_2019_2025.csv'), res, {CORE})
    research_division = ind.research_division_rows(research_year, {CORE: 'core', OTHER: 'affiliate_A'})
    for table, rows in zip(ind.TABLES, [own, feat, res, papers, links, core_rows, yearly, division, ties, new_papers, uni_links,
                                        uni_summary, core_new, research_year, research_division]):
        public = [c['column'] for c in ind.COLUMNS[table]() if not c['restricted']]
        write_csv(root / ind.SNAPSHOT / f'{table}.csv', [{k: r[k] for k in public} for r in rows], public)
    return root


def test_check_passes_and_catches_broken_tables(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    tables = {t: read_csv(root / ind.SNAPSHOT / f'{t}.csv') for t in ind.TABLES}
    monkeypatch.setattr(ind, 'HEADLINE', ind.headline(tables))
    monkeypatch.setattr(ind, 'FINGERPRINTS', {t: ind.fingerprint(rows, ind.KEYS[t]) for t, rows in tables.items()})
    assert not {k: v for k, v in ind.check(root).items() if v}
    path = root / ind.SNAPSHOT / f'{ind.RESEARCH}.csv'
    rows = read_csv(path)
    write_csv(path, rows + rows, list(rows[0]))
    found = ind.check(root)
    assert found[f'industry_{ind.RESEARCH}_duplicate_keys'] == 1
    assert found['industry_research_reconciles_with_2026_11_19_summary'] > 0
    assert found['industry_headline_research_rows'] == 1
    write_csv(path, [{**rows[0], 'record_id': '20', 'npi': CORE}], list(rows[0]))
    found = ind.check(root)
    assert found['industry_research_npi_is_core'] == 1 and found[f'industry_{ind.RESEARCH}_overlaps_earlier_snapshot'] == 1
    write_csv(path, [{**rows[0], 'nct_id': 'NCT99999999'}], list(rows[0]))
    found = {k: v for k, v in ind.check(root).items() if v}
    assert list(found) == [f'industry_{ind.RESEARCH}_fingerprint']
    write_csv(path, rows, list(rows[0]))
    new_path = root / ind.SNAPSHOT / f'{ind.NEW_PAPERS}.csv'
    new_rows_ = read_csv(new_path)
    write_csv(new_path, [{**new_rows_[0], 'pmid': '100'}], list(new_rows_[0]))
    assert ind.check(root)['industry_paper_tables_overlap'] == 1
    write_csv(new_path, new_rows_, list(new_rows_[0]))
    uni_path = root / ind.SNAPSHOT / f'{ind.UNIVERSE_AUTHORS}.csv'
    uni = read_csv(uni_path)
    write_csv(uni_path, uni + [{**uni[0], 'npi': CORE, 'is_core': '1'}], list(uni[0]))
    found = ind.check(root)
    assert found['industry_author_tables_overlap'] == 1 and found['industry_universe_links_not_link_tables'] == 1
    write_csv(uni_path, uni, list(uni[0]))
    assert not {k: v for k, v in ind.check(root).items() if v}
    feature_path = root / ind.SNAPSHOT / f'{ind.OWNERSHIP_FEATURES}.csv'
    features = read_csv(feature_path)
    write_csv(feature_path, [{**features[0], f'ownership_rows_{P}': '3'}] + features[1:], list(features[0]))
    assert ind.check(root)['industry_ownership_features_recompute'] == 1
    year_path = root / ind.SNAPSHOT / f'{ind.BY_YEAR}.csv'
    yearly = read_csv(year_path)
    write_csv(year_path, [{**yearly[0], 'cart_any_slot_usd': '0.01'}] + yearly[1:], list(yearly[0]))
    found = ind.check(root)
    assert found['industry_by_year_recompute'] == 1 and found['industry_by_year_sums_equal_2026_11_19_features'] > 0
