# Using the CAR-T KOL Navigator

This guide covers three things: how to install and start the tool, where its data comes from and how to get the
part that is not in this repository, and how to ask it questions. [README.md](README.md) explains how the tool
works inside, and [PLAN.md](PLAN.md) holds its design and the open work.

The tool is an academic, non-commercial demonstration. It runs on your own machine: a small local language model
(Qwen3.5-9B, run by Ollama) reads your question and chooses which views of the project's tables to show. The
tables are about CAR-T lymphoma clinicians (HCPs, healthcare professionals), treatment centers and the
co-authorship communities the clinicians form.

## What you need

| | |
|---|---|
| Computer | The tool was built and timed on an Apple M5 Pro laptop. No other machine has been tested; a slower one gives slower answers. |
| Software | [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs Python 3.12 for the project; [Ollama](https://ollama.com/download), which runs the model; a current web browser. |
| Internet | For the downloads below, and the first time the page opens, because it loads two libraries (MapLibre GL and D3) from cdnjs. Questions and answers stay on your machine. |
| Disk space | 6.6 GB for the model, about 350 MB for the built data pack, 231 MB for the three table folders, and 900 MB more if you build the map locations yourself. |

## Set up

Until the branch is merged the tool lives on the `cart-kol-navigator` branch. From the repository folder:

```bash
git switch cart-kol-navigator
uv sync --locked                          # the locked Python environment
ollama pull qwen3.5:9b                    # the model, 6.6 GB
```

Then get the data (next section) and run these three commands in order. The first two are needed once.

```bash
uv run python -m kolproject.agent.geo     # map outlines and the location of each center and clinician
uv run kolproject-agent-pack              # the data pack: about 12 seconds
uv run kolproject-agent-serve             # the tool, at http://127.0.0.1:8790
```

Open http://127.0.0.1:8790 in the browser. The server loads the model when it starts, so the first question is
as fast as the later ones. On the Apple M5 Pro the map moves about 3 seconds after a question and the answer is
complete after about 9 to 10 seconds.

## Get the data

The tool reads one set of local files, the data pack in `agent/pack/`. The pack is built from the project's
published tables under `Data/processed/`, and three of those table folders are not in this repository yet.

| What | In this repository | Size | How to get it |
|---|---|---|---|
| Tables of 2026-10-09, 2026-10-15 and 2026-10-24: the 500 core clinicians, their papers, the 173 centers, the 17,510 clinicians with division labels, shared-patient flows | Yes | | Nothing to do |
| The 13 named communities with their colours, and the CAR-T product reference | Yes, in `docs/data/site.json` and [`reference/`](reference/) | | Nothing to do |
| Tables of 2026-11-11, 2026-11-19 and 2026-11-20: payments, claims, disclosed ties, co-authorship beyond the core 500, shared-patient links | **No** | 231 MB | Copy or rebuild, see below |
| Two address files from the Centers for Medicare & Medicaid Services (CMS) | No | 900 MB and 2.5 MB | Download, see below |
| State and ZIP outlines, and the location of each address | No | 67 MB | The geography step downloads them |
| The model | No | 6.6 GB | `ollama pull qwen3.5:9b` |

### Shortest route: copy a built pack

If someone on the project already runs the tool, copy their whole `agent/pack/` folder into yours. You can then
skip the geography and pack steps and start the tool directly. The pack is kept out of Git because it holds
address-level coordinates, so pass it on privately and not through a public link.

The pack alone is enough to run the tool. The project's own table check is a separate matter: until the three
table folders below are in place, `uv run kolproject-evaluate` reports the 2026-11-19 and 2026-11-20 snapshots as
missing, and so does the one test that runs it (`tests/test_evaluate.py`).

### The three table folders

Copy `Data/processed/2026-11-11`, `Data/processed/2026-11-19` and `Data/processed/2026-11-20` from the project's
working folder into the same places in your clone. This is the only route that works for all three today. They
have no download address yet; ask whoever maintains the project.

Each folder also has a command that rebuilds it from its sources, but only the first can run in a fresh clone:

| Folder | Size | Rebuild command | What the rebuild needs |
|---|---|---|---|
| `2026-11-11` | 61 MB | `uv run kolproject-research-payments`, then `uv run kolproject-general-payments --skip-collect` | Only tables already in the repository. The first command downloads the seven Open Payments archives for 2019 to 2025 (6.6 GB) from `download.cms.gov/openpayments/`. This has not been re-run from an empty folder. |
| `2026-11-19` | 43 MB | `uv run kolproject-volume-billing` | Saved source files under `Data/raw/2026-11-19/`, which were collected by a one-off search and are not in Git, plus the Open Payments archives. |
| `2026-11-20` | 127 MB | `uv run kolproject-extensions` | Saved source responses under `Data/raw/2026-11-20/` and `Data/interim/`, which are not in Git. They include the CareSet DocGraph files, which CareSet supplies under its own licence. |

The reasons, the checks and the sources of each folder are in
[`Data/RESEARCH_PAYMENTS_README.md`](../Data/RESEARCH_PAYMENTS_README.md),
[`Data/GENERAL_PAYMENTS_README.md`](../Data/GENERAL_PAYMENTS_README.md),
[`Data/VOLUME_BILLING_README.md`](../Data/VOLUME_BILLING_README.md) and
[`Data/EXTENSIONS_README.md`](../Data/EXTENSIONS_README.md).

### The two address files

The geography step reads each hospital's and each clinician's practice address from two CMS files, which it
expects under `Data/raw/2026-10-24/bulk/`.

```bash
mkdir -p Data/raw/2026-10-24/bulk
curl -L -o Data/raw/2026-10-24/bulk/Hospital_Enrollments_2026.09.29.csv \
  https://data.cms.gov/sites/default/files/2026-10/961708ac-2136-43cb-9587-ac64af23b6ab/Hospital_Enrollments_2026.09.29.csv
```

The second file is `DAC_NationalDownloadableFile.csv`, the CMS Doctors and Clinicians national file (release of
2026-10-15, 900 MB). CMS replaces this file with each release, and the link to the October release no longer
answered on 2026-11-22. Either copy the file from the project's working folder, or download the current release
from the [dataset page](https://data.cms.gov/provider-data/dataset/mj5m-pzi6) and save it under the same name in
the same folder. The geography step has not been tested with a later release; a clinician whose practice address
changed would sit at the new address.

### What the geography step does

`uv run python -m kolproject.agent.geo` downloads two boundary files from the US Census Bureau (state outlines,
and ZIP Code Tabulation Areas, 67 MB) and sends each distinct address once to the free Census Geocoder. Only the
street line, city, state and ZIP are sent, under a running row number: no name, NPI (National Provider
Identifier) or hospital number leaves your machine. There are 4,422 distinct addresses, and the geocoder took
about 20 seconds when this was built. An address that does not match falls back to the middle of its ZIP area.

## Where the data comes from

Every table is built from public sources, except the shared-patient data, which is licensed. The folder column
says which snapshot under `Data/processed/` holds the tables.

| What the tool shows | Source | Fetched from | Folder |
|---|---|---|---|
| Clinicians, papers and co-authorship | Europe PMC (EMBL-EBI); NPI registry (CMS) | `ebi.ac.uk/europepmc/webservices/rest`; `npiregistry.cms.hhs.gov/api` | 2026-10-09, 2026-11-20 |
| Ties disclosed on papers | PubMed records (National Library of Medicine) | `eutils.ncbi.nlm.nih.gov` | 2026-11-20 |
| Industry payments, research funding and ownership interests, 2019 to 2025 | CMS Open Payments, publication of 2026-06-30 | `download.cms.gov/openpayments/` | 2026-11-11, 2026-11-19, 2026-11-20 |
| Medicare Part B services and payments, Part D prescribing, 2017 to 2024 | CMS Medicare Physician & Other Practitioners; Medicare Part D Prescribers | `data.cms.gov` | 2026-10-15, 2026-10-24, 2026-11-19, 2026-11-20 |
| Medicare hospital stays for CAR-T, 2021 to 2024 | CMS Medicare Inpatient Hospitals by Provider and Service | `data.cms.gov` | 2026-10-24, 2026-11-19 |
| National spending by CAR-T product, 2020 to 2024 | CMS Medicare Part B Spending by Drug; Medicaid Spending by Drug | `data.cms.gov` | 2026-11-20 |
| Medicaid paid amounts to clinicians, 2018 to 2024 | US Department of Health and Human Services, Medicaid Provider Spending | `opendata.hhs.gov/datasets/medicaid-provider-spending` | 2026-11-19 |
| 340B status of the centers | Health Resources and Services Administration, 340B registry | `340bopais.hrsa.gov/Reports` | 2026-11-19 |
| Accreditation of the centers | Foundation for the Accreditation of Cellular Therapy (FACT) | `accredited.factglobal.org` | 2026-10-24, 2026-11-19 |
| Shared patients between clinicians and centers, 2017 to 2022 | CareSet Systems DocGraph (CC BY-NC-SA 4.0 with a non-commercial addendum) | `careset.com/data-store/` | 2026-10-24, 2026-11-20 |
| Centers, division labels and addresses | CMS Doctors and Clinicians files; CMS Hospital Enrollments | `data.cms.gov/provider-data` | 2026-10-24 |
| CAR-T product approvals from 2017 | US Food and Drug Administration; each date's source is in [`reference/cart_products.json`](reference/cart_products.json) | `fda.gov` | in `agent/reference/` |
| Map outlines and locations | US Census Bureau boundary files and Geocoder | `www2.census.gov/geo/tiger/`; `geocoding.geo.census.gov` | built into `agent/pack/geo/` |
| Communities and their leaders | This project's community run | | in `docs/data/site.json` |

The complete source lists, with the address of every dataset, are in
[`Data/HCP_EXPANSION_SOURCES.md`](../Data/HCP_EXPANSION_SOURCES.md) for the folders up to 2026-10-24,
[`Data/manifests/2026-11-19/volume_billing_source_terms.csv`](../Data/manifests/2026-11-19/volume_billing_source_terms.csv)
and [`Data/manifests/2026-11-20/extension_sources.csv`](../Data/manifests/2026-11-20/extension_sources.csv).

## Ask a question

Type a question in the box at the top and press Ask, or click one of the example questions under the box.

- **The map** moves to the place, center, clinician or community in question. Points are centers and clinicians,
  coloured by community. The Links menu on the map chooses what the lines mean: co-authorship, shared patients
  (with a Year menu, 2017 to 2022) or research dollars. The legend names what is drawn. "Show everything" brings
  back the whole country.
- **The answer** below the map is a one-line headline and one or two sentences.
- **The panels** under it are charts, tables and fact boxes. Each one is drawn straight from the tables; the
  model chooses which to show but never writes the numbers in them.
- **The notes** at the bottom say what the figures can and cannot support. Read them with each answer.

Clicking a point on the map asks about that center or clinician. A follow-up such as "What about their
payments by year?" is read as being about what the last answer showed. If a name fits several people or places,
the tool asks which one you mean and offers the choices as buttons.

Questions that work, by topic:

| Topic | Example |
|---|---|
| A community (also called an orbit) | Show me the Moffitt orbit and who leads it. |
| | Compare the Mayo Clinic and Memorial Sloan Kettering orbits. |
| A clinician | Show me the profile of Frederick Locke. |
| | Who does Caron Jacobson co-author with most? |
| A center | Tell me about Moffitt Cancer Center. |
| | Which CAR-T centers are in Texas? |
| Industry payments | Which companies pay the MD Anderson community the most? |
| | Payments naming Yescarta to the Stanford orbit by year, with approval dates marked. |
| Ties disclosed on papers | What industry ties has Frederick Locke disclosed? |
| Prescribing and claims | Who are the top prescribers of ibrutinib in the Moffitt orbit? |
| Shared patients | Where does MD Anderson get its shared patients from, and how has that changed since 2017? |
| CAR-T products | When was Breyanzi approved, and for what? |
| What the tool holds | What can I ask? |

The tool says so when a question is outside its data. It does not hold commercial-insurance claims, patient
outcomes or conference activity. It does not write or run code, and it does not show how the communities were
validated.

## Limits to keep in mind

- **Shared patients are not CAR-T referrals.** They are Medicare fee-for-service patients seen by both
  clinicians for any condition.
- **Claims are Medicare and Medicaid only.** Physician and hospital claims do not say which CAR-T product was
  given; only industry payments and national spending are split by product.
- **Communities and their leaders are defined for the 500 core clinicians.** Other clinicians and centers are
  attached to the community they are most linked to.
- **The model's sentences can be wrong in wording.** A sentence containing a number that no table supports is
  never shown, but nothing checks the words around the numbers. The panels are the record.
- **Routing was tested on small question sets** written by the tool's author. [README.md](README.md) gives the
  measured results and how to read them.

## If something goes wrong

| What you see | What to do |
|---|---|
| "The pack is built from published tables that are not on this machine" | Copy the three table folders, see "Get the data". |
| "The geography step needs two CMS address files" | Download or copy the two files, see "The two address files". |
| "The data pack is missing. Build it first" | Run `uv run kolproject-agent-pack`. |
| `kolproject-evaluate` fails with `volume_billing_snapshot_missing, extensions_snapshot_missing` | The three table folders are not in place, see "Get the data". |
| "The model server is not answering" | Start Ollama, then run `ollama pull qwen3.5:9b` if the model is not yet downloaded. |
| The port is already in use | Start the tool on another port: `uv run kolproject-agent-serve --port 8795`. |
| The page has no map | The browser could not load MapLibre GL from cdnjs; check the internet connection and reload. |
| The map has no points | The pack was built before the geography step. Run the geography step, then the pack step again. |

## Checks

```bash
uv run pytest tests/test_agent_*.py          # the tool's tests; they need no model and no data pack
uv run kolproject-agent-pack --check         # re-run the pack's own checks on the built file
uv run kolproject-agent-eval --check         # the test questions name only skills, queries and ids that exist (needs the pack)
uv run kolproject-agent-eval                 # ask the 106 working questions with the model and score them, about 17 minutes
uv run kolproject-evaluate                   # the project's check of every published table (needs the three table folders)
```

In a clone without the three table folders, `uv run pytest` runs 582 tests, skips 28 that need the tables or the
pack, and fails one: `tests/test_evaluate.py::test_published_tables_pass_without_source_caches`, for the reason
given under "Shortest route". With the folders in place no test fails: 606 pass and 5 that need development
files are skipped.

## Terms of use

The tool and its pack are for academic, non-commercial use. The shared-patient tables derive from CareSet
Systems DocGraph and carry its licence (CC BY-NC-SA 4.0 with a non-commercial addendum); commercial use would
need a licence from CareSet. Content from the other sources keeps each source's own terms, which are recorded in
`Data/manifests/2026-11-19/volume_billing_source_terms.csv`.
