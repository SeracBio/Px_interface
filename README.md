# Px_interface

Standalone repo for building the **per-gene 3D Px interface** — the interactive
`Serac_Px_interface.html` (dots = one per gene on SAR predictability × OpenTargets
association × MS score, with compound panels, volcanoes, structure thumbnails,
pin/hide, filters, session save/load, and CSV export).

Extracted from `MS_ML` so the interface can be built and iterated on its own. The
rendering engine is [`python/functions.py`](python/functions.py) (`fn.plot_3d_interface`);
the driver is [`vignettes/MS_Interface.ipynb`](vignettes/MS_Interface.ipynb).

## Setup

```bash
# dedicated env (never use base conda)
conda create -n px python=3.12
conda activate px
pip install -r requirements.txt
python -m ipykernel install --user --name px --display-name "Python (Px)"
```

One external dependency: `serac_df` in the build cell calls `get_df` from the
sibling **CDD_Vault_API** repo, reached via `sys.path` → `~/CDD_Vault_API/python`.
Clone that repo next to this one (and set up its `~/.cdd_token`) if you run the
build branch. The load branch (`IFACE_OVERWRITE=False`) does not need it.

## Run

### CLI

The build/render pipeline runs headless via [`python/Px_interface.py`](python/Px_interface.py).
The script self-locates the repo root and `chdir`s there, so it can be launched from **any**
working directory:

```bash
conda activate px
python python/Px_interface.py
```

| Flag | Default | Purpose |
|---|---|---|
| `--config` | `config/config.yaml` | path to the YAML config |
| `--output_dir` | `output` | base dir for the HTML + volcanoes (`interfaces/` is created under it) |

Whether it rebuilds or just loads is set by `IFACE_OVERWRITE` in the config (see below), same as the notebook.

To write the interface into the Dropbox ML folder (quote the path — it contains spaces):

```bash
python python/Px_interface.py --config config/config.yaml --output_dir "/mnt/c/Users/gtamo/Serac Biosciences Dropbox/Serac_team/4_Data_Sciences/15_ML/"
```

### Notebook

Launch Jupyter **from the repo root** (the notebook's first cell `%cd ../.` sets the
working dir to the root so `import python.functions` resolves):

```bash
jupyter lab    # or: jupyter notebook
```

Open `vignettes/MS_Interface.ipynb` and run top to bottom.

- **`IFACE_OVERWRITE = True`** — rebuild the render inputs (`iface_df`, `compounds_df`,
  `meas`, `plate2date`) from the source tables and the FBX tranches, then save them to
  `IFACE_DIR` and render. Referenced thumbnail/volcano files must exist on disk.
- **`IFACE_OVERWRITE = False`** — load the saved inputs and render only (near-instant;
  skips the heavy combine cells).

All tunable parameters and data paths live in [`config/config.yaml`](config/config.yaml).
Output paths are switched by the `interface_output` knob (`GTLOCAL` vs `DROPBOX_ML`).

## Layout

| Path | Purpose |
|---|---|
| `python/functions.py` | Rendering engine + data-ingest/volcano helpers (`plot_3d_interface`, `load_proteomics_data`, `load_fbx_tranche`, `recompute_volcanoes`, …) |
| `vignettes/MS_Interface.ipynb` | Step-by-step build + render driver |
| `config/config.yaml` | All parameters and data paths |
| `wiki/wiki.md` | Durable repo notes — read at session start |
| `CLAUDE.md` | Collaboration rules + local-only data policy |

## AWS — common commands

The interface is served from a **private EC2 box inside a VPC**, reachable only over the
FortiGate↔VPC Site-to-Site VPN. There is no public IP and **no SSH** — management is via
**SSM Session Manager**, file transfer via **S3 over the gateway endpoint**. The Terraform
stack lives in [`aws-vpn/`](aws-vpn/); the full runbook is
[`aws-vpn/instructions.md`](aws-vpn/instructions.md), decisions are in
[`docs/aws_docs.md`](docs/aws_docs.md).

Resource IDs are never hard-coded here — fetch them live from Terraform. Region is `eu-north-1`.

```bash
terraform -chdir=aws-vpn output            # all outputs
terraform -chdir=aws-vpn output -raw ec2_instance_id
terraform -chdir=aws-vpn output -raw webapp_url
```

### Prerequisites

```bash
aws configure                              # once — credentials + region eu-north-1
aws sts get-caller-identity                # verify the CLI is authenticated
```

Needs the `session-manager-plugin` installed alongside the AWS CLI. FortiClient VPN must be
connected for anything that talks to the box over HTTPS (the AWS API calls themselves do not
need the VPN).

> **Security:** `aws configure` stores a long-lived access key. To replace it with short-lived SSO
> credentials and MFA (IAM Identity Center), follow the local runbook `docs/pwd_security.md`
> (gitignored, so it exists only on the admin's machine).

### Access the instance

```bash
# interactive shell on the box
aws ssm start-session --region eu-north-1 \
  --target "$(terraform -chdir=aws-vpn output -raw ec2_instance_id)"

# one-off command without a shell
aws ssm send-command --region eu-north-1 \
  --instance-ids "$(terraform -chdir=aws-vpn output -raw ec2_instance_id)" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl status nginx --no-pager"]'

# read that command's output back
aws ssm get-command-invocation --region eu-north-1 \
  --command-id <COMMAND_ID> \
  --instance-id "$(terraform -chdir=aws-vpn output -raw ec2_instance_id)"
```

Useful on-box diagnostics: `systemctl status provision-webapp`,
`journalctl -u provision-webapp`, `ls /var/www/webapp/Px_interface/`.

### Move files to the instance

SSM Session Manager has no native file transfer. Stage through the **interface S3 bucket** —
the instance role already has read access, and traffic stays on the S3 gateway endpoint.

```bash
BUCKET=$(terraform -chdir=aws-vpn output -raw interface_bucket)
IID=$(terraform -chdir=aws-vpn output -raw ec2_instance_id)

# 1. laptop -> S3
aws s3 sync tmp/out/interfaces/ "s3://$BUCKET/interfaces/" --region eu-north-1

# 2. S3 -> box (the boot script does the same sync, so a reboot also works)
aws ssm send-command --region eu-north-1 --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters "commands=[\"aws s3 sync s3://$BUCKET/interfaces/ /var/www/webapp/Px_interface/ --region eu-north-1\",\"chown -R root:nginx /var/www/webapp\"]"
```

For a **small** file (< ~100 KB) and no S3 round-trip, base64 it into a `send-command`
instead. Anything larger must go through S3.

To pull a file **off** the box, write it to the bucket from the instance
(`aws s3 cp /path/file s3://$BUCKET/…`) and `aws s3 cp` it down locally.

### Check the stack is healthy

```bash
bash aws-vpn/healthcheck.sh     # read-only: EC2, SSM, VPC endpoints, VPN tunnels, nginx
```

Expect `7 ok / 0 fail`. Tunnels reading `1/2 UP` is normal (one active, one standby).

### Rebuild / change the infrastructure

```bash
cd aws-vpn
terraform init -backend-config=backend.hcl   # -reconfigure if the backend changed
terraform validate
terraform plan                                # always read the plan first
terraform apply
```

Two files must exist locally before an apply (both gitignored, neither is in the repo):

- **`aws-vpn/backend.hcl`** — remote state bucket + lock table; copy
  `backend.hcl.example` and fill from `terraform -chdir=aws-vpn/bootstrap output`.
  Without it, `init` fails with "file could not be read".
- **`~/.serac_aws`** — one line, `serac_user:$2y$…`, created with
  `htpasswd -nbB serac_user 'password' > ~/.serac_aws && chmod 600 ~/.serac_aws` (password from
  1Password). Without it the placeholder hash is baked in and nginx returns **HTTP 500** after
  the password prompt.

> **An `apply` that touches `user_data` or the private IP REPLACES the EC2.** The replacement
> re-provisions itself from S3 at boot, so upload the interface to S3 *before* applying.
>
> **Keep the AMI pinned** (`ec2_ami_id` in `terraform.tfvars`). A floating `most_recent`
> AL2023 once shipped a broken SSM agent and locked the box out — nginx kept serving while
> management was dead. When bumping the pin, confirm `SSM ping: Online` before trusting it.

### Tear down

```bash
cd aws-vpn && terraform destroy              # main stack
cd aws-vpn/bootstrap && terraform destroy    # only when done with the project entirely
```

The state bucket and lock table carry `prevent_destroy = true` — remove those lifecycle
blocks first or the bootstrap destroy errors out.

### Browse the interface

```bash
terraform -chdir=aws-vpn output -raw webapp_url    # https://advantedge.seracbio.com/Px_interface/
terraform -chdir=aws-vpn output -raw ec2_private_ip # fallback if DNS isn't forwarded yet
```

Requires the VPN connected, then Basic Auth (`serac_user` + the shared password). The
self-signed cert warning is expected; install the CA from
`terraform -chdir=aws-vpn output -raw tls_cert_pem` to silence it.

> **Only synthetic data goes on this box.** Auth is a single shared password with no
> per-user audit trail — real chemistry data is gated on M365 SSO landing first. See
> [CLAUDE.md](CLAUDE.md).

### PostgreSQL RDS (`aws-rds/`)

A private PostgreSQL 18 instance (`px-rds`, `db.t4g.micro`, 20 GB gp3, encrypted) sits in two
new subnets of the same VPC (`172.20.6.0/24`, `172.20.7.0/24`). It is a **separate Terraform
stack**: it reads the VPC and the VPN gateway but does not manage them, so a destroy of
`aws-rds/` cannot touch `aws-vpn/`. Port 5432 accepts only the office LAN and the FortiClient
pool. RDS keeps the master password in Secrets Manager (never in Terraform state), and the
server refuses connections without TLS.

```bash
cd aws-rds
cp ../aws-vpn/backend.hcl .                  # same bucket + lock table, own state key
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan                   # read the plan first
terraform apply tfplan
```

Use the CLI in the `ML` env, with the VPN connected. **Every command needs a login**, given after
the command name (the `RDS_*` keys in `config/config.yaml` hold the defaults):

- **Personal — `--user NAME`** (or `RDS_USER`): the CLI asks for that user's password. With
  `--host` (or `RDS_HOST`) too, it needs no AWS access.
- **Master — `--admin`:** the CLI reads the endpoint and the `px_admin` password from AWS at run
  time (Secrets Manager), so your AWS keys must allow it. `create-demo` and `create-user` need it.

Without a login a command stops with a message: the CLI never falls back to the master login.

```bash
python python/px_rds.py check --admin                # endpoint, DNS, TCP 5432, TLS + SQL, one line each
python python/px_rds.py pull px_demo --user <name>   # -> data/rds/px_demo.parquet (or --out file.csv)
python python/px_rds.py set-password --user <name>   # change your own password
python python/px_rds.py create-demo --admin          # replace px_demo with synthetic rows, compare
python python/px_rds.py create-user <name> --admin   # new login (hidden prompt); again = new password
```

Every connection uses `sslmode=verify-full` against `global-bundle.pem`. If `check` stops at
`tcp`, the FortiGate policy does not permit 5432 to `172.20.6.0/23`.

**Personal logins (colleagues, DBeaver, pgAdmin, psql).** RDS rotates the master password every
7 days, so do not save it in a client, and do not share it. Give each person a login:

1. Admin: `create-user <name> --admin` with a temporary password. Send it, and the endpoint name
   from `terraform -chdir=aws-rds output -raw db_address`, through 1Password.
2. Colleague: clone the repo, connect the VPN, and install `psycopg2-binary pandas pyarrow pyyaml
   boto3` (or use the `ML` env). Set `RDS_HOST` and `RDS_USER` in the local `config/config.yaml`,
   or pass `--host` and `--user` on each command. No AWS account is needed.
3. Colleague: `python python/px_rds.py set-password` asks for the temporary password, then for the
   new one twice.

A login can read and write every table and create tables in `public`, but it cannot drop the
tables that `px_admin` owns. Only a SCRAM hash of a password reaches the server; keep passwords
in 1Password. Client settings: host = the endpoint name, port 5432, database `px`, SSL mode
`verify-full`, root certificate `global-bundle.pem` (from a Windows client:
`\\wsl$\<distro>\<repo path>\global-bundle.pem`). To remove a login that owns no tables, run
`REVOKE CREATE ON SCHEMA public FROM <name>; DROP ROLE <name>;` as `px_admin`.

> **CAUTION — `create-demo` replaces the table named by `RDS_DEMO_TABLE`.** Never point it at a
> real table.
>
> **CAUTION — tear down.** Deletion protection is on. To remove the database, run
> `terraform apply -var deletion_protection=false`, then `terraform destroy`. The destroy keeps a
> final snapshot `px-rds-final`; delete it by hand when you do not need it. Without that
> snapshot, all data in the database is lost.
>
> **Only synthetic data goes into this database** until a decision on real data. See
> [CLAUDE.md](CLAUDE.md).

## Data policy

Chemistry data (SMILES, compound IDs, structures, screening results) **stays on this
machine** — see [CLAUDE.md](CLAUDE.md). `data/`, `output/`, and rendered `interfaces/`
are gitignored.
