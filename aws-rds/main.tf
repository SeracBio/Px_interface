terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Same bucket and lock table as aws-vpn, but a separate state key.
  #   cp ../aws-vpn/backend.hcl . && terraform init -backend-config=backend.hcl
  backend "s3" {
    key     = "rds/terraform.tfstate"
    region  = "eu-north-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project = var.project_name
      Stack   = "px-rds"
    }
  }
}
