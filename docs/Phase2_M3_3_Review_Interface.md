# M3.3 — review interface integration

8 November 2026 · base `d297091` · branch `phase2/persistent-review-workflow`.

Prepared implementation; owner-side Windows, Docker, visual interaction and pushed CI remain completion gates. No hours inferred. The current Phase2_Blueprint.md (Library version 3, updated 2 November) and M3.2 implementation were reviewed before design.

## Architecture checkpoint

The browser is a transport adapter. It reads the saved package, displays frozen evidence and submits an explicit decision to the existing atomic authority. It does not calculate eligibility or override verification. `review.ts` owns form rendering, validation and submission locking; `app.ts` owns authentication, transport, navigation and fresh projections. The six fingerprinted implementation files and the Slack implementation retain their bytes. No API schema or dependency changes are needed.

A review binds `expected_revision` and `package_hash` to the detail response rendered on screen, never a newly fetched version hidden from the analyst. The server supplies reviewer identity and rechecks provenance/verification atomically. Frontend eligibility is only a usability gate. A held, blocked, incomplete, queued, running, failed or completed case has explanatory text instead of an active form. Missing/invalid package hashes also prevent review.

Use the select control to choose Approve package or Reject package, enter a meaningful reason (1–1000 characters after trimming) and click Record decision. The payload contains only revision, package hash, decision and reason. Both client and API validate the reason. Approval accepts only this demonstration package; it authorizes no credit action. Live notes remain disabled. This module adds no case-creation form, live-provider execution, notification, Azure deployment or Slack-to-web bridge.

## Behavior checkpoint

While submitting, the form locks against repeated clicks. Success renders the returned saved projection, closes review and refreshes the queue with its existing filter. Thus a completed case disappears from an awaiting_review queue while its selected detail stays visible. Status fields remain distinct. The audit reason/reviewer remain server-side; they are not added to the public projection.

HTTP 409 explains stale/conflicting state and requires Refresh case. Any other mutation failure also leaves the form locked: a transport failure can occur after the server committed. Refresh reads the saved truth; there is no automatic POST retry and no automatic replay of the reason or choice. Read retries remain explicit. A 401 disconnects and clears private UI. Case refresh/selection and disconnect abort requests and prevent late results restoring an old case/session. Aborting a request cannot undo a server decision; read the case again to reconcile it.

Selection and reason are deliberately discarded by refresh/navigation/disconnect. The token remains only in browser memory. No local/session storage, URL tokens, third-party scripts, inline scripts, cookies or raw server errors were introduced. Policy text remains safely rendered as text.

## Verification checkpoint

Workspace Linux Python 3.12: **1,681 passed, 169 subtests passed**, one existing Starlette/AnyIO deprecation warning. Fresh checkout first required `python -m src.data_loader`; the initial run's six missing-table failures cleared after building the verified synthetic database. This setup command was workspace-only; do not rebuild the owner's working data unnecessarily.

Strict TypeScript build and **18 frontend tests passed on Node 18.16.1** and workspace Node 24.22.1. Checks cover approve/reject payloads, required reason, bounds, explicit selection, duplicate prevention, held/incomplete/closed states, conflict/transport-error lockout, saved status/queue updates and late decision isolation after disconnect. linkedom's select setter is absent, so tests select option nodes through their selected property; production uses ordinary browser controls.

Full offline Python regression includes the existing Slack families, API decision/provenance and unchanged fingerprint tests. No live Slack/provider connection was made. A real local Uvicorn HTTP check exercises served review assets, approve/reject, reason validation, stale revision/hash, duplicate decisions and held rejection. CI's container job now runs the same smoke script; six jobs are still expected. Compose commands use `--wait` alone.

Docker is unavailable in this workspace. Actual rendered browser interaction and Windows/Docker/restart/pushed CI checks remain owner verification. DOM tests do not establish visual appearance. M3.3 remains In progress until those gates pass.

## Apply on Windows

Extract the delivered ZIP into a folder outside the repository. It contains `M3_3_review.patch` and this guide. Run PowerShell from the existing repository with the virtual environment active:

```powershell
Set-Location 'C:\Users\iyad\projects\slack-customer-score-copilot'
git branch --show-current
git rev-parse --short HEAD
git status --short
```

Expect the requested branch, `d297091`, and a clean tree. If different or locally modified, stop before applying and reconcile. Set the actual extracted patch path:

```powershell
$patch = "$env:USERPROFILE\Downloads\M3_3_review\M3_3_review.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch apply failed' }
git diff --check
git diff --stat
Push-Location frontend
try {
    npm ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    npm test
    if ($LASTEXITCODE -ne 0) { throw 'Frontend verification failed' }
} finally { Pop-Location }
$env:POLICY_VERSION = 'v1.0'
.\.venv\Scripts\python.exe -m pytest -q -rs
if ($LASTEXITCODE -ne 0) { throw 'Python regression failed' }
```

Reuse the existing local token without printing or saving it:

```powershell
if (-not $env:COPILOT_API_TOKEN) {
    $secret = Read-Host 'Existing local API token' -AsSecureString
    $env:COPILOT_API_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    Remove-Variable secret
}
docker compose up --build --detach --wait
if ($LASTEXITCODE -ne 0) { throw 'Container startup failed' }
.\.venv\Scripts\python.exe -m scripts.smoke_review_phase2
if ($LASTEXITCODE -ne 0) { throw 'Review HTTP smoke failed' }
.\.venv\Scripts\python.exe -m scripts.smoke_ui_phase2
if ($LASTEXITCODE -ne 0) { throw 'Evidence HTTP smoke failed' }
Start-Process 'http://127.0.0.1:8000/'
```

The review smoke creates three synthetic cases and records two decisions (approve and reject). The existing UI smoke creates three further undecided demonstration cases for visual checks. Neither uses an LLM. Existing cases and the named volume are preserved.

## Browser walkthrough and persistence

1. Connect; open the newly created pending 1000003 case. Inspect evidence and hash. Approve/reject selection alone must not enable submission; whitespace-only reason must not enable it. Enter a meaningful reason.
2. Record an approval. Confirm completed/approved in detail and refreshed queue, same package hash and no remaining form. Repeat with a fresh pending case to demonstrate rejection.
3. For a conflict, open the same pending case in two tabs with the token entered separately. Decide in tab A. Submit tab B's previously loaded form: it must show the conflict and lock. Refresh case in B: display the saved decision, with no new form. A second tab is sufficient; no artificial database editing is required.
4. Filter Held; inspect 999999999. Evidence limitations remain visible and no review form is offered. Also inspect queued/running if visible; refresh after completion.
5. For uncertain outcome behavior, load a pending form, stop the container in PowerShell, then submit in the browser. The error must lock review and require Refresh case. Restart, refresh and inspect status before deciding again. A stopped-server request is expected to remain pending; a real interrupted request can have committed.
6. Check keyboard labels/focus, narrow window layout, loading/error notices and Disconnect clearing the evidence. Reload must request the token again. Navigating or disconnecting during a pending POST cannot cancel an already saved decision.
7. Record one completed case ID. Restart without deleting its named volume:

```powershell
docker compose down
docker compose up --detach --wait
if ($LASTEXITCODE -ne 0) { throw 'Container restart failed' }
```

Reconnect and reopen that exact case; confirm its decision and package persist. Never use `down -v`. If needed inspect `docker compose logs --no-color`. Keep provider/live notes disabled.

## Stage, commit and push after verification

```powershell
git add -- .github/workflows/tests.yml frontend/index.html frontend/package.json frontend/src/app.ts frontend/src/review.ts frontend/style.css frontend/test/controller.test.mjs frontend/test/review.test.mjs scripts/smoke_review_phase2.py docs/Phase2_M3_3_Review_Interface.md
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged whitespace check failed' }
git diff --cached --stat
git status --short
git commit -m "Add version-bound browser analyst review integration"
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
git push origin phase2/persistent-review-workflow
if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
git log -1 --oneline
git status --short
```

Report the resulting commit and six CI job results plus browser/Docker outcome. Then reconcile M3.3 to Done using the actual commit; log only explicitly supplied hours. Next approved module: M3.4 Azure hosting, with subscription/spend/region/persistence decisions before deployment. Assess a small Slack-to-web bridge afterward; implementation has not been authorized.
