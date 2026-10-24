# Candidate validation

Date: 2026-10-23

## Completed checks

Fresh Linux / CPython 3.12.14 virtual environment, installed from the unchanged
`requirements-dev.txt` and `constraints.txt`:

- `pip check`: no broken requirements.
- Verified default input regeneration and database build: 72 accounts, 1,380 rows.
- Full applicable regression: **1,335 passed, 169 subtests passed**, no skips on
  this Linux runner. This includes all inherited test definitions plus 64 new
  tests for data generation, database loading, v2 fixtures and setup hardening.
- Ordinary integrated scripted benchmark: **6/6 passed**, five publications and
  the correct CF-004 block; deterministic scoring confirmed all checks.
- Optional provider/Slack SDK imports succeeded without connecting.
- The separate fixture-safety and real-database integration helpers also each
  passed their 10 checks against the integrated candidate.
- Experimental v1.1 import succeeded from its distinct tracked corpus in a
  separate process. Baseline validation uses v1.0.
- Source/fixture review: all 56 inherited test files retained; critical controls
  and their tests remain. No matching four-score literal candidate remains in
  the private comparison screen described in MIGRATION_REVIEW.md.

The earlier generator/database/fixture increments were also confirmed by the
owner on Windows/Python 3.11. This newly integrated candidate still needs its
own local Windows run; previous green checkpoints do not substitute for it.

## Local expectations and limits

Expect 1,335 passes when every platform feature is available. Windows may
instead report **1,334 passed, 1 skipped** if symlink creation is unavailable;
the skip reason must identify that platform permission. Investigate other
failures or skips rather than suppressing them.

CI retains the Windows/Linux × Python 3.11/3.12 matrix and now builds verified
synthetic data, runs regression, replays/scores v2 and checks experimental
corpus import. These hosted jobs have not yet run for the new repository.
No new remote exists and nothing has been pushed.

Dependency installation used the network. Regression and scripted evaluation
used fake/scripted providers and no live API/Slack calls. No provider quality,
served-model identity, performance throughput or real-world risk calibration
is measured here. Historical live-provider results are excluded.

SQLite binary hashes are local integrity references and can differ across
SQLite builds. Default CSV SHA-256 remains:

`92059166ee2927f1bc1f35f64ae0ae009abaef864a93c8185cba8e50f1ef9cc4`

The machine-readable export manifest records candidate file hashes and review
provenance. Code edits after validation require rerunning relevant checks.
