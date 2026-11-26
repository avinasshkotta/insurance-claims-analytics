param(
    [switch]$SkipDns,
    [int]$WaitSeconds = 180
)

# Start the public demo: update DuckDNS, build, run, wait for HTTPS.
#   .\start.ps1
#   .\start.ps1 -SkipDns        # when the A record is already correct
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    throw "Missing .env. Copy .env.example to .env and fill in DOMAIN + DUCKDNS_TOKEN."
}

$lines = Get-Content ".env"
$domain = (($lines | Where-Object { $_ -match '^DOMAIN=' } | Select-Object -First 1) -replace '^DOMAIN=', '').Trim()
if (-not $domain) { throw "DOMAIN is missing from .env" }

if (-not $SkipDns) {
    & (Join-Path $PSScriptRoot "duckdns-update.ps1")
    if ($LASTEXITCODE -ne 0) { throw "DuckDNS update failed" }
}

Write-Host "==> docker compose up (build may take a few minutes the first time)"
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }

Write-Host "==> waiting for https://$domain/api/health (certificate issuance can take ~60s)"
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ok = $false
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod "https://$domain/api/health" -TimeoutSec 8
        if ($health.ok -eq $true) { $ok = $true; break }
    } catch {
        Start-Sleep -Seconds 5
    }
}

if (-not $ok) {
    Write-Host ""
    Write-Host "Not reachable over HTTPS yet. Most common causes:" -ForegroundColor Yellow
    Write-Host "  1. ports 80/443 are not forwarded to this machine"
    Write-Host "  2. Windows Firewall is blocking 80/443"
    Write-Host "  3. the ISP uses CGNAT (no public IPv4) - use the cloudflared fallback"
    Write-Host ""
    Write-Host "Caddy log (certificate errors appear here):"
    docker compose logs caddy --tail 25
    exit 1
}

Write-Host ""
Write-Host "Live:      https://$domain/"
Write-Host "Dashboard: https://$domain/dashboard"
Write-Host ""
Write-Host "Stop with: docker compose down"
