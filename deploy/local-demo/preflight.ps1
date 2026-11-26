# Checks every layer the public demo depends on, in dependency order.
#   .\preflight.ps1
#
# It answers one question: if I start Caddy now, will the certificate succeed?
# That matters because Let's Encrypt allows only 5 failed validations per hour.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$problems = @()
function Ok($msg)   { Write-Host "  PASS  $msg" -ForegroundColor Green }
function Bad($msg, $fix) {
    Write-Host "  FAIL  $msg" -ForegroundColor Red
    if ($fix) { Write-Host "        -> $fix" -ForegroundColor Yellow }
    $script:problems += $msg
}
function Warn($msg) { Write-Host "  WARN  $msg" -ForegroundColor Yellow }

$lines = if (Test-Path ".env") { Get-Content ".env" } else { @() }
function Get-EnvValue([string]$name) {
    $line = $lines | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($line) { return (($line -replace "^$name=", "").Trim()) } else { return "" }
}
$domain = Get-EnvValue "DOMAIN"
$quench = Get-EnvValue "QUENCH_DOMAIN"
$caddyfile = Get-EnvValue "CADDYFILE"

Write-Host ""
Write-Host "1. Docker" -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -eq 0) { Ok "daemon running" } else { Bad "Docker daemon is not running" "start Docker Desktop" }

Write-Host ""
Write-Host "2. Containers" -ForegroundColor Cyan
foreach ($pair in @(@{ Name = "local-demo-app-1"; Label = "claims app" },
                    @{ Name = "quenchguard"; Label = "QuenchGuard" })) {
    $status = (docker inspect --format "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}" $pair.Name 2>$null)
    if ($status -match "^running") {
        if ($status -match "healthy" -or $status.Trim() -eq "running") { Ok "$($pair.Label) ($status)" }
        else { Warn "$($pair.Label) ($status)" }
    } else {
        Bad "$($pair.Label) is not running ($status)" "docker compose up -d"
    }
}

Write-Host ""
Write-Host "3. DuckDNS" -ForegroundColor Cyan
try { $publicIp = (Invoke-RestMethod "https://api.ipify.org?format=json" -TimeoutSec 15).ip }
catch { $publicIp = ""; Bad "could not determine public IP" "check the internet connection" }

$domains = @()
if ($domain) { $domains += $domain }
if ($quench -and $caddyfile -eq "Caddyfile.both") { $domains += $quench }
if ($domains.Count -eq 0) { Bad "no DOMAIN in .env" "copy .env.example to .env" }

foreach ($d in $domains) {
    try {
        $resolved = (Resolve-DnsName $d -Type A -ErrorAction Stop |
            Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
    } catch { $resolved = "" }

    if ($resolved -and $publicIp -and $resolved -eq $publicIp) {
        Ok "$d -> $resolved"
    } elseif ($resolved) {
        Bad "$d -> $resolved but public IP is $publicIp" ".\duckdns-update.ps1 (needs DUCKDNS_TOKEN in .env)"
    } else {
        Bad "$d does not resolve" "create the subdomain on duckdns.org, then .\duckdns-update.ps1"
    }
}

Write-Host ""
Write-Host "4. Windows Firewall" -ForegroundColor Cyan
$rules = @(Get-NetFirewallRule -DisplayName "Claims demo*" -ErrorAction SilentlyContinue)
if ($rules.Count -ge 2) {
    Ok "inbound rules present: $($rules.Count)"
} else {
    Bad "inbound rules for 80/443 are missing" 'admin PowerShell: New-NetFirewallRule -DisplayName "Claims demo HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow   (and 443)'
}

Write-Host ""
Write-Host "5. Inbound reachability (probe, does not touch Let's Encrypt)" -ForegroundColor Cyan
if (-not $domain -or -not $publicIp) {
    Warn "skipped: no domain or public IP"
} else {
    # Caddy must not hold port 80 while we probe.
    docker compose stop caddy *> $null
    docker rm -f claims-portprobe *> $null

    $started = docker run -d --rm --name claims-portprobe -p 80:80 nginx:alpine 2>&1
    Start-Sleep -Seconds 5

    $local = $false
    try {
        $l = Invoke-WebRequest "http://127.0.0.1/" -UseBasicParsing -TimeoutSec 6
        $local = ($l.StatusCode -eq 200)
    } catch { }

    if (-not $local) {
        Bad "probe container did not serve on port 80 locally" "is something else using port 80? netstat -ano | findstr :80"
    } else {
        Ok "probe container serving locally on :80"

        $external = $false
        foreach ($proxyUrl in @("https://r.jina.ai/http://$domain/", "https://api.allorigins.win/raw?url=http%3A%2F%2F$domain%2F")) {
            try {
                $r = Invoke-WebRequest $proxyUrl -UseBasicParsing -TimeoutSec 45
                if ($r.Content -match "nginx|Welcome to nginx") { $external = $true; break }
            } catch { }
        }

        if ($external) {
            Ok "port 80 is reachable from the public internet"
            Write-Host "        (port 443 must be forwarded too for the certificate to matter)"
        } else {
            Bad "port 80 is NOT reachable from the internet" "forward TCP 80 and 443 on the router to this PC's LAN IP"
            Write-Host "        Firewall rule present but unreachable usually means: no port forward, or CGNAT." -ForegroundColor Yellow
        }
    }

    docker stop claims-portprobe *> $null
}

Write-Host ""
if ($problems.Count -eq 0) {
    Write-Host "Ready. Run .\start.ps1" -ForegroundColor Green
    exit 0
}
Write-Host "$($problems.Count) blocker(s). Fix the FAIL lines above, then run this again." -ForegroundColor Red
exit 1
