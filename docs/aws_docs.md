# Connecting to AWS via the CLI

How to authenticate the AWS CLI to your AWS account from a terminal.

## 1. Install the AWS CLI

```bash
# Linux / WSL
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

## 2. Get your credentials

In the AWS Console → **IAM** → your user → **Security credentials** → **Create access key**.
You'll receive an **Access Key ID** and a **Secret Access Key**.

## 3. Configure the CLI

```bash
aws configure
```

Prompts:

```
AWS Access Key ID     [None]: AKIA...
AWS Secret Access Key [None]: ****
Default region name   [None]: us-east-1     # or your region
Default output format [None]: json
```

This writes `~/.aws/credentials` and `~/.aws/config`.

## 4. Verify the connection

```bash
aws sts get-caller-identity
```

If it returns your account ID, user ARN, and user ID, you're connected.

## Notes

- **Prefer SSO / `aws configure sso`** if your org uses AWS IAM Identity Center — it avoids
  long-lived keys. Run `aws configure sso` instead of step 3 and follow the browser login.
- **Named profiles** for multiple accounts: `aws configure --profile seracbio`, then use
  `aws s3 ls --profile seracbio` or `export AWS_PROFILE=seracbio`.
- **Security:** never commit `~/.aws/credentials` or paste keys into chats or web tools.

---

# Px interface on AWS — decisions & deployment

Log of the decisions and steps for serving the Px interface as an internal webapp on AWS.
Newest decisions first; the deployment runbook follows.

## Decision log

### 2026-07-06 — alignment check vs AWS contact's hybrid-connectivity reference
Reviewed `docs/AWS VPN & DNS Hybrid Connectivity.md` (from the AWS contact). **Our build matches the
reference's single-VPC pattern.** Confirmed alignment: CGW=FortiGate, **VGW** (their table: single VPC
→ VGW, not Transit Gateway), 2 IPsec tunnels, route propagation, **non-overlapping CIDRs** (the reason
we moved to `172.20.0.0/16`), static route per on-prem CIDR. Notes/decisions:
- **Static routing vs BGP:** we use **static**; the doc's best-practice leans BGP for automatic
  failover. Fine for our single fixed subnet (still 2-tunnel redundant); optional upgrade later since
  FortiGate supports BGP (`vpn_routing_type = "dynamic"` + BGP ASNs).
- **VGW vs Transit Gateway:** VGW is correct for one VPC. Only reconsider if Serac has/builds a
  shared-services **Transit Gateway landing zone** to attach to. → open question for the AWS contact.
- **Hybrid DNS (Route 53 Resolver): not needed now** — we reach the EC2 by **private IP**. Becomes
  relevant only for name-based access (`px.internal.seracbio.com` → Private Hosted Zone + inbound
  endpoint) or querying **RDS by DNS name** over the VPN (the ~$90/mo inbound Resolver flagged in the
  cost note). The doc confirms the native VPC-resolver is unreachable from on-prem — matches our
  earlier RDS note. Outbound endpoint (AWS→on-prem names) not needed at all.
- **Added CloudWatch alarms** (`monitoring.tf`): `vpn-connection-down` (both tunnels down) +
  `vpn-tunnel-degraded` (one down) on the `TunnelState` metric → SNS topic `vpn-project-alarms`
  (set `alarm_email` in tfvars to subscribe). ~$0.20/mo.
- **Open questions for the AWS contact:** (1) BGP vs static routing? (2) Is there an existing Transit
  Gateway / shared-network VPC we should attach to instead of a standalone VGW?

### 2026-07-06 — actual running cost (deployed stack, eu-north-1)
Estimate for the stack as deployed (on-demand, light internal traffic). **Supersedes the earlier
~$15/mo figure** in the 2026-07-01 sizing entry, which predated the VPN + VPC-endpoint decision.

| Component | ~Monthly |
|---|---|
| Site-to-Site VPN connection ($0.05/hr) | ~$36 |
| 3× VPC interface endpoints (ssm/ssmmessages/ec2messages, ~$8.5 each) | ~$25 |
| EC2 t3.micro (always on) | ~$8.5 |
| EBS gp3 root, 30 GB | ~$2.5 |
| S3 gateway endpoint | free |
| S3 state + DynamoDB lock + SSM params + KMS | pennies |
| Data transfer (light) | ~$1 |
| **Total** | **≈ $73/mo** |

- **VPN + endpoints (~$61, ~85%) are the price of the security model** (no public exposure, no NAT).
  A NAT gateway instead of the 3 endpoints would be ~$32 + data — more expensive *and* less secure.
- **Down:** a 1-yr EC2 Savings Plan trims the t3.micro ~$8.5 → ~$5. VPN + endpoints are ~fixed.
- **Future RDS phase adds** (not yet incurred): Aurora/RDS (~$30–60/mo small instance, or Aurora
  Serverless v2 from a low floor); Fargate rebuild (pennies, 15 min/week); ⚠️ a **Route 53 Resolver
  inbound endpoint** *only if* querying RDS by DNS name from a laptop over the VPN (~$90/mo for the
  pair) — avoidable by using the RDS private IP or letting the EC2-side pipeline do the DB work.

### 2026-07-03 — implementation: Terraform stack (Kiro) + networking finalised with IT
The MVP is built as a **Terraform stack** (generated with Kiro), living **in-repo** at `aws-vpn/`
(moved here 2026-07-06; its `terraform.tfvars` + `.terraform/` + state are kept out of git by
`aws-vpn/.gitignore` — verified only `.tf`/`.sh`/`.md` get committed). Canonical step-by-step is
`aws-vpn/instructions.md`; run Terraform with `terraform -chdir=aws-vpn …`. Key facts:
- **Region `eu-north-1`** (EU data residency), dedicated AWS account (ID via `aws sts get-caller-identity`).
- **Compute:** t3.micro **Amazon Linux 2023**, private subnet, **no public IP**; IMDSv2; encrypted
  gp3 root. **No NAT** — outbound for `dnf`/SSM via **VPC endpoints** (`ssm`, `ssmmessages`,
  `ec2messages` interface + **S3 gateway**). Admin via **SSM Session Manager** (no SSH, no key pair).
- **TLS:** self-signed cert generated by Terraform, stored in **SSM Parameter Store (SecureString)**,
  pulled at boot; nginx serves HTTPS + **basic-auth** (`serac_user`; hash injected via
  `TF_VAR_webapp_htpasswd_hash`, never committed). HSTS left off until the cert is trust-store'd.
- **Remote state:** S3 (encrypted/versioned/TLS-only bucket policy) + DynamoDB lock, created by a
  one-time `bootstrap/` module.
- **Networking (settled with IT):** VPC moved **off `10.0.0.0/16` → `172.20.0.0/16`** — Ridgeline/
  portfolio use 10.x *and* the FortiClient VPN pool (`10.0.14.100-199`) sits inside 10.0.0.0/16, so
  the old CIDR would have overlapped. `on_premises_cidr`/`allowed_cidr` are now **lists** =
  office LAN `192.168.146.0/24` **+** VPN pool `10.0.14.0/24`, each routed over the tunnel (static
  routes) and allowed in the EC2 SG — so **remote** FortiClient users (not just on-site) can reach it.
  FortiGate public IP is in the gitignored `terraform.tfvars` only.
- **Open (blocks apply):** IT to confirm `172.20.0.0/16` is free and to route both CIDRs over the S2S
  tunnel toward it.

### 2026-07-03 — chosen MVP: private EC2 + FortiGate↔VPC Site-to-Site VPN + basic-auth
An AWS colleague flagged **VPC + VPN tunnelling** as the most secure setup, and a scan of this
machine found **FortiClient VPN installed** (Windows host; no AWS VPN Client / WireGuard /
Tailscale / AnyConnect). FortiClient implies Serac likely already runs a **FortiGate** — i.e. an
existing corporate VPN to reuse. **Decision:**
- **Network:** private EC2 with **no public IP**; a **FortiGate↔VPC Site-to-Site (IPsec) VPN**
  makes it reachable only from Serac's network. **Zero public attack surface** — strongest posture,
  and it reuses existing infra (no ~$72/mo AWS Client VPN, no new VPN product/client rollout).
- **Auth:** nginx **basic-auth**, shared password via **1Password**. Behind the VPN this is *solid*
  (not the weak public+password case rejected earlier): to even reach the login you must already be
  on Serac's network via FortiClient. Residual gap = no per-user identity/audit → add M365 SSO later
  (additive swap). Because there's no public exposure, this posture would even be safe for **real**
  data eventually; keep the MVP on synthetic until ready, and gate real-data on SSO only for
  per-user *audit*, not network exposure.
- **TLS:** no public DNS → certbot's HTTP-01 challenge can't run. The IPsec tunnel already encrypts
  transit, so app-TLS isn't load-bearing; use a **self-signed/internal cert** for hygiene (or skip).
- **Bootstrap:** provision the box before the tunnel exists via **SSM Session Manager** (shell with
  no inbound ports, no public IP — needs the SSM agent + an IAM role).
- **Open item:** confirm FortiGate exists and who owns each side of the S2S tunnel (IT = FortiGate
  side, AWS colleague = VPC side).
Supersedes the public-endpoint options below (ALB+SSO / public certbot) as the *starting* point;
M365 SSO remains the eventual per-user upgrade.

### 2026-07-01 — target architecture: decoupled serve + rebuild
The render is slow (~15 min on real data), so **serving and rebuilding are separated**:
- **Serving box** (always on, cheap) — serves pre-built static artifacts.
- **Rebuild** (occasional, big RAM) — regenerates the artifacts and publishes them to the serving box.

```
Trigger (weekly EventBridge cron; later an ETL-emitted event on RDS load)
        │
        ▼
Lambda / Step Functions ──launches──► ephemeral compute (Fargate/Batch, ~32 GB, ~15 min)
                                        reads FBX from RDS → runs Px pipeline
                                        writes interfaces/ to S3, then terminates
        │
        ▼ (S3 put event)
serving t3.small: aws s3 sync s3://…/interfaces  /var/www/px   (atomic swap)
        │
        ▼
users see the refreshed interface (serving box never went down)
```
- **RDS can't natively push on row changes** — use a weekly schedule now; add an ETL-emitted
  event (SNS/EventBridge) for true push once the RDS load process is defined. (RDS *Event
  Notifications* are for instance events like failover/backup, not data changes.)
- Rebuild needs big RAM only **transiently** (24.6M-row `df_raw` in memory), not big disk.
  Prefer a **Fargate/Batch** container job (pay per second, nothing to leave running) over a
  start/stop EC2 2xlarge.
- Requires `DATA.load_new_df` to gain an **RDS source mode** (config toggle; keep the
  CSV/synthetic path for tests).

### 2026-07-01 — storage: EBS root only, S3 as backup
- Total data (srb_png + volcanoes + `df_raw.parquet`) is **< 1 GB** → no separate data volume.
  Use a **20–30 GB gp3 root** on the serving box (~$2/mo).
- **S3** holds the canonical/backup copy of inputs + rendered artifacts so the box is
  disposable/rehydratable (~$0.02/mo at this size). **Don't serve directly from S3** (would
  need CloudFront + its own auth, conflicting with the SSO plan) — nginx serves from EBS.
- EBS is required regardless: the pipeline (pandas/pyarrow/RDKit) and nginx need a real
  filesystem; S3 is an object API, not POSIX.

### 2026-07-01 — instance sizing / cost
- **Serving:** t3.small (2 GB) ≈ **$15/mo** on-demand (~$9 with a 1-yr savings plan).
- **Rebuild:** 8 vCPU/32 GB for 15 min/week ≈ **~$0.45/mo** on Fargate (or ~$0.35/mo start/stop
  EC2). Big RAM contributes almost nothing because it lives only 15 min/week.
- **Total ≈ $15–16/mo.** Storage is rounding error.

### 2026-07-01 — auth: M365 SSO is the target; shared-password MVP to start
- Serac is **remote-heavy** and on **Microsoft 365** (→ Entra ID is the IdP). IP-allowlisting is
  impractical for remote users, and public basic-auth-only would guard chemistry data with a single
  shared secret — not acceptable for real data.
- **Target:** **ALB + authenticate-OIDC against Entra ID** — per-user login (same as email), revoke
  by Entra group, nothing for users to install. Needs an Entra **app registration** (M365 admin).
- **MVP (now):** EC2 + nginx **basic-auth**, shared password distributed via **1Password**, and
  **synthetic data only** — no real chemistry on the box until SSO is live. (1Password is a secret
  vault, not a VPN/IdP; it's for distributing the shared password, not gating the app.)
- **MVP → SSO is a small, additive change:** add the ALB authenticate rule, delete the nginx
  `auth_basic` lines, point Route 53 at the ALB. Content/box/nginx-root unchanged. **Terminate TLS
  at an ALB from day one** if you want the flip to be zero-rework (the runbook below does TLS on the
  box for speed; note the tradeoff).
- Rejected: **Tailscale/VPN + basic-auth** — once remote, it needs a client on every device *and*
  still uses M365 for its own SSO, so it's more work than going straight to ALB+Entra with no user
  benefit.

### Privacy guardrails (hard rules, see CLAUDE.md)
- **Never** rsync the **real** `output/interfaces/` to the box until SSO is live — MVP runs on
  synthetic (fake ids, `CCO` SMILES).
- RDS + EC2 must sit in **Serac's VPC**, encrypted at rest + TLS in transit, non-public.
- DB URL / credentials via env or AWS Secrets Manager — never in the repo.

## Deployment runbook — Terraform stack (`aws-vpn/`)

Canonical steps live in `aws-vpn/instructions.md`. This is the summary + live progress. All Terraform
runs use `terraform -chdir=<aws-vpn>` (region `eu-north-1`). The earlier hand-rolled Ubuntu/nginx
runbook is in git history if ever needed.

1. **Prereqs** — Terraform ≥1.5 + AWS CLI configured for `eu-north-1`.
2. **Bootstrap** (`aws-vpn/bootstrap/`, once) — creates the S3 state bucket + DynamoDB lock; copy
   `backend.hcl.example` → `backend.hcl` (gitignored) with the bucket/table names, then
   `terraform init -backend-config=backend.hcl` the main stack.
3. **Auth hash** (local, keep off the wire) — `htpasswd -nB serac_user` → store password in 1Password,
   keep the `serac_user:$2y$…` line.
4. **Apply** (from `aws-vpn/`, same shell):
   ```bash
   export TF_VAR_webapp_htpasswd_hash='serac_user:$2y$10$…'   # single quotes!
   terraform apply
   ```
5. **FortiGate config (IT)** — hand IT the tunnel outputs (`terraform output tunnel1_address`,
   `-raw tunnel1_preshared_key`, etc.); IKEv2 / AES-256 / SHA-256 / DH14.
6. **Upload the interface** — build locally (`python tests/make_synthetic.py` →
   `python python/Px_interface.py --config tmp/config.yaml --output_dir tmp/out`), then via SSM copy
   `tmp/out/interfaces/` to `/var/www/webapp/` on the box (SSM Session Manager, no SSH).
7. **Verify** — on VPN, browse `https://<ec2_private_ip>/` → basic-auth → interface (off VPN:
   unreachable, by design). Deep-link: `…/Serac_Px_interface.html#p=Pw01` (no trailing slash).

### Live progress (2026-07-03)
- [x] **Step 1** — Terraform v1.15.7 installed; AWS CLI on `eu-north-1` (account ID via `aws sts get-caller-identity`).
- [x] **Step 2** — bootstrap applied: state bucket (`vpn-project-tf-state-<ACCOUNT_ID>`) + lock table
      `vpn-project-tf-lock`; backend supplied via `backend.hcl`; main stack `init` done.
- [x] **Step 3** — auth hash generated for `serac_user` (password in 1Password).
- [x] **Step 4** — `terraform apply` **complete** (29 resources). Live resource IDs (EC2 instance, private
      IP, VPN connection, tunnel addresses) are in the Terraform outputs — `terraform -chdir=aws-vpn output`.
      Box is **healthy**: nginx active, TLS pulled, SSM reachable, 4/4 endpoints available.
- [x] **Step 5 — DONE (2026-07-08):** IT configured the FortiGate side; **tunnel is UP** (`1/2`, which is
      normal — AWS runs one active + one standby, so a single UP tunnel = a working VPN). Config was
      Fortinet FortiGate FortiOS 6.4.4+ IKEv2, routing `172.20.0.0/16` with local selectors both
      `192.168.146.0/24` and `10.0.14.0/24`.
- [x] **Step 7 connectivity — DONE (2026-07-08):** from a remote laptop on FortiClient, `https://<ec2_private_ip>`
      loaded the placeholder page through the tunnel, prompting for `serac_user` + shared password. Full chain
      verified: FortiClient → FortiGate → S2S tunnel → VPC → private EC2 → nginx HTTPS + basic-auth. No public
      exposure. (Cosmetic: placeholder shows a mojibake em-dash `â€"` — no `<meta charset>` in the placeholder;
      goes away once the real interface is uploaded. "Not secure" = self-signed cert, expected.)
- [ ] **Step 6 — pending:** upload the real (2D-default) interface HTML to `/var/www/webapp/` (needs a transfer
      method: S3-via-gateway-endpoint or base64-over-SSM; SSH/scp is closed by design).

**Health check:** `bash aws-vpn/healthcheck.sh` — read-only (AWS `describe` + one SSM
`systemctl is-active nginx`); auto-discovers IDs from Terraform outputs; checks EC2 state/status, SSM
reachability, all 4 endpoints, VPN connection + tunnel UP count, and nginx. Current (2026-07-08): **7 ok /
0 warn / 0 fail — tunnels 1/2 UP.**

**Note — two VPCs in the account is normal:** `vpn-project-vpc` (`172.20.0.0/16`, ours) + the
unnamed AWS **default VPC** (`172.31.0.0/16`, auto-created per region, empty, free). Leave it, or
delete for hygiene.

### Apply gotchas encountered (for future reference)
- **Ctrl+C mid-apply → state checksum mismatch** (S3 vs DynamoDB). Fix: update the DynamoDB `Digest`
  item to the checksum Terraform reports for S3 (`aws dynamodb update-item …`). S3 is written first,
  so S3 is the source of truth.
- **Orphaned endpoints after interrupt** — `ssm`/`ec2messages` were created in AWS but not in state
  (private-DNS conflict on re-create). Fix: `terraform import aws_vpc_endpoint.<name> vpce-…`.
- **AMI snapshot size** — AL2023 root snapshot is 30 GB; `root_block_device.volume_size` must be ≥30
  (was 20). Bumped to 30.
- **Boot-time race → `user_data` failed** — cloud-init ran the script at ~8 s uptime, before the VPC
  endpoints/routing were ready, so `dnf` had no route and `set -e` aborted the whole script (symptom:
  no nginx, `/var/www/webapp` + `/etc/nginx/ssl` missing; `cloud-init status` = error). Fixed once by
  re-running `sudo bash /var/lib/cloud/instance/scripts/part-001`. **Will recur on reboot/replacement
  (incl. cert renewal via `user_data_replace_on_change`).** RECOMMENDED (not yet applied): wrap the
  `dnf` + `aws ssm get-parameter` calls in a retry/wait loop so boot self-heals.

## TODO / pending
**Done:** bootstrap + apply (29 resources), IT's FortiGate side, tunnel UP, end-to-end browser test.
Next-session pickup:
- [ ] **Step 6 — upload the real interface.** Re-render locally first (`python python/Px_interface.py
      --config config/config.yaml --output_dir output` — picks up the 2D-default + `scene.domain.x` panel-
      clearance fix). Then get the HTML onto the box (SSH/scp closed by design):
      - **Option 1 (recommended):** S3 bucket in `eu-north-1` + `s3:GetObject` on the instance role →
        on the box `aws s3 cp s3://…/Serac_Px_interface.html /var/www/webapp/` (private via S3 gateway
        endpoint; scales to a large HTML; becomes the future publish path). *Additive TF change, but
        applying it also pulls in the staged EC2 replacement — see warning below.*
      - **Option 2:** base64-over-SSM (`aws ssm send-command`) — no IAM change, but ~100 KB payload cap,
        so only for a small HTML.
- [ ] ⚠️ **Before any `terraform apply`:** the staged `monitoring.tf` + boot-retry `user_data` changes
      REPLACE the EC2 (`user_data_replace_on_change`), wiping the hand-installed nginx and any uploaded
      interface. Plan for one deliberate re-provision, then re-upload. Don't apply casually.
- [ ] Optional: silence the browser "Not secure" warning — `terraform output tls_cert_pem >
      serac-internal.crt`, install as a trusted root on each machine (Step 7 in `aws-vpn/instructions.md`).
- [ ] Later: M365 SSO (ALB + Entra OIDC) for per-user identity/audit; gate real-data serving on it.
- [ ] RDS phase: `DATA.load_new_df` RDS source mode; Fargate/Batch rebuild job; EventBridge trigger;
      S3 publish + serving-box sync.

**Resource IDs (region `eu-north-1`):** not hard-coded here — fetch live from Terraform:
`terraform -chdir=aws-vpn output` (e.g. `ec2_instance_id`, `ec2_private_ip`, `vpn_connection_id`,
`tunnel1_address`). Health check any time: `bash aws-vpn/healthcheck.sh` (auto-discovers IDs from
outputs). SSM shell:
`aws ssm start-session --target "$(terraform -chdir=aws-vpn output -raw ec2_instance_id)" --region eu-north-1`.
