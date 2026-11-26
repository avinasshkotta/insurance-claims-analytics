param(
    [string]$Domain,
    [string]$Token
)

# Points a DuckDNS subdomain at this machine's current public IP.
#   .\duckdns-update.ps1
# Reads DOMAIN and DUCKDNS_TOKEN from the .env next to this script.
$ErrorActionPreference = "Continue"

$envPath = Join-Path $PSScriptRoot ".env"
if ((-not $Domain -or -not $Token) -and (Test-Path $envPath)) {
    $lines = Get-Content $envPath
    if (-not $Domain) {
        $Domain = ($lines | Where-Object { $_ -match '^DOMAIN=' } | Select-Object -First 1) -replace '^DOMAIN=', ''
    }
    if (-not $Token) {
        $Token = ($lines | Where-Object { $_ -match '^DUCKDNS_TOKEN=' } | Select-Object -First 1) -replace '^DUCKDNS_TOKEN=', ''
    }
}

$Domain = "$Domain".Trim()
$Token = "$Token".Trim()
if (-not $Domain) { throw "DOMAIN is missing. Create deploy/local-demo/.env from .env.example." }
if (-not $Token) { throw "DUCKDNS_TOKEN is missing. Get it from duckdns.org." }

$sub = $Domain -replace '\.duckdns\.org$', ''

try {
    $publicIp = (Invoke-RestMethod "https://api.ipify.org?format=json" -TimeoutSec 15).ip
} catch {
    $publicIp = "(unknown)"
}

$url = "https://www.duckdns.org/update?domains=$sub&token=$Token&ip="
try {
    $response = (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 20).Content.Trim()
} catch {
    throw "DuckDNS request failed: $($_.Exception.Message)"
}

if ($response -ne "OK") {
    throw "DuckDNS rejected the update: '$response'. Check the token and subdomain."
}

Write-Host "DuckDNS updated: $Domain -> $publicIp"
Write-Host "If your public IP changes (router restart, ISP lease), run this again."
