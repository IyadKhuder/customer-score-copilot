# M3.2 — case queue and evidence view

Date: 7 November 2026. Base: `e27561f`, branch `phase2/persistent-review-workflow`.

Implementation prepared and tested in the workspace; owner-side Windows, visual browser, Docker and pushed CI verification remain completion gates. No actual hours are inferred.

## Architecture and learning goals

A browser UI is a transport adapter. It displays the application's saved projections; it does not calculate policy decisions, change verification, or approve a package. `GET /cases` supplies the queue, and `GET /cases/{id}` supplies pending or completed evidence. The approved-only `/evidence` route is intentionally not used for pending review.

The owner's Node 18.16.1/npm 9.5.1 can run the pinned TypeScript 5.9.3 compiler directly. A bundler, React and a separate development server are unnecessary for this modest read-only interface. `tsc` checks strict types and emits browser ES modules; `build.mjs` copies the HTML/CSS. Node is build/test tooling only. No Node process is required at application runtime. Node 18 is end-of-life; a supported Node release is recommended for future tooling. Current Vite requires newer Node, so it was not introduced just to serve a few modules.

| File | Responsibility |
| --- | --- |
| `frontend/src/model.ts` | Typed case projection and calendar gap/segment helpers |
| `frontend/src/view.ts` | Safe DOM rendering, SVG chart and frozen evidence details |
| `frontend/src/app.ts` | Runtime token, authenticated reads, pagination, refresh and request cancellation |
| `src/case_queue.py` | Parameterized, API-owned queue query; filter before pagination |
| `src/api.py` | Serve the shell/assets, security headers, authenticate case data and validate filters |
| `Dockerfile` | Build modules in a Node stage; copy only built assets into existing non-root Python runtime |

The added queue query deliberately lives outside `case_application.py`. The latter is included in the saved implementation fingerprint. Editing it merely for filtering would invalidate historical package decisions. All six M3.1 fingerprinted files retain their exact bytes; a regression check records their baseline SHA-256 values.

## API connection and authentication

Open `http://127.0.0.1:8000/`. Assets and API share one origin. Relative fetch URLs avoid CORS configuration, cross-origin token forwarding and environment-specific API addresses. Local development uses the same FastAPI server after `npm run build`; rebuild after editing TypeScript or static source files. There is no development proxy or hot reload.

The shell and compiled assets contain no private evidence and are accessible without a token. All case reads still require the existing bearer token. Enter the token configured in `COPILOT_API_TOKEN` into the password field at runtime. The browser keeps it only in a JavaScript closure; it is not compiled into the bundle, put into a URL, or written to local/session storage. The input is cleared after connection. Disconnect or reload clears the token and loaded evidence; a 401 disconnects. This remains the M3.1 single demo analyst identity, not per-user authentication or a hosted security design. Same-origin script compromise could still access an in-memory token.

Requests omit cookies, refuse redirects, and use `cache: no-store`. Responses use `Cache-Control: no-store`, `nosniff`, `no-referrer` and a same-origin Content Security Policy with no inline or third-party scripts. Frozen policy text uses `textContent`, never HTML/Markdown execution. Disconnect and case selection abort pending reads; generation checks prevent late results from restoring cleared evidence. Authentication is enforced by the API, not by hiding controls.

## Queue and evidence behavior

The workflow filter uses the actual saved `workflow_status`. `GET /cases` accepts optional validated `workflow_status` and adds `has_more`; existing limit/offset/items remain. Filtering precedes SQL pagination. No private event/reviewer audit fields are added. Queue reads are current snapshots, not an atomic multi-page export; new submissions can shift offset pages. Refresh is explicit, with separate queue and selected-case buttons. There is no automatic polling or automatic decision retry.

The detail view separates workflow status, verification status and analyst decision. `passed` refers to deterministic completion, not evidence completeness or an approved LLM note. It shows scan outcome, reason codes, next step, baseline coverage, missing baseline months, all captured observations, frozen policy filenames/text/hashes, snapshot/package hashes, provenance and the saved deterministic policy result.

The trajectory uses the frozen rows. Calendar position reflects elapsed months. Lines connect consecutive months only; gaps are not zero-filled or interpolated. The fixed 0–1000 score axis follows API history validation, rather than assuming that all data will match the default fixture's 0–449 range. Invalid scores are omitted from the chart with a warning and remain inspectable in the observation table. Gaps within the first/last captured observation are labelled separately from the evaluator's policy baseline gaps. No captured observations means no chart; unavailable coverage is not labelled complete.

The interface has loading, empty, safe error and retry states, keyboard-operable controls, an accessible chart label and numeric observation table, and layouts for laptop/narrow widths. Automated DOM checks do not establish visual quality in an actual browser; use the walkthrough below. Decision controls, reason entry, create-case form and polished review interactions remain M3.3. Live LLM notes remain disabled. No credit action, notification or publication occurs.

## Workspace validation

Linux Python 3.12: full offline suite **1,681 passed, 169 subtests passed**. One third-party Starlette/AnyIO deprecation warning; no test failures. The Linux run includes symlink coverage unavailable in the owner's previous Windows baseline.

Four frontend tests cover missing-month segmentation, safe policy text rendering, separate statuses, empty evidence, bearer header handling, no-store/redirect settings, disconnect, 401 and late-response isolation. Strict TypeScript compilation and all four tests also passed under **Node 18.16.1**, matching the owner's Node version. The primary workspace Node version is 24.22.1. npm dependency audit reported zero vulnerabilities at preparation time; this is time-specific, not a security guarantee.

Focused API checks cover filters before pagination, has-more, invalid filters, authentication, static assets/security headers and unchanged M3.1 implementation fingerprints. A real Uvicorn/HTTP smoke test covers served assets, authenticated reads and three deterministic cases. Docker is unavailable in this workspace. The cloud browser cannot reach this workspace's local service, so rendered browser appearance/interaction remains owner verification. No live provider calls were made.

CI adds a frontend job alongside the four Python matrix jobs and the container job: **six jobs expected**. The frontend job installs from the lockfile and runs compilation/DOM tests. The container builds the frontend itself. Existing persistence smoke tests remain enabled.

## Windows verification

Run from the repository root with the existing Python virtual environment active. First install/build/test the frontend:

```powershell
Push-Location frontend
npm ci --ignore-scripts
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
npm test
if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed' }
Pop-Location
$env:POLICY_VERSION = 'v1.0'
.\.venv\Scripts\python.exe -m pytest -q -rs
if ($LASTEXITCODE -ne 0) { throw 'Python regression failed' }
```

No Python dependency changes are required from M3.1. `frontend/dist` and `node_modules` are ignored; commit source and the lockfile only.

Reuse your existing local API token. If it is absent in this PowerShell session, set it without printing or persisting it:

```powershell
if (-not $env:COPILOT_API_TOKEN) {
    $secret = Read-Host 'Existing local API token' -AsSecureString
    $env:COPILOT_API_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    Remove-Variable secret
}
docker compose up --build --detach --wait
if ($LASTEXITCODE -ne 0) { throw 'Container startup failed' }
.\.venv\Scripts\python.exe -m scripts.smoke_ui_phase2
if ($LASTEXITCODE -ne 0) { throw 'UI HTTP smoke failed' }
Start-Process 'http://127.0.0.1:8000/'
```

The smoke command creates three new offline demonstration cases each time: declining account 1000003, missing-month account 1000010, and nonexistent account 999999999. It prints case IDs/statuses, makes no decisions and invokes no LLM. Your preserved volume's existing cases remain available. The Docker command uses `--wait` alone, compatible with your Compose v2.15.1.

In the browser:

1. Enter the same token. Confirm the case queue appears; a wrong token must not reveal cases.
2. Open declining account 1000003. Inspect distinct status fields, trajectory, numeric observations, policy text and hashes, limitations and deterministic result.
3. Open 1000010. Expand observations and confirm a missing month is not bridged by a chart line. Compare history gaps with baseline gaps.
4. Filter Held and inspect nonexistent account 999999999: absent observations, unavailable coverage or incomplete analysis, reason codes and limitations must remain explicit.
5. Refresh the queue and the selected case. Narrow the window; use Tab/Enter to open controls and disclosures.
6. Disconnect: the loaded evidence disappears. Reload: the token must be requested again.
7. Stop/start the container without deleting its volume, reconnect and reopen a recorded case ID. No new case or analysis is required simply to read it.

```powershell
docker compose down
docker compose up --detach --wait
```

Do not use `down -v`. Optionally rerun the existing two-phase M3.1 persistence/decision demonstration if you want to reconfirm its full accepted-package flow; that script does make demo analyst decisions. It is separate from the read-only M3.2 smoke check.

If Docker startup fails, use `docker compose logs --no-color` to inspect it. If the page returns build instructions in a local Python-only run, build `frontend` first and restart the server. Never run both the container and a local Python server on port 8000. A local Python-only alternative is `python -m uvicorn src.api:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log` after setting the token and ensuring `data/scores.db` exists.

## Completion gate and next module

Mark M3.2 Done only after owner-side build/tests, browser walkthrough, Docker persistence check and pushed CI succeed. Record the owner's actual commit hash then. The shared tracker was updated to In progress; no hours were logged. M3.3 adds full review integration and version/hash-bound analyst controls, with stale-package and failure interactions; it must continue using the existing atomic decision authority.

Official references: [TypeScript compiler options](https://www.typescriptlang.org/tsconfig/), [FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/), [Node release lifecycle](https://nodejs.org/en/about/eol), [Vite Node requirements](https://vite.dev/guide/).
