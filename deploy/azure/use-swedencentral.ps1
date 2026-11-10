# Update the existing LOCAL settings only, after checking an empty cloud group.
# Does not register providers, create/delete resources, or change SSH/DNS identity.
[CmdletBinding()]
param([string]$SettingsPath = 'var/azure/settings.json')
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
$parameterPath = Join-Path (Split-Path -Parent $SettingsPath) 'parameters.json'
$parameters = Get-Content -LiteralPath $parameterPath -Raw | ConvertFrom-Json
function Read-Azure {
    param([string[]]$AzArgs)
    $raw = & az @AzArgs --only-show-errors -o json
    if ($LASTEXITCODE -ne 0) { throw 'Azure read failed; no local settings changed' }
    if ($raw) { return ConvertFrom-Json -InputObject ($raw -join [Environment]::NewLine) }
}
$account = Read-Azure -AzArgs @('account','show')
if ($account.id -ne $config.subscriptionId) { throw 'Wrong selected subscription' }
$resources = Read-Azure -AzArgs @('resource','list','-g',$config.resourceGroup)
$resourceCount = 0
foreach ($resource in $resources) { if ($null -ne $resource) { $resourceCount++ } }
if ($resourceCount -ne 0) { throw 'Resource group contains resources; this migration is only for an empty predeployment group' }
$image = Read-Azure -AzArgs @('vm','image','show','--location','swedencentral','--urn','Canonical:ubuntu-24_04-lts:server:24.04.202610250')
if ($image.architecture -ne 'x64' -or $image.hyperVGeneration -ne 'V2' -or $image.name -ne '24.04.202610250') {
    throw 'Pinned image did not match; no local settings changed'
}
if ($config.dnsLabel -ne $parameters.parameters.dnsLabel.value) { throw 'DNS settings mismatch; reconcile before migration' }
if ($config.location -eq 'swedencentral' -and $parameters.parameters.location.value -eq 'swedencentral') {
    Write-Host 'Already configured for Sweden Central. Original SSH key, DNS label and group preserved.'
    return
}
$settingsBackup = "$SettingsPath.before-swedencentral"
$parametersBackup = "$parameterPath.before-swedencentral"
if ((Test-Path -LiteralPath $settingsBackup) -or (Test-Path -LiteralPath $parametersBackup)) {
    throw 'Migration backups already exist; inspect before retrying. Do not delete/regenerate settings.'
}
Copy-Item -LiteralPath $SettingsPath -Destination $settingsBackup
Copy-Item -LiteralPath $parameterPath -Destination $parametersBackup
try {
    $config | Add-Member -NotePropertyName location -NotePropertyValue 'swedencentral' -Force
    $parameters.parameters | Add-Member -NotePropertyName location -NotePropertyValue ([PSCustomObject]@{value='swedencentral'}) -Force
    $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8
    $parameters | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $parameterPath -Encoding UTF8
    $checkConfig = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    $checkParameters = Get-Content -LiteralPath $parameterPath -Raw | ConvertFrom-Json
    if ($checkConfig.location -ne 'swedencentral' -or $checkParameters.parameters.location.value -ne 'swedencentral') { throw 'Readback failed' }
} catch {
    Copy-Item -LiteralPath $settingsBackup -Destination $SettingsPath -Force
    Copy-Item -LiteralPath $parametersBackup -Destination $parameterPath -Force
    throw
}
Write-Host 'Local region updated to swedencentral. Existing group, DNS label, SSH public key and admin IP preserved.'
Write-Host 'Pinned x64 Gen2 image lookup passed. No cloud resources created or changed. ARM validation is next.'
