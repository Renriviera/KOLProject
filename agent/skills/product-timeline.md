---
name: product-timeline
use_when: The user asks about a CAR-T medication (when it was approved, for what, who makes it), wants the approval timeline, or asks for national spending by product.
default_edges: none
caveats: [NOT_PRODUCT_SPECIFIC]
---
# CAR-T products and timeline

1. `find_entity(text, "product")` accepts brand, generic and short names (for example "axi-cel").
2. Facts: `product_facts(brand)` and `product_events(brand)`. Answer approval questions from these and from the
   product table in your instructions. If a date is not there, say it is not in the database.
3. National spending by product and year: `national_spending_by_brand_year(program)`, where program is
   `medicare_part_b` (outpatient only; most CAR-T is inpatient) or `medicaid`.
4. Payments naming a product, for a community, center or HCP: `payments_by_product(scope, id, brand)`.
5. The map shows the whole country, or the community, center or HCP the user also names. You do not need
   `set_map`.
6. Each query draws its own panel: a timeline of events, a table of product facts, a line per product for
   spending or payments.

Do not compare products on efficacy, safety or value. Do not describe indications beyond the short label in the
product table. For a product that is not approved yet, say only what the product table lists as not approved.
