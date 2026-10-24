# Apply and review the first integrated candidate

Date: 2026-10-23

This package is the new project candidate, not a patch to apply over the old
project. It includes the reviewed runtime, tests, synthetic input bundle,
v2 benchmark, separate policies, new documentation and CI. It has no Git history.

## 1. Extract into a new folder

Save `scorecopilot_synthetic_candidate_v1.zip` to Downloads. In PowerShell:

```powershell
$candidate = "C:\Users\iyad\projects\scorecopilot-synthetic"
if (Test-Path -LiteralPath $candidate) {
    throw "Choose a new candidate folder; this folder already exists."
}
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\scorecopilot_synthetic_candidate_v1.zip" -DestinationPath $candidate
Set-Location -LiteralPath $candidate
```

Keep the existing `scorecopilot` project, repository, virtual environment and
credentials intact. Do not copy its Git directory, ignored outputs or data
into this folder. You may open the new folder as a separate PyCharm project.

## 2. Create and validate a fresh environment

```powershell
py -3.11 -m venv .venv
$py = ".\.venv\Scripts\python.exe"
$env:POLICY_VERSION = "v1.0"
& $py -m pip install -r requirements-dev.txt
& $py -m pip check
& $py -m src.data_loader
& $py -m pytest -q -rs
```

No activation or admin rights are needed. Explicitly invoking the new Python
avoids using an older active environment. Install output will be longer than
the earlier standard-library staging checks. Stop after a failure and share
the failing output before continuing.

Expected setup: **72 accounts / 1,380 rows**. Expected tests: **1,335 passed**,
or **1,334 passed and the one symlink-permission skip** on Windows. Subtests
should report 169 with the pinned plugin. Required data/validator checks no
longer skip when setup is missing. Investigate any extra skip or count change.

## 3. Verify the integrated walkthrough

```powershell
New-Item -ItemType Directory -Force generated | Out-Null
& $py -m scripts.scan_batch_712 --accounts 1000008 --as-of 1991-12-01 --output generated\scan.json
& $py -m scripts.demo_benchmark_62 --output generated\scripted_v2.json
& $py -m scripts.score_benchmark_63 --input generated\scripted_v2.json --output generated\scores_v2.json
```

Expect a completed scan with one attention-required result, and **six benchmark
passes**, including CF-004's block. Existing report filenames are refused;
choose new names when repeating the commands.

These are the normal application entry points with real retrieval-module
imports and real verification/composition code; provider/retrieval responses
inside the scripted replay remain isolated fixtures. They do not call an LLM,
send Slack messages or measure TF-IDF ranking. The scanner separately exercises
the generated SQLite database.

## 4. Review before fresh history

Read README.md and docs/MIGRATION_REVIEW.md. Inspect the source/test changes and
EXPORT_MANIFEST.json. The default CSV/sidecars are independently generated;
the old data, reports, images, Git metadata and scratch files are excluded.

Do not initialize Git, create a remote, push or publish yet. Return the local
setup/test summary and walkthrough result. After that concrete review, the
next step is the explicit fresh-history/staged-content/new-remote procedure,
with local Git operations remaining yours.
