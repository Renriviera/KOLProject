# Extensions of the four dataset categories — 2026-11-20 snapshot

44 tables under `Data/processed/2026-11-20/` extend each of the project's four dataset categories.
They were built on 2026-11-20 by four modules of `src/kolproject/extensions/`, one per category. Earlier snapshots
are unchanged, and no table repeats an entry that an earlier snapshot already holds.

| Category | Tables | Rows | Sources logged |
|---|---|---|---|
| 1. Publication history and co-authorship | 12 | 223,076 | 5 |
| 2. Payer payments and claims | 7 | 193,083 | 28 |
| 3. Industry payments and disclosed ties | 15 | 85,160 | 2 |
| 4. Referral and patient-flow data | 10 | 378,843 | 9 |

People are keyed by NPI (National Provider Identifier) and hospitals by CCN (CMS Certification Number; CMS is the
Centers for Medicare & Medicaid Services). Per-NPI tables do not repeat a clinician's name, place or division:
join `Data/processed/2026-10-24/hcp_divisions.csv` on `npi` and `cart_centers.csv` on `ccn`. Two exceptions are
stated where they occur: authorship tables carry the author name as indexed on the paper and the affiliation text
at publication, and Medicare tables carry the provider type CMS assigns each year. Division groups are core
(`core`), affiliates (`affiliate_A`, `affiliate_B`, `affiliate_C`, `center_affiliate_unlinked`), referring
(`referring_proxy`) and other (`hemonc_other`), label version 1.0.0.

## No duplicates

Every table declares a key. Before publishing, each module drops any row whose key an earlier snapshot already holds
and any repeat inside its own rows, and records the counts under `dedup` in `extensions_metadata.json`. Where a new
table sits beside an older one at the same grain (for example new papers beside the 2026-10-09 papers), the two are
kept as separate tables with a check that they share no key. `kolproject-evaluate` repeats these checks, so a
duplicate or an overlap with an earlier snapshot fails validation.

## Where and when each value came from

- Every row of every table carries provenance columns: a source id or dataset version, the source's release where
  it has one, and the UTC retrieval or ingestion time. The data dictionary lists them per table.
- `Data/manifests/2026-11-20/extension_sources.csv` lists every source by module: publisher, URL, release or pinned
  version, the first and last retrieval time, how it was obtained, and the number of requests.
- Every outside request was frozen under `Data/raw/2026-11-20/<module>/` with its URL, UTC time, HTTP headers and
  SHA-256, and is never repeated on a rebuild. The request-level log (`extension_request_log.csv`) and the raw
  responses stay local, as in earlier snapshots.
- Field definitions are in `Data/manifests/2026-11-20/extensions_data_dictionary.json`: a description, the meaning
  of a blank, and the source of every column.

## What stays out of Git

This repository is public. Text that belongs to publishers or that cannot be screened row by row stays in
`Data/interim/2026-11-20/restricted_terms/` (4 columns across 4 tables):
conflict-of-interest statement text, free-text ownership terms, and a study name that failed the person-name screen.
Author links that rest on a name alone are kept in a local review file and are not tracked. Pair-level shared-patient
edges stay local, as before. CareSet-derived tables are shared under CC BY-NC-SA 4.0 with the CareSet non-commercial
addendum.

## Rebuild and validate

```bash
uv sync --locked
uv run kolproject-extensions                      # rebuild all four modules, manifests and checks
uv run kolproject-extensions --only referral      # rebuild one module
uv run kolproject-evaluate                        # published-table checks, usable in a fresh clone
```

A rebuild needs the frozen inputs under `Data/raw/` and `Data/interim/`, which are excluded from GitHub; with them
present it makes no outside request. Build `publications` before `industry`, which reads its link tables. Each
module pins the totals and a fingerprint of its tables, so a rebuild on changed inputs must refresh those constants
(the failing build prints the new values).

## 1. Publication history and co-authorship

Twelve tables under `Data/processed/2026-11-20/` extend the 2026-10-09 publication data in four ways. They link papers to the whole 17,510-NPI hematology-oncology universe of 2026-10-24, not just the 500 core HCPs (healthcare professionals). They add CAR-T (chimeric antigen receptor T-cell) lymphoma papers indexed after the 2026-10-09 corpus. They record citations between papers of the corpus. And they give co-authorship a time axis. Earlier snapshots are unchanged. No row repeats an (npi, pmid) of `authorships.csv` or a PMID, DOI or normalised title of `publications.csv`, and no row presents a pair of `coauthor_edges.csv` as new. People are keyed by NPI (National Provider Identifier): join `Data/processed/2026-10-24/hcp_divisions.csv` on `npi` for name, place and division.

### Tables

| Table | One row represents | Rows | Key |
|---|---|---|---|
| `universe_authorships.csv` | Non-core universe NPI linked (tier A or B) to an author slot of a held 2017-2025 paper | 855 | `npi`, `pmid` |
| `universe_coauthor_edges.csv` | NPI pair with at least one non-core member sharing included held papers | 4,818 | `npi_a`, `npi_b` |
| `new_publications.csv` | Paper from either 2026-10-09 query not held on 2026-10-09 (all first published in 2026) | 699 | `pmid` |
| `new_authorships.csv` | Core or universe NPI linked (tier A or B) to an author slot of a new paper | 1,138 | `npi`, `pmid` |
| `new_paper_coauthor_edges.csv` | NPI pair sharing included new papers, counted on new papers only | 5,466 | `npi_a`, `npi_b` |
| `corpus_citation_edges.csv` | A corpus paper citing another corpus paper (held or new on either side) | 34,776 | `citing_pmid`, `cited_pmid` |
| `hcp_citation_links.csv` | Directed NPI pair: papers linked to one cite papers linked to the other | 123,557 | `citing_npi`, `cited_npi` |
| `publication_citation_counts_2026_11_20.csv` | Corpus paper, Europe PMC cited-by count observed 2026-11-20 beside the 2026-10-09 count | 4,062 | `pmid` |
| `hcp_universe_publication_features.csv` | Universe NPI, summary of publications, co-authors and citations | 17,510 | `npi` |
| `coauthor_pairs_by_year.csv` | NPI pair (core-core included) and publication year with shared papers that year | 27,018 | `npi_a`, `npi_b`, `publication_year` |
| `hcp_publications_by_year.csv` | NPI with an included link, and a publication year | 3,167 | `npi`, `publication_year` |
| `network_summary_by_year.csv` | Publication year, size and mix of the co-authorship network | 10 | `publication_year` |

### Queries

The 2026-10-09 corpus is the union of two Europe PMC queries. Both were rerun here over 2017-01-01 to 2026-11-20:

- `original_lymphoma_in_title`: lymphoma, DLBCL or LBCL in the title and a CAR-T term in the title or abstract. This is the 245-HCP project query, recorded in `Data/legacy/cohort_245/scripts/collect.py`.
- `supplement_cart_in_title_lymphoma_in_title_or_abstract`: the query held in `src/kolproject/corpus.py`.

Rerun on 2026-11-20, the two queries return 3,362 of the 3,363 held papers and no unheld 2017-2025 paper. The one held paper not returned is PMID 40770877. The 699 new papers (523 from the first query and 176 from the second) carry `query_family`.

### Identity rule

A slot is one author position on one paper. It is linked on the first given name and surname plus further evidence.

- **Tier A, name plus institution.** The distinctive words of a CMS hospital or group name of the NPI must appear in the affiliation. The state must also be named when only one strong word is left, or when the words fit several hospitals or groups (a multi-site system, flagged by `institution_multi_site`). A name with one strong word, or with only state words, must appear as a phrase. Tier A also covers an ORCID iD anchored by an A or B link of exactly one NPI, and institution evidence that separates same-name candidates.
- **Tier B, name plus place.** The city and state of the NPI's practice or of its CAR-T center must appear, and the name must be unique in the universe.

These slots are not tracked:

- same-name candidates with no evidence that separates them
- slots held by 2026-10-09 core links
- an NPI matched twice in one paper
- name-only links (tier C)
- A or B links without institution evidence whose name another hematology-oncology clinician shares nationally (NPPES)

The tier C and namesake links (285 in all) are kept only in a local review file under `Data/interim`. Affiliation text is published with email addresses, street addresses and phone numbers removed.

### Co-authorship and citations without double counting

`universe_coauthor_edges` counts held papers and holds no core-core pair; those are in `coauthor_edges.csv`. `new_paper_coauthor_edges` counts only the new 2026 papers and does include core-core pairs. Its `prior_pair_status` says whether the pair already has a held-paper row (`in_coauthor_edges_2026_10_09` or `in_universe_coauthor_edges`) or is a `new_pair`, so totals add across the tables without double counting.

`corpus_citation_edges` was built from the Europe PMC citations list of every corpus paper with a cited-by count above zero:

- 3,189 requests covered 3,171 papers, between 2026-11-20 21:09:47Z and 21:54:32Z.
- Every list is complete (`citation_pull_status`). 890 papers had no citations and needed no request.
- Only citing records of source MED whose PMID is in the corpus are kept, with no self-pairs.
- 132 edges have a citing paper first published before the cited one. They are kept and flagged in `citing_precedes_cited`.

`hcp_citation_links` counts, for each directed pair of different NPIs, the citing-paper to cited-paper pairs that connect them through included links. Those links are the 2026-10-09 core links plus the tracked A and B links. The count is split into pairs where the two NPIs also share the citing or the cited paper, and pairs where they do not. `publication_citation_counts_2026_11_20` is a dated observation beside `publications.csv`, which is not edited.

The feature table keeps held-paper co-author counts, new-paper co-author counts and citation counts in separate columns. A zero in it means no included link was found, not that the NPI has no publications.

### Time axis

`coauthor_pairs_by_year` splits every included pair, core-core pairs included, by the year of the shared papers. It is a finer grain of the pair tables, not a second listing of them. The 2017-2025 rows of a pair sum to `shared_publications` and `fractional_weight` of `coauthor_edges.csv` (core-core pairs) or of `universe_coauthor_edges` (other pairs). The 2026 row equals `new_paper_coauthor_edges`. `check()` asserts all of these sums.

`hcp_publications_by_year` gives each linked NPI's papers per year, with:

- first-author and last-author papers
- distinct core and non-core co-authors
- co-authors not seen in an earlier year
- citations received from corpus papers that have no link to the NPI

For each of the 500 core HCPs, the 2017-2025 papers sum to `publications_2017_2025` of `hcps.csv`.

`network_summary_by_year` gives per year:

- included papers
- linked NPIs by division group (core, affiliates, referring, other)
- pairs, and pairs first seen that year
- pair counts for each combination of division groups
- the share of pairs that cross groups

The share of pairs that cross groups rises from 4.6% in 2017 to 29.6% in 2025 and 39.9% in 2026. Part of the rise comes from how linking works: non-core NPIs can be linked only through tier A or B evidence, which recent affiliations carry more often.

### Measured error

- **Rule replayed on core slots.** Against the 5,001 core slots of the 2026-10-09 links, which used NPPES middle names, all 2,525 tier A links and all 1,823 tier B links agree.
- **Hand-check of multi-site links.** All 15 links in a random sample of multi-site institution links agree with the affiliation text.
- **Independent signal.** The share of linked NPIs with an independent CAR-T research or treater signal is 57% for tier A and 52% for tier B, against 6.6% for the non-core universe.

### Limits

- Universe NPIs have no middle name, so B uniqueness holds within the universe only.
- Institution matching can still accept a namesake at a sister hospital in the same state.
- New papers come from the live Europe PMC index on 2026-11-20, and all of them fall outside the 2017-2025 window of the 2026-10-09 analysis.
- Citation edges include only citations that Europe PMC has linked.
- A single citation between two large author teams adds to many NPI pairs; `citation_pairs_not_coauthors` gives the count without co-author pairs.

## 2. Payer payments and claims

Built by `uv run python -m kolproject.extensions.payer`. Every request is frozen under `Data/raw/2026-11-20/payer/` with its URL, UTC time and SHA-256 (473 requests and local inputs). CMS files are read from the pinned data.cms.gov version of each service year, taken from the catalog https://data.cms.gov/data.json read on 2026-11-20, never from the series alias; the build asserts each pinned id and its release date against that catalog. A blank is never a zero: CMS drops a row resting on 10 or fewer beneficiaries, and every blank that can mean more than one thing has a status column in words. Per-NPI tables carry no names or divisions; join on `npi` to `Data/processed/2026-10-24/hcp_divisions.csv`. No procedure-code descriptor text is published.

| Table | Rows | Key | What it holds |
|---|---|---|---|
| `hcp_medicare_partb_treatment_markers_2017_2023.csv` | 70,456 | npi, service_year, hcpcs_code, place_of_service | Medicare Physician & Other Practitioners by Provider and Service, 2017-2023, universe NPIs only. 27 codes: chemotherapy infusion administration (96413, 96415, 96417); rituximab (J9310 to 2018, then J9312 and the biosimilars Q5115, Q5119, Q5123) and rituximab with hyaluronidase (C9467 in 2018, then J9311); obinutuzumab (J9301); bendamustine (J9033, J9034, J9036, J9056, J9058, J9059, C9042); polatuzumab (J9309); tafasitamab (J9349, C9070); loncastuximab (J9359, C9084); epcoritamab (J9321, C9155); mosunetuzumab (J9350); glofitamab (J9286). Holds beneficiaries, services, beneficiary-days, average allowed and paid amounts, and an estimated payment (average x services). Rows per year: 10,561 (2017), 10,514, 10,493, 9,798, 9,681, 9,703 and 9,706 (2023). 2024 is in `treatment_markers_2024.csv` and `medicare_partb_drug_rows_2024.csv`. |
| `lymphoma_product_code_validity.csv` | 168 | hcpcs_code, service_year | For each of the 24 product codes and each year 2017-2023: family label, temporary or permanent, added and termination dates from the CMS January 2021-2025 Level II HCPCS files, whether the code was valid all year, part of the year or not at all, rows in the pinned national file, and universe rows. Use it to tell "not billable" from "no clinician at 11 or more beneficiaries". No product-specific temporary code exists for polatuzumab, mosunetuzumab or glofitamab. Miscellaneous codes (J9999, J3490, J3590, C9399) are not attributed to any product. |
| `hcp_medicare_partd_lymphoma_orals_2017_2023.csv` | 87,077 | npi, service_year, generic_name, brand_name | Medicare Part D Prescribers by Provider and Drug, 2017-2023, for ibrutinib, acalabrutinib (capsule and maleate), zanubrutinib, pirtobrutinib, lenalidomide (brand and, from 2022, generic), venetoclax (including the starting pack) and tazemetostat. Claims, 30-day fills, day supply and gross drug cost; beneficiaries are blank when 1-10 (`beneficiaries_status`). Rows per year: 7,905 (2017), 9,111, 10,814, 12,642, 14,055, 15,412 and 17,138 (2023). 2024 is in `part_d_lymphoma_orals_2024.csv`. |
| `hcp_medicare_partb_payments_by_year_2017_2018.csv` | 35,020 | npi, service_year | Medicare Physician & Other Practitioners by Provider, 2017 and 2018: one row per universe NPI and year, in the layout and status vocabulary of `hcp_medicare_partb_payments_by_year.csv` (2026-11-19, which holds 2019-2024). Status is published_split, split_suppressed (the drug and non-drug split is hidden because one side rests on 1 to 10 beneficiaries) or no_row. CMS rows: 11,285 NPIs in 2017 and 11,807 in 2018. Total Medicare payment: $6,532.5M (2017) and $7,341.4M (2018). |
| `cart_product_program_spending_by_year.csv` | 70 | brand_name, program, calendar_year | National CAR-T product spending, claims, dosage units and (Part B only) beneficiaries, 2020-2024. From Medicare Part B Spending by Drug (outpatient Part B only; inpatient CAR-T is paid under Part A) and Medicaid Spending by Drug (all-manufacturer row). `value_status` separates published, no value for the year, and brand absent from the file. |
| `state_cart_hospital_stays_by_payer.csv` | 236 | ccn, year, source, payer_group | All-payer inpatient stays in MS-DRG 018 (CAR-T and other immunotherapies) not yet in `cart_site_volume_panel.csv`. `ca_hcai_discharges`: California HCAI Top 25 MS-DRGs Individual Hospital Pivot Profile, 2021-2023, Creative Commons Attribution; a hospital is listed only when MS-DRG 018 is in one of its top-25 tables. `fl_ahca_discharges`: Florida AHCA FloridaHealthFinder inpatient query tool, 2023-2025, all-payer and five principal-payer groups, from query outputs saved by the 2026-11-19 search (file hashes in the module metadata). The site disclaimer says facilities with fewer than 30 cases are shown as N/A and those with zero cases are excluded, so a center not shown is blank with status `not_shown_below_display_threshold` (0 to 29). Nothing is filled by subtraction; counts are quoted with attribution and no affiliation. Facilities are mapped to CCN by hand. New York before 2023 is not used because it pools CAR-T with transplant. |
| `state_medicare_cart_administration_by_year.csv` | 56 | geography_code, service_year, hcpcs_code, place_of_service | Medicare Physician & Other Practitioners by Geography and Service, 2019-2024, national and state rows for the CAR-T episode codes 0537T-0540T. Only 0540T (administration) has rows; states appear only at 11 or more beneficiaries, so state rows do not sum to the national row. NPI-level 0540T rows are already in `npi_cart_treater_evidence.csv` (2026-11-19) and are not repeated. |

Sources:
- Centers for Medicare & Medicaid Services: data.cms.gov datasets and the Level II HCPCS files (US government public data).
- California Department of Health Care Access and Information, via data.chhs.ca.gov (CC BY).
- Florida Agency for Health Care Administration (copyright notice, no reuse grant; only displayed counts with the query parameters are published).

`check()` reads only the processed directory. It checks duplicate keys, overlap with earlier snapshots (the 2024 marker, drug-row and Part D tables, the 2019-2024 payment table and the volume panel), universe and center joins, complete grids, suppression and display thresholds, status consistency, code validity, estimated payments and split sums, a frozen headline, and a cell fingerprint of every table.

## 3. Industry payments and disclosed ties

Built by `uv run python -m kolproject.extensions.industry` from three sources:
- The Ownership and Research detail files of the seven CMS Open Payments program-year archives (2019-2025, publication of 2026-06-30), already on disk from the 2026-11-11 snapshot.
- The published row-level table `2026-11-19/universe_cart_general_payments_2019_2025.csv`, for the yearly view; no record is published again.
- PubMed records of the 3,363 held papers and the 699 papers of `new_publications.csv`. These come from NCBI E-utilities efetch on 2026-11-20 (22 requests) and are frozen under `Data/raw/2026-11-20/industry/`.

Statement text, ownership terms and the one study name that fails the screen stay in `Data/interim/2026-11-20/restricted_terms/`.

CAR-T manufacturer groups: Kite/Gilead; Bristol Myers Squibb/Celgene/Juno; Novartis; Janssen/Johnson & Johnson; Legend Biotech; Autolus; bluebird bio/2seventy bio. The CMS names matched are listed in the module metadata.

| Table | Key | Rows | What it adds |
|---|---|---|---|
| `universe_ownership_interests_2019_2025` | `record_id` | 606 | Ownership and investment interests of universe physicians, with a CAR-T manufacturer flag. |
| `hcp_universe_ownership_features` | `npi` | 17,510 | Per-NPI ownership counts (zero-filled) and sums (blank without a record). |
| `universe_cart_research_payments_2019_2025` | (`npi`, `record_id`) | 8,023 | CAR-T research records of non-core universe NPIs: role, investigator slots, screened study name, ClinicalTrials.gov id, payer, record total. |
| `hcp_universe_cart_general_payments_by_year` | (`npi`, `program_year`) | 39,781 | Yearly CAR-T general payments under the any_slot, cart_only, lymphoma_only and pure_play_payer rules, for the 5,683 NPIs in the 2026-11-19 row-level table; NPIs without a record are absent. |
| `hcp_universe_cart_research_by_year` | (`npi`, `program_year`) | 4,732 | Yearly CAR-T research series for the 676 universe NPIs (247 core) named on a CAR-T research record: product-field and text-only records, as investigator, as sole principal investigator, record-total dollars, lymphoma-product records, studies, ClinicalTrials.gov ids, manufacturer groups. Core rows aggregate `2026-11-11/research_payments_2019_2025.csv`, the others this snapshot's universe research table, under the same definitions. NPIs without a record are absent. |
| `cart_research_by_division_year` | (`division_group`, `program_year`) | 28 | NPIs with a product-field CAR-T research record, records, and as-investigator records, by division group and year. |
| `cart_general_payments_by_division_year` | (`division_group`, `program_year`) | 28 | cart_only services and hospitality dollars and NPIs paid, by division group and year. |
| `publication_industry_disclosures` | `pmid` | 3,363 | Held papers: statement present, CAR-T manufacturer groups named, industry co-authorship. |
| `new_publication_industry_disclosures` | `pmid` | 699 | The same for papers first published in 2026; no PMID is in both tables. 471 have a statement, 228 are returned without one. |
| `core_author_publication_industry_ties` | (`npi`, `pmid`) | 4,991 | Core HCPs on held papers: named in the statement, named with a CAR-T manufacturer (1, 0, or blank when not determinable), own industry affiliation. |
| `universe_author_publication_industry_ties` | (`npi`, `pmid`) | 1,993 | The same for non-core NPIs on held papers (`universe_authorships`) and all universe NPIs on 2026 papers (`new_authorships`); no pair is in both author tables. |
| `core_hcp_disclosed_ties_by_year` | (`npi`, `publication_year`) | 1,996 | Core ties on held papers by publication year and manufacturer group. |
| `core_hcp_industry_ties_summary` | `npi` | 500 | Per core HCP: ownership, CAR-T product research roles (from 2026-11-11), disclosed ties on held papers. |
| `core_hcp_new_paper_ties` | `npi` | 292 | Per core HCP with a 2026 paper: new-paper statement and tie counts. |
| `universe_hcp_industry_ties_summary` | `npi` | 618 | Per non-core NPI with a paper link, in the core summary layout: ownership, CAR-T product research rows, disclosed ties. |

Findings:
- No physician reports an ownership interest in a CAR-T manufacturer in 2019-2025.
- 261 of the 500 core HCPs are named with a CAR-T manufacturer on a held paper, and 131 on a 2026 paper.
- 105 of the 618 non-core NPIs with a paper link are named with one.
- Product-field CAR-T research records naming a universe NPI rose every year, from 628 (143 NPIs) in 2019 to 2,384 (281 NPIs) in 2025; about 98% name the NPI as investigator.

Statement parsing: a passage starts at a mention of the author and ends at the next mention of another author or at a general sentence. A mention can be a full name, initials, first initial and surname, the surname alone, or an honorific with the surname.
- Precision (hand-checked): 30/30 on all positives, and 20/20 on the new rows.
- Recall: about 1 missed tie in 40 links with value 0 or blank.

`check()` asserts:
- unique keys
- no overlap with earlier snapshots or between the paired paper and author tables
- joins to the universe, core, publication and authorship tables, including the publications module's three 2026-11-20 tables
- every derived table equals its recomputation
- the yearly general-payment table agrees with the 2026-11-19 and 2026-11-11 payment tables
- the yearly research table sums to the 2026-11-19 per-NPI research features for every NPI, and to the 2026-11-11 core research features
- the tables of earlier passes are rebuilt byte-identical (asserted before publishing)
- research reconciles with the 2026-11-19 per-NPI summaries
- every headline value and a SHA-256 fingerprint of every table

Limitations:
- A missing statement is not evidence of no ties, especially for 2026 papers indexed recently.
- Authors who share initials cannot be told apart.
- A company match is a tie to the company, not to a CAR-T product.
- `amount_usd` and the research dollar columns are record totals, repeated across the NPIs of a record; they are not personal income.
- `studies_named` leaves out one non-core study name withheld by the screen.
- Ownership values are per program year.
- The new author links depend on the publications module's name matching.

## 4. Referral and patient-flow data

Built by `uv run python -m kolproject.extensions.referral` from two sources:

- the six local CareSet DocGraph Hop Teaming edge tables of the 2026-10-24 snapshot (Medicare fee-for-service shared-patient pairs, 2017-2022);
- the seven CMS Physician Shared Patient Patterns 180-day files, 2009-2015, from the NBER mirror. Each was streamed once (2014 and 2015 twice, with identical SHA-256 and line counts), and only a filtered extract is kept locally.

CareSet-derived tables are shared under CC BY-NC-SA 4.0 with the CareSet Noncommercial Data License Addendum. They carry attribution to CareSet Systems, Inc. and are for non-commercial use only. CMS data are public domain.

Every row carries a short source id (`careset_hop_<year>` or `cms_pspp_<year>_180`), an 8-character SHA-256 prefix and the ingestion or retrieval time. Full hashes are in the module metadata. Pair-level edges stay local.

A link to a center follows the "to centers" rule of the 2026-10-24 yearly features (`careset.target_centers`). It is a directed pair from a universe NPI to either:

- a center organization NPI, or
- a universe clinician whose hospital CCNs include that center.

Counts are sums of pair counts. Each pair has at least 11 Medicare fee-for-service patients, and the sums are not distinct patients. A shared patient is not a referral, and nothing here is specific to CAR-T. A year without a link is `not_observed`, never zero.

| Table | Key | Rows | Adds |
|---|---|---|---|
| `shared_patient_npi_center_flows_2017_2022` | npi, ccn, year | 147,226 | Panel: patients, patients split across a receiver's centers, receiving NPIs, patient-weighted mean wait, region relation |
| `shared_patient_npi_longitudinal_2017_2022` | npi | 17,510 | First and last year linked, years linked, persistence, link retention, distinct centers, primary center, its share and its region |
| `shared_patient_npi_link_changes_2017_2022` | npi, year | 70,700 | Links gained, lost and retained, for linked years and for unlinked years that lost links |
| `shared_patient_center_referrer_base_2017_2022` | ccn, year | 1,038 | Referrers, patients, Herfindahl index of referrers, top-referrer share, out-of-region share, referrer turnover |
| `shared_patient_division_group_flows_2017_2022` | division_group, year | 24 | Linked NPIs, links, patients, out-of-region share, links gained and lost by division group |
| `shared_patient_npi_core_hcp_links_2017_2022` | npi, year | 6,597 | Per sender and year: core HCPs reached, patients to them, the share of the sender's center-linked flow that goes through a core HCP, mean wait |
| `shared_patient_core_hcp_senders_2017_2022` | npi, year | 2,079 | Per core HCP and year: senders and patients by sender group, out-of-region share, Herfindahl index of senders, senders gained and lost |
| `shared_patient_core_reach_by_group_2017_2022` | year, division_group | 24 | Senders linked to at least one core HCP and their patients, by sender group |
| `shared_patient_npi_center_baseline_2009_2015` | npi, ccn, year | 133,617 | Pre-CAR-T baseline from CMS: beneficiaries, pair and same-day counts by the same link rule |
| `shared_patient_baseline_coverage_2009_2015` | year, division_group | 28 | How many universe NPIs each CMS year sees as npi1 at all, how many are linked, and the linked share |

### CareSet panel, 2017-2022

Summed over centers, the panel's split patient counts equal `out_patients_to_centers` of the yearly features for every NPI and year. Its referring-proxy rows equal the published HRR-to-center flows exactly.

- 13,802 universe NPIs link to 161 centers.
- 8,580 NPIs are linked in all six years, and 5,920 have a primary center outside their own HRR.
- The 12 centers that are never linked are all children's hospitals.
- Between 2017 and 2022 the share of patients sent outside the referrer's region falls from 0.546 to 0.490 for the referring group, and from 0.271 to 0.202 for affiliates.

### Links to core HCPs, 2017-2022

These tables use the same six CareSet years, restricted to pairs whose receiver is one of the 500 core HCPs.

- The number of core HCPs receiving from universe senders rises from 291 in 2017 to 355 in 2022.
- Most of that flow comes from core and affiliate senders. In 2022, 806 affiliate senders account for 78,211 summed patients and 298 core senders for 34,711.
- Only 43 referring-group senders reach a core HCP in 2022 (2,955 patients); in 2017 it was 50 senders (3,318 patients).
- Most center-linked flow lands on center organization NPIs or on affiliates, so the core tables describe a small part of the flow.

The referring group's patients and senders per core HCP are not repeated in these tables. They are `in_patients_from_referrers` and `referrer_sources` of the 2026-10-24 yearly features, and `check()` asserts that they equal the universe total minus the three published groups.

### CMS baseline, 2009-2015

The CMS baseline is a separate source and is not part of the 2017-2022 series.

**Direction.** The CMS files are directed. The CMS technical requirements say "A shared relationship is presumed from physician A to physician B" when B's claim follows A's, so npi1 is the earlier provider. Same-day pairs are assigned to the lesser NPI, so their direction is arbitrary.

**Method differences from CareSet:**

- a 180-day follow-on window;
- beneficiary counts instead of patient counts;
- no wait time;
- wider claim types, including Medicare Advantage no-pay claims.

**Years.** Each year runs from initial claims in that year to follow-ups by 1 July of the next year. 2015 stops at 1 October 2015.

**Early-year coverage.** The universe, divisions, hospital affiliations and center set are 2024 facts applied backwards. The CMS file therefore sees fewer of today's universe NPIs in early years, while the linked share among those it sees stays steady.

| CMS year | Universe NPIs seen as npi1 (any partner) | Universe NPIs linked to a center |
|---|---|---|
| 2009 | 7,868 | 6,847 |
| 2010 | 8,342 | 7,248 |
| 2011 | 8,789 | 7,716 |
| 2012 | 9,264 | 8,198 |
| 2013 | 9,827 | 8,717 |
| 2014 | 10,347 | 9,240 |
| 2015 (to 1 October) | 10,067 | 8,363 |

The universe has 17,510 NPIs. Read early years with the coverage table.

**Lines dropped as not data.** A few records failed the data test (first field digits, at least five fields): 4 in 2011, 6 in 2012 and 5 in 2013. They were dropped without being saved. The 2014 and 2015 files had none.
