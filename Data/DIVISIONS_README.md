# Referring and affiliate HCP divisions — 2026-10-24 snapshot

Tables under `Data/processed/2026-10-24/` place the preserved 500-HCP core inside
the 2024 US hematology/oncology universe and label every NPI by its evidence of
affiliation with a CAR-T center or of community lymphoma treatment. The
2026-10-09 cohort tables and the 2026-10-15 utilization tables are unchanged
(hashes are frozen in `Data/interim/2026-10-24/preserved_inputs.json`).
Source selection and the reasoning behind it are in
[`HCP_EXPANSION_SOURCES.md`](HCP_EXPANSION_SOURCES.md).

Labels are evidence tiers built from public CMS enrollment and claims files.
They are not referral volumes, employment records, or measures of influence.

## Tables

| Table | One row represents | Rows |
|---|---|---|
| `cart_centers.csv` | Hospital CCN in the CAR-T center set | 173 |
| `fact_iec_programs.csv` | FACT-accredited US immune effector cell program with its matched CCN | 173 |
| `hcp_divisions.csv` | NPI in the universe with structural links, markers, HRR, and a division label | 17,510 |
| `hcp_hospital_affiliations.csv` | NPI × hospital CCN edge (claims-derived) | 42,967 |
| `hcp_group_memberships.csv` | NPI × PECOS group × practice address | 42,908 |
| `treatment_markers_2024.csv` | NPI × marker HCPCS code × setting (2024 Part B) | 9,520 |
| `part_d_lymphoma_orals_2024.csv` | NPI × lymphoma oral agent (2024 Part D) | 21,427 |

`division_metadata.json` carries counts, source releases, and limitations.
Field definitions are in `Data/manifests/2026-10-24/division_data_dictionary.json`.
The request and checksum manifests in that folder stay local.

## The CAR-T center set

A hospital CCN enters the set through either of two pieces of evidence:

- **MS-DRG 018 discharges in the 2024 Medicare inpatient file** (49 hospitals;
  hospitals with 10 or fewer FFS discharges are suppressed, so this is a floor).
- **FACT accreditation for Immune Effector Cellular Therapy** (173 US programs
  listed on 2026-10-24). Programs were matched to a CCN by name within the
  listed state, preferring the listed city. 125 matched automatically; 48 were
  resolved by hand against the CMS Hospital Enrollments file and are recorded
  with their evidence in `configs/cart_center_overrides.csv`.

The two sources agree on 42 hospitals; 7 appear only through DRG 018 (for
example Brigham and Women's and UPMC Presbyterian Shadyside, whose programs are
listed by FACT under a partner institution) and 124 only through FACT. 92 of the
173 centers have at least one core HCP affiliated.

## The universe

| Source | NPIs | Rule |
|---|---|---|
| 2024 Medicare Physician & Other Practitioners by Provider | 14,030 | Claims specialty Hematology-Oncology (9,158), Medical Oncology (3,805), Hematology (924), or Hematopoietic Cell Transplantation and Cellular Therapy (143) |
| Doctors and Clinicians National Downloadable File (2026-10-15) | 16,828 | PECOS primary specialty in those fields, Internal Medicine or Pediatric Medicine with such a secondary specialty, or NP/PA/CNS with such a secondary specialty |
| Core cohort | 500 | Always included |
| Union | 17,510 | |

68 core HCPs have no qualifying specialty in either file (54 appear in neither
2024 source); they keep the `core` label but cannot anchor affiliates.

## Labels (version 1.0.0)

Tested in order; the first rule that applies wins.

| Division | Rule | Count |
|---|---|---|
| `core` | NPI is one of the 500 | 500 |
| `affiliate_A` | Shares a center CCN that has a core HCP *and* shares a PECOS group with a core HCP | 5,434 |
| `affiliate_B` | Exactly one of those two structural links | 2,255 |
| `center_affiliate_unlinked` | Affiliated with a center CCN where no core HCP is affiliated | 1,764 |
| `affiliate_C` | Shares only a practice building with a core HCP | 95 |
| `referring_proxy` | Physician with no center or core-group link and at least one treatment marker | 4,632 |
| `hemonc_other` | Eligible specialty, no links, no markers | 2,830 |

Treatment markers, all from 2024 Medicare files under the NPI's own billing:

- `office_chemo_administration`: 96413, 96415, or 96417 in the non-facility setting.
- `rituximab_own_npi`: J9312, Q5115, Q5119, Q5123, or J9311 in any setting.
- `dlbcl_agent_own_npi`: J9309, J9349, J9359, J9321, J9286, J9350. No physician-billed
  rows exist for these codes in the 2024 file; the marker is retained for later years.
- `part_d_lymphoma_oral`: ibrutinib, acalabrutinib, zanubrutinib, pirtobrutinib,
  lenalidomide, venetoclax, or tazemetostat prescribing.

The largest referrer groups are the expected community networks (Texas
Oncology 671 NPIs, Florida Cancer Specialists 600). About half of referrers
practice in a hospital referral region that contains a center.

## Reproduce

The build needs the local raw files (about 1 GB of CMS bulk CSVs, ignored by
Git) and the interim query results:

```bash
uv run kolproject-divisions --skip-collect
```

Without `--skip-collect` the command re-runs the dated API collection, which
reuses cached responses and only fetches what is missing. Bulk files are
downloaded with `curl` because the Doctors and Clinicians host rejects other
clients; their headers and timestamps sit next to each file under
`Data/raw/2026-10-24/bulk/`.

## Catchment graph on the site

`uv run kolproject-catchment` writes `docs/data/catchment.json`, which the
site's Catchment tab draws as a second graph with its own state, separate from
the coauthorship map on the Results tab:

- **Layer A anchors.** Each center hospital is placed at the centroid of its
  core members on the coauthorship layout and colored by their dominant
  community (92 centers have a core member; the other 81 are grey and placed
  next to what they link to).
- **Layer B as counts.** Tier A and tier B affiliates and unlinked center staff
  are attached to each center; the side card lists the core members.
- **Layer C edges.** Squares are hospital referral regions sized by referring
  physicians. The proxy layer links a region to the adult centers inside it,
  or, when it has none, to the adult centers in its state (dashed, off by
  default); children's hospitals are kept as centers but excluded as proxy
  targets. Each ingested CareSet year adds an observed layer of patient flows
  from a region's referring physicians to center physicians and organizations;
  the latest year is the default layer.

Positions outside the anchored set are barycentric with a short spring pass,
clamped to the coauthorship frame, so the two graphs share a coordinate system.

## CareSet DocGraph (Layer C, observed)

The 2021 and 2022 DocGraph Hop Teaming files were obtained under CareSet's
non-commercial license (CC BY-NC-SA 4.0 plus the CareSet Noncommercial Data
License Addendum: attribution, non-commercial use, derived tables under the
same license, no re-identification). The delivered folders live next to the
project at `../DocGraph_2021_NonCommercial/` and `../DocGraph_2022_NonCommercial/`;
MD5 checksums matched the shipped `.md5` files. Each file has about 7.9 GB and
206 to 210 million directed provider pairs.

```bash
uv run kolproject-careset ../DocGraph_2022_NonCommercial/DocGraph_Hop_Teaming_2022.csv --year 2022 --kind annual
uv run kolproject-catchment
```

The loader streams the file once, keeps directed edges that touch the division
universe or a center organization NPI, and writes:

| Output | Location | 2021 | 2022 |
|---|---|---|---|
| `shared_patient_annual_<year>_edges.csv` | `Data/interim/2026-10-24/careset/` (local; about 650 MB each) | 6,418,279 edges | 6,585,585 edges |
| `shared_patient_annual_<year>_features.csv` | `Data/processed/2026-10-24/` | 13,750 NPIs | 14,251 NPIs |
| `shared_patient_annual_<year>_hrr_center_flows.csv` | `Data/processed/2026-10-24/` | 1,713 HRR to center flows, 459,103 patients | 1,690 flows, 453,840 patients |
| `shared_patient_annual_<year>_metadata.json` | `Data/processed/2026-10-24/` | row counts, file hash, license | same |

What the flows show, 2022: 3,547 of the 4,412 referring proxies with any kept
edge share patients with a CAR-T center physician or organization; 297 referral
regions send to 152 centers; 51% of patients stay inside the referring
physician's own region, which is the share the geographic proxy gets right by
construction. Only 55 core HCPs receive flow directly from referrers, because
most flow lands on center organization NPIs or on affiliates rather than on the
publishing physicians.

The edge tables stay local because of their size, not the license; the derived
feature and flow tables are published with attribution to CareSet Systems, Inc.
under CC BY-NC-SA 4.0. The Catchment tab offers the proxy and each observed
year as switchable edge layers; node positions are computed once from the
union of layers so switching never moves a node. DocGraph years precede the
2024 markers and center set and the 2025 removal of CAR-T REMS requirements.

## Limitations

- Referring labels are proxies from 2024 markers. Observed shared-patient flows
  come from CareSet DocGraph 2021 and 2022 and are Medicare FFS patients of any
  diagnosis, not lymphoma referrals.
- Markers come from Medicare FFS with 11-beneficiary suppression. Absence of a
  marker is not evidence of no lymphoma treatment. BTK inhibitors and
  lenalidomide skew to CLL, MCL, WM, and myeloma.
- Hospital affiliation reflects a 12-month claims look-back and group
  membership reflects PECOS enrollment; both lag moves. Faculty practice plans
  are large, so a shared group is a weaker signal at academic centers than at
  small practices.
- The center set is a floor. Community sites that began treating after the
  June 2025 removal of CAR-T REMS requirements appear only if FACT-accredited.
- HRR assignment uses the 2019 Dartmouth ZIP crosswalk.
- PECOS reassignment, Open Payments profile addresses, and ClinicalTrials.gov
  roles are not yet used as votes.
