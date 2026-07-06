# Px_interface — project wiki
_Durable, aggregate memory of this repo — read at session start. Aggregate only: no SMILES / compound IDs._

## Where we are now
- **Focus:** building the per-gene 3D Px interface (`Serac_Px_interface.html`) via `fn.plot_3d_interface`.
- Extracted from `MS_ML` (2026-06-23) so the interface lives on its own. The engine
  `python/functions.py` is the **whole** MS_ML module (carries some unused ML/signature/cytotox
  helpers); the driver is `vignettes/MS_Interface.ipynb`. `Rdkit_tools`/`Statistics_tools` imports
  were dropped (they were unused); `get_df` still comes from the sibling `CDD_Vault_API` repo via
  `sys.path` → `~/CDD_Vault_API/python`.

## How it runs
- Launch Jupyter from the **repo root**; cell 0 `%cd ../.` sets cwd so `import python.functions` resolves.
- `IFACE_OVERWRITE=True` rebuilds the four render inputs (`iface_df`, `compounds_df`, `meas`,
  `plate2date`) from source tables + FBX tranches and saves them to `IFACE_DIR`; `False` loads them
  and renders only (fast). Output dir switched by the `interface_output` knob (`GTLOCAL`/`DROPBOX_ML`).

## Command-line build (`python/Px_interface.py`) — complete port (2026-06-30)
Full `.py` port of the notebook so the interface rebuilds from the CLI without running cells. Runs
end-to-end (data → combine → iface → render) and is verified on the synthetic fixture.
Run: `python python/Px_interface.py --config config/config.yaml --output_dir <dir>`.
- `--config` (default `config/config.yaml`); `--output_dir` (default `output`) is the base for the
  HTML + volcanoes (`<output_dir>/interfaces/…`). The script self-locates repo root (`sys.path` +
  `os.chdir`) so it works from any cwd.
- **`PARAMS(config_path)`** → `load_params()` reads the YAML and sets every key as an attribute
  (`params.DFRAW_PATH`, …); returns `self`.
- **`DATA()`** — methods take `params`, store on `self`, return None: `load_chemical_lib_df` (serac_df:
  CDD pull if `CHEMLIB_OVERWRITE` else cached csv; yes/no→1/0/NaN), `load_old_df` (df_raw + ms_score +
  df_ms; MS), `load_new_df` (FBX MEASURE/MSSCORE/REPORT auto-discovered tranches, target2R2_df,
  uc2compound), `get_contaminants_and_controls` (control_compounds, contaminants), `get_gene_research`
  (gene_research list).
- **`OUTPUT()`** — methods take `(data, params)`: `combine_datasets` (§0.3: measure/mscore/report +
  plate2date; FBX source-of-truth on shared keys), `get_de_validated` (validated/devalidated
  targets+compounds from serac_df Px_Ligase_dependent), `get_iface` (builds/saves the four render inputs
  iface_df/compounds_df/meas/plate2date to `IFACE_DIR`; `IFACE_OVERWRITE=False` loads + frees heavy
  frames; keeps only compounds present in serac_df), `build_interface(data, params, output_dir)` (calls
  `fn.plot_3d_interface`, writes HTML + volcanoes + thumbnails under `output_dir`, caches panels.json).
- Convention: classes in CLASSES section; MAIN wires params→data→output. Method bodies use `self.*` /
  `data.*` / `params.*` — no bare globals (that's the #1 porting bug when moving a cell in).
- **Notebook debugging:** the notebook's params cell now does `from python.Px_interface import PARAMS,
  DATA, OUTPUT` (+ plain `CONFIG` / `OUTPUT_DIR` vars, no argparse — `parse_args()` breaks under Jupyter)
  and steps through the same classes, with `%autoreload 2` picking up edits to `Px_interface.py`.

## Test fixture — `tests/make_synthetic.py` → `tmp/` (2026-06-30)
Fast synthetic dataset mirroring the real schema (fake `C_`/`G_`/`PG_` ids, public SMILES `CCO`; built
from real **headers only**) so the whole DATA + combine flow runs in ~1.3s vs the 24.6M-row real df_raw.
`python tests/make_synthetic.py` writes `tmp/` and a **minimal leak-free `tmp/config.yaml`** (only the
keys the pipeline reads, relative paths — no real Dropbox paths or control compound ids): df_raw/MS
parquet, gene_sar.csv, 2 FBX tranches, chemlib (`SRB-#######` compounds, `CCO` smiles) + 5 proteomics
source csvs, contaminants.csv, gene_research.json, ot_cache.parquet, pharma_patent/bms_genes csvs.
Compounds use real `SRB-` format so `get_iface`'s `startswith('SRB-')` filter passes. `tmp/` is gitignored
— regenerate after a clone/reboot. Full run incl. render (~20s):
`python python/Px_interface.py --config tmp/config.yaml --output_dir tmp/out`. Known fidelity gaps (fine
for structure tests): synthetic `df_raw.uniquecontrast` is per-compound not per (compound,plate);
FBX/df_raw uniquecontrasts are disjoint so the MEASURE/REPORT source-of-truth dedup path isn't exercised
(MS-SCORE's (gene,plate) dedup is); no real PNGs so thumbnails are RDKit-rendered from `CCO`.

## Interface conventions (the render engine)
- **Axes:** x = R2 (SAR predictability, full-genome), y = OpenTargets association, z = MS score.
  Dots = one per gene over the mscore universe; missing R2 / association → 0.0 (still plotted).
- **FBX ingest is auto-discovered:** every date-named subdir (`YYYYMMDD…`) of `FBX_DIR` holding
  `*FBX_<KIND>*.csv` is a tranche; plate dates come from the **folder name**. Drop a new folder in +
  one `IFACE_OVERWRITE=True` rebuild — no config/code edits. (Replaced the old `FBX_BATCHES` list +
  `_FBX_DATE` dict.)
- **`plate_dates=` →** Plates filter renders **nested-by-date** (collapsible per-date sub-blocks,
  tri-state parents). **`plate_defaults=`** (list of plates) starts only those ticked; the notebook
  passes the **latest tranche's plates** so the default view shows just the newest date.
- **gene research** is sourced from `config.GENE_RESEARCH` (whole-genome ~10K-gene JSON) and
  **filtered to plotted genes** before injection (`__GENE_RESEARCH__`) to bound the payload; the
  build prints `[gene_research] kept/total — MB injected`.
- **PIN/HIDE** (top filter section, collapsible): search+autocomplete over genes & compounds; a
  **Selector** (orange) pins, a **Hide** (red) hides. Pinning a compound pins its target genes;
  hiding a **gene** drops its dot, hiding a **compound** drops only that compound (gates at
  `cmpAllowed`), its target genes stay. Hide supersedes pin. Click-to-select on the plot/panel.
  - **Pins are gated by current filters (2026-06-30):** a pinned gene renders (dot/label/count) only
    if it has ≥1 compound on the ticked plates+activities (`geneHasVisibleCompound` via new
    `visiblePinSet()`); the pin chip is retained, so re-ticking the plate brings the dot back.
    `applyRanges` now repaints the pin overlay (`buildPinTraceHook`) on every filter change, not just
    pin/unpin. Note this also suppresses pins whose only compounds are filtered out by class/dep/conf/
    lof/validation, and genes with no compounds at all (single-volcano/non-plate entries still count).
- **Compound-validation filter** (COMPOUND FILTERS): FBXO31 dependent/independent tickboxes keyed to
  `validated_compounds`/`devalidated_compounds`; mirror of the target-centric Validation filter.
- **Session save/load** (SESSION subsection): `.iface` JSON captures pins, hides, all filter
  tickboxes, the 3 range sliders, 2D/3D mode + camera; Load restores them faithfully.
- **Shareable deep-link (URL hash, 2026-07-01):** the **Link** button in the SESSION subsection copies
  a URL whose hash carries plates + pinned/hidden genes & compounds
  (`#p=Pw50,Pw63&pg=…&pc=…&hg=…&hc=…`); opening such a URL reproduces that view on load. Reuses the
  session `apply()` (a hash is just a partial session in the URL); a `p=` list means *exact* plate view
  (only listed plates ticked). Hash chosen over query/path so it never hits the server (no round-trip,
  stays out of access logs). Clipboard needs a secure context (HTTPS/localhost); else the URL is shown
  in the note. Aimed at the planned FastAPI/EC2 webapp so `seracbio.com/…#p=…` links share a view.
- **Download selection** → CSV (`Batch Molecule-Batch ID, genes, Plate, Activity`); batch id from
  `molecule_batch_id`, reconstructed from `uniquecontrast` (`split('_vs_')[0]`, dots→dashes) for
  experiments absent from `df_raw` (the `…WT/KO/Eval` plates).
- **Filter panel** fixed `width: 415px` so chip boxes wrap (3 compound chips per row). Labels:
  `labelMax` floor 800, sampled evenly across the MS range (not top-N) so they spread across the cloud.
- **Thumbnails:** source PNGs preferred (copied to `srb_png/` next to the HTML), else RDKit from
  SMILES. Source dir is `config.SRB_PNG_DIR` (`/home/gtamo/MS_ML/data/srb_png`, ~12.5K PNGs),
  **passed explicitly as `png_dir=SRB_PNG_DIR` in the cell-20 `plot_3d_interface` call** — the
  function default (`data/srb_png`) doesn't exist, so omitting it silently RDKit-renders everything
  (`png=0`). The copy into `srb_png/` refreshes when `dst` is missing, `source` is newer (mtime), **or
  the file size differs** — the size check (added 2026-06-30) self-heals stale RDKit renders whose mtime
  is newer than the (older) real source, so a partial folder-clear can't leave wrong images behind. The
  copy runs only inside the panel build, so it needs an `IFACE_OVERWRITE=true` rebuild to take effect.
  The CDD structure fetcher is a separate repo: `CDD_Vault_API/python/download_cdd_structures.py`.

## Deployment / webapp architecture (planned, 2026-07-01)
Goal: serve the interface as an internal webapp on AWS, auto-refreshing when new FBX data lands.
Decided direction (not yet built — RDS not functional; MVP starts with synthetic data + basic-auth):

- **Serve vs rebuild are decoupled.** The render is slow (~15 min on real data) so it never runs in
  a web request. A small always-on box **serves** pre-built static artifacts; a big box **rebuilds**
  them occasionally and publishes to the serving box.
- **Serving box:** one **t3.small** (~$15/mo, ~2 GB RAM is plenty for static). nginx serves the
  rendered `interfaces/` dir off its **root EBS** volume. Storage is tiny (srb_png + volcanoes +
  df_raw.parquet **< 1 GB**) → 20–30 GB gp3 root, **no separate data volume**; S3 as ~free backup.
  The interface is fully self-contained static (HTML + `_data.js` + plotly + volcano SVGs + srb_png),
  and deep-links work over any real HTTP server (the earlier 404 was a stray trailing slash).
- **Rebuild (target, RDS phase):** ephemeral **Fargate/Batch** job (or start/stop EC2 2xlarge, ~32 GB
  for the 24.6M-row df_raw in memory — RAM need is *transient*, not disk) triggered **weekly by an
  EventBridge cron** (add an ETL-emitted event later for true push; RDS can't natively signal row
  changes). Job reads FBX from RDS → runs the Px pipeline → writes `interfaces/` to **S3** → serving
  t3.small `aws s3 sync`s to its nginx root (atomic swap). Compute cost ≈ pennies/mo (15 min/week);
  total ≈ **$15–16/mo**. Requires `DATA.load_new_df` to gain an RDS source mode (config toggle, keep
  the CSV/synthetic path for tests).
- **Network + auth — chosen MVP (2026-07-03): private EC2 + FortiGate↔VPC Site-to-Site VPN + nginx
  basic-auth.** A machine scan found **FortiClient VPN installed** → Serac likely already runs a
  **FortiGate**, so reuse it: box has **no public IP**, reachable only over the IPsec tunnel = zero
  public attack surface (most secure, no ~$72/mo AWS Client VPN). Behind the VPN a **shared password
  (via 1Password)** is solid — you must already be on Serac's network to reach the login. TLS not
  load-bearing (tunnel encrypts transit); self-signed cert for hygiene, no certbot (no public DNS).
  Provision the box before the tunnel via **SSM Session Manager** (no inbound ports). **Eventual
  upgrade:** M365 SSO (ALB + Entra OIDC) for per-user identity/audit — additive. Rejected as
  *starting* points: public+basic-auth (weak for a remote team) and Tailscale (needs a client on
  every device + still M365 for its own SSO). Open item: confirm FortiGate + who owns each S2S side.
- **Privacy:** keep the MVP on **synthetic** data (fake ids, `CCO`); no public exposure means real
  data would also be safe behind the VPN later, but gate real-data serving on M365 SSO for per-user
  *audit*. RDS + EC2 sit in Serac's VPC, encrypted, non-public. Full runbook: `docs/aws_docs.md`.

## Decisions & conventions
- All parameters/paths live in `config/config.yaml`; data paths are absolute (Dropbox/local).
- Local-only data policy (see CLAUDE.md): chemistry data never leaves the machine.

## Log
- 2026-06-23 — repo created: copied `functions.py`/`config.yaml`/`CLAUDE.md` + the notebook from
  MS_ML (copy-only; MS_ML left intact). Dropped unused `Rdkit_tools`/`Statistics_tools` imports and
  the `../Scripts` path insert. Added `requirements.txt` (pinned), `.gitignore`, README.
- 2026-06-30 — compound-panel build sped up ~7× (14:34 → 2:04 on 8,230 genes, 9.4 → 66 gene/s) via two
  changes in `plot_3d_interface` (functions.py): (1) memoized the thumbnail builder on `(compound, smi)`
  so the per-compound filesystem stat / copy / RDKit render runs once per unique compound instead of once
  per (gene, compound) — the big win on WSL2; (2) replaced the per-plate `cg.iterrows()` with column
  NumPy arrays. Both output-preserving (verified on synthetic data). Also fixed thumbnails: `png_dir`
  wasn't passed in the notebook (fell back to nonexistent `data/srb_png` → `png=0`, all RDKit); added
  `SRB_PNG_DIR` to config + `png_dir=SRB_PNG_DIR` to the cell-20 call. After clearing the stale
  `srb_png/` cache, a rebuild produced `png=57397, rdkit=0, missing=0` (57,397 entries across 8,230 genes).
  Follow-up: a partial folder-clear had left 84 compounds showing stale RDKit dst files (real source
  exists but older mtime than the stale render, so the mtime-only copy skipped them). Made the copy
  **size-aware** (refresh when `getsize(source) != getsize(dst)`) so it self-heals on the next
  `IFACE_OVERWRITE=true` rebuild — no manual deletion needed.
- 2026-06-30 — started the CLI port `python/Px_interface.py` (PARAMS / DATA / OUTPUT / MAIN, `--config`
  arg) covering the chemical-lib load, df_raw+MS load, FBX load, and the §0.3 combine. Added
  `tests/make_synthetic.py` generating a fast synthetic fixture in `tmp/` (+ minimal leak-free config);
  full DATA + combine_datasets flow runs end-to-end on it in ~1.3s. See the two new wiki sections above.
- 2026-06-30 — **CLI port complete**: added `get_de_validated`, `get_iface`, and `build_interface` (the
  render) to `Px_interface.py`; `--output_dir` CLI arg drives HTML + volcano output location. Extended
  the fixture (SRB-format compounds, OT cache, pharma/BMS lists, gene_research, contaminants, +config
  keys) so the whole pipeline incl. render runs on synthetic in ~20s. Added a serac_df-membership filter
  to the interface build (compounds absent from serac_df excluded from the viz). Capped the render tqdm
  bars at `ncols=80`. `tmp/` gitignored.
- 2026-07-01 — settled the **webapp deployment architecture** (see the new section above): decoupled
  t3.small serving + ephemeral weekly rebuild → S3 → sync; auth is M365 SSO (ALB + Entra OIDC) with a
  shared-password + synthetic-data basic-auth MVP to start. Next: write the t3.small EC2 + nginx +
  basic-auth deploy steps targeting the synthetic build.
- 2026-07-03 — **security direction changed to VPN-first**: machine scan found FortiClient VPN
  installed → reuse a probable FortiGate via a **FortiGate↔VPC Site-to-Site VPN**, private EC2 (no
  public IP) + nginx basic-auth (1Password). Drops the public endpoint entirely; M365 SSO becomes a
  later per-user upgrade. Rewrote the `docs/aws_docs.md` runbook for the private/SSM-bootstrap variant.
- 2026-07-03 — **AWS deployment underway** as a Terraform stack (Kiro-generated), in-repo at
  `aws-vpn/` (moved there 2026-07-06; secrets kept out of git by `aws-vpn/.gitignore`). Region
  `eu-north-1`, acct `620423424620`: AL2023 t3.micro private,
  VPC endpoints (no NAT), self-signed TLS via SSM, S3+DynamoDB remote state. Steps 1–3 done (bootstrap
  applied, state bucket + auth hash). **Step 4 apply held for IT** to confirm the VPC CIDR. Networking
  finalised with IT: VPC moved off 10.x → **172.20.0.0/16** (10.x collides with Ridgeline + the
  FortiClient pool `10.0.14.0/24`); VPN routes/SG now lists = office LAN `192.168.146.0/24` + pool
  `10.0.14.0/24` so remote users reach it. Full detail + live progress tracker in `docs/aws_docs.md`.
- 2026-07-06 — **AWS stack deployed & healthy** (`terraform apply` complete, 29 resources): EC2
  `i-04965b616b4415778` @ `172.20.2.125`, VPN `vpn-02994f99eeacd59fd`. Box verified via
  `aws-vpn/healthcheck.sh` = 6 ok / 1 warn (VPN tunnels 0/2, expected) / 0 fail — nginx active, SSM
  reachable, 4/4 endpoints up. **Only remaining milestone: IT configures the FortiGate side** (route
  `172.20.0.0/16` over the tunnel, local selectors incl. both `192.168.146.0/24` + `10.0.14.0/24`),
  then upload the interface via SSM + browser-test `https://172.20.2.125/`. Apply hit four fixable
  snags (all logged in `docs/aws_docs.md`): Ctrl+C state-checksum mismatch, orphaned ssm/ec2messages
  endpoints (imported), AL2023 needing ≥30 GB root, and a boot-time race where `user_data` ran before
  the endpoints were ready (fixed by re-running the boot script; retry-loop hardening recommended).
- 2026-07-06 — validated the stack against the AWS contact's hybrid-connectivity reference — our
  single-VPC **VGW** S2S design matches it (static routing acceptable; hybrid DNS/Route 53 Resolver
  not needed until name-based or RDS-by-name access). Added **boot-retry hardening** to `user_data`
  and **CloudWatch VPN tunnel alarms** (`monitoring.tf`, SNS `vpn-project-alarms`) — both pending the
  next `terraform apply` (which replaces the EC2). Open Qs for AWS contact: BGP vs static, and whether
  to attach to an existing Transit Gateway / shared-network landing zone.
