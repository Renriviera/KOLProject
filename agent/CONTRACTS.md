# Build contracts for the agent tool

Shared rules for everyone writing a part of `src/kolproject/agent/`. Read `agent/PLAN.md` first.

## Fixed files (do not edit; report a needed change instead)

`paths.py`, `packbase.py`, `queries.py`, `pack_dims.py`, everything under `agent/` except `agent/pack/`.

## The pack

- One SQLite file, built only from published tables under `Data/processed/` (use `packbase.source(snapshot, table)`).
  Never read `Data/raw`, `Data/interim`, `research_notes` or the network in a pack or query module.
- Core tables already exist in `agent/pack/dims.sqlite` (rebuild with `uv run python -m kolproject.agent.pack_dims`):
  `dim_hcp(npi, name, first_name, last_name, credentials, city, state, zip5, hrr, lat, lon, geo_precision, division,
  division_group, is_core, community_id, community_basis, pagerank, pagerank_rank, is_leader, network_role, institution,
  core_publications, primary_center_ccn, specialty)`, `dim_center(ccn, name, city, state, zip5, hrr, lat, lon,
  geo_precision, community_id, community_basis, core_hcps, linked_hcps, is_main_center, fact_accredited, class_340b,
  pps_exempt_cancer_hospital, medicare_file_status_2024)`, `dim_community(community_id, label, short_label, kind,
  color_light, color_dark, region, top_institution, core_members, attached_hcps, centers)`, `hcp_center(npi, ccn)`,
  `dim_product(brand, generic, short_name, company_group, partner, target, lymphoma_product, hcpcs_code,
  first_us_approval)`, `product_event(date, brand, event_type, label, lymphoma, verified, source_url)`.
  `division_group` is one of core, affiliates, referring, other. `community_id` is NULL when unassigned; ids 1 to 13
  are the named communities, 14 to 17 isolated HCPs. lat/lon are filled later by the geography step.
- A part module `pack_<part>.py` exposes: `TABLES` (names it creates); `build(con)` (creates them with
  `packbase.create_table` / `load_csv`, adds indexes on npi, ccn, year as needed, and calls `packbase.register`
  for each with grain, source tables, one-sentence description, years); `card_facts(con)` returning
  `{(kind, id): [sentence, ...]}` with kind in hcp, center, community, product, manufacturer and id a string
  (at most three short factual sentences per entity, numbers with units and years, no names of other people);
  `check(con)` returning `{check_name: error_count}` (zero passes; row counts against the source CSVs, key
  uniqueness, foreign keys to the dims, no negative counts, totals that must match the source); and
  `main()` which copies `agent/pack/dims.sqlite` to `agent/pack/dev_<part>.sqlite`, runs build and check there
  and prints a summary. Develop only in your own dev file: several people are building at once.
- Blank stays NULL, never zero. Suppressed Medicare cells stay NULL. Keep ids as text (NPI ten digits, CCN six
  characters with leading zeros). Years are integers.
- Leave out: anything commercial-insurance, method diagnostics (stability, tiers, movers, backtests, scores),
  restricted-source content, free text written by manufacturers, statement text, street addresses, emails, phones.

## Named queries

- Register with `@query(...)` from `queries.py` in `queries_<part>.py`; read that file's docstrings. Names and
  parameters follow the skill files in `agent/skills/` and section 5.6 of the plan; if a name there cannot work
  as written, implement the closest thing and report the difference.
- A query function is `fn(con, **params) -> list of dict rows`, read-only, plain SQL with bound parameters only
  (never string-format a parameter into SQL), ordered, at most 2,000 rows. Use `packbase.scope_filter(scope)` for
  the four scopes; a community scope means every HCP attached to the community (core and orbit).
- Columns: declare `(name, label, unit)` in display order; labels are plain words a non-specialist reads on a
  chart axis; units from `queries.UNITS`. Shares are 0 to 1. Dollar columns end in `_usd`. Use `year` for years.
- Give each query a `default_panel` that draws it sensibly without the model's help, the caveat codes that apply
  (REFERRAL, MEDICARE, MEDICAID, NOT_PRODUCT_SPECIFIC, PAYMENTS, TIES, LABELS, CORE_ONLY), the tables read and
  the years covered.
- An id that does not exist should produce an empty result or a `QueryError` with a sentence the model can act
  on, never an exception trace.

## Tests and style

- `tests/test_agent_<part>.py`: build tiny synthetic tables in a temporary SQLite (invented NPIs, names and
  numbers; never real people), test each query's logic and blank handling, `check()` catching a broken table,
  and parameter validation through `queries.run`. One optional test may run every one of your queries against
  your real dev file when it exists. Run only your own test file: `uv run pytest tests/test_agent_<part>.py -q`.
- Style: compact Python 3.12, single quotes, no type annotations, small functions, module docstring; see
  `src/kolproject/agent/pack_dims.py`.
- Public repository: commit nothing, run no git command that changes state, write no real person's data into
  tests or code constants.
