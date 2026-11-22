# Open Payments general records, 2019–2025 — 2026-11-11 snapshot

Tables under `Data/processed/2026-11-11/` extend the cohort's CMS Open Payments
**General** payments from one program year to seven. Every record whose covered
recipient is one of the 500 core HCPs is kept, for program years 2019 to 2025 of
the 2026-06-30 publication. The 2026-10-09 table `general_payments_2024.csv` is
unchanged; the 2024 rows here reproduce it record for record.

General payments evidence consulting, speaking, education and hospitality paid
by manufacturers. They do not record Medical Science Liaison contact. See
[`RESEARCH_PAYMENTS_README.md`](RESEARCH_PAYMENTS_README.md) for the companion
research-payment tables built from the same archives.

## Tables

| Table | One row represents | Rows |
|---|---|---|
| `general_payments_2019_2025.csv` | General payment record paid to a core HCP | 41,622 |
| `hcp_general_payments_by_year.csv` | Core HCP × program year, zero-filled | 3,500 |
| `hcp_general_payment_features.csv` | Core HCP, seven-year summary | 500 |

`general_payments_metadata.json` carries scan counts, totals by year and nature
group, CAR-T breakdowns, and the 2024 reconciliation. Field definitions are in
`Data/manifests/2026-11-11/general_payments_data_dictionary.json`.

## What was collected

| Program year | General rows scanned | Rows paid to a core HCP | HCPs paid | HCPs with a CAR-T record |
|---|---|---|---|---|
| 2019 | 10,558,933 | 9,064 | 352 | 153 |
| 2020 | 5,851,438 | 3,435 | 310 | 110 |
| 2021 | 11,558,469 | 3,428 | 323 | 105 |
| 2022 | 13,322,266 | 5,117 | 351 | 120 |
| 2023 | 14,734,121 | 6,621 | 382 | 141 |
| 2024 | 15,498,687 | 6,445 | 387 | 130 |
| 2025 | 16,131,856 | 7,512 | 391 | 159 |

The General detail file inside each program-year archive was scanned in full.
The archives are the ones downloaded for the research-payment tables; their
hashes are in the metadata file.

**2024 reconciliation.** The bulk 2024 rows and the API-collected
`general_payments_2024.csv` share all 6,445 record IDs, with no difference in
amount, date, manufacturer, nature, form, products, or the lymphoma CAR-T flag.
`kolproject-evaluate` repeats this check.

## Headline counts

- 478 of 500 core HCPs received at least one general payment in 2019–2025.
- 300 core HCPs have a record naming a CAR-T product, against 98 in 2024 alone
  under the earlier lymphoma-only flag.
- 200 core HCPs have a CAR-T record in an advisory group.
- 5,871 records name a CAR-T product, worth $6.4 million.

## Nature groups

`nature_group` collapses the CMS nature-of-payment categories. The grouping is
this project's reading; CMS does not label a payment as medical or commercial.

| Group | CMS categories | Rows | USD |
|---|---|---|---|
| `consulting` | Consulting Fee | 8,725 | 28.0M |
| `honoraria` | Honoraria | 462 | 1.3M |
| `medical_education` | Education; faculty or speaker for a medical education program, including the pre-2021 accredited and non-accredited names | 593 | 0.2M |
| `grant` | Grant | 39 | 0.3M |
| `speaking_non_cme` | Compensation for services other than consulting, including speaking outside continuing education | 4,909 | 14.1M |
| `hospitality` | Food and Beverage; Travel and Lodging; Gift; Entertainment | 26,888 | 5.5M |
| `other` | Acquisitions; Royalty or License | 6 | 10.1M |

`advisory_usd` sums `consulting`, `honoraria`, `medical_education`, and `grant`.
Speaking outside continuing education is kept apart because it covers
promotional speaker programs.

## CAR-T flags

| Field | Rule |
|---|---|
| `cart_product_mentioned` | Brand, generic, or development name of any of the seven approved CAR-T products in one of the five product slots |
| `cart_products` | Canonical brands matched |
| `lymphoma_cart_product_mentioned` | The 2026-10-09 pattern, unchanged: Yescarta, Kymriah, Breyanzi, or Tecartus |

Use `cart_product_mentioned` for new work. It adds Abecma, Carvykti and Aucatzyl
to the earlier flag. No record in these seven years names Aucatzyl.

## Reading the numbers

- **Six `other` records carry $10.1 million.** Five are Acquisitions, including
  one of $9.0 million in 2025. They dominate `general_payments_usd` for the HCPs
  concerned; prefer `advisory_usd` or the group columns for comparisons.
- **CAR-T totals are a floor.** Manufacturers often leave the product slots
  blank on consulting fees.
- **Manufacturer names vary in case and legal entity.** Novartis appears under
  three names and Janssen under five entities. Normalize before grouping.
- **2020 is low across the whole file**, at 5.9 million rows against 10.6 million
  in 2019, so the dip is not specific to this cohort.
- **Zeros are observed counts** for this publication. CMS revises records at
  each refresh; the next is expected in early 2027.

## Rebuild

```bash
uv run kolproject-general-payments --skip-collect
```

Without `--skip-collect` the command downloads the archives first. The scan of
all seven years takes about one minute on seven workers.
