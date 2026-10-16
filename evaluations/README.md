# Evaluation and release checks

`uv run kolproject stage=validate` checks 500 rows, unique NPIs and stable IDs,
active qualifying US registry records, two independently indexed works per person,
publication and payment joins, duplicate authorship and payment rows, fuzzy-review
resolution, date syntax, holdout separation, PageRank mass and payment reconciliation.

`uv run pytest` tests identity rules on controlled fixtures, including wrong middle
initials, duplicated author entries, secondary practice locations versus mailing
addresses, same-NPI aliases and ambiguous identical names with different NPIs.

Hydra writes each run's configuration to `runs`. `validation_report.json` is the
release gate; these checks do not establish clinical effectiveness or causal
marketing influence. The legacy guideline comparison is not a prospective test.
