---
name: center-profile
use_when: The user asks about one treatment center or hospital (who works there, its CAR-T volume, which community the hospital belongs to, its accreditation or 340B status), or asks for the centers in a state or a ranking of centers by Medicare CAR-T stays.
default_edges: none
caveats: [MEDICARE, NOT_PRODUCT_SPECIFIC, LABELS]
---
# Center profile

1. `find_entity(text, "center")`, or `find_entity(text, "place")` for "centers in Texas".
2. One center: run `center_profile` (place, community, linked HCPs, FACT accreditation, 340B class, cancer-hospital
   status) and `center_core_hcps`; for volume run `center_medicare_cart_stays`.
   A state: run `centers_in_state(state)`. A ranking: `top_centers_by_medicare_stays(year, state)`.
3. The map already shows the center with its linked HCPs, or the centers of the state. You do not need `set_map`.
4. Each query draws its own panel: a fact box for the profile, a table of core HCPs, a line chart of stays with
   the approval dates marked.

Medicare stays count all CAR-T products and some other cell therapies together, traditional Medicare only. A
blank year means Medicare did not publish the count (fewer than 11), not zero. Cancer hospitals paid outside the
standard Medicare system and children's hospitals have no count at all: use the status words the query returns.
If the question is about the center's shared patients or referrers, or the payments, research studies,
prescribing or disclosed ties of its HCPs, the database has that too: run the matching query from "Also
available here" below (`scope="center"` with the CCN as `id`; shared-patient queries take `ccn` and `year`).
Never say such data is missing or not published without running its query.
