# Runs at logon (and is safe to run by hand): waits for Docker, refreshes DNS,
# brings the stack up. Scheduled by install-autostart.ps1.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$log = Join-Path $PSScriptRoot "boot.log"
function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $log -Value $line
    Write-Host $line
}

Write-Log "boot: waiting for Docker"
$deadline = (Get-Date).AddSeconds(300)
$dockerUp = $false
while ((Get-Date) -lt $deadline) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerUp = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $dockerUp) {
    Write-Log "boot: Docker did not start within 5 minutes; giving up"
    exit 1
}
Write-Log "boot: Docker is up"

& (Join-Path $PSScriptRoot "duckdns-update.ps1") *>> $log
if ($LASTEXITCODE -ne 0) { Write-Log "boot: DuckDNS update failed (continuing)" }

docker compose up -d *>> $log
if ($LASTEXITCODE -ne 0) { Write-Log "boot: docker compose failed"; exit 1 }

Write-Log "boot: stack started"
