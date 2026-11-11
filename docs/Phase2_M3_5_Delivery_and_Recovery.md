# M3.5 — release identity and bounded recovery

10 November 2026. Prepared against `8166b2c`, branch `phase2/persistent-review-workflow`.
Status: implementation prepared; owner-side Windows/Docker/hosted gates remain pending.
The 8h allocation is a plan, not actual logged time. Slack-to-web integration remains deferred.

## Architecture and contract

This checkpoint adds owner-run tools, not a new deployment service. FastAPI, its one
worker, SQLite/LangGraph persistence, authentication, the frontend and Slack keep
their existing behavior. The six implementation-fingerprinted files are unchanged.
No schema migration or provider call is introduced.

`release.py pack` records the Git revision label, exact application image ID,
exported archive SHA256 and six implementation hashes. `deploy` checks the archive,
loads the image, checks its label/platform/fingerprints and compares it to the
running application. It saves the previous application and Caddy image IDs before
replacing anything. A separate Compose override selects those exact local IDs and
forbids pulling. It preserves the root-only API environment file, database mount,
Caddy certificates, network and lifecycle policy. The existing VM must already be
running with healthy services. It never provisions infrastructure or starts Azure.

The source revision label records the owner's build assertion; it is not a signed
build attestation. Mutable Dockerfile bases remain a build reproducibility limit.
The exported image, not rebuilding a tag later, is the release artifact. Caddy is
pinned to the already loaded image ID during activation; this does not upgrade it
or invent the missing historical registry digest. No paid registry is required.

`submit_case.py` persists a synthetic request and UUID idempotency key before HTTP.
It makes at most three attempts per invocation, with 10-second request timeouts
and delays of 1s and 2s. Retryable failures: connection/timeout failures and HTTP
502/503/504. TLS errors, redirects, authentication, conflicts, validation and other
HTTP errors stop immediately. Malformed successful responses stop conservatively.
The timeout is per socket operation, not a strict overall wall-clock deadline.
On exhaustion the outcome remains unknown; rerun with the SAME receipt after
checking service availability. A successful replay returns the original case even
if processing has advanced. Never delete an uncertain receipt to obtain a new key.
Run only one invocation per receipt at a time.

This is a standalone operational submission client, not a new web form or browser
retry policy. It never submits a decision. The existing browser still never
retries decision POSTs automatically. If a decision response is lost, read that
case first and inspect its saved status before any further human action.

Existing backend guarantees remain authoritative: same key/body returns one case;
conflicting reuse rejects; queued work recovers; interrupted running analysis with
no result becomes held; saved analysis/decisions recover checkpoint gaps; a worker
failure fails readiness rather than spinning. A held interrupted case is not
silently requeued. External Slack delivery and automatic LLM recovery are outside
this checkpoint.

## 1. Apply and validate locally

Extract the delivered ZIP outside the repository. Its patch includes only the
new tools, tests and this guide; it does not overwrite local settings. From the
existing Windows PowerShell environment:

```powershell
Set-Location 'C:\Users\iyad\projects\slack-customer-score-copilot'
git branch --show-current
git log -1 --oneline
git status --short
# Require phase2/persistent-review-workflow at 8166b2c; inspect known diagnostics.
$patch = "$env:USERPROFILE\Downloads\M3_5_delivery\M3_5_delivery.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch apply failed' }
git diff --check
.\.venv\Scripts\python.exe -m pytest -q -rs tests/test_delivery_phase2.py tests/test_delivery_api_phase2.py tests/test_api_phase2.py tests/test_review_graph_phase2.py tests/test_review_decisions_phase2.py
if ($LASTEXITCODE -ne 0) { throw 'Recovery regression failed' }
```

Do not rebuild the selected score database. These tests create isolated stores.
Run the full offline suite before committing; existing CI runs it as well. New
tests join that existing matrix automatically; no duplicate CI workflow is added.

```powershell
$env:POLICY_VERSION = 'v1.0'
.\.venv\Scripts\python.exe -m pytest -q -rs
if ($LASTEXITCODE -ne 0) { throw 'Regression failed' }
git add -- deploy/azure/release.py deploy/azure/submit_case.py tests/test_delivery_phase2.py tests/test_delivery_api_phase2.py docs/Phase2_M3_5_Delivery_and_Recovery.md
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged whitespace check failed' }
git diff --cached --stat
git commit -m "Add explicit release identity and bounded submission recovery"
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
git push origin phase2/persistent-review-workflow
if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
```

A committed candidate is necessary to label its image. This commit and green CI
do not close M3.5: hosted checks below and a final verification record remain.
Never stage `docs/Azure_*`, settings, receipts, archives, databases or keys.

## 2. Build a recorded candidate

```powershell
$revision = (git rev-parse HEAD).Trim()
$imageTag = "scorecopilot:m35-$($revision.Substring(0,7))"
docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$revision" -t $imageTag .
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$releaseDir = "var/azure/m35-$($revision.Substring(0,7))"
.\.venv\Scripts\python.exe deploy/azure/release.py pack $imageTag $releaseDir
if ($LASTEXITCODE -ne 0) { throw 'Release export failed' }
```

The output directory must not already exist. Packaging refuses tracked dirty
changes, untracked application inputs, wrong platform, missing revision label or
mismatched implementation. Keep the release directory locally. Checksums detect
accidental corruption, not a malicious replacement of both archive and manifest.
SSH host verification protects transport.

## 3. Start the existing guarded session

Follow M3.4's existing-session procedure, not initial provisioning. Monitor credit;
trial authorization expires 8 December 2026. Start with `session.ps1 -Action Start`,
arm `-Action Guard` in a visible second window, refresh `$remote`, `$key`, `$origin`
and the FQDN from current settings/Azure. Keep the existing verified HostKeyAlias.
Update only the existing SSH /32 rule if your public IPv4 changed. Do not rerun
ARM creation, configure.ps1 or use-swedencentral.ps1.

On Linux, start the existing deployment; after M3.5 activation always include its
override on subsequent starts, so the recorded IDs remain selected:

```bash
cd /opt/scorecopilot
if [ -f release.override.json ]; then
  sudo docker compose -f compose.yaml -f release.override.json up --detach --wait
else
  sudo docker compose up --detach --wait
fi
```

Check trusted public HTTPS and read back the ORIGINAL M3.4 receipt using
`smoke.py ... --verify` (M3.4 token-in-memory try/finally procedure). Do not run
smoke without `--verify` over an existing receipt.

Before replacing the image, take a fresh stopped backup with a NEW filename using
M3.4 section 7. Download the DB/receipt and verify SHA256 on Windows. Restart the
services and verify the original case again. Keep the primary database; never
restore a backup over it for this release or rollback test.

## 4. Upload and activate

From Windows, using the session's `$remote` and `$key`:

```powershell
scp -i $key -o HostKeyAlias=scorecopilot-m34 -r $releaseDir "${remote}:"
if ($LASTEXITCODE -ne 0) { throw 'Release upload failed' }
scp -i $key -o HostKeyAlias=scorecopilot-m34 deploy/azure/release.py "${remote}:m35-release.py"
if ($LASTEXITCODE -ne 0) { throw 'Tool upload failed' }
```

On Linux, replace `m35-COMMIT7` with the actual exported directory basename:

```bash
sudo install -m 0755 /home/copilotadmin/m35-release.py /opt/scorecopilot/release.py
sudo install -d -m 0700 /opt/scorecopilot/releases
sudo mv /home/copilotadmin/m35-COMMIT7 /opt/scorecopilot/releases/
sudo chown -R root:root /opt/scorecopilot/releases/m35-COMMIT7
sudo chmod -R go-rwx /opt/scorecopilot/releases/m35-COMMIT7
cd /opt/scorecopilot
sudo python3 release.py deploy /opt/scorecopilot/releases/m35-COMMIT7
```

No automatic rollback occurs. If readiness fails, inspect bounded Docker logs and
run the explicit rollback below. An activation receipt is saved BEFORE replacing
containers. Do not delete it and repeat deploy blindly after an interrupted run.
If the candidate API container exists but exited, rollback can still use it for
identity comparison. If no API container exists, or its identity differs from the
receipt, stop and inspect; do not bypass the identity check. If Azure/SSH fails,
still execute the final Stop procedure from Windows.

After local readiness succeeds, repeat trusted HTTPS, original saved-case readback
and browser frozen-evidence inspection. The local readiness check is not proof
of public TLS or case preservation.

## 5. Bounded submission and duplicate demonstration

With the cloud token temporarily in the environment using M3.4's try/finally
procedure, run twice on Windows with the SAME new receipt path:

```powershell
.\.venv\Scripts\python.exe deploy/azure/submit_case.py $origin var/azure/m35-submission.json
if ($LASTEXITCODE -ne 0) { throw 'Submission unresolved; retain receipt' }
.\.venv\Scripts\python.exe deploy/azure/submit_case.py $origin var/azure/m35-submission.json
if ($LASTEXITCODE -ne 0) { throw 'Replay unresolved; retain receipt' }
```

Require the same case ID both times. Find it in the authenticated UI, inspect its
saved evidence, and record a human decision with a reason if testing a decision.
A deliberate live response drop is unnecessary: deterministic tests cover the
commit-before-response-loss scenario against the real API/store. Conflicting key
reuse is already covered by backend tests. No automatic decision replay is added.
Do not interpret another successful case as preservation of the original case.

## 6. Explicit rollback and restart

While the candidate is active, on Linux:

```bash
cd /opt/scorecopilot
sudo python3 release.py rollback /opt/scorecopilot/releases/m35-COMMIT7
```

Require trusted HTTPS and the original saved-case/hash/decision readback again.
Also verify the new M3.5 case and its recorded decision, if one was made. The
rollback retains the same database and is allowed only with matching six
implementation hashes. It does not reverse decisions or restore old data.
Retain both image IDs; do not run image pruning. If rollback compatibility fails,
leave the service stopped and inspect rather than forcing an older implementation.

For the restart gate, use the normal session Stop/Start cycle, arm a new guard,
refresh session addresses and explicitly start Compose with BOTH files as in
section 3. Check trusted HTTPS and the same original case. The override now points
to the rollback release by design. Record the final image ID. A future activation
uses a new release directory/receipt; this tool intentionally refuses overwriting
an existing activation record. No successful-deploy or rollback claim is made
until the owner observes these results.

Delete only the known exported `app-image.tar` on the VM after successful gates
and retaining the local copy; keep release JSON and activation receipt. Do not
remove prior images, backups or unrelated files. Capture the current proxy image
ID using `sudo docker compose -f compose.yaml -f release.override.json images`.
Registry RepoDigests may be inspected separately; a local image ID is not a
registry digest.

## 7. End session and closure

Always run `session.ps1 -Action Stop`, then Status and inventory checks, even when
an application check fails. Require deallocated VM, absent session public IP,
retained expected disk/NIC/VNet/NSG and no unexpected resources. Cancel the guard
only after verifying cleanup. Disk billing continues. No actual hours are inferred.

Record: candidate Git commit/CI, manifest and exact image IDs, backup hash,
release and rollback readiness, trusted HTTPS, original case/hash/decision after
each step and restart, same-key submission result, final deployment image, and
verified deallocation/IP removal. M3.5 remains In progress until these gates and
final commit/push/CI evidence exist. Backups remain manual; this is one VM, one
worker and a shared analyst token, not high availability or signed supply-chain
attestation. Deployment commands and compatibility checks are bounded in scope;
no claim of universal crash safety is made.

## Image-store compatibility correction — 10 November 2026

Owner testing of candidate d1e8703 exposed an incorrect portability assumption:
Windows Engine 20 exported configuration ID eafe6484..., while hosted Engine
29.9.3 loaded it as host image ID 1c05dafb.... The archive's raw configuration
SHA256, linux/amd64 platform and Git revision matched the original candidate.
The original application remained running; activation stopped before receipt or
Compose replacement. No manifest was manually edited and no guard deleted images.

The corrected tool verifies the archive checksum, reads its single image config,
checks the declared config identity and displays Docker load output. It resolves
the actual loaded host ID, re-exports that exact host object to a temporary archive
and requires the raw configuration hash to match. The config binds ordered rootfs
diff_ids. It still checks platform, revision and six implementation fingerprints
before writing the activation receipt or replacing containers. The host export
adds temporary disk I/O and roughly one image archive of disk space; it is removed
on normal exit and exceptions. A forced process/VM interruption may leave a
copilot-image-check-* directory under the host temp directory; inspect before
removing that named temporary artifact. Do not prune Docker images.

Existing schema-1 releases from the classic store remain valid without changes.
New packaging emits schema 2 with a separate config_id, allowing newer source
stores to use a different source image ID too. Activation receipts retain source
image ID, verified config ID and actual host image ID. Compose and rollback use
host IDs. This is a transport compatibility fix, not a new application image,
schema migration or weakened image-identity check.

Apply the correction against committed d1e8703; run its 15 standard-library tests.
Upload only the corrected release.py to the already installed host path, then
rerun release.py deploy with the EXISTING release directory. Do not repeat the
initial directory move or regenerate release.json. That existing candidate still
correctly identifies its original d1e8703 source. Commit the tool/test/docs fix
separately and record both commits during closure. The first activation attempt
was unsuccessful and must remain in the verification history. Live corrected
Docker activation and public/saved-case checks remain owner-run gates.
