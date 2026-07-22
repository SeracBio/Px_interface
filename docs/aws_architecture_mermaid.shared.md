# Px interface — AWS architecture diagrams (shareable)

Redacted architecture overview for external review. Account identifiers, the Terraform state bucket
name, and internal IP addresses / CIDR ranges have been removed; topology and services are unchanged.

---

## 1. Full infrastructure map

How every component connects — from a Serac employee's laptop to the EC2 instance in AWS.

```mermaid
flowchart TB
    %% ===================== Serac side =====================
    subgraph SERAC["Serac network (on-premises + remote)"]
        RU["Remote employee<br/>FortiClient VPN<br/>assigned VPN client pool"]
        OU["Office employee (60C)<br/>office LAN"]
        FG["FortiGate firewall (60F)<br/>public IP<br/>= AWS 'Customer Gateway'"]
        RU --> FG
        OU --> FG
    end

    %% ===================== AWS side =====================
    subgraph AWS["AWS account - region eu-north-1"]
        CGW["Customer Gateway<br/>(record of FortiGate public IP)"]
        VGW["Virtual Private Gateway<br/>(AWS side of the VPN)"]
        VPN["Site-to-Site VPN connection<br/>2 x IPsec IKEv2 tunnels<br/>static routes: office LAN + VPN client ranges"]

        subgraph VPC["VPC - private range"]
            subgraph PRIVSN["Private subnets (2 AZs)"]
                EC2["EC2 t3.micro (fixed private IP) - Amazon Linux 2023<br/>NO public IP<br/>nginx: HTTPS + Basic Auth, serves /Px_interface/<br/>IMDSv2, encrypted gp3 root"]
                E1["VPC endpoint: ssm"]
                E2["VPC endpoint: ssmmessages"]
                E3["VPC endpoint: ec2messages"]
                RESOLVER["Route 53 inbound resolver<br/>ENIs in 2 AZs"]
            end
            S3GW["S3 gateway endpoint<br/>(dnf package installs)"]
            RT["Private route table<br/>VPN-propagated routes + S3 endpoint"]
            SG1["SG ec2-sg<br/>ingress: 443/80/ICMP from<br/>office LAN + VPN client ranges<br/>egress: locked to endpoints + S3 only"]
            SG2["SG vpce-sg<br/>allow 443 from VPC"]
        end

        SSM["SSM Parameter Store - SecureString<br/>TLS cert + private key (KMS-encrypted)"]
        IAM["IAM role + instance profile<br/>SSM core, read TLS params, KMS decrypt, read interface bucket"]
        R53Z["Route 53 private zone<br/>internal hostname -> EC2 fixed IP"]
        S3IF["S3 bucket - interface artifacts<br/>(EC2 pulls to /var/www/webapp/Px_interface at boot)"]

        subgraph MON["Monitoring"]
            CW["CloudWatch alarms<br/>VPN TunnelState (both / one down)"]
            SNS["SNS topic - email alert"]
        end

        subgraph BACKEND["Terraform backend (management plane)"]
            S3B["S3 bucket - encrypted + versioned<br/>(Terraform state)<br/>TLS-only + same-account policy"]
            DDB["DynamoDB - state lock"]
        end
    end

    DEV["You (developer)<br/>Terraform + AWS CLI"]

    %% ===================== links =====================
    FG == 2 IPsec tunnels ==> VGW
    CGW --- VPN
    VGW --- VPN
    VPN -. propagates on-prem routes .-> RT
    RT --> EC2

    EC2 --> E1
    EC2 --> E2
    EC2 --> E3
    EC2 --> S3GW
    SG1 -. protects .-> EC2
    SG2 -. protects .-> E1
    SG2 -. protects .-> E2
    SG2 -. protects .-> E3

    IAM -. attached .-> EC2
    EC2 -. reads at boot .-> SSM

    FG -. forwards internal hostname .-> RESOLVER
    RESOLVER -. answers from .-> R53Z
    R53Z -. resolves to .-> EC2
    EC2 -. pulls interface at boot .-> S3IF

    VPN -. tunnel telemetry .-> CW
    CW --> SNS

    DEV == SSM Session Manager ==> EC2
    DEV -. state .-> S3B
    DEV -. lock .-> DDB
```

---

## 2. Runtime: a user opening the interface

What happens when you (remote, on FortiClient) load the page. Nothing is ever public — the whole
exchange rides inside the encrypted VPN tunnel.

```mermaid
sequenceDiagram
    participant U as Remote user (FortiClient)
    participant FG as FortiGate (+ on-prem DNS)
    participant RES as Route 53 inbound resolver
    participant VPN as S2S VPN / VGW
    participant EC2 as EC2 nginx

    U->>FG: resolve internal hostname
    FG->>RES: forward query (over tunnel) -> EC2 fixed IP
    U->>FG: GET https://<internal-hostname>/Px_interface/
    FG->>VPN: encrypt + send through IPsec tunnel
    VPN->>EC2: deliver inside the VPC private subnet
    EC2-->>U: 401 - Basic Auth challenge
    U->>EC2: serac_user + shared password
    EC2-->>U: 200 - Serac_Px_interface.html (self-signed TLS, via tunnel)
```

---

## 3. Boot: how the EC2 configures itself

At first launch the instance has no software on it — `user_data` builds it, pulling everything from
inside AWS (no internet, no NAT) thanks to the VPC endpoints.

```mermaid
sequenceDiagram
    participant TF as Terraform apply
    participant EC2 as EC2 instance
    participant S3EP as S3 gateway endpoint
    participant SSMEP as ssm endpoint
    participant PS as SSM Parameter Store

    TF->>EC2: launch (user_data + IAM role)
    EC2->>EC2: user_data drops systemd oneshot (provision-webapp), waits for network-online
    Note over EC2: retries the whole pass until endpoints answer (no cloud-init boot race)
    EC2->>S3EP: dnf install nginx (AL2023 repos in S3)
    EC2->>SSMEP: get TLS cert + key + basic-auth hash
    SSMEP->>PS: fetch SecureString (KMS decrypt)
    PS-->>EC2: cert + key + htpasswd
    EC2->>S3EP: pull rendered interface from S3 bucket
    EC2->>EC2: write nginx.conf + .htpasswd, nginx -t, start nginx
```
