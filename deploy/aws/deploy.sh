#!/usr/bin/env bash
# Deploy the claims console to AWS Lambda (container image + public Function URL).
#   OPENAI_API_KEY=sk-... ./deploy/aws/deploy.sh
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
FUNCTION="${FUNCTION:-claims-console}"
REPO="${REPO:-claims-console}"
MEMORY="${MEMORY:-2048}"
TIMEOUT="${TIMEOUT:-60}"
MAX_CONCURRENCY="${MAX_CONCURRENCY:-2}"
: "${OPENAI_API_KEY:?set OPENAI_API_KEY}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:latest"

echo "==> ECR ${REPO} (${REGION})"
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 ||
  aws ecr create-repository --repository-name "$REPO" --region "$REGION" \
    --image-scanning-configuration scanOnPush=true >/dev/null

echo "==> build + push ${IMAGE}"
aws ecr get-login-password --region "$REGION" |
  docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
docker build --platform linux/amd64 -f Dockerfile.lambda -t "$IMAGE" .
docker push "$IMAGE"

echo "==> execution role"
ROLE_NAME="${FUNCTION}-lambda"
if ! ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text 2>/dev/null)"; then
  TRUST="$(mktemp)"
  cat >"$TRUST" <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
  ROLE_ARN="$(aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://${TRUST}" --query Role.Arn --output text)"
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole >/dev/null
  sleep 10
fi

ENV_VARS="Variables={DEMO_MODE=1,DEMO_RPM=30,DEMO_DAILY_USD=2,DEMO_ALLOW_UPLOADS=0,MAX_OUTPUT_TOKENS=400,LLM_MODEL=gpt-4o-mini,WRITE_DIR=/tmp/data,OPENAI_API_KEY=${OPENAI_API_KEY}"
if [ -n "${OPENAI_BASE_URL:-}" ]; then ENV_VARS="${ENV_VARS},OPENAI_BASE_URL=${OPENAI_BASE_URL}"; fi
ENV_VARS="${ENV_VARS}}"

echo "==> lambda ${FUNCTION}"
if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION" --image-uri "$IMAGE" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
    --memory-size "$MEMORY" --timeout "$TIMEOUT" --environment "$ENV_VARS" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
else
  aws lambda create-function --function-name "$FUNCTION" --region "$REGION" \
    --package-type Image --code "ImageUri=${IMAGE}" --role "$ROLE_ARN" \
    --architectures x86_64 --memory-size "$MEMORY" --timeout "$TIMEOUT" \
    --environment "$ENV_VARS" >/dev/null
  aws lambda wait function-active --function-name "$FUNCTION" --region "$REGION"
fi

echo "==> reserved concurrency ${MAX_CONCURRENCY} (cost cap)"
aws lambda put-function-concurrency --function-name "$FUNCTION" \
  --reserved-concurrent-executions "$MAX_CONCURRENCY" --region "$REGION" >/dev/null

echo "==> public Function URL"
if ! aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  aws lambda create-function-url-config --function-name "$FUNCTION" --region "$REGION" \
    --auth-type NONE --cors 'AllowOrigins=*,AllowMethods=*,AllowHeaders=*' >/dev/null
  aws lambda add-permission --function-name "$FUNCTION" --region "$REGION" \
    --statement-id FunctionURLAllowPublicAccess --action lambda:InvokeFunctionUrl \
    --principal "*" --function-url-auth-type NONE >/dev/null 2>&1 || true
fi

URL="$(aws lambda get-function-url-config --function-name "$FUNCTION" --region "$REGION" --query FunctionUrl --output text)"
echo
echo "Deployed: ${URL}"
echo "Dashboard: ${URL}dashboard"
