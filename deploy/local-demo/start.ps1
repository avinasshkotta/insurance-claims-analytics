param(
    [switch]$SkipDns,
    [int]$WaitSeconds = 180
)

# Start both public demos: update DuckDNS, bring the stack up, wait for HTTPS.
#   .\start.ps1
#   .\start.ps1 -SkipDns        # when the A record is already correct
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    throw "Missing .env. Copy .env.example to .env and fill in DOMAIN + DUCKDNS_TOKEN."
}

$lines = Get-Content ".env"
function Get-EnvValue([string]$name) {
    $line = $lines | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($line) { return (($line -replace "^$name=", "").Trim()) } else { return "" }
}

$domain = Get-EnvValue "DOMAIN"
$quench = Get-EnvValue "QUENCH_DOMAIN"
$caddyfile = Get-EnvValue "CADDYFILE"
if (-not $domain) { throw "DOMAIN is missing from .env" }

if (-not $SkipDns) {
    & (Join-Path $PSScriptRoot "duckdns-update.ps1")
    if ($LASTEXITCODE -ne 0) { throw "DuckDNS update failed" }
}

Write-Host "==> docker compose up"
docker compose up -d
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
    Write-Host "  3. the ISP uses CGNAT (no public IPv4)"
    Write-Host ""
    Write-Host "Run .\preflight.ps1 - it tells you which one it is."
    Write-Host ""
    Write-Host "Caddy log:"
    docker compose logs caddy --tail 25
    exit 1
}

Write-Host ""
Write-Host "Claims:    https://$domain/" -ForegroundColor Green
Write-Host "Dashboard: https://$domain/dashboard" -ForegroundColor Green

if ($quench -and $caddyfile -eq "Caddyfile.both") {
    Write-Host ""
    Write-Host "==> checking QuenchGuard at https://$quench/ (401 is success: password required)"
    try {
        $r = Invoke-WebRequest "https://$quench/" -UseBasicParsing -TimeoutSec 15 -MaximumRedirection 0
        Write-Host "QuenchGuard: HTTP $($r.StatusCode)" -ForegroundColor Green
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -eq 401) {
            Write-Host "QuenchGuard: HTTP 401 (protected, certificate valid)" -ForegroundColor Green
        } else {
            Write-Host "QuenchGuard: failed ($code $($_.Exception.Message))" -ForegroundColor Red
            Write-Host "  docker compose logs caddy --tail 20" -ForegroundColor Yellow
        }
    }
    Write-Host "  username: demo   password: see .credentials.txt"
}

Write-Host ""
Write-Host "Stop with: docker compose down"
