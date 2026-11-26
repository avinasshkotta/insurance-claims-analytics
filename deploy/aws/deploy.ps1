param(
    [string]$Region    = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { "ap-south-1" }),
    [string]$Function  = "claims-console",
    [string]$Repo      = "claims-console",
    [string]$ApiKey    = $env:OPENAI_API_KEY,
    [string]$BaseUrl   = "",
    [int]$MemoryMb     = 2048,
    [int]$TimeoutSec   = 60,
    [int]$MaxConcurrency = 2
)

# Windows PowerShell 5.1 turns a native command's stderr into a terminating error when
# ErrorActionPreference is Stop. The AWS CLI writes normal "not found" probes to stderr,
# so this script runs in Continue mode and checks exit codes explicitly.
$ErrorActionPreference = "Continue"

function Invoke-Aws {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $lines = & aws @Args 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw ("aws {0} failed (exit {1})`n{2}" -f ($Args -join ' '), $code, ($lines -join "`n"))
    }
    return (($lines -join "`n").Trim())
}

function Test-Aws {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & aws @Args 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not $ApiKey) { throw "Set OPENAI_API_KEY in the environment or pass -ApiKey." }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is required." }

Write-Host "==> Account and region"
$Account = Invoke-Aws sts get-caller-identity --query Account --output text
$Registry = "$Account.dkr.ecr.$Region.amazonaws.com"
$Image = "$Registry/${Repo}:latest"
Write-Host "    account=$Account region=$Region"

Write-Host "==> ECR repository"
if (-not (Test-Aws ecr describe-repositories --repository-names $Repo --region $Region)) {
    Write-Host "    creating $Repo"
    Invoke-Aws ecr create-repository --repository-name $Repo --region $Region `
        --image-scanning-configuration scanOnPush=true | Out-Null
}

Write-Host "==> Docker login, build (linux/amd64), push"
$password = Invoke-Aws ecr get-login-password --region $Region
$password | docker login --username AWS --password-stdin $Registry | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker login failed" }

& docker build --platform linux/amd64 -f Dockerfile.lambda -t $Image .
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

& docker push $Image
if ($LASTEXITCODE -ne 0) { throw "docker push failed" }

Write-Host "==> Execution role"
$RoleName = "$Function-lambda"
if (Test-Aws iam get-role --role-name $RoleName) {
    $RoleArn = Invoke-Aws iam get-role --role-name $RoleName --query Role.Arn --output text
} else {
    $trust = Join-Path $env:TEMP "lambda-trust-$Function.json"
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' |
        Set-Content -Path $trust -Encoding ascii
    $RoleArn = Invoke-Aws iam create-role --role-name $RoleName `
        --assume-role-policy-document "file://$trust" --query Role.Arn --output text
    Invoke-Aws iam attach-role-policy --role-name $RoleName `
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole | Out-Null
    Write-Host "    role created; waiting 10s for IAM propagation"
    Start-Sleep -Seconds 10
}
Write-Host "    role=$RoleArn"

$envVars = "Variables={DEMO_MODE=1,DEMO_RPM=30,DEMO_DAILY_USD=2,DEMO_ALLOW_UPLOADS=0,MAX_OUTPUT_TOKENS=400,LLM_MODEL=gpt-4o-mini,WRITE_DIR=/tmp/data,OPENAI_API_KEY=$ApiKey"
if ($BaseUrl) { $envVars += ",OPENAI_BASE_URL=$BaseUrl" }
$envVars += "}"

Write-Host "==> Lambda function"
if (Test-Aws lambda get-function --function-name $Function --region $Region) {
    Write-Host "    exists: updating code and configuration"
    Invoke-Aws lambda update-function-code --function-name $Function `
        --image-uri $Image --region $Region | Out-Null
    Invoke-Aws lambda wait function-updated --function-name $Function --region $Region
    Invoke-Aws lambda update-function-configuration --function-name $Function --region $Region `
        --memory-size $MemoryMb --timeout $TimeoutSec --environment $envVars | Out-Null
    Invoke-Aws lambda wait function-updated --function-name $Function --region $Region
} else {
    Write-Host "    creating"
    Invoke-Aws lambda create-function --function-name $Function --region $Region `
        --package-type Image --code "ImageUri=$Image" --role $RoleArn `
        --architectures x86_64 --memory-size $MemoryMb --timeout $TimeoutSec `
        --environment $envVars | Out-Null
    Invoke-Aws lambda wait function-active --function-name $Function --region $Region
}

Write-Host "==> Cost guardrail: reserved concurrency $MaxConcurrency"
Invoke-Aws lambda put-function-concurrency --function-name $Function `
    --reserved-concurrent-executions $MaxConcurrency --region $Region | Out-Null

Write-Host "==> Public Function URL"
if (-not (Test-Aws lambda get-function-url-config --function-name $Function --region $Region)) {
    Invoke-Aws lambda create-function-url-config --function-name $Function --region $Region `
        --auth-type NONE --cors 'AllowOrigins=*,AllowMethods=*,AllowHeaders=*' | Out-Null
    Invoke-Aws lambda add-permission --function-name $Function --region $Region `
        --statement-id FunctionURLAllowPublicAccess --action lambda:InvokeFunctionUrl `
        --principal "*" --function-url-auth-type NONE | Out-Null
}

$Url = Invoke-Aws lambda get-function-url-config --function-name $Function `
    --region $Region --query FunctionUrl --output text
Write-Host ""
Write-Host "Deployed: $Url"
Write-Host "Dashboard: ${Url}dashboard"
Write-Host ""
Write-Host "Verify:"
Write-Host "  .\scripts\verify_demo.ps1 $Url"
