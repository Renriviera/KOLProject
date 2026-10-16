"""Source inventories, field dictionary and immutable-file checksums."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from .common import ROOT, RAW, PROCESSED, INTERIM, load, dump, write_csv, read_csv, now
from .corpus import SUPPLEMENT_QUERY

DESCRIPTIONS = {
    'hcp_id': 'Stable professional identity key: NPI followed by the ten-digit individual NPI.',
    'npi': 'Verified US individual National Provider Identifier; keep as a string.',
    'legacy_hcp_id': 'Previous HCPnnn identifier, when this identity was in the older cohort.',
    'pmid': 'PubMed publication identifier; keep as a string.',
    'doi': 'Digital object identifier supplied by Europe PMC, when present.',
    'display_name': 'Full registered NPI first, middle and family name; not inferred from social media.',
    'supporting_pmids': 'Distinct eligible, identity-supported PMIDs that satisfy cohort inclusion.',
    'publications_2017_2025': 'Count of distinct accepted eligible PMIDs, excluding correction/retraction records and the holdout guideline.',
    'npi_status': 'Registry administrative status; A means active. Not a clinical credential verification.',
    'npi_city': 'City of the primary registry practice address at observation; may be stale.',
    'npi_state': 'State of the primary registry practice address at observation; not necessarily current employment.',
    'qualifying_taxonomy_codes': 'Intersection between the NPI taxonomy list and the four allowed hematology/oncology codes.',
    'npi_taxonomy_codes': 'All taxonomy codes on the observed NPI record, including nonqualifying codes.',
    'npi_specialties': 'Registry taxonomy descriptions as observed.',
    'npi_last_updated_date': 'Date reported by NPPES for the registry record update; distinct from retrieval time.',
    'npi_retrieved_at_utc': 'Actual current API observation timestamp in UTC, ISO 8601 with Z.',
    'npi_source_id': 'Stable source-request key linking to the raw response metadata and source manifest.',
    'first_publication_date': 'First publication date supplied by Europe PMC; separate from metadata observation date.',
    'publication_date': 'First publication date of the linked PubMed record.',
    'citation_count_epmc': 'Europe PMC citation count at this record’s dated observation; not a historical citation trajectory.',
    'citation_observed_on': 'Calendar date on which the citation metadata was observed.',
    'citation_year_percentile': 'Midrank citation percentile within the eligible collected corpus for the publication year.',
    'mean_citation_year_percentile': 'Mean within-year citation percentile across an HCP’s accepted eligible publications.',
    'metadata_retrieved_on': 'Actual collection date when day precision is available; exact timestamp remains blank for the legacy corpus.',
    'metadata_retrieved_at_utc': 'Exact UTC metadata retrieval time for new API requests; blank for date-only legacy observations.',
    'retrieval_time_precision': 'Precision of the stored source observation: day for legacy data or second for new requests.',
    'query_family': 'Original title-constrained search or separately recorded CAR-T-title/lymphoma-abstract supplement.',
    'analysis_include': '1 if eligible for qualification and features; 0 for excluded publication types or the validation holdout.',
    'exclusion_reason': 'Reason a publication is excluded from qualification and graph/score features.',
    'author_name_as_indexed': 'Name as indexed on this paper; may differ from the NPI registered name.',
    'author_position': 'One-based author-list position in the indexed publication.',
    'author_count': 'All indexed authors on the paper, not only selected cohort members.',
    'orcid': 'ORCID associated with this indexed author mention, if supplied.',
    'orcid_ids': 'ORCIDs observed among accepted eligible author mentions; not assumed complete.',
    'affiliation_at_publication': 'Dated historical indexed affiliation, with email text removed from analytical exports.',
    'match_rule': 'Exact-name/middle-initial city evidence or an ORCID anchored by that evidence.',
    'latest_publication_affiliation': 'Affiliation in the latest linked paper; not a verified current employment claim.',
    'affiliation_evidence_date': 'Publication date supporting the historical affiliation.',
    'recent_publications_2023_2025': 'Accepted eligible publication count with first publication year 2023–2025.',
    'first_or_last_author_publications': 'Accepted eligible papers where the matched author is first or last in the indexed list.',
    'fractional_publications': 'Sum of 1 / all indexed author count across accepted eligible papers.',
    'citations_in_topic_corpus': 'Sum of observed Europe PMC citation counts across accepted eligible papers; not career citations.',
    'access_keyword_flag': 'Keyword indicator for access/referral/disparity/logistics language in title or abstract; not manually adjudicated.',
    'access_keyword_publications': 'Accepted eligible papers matching the access keyword screen.',
    'coauthor_degree': 'Number of distinct observed coauthors within the selected cohort.',
    'coauthor_fractional_strength': 'Sum of fractional coauthorship edge weights incident to this HCP.',
    'shared_publications': 'Distinct supporting PMIDs for the undirected HCP pair.',
    'fractional_weight': 'Sum of 1/(all indexed authors minus 1) across shared papers.',
    'evidence_pmids': 'Supporting PMID list for an observed coauthorship edge.',
    'edge_type': 'Observed coauthorship; does not represent patient referral or prescribing influence.',
    'pagerank': 'Weighted PageRank on the selected coauthorship graph with damping 0.85.',
    'weighted_betweenness': 'Normalized weighted betweenness using distance 1/fractional edge weight.',
    'community_id': 'Greedy-modularity community label; labels are arbitrary and cohort-dependent.',
    'exploratory_score': 'Unfitted weighted combination of cohort feature percentiles; no causal interpretation.',
    'exploratory_rank': 'Descending exploratory score rank, with HCP ID as tie breaker.',
    'patient_copay_usd': 'Unavailable. CMS Open Payments does not report patient out-of-pocket costs.',
    'social_followers': 'Not collected. Blank must not be interpreted as zero.',
    'record_id': 'CMS payment record identifier for exact duplicate checks.',
    'amount_usd': 'Reported general-payment amount in US dollars.',
    'general_payments_usd_2024': 'Sum of completed exact-NPI CMS 2024 general-payment results. Not patient copays.',
    'consulting_usd_2024': 'Sum of general-payment amounts whose CMS payment nature is Consulting Fee.',
    'cart_product_mentioned_usd_2024': 'Full amounts of records mentioning a selected lymphoma CAR-T product, counted once per record; not product-attributable spending.',
    'payment_date': 'Date of the reported transfer of value in payment program year 2024.',
    'payment_publication_date': 'CMS-reported date when the payment was published.',
    'source_release_date': 'Release date of the frozen CMS dataset used for this query.',
    'retrieved_at_utc': 'Actual source request observation time in UTC.',
    'row_assembled_at_utc': 'When the derived HCP row was assembled, not the date of all underlying facts.',
    'computed_at_utc': 'Timestamp of the derived network calculation.',
    'sha256': 'SHA-256 of the referenced local file bytes, for provenance and integrity verification.',
}

def make_manifests():
    out = ROOT / 'Data' / 'manifests'
    out.mkdir(exist_ok=True)
    sources = [load(p) for p in sorted(RAW.rglob('*.meta.json'))]
    write_csv(out / 'source_requests.csv', sources)
    inventory = []
    for base in ['Data/raw', 'Data/interim', 'Data/processed', 'Data/references', 'Data/legacy']:
        for path in sorted((ROOT / base).rglob('*')):
            if not path.is_file() or path.suffix == '.tmp':
                continue
            payload = path.read_bytes()
            inventory.append({'path': str(path.relative_to(ROOT)), 'bytes': len(payload),
                              'sha256': hashlib.sha256(payload).hexdigest(),
                              'file_mtime_utc_not_retrieval': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')})
    write_csv(out / 'data_file_checksums.csv', inventory)
    dictionary = []
    for path in sorted(PROCESSED.glob('*.csv')):
        rows = read_csv(path)
        with path.open(newline='', encoding='utf-8') as stream:
            fields = next(csv.reader(stream))
        for field in fields:
            values = [r[field] for r in rows if r.get(field, '') != '']
            description = DESCRIPTIONS.get(field)
            if description is None:
                if field.endswith('_cohort_percentile'):
                    description = 'Midrank percentile of ' + field.removesuffix('_cohort_percentile') + ' within this selected cohort.'
                elif field.endswith('_at_utc'):
                    description = field.replace('_', ' ').capitalize() + '; explicit UTC observation or computation timestamp.'
                elif field.endswith('_on') or field.endswith('_date'):
                    description = field.replace('_', ' ').capitalize() + '; ISO calendar date, distinct from exact collection timestamps.'
                elif field.endswith('_url'):
                    description = 'Public source URL for the corresponding record or evidence.'
                else:
                    description = field.replace('_', ' ').capitalize() + '; see table context and dataset methodology.'
            dtype = 'string'
            if values and field not in ['npi', 'pmid', 'record_id', 'cms_profile_id', 'matched_npi']:
                try:
                    for value in values:
                        float(value)
                    dtype = 'numeric'
                except ValueError:
                    pass
            dictionary.append({'table': path.stem, 'column': field, 'storage_type': dtype,
                               'description': description, 'missing_values': 'blank = unavailable or not collected; consult status columns',
                               'example': values[0][:160] if values else ''})
    write_csv(out / 'data_dictionary.csv', dictionary)
    dump(out / 'source_catalog.json', {
        'created_at_utc': now(), 'sources': [
            {'source': 'Europe PMC / PubMed MED', 'original_observed_on': '2026-10-08',
             'original_query': '(TITLE_ABS:"CAR-T" OR TITLE_ABS:"CAR T" OR TITLE_ABS:"chimeric antigen receptor" OR TITLE_ABS:axicabtagene OR TITLE_ABS:tisagenlecleucel OR TITLE_ABS:lisocabtagene OR TITLE_ABS:brexucabtagene) AND (TITLE:lymphoma OR TITLE:DLBCL OR TITLE:LBCL) AND SRC:MED',
             'supplement_query': SUPPLEMENT_QUERY, 'publication_window': ['2017-01-01', '2025-12-31'],
             'supplement_manifest': 'Data/interim/2026-10-09/supplement_query_manifest.json',
             'documentation_url': 'https://europepmc.org/RestfulWebService'},
            {'source': 'CMS NPPES NPI Registry', 'documentation_url': 'https://npiregistry.cms.hhs.gov/api-page',
             'api_version': '2.1', 'observation_times': 'per-response metadata and source_requests.csv'},
            {'source': 'CMS Open Payments', 'dataset_id': 'e6b17c6a-2534-4207-a4a1-6746a14911ff',
             'program_year': 2024, 'release_date': '2026-06-30',
             'source_url': 'https://openpaymentsdata.cms.gov/', 'data_type': 'general payments only'},
            {'source': 'IQVIA CAR-T access white paper', 'publication_year': 2025,
             'role': 'aggregate access context; not an HCP identity or patient copay source',
             'source_url': 'https://github.com/AndiCupallariCU/Applied_AI_Life_Sciences/blob/main/Week%2002%20-%20Pharma%20Datasets%20and%20Claims%20Analytics/reading%20assignment/iqvia-the-path-to-car-tcell-therapy-white-paper-2025.pdf'},
        ],
        'legacy_file_dates': 'Original source request times were not fully recorded. File mtimes are retained only as file metadata and must not be interpreted as retrieval times.',
    })
    print('MANIFESTS', len(sources), 'SOURCE_REQUESTS', len(inventory), 'FILES', len(dictionary), 'FIELDS')

if __name__ == '__main__':
    make_manifests()
