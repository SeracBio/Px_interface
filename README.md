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
| `--output_dir` | `output` | base dir for the HTML + volcanoes (`interfaces/` is created under it), or the config `PUBLISH_URL` to build and publish to AWS (see "Publish the interface") |
| `--show_plate` | config `SHOW_PLATE` | dates (`YYYYMMDD`, comma-separated) whose plates start ticked |

Four config flags set what the CLI rebuilds (the notebook uses the same flags):

- **`CHEMLIB_OVERWRITE`** — get the compound library from CDD Vault again; `false` loads the saved CSV.
- **`UPDATE_PNGS`** — get new or missing compound PNGs from CDD Vault into `SRB_PNG_DIR`. It does not
  download a PNG again if the file already exists.
- **`DFRAW_OVERWRITE`** — rebuild `df_raw` and `MS` from the three old proteomics exports (2026-04-29,
  2026-05-20, 2026-05-29), and write them over `DFRAW_PATH` and `MS_PATH`. The FBX tranches are not in
  these files; the build loads them separately.
- **`IFACE_OVERWRITE`** — rebuild the render inputs, the compound panels, the volcanoes and the thumbnails
  (see below).

> **CAUTION:** `DFRAW_OVERWRITE: true` replaces the files at `DFRAW_PATH` and `MS_PATH`. Make a copy of
> them before the first run.

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
  `IFACE_DIR` and render. The build writes the volcanoes and the thumbnails next to the HTML.
- **`IFACE_OVERWRITE = False`** — load the saved inputs and the saved compound panels, and render
  only (fast; skips the heavy combine cells). This path does not write volcanoes or thumbnails, so
  the output folder must already hold `interfaces/volcanoes_px/` and `interfaces/srb_png/`.

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

### Publish the interface (one command)

Build the interface and push it to the box in one run. Real Px interface data can go on this box
(see the note under "Browse the interface").

```bash
python python/Px_interface.py --config config/config.yaml \
  --output_dir "https://advantedge.seracbio.com/Px_interface/"
```

When `--output_dir` is the config `PUBLISH_URL`, the CLI:

1. Finds the bucket (from the AWS account) and the EC2 (by its `Name` tag), and checks that SSM is
   Online. It does this before the build, so a problem stops it in seconds.
2. Builds in `PUBLISH_STAGE_DIR` (default `output`), as a normal build does.
3. Runs `aws s3 sync` from `interfaces/` to `s3://<bucket>/interfaces/`. The sync sends only the
   changed files, and it skips the build-only files in `PUBLISH_EXCLUDE`.
4. Sends one SSM command. The EC2 copies the files into `/var/www/webapp/Px_interface/`, the HTML
   last, and then sets the nginx owner and the file modes.

The CLI prints counts only, because the file names hold compound IDs. It needs AWS credentials that
can write to the bucket and send SSM commands. It does not need the VPN. The `PUBLISH_*` keys in
`config/config.yaml` hold the settings.

### Move files to the instance

The publish command above does these two steps for the interface. Use them by hand for other files.

SSM Session Manager has no native file transfer. Stage through the **interface S3 bucket** —
the instance role already has read access, and traffic stays on the S3 gateway endpoint.

```bash
BUCKET=$(terraform -chdir=aws-vpn output -raw interface_bucket)
IID=$(terraform -chdir=aws-vpn output -raw ec2_instance_id)

# 1. laptop -> S3
aws s3 sync tmp/out/interfaces/ "s3://$BUCKET/interfaces/" --region eu-north-1

# 2. S3 -> box (the boot script does the same sync, so a reboot also works)
#    --exact-timestamps: else the sync skips a changed file of the same size, for example a new HTML
aws ssm send-command --region eu-north-1 --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters "commands=[\"aws s3 sync s3://$BUCKET/interfaces/ /var/www/webapp/Px_interface/ --region eu-north-1 --exact-timestamps\",\"chown -R root:nginx /var/www/webapp\"]"
```

For a **small** file (< ~100 KB) and no S3 round-trip, base64 it into a `send-command`
instead. Anything larger must go through S3.

**Bucket rules** (`aws-vpn/s3.tf`): only the EC2 can read files from the bucket, because the bucket
policy denies `s3:GetObject` unless the request comes through the VPC's S3 gateway endpoint. From your
laptop you can list and write, but `aws s3 cp s3://…`, `head-object` and console downloads fail with
`AccessDenied`. S3 deletes an old (replaced or deleted) file version after `interface_noncurrent_days`
(7) days.

To pull a file **off** the box, use the base64 method above in reverse: print the file as base64 in a
`send-command`, and decode the output locally. This works only for a small file.

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
  `htpasswd -nB serac_user > ~/.serac_aws && chmod 600 ~/.serac_aws` (it asks for the password from
  1Password two times, so the password does not go into the shell history). Without it the placeholder
  hash is baked in and nginx returns **HTTP 500** after the password prompt.

**Change the interface login** (user name and password):

1. `cp -p ~/.serac_aws ~/.serac_aws.bak`, then `htpasswd -nB <user> > ~/.serac_aws && chmod 600 ~/.serac_aws`.
2. `terraform plan`, then `terraform apply`. The apply writes the new line to the SSM parameter
   `/<project>/webapp/htpasswd`.
3. The box reads that parameter only at boot. An apply that also replaces the EC2 gives the new login
   at once. Else, copy the parameter into `/etc/nginx/.htpasswd` on the box by hand (SSM session).
4. Put the new password in 1Password, and tell the users.

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

> **You can put real Px interface files on this box** (decision of 2026-10-01). Three controls
> protect them: the box is reachable only over the VPN (no public IP), nginx asks for the
> Basic-Auth password (shared through 1Password), and TLS encrypts the traffic. All users share
> one password, so the logs cannot show which person opened the interface. M365 SSO stays the
> upgrade for that. This rule applies to the Px interface only. The assistant still never reads
> real data (see [CLAUDE.md](CLAUDE.md)).

### PostgreSQL RDS (`aws-rds/`)

Two private PostgreSQL 18.3 databases run in the VPN VPC. They share two subnets (`172.20.6.0/24`,
`172.20.7.0/24`), one security group and one parameter group (`rds.force_ssl=1`). A change to one of
those three changes both databases.

| | `px-seracbio-prod` | `px-seracbio-dev` |
|---|---|---|
| Content | a copy of `seracbio-prod` (real data), restored from a snapshot (`restore.tf`) | empty, made new (`dev.tf`) |
| Class and storage | `db.m7g.large`, 600 GB gp3, Multi-AZ | `db.t4g.micro`, 20 GB gp3, single-AZ |
| Deletion protection | on | off, because dev is disposable |
| Login | user `seracbio`, database `postgres`, port 5432 | the same |
| Password file | `~/.px_db_password` | `~/.px_db_dev_password` |
| Test script | `python/20261001_test_prod_connect.py` | `python/20261001_test_dev_connect.py` |

`aws-rds/` is a **separate Terraform stack**. It reads the VPC and the VPN gateway, but it does not
manage them, so a destroy of `aws-rds/` cannot touch `aws-vpn/`. Port 5432 accepts only the office LAN
and the FortiClient pool. The server refuses connections without TLS.

```bash
cd aws-rds
cp ../aws-vpn/backend.hcl .                  # same bucket + lock table, own state key
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan                   # read the plan first
terraform apply tfplan
terraform output                             # restored_endpoint, dev_endpoint, ...
```

**Passwords.** Each database has one fixed password, shared through 1Password. RDS does not rotate it.
The password file holds one line, `user:password`. Terraform and the test scripts read it. Use printable
ASCII, 8 to 128 characters, and never `/`, `@`, `"` or a space.

> **CAUTION — the passwords are in the Terraform state.** Anyone who can read the state bucket can read
> them. Keep that access narrow.

**Connect.** You need the VPN, `psycopg2-binary`, `global-bundle.pem` (repo root) and the password.
You do not need an AWS account, because the test scripts make no AWS call.

```bash
conda activate ML                                     # --prompt fails under `conda run` (stdin is closed)
python python/20261001_test_dev_connect.py            # reads ~/.px_db_dev_password
python python/20261001_test_dev_connect.py --prompt   # type the password instead
python python/20261001_test_prod_connect.py --prompt  # runs a query and prints the first 10 rows; --out file.csv
```

Each connection uses `sslmode=verify-full` with `global-bundle.pem`. Client settings (DBeaver, pgAdmin,
psql): host = the endpoint from `terraform -chdir=aws-rds output`, port 5432, database `postgres`, SSL
mode `verify-full`, root certificate `global-bundle.pem` (from a Windows client:
`\\wsl$\<distro>\<repo path>\global-bundle.pem`).

> **CAUTION — `px-seracbio-prod` holds real data.** Never write a query result into `python/` or another
> tracked folder: `uniquecontrast` holds compound identifiers.
>
> **CAUTION — tear down.** A `terraform destroy` removes both databases. To remove only dev, set
> `create_dev = false` and apply. Prod has deletion protection, so apply with
> `restore_deletion_protection = false` before a destroy. Each destroy keeps a final snapshot
> (`px-seracbio-prod-final`, `px-seracbio-dev-final`); delete it by hand when you do not need it.
> Without that snapshot, all data in the database is lost.

## Data policy

Chemistry data (SMILES, compound IDs, structures, screening results) **stays on this
machine** — see [CLAUDE.md](CLAUDE.md). `data/`, `output/`, and rendered `interfaces/`
are gitignored. The one exception is the Px interface publish to AWS (see "Publish the
interface").
