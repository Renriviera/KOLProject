# Open Payments research records — 2026-11-11 snapshot

Tables under `Data/processed/2026-11-11/` list every CMS Open Payments **Research**
record, program years 2019 to 2025, that names one of the 500 core HCPs as a
principal investigator or as the covered recipient. The 2026-10-09 cohort tables are
unchanged. The source is the 2026-06-30 Open Payments publication.

These rows evidence a sponsor–investigator relationship. They do not record
Medical Science Liaison contact, which involves no transfer of value and is not
published anywhere (see `reports/Public MSL data for CAR-T.md`).

## Tables

| Table | One row represents | Rows |
|---|---|---|
| `research_payments_2019_2025.csv` | Research record × core HCP named on it | 74,094 |
| `hcp_research_payment_features.csv` | Core HCP, including those with no published record | 500 |

`research_payments_metadata.json` carries archive hashes, per-year scan counts, the
CAR-T matching patterns, and limitations. Field definitions are in
`Data/manifests/2026-11-11/research_payments_data_dictionary.json`. The checksum
manifest in that folder stays local.

## What was collected

| Program year | Research rows scanned | Rows naming a core HCP |
|---|---|---|
| 2019 | 807,295 | 8,815 |
| 2020 | 760,295 | 9,349 |
| 2021 | 894,317 | 9,022 |
| 2022 | 1,011,616 | 9,416 |
| 2023 | 1,092,474 | 11,758 |
| 2024 | 817,215 | 12,932 |
| 2025 | 931,959 | 12,719 |

The seven program-year archives (`PGYR<year>_P06302026_06032026.zip`, 6.6 GB) were
downloaded from `download.cms.gov/openpayments/`, hashed, and scanned locally. A
record is kept when a cohort NPI equals `principal_investigator_{1..5}_npi` or
`covered_recipient_npi`. Two spot checks against the live datastore API on
2026-11-11 returned identical counts.

Scanning the bulk files matters: 97% of matched records are paid to an institution
or a non-covered entity, so a query on `covered_recipient_npi` alone, the pattern
used for the 2024 general payments, would have found 2,144 of 74,011 records.

## Headline counts

- 420 of 500 core HCPs are named on at least one research record; 80 have none.
- 74,011 distinct records total $1.62 billion in record amounts.
- 11,735 records carry a CAR-T match, naming 247 core HCPs.
- 75 records name two to four core HCPs; all others name one.

## CAR-T flags

| Field | Rule |
|---|---|
| `cart_product_mentioned` | A brand, generic, or development name of an approved CAR-T product appears in one of the five product-name slots |
| `lymphoma_cart_product_mentioned` | That match is Yescarta, Tecartus, Kymriah, or Breyanzi |
| `cart_text_match` | Study name or research context names a CAR-T product or uses generic CAR-T wording |
| `cart_products` | Canonical brands matched in either place; blank on generic-wording matches |

Development names are included because research records often predate approval
(`KTE-C19`, `KTE-X19`, `CTL019`, `JCAR017`, `bb2121`, `JNJ-68284528`, `AUTO1`).
Generic-wording matches also catch CAR-T-adjacent research, such as supportive-care
trials and investigational constructs from other sponsors. Filter on
`cart_products` when only the seven approved products are wanted.

## Reading the amounts

`amount_usd` is the whole record. It is paid mostly to institutions, is repeated on
each HCP row of a multi-investigator record, and must not be summed across HCPs or
read as personal income. `research_record_usd_2019_2025` is the sum of record
amounts on which the HCP is named.

## Limitations

- CMS lets manufacturers delay publication of research payments for products in
  development for up to four years. Recent years are a floor.
- Zero rows is an observation count, not evidence of no research relationship.
- Fewer than 0.05% of investigator entries lack an NPI in each year; those cannot
  be matched.
- Only 32% of matched rows carry a ClinicalTrials.gov identifier; the study name
  is present on nearly all.
- CMS revises records at each refresh. The next one is expected in early 2027.

## Rebuild

```bash
uv run kolproject-research-payments --skip-collect
```

Without `--skip-collect` the command downloads the archives first. Raw archives
and interim scans stay local under `Data/raw/2026-11-11/` and
`Data/interim/2026-11-11/`.
