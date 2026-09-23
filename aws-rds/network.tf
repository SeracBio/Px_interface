# Two new subnets, one in each AZ. Their route table has no route to the internet.
resource "aws_subnet" "db" {
  for_each = var.db_subnets

  vpc_id                  = data.aws_vpc.main.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = false

  tags = { Name = "${var.project_name}-db-${each.key}" }
}

resource "aws_route_table" "db" {
  vpc_id = data.aws_vpc.main.id
  tags   = { Name = "${var.project_name}-db-rt" }
}

resource "aws_route_table_association" "db" {
  for_each = aws_subnet.db

  subnet_id      = each.value.id
  route_table_id = aws_route_table.db.id
}

# The VPN routes send the replies back to the on-premises clients.
resource "aws_vpn_gateway_route_propagation" "db" {
  vpn_gateway_id = data.aws_vpn_gateway.main.id
  route_table_id = aws_route_table.db.id
}

# No egress rule: Terraform removes the AWS default, and the group lets replies out (stateful).
resource "aws_security_group" "db" {
  name        = "${var.project_name}-sg"
  description = "PostgreSQL from on-premises over the VPN only"
  vpc_id      = data.aws_vpc.main.id

  ingress {
    description = "PostgreSQL from on-premises over the VPN"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr
  }

  tags = { Name = "${var.project_name}-sg" }
}

resource "aws_db_subnet_group" "db" {
  name       = "${var.project_name}-subnets"
  subnet_ids = [for s in aws_subnet.db : s.id]
}
