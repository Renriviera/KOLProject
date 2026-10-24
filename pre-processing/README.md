# Collection and preprocessing

Run from the project root with `uv run kolproject stage=all`, or `stage=rebuild`
for an offline rebuild from the delivered snapshot. These commands require the
source inputs excluded from GitHub; restore `Data/raw/`, `Data/interim/`,
`Data/legacy/` from a local backup when using a fresh clone. The consolidated local
project already contains these inputs. Populated `affiliation_evidence.csv` and
`identity_review.csv` live in ignored `configs/local/` and take precedence over
the tracked configuration templates.
Implementation is in
`src/kolproject/screen.py`, `identity.py`, `deduplicate.py`, `rescreen.py`,
`payments.py`, and `build.py`.

Each HTTP response is cached with its URL, exact UTC retrieval time and SHA-256.
Collection is resumable. The frozen publication corpus retains its original
date-only observation date. Raw responses are not modified by preprocessing.
`prefetch_payments.py` is an optional helper to overlap independent CMS collection
with NPI screening; the normal pipeline does not require it.

The processed tables are included in GitHub. To produce a modeling input without
the source caches, run `uv run python pre-processing/join_utilization.py`.
This writes a 500-row descriptive join to `models/inputs/`, checks one-to-one
NPI/HCP-ID cardinality, and leaves the original tables unchanged.
