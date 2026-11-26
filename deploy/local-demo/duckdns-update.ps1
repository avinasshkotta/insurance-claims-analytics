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

try {
    $publicIp = (Invoke-RestMethod "https://api.ipify.org?format=json" -TimeoutSec 15).ip
} catch {
    $publicIp = ""
}

$sub = $Domain -replace '\.duckdns\.org$', ''

# No token: only acceptable if the record already points here, so a missing token
# cannot silently leave a stale A record behind.
if (-not $Token) {
    $current = ""
    try {
        $current = (Resolve-DnsName $Domain -Type A -ErrorAction Stop |
            Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
    } catch { }
    if ($current -and $publicIp -and $current -eq $publicIp) {
        Write-Host "DuckDNS already points at $publicIp; no token needed for this run."
        exit 0
    }
    throw "DUCKDNS_TOKEN is missing and $Domain points at '$current' (public IP is '$publicIp'). Paste the token into .env."
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
