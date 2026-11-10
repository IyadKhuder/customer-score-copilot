# M3.4 — on-demand Azure hosting

10 November 2026. Sweden Central follow-up to the already applied 9 November package. Base `78d7f0d`, branch `phase2/persistent-review-workflow`. Deployed and owner-verified on 10 November; final documentation commit, push and CI remain pending. See the verification record below. This guide supersedes the unapplied `M3_4_design.zip`; do not apply that earlier patch. The current Phase 2 blueprint version 3 and M3.3 implementation were inspected before this design.

## Region change history — completed

West Europe ARM validation and what-if were rejected by `RequestDisallowedByAzure`: the region is not accepting new customers for this tenant. No West Europe VM deployment was attempted. The group was empty at that checkpoint; it now contains the retained Sweden Central deployment. Earlier portal/SKU/quota checks did not prove region admission. Microsoft recommends selecting another region for this situation.

Successfully deployed target: **Sweden Central, zone 1, Standard_B2ls_v2**, same pinned Ubuntu image and 32-GiB retained Standard_LRS disk. Owner supplied raw SKU data with empty restrictions and quota rows (regional/Bsv2 usage 0, limits 4). Sweden Central image lookup, ARM validation, what-if and allocation subsequently succeeded. Actual retained disk properties were verified as 32 GiB / Standard_LRS.

For the current owner, retain the existing **rg-scorecopilot-demo-weu** group and its saved identity. A group's metadata location does not force its resources to use that region: all five template resources explicitly use Sweden Central. The `weu` suffix is historical; it is not the deployment location. Do not delete or recreate the group or rerun configure.ps1. Fresh setups default to rg-scorecopilot-demo-sec.

The following migration steps are historical and were completed before creation. Do not rerun this empty-group helper against the deployed resources. Before creation, run `deploy/azure/use-swedencentral.ps1`. It verifies the selected subscription, empty group and pinned x64 Gen2 image, backs up local settings/parameters, and changes only their region fields. It preserves DNS label, public SSH key, admin IPv4 and group. It does not create, delete or update Azure resources. Then refresh `$config` and `$rg` from the saved settings and rerun section 4 validation/what-if ONLY. Do not proceed to creation until those pass and have been reviewed.

The original local image archive, SSH key, smoke result and case volume remain valid. No image rebuild, data rebuild or local container restart is required for this region-only change. Remaining sections describe deployment after validation; do not rerun original patch/setup steps already completed.

## 1. Architecture and scope

Windows remains the development environment. Azure runs a separate synthetic demonstration: FastAPI and its one worker in the existing image, plus Caddy for HTTPS. Live LLM notes stay disabled. The local Compose file, named volume, six fingerprinted domain files and Slack path are unchanged. Cloud state is not synchronized with local cases. No database migration, live provider, Slack bridge, paid registry, paid DNS zone, paid logging stack, Key Vault, Bastion, managed database or load balancer is added.

`deploy/azure/main.json` declares five resources: VM, NIC, public IP, VNet and network security group (NSG). One 32-GiB managed OS disk is created with the VM. The existing owner's resource group remains `rg-scorecopilot-demo-weu` (fresh setup defaults to `rg-scorecopilot-demo-sec`); VM `vm-scorecopilot-demo`; networking resource names `scorecopilot`; disk `disk-scorecopilot-os`. The public IP has a generated DNS label. No Azure subscription identifier is in the template. Local settings under ignored `var/azure` bind operations to the selected subscription. Never stage those settings or raw account-specific diagnostics.

Verified deployment: Sweden Central, zone 1, Standard_B2ls_v2 (2 vCPUs, 4 GiB, x64), Ubuntu 24.04 Gen2 image version `24.04.202610250`, Trusted Launch, 32-GiB Standard_LRS HDD, OS disk delete option Detach. Retain secure boot and vTPM. Owner-side Sweden Central quota shows regional and Bsv2 limits 4, usage 0. Raw SKU data lists zones 1/2/3 and no restrictions for B2ls_v2. The first deployment passed ARM validation and allocation; future capacity is not guaranteed. Do not silently resize or change zones.

The original West Europe portal accepted the size and 32-GiB Standard HDD, but regional admission subsequently failed. That portal evidence must not be represented as Sweden Central deployment evidence. The image-show API does not report source disk size. Read actual disk properties after successful allocation and require 32 GiB / Standard_LRS. Standard HDD OS disks have a September 2028 retirement notice; this is a short demonstration architecture, not a long-term infrastructure promise.

### Why a VM rather than Container Apps?

The case store and LangGraph use the same `cases.db`. The pinned LangGraph SQLite adapter 3.1.3 enables WAL (write-ahead logging), which requires local locking/shared memory. Azure Files is network file storage; it is not an appropriate WAL database mount. Container Apps temporary storage cannot retain cases across replica deletion. One replica does not cure the filesystem issue. App Service/ACI with network file mounts have the same concern. A server database could solve it but needs a separate migration and cost assessment. A VM managed block disk with a local ext4 filesystem fits the current code.

Cloud Compose bind-mounts `/srv/copilot/state` to `/app/var/api`. That directory includes the database, WAL/shared-memory sidecars and application lease. It is owned by UID/GID 10001. Do not place it on a temporary/resource disk. Keep one Uvicorn process and one worker. Bind API port 8000 only to loopback on the host; Caddy reaches it through the private Compose network. Externally open TCP 80/443 for HTTPS and ACME certificate issuance. SSH TCP 22 is allowed only from your supplied IPv4 /32. No port 8000 NSG rule exists. Caddy access logging is not enabled; Docker logs rotate at two 5-MB files per container.

Caddy's `caddy:2` is the official image, resolved at first pull. Record its image ID/digest when verifying. It is a mutable major tag; this initial deployment does not claim fully reproducible image builds. The existing app Dockerfile also uses mutable base tags. Digest/version hardening and fuller deployment automation belong to M3.5. Persist Caddy certificate data on the OS disk. Recreated public IPs can change address, and DNS-label reclamation is not guaranteed. Check the returned FQDN every session; never bypass a TLS error. Stop/cleanup if DNS or certificate issuance cannot be resolved within the planned guarded session.

### Costs, credit and limits

**Trial decision, authorized 10 November 2026:** Until the sign-up credit expires on 8 December 2026, the $3/month target and ten allocated hours/month allowance are relaxed for eligible deployment, setup, debugging, demonstrations and lifecycle/recovery tests. Stay within the remaining credit and monitor it; this authorizes neither excluded purchases nor paid usage beyond the credit, nor a Pay-As-You-Go upgrade. Continue economical start/stop verification. Before expiry, review retained resources and prepare to return to the occasional-use plan below $3/month; broader paid usage needs a separate decision.

The table below models that **post-trial occasional-use plan**, not a current trial-time limit.

Fresh Microsoft Retail Prices API snapshot: `deploy/azure/prices_swedencentral_2026-11-10.json`. USD retail, Sweden Central (the original West Europe snapshot is retained as historical evidence); no trial credit or temporary free grant assumed.

| Meter | Rate / allowance | 10 allocated hours/month |
| --- | --- | ---: |
| Linux B2ls_v2 compute | $0.0432/hour | $0.432 |
| 32-GiB S4 managed HDD | $1.536/month | $1.536 |
| Standard IPv4, only during sessions | $0.005/hour | $0.050 |
| HDD transactions | $0.0005/10,000; model 1 million | $0.050 |
| Modeled subtotal | | $2.068 |
| Illustration adding 21% VAT | actual tax treatment unverified | $2.502 |

Reserve about $0.20 before tax for incidental usage: approximately $2.74 with illustrative VAT. This leaves little room. **For the post-trial plan, use a 10-hour/month allocated-time allowance**, including setup/debugging/upload and startup time, not only browser demonstrations. At 20h the same model is $2.550 before tax / $3.086 with illustrative VAT and fails the target. A public IP retained all month costs about $3.65 by itself; allocated compute all month costs $31.54. The portal monthly compute estimate assumes 730h, not a flat monthly commitment. Disk charges continue while deallocated. Standard HDD transactions can exceed the modeled allowance with package installation/updates; inspect actual usage after the first session before continuing. A 32-GiB disk may be tight: keep logs bounded, remove only your known uploaded image archive after loading, and do not cache unlimited images/backups.

No Azure Backup, snapshot or Blob account is planned. Download verified stopped-database backups to the owner computer: no additional Azure backup-storage charge. Small demo outbound transfer is expected to fit the applicable free allowance, but confirm that the subscription has not exhausted it; otherwise add actual egress charges. No commercial OS license is used. Inspect the resource inventory for unexpected resources and paid subscription defaults/Defender plans. Resource group, NSG, VNet and NIC do not add the planned per-hour services of a gateway or Bastion. Prices and tax are estimates, not a monetary ceiling.

Azure budgets notify with delay and do not stop spending. Pay-as-you-go has no arbitrary $3 hard cap. The local session guard is best effort: it fails if the laptop sleeps, disconnects, closes the window, or Azure operations fail. It is not a hard cap. Keep the guard window visible and verify the final Azure state. The trial expires 8 December; no upgrade or removal of its spending limit is authorized here. Without upgrade, service will be disabled after trial expiry/exhaustion. Export verified backups and remove billable resources deliberately before expiry if not continuing. Deleting the resource group deletes retained disks too; only do that after verified export and a separate explicit decision.

## 2. Apply and verify on Windows

The portal form was inspection only. Do not press Create there; the ARM template is the sole creation path for this package. It implements the same inspected VM/disk settings plus explicit network rules. Close/cancel the unsubmitted draft after recording your checks.

Extract this package outside the repo. The existing five untracked `docs/Azure_*` files can remain; the patch does not touch them. The image diagnostic includes your subscription ID, so do not add it to a public commit. Never use `git add .` here.

```powershell
Set-Location 'C:\Users\iyad\projects\slack-customer-score-copilot'
git branch --show-current
git log -1 --oneline
git status --short
# Expect the named branch, 78d7f0d and only your known untracked diagnostics.
$patch = "$env:USERPROFILE\Downloads\M3_4_azure\M3_4_azure.patch"
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch check failed' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw 'Patch apply failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed' }
git status --short
```

Local app image verification uses the existing local token and named volume. Do not regenerate your working score database. These commands rebuild the image and run the existing review smoke, which creates three synthetic local cases and records two decisions:

```powershell
if (-not $env:COPILOT_API_TOKEN) {
    $secret = Read-Host 'Existing local API token' -AsSecureString
    $env:COPILOT_API_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    Remove-Variable secret
}
docker compose up --build --detach --wait
if ($LASTEXITCODE -ne 0) { throw 'Local Docker startup failed' }
.\.venv\Scripts\python.exe -m scripts.smoke_review_phase2
if ($LASTEXITCODE -ne 0) { throw 'Local review smoke failed' }
```

No `--wait-timeout`, no `down -v`. Domain/UI files are unchanged, so a new full regression is optional unless your local checks expose an issue. Existing CI will run on push. Build the cloud image separately using the same Dockerfile:

```powershell
New-Item -ItemType Directory -Force 'var\azure' | Out-Null
$imageTag = 'scorecopilot:m34-78d7f0d'
docker build --platform linux/amd64 -t $imageTag .
if ($LASTEXITCODE -ne 0) { throw 'Cloud image build failed' }
docker save --output 'var\azure\app-image.tar' $imageTag
if ($LASTEXITCODE -ne 0) { throw 'Image export failed' }
Get-Item 'var\azure\app-image.tar' | Select-Object Name, Length
```

## 3. Local settings and subscription preparation

Create a dedicated SSH key if the chosen path does not exist. Enter a passphrase locally and preserve the private key; never upload it. You can use an existing Ed25519 public key instead.

```powershell
$key = Join-Path $env:USERPROFILE '.ssh\scorecopilot_azure'
New-Item -ItemType Directory -Force (Split-Path -Parent $key) | Out-Null
if (-not (Test-Path -LiteralPath $key)) {
    ssh-keygen -t ed25519 -f $key -C 'scorecopilot-azure-demo'
    if ($LASTEXITCODE -ne 0) { throw 'SSH key creation failed' }
}
```

Find your current public IPv4 using your router/VPN status or a trusted IP-check service. Enter just the dotted address when prompted. The script limits SSH to this address /32 and checks the selected subscription. Keep this address private in shared screenshots. A private LAN address such as 192.168.x.x will not work for this rule.

```powershell
$adminIPv4 = Read-Host 'Your current PUBLIC IPv4 address, without /32'
.\deploy\azure\configure.ps1 -AdminIPv4 $adminIPv4 -PublicKeyPath "$key.pub"
```

If script execution is blocked, use your existing approved PowerShell execution policy/workflow; do not change machine-wide policy. Local settings and parameters are ignored by git. They contain identifiers/public IP and a public SSH key, not the private key or API token. Do not regenerate the DNS label once the VM exists.

The following setup changes subscription configuration/registers the networking provider and creates an empty resource group. Neither creates a VM. Use the CLI's existing authentication; do not upgrade the account. Define a helper that checks native exit codes (PowerShell ErrorActionPreference alone does not catch Azure CLI failures):

```powershell
function Invoke-AzNone {
    param([string[]]$AzArgs)
    & az @AzArgs --only-show-errors -o none
    if ($LASTEXITCODE -ne 0) { throw 'Azure operation failed; inspect the preceding error' }
}
Invoke-AzNone -AzArgs @('provider','register','--namespace','Microsoft.Network','--wait')
$config = Get-Content 'var\azure\settings.json' -Raw | ConvertFrom-Json
$rg = $config.resourceGroup
# If this group already exists, inspect it and stop if it contains any resources.
az resource list -g $rg -o table
# A missing-group error is expected on first use; do not treat it as an empty existing group.
Invoke-AzNone -AzArgs @('group','create','-n',$rg,'-l','swedencentral')
az resource list -g $rg -o table
```

If resources are listed, stop before initial deployment and reconcile. Do not redeploy the initial template over an existing working installation.

## 4. Validate, review, create, and arm cleanup

Before creating billable resources, validate and inspect what-if. These are live Azure checks not performed in the preparation workspace. Parameters are passed from a local JSON file to avoid Windows JMESPath/backtick quoting problems.

```powershell
Invoke-AzNone -AzArgs @('deployment','group','validate','-g',$rg,'--template-file','deploy/azure/main.json','--parameters','@var/azure/parameters.json')
az deployment group what-if -g $rg --template-file deploy/azure/main.json --parameters '@var/azure/parameters.json'
if ($LASTEXITCODE -ne 0) { throw 'What-if failed' }
```

Review expected changes: one VM B2ls_v2 in Sweden Central zone 1, one 32-GiB Standard_LRS OS disk with Detach, no data disks, one Standard IPv4, one VNet/subnet, NIC and NSG; SSH only owner /32, ports 80/443 public, no port 8000. No managed database, backup vault, registry, monitoring workspace, gateway or paid support resource. Stop on a different size, image, disk, location, security configuration or unexpected resource. ARM may reject the pinned image or disk; report the actual error, do not choose a larger disk automatically.

**This next command creates billable resources.** You apply it locally after reviewing what-if and the cost envelope. Have the Stop command ready in another window:

```powershell
Invoke-AzNone -AzArgs @('deployment','group','create','-g',$rg,'-n','m34-initial','--template-file','deploy/azure/main.json','--parameters','@var/azure/parameters.json')
.\deploy\azure\session.ps1 -Action Status
```

If creation fails, inspect `az resource list -g $rg -o table` immediately: partial disks/IPs can still bill. If the VM exists, run Stop; if only the public IP exists, delete that named IP after checking it has no attachment. A retained disk will continue billing until explicitly cleaned up. Do not abandon a failed deployment with resources still allocated.

In a SECOND PowerShell window, from the repo, arm a 60-minute best-effort fallback. Keep the laptop awake/online. Do not arm before initial deployment completes; the guard expects a VM to exist. Cancel the guard with Ctrl+C only after you have manually stopped and verified cleanup, to avoid it stopping a later session unexpectedly.

```powershell
.\deploy\azure\session.ps1 -Action Guard -Minutes 60
```

The guard deallocates the VM and deletes its public IP at expiry, even if backup was not completed. It does not save your work or guarantee a monetary cap. Manual verified Stop is the primary procedure; setup time counts against the monthly allowance.

The status result must show 32 GiB, Standard_LRS and running power. Obtain the actual address/FQDN, not a constructed URL:

```powershell
$ipRaw = az network public-ip show -g $rg -n scorecopilot -o json
if ($LASTEXITCODE -ne 0) { throw 'Public IP lookup failed' }
$ip = ConvertFrom-Json -InputObject ($ipRaw -join [Environment]::NewLine)
$fqdn = $ip.dnsSettings.fqdn
if ($fqdn -notmatch '^[a-z0-9.-]+\.cloudapp\.azure\.com$') { throw 'Unexpected or missing public DNS name' }
$remote = "copilotadmin@$($ip.ipAddress)"
$origin = "https://$fqdn"
```

## 5. Verify SSH and prepare the host

Get the VM's Ed25519 host fingerprint via the authenticated Azure control plane, not from the SSH connection being verified:

```powershell
az vm run-command invoke -g $rg -n vm-scorecopilot-demo --command-id RunShellScript --scripts 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' -o json
if ($LASTEXITCODE -ne 0) { throw 'Host fingerprint lookup failed' }
ssh -i $key -o HostKeyAlias=scorecopilot-m34 $remote
```

Compare the fingerprint in the SSH prompt with the control-plane output before accepting. If it differs, stop; do not disable host-key checking. Exit that shell after the test. HostKeyAlias keeps the verified VM identity stable across IP changes. Revalidate if the VM is deliberately replaced.

Copy only the deployment directory and app image archive, never the repo, private key, .env, local databases or diagnostic JSONs:

```powershell
scp -i $key -o HostKeyAlias=scorecopilot-m34 -r deploy/azure "${remote}:azure-m34"
if ($LASTEXITCODE -ne 0) { throw 'Deployment-file upload failed' }
scp -i $key -o HostKeyAlias=scorecopilot-m34 var/azure/app-image.tar "${remote}:app-image.tar"
if ($LASTEXITCODE -ne 0) { throw 'Image upload failed' }
ssh -i $key -o HostKeyAlias=scorecopilot-m34 $remote
```

In the Linux SSH shell, execute:

```bash
sudo bash azure-m34/host-setup.sh
sudo cp -r azure-m34/. /opt/scorecopilot/
sudo chown -R root:root /opt/scorecopilot
sudo docker load --input app-image.tar
# Confirm the expected tag was loaded, then remove only that uploaded archive.
sudo docker image inspect scorecopilot:m34-78d7f0d --format '{{.Id}}'
rm -- app-image.tar
df -h / /srv/copilot/state
sudo findmnt -T /srv/copilot/state -o TARGET,SOURCE,FSTYPE
```

Stop on host-setup, image-load or disk-space errors. Expected filesystem is the managed OS disk's local filesystem, not tmpfs, SMB, NFS or a resource/temporary disk. The script installs Docker from its official signed apt repository; no Docker-group privilege is granted to the user. It generates a separate 64-character cloud token into root-only `/etc/scorecopilot/api.env`; no token is printed or placed in the image. Linux sudo permission remains necessary for administration. Keep the guard active during setup; if time runs short, Stop and resume another session.

Still in Linux, create `/opt/scorecopilot/.env` using the ACTUAL `$fqdn` printed/retained on your Windows side. Substitute it below; it is not a token:

```bash
sudo install -m 0600 /dev/null /opt/scorecopilot/.env
sudo nano /opt/scorecopilot/.env
```

Enter these two lines, save and exit:

```text
COPILOT_IMAGE=scorecopilot:m34-78d7f0d
APP_DOMAIN=YOUR-ACTUAL-AZURE-FQDN
```

If `nano` is unavailable, use `sudo vi` or install the standard Ubuntu nano package. No secrets are in this Compose settings file. The API token stays in the separate root-only runtime file. The cloud project's name and bind mounts are distinct from local Compose.

```bash
cd /opt/scorecopilot
sudo docker compose config --quiet
sudo docker compose up --detach --wait
sudo docker compose ps
sudo docker image inspect caddy:2 --format '{{json .RepoDigests}}'
```

Do NOT use `docker compose config` without `--quiet` because resolved config includes API credentials. Proxy startup does not prove that a trusted certificate has been issued. Check remote HTTPS from Windows next. If needed, `sudo docker compose logs --tail 50 proxy` shows ACME errors; do not dump API environment or inspect full container configuration in shared output. Keep API port 8000 private.

## 6. Hosted checks and saved-case receipt

Back in Windows PowerShell (exit SSH), verify trusted HTTPS. No `-k`, insecure mode or certificate-warning bypass:

```powershell
Invoke-WebRequest -Uri "$origin/health/ready" -UseBasicParsing
Start-Process $origin
```

To run the HTTPS smoke without printing/saving the token on Windows, capture its SSH response directly into memory. Avoid PowerShell transcription/screen sharing during secret handling. This replaces only the current shell's token temporarily and restores the previous local value afterward:

```powershell
$previousToken = $env:COPILOT_API_TOKEN
try {
    $cloudToken = ssh -i $key -o HostKeyAlias=scorecopilot-m34 $remote "sudo sed -n 's/^COPILOT_API_TOKEN=//p' /etc/scorecopilot/api.env"
    if ($LASTEXITCODE -ne 0) { throw 'Cloud token retrieval failed' }
    $env:COPILOT_API_TOKEN = ($cloudToken -join '').Trim()
    .\.venv\Scripts\python.exe deploy/azure/smoke.py $origin var/azure/hosted-case.json
    if ($LASTEXITCODE -ne 0) { throw 'Hosted smoke failed' }
    # Copy only if you want to paste into the browser's token input; then clear clipboard.
    Set-Clipboard -Value $env:COPILOT_API_TOKEN
    Read-Host 'Paste token into the app, connect, then press Enter here to clear the clipboard'
    Set-Clipboard -Value ''
} finally {
    $env:COPILOT_API_TOKEN = $previousToken
    Remove-Variable cloudToken -ErrorAction SilentlyContinue
}
```

The smoke creates one synthetic case, waits for analysis, records an approval, checks duplicate rejection and unauthenticated reads/decisions, and saves only case ID/hash/revision/status/decision to ignored `var/azure/hosted-case.json`. It does not create an LLM note. Browser check: inspect the same completed case and frozen evidence; disconnect clears private content; reload requests token again. No per-user identity claim is added: this remains a single-analyst bearer-token demonstration.

To verify persistence later, use the same try/finally token-capture block but replace the smoke invocation with:

```powershell
.\.venv\Scripts\python.exe deploy/azure/smoke.py $origin var/azure/hosted-case.json --verify
if ($LASTEXITCODE -ne 0) { throw 'Saved case readback failed' }
```

Do not recreate the case receipt after a restart: it is the comparison baseline. The script compares all five saved fields and requires normal TLS verification.

## 7. Container replacement, backup and isolated restoration

During the same session, replace only the cloud API container (Linux SSH shell in `/opt/scorecopilot`):

```bash
sudo docker compose up --detach --force-recreate --wait api
```

Repeat the Windows saved-case readback and browser evidence check. Then stop BOTH cloud services, taking the hosted endpoint offline, and create a stopped-store backup. The backup helper acquires the same OS lease as the API, uses SQLite's backup API, checks integrity and required case/idempotency/LangGraph tables, and refuses existing output paths. It never copies a live database file alone.

```bash
cd /opt/scorecopilot
sudo docker compose stop
sudo python3 backup.py /srv/copilot/state/cases.db /srv/copilot/backups/m34-first.db
sudo install -m 0600 -o copilotadmin -g copilotadmin /srv/copilot/backups/m34-first.db /home/copilotadmin/m34-first.db
sudo install -m 0600 -o copilotadmin -g copilotadmin /srv/copilot/backups/m34-first.db.json /home/copilotadmin/m34-first.db.json
```

Do not overwrite `m34-first.db` on later sessions; use a new date/session filename. If backup fails, still run the Azure Stop procedure to avoid leaving compute/IP allocated, then report the backup failure. Stop is not dependent on SSH availability.

From Windows, download and hash-check this backup before stopping the Azure session:

```powershell
scp -i $key -o HostKeyAlias=scorecopilot-m34 "${remote}:m34-first.db" var/azure/m34-first.db
if ($LASTEXITCODE -ne 0) { throw 'Backup download failed' }
scp -i $key -o HostKeyAlias=scorecopilot-m34 "${remote}:m34-first.db.json" var/azure/m34-first.db.json
if ($LASTEXITCODE -ne 0) { throw 'Receipt download failed' }
$receipt = Get-Content var/azure/m34-first.db.json -Raw | ConvertFrom-Json
$actualHash = (Get-FileHash var/azure/m34-first.db -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $receipt.sha256) { throw 'Downloaded backup hash mismatch' }
```

For a meaningful restoration check, copy the backup to a NEW isolated directory and run the same application image against it, without replacing the primary store. First upload the saved-case receipt from Windows:

```powershell
scp -i $key -o HostKeyAlias=scorecopilot-m34 var/azure/hosted-case.json "${remote}:hosted-case.json"
if ($LASTEXITCODE -ne 0) { throw 'Case receipt upload failed' }
```

Then in Linux SSH, primary services still stopped:

```bash
sudo mkdir /srv/copilot/restore-check
sudo install -m 0600 -o 10001 -g 10001 /srv/copilot/backups/m34-first.db /srv/copilot/restore-check/cases.db
sudo chown 10001:10001 /srv/copilot/restore-check
sudo docker run --detach --name scorecopilot-restore-check --init \
  --env-file /etc/scorecopilot/api.env \
  --mount type=bind,source=/srv/copilot/restore-check,target=/app/var/api \
  --publish 127.0.0.1:8001:8000 \
  --log-opt max-size=5m --log-opt max-file=2 \
  scorecopilot:m34-78d7f0d
# Wait until this reports healthy (repeat the read if still starting):
sudo docker inspect --format '{{.State.Health.Status}}' scorecopilot-restore-check
# Run in a root shell so the root-only token file is readable; it is not printed:
sudo bash -c 'set -a; . /etc/scorecopilot/api.env; set +a; python3 /opt/scorecopilot/smoke.py http://127.0.0.1:8001 /home/copilotadmin/hosted-case.json --verify'
sudo docker stop scorecopilot-restore-check
sudo docker rm scorecopilot-restore-check
```

Only this explicitly named disposable container is removed. Keep the isolated restored directory as verification evidence; do not run it beside the primary service after testing. The live store was never overwritten. The loopback-only HTTP allowance exists solely for local restore checks; remote smoke requires HTTPS. Count and compare the case/hash/decision after restore, not only `integrity_check`. Check SQLite runtime journal mode and integrity on the stopped primary store:

```bash
sudo python3 - <<'PY'
import sqlite3
with sqlite3.connect('file:/srv/copilot/state/cases.db?mode=ro', uri=True) as db:
    print('journal_mode:', db.execute('PRAGMA journal_mode').fetchone()[0])
    print('integrity_check:', db.execute('PRAGMA integrity_check').fetchone()[0])
PY
```

Expected WAL and ok. The existing single-process lease is unchanged. Preparation verified backup refusal while that lease is held; repeat a live second-server check on the VM if claiming host-level enforcement: while primary API is running, run a second `docker run --rm --env-file /etc/scorecopilot/api.env --mount type=bind,source=/srv/copilot/state,target=/app/var/api scorecopilot:m34-78d7f0d` with no published ports. It should fail startup on the held lease. Do not run this after stopping primary and mistake a successful startup for failure; if it stays running unexpectedly, Ctrl+C and report it.

## 8. End every session: deallocate AND delete the public IP

From Windows, whether or not SSH/application checks succeeded:

```powershell
.\deploy\azure\session.ps1 -Action Stop
.\deploy\azure\session.ps1 -Action Status
az resource list -g $rg -o table
```

Require power `PowerState/deallocated`, no public IP/FQDN, retained disk 32 GiB / Standard_LRS, and no unexpected resources. The Stop script detaches and deletes the session public IP after verifying deallocation. The OS disk, NIC, VNet and NSG remain. No VM/disk/resource-group deletion is performed. If any command fails, use the Azure portal to stop/deallocate the VM, then dissociate/delete the public IP, and verify both; do not leave the error for another day. Cancel the guard window after successful Stop.

Record actual allocated session duration. Check resource-group usage/cost when billing catches up, including disk transactions; budget alerts have delay. No hours are logged to the project tracker unless the owner supplies them.

## 9. Next session and restart-persistence gate

If your public IPv4 changed, update the existing `ssh-owner` NSG rule's source prefix to your new /32 before connecting (Azure portal is sufficient). Do not broaden it to Any. Preserve local settings and private SSH key. In Windows:

```powershell
.\deploy\azure\session.ps1 -Action Start
.\deploy\azure\session.ps1 -Action Status
```

Arm the guard in another window, and rerun section 4's public-IP/FQDN lookup to refresh `$remote`, `$fqdn` and `$origin`. SSH uses the same verified HostKeyAlias. Inspect `/opt/scorecopilot/.env` and update APP_DOMAIN if the returned FQDN changed. The saved certificate data remains; DNS propagation/certificate issuance may take time. Restart services explicitly (restart policy is no):

```bash
cd /opt/scorecopilot
sudo docker compose up --detach --wait
```

Verify trusted HTTPS, the same saved-case receipt with `--verify`, and frozen evidence in the browser. A newly created successful case is not evidence that the old case persisted. Back up and run Stop at the end. Do not rerun the initial ARM deployment to restart: it is for first provisioning only.

Rollback of the application uses a previously retained, recorded image tag with the same state directory and compatible fingerprints. Do not assume an older code version can approve a package produced by different code. For this first hosting checkpoint, preserving `scorecopilot:m34-78d7f0d` and stopping the hosted service is the safe rollback. No automatic migration or code downgrade is performed.

## Owner verification record — 10 November 2026

These results were reported by the owner on Windows and the hosted Linux VM. They are separate from preparation-workspace checks. No application, image or infrastructure changes are introduced by this documentation closure.

| Gate | Observed result |
| --- | --- |
| Local Docker review smoke | Healthy; approve/reject, required reason, stale/hash/duplicate conflicts and held gate passed. Existing named volume preserved. |
| Cloud image | amd64 archive 612,765,696 bytes; loaded tag `scorecopilot:m34-78d7f0d`; uploaded archive removed from VM. |
| Storage | ext4 on managed OS disk; retained 32 GiB Standard_LRS; primary SQLite `journal_mode: wal`, `integrity_check: ok`. |
| HTTPS and browser | Trusted certificate without warning; readiness HTTP 200, offline mode, live_notes false; browser connected and displayed synthetic account 1000003, assessment 1991-12. |
| Saved review | Receipt recorded revision 3, completed, approved. Initial smoke console output was not captured; the receipt and subsequent successful readback corroborate the saved outcome. |
| Container replacement | API force-recreated healthy; original case still approved, exact package hash unchanged, frozen evidence available, no review form. |
| Stopped backup | Both primary containers stopped; backup counts: api_requests 1, checkpoints 4, review_cases 1. Downloaded database and JSON receipt; SHA256 verified on Windows. |
| Isolated restore | Separate restored directory, UID/GID 10001, database mode 0600; disposable loopback test container healthy; original package/decision readback PASS; only disposable test container removed. Primary database not overwritten. |
| VM restart | Deallocated, public IP removed, restarted with a different IP; original FQDN retained. Explicit Compose startup healthy; trusted HTTPS 200 offline; same original saved case readback PASS. |
| Final shutdown | Stop and Status confirmed deallocated, public IP/FQDN absent; guard canceled. Inventory contains only VM, disk, NIC, VNet and NSG, all in Sweden Central. Retained disk continues billing. |

Synthetic baseline case: `e1bd8ce0-76e0-4059-bb38-944fa475d9c8`. Package SHA256: `19514e3444fb1906403ae381c23e6d225863d5851a6e5bda1e57742a7d5b3507`. Backup SHA256: `436d45b603d017f0c0f89bc483b7c78c551208c7a7b2f1e26f18bb0e63f422aa`. Backup and case receipts remain under ignored `var/azure`; do not commit databases, tokens, local settings or raw subscription diagnostics.

At final shutdown the portal still displayed **€179.54 remaining**, expiring **8 December 2026**. This is the observed display, not a reconciled cost or proof of zero usage. Azure usage reporting can lag; review subscription credit and resource-group costs again after billing data arrives. No actual allocated-duration total or final charge was supplied, so neither is inferred. Budget alerts and the local guard are not hard spending caps.

Limits of this verification: guard arming was observed, automatic expiry was not exercised; a hosted second-server lease rejection was not separately demonstrated; Caddy's resolved digest was not captured. Backups are manual and one verified off-host recovery point was tested. There is no high availability or automatic recovery claim. The deeper deployment/recovery work remains for M3.5. M3.3 coverage notes remain unchanged: queued/running states were not observed and navigation during submission was not separately tested; disconnect during submission passed.

Runtime hosting and persistence gates passed. Record the final documentation/deployment commit and successful CI before closing M3.4 in the tracker. No additional project hours are logged without an explicit owner-supplied amount. The Slack-to-web bridge remains outside approved implementation scope.

## 10. Validation, commit, and completion reporting

Preparation checks are reported in the package's `VALIDATION.md`. Docker, Azure control-plane validation, Windows 5.1 execution, trusted public TLS and owner-side persistence are separate gates. Do not mark M3.4 Done from a prepared patch or portal form.

Stage ONLY these files after owner checks; raw diagnostics and `var` remain excluded:

```powershell
git add -- .gitattributes deploy/azure/main.json deploy/azure/compose.yaml deploy/azure/Caddyfile deploy/azure/host-setup.sh deploy/azure/configure.ps1 deploy/azure/session.ps1 deploy/azure/backup.py deploy/azure/smoke.py deploy/azure/prices_2026-11-09.json deploy/azure/prices_swedencentral_2026-11-10.json deploy/azure/use-swedencentral.ps1 docs/Phase2_M3_4_Azure_Hosting.md
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged whitespace check failed' }
git diff --cached --stat
git status --short
git commit -m "Add on-demand Azure hosting and persistence runbook"
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
git push origin phase2/persistent-review-workflow
if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
git log -1 --oneline
git status --short
```

Report: commit/CI, public trusted HTTPS, unauthorized reads/decisions, browser evidence, same-case container replacement and VM restart readback, stopped-backup download/hash, isolated restoration, filesystem/WAL/integrity, final deallocated state/IP absence, and actual cost once available. Keep credentials/account IDs out of reports. Log only supplied hours. Finish M3.4 before assessing the unapproved Slack bridge. M3.5 remains the next planned deeper deployment/recovery module.

Primary references (region-admission and group-location guidance rechecked 10 November):
- https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-region-access-policy
- https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/overview
- https://sqlite.org/wal.html
- https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts
- https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing
- https://learn.microsoft.com/en-us/azure/virtual-machines/delete
- https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available
- https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets
- https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/avoid-charges-free-account
- https://learn.microsoft.com/en-us/cli/azure/vm
- https://learn.microsoft.com/en-us/cli/azure/network/public-ip
- https://docs.docker.com/engine/install/ubuntu/
- https://caddyserver.com/docs/automatic-https

Billing-data reference: https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/understand-cost-mgt-data
