variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "skyview"
  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.project_name))
    error_message = "Project name must contain only lowercase letters, numbers, and hyphens."
  }
}

variable "aws_region" {
  description = "AWS region for all resources (e.g. us-west-2)"
  type        = string
  default     = "us-west-2"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "openrouter_api_key" {
  description = "OpenRouter API key (set via TF_VAR_openrouter_api_key or a non-committed tfvars file)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "openrouter_model" {
  description = "OpenRouter model id, e.g. openai/gpt-4o"
  type        = string
  default     = "openai/gpt-4o"
}

variable "solana_rpc_url" {
  description = "Solana RPC URL, e.g https://api.devnet.solana.com"
  type        = string
  default     = "https://api.devnet.solana.com"
}

variable "openrouter_referer" {
  description = "OpenRouter HTTP-Referer header"
  type        = string
  default     = "https://localhost"
}

variable "openrouter_title" {
  description = "OpenRouter X-Title header"
  type        = string
  default     = "Skyview"
}

variable "app_runner_auto_deploy" {
  type        = bool
  default     = false
  description = "Redeploy App Runner automatically when ECR :latest updates"
}

variable "create_app_runner" {
  type        = bool
  default     = false
  description = "Phase 2 switch: set true after pushing an ECR :latest image"
}

variable "github_repository" {
  description = "GitHub repo for OIDC (owner/repo). Used by github-oidc.tf; must match the repo that runs the workflow."
  type        = string
  default     = "Timadigwe/SkyView"
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/repo (letters, numbers, -, ., _)."
  }
}

variable "github_actions_role_name" {
  description = "IAM role name for GitHub Actions; set repository secret AWS_ROLE_ARN to this role's ARN"
  type        = string
  default     = "github-actions-skyview"
}

variable "terraform_state_bucket_name" {
  description = "S3 bucket for remote state. Empty uses {project_name}-terraform-state-{account_id} in IAM policy."
  type        = string
  default     = ""
}

variable "terraform_lock_table_name" {
  description = "DynamoDB table for state locking. Empty omits lock permissions from the GitHub deploy policy."
  type        = string
  default     = ""
}
