# Score charts in Slack

2026-10-24

Each supported review with observations in the last 12 calendar months can attach
one PNG chart in the same Slack thread, after the text response. This works in
both scans-only and live-note modes; rendering needs no LLM calls.

## Enable file uploads in your Slack app

1. Stop the bot with Ctrl+C.
2. Open your existing app in the [Slack app dashboard](https://api.slack.com/apps).
3. Open **OAuth & Permissions**. Under **Bot Token Scopes**, add `files:write` if
   absent. Keep the existing bot scopes and app-level Socket Mode settings.
4. Use **Reinstall to Workspace** (or the offered installation/authorization
   action), choose the intended workspace, and approve the added permission.
   Do not remove or recreate the app.
5. Check the bot token shown after authorization. If it changed, update only
   `SLACK_BOT_TOKEN` in the ignored local `.env`. Keep tokens out of screenshots.
6. Confirm the bot is a member of the configured allowed channel.

This implementation uses `files_upload_v2`, not deprecated `files.upload`.
It needs `files:write`; it does not add a need for `files:read`. Uploading a chart
involves Slack's upload-URL, byte-transfer and completion steps. SDK retry handlers
are disabled by the existing launcher; the listener does not retry an upload.
An upload may have succeeded even if its final acknowledgement was lost; do not
assume that an error means no file reached Slack.

## Install and launch

From the project root, after applying the patch:

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
& ".\.venv\Scripts\python.exe" -m pip check
$env:POLICY_VERSION = "v1.0"
& ".\.venv\Scripts\python.exe" -m scripts.run_slack_review_83
```

This adds pinned Matplotlib/chart dependencies to the existing environment. No
GUI or separate image-hosting service is needed. Installation uses the network.

In Slack, mention the bot:

```text
@Score Copilot review account 1000008 as of 1991-12
```

Expect the text review followed by a PNG in the same thread. First workspace
verification is manual; offline tests do not establish a successful real upload.

To run text-only, without rendering or file uploads:

```powershell
& ".\.venv\Scripts\python.exe" -m scripts.run_slack_review_83 --no-charts
```

The same `--no-charts` option can be appended to the live-note command documented
in SLACK_BOT_MODES.md. Live-provider request controls are otherwise unchanged.
Charts do not consume an LLM request allowance; they do use Slack network requests.

## What the chart means

- Exactly 12 calendar months ending at the requested review month; future scores
  are excluded. The text's six-month summary intentionally has a shorter window.
- Fixed 0–449 score scale for the verified synthetic demonstration dataset.
- Neutral line and markers; missing months are gaps with grey shading, never
  zero-filled or connected across missing observations.
- Account ID, review month and synthetic-data label; quality-review caption when
  the existing scan flags data quality. No invented causal or recovery conclusion.
- Available points remain visible for partial histories. An isolated observation
  is a point rather than an inferred trend.
- No chart for absent accounts, unsupported/failed scans, or an empty recent
  window. Off-scale or malformed chart values are rejected, not silently clipped.

The scan's existing read-only snapshot supplies the bounded chart projection.
There is no extra score tool, database query, public image URL or scenario-label
lookup. PNG bytes are rendered in memory and are not saved as chart files.
Matplotlib may create its ordinary font cache. Uploaded files persist in Slack
according to workspace retention; the application does not delete them.

Chart preparation/render/upload failure leaves the completed text review and
publication gate unchanged. A sanitized message reports chart unavailability.
The listener never reruns the scan or LLM note to repair a failed chart. Existing
event deduplication remains process-local and expires as documented in the listener;
restarting the process does not provide permanent deduplication.

## Useful checks

| Account / month | Check |
| --- | --- |
| 1000001 / 1991-12 | Broadly stable line |
| 1000003 / 1991-12 | Gradual decline |
| 1000007 / 1991-12 | Jump and reversal, with quality caption |
| 1000008 / 1991-12 | Severe drop |
| 1000009 / 1991-12 | Visible October gap |
| 1000010 / 1991-12 | Missing December point |
| 1000011 / 1991-12 | Only two observed points |
| 1208 / 1991-12 | Unknown-account message; no chart |

If text arrives without a chart, check dependency installation, the bot's granted
file-upload scope, channel membership and connectivity. A `--no-charts` launch
should always remain available for text-only operation.

## References

- [Slack Python SDK: uploading files into a thread](https://docs.slack.dev/tools/python-slack-sdk/web/)
- [Slack files:write scope](https://docs.slack.dev/reference/scopes/files.write/)
- [Slack upload-URL method](https://docs.slack.dev/reference/methods/files.getUploadURLExternal/)
- [Matplotlib rendering backends](https://matplotlib.org/stable/users/explain/figure/backends.html)
