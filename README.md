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
| `--show_plate` | config `SHOW_PLATE` | Plates-filter blocks that start ticked, comma-separated: a date (`YYYYMMDD`) or `validation YYYYMMDD` |

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

The build reads two kinds of tranche folders (date-named subfolders, `YYYYMMDD…`):

- **`FBX_DIR`** — screen tranches (`*_FBX_REPORT.csv`, `*_FBX_MEASURE.csv`, `*_FBX_MSSCORE.csv`). A plate
  gets the date of its folder. If two folders hold the same plate, the later folder gives the date.
- **`VALIDATION_DIR`** — validation exports (`*_REPORT.csv`, `*_MEASURE.csv`, with the plate in the
  contrast name). A folder date goes only to plates that have no date yet, so re-runs and primary-screen
  references keep their first date. The Plates filter shows one validation block per date.

If a contrast is in more than one folder, the build keeps the rows of the newest folder. It skips a
per-target MSSCORE (no contrast column) and drops a contrast with no plate in the file or in its name.
The REPORT `target` column names the gene(s) that each experiment tests (`;` between genes). Each target
gene's panel shows that experiment, also when the gene is not significant ("not significant" tag), and
it shows every batch of a compound. A change to this data needs a build with `IFACE_OVERWRITE: true`.

### Dropbox access (the cluster)

The Dropbox path keys in the config (`DROPBOX_*`, `PX_2026*`, `FBX_DIR`, `VALIDATION_DIR`, `PX_PARQUET_DIR`) keep
the laptop paths (`/mnt/c/Users/gtamo/Serac Biosciences Dropbox/...`). The cluster has no Dropbox client, so the
build reads these files with `rclone`, the same way as MS_ML. `DROPBOX_BACKEND` selects the method:

- **`rclone`** (the cluster) — a path under `DROPBOX_LOCAL_ROOT` maps to the remote `DROPBOX_REMOTE`
  (`.../Serac Biosciences Dropbox/Serac_team/x` → `dropbox:Serac_team/x`). `rclone cat` streams the file into
  memory, so nothing goes to the local disk. A path outside `DROPBOX_LOCAL_ROOT` stays a local path.
- **`local`** (or no key) — read every path from the disk: a laptop with the Dropbox client, or the test fixture.

`load_params` binds three readers: `params.dbx(path)` opens a file for pandas, `params.dbx_glob(dir, pattern)`
lists a folder, and `params.dbx_push(df, path)` writes a DataFrame (`.csv` or `.parquet`). The build lists each
tranche folder one time (about 1 s for each `rclone lsf`). It streams all the tranches and the two DB exports,
about 13 GB (2026-10). The one-time setup of the `dropbox:` remote and its team `root_namespace` is in the MS_ML
README, "Pulling data files from Dropbox".

> **CAUTION:** `params.dbx_push` to a Dropbox path (for example `EXPORT_COMBINED`) writes into the company Dropbox.

On the cluster, `--output_dir` is a local folder, because there is no Dropbox folder to write into. To put the
interface in the Dropbox ML folder, see "Publish the interface".

### Px data from RDS

The FBX tables (measure, report, MS score) can come from the Px database `px-seracbio-prod` (PostgreSQL,
over the VPN) instead of the Dropbox tranche folders. `PX_SOURCE` in the config selects the source:

- **`rds`** — `load_new_df` reads `px_fbx_measure` + `px_fbx_glues` (the validation exports, with the plate
  in the contrast name) and `px_fbx_report` + `px_fbx_glues_report`. `fn.rds_table` streams each table in a
  read-only session with a verified certificate (`RDS_*` keys; the password file is `RDS_CRED_FILE`). A
  contrast that was uploaded two times keeps its newest insert. The MS score is `-log10(p) × -logfc` on the
  significant, down-regulated rows of the main table: the formula of the platform's MSSCORE exports. A
  glues contrast gets no formula score; it keeps the score of an older FBX export from the archive, as in
  the last build. The validation MS score (one per target) comes from `px_ms_score_glues`. The plate dates
  come from `PX_DATES`. A build in this mode reads nothing from Dropbox.

**The association of a gene dot** (y; config `ASSOC_SOURCE: opentarget`, 2026-10-26) is the
`px_ms_score_glues` value for a validation target, else the value in the RDS `opentarget` table, else 0. The
FBX MSSCORE exports hold the same `opentarget` values. `ASSOC_SOURCE: ot_cache` gives the old y (the highest
`OT_CACHE` overall_score). The disease-area colours always come from `OT_CACHE`.

**The MS score of a gene dot** (z; user decision 2026-10-26) is the validation score for a validation
target, else the highest FBX score, else the highest April-May (`df_raw`) score, else 0, on the raw scale
(0 to about 212). The hover names the source. The z values do not change: the MS slider filters each
experiment by its own score, and a target's experiments on its validation plates use its validation score.
The top of the z axis sits a little above the highest gene that shows, pins included (config `Z_FIT_PAD`,
default 0.05 of the span; `null` fits all genes). It changes with the filters, so the plot then stretches.
- **`dropbox`** (also when the key is absent) — the `FBX_DIR` / `VALIDATION_DIR` tranche folders, as before.

The database has no date column yet, and it lacks some older experiments. `px.build_px_archive` reads
Dropbox one time and writes two things:

- `PX_DATES` (`data/PX_dates.csv`): one row per experiment of the last Dropbox build, with `uniquecontrast`,
  `molecule_batch_id`, `plate`, `date` and `source`. The RDS mode keeps the FBX experiments in this file. A
  new database experiment shows only when it has a date (a row here, or later a datestamp in the database).
- `PX_ARCHIVE_DIR` (`data/px_archive/`): the FBX rows (measure, report, MS score) of the experiments that
  the database lacks, and the exported MS score rows of the experiments that only the glues tables hold.
  If a contrast is in both, the database wins.

The steps are in `vignettes/RDS_extraction.ipynb`. To publish, use the CLI (see "Publish the interface").
To read any query result, use `fn.rds_query(sql, ...)`: the same read-only session and exact parse as
`fn.rds_table`, for example `SELECT * FROM px_ms_score_glues`. Filter `px_fbx_measure` (55M rows) with a
`WHERE` clause or a `LIMIT`.

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
The notebook writes under `OUTPUT_DIR` (`output`, the same as the CLI default).

## Layout

| Path | Purpose |
|---|---|
| `python/functions.py` | Rendering engine + data-ingest/volcano helpers (`plot_3d_interface`, `load_proteomics_data`, `recompute_volcanoes`, the Dropbox readers `dbx_open` / `dbx_glob` / `dbx_push`, the RDS readers `rds_table` / `rds_query`, `ms_score_rows`, …) |
| `python/nb_edit.py` | Edit notebook cells without touching the other cells' outputs (copied from ADME_ML; can also insert new cells) |
| `python/prune_volcanoes.py` | Delete the volcano SVGs that the page does not use (dry run unless `--apply`; see "Publish the interface") |
| `vignettes/RDS_extraction.ipynb` | The Px data from RDS: connect, archive, load, build the interface inputs |
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

### Publish the interface (AWS or Dropbox)

**AWS (one command).** Build the interface and push it to the box in one run. Real Px interface data can go
on this box (see the note under "Browse the interface").

```bash
python python/Px_interface.py --config config/config.yaml \
  --output_dir "https://advantedge.seracbio.com/Px_interface/"
```

**Dropbox ML folder (two commands).** The cluster has no Dropbox folder to write into. Build into the local
`output` folder, then copy `output/interfaces` to Dropbox with rclone (quote the path: it contains spaces):

```bash
python python/Px_interface.py --config config/config.yaml --output_dir output
rclone copy output/interfaces "dropbox:Serac_team/4_Data_Sciences/15_ML/interfaces" --checksum --transfers 32
```

The folder holds about 13,000 small files (volcanoes and thumbnails), and Dropbox limits the write operations
in the team space. `--checksum` skips each file that Dropbox already holds with the same content. Without it,
rclone uploads each file again, because Dropbox cannot change a file time. `--transfers 32` commits the uploads
in batches of 32, not 4. On `too_many_requests`, rclone waits and tries again. A second run completes a
stopped copy.

> **CAUTION:** `rclone copy` replaces each file of the same name in the Dropbox folder (for example the HTML).
> It does not delete the other files.

On a laptop with the Dropbox client, one command does the same: set `--output_dir` to the Dropbox folder
`".../Serac Biosciences Dropbox/Serac_team/4_Data_Sciences/15_ML"`.

**Remove the unused volcano images (after a publish with new data).** The volcano file names carry a hash of
their data (2026-10-23), so each data change renders new files and leaves the old ones. The publish never
deletes. After the new page works, remove the old files with `python/prune_volcanoes.py`:

```bash
python python/prune_volcanoes.py output/interfaces --s3            # dry run: counts only
python python/prune_volcanoes.py output/interfaces --s3 --apply    # delete: the local folder, S3, then the box
```

Without `--s3`, it changes only the local folder. The script finds the image names in the page (HTML and
`data.js`) and deletes only the `volcanoes_px` SVGs that the page does not use. It uses the publish targets
(`aws_publish_targets`) and deletes on S3 with `delete_objects`. Then it makes the box remove the same files
with an `aws s3 sync --delete` of `volcanoes_px/` only. It stops before any delete in these conditions:

- The page is older than `python/functions.py`.
- The page uses a file that the local folder or S3 lacks.
- The `data.js` on S3 or on the box has another size (the published page is not this page).

The bucket keeps a deleted file as an old version for `interface_noncurrent_days` (`aws-vpn/s3.tf`).

> **CAUTION:** `--apply` deletes files. Run the dry run first, and check the new page before you delete.

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

**Change the interface login** (user name and password). `~/.serac_aws` holds only a bcrypt hash
of the password. Nobody can get the password back from it, so keep the password in 1Password.

1. In 1Password, make a random password of 20 or more characters, and save it.
2. `cp -p ~/.serac_aws ~/.serac_aws.bak`, then `htpasswd -nB <user> > ~/.serac_aws && chmod 600 ~/.serac_aws`.
   It asks for the password two times: paste it from 1Password. Keep the default cost (`-B` = 05):
   nginx checks the hash on each file request, and on the box cost 10 takes 61 ms a file (cost 05: 2 ms).
3. `terraform plan` (expect `aws_ssm_parameter.htpasswd` to change in place), then `terraform apply`.
   The apply writes the new line to the SSM parameter `/<project>/webapp/htpasswd`.
4. The box reads that parameter only at boot. If the apply did not replace the EC2, run the boot
   steps again (they read the parameters, sync from S3 and restart nginx; about one minute):
   ```bash
   IID=$(terraform output -raw ec2_instance_id)
   CID=$(aws ssm send-command --region eu-north-1 --instance-ids "$IID" --document-name AWS-RunShellScript \
     --parameters 'commands=["systemctl restart provision-webapp"]' --query Command.CommandId --output text)
   sleep 90; aws ssm get-command-invocation --region eu-north-1 --instance-id "$IID" --command-id "$CID" \
     --query Status --output text    # Success
   ```
5. Log in from a private browser window, then tell the users.

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

The page shows a spinner while it loads, and it shows the interface only when the interface is
ready. nginx compresses the HTML, JS and SVG files (gzip), so a load moves about 25 MB, not the
112 MB on disk (`Serac_Px_interface_data.js` is 104 MB on disk and about 22 MB on the VPN).

**Link to plates.** Add `#Plate=` to the URL. The entries follow the `SHOW_PLATE` rule, with commas
between entries:

| Link | Plates ticked |
|---|---|
| `…/Px_interface/#Plate=20261006` | the date block 2026-10-06 (primary plates) |
| `…/Px_interface/#Plate=validation20261006` | the validation block 2026-10-06 |
| `…/Px_interface/#Plate=20261006,validation20261006` | both blocks |
| `…/Px_interface/#Plate=Pw50,Pw63` | these plates only |

Add `&Labels=false` to start with the Labels eye shut (only FBXO31-dependent and pinned genes keep
their labels), e.g. `…/Px_interface/#Plate=20261006&Labels=false`; `Labels=true` opens the eye. The
link wins over the config default (`LABELS_ON`).

The browser does not send the part after `#` to the server, so the link needs no server change.
It also works on the Dropbox copy (`Serac_Px_interface.html#Plate=…`). A changed link in an open tab
applies at once. If no plate matches, the page keeps its default plates and the Session note tells
you. The **Link** button copies the full view (exact plates, pins, hides, colour mode and, when it
differs from the config default, the Labels eye).

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
