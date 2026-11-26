# Registers Windows scheduled tasks so the demo comes back after a reboot.
#   Right-click PowerShell -> Run as administrator, then:
#   .\install-autostart.ps1
#
# Removes with:
#   Unregister-ScheduledTask -TaskName "ClaimsDemo-Stack","ClaimsDemo-Dns" -Confirm:$false
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Continue"
$here = $PSScriptRoot

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName "ClaimsDemo-Stack", "ClaimsDemo-Dns" -Confirm:$false -ErrorAction Continue
    Write-Host "Removed ClaimsDemo-Stack and ClaimsDemo-Dns."
    exit 0
}

$boot = Join-Path $here "boot.ps1"
$dns = Join-Path $here "duckdns-update.ps1"
foreach ($f in @($boot, $dns)) {
    if (-not (Test-Path $f)) { throw "Missing $f" }
}

# 1) Start the stack at logon, after waiting for Docker.
$stackAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$boot`""
$stackTrigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "ClaimsDemo-Stack" -Action $stackAction `
    -Trigger $stackTrigger -Settings $settings -Force `
    -Description "Start the claims demo stack at logon" | Out-Null

# 2) Refresh the DuckDNS record so a changed public IP self-heals.
$dnsAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dns`""
$dnsTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "ClaimsDemo-Dns" -Action $dnsAction `
    -Trigger $dnsTrigger -Settings $settings -Force `
    -Description "Keep the DuckDNS record pointed at this machine" | Out-Null

Write-Host "Registered:"
Get-ScheduledTask -TaskName "ClaimsDemo-Stack", "ClaimsDemo-Dns" |
    Select-Object TaskName, State | Format-Table -AutoSize

Write-Host ""
Write-Host "One manual step remains: Docker Desktop -> Settings -> General ->"
Write-Host "  [x] Start Docker Desktop when you sign in"
Write-Host "(Your settings-store.json currently has AutoStart = false.)"
