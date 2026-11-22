---
name: scope-and-help
use_when: The user asks what the tool can do, asks something outside the database, asks how the data or communities were built or validated, or the question fits no other skill.
default_edges: none
caveats: []
---
# Scope and help

- "What can I ask?": run `data_inventory()`, which draws the table, and give four example questions in the answer.
- Outside the database (commercial claims, patient records, sales or liaison visit logs, conference
  presentations, clinical advice, product comparisons, forecasts): say in one sentence that it is not in the database, then name the nearest thing that is.
- Methods, validation, stability, backtesting: give the one fixed sentence from your instructions and nothing more.
- Requests to run code, export raw files, or compute models: say the tool shows views and simple totals only.
- Leave the map as it is: do not call `set_map`, and do not look anything up.
