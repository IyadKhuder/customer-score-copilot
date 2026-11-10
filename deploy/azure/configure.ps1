# Writes local parameters only. Does NOT register providers or create resources.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$AdminIPv4,
    [Parameter(Mandatory=$true)][string]$PublicKeyPath,
    [string]$SettingsPath = 'var/azure/settings.json'
)
$ErrorActionPreference = 'Stop'
$address = [System.Net.IPAddress]::Parse($AdminIPv4)
if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw 'Use a public IPv4 address' }
if ($address.ToString() -ne $AdminIPv4) { throw 'Use canonical IPv4 notation without a CIDR suffix' }
if (-not $PublicKeyPath.EndsWith('.pub')) { throw 'Provide a PUBLIC .pub key, never a private key' }
$publicKey = (Get-Content -LiteralPath $PublicKeyPath -Raw).Trim()
if ($publicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$') { throw 'Expected an Ed25519 OpenSSH public key' }
$raw = az account show --only-show-errors -o json
if ($LASTEXITCODE -ne 0) { throw 'Azure account lookup failed' }
$account = ConvertFrom-Json -InputObject ($raw -join [Environment]::NewLine)
if ($account.name -ne 'Azure subscription 1' -or $account.state -ne 'Enabled') { throw 'Expected enabled Azure subscription 1' }
if (Test-Path -LiteralPath $SettingsPath) { throw 'Settings already exist. Preserve the DNS label; do not regenerate for an existing VM.' }
$directory = Split-Path -Parent $SettingsPath
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$dnsLabel = 'scorecopilot-' + [Guid]::NewGuid().ToString('N').Substring(0,10)
$settings = [ordered]@{
    subscriptionId = $account.id
    resourceGroup = 'rg-scorecopilot-demo-sec'
    location = 'swedencentral'
    dnsLabel = $dnsLabel
    adminIPv4 = $AdminIPv4
    publicKeyPath = (Resolve-Path -LiteralPath $PublicKeyPath).Path
}
$settings | ConvertTo-Json | Set-Content -LiteralPath $SettingsPath -Encoding UTF8
$parameters = [ordered]@{
    '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters = [ordered]@{
        location = @{ value = 'swedencentral' }
        adminCidr = @{ value = "$AdminIPv4/32" }
        sshPublicKey = @{ value = $publicKey }
        dnsLabel = @{ value = $dnsLabel }
    }
}
$parameters | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $directory 'parameters.json') -Encoding UTF8
Write-Host 'Local settings and ARM parameters saved under var/azure. No Azure resources created.'
