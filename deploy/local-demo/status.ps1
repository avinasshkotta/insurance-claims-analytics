# Pre-interview health check. Run 5 minutes before a call.
#   .\status.ps1
param([int]$TimeoutSec = 8)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Get-EnvValue([string]$name) {
    if (-not (Test-Path ".env")) { return "" }
    $line = Get-Content ".env" | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($line) { return (($line -replace "^$name=", "").Trim()) } else { return "" }
}

$claims = Get-EnvValue "DOMAIN"
$quench = Get-EnvValue "QUENCH_DOMAIN"

try { $publicIp = (Invoke-RestMethod "https://api.ipify.org?format=json" -TimeoutSec 10).ip }
catch { $publicIp = ""; Write-Host "warning: could not determine public IP" -ForegroundColor Yellow }

Write-Host "Public IP: $publicIp"
Write-Host ""

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker daemon: NOT RUNNING (start Docker Desktop)" -ForegroundColor Red
} else {
    Write-Host "Docker daemon: running" -ForegroundColor Green
    docker ps --format "  {{.Names}}  {{.Status}}" 2>$null | Out-String | Write-Host
}

$targets = @()
if ($claims) { $targets += [pscustomobject]@{ Name = "claims"; Domain = $claims; Path = "/api/health" } }
if ($quench) { $targets += [pscustomobject]@{ Name = "quench"; Domain = $quench; Path = "/health" } }

foreach ($t in $targets) {
    $resolved = ""
    try {
        $resolved = (Resolve-DnsName $t.Domain -Type A -ErrorAction Stop |
            Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
    } catch { }

    $dnsOk = ($resolved -eq $publicIp -and $publicIp -ne "")
    $dnsText = if ($dnsOk) { "DNS -> $resolved (matches)" }
               elseif ($resolved) { "DNS -> $resolved (DIFFERS from public IP)" }
               else { "DNS -> does not resolve" }

    $httpText = "not checked"
    $httpOk = $false
    if ($dnsOk) {
        try {
            $r = Invoke-RestMethod "https://$($t.Domain)$($t.Path)" -TimeoutSec $TimeoutSec
            $httpOk = $true
            $httpText = "HTTPS 200"
        } catch {
            $code = $_.Exception.Response.StatusCode.value__
            $httpText = if ($code) { "HTTPS $code" } else { "HTTPS failed: $($_.Exception.Message)" }
        }
    }

    Write-Host ""
    if ($dnsOk -and $httpOk) {
        Write-Host "$($t.Name): UP   https://$($t.Domain)/" -ForegroundColor Green
    } else {
        Write-Host "$($t.Name): DOWN https://$($t.Domain)/" -ForegroundColor Red
    }
    Write-Host "  $dnsText"
    Write-Host "  $httpText"

    if ($dnsOk -and -not $httpOk) {
        Write-Host "  checks: ports 80/443 forwarded? firewall rules? 'docker compose ps'?" -ForegroundColor Yellow
        Write-Host "  logs:   docker compose logs --tail 20 caddy app" -ForegroundColor Yellow
    }
    if (-not $dnsOk -and $publicIp) {
        Write-Host "  fix:    .\duckdns-update.ps1   (your public IP changed)" -ForegroundColor Yellow
    }
}
