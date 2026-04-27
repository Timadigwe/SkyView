# One GitHub OIDC provider per AWS account. IAM role for GitHub Actions: terraform apply + ECR + App Runner + S3.
# Set github_repository in tfvars, then: terraform apply (or -target=aws_iam_openid_connect_provider.github first).
locals {
  tf_state_bucket = coalesce(
    var.terraform_state_bucket_name != "" ? var.terraform_state_bucket_name : null,
    "${var.project_name}-terraform-state-${data.aws_caller_identity.current.account_id}"
  )
}

# Register GitHub as an identity provider (required once per account).
resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
  client_id_list = [
    "sts.amazonaws.com",
  ]
  # AWS / GitHub recommended thumbprints (intermediate CAs for token.actions.githubusercontent.com)
  thumbprint_list = [
    "6938fd4d98bab03aaadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
  tags = local.common_tags
}

data "aws_iam_policy_document" "github_actions_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = var.github_actions_role_name
  description        = "GitHub Actions OIDC: SkyView deploy"
  assume_role_policy = data.aws_iam_policy_document.github_actions_trust.json
  tags               = local.common_tags
}

# Permissions needed for deploy.sh: state S3, app S3, ECR, App Runner, IAM, Logs.
# Narrow further in production if required.
data "aws_iam_policy_document" "github_actions_deploy" {
  # Terraform remote state
  statement {
    sid     = "TfStateBucket"
    effect  = "Allow"
    actions = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [
      "arn:aws:s3:::${local.tf_state_bucket}",
    ]
  }
  statement {
    sid    = "TfStateObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [
      "arn:aws:s3:::${local.tf_state_bucket}/*",
    ]
  }

  # App buckets (chat memory, frontend, ECR get login uses ECR not S3 for image — include broad project prefix on S3)
  statement {
    sid     = "SkyviewS3"
    effect  = "Allow"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::${local.name_prefix}-*",
      "arn:aws:s3:::${local.name_prefix}-*/*",
      "arn:aws:s3:::${var.project_name}-terraform-state-*",
      "arn:aws:s3:::${var.project_name}-terraform-state-*/*",
    ]
  }

  statement {
    sid     = "SkyviewECR"
    effect  = "Allow"
    actions = ["ecr:*"]
    resources = ["*"]
  }

  statement {
    sid     = "SkyviewAppRunner"
    effect  = "Allow"
    actions = ["apprunner:*"]
    resources = ["*"]
  }

  # Terraform refresh/plan needs read APIs on existing roles/policies, not only create/delete.
  statement {
    sid    = "SkyviewIAM"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PassRole",
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:ListRolePolicies",
      "iam:GetRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:TagPolicy",
      "iam:UntagPolicy",
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider",
      "iam:ListOpenIDConnectProviders",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "SkyviewLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "SkyviewSTS"
    effect  = "Allow"
    actions = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

# Optional: DynamoDB state lock (if you use dynamodb_table in terraform backend)
data "aws_iam_policy_document" "github_actions_lock" {
  count = var.terraform_lock_table_name != "" ? 1 : 0
  statement {
    sid    = "DynamoStateLock"
    effect = "Allow"
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = [
      "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.terraform_lock_table_name}",
    ]
  }
}

data "aws_iam_policy_document" "github_actions_merged" {
  source_policy_documents = var.terraform_lock_table_name != "" ? [
    data.aws_iam_policy_document.github_actions_deploy.json,
    data.aws_iam_policy_document.github_actions_lock[0].json,
    ] : [
    data.aws_iam_policy_document.github_actions_deploy.json,
  ]
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "skyview-github-deploy"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_merged.json
}

# Managed policy: full IAM read (Get*, List*) so `terraform plan` refresh never 403s on
# GetPolicyVersion, ListRolePolicies, etc. The inline policy above can drift; this does not.
resource "aws_iam_role_policy_attachment" "github_iam_readonly" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/IAMReadOnlyAccess"
}
