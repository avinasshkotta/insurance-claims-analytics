param(
    [Parameter(Mandatory = $true)]
    [string]$Space,                      # e.g. avinasshkotta/claims-console
    [string]$Message = "Deploy claims console"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$tmp = Join-Path $env:TEMP ("hf-space-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

Write-Host "Cloning space $Space into $tmp"
git clone "https://huggingface.co/spaces/$Space" $tmp
if (-not (Test-Path (Join-Path $tmp ".git"))) { throw "git clone failed" }

# Only what the image needs. Never copy local secrets or runtime artifacts.
$items = @("Dockerfile", "requirements.txt", "pyproject.toml", "src", "prompts", "eval", "models", "data")
foreach ($item in $items) {
    $source = Join-Path $root $item
    if (Test-Path $source) {
        Copy-Item -Recurse -Force $source (Join-Path $tmp $item)
    } else {
        Write-Warning "missing $item - check the Dockerfile"
    }
}

# Frontend sources only: node_modules and dist are build outputs.
robocopy (Join-Path $root "frontend") (Join-Path $tmp "frontend") /E /XD node_modules dist /NFL /NDL /NJH /NJS | Out-Null

Copy-Item -Force (Join-Path $PSScriptRoot "README.md") (Join-Path $tmp "README.md")

Remove-Item (Join-Path $tmp ".env") -Force -ErrorAction SilentlyContinue
foreach ($artifact in @("data\uploads", "data\runs.jsonl", "data\llm_usage.jsonl", "data\case_queue.json")) {
    Remove-Item (Join-Path $tmp $artifact) -Recurse -Force -ErrorAction SilentlyContinue
}

git -C $tmp add -A
git -C $tmp -c user.name="deploy" -c user.email="deploy@local" commit -m $Message
git -C $tmp push

Write-Host ""
Write-Host "Pushed. Build at https://huggingface.co/spaces/$Space"
Write-Host "Then set the Space variables listed in DEPLOY.md."
