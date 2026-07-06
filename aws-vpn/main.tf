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
  # Remote backend — encrypted S3 state + DynamoDB locking
  #
  # BEFORE running terraform init here, run the bootstrap:
  #   cd bootstrap && terraform init && terraform apply
  #
  # Then fill in the bucket name from bootstrap's output.
  # -------------------------------------------------------
  backend "s3" {
    bucket         = "vpn-project-tf-state-620423424620"
    key            = "vpn/terraform.tfstate"
    region         = "eu-north-1"
    encrypt        = true
    dynamodb_table = "vpn-project-tf-lock"
  }
}

provider "aws" {
  region = var.aws_region
}
