# -------------------------------------------------------
# VPC & Networking
# -------------------------------------------------------
output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "private_subnet_id" {
  description = "ID of the private subnet"
  value       = aws_subnet.private.id
}

output "public_subnet_id" {
  description = "ID of the public subnet"
  value       = aws_subnet.public.id
}

# -------------------------------------------------------
# VPN Gateway & Customer Gateway
# -------------------------------------------------------
output "vpn_gateway_id" {
  description = "ID of the Virtual Private Gateway"
  value       = aws_vpn_gateway.main.id
}

output "customer_gateway_id" {
  description = "ID of the Customer Gateway"
  value       = aws_customer_gateway.main.id
}

output "vpn_connection_id" {
  description = "ID of the Site-to-Site VPN connection"
  value       = aws_vpn_connection.main.id
}

# -------------------------------------------------------
# Tunnel endpoints — use these to configure your VPN device
# -------------------------------------------------------
output "tunnel1_address" {
  description = "Public IP of AWS VPN tunnel 1 endpoint"
  value       = aws_vpn_connection.main.tunnel1_address
}

output "tunnel1_cgw_inside_address" {
  description = "Inside IP address of the customer gateway for tunnel 1"
  value       = aws_vpn_connection.main.tunnel1_cgw_inside_address
}

output "tunnel1_vgw_inside_address" {
  description = "Inside IP address of the virtual private gateway for tunnel 1"
  value       = aws_vpn_connection.main.tunnel1_vgw_inside_address
}

output "tunnel2_address" {
  description = "Public IP of AWS VPN tunnel 2 endpoint"
  value       = aws_vpn_connection.main.tunnel2_address
}

output "tunnel2_cgw_inside_address" {
  description = "Inside IP address of the customer gateway for tunnel 2"
  value       = aws_vpn_connection.main.tunnel2_cgw_inside_address
}

output "tunnel2_vgw_inside_address" {
  description = "Inside IP address of the virtual private gateway for tunnel 2"
  value       = aws_vpn_connection.main.tunnel2_vgw_inside_address
}

# PSKs are sensitive — Terraform marks them as such automatically
output "tunnel1_preshared_key" {
  description = "Pre-shared key for tunnel 1 (sensitive)"
  value       = aws_vpn_connection.main.tunnel1_preshared_key
  sensitive   = true
}

output "tunnel2_preshared_key" {
  description = "Pre-shared key for tunnel 2 (sensitive)"
  value       = aws_vpn_connection.main.tunnel2_preshared_key
  sensitive   = true
}

# -------------------------------------------------------
# EC2 Instance
# -------------------------------------------------------
output "ec2_instance_id" {
  description = "ID of the EC2 instance"
  value       = aws_instance.main.id
}

output "ec2_private_ip" {
  description = "Private IP address of the EC2 instance"
  value       = aws_instance.main.private_ip
}

output "ec2_ami_used" {
  description = "AMI ID used for the EC2 instance"
  value       = aws_instance.main.ami
}

# -------------------------------------------------------
# TLS Certificate
# -------------------------------------------------------
output "tls_cert_pem" {
  description = "Self-signed TLS certificate (PEM) — distribute to employee trust stores to avoid browser warnings"
  value       = tls_self_signed_cert.webapp.cert_pem
  sensitive   = false # It's a public cert, not a secret
}

output "tls_cert_expiry" {
  description = "Expiry date of the TLS certificate"
  value       = tls_self_signed_cert.webapp.validity_end_time
}
