# aws-vpn owns the VPC and the VPN gateway. This stack only reads them, thus a destroy here cannot remove them.
data "aws_vpc" "main" {
  cidr_block = var.vpc_cidr
}

data "aws_vpn_gateway" "main" {
  attached_vpc_id = data.aws_vpc.main.id
}
