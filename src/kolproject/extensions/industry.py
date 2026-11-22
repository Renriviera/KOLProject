"""Industry ties of the 17,510-NPI universe that name CAR-T products, program years 2019-2025, and on held papers.

Three sources. The Ownership and Research detail files of the seven Open Payments program-year archives of the
2026-06-30 publication, already on disk for the 2026-11-11 snapshot, are streamed once each and their universe
rows saved under `Data/interim/2026-11-20/industry/` so a rebuild skips the scan. PubMed records of the 3,363
held CAR-T lymphoma papers are fetched with NCBI E-utilities efetch in batches of 200 and frozen under
`Data/raw/2026-11-20/industry/`; conflict-of-interest statement text stays local, only derived flags are tracked.
No 2026-11-11 or 2026-11-19 table is rewritten, and no general-payment record is published again.
"""
from __future__ import annotations

import concurrent.futures
import csv
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict

from ..research_payments import PI_SLOTS, PRODUCT_SLOTS, RELEASE_DATE, YEARS, archive_name, archive_url, cart_products
from ..research_payments import RAW as ARCHIVES
from ..volume_billing.industry_payments import fingerprint, research_links, research_text_match, to_cents, usd
from .base import (DIVISION_GROUPS, EARLIER_SNAPSHOTS, INTERIM, OUT, PROCESSED, RESTRICTED, ROOT, SNAPSHOT, UNIVERSE, column, dump,
                   existing_keys, fetch, load, log_local, new_rows, now, publish, read_csv, request_log, sha, universe,
                   write_csv, write_module_metadata)

MODULE = 'industry'
WORK = INTERIM / MODULE
SCAN_VERSION = '1.1.0'
PERIOD = f'{YEARS[0]}_{YEARS[-1]}'
TABLES = ['universe_ownership_interests_2019_2025', 'hcp_universe_ownership_features',
          'universe_cart_research_payments_2019_2025', 'publication_industry_disclosures',
          'core_author_publication_industry_ties', 'core_hcp_industry_ties_summary',
          'hcp_universe_cart_general_payments_by_year', 'cart_general_payments_by_division_year',
          'core_hcp_disclosed_ties_by_year', 'new_publication_industry_disclosures',
          'universe_author_publication_industry_ties', 'universe_hcp_industry_ties_summary', 'core_hcp_new_paper_ties',
          'hcp_universe_cart_research_by_year', 'cart_research_by_division_year']
(OWNERSHIP, OWNERSHIP_FEATURES, RESEARCH, PAPERS, AUTHORS, CORE_SUMMARY, BY_YEAR, DIVISION_YEAR, TIES_BY_YEAR,
 NEW_PAPERS, UNIVERSE_AUTHORS, UNIVERSE_SUMMARY, CORE_NEW, RESEARCH_YEAR, RESEARCH_DIVISION_YEAR) = TABLES
# Tables published before this pass, which a rebuild must leave byte-identical.
FROZEN_TABLES = TABLES[:13]
LYMPHOMA_BRANDS = {'YESCARTA', 'TECARTUS', 'KYMRIAH', 'BREYANZI'}
# Tables of the publications module of this snapshot, read only.
NEW_PUBLICATIONS, NEW_AUTHORSHIPS, UNIVERSE_AUTHORSHIPS = 'new_publications', 'new_authorships', 'universe_authorships'
CORE_SNAPSHOT, UNIVERSE_PAYMENTS = '2026-11-11', '2026-11-19'
GENERAL_DETAIL = f'{UNIVERSE_PAYMENTS}/universe_cart_general_payments_2019_2025.csv'
GENERAL_FEATURES = f'{UNIVERSE_PAYMENTS}/hcp_universe_general_payment_features.csv'
CORE_BY_YEAR = f'{CORE_SNAPSHOT}/hcp_general_payments_by_year.csv'
CORE_RESEARCH = f'{CORE_SNAPSHOT}/research_payments_2019_2025.csv'
CORE_RESEARCH_FEATURES = f'{CORE_SNAPSHOT}/hcp_research_payment_features.csv'
UNIVERSE_RESEARCH_FEATURES = f'{UNIVERSE_PAYMENTS}/hcp_universe_research_payment_features.csv'
# UTC download times of the program-year archives (their .meta.json files, 2026-11-11 snapshot), for yearly provenance.
ARCHIVE_RETRIEVED = {2019: '2026-11-12T03:01:20Z', 2020: '2026-11-12T03:01:53Z', 2021: '2026-11-12T03:01:33Z',
                     2022: '2026-11-12T03:01:50Z', 2023: '2026-11-12T03:02:30Z', 2024: '2026-11-12T03:02:42Z',
                     2025: '2026-11-12T03:02:21Z'}
# Rule prefix in the yearly table -> flag column of the 2026-11-19 row-level table (same prefixes as its feature table).
YEAR_RULES = {'cart_any_slot': 'any_slot', 'cart_only': 'cart_only', 'lymphoma_only': 'lymphoma_only',
              'pure_play_payer': 'pure_play_payer'}
EFETCH = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&retmode=xml&id='
BATCH = 200

# CAR-T manufacturer groups, matched case-insensitively on a manufacturer name or in disclosure text. The CMS
# manufacturer names each group matched are listed in the module metadata (cms_manufacturer_names_matched).
CART_MANUFACTURERS = {
    'Kite/Gilead': r'\bkite\b|\bgilead\b',
    'Bristol Myers Squibb/Celgene/Juno': r'bristol[- ]?myers|\bsquibb\b|\bbms\b|\bcelgene\b|\bjuno\b',
    'Novartis': r'\bnovartis\b',
    'Janssen/Johnson & Johnson': r'\bjans+en\b|johnson\s*(?:&|and)\s*johnson|\bj\s?&\s?j\b',
    'Legend Biotech': r'\blegend biotech\b|(?-i:\bLegend\b)',
    'Autolus': r'\bautolus\b',
    'bluebird bio/2seventy bio': r'\bbluebird\b|\b2seventy\b',
}
MANUFACTURER_PATTERNS = {group: re.compile(p, re.I) for group, p in CART_MANUFACTURERS.items()}
NO_CONFLICT = re.compile(r'\b(?:no|none|nothing)\b[^.]{0,60}\b(?:conflicts?|competing|disclos|financial)', re.I)

# A study name fails the screen, and is kept only in the local companion, when it holds what may identify a person
# or a place: an e-mail address, a telephone number, a street address, an honorific, or a person name on the record.
SCREEN = re.compile(r'@|\b\d{3}[-. )]\s?\d{3}[-. ]\d{4}\b|\b\d+\s+\w+(?:\s\w+)?\s(?:street|st|avenue|ave|road|rd|'
                    r'boulevard|blvd|drive|dr|suite|ste|lane|ln|way|parkway|pkwy)\b|\b(?:dr|prof)\b\.?\s|\b(?:mr|mrs|ms)\.\s|'
                    r'\bm\.?d\.?,|\bph\.?d\b', re.I)


def manufacturers_named(text):
    """CAR-T manufacturer groups named in a text, in list order."""
    return [group for group, pattern in MANUFACTURER_PATTERNS.items() if text and pattern.search(text)]


def screen_study(study, names):
    """'passed', 'failed' or 'blank' for a study name, given the person names (first, last) on its record."""
    if not study.strip():
        return 'blank'
    if SCREEN.search(study):
        return 'failed'
    words = set(re.findall(r'[a-z]+', study.lower()))
    return 'failed' if any(len(n) >= 3 and n.lower() in words for n in names if n) else 'passed'


# ---------------------------------------------------------------- archive scan

def scan(year):
    """Stream the Ownership and Research detail files of one archive and save universe rows; skipped when done."""
    summary_path = WORK / f'scan_{year}.json'
    meta = load((ARCHIVES / archive_name(year)).with_suffix('.meta.json'))
    stamp = {'scan_version': SCAN_VERSION, 'archive_sha256': meta['sha256'], 'universe_sha256': sha(UNIVERSE)}
    if summary_path.exists():
        done = load(summary_path)
        if all(done.get(k) == v for k, v in stamp.items()) and all((WORK / n).exists() for n in done['outputs']):
            return done
    WORK.mkdir(parents=True, exist_ok=True)
    npis = set(universe())
    archive_path = ARCHIVES / archive_name(year)
    assert sha(archive_path) == meta['sha256'], f'Archive differs from its recorded hash: {archive_path}'
    counts, outputs, members, cart_owners = Counter(), {}, {}, Counter()
    with zipfile.ZipFile(archive_path) as archive:
        for code, kind in [('OWNRSHP', 'ownership'), ('RSRCH', 'research')]:
            found = [m for m in archive.infolist() if re.search(r'OP_DTL_%s_PGYR%d_.*\.csv$' % (code, year), m.filename)]
            assert len(found) == 1, f'Expected one {kind} detail file for {year}'
            member = members[kind] = found[0]
            path = WORK / f'{kind}_rows_{year}.csv'
            with archive.open(member) as binary, path.open('w', newline='', encoding='utf-8') as sink:
                reader = csv.reader(io.TextIOWrapper(binary, encoding='utf-8-sig', newline=''))
                header = [name.strip().lower() for name in next(reader)]
                at = {name: i for i, name in enumerate(header)}
                writer = csv.writer(sink)
                writer.writerow(header)
                for row in reader:
                    counts[f'{kind}_rows_scanned'] += 1
                    assert row[at['program_year']] == str(year), f'Unexpected program year in {member.filename}'
                    if kind == 'ownership':
                        payer = row[at['applicable_manufacturer_or_applicable_gpo_making_payment_name']]
                        if manufacturers_named(payer):
                            cart_owners[payer] += 1
                        if row[at['physician_npi']] in npis:
                            writer.writerow(row)
                            counts['ownership_rows_kept'] += 1
                        continue
                    pis = [row[at[f'principal_investigator_{k}_npi']] for k in PI_SLOTS]
                    if not research_links([p for p in pis if p], row[at['covered_recipient_npi']], npis):
                        continue
                    names = [row[at[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}']] for k in PRODUCT_SLOTS]
                    if cart_products(*names) or research_text_match(row[at['name_of_study']], row[at['context_of_research']]):
                        writer.writerow(row)
                        counts['research_rows_kept'] += 1
            outputs[path.name] = sha(path)
    summary = {**stamp, 'program_year': year, 'archive': archive_name(year),
               'members': {k: {'member': m.filename, 'bytes': m.file_size, 'crc32': f'{m.CRC:08x}'} for k, m in members.items()},
               **counts, 'ownership_cart_manufacturer_names_all_physicians': dict(sorted(cart_owners.items())),
               'outputs': outputs, 'scanned_at_utc': now(), 'retrieved_at_utc': meta['retrieved_at_utc']}
    dump(summary_path, summary)
    print(MODULE.upper(), 'SCAN', year, dict(counts), flush=True)
    return summary


def scan_or_extract(year):
    """The saved scan summary when its extracts exist (a rebuild needs no archive), else a fresh scan."""
    path = WORK / f'scan_{year}.json'
    if path.exists() and all((WORK / n).exists() for n in load(path)['outputs']) and load(path).get('scan_version') == SCAN_VERSION:
        return load(path)
    return scan(year)


def scan_all(workers=None):
    with concurrent.futures.ProcessPoolExecutor(workers or min(len(YEARS), os.cpu_count() or 4)) as executor:
        return list(executor.map(scan, YEARS))


# ---------------------------------------------------------------- PubMed

def held_pmids(processed=PROCESSED):
    return sorted({r['pmid'] for r in read_csv(processed / '2026-10-09' / 'publications.csv')}, key=int)


def fetch_pubmed(pmids):
    """Frozen efetch batches of the held PMIDs: (path, record) per batch, at most two requests a second."""
    return [fetch(EFETCH + ','.join(pmids[i:i + BATCH]), MODULE, pause=0.5, suffix='.xml')
            for i in range(0, len(pmids), BATCH)]


def text_of(node):
    return re.sub(r'\s+', ' ', ''.join(node.itertext())).strip() if node is not None else ''


def parse_articles(xml_bytes):
    """pmid -> {'coi', 'authors': [{last, fore, initials, affiliations}], 'revised', 'kind'} from one efetch payload.

    Journal articles (PubmedArticle) and books or book chapters (PubmedBookArticle, for example health technology
    assessment reports) are both read; a book record has its author list under BookDocument.
    """
    out = {}
    tree = ET.fromstring(xml_bytes)
    for tag, base, kind in [('PubmedArticle', 'MedlineCitation', 'journal_article'), ('PubmedBookArticle', 'BookDocument', 'book')]:
        for article in tree.iter(tag):
            pmid = text_of(article.find(f'{base}/PMID'))
            path = 'MedlineCitation/Article/AuthorList/Author' if kind == 'journal_article' else 'BookDocument/AuthorList/Author'
            authors = [{'last': text_of(a.find('LastName')), 'fore': text_of(a.find('ForeName')),
                        'initials': text_of(a.find('Initials')), 'collective': text_of(a.find('CollectiveName')),
                        'affiliations': [text_of(x) for x in a.findall('AffiliationInfo/Affiliation')]}
                       for a in article.findall(path)]
            revised = article.find(f'{base}/DateRevised')
            stamp = '-'.join(text_of(revised.find(k)) for k in ['Year', 'Month', 'Day']) if revised is not None else ''
            out[pmid] = {'coi': ' '.join(text_of(c) for c in article.iter('CoiStatement')).strip(), 'authors': authors,
                         'revised': stamp, 'kind': kind}
    return out


# Surnames that are also words of manufacturer names, never matched alone.
SURNAME_STOP = {'myers', 'squibb', 'bristol', 'johnson', 'kite', 'legend', 'juno', 'gilead', 'janssen', 'novartis'}


def author_patterns(author):
    """Regular expressions that find one author in a statement.

    Forms: full fore name and surname (periods optional), first fore name and surname, an honorific and surname
    (Dr. Surname), first initial and surname (B. Hill), the surname alone (case-sensitive, at least three letters,
    not a manufacturer word; also the last word of a compound surname), initials and surname, and initials alone with the surname initial (A.B.C., ABC, A.-B.C.).
    """
    last, fore, initials = author['last'], author['fore'], author['initials']
    if not last:
        return []
    surname = re.escape(last)
    found = [re.compile(r'\b(?:dr|drs|prof|professor)\.?\s+' + surname + r'\b', re.I)]
    words = [w for w in re.split(r'[\s.]+', fore) if w]
    if words:
        found.append(re.compile(r'\b' + r'\.?\s+'.join(re.escape(w) for w in words + [last]) + r'\b', re.I))
        found.append(re.compile(r'\b' + re.escape(words[0]) + r'\s+(?:[A-Z]\.?\s+)?' + surname + r'\b', re.I))
    if words:  # first initial, optional further initials or hyphen, then surname: 'B. Hill', 'M.-A. Perales'
        found.append(re.compile(r'(?<![A-Za-z])' + re.escape(words[0][0].upper()) + r'\.?(?:\s?-?[A-Z]\.?){0,2}\s?' + surname + r'\b'))
    if len(last) >= 3 and last.lower() not in SURNAME_STOP:  # surname alone, case-sensitive: 'Locke:', '(Epperla)'
        found.append(re.compile(r'(?<![A-Za-z\-])' + surname + r'\b'))
    tail = last.split()[-1]
    if ' ' in last and len(tail) >= 4 and tail.lower() not in SURNAME_STOP:  # last word of a compound surname
        found.append(re.compile(r'(?<![A-Za-z\-])' + re.escape(tail) + r'\b'))
    if initials:
        letters = list(initials) + [last[0].upper()]
        found.append(re.compile(r'(?<![A-Za-z.])' + r'\.?\s?-?'.join(letters) + r'\.?(?![A-Za-z])'))
        found.append(re.compile(r'\b' + r'\.?\s?'.join(initials) + r'\.?\s' + surname + r'\b'))
    return found


SENTENCE_END = re.compile(r'(?<=[a-z0-9)\]]{2})\.(?=\s|$)|\n')
# A sentence opening like these speaks for no single named author, or for an author tag the author list does not
# explain (initials then a colon or a reporting verb), so it ends the passage before it.
AUTHOR_TAG = re.compile(r'\s*(?:[A-Z][A-Z.\u00b7\-\s]{1,8}:|(?:[A-Z]\.\s?-?){1,4}\s|[A-Z][A-Za-z]?\.?\s?[A-Z]{1,3}\.?\s?:|[A-Z]{2,4}\s(?:reports?|has|have|is|received?|receives|serves?|declares?|holds?)\b|(?:dr|prof)\.?\s)')
GENERAL = re.compile(r'\s*(?:the\s+(?:remaining|other)|all\s+other|other\s+authors|none\s+of|no\s+other|the\s+authors?|'
                     r'this\s+(?:study|work|trial|research|article|manuscript)|funding|the\s+(?:study|trial|work)|'
                     r'author\s+contributions?|contributions?)\b', re.I)
JOINER = re.compile(r'^[\s,;&]*(?:and[\s,]*)?$', re.I)


def mentions(statement, authors):
    """(start, end, author index) of every author mention in a statement, earliest first, overlaps dropped."""
    found = sorted((m.start(), -m.end(), k) for k, a in enumerate(authors) for p in author_patterns(a)
                   for m in p.finditer(statement))
    out, reach = [], -1
    for start, end, k in found:
        if start >= reach:
            out.append((start, -end, k))
            reach = -end
    return out


def author_segments(statement, author, authors=None):
    """Passages of a statement that disclose for one author.

    A passage starts at a mention of the author and runs to the next mention of another author, to a sentence
    (ended by '.' after a lowercase word, a number or a bracket) that opens with general wording such as 'The
    remaining authors' or 'This study' or with an author tag (initials and a colon or a reporting verb, or 'Dr'), or to
    the end of the statement, whichever comes first. Another author
    mentioned right after this one with only commas, semicolons, '&' or 'and' between is co-named and does not end it.
    """
    authors = authors or [author]
    target = next((k for k, a in enumerate(authors) if a is author), None)
    if target is None and author in authors:
        target = authors.index(author)
    found = mentions(statement, authors)
    ends = [m.end() for m in SENTENCE_END.finditer(statement)
            if GENERAL.match(statement, m.end()) or AUTHOR_TAG.match(statement, m.end())] + [len(statement)]
    out = []
    for n, (start, end, k) in enumerate(found):
        if k != target:
            continue
        stop, last = next(e for e in ends if e >= end), end
        for other_start, other_end, other in found[n + 1:]:
            if other_start >= stop:
                break
            if other != target and not JOINER.match(statement[last:other_start]):
                stop = other_start
                break
            last = other_end
        out.append(statement[start:stop])
    return out


def author_tie(statement, authors, index):
    """(status, manufacturer groups) for author `index` of a paper under the statement parsing rule.

    no_statement: no statement. no_cart_manufacturer: the statement names no CAR-T manufacturer, so the author is not
    named with one. not_structured: a CAR-T manufacturer is named but no author of the paper can be found in the
    statement, so the statement cannot be read author by author. author_not_named: other authors are found, this
    one is not. author_named: the author is found; the groups are those named in the sentences that name the author.
    """
    if not statement:
        return 'no_statement', []
    if not manufacturers_named(statement):
        return 'no_cart_manufacturer', []
    if not mentions(statement, authors):
        return 'not_structured', []
    own = author_segments(statement, authors[index], authors) if index is not None else []
    if not own:
        return 'author_not_named', []
    return 'author_named', manufacturers_named(' '.join(own))


TIE_VALUE = {'no_statement': '', 'no_cart_manufacturer': '0', 'not_structured': '', 'author_not_named': '0'}


def locate(authors, position, last):
    """Index of a core author in the PubMed author list: by position and surname, else by a unique surname."""
    norm = lambda s: re.sub(r'[^a-z]', '', s.lower())
    i = int(position) - 1 if str(position).isdigit() else -1
    if 0 <= i < len(authors) and norm(authors[i]['last']) == norm(last):
        return i, 'position_and_surname'
    same = [k for k, a in enumerate(authors) if norm(a['last']) == norm(last)]
    return (same[0], 'unique_surname') if len(same) == 1 else (None, 'not_located')


# ---------------------------------------------------------------- record shaping

def money_text(value):
    """Dollar text in whole cents; a blank stays blank."""
    return usd(to_cents(value)) if str(value).strip() else ''


def iso(value):
    month, day, year = value.split('/') if value else ('', '', '')
    return f'{year}-{month}-{day}' if value else ''


def manufacturer_group(name):
    groups = manufacturers_named(name)
    return groups[0] if groups else ''


def source_fields(year, scan_summary, kind):
    return {'source_file': scan_summary['members'][kind]['member'], 'source_url': archive_url(year),
            'source_release_date': RELEASE_DATE, 'retrieved_at_utc': scan_summary['retrieved_at_utc']}


def ownership_row(record, scan_summary):
    """One published ownership-interest row; the free-text terms go to the local companion only."""
    payer = record['applicable_manufacturer_or_applicable_gpo_making_payment_name']
    group = manufacturer_group(payer)
    terms = record['terms_of_interest'].strip()
    return {'record_id': record['record_id'], 'npi': record['physician_npi'], 'program_year': record['program_year'],
            'manufacturer': payer, 'manufacturer_id': record['applicable_manufacturer_or_applicable_gpo_making_payment_id'],
            'submitting_manufacturer': record['submitting_applicable_manufacturer_or_applicable_gpo_name'],
            'cart_manufacturer': str(int(bool(group))), 'cart_manufacturer_group': group,
            'value_of_interest_usd': money_text(record['value_of_interest']),
            'amount_invested_usd': money_text(record['total_amount_invested_usdollars']),
            'interest_holder': record['interest_held_by_physician_or_an_immediate_family_member'],
            'terms_reported': str(int(bool(terms))), 'terms_of_interest': terms,
            'dispute_status': record['dispute_status_for_publication'], 'change_type': record['change_type'],
            'payment_publication_date': iso(record['payment_publication_date']),
            **source_fields(int(record['program_year']), scan_summary, 'ownership')}


def research_rows(record, npis, scan_summary):
    """Published rows of one Research record, one per non-core universe NPI named on it."""
    pis = [record[f'principal_investigator_{k}_npi'] for k in PI_SLOTS]
    links = research_links([p for p in pis if p], record['covered_recipient_npi'], npis)
    names = [record[f'name_of_drug_or_biological_or_device_or_medical_supply_{k}'] for k in PRODUCT_SLOTS]
    brands = cart_products(*names)
    study = record['name_of_study'].strip()
    people = [record[f'principal_investigator_{k}_{part}_name'] for k in PI_SLOTS for part in ['first', 'last']]
    people += [record['covered_recipient_first_name'], record['covered_recipient_last_name']]
    status = screen_study(study, people)
    payer = record['applicable_manufacturer_or_applicable_gpo_making_payment_name']
    base = {'record_id': record['record_id'], 'program_year': record['program_year'],
            'payment_date': iso(record['date_of_payment']),
            'amount_usd': usd(to_cents(record['total_amount_of_payment_usdollars'])),
            'record_pi_count': str(sum(bool(record[f'principal_investigator_{k}_npi'] or
                                            record[f'principal_investigator_{k}_last_name']) for k in PI_SLOTS)),
            'record_universe_npi_count': str(len(links)), 'covered_recipient_type': record['covered_recipient_type'],
            'teaching_hospital_ccn': record['teaching_hospital_ccn'], 'manufacturer': payer,
            'manufacturer_id': record['applicable_manufacturer_or_applicable_gpo_making_payment_id'],
            'cart_manufacturer_group': manufacturer_group(payer),
            'cart_products': ';'.join(brands), 'cart_product_mentioned': str(int(bool(brands))),
            'match_basis': 'product_field' if brands else 'study_or_context_text',
            'study_name': study if status == 'passed' else '', 'study_name_screen': status,
            'study_name_withheld': study if status == 'failed' else '',
            'nct_id': record['clinicaltrials_gov_identifier'].strip(),
            'preclinical_research_indicator': record['preclinical_research_indicator'],
            'payment_form': record['form_of_payment_or_transfer_of_value'],
            'dispute_status': record['dispute_status_for_publication'], 'change_type': record['change_type'],
            'payment_publication_date': iso(record['payment_publication_date']),
            **source_fields(int(record['program_year']), scan_summary, 'research')}
    out = []
    for npi, role in links:
        slots = [str(k) for k, p in zip(PI_SLOTS, pis) if p == npi]
        out.append({'npi': npi, 'role': role, 'pi_slots': ';'.join(slots), **base})
    return out


def ownership_features(npi, rows):
    """One zero-filled per-NPI ownership row; dollar sums stay blank when the NPI has no record."""
    cart = [r for r in rows if r['cart_manufacturer'] == '1']
    total = lambda part, field: usd(sum(to_cents(r[field]) for r in part if r[field])) if part else ''
    return {'npi': npi, 'ownership_data_status': 'published_records' if rows else 'no_published_records',
            f'ownership_rows_{PERIOD}': str(len(rows)),
            f'ownership_physician_held_rows_{PERIOD}': str(sum(r['interest_holder'] == 'Physician Covered Recipient' for r in rows)),
            f'ownership_family_held_rows_{PERIOD}': str(sum(r['interest_holder'] == 'Immediate family member' for r in rows)),
            'ownership_manufacturers': str(len({r['manufacturer_id'] for r in rows})),
            'ownership_active_years': str(len({r['program_year'] for r in rows})),
            'ownership_latest_program_year': max((r['program_year'] for r in rows), default=''),
            f'ownership_value_of_interest_usd_{PERIOD}': total(rows, 'value_of_interest_usd'),
            f'ownership_amount_invested_usd_{PERIOD}': total(rows, 'amount_invested_usd'),
            f'cart_manufacturer_ownership_rows_{PERIOD}': str(len(cart)),
            f'cart_manufacturer_ownership_value_usd_{PERIOD}': total(cart, 'value_of_interest_usd'),
            'cart_manufacturer_ownership_groups': ';'.join(sorted({r['cart_manufacturer_group'] for r in cart})),
            'program_years_scanned': f'{YEARS[0]}-{YEARS[-1]}', 'source_release_date': RELEASE_DATE}


def paper_row(pmid, article, record):
    """One per-paper disclosure row from a parsed PubMed record (None when PubMed did not return the PMID)."""
    prov = {'source_id': record['source_id'], 'pubmed_url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
            'pubmed_date_revised': article['revised'] if article else '', 'retrieved_at_utc': record['retrieved_at_utc']}
    if not article:
        blank = {k: '' for k in ['pubmed_author_count', 'coi_statement_present', 'coi_cart_manufacturers',
                                 'coi_cart_manufacturer_count', 'coi_no_conflict_wording', 'coi_names_authors',
                                 'industry_affiliation_authors', 'industry_coauthorship', 'industry_affiliation_groups',
                                 'coi_statement']}
        return {'pmid': pmid, 'pubmed_record_status': 'not_returned', 'pubmed_record_type': '', **blank, **prov,
                'pubmed_date_revised': 'not_returned'}
    coi, authors = article['coi'], article['authors']
    groups = manufacturers_named(coi)
    industry = [manufacturers_named(' | '.join(a['affiliations'])) for a in authors]
    return {'pmid': pmid, 'pubmed_record_status': 'returned', 'pubmed_record_type': article.get('kind', 'journal_article'),
            'pubmed_author_count': str(len(authors)),
            'coi_statement_present': str(int(bool(coi))), 'coi_cart_manufacturers': ';'.join(groups),
            'coi_cart_manufacturer_count': str(len(groups)) if coi else '',
            'coi_no_conflict_wording': str(int(bool(NO_CONFLICT.search(coi)))) if coi else '',
            'coi_names_authors': str(int(bool(mentions(coi, authors)))) if coi else '',
            'industry_affiliation_authors': str(sum(bool(g) for g in industry)),
            'industry_coauthorship': str(int(any(industry))),
            'industry_affiliation_groups': ';'.join(g for g in MANUFACTURER_PATTERNS if any(g in x for x in industry)),
            'coi_statement': coi, **prov, 'pubmed_date_revised': article['revised'] or 'not_reported'}


def author_row(link, article, record):
    """One (core NPI, PMID) row: is the author named in the statement, and named with a CAR-T manufacturer."""
    prov = {'source_id': record['source_id'], 'retrieved_at_utc': record['retrieved_at_utc'],
            'pubmed_date_revised': (article['revised'] or 'not_reported') if article else 'not_returned'}
    base = {'npi': link['npi'], 'pmid': link['pmid'], 'author_position': link['author_position']}
    if not article:
        return {**base, 'pubmed_author_located': 'record_not_returned', 'statement_status': '', 'named_in_statement': '',
                'named_with_cart_manufacturer': '', 'cart_manufacturers_named_for_author': '',
                'own_affiliation_cart_manufacturer': '', **prov}
    authors, coi = article['authors'], article['coi']
    index, how = locate(authors, link['author_position'], link['last_name_as_indexed'])
    status, groups = author_tie(coi, authors, index)
    named = ''
    if coi and mentions(coi, authors) and index is not None:
        named = str(int(bool(author_segments(coi, authors[index], authors))))
    tie = TIE_VALUE.get(status, str(int(bool(groups))))
    if status == 'author_named' and index is None:
        tie = ''
    own = '' if index is None else str(int(bool(manufacturers_named(' | '.join(authors[index]['affiliations'])))))
    return {**base, 'pubmed_author_located': how, 'statement_status': status, 'named_in_statement': named,
            'named_with_cart_manufacturer': tie if index is not None or status in TIE_VALUE else '',
            'cart_manufacturers_named_for_author': ';'.join(groups), 'own_affiliation_cart_manufacturer': own, **prov}


def core_summary(npi, features, research, links, inputs=None):
    """One row per HCP joining ownership, CAR-T product research roles and disclosed ties (core: 2026-11-11 research rows)."""
    product = [r for r in research if r['cart_product_mentioned'] == '1']
    research_groups = {manufacturer_group(r['manufacturer']) for r in product} - {''}
    disclosed = {g for r in links for g in r['cart_manufacturers_named_for_author'].split(';') if g}
    count = lambda field, value: str(sum(r[field] == value for r in links))
    return {'npi': npi, f'ownership_rows_{PERIOD}': features[f'ownership_rows_{PERIOD}'],
            f'cart_manufacturer_ownership_rows_{PERIOD}': features[f'cart_manufacturer_ownership_rows_{PERIOD}'],
            f'cart_product_research_rows_{PERIOD}': str(len(product)),
            f'cart_product_research_investigator_rows_{PERIOD}': str(sum(bool(r['pi_slots']) for r in product)),
            f'cart_product_research_sole_pi_rows_{PERIOD}': str(sum(bool(r['pi_slots']) and r['record_pi_count'] == '1' for r in product)),
            'cart_product_research_nct_ids': str(len({r['nct_id'] for r in product if r['nct_id']})),
            'cart_product_research_manufacturer_groups': ';'.join(g for g in CART_MANUFACTURERS if g in research_groups),
            'held_paper_links': str(len(links)),
            'papers_with_coi_statement': str(sum(r['statement_status'] not in ('', 'no_statement') for r in links)),
            'papers_named_with_cart_manufacturer': count('named_with_cart_manufacturer', '1'),
            'papers_not_named_with_cart_manufacturer': count('named_with_cart_manufacturer', '0'),
            'papers_undetermined': count('named_with_cart_manufacturer', ''),
            'disclosed_cart_manufacturer_groups': ';'.join(g for g in CART_MANUFACTURERS if g in disclosed),
            'papers_with_own_cart_affiliation': count('own_affiliation_cart_manufacturer', '1'),
            'groups_in_both_research_and_disclosure': str(len(research_groups & disclosed)),
            'inputs': inputs or f'{SNAPSHOT}/{OWNERSHIP_FEATURES};{CORE_SNAPSHOT}/research_payments_2019_2025;{SNAPSHOT}/{AUTHORS}',
            'source_release_date': f'Open Payments {RELEASE_DATE}; PubMed as retrieved {SNAPSHOT}',
            'retrieved_at_utc': max((r['retrieved_at_utc'] for r in links), default=ARCHIVE_RETRIEVED[YEARS[-1]])}


# ---------------------------------------------------------------- columns

NEVER = 'Never blank.'


def op(name, description, missing=NEVER, restricted=False):
    return column(name, description, missing, 'open_payments', restricted)


def pm(name, description, missing=NEVER, restricted=False):
    return column(name, description, missing, 'pubmed', restricted)


def archive_provenance():
    return [op('source_file', 'Detail file inside the Open Payments program-year archive the record was read from.'),
            op('source_url', 'URL of the CMS Open Payments program-year archive (publication of 2026-06-30).'),
            op('source_release_date', 'Publication date of the Open Payments archive (2026-06-30).'),
            op('retrieved_at_utc', 'Coordinated Universal Time (UTC) at which the archive was downloaded for the 2026-11-11 snapshot.')]


GROUP_TEXT = ('one of seven CAR-T manufacturer groups (Kite/Gilead; Bristol Myers Squibb/Celgene/Juno; Novartis; '
              'Janssen/Johnson & Johnson; Legend Biotech; Autolus; bluebird bio/2seventy bio)')


def ownership_columns():
    return [
        op('record_id', 'CMS Open Payments record identifier of the ownership or investment interest; unique in this table.'),
        op('npi', 'Ten-digit National Provider Identifier (NPI) of the physician holding the interest; a universe NPI of hcp_divisions.csv (2026-10-24).'),
        op('program_year', 'Open Payments program year of the report (2019-2025).'),
        op('manufacturer', 'Name of the applicable manufacturer or group purchasing organization (GPO) in which the interest is held, as reported to CMS.'),
        op('manufacturer_id', 'CMS identifier of that manufacturer or GPO.'),
        op('submitting_manufacturer', 'Name of the manufacturer or GPO that submitted the report.'),
        op('cart_manufacturer', f'1 when the manufacturer name matches {GROUP_TEXT}, else 0.'),
        op('cart_manufacturer_group', 'The CAR-T manufacturer group matched by the manufacturer name.', 'Blank when cart_manufacturer is 0.'),
        op('value_of_interest_usd', 'Dollar value of the ownership or investment interest reported for the program year, in whole cents.', 'Blank when CMS reports no value.'),
        op('amount_invested_usd', 'Total dollar amount invested reported for the program year, in whole cents.', 'Blank when CMS reports no amount.'),
        op('interest_holder', 'Who holds the interest: the physician (Physician Covered Recipient) or an immediate family member.'),
        op('terms_reported', '1 when the manufacturer reported free-text terms of the interest, else 0; the text itself is kept local because it can name people and places.'),
        op('terms_of_interest', 'Free-text terms of the interest as reported by the manufacturer.', 'Blank when none was reported.', restricted=True),
        op('dispute_status', 'CMS dispute status for publication (Yes or No).'),
        op('change_type', 'CMS change type of the record in this publication (NEW, ADD, CHANGED, UNCHANGED).'),
        op('payment_publication_date', 'Date CMS published the record (ISO 8601).'),
        *archive_provenance()]


def ownership_feature_columns():
    P = PERIOD.replace('_', '-')
    return [
        op('npi', 'Ten-digit National Provider Identifier (NPI); every NPI of hcp_divisions.csv (2026-10-24) appears once.'),
        op('ownership_data_status', 'published_records when at least one ownership or investment record names the NPI in program years 2019-2025, else no_published_records.'),
        op(f'ownership_rows_{PERIOD}', f'Ownership or investment records naming the NPI, program years {P}; zero when none.'),
        op(f'ownership_physician_held_rows_{PERIOD}', 'Records whose interest is held by the physician; zero when none.'),
        op(f'ownership_family_held_rows_{PERIOD}', 'Records whose interest is held by an immediate family member; zero when none.'),
        op('ownership_manufacturers', 'Distinct manufacturer or GPO identifiers on the NPI records; zero when none.'),
        op('ownership_active_years', 'Program years with at least one record; zero when none.'),
        op('ownership_latest_program_year', 'Latest program year with a record.', 'Blank when the NPI has no record.'),
        op(f'ownership_value_of_interest_usd_{PERIOD}', f'Sum of the reported value of interest over the NPI records, program years {P}, in US dollars; a holding reported in several years is counted in each year.', 'Blank when the NPI has no record.'),
        op(f'ownership_amount_invested_usd_{PERIOD}', f'Sum of the reported amount invested over the NPI records, program years {P}, in US dollars.', 'Blank when the NPI has no record.'),
        op(f'cart_manufacturer_ownership_rows_{PERIOD}', f'Records whose manufacturer matches {GROUP_TEXT}; zero when none.'),
        op(f'cart_manufacturer_ownership_value_usd_{PERIOD}', 'Sum of the reported value of interest over those CAR-T manufacturer records, in US dollars.', 'Blank when the NPI has no CAR-T manufacturer record.'),
        op('cart_manufacturer_ownership_groups', 'CAR-T manufacturer groups of those records, semicolon-separated.', 'Blank when the NPI has no CAR-T manufacturer record.'),
        op('program_years_scanned', 'Range of Open Payments program years whose Ownership file was scanned in full.'),
        op('source_release_date', 'Publication date of the Open Payments archives (2026-06-30).')]


def research_columns():
    return [
        op('npi', 'Ten-digit National Provider Identifier (NPI) of a universe NPI (hcp_divisions.csv, 2026-10-24) who is not one of the 500 core HCPs.'),
        op('record_id', 'CMS Open Payments research record identifier; with npi, the key of this table.'),
        op('role', 'investigator when the NPI is in a principal-investigator slot; recipient_only when it is only the covered recipient.'),
        op('pi_slots', 'Principal-investigator slot numbers (1-5) holding the NPI, semicolon-separated.', 'Blank when role is recipient_only.'),
        op('program_year', 'Open Payments program year of the record (2019-2025).'),
        op('payment_date', 'Date of payment reported by the manufacturer (ISO 8601).', 'Blank when CMS reports none.'),
        op('amount_usd', 'Total amount of the research record in US dollars, in whole cents; a record-level value repeated on every NPI row of the record, not personal income.'),
        op('record_pi_count', 'Number of populated principal-investigator slots on the record.'),
        op('record_universe_npi_count', 'Number of universe NPIs (core included) named on the record as investigator or recipient.'),
        op('covered_recipient_type', 'CMS covered-recipient type of the record (for example Covered Recipient Teaching Hospital or Non-covered Recipient Entity).'),
        op('teaching_hospital_ccn', 'CMS Certification Number (CCN) of the teaching hospital recipient.', 'Blank when the recipient is not a teaching hospital.'),
        op('manufacturer', 'Applicable manufacturer or group purchasing organization (GPO) making the payment.'),
        op('manufacturer_id', 'CMS identifier of the manufacturer making the payment.'),
        op('cart_manufacturer_group', f'The payer name matched to {GROUP_TEXT}.', 'Blank when the payer matches none.'),
        op('cart_products', 'CAR-T products (Yescarta, Tecartus, Kymriah, Breyanzi, Abecma, Carvykti, Aucatzyl) named by brand, generic or development name in the five product fields, semicolon-separated.', 'Blank when match_basis is study_or_context_text.'),
        op('cart_product_mentioned', '1 when a product field names a CAR-T product, else 0.'),
        op('match_basis', 'product_field when a product field names a CAR-T product; study_or_context_text when only the study name or research context names a CAR-T product or uses generic CAR-T wording (a text heuristic).'),
        op('study_name', 'Name of the study reported by the manufacturer, published only when it passes the person-and-address screen.', 'Blank when no study name was reported or it failed the screen (see study_name_screen).'),
        op('study_name_screen', 'passed, failed (holds an e-mail, telephone number, street address, honorific or a person name on the record; kept local) or blank (none reported).'),
        op('study_name_withheld', 'Study name that failed the screen.', 'Blank unless study_name_screen is failed.', restricted=True),
        op('nct_id', 'ClinicalTrials.gov identifier reported by the manufacturer.', 'Blank when none was reported.'),
        op('preclinical_research_indicator', 'CMS preclinical research indicator (Yes or No).', 'Blank when not reported.'),
        op('payment_form', 'Form of payment or transfer of value.'),
        op('dispute_status', 'CMS dispute status for publication (Yes or No).'),
        op('change_type', 'CMS change type of the record in this publication (NEW, ADD, CHANGED, UNCHANGED).'),
        op('payment_publication_date', 'Date CMS published the record (ISO 8601).'),
        *archive_provenance()]


def paper_columns():
    na = 'Blank when PubMed did not return the record.'
    nos = 'Blank when PubMed did not return the record or it has no statement.'
    return [
        pm('pmid', 'PubMed identifier (PMID) of a held CAR-T lymphoma paper of publications.csv (2026-10-09); unique.'),
        pm('pubmed_record_status', 'returned when the efetch response held the PMID, not_returned otherwise.'),
        pm('pubmed_record_type', 'journal_article (PubmedArticle) or book (PubmedBookArticle, such as a health technology assessment report).', na),
        pm('pubmed_author_count', 'Number of entries in the PubMed author list (collective names included).', na),
        pm('coi_statement_present', '1 when the PubMed record carries a conflict-of-interest (COI) statement, else 0.', na),
        pm('coi_cart_manufacturers', f'CAR-T manufacturer groups named anywhere in the statement: {GROUP_TEXT[4:]}.', 'Blank when none is named or there is no statement.'),
        pm('coi_cart_manufacturer_count', 'Number of CAR-T manufacturer groups named in the statement.', nos),
        pm('coi_no_conflict_wording', '1 when the statement contains no-conflict wording (for example "no competing interests"), else 0; a statement can hold both.', nos),
        pm('coi_names_authors', '1 when at least one author of the paper is found in the statement by full name, initials and surname, or initials, else 0.', nos),
        pm('industry_affiliation_authors', 'Authors with at least one PubMed affiliation naming a CAR-T manufacturer group.', na),
        pm('industry_coauthorship', '1 when any author affiliation names a CAR-T manufacturer group, else 0.', na),
        pm('industry_affiliation_groups', 'CAR-T manufacturer groups named in author affiliations.', 'Blank when none or the record was not returned.'),
        pm('coi_statement', 'Conflict-of-interest statement text as published; publisher text, kept local.', 'Blank when none.', restricted=True),
        pm('source_id', 'Identifier of the frozen efetch response (Data/raw/2026-11-20/industry/) holding the record.'),
        pm('pubmed_url', 'PubMed page of the paper.'),
        pm('pubmed_date_revised', 'DateRevised of the PubMed record (YYYY-MM-DD), the version read; not_returned or not_reported otherwise.'),
        pm('retrieved_at_utc', 'Coordinated Universal Time (UTC) at which the efetch response was retrieved.')]


def author_columns():
    unk = 'Blank when the record was not returned, has no statement, or the author could not be read from it.'
    return [
        pm('npi', 'Ten-digit National Provider Identifier (NPI) of a core HCP (hcps.csv, 2026-10-09).'),
        pm('pmid', 'PubMed identifier of a held paper; (npi, pmid) is a link of authorships.csv (2026-10-09) and the key here.'),
        pm('author_position', 'Position of the core author in the author list, from authorships.csv.'),
        pm('pubmed_author_located', 'How the core author was found in the PubMed author list: position_and_surname, unique_surname, not_located or record_not_returned.'),
        pm('statement_status', 'no_statement; no_cart_manufacturer (statement names none); not_structured (names one but no author of the paper can be found in it); author_not_named; author_named.', 'Blank when the record was not returned.'),
        pm('named_in_statement', '1 when the author is found in a statement that names authors, else 0.', unk),
        pm('named_with_cart_manufacturer', '1 when a passage of the statement that discloses for this author names a CAR-T manufacturer group; 0 when it does not, when the statement names none, or when other authors are found and this one is not.', 'Blank when there is no statement, it is not structured, or the author was not located.'),
        pm('cart_manufacturers_named_for_author', 'CAR-T manufacturer groups named in the passages that disclose for this author.', 'Blank when none.'),
        pm('own_affiliation_cart_manufacturer', '1 when one of the author own PubMed affiliations names a CAR-T manufacturer group, else 0.', 'Blank when the author was not located or the record was not returned.'),
        pm('source_id', 'Identifier of the frozen efetch response holding the record.'),
        pm('pubmed_date_revised', 'DateRevised of the PubMed record (YYYY-MM-DD), the version read; not_returned or not_reported otherwise.'),
        pm('retrieved_at_utc', 'Coordinated Universal Time (UTC) at which the efetch response was retrieved.')]


def core_summary_columns():
    pj = lambda n, d, m=NEVER: column(n, d, m, 'project')
    P = PERIOD.replace('_', '-')
    return [
        pj('npi', 'Ten-digit National Provider Identifier (NPI) of a core HCP (hcps.csv, 2026-10-09); all 500 appear once.'),
        pj(f'ownership_rows_{PERIOD}', f'Ownership or investment records naming the HCP, program years {P} (from hcp_universe_ownership_features); zero when none.'),
        pj(f'cart_manufacturer_ownership_rows_{PERIOD}', 'Of those, records in a CAR-T manufacturer group; zero when none.'),
        pj(f'cart_product_research_rows_{PERIOD}', f'Open Payments research records naming the HCP whose product fields name a CAR-T product, program years {P}, from research_payments_2019_2025.csv (2026-11-11); zero when none.'),
        pj(f'cart_product_research_investigator_rows_{PERIOD}', 'Of those, records with the HCP in a principal-investigator slot.'),
        pj(f'cart_product_research_sole_pi_rows_{PERIOD}', 'Of those, records on which the HCP is the only principal investigator.'),
        pj('cart_product_research_nct_ids', 'Distinct ClinicalTrials.gov identifiers on those records; zero when none.'),
        pj('cart_product_research_manufacturer_groups', 'CAR-T manufacturer groups paying those records, semicolon-separated.', 'Blank when none.'),
        pj('held_paper_links', 'Held CAR-T lymphoma papers authored by the HCP (links of authorships.csv, 2026-10-09).'),
        pj('papers_with_coi_statement', 'Of those, papers whose PubMed record carries a conflict-of-interest statement.'),
        pj('papers_named_with_cart_manufacturer', 'Papers whose statement discloses for the HCP a tie to a CAR-T manufacturer group (named_with_cart_manufacturer = 1).'),
        pj('papers_not_named_with_cart_manufacturer', 'Papers whose statement is read as naming no CAR-T manufacturer group for the HCP (value 0).'),
        pj('papers_undetermined', 'Papers with no statement, a statement that cannot be read author by author, or the HCP not located (blank value).'),
        pj('disclosed_cart_manufacturer_groups', 'CAR-T manufacturer groups disclosed for the HCP across held papers, semicolon-separated.', 'Blank when none.'),
        pj('papers_with_own_cart_affiliation', 'Papers on which one of the HCP own PubMed affiliations names a CAR-T manufacturer group.'),
        pj('groups_in_both_research_and_disclosure', 'CAR-T manufacturer groups that both pay CAR-T product research naming the HCP and are disclosed for the HCP on a held paper.'),
        pj('inputs', 'Published tables the row is computed from, semicolon-separated, as snapshot/table.'),
        pj('source_release_date', 'Release of the underlying sources: Open Payments publication date and PubMed retrieval date.'),
        pj('retrieved_at_utc', 'Latest UTC retrieval time of the efetch responses behind the row (the 2025 archive download time when the HCP has no held paper).')]


COLUMNS = {OWNERSHIP: ownership_columns, OWNERSHIP_FEATURES: ownership_feature_columns, RESEARCH: research_columns,
           PAPERS: paper_columns, AUTHORS: author_columns, CORE_SUMMARY: core_summary_columns,
           BY_YEAR: lambda: by_year_columns(), DIVISION_YEAR: lambda: division_year_columns(), TIES_BY_YEAR: lambda: ties_year_columns(),
           NEW_PAPERS: lambda: new_paper_columns(), UNIVERSE_AUTHORS: lambda: universe_author_columns(),
           UNIVERSE_SUMMARY: lambda: universe_summary_columns(), CORE_NEW: lambda: core_new_columns(),
           RESEARCH_YEAR: lambda: research_year_columns(), RESEARCH_DIVISION_YEAR: lambda: research_division_columns()}
KEYS = {OWNERSHIP: ['record_id'], OWNERSHIP_FEATURES: ['npi'], RESEARCH: ['npi', 'record_id'], PAPERS: ['pmid'],
        AUTHORS: ['npi', 'pmid'], CORE_SUMMARY: ['npi'], BY_YEAR: ['npi', 'program_year'],
        DIVISION_YEAR: ['division_group', 'program_year'], TIES_BY_YEAR: ['npi', 'publication_year'], NEW_PAPERS: ['pmid'],
        UNIVERSE_AUTHORS: ['npi', 'pmid'], UNIVERSE_SUMMARY: ['npi'], CORE_NEW: ['npi'], RESEARCH_YEAR: ['npi', 'program_year'],
        RESEARCH_DIVISION_YEAR: ['division_group', 'program_year']}
PROVENANCE = {OWNERSHIP: ['source_file', 'source_release_date', 'retrieved_at_utc'],
              OWNERSHIP_FEATURES: ['program_years_scanned', 'source_release_date'],
              RESEARCH: ['source_file', 'source_release_date', 'retrieved_at_utc'],
              PAPERS: ['source_id', 'pubmed_date_revised', 'retrieved_at_utc'],
              AUTHORS: ['source_id', 'pubmed_date_revised', 'retrieved_at_utc'], CORE_SUMMARY: ['inputs', 'source_release_date', 'retrieved_at_utc'],
              BY_YEAR: ['source_file', 'source_release_date', 'retrieved_at_utc'],
              DIVISION_YEAR: ['source_table', 'source_release_date', 'retrieved_at_utc'],
              TIES_BY_YEAR: ['inputs', 'source_release_date', 'retrieved_at_utc'],
              NEW_PAPERS: ['source_id', 'pubmed_date_revised', 'retrieved_at_utc'],
              UNIVERSE_AUTHORS: ['source_id', 'pubmed_date_revised', 'retrieved_at_utc'],
              UNIVERSE_SUMMARY: ['inputs', 'source_release_date', 'retrieved_at_utc'],
              CORE_NEW: ['inputs', 'source_release_date', 'retrieved_at_utc'],
              RESEARCH_YEAR: ['source_table', 'source_release_date', 'retrieved_at_utc'],
              RESEARCH_DIVISION_YEAR: ['source_table', 'source_release_date', 'retrieved_at_utc']}
EARLIER = {  # earlier tables holding entries of the same kind, under the same key (paths relative to Data/processed)
    OWNERSHIP: [f'{CORE_SNAPSHOT}/general_payments_2019_2025.csv', f'{UNIVERSE_PAYMENTS}/universe_cart_general_payments_2019_2025.csv'],
    RESEARCH: [f'{CORE_SNAPSHOT}/research_payments_2019_2025.csv'],
}


def core_rows(core, features, research, links):
    feature = {r['npi']: r for r in features}
    by_research, by_links = defaultdict(list), defaultdict(list)
    for r in research:
        by_research[r['npi']].append(r)
    for r in links:
        by_links[r['npi']].append(r)
    return [core_summary(npi, feature[npi], by_research[npi], by_links[npi]) for npi in sorted(core)]


def general_year_row(npi, year, records):
    """One (NPI, program year) row aggregated from 2026-11-19 row-level CAR-T general records; zero-filled."""
    def part(rows, nature=None, direct=False):
        return [r for r in rows if (nature is None or r['nature_class'] == nature) and (not direct or r['direct_payment'] == '1')]
    out = {'npi': npi, 'program_year': str(year),
           'year_status': 'records_in_year' if records else 'no_records_in_year', 'records': str(len(records)),
           'usd': usd(cents_sum(records, 'amount_usd'))}
    for prefix, flag in YEAR_RULES.items():
        rows = [r for r in records if r[flag] == '1']
        out[f'{prefix}_rows'] = str(len(rows))
        out[f'{prefix}_usd'] = usd(cents_sum(rows, 'amount_usd'))
        if prefix in ('cart_only', 'lymphoma_only'):
            out[f'{prefix}_services_usd'] = usd(cents_sum(part(rows, 'services'), 'amount_usd'))
            out[f'{prefix}_hospitality_education_usd'] = usd(cents_sum(part(rows, 'hospitality_education'), 'amount_usd'))
        if prefix == 'cart_only':
            out['cart_only_direct_services_usd'] = usd(cents_sum(part(rows, 'services', True), 'amount_usd'))
    out['manufacturers'] = str(len({r['manufacturer_id'] for r in records}))
    out.update({'source_table': GENERAL_DETAIL, 'source_file': f'OP_DTL_GNRL_PGYR{year}_P06302026_06032026.csv',
                'source_release_date': RELEASE_DATE, 'retrieved_at_utc': ARCHIVE_RETRIEVED[year]})
    return out


def general_by_year(detail):
    """Seven rows per NPI with at least one record in the 2026-11-19 row-level table."""
    by_key = defaultdict(list)
    for r in detail:
        by_key[r['npi'], r['program_year']].append(r)
    return [general_year_row(npi, year, by_key[npi, str(year)]) for npi in sorted({r['npi'] for r in detail}) for year in YEARS]


def division_year_rows(yearly, divisions):
    """Division group x program year: cart_only services and hospitality dollars and NPIs paid."""
    groups = list(dict.fromkeys(DIVISION_GROUPS.values()))
    sizes = Counter(DIVISION_GROUPS[d] for d in divisions.values())
    out = []
    for group in groups:
        for year in YEARS:
            rows = [r for r in yearly if r['program_year'] == str(year) and DIVISION_GROUPS[divisions[r['npi']]] == group]
            paid = lambda field: str(sum(r[field] != '0.00' for r in rows))
            out.append({'division_group': group, 'program_year': str(year), 'npis_in_division_group': str(sizes[group]),
                        'cart_only_npis_paid': str(sum(r['cart_only_rows'] != '0' for r in rows)),
                        'cart_only_rows': str(sum(int(r['cart_only_rows']) for r in rows)),
                        'cart_only_services_npis_paid': paid('cart_only_services_usd'),
                        'cart_only_services_usd': usd(cents_sum(rows, 'cart_only_services_usd')),
                        'cart_only_direct_services_usd': usd(cents_sum(rows, 'cart_only_direct_services_usd')),
                        'cart_only_hospitality_education_npis_paid': paid('cart_only_hospitality_education_usd'),
                        'cart_only_hospitality_education_usd': usd(cents_sum(rows, 'cart_only_hospitality_education_usd')),
                        'universe_snapshot_date': '2026-10-24', 'source_table': GENERAL_DETAIL,
                        'source_release_date': RELEASE_DATE, 'retrieved_at_utc': ARCHIVE_RETRIEVED[year]})
    return out


def ties_by_year(links, years):
    """(core NPI, publication year) rows from the author-link table, for years with a held paper."""
    by_key = defaultdict(list)
    for r in links:
        by_key[r['npi'], years[r['pmid']]].append(r)
    out = []
    for (npi, year), rows in sorted(by_key.items()):
        row = {'npi': npi, 'publication_year': year, 'held_papers': str(len(rows)),
               'papers_with_coi_statement': str(sum(r['statement_status'] not in ('', 'no_statement') for r in rows)),
               'papers_determinable': str(sum(r['named_with_cart_manufacturer'] != '' for r in rows)),
               'papers_named_with_cart_manufacturer': str(sum(r['named_with_cart_manufacturer'] == '1' for r in rows))}
        for group, label in GROUP_COLUMNS.items():
            row[f'papers_named_with_{label}'] = str(sum(group in r['cart_manufacturers_named_for_author'].split(';') for r in rows))
        row.update({'inputs': f'{SNAPSHOT}/{AUTHORS};2026-10-09/publications', 'source_release_date': f'PubMed as retrieved {SNAPSHOT}',
                    'retrieved_at_utc': max(r['retrieved_at_utc'] for r in rows)})
        out.append(row)
    return out


GROUP_COLUMNS = {'Kite/Gilead': 'kite_gilead', 'Bristol Myers Squibb/Celgene/Juno': 'bms_celgene_juno', 'Novartis': 'novartis',
                 'Janssen/Johnson & Johnson': 'janssen_jnj', 'Legend Biotech': 'legend', 'Autolus': 'autolus',
                 'bluebird bio/2seventy bio': 'bluebird_2seventy'}


def by_year_columns():
    pj = lambda n, d, m=NEVER: column(n, d, m, 'open_payments')
    rule = {'cart_any_slot': 'that name a CAR-T product in at least one product-name field',
            'cart_only': 'on which every named product is a CAR-T product',
            'lymphoma_only': 'on which every named product is a CAR-T product and one is Yescarta, Tecartus, Kymriah or Breyanzi',
            'pure_play_payer': 'paid by Kite Pharma, Legend Biotech or Autolus, whatever the product fields say'}
    cols = [pj('npi', 'Ten-digit National Provider Identifier (NPI) of a universe NPI with at least one record in universe_cart_general_payments_2019_2025.csv (2026-11-19); NPIs with no such record are absent, not zero.'),
            pj('program_year', 'Open Payments program year (2019-2025); every NPI present has all seven years.'),
            pj('year_status', 'records_in_year when the NPI has at least one CAR-T general record in the year, else no_records_in_year (all counts zero).'),
            pj('records', 'CAR-T general records of the NPI in the year in the 2026-11-19 row-level table (any of its rules).'),
            pj('usd', 'Dollars of those records, in whole cents.')]
    for prefix, text in rule.items():
        cols += [pj(f'{prefix}_rows', f'Records of the year {text}.'), pj(f'{prefix}_usd', f'Dollars of the records of the year {text}, in whole cents.')]
        if prefix in ('cart_only', 'lymphoma_only'):
            cols += [pj(f'{prefix}_services_usd', f'Services dollars (consulting, honoraria, speaking and other compensation for services) of the {prefix} records of the year.'),
                     pj(f'{prefix}_hospitality_education_usd', f'Food, travel, lodging, education, gift and entertainment dollars of the {prefix} records of the year.')]
        if prefix == 'cart_only':
            cols.append(pj('cart_only_direct_services_usd', 'Services dollars of the cart_only records of the year paid directly to the physician (no third-party payee).'))
    return cols + [pj('manufacturers', 'Distinct manufacturer identifiers on the NPI CAR-T general records of the year.'),
                   pj('source_table', 'Published table the row aggregates (Data/processed/2026-11-19/universe_cart_general_payments_2019_2025.csv).'),
                   pj('source_file', 'General detail file of the program-year archive the records of the year were read from.'),
                   pj('source_release_date', 'Publication date of the Open Payments archives behind that table (2026-06-30).'),
                   pj('retrieved_at_utc', 'Coordinated Universal Time (UTC) at which that program-year archive was downloaded (2026-11-11 snapshot).')]


def division_year_columns():
    pj = lambda n, d, m=NEVER: column(n, d, m, 'open_payments')
    return [pj('division_group', 'Division group of hcp_divisions.csv (label version 1.0.0): core, affiliates, referring or other.'),
            pj('program_year', 'Open Payments program year (2019-2025).'),
            pj('npis_in_division_group', 'Universe NPIs in the division group.'),
            pj('cart_only_npis_paid', 'NPIs of the group with at least one cart_only record in the year.'),
            pj('cart_only_rows', 'cart_only records of the group in the year.'),
            pj('cart_only_services_npis_paid', 'NPIs of the group with cart_only services dollars in the year.'),
            pj('cart_only_services_usd', 'cart_only services dollars of the group in the year, in whole cents.'),
            pj('cart_only_direct_services_usd', 'Of those, dollars paid directly to the physician.'),
            pj('cart_only_hospitality_education_npis_paid', 'NPIs of the group with cart_only hospitality and education dollars in the year.'),
            pj('cart_only_hospitality_education_usd', 'cart_only food, travel, lodging, education, gift and entertainment dollars of the group in the year.'),
            pj('universe_snapshot_date', 'Snapshot of the universe and division labels (2026-10-24).'),
            pj('source_table', 'Published row-level table aggregated (2026-11-19).'),
            pj('source_release_date', 'Publication date of the Open Payments archives (2026-06-30).'),
            pj('retrieved_at_utc', 'Coordinated Universal Time (UTC) at which the program-year archive was downloaded (2026-11-11 snapshot).')]


def ties_year_columns():
    pj = lambda n, d, m=NEVER: column(n, d, m, 'project')
    cols = [pj('npi', 'Ten-digit National Provider Identifier (NPI) of a core HCP (hcps.csv, 2026-10-09).'),
            pj('publication_year', 'Publication year of the held papers (publications.csv, 2026-10-09); only years in which the HCP has a held paper appear.'),
            pj('held_papers', 'Held CAR-T lymphoma papers of the HCP published in the year (authorships.csv links).'),
            pj('papers_with_coi_statement', 'Of those, papers whose PubMed record carries a conflict-of-interest statement.'),
            pj('papers_determinable', 'Papers whose statement lets the HCP tie be read (named_with_cart_manufacturer is 0 or 1).'),
            pj('papers_named_with_cart_manufacturer', 'Papers on which the statement discloses for the HCP a tie to a CAR-T manufacturer group.')]
    cols += [pj(f'papers_named_with_{label}', f'Papers on which the statement discloses for the HCP a tie to {group}.') for group, label in GROUP_COLUMNS.items()]
    return cols + [pj('inputs', 'Published tables the row is computed from, as snapshot/table.'),
                   pj('source_release_date', 'PubMed retrieval date of the statements.'),
                   pj('retrieved_at_utc', 'Latest UTC retrieval time of the efetch responses behind the row.')]


UNIVERSE_INPUTS = f'{SNAPSHOT}/{OWNERSHIP_FEATURES};{SNAPSHOT}/{RESEARCH};{SNAPSHOT}/{UNIVERSE_AUTHORS}'


def universe_rows(features, research, links, core):
    """One summary row per non-core universe NPI with at least one author link, in the core summary layout."""
    feature = {r['npi']: r for r in features}
    by_research, by_links = defaultdict(list), defaultdict(list)
    for r in research:
        by_research[r['npi']].append(r)
    for r in links:
        if r['npi'] not in core:
            by_links[r['npi']].append(r)
    return [core_summary(npi, feature[npi], by_research[npi], by_links[npi], UNIVERSE_INPUTS) for npi in sorted(by_links)]


def core_new_rows(links, core):
    """One row per core HCP with a link to a paper first published in 2026 (new_authorships.csv)."""
    by_links = defaultdict(list)
    for r in links:
        if r['npi'] in core and r['link_table'] == NEW_AUTHORSHIPS:
            by_links[r['npi']].append(r)
    out = []
    for npi, rows in sorted(by_links.items()):
        disclosed = {g for r in rows for g in r['cart_manufacturers_named_for_author'].split(';') if g}
        count = lambda field, value: str(sum(r[field] == value for r in rows))
        out.append({'npi': npi, 'new_paper_links': str(len(rows)),
                    'new_paper_with_coi_statement': str(sum(r['statement_status'] not in ('', 'no_statement') for r in rows)),
                    'new_paper_named_with_cart_manufacturer': count('named_with_cart_manufacturer', '1'),
                    'new_paper_not_named_with_cart_manufacturer': count('named_with_cart_manufacturer', '0'),
                    'new_paper_undetermined': count('named_with_cart_manufacturer', ''),
                    'new_paper_disclosed_cart_manufacturer_groups': ';'.join(g for g in CART_MANUFACTURERS if g in disclosed),
                    'new_paper_own_cart_affiliation': count('own_affiliation_cart_manufacturer', '1'),
                    'inputs': f'{SNAPSHOT}/{UNIVERSE_AUTHORS}', 'source_release_date': f'PubMed as retrieved {SNAPSHOT}',
                    'retrieved_at_utc': max(r['retrieved_at_utc'] for r in rows)})
    return out


def universe_author_columns():
    return [c for c in author_columns() if c['column'] != 'npi' and c['column'] != 'pmid'][:0] + [
        pm('npi', 'Ten-digit National Provider Identifier (NPI) of a universe NPI (hcp_divisions.csv, 2026-10-24), core or not.'),
        pm('pmid', 'PubMed identifier; (npi, pmid) is a link of universe_authorships.csv or new_authorships.csv (2026-11-20) and the key here; no pair is also in core_author_publication_industry_ties.'),
        pm('link_table', 'Link table the pair comes from: universe_authorships (non-core NPI on a held paper) or new_authorships (core or non-core NPI on a paper first published in 2026).'),
        pm('is_core', '1 when the NPI is one of the 500 core HCPs (hcps.csv, 2026-10-09), else 0.'),
        *[c for c in author_columns() if c['column'] not in ('npi', 'pmid')]]


def new_paper_columns():
    cols = paper_columns()
    cols[0] = pm('pmid', 'PubMed identifier (PMID) of a CAR-T lymphoma paper first published in 2026 (new_publications.csv, 2026-11-20); unique, and never also in publication_industry_disclosures.')
    return cols


def core_new_columns():
    pj = lambda n, d, m=NEVER: column(n, d, m, 'project')
    return [pj('npi', 'Ten-digit NPI of a core HCP (hcps.csv, 2026-10-09) with at least one link to a 2026 paper in new_authorships.csv; core HCPs without one are absent.'),
            pj('new_paper_links', 'Links of the HCP to papers first published in 2026.'),
            pj('new_paper_with_coi_statement', 'Of those, papers whose PubMed record carries a conflict-of-interest statement.'),
            pj('new_paper_named_with_cart_manufacturer', 'Papers whose statement discloses for the HCP a tie to a CAR-T manufacturer group.'),
            pj('new_paper_not_named_with_cart_manufacturer', 'Papers whose statement is read as naming no CAR-T manufacturer group for the HCP.'),
            pj('new_paper_undetermined', 'Papers with no statement, an unreadable statement, or the HCP not located.'),
            pj('new_paper_disclosed_cart_manufacturer_groups', 'CAR-T manufacturer groups disclosed for the HCP on 2026 papers.', 'Blank when none.'),
            pj('new_paper_own_cart_affiliation', 'Papers on which one of the HCP own affiliations names a CAR-T manufacturer group.'),
            pj('inputs', 'Published table the row is computed from.'),
            pj('source_release_date', 'PubMed retrieval date of the statements.'),
            pj('retrieved_at_utc', 'Latest UTC retrieval time of the efetch responses behind the row.')]


def universe_summary_columns():
    cols = core_summary_columns()
    cols[0] = column('npi', 'Ten-digit NPI of a non-core universe NPI with at least one link in universe_author_publication_industry_ties; core HCPs are in core_hcp_industry_ties_summary.', NEVER, 'project')
    for k, c in enumerate(cols):
        if c['column'].startswith('cart_product_research') or c['column'] == 'held_paper_links':
            text = c['description'].replace('from research_payments_2019_2025.csv (2026-11-11)', 'from universe_cart_research_payments_2019_2025.csv (2026-11-20, product-field matches)')
            text = text.replace('Held CAR-T lymphoma papers authored by the HCP (links of authorships.csv, 2026-10-09).',
                                'Papers linked to the NPI in universe_authorships.csv (held papers) or new_authorships.csv (2026 papers).')
            cols[k] = {**c, 'description': text}
    return cols


def research_link(row, core):
    """One published research row (2026-11-11 core table or this snapshot's universe table) in a common shape."""
    if core:
        product = row['cart_product_mentioned'] == '1'
        if not product and row['cart_text_match'] != '1':
            return None
        investigator, lymphoma, source = bool(row['pi_slots']), row['lymphoma_cart_product_mentioned'] == '1', CORE_RESEARCH
        brands = row['cart_products'] if product else ''
        brands = ';'.join(b for b in brands.split(';') if b and b in cart_products(*[n for n in row['products'].split(';')]))
    else:
        product = row['match_basis'] == 'product_field'
        investigator, source = row['role'] == 'investigator', f'{SNAPSHOT}/{RESEARCH}.csv'
        brands = row['cart_products']
        lymphoma = product and bool(LYMPHOMA_BRANDS & set(brands.split(';')))
    return {'npi': row['npi'], 'record_id': row['record_id'], 'program_year': row['program_year'], 'product': product,
            'investigator': investigator, 'sole_pi': investigator and row['record_pi_count'] == '1', 'lymphoma': lymphoma,
            'cents': to_cents(row['amount_usd']), 'brands': brands, 'study': row['study_name'].strip(),
            'nct': row['nct_id'].strip(), 'group': manufacturer_group(row['manufacturer']), 'source': source}


def research_year_row(npi, year, links):
    product = [r for r in links if r['product']]
    groups = {r['group'] for r in links} - {''}
    sources = sorted({r['source'] for r in links})
    return {'npi': npi, 'program_year': str(year), 'year_status': 'records_in_year' if links else 'no_records_in_year',
            'cart_research_rows': str(len(links)), 'product_field_rows': str(len(product)),
            'text_only_rows': str(len(links) - len(product)),
            'product_field_investigator_rows': str(sum(r['investigator'] for r in product)),
            'product_field_recipient_only_rows': str(sum(not r['investigator'] for r in product)),
            'product_field_sole_pi_rows': str(sum(r['sole_pi'] for r in product)),
            'investigator_rows': str(sum(r['investigator'] for r in links)),
            'product_field_record_usd': usd(sum(r['cents'] for r in product)),
            'text_only_record_usd': usd(sum(r['cents'] for r in links if not r['product'])),
            'lymphoma_product_rows': str(sum(r['lymphoma'] for r in product)),
            'product_field_brands': ';'.join(sorted({b for r in product for b in r['brands'].split(';') if b})),
            'studies_named': str(len({r['study'] for r in links if r['study']})),
            'nct_ids': str(len({r['nct'] for r in links if r['nct']})),
            'manufacturer_groups': ';'.join(g for g in CART_MANUFACTURERS if g in groups),
            'source_table': SOURCE_BY_GROUP[npi_group(links)] if links else 'none',
            'source_release_date': RELEASE_DATE, 'retrieved_at_utc': ARCHIVE_RETRIEVED[year]}


SOURCE_BY_GROUP = {'core': CORE_RESEARCH, 'non_core': f'{SNAPSHOT}/{RESEARCH}.csv'}


def npi_group(links):
    return 'core' if links[0]['source'] == CORE_RESEARCH else 'non_core'


def research_by_year(core_rows_, universe_rows_, core):
    """Seven rows per NPI with at least one CAR-T research record: core from 2026-11-11, others from this snapshot."""
    links = [x for x in (research_link(r, True) for r in core_rows_ if r['npi'] in core) if x]
    links += [research_link(r, False) for r in universe_rows_ if r['npi'] not in core]
    by_key, kind = defaultdict(list), {}
    for r in links:
        by_key[r['npi'], r['program_year']].append(r)
        kind[r['npi']] = npi_group([r])
    out = []
    for npi in sorted(kind):
        for year in YEARS:
            row = research_year_row(npi, year, by_key[npi, str(year)])
            row['source_table'] = SOURCE_BY_GROUP[kind[npi]]
            out.append(row)
    return out


def research_division_rows(yearly, divisions):
    groups = list(dict.fromkeys(DIVISION_GROUPS.values()))
    sizes = Counter(DIVISION_GROUPS[d] for d in divisions.values())
    out = []
    for group in groups:
        for year in YEARS:
            rows = [r for r in yearly if r['program_year'] == str(year) and DIVISION_GROUPS[divisions[r['npi']]] == group]
            out.append({'division_group': group, 'program_year': str(year), 'npis_in_division_group': str(sizes[group]),
                        'npis_with_product_field_record': str(sum(r['product_field_rows'] != '0' for r in rows)),
                        'product_field_rows': str(sum(int(r['product_field_rows']) for r in rows)),
                        'product_field_investigator_rows': str(sum(int(r['product_field_investigator_rows']) for r in rows)),
                        'universe_snapshot_date': '2026-10-24',
                        'source_table': f'{SNAPSHOT}/{RESEARCH_YEAR}.csv', 'source_release_date': RELEASE_DATE,
                        'retrieved_at_utc': ARCHIVE_RETRIEVED[year]})
    return out


def research_year_columns():
    o = lambda n, d, m=NEVER: column(n, d, m, 'open_payments')
    return [
        o('npi', 'Ten-digit NPI of a universe NPI (hcp_divisions.csv, 2026-10-24) named on at least one CAR-T research record; NPIs with none are absent, not zero.'),
        o('program_year', 'Open Payments program year (2019-2025); every NPI present has all seven years.'),
        o('year_status', 'records_in_year when the NPI has a CAR-T research record in the year, else no_records_in_year (counts zero).'),
        o('cart_research_rows', 'Research records naming the NPI (as principal investigator or covered recipient) whose product fields, study name or research context name a CAR-T product or generic CAR-T wording.'),
        o('product_field_rows', 'Of those, records whose product fields name a CAR-T product (brand, generic or development name).'),
        o('text_only_rows', 'Of those, records matched only on study name or research context (a text heuristic).'),
        o('product_field_investigator_rows', 'Product-field records with the NPI in a principal-investigator slot.'),
        o('product_field_recipient_only_rows', 'Product-field records naming the NPI only as covered recipient.'),
        o('product_field_sole_pi_rows', 'Product-field records on which the NPI is the only populated principal-investigator slot.'),
        o('investigator_rows', 'Records of either match type with the NPI in a principal-investigator slot.'),
        o('product_field_record_usd', 'Sum of record totals of the product-field records, in US dollars; record totals are repeated across the NPIs of a record and are not personal income.'),
        o('text_only_record_usd', 'Sum of record totals of the text-only records, in US dollars.'),
        o('lymphoma_product_rows', 'Product-field records naming Yescarta, Tecartus, Kymriah or Breyanzi.'),
        o('product_field_brands', 'CAR-T products named in product fields of the year, semicolon-separated.', 'Blank when there is no product-field record.'),
        o('studies_named', 'Distinct published study names on the records of the year; for non-core NPIs a study name withheld by the person-and-address screen (one record) is not counted.'),
        o('nct_ids', 'Distinct ClinicalTrials.gov identifiers reported on the records of the year.'),
        o('manufacturer_groups', 'CAR-T manufacturer groups paying the records of the year.', 'Blank when no payer is in a group.'),
        o('source_table', 'Published row-level table aggregated: 2026-11-11/research_payments_2019_2025.csv for core HCPs, 2026-11-20/universe_cart_research_payments_2019_2025.csv for the others.'),
        o('source_release_date', 'Publication date of the Open Payments archives (2026-06-30).'),
        o('retrieved_at_utc', 'UTC time the program-year archive was downloaded (2026-11-11 snapshot).')]


def research_division_columns():
    o = lambda n, d, m=NEVER: column(n, d, m, 'open_payments')
    return [o('division_group', 'Division group of hcp_divisions.csv (label version 1.0.0): core, affiliates, referring or other.'),
            o('program_year', 'Open Payments program year (2019-2025).'),
            o('npis_in_division_group', 'Universe NPIs in the division group.'),
            o('npis_with_product_field_record', 'NPIs of the group named on at least one product-field CAR-T research record in the year.'),
            o('product_field_rows', 'Product-field CAR-T research records of the group in the year (NPI-record pairs).'),
            o('product_field_investigator_rows', 'Of those, records with the NPI in a principal-investigator slot.'),
            o('universe_snapshot_date', 'Snapshot of the universe and division labels (2026-10-24).'),
            o('source_table', 'Published table aggregated (hcp_universe_cart_research_by_year, 2026-11-20).'),
            o('source_release_date', 'Publication date of the Open Payments archives (2026-06-30).'),
            o('retrieved_at_utc', 'UTC time the program-year archive was downloaded (2026-11-11 snapshot).')]


def research_year_checks(data, name, core):
    """The yearly research table against its inputs and the published per-NPI research features."""
    out = {}
    yearly = data[RESEARCH_YEAR]
    out[name('research_year_recompute')] = int(research_by_year(data['core_research'], data[RESEARCH], core) != yearly)
    divisions = {r['npi']: r['division'] for r in data['universe']}
    out[name('research_year_npi_not_in_universe')] = sum(r['npi'] not in divisions for r in yearly)
    out[name('research_division_year_recompute')] = int(research_division_rows(yearly, divisions) != data[RESEARCH_DIVISION_YEAR])
    sums = defaultdict(Counter)
    brands = defaultdict(set)
    for r in yearly:
        for f in ['product_field_rows', 'product_field_investigator_rows', 'product_field_recipient_only_rows', 'cart_research_rows']:
            sums[r['npi']][f] += int(r[f])
        sums[r['npi']]['product_cents'] += to_cents(r['product_field_record_usd'])
        sums[r['npi']]['all_cents'] += to_cents(r['product_field_record_usd']) + to_cents(r['text_only_record_usd'])
        brands[r['npi']] |= set(filter(None, r['product_field_brands'].split(';')))
    bad = 0
    for f in data['summary']:
        g = sums.get(f['npi'], Counter())
        cents = lambda v: to_cents(v) if v else 0
        bad += g['product_field_rows'] != int(f[f'cart_product_research_rows_{PERIOD}'])
        bad += g['product_field_investigator_rows'] != int(f[f'cart_product_research_investigator_rows_{PERIOD}'])
        bad += g['product_field_recipient_only_rows'] != int(f[f'cart_product_research_recipient_only_rows_{PERIOD}'])
        bad += g['product_cents'] != cents(f[f'cart_product_research_record_usd_{PERIOD}'])
        bad += g['cart_research_rows'] != int(f[f'cart_product_or_text_research_rows_{PERIOD}'])
        bad += g['all_cents'] != cents(f[f'cart_product_or_text_research_record_usd_{PERIOD}'])
        bad += ';'.join(sorted(brands.get(f['npi'], set()))) != f['cart_product_research_brands']
    out[name('research_year_sums_equal_2026_11_19_features')] = bad + len(set(sums) - {f['npi'] for f in data['summary']})
    bad = 0
    for f in data['core_research_features']:
        g = sums.get(f['npi'], Counter())
        bad += g['cart_research_rows'] != int(f['cart_research_rows_2019_2025'])
        bad += g['all_cents'] != (round(float(f['cart_research_record_usd_2019_2025']) * 100) if f['cart_research_record_usd_2019_2025'] else 0)
    out[name('research_year_core_equals_2026_11_11_features')] = bad
    return out


# ---------------------------------------------------------------- headline

def cents_sum(rows, field):
    return sum(to_cents(r[field]) for r in rows if r[field])


def headline(t):
    """Headline numbers recomputed from the published rows (t maps table name to rows read with read_csv)."""
    own, feat, res, papers, links, summary = (t[name] for name in TABLES[:6])
    records = {r['record_id']: r for r in res}
    product = [r for r in res if r['match_basis'] == 'product_field']
    returned = [r for r in papers if r['pubmed_record_status'] == 'returned']
    return {
        'universe_npis': len(feat), 'ownership_rows': len(own), 'ownership_npis': len({r['npi'] for r in own}),
        'ownership_value_of_interest_usd': usd(cents_sum(own, 'value_of_interest_usd')),
        'ownership_amount_invested_usd': usd(cents_sum(own, 'amount_invested_usd')),
        'ownership_family_held_rows': sum(r['interest_holder'] == 'Immediate family member' for r in own),
        'ownership_manufacturers': len({r['manufacturer_id'] for r in own}),
        'cart_manufacturer_ownership_rows': sum(r['cart_manufacturer'] == '1' for r in own),
        'cart_manufacturer_ownership_npis': len({r['npi'] for r in own if r['cart_manufacturer'] == '1'}),
        'research_rows': len(res), 'research_npis': len({r['npi'] for r in res}), 'research_records': len(records),
        'research_record_usd': usd(cents_sum(records.values(), 'amount_usd')),
        'product_field_rows': len(product), 'product_field_npis': len({r['npi'] for r in product}),
        'product_field_records': len({r['record_id'] for r in product}),
        'product_field_record_usd': usd(cents_sum({r['record_id']: r for r in product}.values(), 'amount_usd')),
        'text_only_rows': len(res) - len(product), 'investigator_rows': sum(r['role'] == 'investigator' for r in res),
        'records_with_nct_id': len({r['record_id'] for r in res if r['nct_id']}),
        'study_names_withheld_rows': sum(r['study_name_screen'] == 'failed' for r in res),
        'papers': len(papers), 'papers_returned': len(returned),
        'papers_book_records': sum(r['pubmed_record_type'] == 'book' for r in returned),
        'papers_with_coi_statement': sum(r['coi_statement_present'] == '1' for r in returned),
        'papers_naming_cart_manufacturer': sum(bool(r['coi_cart_manufacturers']) for r in returned),
        'papers_with_industry_coauthorship': sum(r['industry_coauthorship'] == '1' for r in returned),
        'core_author_links': len(links), 'core_author_links_named_with_cart_manufacturer': sum(r['named_with_cart_manufacturer'] == '1' for r in links),
        'core_author_links_not_named_with_cart_manufacturer': sum(r['named_with_cart_manufacturer'] == '0' for r in links),
        'core_author_links_undetermined': sum(r['named_with_cart_manufacturer'] == '' for r in links),
        'core_hcps_named_with_cart_manufacturer': len({r['npi'] for r in links if r['named_with_cart_manufacturer'] == '1'}),
        'core_author_links_with_own_cart_affiliation': sum(r['own_affiliation_cart_manufacturer'] == '1' for r in links),
        'core_hcps_with_cart_product_research_and_disclosed_tie': sum(r[f'cart_product_research_rows_{PERIOD}'] != '0' and r['papers_named_with_cart_manufacturer'] != '0' for r in summary),
        'core_hcps_with_group_in_both': sum(r['groups_in_both_research_and_disclosure'] != '0' for r in summary),
        **yearly_headline(t),
    }


def link_errors(paper, links):
    """Rule violations of author-link rows against their paper rows (paper maps PMID to row)."""
    bad = 0
    for r in links:
        p = paper[r['pmid']]
        status, tie = r['statement_status'], r['named_with_cart_manufacturer']
        bad += (p['pubmed_record_status'] != 'returned') != (status == '')
        bad += (status == 'no_statement') != (p.get('coi_statement_present') == '0' and p['pubmed_record_status'] == 'returned')
        bad += status == 'no_cart_manufacturer' and (tie != '0' or bool(p['coi_cart_manufacturers']))
        bad += status in ('no_statement', 'not_structured') and tie != ''
        bad += status == 'author_not_named' and (tie != '0' or r['named_in_statement'] != '0')
        bad += status == 'author_named' and (r['named_in_statement'] != '1' or tie != str(int(bool(r['cart_manufacturers_named_for_author']))))
        bad += bool(set(filter(None, r['cart_manufacturers_named_for_author'].split(';'))) - set(filter(None, p['coi_cart_manufacturers'].split(';'))))
    return bad


def paper_errors(papers):
    """Blank and count rules of paper rows."""
    bad, groups = 0, set(CART_MANUFACTURERS)
    for r in papers:
        returned, statement = r['pubmed_record_status'] == 'returned', r['coi_statement_present'] == '1'
        for field in ['coi_cart_manufacturer_count', 'coi_no_conflict_wording', 'coi_names_authors']:
            bad += (r[field] == '') == statement
        bad += (r['coi_statement_present'] == '') == returned
        bad += returned and ((r['industry_affiliation_authors'] != '0') != bool(r['industry_affiliation_groups']))
        named = list(filter(None, r['coi_cart_manufacturers'].split(';')))
        bad += statement and r['coi_cart_manufacturer_count'] != str(len(named))
        bad += bool(set(named) - groups) or bool(set(filter(None, r['industry_affiliation_groups'].split(';'))) - groups)
    return bad


def new_link_checks(data, name, core):
    """Checks of the tables built on the publications module's 2026 papers and universe links."""
    out = {}
    held, new = data[PAPERS], data[NEW_PAPERS]
    out[name('paper_tables_overlap')] = len({r['pmid'] for r in held} & {r['pmid'] for r in new})
    out[name('new_papers_not_new_publications')] = int({r['pmid'] for r in new} != {r['pmid'] for r in data[NEW_PUBLICATIONS]})
    out[name('new_paper_rules')] = paper_errors(new)
    links = data[UNIVERSE_AUTHORS]
    expected = {(r['npi'], r['pmid']): UNIVERSE_AUTHORSHIPS for r in data[UNIVERSE_AUTHORSHIPS]}
    expected.update({(r['npi'], r['pmid']): NEW_AUTHORSHIPS for r in data[NEW_AUTHORSHIPS]})
    out[name('author_tables_overlap')] = len({(r['npi'], r['pmid']) for r in links} & {(r['npi'], r['pmid']) for r in data[AUTHORS]})
    out[name('universe_links_not_link_tables')] = int({(r['npi'], r['pmid']): r['link_table'] for r in links} != expected)
    universe_npis = {r['npi'] for r in data['universe']}
    out[name('universe_links_npi_not_in_universe')] = sum(r['npi'] not in universe_npis for r in links)
    out[name('universe_links_is_core')] = sum(r['is_core'] != str(int(r['npi'] in core)) for r in links)
    out[name('universe_links_core_on_held_paper')] = sum(r['link_table'] == UNIVERSE_AUTHORSHIPS and r['npi'] in core for r in links)
    paper = {r['pmid']: r for r in held + new}
    out[name('universe_links_unknown_paper')] = sum(r['pmid'] not in paper for r in links)
    if not out[name('universe_links_unknown_paper')]:
        out[name('universe_link_rows_consistent')] = link_errors(paper, links)
    research = [r for r in data[RESEARCH] if r['match_basis'] == 'product_field']
    out[name('universe_summary_recompute')] = int(universe_rows(data[OWNERSHIP_FEATURES], research, links, core) != data[UNIVERSE_SUMMARY])
    out[name('universe_summary_has_core')] = sum(r['npi'] in core for r in data[UNIVERSE_SUMMARY])
    out[name('core_new_recompute')] = int(core_new_rows(links, core) != data[CORE_NEW])
    return out


def yearly_checks(data, name, core):
    """Checks of the three yearly tables against their published inputs."""
    out = {}
    yearly, division, ties = data[BY_YEAR], data[DIVISION_YEAR], data[TIES_BY_YEAR]
    out[name('by_year_recompute')] = int(general_by_year(data['general_detail']) != yearly)
    divisions = {r['npi']: r['division'] for r in data['universe']}
    out[name('by_year_npi_not_in_universe')] = sum(r['npi'] not in divisions for r in yearly)
    out[name('division_year_recompute')] = int(division_year_rows(yearly, divisions) != division)
    # Summing the yearly rows reproduces the 2026-11-19 seven-year features on every shared column, for every NPI.
    shared = {f'{prefix}_rows': f'{prefix}_rows_{PERIOD}' for prefix in YEAR_RULES}
    shared.update({f'{prefix}_usd': f'{prefix}_usd_{PERIOD}' for prefix in YEAR_RULES})
    shared.update({f'{p}_services_usd': f'{p}_services_usd_{PERIOD}' for p in ('cart_only', 'lymphoma_only')})
    shared['cart_only_direct_services_usd'] = f'cart_only_direct_services_usd_{PERIOD}'
    sums = defaultdict(Counter)
    for r in yearly:
        for mine in shared:
            sums[r['npi']][mine] += int(r[mine]) if mine.endswith('_rows') else to_cents(r[mine])
    bad = 0
    for f in data['general_features']:
        got = sums.get(f['npi'], Counter())
        for mine, theirs in shared.items():
            want = int(f[theirs]) if mine.endswith('_rows') else to_cents(f[theirs])
            bad += got[mine] != want
    out[name('by_year_sums_equal_2026_11_19_features')] = bad + len(set(sums) - {f['npi'] for f in data['general_features']})
    # Core rows under any_slot agree with the 2026-11-11 core table by year.
    mine = {(r['npi'], r['program_year']): r for r in yearly}
    bad = 0
    for r in data['core_by_year']:
        row = mine.get((r['npi'], r['program_year']))
        rows, cents = (int(row['cart_any_slot_rows']), to_cents(row['cart_any_slot_usd'])) if row else (0, 0)
        bad += rows != int(float(r['cart_product_rows'] or 0)) or cents != to_cents(r['cart_product_usd'] or 0)
    out[name('by_year_core_any_slot_equals_2026_11_11')] = bad + sum(
        npi in core and (npi, year) not in {(c['npi'], c['program_year']) for c in data['core_by_year']} and mine[npi, year]['cart_any_slot_rows'] != '0'
        for npi, year in mine)
    years = {r['pmid']: r['publication_year'] for r in data['publications']}
    out[name('ties_by_year_recompute')] = int(ties_by_year(data[AUTHORS], years) != ties)
    totals = defaultdict(Counter)
    for r in ties:
        for field in ['held_papers', 'papers_with_coi_statement', 'papers_determinable', 'papers_named_with_cart_manufacturer']:
            totals[r['npi']][field] += int(r[field])
    bad = 0
    for r in data[CORE_SUMMARY]:
        t = totals.get(r['npi'], Counter())
        bad += t['held_papers'] != int(r['held_paper_links'])
        bad += t['papers_with_coi_statement'] != int(r['papers_with_coi_statement'])
        bad += t['papers_named_with_cart_manufacturer'] != int(r['papers_named_with_cart_manufacturer'])
        bad += t['papers_determinable'] != int(r['papers_named_with_cart_manufacturer']) + int(r['papers_not_named_with_cart_manufacturer'])
    out[name('ties_by_year_sums_equal_core_summary')] = bad
    return out


def yearly_headline(t):
    yearly, division, ties = t[BY_YEAR], t[DIVISION_YEAR], t[TIES_BY_YEAR]
    out = {'by_year_rows': len(yearly), 'by_year_npis': len({r['npi'] for r in yearly}),
           'by_year_rows_with_records': sum(r['year_status'] == 'records_in_year' for r in yearly)}
    for year in YEARS:
        rows = [r for r in yearly if r['program_year'] == str(year)]
        out[f'cart_only_usd_{year}'] = usd(cents_sum(rows, 'cart_only_usd'))
        out[f'cart_only_services_usd_{year}'] = usd(cents_sum(rows, 'cart_only_services_usd'))
        out[f'cart_only_npis_{year}'] = sum(r['cart_only_rows'] != '0' for r in rows)
    out['division_year_rows'] = len(division)
    out['core_cart_only_services_usd_all_years'] = usd(cents_sum([r for r in division if r['division_group'] == 'core'], 'cart_only_services_usd'))
    new, links = t[NEW_PAPERS], t[UNIVERSE_AUTHORS]
    out.update({'new_papers': len(new), 'new_papers_returned': sum(r['pubmed_record_status'] == 'returned' for r in new),
                'new_papers_with_coi_statement': sum(r['coi_statement_present'] == '1' for r in new),
                'new_papers_without_coi_statement': sum(r['coi_statement_present'] == '0' for r in new),
                'new_papers_naming_cart_manufacturer': sum(bool(r['coi_cart_manufacturers']) for r in new),
                'new_papers_with_industry_coauthorship': sum(r['industry_coauthorship'] == '1' for r in new),
                'universe_links': len(links), 'universe_links_new_papers': sum(r['link_table'] == NEW_AUTHORSHIPS for r in links),
                'universe_links_named_with_cart_manufacturer': sum(r['named_with_cart_manufacturer'] == '1' for r in links),
                'universe_links_undetermined': sum(r['named_with_cart_manufacturer'] == '' for r in links),
                'non_core_npis_with_links': len(t[UNIVERSE_SUMMARY]),
                'non_core_npis_named_with_cart_manufacturer': sum(r['papers_named_with_cart_manufacturer'] != '0' for r in t[UNIVERSE_SUMMARY]),
                'core_hcps_with_new_papers': len(t[CORE_NEW]),
                'core_hcps_named_on_new_papers': sum(r['new_paper_named_with_cart_manufacturer'] != '0' for r in t[CORE_NEW])})
    ry = t[RESEARCH_YEAR]
    out.update({'research_year_rows': len(ry), 'research_year_npis': len({r['npi'] for r in ry}),
                'research_year_core_npis': len({r['npi'] for r in ry if r['source_table'] == CORE_RESEARCH})})
    for year in YEARS:
        rows = [r for r in ry if r['program_year'] == str(year)]
        out[f'research_product_field_rows_{year}'] = sum(int(r['product_field_rows']) for r in rows)
        out[f'research_product_field_npis_{year}'] = sum(r['product_field_rows'] != '0' for r in rows)
        out[f'research_product_field_investigator_rows_{year}'] = sum(int(r['product_field_investigator_rows']) for r in rows)
    out.update({'ties_by_year_rows': len(ties), 'ties_by_year_npis': len({r['npi'] for r in ties}),
                'ties_by_year_named_with_cart_manufacturer': sum(int(r['papers_named_with_cart_manufacturer']) for r in ties)})
    return out


# ---------------------------------------------------------------- build

def unchanged(table, rows):
    """True when the table is not yet written or its file equals, byte for byte, what publish would write."""
    path = OUT / f'{table}.csv'
    if not path.exists():
        return True
    public = [c['column'] for c in COLUMNS[table]() if not c['restricted']]
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=public)
    writer.writeheader()
    writer.writerows([{n: r[n] for n in public} for r in rows])
    return buffer.getvalue().encode('utf-8') == path.read_bytes()


def build(rescan=False):
    started = now()
    npis_all = universe()
    core = {r['npi'] for r in read_csv(PROCESSED / '2026-10-09' / 'hcps.csv')}
    non_core = set(npis_all) - core
    summaries = {s['program_year']: s for s in (scan_all() if rescan else [scan_or_extract(y) for y in YEARS])}
    for year in [y for y in YEARS if (ARCHIVES / archive_name(y)).exists()]:  # a rebuild from extracts needs no archive
        meta = load((ARCHIVES / archive_name(year)).with_suffix('.meta.json'))
        log_local(MODULE, f'open_payments_{year}', ARCHIVES / archive_name(year), meta['retrieved_at_utc'], RELEASE_DATE,
                  meta['sha256'], 'Ownership and Research detail files streamed; hash from the 2026-11-11 retrieval record.')
    candidates, dedup, tables = {}, {}, {}
    candidates[OWNERSHIP] = [ownership_row(r, summaries[y]) for y in YEARS for r in read_csv(WORK / f'ownership_rows_{y}.csv')]
    candidates[RESEARCH] = [row for y in YEARS for r in read_csv(WORK / f'research_rows_{y}.csv')
                            for row in research_rows(r, non_core, summaries[y])]
    for table in [OWNERSHIP, RESEARCH]:
        before = existing_keys([PROCESSED / p for p in EARLIER[table]], KEYS[table])
        within, internal = new_rows(candidates[table], KEYS[table], set())
        tables[table], earlier = new_rows(within, KEYS[table], before)
        dedup[table] = {'candidate_rows': len(candidates[table]), 'dropped_already_published': earlier,
                        'dropped_internal_duplicates': internal}
    by_npi = defaultdict(list)
    for row in tables[OWNERSHIP]:
        by_npi[row['npi']].append(row)
    tables[OWNERSHIP_FEATURES] = [ownership_features(npi, by_npi[npi]) for npi in sorted(npis_all)]
    pmids = held_pmids()
    batches = fetch_pubmed(pmids)
    articles, where = {}, {}
    for path, record in batches:
        parsed = parse_articles(path.read_bytes())
        articles.update(parsed)
        where.update({pmid: record for pmid in parsed})
    fallback = {pmid: batches[i // BATCH][1] for i, pmid in enumerate(pmids)}
    tables[PAPERS] = [paper_row(pmid, articles.get(pmid), where.get(pmid, fallback[pmid])) for pmid in pmids]
    links = read_csv(PROCESSED / '2026-10-09' / 'authorships.csv')
    tables[AUTHORS] = [author_row(link, articles.get(link['pmid']), where.get(link['pmid'], fallback[link['pmid']]))
                       for link in sorted(links, key=lambda r: (r['npi'], int(r['pmid'])))]
    new_pmids = sorted({r['pmid'] for r in read_csv(OUT / f'{NEW_PUBLICATIONS}.csv')}, key=int)
    assert not set(new_pmids) & set(pmids), 'A new PMID is also a held PMID'
    new_batches = fetch_pubmed(new_pmids)
    for path, record in new_batches:
        parsed = parse_articles(path.read_bytes())
        articles.update(parsed)
        where.update({pmid: record for pmid in parsed})
    fallback.update({pmid: new_batches[i // BATCH][1] for i, pmid in enumerate(new_pmids)})
    tables[NEW_PAPERS] = [paper_row(pmid, articles.get(pmid), where.get(pmid, fallback[pmid])) for pmid in new_pmids]
    universe_links = [{**r, 'link_table': UNIVERSE_AUTHORSHIPS} for r in read_csv(OUT / f'{UNIVERSE_AUTHORSHIPS}.csv')]
    universe_links += [{**r, 'link_table': NEW_AUTHORSHIPS} for r in read_csv(OUT / f'{NEW_AUTHORSHIPS}.csv')]
    candidates[UNIVERSE_AUTHORS] = [{**author_row(link, articles.get(link['pmid']), where.get(link['pmid'], fallback[link['pmid']])),
                                     'link_table': link['link_table'], 'is_core': str(int(link['npi'] in core))}
                                    for link in sorted(universe_links, key=lambda r: (r['npi'], int(r['pmid'])))]
    within, internal = new_rows(candidates[UNIVERSE_AUTHORS], KEYS[AUTHORS], set())
    tables[UNIVERSE_AUTHORS], earlier = new_rows(within, KEYS[AUTHORS], {(r['npi'], r['pmid']) for r in tables[AUTHORS]})
    dedup[UNIVERSE_AUTHORS] = {'candidate_rows': len(candidates[UNIVERSE_AUTHORS]), 'dropped_already_published': earlier,
                               'dropped_internal_duplicates': internal}
    tables[UNIVERSE_SUMMARY] = universe_rows(tables[OWNERSHIP_FEATURES], [r for r in tables[RESEARCH] if r['match_basis'] == 'product_field'],
                                             tables[UNIVERSE_AUTHORS], core)
    tables[CORE_NEW] = core_new_rows(tables[UNIVERSE_AUTHORS], core)
    tables[CORE_SUMMARY] = core_rows(core, tables[OWNERSHIP_FEATURES], read_csv(PROCESSED / CORE_SNAPSHOT / 'research_payments_2019_2025.csv'),
                                     tables[AUTHORS])
    tables[BY_YEAR] = general_by_year(read_csv(PROCESSED / GENERAL_DETAIL))
    tables[DIVISION_YEAR] = division_year_rows(tables[BY_YEAR], {r['npi']: r['division'] for r in read_csv(UNIVERSE)})
    years = {r['pmid']: r['publication_year'] for r in read_csv(PROCESSED / '2026-10-09' / 'publications.csv')}
    tables[TIES_BY_YEAR] = ties_by_year(tables[AUTHORS], years)
    tables[RESEARCH_YEAR] = research_by_year(read_csv(PROCESSED / CORE_RESEARCH), tables[RESEARCH], core)
    tables[RESEARCH_DIVISION_YEAR] = research_division_rows(tables[RESEARCH_YEAR], {r['npi']: r['division'] for r in read_csv(UNIVERSE)})
    for table in [OWNERSHIP_FEATURES, PAPERS, AUTHORS, CORE_SUMMARY, BY_YEAR, DIVISION_YEAR, TIES_BY_YEAR, NEW_PAPERS, UNIVERSE_SUMMARY, CORE_NEW,
                  RESEARCH_YEAR, RESEARCH_DIVISION_YEAR]:
        kept, internal = new_rows(tables[table], KEYS[table], set())
        dedup[table] = {'candidate_rows': len(tables[table]), 'dropped_already_published': 0, 'dropped_internal_duplicates': internal}
        tables[table] = kept
    tables[OWNERSHIP].sort(key=lambda r: (r['npi'], r['program_year'], r['record_id']))
    tables[RESEARCH].sort(key=lambda r: (r['npi'], r['program_year'], r['record_id']))
    for t in FROZEN_TABLES:  # tables of earlier passes must not change by a byte
        assert unchanged(t, tables[t]), f'{t}: a rebuild would change a published table'
    summaries_out = [publish(t, tables[t], COLUMNS[t](), KEYS[t], PROVENANCE[t]) for t in TABLES]
    public = {t: read_csv(OUT / f'{t}.csv') for t in TABLES}
    head = headline(public)
    groups_seen = defaultdict(set)
    for name in {r['applicable_manufacturer_or_applicable_gpo_making_payment_name'] for y in YEARS
                 for r in read_csv(WORK / f'research_rows_{y}.csv')} | {r['manufacturer'] for r in tables[OWNERSHIP]}:
        if manufacturer_group(name):
            groups_seen[manufacturer_group(name)].add(name)
    log = request_log(MODULE)
    web = [r for r in log if r['method'] == 'GET']
    write_module_metadata(MODULE, {
        'tables': summaries_out, 'headline': head, 'dedup': dedup,
        'sources': [
            {'name': 'CMS Open Payments program-year archives 2019-2025, Ownership and Research detail files',
             'publisher': 'Centers for Medicare & Medicaid Services (CMS)', 'url': 'https://download.cms.gov/openpayments/',
             'release_or_version': f'publication {RELEASE_DATE}', 'retrieved_from_utc': min(s['retrieved_at_utc'] for s in summaries.values()),
             'retrieved_to_utc': max(s['retrieved_at_utc'] for s in summaries.values()),
             'how_obtained': 'Archives on disk from the 2026-11-11 snapshot, streamed locally; no new download.',
             'requests': 0, 'local_inputs': len(YEARS), 'terms_class': 'US federal government public data',
             'scans': {y: {k: s.get(k) for k in ['members', 'ownership_rows_scanned', 'ownership_rows_kept',
                                                 'research_rows_scanned', 'research_rows_kept']} for y, s in summaries.items()}},
            {'name': 'PubMed records of the 3,363 held PMIDs and the 699 PMIDs of new_publications.csv (NCBI E-utilities efetch, XML)', 'publisher': 'National Library of Medicine (NLM), NCBI',
             'url': EFETCH.split('?')[0], 'release_or_version': 'Live PubMed records; DateRevised per paper in the table',
             'retrieved_from_utc': min(r['retrieved_at_utc'] for r in web), 'retrieved_to_utc': max(r['retrieved_at_utc'] for r in web),
             'how_obtained': f'efetch GET in batches of {BATCH} PMIDs (17 held, 4 new) plus one re-request of PMIDs first missing, at most two requests a second, frozen by fetch().',
             'requests': len(web), 'terms_class': 'NLM PubMed data, free reuse; abstracts and statements may be publisher copyright (statement text kept local)'}],
        'cart_manufacturer_patterns': CART_MANUFACTURERS,
        'cms_manufacturer_names_matched': {g: sorted(v) for g, v in sorted(groups_seen.items())},
        'ownership_cart_manufacturer_names_all_physicians': {y: s.get('ownership_cart_manufacturer_names_all_physicians', {})
                                                             for y, s in sorted(summaries.items())},
        'study_name_screen': SCREEN.pattern + ' ; or any first or last name (3+ letters) of an investigator or covered recipient on the record',
        'statement_parsing_rule': author_tie.__doc__.strip() + ' ' + author_segments.__doc__.strip(),
        'statement_parsing_precision': PRECISION, 'statement_parsing_recall': RECALL,
        'fingerprints': {t: fingerprint(public[t], KEYS[t]) for t in TABLES},
        'limitations': LIMITATIONS, 'defects_open': DEFECTS,
    })
    print(MODULE.upper(), 'BUILT', {t: s['rows'] for t, s in zip(TABLES, summaries_out)}, 'started', started, flush=True)
    return head


PRECISION = {
    'measure': ('Precision of named_with_cart_manufacturer = 1: share of positive (core NPI, PMID) rows whose passage, read by '
                'hand, is the author own disclosure and names a manufacturer of the listed groups.'),
    'final_rule': {'draw': 'random.seed(5), 30 of all positive rows under the second-pass rule', 'checked': 30, 'correct': 30,
                   'precision': 1.0},
    'changed_rows': {'draw': 'random.seed(31), 30 of the positive rows whose value or groups changed with the second-pass rule',
                     'checked': 30, 'correct': 29,
                     'error': 'a passage ran past a co-author tagged by the last word of a compound surname; fixed by matching that word, after which the row is correct',
                     'corrected_by_rule': 'two first-pass positives became 0, both first-pass errors (an initials collision, and a passage running into the next author)'},
    'new_rows': {'draw': 'random.seed(1121), 20 of the positive rows of universe_author_publication_industry_ties (held papers of non-core NPIs and 2026 papers)',
                 'checked': 20, 'correct': 20, 'precision': 1.0,
                 'note': 'The 2026 statements use the same initials-and-verb layouts; no new format failed.'},
    'first_pass_rounds': [
        {'rule': 'sentence-only passages', 'checked': 20, 'correct': 18},
        {'rule': 'sentence plus next-author cut', 'checked': 30, 'correct': 30},
        {'rule': 'next-author cut with general-wording stops', 'checked': 30, 'correct': 29},
        {'rule': 'first-pass final rule, random.seed(4242)', 'checked': 30, 'correct': 30}],
}
RECALL = {
    'measure': ('Share of author links with value 0 or blank and a statement present (pool of about 1,450-1,490) that, read by hand, '
                'really disclose a CAR-T manufacturer tie for that author.'),
    'random_samples': [
        {'rule': 'first-pass rule', 'draw': 'random.seed(20261120), 40 links', 'missed_ties': 0},
        {'rule': 'second-pass rule', 'draw': 'random.seed(77), 40 links', 'missed_ties': 1,
         'causes': {'initials_collision': 1, 'surname_only': 0, 'misspelling': 0, 'company_alias': 0, 'initials_format': 0}}],
    'targeted_scan': {'rule': 'first-pass rule', 'method': 'every pool link whose surname appears within 250 characters before a group name, plus every author passage holding Legend alone, Jansen or Jansssen; candidates read by hand',
                      'candidates': 38, 'by_cause': {'surname_only_or_single_initial': 35, 'company_alias_legend_alone': 3},
                      'after_second_pass_rule': {'candidates_left': 2, 'detail': 'one surname that sits inside a company name (not a tie of that author), one paper with two authors of the same surname'}},
    'estimate': 'About 1 missed tie in 40 links with value 0 or blank after the second pass (random sample), mostly from authors who share initials; before the fix, the targeted scan found about 2.5% of the pool.',
}
LIMITATIONS = [
    'Ownership and investment interests are reported by manufacturers and GPOs for physicians only; values are those reported for each program year, so a holding reported in several years is counted once per year in sums.',
    'No universe NPI holds a reported ownership interest in a listed CAR-T manufacturer in 2019-2025; most universe records are interests in oncology group purchasing organizations.',
    'Research rows cover non-core universe NPIs; the core HCPs records are in research_payments_2019_2025.csv of 2026-11-11 and are not repeated.',
    'match_basis study_or_context_text is a text heuristic over the study name and research context (context text itself is not published) and does not confirm the trial intervention.',
    'Study names are screened by rule for person names on the record, honorifics, e-mail addresses, telephone numbers and street addresses; a name of a person not on the record would pass.',
    'amount_usd is the record total, repeated on every NPI row of a multi-investigator record; it is not personal income and is not additive across NPIs.',
    'PubMed carries a conflict-of-interest statement only where the publisher deposited one; a missing statement is not evidence of no ties.',
    'CAR-T manufacturer names are matched by pattern; Gilead and Novartis and Johnson & Johnson and Bristol Myers Squibb make many non-CAR-T products, so a match is a tie to the company, not to a CAR-T product.',
    'Author matching in statements uses full names, first name and surname, honorific and surname, initials with surname, and initials; authors sharing initials on one paper cannot be told apart, a surname alone or a misspelled name is not found, and value 0 can therefore hide a tie.',
    'Manufacturer group patterns match "Legend" (capitalised) and "Janssen" with any number of s, but not other misspellings such as "Jannsen", so groups named for an author are a floor.',
    'Authors sharing initials, or a surname, on one paper cannot be told apart; the earlier author in the PubMed list takes the mention (about 1 missed tie in 40 value-0 or blank links).',
    'hcp_universe_cart_general_payments_by_year aggregates universe_cart_general_payments_2019_2025 (2026-11-19) only: an NPI absent from that table has no row (not zero rows), and the yearly values cover the same records as the 2026-11-19 seven-year features.',
    'hcp_general_payments_by_year (2026-11-11) shares the key (npi, program_year) for core HCPs but holds all general payments and the looser any-slot CAR-T rule; the new yearly table adds the strict rules and is checked to agree with it on any-slot rows and dollars.',
    'hcp_universe_cart_research_by_year takes core HCPs from research_payments_2019_2025 (2026-11-11) and other NPIs from universe_cart_research_payments_2019_2025 (2026-11-20). Both apply the same match (principal-investigator slot or covered recipient), product rule, text heuristic, lymphoma rule and sole-PI rule; the core table cart_products column also holds text-matched brands, so product_field_brands is read from the core product names instead. studies_named is not fully comparable: one non-core study name withheld by the screen is not counted.',
        'core_hcp_disclosed_ties_by_year uses the publication year of publications.csv; a statement can describe ties of an earlier period than its year.',
]
DEFECTS = ['efetch returned no record for 9 held PMIDs (34700459-34700463, 34701125, 34701139, 34701146, 34701153: 2021 meeting abstracts indexed in Europe PMC); their rows are not_returned with blank flags.']


# ---------------------------------------------------------------- check

# Headline numbers of the published tables (2026-06-30 Open Payments publication, PubMed as retrieved 2026-11-20).
HEADLINE = {
    'universe_npis': 17510, 'ownership_rows': 606, 'ownership_npis': 86,
    'ownership_value_of_interest_usd': '20526319.30', 'ownership_amount_invested_usd': '4922141.38',
    'ownership_family_held_rows': 14, 'ownership_manufacturers': 16, 'cart_manufacturer_ownership_rows': 0,
    'cart_manufacturer_ownership_npis': 0, 'research_rows': 8023, 'research_npis': 429, 'research_records': 7973,
    'research_record_usd': '234135105.72', 'product_field_rows': 4296, 'product_field_npis': 239,
    'product_field_records': 4272, 'product_field_record_usd': '74950073.25', 'text_only_rows': 3727,
    'investigator_rows': 7954, 'records_with_nct_id': 194, 'study_names_withheld_rows': 1, 'papers': 3363,
    'papers_returned': 3354, 'papers_book_records': 37, 'papers_with_coi_statement': 1922,
    'papers_naming_cart_manufacturer': 783, 'papers_with_industry_coauthorship': 241, 'core_author_links': 4991,
    'core_author_links_named_with_cart_manufacturer': 1487,
    'core_author_links_not_named_with_cart_manufacturer': 1434, 'core_author_links_undetermined': 2070,
    'core_hcps_named_with_cart_manufacturer': 261, 'core_author_links_with_own_cart_affiliation': 25,
    'core_hcps_with_cart_product_research_and_disclosed_tie': 139, 'core_hcps_with_group_in_both': 130,
    'by_year_rows': 39781, 'by_year_npis': 5683, 'by_year_rows_with_records': 13173,
    'cart_only_usd_2019': '2411044.25', 'cart_only_services_usd_2019': '1898221.85', 'cart_only_npis_2019': 721,
    'cart_only_usd_2020': '1161493.98', 'cart_only_services_usd_2020': '1054827.51', 'cart_only_npis_2020': 764,
    'cart_only_usd_2021': '1346547.88', 'cart_only_services_usd_2021': '1287643.34', 'cart_only_npis_2021': 752,
    'cart_only_usd_2022': '2115356.41', 'cart_only_services_usd_2022': '1856059.73', 'cart_only_npis_2022': 469,
    'cart_only_usd_2023': '1537671.76', 'cart_only_services_usd_2023': '1310917.50', 'cart_only_npis_2023': 1361,
    'cart_only_usd_2024': '1707096.86', 'cart_only_services_usd_2024': '1464353.31', 'cart_only_npis_2024': 1839,
    'cart_only_usd_2025': '2936538.22', 'cart_only_services_usd_2025': '2468475.39', 'cart_only_npis_2025': 2887,
    'division_year_rows': 28, 'core_cart_only_services_usd_all_years': '5321023.30', 'new_papers': 699,
    'new_papers_returned': 699, 'new_papers_with_coi_statement': 471, 'new_papers_without_coi_statement': 228,
    'new_papers_naming_cart_manufacturer': 177, 'new_papers_with_industry_coauthorship': 46, 'universe_links': 1993,
    'universe_links_new_papers': 1138, 'universe_links_named_with_cart_manufacturer': 432,
    'universe_links_undetermined': 820, 'non_core_npis_with_links': 618,
    'non_core_npis_named_with_cart_manufacturer': 105, 'core_hcps_with_new_papers': 292,
    'core_hcps_named_on_new_papers': 131, 'research_year_rows': 4732, 'research_year_npis': 676,
    'research_year_core_npis': 247, 'research_product_field_rows_2019': 628, 'research_product_field_npis_2019': 143,
    'research_product_field_investigator_rows_2019': 527, 'research_product_field_rows_2020': 1208,
    'research_product_field_npis_2020': 127, 'research_product_field_investigator_rows_2020': 1186,
    'research_product_field_rows_2021': 1419, 'research_product_field_npis_2021': 178,
    'research_product_field_investigator_rows_2021': 1402, 'research_product_field_rows_2022': 1602,
    'research_product_field_npis_2022': 218, 'research_product_field_investigator_rows_2022': 1558,
    'research_product_field_rows_2023': 2021, 'research_product_field_npis_2023': 208,
    'research_product_field_investigator_rows_2023': 1982, 'research_product_field_rows_2024': 2325,
    'research_product_field_npis_2024': 245, 'research_product_field_investigator_rows_2024': 2292,
    'research_product_field_rows_2025': 2384, 'research_product_field_npis_2025': 281,
    'research_product_field_investigator_rows_2025': 2354, 'ties_by_year_rows': 1996, 'ties_by_year_npis': 500,
    'ties_by_year_named_with_cart_manufacturer': 1487,
}


# sha256 of every cell of each published table (fingerprint of volume_billing.industry_payments). After a rebuild on new
# inputs, replace with the 'fingerprints' object of the module metadata.
FINGERPRINTS = {
    'universe_ownership_interests_2019_2025': 'dfd6996944f774f321cc050eb7360ec111e08bc9942245c4fe3b1cda2b582932',
    'hcp_universe_ownership_features': '2427853beab015dd99e3a2d99574fb46eb990f5b45773c15545c94b5eba8edb1',
    'universe_cart_research_payments_2019_2025': 'd58c782c4bf5c0f9214889a340a7c93d215cb37f1722cf196e7a2c56788bd4c1',
    'publication_industry_disclosures': '2c98147dd7bbdb0f7122c5949b7f23d411af05d8e094ec386b0d7f01595cbd33',
    'core_author_publication_industry_ties': 'e329152399f77d23e4118abb2996513094ee92b854ab04542c3746f4ef5a8612',
    'core_hcp_industry_ties_summary': 'abb8a41a958d5e1d00db2320e66e665edba6920b9d43602a43597d5775a0477e',
    'hcp_universe_cart_general_payments_by_year': '00cbd1740af4fe803a417b6f1c74ea1453e3b9ec8836d03e06f121022f30abc3',
    'cart_general_payments_by_division_year': 'af40aba2ee12eb34720c14e6f7f58c9c102451c6d8f819fb0e154f971989cd68',
    'core_hcp_disclosed_ties_by_year': '859afd3fc81e54d033f2a6052763c81952b22a3db36119ea0878d5b81eeedaf7',
    'new_publication_industry_disclosures': '31f0a5a42501927085b03c7a380c8c1c9280bfcd0f485a9a8c5858bd58138ac8',
    'universe_author_publication_industry_ties': '273f7d4f87ff01323d2bcb94ce06e2ca69b17fd895c33fadb4483bcf581a8bc4',
    'universe_hcp_industry_ties_summary': '41bb72118fcf662eb0ca4b5458d6449674be01d49942d207607bc86a5a585464',
    'core_hcp_new_paper_ties': 'b9c7bf2b335a250f4ef5c7c432e42e69a09270c4e92b752f0196cc2623319797',
    'hcp_universe_cart_research_by_year': 'eca98cc8b4e40f06474e4b172e8cc3d303d988a04ebfc40b15de05940fde075f',
    'cart_research_by_division_year': '3e183b7ae59ef195bd436524db48504d4690a733a155c453b3a5b100879bd77e',
}


def check(processed):
    """Error counts for the published tables, read only from a Data/processed directory; zero means pass."""
    name = lambda label: f'industry_{label}'
    here = processed / SNAPSHOT
    needed = {t: here / f'{t}.csv' for t in TABLES}
    needed.update({'universe': processed / '2026-10-24' / 'hcp_divisions.csv', 'core': processed / '2026-10-09' / 'hcps.csv',
                   'publications': processed / '2026-10-09' / 'publications.csv',
                   'authorships': processed / '2026-10-09' / 'authorships.csv',
                   'summary': processed / UNIVERSE_PAYMENTS / 'hcp_universe_research_payment_features.csv',
                   'core_research': processed / CORE_SNAPSHOT / 'research_payments_2019_2025.csv',
                   NEW_PUBLICATIONS: here / f'{NEW_PUBLICATIONS}.csv', NEW_AUTHORSHIPS: here / f'{NEW_AUTHORSHIPS}.csv',
                   UNIVERSE_AUTHORSHIPS: here / f'{UNIVERSE_AUTHORSHIPS}.csv',
                   'general_detail': processed / GENERAL_DETAIL, 'general_features': processed / GENERAL_FEATURES,
                   'core_by_year': processed / CORE_BY_YEAR,
                   'core_research_features': processed / CORE_RESEARCH_FEATURES,
                   **{p: processed / p for t in EARLIER for p in EARLIER[t]}})
    out = {name('files_present'): sum(not path.is_file() for path in needed.values())}
    if out[name('files_present')]:
        return out
    data = {key: read_csv(path) for key, path in needed.items()}
    own, feat, res, papers, links, summary_rows = (data[t] for t in TABLES[:6])
    universe_npis = {r['npi'] for r in data['universe']}
    core = {r['npi'] for r in data['core']}

    for t in TABLES:
        public = [c['column'] for c in COLUMNS[t]() if not c['restricted']]
        out[name(f'{t}_columns')] = int(list(data[t][0].keys()) != public) if data[t] else 1
        keys = [tuple(r[k] for k in KEYS[t]) for r in data[t]]
        out[name(f'{t}_duplicate_keys')] = len(keys) - len(set(keys))
        out[name(f'{t}_blank_keys')] = sum(not all(k) for k in keys)
        out[name(f'{t}_blank_provenance')] = sum(not r[c] for r in data[t] for c in PROVENANCE[t])
    for t, paths in EARLIER.items():
        before = {tuple(r[k] for k in KEYS[t]) for p in paths for r in data[p]}
        out[name(f'{t}_overlaps_earlier_snapshot')] = sum(tuple(r[k] for k in KEYS[t]) in before for r in data[t])

    # Joins.
    out[name('ownership_npi_not_in_universe')] = sum(r['npi'] not in universe_npis for r in own)
    out[name('ownership_features_not_universe')] = int({r['npi'] for r in feat} != universe_npis) + len(feat) - len(universe_npis)
    out[name('research_npi_not_in_universe')] = sum(r['npi'] not in universe_npis for r in res)
    out[name('research_npi_is_core')] = sum(r['npi'] in core for r in res)
    out[name('papers_not_held_set')] = int({r['pmid'] for r in papers} != {r['pmid'] for r in data['publications']})
    link_keys = {(r['npi'], r['pmid']) for r in data['authorships']}
    out[name('author_links_not_authorships')] = int({(r['npi'], r['pmid']) for r in links} != link_keys)
    out[name('author_npi_not_core')] = sum(r['npi'] not in core for r in links)

    # Value rules.
    groups = set(CART_MANUFACTURERS)
    out[name('ownership_flag_rule')] = sum(r['cart_manufacturer'] != str(int(bool(manufacturer_group(r['manufacturer']))))
                                           or r['cart_manufacturer_group'] != manufacturer_group(r['manufacturer']) for r in own)
    out[name('ownership_year_range')] = sum(not (YEARS[0] <= int(r['program_year']) <= YEARS[-1]) for r in own)
    out[name('research_product_rule')] = sum(r['cart_products'] != ';'.join(cart_products(*r['cart_products'].split(';')))
                                             or r['cart_product_mentioned'] != str(int(bool(r['cart_products'])))
                                             or r['match_basis'] != ('product_field' if r['cart_products'] else 'study_or_context_text')
                                             for r in res)
    out[name('research_role_rule')] = sum((r['role'] == 'investigator') != bool(r['pi_slots']) or
                                          r['role'] not in ('investigator', 'recipient_only') for r in res)
    out[name('research_study_screen')] = sum((r['study_name_screen'] != 'passed') != (r['study_name'] == '') or
                                             bool(r['study_name'] and SCREEN.search(r['study_name'])) for r in res)
    record_values = defaultdict(set)
    for r in res:
        record_values[r['record_id']].add((r['amount_usd'], r['manufacturer_id'], r['program_year'], r['cart_products']))
    out[name('research_record_inconsistent')] = sum(len(v) > 1 for v in record_values.values())
    out[name('paper_group_values')] = sum(bool(set(filter(None, r['coi_cart_manufacturers'].split(';'))) - groups) or
                                          bool(set(filter(None, r['industry_affiliation_groups'].split(';'))) - groups)
                                          for r in papers)
    out[name('paper_counts_consistent')] = sum(r['pubmed_record_status'] == 'returned' and r['coi_statement_present'] == '1'
                                               and r['coi_cart_manufacturer_count'] != str(len(list(filter(None, r['coi_cart_manufacturers'].split(';')))))
                                               for r in papers) + \
        sum(r['industry_coauthorship'] != ('' if r['pubmed_record_status'] != 'returned' else str(int(r['industry_affiliation_authors'] not in ('', '0'))))
            for r in papers)
    paper = {r['pmid']: r for r in papers}
    bad = 0
    for r in links:
        p = paper[r['pmid']]
        status, tie = r['statement_status'], r['named_with_cart_manufacturer']
        bad += (p['pubmed_record_status'] != 'returned') != (status == '')
        bad += (status == 'no_statement') != (p.get('coi_statement_present') == '0' and p['pubmed_record_status'] == 'returned')
        bad += status == 'no_cart_manufacturer' and (tie != '0' or bool(p['coi_cart_manufacturers']))
        bad += status in ('no_statement', 'not_structured') and tie != ''
        bad += status == 'author_not_named' and (tie != '0' or r['named_in_statement'] != '0')
        bad += status == 'author_named' and (r['named_in_statement'] != '1' or tie != str(int(bool(r['cart_manufacturers_named_for_author']))))
        bad += bool(set(filter(None, r['cart_manufacturers_named_for_author'].split(';'))) - set(filter(None, p['coi_cart_manufacturers'].split(';'))))
    out[name('author_rows_consistent')] = bad

    # Feature table equals recomputation from the row-level ownership table.
    by_npi = defaultdict(list)
    for r in own:
        by_npi[r['npi']].append(r)
    out[name('ownership_features_recompute')] = sum(ownership_features(r['npi'], by_npi[r['npi']]) != r for r in feat)

    out[name('core_summary_recompute')] = int(core_rows(core, feat, data['core_research'], links) != summary_rows)

    out.update(yearly_checks(data, name, core))
    out.update(new_link_checks(data, name, core))
    out.update(research_year_checks(data, name, core))

    # Research rows reconcile with the published per-NPI summaries of 2026-11-19 for every non-core NPI.
    summary = {r['npi']: r for r in data['summary']}
    mine = defaultdict(list)
    for r in res:
        mine[r['npi']].append(r)
    bad = 0
    for npi, s in summary.items():
        if npi in core:
            continue
        rows = mine.get(npi, [])
        product = [r for r in rows if r['match_basis'] == 'product_field']
        total = lambda part: usd(cents_sum(part, 'amount_usd')) if part else ''
        bad += s[f'cart_product_research_rows_{PERIOD}'] != str(len(product))
        bad += s[f'cart_product_research_investigator_rows_{PERIOD}'] != str(sum(r['role'] == 'investigator' for r in product))
        bad += s[f'cart_product_research_recipient_only_rows_{PERIOD}'] != str(sum(r['role'] == 'recipient_only' for r in product))
        bad += s[f'cart_product_research_record_usd_{PERIOD}'] != total(product)
        bad += s['cart_product_research_brands'] != ';'.join(sorted({b for r in product for b in r['cart_products'].split(';')}))
        bad += s[f'cart_product_or_text_research_rows_{PERIOD}'] != str(len(rows))
        bad += s[f'cart_product_or_text_research_record_usd_{PERIOD}'] != total(rows)
    out[name('research_reconciles_with_2026_11_19_summary')] = bad + len(set(mine) - set(summary))

    # Blank rules of the paper table, and affiliation groups agree with the author rows.
    bad = 0
    for r in papers:
        returned, statement = r['pubmed_record_status'] == 'returned', r['coi_statement_present'] == '1'
        for field in ['coi_cart_manufacturer_count', 'coi_no_conflict_wording', 'coi_names_authors']:
            bad += (r[field] == '') == statement
        bad += (r['coi_statement_present'] == '') == returned
        bad += returned and ((r['industry_affiliation_authors'] != '0') != bool(r['industry_affiliation_groups']))
    own_groups = defaultdict(int)
    for r in links:
        own_groups[r['pmid']] += r['own_affiliation_cart_manufacturer'] == '1'
    bad += sum(own_groups[r['pmid']] > int(r['industry_affiliation_authors'] or 0) for r in papers)
    out[name('paper_blank_rules')] = bad

    # sha256 over every cell of each table: source values that cannot be recomputed from other published tables
    # (manufacturer names, study names, ClinicalTrials.gov ids, statement-derived flags) are pinned this way.
    for t in TABLES:
        out[name(f'{t}_fingerprint')] = int(fingerprint(data[t], KEYS[t]) != FINGERPRINTS.get(t))

    # Every headline total.
    recomputed = headline(data)
    for key, value in HEADLINE.items():
        out[name(f'headline_{key}')] = int(str(recomputed.get(key)) != str(value))
    out[name('headline_complete')] = len(set(recomputed) ^ set(HEADLINE))
    return out


if __name__ == '__main__':
    build()
