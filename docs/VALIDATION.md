# Validation evidence

Updated: 2026-10-24

Initial Linux validation: 2026-10-23

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

## Owner-confirmed repository checkpoint — 2026-10-24

The owner reports that all commands in APPLY_AND_REVIEW.md completed without
issues in the new Windows/Python 3.11 environment, including regression and the
offline walkthrough. Exact Windows test totals were not supplied here.

Fresh root commit: `6586c3e`. The owner confirmed a clean local branch synchronized
with origin/main after pushing to `IyadKhuder/slack-customer-score-copilot`.
The supplied Actions screenshot shows overall Success and four green matrix
jobs for this commit: Ubuntu/Windows with Python 3.11/3.12. Individual hosted
job logs and test totals were not inspected. The repository is now public.

## Synthetic Slack demonstration — 2026-10-24

The owner supplied the conversation transcript and an actual Slack screenshot
for `review account 1000008 as of 1991-12`. The bot acknowledged the request and
returned warning, analyst-referral and committee-review indications, complete
required baseline coverage, and next step `committee review`.

The response explicitly stated that live LLM notes were disabled and no LLM
was called. This verifies the observed Slack request/response round trip over
the replacement synthetic database. It does not measure live-provider quality
or validate generated notes. See the [screenshot](images/slack_synthetic_review.png).

The owner also reran the terminal scan (one attention-required result) and v2
scripted benchmark (6/6 passes, including CF-004's correct block). PyCharm's
Python Console was confirmed to use the renamed project's Python 3.11.9 venv.
Historical live-provider results do not apply to this checkpoint.

## Local expectations and limits

Expect 1,335 passes when every platform feature is available. Windows may
instead report **1,334 passed, 1 skipped** if symlink creation is unavailable;
the skip reason must identify that platform permission. Investigate other
failures or skips rather than suppressing them.

CI retains the Windows/Linux × Python 3.11/3.12 matrix and now builds verified
synthetic data, runs regression, replays/scores v2 and checks experimental
corpus import. The owner-confirmed hosted run for the new repository passed as recorded above.

Dependency installation used the network. Regression and scripted evaluation
used fake/scripted providers and no live API/Slack calls. No provider quality,
served-model identity, performance throughput or real-world risk calibration
is measured here. Historical live-provider results are excluded.

SQLite binary hashes are local integrity references and can differ across
SQLite builds. Default CSV SHA-256 remains:

`92059166ee2927f1bc1f35f64ae0ae009abaef864a93c8185cba8e50f1ef9cc4`

The machine-readable export manifest records the original migration export at
commit 6586c3e; it is a historical snapshot, not a current-tree checksum index.
Code edits after validation require rerunning relevant checks.
