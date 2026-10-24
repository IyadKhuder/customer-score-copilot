# Launching the Slack bot: scans and live LLM notes

Updated: 2026-10-24

Both modes connect to Slack and scan the local synthetic database. The difference
is whether an eligible review may request an LLM-generated note.

## Before starting

Run from the project root in PowerShell, after completing the README setup and
building `data/scores.db`. The Slack app must already be configured for Socket
Mode and app mentions, with the bot present in the allowed channel.

Your ignored local `.env` file must contain:

```text
SLACK_BOT_TOKEN=your_bot_token
SLACK_APP_TOKEN=your_app_token
SLACK_ALLOWED_CHANNEL_ID=your_channel_id
```

These are placeholders. Never commit or display real tokens in a recording.
For live notes, also configure `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` for the
chosen provider. Package dependencies must already be installed.

Select the project's Python and baseline policy version:

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:POLICY_VERSION = "v1.0"
```

Explicitly calling this Python works without activating the venv. Repeat these
variable assignments in a new terminal session.

## Mode 1: local scans without LLM calls

```powershell
& $py -m scripts.run_slack_review_83
```

Expect a startup message containing:

```text
local scans only; live notes disabled
```

The bot connects to Slack and runs local database scans. No LLM is called.
This mode still requires an internet connection to Slack.

## Mode 2: local scans plus live LLM notes

For Gemini:

```powershell
& $py -m scripts.run_slack_review_83 `
    --confirm-live `
    --provider gemini `
    --daily-remaining 8 `
    --max-requests 8 `
    --per-review-max-requests 8 `
    --interval-seconds 5
```

PowerShell backticks continue the command onto the next line. Do not put spaces
after a backtick. The same command on one line is:

```powershell
& $py -m scripts.run_slack_review_83 --confirm-live --provider gemini --daily-remaining 8 --max-requests 8 --per-review-max-requests 8 --interval-seconds 5
```

For Anthropic, replace `--provider gemini` with `--provider anthropic` and
configure `ANTHROPIC_API_KEY` instead.

The selected model and adapter settings come from `src/config.py`. A valid API
key alone does not establish model availability or sufficient provider quota.
Startup checks configuration; it does not prove a successful provider request.
Live calls may incur provider charges.

Expect a startup message containing `live notes enabled`, the provider name,
and the session request allowance.

### Request controls

| Option | Meaning |
| --- | --- |
| `--confirm-live` | Explicitly enables live LLM notes. |
| `--provider gemini` | Selects Gemini; `anthropic` is the other supported choice. |
| `--daily-remaining 8` | Your declared remaining request allowance; the application does not query the provider's daily quota. |
| `--max-requests 8` | Maximum provider requests during this running bot session. |
| `--per-review-max-requests 8` | Maximum provider requests for one review. |
| `--interval-seconds 5` | Minimum spacing between provider requests. |

The effective session allowance cannot exceed the declared remaining allowance
or the session cap. One review can require several provider requests because
of tool calling. These limits control request counts, not token costs or money.

Restarting creates a new session allowance. Keep track of earlier usage yourself;
do not assume restarting restores provider quota. Use a declared allowance no
larger than the remaining budget you intend to permit.

## Send a review request in Slack

Keep the terminal running. In the configured channel, mention the actual bot
and send:

```text
@Score Copilot review account 1000008 as of 1991-12
```

Select the bot from Slack's mention suggestions. If you named your bot
differently, mention that bot instead. Each request is independent.

In both modes, the local scan runs first. In live mode, only an
`attention_required` result proceeds to an LLM note, with allowance available.
A generated note is shown only when the verification and publication checks
pass. A blocked or failed note can leave the completed scan result available,
with a note-unavailable message. Enabling live mode does not guarantee publication.

For the planted synthetic account above, the verified scan demonstration showed
complete baseline coverage, warning and analyst-referral indications, and a
committee-review next step. This is a scoped policy demonstration, not an
authorized credit decision or causal explanation.

## Stop or switch modes

1. Press **Ctrl+C** in the running bot terminal.
2. Wait for the process to exit and the PowerShell prompt to return.
3. Start the other mode with its command above.
4. Send a new Slack request.

Use one listener process for this bot during the demonstration. There is no
in-chat mode toggle. When the local listener is stopped, it cannot handle requests.

## Results, troubleshooting and demonstration labels

- Missing Slack configuration: check the three Slack settings in the local `.env`.
- Missing provider configuration: check the selected provider's API key and settings.
- No Slack response: confirm the listener is running, the bot is mentioned, and
  the message is in the configured allowed channel.
- No generated note: read the reason shown in Slack; it can reflect scan scope,
  request allowance, provider failure or verification/publication checks.
- Live sessions create local request logs under `results/slack/<session-id>/`.
  Keep raw logs out of screenshots and public commits; inspect them locally
  before sharing any diagnostic information.

For a recording, label Mode 1 **"Local database scan; no LLM call"**.
Label a successful live-note demonstration **"Local scan plus verified
LLM-generated note"** only when that note actually passes the gate and appears.
Do not describe a blocked note as a published explanation.

The synthetic Slack scan round trip was demonstrated on 2026-10-24. That result
does not establish live-provider note quality on the replacement dataset.

See [the README](../README.md), [the demonstration guide](PORTFOLIO_DEMO.md), and
[validation evidence](VALIDATION.md) for setup and verification context.
