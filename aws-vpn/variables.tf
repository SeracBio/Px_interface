variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "eu-north-1"
}

variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "vpn-project"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Must NOT overlap Ridgeline's 10.x ranges or the VPN client pool (10.0.14.0/24)."
  type        = string
  default     = "172.20.0.0/16"
}

variable "public_subnet_cidr" {
  description = "CIDR block for the public subnet"
  type        = string
  default     = "172.20.1.0/24"
}

variable "private_subnet_cidr" {
  description = "CIDR block for the private subnet"
  type        = string
  default     = "172.20.2.0/24"
}

variable "availability_zone" {
  description = "Availability zone for subnets"
  type        = string
  default     = "eu-north-1a"
}

# Customer Gateway (your on-premises/remote VPN device)
variable "customer_gateway_ip" {
  description = "Public IP address of your on-premises VPN device"
  type        = string
  # Replace with your actual public IP
  default     = "203.0.113.1"
}

variable "customer_gateway_bgp_asn" {
  description = "BGP ASN for the customer gateway (use 65000 for static routing)"
  type        = number
  default     = 65000
}

# On-premises networks routed over the VPN (static routing).
# Includes the office LAN and the FortiClient remote-user address pool.
variable "on_premises_cidr" {
  description = "List of on-premises/remote CIDRs to route over the VPN tunnel"
  type        = list(string)
  default     = ["192.168.146.0/24", "10.0.14.0/24"]
}

variable "vpn_routing_type" {
  description = "VPN routing type: 'static' or 'dynamic' (BGP)"
  type        = string
  default     = "static"

  validation {
    condition     = contains(["static", "dynamic"], var.vpn_routing_type)
    error_message = "vpn_routing_type must be 'static' or 'dynamic'."
  }
}

# EC2
variable "ec2_instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "ec2_ami_id" {
  description = "AMI ID for the EC2 instance (leave empty to use latest Amazon Linux 2023)"
  type        = string
  default     = ""
}

variable "key_pair_name" {
  description = "Name of an existing EC2 key pair for SSH access (leave empty to skip)"
  type        = string
  default     = ""
}

variable "allowed_cidr" {
  description = "List of CIDRs allowed to reach the EC2 instance over the VPN (HTTP, HTTPS, ICMP)"
  type        = list(string)
  default     = ["192.168.146.0/24", "10.0.14.0/24"]
}

variable "webapp_htpasswd_hash" {
  description = <<-EOT
    bcrypt password hash for HTTP basic auth (generated externally, e.g. via htpasswd -nbB).
    Store the real value in 1Password and inject it at apply time:
      TF_VAR_webapp_htpasswd_hash='username:$$2y$$...' terraform apply
    The username is included in the hash string (htpasswd format: "user:hash").
  EOT
  type        = string
  sensitive   = true
  default     = "serac_user:$2y$10$REPLACEME_RUN_htpasswd_nbB_serac_user_yourpassword"
}

# TLS certificate
variable "tls_cert_org" {
  description = "Organisation name to embed in the self-signed TLS certificate"
  type        = string
  default     = "My Company"
}

variable "tls_cert_validity_days" {
  description = "Number of days the self-signed certificate is valid"
  type        = number
  default     = 825 # ~2 years; browsers reject > 825 days
}
