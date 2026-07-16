# -------------------------------------------------------
# Bootstrap — run ONCE before the main configuration.
# Creates the S3 bucket and DynamoDB table used by the
# remote backend. Uses local state intentionally (this
# tiny stack has no secrets worth protecting in state).
#
# Usage:
#   cd bootstrap
#   terraform init
#   terraform apply
# -------------------------------------------------------

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# -------------------------------------------------------
# S3 bucket for Terraform state
# -------------------------------------------------------
resource "aws_s3_bucket" "tf_state" {
  bucket = var.state_bucket_name

  # Prevent accidental deletion of the bucket (and all state inside)
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

# Block all public access — state must never be public
resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# State holds the webapp TLS private key and the VPN tunnel PSKs, so the bucket is
# defended in depth: TLS-only, same-account-only, and an OPTIONAL principal allow-list.
resource "aws_s3_bucket_policy" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "DenyNonTLS"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:*"
          Resource  = [aws_s3_bucket.tf_state.arn, "${aws_s3_bucket.tf_state.arn}/*"]
          Condition = { Bool = { "aws:SecureTransport" = "false" } }
        },
        {
          # Only principals in THIS account may touch the state (blocks any cross-account
          # or anonymous access even if an IAM/ACL mistake is made elsewhere).
          Sid       = "DenyOutsideAccount"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:*"
          Resource  = [aws_s3_bucket.tf_state.arn, "${aws_s3_bucket.tf_state.arn}/*"]
          Condition = {
            StringNotEquals = { "aws:PrincipalAccount" = data.aws_caller_identity.current.account_id }
          }
        }
      ],
      # Optional hard allow-list: when set, every OTHER same-account principal is denied.
      # The account root is auto-appended so a wrong ARN can never lock the account out.
      length(var.allowed_state_principals) > 0 ? [
        {
          Sid       = "RestrictToAllowedPrincipals"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:*"
          Resource  = [aws_s3_bucket.tf_state.arn, "${aws_s3_bucket.tf_state.arn}/*"]
          Condition = {
            StringNotLike = {
              "aws:PrincipalArn" = concat(
                var.allowed_state_principals,
                ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
              )
            }
          }
        }
      ] : []
    )
  })
}

# -------------------------------------------------------
# DynamoDB table for state locking
# -------------------------------------------------------
resource "aws_dynamodb_table" "tf_lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  # Protect the lock table from accidental deletion too
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform-bootstrap"
  }
}

# -------------------------------------------------------
# Outputs — paste these into main config's backend block
# -------------------------------------------------------
output "state_bucket_name" {
  description = "S3 bucket name to use in the backend config"
  value       = aws_s3_bucket.tf_state.id
}

output "lock_table_name" {
  description = "DynamoDB table name to use in the backend config"
  value       = aws_dynamodb_table.tf_lock.name
}

output "aws_region" {
  description = "Region to use in the backend config"
  value       = var.aws_region
}
