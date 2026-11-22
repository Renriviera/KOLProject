---
name: industry-payments
use_when: The user asks about money from manufacturers (payments, fees, transfers of value, consulting or speaking, meals and travel, research funding and funded studies), by year, product or company, who is paid most, or ownership interests.
default_edges: payments
caveats: [PAYMENTS]
---
# Industry payments

Resolve who the question is about with `find_entity`, then pass `scope` and `id`:
`scope="hcp"` (id = NPI), `"center"` (id = CCN), `"community"` (every HCP attached to the community),
`"community_core"` (only its core members; use this when the user says "leaders", "core" or "KOLs"), or
`"division_group"` (id = core, affiliates, referring or other).

- The user names one product ("payments naming Yescarta"): run `payments_by_product` with `brand` set to it. Its
  chart marks that product's approval dates. Do not use `payments_by_year` for this: it mixes products.
- By year, no product named: `payments_by_year`. Use `rule="lymphoma_only"` by default; use `rule="cart_only"`
  when the user says "all CAR-T" or names a myeloma or leukemia product.
- Products side by side: `payments_by_product` without `brand`; it draws one line per product.
- By company: `payments_by_manufacturer`.
- Month by month: `payments_monthly` (add `brand` for one product).
- Who receives most: `top_paid_hcps` with `nature="services"` (consulting, speaking, honoraria) or
  `"hospitality_education"` (meals, travel, education).
- Research (research payments, research funding, studies, trials, investigators): `research_by_year` and
  `research_studies`, never `payments_by_year`, which holds only consulting, speaking, meals, travel and education.
  Research amounts are whole-study totals: never call them income and never add them across people.
- Payments beside the ties disclosed on papers: `ties_vs_payments`. Show both; do not say they match or differ.
- Ownership: `ownership`. No physician holds a reported interest in a CAR-T manufacturer; say so plainly if asked.

Each query draws its own panel, and time charts mark the approval dates.
The map already shows the entity with research-payment links, which join an investigator to the hospital named
on a research payment; circle size shows payment dollars. You do not need `set_map`.
Payments cover 2019 to 2025 and are floors: manufacturers often leave the product blank.
