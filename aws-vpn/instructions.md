# AWS VPC + Site-to-Site VPN — Deployment Instructions

Last updated: 2026-07-03

---

## What This Creates

- A VPC with a public and private subnet
- An EC2 instance (t3.micro, Amazon Linux 2023) in the private subnet — no public IP, reachable only via VPN
- VPC interface endpoints for SSM, SSMMessages, and EC2Messages (no NAT gateway needed)
- A VPC gateway endpoint for S3 (for `dnf` package installs at boot)
- A Virtual Private Gateway (VGW) and Customer Gateway (CGW) for Site-to-Site VPN
- Two IPsec/IKEv2 tunnels with AES-256 encryption
- Nginx serving the Serac Px Interface over HTTPS with HTTP basic auth
- Self-signed TLS certificate stored encrypted in SSM Parameter Store
- Remote Terraform state in S3 (encrypted, versioned) with DynamoDB locking

---

## Step 1 — Prerequisites (do once)

Install [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.0 if not already installed.

Configure the AWS CLI for `eu-north-1`:
```bash
aws configure
```

Get your AWS account ID — you'll need it for the state bucket name:
```bash
aws sts get-caller-identity --query Account --output text
```

---

## Step 2 — Bootstrap the Remote Backend (do once)

The S3 bucket and DynamoDB table for Terraform state must exist before the main config can be initialised.

```bash
cd aws-vpn/bootstrap
```

Create `bootstrap/terraform.tfvars`:
```hcl
aws_region        = "eu-north-1"
project_name      = "vpn-project"
state_bucket_name = "vpn-project-tf-state-123456789012"  # replace with your account ID
lock_table_name   = "vpn-project-tf-lock"
```

Apply:
```bash
terraform init
terraform apply
```

Take the `state_bucket_name` value from the output and paste it into the `bucket = "..."` line inside the `backend "s3"` block in `aws-vpn/main.tf`.

---

## Step 3 — Generate the Basic Auth Password

Run this on your local machine. Do not save the plaintext password in any file — store it in 1Password.

```bash
htpasswd -nbB serac_user 'your-password-from-1password'
# outputs a line like: serac_user:$2y$10$...
```

Copy the full output line. You will pass it as an environment variable in the next step.

---

## Step 4 — Deploy the Main Infrastructure

```bash
cd aws-vpn
export TF_VAR_webapp_htpasswd_hash='serac_user:$2y$10$...'   # paste from step 3
terraform init
terraform plan
terraform apply
```

Review the plan, then type `yes` when prompted.

This creates everything: VPC, VPN connection, EC2 instance, Nginx with HTTPS, TLS certificate, and VPC endpoints.

---

## Step 5 — Configure Your VPN Device (on-premises side)

After apply completes, retrieve the tunnel details:
```bash
terraform output tunnel1_address
terraform output tunnel2_address
terraform output -raw tunnel1_preshared_key
terraform output -raw tunnel2_preshared_key
terraform output tunnel1_cgw_inside_address
terraform output tunnel1_vgw_inside_address
```

Use these to configure your on-premises VPN device. For a vendor-specific config file, go to:
**AWS Console → VPC → Site-to-Site VPN Connections → select your connection → Download Configuration**

Key tunnel parameters:

| Parameter | Value |
|---|---|
| IKE version | IKEv2 |
| Encryption | AES-256 |
| Integrity | SHA-256 |
| DH group | 14 |
| Dead peer detection | Enabled |

Configure both tunnels — AWS uses one as active, one as standby for redundancy.

---

## Step 6 — Upload the Serac Px Interface

Once the VPN tunnel is established, connect to the EC2 instance via SSM Session Manager (no SSH key needed):

```bash
aws ssm start-session \
  --target $(terraform output -raw ec2_instance_id) \
  --region eu-north-1
```

Then on the instance, replace the placeholder with the real app file:
```bash
sudo cp /path/to/Serac_Px_interface.html /var/www/webapp/Serac_Px_interface.html
```

---

## Step 7 — Test in the Browser

From any machine connected to the VPN, open:
```
https://<ec2_private_ip>
```

Get the private IP:
```bash
terraform output ec2_private_ip
```

You will see an HTTP basic auth prompt — use the `serac_user` credentials from step 3.

### Remove the self-signed cert browser warning (optional but recommended)

Export the certificate:
```bash
terraform output tls_cert_pem > serac-internal.crt
```

Install it on each employee machine:
- **Windows:** Double-click → Install Certificate → Local Machine → Trusted Root Certification Authorities
- **macOS:** Double-click → Keychain Access → System keychain → set to Always Trust
- **Linux:** Copy to `/usr/local/share/ca-certificates/` and run `sudo update-ca-certificates`

---

## Architecture Overview

```
On-premises network              AWS VPC (10.0.0.0/16)
(192.168.146.0/24)                      |
      |                         Private Subnet (10.0.2.0/24)
  VPN Device <-- IPsec/IKEv2 --> Virtual Private Gateway
                                         |
                                  EC2 t3.micro
                                  Nginx + HTTPS + Basic Auth
                                  Serac Px Interface
                                         |
                              (SSM/S3 via VPC endpoints — no NAT)
```

---

## Certificate Renewal

The TLS certificate is valid for 825 days (~2 years) from the date of `terraform apply`.
To renew it before expiry:
```bash
terraform apply -replace=tls_self_signed_cert.webapp
```
The EC2 instance will be reprovisioned automatically with the new cert.

Check the current expiry at any time:
```bash
terraform output tls_cert_expiry
```

---

## Tear Down

```bash
# Main infrastructure
cd aws-vpn
terraform destroy

# Bootstrap resources (only when completely done with the project)
cd aws-vpn/bootstrap
terraform destroy
```

Note: the S3 bucket and DynamoDB table have `prevent_destroy = true`. Remove those lifecycle blocks before destroying the bootstrap stack, otherwise `terraform destroy` will error.

---

## Security Notes

- EC2 has no public IP and no SSH port open — only reachable through the VPN tunnel.
- Administration is done via SSM Session Manager (no key pair required; all sessions are logged to CloudTrail).
- IMDSv2 is enforced on the EC2 instance to prevent SSRF-based metadata attacks.
- Terraform state is encrypted at rest (SSE-KMS) and in transit (TLS-only bucket policy), and versioned.
- The basic auth password hash is never stored in the repo or `terraform.tfvars` — injected via `TF_VAR_webapp_htpasswd_hash` at apply time.
- VPN pre-shared keys are auto-generated by AWS and stored only in remote state (encrypted).
- HSTS is intentionally disabled until the TLS certificate is distributed to employee trust stores. Once it is, re-enable it in the nginx config in `ec2.tf`.
