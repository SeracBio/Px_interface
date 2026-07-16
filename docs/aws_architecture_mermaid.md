# Px interface — AWS architecture diagrams

Visual companion to `aws_architecture_learn.md` (which explains every piece) and `aws_docs.md`
(decisions + runbook). Reflects the Terraform stack in `aws-vpn/` (in-repo).

---

## 1. Full infrastructure map

How every component connects — from a Serac employee's laptop to the EC2 instance in AWS.

```mermaid
flowchart TB
    %% ===================== Serac side =====================
    subgraph SERAC["Serac network (on-premises + remote)"]
        RU["Remote employee<br/>FortiClient VPN<br/>assigned 10.0.14.100-199"]
        OU["Office employee (60C)<br/>LAN 192.168.146.0/24"]
        FG["FortiGate firewall (60F)<br/>public IP<br/>= AWS 'Customer Gateway'"]
        RU --> FG
        OU --> FG
    end

    %% ===================== AWS side =====================
    subgraph AWS["AWS account - region eu-north-1"]
        CGW["Customer Gateway<br/>(record of FortiGate public IP)"]
        VGW["Virtual Private Gateway<br/>(AWS side of the VPN)"]
        VPN["Site-to-Site VPN connection<br/>2 x IPsec IKEv2 tunnels<br/>static routes: 192.168.146.0/24 + 10.0.14.0/24"]

        subgraph VPC["VPC - 172.20.0.0/16"]
            IGW["Internet Gateway<br/>(egress for public subnet)"]
            subgraph PUBSN["Public subnet - 172.20.1.0/24"]
                PUBNOTE["reserved - EC2 is NOT here"]
            end
            subgraph PRIVSN["Private subnet - 172.20.2.0/24"]
                EC2["EC2 t3.micro - Amazon Linux 2023<br/>NO public IP<br/>nginx: HTTPS + Basic Auth<br/>IMDSv2, encrypted gp3 root"]
                E1["VPC endpoint: ssm"]
                E2["VPC endpoint: ssmmessages"]
                E3["VPC endpoint: ec2messages"]
            end
            S3GW["S3 gateway endpoint<br/>(dnf package installs)"]
            RT["Private route table<br/>VPN-propagated routes + S3 endpoint"]
            SG1["SG ec2-sg<br/>ingress: 443/80/ICMP from<br/>192.168.146.0/24 + 10.0.14.0/24<br/>egress: locked to endpoints + S3 only"]
            SG2["SG vpce-sg<br/>allow 443 from VPC"]
        end

        SSM["SSM Parameter Store - SecureString<br/>TLS cert + private key (KMS-encrypted)"]
        IAM["IAM role + instance profile<br/>SSM core, read TLS params, KMS decrypt"]

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
    IGW --- PUBSN

    IAM -. attached .-> EC2
    EC2 -. reads at boot .-> SSM

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
    participant FG as FortiGate
    participant VPN as S2S VPN / VGW
    participant EC2 as EC2 nginx

    U->>FG: HTTPS request to 172.20.2.x (EC2 private IP)
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

    TF->>EC2: launch (user_data script + IAM role)
    EC2->>S3EP: dnf install nginx (AL2023 repos in S3)
    EC2->>SSMEP: get TLS cert + key
    SSMEP->>PS: fetch SecureString (KMS decrypt)
    PS-->>EC2: cert + key
    EC2->>EC2: write nginx.conf + .htpasswd, nginx -t, start nginx
```
