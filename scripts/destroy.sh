#!/usr/bin/env bash
set -euo pipefail

# Destroy SkyView resources. Cleans S3 buckets first so destroy won't fail on non-empty buckets.
# Usage:
#   ./scripts/destroy.sh [dev|staging|prod]
#
# Env:
#   AWS_REGION / AWS_DEFAULT_REGION (defaults to us-west-2)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVIRONMENT="${1:-dev}"
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
REGION="$AWS_REGION"

cd "$ROOT/terraform"
INIT_CMD=(terraform init -input=false)
if [[ -n "${TF_STATE_BUCKET:-}" ]]; then
  INIT_CMD+=(
    -backend-config="bucket=${TF_STATE_BUCKET}"
    -backend-config="key=skyview/${ENVIRONMENT}/terraform.tfstate"
    -backend-config="region=${TF_STATE_REGION:-${AWS_REGION}}"
    -backend-config="encrypt=true"
  )
fi
"${INIT_CMD[@]}"

TF_VARS=(
  -var="environment=${ENVIRONMENT}"
  -var="aws_region=${REGION}"
  -var="create_app_runner=true"
)

echo "==> Empty S3 buckets (ignore if not created yet)"
FRONTEND_BUCKET="$(terraform output -raw s3_frontend_bucket 2>/dev/null || true)"
MEM_BUCKET="$(terraform output -raw s3_chat_memory_bucket 2>/dev/null || true)"
if [[ -n "$FRONTEND_BUCKET" ]]; then
  aws s3 rm "s3://${FRONTEND_BUCKET}" --recursive --region "$REGION" || true
fi
if [[ -n "$MEM_BUCKET" ]]; then
  aws s3 rm "s3://${MEM_BUCKET}" --recursive --region "$REGION" || true
fi

echo "==> Terraform destroy"
terraform destroy -auto-approve "${TF_VARS[@]}"
