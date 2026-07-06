# -------------------------------------------------------
# Security Group for VPC Interface Endpoints
# Allows the private subnet to reach AWS service APIs
# over the endpoint ENIs on port 443.
# -------------------------------------------------------
resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.project_name}-vpce-sg"
  description = "Allow HTTPS from within the VPC to interface endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-vpce-sg"
    Project = var.project_name
  }
}

# -------------------------------------------------------
# Interface Endpoints — SSM, SSMMessages, EC2Messages
#
# These three together enable:
#   - SSM Parameter Store access (ssm)
#   - SSM Session Manager shell sessions (ssmmessages + ec2messages)
#
# private_dns_enabled = true means the standard AWS service
# hostnames resolve to the endpoint IPs inside the VPC,
# so no code changes are needed in user_data or the app.
# -------------------------------------------------------
resource "aws_vpc_endpoint" "ssm" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssm"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name    = "${var.project_name}-vpce-ssm"
    Project = var.project_name
  }
}

resource "aws_vpc_endpoint" "ssmmessages" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssmmessages"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name    = "${var.project_name}-vpce-ssmmessages"
    Project = var.project_name
  }
}

resource "aws_vpc_endpoint" "ec2messages" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ec2messages"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name    = "${var.project_name}-vpce-ec2messages"
    Project = var.project_name
  }
}

# -------------------------------------------------------
# Gateway Endpoint — S3
#
# Amazon Linux 2023 dnf pulls packages from S3-backed
# repos. A gateway endpoint is free and routes that
# traffic inside AWS without leaving the VPC.
# Associated with the private route table only.
# -------------------------------------------------------
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name    = "${var.project_name}-vpce-s3"
    Project = var.project_name
  }
}
