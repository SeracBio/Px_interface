# -------------------------------------------------------
# S3 bucket for the rendered interface artifacts.
#
# Upload flow (no public access, no SSH):
#   workstation  --aws s3 sync-->  this bucket  --S3 gateway endpoint-->  EC2  --> /var/www/webapp
#
# The bucket name is computed from the account ID (globally unique) so no
# account-specific literal is committed to git.
# -------------------------------------------------------
data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "interface" {
  bucket = "${var.project_name}-interface-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name    = "${var.project_name}-interface"
    Project = var.project_name
  }
}

resource "aws_s3_bucket_versioning" "interface" {
  bucket = aws_s3_bucket.interface.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 (AES256) — encrypts at rest with no extra KMS-decrypt grant needed on the
# instance role. Sufficient for the (synthetic) interface artifacts in a private bucket.
resource "aws_s3_bucket_server_side_encryption_configuration" "interface" {
  bucket = aws_s3_bucket.interface.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "interface" {
  bucket                  = aws_s3_bucket.interface.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# TLS-only, same-account-only — matches the state-bucket posture.
resource "aws_s3_bucket_policy" "interface" {
  bucket = aws_s3_bucket.interface.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.interface.arn, "${aws_s3_bucket.interface.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "DenyOutsideAccount"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.interface.arn, "${aws_s3_bucket.interface.arn}/*"]
        Condition = {
          StringNotEquals = { "aws:PrincipalAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

# Let the EC2 instance role PULL (read) objects from this bucket via the S3 gateway endpoint.
resource "aws_iam_role_policy" "s3_interface_read" {
  name = "${var.project_name}-s3-interface-read"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.interface.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.interface.arn]
      }
    ]
  })
}

output "interface_bucket" {
  description = "S3 bucket holding the rendered interface artifacts"
  value       = aws_s3_bucket.interface.id
}
