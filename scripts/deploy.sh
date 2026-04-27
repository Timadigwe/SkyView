#!/usr/bin/env bash
set -euo pipefail

# Order matches ai_financial_advisor/guides/4_researcher.md: if App Runner is not in state,
# first apply _without_ it (ECR+S3+IAM via -target), push :latest, then full apply. Otherwise
# one full apply, then image + frontend.
# Usage: ./scripts/deploy.sh [dev|staging|...]
# Env: AWS creds, DEFAULT_AWS_REGION, TF_STATE_BUCKET, TF_VAR_openrouter_api_key / .env
#      SKIP_TERRAFORM=1 — skip terraform (image + site only; App Runner must exist)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ENV="${1:-dev}"
export AWS_REGION="${DEFAULT_AWS_REGION:-${AWS_REGION:-us-west-2}}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
if [[ -n "${OPENROUTER_API_KEY:-}" && -z "${TF_VAR_openrouter_api_key:-}" ]]; then
  export TF_VAR_openrouter_api_key="$OPENROUTER_API_KEY"
fi

cd "$ROOT/terraform"
INIT_CMD=(terraform init -input=false)
if [[ -n "${TF_STATE_BUCKET:-}" ]]; then
  INIT_CMD+=(
    -backend-config="bucket=${TF_STATE_BUCKET}"
    -backend-config="key=skyview/${ENV}/terraform.tfstate"
    -backend-config="region=${TF_STATE_REGION:-${AWS_REGION}}"
    -backend-config="encrypt=true"
  )
fi
"${INIT_CMD[@]}"

apprunner_in_state() {
  terraform state list 2>/dev/null | grep -qE '^aws_apprunner_service\.api$'
}

RUN_FULL_APPLY_AFTER_IMAGE=false
if [[ "${SKIP_TERRAFORM:-0}" != "1" ]]; then
  if apprunner_in_state; then
    terraform apply -auto-approve \
      -var="environment=$ENV" \
      -var="aws_region=$AWS_REGION" \
      -var="create_app_runner=true"
  else
    # Everything in main.tf except App Runner; then push, then second apply creates the service
    terraform apply -auto-approve \
      -var="environment=$ENV" \
      -var="aws_region=$AWS_REGION" \
      -target=aws_s3_bucket.chat_memory \
      -target=aws_s3_bucket_public_access_block.chat_memory \
      -target=aws_s3_bucket_ownership_controls.chat_memory \
      -target=aws_iam_policy.chat_memory_s3 \
      -target=aws_ecr_repository.api \
      -target=aws_iam_role.app_runner_role \
      -target=aws_iam_role_policy_attachment.app_runner_ecr_access \
      -target=aws_iam_role.app_runner_instance_role \
      -target=aws_iam_role_policy_attachment.app_runner_instance_chat_memory \
      -target=aws_s3_bucket.frontend \
      -target=aws_s3_bucket_public_access_block.frontend \
      -target=aws_s3_bucket_ownership_controls.frontend \
      -target=aws_s3_bucket_website_configuration.frontend \
      -target=aws_s3_bucket_policy.frontend
    RUN_FULL_APPLY_AFTER_IMAGE=true
  fi
else
  echo "SKIP_TERRAFORM=1: not running terraform apply"
fi

ECR_URL=$(terraform output -raw ecr_repository_url)
BUCKET=$(terraform output -raw s3_frontend_bucket)
FRONTEND_HTTP=$(terraform output -raw s3_frontend_website_url)
REGION="$AWS_REGION"
cd "$ROOT"
ECR_HOST="${ECR_URL%%/*}"
echo "==> ECR login ($ECR_HOST)"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_HOST"
echo "==> docker buildx (linux/amd64) + push :latest"
docker buildx build --platform linux/amd64 \
  -f backend/Dockerfile \
  -t "${ECR_URL}:latest" \
  --push \
  .

if [[ "${SKIP_TERRAFORM:-0}" != "1" && "$RUN_FULL_APPLY_AFTER_IMAGE" == true ]]; then
  echo "==> Full terraform apply (create App Runner; image is in ECR)"
  cd "$ROOT/terraform"
  terraform apply -auto-approve \
    -var="environment=$ENV" \
    -var="aws_region=$AWS_REGION" \
    -var="create_app_runner=true"
  cd "$ROOT"
fi

cd "$ROOT/terraform"
API_HTTPS=$(terraform output -raw app_runner_service_url)
SVC_ARN=$(terraform output -raw app_runner_service_arn)
cd "$ROOT"

if [[ -z "$API_HTTPS" || -z "$SVC_ARN" ]]; then
  echo "app_runner URL or service ARN is empty" >&2
  exit 1
fi
echo "==> App Runner: start deployment"
aws apprunner start-deployment --region "$REGION" --service-arn "$SVC_ARN" 2>/dev/null || {
  echo "start-deployment: re-run or enable app_runner_auto_deploy in tfvars"
}
cd "$ROOT/frontend"
export NEXT_PUBLIC_API_URL="${API_HTTPS}"
echo "==> npm run build (NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL)"
npm ci
npm run build
echo "==> s3 sync → $BUCKET"
aws s3 sync out/ "s3://${BUCKET}/" --delete --region "$REGION"
echo ""
echo "================ DEPLOY ================"
echo "Frontend (S3 website):  $FRONTEND_HTTP"
echo "API (App Runner HTTPS): $API_HTTPS"
echo "========================================"
