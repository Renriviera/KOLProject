---
name: prescribing-claims
use_when: The user asks about prescribing habits, lymphoma drug use, top prescribers, infusion volume, Medicare or Medicaid payments to clinicians, or how a community's or center's clinicians bill.
default_edges: none
caveats: [MEDICARE, NOT_PRODUCT_SPECIFIC, LABELS]
---
# Prescribing and claims (Medicare and Medicaid only)

Pass `scope` and `id`: `"hcp"` (NPI), `"center"` (CCN), `"community"` (every HCP attached to the community),
`"community_core"` (only its core members), or `"division_group"` (core, affiliates, referring, other).
A community total covers all its attached HCPs, most of them affiliates and referring HCPs: say "HCPs attached to
the community", never "the hospital's billing".

- Headline numbers for one year: `prescribing_summary` (panel `kpis`).
- Oral lymphoma drugs (Medicare Part D, 2017 to 2024): `partd_by_drug_year`; ranking: `top_prescribers`.
- A named infused drug (rituximab, bendamustine, obinutuzumab) or chemotherapy administration, billed by the
  clinician under Medicare Part B, 2017 to 2024: `partb_markers_by_year` (set `family` for one drug). Only this
  query splits by infused drug; `prescribing_summary` and `partb_payment_by_year` do not.
- Medicare Part B payment split into drugs and everything else: `partb_payment_by_year`.
- Medicaid paid to clinicians (2018 to 2024): `medicaid_by_year`; it is a table, not a trend: do not compare years
  or states. It is about clinicians, not about CAR-T products.
- National spending on CAR-T by product, with no entity to look up: `national_spending_by_brand_year` with
  `program="medicare_part_b"` or `program="medicaid"`. Use it whenever the question is about spending on CAR-T
  products or the whole country.
- Hospital CAR-T stays: `center_medicare_cart_stays`, `community_medicare_cart_stays`,
  `top_centers_by_medicare_stays`.

Each query draws its own panel: a line chart by year with the approval dates marked, bars for a single-year
ranking, a table for detail. The map already shows the entity, or the whole country for a national question. You
do not need `set_map`.

Say every time it applies:
- These claims do not name the CAR-T product. To split by product, offer industry payments or national spending.
- A hospital-employed clinician's drugs are billed by the hospital, so a low figure is about who bills.
- Part D cost is the gross cost of prescriptions, not money paid to the prescriber.
- There is no commercial-insurance data. If asked, say so and stop.
