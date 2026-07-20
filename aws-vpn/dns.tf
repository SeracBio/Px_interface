# -------------------------------------------------------
# Internal DNS: a Route 53 PRIVATE hosted zone for the friendly name, resolvable only over the
# VPN via a Route 53 Resolver INBOUND endpoint. On-prem DNS (FortiGate) forwards the zone to the
# endpoint IPs (conditional forwarder — IT action, see outputs). Cost: ~$90/mo for the endpoint.
#
# The zone is scoped to just `advantedge.seracbio.com` (NOT all of seracbio.com) so it does not
# shadow the public domain for VPC/on-prem clients.
# -------------------------------------------------------

# Second private subnet in a 2nd AZ — the inbound resolver endpoint needs IPs in 2 AZs for HA.
resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_b_cidr
  availability_zone = var.availability_zone_b

  tags = {
    Name    = "${var.project_name}-private-subnet-b"
    Project = var.project_name
    Tier    = "private"
  }
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}

# SG for the resolver endpoint: accept DNS from on-prem/VPN only.
resource "aws_security_group" "resolver" {
  name        = "${var.project_name}-resolver-sg"
  description = "Allow DNS (53) from on-premises/VPN to the inbound resolver endpoint"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "DNS UDP from on-premises/VPN"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = var.on_premises_cidr
  }
  ingress {
    description = "DNS TCP from on-premises/VPN"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = var.on_premises_cidr
  }
  egress {
    description = "Resolve within the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name    = "${var.project_name}-resolver-sg"
    Project = var.project_name
  }
}

# Inbound resolver endpoint — on-prem forwards the zone here; IPs auto-assigned in the 2 AZs.
resource "aws_route53_resolver_endpoint" "inbound" {
  name               = "${var.project_name}-inbound"
  direction          = "INBOUND"
  security_group_ids = [aws_security_group.resolver.id]

  ip_address {
    subnet_id = aws_subnet.private.id
  }
  ip_address {
    subnet_id = aws_subnet.private_b.id
  }

  tags = {
    Name    = "${var.project_name}-inbound-resolver"
    Project = var.project_name
  }
}

# Private hosted zone for the friendly name, attached to the VPC.
resource "aws_route53_zone" "advantedge" {
  name    = var.webapp_hostname
  comment = "Private zone: ${var.webapp_hostname} -> internal Px interface (VPN only)"

  vpc {
    vpc_id = aws_vpc.main.id
  }
}

# A record at the zone apex -> the EC2's fixed private IP.
resource "aws_route53_record" "advantedge" {
  zone_id = aws_route53_zone.advantedge.zone_id
  name    = var.webapp_hostname
  type    = "A"
  ttl     = 300
  records = [var.ec2_private_ip]
}

# -------------------------------------------------------
# Outputs — hand these to IT to set up the on-prem conditional forwarder.
# -------------------------------------------------------
output "resolver_inbound_ips" {
  description = "Inbound resolver endpoint IPs; point the FortiGate/on-prem conditional forwarder for the webapp hostname at these"
  value       = [for a in aws_route53_resolver_endpoint.inbound.ip_address : a.ip]
}

output "webapp_url" {
  description = "Friendly URL once DNS forwarding is live (VPN only)"
  value       = "https://${var.webapp_hostname}/Px_interface/"
}
