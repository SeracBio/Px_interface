terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # -------------------------------------------------------
  # Remote backend — encrypted S3 state + DynamoDB locking.
  #
  # BEFORE init: run the bootstrap once (cd bootstrap && terraform init && terraform apply).
  #
  # `bucket` and `dynamodb_table` are supplied at init time via -backend-config so the
  # account-id-bearing bucket name stays OUT of version control:
  #   cp backend.hcl.example backend.hcl   # fill in real names from `terraform -chdir=bootstrap output`
  #   terraform init -backend-config=backend.hcl
  # (backend.hcl is gitignored.)
  # -------------------------------------------------------
  backend "s3" {
    key     = "vpn/terraform.tfstate"
    region  = "eu-north-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region
}
