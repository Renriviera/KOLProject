# CAR-T KOL tool on Qwen3.5-9B: design and build status

Status on 2026-11-20: **built and running locally.** Phases 0 to 5 are done; section 8 lists what was checked
and what is still open. [`README.md`](README.md) has the run instructions.

**Use:** an academic, non-commercial demonstration. Medical Science Liaisons and a marketing team are the
imagined audience that shapes the questions; the tool is not deployed to either.

## 1. What it is

A local web tool. The user types a question; the agent answers by **choosing views of our existing datasets**:

- **Top: a map of the United States** (state outlines, ZIP outlines when zoomed in) with CAR-T centers and HCPs at
  their real locations. The agent moves the map to the community, center or HCP in question, removes everything
  irrelevant, highlights leaders (PageRank) or main centers, and picks which links to draw: co-authorship,
  shared patients, or research dollars.
- **Below: a flexible canvas** of charts, tables and short text that answers the question.

The agent only shows views and does simple arithmetic (sum, share, difference, percent change, rank). It runs no
code, does not discuss how methods were validated, and has no access to community-stability or backtesting
outputs.

```
question -> one skill -> resolve names -> named queries -> map directive + panels + short text
            (Qwen3.5-9B)  (code, fuzzy)    (fixed SQL)     (validated answer, drawn by the page)
```

## 2. Decisions

- **Referral data is included for every user.** The tool is an academic demo, which the CareSet DocGraph
  non-commercial licence allows. Section 7 lists what that licence still requires.
- **Conferences are left out.** We hold no conference data, so there is no conference skill, table or card. The
  agent says conference presentations are not in the database.
- **Downloads made on 2026-11-20:** the model (`qwen3.5:9b`, 6.6 GB), two Census boundary files (67 MB), and
  address geocoding through the free Census Geocoder (street line, city, state and ZIP only).
- **Runs single-user on this Mac.** A shared server is not set up.

## 3. Architecture

| Part | What was built |
|---|---|
| Model | `qwen3.5:9b` served by Ollama on this machine, thinking off, temperature 0.1, 16k context. About 44 tokens per second on the M5 Pro. |
| Agent loop | `src/kolproject/agent/loop.py`: three model replies for a usual question (at most 9), seven typed tools, one skill per question. The model's reply is read as it is written, and each tool call runs as soon as it is complete. |
| Data pack | `uv run kolproject-agent-pack` builds `agent/pack/kol.sqlite` (41 tables, full-text index, name aliases), `cards.jsonl` and `manifest.json` from the published snapshot tables in about 12 seconds. The new pack replaces the old one only if all 248 checks pass. |
| Server | FastAPI on `127.0.0.1:8790`: `/api/chat` (streams the steps, the map and the panels as each is ready, then the checked answer), `/api/map`, `/api/geo/states`, `/api/geo/zcta`, `/api/meta`. At start it loads the model and has it read the prompt, so the first question is as fast as the rest. |
| Page | Static files in `agent/web/`: MapLibre GL for the map (no outside tiles: state outlines as one file, ZIP outlines fetched for the visible box when zoomed in), D3 for charts, the existing site's colour tokens. |

Tracked: `agent/` except `agent/pack/`, `src/kolproject/agent/`, tests. Not tracked: `agent/pack/` (it holds
address-level coordinates) and the raw geocoder responses under `Data/raw/`.

## 4. The agent

**Prompt.** [`prompts/system_prompt.md`](prompts/system_prompt.md), about 4,200 tokens once filled. Its
community table, product table, approval dates and skill list are rendered from the reference files on every
call, so the prompt and the database cannot drift apart. There is one prompt for every user; no roles.

**Skills.** Nine short playbooks in [`skills/`](skills/). The model loads exactly one per question; loading it
returns the playbook plus the exact signatures of the queries that skill may run.

| Skill | Answers | Default links on the map | Queries |
|---|---|---|---|
| `community-view` | Members, leaders, anchor centers, growth, comparison of two communities | co-authorship | 9, and 17 topic queries |
| `hcp-profile` | One clinician: papers, co-authors, payments, ties, prescribing, referral links | co-authorship | 8, and 15 topic queries |
| `center-profile` | A center or the centers in a state: people, Medicare CAR-T stays | none | 8, and 23 topic queries |
| `referral-flow` | Where a center's shared patients come from, year-to-year flow, out-of-region share, links to core HCPs | shared patients | 15 |
| `industry-payments` | Transfers of value by year, product, manufacturer, month; research payments; ownership | research dollars | 9 |
| `disclosed-ties` | Companies named with an author in conflict-of-interest statements, next to reported payments | co-authorship | 5 |
| `prescribing-claims` | Medicare Part D and Part B, Medicaid, hospital CAR-T stays, national spending | none | 11 |
| `product-timeline` | CAR-T medication facts, approval dates, payments and national spending by product | none | 4 |
| `scope-and-help` | What can I ask; out-of-scope and methods questions | unchanged | 1 |

**A wrong skill should still show the right view.** The model is told to choose the skill by what is asked
(payments, prescribing, ties, shared patients), not by who it is about, but it sometimes opens the profile of
the community, hospital or person instead. The three profile skills may therefore also run the topic queries
(`ALSO` in `queries.py`), listed in short form at the end of the skill text, and the map then draws that topic's
links. Without this the model answered from the profile, or said the figures were "not published".

**Tools.** `load_skill`, `find_entity`, `search_cards`, `run_query`, `set_map`, `add_panel`, `ask_user`. The
answer is plain text, with no tool. There is no code tool and no free SQL.

**One question, three replies.** The model writes about 40 tokens a second, and that writing is three quarters
of an answer's time; reading the prompt is not, because the model server keeps the prompt and the tool list
between questions. So the turn is built to make the model write little and to show each piece early:

1. *Choose and look up*: `load_skill` and `find_entity` in one reply. As soon as the entity is known the page
   moves the map to the skill's usual view of it.
2. *Read and show*: one `run_query` call names several queries and gives their shared parameters once. Each
   result is drawn at once as that query's standard panel, with a heading kept beside the query
   (`TITLES` in `queries.py`). `set_map` is called when the usual view is not the right one (another year,
   other links); `add_panel` only to draw a result another way.
3. *Answer*: a headline and one or two sentences, written as plain text and not as a tool call, so that the
   page can show it while it is written. The loop passes on each sentence once it is complete and the number
   check has accepted it. The caveats are attached by code from the queries that ran.

The preview of a result that the model reads is a small table with one header line (less than half the length
of naming every column on every row), with long text cells clipped.

**Guard rails, enforced in code, not left to the model.**

- A query runs only if the loaded skill lists it; parameters are typed and checked, and an error message tells
  the model what to fix.
- Numbers in charts and tables never pass through the model. The model reads a preview (at most 14 rows; a
  long time series is sampled at both ends, and dollar columns come with their totals) and refers to a result by
  its handle, which is the query name.
- Every number in the model's text is compared with the query results. A sentence with a number that no result
  supports is dropped; a headline with one is replaced by the first panel's title.
- Caveat sentences, legends and source lines are printed by the page from codes, so they are always worded the
  same. The referral caveat is attached whenever referral data or referral links are on screen.
- If the model does not set the map, the skill's usual view of the entity it looked up is used. When nothing
  from the database is shown (a refusal), the map is left where it was.
  An unreadable tool call is retried, and a tool that fails returns an error the model can act on instead of
  ending the turn.
- What the page draws early is provisional. The final answer is the checked one, and the page replaces anything
  drawn before it that it does not confirm.
- Equally good name matches end the turn with a question back to the user.
- The assembled answer is validated against [`schemas/view_spec.schema.json`](schemas/view_spec.schema.json).

**What is deliberately absent.** Stability tiers, seed consensus, perturbation and mover analyses, temporal
backtests, composite scores, and any commercial-insurance data are not loaded into the pack, and the pack build
fails if a column with such a name appears. The agent cannot serve them even if asked.

## 5. Data pack ("RAG" layer)

Two kinds of retrieval feed the model: **named queries** over tables for numbers, and **text cards** for finding
and describing things. Both are built only from published snapshot tables.

### 5.1 Tables (Medicare and Medicaid are the claims core)

| Pack table | Grain | Rows |
|---|---|---|
| `dim_hcp` | HCP: division, community and basis, PageRank, location | 17,510 |
| `dim_center`, `hcp_center` | center; HCP x center | 173; 10,331 |
| `dim_community` | community (13 named, 4 isolated) | 17 |
| `dim_product`, `product_event`, `manufacturer` | product; dated event; company group | 7; 25; 7 |
| `pub`, `authorship`, `hcp_pub_year`, `hcp_pub_summary` | paper; HCP x paper; HCP x year; HCP | 4,062; 6,947; 3,167; 1,117 |
| `coauthor_pair`, `coauthor_pair_year` | HCP pair; pair x year | 17,437; 27,018 |
| `pay_general_record`, `pay_general_record_brand` | payment record; record x brand | 39,655; 29,750 |
| `pay_general_year`, `pay_general_hcp_mfr_year` | HCP x year; HCP x company x year | 39,781; 10,829 |
| `pay_research_record`, `pay_research_year`, `pay_research_hcp_center_year` | record; HCP x year; investigator x hospital x year | 19,790; 4,732; 482 |
| `ownership` | record | 606 |
| `tie_paper`, `tie_author_paper`, `tie_author_group`, `tie_hcp_year` | paper; HCP x paper; HCP x company; HCP x year | 4,062; 6,984; 4,825; 3,174 |
| `mcr_partb_year` | HCP x year, Medicare Part B drug and non-drug payment 2017 to 2024 | 140,080 |
| `mcr_marker_year` | HCP x year x code, Part B lymphoma drug and infusion rows | 79,992 |
| `mcr_partd_year` | HCP x year x drug, Part D oral lymphoma drugs | 108,504 |
| `mcd_year` | HCP x year, Medicaid paid amounts 2018 to 2024 | 54,660 |
| `center_medicare_stays`, `mcr_cart_admin_state_year` | center x year; state x year | 692; 56 |
| `national_spending` | brand x program x year | 70 |
| `ref_link_year` | HCP x center x year, shared patients 2017 to 2022 | 147,226 |
| `ref_hcp`, `ref_hcp_change_year` | HCP; HCP x year | 17,510; 70,700 |
| `ref_center_year`, `ref_region_center_year` | center x year; region x center x year | 1,038; 11,083 |
| `ref_core_links`, `ref_core_senders`, `ref_group_year`, `ref_reach_group_year` | links to core HCPs; division-group totals | 6,597; 2,079; 24; 24 |
| `card`, `alias` | text card; lookup name | 21,873; 56,947 |

### 5.2 Left out on purpose

- Anything commercial: hospital price-file remittance counts and the New York, California and Florida all-payer
  tables.
- Method diagnostics: community stability, perturbation, movers, backtests, composite scores.
- The 2009 to 2015 referral baseline (a different method that would invite wrong comparisons), pair-level
  shared-patient edges, teaching-hospital general payments, review queues and audit tables.
- Everything restricted by source terms (manufacturer locator content, exact BMT InfoNet counts,
  conflict-of-interest statement text, street addresses).
- Conference data: none is held.

### 5.3 CAR-T medication reference

[`reference/cart_products.json`](reference/cart_products.json) lists the seven approved products (Kymriah 2017,
Yescarta 2017, Tecartus 2020, Breyanzi 2021, Abecma 2021, Carvykti 2022, Aucatzyl 2024) with every later
indication, the company group, target, Medicare billing code, a lymphoma flag, class-wide and coding events, and
one pending product. All 25 dated events were checked against FDA or CMS pages on 2026-11-20 and carry their
source address. The same file fills the system prompt and the `dim_product` and `product_event` tables. The
earlier draft is kept beside it as `cart_products.draft.json`.

What it enables, stated honestly:

- Vertical approval markers on any time chart (`options.markers`); a one-product chart gets them by default.
- A split by product where the data names the product: industry payments (per year, per month) and national
  Medicare and Medicaid spending.
- **Not** a split of physician or hospital claims by product: Medicare's hospital group covers all CAR-T products
  together and physician claims carry almost no CAR-T rows. The agent says so (caveat `NOT_PRODUCT_SPECIFIC`).

### 5.4 Communities, orbits and colours

- **Fixed.** [`reference/communities.json`](reference/communities.json) freezes the 13 named communities from the
  published site run (ids, labels, the five leaders by PageRank) and their colours, taken from the existing site's
  palette so the two match. Four isolated HCPs share one grey "outside main network" entry. Nothing is recomputed
  at run time.
- **Orbit rule for everyone else,** stored with its basis so the agent can state it: a non-core HCP with
  co-authorship links joins the community holding most of their co-author weight; otherwise an HCP at a center
  joins that center's community; a center takes the community of most of its core HCPs; a referring HCP joins the
  community of their main center in the referral data; the rest stay unassigned and are drawn hollow.
- **Two readings of a community in queries:** `community` (core members plus attached HCPs) and `community_core`
  (core members only). Every panel states which one it shows.

### 5.5 Text cards and search

21,873 cards in `cards.jsonl`, indexed with SQLite full-text search: HCP 17,510, paper 4,062, center 173,
inventory 41, glossary 26, product event 25, community 14, caveat 8, product 7, manufacturer 7. Each card cites the
pack tables it was written from. `find_entity` matches names first (exact, then fuzzy, with aliases such as
"axi-cel", "MSK" or a leader's name for a community); `search_cards` ranks cards for a described thing ("children's
hospitals in Ohio"). No embedding model is used.

### 5.6 Named queries (the only way numbers reach the agent)

53 fixed, parameterised queries; each declares its columns with units, a default panel, the caveats it carries, the
tables it reads and the years covered. Most take `scope` (`hcp`, `community`, `community_core`, `center`,
`division_group`) and `id`. The list per skill is in section 4; the definitions are in
`src/kolproject/agent/queries_people.py`, `queries_money.py`, `queries_claims.py` and `queries_referral.py`.

## 6. The page

### 6.1 Map

- **Base:** state outlines always; ZIP-code outlines from a regional zoom level. No street map, no outside tiles.
- **Points:** centers as squares, HCPs as circles, at geocoded addresses (ZIP centroid when an address cannot be
  matched). People at one address fan out. All 173 centers and 99.4% of HCPs have a point.
- **Colour:** the fixed community colour. Unassigned points are grey and hollow.
- **Focus:** the map flies to the focus and keeps only what the agent asked for; everything else is removed, not
  faded. "Show everything" restores the national view, and the link type and referral year can be changed by hand.
- **Highlight:** leaders get a ring and a name label; main centers get a label.
- **Links and legend,** chosen per question; the legend is generated with fixed wording:

| Links | Drawn as | Width | Legend also states |
|---|---|---|---|
| Co-authorship | line between two HCPs | shared papers | years covered |
| Shared patients | arrow from HCP to center | shared Medicare patients | the year, and "Any condition, Medicare fee-for-service only. Not CAR-T referrals." with the CareSet credit |
| Research dollars | dashed line from investigator to the hospital on the payment; circle size = payments received | whole-study dollars | years, "amounts reported by manufacturers" |

### 6.2 Canvas below the map

- A 12-column grid of panel cards. Panel types: `kpis` (fact box), `table`, `bar`, `line`, `timeline`,
  `ranked_list`, `text`; widths full, half or third. Up to six panels per answer.
- Line charts take approval markers and a split (brand, drug, company, division group); different units become
  separate small charts, never two axes on one. Every chart has a "Show as table" switch and a hover readout.
- Above the panels: the headline and the short text. Below each panel: its source and years. At the end: the
  caveat sentences.
- The splitter between map and canvas can be dragged. Clicking a point or a table row asks the follow-up question
  ("Show the profile of ...") through the same agent path.

## 7. Use and licence

- **CareSet DocGraph (referral data):** CC BY-NC-SA 4.0 with CareSet's non-commercial addendum. The page credits
  CareSet Systems on every referral view and its header reads "Academic, non-commercial demonstration". Moving the
  tool into commercial or marketing use would need a commercial licence from CareSet first; nothing else in the
  pack has that limit.
- **Other sources** are federal or state public data and publication metadata, named in each panel's source line.
- **Tone:** one neutral voice: no promotional claims, no ranking of clinicians as targets, no suggested messages.

## 8. Build status

| Phase | Work | Status |
|---|---|---|
| 0. Setup | Model pulled; tool calling confirmed | Done |
| 1. Reference and geography | Product dates verified with sources; orbit assignment; geocoding; state and ZIP outlines | Done |
| 2. Pack builder | Tables, cards, full-text index, aliases, manifest with source hashes, forbidden-column check | Done: 248 checks pass |
| 3. Queries and server | 53 queries, each tested against known answers on invented data and reconciled on the real pack | Done |
| 4. Agent | Loop, prompt, skills, number check, fallbacks; tested with a scripted model | Done; see 8.2 |
| 5. Page | Map with three link layers and legends, panel canvas | Done |
| 6. Hardening | Answer speed and a routing test set | Done on 2026-11-21; see below |
| 6. Hardening | Narrow-screen review | Open |

### 8.1 Speed and routing (2026-11-21)

**Where the time went.** Measured per model reply from the model server's own counters: about three quarters of
an answer was the model writing tool calls and text (about 500 tokens at 40 a second), and only a quarter was
reading, because the server keeps the prompt between questions. So the work went into writing less and showing
each piece sooner, not into a shorter prompt.

| Change | Effect |
|---|---|
| Skill choice and name lookup in one reply; queries in the next; the answer in the third | 4 replies down to 3 |
| Each query draws its own standard panel, with a heading kept beside the query | No `add_panel` calls: about 50 tokens saved per panel |
| One `run_query` call names several queries and gives their parameters once | About 30 tokens saved per extra query |
| The map follows what was looked up and queried (links, year, focus) | `set_map` is rarely written: about 60 tokens saved |
| Previews for the model as one small table, long text clipped | Less than half as much to read before the answer |
| A headline and one or two sentences instead of up to 120 words | About 100 tokens saved |
| The reply is read as it is written and each tool call runs at once | Map at about 3 s, panels at about 5 s |
| The answer is plain text, passed on sentence by sentence after the number check | Headline about 1 s before the answer is complete |
| The server loads the model and has it read the prompt at start | First question 5 to 10 s faster |

**Result**, each question asked once per run (`uv run kolproject-agent-eval`):

| | Before | After, working set | After, held-out set |
|---|---|---|---|
| Questions | 102 | 106 | 83 |
| Routed correctly (right outcome, skill and entity) | 90% | 100% | 98% |
| Every check passed | 89% | 100% | 98% |
| Right skill | 92% | 100% | 99% |
| Right entity | 99% | 100% | 98% |
| Right queries | 91% | 100% | 99% |
| Right map | 93% | 100% | 97% |
| Right caveats | 91% | 100% | 100% |
| Seconds per answer, median | 18.4 | 9.8 | 8.9 |
| Seconds per answer, 90th percentile | 34.2 | 14.5 | 11.2 |
| Seconds per answer, slowest | 44.6 | 19.2 | 15.8 |
| Answers within 10 seconds | 17% | 55% | 80% |
| Seconds to the first map or panel, median | 18.4 | 2.8 | 2.5 |
| Seconds to the first words of the answer, median | 18.4 | 8.5 | 7.6 |
| Model replies per answer, median | 4 | 3 | 3 |
| Tokens written per answer, median | 499 | 209 | 197 |
| Answers with a sentence removed by the number check | 10 | 2 | 1 |
| Answers with a tool error | 8 | 1 | 2 |

Before the answer was streamed, the same three-reply agent measured a median of 9.8 s with 54% of answers within
10 s on the working set. Streaming puts the headline on screen about a second before the answer is complete and
does not shorten the whole answer measurably: the answer is short.

**The two sets.** [`eval/routing_set.json`](eval/routing_set.json) holds 106 questions in twelve groups
(community, HCP, center, payments, ties, prescribing, referral, product, out of scope, ambiguous names,
follow-ups, and a new entity named after another answer), each with the skill, entity ids, queries, parameters,
map and caveats a correct answer must use. It is the working set: the prompt, the skills and the loop were
adjusted against its failures, so its 100% only says those failures are fixed.
[`eval/routing_holdout.json`](eval/routing_holdout.json) holds 83 questions, each written before the agent was
run on it and never tuned against. Its three groups have different histories:

| Held-out group | Written | Asked | Routed correctly |
|---|---|---|---|
| `h-` (40 questions) | before the first run of the three-reply agent | five times, with fixes in between | 39, then 36, 39, 39 and 40 of 40 |
| `h2-` (21 questions) | before the first run of the repaired streamed answer | three times, with fixes in between | 18, then 19 and 20 of 21 |
| `h3-` (22 questions) | before the first run after the profile skills could run topic queries | once | 21 of 22 |

The first-ask score of the newest group, 21 of 22, is the cleanest estimate of routing on unseen questions, on a
very small sample; the first-ask scores of the two earlier groups were 39 of 40 and 18 of 21. After fixing
anything a held-out run reveals, add new held-out questions (`h4-`) before measuring again.

A skill counts as right when it is one the question lists, or when it ran the queries the question requires:
since the profile skills can run the topic queries, a skill that shows the right view is a right choice. The
rule was introduced with that change and alters no earlier result.

**What the runs found, and the fix.**

| Failure | Fix |
|---|---|
| Payment and prescribing questions about a community went to `disclosed-ties` or `community-view`, which showed the wrong thing or said the data was "not in the database" | A rule in the prompt to choose the skill by what is asked, not by who it is about; sharper skill descriptions; a redirect at the top of `community-view`. Not fully cured: see 8.2 |
| A ranking by shared patients was answered with Medicare stays | `center-profile` now says its ranking is by stays and sends shared-patient questions to `referral-flow` |
| A map focus or lookup kind that does not exist ended the turn with an error | Both now return a message the model can act on; any tool that raises does the same |
| The model wrote the same malformed tool call nine times in a row | A retry is written with more variety; three unreadable replies in a row end the turn with a plain message |
| A query name called as if it were a tool | It is run as that query |
| "All affiliates" was met with a question back | The prompt explains the national division scope |
| The number check read "340B" as 340 billion and removed true sentences; "H. Lee Moffitt" was split as two sentences | Program names with digits are not read as figures; an initial does not end a sentence |
| With the answer as plain text, the model answered straight after the name lookup, with no query and sometimes wrongly (working set fell to 87%) | Words alone are an answer only after a query has run or in the help skill; otherwise the model is told once to load a skill or run its queries, and a second reply in words is taken as a refusal |
| With the answer as plain text, an ambiguous name was written about instead of asked about | The question back is built in code from the lookup's candidates |
| A question naming a new hospital right after another answer was answered about the previous one (found in the browser, not by either set) | The follow-up note now says it applies only when the question names nothing itself; four such questions were added to the working set |
| The model said accreditation and 340B status were "not in the database" | The prompt's inventory and the query list the model reads now say what each query returns |
| Topic questions about a community or a hospital still went to its profile skill, which answered from the profile or said the figures were "not published" (3 of 61 held-out) | The three profile skills may run the topic queries themselves and are told never to call such data missing without running its query; the map then draws that topic's links |
| The right skill ran the wrong query under a misleading headline: research payments answered with general payments, a named infused drug with a one-year summary, national Medicaid spending on CAR-T with Medicaid paid to clinicians | The payments and prescribing recipes name the query for each of these and say which queries do not hold it |

Two scoring rules were relaxed after the first run, and the first run was re-scored under them: the skill is not
tested when the right outcome is a question back or a refusal, and a refusal may be worded in several ways. One
vague question (ref-08) was reworded, and four questions were added to the working set after the first run.

**Checked in the browser with the model** (2026-11-21, final agent): Medicare Part B payment to the HCPs of one
hospital (the payment chart, where the same question had drawn "not published" before) and the largest research
studies of one community (the studies table with research-dollar links, where it had drawn a paper count), asked
one after the other in one session. Earlier the same day, with the streamed answer: the MD Anderson referral question (map
at 2.2 s, four panels at 6.6 s, headline at 9.0 s, last sentence at 11 s, caveats at 12.8 s; the whole answer
took 32 s before), an ambiguous name (five choices at 4.8 s), and the accreditation question. Before streaming,
also Yescarta payments to the Stanford orbit and a refusal (map not moved, panels cleared). With canned model
replies: a panel redrawn by `add_panel`, a map year changed by `set_map`, and a failure after pieces were
already drawn.

### 8.2 Open

- **About half the working-set answers and a fifth of the held-out ones take more than 10 seconds** (slowest 16
  to 19). The remaining time is the model writing about 200 tokens per answer and, on some questions, one push
  to load a skill or run a query. The next gain would be to resolve names in code before the first reply.
- **Refusals with something shown.** A question outside the database that names a product or a person is
  sometimes refused in words while that product's or person's panel is drawn (one of 22 newest held-out
  questions). The words are right; the panel is beside the point.
- **Headlines.** With the answer as plain text the model writes no separate headline. In about one answer in ten
  its first sentence is too long to be one and the panel's heading is shown instead; before streaming it was 1
  in 102.
- **The test sets are small and written by the same author as the skills.** A set written by someone else, in
  their own words, would be a fairer test. The model's choices vary a little between runs.
- **Routing is scored, wording is not.** Nothing checks whether the sentences are a good reading of the panels
  beyond the number check. One sampled answer said a count "grew" between two years when it fell by 14.
- **The model sometimes** picks `community_core` where `community` was meant (the panel says which was used) or
  writes a sum that is wrong (the number check then removes that sentence, so the text can be shorter than
  intended).
- **Lightly reviewed:** the light colour theme was checked on one answer; screens narrower than about 900
  pixels were not checked.
- A few center names keep odd capitals from the source ("Uclamc"); 54 core HCPs have no location and are counted
  in the legend as "without a location".

## 9. Known limits to state up front

- Referral data is Medicare fee-for-service shared patients for any condition, 2017 to 2022. It is not CAR-T
  specific, and the page says so wherever it appears.
- Conference presentations are not in the database.
- Claims are Medicare and Medicaid only, and they do not identify the CAR-T product.
- Communities and PageRank exist for the 500 core HCPs; everyone else is attached by the orbit rule.
- Locations and division labels are 2024 facts.
- A 9B model will sometimes pick the wrong skill or entity. The design limits the damage: a wrong choice produces
  a wrong view of real numbers, never invented numbers.
