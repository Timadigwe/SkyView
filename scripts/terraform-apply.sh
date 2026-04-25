#!/usr/bin/env bash
# Full terraform apply. On a brand-new state this can create App Runner before ECR has :latest — use
# ./scripts/deploy.sh (or apply ECR+S3+IAM with -target=… from deploy.sh) first, then apply again.
# For full image + site, use ./scripts/deploy.sh
# Remote state: TF_STATE_BUCKET, AWS_REGION. Keys: TF_VAR_openrouter_api_key, etc.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  [[ -n "${OPENROUTER_API_KEY:-}" && -z "${TF_VAR_openrouter_api_key:-}" ]] && export TF_VAR_openrouter_api_key="$OPENROUTER_API_KEY"
fi
cd "$ROOT/terraform"
ENV="${1:-dev}"
export AWS_REGION="${AWS_REGION:-us-west-2}"

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

terraform apply \
  -var="environment=$ENV" \
  -var="aws_region=$AWS_REGION" \
  -auto-approve

terraform output
