# Understanding the Px interface AWS architecture

A plain-language walkthrough of every piece of the infrastructure, why it exists, and how the parts
fit together. Read this next to the diagrams in `aws_architecture_mermaid.md`. The goal is that you
could rebuild or explain this setup yourself.

**The one-sentence summary:** we run a tiny web server on a private machine inside AWS that has *no
connection to the public internet at all*, and the only way to reach it is to first be on Serac's
corporate network through the FortiClient VPN — so the chemistry data on it is never exposed.

---

## The big idea: "private by default"

Most websites live on a server with a public address that anyone on the internet can reach, and you
protect them with passwords and firewalls. We do the **opposite**: the server has **no public
address**. It sits in a private network segment in AWS, and we build an encrypted "tunnel" between
Serac's office network and that private segment. If you're not on Serac's network, the server
simply doesn't exist as far as the internet is concerned — there is nothing to attack, scan, or
brute-force. This is the strongest possible posture for sensitive data, and it's why we chose it
over a public login page.

Everything below is in service of that idea.

---

## Part 1 — The Serac side (on-premises + remote)

### Office employee (60C)
Someone physically in the office is on the LAN `192.168.146.0/24` (a private IP range used inside the
building). Their traffic to AWS goes out through the office firewall.

### Remote employee (you, on FortiClient)
When you work from home, you launch **FortiClient** and log in. FortiClient builds an encrypted
connection from your laptop to the office firewall, and hands your laptop a temporary IP address from
a dedicated pool: **`10.0.14.100–10.0.14.199`**. From that moment your laptop behaves as if it were
inside Serac's network, even though you're at home. This is why we had to explicitly account for the
`10.0.14.0/24` range — a remote user is *not* on the `192.168.146.x` office LAN.

### FortiGate firewall (60F)
This is Serac's corporate firewall/VPN appliance. It has a **public IP address** (reachable from the
internet, because it has to be — it's the front door). In AWS terminology, this device is called the
**Customer Gateway**: "customer" = us, "gateway" = the device on our side of the tunnel. It's the
Serac end of the encrypted tunnel to AWS.

---

## Part 2 — The VPN (the encrypted bridge to AWS)

This is the heart of the design: a **Site-to-Site VPN** ("S2S"). "Site-to-site" means it connects two
*networks* (Serac's network ↔ the AWS network), as opposed to a "client VPN" that connects one
laptop. Three AWS objects work together:

### Customer Gateway (CGW)
Just a **record in AWS of the FortiGate's public IP**. It doesn't do anything by itself — it tells
AWS "the device at this public IP is the thing on the other end of the tunnel."

### Virtual Private Gateway (VGW)
The **AWS end** of the tunnel — think of it as AWS's own VPN appliance, attached to our VPC. Traffic
arriving from Serac lands here and gets injected into our AWS network; traffic going back to Serac
leaves through here.

### Site-to-Site VPN Connection
The actual encrypted link between the CGW and the VGW. AWS always builds **two tunnels** for
redundancy (one active, one standby) — if one drops, traffic fails over to the other. The encryption
settings both sides must agree on:
- **IKEv2** — the modern protocol for negotiating the secure connection.
- **AES-256** — the encryption strength.
- **SHA-256** — integrity checking (detects tampering).
- **DH group 14** — the key-exchange math.
- **Pre-shared keys (PSKs)** — a shared secret each tunnel uses. AWS auto-generates these; IT pastes
  them into the FortiGate. (They live only in encrypted Terraform state — never in the repo.)

### Static routes + route propagation
For traffic to actually flow, AWS has to know *which* Serac networks are reachable through the tunnel.
We declared two **static routes**: `192.168.146.0/24` (office) and `10.0.14.0/24` (remote pool). AWS
then **propagates** these into the VPC's route table, so the EC2 instance knows "to reach those
addresses, send the packets back through the VGW." Without the `10.0.14.0/24` route, your replies as a
remote user would have nowhere to go — this is exactly the gotcha we fixed.

---

## Part 3 — The VPC (our private network in AWS)

A **VPC (Virtual Private Cloud)** is your own isolated private network inside AWS — your own slice of
the cloud with its own IP address range, subnets, and routing rules. Ours is **`172.20.0.0/16`**.

> **Why `172.20.0.0/16` and not the default `10.0.0.0/16`?** Two networks that use the same IP range
> can't route to each other (addresses would be ambiguous). Ridgeline/portfolio companies already use
> lots of `10.x` addresses, *and* the FortiClient pool `10.0.14.x` sits inside `10.0.0.0/16`. If our
> VPC were also `10.0.0.0/16`, it would think `10.0.14.x` was *local* and never send your traffic back
> through the tunnel. Moving to `172.20.0.0/16` (a different private range entirely) avoids all
> collisions.

### Subnets
A VPC is divided into **subnets** — smaller address ranges, each living in one data-center
(Availability Zone). We use **private subnets only**:
- **Private subnet `172.20.2.0/24`** — no route to the internet. **This is where the EC2 lives**, which
  is what makes it unreachable from the public internet.

There is deliberately **no public subnet and no Internet Gateway.** A Site-to-Site VPN doesn't need
one, and the EC2 reaches the AWS services it needs (S3, SSM) through VPC endpoints — see Part 5 — so
nothing in this stack ever touches the public internet.

### Route tables
A **route table** is a set of "to reach X, send traffic to Y" rules attached to a subnet. The private
route table says: reach Serac's networks → via the VGW (the propagated VPN routes); reach S3 → via the
S3 gateway endpoint (below). It has **no** `0.0.0.0/0 → internet` rule, so nothing can wander out to
the open internet.

---

## Part 4 — The server itself (EC2)

**EC2 (Elastic Compute Cloud)** is just "a virtual computer you rent in AWS." Ours:
- **t3.micro** — the smallest practical size (1 GB RAM). Plenty, because it only *serves* pre-built
  static files; it doesn't run the heavy pipeline.
- **Amazon Linux 2023 (AL2023)** — AWS's own Linux distribution. **The AMI is pinned** (`ec2_ami_id` in
  `terraform.tfvars`), not floating on "latest": a newer AL2023 build once shipped an `amazon-ssm-agent`
  that wouldn't register, which — on a box you can only reach via SSM — locks out management on the next
  replace. Bump the pin deliberately and confirm SSM comes back before trusting it.
- **No public IP** — cannot be reached from the internet; only via the tunnel.
- **Encrypted gp3 root disk** — its storage is encrypted at rest automatically.
- **IMDSv2 enforced** — a hardening setting. Every EC2 has an internal "metadata service" that hands
  out its credentials; IMDSv2 requires a token to query it, which blocks a common class of attacks
  (SSRF) where a tricked web app leaks those credentials.

### user_data (the boot script)
When the instance first starts, AWS runs a script we provided (`user_data`). It:
1. Installs nginx.
2. Pulls the TLS certificate + key from Parameter Store.
3. Writes the nginx config and the `.htpasswd` file.
4. Validates the config (`nginx -t`) and starts nginx.

So the machine configures *itself* on first boot — nothing is set up by hand. (See diagram #3.)

### nginx
The web server software. It's configured to:
- Serve the files in `/var/www/webapp/` (where `Serac_Px_interface.html` lives).
- Force **HTTPS** (encrypt the browser↔server connection) and redirect plain HTTP to it.
- Require **HTTP Basic Auth** — the `serac_user` + shared-password prompt — as a second layer on top
  of the VPN.

---

## Part 5 — Getting outbound access WITHOUT the internet (VPC endpoints)

Here's a puzzle: the private subnet has no internet access, but at boot the instance needs to (a)
download nginx and (b) fetch its TLS cert from AWS's Parameter Store. Both normally require reaching
AWS service addresses "on the internet."

The usual fix is a **NAT gateway** (a device that lets private machines make outbound-only internet
connections) — but it costs ~$32/month and technically routes through the internet edge. Instead we
use **VPC endpoints**, which are private doorways from your VPC *directly* to specific AWS services,
without touching the internet at all:
- **Interface endpoints** `ssm`, `ssmmessages`, `ec2messages` — these three together enable **SSM**
  (Session Manager shell + Parameter Store access). They appear as network cards inside the private
  subnet.
- **S3 gateway endpoint** — a free route to S3. AL2023's software packages (`dnf install`) are hosted
  in S3, so this is how the instance downloads nginx.

Result: the instance is fully functional yet has **zero** internet exposure. This is both cheaper and
more secure than a NAT gateway.

---

## Part 6 — Administering the box without SSH (SSM Session Manager)

Normally you'd SSH into a server (port 22 + a key pair). We don't — there's no SSH port open and no
key pair. Instead we use **SSM Session Manager**: a service where the instance runs a small agent that
*dials out* to AWS (via those `ssm` endpoints), and you get a shell through the AWS API. Benefits:
- **No inbound ports** — nothing to attack.
- **No SSH keys** to manage or leak.
- **Every session is logged** in CloudTrail (an audit trail of who connected when).

You start a session with `aws ssm start-session --target <instance-id>`.

---

## Part 7 — Secrets and permissions

### SSM Parameter Store (the TLS cert + key)
The self-signed TLS certificate and its private key are stored as **SecureString** parameters —
encrypted at rest with a KMS key (AWS's Key Management Service). The instance reads them at boot. This
keeps the private key out of the code and out of the disk image; it's fetched fresh, decrypted in
memory, and written to nginx's config directory.

> **Subtle but important:** there is **no KMS VPC endpoint** in this stack, yet decryption still
> works. That's because when the instance calls `ssm get-parameter --with-decryption`, the SSM
> service performs the KMS decrypt *server-side* using the role's `kms:Decrypt` permission and returns
> plaintext — the instance never makes a direct KMS network call. So the `ssm` endpoint is all that's
> needed; the `kms:Decrypt` grant in the IAM role is what authorises it.

### IAM role + instance profile
**IAM (Identity and Access Management)** controls who/what can do what in AWS. Instead of putting AWS
credentials on the box, we attach a **role** to it (via an "instance profile"). The role grants
exactly three things and nothing more (least privilege):
1. Use **SSM Session Manager** (the `AmazonSSMManagedInstanceCore` policy).
2. **Read** the two TLS parameters.
3. **Decrypt** them with the KMS key.

The instance automatically assumes this role — no secret keys stored anywhere.

### Self-signed TLS certificate
A normal HTTPS certificate is issued by a public authority and tied to a public domain name. We have
neither (the box is private, no public DNS), so Terraform generates a **self-signed** cert. Browsers
show a "not trusted" warning the first time; you either click through, or (better) distribute the cert
to employees' trust stores once. Note the TLS here is largely belt-and-suspenders — the **IPsec tunnel
already encrypts everything in transit** — so a self-signed cert is perfectly adequate. (We also left
the HSTS header off, because combined with a self-signed cert it can hard-lock browsers.)

---

## Part 8 — How Terraform manages all this

**Terraform** is "infrastructure as code": you describe the desired infrastructure in `.tf` files, and
Terraform creates/updates it to match. The benefit is that the whole setup is reproducible, reviewable,
and version-controlled — not a pile of manual clicks in the AWS console.

### State
Terraform records what it has built in a **state file**. That file is sensitive (it contains the VPN
pre-shared keys and the TLS private key), so we don't keep it on a laptop. It lives in:
- an **S3 bucket** — encrypted, versioned, and locked to TLS-only access, and
- a **DynamoDB table** — used as a **lock** so two people can't run Terraform at the same time and
  corrupt the state.

### Bootstrap
There's a chicken-and-egg problem: the S3 bucket that holds the state must exist *before* Terraform can
use it. So a tiny separate `bootstrap/` configuration creates the bucket + lock table first (using a
local state file, since it has no secrets worth protecting). You run it once, then the main stack uses
the bucket. Both the bucket and table are marked `prevent_destroy` so they can't be deleted by
accident.

---

## Putting it together: two journeys

**A page load (diagram #2):** You're on FortiClient → you open `https://<private-IP>` → FortiGate
encrypts it into the IPsec tunnel → it arrives at the VGW inside AWS → the route table delivers it to
the EC2 in the private subnet → nginx asks for the basic-auth password → then serves
`Serac_Px_interface.html` back down the same tunnel. At no point does any of this traverse the public
internet.

**First boot (diagram #3):** Terraform launches the EC2 with the boot script and IAM role → the script
installs nginx through the S3 endpoint → fetches and decrypts the TLS cert through the ssm endpoint +
KMS → writes the nginx config + password file → starts nginx. The box is now serving, having never
touched the internet.

---

## Cost recap

| Piece | Monthly (approx.) |
|---|---|
| Site-to-Site VPN connection ($0.05/hr) | ~$36 |
| 3 interface VPC endpoints (~$8.5 each) | ~$25 |
| EC2 t3.micro (serving, always on) | ~$8.5 |
| EBS gp3 root, 30 GB | ~$2.5 |
| S3 state + DynamoDB + Parameter Store + S3 gateway endpoint | pennies (S3 gateway is free) |
| Data transfer (light) | ~$1 |
| **Total** | **~$73/mo** |

(The VPN connection and endpoints are the bulk — the trade for "no public exposure, no NAT." A NAT
gateway instead of endpoints would be ~$32 and less secure.)

---

## Mini-glossary

- **VPC** — your private network inside AWS.
- **Subnet** — a slice of a VPC in one data center; "public" = has internet route, "private" = doesn't.
- **CIDR** (e.g. `172.20.0.0/16`) — a way of writing a range of IP addresses; the `/16` says how big.
- **IGW (Internet Gateway)** — a VPC's connection to the internet (not used in this stack).
- **VGW (Virtual Private Gateway)** — the AWS end of a VPN.
- **CGW (Customer Gateway)** — AWS's record of *your* VPN device (the FortiGate).
- **Site-to-Site VPN** — an encrypted tunnel joining two whole networks.
- **IPsec / IKEv2** — the protocols that encrypt and negotiate that tunnel.
- **EC2** — a virtual server you rent.
- **AMI** — the OS image an EC2 boots from (here, Amazon Linux 2023).
- **Security Group** — a per-resource firewall (what traffic is allowed in/out).
- **VPC endpoint** — a private doorway from your VPC to an AWS service, bypassing the internet.
- **NAT gateway** — a device giving private machines outbound-only internet (we avoided it).
- **SSM (Systems Manager)** — AWS tooling; here for Session Manager (shell) + Parameter Store (secrets).
- **IAM role** — a set of permissions a resource assumes, instead of storing credentials.
- **KMS** — AWS's key-management/encryption service.
- **IMDSv2** — hardened access to an instance's internal metadata/credentials service.
- **Terraform** — infrastructure-as-code tool; **state** = its record of what it built.
- **HTTP Basic Auth** — the simple username/password browser prompt.
