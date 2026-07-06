# Connecting a Data Centre to AWS VPC via Site-to-Site VPN & Hybrid DNS

## Overview

This document covers how to establish a secure, encrypted connection from your on-premises data centre to an AWS VPC using AWS Site-to-Site VPN, and how to configure hybrid DNS resolution using Amazon Route 53 Resolver.

---

## 1. AWS Site-to-Site VPN

### How It Works

AWS Site-to-Site VPN creates an **encrypted IPsec tunnel** between your on-premises network and your AWS VPC. It supports Internet Protocol Security (IPsec) connections. Data transferred between your VPC and data centre routes over an encrypted VPN connection to help maintain the confidentiality and integrity of data in transit.

> An internet gateway is **not** required to establish a Site-to-Site VPN connection.

### Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| **Customer Gateway (CGW)** | Your data centre | Your physical or software VPN device |
| **Virtual Private Gateway (VGW)** | AWS | VPN termination point attached to a single VPC |
| **Transit Gateway (TGW)** | AWS | Hub for connecting multiple VPCs and on-premises networks |
| **VPN Connection** | Between CGW and VGW/TGW | Two redundant IPsec tunnels (each in a different AZ) |

### When to Use VGW vs Transit Gateway

| Scenario | Recommendation |
|----------|---------------|
| Single VPC connectivity | Virtual Private Gateway |
| Multiple VPCs | Transit Gateway |
| Multiple data centres | Transit Gateway + VPN CloudHub |
| Need ECMP load balancing | Transit Gateway |
| Need accelerated performance | Accelerated Site-to-Site VPN (with AWS Global Accelerator) |

### Step-by-Step Setup (Console)

1. **Create a Customer Gateway** — provide your on-prem device's public IP and (optionally) BGP ASN
2. **Create a Virtual Private Gateway** (or use an existing Transit Gateway)
3. **Attach the VGW to your VPC** (or attach a VPN to the TGW)
4. **Create the Site-to-Site VPN Connection** — select target gateway type, customer gateway, and routing type (static or BGP dynamic)
5. **Download the configuration file** — AWS generates device-specific config for your router (Cisco, Juniper, pfSense, etc.)
6. **Configure your on-prem device** with the downloaded config
7. **Update VPC route tables** — add routes for your on-prem CIDR pointing to the VGW/TGW

> **Important:** Ensure IP address ranges do not overlap between VPCs and the on-premises address space.

---

### Terraform Examples

#### Basic VPN with Virtual Private Gateway

```hcl
# Customer Gateway (your data centre device)
resource "aws_customer_gateway" "dc" {
  bgp_asn    = 65000
  ip_address = "203.0.113.1"  # Your DC's public IP
  type       = "ipsec.1"
  tags       = { Name = "dc-customer-gateway" }
}

# Virtual Private Gateway
resource "aws_vpn_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "dc-vpn-gateway" }
}

# VPN Connection
resource "aws_vpn_connection" "dc_to_aws" {
  customer_gateway_id = aws_customer_gateway.dc.id
  vpn_gateway_id      = aws_vpn_gateway.main.id
  type                = "ipsec.1"
  static_routes_only  = false  # Use BGP for dynamic routing

  tags = { Name = "dc-to-aws-vpn" }
}

# Route propagation (VGW advertises on-prem routes to VPC route table)
resource "aws_vpn_gateway_route_propagation" "main" {
  vpn_gateway_id = aws_vpn_gateway.main.id
  route_table_id = aws_route_table.private.id
}
```

#### VPN with Transit Gateway (Recommended for Multi-VPC)

```hcl
# Transit Gateway
resource "aws_ec2_transit_gateway" "main" {
  description = "Hub for hybrid connectivity"
  tags        = { Name = "hybrid-tgw" }
}

# VPN Connection to Transit Gateway
resource "aws_vpn_connection" "dc_to_tgw" {
  customer_gateway_id   = aws_customer_gateway.dc.id
  transit_gateway_id    = aws_ec2_transit_gateway.main.id
  type                  = "ipsec.1"
  static_routes_only    = false
  enable_acceleration   = true  # Uses AWS Global Accelerator

  tags = { Name = "dc-to-tgw-vpn" }
}

# Attach VPCs to Transit Gateway
resource "aws_ec2_transit_gateway_vpc_attachment" "vpc_a" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  vpc_id             = aws_vpc.vpc_a.id
  subnet_ids         = [aws_subnet.private_a_az1.id, aws_subnet.private_a_az2.id]
}
```

#### Static Route Example (if not using BGP)

```hcl
resource "aws_vpn_connection_route" "onprem_cidr" {
  vpn_connection_id      = aws_vpn_connection.dc_to_aws.id
  destination_cidr_block = "10.0.0.0/16"  # On-premises CIDR
}
```

---

### Routing Options

| Type | How it works | Best for |
|------|-------------|----------|
| **BGP (Dynamic)** | Routes exchanged automatically via BGP | Production — automatic failover and route updates |
| **Static** | Manually defined routes | Simple setups or devices without BGP support |

---

## 2. Hybrid DNS — Route 53 Resolver

### The Problem

Once VPN connectivity is established, you need **bidirectional DNS resolution**:
- On-prem hosts must resolve AWS private hosted zones (e.g. `api.internal.example.com`)
- AWS resources must resolve on-prem domains (e.g. `dc-db01.corp.example.com`)

The native Route 53 Resolver (VPC +2 address) is **not reachable** from on-premises networks over VPN or Direct Connect. You need Route 53 Resolver endpoints.

### Architecture

```
On-Prem DNS Server
    ↕ forwards queries for *.aws.example.com
Route 53 Resolver INBOUND Endpoint (in shared/network VPC)
    → resolves from Private Hosted Zones

AWS EC2 / services
    ↕ queries for *.corp.example.com
Route 53 Resolver OUTBOUND Endpoint
    → forwards to on-prem DNS server IPs (over VPN)
```

### Components

| Component | Direction | Purpose |
|-----------|-----------|---------|
| **Inbound Endpoint** | On-prem → AWS | Allows on-prem DNS to forward queries into your VPC |
| **Outbound Endpoint** | AWS → On-prem | Allows VPC resources to resolve on-prem domains |
| **Forwarding Rules** | Configurable | Specify which domains go to which DNS servers |

### Key Design Decisions

- **Centralise endpoints** in a shared/network services VPC — do NOT create them in every VPC
- Each endpoint supports ~10,000 queries per second per ENI
- Share forwarding rules across accounts using **AWS Resource Access Manager (RAM)**
- Place endpoints in at least **2 Availability Zones** for redundancy

---

### Terraform Example

```hcl
# Security group for DNS traffic
resource "aws_security_group" "dns" {
  name_prefix = "dns-resolver-"
  vpc_id      = aws_vpc.shared_services.id

  ingress {
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]  # On-prem + VPC ranges
  }

  ingress {
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Inbound endpoint — on-prem resolvers forward here
resource "aws_route53_resolver_endpoint" "inbound" {
  name      = "hybrid-inbound"
  direction = "INBOUND"

  security_group_ids = [aws_security_group.dns.id]

  ip_address { subnet_id = aws_subnet.private_az1.id }
  ip_address { subnet_id = aws_subnet.private_az2.id }
}

# Outbound endpoint — VPC queries forwarded to on-prem
resource "aws_route53_resolver_endpoint" "outbound" {
  name      = "hybrid-outbound"
  direction = "OUTBOUND"

  security_group_ids = [aws_security_group.dns.id]

  ip_address { subnet_id = aws_subnet.private_az1.id }
  ip_address { subnet_id = aws_subnet.private_az2.id }
}

# Forwarding rule: send corp.example.com queries to on-prem DNS
resource "aws_route53_resolver_rule" "forward_to_onprem" {
  domain_name          = "corp.example.com"
  name                 = "forward-corp-to-onprem"
  rule_type            = "FORWARD"
  resolver_endpoint_id = aws_route53_resolver_endpoint.outbound.id

  target_ip {
    ip   = "10.0.1.53"  # On-prem DNS server 1
    port = 53
  }
  target_ip {
    ip   = "10.0.2.53"  # On-prem DNS server 2
    port = 53
  }
}

# Associate rule with VPC(s)
resource "aws_route53_resolver_rule_association" "main" {
  resolver_rule_id = aws_route53_resolver_rule.forward_to_onprem.id
  vpc_id           = aws_vpc.shared_services.id
}

# Share rule with other accounts via RAM (optional)
resource "aws_ram_resource_share" "dns_rules" {
  name                      = "dns-forwarding-rules"
  allow_external_principals = false
}

resource "aws_ram_resource_association" "dns_rule" {
  resource_arn       = aws_route53_resolver_rule.forward_to_onprem.arn
  resource_share_arn = aws_ram_resource_share.dns_rules.arn
}
```

### On-Premises DNS Configuration

On your on-premises DNS server, add a **conditional forwarder** for your AWS-hosted domains pointing to the inbound endpoint IPs:

#### Windows DNS Server (PowerShell)

```powershell
Add-DnsServerConditionalForwarderZone `
  -Name "aws.example.com" `
  -MasterServers 10.100.1.10, 10.100.2.10  # Inbound endpoint IPs
```

#### BIND (named.conf)

```
zone "aws.example.com" {
    type forward;
    forwarders { 10.100.1.10; 10.100.2.10; };
};
```

### Verification

```bash
# From on-premises, verify resolution of AWS private hosted zone
nslookup api.internal.example.com 10.100.1.10

# From EC2, verify resolution of on-prem domain
dig dc-db01.corp.example.com

# Check resolver endpoint status
aws route53resolver list-resolver-endpoints
```

---

## 3. End-to-End Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        ON-PREMISES DATA CENTRE                           │
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────────────────┐   │
│  │ App Servers │     │  DNS Server │     │ Customer Gateway Device │   │
│  │ 10.0.x.x   │     │  10.0.1.53  │     │ Public IP: 203.0.113.1 │   │
│  └─────────────┘     └──────┬──────┘     └────────────┬────────────┘   │
│                              │                          │                │
└──────────────────────────────┼──────────────────────────┼────────────────┘
                               │                          │
                               │ Conditional              │ IPsec Tunnels
                               │ Forwarder                │ (encrypted)
                               │                          │
┌──────────────────────────────┼──────────────────────────┼────────────────┐
│                         AWS CLOUD                        │                │
│                              │                          │                │
│  ┌───────────────────────────┼──────────────────────────┼─────────────┐  │
│  │  Shared Services / Network VPC                       │             │  │
│  │                           │                          │             │  │
│  │  ┌────────────────────────▼───────┐    ┌────────────▼──────────┐  │  │
│  │  │ Route 53 Inbound Endpoint      │    │ Virtual Private GW    │  │  │
│  │  │ (receives on-prem DNS queries) │    │ or Transit Gateway    │  │  │
│  │  └────────────────────────────────┘    └───────────────────────┘  │  │
│  │                                                                    │  │
│  │  ┌────────────────────────────────┐                               │  │
│  │  │ Route 53 Outbound Endpoint     │                               │  │
│  │  │ (forwards to on-prem DNS)      │                               │  │
│  │  └────────────────────────────────┘                               │  │
│  │                                                                    │  │
│  │  ┌────────────────────────────────┐                               │  │
│  │  │ Private Hosted Zone            │                               │  │
│  │  │ aws.example.com                │                               │  │
│  │  └────────────────────────────────┘                               │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐                       │
│  │ Workload VPC A      │  │ Workload VPC B      │                       │
│  │ (attached to TGW)   │  │ (attached to TGW)   │                       │
│  └─────────────────────┘  └─────────────────────┘                       │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Best Practices

### VPN

- Use **BGP dynamic routing** for automatic failover
- Deploy **two tunnels** (AWS does this by default) for redundancy
- Consider **Accelerated Site-to-Site VPN** for latency-sensitive workloads
- Use **Transit Gateway** for multi-VPC environments
- Monitor with **CloudWatch** metrics (TunnelState, TunnelDataIn/Out)
- Avoid overlapping CIDR ranges between on-prem and AWS

### DNS

- Centralise resolver endpoints in a **shared network VPC**
- Deploy across **at least 2 AZs**
- Share forwarding rules via **AWS RAM** for multi-account setups
- Use **Route 53 Resolver Query Logging** for troubleshooting
- Keep forwarding rules specific — avoid forwarding all queries unnecessarily

---

## 5. Documentation & References

### Site-to-Site VPN

| Resource | Link |
|----------|------|
| How Site-to-Site VPN works | https://docs.aws.amazon.com/vpn/latest/s2svpn/how_it_works.html |
| Getting started guide | https://docs.aws.amazon.com/vpn/latest/s2svpn/SetUpVPNConnections.html |
| Customer gateway device requirements | https://docs.aws.amazon.com/vpn/latest/s2svpn/your-cgw.html |
| Transit Gateway VPN attachments | https://docs.aws.amazon.com/vpc/latest/tgw/tgw-vpn-attachments.html |
| VPN FAQs | https://aws.amazon.com/vpn/faqs/ |
| Site-to-Site VPN product page | https://aws.amazon.com/vpn/site-to-site-vpn/ |
| VPC networking (how it works) | https://docs.aws.amazon.com/vpc/latest/userguide/how-it-works.html |

### Hybrid DNS

| Resource | Link |
|----------|------|
| Route 53 Resolver overview | https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver.html |
| Hybrid Cloud DNS Options (whitepaper) | https://docs.aws.amazon.com/whitepapers/latest/hybrid-cloud-dns-options-for-vpc/hybrid-cloud-dns-options-for-vpc.html |
| Resolver endpoints & forwarding rules | https://docs.aws.amazon.com/whitepapers/latest/hybrid-cloud-dns-options-for-vpc/route-53-resolver-endpoints-and-forwarding-rules.html |
| Centralized DNS with Transit Gateway (blog) | https://aws.amazon.com/blogs/networking-and-content-delivery/centralized-dns-management-of-hybrid-cloud-with-amazon-route-53-and-aws-transit-gateway/ |
| Single-account hybrid DNS pattern | https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/set-up-dns-resolution-for-hybrid-networks-in-a-single-account-aws-environment.html |

### Related

| Resource | Link |
|----------|------|
| AWS Direct Connect (alternative to VPN) | https://docs.aws.amazon.com/directconnect/latest/UserGuide/Welcome.html |
| Landing Zone networking (Control Tower) | https://docs.aws.amazon.com/prescriptive-guidance/latest/designing-control-tower-landing-zone/networking.html |
| Multi-VPC architecture whitepaper | https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/ |

---

*Document generated: 2 July 2026*
