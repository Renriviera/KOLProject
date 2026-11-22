# CAR-T KOL Navigator

An academic, non-commercial demonstration. You type a question about CAR-T lymphoma clinicians (HCPs), treatment
centers or their co-authorship communities; a small local model (Qwen3.5-9B) answers by choosing views of the
published tables: a map of the United States on top, and charts, tables and short text below it.

The agent only shows views of the data and does simple arithmetic. It runs no code, has no commercial-insurance
data, and cannot show or discuss how the communities were validated.

**To install it, get its data and ask it questions, read [USAGE.md](USAGE.md).** This file explains how the tool
works and what was measured.

## Run it

Everything runs on this machine. The four steps are needed once; after that only the last one. The second and
third need data that is not in Git: three table folders and two CMS address files. [USAGE.md](USAGE.md) says
where each comes from.

```bash
ollama pull qwen3.5:9b                       # the model, 6.6 GB
uv run python -m kolproject.agent.geo        # map outlines and locations (see "Geography" below)
uv run kolproject-agent-pack                 # the data pack: about 12 seconds, 205 MB
uv run kolproject-agent-serve                # http://127.0.0.1:8790
```

On an Apple M5 Pro the map moves about 3 seconds after a question, the first panels appear at about 5, the
headline at about 8, and the answer is complete at about 9 to 10 (see "Speed and routing, measured" below). The server loads the model when it
starts, so the first question is not slower than the rest.

## What is where

| Path | Contents |
|---|---|
| [`prompts/system_prompt.md`](prompts/system_prompt.md) | The one system prompt. Its community table, product table, approval dates and skill list are filled in from the reference files each time, so prompt and database cannot disagree. |
| [`skills/`](skills/) | Nine short playbooks. The model loads exactly one per question; each lists the queries it may run. The three profile skills (community, center, HCP) may also run the payment, ties, prescribing and referral queries, so a question about a hospital's payments still gets the payment view if the model opens the hospital's profile. |
| [`reference/communities.json`](reference/communities.json) | The 13 named communities with fixed ids, labels, colours and leaders by PageRank. |
| [`reference/cart_products.json`](reference/cart_products.json) | The seven approved CAR-T products from Kymriah (2017) on: every US approval and later indication, company, target, Medicare billing code, with the source of each date. |
| [`schemas/view_spec.schema.json`](schemas/view_spec.schema.json) | The shape of an answer: one map directive, up to six panels, a headline, text and caveat codes. |
| [`web/`](web/) | The page: map (MapLibre GL), charts and tables (D3). No outside map tiles. |
| [`eval/`](eval/) | Two sets of test questions with the skill, entity, queries, map and caveats a correct answer must use: a working set of 106 and a held-out set of 83. |
| `pack/` (not in Git) | Built files: `kol.sqlite` (41 tables, full-text index, name aliases), `cards.jsonl` (21,873 text cards), `geo/` (points and outlines), `manifest.json`. |
| [`../src/kolproject/agent/`](../src/kolproject/agent/) | Pack builder, the 53 named queries with their panel headings, name search, the agent loop, the number check, map selection, the server, the scorer for the test questions. |
| [`USAGE.md`](USAGE.md) | How to install the tool, where each dataset is fetched from, and how to ask questions. |
| [`PLAN.md`](PLAN.md) | The design, what was built and what is still open. |

## How an answer is made

The model works in three replies, and the page draws each piece as soon as the model has written the tool call
for it, without waiting for the rest.

1. **Choose and look up.** The model picks one skill and calls `find_entity` for each name in the question.
   Equally good matches end the turn with a question back to the user. Otherwise the map moves at once to the
   skill's usual view of that entity.
2. **Read and show.** It calls named queries (`run_query`), several in one call when they share their
   parameters. There is no code tool and no free SQL: a query is a fixed statement with typed parameters, and a
   skill can only run the queries listed for it. Each result is drawn as that query's standard panel, with a
   heading written in the query file; the model calls `add_panel` only to draw a result another way. It sets the
   map (`set_map`: where to look, what stays visible, which links to draw, what to highlight) when the usual
   view is not what the question needs. Data values never pass through the model.
3. **Answer.** It writes a headline and one or two sentences as plain text. Each sentence is shown as soon as it
   is complete and the number check has accepted it; a sentence containing a number that no query result
   supports is never shown. At the end code attaches the caveat sentences and validates the whole answer against
   the schema.

Map links are one of three kinds, chosen per question, each with its own legend:

| Links | Drawn as | Width |
|---|---|---|
| Co-authorship | line between two HCPs | shared papers |
| Shared patients | arrow from an HCP to a center, one year from 2017 to 2022 | shared Medicare patients |
| Research dollars | dashed line from an investigator to the hospital named on the payment | whole-study dollars |

## Speed and routing, measured

Measured on 2026-11-21 with `uv run kolproject-agent-eval`, each question asked once per run, on an Apple M5 Pro.
"Before" is the agent as first built; "after" is the agent described above.

| | Before | After, working set | After, held-out set |
|---|---|---|---|
| Questions | 102 | 106 | 83 |
| Routed correctly (right outcome, skill and entity) | 90% | 100% | 98% |
| Every check passed (also queries, parameters, map, caveats) | 89% | 100% | 98% |
| Seconds per answer, median | 18.4 | 9.8 | 8.9 |
| Seconds per answer, 90th percentile | 34.2 | 14.5 | 11.2 |
| Seconds per answer, slowest | 44.6 | 19.2 | 15.8 |
| Answers within 10 seconds | 17% | 55% | 80% |
| Seconds to the first map or panel, median | 18.4 | 2.8 | 2.5 |
| Seconds to the first words of the answer, median | 18.4 | 8.5 | 7.6 |

How to read this:

- **The working set flatters the agent.** The prompt, the skills and the loop were adjusted against its failures
  over several runs, so 100% says the known failures are fixed, not that the agent is always right.
- **The held-out figure to quote is 21 of 22.** Held-out questions are written before the agent is run on them
  and are never tuned against. They came in three groups, each added after fixes prompted by the one before:
  the newest 22 were asked once and 21 were routed correctly. The two earlier groups scored 39 of 40 and 18 of
  21 when first asked, and 40 of 40 and 20 of 21 in the last run, after the fixes.
- **Twenty-two questions is a small sample**, and every question was written by the same author as the skills,
  in the same vocabulary. Expect more misses on questions phrased very differently.
- **What the misses look like now.** One question about a person was answered from their community's view (the
  answer was correct). One question outside the database (survival rates) was correctly said to be missing, but
  with a product panel shown beside the refusal.
- **A wrong skill no longer means a wrong view.** The community, hospital and clinician skills can also run the
  payment, ties, prescribing and referral queries. In the last held-out run three answers were given that way.
- **The slower working-set answers** are its heavier questions (full profiles, five-query referral views). On
  either set the map moves at about 3 seconds, panels appear at about 5, and the headline at about 8.
- **Streaming the answer bought about one second**, and in about one answer in ten the model's first sentence is
  too long for a headline, so the panel's heading is shown instead.
- **Routing is not correctness of the text.** The scorer checks which skill, entity, queries, map and caveats
  were used. The model's sentences are checked only by the number check.
- The model's choices vary a little between runs, so a rerun can differ by a question or two.

The questions are in [`eval/routing_set.json`](eval/routing_set.json) and
[`eval/routing_holdout.json`](eval/routing_holdout.json); the result files are in
[`../evaluations/`](../evaluations/) (`agent_routing_baseline.json`, `agent_routing.json`,
`agent_routing_holdout.json`). A held-out question stays useful only while nobody tunes against it: after fixing
something a held-out run revealed, add new held-out questions before measuring again.

## What the data can and cannot say

- **Referral views are not CAR-T referrals.** They are Medicare fee-for-service patients shared between
  clinicians for any condition, 2017 to 2022 (CareSet Systems DocGraph, CC BY-NC-SA 4.0 with a non-commercial
  addendum). The legend and a caveat say so on every referral view. Commercial use would need a licence from
  CareSet.
- **Claims are Medicare and Medicaid only**, and physician and hospital claims do not identify the CAR-T product.
  Only industry payments and national spending are split by product. Approval dates can be drawn as vertical
  markers on any time chart.
- **Communities and PageRank are defined for the 500 core HCPs.** Other HCPs and centers are attached to the
  community they are most linked to (co-authors first, then their center, then their main center in the referral
  data). The basis is stored for each one.
- **Not in the pack on purpose:** commercial-insurance tables, community stability and backtesting outputs,
  composite scores, conference data (we hold none), and anything restricted by source terms.
- Locations and division labels (core, affiliate, referring) are 2024 facts.

## Geography

`uv run python -m kolproject.agent.geo` downloads two Census boundary files (state outlines, and ZIP Code
Tabulation Areas, 67 MB) and geocodes each distinct practice or hospital address with the free Census Geocoder.
Only the street line, city, state and ZIP are sent, under a running row number: no name, NPI or hospital number
leaves this machine. An address that does not match falls back to the centroid of its ZIP area. All 173 centers and
99.4% of HCPs have a point. The coordinates stay in the git-ignored `pack/` folder.

## Checks

```bash
uv run pytest tests/test_agent_*.py          # queries against known answers, search, map selection, the loop with a scripted model
uv run kolproject-agent-pack --check         # re-run the pack's own checks on the built file
uv run kolproject-agent-eval --check         # the test questions name only skills, queries and ids that exist
uv run kolproject-agent-eval                 # ask the 106 working questions with the model and score them (about 17 minutes)
uv run kolproject-agent-eval --set holdout   # the 83 held-out questions (about 13 minutes)
```

The pack build stops if any table carries a column from the restricted list (street address, phone, email,
statement text, manufacturer locator fields).
