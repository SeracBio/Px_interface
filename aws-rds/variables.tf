variable "aws_region" {
  description = "Region of the existing VPN VPC"
  type        = string
  default     = "eu-north-1"
}

variable "project_name" {
  description = "Name prefix for every resource in this stack. It is also the RDS instance identifier."
  type        = string
  default     = "px-rds"
}

variable "vpc_cidr" {
  description = "CIDR of the existing VPN VPC. This stack uses it to find the VPC and never creates a VPC."
  type        = string
  default     = "172.20.0.0/16"
}

variable "db_subnets" {
  description = "AZ to CIDR of the database subnets. A DB subnet group needs two AZs. aws-vpn uses 172.20.2-3 and Signals reserves 172.20.4-5."
  type        = map(string)
  default = {
    "eu-north-1a" = "172.20.6.0/24"
    "eu-north-1b" = "172.20.7.0/24"
  }
}

variable "allowed_cidr" {
  description = "On-premises CIDRs that can connect on 5432 over the VPN: the office LAN and the FortiClient pool"
  type        = list(string)
  default     = ["192.168.146.0/24", "10.0.14.0/24"]
}

variable "engine_version" {
  description = "PostgreSQL major version. RDS applies the minor upgrades."
  type        = string
  default     = "18"

  validation {
    condition     = can(regex("^[0-9]+$", var.engine_version))
    error_message = "engine_version must be a major version only, for example 18."
  }
}

variable "instance_class" {
  description = "DB instance class. A change here modifies the instance in place; it does not replace it."
  type        = string
  default     = "db.m7g.large"
}

variable "allocated_storage" {
  description = "gp3 storage in GB"
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Name of the database that RDS creates"
  type        = string
  default     = "px"
}

variable "master_username" {
  description = "Master user. RDS keeps its password in Secrets Manager."
  type        = string
  default     = "px_admin"
}

variable "backup_retention_days" {
  description = "Days of automated backups"
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Set to false and apply before you destroy the database"
  type        = bool
  default     = true
}

# --- restore.tf: a copy of an existing database into this stack's VPN networking ---

variable "restore_snapshot_identifier" {
  description = "Snapshot to restore. Empty means restore.tf creates nothing."
  type        = string
  default     = ""
}

variable "restore_identifier" {
  description = "Name of the restored instance. RDS needs lowercase."
  type        = string
  default     = "px-seracbio-prod"

  validation {
    condition     = var.restore_identifier == lower(var.restore_identifier)
    error_message = "restore_identifier must be lowercase."
  }
}

variable "restore_allocated_storage" {
  description = "Storage in GB. It must be equal to or larger than the snapshot."
  type        = number
  default     = 600
}

variable "restore_storage_type" {
  description = "gp3 costs less than the source io2. Use io2 only if you need its IOPS."
  type        = string
  default     = "gp3"
}

variable "restore_iops" {
  description = "Provisioned IOPS. It applies to io1 and io2 only."
  type        = number
  default     = 3000
}

variable "restore_multi_az" {
  description = "Multi-AZ, as the source is. It doubles the cost and it gives an automatic failover."
  type        = bool
  default     = true
}

variable "restore_kms_key_id" {
  description = "KMS key of the restored volume. Empty keeps the key of the snapshot."
  type        = string
  default     = ""
}

variable "restore_deletion_protection" {
  description = "Protection for the restored copy only. It is separate from the placeholder flag."
  type        = bool
  default     = true
}
