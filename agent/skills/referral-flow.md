---
name: referral-flow
use_when: The user asks about shared patients or referrals (where a center gets patients from, its patient flow year to year, its out-of-region share, who sends to a core HCP, which centers a clinician is linked to, which centers have the most shared patients, or what share of affiliates and referring HCPs share patients with centers or core HCPs).
default_edges: referral
caveats: [REFERRAL, MEDICARE, LABELS]
---
# Referral and patient flow

Write "shared Medicare patients" and state once that these are not CAR-T-specific referrals. Counts add up clinician pairs, so call them "shared-patient counts", never "patients treated".

For a center:
1. `center_referral_summary(ccn, year)` for the fact box, and `center_referrer_base_by_year(ccn)` for the trend
   2017 to 2022.
2. Where the patients come from: `center_referrers_by_division(ccn, year)` (most of a center's count comes from
   its own affiliates, so show this split) and `center_referrers_by_region(ccn, year)` (referring HCPs by region).
3. Who: `center_top_referrers(ccn, year)`. Links to core HCPs at the center: `center_core_hcp_senders(ccn, year)`.
4. Pass the year the user names in `params` (the latest is 2022). The map already shows the center with its
   shared-patient links for that year. You do not need `set_map`.
5. Each query draws its own panel. Run the summary and the queries the question asks about, not all of them:
   "how has it changed" needs the by-year query, "from where" the division and region queries, "who" the top
   referrers.

For an HCP: `hcp_referral_summary`, `hcp_referral_links`, `hcp_referral_by_year`, `hcp_core_links`.
For a community: `community_referral_flows(community_id, year)` and `community_referral_by_year(community_id)`.
National, with no entity to look up: `top_centers_by_shared_patients(year, state)` ranks centers;
`referral_flows_by_group` gives, per division and year, the HCPs sharing patients with centers and their
out-of-region share; `referral_reach_by_group` gives the share of each division that shares patients with core
HCPs. The map then shows the whole country.

A year with no link is "not observed", never zero. Do not call any clinician a "referrer of CAR-T patients".
