---
name: community-view
use_when: The user asks about a community or orbit (who is in it, who leads it, which centers anchor it, how it has grown), compares two communities, or names an institution's network. Not for one hospital (center-profile), and not for a community's payments, prescribing, disclosed ties or shared patients, which have their own skills.
default_edges: coauthor
caveats: [CORE_ONLY, LABELS]
---
# Community view

This skill covers who is in a community, who leads it and which centers anchor it. If the question is about the
community's payments, research studies, prescribing, disclosed ties or shared patients, the database has that
too: run the matching query from "Also available here" below, with `scope="community"` and this community's id
as `id` (`scope="community_core"` for leaders or core members only). Never say such data is missing or not
published without running its query.

1. `find_entity(text, "community")`. An institution name ("the Moffitt group") resolves to its community.
2. Run `community_summary` and `community_leaders`. Add `community_centers` when centers matter,
   `community_members` when the user wants the list of people (set `division_group` to core, affiliates,
   referring or other), and `community_coauthor_by_year` when the question is about growth or change over time.
3. For two communities run `community_compare` with both ids and focus the map on both.
4. The map already shows the community with co-authorship links and its leaders ringed. Call `set_map` only to
   change that, keeping `focus={"kind":"community","ids":[id]}` and `edges="coauthor"`: `highlight="main_centers"`
   when the question is about centers, or `show="focus_and_linked"` when the user asks about the community's
   affiliates or referrers.
5. Each query draws its own panel: a fact box for the summary, a ranked list of leaders, a table of members or
   centers, a line chart for a by-year query. Run a by-year query only when the question is about change.

Say "leaders by PageRank in the co-authorship network". Do not call anyone "the most important" or "the best".
Do not describe how firm or stable a community is. Paper and pair counts are for core members only.
If the user also asks about the community's shared patients, run `community_referral_flows` and say these are
not CAR-T referrals; for its Medicare CAR-T stays run `community_medicare_cart_stays` and say how many centers
are behind the sum.
