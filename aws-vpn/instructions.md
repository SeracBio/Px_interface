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

Then create the backend config for the main stack (kept out of git — the bucket name embeds your account ID):
```bash
cd aws-vpn
cp backend.hcl.example backend.hcl
# edit backend.hcl: set bucket = the state_bucket_name from the bootstrap output
```

---

## Step 3 — Generate the Basic Auth Hash

Run this on your local machine. Store the *password* in 1Password; only the bcrypt hash is written to disk.

```bash
htpasswd -nbB serac_user 'your-password-from-1password' > ~/.serac_aws
chmod 600 ~/.serac_aws            # contains the auth hash — keep it private
# ~/.serac_aws now holds one line: serac_user:$2y$10$...
```

Terraform reads `~/.serac_aws` automatically at apply time — no env var needed. (If the file is ever absent, it falls back to `TF_VAR_webapp_htpasswd_hash`.)

---

## Step 4 — Deploy the Main Infrastructure

```bash
cd aws-vpn
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

> The auth hash comes from `~/.serac_aws` (Step 3) — no env var to export.
> If you previously ran `terraform init` with the old hard-coded backend, re-run
> `terraform init -backend-config=backend.hcl -reconfigure` to pick up the new partial config.
> **Tip:** upload the interface to S3 (Step 6) *before* this apply and a fresh box will serve it at boot.

Review the plan, then type `yes` when prompted.

This creates everything: VPC, VPN connection, EC2 instance, Nginx with HTTPS, TLS certificate, VPC endpoints,
the interface S3 bucket, and the Route 53 private zone + inbound resolver for the friendly hostname.

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

## Step 6 — Upload the Serac Px Interface (via S3)

The instance **auto-pulls** the interface from the S3 artifacts bucket at boot into
`/var/www/webapp/Px_interface/` — so you just place the files in the bucket (no SSH, no manual `cp`). Upload
**before** Step 4's apply and a fresh box serves it immediately; upload after and refresh with one command.

Render (SYNTHETIC — real data waits for M365 SSO) and upload from your workstation:
```bash
conda run -n ML python tests/make_synthetic.py --out tmp
conda run -n ML python python/Px_interface.py --config tmp/config.yaml --output_dir tmp/out
BUCKET=$(terraform -chdir=aws-vpn output -raw interface_bucket)
aws s3 sync tmp/out/interfaces/ "s3://$BUCKET/interfaces/" --region eu-north-1 --exclude "*_2dtest.html"
```

To refresh a **running** box (new render, no reboot) — pull the new files onto it via SSM:
```bash
aws ssm start-session --target "$(terraform output -raw ec2_instance_id)" --region eu-north-1
# on the box:
sudo aws s3 sync s3://<BUCKET>/interfaces/ /var/www/webapp/Px_interface/ --region eu-north-1
exit
```

---

## Step 7 — Enable the friendly hostname (advantedge.seracbio.com)

The apply creates a Route 53 **private zone** (`advantedge.seracbio.com`) + an **inbound resolver** so the
interface is reachable at a friendly, VPN-only name pointing at the EC2's fixed IP (`172.20.2.10`). Finish the
on-prem side:

1. Get the resolver endpoint IPs:
   ```bash
   terraform output resolver_inbound_ips
   ```
2. On the **FortiGate / on-prem DNS**, add a **conditional forwarder**: forward **only** `advantedge.seracbio.com`
   to those IPs. Do **not** forward all of `seracbio.com` — that would break public resolution.
3. Confirm the URL: `terraform output webapp_url` → `https://advantedge.seracbio.com/Px_interface/`.

---

## Step 8 — Test in the Browser

From any machine on the VPN, open the friendly name (once Step 7's forwarder is live):
```
https://advantedge.seracbio.com/Px_interface/
```
Or by private IP directly (works immediately, no DNS needed; cert will warn on the bare IP):
```
https://172.20.2.10/Px_interface/          # or: terraform output ec2_private_ip
```

You will see an HTTP basic auth prompt — use the `serac_user` credentials from step 3. Off the VPN it is
unreachable, by design.

> **A freshly applied/replaced box self-provisions in the background** — `user_data` installs a systemd unit
> (`provision-webapp`) that waits for the network then retries `dnf`/SSM/S3 until they answer, so `nginx` may take
> a few minutes to start (no manual `part-001`/SSH needed). If the page refuses to connect right after apply,
> wait ~2–3 min. To watch progress over SSM: `systemctl status provision-webapp` / `journalctl -u provision-webapp`.

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
On-prem / VPN                         AWS VPC (172.20.0.0/16)
(192.168.146.0/24, 10.0.14.0/24)        Private subnets (172.20.2.0/24, 172.20.3.0/24)
      |                                        |
  FortiGate  <-- IPsec/IKEv2 (2 tunnels) --> Virtual Private Gateway
      |   \                                    |
      |    `-- DNS: advantedge.seracbio.com -> Route 53 inbound resolver
      |          (conditional forwarder)       (private zone -> 172.20.2.10)
      |                                        |
      |                               EC2 t3.micro @ 172.20.2.10
      |                               nginx HTTPS + Basic Auth
      |                               serves /Px_interface/  (auto-pulled from S3 at boot)
      |                                        |
      |                        (SSM + S3 + DNS via VPC endpoints — no NAT, no public IP)
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
- The basic auth password hash is never stored in the repo or `terraform.tfvars` — read from `~/.serac_aws` at apply time and stored as an SSM SecureString (`/<project>/webapp/htpasswd`), which the instance fetches at boot. It is deliberately kept out of `user_data` (which is readable on-box via IMDS).
- VPN pre-shared keys are auto-generated by AWS and stored only in remote state (encrypted).
- HSTS is intentionally disabled until the TLS certificate is distributed to employee trust stores. Once it is, re-enable it in the nginx config in `ec2.tf`.
