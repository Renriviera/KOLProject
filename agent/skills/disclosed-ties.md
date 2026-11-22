---
name: disclosed-ties
use_when: The user asks what a clinician or a community has disclosed on papers (conflict-of-interest statements and the companies named in them). Not for money paid or funded, which is industry-payments.
default_edges: coauthor
caveats: [TIES]
---
# Disclosed industry ties

1. Resolve the HCP or community, then pass `scope` and `id` (`"hcp"`, `"community"`, `"community_core"`, `"center"`).
2. `ties_by_manufacturer` and `ties_by_year`. For one HCP, `hcp_ties_papers(npi)` lists the papers where the HCP
   is named with a CAR-T manufacturer.
3. To set disclosures beside reported payments run `ties_vs_payments`. Do not say they "match" or "do not
   match"; show both.
4. The map already shows the HCP or community with co-authorship links. You do not need `set_map`.
5. Each query draws its own panel: bars by company, a line by year, a table of papers or of ties beside payments.

The tool never shows the statement text. Publication year is not the year of the relationship. "No statement" and
"not named" are different: use the words the data returns.
