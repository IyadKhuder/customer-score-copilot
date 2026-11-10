# Azure control-plane lifecycle only. Never prints an API token.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Status','Stop','Start','Guard')][string]$Action,
    [string]$SettingsPath = 'var/azure/settings.json',
    [ValidateRange(1,120)][int]$Minutes = 60
)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
if ($config.location -ne 'swedencentral') { throw 'Run use-swedencentral.ps1 before using this revised lifecycle script' }
$rg = $config.resourceGroup
$prefix = 'scorecopilot'
$vmName = 'vm-scorecopilot-demo'
function Read-Azure {
    param([string[]]$AzArgs)
    $raw = & az @AzArgs --only-show-errors -o json
    if ($LASTEXITCODE -ne 0) { throw "Azure operation failed: $($AzArgs[0..1] -join ' ')" }
    if ($raw) { return ConvertFrom-Json -InputObject ($raw -join [Environment]::NewLine) }
}
function Change-Azure {
    param([string[]]$AzArgs)
    & az @AzArgs --only-show-errors -o none
    if ($LASTEXITCODE -ne 0) { throw "Azure operation failed: $($AzArgs[0..1] -join ' ')" }
}
$account = Read-Azure -AzArgs @('account','show')
if ($account.id -ne $config.subscriptionId) { throw 'Selected subscription differs from saved settings' }
if ($Action -eq 'Guard') {
    Write-Host "Guard armed for $Minutes minutes. Keep this computer awake, online and this window open."
    $deadline = [DateTime]::UtcNow.AddMinutes($Minutes)
    while ([DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 15 }
    # Emergency fallback: backup is deliberately not a prerequisite to stopping spend.
    & $PSCommandPath -Action Stop -SettingsPath $SettingsPath
    exit
}
if ($Action -eq 'Stop') {
    # Deallocate even if SSH is unavailable. Do not use Linux shutdown as a substitute.
    Change-Azure -AzArgs @('vm','deallocate','-g',$rg,'-n',$vmName)
    $view = Read-Azure -AzArgs @('vm','get-instance-view','-g',$rg,'-n',$vmName)
    if (-not ($view.instanceView.statuses.code -contains 'PowerState/deallocated')) {
        throw 'VM is not verified deallocated; inspect Azure immediately'
    }
    $ips = Read-Azure -AzArgs @('network','public-ip','list','-g',$rg)
    $ip = $ips | Where-Object { $_.name -eq $prefix }
    if ($ip) {
        # --remove avoids the Windows empty-string argument problem.
        Change-Azure -AzArgs @('network','nic','ip-config','update','-g',$rg,'--nic-name',$prefix,'-n','primary','--remove','publicIpAddress')
        Change-Azure -AzArgs @('network','public-ip','delete','-g',$rg,'-n',$prefix)
    }
    $ips = Read-Azure -AzArgs @('network','public-ip','list','-g',$rg)
    if ($ips | Where-Object { $_.name -eq $prefix }) { throw 'Public IP still exists and can incur charges' }
    Write-Host 'Verified VM deallocated and session public IP absent. Retained disk still bills.'
} elseif ($Action -eq 'Start') {
    $view = Read-Azure -AzArgs @('vm','get-instance-view','-g',$rg,'-n',$vmName)
    if (-not ($view.instanceView.statuses.code -contains 'PowerState/deallocated')) {
        throw 'Start expects the existing VM to be deallocated'
    }
    $ips = Read-Azure -AzArgs @('network','public-ip','list','-g',$rg)
    if ($ips | Where-Object { $_.name -eq $prefix }) { throw 'Public IP already exists; inspect before continuing' }
    Write-Host 'Recreating a billable IP and starting billable compute. Arm a guard in another window.'
    try {
        Change-Azure -AzArgs @('network','public-ip','create','-g',$rg,'-n',$prefix,'-l',$config.location,'--sku','Standard','--allocation-method','Static','--version','IPv4','--zone','1','--dns-name',$config.dnsLabel)
        Change-Azure -AzArgs @('network','nic','ip-config','update','-g',$rg,'--nic-name',$prefix,'-n','primary','--public-ip-address',$prefix)
        Change-Azure -AzArgs @('vm','start','-g',$rg,'-n',$vmName)
    } catch {
        Write-Warning 'Start failed. Attempting deallocation and IP cleanup; inspect any cleanup errors.'
        & $PSCommandPath -Action Stop -SettingsPath $SettingsPath
        throw
    }
}
$view = Read-Azure -AzArgs @('vm','get-instance-view','-g',$rg,'-n',$vmName)
$ips = Read-Azure -AzArgs @('network','public-ip','list','-g',$rg)
$ip = $ips | Where-Object { $_.name -eq $prefix }
$disk = Read-Azure -AzArgs @('disk','show','-g',$rg,'-n','disk-scorecopilot-os')
[PSCustomObject]@{
    power = ($view.instanceView.statuses | Where-Object { $_.code -like 'PowerState/*' }).code
    publicIP = $ip.ipAddress
    fqdn = $ip.dnsSettings.fqdn
    diskGiB = $disk.diskSizeGb
    diskType = $disk.sku.name
}
