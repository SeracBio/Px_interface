# -------------------------------------------------------
# Virtual Private Gateway (AWS side of the VPN)
# -------------------------------------------------------
resource "aws_vpn_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name    = "${var.project_name}-vgw"
    Project = var.project_name
  }
}

# -------------------------------------------------------
# Customer Gateway (represents your on-premises VPN device)
# -------------------------------------------------------
resource "aws_customer_gateway" "main" {
  bgp_asn    = var.customer_gateway_bgp_asn
  ip_address = var.customer_gateway_ip
  type       = "ipsec.1"

  tags = {
    Name    = "${var.project_name}-cgw"
    Project = var.project_name
  }
}

# -------------------------------------------------------
# Site-to-Site VPN Connection
# AWS creates two tunnels automatically for redundancy.
# -------------------------------------------------------
resource "aws_vpn_connection" "main" {
  vpn_gateway_id      = aws_vpn_gateway.main.id
  customer_gateway_id = aws_customer_gateway.main.id
  type                = "ipsec.1"

  # Set to true for static routing, false to use BGP (dynamic)
  static_routes_only = var.vpn_routing_type == "static"

  # Tunnel 1 options (optional — AWS auto-generates PSKs if omitted)
  tunnel1_ike_versions                 = ["ikev2"]
  tunnel1_phase1_encryption_algorithms = ["AES256"]
  tunnel1_phase1_integrity_algorithms  = ["SHA2-256"]
  tunnel1_phase1_dh_group_numbers      = [14]
  tunnel1_phase2_encryption_algorithms = ["AES256"]
  tunnel1_phase2_integrity_algorithms  = ["SHA2-256"]
  tunnel1_phase2_dh_group_numbers      = [14]

  # Tunnel 2 options (mirrors tunnel 1)
  tunnel2_ike_versions                 = ["ikev2"]
  tunnel2_phase1_encryption_algorithms = ["AES256"]
  tunnel2_phase1_integrity_algorithms  = ["SHA2-256"]
  tunnel2_phase1_dh_group_numbers      = [14]
  tunnel2_phase2_encryption_algorithms = ["AES256"]
  tunnel2_phase2_integrity_algorithms  = ["SHA2-256"]
  tunnel2_phase2_dh_group_numbers      = [14]

  tags = {
    Name    = "${var.project_name}-vpn-connection"
    Project = var.project_name
  }
}

# -------------------------------------------------------
# Static routes to on-premises networks (only for static routing).
# One route per CIDR: office LAN + FortiClient remote-user pool.
# -------------------------------------------------------
resource "aws_vpn_connection_route" "on_premises" {
  for_each = var.vpn_routing_type == "static" ? toset(var.on_premises_cidr) : toset([])

  destination_cidr_block = each.value
  vpn_connection_id      = aws_vpn_connection.main.id
}
