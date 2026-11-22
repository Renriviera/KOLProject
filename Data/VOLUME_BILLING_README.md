# CAR-T volume, hospital billing and payments by division — 2026-11-19 snapshot

Seventeen tables under `Data/processed/2026-11-19/` hold what a search on 2026-11-19 found about
three questions: who is involved in CAR-T (chimeric antigen receptor T-cell) therapy and at what
volume, what hospitals bill for it beyond the single Medicare count already held, and what payers and
manufacturers pay the project's core, affiliate and referring HCPs (healthcare professionals). The
search report, `reports/CAR-T physician and hospital volume data.md`, is kept local and is not in Git. Earlier
snapshots are unchanged.

No public source gives CAR-T volume per physician, and none shows a CAR-T dose bought at a 340B price.
These tables hold the nearest public evidence: site-level counts, evidence of who is named at a
program, and payment totals. None of them is a physician volume measure.

People are keyed by NPI (National Provider Identifier) and hospitals by CCN (CMS Certification
Number; CMS is the Centers for Medicare & Medicaid Services). Per-NPI tables do not repeat the name or
place of universe NPIs: join `Data/processed/2026-10-24/hcp_divisions.csv` on `npi` and
`Data/processed/2026-10-24/cart_centers.csv` on `ccn`. Two exceptions are stated where they occur: the
treater table carries `in_universe`, `is_core` and `division`, and the federal registry name, state and
taxonomy of NPIs outside the universe; the Medicare by-year table carries the CMS provider type. Division groups used in every summary are
core (`core`), affiliates (`affiliate_A`, `affiliate_B`, `affiliate_C`, `center_affiliate_unlinked`),
referring (`referring_proxy`) and other (`hemonc_other`), label version 1.0.0.

## Tables

| Table | One row represents | Rows | Key |
|---|---|---|---|
| `cart_site_volume_panel.csv` | CAR-T treating site supported by a public source, with each source's volume measure | 240 | `site_key` |
| `cart_center_340b_status.csv` | CAR-T center hospital and its standing in the 340B drug discount program | 173 | `ccn` |
| `cart_product_orphan_designations.csv` | CAR-T product and indication with an orphan designation in an FDA document | 16 | `product`, `designated_indication` |
| `npi_cart_treater_evidence.csv` | NPI named in a public, non-claims source as involved with CAR-T | 2,366 | `npi` |
| `division_relabel_candidates.csv` | Universe NPI labelled referring or other that has such evidence (review queue) | 147 | `npi` |
| `universe_cart_general_payments_2019_2025.csv` | Open Payments general record to a universe NPI naming a CAR-T product or paid by a CAR-T-only company | 39,655 | `record_id` |
| `hcp_universe_general_payment_features.csv` | Universe NPI, seven-year general payment summary | 17,510 | `npi` |
| `hcp_universe_research_payment_features.csv` | Universe NPI, seven-year research payment summary | 17,510 | `npi` |
| `cart_industry_payments_by_division_2019_2025.csv` | Record rule × division group × nature class | 96 | `rule`, `division_group`, `nature_class` |
| `cart_center_teaching_hospital_payments_2019_2025.csv` | CAR-T center, Open Payments to the teaching hospital | 173 | `ccn` |
| `hcp_medicare_partb_payments_by_year.csv` | Universe NPI × service year, Medicare Part B drug and non-drug payment | 105,060 | `npi`, `service_year` |
| `hcp_medicare_service_product_features_2024.csv` | Universe NPI, four 2024 Medicare sums | 17,510 | `npi` |
| `medicare_partb_drug_rows_2024.csv` | NPI × drug billing code × place of service, 2024 | 46,191 | `npi`, `hcpcs_code`, `place_of_service` |
| `hcp_medicaid_payments_by_year_2018_2024.csv` | Universe NPI × year with a released Medicaid row | 54,745 | `npi`, `year` |
| `hcp_medicaid_payment_features_2018_2024.csv` | Universe NPI, Medicaid summary | 17,510 | `npi` |
| `medicaid_payments_by_division_year_2018_2024.csv` | Join side × division or group × year | 154 | `join_side`, `division_level`, `division`, `year` |
| `hcp_medicaid_center_billing_links_2018_2024.csv` | Universe NPI × CAR-T center that billed Medicaid for that NPI | 1,816 | `npi`, `ccn` |

`volume_billing_metadata.json` carries sources, counts, limits and open defects for each group of
tables. Field definitions are in `Data/manifests/2026-11-19/volume_billing_data_dictionary.json`,
which gives every column a description, the meaning of a blank, and its source.

## How this snapshot differs from the earlier ones

The inputs were collected by an ad hoc search, not by the dated collectors of the earlier snapshots.
Each input was then frozen under `Data/raw/2026-11-19/` with its hash, and every table is rebuilt in
code from those frozen files; the Open Payments tables are rebuilt from the 2026-06-30 archives
already held. Where the search saved no request time for an input, the metadata says so. Tables whose
values could not be recomputed from another tracked table are guarded by a fingerprint of their cells,
so `kolproject-evaluate` notices any edit.

## What is kept out of Git, and why

This repository is public, so each source's reuse terms were read on 2026-11-19 and recorded in
`Data/manifests/2026-11-19/volume_billing_source_terms.csv`. That file is a risk classification, not
legal advice.

- **Kite and Bristol Myers Squibb treatment-center locators.** Both sites limit reuse to personal,
  non-commercial use. Nothing derived from them is tracked: no column, no per-site or per-person value
  computed with them, and no row that exists only because a locator lists the site or the person. The
  tracked files say only how many rows are held back.
- **BMT InfoNet directory.** The site reserves all rights and is silent on reuse. Tracked: directory
  membership, counts of programs and of programs reporting, record numbers, 2024 size bands (all programs
  and non-pediatric programs; a reported zero is shown as `0`), whether the record shows FACT approval,
  whether a program is pediatric, and the per-NPI roster flag. Not tracked: exact yearly counts per center
  and text copied from the site.
- **FACT listing** (Foundation for the Accreditation of Cellular Therapy). The site also reserves all
  rights. Accreditation flags and the per-NPI program-director flag are tracked; no name is copied from the
  listing.
- **Every other source** is federal or state open data, or facts that hospitals must publish, and is
  tracked with attribution.

The 35 held columns, and the held rows, are written to
`Data/interim/2026-11-19/restricted_terms/`, which Git ignores. They join to the tracked tables on the
same key, so nothing is lost locally. Publishing any of them needs written permission from the source.
The search's own working files under `research_notes/*/probes/` are also ignored, because they hold raw
copies of those pages.

## Rebuild and validate

```bash
uv sync --locked
uv run kolproject-volume-billing                   # rebuild all tables, manifests and checks
uv run kolproject-volume-billing --only status_340b  # rebuild one group
uv run kolproject-evaluate                         # published-table checks, usable in a fresh clone
```

A rebuild needs the frozen inputs under `Data/raw/2026-11-19/`, which are excluded from GitHub, and
for the Open Payments tables the archives under `Data/raw/2026-11-11/`. A fresh clone can run
`kolproject-evaluate`, which reads only `Data/processed/`. The Medicaid extract was made once by remote
query with DuckDB; with the frozen extract present no extra package is needed, and without it run
`uv run --with duckdb python -m kolproject.volume_billing.medicaid_payments`.

## CAR-T treating sites and their volume measures: `cart_site_volume_panel`

One table under `Data/processed/2026-11-19/` lists the sites that the 2026-11-19 search identified as CAR-T (chimeric antigen receptor T-cell) treating sites and that a public source supports, with the volume measure each public source shows for them. Hospitals are keyed by CCN (CMS Certification Number, six digits, load as text). The 173-center set of `Data/processed/2026-10-24/cart_centers.csv` is unchanged.

### Tables

| Table | One row represents | Rows | Key |
|---|---|---|---|
| `cart_site_volume_panel.csv` | One CAR-T treating site | 240 | `site_key` |

The 240 sites are 173 center-set hospitals, 45 other hospitals (43 on the BMT InfoNet directory and 2 California hospitals that appear only in the state discharge file), 10 physician practices, 1 federal hospital and 11 Canadian centers. A row is in the table when the site is in the center set, on the BMT InfoNet directory, or in a state discharge file. `site_key` is filled on every row: it is the CCN for the 218 hospital rows and a lowercase slug (for example `practice-tennessee-oncology-nashville-tn`) for the 22 other rows, where `ccn` is blank. The table has 51 columns. Hospital 340B status is not repeated here: join `cart_center_340b_status.csv` of the same snapshot on `ccn`.

### What was collected and from where

| Measure | Source | Years | Sites with a value | Total |
|---|---|---|---|---|
| Medicare fee-for-service inpatient discharges in MS-DRG 018 (Medicare Severity Diagnosis Related Group for CAR-T and other immunotherapy) | CMS Medicare Inpatient Hospitals by Provider and Service | 2021 | 23 | 544 |
| | | 2022 | 37 | 981 |
| | | 2023 | 46 | 1,303 |
| | | 2024 | 49 | 1,360 |
| All-payer inpatient discharges in APR-DRG 011 (All Patient Refined Diagnosis Related Group for CAR-T and other immunotherapy) | New York SPARCS de-identified public file (Statewide Planning and Research Cooperative System) | 2023 | 10 | 483 |
| | | 2024 | 10 | 557 |
| All-payer inpatient discharges in MS-DRG 018 | California HCAI (Department of Health Care Access and Information), Top 25 MS-DRGs Individual Hospital profile | 2024 | 16 | 738 |
| | | 2025 | 19 | 921 |
| Remittance counts by payer class for MS-DRG 018 or APR-DRG 011 | Each hospital's own price transparency file | about January 2025 to March 2026 | 7 | not additive |
| Research dollars naming a CAR-T product, paid to the CCN as teaching hospital | CMS Open Payments, publication of 2026-06-30 | 2019 to 2025 | 197 | 95,383,961.76 dollars |
| Size band of self-reported CAR-T therapies | BMT InfoNet (Blood & Marrow Transplant Information Network) CAR T-cell Center Directory, read 2026-11-19 | 2024 | 179 | bands only |

The 2024 New York payer split is Medicare 258, Medicaid 66, commercial 219 and other 14. Statewide California totals are 788 (2024) and 976 (2025). The Open Payments amount that also counts records matched by study text is 249,519,073.35 dollars.

For the 2024 Medicare column, every CCN was checked against the whole 2024 file (145,879 rows, 2,906 hospitals, pinned dataset version `ca9b5ef0-1386-4759-b2b6-5f9b35116786`, pulled 2026-11-19). The raw pages are under `Data/raw/2026-11-19/cms_inpatient_2024/`, each with its hash and retrieval time.

### Headline counts

- 49 hospitals have a published 2024 Medicare count. 121 hospitals are in the 2024 file without an MS-DRG 018 row (fewer than 11 discharges, or none); 91 of them are in the center set. 48 hospitals are absent from the file for every group (33 in the center set): 36 children's hospitals, 10 cancer hospitals paid outside the prospective payment system, and two others (050778, 400016). 22 rows have no CCN.
- 57 hospitals have a Medicare count in at least one year from 2021 to 2024; all are in the center set, and the smallest published value is 11.
- 229 sites are on the BMT InfoNet directory (164 of the 173 centers), through 277 directory records. 179 sites have a 2024 size band (142 of the 173 centers): 0 at 15 sites, 1-10 at 53, 11-25 at 33, 26-50 at 26, 51-100 at 36, 101-200 at 13, and 201+ at 3. Four bands are partial sums because one matched program reports a number and another does not.
- 197 hospitals are the teaching hospital on at least one Open Payments research record in 2019 to 2025; 66 have a product-named CAR-T amount above zero and 95 an amount above zero under the wider text rule. 21 hospitals have no record.
- 34 sites (31 in the center set) have at least one all-payer measure from a state file or a parsed price file (`has_public_all_payer_count`).
- 22 rows must be read together with another row or carry an accreditation-only note; `related_site_key` points to the other row (21 rows) and `related_site_note` says why. Eight of the 173 centers carry an accreditation that belongs wholly or partly to another site (340166, 490101, 390114, 380004, 490007, 390050, 340014, 440161). They are flagged, not relabelled.

### How to read blanks

A blank never means zero in this table, and each ambiguous blank has a status column in words.

| Column group | Status column | Values |
|---|---|---|
| Medicare 2024 | `medicare_file_status_2024` | `count_published`, `in_file_no_drg018_row`, `not_in_file`, `no_ccn` |
| New York | `ny_sparcs_status` | `discharges_in_file`, `in_state_none_in_file`, `in_state_no_ccn` (none in this snapshot), `outside_new_york` |
| California | `ca_hcai_status` | `drg018_in_top25_table`, `in_state_drg018_not_in_top25_table`, `in_state_no_ccn`, `outside_california` |
| Price files | `pt_remit_status` | `file_parsed`, `file_not_parsed`, `no_ccn` |
| Open Payments | `open_payments_research_status` | `published_records`, `no_published_records`, `no_ccn` |

- Medicare counts below 11 are suppressed by CMS, so the smallest published value is 11 and 0 never appears. Medicare blanks for 2021 to 2023 are not split by reason.
- 3 New York hospitals in the table have no APR-DRG 011 record (`in_state_none_in_file`). California lists a hospital only when MS-DRG 018 is in one of its top-25 tables, so `in_state_drg018_not_in_top25_table` (5 hospitals) is not evidence of zero.
- In the price-file columns, an `_exact_n` column is the sum of exact remittance counts and stays blank when the file has no exact row. A `_masked_rows` column counts file rows whose count is masked as 1 through 10; it is 0 at a parsed hospital with no such row. Masked rows are never added to cases.
- A blank `bmtinfonet_n_2024_band` means no matched program shows a 2024 number; the band `0` means a reported zero.
- Open Payments dollars are blank when the CCN is the teaching hospital on no research record, and 0.00 when it has records but none match. The table `cart_center_teaching_hospital_payments_2019_2025.csv` writes 0.00 with its own status column in the first case; the amounts agree on all 173 centers.

### What is not in the tracked table

The search also reconciled two manufacturer center locators (Kite and Bristol Myers Squibb). Their terms limit reuse, so nothing derived from them is tracked: no column, no value computed with them, and no row for a site that is in the search table only because a locator lists it (27 rows, written whole to `Data/interim/2026-11-19/restricted_terms/cart_site_volume_panel_manufacturer_list_only__restricted.csv`). The table is therefore not a complete list of treating sites outside the center set. The build rebuilds the tracked table without any manufacturer record and requires an identical result.

The BMT InfoNet site reserves all rights and is silent on reuse, so the table carries its list flag, program count, directory record numbers and 2024 size bands, but not the exact yearly counts or text copied from the site. These columns are written to `Data/interim/2026-11-19/restricted_terms/cart_site_volume_panel__restricted.csv`, which joins on `site_key`. Git ignores both local files.

### Limits

- No column is specific to lymphoma. MS-DRG 018 and APR-DRG 011 include myeloma and leukemia CAR-T and other cell therapies; BMT InfoNet counts cover all indications; Open Payments amounts include myeloma products.
- The columns come from different sources, years and definitions, and they overlap. Do not add them across sources.
- Medicare columns cover fee-for-service inpatient stays only: no Medicare Advantage and no outpatient infusions.
- State columns are inpatient stays at a licensed facility mapped to a CCN by hand. The New York Medicare payer group includes Medicare Advantage. New York cells below 11 are published as counted from the public record-level file.
- Price-file columns count remittances, including partial and adjustment payments, over the 12 to 15 months before each file was posted. They cover 7 hospitals parsed in the first search pass, and the payer rule recognises traditional Medicare in the two Mass General Brigham files only. The Stanford and Emory file dates were not recorded.
- BMT InfoNet counts are self-reported and unaudited, and the site does not define them. The records of 340014 and 340053 show identical numbers in all five years and may be one program counted twice (`bmtinfonet_duplicate_suspect_site_key`).
- Presence on the directory records a listing on 2026-11-19, not proof of treatment. Of the 277 directory records, 113 were matched to a site by name similarity and 47 by hand, verified by spot checks only; four judgement calls carry a `bmtinfonet_match_review_note`.
- Rows are CCNs, not programs. Children's programs that share a hospital CCN are summed into that hospital's row.
- Open Payments amounts are as reported by manufacturers, and count only research paid to the hospital itself, not to a university or institute.

Attribution: Centers for Medicare & Medicaid Services (Medicare inpatient file, Hospital Enrollments, Open Payments); New York State Department of Health (SPARCS de-identified file, Open NY terms of 2013-03-08); California Department of Health Care Access and Information (Creative Commons Attribution); each hospital's price transparency file; BMT InfoNet directory (read 2026-11-19); Foundation for the Accreditation of Cellular Therapy (accreditation flags through `cart_centers`).

## 340B status of the CAR-T centers and orphan designations

Two tables under `Data/processed/2026-11-19/` describe where the 173 CAR-T center
hospitals stand in the federal 340B drug discount program, and why that matters
for CAR-T. 340B lets eligible hospitals buy outpatient drugs at a reduced price.
Both tables are built by `kolproject.volume_billing.status_340b`; the second is a
small hand-compiled reference list.

| Table | One row represents | Rows |
|---|---|---|
| `cart_center_340b_status.csv` | CAR-T center hospital, keyed by CMS Certification Number (CCN) | 173 |
| `cart_product_orphan_designations.csv` | CAR-T product and indication with an orphan designation stated in an FDA document | 16 |

Join `cart_center_340b_status.csv` to `Data/processed/2026-10-24/cart_centers.csv`
on `ccn` for the hospital name, state and Medicare discharge count. Those columns
are not repeated here.

### What was collected

| Source | What it gives | Version |
|---|---|---|
| Health Resources and Services Administration (HRSA) 340B registry, covered-entity daily export | Registration records of each hospital and its child sites | Export of 2026-06-23: 93,022 records, of which 60,199 are hospital records over 3,201 Medicare provider numbers |
| CMS Addendum AAA of the final rule on the remedy for 340B-acquired drug payment (CMS-1793-F) | Lump-sum remedy amounts per hospital for 2018 to 2022 | Files dated 2023-10-27: 4,785 providers, national total $9,003,794,720.75 |
| CMS page of cancer hospitals exempt from the inpatient prospective payment system (PPS) | The 11 exempt cancer hospitals | Page last modified 2026-03-17 |
| FDA approval letters, summary documents and two announcements | Orphan-designation statements | 23 saved PDF files and 2 saved pages |

The registry copy came through a third-party GitHub mirror, not from HRSA
directly. The official Reports page answered on 2026-11-19, but it hands out the
export through an interactive button, so the official file was not fetched and
the mirror copy is not confirmed against it. The mirror's two files agree with
each other on the 340B ID, entity type, participating flag and start date of all
93,022 records. The mirror files stay local; only per-center values are
published.

In the registry a hospital has one parent record (its own registration) and a
child-site record for each off-site clinic. Centers are matched on the Medicare
provider number and the parent 340B ID in the registry file. For the 34 centers
with no participating parent record at their own CCN, the route rests on a manual
search of child sites; the two records that start on 2026-07-01 are also found by
rule. Those findings sit in a reviewed input file, one row per center, and the
build checks each row against the registry.

### How to read the main columns

`route_340b` says how a center reaches the program on 2026-06-23:

| Route | Meaning | Centers |
|---|---|---|
| `direct` | Participating parent record at the center's own CCN | 139 |
| `sibling` | No participating record at its own CCN; a participating child site of another hospital in the same health system sits at or beside the center, or names its transplant or cell-therapy clinic | 7 |
| `pending_direct` | Parent record at its own CCN that starts on 2026-07-01 | 2 |
| `outside` | None of these | 25 |

`entity_type_340b` is the HRSA hospital type of the supporting record: DSH
(disproportionate share hospital), PED (children's hospital), RRC (rural referral
center) or CAN (free-standing cancer hospital). Of the 139 direct centers, 106
are DSH, 22 PED, 8 RRC and 3 CAN.

`class_340b` turns the type into what the law allows for an outpatient CAR-T dose:

| Class | Meaning | Centers | 2024 Medicare MS-DRG 018 discharges |
|---|---|---|---|
| A | DSH or PED: a 340B price is legally possible | 136 | 1,086 (79.9%) |
| B | CAN, RRC, SCH (sole community hospital) or CAH (critical access hospital): drugs with an orphan designation, which every CAR-T product has, are excluded from 340B pricing unless the manufacturer offers it voluntarily | 12 | 141 (10.4%) |
| C | Outside 340B | 25 | 133 (9.8%) |

The discharge counts come from `cart_centers.csv` and cover the 49 centers with a
published count (1,360 discharges).

**As-of rule.** The export already holds changes scheduled for 2026-07-01.
`class_340b` keeps the counts the 2026-11-19 search reported: it applies the two
records that start on that date (CCNs 170122 and 410004) and not the two that end
on it (310001 loses its DSH record with no successor; 520037 changes from DSH to
SCH, sole community hospital). `scheduled_registry_change` names all four so a
reader can apply a consistent rule:

| Rule | A / B / C centers | A / B / C discharges |
|---|---|---|
| As published | 136 / 12 / 25 | 1,086 / 141 / 133 |
| Strictly on 2026-06-23 | 135 / 11 / 27 | 1,086 / 141 / 133 |
| With all four scheduled changes | 134 / 13 / 26 | 1,042 / 141 / 177 |
| Participating records at the center's own CCN on 2026-06-23 only | 128 / 11 / 34 | 1,049 / 141 / 170 |

### Headline counts

- 139 of 173 centers have a participating parent record at their own CCN; 5 have
  records but none participating; 29 have no hospital record.
- 4 of the 7 sibling rows rest on a child site that names a bone marrow transplant
  or cell-therapy clinic (`sibling_site_names_cell_therapy` = 1); 3 rest on a
  general site at or next door to the center's address.
- 10 of the 11 PPS-exempt cancer hospitals are centers: 3 in class B and 7 in
  class C. None has a Medicare discharge count.
- 172 centers are listed in the CMS remedy file and 121 have an amount above
  zero, totalling $3,690,522,403.29, which is 41.0% of the national total. By
  class: A $3,600,363,684.62 (108 centers), B $84,326,352.64 (8), C
  $5,832,366.03 (5).
- All 7 CAR-T products carry an orphan designation in an FDA document. 10 of the
  16 rows state a date, from 2014-01-31 (Kymriah, acute lymphoblastic leukemia)
  to 2019-02-01 (Carvykti).

### Blanks and zeros

- `entity_type_340b`, `evidence_340b_id`, `evidence_ccn` and
  `evidence_start_date` are blank when `route_340b` is `outside`.
- `sibling_site_names_cell_therapy` is blank unless the route is `sibling`.
- `scheduled_registry_change` is blank when the export holds no later change for
  the center. A future-dated record that ends on the day it starts is void and is
  not counted (CCN 030122).
- `status_note` is blank for direct rows.
- Remedy amounts are blank only when the CCN is not in the CMS file
  (`in_cms_340b_remedy_file` = 0; one center, 443302). `0.00` means listed with
  no amount. 51 centers are listed at zero. Zero is expected for hospital types
  exempt from the payment cut and for hospitals not in 340B in those years, so a
  zero does not by itself mean the hospital was outside 340B.
- `designation_date` in the orphan table is blank when the FDA document confirms
  the designation without a date; `designation_date_precision` then reads
  `not_stated`.

### Limits

- Class A means a 340B price is legally possible for the hospital type. No source
  shows that any CAR-T dose was bought at a 340B price at any center.
- 340B covers outpatient drugs only, and most CAR-T infusions are inpatient
  stays. The discharge shares show where Medicare inpatient volume sits, not
  volume eligible for 340B.
- Class B does not mean no discount. A manufacturer may offer 340B pricing on an
  orphan drug voluntarily; whether any CAR-T manufacturer does is unknown.
- The registry columns describe one day, 2026-06-23, five months before this
  snapshot. `evidence_start_date` is the start of the current registration, not
  the first date the hospital joined 340B.
- The sibling and outside rows rest on a manual search. In every sibling row the
  registration belongs to another hospital that is not among the 173 centers.
  CCN 490101 is probably a center-set error for 490063, and CCN 500138 is left
  outside by judgement; `status_note` says so for both.
- Remedy amounts are CMS estimates for all 340B-acquired outpatient drugs on 2018
  to 2022 claims, including the beneficiary copayment share. They are not
  purchases or savings and have no CAR-T breakout. `remedy_2022_net_usd` is what
  was still owed after earlier repayment and is not comparable with the 2018 to
  2021 columns; `remedy_2022_withheld_usd` is the comparable part-year figure.
- The orphan table is hand-compiled from FDA documents. It is not a complete list
  of designations or of approved indications. Each quote was found word for word
  in a saved FDA document of the same product; the search did not record which
  saved file came from which address.

`kolproject-evaluate` repeats 63 checks on these tables, including the class
counts under each as-of rule, the discharge sums by class, the remedy totals, and
a fingerprint of each block of published cells so that an edit which leaves every
total unchanged is still noticed.

## CAR-T treater evidence by NPI

Two tables under `Data/processed/2026-11-19/` record which individual National Provider Identifiers (NPIs) are named in a public, non-claims source as involved with chimeric antigen receptor T-cell (CAR-T) therapy. They are a screen of who is named. They do not show who infuses CAR-T and they are not a measure of volume.

| Table | One row represents | Rows |
|---|---|---|
| `npi_cart_treater_evidence.csv` | NPI with at least one published evidence type | 2,366 |
| `division_relabel_candidates.csv` | Universe NPI labelled `referring_proxy` or `hemonc_other` with evidence other than service payments | 147 |

Of the 2,366 NPIs, 1,846 are in the 17,510-NPI universe (`hcp_divisions.csv`, 2026-10-24) and 520 are outside it (`division = outside_universe`). Names, state and specialty of universe NPIs are not repeated; join on `npi`.

### What was collected

| Flag | Source | How the NPI was reached | NPIs |
|---|---|---|---|
| `ev_bmt_roster` | BMT InfoNet (Blood & Marrow Transplant Information Network) CAR T-cell center directory, pages read 2026-11-19 | Name and state match | 1,562 |
| `ev_op_cart_services` | Centers for Medicare & Medicaid Services (CMS) Open Payments General records, 2019–2025 | NPI on the record | 850 |
| `ev_op_cart_research_pi` | CMS Open Payments Research records, 2019–2025, principal-investigator slots | NPI on the record | 493 |
| `ev_ctgov_investigator` | ClinicalTrials.gov, 337 CAR-T lymphoma trials kept from 402 records processed 2026-11-19 | Name and state match | 316 |
| `ev_medicare_cell_therapy` | Medicare Physician & Other Practitioners files: code 0540T rows (2013–2024) or the cell-therapy provider type (2019–2024) | NPI on the record | 215 |
| `ev_fact_director` | Foundation for the Accreditation of Cellular Therapy (FACT) program directors, listing observed 2026-10-25 | Name and state match | 180 |

The trial filter and the name matching are this project's modifications of ClinicalTrials.gov data. Open Payments amounts are as reported by manufacturers. Outside-universe names and states come only from the National Plan and Provider Enumeration System (NPPES) registry, as cached on 2026-11-19.

The tables are rebuilt in code from the saved search files. The rebuild reproduces the search's own table on all 2,413 NPIs before the fixes below are applied.

### Changes from the search table

- **2 rows removed** because their only evidence was a name link judged false.
- **45 NPIs left out** because their only evidence was a Kite treatment-team listing. The Kite site terms restrict reuse, so Kite evidence is kept in local files only. Counts and the sort aid ending in `_public` are computed without it.
- The five per-physician allocation columns were dropped. A center total divided across listed names is not a physician's volume.
- Columns once called "delivery" evidence are now `..._excl_services`; they count listings, trial roles, research slots and Medicare markers, not treatment.
- Link confidence uses the best match per source. Rows with no name matching at all are labelled `npi_keyed_only`.
- Company service counts are now distinct program years (1 to 7).
- `taxonomy_class` uses one set of labels whether the class comes from the registry or from Open Payments.

### Headline counts

- 381 of the 500 core healthcare professionals (HCPs) have a row; 347 have evidence other than service payments, and 340 keep it when weak name links are discarded. 119 have no row.
- 1,933 NPIs have evidence other than service payments; 606 have two or more such types.
- 433 NPIs have service payments as their only published evidence.
- Link confidence: 1,571 high, 680 NPI-keyed only, 105 low, 10 mixed.
- 177 rows carry `needs_review = 1`.
- Review list: 147 candidates (78 `hemonc_other`, 69 `referring_proxy`); 143 remain under strict name links.

### Over-inclusion flags

| Flag | Meaning | NPIs |
|---|---|---|
| `flag_bmt_pediatric_program_only` | Listed only at pediatric programs | 355 |
| `flag_services_company_arm_only` | Service flag rests only on payments from Kite, Legend Biotech or Autolus | 120 |
| `flag_services_legend_autolus_only` | Of those, no Kite payment (myeloma or leukemia company) | 75 |
| `flag_medicare_specialty_only_public` | Medicare cell-therapy provider type is the only non-service evidence | 80 |
| `flag_non_physician_taxonomy_class` | Nurse practitioner, physician assistant, nurse, pharmacist, trainee or similar | 67 |
| `flag_ctgov_context_only` | Matched only on trials that mention CAR-T without giving it | 52 |
| `flag_bmt_centers_in_two_states` | Rostered at centers in two states | 4 |

For an adult lymphoma reading, exclude at least the pediatric-only listings: 56 of the 147 review candidates and 20 core HCPs carry that flag.

### Reading blanks

- An `ev_` flag of 0 means no accepted match, not confirmed absence.
- `op_lymphoma_only_cart_services_usd_2019_2025` is blank for outside-universe NPIs because it was not computed for them; `0.00` is an observed zero. The outside-universe dollar column is blank for universe NPIs for the same reason.
- `medicare_0540t_year_services` is blank when CMS published no row. CMS withholds rows with 10 or fewer beneficiaries, so blank is not zero.
- `nppes_` columns are blank for universe NPIs and for 132 outside-universe NPIs with no registry record attached; `identity_source` says which. `nppes_primary_taxonomy` is blank for 4 more whose registry record gives a taxonomy code without a description.
- A blank match-status column means that source did not link the NPI.

### Limits

- Absence is not evidence of non-involvement. Some centers list few physicians, pivotal trials list no investigators in the registry, and 255 name mentions in the published sources (BMT InfoNet, FACT, ClinicalTrials.gov) could not be tied to an NPI.
- BMT InfoNet, FACT and the Medicare markers cover all indications, adult and pediatric. Only the Open Payments lymphoma-brand fields and the trial filter are lymphoma-specific.
- Name links rest on name and state. `high` is not verified identity, and the best match per source can hide a weak link to one center or trial. Check `needs_review` rows by hand before changing any label.
- `evidence_score_public` is an arbitrary additive sort aid; its formula is in the data dictionary.
- Research and service flags skew toward Kite and Johnson & Johnson relationships, whose products dominate the CAR-T records. 153 of the 493 research NPIs have no lymphoma-brand record.
- Rosters are self-reported, undated and stale in places. Listings at centers that report no CAR-T therapies cannot be separated in the tracked table.
- The 520 outside-universe rows are not cohort HCPs; 250 are pediatric hematology-oncology physicians.
- `division_relabel_candidates.csv` is a review queue, not a decision.

### Checks

`check()` in `src/kolproject/volume_billing/treater_roster.py` reads only `Data/processed/`. It tests the keys and joins (`hcp_divisions.csv`, `cart_centers.csv`, and BMT InfoNet identifiers against `cart_site_volume_panel.csv`), recomputes every flag, count and the sort aid from the detail columns, rebuilds the review list from the evidence table, and compares every headline count, column totals and a fingerprint of each table with the values recorded for this snapshot.

### Local files not in Git

`Data/interim/2026-11-19/restricted_terms/npi_cart_treater_evidence__restricted.csv` holds 14 columns keyed by `npi`: Kite evidence, counts that include it, and center names, role titles and program names as the sites write them. `npi_cart_treater_evidence_kite_only__restricted.csv` holds the 45 Kite-only NPIs.

### Rebuild

```bash
uv run python -m kolproject.volume_billing.treater_roster
```

## Open Payments for the universe and the centers, 2019–2025

These tables extend the 2026-11-11 Open Payments tables from the 500 core HCPs to all 17,510 NPIs (National Provider Identifiers) of `Data/processed/2026-10-24/hcp_divisions.csv`, and to the 173 hospitals of `cart_centers.csv`. They are rebuilt from the same seven program-year archives of the CMS (Centers for Medicare & Medicaid Services) Open Payments publication of 2026-06-30. No new download was made. The 2026-11-11 tables are unchanged, and the core rows here reproduce them.

### Tables

| Table | One row represents | Rows |
|---|---|---|
| `universe_cart_general_payments_2019_2025.csv` | General Payment record to a universe NPI that names a CAR-T product or is paid by a CAR-T-only company | 39,655 |
| `hcp_universe_general_payment_features.csv` | Universe NPI, seven-year General Payment summary, zero-filled | 17,510 |
| `hcp_universe_research_payment_features.csv` | Universe NPI, seven-year Research Payment summary | 17,510 |
| `cart_industry_payments_by_division_2019_2025.csv` | Record rule × division group × nature class | 96 |
| `cart_center_teaching_hospital_payments_2019_2025.csv` | CAR-T center (CCN, CMS Certification Number), payments to the teaching hospital, zero-filled | 173 |

Per-NPI tables carry no name, place or division. Join `hcp_divisions.csv` on `npi`, and `cart_centers.csv` on `ccn`.

### What was collected

| Program year | General rows scanned | Rows to a universe NPI | Rows kept in the row-level table | Research rows scanned | Research records naming a universe NPI |
|---|---|---|---|---|---|
| 2019 | 10,558,933 | 429,262 | 3,543 | 807,295 | 159,956 |
| 2020 | 5,851,438 | 205,377 | 2,603 | 760,295 | 165,143 |
| 2021 | 11,558,469 | 258,197 | 3,864 | 894,317 | 132,945 |
| 2022 | 13,322,266 | 353,098 | 5,048 | 1,011,616 | 137,285 |
| 2023 | 14,734,121 | 422,039 | 5,708 | 1,092,474 | 159,752 |
| 2024 | 15,498,687 | 452,041 | 5,902 | 817,215 | 167,298 |
| 2025 | 16,131,856 | 463,438 | 12,987 | 931,959 | 218,340 |

Both detail files of each archive were scanned in full (87,655,770 General rows and 6,315,171 Research rows). The row counts equal the 2026-11-11 scans. NPIs and CCNs are matched by exact equality.

### Record rules

Each row of the row-level table carries the first four as flags; the fifth is their combination. The per-NPI and division tables are sums over them. The division table has six `rule` values: these five and `cart_only_named_product`, which is `cart_only` without the records matched only by a development code. Its `nature_class` has a fourth value, `all`, the sum of the three classes.

| Rule | Meaning | Records | USD | NPIs |
|---|---|---|---|---|
| `any_slot` | A CAR-T (chimeric antigen receptor T-cell) product is named in at least one of the five product fields; other products may be named too | 29,750 | 17,803,679.45 | 5,082 |
| `cart_only` | Every named product is a CAR-T product | 23,563 | 13,215,749.36 | 4,638 |
| `lymphoma_only` | `cart_only`, and the product is Yescarta, Tecartus, Kymriah or Breyanzi | 16,725 | 11,348,829.12 | 3,648 |
| `pure_play_payer` | Paid by Kite Pharma, Legend Biotech or Autolus, whatever the product fields say | 24,860 | 11,534,540.33 | 4,455 |
| `cart_only` or `pure_play_payer` | Either rule, each record once | 33,468 | 15,612,193.08 | 5,301 |

Products are matched by brand, generic or development name of seven approved products, with the same patterns as the 2026-11-11 tables. `development_name_only` marks the 471 records ($447,824.08, all in 2019) matched only by the codes JCAR017 or BB-2121. `direct_payment` is 1 when the CMS third-party indicator says the payment went to the recipient; 536 records in the table went to a third-party entity instead.

`nature_class` groups the CMS nature of payment: `services` (consulting, honoraria, speaking and faculty compensation), `hospitality_education` (food and beverage, travel and lodging, education, gift, entertainment) and `other_financial` (everything else, such as grants and royalties). It is not the `advisory` grouping of the 2026-11-11 tables. No record that names a CAR-T product has an `other_financial` nature, so under the three product rules the dollars that are not services are hospitality and education.

### Headline counts

- 16,317 of 17,510 universe NPIs have at least one General Payment record: 2,583,452 records, $692.9 million. Services account for $552.2 million across 7,294 NPIs. 31,152 records ($55.1 million) were paid to a third party.
- Direct CAR-T-only services payments (`cart_only`, `services`, direct) by division group:

| Division group | NPIs in group | NPIs paid | Direct USD | Median per paid NPI | Paid to a third party, USD |
|---|---|---|---|---|---|
| core | 500 | 200 | 4,944,607.60 | 12,761.25 | 376,415.70 |
| affiliates | 9,548 | 338 | 4,475,902.33 | 5,000.00 | 480,440.50 |
| referring | 4,632 | 148 | 727,654.80 | 3,337.50 | 35,575.00 |
| other | 2,830 | 28 | 278,690.20 | 4,522.85 | 21,212.50 |

  Affiliates are the labels `affiliate_A`, `affiliate_B`, `affiliate_C` and `center_affiliate_unlinked`; referring is `referring_proxy`; other is `hemonc_other`. Without the development-code records the first three rows are 196 NPIs / $4,740,660.35, 326 / $4,352,677.33 and 142 / $674,472.30.
- 8,072 universe NPIs are named on a research record, through 1,142,639 NPI-record links: 1,118,148 as investigator and 24,491 as recipient only. 419 NPIs have a research record that names a CAR-T product in a product field (11,587 links); 397 of them as investigator.
- 160 of the 173 centers received General payments as a teaching hospital (124,749 records, $5.69 billion, of which $4.56 billion is royalty or licence and $1.48 billion falls in program years 2024 and 2025); 145 of them have records in all seven years. 158 received research payments (625,224 records, $9.63 billion); 62 have research records naming a CAR-T product (4,400 records, $95.2 million).
- For the 500 core HCPs, General rows, dollars, CAR-T rows and dollars, brands, active years and the three nature classes equal the 2026-11-11 General tables for all 500. Research rows, dollars, active years and the product-or-text CAR-T rows and dollars equal `hcp_research_payment_features.csv` of 2026-11-11 for all 500.

### How to read zeros and blanks

- Counts and General dollar columns are never blank. Zero means the full scan of this publication found no such record. `general_data_status` and `research_data_status` say `published_records` or `no_published_records`.
- In the per-NPI research table, dollar sums are blank when the count beside them is zero, as in the 2026-11-11 research table. The center table is zero-filled throughout, research dollars included.
- `cart_products` is blank on a row-level record that is in the table only because of its payer. `products` is blank when the manufacturer named no product.
- In the center table, `no_published_records` means the CCN never appears as a teaching hospital in the file. The files do not say whether the hospital is absent from the CMS teaching-hospital list, listed and unpaid, or registered under a sibling CCN. This applies to 13 centers for General and 15 for Research payments.
- `direct_median_usd_per_paid_npi` is blank when no NPI in the cell has a direct payment.

### Limits

- Amounts are reported by manufacturers and not verified by CMS. They are not payer reimbursement, product volume or evidence of Medical Science Liaison contact.
- Every CAR-T figure is a floor. Kite Pharma leaves the product fields blank on 9,150 of its 22,323 records to universe NPIs, Legend Biotech on 587 of 2,371 and Autolus on all 166. The `pure_play_payer` rule recovers those, but it cannot see CAR-T payments by Bristol Myers Squibb, Novartis or Janssen that name no product. It also counts 2 Kite Pharma records that name only a product that is not a CAR-T therapy.
- `any_slot` includes 6,187 records ($4.59 million) that name a CAR-T product beside another product. 6,167 are Janssen-group records naming Carvykti, and 4,212 of the 6,187 fall in 2025. Use `cart_only` or `lymphoma_only` for CAR-T totals.
- `lymphoma_only` is a product rule, not an indication rule. The four products are also used for other diseases.
- Direct figures leave out payments routed to a third party. On CAR-T-only services these add 7.6% for core, 10.7% for affiliates and 4.9% for referring.
- Division labels are proxies. Some `referring_proxy` NPIs practise at treating sites.
- Research amounts are whole-record amounts, mostly paid to institutions and repeated for every investigator. They are not personal income and must not be added across NPIs. Recent years are a floor because publication can be delayed up to four years.
- The center table counts only payments to the hospital as a covered teaching hospital. Research paid to a university or research institute is a separate record type and is not in it; in the whole file such records naming a CAR-T product number 7,062 ($115.7 million), more than the 5,019 ($108.2 million) paid to teaching hospitals.
- The manufacturer's free-text context and the name of any third-party payee are not published.
- Zero is an observed count for the 2026-06-30 publication only. CMS revises records at each refresh.

### Validation

`kolproject.volume_billing.industry_payments.check` reads only `Data/processed` and runs 30 checks: keys and joins to the universe and center tables, cell formats, record flags recomputed from the published fields, class and direct-plus-third-party sums, the division table recomputed from the row-level table, equality with the 2026-11-11 tables for the 500 core HCPs, the totals quoted above, and a fingerprint of every cell of each table. The totals and fingerprints belong to the 2026-06-30 publication and must be replaced when the tables are rebuilt on a later one.

Source: CMS Open Payments, General Payment and Research Payment detail files, program years 2019–2025, publication of 2026-06-30, retrieved 2026-11-12. United States Government work, public domain.

## Medicare Part B payments by division, 2019–2024

Three tables under `Data/processed/2026-11-19/` describe what Medicare fee-for-service Part B paid for services and drugs rendered by the 17,510 clinicians of the universe table `Data/processed/2026-10-24/hcp_divisions.csv`. They are built by `kolproject.volume_billing.medicare_payments` from files of the Centers for Medicare & Medicaid Services (CMS). Clinicians are identified by National Provider Identifier (NPI); names, state and division are not repeated, so join on `npi`.

### Tables

| Table | One row represents | Rows |
|---|---|---|
| `hcp_medicare_partb_payments_by_year.csv` | NPI × service year 2019–2024, complete grid, not zero-filled | 105,060 |
| `hcp_medicare_service_product_features_2024.csv` | NPI, with four 2024 sums and two status columns | 17,510 |
| `medicare_partb_drug_rows_2024.csv` | NPI × drug billing code × place of service, 2024 | 46,191 |

### What was collected

| Source (data.cms.gov) | Service year | Version identifier | How obtained |
|---|---|---|---|
| Medicare Physician & Other Practitioners – by Provider | 2019 | `6a53afe5-1cbc-4b33-9dc8-926ee532dc66` | Search pull of 2026-11-19, 12,337 rows |
| same | 2020 | `29d799aa-c660-44fe-a51a-72c4b3e661ac` | Search pull, 12,814 rows |
| same | 2021 | `44e0a489-666c-4ea4-a1a2-360b6cdc19db` | Search pull, 13,331 rows |
| same | 2022 | `21555c17-ec1b-4e74-b2c6-925c6cbb3147` | Search pull, 13,811 rows |
| same | 2023 | `8ba584c6-a43a-4b0b-a35a-eb9a59e3a571` | Search pull, 14,342 rows |
| same | 2024 | `4d0b2df0-1e99-4db7-a574-a571d99217f1` | 14,059 rows from queries saved on 2026-10-25, plus 856 rows from 35 requests made on 2026-11-19 for the 3,451 NPIs with no saved row |
| Medicare Physician & Other Practitioners – by Provider and Service | 2024 | `335e5f35-eca6-482d-87b3-f99883e213e3` | Search pull of the released drug rows of universe NPIs (46,191 rows; see Limits for which NPIs were queried); code queries for 3 administration codes and 25 product codes |
| Medicare Part D Prescribers – by Provider and Drug | 2024 | `d5aa71a8-dcc0-4570-8bcf-bd39deac69fe` | Read from `part_d_lymphoma_orals_2024.csv` of the 2026-10-24 snapshot |

Every year is read from its pinned version, never from the "latest" alias of the series. The search had pulled 2024 through the alias; that file is kept only as a cross-check and agrees with the pinned rows on all 14,915 NPIs. Request times were not recorded for the search's own pulls (2019–2023 by-Provider and the 2024 drug rows): only the saved response files and their SHA-256 hashes exist, and the build stops if a hash changes. Requests made by this module are saved one response per file with hash and UTC time. A rebuild reads only the frozen copies under `Data/raw/2026-11-19/` and reproduces the three tables byte for byte. CMS data are works of the United States Government in the public domain; attribution to CMS is given here. Billing codes are published as code numbers with this project's own labels, without descriptor text.

### Headline counts

- 81,550 of 105,060 NPI-years have a CMS row. Rows per year rise from 12,337 (2019) to 14,915 (2024); 11,427 NPIs have a row in all six years and 2,054 have none in any year.
- 2024 status: 13,770 NPIs with drug and non-drug payment published, 1,145 with the total only, 2,595 with no row.
- 2024 universe total $9,324.7 million: drug $7,733.4 million, non-drug $1,494.8 million, and $96.6 million on rows where CMS hides the split.

| 2024, by division group | NPIs | With a CMS row | Total payment | Drug payment | NPIs with published drug payment above zero | NPIs with drug side hidden (1 to 10 beneficiaries) |
|---|---|---|---|---|---|---|
| Referring (`referring_proxy`) | 4,632 | 4,504 | $5,560.2M | $4,766.7M | 2,490 | 416 |
| Affiliates (`affiliate_A`, `affiliate_B`, `affiliate_C`, `center_affiliate_unlinked`) | 9,548 | 8,301 | $3,582.4M | $2,884.8M | 1,759 | 473 |
| Core (`core`) | 500 | 414 | $71.6M | $50.0M | 58 | 13 |
| Other (`hemonc_other`) | 2,830 | 1,696 | $110.6M | $31.9M | 189 | 242 |

- Drug share in 2024 is 86%, 82% and 72% for referring, affiliates and core when computed as drug divided by drug plus non-drug on rows with a published split, and 86%, 81% and 70% when computed as drug divided by total payment.
- Referring drug payment grew from $3,815.2 million (2019) to $4,766.7 million (2024), +25%, on all rows, but +15% ($3,791.2 million to $4,360.0 million) on the 3,839 referring NPIs present in all six years.
- Drug rows: 46,191 rows for 4,011 NPIs and 221 codes, all in the non-facility setting. Their estimated payment is $2,705.5 million, 35% of the 2024 by-Provider drug payment.
- Sums in the feature table: lymphoma product codes 390 NPIs, $35.7 million; chemotherapy administration (codes 96413, 96415, 96417) 3,669 NPIs, $119.2 million; Part D lymphoma orals 7,828 NPIs, $9,365.5 million gross drug cost.

Every figure above is recomputed to the cent from the published rows by the module's `check()`, which also compares each table with a fingerprint of its cells as built.

### How to read blanks

A blank is never a zero in these tables.

- `by_provider_status = no_row`: CMS has no row for the NPI that year, which happens with 10 or fewer fee-for-service beneficiaries or no claims as rendering provider. All amounts and counts are blank.
- `by_provider_status = split_suppressed`: the total is published but drug and non-drug payment and their beneficiary counts are blank, because one side rests on 1 to 10 beneficiaries. A `*` in `drug_suppression_indicator` means the NPI did bill drugs, for 1 to 10 beneficiaries; a `#` means the side is hidden only to protect the other side. These two codes are stored as CMS writes them, so a CSV reader must not treat `#` as a comment mark.
- `by_provider_status = published_split`: a `0.00` in `drug_medicare_payment_usd` is published by CMS and is a true zero.
- In the feature table each sum is blank when CMS released no cell for it (a cell needs 11 or more beneficiaries, or 11 claims for Part D). `partb_drug_rows_status` says why the drug-row sum is blank: `no_released_rows` (485 NPIs with published drug payment but every code cell under 11 beneficiaries), `drug_payment_zero` (9,274), `split_suppressed` (1,145) or `no_by_provider_row` (2,595).
- No hidden value is recovered by subtraction.

### Limits

- Medicare fee-for-service Part B non-institutional claims only. Medicare Advantage, Medicaid, commercial payers, hospital facility billing and durable medical equipment are absent.
- Dollars follow the rendering NPI but are paid to the billing practice. A hospital-employed physician's drugs are billed by the hospital and show as a published zero, so differences between divisions show who bills drugs under their own NPI, not who treats more patients.
- Division labels and the universe are a 2024 snapshot applied to every year. Use the 11,427 NPIs with a row in all six years for trend statements. The fall of the "other" group over time is an effect of the labelling.
- The referring label is defined by the same 2024 markers that feed the administration, lymphoma-product and Part D sums, so gaps between referring and other NPIs on those sums hold by construction.
- "Drug" means codes on the Medicare Part B Average Sales Price file of that year, which CMS says is not a complete list of Part B drugs; "non-drug" is everything else, including laboratory and imaging.
- A count of NPIs with published drug payment is not a count of NPIs billing any Part B drug; add the NPIs with the drug side hidden (table above).
- The three code-level sums are estimates (CMS average payment times services) on released cells only, so they are floors. Rituximab, obinutuzumab and bendamustine are used outside lymphoma, lenalidomide is mostly a myeloma drug, and CAR-T product codes have no NPI-level row nationally in 2024. No column measures lymphoma or CAR-T activity.
- Part D cost is the gross cost of prescriptions at the pharmacy, not money to the prescriber, and must not be added to Part B payments.
- Amounts are nominal payments after deductible and coinsurance, not practice revenue. `cms_provider_type` is the claims specialty CMS assigns per year and can change between years; do not carry the 2024 value back. The 2021 file includes COVID-19 vaccination claims for Medicare Advantage enrollees.
- Released drug rows were requested for the first 2,480 NPIs in sorted order whatever their status, and for the others only when the 2024 drug payment was published and above zero. For the rest the blank follows from the CMS release rule (no code cell can reach 11 beneficiaries); among the NPIs queried in full, none showed a drug row outside that condition.

## Medicaid paid amounts for the HCP universe, 2018–2024

Four tables under `Data/processed/2026-11-19/` describe what Medicaid paid on claim lines that name one of the 17,510 universe health care professionals (HCPs) as servicing or billing provider. They come from one federal file and are lower bounds, not totals.

| Table | One row represents | Rows |
|---|---|---|
| `hcp_medicaid_payments_by_year_2018_2024.csv` | Universe HCP × calendar year with at least one released row | 54,745 |
| `hcp_medicaid_payment_features_2018_2024.csv` | Universe HCP, observed or not | 17,510 |
| `medicaid_payments_by_division_year_2018_2024.csv` | Join side × division or division group × year | 154 |
| `hcp_medicaid_center_billing_links_2018_2024.csv` | Universe HCP × CAR-T center whose organization NPI billed for that HCP | 1,816 |

HCPs are keyed by National Provider Identifier (NPI) and centers by CMS Certification Number (CCN). Names, practice state, specialty and division are not repeated; join `npi` to `Data/processed/2026-10-24/hcp_divisions.csv` and `ccn` to `cart_centers.csv`.

### What was collected

The source is the U.S. Department of Health and Human Services (HHS) Open Data file *Medicaid Provider Spending by HCPCS* (Healthcare Common Procedure Coding System, the billing-code set), release 2026-02-09. It has one row per billing NPI × servicing NPI × procedure code × month from January 2018 to December 2024, and a row is released only when it has at least 12 claim lines and at least 12 beneficiaries. It covers Medicaid and the Children's Health Insurance Program (CHIP), with fee-for-service claims and managed-care encounters mixed and no flag to separate them. It holds outpatient and professional claim lines only and has no state column.

The file (3.07 GB, 238,015,729 rows) was queried remotely on 2026-11-19 and never downloaded whole. The 1,594,231 monthly rows that name a universe NPI on either side were kept. The file was last modified on 6 April 2026, after its release label; its ETag `0x8DE93E6CBECD570` was unchanged when the build re-read the headers. The publisher-stated SHA-256 is recorded but not verified. No licence is posted; the file is treated as a work of the United States Government and attributed to HHS.

Enrollment columns come from the HHS *Medicaid Provider Enrollment Segments* file, release 2026-07-24 (306,889 segments for universe NPIs).

Amounts are summed in whole cents and written with two decimals. The search's own tables were reproduced: every dollar cell agrees within $1 and every count agrees, except the active-enrollment flag, which the search understated and which is now 1 for 145 more NPIs.

### Two sides and two code classes

- **Servicing side**: rows naming the HCP as servicing provider. The money was paid to the billing NPI, usually a group or hospital.
- **Billing side**: rows naming the HCP's own NPI as billing provider. Most of these rows also name the HCP as servicing provider, so the two sides overlap and must not be added. The `self_billed` columns give the overlap.
- **`visit_admin`**: office visits 99202–99205 and 99211–99215, hospital visits 99221–99223 and 99231–99233, and infusion, injection and chemotherapy administration 96360–96379 and 96401–96417.
- **`drug_code`**: codes matching J, Q5, Q20, C9 or S0 followed by digits, plus Q0138, Q0139 and Q0082. This is a pattern over code numbers, mostly supportive-care drugs and fluids. It is not a list of lymphoma or CAR-T products.

Both rules are kept exactly as the search defined them and are in the metadata word for word. `visit_admin` leaves out visit-equivalent codes such as G0463, T1015 and Q0084, which fall in the remaining class.

### Headline counts

- 11,602 of 17,510 universe NPIs have at least one released row: 11,586 as servicing provider and 1,877 as billing provider (16 as billing provider only).
- Servicing-side paid amount, 2018–2024: $1,593.8 million, of which $1,003.6 million `visit_admin` and $209.4 million `drug_code`.
- Billing-side paid amount: $114.9 million, of which $109.9 million is also inside the servicing side.

| Division group, 2024 | NPIs in group | NPIs with a servicing-side row | All codes | `visit_admin` | NPIs with `visit_admin` paid above $0 | `drug_code` |
|---|---|---|---|---|---|---|
| core | 500 | 93 | $754,844 | $592,225 | 81 | $32 |
| affiliates | 9,548 | 3,895 | $70,004,014 | $49,070,242 | 3,396 | $4,194,695 |
| referring | 4,632 | 3,364 | $110,316,254 | $68,985,815 | 3,062 | $18,154,478 |
| other | 2,830 | 1,102 | $30,513,331 | $21,101,456 | 972 | $691,579 |

The `visit_admin` dollars sit on the NPIs in the sixth column, not on every NPI with a row. Group names are `core`, `affiliates` (affiliate_A, affiliate_B, affiliate_C, center_affiliate_unlinked), `referring` (referring_proxy) and `other` (hemonc_other).

- Center billing links: 1,816 pairs covering 1,772 HCPs and 99 of 173 centers, $73.2 million. 1,309 pairs name a center that is among the HCP's hospital CCNs; 196 pairs have gap years; 44 HCPs link to more than one center.
- Enrollment file: 17,117 NPIs appear; 16,974 have an active segment reaching 2024 in any state and plan; 15,569 have one under a MEDICAID or CHIP plan.

### How to read blanks

`medicaid_data_status` is `observed` or `not_observed`. A blank dollar or count cell means no released row exists for that HCP, side, class and period. It does not mean the HCP sees no Medicaid patients: the rows may have been under the release threshold, the claim may carry the organization's NPI, or the state may not report. `0.00` means released rows exist and their paid amounts sum to exactly $0, which is common for managed-care encounters: 134,586 of the 1,594,231 monthly source rows have a paid amount of exactly $0. Thirteen source rows have a negative paid amount (lowest −$393.36) and are summed as they are; no published amount is negative. In the division table, counts of rows, lines and NPIs are 0 when nothing was released, while dollar cells are blank.

### Limits

- **No CAR-T.** The source has no row for any CAR-T product or administration code, and it has no inpatient claims.
- **No lymphoma product dollars.** `drug_code` is not a product measure. The wider list of oncology drug codes in `paid_listed_oncology_drug_usd` includes rituximab and bendamustine codes used well beyond lymphoma.
- **No payer mix.** These rows are cut by month, code and billing pair above a threshold; Medicare totals are annual per NPI. Do not divide one by the other.
- **No physician income.** Servicing-side money went to the billing NPI.
- **No ranking** of HCPs, divisions, centers or states. Visibility depends on monthly volume per code, on how the practice fills the claim, and on the state.
- **No trend.** 2024 is preliminary and cut off: December 2024 holds $1.79 million against $16.72 million for December 2023.
- **States are missing or unreliable.** The file has no state column, so this was measured by the HCP's practice state, which is not necessarily the paying state: Michigan has a row for 17 to 20 of 455 NPIs a year; Utah falls from 42 NPIs in 2022 to 13 in 2023 and 2 in 2024; Florida moves from $2.35 million in 2018 to $36.57 million in 2019 and $11.19 million in 2021; Texas has 235 NPIs in 2018, 445 at its peak and 292 in 2024; Massachusetts stays near a quarter of its 839 NPIs. Causes are unknown.
- A center link shows only that the center's organization NPI billed with the HCP as servicing provider. It does not show employment, CAR-T involvement or referral, and only one organization NPI per center is matched.
- The enrollment flags do not separate divisions and are not proof of participation. `medicaid_enrollment_states` never contains New Mexico or Oregon because their source rows carry no NPI, and the search measured 35 to 72 percent of rows without an NPI in Illinois, Minnesota, Michigan, Wisconsin, California, Washington, Nebraska and Texas.
- Unknown: whether denied claim lines are included, and how much of the visit dollars is Medicaid paying Medicare cost-sharing.

### Checks

`medicaid_payments.check` reads only `Data/processed/` and returns 23 counts, all zero for this build. It checks cell formats, the keys, the joins to `hcp_divisions.csv` and `cart_centers.csv`, that the feature and division tables equal what the NPI × year table gives, the blank-versus-zero rules, every headline number above, and a digest of each table for the cells no rule can rebuild.

Column definitions are in the data dictionary; sources, hashes, both code rules and coverage by state are in the module metadata.
