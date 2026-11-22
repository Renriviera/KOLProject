---
name: hcp-profile
use_when: The user names one clinician and asks about that person (profile, publications, co-authors, payments, prescribing, links to centers). Not for a ranking or a group of clinicians.
default_edges: coauthor
caveats: [CORE_ONLY, LABELS]
---
# HCP profile

1. `find_entity(text, "hcp")`. If more than one person fits, `ask_user` with name, city and state.
2. A general profile ("show me", "tell me about", "who is"): run `hcp_profile`, `hcp_publications_by_year`,
   `hcp_top_coauthors` and `payments_by_year`, and nothing else. A question about one topic: run `hcp_profile` and
   only that topic's queries:
   - publications: `hcp_publications_by_year`, `hcp_top_coauthors`
   - payments: `payments_by_year` with `scope="hcp"` and `id` set to the NPI
   - disclosed ties: `hcp_ties_papers`
   - prescribing: `partd_by_drug_year` or `prescribing_summary` with `scope="hcp"`
   - links to centers: `hcp_referral_links`; say "shared Medicare patients", never "referrals of CAR-T patients"
3. The map already shows the clinician with co-authorship links, or shared-patient links when you ran
   `hcp_referral_links`. Call `set_map` only to draw research-payment links: `focus={"kind":"hcp","ids":[npi]}`,
   `show="focus_and_linked"`, `edges="payments"`.
4. Each query draws its own panel, the profile fact box first.

Anything else about this person's payments, research studies, disclosed ties, Medicare billing or shared patients
is under "Also available here" below (`scope="hcp"` with the NPI as `id`). Never say such data is missing
without running its query.

For a non-core HCP there is no PageRank and no community of their own: say which community they are attached to
and on what basis, as the profile returns it. If the profile is nearly empty, say so; do not pad.
