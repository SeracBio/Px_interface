variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-north-1"
}

variable "project_name" {
  description = "Project name used for tagging"
  type        = string
  default     = "vpn-project"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for Terraform state (must be globally unique across all AWS accounts)"
  type        = string
  # Suggested pattern: <project>-tf-state-<account-id>
  # e.g. "vpn-project-tf-state-123456789012"
}

variable "lock_table_name" {
  description = "DynamoDB table name for Terraform state locking"
  type        = string
  default     = "vpn-project-tf-lock"
}
