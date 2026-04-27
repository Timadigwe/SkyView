# SkyView Terraform (modeled after alex/terraform/4_researcher):
# - Phase 1: create ECR + IAM + S3 buckets/policies
# - Push image to ECR (:latest)
# - Phase 2: set create_app_runner=true and apply to create App Runner service

data "aws_caller_identity" "current" {}

locals {
  name_prefix  = "${var.project_name}-${var.environment}"
  service_name = replace("${local.name_prefix}-api", "_", "-")
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# -----------------------------------------------------------------------------
# S3 memory (private)
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "chat_memory" {
  bucket = "${local.name_prefix}-chat-memory-${data.aws_caller_identity.current.account_id}"
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "chat_memory" {
  bucket = aws_s3_bucket.chat_memory.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "chat_memory" {
  bucket = aws_s3_bucket.chat_memory.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_iam_policy" "chat_memory_s3" {
  # Fixed name so it matches a policy you import and avoids a destroy/recreate
  # when the same policy name already exists in the account.
  name = "${local.name_prefix}-chat-memory-s3"
  tags = local.common_tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadWriteSessionObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = "${aws_s3_bucket.chat_memory.arn}/*"
      },
      {
        Sid      = "ListMemoryBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.chat_memory.arn
      },
    ]
  })
}

# -----------------------------------------------------------------------------
# ECR + App Runner
# -----------------------------------------------------------------------------

resource "aws_ecr_repository" "api" {
  name                 = local.service_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration { scan_on_push = true }
  tags = local.common_tags
}

resource "aws_iam_role" "app_runner_role" {
  name = replace("${local.name_prefix}-apprunner-ecr", "_", "-")
  tags = local.common_tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "build.apprunner.amazonaws.com" }
      },
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "tasks.apprunner.amazonaws.com" }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "app_runner_ecr_access" {
  role       = aws_iam_role.app_runner_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_iam_role" "app_runner_instance_role" {
  name = replace("${local.name_prefix}-apprunner-inst", "_", "-")
  tags = local.common_tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "tasks.apprunner.amazonaws.com" }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "app_runner_instance_chat_memory" {
  role       = aws_iam_role.app_runner_instance_role.name
  policy_arn = aws_iam_policy.chat_memory_s3.arn
}

resource "aws_apprunner_service" "api" {
  count        = var.create_app_runner ? 1 : 0
  service_name = local.service_name
  tags         = local.common_tags

  source_configuration {
    auto_deployments_enabled = var.app_runner_auto_deploy
    authentication_configuration {
      access_role_arn = aws_iam_role.app_runner_role.arn
    }
    image_repository {
      image_identifier = "${aws_ecr_repository.api.repository_url}:latest"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          AWS_REGION            = var.aws_region
          USE_S3                = "true"
          S3_BUCKET             = aws_s3_bucket.chat_memory.id
          PERSIST_CONVERSATIONS = "true"
          SOLANA_RPC_URL        = var.solana_rpc_url
          OPENROUTER_API_KEY    = var.openrouter_api_key
          OPENROUTER_MODEL      = var.openrouter_model
          OPENROUTER_REFERER    = var.openrouter_referer
          OPENROUTER_TITLE      = var.openrouter_title
        }
      }
      image_repository_type = "ECR"
    }
  }

  instance_configuration {
    cpu               = "1 vCPU"
    memory            = "2 GB"
    instance_role_arn = aws_iam_role.app_runner_instance_role.arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/api/health"
    interval            = 10
    timeout             = 10
    healthy_threshold   = 1
    unhealthy_threshold = 10
  }

  depends_on = [
    aws_iam_role_policy_attachment.app_runner_ecr_access,
    aws_iam_role_policy_attachment.app_runner_instance_chat_memory,
  ]
}

# -----------------------------------------------------------------------------
# Static frontend S3 website (public)
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "frontend" {
  bucket = "${local.name_prefix}-frontend-${data.aws_caller_identity.current.account_id}"
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  index_document { suffix = "index.html" }
  error_document { key = "404.html" }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.frontend.arn}/*"
      },
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.frontend]
}
