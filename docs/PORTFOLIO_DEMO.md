# Synthetic demonstration capture guide

Date: 2026-10-24

Use the verified synthetic database and baseline POLICY_VERSION=v1.0. Do not
reuse screenshots or live results from the previous project.

## Terminal demonstration

From the project root, with the new environment activated:

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:POLICY_VERSION = "v1.0"
& $py -m scripts.scan_batch_712 --accounts 1000008 --as-of 1991-12-01 --output generated\portfolio_scan_01.json
& $py -m scripts.demo_benchmark_62 --output generated\portfolio_scripted_01.json
& $py -m scripts.score_benchmark_63 --input generated\portfolio_scripted_01.json --output generated\portfolio_scores_01.json
```

Choose new filenames if those reports exist. Capture the scan summary and the
six-case scoring summary. Label the benchmark as scripted/offline, not live-model
accuracy. Expected: six passes, including CF-004's correct evidence block.

## Slack demonstration without LLM calls

If the existing Slack app is already configured for Socket Mode/app mentions,
put its three Slack settings in the ignored local .env file. Do not copy the old
project's .env wholesale. Start:

```powershell
& $py -m scripts.run_slack_review_83
```

This connects to Slack. No live-provider options are needed. In the configured
channel, mention the bot and send `review account 1000008 as of 1991-12`.
Capture the request and response showing committee review and that live LLM
notes are disabled. Stop the listener with Ctrl+C.

Only capture a workspace/channel you are permitted to show. Crop unrelated
messages, names and workspace details. Never capture .env files or tokens.
Caption: "Synthetic account review through Slack; local scan, no LLM call."
Do not fabricate a Slack screenshot or describe a local formatter replay as a
Slack connection. If the app is unavailable, publish the terminal demonstration
first and leave Slack capture pending.

## Publication follow-up

Review screenshots before adding them under docs/images and linking them from
the README. Generated raw reports remain ignored. A license has not been selected
in this documentation patch; settle that separately before describing the project
as licensed open-source software.
