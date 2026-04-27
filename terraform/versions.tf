terraform {
  required_version = ">= 1.5.0"

  # State in S3: pass bucket/key/region on init (see deploy.sh / commands below). Omit init flags to use local state only if this block is removed.
  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}