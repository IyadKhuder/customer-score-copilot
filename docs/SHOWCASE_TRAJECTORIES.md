# Two contrasting score trajectories

Added 2026-10-25. These independently invented profiles illustrate score shapes,
not causes, formal recovery eligibility or operational warning clearance.

The optional `showcase` generator profile contains 78 accounts / 1,500 monthly
observations across three markets. It preserves every observation and metadata
entry for the original 72 accounts and adds two fixed profiles per market.
The default `small` profile remains 72 accounts / 1,380 observations, and the
existing `large` profile is unchanged. Only the showcase profile assigns these
special shapes to ordinals 25 and 26; account IDs are dataset-specific.

| Month in 1991 | 1000025: sharp drop, gradual recovery | 1000026: sharp rise, gradual decline |
| --- | ---: | ---: |
| January | 346 | 220 |
| February | 351 | 224 |
| March | 347 | 219 |
| April | 350 | 222 |
| May | 220 | 352 |
| June | 229 | 343 |
| July | 241 | 331 |
| August | 238 | 334 |
| September | 256 | 316 |
| October | 273 | 299 |
| November | 291 | 281 |
| December | 309 | 263 |

The small setbacks in August make recovery/decline uneven. Earlier months in
established markets are padded with the initial value. These fixed 12-month
tails move with a configured calendar's end. The showcase profile requires at
least 26 accounts per market and 12 new-market months. The seed still controls
background accounts; these two authored shapes intentionally remain fixed.

## Generate, verify and try in Slack

From the project root in PowerShell, after applying the patch and passing tests:

```powershell
$py = '.\.venv\Scripts\python.exe'
$env:POLICY_VERSION = 'v1.0'
& $py -m src.synthetic_dataset --profile showcase --output-dir generated\showcase
& $py -m src.synthetic_database --dataset-dir generated\showcase --output-dir generated\showcase-db
```

Generation and verified database loading make no provider calls. Both output
directories must be NEW; choose another name if they exist. Exact regeneration
checks all three input files before loading. No binary database is committed.

Stop the running listener with Ctrl+C BEFORE replacing its database. To select
the verified showcase database for the existing listener:

```powershell
Copy-Item generated\showcase-db\scores.db data\scores.db -Force
Copy-Item generated\showcase-db\database_receipt.json data\database_receipt.json -Force
& $py -m scripts.run_slack_review_83
```

This deliberately replaces only the local generated database and receipt; the
committed default CSV and sidecars stay in place. The active local database now
has 78 accounts / 1,500 rows; its verified inputs and receipt are retained under
`generated\showcase*`. The launch above is scans-only: no LLM requests.

Try in the configured channel:

```text
@Score Copilot review account 1000025 as of 1991-12
@Score Copilot review account 1000026 as of 1991-12
```

For a before/after assessment, also review `1000025` as of `1991-05`.
Use your usual explicit live-provider options only if you want optional notes;
provider allowances and all verification gates continue to apply.

To return to the default database, stop the listener and run:

```powershell
& $py -m src.data_loader
```

This rebuilds the verified 72-account database from the unchanged default
inputs. Restart the listener afterwards. The two additional IDs then receive
account-not-found responses, as expected.

## Tested outcomes with baseline v1.0

| Review | Observed local result | Next step |
| --- | --- | --- |
| 1000025, May 1991 | Warning plus analyst and committee indications | Committee review |
| 1000025, December 1991 | No selected warning/escalation signal in scope | No follow-up indicated by this scan |
| 1000026, December 1991 | Warning signal triggered | Analyst review of the warning |

No selected signal is not comprehensive clearance, reinstatement eligibility
or confirmation of warning removal. Large upward movements can also trigger
volatility-related warnings: direction alone does not determine the outcome.

The public summary uses existing six-observation descriptions. Both December
reviews may be called "mixed" because of the August reversal, while their
12-month charts clearly show the larger trajectory. Scenario names are
construction metadata, not privileged evidence supplied to the LLM.

Tests cover unchanged existing accounts, deterministic regeneration, database
verification, calendar shifts, actual policy outcomes, real chart rendering and
exclusion of later data from an earlier assessment. No policy thresholds,
publication gates, chart windows or request controls were changed.
