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

- **Core versus affiliates.** The core is the 500 publication-selected
  CAR-T lymphoma authors: the publishing layer. Affiliates are clinicians who
  share a center hospital or PECOS group with a core member but did not author
  in the corpus: the non-publishing colleagues. Every center CCN is on the map.
- **Layer A anchors and core presence.** A center with a core member is
  filled with its members' dominant community color and placed at their
  centroid on the coauthorship layout (92 centers). A center with no core
  member is hollow; dashed when it has no tier A or B affiliate either (the
  other 81, placed next to what they link to or next to their state).
- **Layer B as counts.** Tier A and tier B affiliates and unlinked center staff
  are attached to each center; the side card lists the core members with the
  patients each receives directly from referring physicians.
- **Layer C edges.** Squares are hospital referral regions sized by referring
  physicians; node size defaults to inflow, so a large hollow circle is flow the
  publishing core does not touch. The proxy layer links a region to the adult
  centers inside it, or, when it has none, to the adult centers in its state
  (dashed, off by default); children's hospitals are kept as centers but
  excluded as proxy targets. Each ingested CareSet year adds an observed layer;
  the latest year is the default.
- **Time progression.** Above the community table, two charts trace the
  CareSet years for the current selection: with nothing selected, patients from
  referring physicians by where they land (core, affiliates only, no link) and
  the breadth of the referral base; for a selected center, its own inflow and
  referrer links; for a selected region, patients sent and where they go.
  Dashed vertical tickers mark CAR-T regulatory milestones (first approvals of
  Kymriah and Yescarta in 2017, Kymriah's DLBCL indication in 2018, the 2019 ASCO Annual Meeting, Tecartus
  in 2020, Breyanzi and Abecma in 2021, Tecartus's ALL expansion, Carvykti and
  Yescarta's second-line move in 2022); the list lives in `catchment.py`.
- **Coverage of the publishing core.** For every layer the page reports where
  the flow lands: centers with a core member, centers reached only through
  affiliates, and centers with no link to the core network. On 2022 flows, 65%
  of patients land at centers with a publishing core member, 27% at
  affiliate-only centers, and 8% at centers the network does not reach, led by
  Baylor University Medical Center, Baptist Memorial Memphis, and Sentara
  Norfolk. Those blind spots are listed in their own table.

Positions outside the anchored set are barycentric with a short spring pass,
clamped to the coauthorship frame, so the two graphs share a coordinate system.
The axes of both graphs carry no meaning: the coauthorship layout is a seeded
Fruchterman-Reingold spring embedding, so only neighborhoods (who sits near whom,
which cluster a node belongs to) are interpretable, not distances or directions.
Because of that, the Catchment tab also ships an optional spread layout (off by default):
a monotone radial expansion about the anchored centroid plus a collision pass,
which keeps every node's direction and rank by distance from the middle while
opening the dense academic core for reading when switched on.

## CareSet DocGraph (Layer C, observed)

The 2017 through 2022 DocGraph Hop Teaming files were obtained under CareSet's
non-commercial license (CC BY-NC-SA 4.0 plus the CareSet Noncommercial Data
License Addendum: attribution, non-commercial use, derived tables under the
same license, no re-identification). The delivered folders live next to the
project at `../DocGraph_<year>_NonCommercial/`; MD5 checksums matched the
shipped `.md5` files (BSD format for 2017 to 2019). Each file holds 7 to 11 GB
and 186 to 223 million directed provider pairs.

```bash
uv run kolproject-careset ../DocGraph_2022_NonCommercial/DocGraph_Hop_Teaming_2022.csv --year 2022 --kind annual
uv run kolproject-catchment
```

The loader streams each file once, keeps directed edges that touch the
division universe or a center organization NPI, and writes per year
`shared_patient_annual_<year>_edges.csv` (local, `Data/interim/2026-10-24/careset/`,
about 600 MB each), plus the published `_features.csv`, `_hrr_center_flows.csv`,
and `_metadata.json` under `Data/processed/2026-10-24/`.

| Year | Pairs in file | Edges kept | NPIs with features | HRR to center flows | Patients in flows |
|---|---|---|---|---|---|
| 2017 | 203,330,907 | 5,746,912 | 11,640 | 1,938 | 421,795 |
| 2018 | 217,911,308 | 6,260,795 | 12,242 | 1,978 | 460,810 |
| 2019 | 223,400,012 | 6,584,925 | 12,801 | 1,966 | 506,700 |
| 2020 | 186,367,054 | 5,763,378 | 13,251 | 1,798 | 445,256 |
| 2021 | 206,574,841 | 6,418,279 | 13,750 | 1,713 | 459,103 |
| 2022 | 210,313,975 | 6,585,585 | 14,251 | 1,690 | 453,840 |

The referring physicians are those labeled on 2024 markers, so earlier years
describe what that same set of physicians did then. Across the six years the
share of flow landing at centers with a publishing core member stays between
62% and 65%, affiliate-only centers take 27% to 30%, and centers with no link
to the core network 7% to 8%; the share of patients staying in the referring
physician's own region rises from 45% in 2017 to 51% in 2022. Only about 55
core HCPs receive flow directly from referrers in any year, because most flow
lands on center organization NPIs or on affiliates rather than on the
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
