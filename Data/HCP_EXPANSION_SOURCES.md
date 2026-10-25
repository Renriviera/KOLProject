# Sources for expanding the HCP dataset: referring and affiliate divisions

Status: source inventory, verified 2026-10-24. First collection and labeling run: see `Data/DIVISIONS_README.md`.
Scope: US, CAR-T in lymphoma, NPI as the only identity key, same provenance
discipline as the existing pipeline (dated requests, pinned dataset versions,
raw responses kept locally).

## 1. Why two divisions

The current 500 HCPs were selected from CAR-T lymphoma publications. They are
academic treaters at a few dozen centers (MSK, Mayo, Penn, Fred Hutch, Stanford,
MD Anderson, Moffitt, City of Hope, Ohio State...). The planning sheet lists
"data-source bias (academics over community physicians)" as a mentor trap and
names "referral/co-management links from claims" in the data plan. Two
divisions address that directly:

| Division | Who | Relationship to the core 500 | Primary evidence type |
|---|---|---|---|
| **Core** (existing) | Publication-selected CAR-T lymphoma authors | n/a | Publications, coauthorship |
| **Referring HCP** | Community hematologist/oncologists who treat B-cell lymphoma and are *not* at a CAR-T center | Send or co-manage patients toward the centers where core HCPs practice | Claims-derived patient sharing, office drug billing, prescribing, geography |
| **Affiliate HCP** | Clinicians at the *same* institution, group practice, or trial site as a core HCP (physicians and APPs in hem/onc, BMT, cellular therapy) | Share an employer, hospital, or trial with a core HCP | Enrollment and claims-derived affiliation (PECOS, CMS Doctors and Clinicians) |

Operational definitions proposed:

- **CAR-T center set** = hospital CCNs with MS-DRG 018 discharges in the 2024
  Medicare inpatient file (49 hospitals, ≥11 FFS discharges) plus FACT-accredited
  Immune Effector Cell programs (173 US programs) mapped to CCNs. This set is the
  boundary between the two divisions.
- **Affiliate** = hem/onc, hematology, medical oncology, BMT/cellular-therapy
  physician or NP/PA with those secondary specialties whose NPI is linked to a
  CAR-T center CCN or group that a core HCP is also linked to (confidence tiers
  in section 4).
- **Referring** = hem/onc, hematology, or medical oncology NPI with lymphoma
  treatment markers (office chemo administration, rituximab or DLBCL agents
  billed under their own NPI, or BTKi/lenalidomide/venetoclax Part D
  prescribing) whose group and hospital affiliations do *not* intersect the
  CAR-T center set. A directed shared-patient edge to a center NPI upgrades the
  label from "proxy referrer" to "observed referrer".

## 2. Backbone sources used by both divisions

All free, bulk, NPI- or CCN-keyed, from CMS. These five do most of the work.

| Source | Latest confirmed | Key fields | Role | Caveats |
|---|---|---|---|---|
| **Medicare Physician & Other Practitioners, by Provider and by Provider & Service, 2024 bulk** ([landing](https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider-and-service)); by Provider API id `8889d81e-2ee7-448f-8713-f071038289b5`, Prov&Svc series id `92396110-2aed-4d63-a6a2-5d6207d46a29` | 2024 data, modified 2026-05-21; 1,296,739 NPIs; 9,781,673 service rows | `Rndrng_NPI`, `Rndrng_Prvdr_Type`, address, `Zip5`, RUCA, `HCPCS_Cd`, `Place_Of_Srvc`, `Tot_Benes`, `Tot_Srvcs`; by-Provider adds `Bene_CC_PH_Cancer6_V2_Pct` | Defines the full hem/onc universe and office-based treatment activity. Provider types in 2024: Hematology-Oncology 9,158; Medical Oncology 3,805; Hematology 924; Hematopoietic Cell Transplantation and Cellular Therapy 143 | Medicare FFS only; rows with ≤10 beneficiaries dropped; CAR-T product codes never appear under physician NPIs (facility-billed). Same vintage as the existing per-NPI pull, so the 500 can be re-derived from the bulk file as a consistency check |
| **CMS Doctors and Clinicians National Downloadable File** ([dataset](https://data.cms.gov/provider-data/dataset/mj5m-pzi6), [dictionary](https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/physician/DOC_Data_Dictionary.pdf)) | released 2026-10-15, modified 2026-10-02, monthly | `NPI`, `Ind_PAC_ID`, `pri_spec`, `sec_spec_all`, `Facility Name` (group legal name), `org_pac_id`, `num_org_mem`, address, `adrs_id` | Group-practice identity for both divisions: community networks (Texas Oncology, Florida Cancer Specialists, Tennessee Oncology, NYCBS) and faculty practice plans both enroll as groups. `adrs_id` prefix gives building-level co-location | PECOS self-reported; one row per clinician × enrollment × group × address; Medicare-enrolled clinicians only |
| **CMS Doctors and Clinicians Facility Affiliation Data** ([dataset](https://data.cms.gov/provider-data/dataset/27ea-46a8)) | same release; 2,276,877 rows | `NPI`, `Ind_PAC_ID`, `facility_type`, `Facility Affiliations Certification Number` (CCN) | NPI → hospital CCN, derived by CMS from 12 months of claims, not self-reported. Joins directly to the CAR-T center set | Hospital level, not cancer-center level; large AMCs produce thousands of affiliates, so always intersect with specialty |
| **PECOS Medicare FFS Public Provider Enrollment (PPEF)** ([landing](https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment/medicare-fee-for-service-public-provider-enrollment), [dictionary](https://data.cms.gov/sites/default/files/2026-07/PPEF_Data_Dictionary.pdf)); Enrollment, Reassignment, Address, Secondary Specialty sub-files | release 2026-10-01 (quarterly), files dated 2026-09-29 | `NPI`, `ENRLMT_ID`, `PECOS_ASCT_CNTL_ID`, `ORG_NAME`; Reassignment: `REASGN_BNFT_ENRLMT_ID → RCV_BNFT_ENRLMT_ID` | Employer-level link: individuals reassigning benefits to the same organization as a core HCP. Catches APPs and physicians who rarely appear on inpatient claims | Approved enrollments only; reassignment to several orgs is common (locums); org names are legal entities. The monthly pre-joined [Revalidation Reassignment List](https://data.cms.gov/sites/default/files/2026-09/349c54ed-ea8e-4342-9468-31f25033da0d/revalidation_group_reassignment_pa_xref.csv) is easier but scoped to revalidation cycles |
| **Hospital Enrollments** ([file](https://data.cms.gov/sites/default/files/2026-10/961708ac-2136-43cb-9587-ac64af23b6ab/Hospital_Enrollments_2026.09.29.csv)) and Hospital All Owners | 2026-10-01, monthly | `CCN`, organization `NPI`, PAC ID, `ORGANIZATION NAME`, DBA name, ownership chain | Cleanest free CCN ↔ organization NPI ↔ PAC ID crosswalk; owners file gives health-system parents | n/a |

Supporting lookups:

- **Open Payments Covered Recipient Profile Supplement** ([CSV](https://download.cms.gov/openpayments/PHPRFL_P06302026_06032026/OP_CVRD_RCPNT_PRFL_SPLMTL_P06302026_06032026.csv), refreshed 2026-06-30, ~1.7M rows): `Covered_Recipient_NPI`, profile address, `Primary_Specialty`, `OPS_Taxonomy_1..6`, license states. Covers only recipients with ≥1 published payment since PY2018 (NPPs since PY2021), so it is not a universe file, but nearly every hem/onc clinician appears. PY2025 general payments are now published; the pipeline uses PY2024.
- **Open Payments Teaching Hospital profile list** ([CSV](https://download.cms.gov/openpayments/SMRY_RPTS_P06302026_06032026/PBLCTN_TH_PRFL_SRCH_P06302026_06032026.csv)): `Teaching_Hospital_CCN`, name, five alternate names. A free hospital-name alias table keyed to CCN.
- **NPPES**: the official bulk download site (download.cms.gov/nppes) currently shows "down for maintenance until further notice". The per-NPI API the pipeline already uses works. The [NBER mirror](https://www.nber.org/research/data/national-plan-and-provider-enumeration-system-nppesnpi) holds monthly files through August 2026 for bulk taxonomy filtering.
- **Dartmouth Atlas ZIP → HSA/HRR crosswalk** ([supplemental](https://data.dartmouthatlas.org/supplemental/), latest 2019): catchment assignment for referrers; free with a required acknowledgment statement.

## 3. Referring HCP division

### 3.1 The one real referral-edge source

| Source | What it is | Access and cost | Fit | Caveats |
|---|---|---|---|---|
| **CareSet DocGraph Hop Teaming** ([data store](https://careset.com/data-store/)) | Directed NPI → NPI shared-patient edges from 100% Medicare FFS claims, annual since 2014, latest 2025. Fields: `FROM_NPI, TO_NPI, PATIENT_COUNT, TRANSACTION_COUNT, AVERAGE_DAY_WAIT, STD_DAY_WAIT` | Non-commercial license (accredited university qualifies): 2014–2022 no cost, 2023 $250, 2024 $1,000, 2025 contact. Commercial 2025: $44,000 Annual, $22,000 Same Day. Verified on the page 2026-10-24 | The only post-2015 public shared-patient edge list. Directly gives community hem/onc → CAR-T center physician flows with patient counts and wait times, which separates referral from co-management | Medicare FFS only (acceptable for LBCL, median age ~65+); disease-agnostic, so both endpoints must be restricted (referrer = hem/onc with lymphoma markers, target = NPI affiliated with a CAR-T center or typed HCT&CT); >10-patient suppression; README not public, confirm by email before quoting |
| **CMS Physician Shared Patient Patterns 2009–2015** ([NBER mirror](https://www.nber.org/research/data/physician-shared-patient-patterns-data)) | Same idea, CMS FOIA release, 30/60/90/180-day windows, free | Free | Pre-dates CAR-T approval (Oct 2017). Useful only to show the method reproduces known patient-sharing structure | Historical only; NPIs churn |

### 3.2 Proxy sources that define the referrer pool without an edge list

| Source | Latest | Referrer signal | 2024 counts pulled | Caveats |
|---|---|---|---|---|
| Physician & Other Practitioners, Prov & Svc 2024 (section 2) | 2024 | Hem/onc NPI billing chemo administration (96413/96415/96417) or rituximab (J9312, Q5115, Q5119, Q5123) or DLBCL agents (J9309 polatuzumab, J9359 loncastuximab, J9321 epcoritamab, J9286 glofitamab, J9350 mosunetuzumab, J9349 tafasitamab) under their own NPI, `Place_Of_Srvc = 'O'`. Office buy-and-bill is itself a community marker: academic infusions bill under the hospital CCN | 96413: 7,637 NPI rows (Hem-Onc 2,609, Med Onc 863, Hematology 89); J9312: 336; Q5115: 67; Q5119: 133; Q5123: 189 | ≤10-beneficiary suppression; the DLBCL agent codes were not yet queried |
| **Medicare Part D Prescribers by Provider and Drug 2024** ([landing](https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/medicare-part-d-prescribers-by-provider-and-drug); API id `9552739e-3d05-4c1b-8eff-ecabf391e2e5`; 28.0M rows) | 2024, modified 2026-05-21 | Hem/onc NPIs prescribing lymphoma orals: ibrutinib, acalabrutinib, zanubrutinib, pirtobrutinib, lenalidomide, venetoclax, tazemetostat. Catches lymphoma hematologists who do not infuse in office | Imbruvica 4,797 prescriber rows (Hem-Onc 2,658, Med Onc 729, Hematology 158, NP 722); Calquence 3,962; Brukinsa 3,905; Revlimid 5,780 + generic 5,095; Venclexta 3,281; Jaypirca 288 | BTKis skew CLL/MCL/WM and lenalidomide skews myeloma, so this is a "B-cell malignancy treater" proxy, not DLBCL-specific. Exclude pomalidomide. Drug rows with ≤10 claims suppressed; `Tot_Benes` blank under 11 (verified) |
| Doctors and Clinicians NDF + Facility Affiliation (section 2) | 2026-10-15 | Academic-vs-community classifier: group legal name, `num_org_mem`, and absence of any CAR-T center CCN among the NPI's hospital affiliations | n/a | PECOS lag on moves |
| Open Payments general payments PY2024/PY2025 | 2026-06-30 | Hem/onc NPIs outside CAR-T centers with payments from Kite/Gilead, Novartis, BMS, Janssen, Autolus tagged to Yescarta, Tecartus, Kymriah, Breyanzi, Carvykti, Abecma, Aucatzyl: manufacturer-targeted community referrers | not pulled | Measures marketing attention, not referral; some rows lack NPI; manufacturer names vary |
| **Medicare FFS Order and Referring file** ([landing](https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment/order-and-referring)) | 2026-10-23, ~twice weekly, 2.06M rows | Eligibility roster only (`NPI, PARTB, DME, HHA, PMD, HOSPICE` Y/N). Not referral events | n/a | Use only as an "actively enrolled" filter |

### 3.3 Community network directories (institution names only)

US Oncology Network (~3,300 providers, no central directory), OneOncology (36
partner practices listed; terms prohibit harvesting), American Oncology
Network, Community Oncology Alliance (no public member list), NCORP
([find a site](https://ncorp.cancer.gov/findasite/), 44 grantees with affiliates,
HTML only), Florida Cancer Specialists, Tennessee Oncology, NYCBS (JS-loaded).
All are name-only, scrape-only, and mostly terms-restricted. Recommended use:
read the *practice legal names* by hand and resolve them to NPIs through the
Doctors and Clinicians `Facility Name` / `org_pac_id`. Do not scrape physician
lists.

### 3.4 Published referral studies (method precedent, no data)

- Trinity Life Sciences, ISPOR Europe 2026 poster: Komodo claims, ~23,000 DLBCL
  patients 2023–2025; academic vs community labeled with Definitive Healthcare
  affiliations; 3% received CAR-T within 2 years, only 16% had referrals.
- Shadman et al., Blood 2025;146(Suppl 1):6272 (TCT 2025): Komodo, 8,959 R/R
  LBCL; 66% received ≥1 line at a non-CAR-T site; referral rates 19–25% by line.
- Chung et al., Blood Adv 2025;9(18):4727: SEER-Medicare; 5.4% of 3L+ DLBCL got
  CAR-T 2017–2020; probability falls ~6% per additional 10 miles to the nearest
  treatment center.

Every quantitative study uses paid Komodo or SEER-Medicare under a data use
agreement. None releases an NPI-level network. The citable precedents are
Definitive Healthcare affiliations for the academic/community label and "site
switch between lines of therapy" as the referral definition.

### 3.5 Recommended build order for referrers

1. Physician & Other Practitioners 2024 bulk (by Provider, then Prov & Svc) to
   define the hem/onc universe and office treatment markers.
2. Doctors and Clinicians NDF + Facility Affiliation to attach groups and
   hospital CCNs, then exclude anyone touching the CAR-T center set.
3. Part D 2024 to add prescriber-only lymphoma hematologists.
4. CareSet Hop Teaming 2022 (free) for a validation year, and 2023 or 2024
   ($250 / $1,000) if the budget allows, to convert proxies into observed edges.
5. Dartmouth HRR crosswalk to assign every referrer to a catchment and compute
   distance to the nearest CAR-T center.
6. Open Payments product-tagged payments as an engagement feature, not as the
   selection rule.

## 4. Affiliate HCP division

### 4.1 Sources that map NPI to institution

| Source | Latest | Link type | Confidence it supports | Caveats |
|---|---|---|---|---|
| Facility Affiliation Data (section 2) | 2026-10-15 | NPI → hospital CCN, claims-derived | Structural (strong) | Hospital, not department; intersect with specialty |
| PPEF Reassignment (section 2) | 2026-10-01 | NPI → organization enrollment / PAC ID, enrollment-derived | Structural (strong), independent of claims | Multiple employers per NPI |
| Doctors and Clinicians NDF (section 2) | 2026-10-15 | NPI → `org_pac_id`, `adrs_id` building prefix | Structural (group) and address co-location | Faculty practice plans are large (one example has 3,215 members) |
| Open Payments Profile Supplement | 2026-06-30 | NPI → manufacturer-reported business address, specialty, taxonomies | Address co-location vote | Manufacturer-reported, may lag; recipients with payments only |
| NPPES practice location (API now; bulk via NBER mirror) | monthly | NPI → self-reported practice address; Type-2 parent organization name for subparts | Address co-location vote (weak) | Stale addresses; departmental or billing addresses common |
| **ClinicalTrials.gov API v2** ([OpenAPI](https://clinicaltrials.gov/api/oas/v2); data as of 2026-10-23) | live | Site PI / sub-investigator / contact names with facility, city, state for CAR-T lymphoma interventional US trials: 350 studies, 121 recruiting | Role evidence (research-active, including non-billing clinicians); requires name → NPI resolution via NPPES constrained by state and oncology taxonomy | No NPI; investigators listed mainly while recruiting; facility strings are free text; CONTACT role is often a research nurse |
| **ORCID public API** `/{orcid}/employments` | live | Employment records with organization name, city, and ROR/Ringgold identifiers | Institution normalization for core HCPs (ORCIDs already collected) | About half of US clinicians have ORCIDs, fewer maintain employments; non-commercial terms on the public API |
| **ROR** ([API v2](https://api.ror.org/v2/organizations), data dump 2026-10-20, CC0) | 2026-10-20 | Affiliation-string matcher to a stable organization ID (verified: MSK → ror.org/02yrq0923, score 1.0) | Normalization of PubMed affiliation strings already in `hcps.csv` | One record per organization: "Stanford Health Care" (CCN) and "Stanford University" differ; departments and cancer centers may lack records. A manual ROR ↔ CCN ↔ PAC ID concordance for the ~40 core institutions is needed |

### 4.2 CAR-T center and institution lists (institution attributes, not HCP rosters)

| Source | Latest | Content | Access |
|---|---|---|---|
| **Medicare Inpatient Hospitals by Provider and Service, MS-DRG 018** ([landing](https://data.cms.gov/provider-summary-by-type-of-service/medicare-inpatient-hospitals/medicare-inpatient-hospitals-by-provider-and-service); API id `690ddc6c-2767-4618-b277-420ffb2bf27c`) | 2024, modified 2026-04-23 | 49 hospitals with ≥11 CAR-T/immunotherapy discharges, by CCN with discharge counts (Kansas 75, Stanford 63, Brigham 55, HUP 53, Iowa 50, MGH 47, UAB 45, Barnes-Jewish 45, UCSF 44, Hackensack 44) | Free bulk/API. Floor, not census: FFS only, suppression, DRG also includes other cell therapies |
| **FACT accredited organizations** ([search](https://accredited.factglobal.org/?_sft_country=usa&_sft_clinical=immune-effector-cellular-therapy)) | hourly | 173 US programs with Immune Effector Cellular Therapy service, program name, director name and credentials, city, state. 353 total, 261 US | Official listing, .xls download button; no explicit license. Director names are a small high-value lead list; no NPIs |
| Manufacturer treatment-center locators: Kite ([Yescarta/Tecartus](https://www.kitepharma.com/patient-resources/find-a-treatment-center), ">160 in the U.S."), Novartis ([Kymriah](https://www.us.kymriah.com/treatment-center-locator/)), BMS ([Breyanzi](https://www.breyanzi.com/find-a-treatment-center)) | live | Center names and addresses by search | Scrape-only, no download, no NPIs. FDA removed CAR-T REMS in June 2025, so REMS site lists no longer exist and community sites can now treat; expect the treater universe to widen beyond the historical ~160 |
| NMDP transplant center directory | live | >150 centers, transplant volumes 2023–2024, no CAR-T volumes, no physician lists | Scrape-only under NMDP terms |
| NCI-designated cancer centers ([list](https://www.cancer.gov/research/infrastructure/cancer-centers/find), updated 2026-06-10) | 74 centers | Name, parent institution, designation tier | HTML, government content |
| NCCN member institutions ([list](https://www.nccn.org/home/member-institutions)) | 33 | Names and cities | HTML; NCCN prohibits copying content, so use names for matching only |
| Cooperative groups: SWOG (1,922 institutions with CTEP codes, scrape-only), Alliance, ECOG-ACRIN (login for rosters), NRG (PDF rosters, July 2026) | varies | Institution membership flags | Institution attributes only |
| AHRQ Compendium of US Health Systems | 2023 edition (updated Sept 2025); group-practice linkage 2020–2022 | Hospital CCN → health system ID, physician counts | Page blocked by bot challenge today; columns unverified |

### 4.3 Confidence tiers for an affiliate edge

Anchor each core institution on one or more hospital CCNs (from Hospital
Enrollments and Hospital All Owners), then attach the NDF `org_pac_id` and
`adrs_id` building prefixes that core HCPs occupy, the ROR ID of the academic
parent, and the trial facility strings seen for them. Score each candidate NPI
(hem/onc, hematology, medical oncology physicians, and NP/PA with those
secondary specialties, US only) by independent votes. The tiers as implemented
in `kolproject-divisions` (label version 1.0.0):

- **Tier A (confirmed)**: shares a center-set CCN in Facility Affiliation with
  >=1 core HCP *and* shares a PECOS group (`org_pac_id`) with a core HCP. Two
  independent CMS systems (claims look-back and enrollment) agree.
- **Tier B (probable)**: exactly one of those two structural links.
- **Center affiliate, unlinked**: affiliated with a center-set CCN where no core
  HCP is affiliated (for example a community CAR-T site outside the cohort).
  Not a referrer, not yet linked to a KOL.
- **Tier C (possible)**: shares only a Doctors and Clinicians building key with
  a core HCP. Manual review before use.

PECOS PPEF reassignment, Open Payments profile addresses, and ClinicalTrials.gov
roles are planned as further votes but are not in label version 1.0.0. Record
source, release date, and the core HCP(s) through which each link was made.
Require Tier A for any affiliate counted in influence metrics.

### 4.4 Recommended build order for affiliates

1. Facility Affiliation + DRG 018 CCN join, intersected with NDF specialty.
2. PPEF Reassignment (or the pre-joined Revalidation Reassignment List) from
   the core HCPs' receiving organizations.
3. NDF `org_pac_id` and `adrs_id` as a second vote and for group size.
4. Open Payments Profile Supplement for address votes and the bridge to the
   payments graph already collected.
5. ClinicalTrials.gov v2 for research-active clinicians with role labels.
6. FACT, NCI, NCCN, and ROR as institution attributes and normalization.

## 5. Not recommended as primary sources

- Hospital "find a doctor" pages (MSK, MD Anderson, Mayo, Penn): no structured
  data; Penn's robots.txt disallows the finder data path; terms pages could not
  be fetched. Spot validation only, cite the page, do not redistribute.
- Society directories: ASTCT member directory is login-only; ASH "Find a
  Hematologist" is an opt-in patient-facing subset; ASCO's public directory
  appears discontinued with Cancer.Net (June 2024).
- State boards / FSMB DocInfo: no NPI, no bulk; licensure confirmation only.
- Commercial: IQVIA OneKey (~6.1M HCP-to-organization affiliations), Definitive
  Healthcare PhysicianView and Network Intelligence (affiliations plus
  claims-derived pathways), Komodo (closed claims used by both referral
  abstracts), HealthLink Dimensions, Symphony. None is necessary for a
  defensible chain; they mainly add non-Medicare coverage and department labels.

## 6. Verification status

Verified 2026-10-24 by fetching the live resource (header, API count, or page
text): all CMS files and API ids in sections 2–4, the DRG 018 count (49), the
Facility Affiliation header and row count, CareSet pricing table, Part D
suppression pattern, FACT counts, ClinicalTrials.gov study count, ROR matcher,
ORCID employments structure, Order and Referring fields.

Not verified or partially verified:

- NPPES bulk download site offline; reference-file layouts from the standard
  layout, not re-fetched.
- AHRQ Compendium pages blocked; year and files from the Mathematica release.
- CareSet README and data dictionary not public; suppression rule from older
  CareSet documentation.
- Doctors and Clinicians refresh cadence not stated in the 9/10/26 dictionary
  (observed monthly).
- Whether the Outpatient hospital file has a CAR-T-identifying APC: not checked.
- ASCO directory status, Kymriah and Breyanzi center counts, commercial vendor
  figures: from secondary sources or vendor marketing.

## 7. Proposed next steps

1. Build the CAR-T center CCN set (DRG 018 + FACT → CCN by name match through
   Hospital Enrollments and the Teaching Hospital alias file) and freeze it with
   a dated manifest.
2. Pull the 2024 Physician & Other Practitioners by-Provider bulk file and the
   two Doctors and Clinicians files into `Data/raw/<snapshot>/` with the same
   `source_requests.csv` provenance pattern used for the existing collectors.
3. Label every hem/onc NPI as core, affiliate (tiered), referrer (proxy), or
   neither; publish counts by state and HRR; compare against the 500 to confirm
   all core HCPs resolve to a center.
4. Request the free CareSet 2022 file under the non-commercial attestation and
   decide on 2023/2024 after seeing edge density for the center set.
5. Add the new divisions as a `division` column plus per-division evidence
   tables rather than widening `hcps.csv`, so the published 2026-10-09 tables
   stay byte-for-byte unchanged.
