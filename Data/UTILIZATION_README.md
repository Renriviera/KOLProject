# Medicare utilization extension — 2024 services

Two new analytical tables are under `Data/processed/2026-10-15/`. The original
`Data/processed/2026-10-09/hcps.csv` and every other file in that original
processed directory remain byte-for-byte unchanged.

| Table | One row represents | Purpose |
|---|---|---|
| `cms_provider_service.csv` | NPI × service year × HCPCS × setting × CMS dataset version | All released service rows for the existing 500 NPIs, with dated provenance |
| `hcp_utilization_features.csv` | NPI × service year × CMS dataset version × feature version | One row per existing HCP, including HCPs without observable records |

Counts and coverage are in `utilization_metadata.json`. Validation is in
`evaluations/utilization_validation_2026-10-15.json`. The field dictionary and
source/checksum inventories are under `Data/manifests/2026-10-15/`.

### Observed coverage in this release

- **3,318** service rows cover **411** of the 500 HCPs.
- The feature table has **500** distinct NPI rows; **89** have no published
  service rows and retain blank numeric utilization features.
- **7** HCPs have at least one selected code: **6** have IV rituximab/biosimilar
  rows and **1** has a CAR-T administration row. The latter code covers all
  indications and does not establish lymphoma-specific treatment.
- No selected rituximab/hyaluronidase, polatuzumab, tafasitamab or lymphoma CAR-T
  product rows were published for these NPIs in this release. This is absence of
  observable records, not evidence of zero treatment.

All 500 queries completed successfully. Requests for the cohort ran from
**2026-10-15T23:38:47Z** through **2026-10-15T23:53:18Z**. Exact-NPI joins preserve
all 500 HCPs; no duplicate service keys were found. This source contributes
general service activity more broadly than the selected drug-specific features.

## Source and dates

Source: **CMS Medicare Physician & Other Practitioners — by Provider and Service**.
Services cover **2024-01-01 through 2024-12-31**. CMS catalog modified date is
**2026-05-21**, distinct from collection and the older HCP cohort's snapshot.

Pinned annual dataset version: `335e5f35-eca6-482d-87b3-f99883e213e3`.
The moving series/latest identifier `92396110-2aed-4d63-a6a2-5d6207d46a29` is not
queried for cohort collection. The source catalog, schema, methodology,
technical specifications, requests and response hashes are saved locally.

Each request has an exact UTC observation timestamp; query completion and table
construction times are separate. The folder date labels the collection snapshot;
exact request timestamps remain authoritative if collection crosses midnight.
No 2026 service activity is inferred from this 2024 release.

The pipeline queries all 500 exact NPIs, retains **all** published HCPCS codes,
and follows pagination through a terminal empty page. No additional specialty
filter is applied: claims-derived specialties can differ from current NPPES
taxonomy. Keeping all released service rows allows later feature revisions.

## Selected codes

| Family | 2024 codes | Service/unit interpretation |
|---|---|---|
| IV rituximab and biosimilars | J9312, Q5115, Q5119, Q5123 | 10 mg billed drug units |
| Rituximab with hyaluronidase | J9311 | 10 mg billed rituximab with hyaluronidase |
| Polatuzumab vedotin-piiq | J9309 | 1 mg billed drug units |
| Tafasitamab-cxix | J9349 | 2 mg billed drug units |
| Lymphoma-relevant CAR-T products | Q2041, Q2042, Q2053, Q2054 | Product therapeutic-dose billing units |
| CAR-T administration, all indications | 0540T | Administration services; not lymphoma-specific |

The eleven product descriptors were checked against all four official **2024**
CMS Level II quarterly releases. The administration mapping is documented in
CMS Claims Processing Manual chapter 32, sections 400.2/400.2.3: **0540T** ends
December 31, 2024; **38228** starts January 1, 2025.

This deliberately narrow list covers the professor's suggested product families
and relevant CAR-T products; it is not an exhaustive lymphoma regimen list.
Q2055/Q2056 are not classified as lymphoma CAR-T products. Unclassified drug
codes are not assumed to identify CAR-T. All unselected rows remain in the
detailed table as `feature_group=other`.

Code-list version: `2024-v1`; feature version: `1.0.0`. Definitions, descriptions,
billing units and source evidence are in `configs/utilization_codes_2024.json`.

## Interpretation and missingness

- A published code indicates activity in released Medicare FFS Part B
  non-institutional data. It does not establish DLBCL diagnosis, treatment
  indication, unique CAR-T patient count, prescribing influence or care quality.
- CMS omits provider-code-setting cells based on 10 or fewer beneficiaries.
  The file does not cover the whole practice, Medicare Advantage or all hospital
  facility billing. Organization-level billing cannot be reassigned to an HCP.
- A **zero observation flag or published-row count** means no matching released
  record, not zero clinical activity. Numeric activity/payment features remain
  **blank** when no corresponding row exists. Suppression and absence of covered
  activity cannot be distinguished. Failed API queries block release.
- `services_or_units` is not generally a count of treatment sessions. Drug
  quantities reflect code-specific billing units and may include billed discarded
  drug. Incompatible units are never summed across product families.
- Beneficiaries are unique only within a source cell. Features report the maximum
  count in one family cell rather than adding counts as unique patients. Summed
  beneficiary-code-setting days may repeat the same patient-day across cells.
- Payment estimates multiply published averages by services/units. Published
  averages are rounded, so these are approximate released-cell reimbursements,
  not income, patient copays or total practice revenue. Feature sums are rounded
  once; summing rounded detail estimates can differ slightly.
- Only 2024 is collected; no year-over-year trend is inferred.

## Rebuild, validate and join

```bash
uv sync --locked
uv run kolproject-utilization stage=validate
uv run kolproject-utilization stage=rebuild  # offline from retained responses
uv run kolproject-utilization stage=all      # resume collection, build, validate
```

The default command validates offline. A different year, snapshot, code list or
feature specification requires a versioned change; unsupported overrides fail.

Generate a separate descriptive modeling input when needed:

```bash
uv run python pre-processing/join_utilization.py
```

This writes `models/inputs/hcp_utilization_2024.csv` and metadata, using a
validated one-to-one left join on NPI/HCP ID that preserves all 500 rows.
The delivery includes the script; the wide modeling table is generated on demand.
Do not directly join the detailed service table into a one-row-per-HCP analysis:
that repeats publication and network features across service rows.

The existing publication/network features were observed in 2026 and include
publications through 2025. A join supports descriptive analysis, but those
future-observed features must not be prospective predictors of 2024 utilization.
No influence model is refitted and no existing ranking changes in this extension.

## Primary documentation

- CMS dataset: https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider-and-service
- HCPCS releases: https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update
- CMS Claims Processing Manual chapter 32: https://www.cms.gov/Regulations-and-Guidance/Guidance/Manuals/Downloads/clm104c32.pdf

CMS/AMA coding terms continue to apply to source descriptions; this project does
not relicense third-party source content.
