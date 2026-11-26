param(
    [Parameter(Mandatory = $true)]
    [string]$Url
)

# Post-deploy smoke test for the public demo.
#   .\scripts\verify_demo.ps1 https://abc123.lambda-url.ap-south-1.on.aws/
$ErrorActionPreference = "Stop"
$Url = $Url.TrimEnd("/") + "/"
$failures = 0

function Check {
    param([string]$Name, [scriptblock]$Test)
    try {
        $result = & $Test
        if ($result) { Write-Host "  PASS  $Name" -ForegroundColor Green }
        else { Write-Host "  FAIL  $Name" -ForegroundColor Red; $script:failures++ }
    } catch {
        Write-Host "  FAIL  $Name -> $($_.Exception.Message)" -ForegroundColor Red
        $script:failures++
    }
}

Write-Host "Verifying $Url"

Check "health returns ok" {
    $h = Invoke-RestMethod "$($Url)api/health"
    $h.ok -eq $true
}

Check "demo guards are on" {
    $h = Invoke-RestMethod "$($Url)api/health"
    $h.demo.enabled -eq $true
}

Check "metrics summary has a pass rate" {
    $m = Invoke-RestMethod "$($Url)api/metrics/summary"
    $null -ne $m.headline.golden_set_pass_rate
}

Check "dashboard serves HTML" {
    $d = Invoke-WebRequest "$($Url)dashboard" -UseBasicParsing
    $d.StatusCode -eq 200 -and $d.Content -match "Claims Triage Console"
}

Check "uploads are blocked (403)" {
    try {
        Invoke-WebRequest "$($Url)api/uploads" -Method Post -UseBasicParsing -Body @{} | Out-Null
        $false
    } catch {
        $_.Exception.Response.StatusCode.value__ -eq 403
    }
}

Write-Host ""
if ($failures -eq 0) {
    Write-Host "All checks passed. Paste the URL into README.md (Live demo line)." -ForegroundColor Green
    exit 0
}
Write-Host "$failures check(s) failed." -ForegroundColor Red
exit 1
