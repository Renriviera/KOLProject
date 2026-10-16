# Collection and preprocessing

Run from the project root with `uv run kolproject stage=all`, or `stage=rebuild`
for an offline rebuild from the delivered snapshot. These commands require the
inputs excluded from GitHub; restore the complete `Data/` directory and populated
`configs/affiliation_evidence.csv` and `configs/identity_review.csv` from the
complete local project/archive first.
Implementation is in
`src/kolproject/screen.py`, `identity.py`, `deduplicate.py`, `rescreen.py`,
`payments.py`, and `build.py`.

Each HTTP response is cached with its URL, exact UTC retrieval time and SHA-256.
Collection is resumable. The frozen publication corpus retains its original
date-only observation date. Raw responses are not modified by preprocessing.
`prefetch_payments.py` is an optional helper to overlap independent CMS collection
with NPI screening; the normal pipeline does not require it.
