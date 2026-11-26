# Deploying to AWS — Lambda container + Function URL (≈ $0 idle)

> **If this fails with `ecr:CreateRepository ... explicit deny in a service control policy`,
> your account is governed by an AWS Organizations SCP and this path is blocked.** Container
> Lambdas require a private ECR repository, and Lambda zip packages are capped at 250 MB
> unzipped — this app's dependencies exceed that. Use `../local-demo/` instead; it gives you
> an HTTPS URL with no AWS account involved.

Why Lambda and not the obvious choices:

| Option | Idle cost | Catch |
|---|---|---|
| **Lambda + Function URL** | **$0** | cold starts; `/tmp` state is per-container |
| App Runner | ~$50/mo at 1 vCPU / 2 GB | no scale-to-zero; you pay while it runs |
| ECS Fargate + ALB | ~$25–35/mo | the ALB alone is ~$18/mo |
| ECS Fargate + scale-to-zero | $0 idle | needs API Gateway/ALB anyway to be reachable; more moving parts |
| EC2 t4g.small | ~$12/mo | you own the OS, TLS and patching |

Lambda's always-free allowance (1,000,000 requests and 400,000 GB-seconds per month) is not
time-limited. At **2048 MB** that is ~200,000 seconds ≈ **55 hours of execution per month**.
A portfolio demo uses a small fraction of that. The only recurring charge is the ECR image
(~$0.10/GB-month); delete the repository when you are done and it is exactly $0.

## Prerequisites

```bash
docker info                     # Docker Desktop must be running
aws sts get-caller-identity     # AWS CLI configured, with permissions to create
                                # ECR repos, IAM roles and Lambda functions
```

## Deploy

```powershell
# PowerShell
$env:OPENAI_API_KEY = "sk-..."
.\deploy\aws\deploy.ps1
```

```bash
# Git Bash / WSL
OPENAI_API_KEY=sk-... ./deploy/aws/deploy.sh
```

Options: `-Region ap-south-1` (default: Mumbai), `-MemoryMb 2048`, `-TimeoutSec 60`,
`-MaxConcurrency 2`, `-BaseUrl https://...` for a non-OpenAI compatible endpoint.

The script creates, idempotently:

1. an ECR repository and pushes the image (`--platform linux/amd64`);
2. an execution role `${Function}-lambda` with `AWSLambdaBasicExecutionRole`;
3. the Lambda function (container image) with demo env vars;
4. **reserved concurrency = 2** — the cost circuit-breaker;
5. a public Function URL (auth `NONE`).

It prints the URL. Re-running it updates code, configuration and image.

## What gets set

| Variable | Value |
|---|---|
| `DEMO_MODE` | `1` |
| `DEMO_RPM` | `30` (higher than Cloud Run: Function URLs may not always surface a per-client IP) |
| `DEMO_DAILY_USD` | `2` |
| `DEMO_ALLOW_UPLOADS` | `0` |
| `MAX_OUTPUT_TOKENS` | `400` |
| `WRITE_DIR` | `/tmp/data` (Lambda's `/var/task` is read-only) |
| `OPENAI_API_KEY` | your key — visible to anyone with `lambda:GetFunctionConfiguration`, so **rotate it after demo week** or move it to Secrets Manager |

## Verify

```powershell
$u = "https://<id>.lambda-url.ap-south-1.on.aws/"
curl.exe -s "$($u)api/health"
curl.exe -s "$($u)api/metrics/summary"
curl.exe -s -o NUL -w "%{http_code}`n" "$($u)dashboard"                  # 200
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "$($u)api/uploads"        # 403
```

First request after idle takes **5–15 s** (image start + sklearn/pandas imports). Open the
URL once before sharing it.

## Known demo limitations (state these before an interviewer finds them)

- **Cold start 5–15 s.** Fine for a portfolio link, not for an SLO.
- **`/tmp` state is per container.** The review queue, token ledger and run traces reset on
  cold start and are not shared between concurrent containers. The dashboard therefore shows
  numbers from the container that served the request.
- **Uploads are disabled** in demo mode by design (PII, and the 6 MB Function URL payload cap).
- **60 s timeout.** A slow upstream model call can hit it; raise `-TimeoutSec` if needed.
- **No WAF in front.** The app's per-IP limit and spend cap are the only guards.

## Cost guardrails

- `max-instances` equivalent: `put-function-concurrency --reserved-concurrent-executions 2`
- `DEMO_DAILY_USD` blocks paid calls once the ledger crosses the cap — but remember the
  ledger is per container, so the **provider-side spend limit is the real protection**.
- Set a Billing budget alert at $1 before you start.

## Teardown (back to exactly $0)

```bash
aws lambda delete-function-url-config --function-name claims-console --region ap-south-1
aws lambda delete-function --function-name claims-console --region ap-south-1
aws ecr delete-repository --repository-name claims-console --force --region ap-south-1
aws iam detach-role-policy --role-name claims-console-lambda \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name claims-console-lambda
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Read-only file system` in logs | `WRITE_DIR` missing — it must be set to `/tmp/data` |
| `502` / `Task timed out` | cold start or a slow model call; raise memory (faster imports) and timeout |
| `exec format error` | image built without `--platform linux/amd64` |
| `403` on `/api/uploads` | expected in demo mode |
| `429` on the first visit | the Function URL may look like one client; raise `DEMO_RPM` |
| Dashboard blank, `/api/metrics/summary` 500 | check `eval/golden_set.json` is in the image |
