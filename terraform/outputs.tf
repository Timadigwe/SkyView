output "s3_frontend_bucket" {
  description = "Name of the S3 bucket for frontend"
  value       = aws_s3_bucket.frontend.id
}

output "s3_frontend_website_url" {
  description = "HTTP URL for the static frontend (S3 website hosting)"
  value       = "http://${aws_s3_bucket_website_configuration.frontend.website_endpoint}"
}

output "s3_chat_memory_bucket" {
  description = "Private S3 bucket used by the backend to persist chat sessions"
  value       = aws_s3_bucket.chat_memory.id
}

output "s3_chat_memory_bucket_arn" {
  value = aws_s3_bucket.chat_memory.arn
}

output "chat_memory_iam_policy_arn" {
  value       = aws_iam_policy.chat_memory_s3.arn
  description = "Attached to App Runner instance role"
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.api.repository_url
  description = "Push your backend image here with tag :latest"
}

output "app_runner_service_url" {
  value       = length(aws_apprunner_service.api) > 0 ? "https://${aws_apprunner_service.api[0].service_url}" : ""
  description = "Empty until create_app_runner=true and applied"
}

output "app_runner_service_arn" {
  value       = length(aws_apprunner_service.api) > 0 ? aws_apprunner_service.api[0].arn : ""
  description = "Empty until create_app_runner=true and applied"
}

output "github_actions_role_arn" {
  description = "Set GitHub secret AWS_ROLE_ARN to this value for OIDC deploys"
  value       = aws_iam_role.github_actions.arn
}

output "setup_instructions" {
  description = "Two-phase order (alex/4_researcher): apply -> push :latest -> apply with create_app_runner=true"
  value       = <<-EOT
    Phase 1 (infra, no App Runner):
      terraform apply -auto-approve -var="create_app_runner=false"

    Push image (from repo root):
      docker buildx build --platform linux/amd64 -f backend/Dockerfile -t $(terraform output -raw ecr_repository_url):latest --push .

    Phase 2 (create App Runner):
      terraform apply -auto-approve -var="create_app_runner=true"

    Frontend:
      NEXT_PUBLIC_API_URL=$(terraform output -raw app_runner_service_url) npm run build (frontend) then sync to s3://$(terraform output -raw s3_frontend_bucket)/
  EOT
}
