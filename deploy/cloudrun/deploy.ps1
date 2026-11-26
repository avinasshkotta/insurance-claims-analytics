param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "asia-south1",
    [string]$Service = "claims-console",
    [string]$ApiKey = $env:OPENAI_API_KEY,
    [int]$MaxInstances = 2,
    [int]$Memory = 1
)

# Deploys the claims console to Cloud Run from source (Cloud Build + the Dockerfile).
#   .\deploy.ps1 -ProjectId my-project -ApiKey sk-...
#
# Prerequisites: gcloud installed, `gcloud auth login` done, billing enabled on the project.
$ErrorActionPreference = "Continue"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repo

$gcloud = (Get-Command gcloud -ErrorAction SilentlyContinue)
if ($gcloud) { $gcloudExe = $gcloud.Source }
else {
    $candidate = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path $candidate) { $gcloudExe = $candidate }
    else { throw "gcloud not found. Install the Cloud SDK or add it to PATH." }
}

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $out = & $gcloudExe @Args 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw ("gcloud {0} failed (exit {1})`n{2}" -f ($Args -join ' '), $code, ($out -join "`n"))
    }
    return (($out -join "`n").Trim())
}

if (-not $ApiKey) { throw "Pass -ApiKey or set OPENAI_API_KEY." }

Write-Host "==> account"
$account = Invoke-Gcloud auth list --filter=status:ACTIVE --format="value(account)"
if (-not $account) { throw "Not authenticated. Run: gcloud auth login" }
Write-Host "    $account"

Write-Host "==> project $ProjectId ($Region)"
Invoke-Gcloud config set project $ProjectId | Out-Null

Write-Host "==> enabling APIs (first run takes ~2 min)"
Invoke-Gcloud services enable run.googleapis.com cloudbuild.googleapis.com `
    artifactregistry.googleapis.com secretmanager.googleapis.com --project $ProjectId | Out-Null

Write-Host "==> API key secret"
$exists = $false
try {
    Invoke-Gcloud secrets describe openai-key --project $ProjectId | Out-Null
    $exists = $true
} catch { $exists = $false }

if (-not $exists) {
    $tmp = Join-Path $env:TEMP "openai-key.txt"
    [System.IO.File]::WriteAllText($tmp, $ApiKey)
    Invoke-Gcloud secrets create openai-key --data-file=$tmp --replication-policy=automatic --project $ProjectId | Out-Null
    Remove-Item $tmp -Force
    Write-Host "    created"
} else {
    Write-Host "    exists (left unchanged)"
}

$projectNumber = Invoke-Gcloud projects describe $ProjectId --format="value(projectNumber)"
$runtimeSa = "$projectNumber-compute@developer.gserviceaccount.com"

Write-Host "==> granting the Cloud Run runtime service account access to the secret"
Invoke-Gcloud secrets add-iam-policy-binding openai-key `
    --member="serviceAccount:$runtimeSa" `
    --role="roles/secretmanager.secretAccessor" `
    --project $ProjectId | Out-Null
Write-Host "    $runtimeSa"

Write-Host "==> deploying (first build ~5-8 min)"
Invoke-Gcloud run deploy $Service `
    --source . `
    --project $ProjectId `
    --region $Region `
    --allow-unauthenticated `
    --memory "${Memory}Gi" `
    --cpu 1 `
    --min-instances 0 `
    --max-instances $MaxInstances `
    --concurrency 20 `
    --timeout 300 `
    --set-env-vars "DEMO_MODE=1,DEMO_RPM=20,DEMO_DAILY_USD=1,DEMO_ALLOW_UPLOADS=0,MAX_OUTPUT_TOKENS=500,LLM_MODEL=gpt-4o-mini" `
    --set-secrets "OPENAI_API_KEY=openai-key:latest" | Out-Null

$url = Invoke-Gcloud run services describe $Service --project $ProjectId --region $Region --format="value(status.url)"
Write-Host ""
Write-Host "Deployed: $url" -ForegroundColor Green
Write-Host "Dashboard: $url/dashboard" -ForegroundColor Green
Write-Host ""
Write-Host "Verify:  .\scripts\verify_demo.ps1 $url/"
