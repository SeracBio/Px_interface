# Px_interface — project wiki
_Durable, aggregate memory of this repo — read at session start. Aggregate only: no SMILES / compound IDs._

## Where we are now
- **Focus:** building the per-gene 3D Px interface (`Serac_Px_interface.html`) via `fn.plot_3d_interface`.
- **OPEN TO-DO (2026-10-05): security batch B on the AWS box** — bcrypt cost 10 + long password, secret
  files at 0600 from the start, optional `limit_req` and per-person logins. It replaces the EC2, so do it at a
  quiet time. Details: log entry "Signals agent handoff: SSM and password hardening for Px" (2026-10-05).
- **Architecture doc:** [`docs/INTERFACE.md`](../docs/INTERFACE.md) — full map of how the build fits together
- **Data-transform doc:** [`docs/data_transform.md`](../docs/data_transform.md) — how `measure`/`mscore`/`report` (+ `df_raw`/`MS`/FBX) are derived from the raw files
  (pipeline → `plot_3d_interface` → volcano cache → `_INTERFACE_INJECT` JS → the JS↔Python `__X__` contract +
  invariants). Created 2026-07-21 from a multi-agent audit; read it before non-trivial interface edits.
- **Audit + simplification pass (2026-07-21, multi-agent):** `functions.py` **7023→6048 lines (−14%)**.
  - **Dead code deleted** (verified zero callers): `plot_target_3d`, `_HOVER_INJECT`, `_build_gene_patents_html_map`,
    `plot_volcano_significant`, `load_fbx_tranche` (+ its `FBX_DFRAW_COLS`/`FBX_MS_COLS`). The legacy patents panel
    is gone for good — live target links come via `__GENE_RESEARCH__` (`renderResearch`).
  - **Simplifications** (~16, behaviour-preserving): dedup'd heavy computes in `combine_datasets`/`get_iface`,
    `_dep0/_dep1` in `get_de_validated`, module-level `_fbx_csv`, `PRIORITY_DISEASE_AREAS` constant + assert,
    `BMS_C`→config, `_uc2mbid` hoist, area_order set membership, single `_hover_text`, removed unused `import hashlib`.
  - **Correctness fixes:** stem-trace `_ext` now matches the render/`ring_pos` extension; `recompute_volcanoes` /
    `floor_zero_pvalues_and_refresh_volcanoes` gained `plate_validation_suffixes` so they salt validation volcanoes
    `v2` (previously refreshing a WT/MLN/KO volcano was a silent no-op); JS activity filter uses `!pl[3]` (Python
    injects `''`, not `undefined`, for missing activity).
  - **Perf/size:** injected coords rounded to 4dp (smaller `_data.js`); slider drags coalesced to one `applyRanges`
    per animation frame (`scheduleApply`); shared `saveBlob` helper for CSV/session export.
  - **Not done (opt-in, need browser QA):** `renderChipBox`/stem-grouping dedup + single-pass `applyRanges`.
  - All 18 tests green throughout. NOTE: JS changes are static-checked only (no headless browser in this env) —
    hard-refresh + eyeball after rebuild.
- **Volcano render dedup (2026-07-24).** The volcano body (grey cloud + significant points + axes) depends
  only on the experiment (`uniquecontrast`), not the focal gene, but was re-rendered once per
  `(gene, experiment)` cell — the dominant build cost (~10 CPU-s/image; the run was ETA ~80 min for 14.8k
  cells at ~2.9/s on 30 workers, fully CPU-bound). Now the **base is rendered once per unique experiment**
  (`_volcano_base_svg` → `(svg, geom)`) and each focal gene's **ring+label is overlaid by string injection**
  (`_apply_ring`, position computed analytically) — no matplotlib per gene. Synthetic fixture: 2,338 cells →
  **320 base renders**; overlay ~6.8k/s. Base switched to a **fixed axes rectangle** (`_VOLCANO_AXRECT`)
  instead of `bbox_inches='tight'` so images are `size_px`-square and the axis→image mapping is closed-form
  (also ~2× faster savefig). Cache salt bumped `''`/`'v2'` → **`'g2'`/`'g2v'`** (validation), so old
  tight-bbox images regenerate uniformly on next build (cheap now). `_volcano_svg_string` kept as a
  base+ring composition for single-shot callers. Also fixed a latent double-`<title>` bug (SubElement +
  insert added the same node twice). 26 tests green (4 new `TestVolcanoDedup`). Remaining ideas from the
  perf triage (not done): vectorised sig scatter is now in the base; browser-side ring overlay not needed.
- **Overlay writes parallelised + `NJOBS` config knob (2026-07-24).** After the base dedup, the tail cost
  was writing ~75k tiny SVGs to `/mnt/c` (WSL2 9p, ~25ms/file, serial → ~37/s, ETA ~33 min). File writes
  release the GIL, so the overlay+write now runs on a **ThreadPool** (`min(64, n_jobs*2)`) — threads share
  the in-memory bases (no IPC); `custom`/`ring_pos` mutation stays on the main thread. Added **`NJOBS`** to
  `config.yaml` (`0`→auto `CPU-2`, positive→exact; this box has 32 CPUs→30); `build_interface` resolves it
  via `resolve_n_jobs()` and passes `volcano_n_jobs`, which also drives the write pool. The only
  multiprocessing on the build path is the volcano render — RF/`function_enrichment_all` are legacy (uncalled),
  `recompute_volcanoes`/`floor_*` are manual utils keeping their own `n_jobs`. For a *much* bigger win the
  volcano dir could live on local ext4 instead of `/mnt/c` (offered, not done).
- **Build memory reduction (2026-07-24).** MEASURE is ~47.7M rows; the build was peaking ~30 GB (into swap).
  Fixes: **(1)** `combine_datasets` frees `FBX_MEASURE` before the section-1 concat and `del`s the `fbx_std`/`dr_std`
  intermediates right after, then frees `FBX_MSSCORE/REPORT`+`MS` at the end; `get_iface` frees `df_raw` at its end —
  all gated by **`FREE_UPSTREAM`** (config; default off so tests/inspection keep the frames). `self.measure/mscore/report`
  are KEPT (the notebook still exports them to `Px_MEASURE/MSCORE/REPORT.parquet`). **(2)** `meas` (the render source /
  `meas.parquet`) slimmed to 6 cols `[uniquecontrast, genes, plate, logfc, pvalue, significant]` — halves the second
  47M-row frame; `pvalue` stays float64 (float32 underflows `-log10(p)`). **(3)** The volcano-render dedup was itself a
  hog: `base_by_vk`'s `geom['xy']` stored EVERY measured gene's position per experiment (~10k) though `_apply_ring` only
  needs the **focal** genes (~16/exp). Now the driver passes `focal_by_vk` to `_volcano_base_svg(focal_genes=)` so `geom['xy']`
  keeps only focal genes (~500× smaller `base_by_vk`); the per-experiment `sub_cache` is `del`'d before the write fan-out.
  `Px_MEASURE.parquet` is unaffected (that's `self.measure`, full 11 cols — only `meas` was slimmed).
- **Build memory reduction, round 2 (2026-07-24).** After round 1 the *`get_iface`* build still peaked ~28 GB
  (the two `molecule_batch_id`/`Silent-activity` prints mark the spot). Two more fixes, both always-on:
  **(4)** `combine_datasets` now downcasts the repeated string columns `[genes, uniquecontrast, plate, compound,
  pg, source]` of `measure/mscore/report` to **`category`** (int codes + one copy of each value vs a Python `str`
  per cell over 47.7M rows) — ~10× smaller `self.measure`, a smaller `meas` copy, and smaller parquet exports.
  Round-trips through parquet (verified) and is transparent to groupby/isin/`.str`/merge. **(5)** the validation-
  stem `_measured` set in `get_iface` was `set(zip(meas['genes'], meas['uniquecontrast']))` over all ~47.7M rows
  (~3–4 GB of tuples) though the completion loop only queries **validation-plate** contrasts — now scoped to
  `rep`'s val-plate `uniquecontrast`s first (tiny). Stem-trace count unchanged (1,904/199 on the fixture), all
  24 pipeline+render tests green.
- **Build memory reduction, round 3 (2026-08-13).** The EM_S tranche grew combined MEASURE to ~65M rows
  and the build went into swap again. Root realisation: `self.measure`/`mscore`/`report` were held resident
  through all of `get_iface` **and** the render solely to feed the cell-6 parquet export — which was
  **commented out** — while the render only ever uses `meas`. Fix (Phase 1): under `FREE_UPSTREAM`,
  `get_iface` now frees `self.measure` right after `meas` is built (its last use is the p-value floor) and
  frees `mscore`/`report` after their derivations (`ms_gene`/`ms_per_uc`/`rep`), so the ~65M-row frame no
  longer coexists with `meas`. The optional export moved to `combine_datasets` behind **`EXPORT_COMBINED`**
  (default off, `PX_PARQUET_DIR`) — writes the three parquet dumps while the frames are complete, then they
  can be freed. Guarded by `TestMemoryFreeing` (frames None after get_iface, render still builds, export
  written). **Also (same round):** two full-`meas` groupbys in the completion/primary passes were building
  frame-sized objects to serve a handful of rows — `_mean_logfc` (completion) grouped all ~65M (gene,uc)
  pairs though only the ~2.7k `_add` rows use it → scoped to the `_add` experiments; and the primary attach's
  `_pm`/`_prim_logfc`/`_prim_measured` covered *all non-validation* contrasts (≈60M-entry Series + 60M-tuple
  set) though the loop only queries the **validation genes** → scoped `_pm` to `_val_genes`. Same trap as the
  earlier `_measured` scoping; counts unchanged (1,158 completion / 726 pairs; 24+709 primary on the fixture).
  Phase 2 (batch the render's `volcano_source` from `meas.parquet` per-experiment) and Phase 3 (stream the
  combine union) remain **not yet done** — pending a re-measure of the new peak.
- **FBX export column drift — silent tranche drop (2026-08-13).** A tranche's `FBX_MEASURE` had its key
  column exported as **`contrastunique`** instead of **`uniquecontrast`** (the `20260813` tranche, 75 `EM_S…`
  plates). On `pd.concat` across tranches the mismatched name became all-NaN `uniquecontrast`, so every one
  of those rows failed the `uc2compound` map → dropped from `compounds_df` with **no error**: the plates got
  a correct date via the (properly-named) `FBX_REPORT` and showed in `plate2date`/`meas`, but appeared
  **nowhere** in the interface (max date stuck at the previous tranche). Symptom to watch for: a new tranche's
  plates missing from the interface though its date shows. Diagnosis: compare `FBX_MEASURE` headers across
  tranches (`pd.read_csv(f, nrows=0).columns`) or check `meas`'s `uniquecontrast` null-rate per plate. **Fix
  applied: renamed the column in the CSV header in place** (14-byte swap, `contrastunique`↔`uniquecontrast`
  are same length, data untouched). Considered a code-side alias-normalise on load but chose the data fix.
- **Primary-screen volcano attached to validation stems (2026-07-24).** For a validation-plate hit
  (e.g. ARG1 / SRB-0005514 on WT+KO), the interface now also shows that compound's **broad
  primary-screen volcano** and links the gene across `primary → WT → MLN → KO` on hover. `get_iface`
  adds a pass after stem-completion: for each validation `(gene, compound)`, pick the compound's
  non-validation contrast where the gene has the **highest MS score** (tie-break strongest/most-negative
  logfc); flag an existing hit row `is_primary=True` in place, else add a ride-along row. Note MS score
  is absent on the `df_raw` broad side, so in practice the logfc tie-break decides. Plate-row schema
  gained **`pl[10]` = is_primary** and `pl[7]` (contrast id, previously validation-only) is now set for
  primary rows too, so `stem_trace` picks them up automatically. Client: `buildVolcanoHtml` prepends the
  primary as the stem's **left-most `.vcell`** (labelled "primary screen", `.vprimary`); `visPlates`
  shows it whenever its stem is visible (bypassing its own dated-plate tick), else falls back to a
  stacked volcano. Tested (data-level `is_primary` flag + render-level `pl[10]`/`STEM_TRACE`); fixture
  gained a deterministic Pw00 primary hit for SRB-0000006/G_00000. 26 tests green. **Visually verified in
  headless chromium** (CDP recipe below): the stem renders `[primary screen | WT | MLN | KO]` with the
  primary leftmost + blue "primary screen" label, and the hover-trace polyline links G_00000 across all
  four (horizontal across primary/WT/MLN, dropping to KO where it's not significant). The primary also
  attaches to *each* of a compound's stems (Pw10 + Pw11 both showed it). A gene **not significant** in the
  primary still shows its **location** (ring) there — same `pl[9]`/placeRings path as the KO completion cells.
- **Base-dedup cache invalidation for grown focal sets (2026-07-24).** Bug found while adding the above:
  the volcano base-dedup skipped re-rendering a cached `vk` (`vk not in positions`), so **primary-screen
  genes newly attached to an already-cached base never got a ring position** — on a *rebuild over an
  existing `volcanoes_px`*, the non-significant primary genes' locations silently went missing (493/709 on
  the stale synthetic cache; **709/709 on a clean build**). Fix: `render_vks` now also re-renders a `vk`
  when its focal set isn't a subset of the cached `positions[vk]` keys, and `_write_base` records **every**
  focal gene (measured → `[fx,fy]`, absent-here → `null`) so the subset check terminates (no perpetual
  re-render — verified: rebuild re-renders only the grown vks, a 2nd rebuild renders 0). **Consequence:
  rebuilding on real data will re-render the experiments whose focal set grew (the newly-attached primary
  genes) — you do NOT need to clear `volcanoes_px`.** Guarded by `test_nonsignificant_primary_shows_location`.
- **Opt-in compound-PNG refresh (2026-07-24).** Thumbnails are served from `SRB_PNG_DIR` (real CDD
  structures; RDKit only fills gaps — last real build: 11,015/11,015 from the library, 0 rdkit). New
  `DATA.download_cdd_pngs(params)` refreshes that dir from CDD Vault, reusing the `~/CDD_Vault_API`
  downloader (already on `sys.path` alongside `get_df`) — `make_session` + `list_molecules_in_search`
  + `download_all`, no subprocess. Gated by **`UPDATE_PNGS`** (default false = no-op); config keys
  `CDD_VAULT` (7108), `CDD_SEARCH` (23196193 = SRB library), `CDD_TOKEN_FILE` (`~/.cdd_token`); output
  = `SRB_PNG_DIR`. Resume-safe (skips existing → normal runs fetch only new compounds); filenames
  `SRB-XXXXXXX.png` (prefix kept, `strip_prefix=False`) = exactly what `_build_thumb` looks up. Tested
  (no-op + mocked-call, no network). PNGs stay local; only the token leaves.
- **Base-render loky closure bug (2026-07-24).** The base-dedup path (`if _sig and _external`) dispatched
  `delayed(_render_base)(vk)` where `_render_base` was a **closure over `sub_cache`** (the ~meas-sized dict of
  ALL experiments). loky pickles the dispatched callable to every worker, so it shipped the whole dict ~per
  worker → swap explosion + ~26 s/vol (memory-thrash, not render). Fixed to dispatch the module-level
  `_volcano_base_worker` with each per-experiment subframe as an **explicit arg** (evaluated in the parent),
  mirroring the correct fallback path; `del sub_cache` right after. **Rule: never `delayed()` a closure that
  captures a large frame — pass the slice as an arg.**
- **Volcano base-dedup + client-drawn ring (2026-07-24).** The baked-ring model wrote one SVG per
  `(gene, experiment)` — 75,780 files / **6.5 GB** on real data, each embedding its own copy of the
  (raster-heavy) grey cloud, ~19× duplicated across a shared experiment. Killer for Dropbox sync. Now:
  **one base SVG per experiment** (`_volcano_base_cache_fname`, salt `b1`; the base has no ring), and
  the focal-gene **ring + label are drawn client-side** (`placeRings`/`volEl`/`ringFor` + `.vringsvg`
  overlay) at `pl[9] = [fx, fy]` — the closed-form ring fraction (`_ring_frac`) injected per plate-row.
  `positions.json` = `{vk: {gene:[fx,fy]}}` persists positions so a rebuild with cached bases fills
  `pl[9]` without re-rendering. `stem_trace` now reads `pl[9]` from `custom` (cache-independent; no more
  `ring_pos.json` dependency). Result: **75,780 → ~4,056 files, 6.5 GB → ~350 MB (~19×)**; synthetic
  2,338 cells → 320 bases. Visuals unchanged (ring + tooltips preserved), lazy-loaded, `file://`-safe.
  29 tests green (+`test_volcano_base_dedup`); headless-Chromium load shows no JS errors. `Px_MEASURE`
  etc. unaffected. NOTE: old `g2`/`g2v` per-gene files are ignored — delete `volcanoes_px/` before the
  next build so only the ~4k `b1` bases remain. Client ring is static-checked + math-verified; hard-refresh
  + eyeball the actual overlay after rebuild.
- **Shared y-max per validation stem (2026-08-14).** So a stem's volcanoes are visually comparable, all
  volcanoes in one stem — a compound's WT/MLN/KO/BIND conditions **plus its attached primary-screen
  volcano** — render on **one shared y-axis max** = the stem's tallest `-log10 p`. Server-side by
  necessity: the y-scale is baked into the external base SVG (points + axis ticks) **and** the client
  ring fractions (`geom['ymax']` in `_ring_frac`), so a client-only rescale would desync the rings and
  misrender the ticks (confirmed by reading the render path — the base is an external `<object>`, not
  reachable/rescalable from JS). `_stem_shared_ymax(compounds_df, vsrc, suffixes)` returns `{vk: ymax}`
  for only the volcanoes scaled **up** — the tallest keeps auto-scale, so **only the rescaled stem
  volcanoes get a new cache name (`_volcano_base_cache_fname(…, ymax=)`) and re-render**; every standalone
  volcano is untouched (no re-render). The override threads `_volcano_base_worker` →
  `_volcano_base_svg(ymax_override=)` (never below the data's own max), so points/axis/rings stay
  consistent. Only PATH 1 (`if _sig and _external`, the real interface) applies it; the non-external /
  non-significant `else` fallback stays auto. End-to-end verified: stem `SRB-0000006/Pw10`'s KO/MLN/WT
  auto-scale to different y-maxes but share **6.3** with the override. Tested by `TestStemSharedYmax`
  (grouping + skips-tallest + `_volcano_base_svg` honours the override).
- Extracted from `MS_ML` (2026-06-23) so the interface lives on its own. The engine
  `python/functions.py` is the **whole** MS_ML module (carries some unused ML/signature/cytotox
  helpers); the driver is `vignettes/MS_Interface.ipynb`. `Rdkit_tools`/`Statistics_tools` imports
  were dropped (they were unused); `get_df` still comes from the sibling `CDD_Vault_API` repo via
  `sys.path` → `~/CDD_Vault_API/python`.

## Compound prioritization / selection reconciliation — `vignettes/MS_Prioritization.ipynb` (2026-07-22)
Notebook to reproduce the interface's compound selection outside the browser and produce a deliverable table.
- **KEY GOTCHA — the interface's association (y) axis is the OpenTargets `overall_score` per gene
  (`OT_CACHE`, max per gene), NOT `mscore['association_score']`** (which varies per plate, so it's wrong to
  filter on). This was the whole reconciliation blocker. At `ms_score>10 & assoc>0.6`: 5,127 genes clear ms>10;
  adding assoc>0.6 gives **839 genes via `mscore.association_score`** vs **1,723 via OT** (the interface uses OT).
- **`mscore` is now PER-COMPOUND (2026-07-23):** `combine_datasets` no longer collapses to the best compound per
  (gene,plate) — it keeps **one row per (genes, uniquecontrast)** (FBX wins on shared uc, noisy plates dropped, df_raw
  significant rows for non-FBX uc). The old `.sort_values('ms_score').groupby(['genes','plate']).first()` was dropped.
  **The 3D dot z = `mscore.groupby('genes')['ms_score'].max()`** (per-gene max) — unchanged positions, since max-of-
  per-compound = max-of-best-per-plate. `compounds_df.ms_score` is now sourced straight from `mscore` (single source of
  truth), so the slider filters each experiment by the SAME official score the position is derived from — no more
  per-uc-vs-per-plate inconsistency. Real-data caveat: ~7/8332 genes shift z *up* slightly (where df_raw beat FBX on a
  shared gene-plate — previously suppressed by the collapse); everything else identical. **To use per-compound `mscore`
  in the reconcile notebook, regenerate `Px_MSCORE.parquet`** via the `output.mscore.to_parquet(...)` cell in
  MS_Interface.ipynb (else the notebook keeps loading the old collapsed file). Compounds =
  significant-down hits from `measure` (`significant==1 & logfc<0`), non-noisy plates (`Plate12/15/23` dropped),
  compound in `serac_df`, `activity != 'Silent'`, one per (gene,compound,plate). Reproduces ≈2,252 compounds for
  the MS>10 & assoc>0.6 & Single+Low view (2,297 before the drop-plates + serac-library filters close the gap).
- **`USE_MAX_MS` toggle** (reconcile cell): max>ctf (gene set = "any plate clears ctf", interface behaviour) vs
  as-is per (gene,plate) — same gene set, but as-is is stricter on hits (a hit counts only on the plate where the
  gene actually cleared the ctf) → fewer compounds.
- **Deliverable table (pptx):** `## making the deliverable table:` cell builds a 3×6 grid — rows = MS ctf {5,10,20},
  cols = 3 activity groups {Single / +Low / +Medium(11-25)} × assoc ctf {0.6,0.7} — of distinct **NEW** compounds
  (not in `val_cmps`, `compound_no ≥ 6400`) meeting each threshold, and emits a styled `python-pptx` table (blue/
  purple/orange group headers, thick black group separators) to `GTLOCAL/Claude_ppt/YYYYMMDD_Px_selection_deliverable.pptx`.
  Uses `USE_MAX_MS=False`. `compound_no` = int of `compound.replace('SRB-','')`. `python-pptx==1.0.2` added to requirements.

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
  `os.chdir`) so it works from any cwd. **`--output_dir <PUBLISH_URL>`** (2026-10-01) builds in
  `PUBLISH_STAGE_DIR` and then publishes to S3 and the EC2 (see the 2026-10-01 publish log entry).
- **`PARAMS(config_path)`** → `load_params()` reads the YAML and sets every key as an attribute
  (`params.DFRAW_PATH`, …); returns `self`.
- **`DATA()`** — methods take `params`, store on `self`, return None: `load_chemical_lib_df` (serac_df:
  CDD pull if `CHEMLIB_OVERWRITE` else cached csv; yes/no→1/0/NaN), `download_cdd_pngs` (MAIN calls it
  after the library load since 2026-10-01; no-op unless `UPDATE_PNGS`), `load_old_df` (df_raw + ms_score +
  df_ms; MS; with `DFRAW_OVERWRITE` it first calls `build_old_df`, added 2026-10-01), `load_new_df` (FBX MEASURE/MSSCORE/REPORT auto-discovered tranches, target2R2_df,
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
`python python/Px_interface.py --config tmp/config.yaml --output_dir tmp/out`. Synthetic
`df_raw.uniquecontrast` is now **plate-specific** (`{mbid}.{plate}_vs_DMSO`, matching the FBX
convention) so a compound tested on several plates keeps one row per plate — needed for the shared-stem
validation plates (`Pw10WT/MLN/KO`, `Pw11WT/KO`) to all survive combine. Known fidelity gaps (fine for
structure tests): FBX/df_raw uniquecontrasts are disjoint so the MEASURE/REPORT source-of-truth dedup
path isn't exercised (MS-SCORE's (gene,plate) dedup is); interface plates use the **MSPlate** namespace
while FBX plates use `Pw{ti}{p}` (disjoint → `__PLATE_DEFAULTS__` filters to `[]` on synthetic; real
data shares the namespace); no real PNGs so thumbnails are RDKit-rendered from `CCO`.

## Interface conventions (the render engine)
- **Axes:** x = R2 (SAR predictability, full-genome), y = OpenTargets association, z = MS score.
  Dots = one per gene over the mscore universe; missing R2 / association → 0.0 (still plotted).
- **FBX ingest is auto-discovered:** every date-named subdir (`YYYYMMDD…`) of `FBX_DIR` holding
  `*FBX_<KIND>*.csv` is a tranche; plate dates come from the **folder name**. Drop a new folder in +
  one `IFACE_OVERWRITE=True` rebuild — no config/code edits. (Replaced the old `FBX_BATCHES` list +
  `_FBX_DATE` dict.)
  - **Missing-kind tranches tolerated (2026-08-14):** a tranche need not carry all three kinds —
    a **validation-only** drop-in ships MEASURE + REPORT but **no MSSCORE** (its MS scores were
    computed in an earlier tranche). `_fbx_csv` now returns `None` when a kind is absent (was
    `next(...)` → `StopIteration` crash), and `_load_fbx` skips those tranches for that kind, printing
    `> note: no FBX_<KIND> in N tranche(s), skipped: …` so a *genuinely* missing file isn't swallowed
    silently. Discovery still keys on FBX_REPORT, so every tranche must have REPORT. Diagnosed on the
    `20260817` tranche (MEASURE+REPORT only). Tested by `TestValidationOnlyTranche`.
  - **Missing `plate` column reconstructed (2026-08-14):** the same `20260817` validation tranche also
    ships **no `plate` column** in MEASURE/REPORT (its REPORT is minimal: `uniquecontrast, srbnumber,
    condition, target`) — the plate is embedded in the contrast instead:
    `SRB…_vs_SRB…_complement_Pw144VM_BIND` → `Pw144VMBIND` (stem+condition, the `Pw###VM{WT,MLN,KO,BIND}`
    convention). `_plate_from_uc` parses it and `_ensure_plate` fills a missing/NaN `plate` from the
    contrast (existing values untouched), applied to FBX_MEASURE/REPORT in `load_new_df` and in the
    `plate2date` loop (which now reads the whole small REPORT, not `usecols=['plate']` — that was the
    `ValueError: Usecols do not match columns … ['plate']` crash). Real-data check: MEASURE 100%
    (398,523 rows), REPORT 48/49 reconstructed → 5 stems (`Pw105/122/144/177/193 VM`) × `{WT,KO,BIND}`.
    **`BIND` added to `VALIDATION_PLATE_SUFFIXES`** (now `WT/MLN/KO/BIND`) so those form validation stems.
    Note some stems (e.g. `Pw105VM`) **collide** with the earlier 2026-06-01 tranche's plates → the
    duplicate-tranche rule below restamps them to 2026-08-17. Tested by `TestPlateReconstruction`.
  - **⚠️ Duplicate-tranche gotcha (2026-08-13):** `plate2date` is built by `dict.update()` over the
    **sorted** tranches, so if two date folders hold the **same plate names**, the *later* folder wins
    the date and the earlier date **vanishes entirely** from the interface (its plates are all restamped).
    Diagnosed live: `20260812` and `20260814` were identical copies of the same 5 plates
    (`Pw222/223/226/227/255`) → no `2026-08-12` ever appeared, and those rows were also loaded twice
    (mostly absorbed by the mscore/compounds de-dups, but wasted RAM). **Fix is at the data layer** —
    don't keep two date folders with the same plates. (User removed the duplicate folder manually.)
    Now guarded by `TestDuplicateTrancheDate` (last-tranche-wins on shared plate names).
- **`plate_dates=` →** Plates filter renders **nested-by-date** (collapsible per-date sub-blocks,
  tri-state parents). **`plate_defaults=`** (list of plates) starts only those ticked; the driver
  passes whatever `resolve_plate_defaults(plate2date, SHOW_PLATE)` selects so the default view opens
  on the chosen date(s).
- **`SHOW_PLATE` config / `--show_plate` CLI (2026-08-13):** pick which plate **date(s)** open
  default-ticked in the Plates filter. Config `SHOW_PLATE: [20260812, 20260813]` (list of YYYYMMDD),
  or CLI `--show_plate "20260812, 20260813"` which **overrides** the config. Empty/absent → the
  single **latest** date only (previous behaviour). `resolve_plate_defaults()` (in `Px_interface.py`,
  beside `resolve_n_jobs`) normalises YYYYMMDD → the `YYYY-MM-DD` form `plate2date` stores and returns
  `(plates_to_tick, dates_selected)`. Unit-tested by `TestResolvePlateDefaults`.
- **`FBXO31_INDEPENDENT_TICKED` config (2026-08-14):** default tick state of the target-validation
  filter's "FBXO31 independent" box. `false` → interface opens with only "FBXO31 dependent" ticked
  (independent genes hidden on load, still toggle-able); `true`/absent → both ticked (previous
  behaviour). Driver-level: `build_interface` maps it to `plot_3d_interface(validation_defaults=)`
  (`None` when true, `['FBXO31 dependent']` when false) → injected `__VALIDATION_DEFAULTS__` (`null`
  vs the dependent-only list). Only the **target** filter; the compound-validation filter is untouched.
  Unit-tested by `TestFbxo31IndependentTicked`.
- **`VALIDATED_TARGET_FILE` config (2026-08-14):** optional override for the validated
  (FBXO31-dependent) target list. A path to a comma/whitespace-delimited gene file (e.g.
  `data/validated.txt`) **replaces** `self.validated_targets` at the end of `get_de_validated`
  (genes upper-cased + deduped, split on `[,\s]+`); empty/absent keeps the CDD-derived list. This is
  the single source of the "dependent" set, so it drives V-mode purple colouring, the Target-validation
  filter, and the dependent-on-top / Labels behaviours. Unit-tested by `test_validated_target_file_override`.
- **Val-legend follows the target tickbox (2026-08-14):** in V mode, each validation legend key now
  tracks its **Target-validation** tickbox — `syncValLegendFromTicks()` sets the proxy trace's
  `showlegend` from `tickedVal` (`dependent`↔`valLabelYes`, `independent`↔`valLabelNo`; `rest`/"other"
  has no box → always shown). So unticking "FBXO31 independent" **removes** the orange key (not just
  dims it, unlike the legend-click `valCatShown` path); re-ticking restores it. Wired via a new optional
  `onChange` arg to `buildGroup` (called before the redraw) and also fired on V/D switch, session load,
  and init (so `FBXO31_INDEPENDENT_TICKED=false` opens with the key already absent). Verified via headless
  CDP: proxies `[true,true,true]`→`[true,false,true]`→`[true,true,true]`, legend names drop the middle key.
- **Labels eye toggle + FBXO31-dependent-on-top (2026-08-14):** two DISPLAY-row additions (both
  client-only, in `functions.py`).
  - **Labels toggle** (`#label-toggle`, open/closed-eye SVG): flips a `hideOtherLabels` flag; when on,
    `refreshLabels()` skips every gene whose `valCatOf` ≠ `"dependent"` (pinned genes always keep
    labels), so only FBXO31-dependent labels remain. Just a `scene.annotations` rebuild, no data
    re-layout.
  - **Leader-line declutter (2D + labels-off, added 2026-08-14):** `declutterLabels()` spreads the
    dependent labels so they don't overlap and draws a short arrow from each moved label to its dot.
    Key facts established via CDP: **gl3d scene annotations are real DOM** (`text.annotation-text`, in
    `g.annotation-text-g`), gl3d **honours `ax`/`ay`** (text moved 386→315px for `ay:-80`) and **draws
    the arrow** (element class is `annotation-arrow-g`, *not* `.annotation-arrow`). Algorithm: measure
    each rendered label box (`getBoundingClientRect` — note **DOMRect is `.width`/`.height`, not
    `.w`/`.h`**, the bug that first made it a no-op), estimate each dot's screen pos, greedily walk
    top-to-bottom pushing any label overlapping an already-placed one (same x column) below it, then
    relayout with `showarrow`+pixel `ay`. Runs one frame after the paint (boxes must exist) and on 2D
    pan. **2D-only** (3D reprojects each rotation). CDP-verified: 45 clustered dependent labels →
    overlapping pairs **15→0**, 18 labels displaced, arrows render; toggling labels back on clears them.
  - **Dependent-on-top**: in the **2D orthographic** view the camera looks down the x (SAR) axis, so
    a point's x sets only its **depth**, not its on-screen y/z. `applyRanges` (`_depthX`) pushes the
    *plotted* x of dependent genes to just past `R.x.max` → their full circles render in front of every
    other dot. Plotted copy only (`fx` + ring underlay); `o.x`/mask/hover/labels keep the real x. **3D
    shows the SAR axis, so no bump there** (a rotatable scene has no stable "on top"); `setMode` re-runs
    `recolor3d` to apply/drop the bump on 2D↔3D switch. Verified via headless CDP on the synthetic
    render: 6 dependent genes bumped to x=0.63 (>0.60 max) in 2D, bump gone in 3D, labels drop 290→6
    when the eye is toggled, no JS errors.
- **Validation plates (2026-07-17):** plates whose name ends in a configured suffix — param
  `plate_validation_suffixes=('WT','MLN','KO')` on `plot_3d_interface`, injected as
  `__VALIDATION_SUFFIXES__` — are pulled out of their date groups into a **dedicated "validation"
  sub-block** (rendered **first/at the top**, gold-accented header + a `border-bottom` rule
  (`.pf-validation`) separating it from the dated plates; same `.pf-date` structure so the tri-state
  parent + collapse work for free). **One checkbox per stem (2026-07-20, supersedes the earlier
  per-condition side-by-side checkboxes):** `validationBlock()` renders a single `.pf-stem` box per
  stem (name minus the WT/MLN/KO suffix, e.g. `Pw10WT/Pw10MLN/Pw10KO` → stem `Pw10`), labelled
  `Pw10  WT/MLN/KO`; its `data-plates` lists the member plates ordered WT→MLN→KO. Ticking a stem
  toggles ALL its member plates in `ticked` (change handler resolves a box to plates via `platesOf`;
  `syncStems()` derives each stem box's checked/indeterminate from members; `syncPlateUIHook` re-syncs
  on session load since stem boxes have no `value=`). They **start UNticked** regardless of
  `plate_defaults` (JS `isValidationPlate` forces `ticked[p]=false` at init). Shared helpers
  `valStemOf`/`valSufOf`/`valRank`/`validationGroups` (defined once near `isValidationPlate`) are used
  by both the filter and the volcano panel.
- **Grouped WT/MLN/KO volcanoes on gene hover (2026-07-20).** The per-condition "side by side" now
  lives in the **volcano panel**: `buildVolcanoHtml` splits a compound's visible plate-rows into
  validation (grouped by stem → one `.vstem` flex row per stem, conditions side by side in WT/MLN/KO
  order, each volcano highlighting the gene) vs dated (stacked as before; CSS `.vstem`/`.vstem-lab`).
  **Volcanoes only show after a gene is CLICKED (2026-07-21).** Hover just opens the compound panel; the
  `plotly_hover` handler no longer auto-shows any volcano (the old `cellHasValidation` auto-show + helper
  were removed). Once a gene is clicked (panel `pinned`), hovering a compound row shows that compound's
  grouped volcanoes (`row` `mouseover` → `showVolcano`, gated on `pinned`); the panel widens (max 96vw,
  `.vstem` scrolls if needed). Multiple compounds: paged via the existing ◀▶ (one compound at a time).
  - **Clicked gene → green ring (2026-07-21).** Clicking a dot sets `clickedGene = currentGene`; the ring
    underlay paints that gene's ring `CLICK_RING` (`#2ca02c`) instead of its category/`#333` colour, in both
    the area underlay (`applyRanges`) and the pin underlay (`buildPinTrace`). `plotly_click` defers
    `recolor3d` via `setTimeout(…,0)` (a synchronous `Plotly.redraw` inside the click dispatch re-enters and
    hangs); `unpin` clears `clickedGene` + repaints (sync is safe — it fires from the close button / Esc).
  - **Hover-any-gene trace across a stem's volcanoes (2026-07-20).** In a WT/MLN/KO stem, **hovering any
    significant-down gene point** draws a polyline connecting that gene's position across the stem's
    conditions (incl. where it's no longer significant — e.g. suppressed in WT, gone in KO), like a slope
    chart. It is **hover-driven** (no always-on line) and works for **every** significant-down gene in the
    panel, not just the compound's focal gene. Data: `__STEM_TRACE__[vk][gene] = [fx, fy, aspect, isHit]`
    for every validation contrast `vk` — positions **reused from `ring_pos`** (no re-render; every
    significant-down gene is already a focal gene of its own volcano via `compounds_df`+completion, so its
    ring position in each condition exists). Built in `plot_3d_interface` by iterating `custom` +
    `ring_pos`; each validation plate-row carries its `vk` at index 7 so the JS maps a cell → contrast.
    **Runs on BOTH the fresh-render AND the panels-cache path (fix 2026-07-20):** the build was previously
    inside the `panels is None` (rebuild) branch, so `IFACE_OVERWRITE=false` (panels loaded from cache)
    skipped it and shipped an empty `__STEM_TRACE__` → gene-linking silently disabled. It now lives after
    both branches, reusing the `ring_pos.json` the cache branch already loads, so linking survives cache
    re-runs. Regression test: build twice (overwrite then `IFACE_OVERWRITE=false`) → same 1,904 positions. JS: `placeHotspots`
    lays invisible `.vhot` targets over each `isHit` gene point (positioned via the `<object>` rect +
    injected `(fx,fy)` + letterbox math — works http AND `file://`, no `contentDocument`); `traceGene`
    draws the `<polyline>` + per-point markers + gene label into the `.vstem-trace` overlay; re-placed on
    `showVolcano` + `<object>` load + delayed passes + resize. The per-volcano `#tgt-ring` marker / ring
    `<path>` bbox-centre (parsed from the serialised SVG, no extra draw) still feeds `ring_pos`; `ring_pos`
    is persisted to `volcanoes_px/ring_pos.json` for cached re-runs. `__RING_POS__` (the old focal-only
    always-on line) is superseded by `__STEM_TRACE__`. **Cache (SELECTIVE — important):** the on-disk
    volcano cache is keyed by identity, not content. `_volcano_cache_fname(..., version=...)` salts the key
    ONLY for validation-plate volcanoes (`version='v2'`), so on the trace-line change ONLY those re-render
    while every other volcano keeps its original (unsalted) filename → cache hit. This matters at scale: a
    global bump would re-render ALL volcanoes (real data ≈ 67k → ~4.5 h); the selective salt re-renders
    only the validation subset. `_task_ver` derives validation-ness from the plate at `custom[g][ei][3][pi][0]`
    via `plate_validation_suffixes`; `ring_pos` (and thus `__STEM_TRACE__`) is recorded only for `_val_fns`
    (validation filenames) so the injected map stays small. `ring_pos.json` persists it for cached re-runs.
    QA both `file://` + `http.server`.
    **Volcano-render perf:** `build_interface` sets `volcano_n_jobs = os.cpu_count()-2` (was hardcoded 16);
    combined with the no-extra-draw ring extraction, a cold validation render is ~3-4× faster. The dense
    non-significant cloud (all ~8k genes) rasterised per volcano is the remaining per-volcano cost.
  - Synthetic fixture: validation plates `Pw10{WT,MLN,KO}` + `Pw11{WT,KO}` (MSPlate namespace);
  the **deterministic case** (updated 2026-07-20 for stem completion) forces compound `SRB-0000006` /
  gene `G_00000` to be a significant down-hit on `Pw10WT/Pw10MLN/Pw11WT`, measured-but-NOT-significant
  on `Pw10KO` (logfc 0.20), and drops all `SRB-0000006 × Pw11KO` rows (compound never run there).
  Validation-plate experiments are made non-`Silent` (line-359 drop) so hits always render. Note:
  interface plates = the **MSPlate** namespace, NOT the FBX `plate` column —
  disjoint in the fixture, so `__PLATE_DEFAULTS__` filters to `[]` there (known gap; real data shares
  the namespace). Fixture `df_raw.uniquecontrast` is plate-specific so a compound's plates all survive.
- **Validation-stem completion (2026-07-20).** A `(gene,compound)` is a hit only where it is
  significant-down, so a gene significant in one condition of a stem (e.g. WT) but not another (KO)
  would have no KO volcano — breaking the side-by-side comparison. `get_iface` now **completes stems**:
  for each significant hit on a validation plate, it adds the stem's OTHER conditions **where the
  compound was actually run** (contrast exists in `report`) **and** the gene was measured (`meas`),
  pulling the gene's real (non-significant) logfc from the full MEASURE. Conditions where the compound
  was never run are omitted (no fabricated volcano). Added rows carry `is_completion=True`
  (new `compounds_df` column); the volcano render already rings the target gene regardless of
  significance (`_volcano_svg_string`), so the gene shows in the insignificant grey cloud.
  Threading: suffix rule from `config.VALIDATION_PLATE_SUFFIXES` (default `[WT,MLN,KO]`, also passed to
  `plot_3d_interface` as `plate_validation_suffixes`); the flag rides as plate-row **index 6** (`pl[6]`).
  JS: completion rows **ride along** their significant sibling — `visPlates` includes a completion row
  iff its plate is ticked AND its stem has a visible real hit (bypassing the activity filter so an
  off-activity KO still appears); `geneHasVisibleCompound`/`collectExport` **skip** completion rows
  (they are display-only context, not hits/exports); `buildVolcanoHtml` tags them `.vcomp` + a
  `.vns` "not significant" label. Verified end-to-end (`test_validation_stem_completion` + headless
  CDP): `Pw10` shows WT/MLN hits + a KO "not significant" cell; `Pw11` shows WT only (KO omitted).
- **gene research** is sourced from `config.GENE_RESEARCH` (whole-genome ~10K-gene JSON) and
  **filtered to plotted genes** before injection (`__GENE_RESEARCH__`) to bound the payload; the
  build prints `[gene_research] kept/total — MB injected`.
- **Per-gene patents / DepMap card removed from the Px interface (2026-07-20).** The `#hover-patents`
  side-card (gene + "DepMap ↗" link + patents table / "no patent entries") was **never fed data** in
  `plot_3d_interface` (`build_interface` doesn't pass `gene_patents_df`), so it always rendered an empty
  slim card — removed from `_INTERFACE_INJECT` (HTML/CSS/JS) and its `__GENE_PATENTS__`/`__DEPMAP_URL__`
  injection + `gene_patents_df`/`gene_patents_top_n`/`depmap_url_template` params dropped from
  `plot_3d_interface`. The separate `plot_target_3d` still has its own patents panel (untouched), and the
  `_build_gene_patents_html_map` helper remains for it. Don't re-add it to the Px interface.
- **PIN/HIDE** (top filter section, collapsible): search+autocomplete over genes & compounds; a
  **Selector** (orange) pins, a **Hide** (red) hides. Pinning a compound pins its target genes;
  hiding a **gene** drops its dot, hiding a **compound** drops only that compound (gates at
  `cmpAllowed`), its target genes stay. Hide supersedes pin. Click-to-select on the plot/panel.
  - **Pins are ALWAYS shown, shape encodes selection state (2026-07-17, reverses the 2026-06-30
    filter-gating):** a pinned gene renders (dot/label/count) regardless of whether it has a compound
    on the ticked plates/activities — the overlay now uses `shownPinSet()` (= `effectivePinSet()` gated
    only by the master toggle), and `visiblePinSet()` was removed. Per-point `marker.symbol` on the
    overlay: **circle** when the pin has a visible compound (`geneHasVisibleCompound` true — looks like a
    normal in-selection dot), **diamond/losange** when it doesn't. `applyRanges` still repaints the
    overlay (`buildPinTraceHook`) on every filter change so the shape flips live as plates/activities
    are ticked. Pin outline matched to the area dots (`line #333/1`).
  - **Master "★ pinned (N)" toggle:** a single HTML checkbox (`#pin-toggle`) docked just below the Plotly
    legend — positioned from the legend's `getBoundingClientRect()` via `placePinToggle` (re-run on
    resize + `plotly_afterplot`; falls back top-right if no legend). Enables/disables the whole pinned
    overlay (`showPins`) without unpinning (chips stay). The overlay trace is `showlegend=False` (the
    toggle is the single control; a native key would misrepresent the mixed circle/diamond shapes).
    Persisted in `.iface` session (`showPins`) and the URL hash (`sp=0` when off).
  - **Solo / "only pinned" view (2026-07-20):** **double-clicking** `#pin-toggle` sets `soloPins` — the
    3D/2D scatter then shows **only the pinned genes**, hiding all others (mirrors Plotly's
    legend-double-click-to-isolate). Implemented in `applyRanges`: when `soloPins`, a point's mask is
    `!!shownPinSet()[gene]` (all sliders/plate/activity/target filters bypassed) instead of the normal
    filter chain, so every pin shows and nothing else does. Double-click again to restore. The toggle
    goes gold (`.solo`) with a "· only" suffix (`#pin-toggle-solo`); auto-exits (and re-filters) when the
    last pin is removed. `redrawPins` re-runs `recolor3d` on solo enter/refresh/exit (pin/unpin during
    solo re-filters the area traces). Persisted in session (`soloPins`) and hash (`so=1`). Verified
    headless (CDP): in solo the area traces collapse to exactly the pinned set; restore returns to the
    prior filtered view.
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
- **Filter panel** fixed `width: 415px` (a `position:fixed` overlay, top-left) so chip boxes wrap
  (3 compound chips per row). Labels: `labelMax` floor 800, sampled evenly across the MS range (not
  top-N) so they spread across the cloud.
- **Default view is 2D + plot in a bounded box next to the panel (2026-07-06, redone 2026-07-13):** the Axes
  toggle starts on **2D** (`seg2d active` + an on-load `setMode(true)` via `initTwoD`). The plot is **pinned by
  CSS to a fixed box** immediately to the right of the filter panel and above the range panel —
  `.plotly-graph-div { position:fixed; left:455px; right:8px; top:8px; bottom:205px }` — and Plotly (responsive)
  fills that box. `CAM2D` is orthographic with **`eye/center z=-0.40`** — looking slightly below centre lifts
  the plot's **top to align with the panel top** (viewport-independent, so it holds on any screen). **`fitBox`**
  (run by `initTwoD` on load, on the 2D toggle, + on every window `resize`) calls `Plotly.Plots.resize` and, via
  one relayout: (a) **square domain** `scene.domain.x=[0, fx]`, `fx=min(1, h/w)` — left-aligns the plot in the
  box next to the panel; (b) **`aspectmode='manual', aspectratio={x:1,y:1.15,z:1.15}`**; (c) **camera pan** —
  `camera.eye/center.y = pan` slides the data cube **left toward the panel** (see below); and (d) **legend
  tracks the data's right edge**: `legend.x = min(fx*0.90 − pan*0.30 + 0.02, (w−235)/w)` (xanchor left) — the
  `−pan*0.30` follows the data as the pan shifts it, the `(w−235)/w` cap keeps the legend text on-screen; the
  default `legend.x≈1.02` sits off the wide div and vanishes. **Why the pan is the key lever (2026-07-14):** the
  gl3d scene reserves a large *internal* left margin (the vertical "MS score" title strip) that domain/aspect
  tricks CANNOT remove — so the data floats ~200px from the panel by default. Only a **camera pan** moves the
  data cube itself (safely clipped at the panel edge by the CSS box — the old "pan drifts into panel" failure
  was pre-box). The pan is **height-adaptive**: `pan = clamp((h−640)*0.0007, −0.05, 0.12)` — tall boxes have a
  wider gl3d margin so we pan more (tight left); short boxes have almost none, so we nudge *right* (negative) to
  keep the title from clipping. Result on 1912×1018: panel→plot ≈56px, plot→legend ≈133px (was ~210 / ~240).
  Removing the old `×1.10` on `fx` was also part of the fix — it *added* horizontal centring pad that pushed the
  plot away. **Aspect/pan are safe ONLY because the CSS box bounds the plot.** So: box bounds size; pan+domain do
  horizontal tightness; camera-z does vertical position. The camera relayout is applied **only when 2D is
  active** (else a resize in 3D would clobber the turntable camera). Verified 1912×1018, 1920×800, 1366×768,
  2560×1330. **Known edge case:** 2560×**1030** (ultra-wide monitor + unusually short window) clips the title —
  that geometry has a near-zero gl3d left margin; realistic ultra-wide (2560×1330) is fine.
  **Why the box, not domain/aspect/camera tuning:** the plot is `responsive:true` (fills the window), so a
  fixed `scene.aspectratio` (tried 1.45) made it too **tall** and it overflowed short viewports (0-tick below
  the fold, only "50" visible) — exactly the bug reported. Hand-tuned domain/camera/aspect only looked right at
  the one window size they were tuned at. The CSS box decouples the plot from window size entirely. Also learned:
  **orthographic zoom ignores camera distance** (`eye.x`), and `scene.aspectmode:'manual'` frame geometry is
  data-range-independent (0-100 vs 0-212 MS render identically). **Do not use `margin.l`** to clear the panel —
  it squeezes the scene and clips the vertical axis. Session/hash loads still override the 2D/3D mode.
- **Gene colour V/D toggle (2026-07-20):** a second pill (`#color-toggle`) sits **on the same Display row** as the
  Axes 2D/3D pill, separated by a `|` (`.disp-sep`), and switches how genes are coloured. **V (validation, default)**
  = per-gene by FBXO31 status, each drawn as a **dark ring + light fill** (like the reference volcano circles):
  **purple** dependent (fill `#B98BD6` / ring `#7B2D8E`), **orange** independent (fill `#F2B366` / ring `#D07C1A`),
  **light blue** other (fill `#B3D4E6` / ring `#6BA3C7`). **D (disease)** = the original per-area colours (each
  Scatter3d trace's fixed `disease_area` colour, solid + `#333` ring). Colours from `VALIDATION_COLORS`
  (Px_interface `build_interface`) → `validation_colors` param → `__VALIDATION_COLORS__`; default via
  `color_mode_default='V'` → `__COLOR_MODE_DEFAULT__`. **No re-render** — recolouring rides `recolor3d`
  (`applyRanges`): in V each area trace's `marker.color` becomes `ft.map(valFillOf)` (light fill); in D →
  `origColor[ti]` (solid disease colour, captured in `captureOrig` from `area_data[i].color`). The dark **ring** is
  NOT `marker.line` — gl3d caps outline width (verified: 3.5 vs 8 render identically) — but a larger dot drawn
  underneath (ring underlay; see the dot-size/ring-thickness entry). (Trace 0 backdrop is `opacity=0`, so
  its colour flip is a no-op — the visible dots are the highlighted/area traces only.) **Caveat:** gl3d officially
  supports per-point `marker.color` string arrays but only numeric→colorscale for `marker.line.color`; the per-point
  string ring array is accepted by plotly.py and *may* render — if a build shows uniform/black rings on the plot,
  switch the ring to the numeric-index + `line.colorscale` mechanism (legend swatches are unaffected — see below).
  **Legend follows the mode (native top-right Plotly legend):** 3 legend-proxy Scatter3d traces
  (`__VAL_LEGEND_TRACES__`, indices after the pin trace) each carry ONE `None` point (gl3d shows no legend entry for
  a truly empty `x=[]` trace) with a uniform fill+ring (so each swatch renders its ring reliably), names
  `FBXO31 dependent` / `FBXO31 independent` / `other`. `setColorModeHook` flips `showlegend` — disease-area traces
  on in D / off in V, the 3 validation traces the reverse (Python sets the load state via each trace's
  `showlegend`). **Legend keys filter by category (2026-07-20):** since the proxy traces are empty, Plotly's default
  legend toggle can't act on the real points — so `plotly_legendclick` / `plotly_legenddoubleclick` are intercepted
  for the 3 proxy curves (return `false` to suppress default): click toggles one `valCatShown` category, double-click
  isolates it (or restores all). The mask in `applyRanges` gates V-mode points by `valCatShown[valCatOf(gene)]`, and
  `_syncValLegendDim` parks a hidden category's proxy at `visible:'legendonly'` so its key dims. Non-validation keys
  (disease areas in D) keep Plotly's default toggle. **Plotted rings are an underlay dot, not `marker.line`
  (2026-07-21)** — see the ring-thickness entry; the SVG legend swatches still use `marker.line` (width 3.5), which
  SVG honours. Persisted in session (`s.colorMode`) + hash
  (`cm=D`, default V omitted) via `setColorModeHook`.
- **Dot size = # significant compounds (2026-07-21):** each gene's marker is sized by how many DISTINCT compounds it
  is a significant (non-`is_completion`) hit in — a **static grand total across all plates/activities** (a fixed gene
  property; filters change which dots *show*, never their size — user's explicit choice over the dynamic/current-filter
  alternative). Buckets 1,2,3,4,5,>5 → `size_buckets` px (config `GENE_SIZE_BUCKETS`, default `[6,8,10,12,15,20]`;
  `plot_3d_interface` param → `__SIZE_BUCKETS__`). Count computed once from `custom[gene]` (`_sig_count`: entries with
  ≥1 non-completion plate-row, or a scalar-volcano entry) → `gene_size` → `__GENE_SIZE__` (`{gene: px}`). Painted
  per-point via `sizeOf` in **both** colour modes: `applyRanges` sets `marker.size = ft.map(sizeOf)` on every area
  trace and `buildPinTrace` sets it on the pin overlay; `_add_colour_trace` seeds the same array so it's right before
  the first `applyRanges`. A **size key** (`#size-legend`: 6 grey dots at the bucket px, labels `1…>5`, title
  *size = # significant compounds*) is docked **above the top-right gene legend** by `positionSizeLegend` (anchored to
  the `.legend` rect on load + `plotly_afterplot` + resize; left-aligned but clamped so the wider box never clips the
  right edge). To make room, the gene legend is nudged down to `legend.y=0.88` — set in `update_layout` AND re-applied
  by `fitBox` on every resize/2D toggle (keep the two in sync). gl3d per-point `marker.size` arrays are fully supported
  (unlike the ring-colour caveat above). **The old bottom-left `#axis-legend` (X/Y/Z descriptions) was removed
  entirely (2026-07-21)** with its `__AXIS_LABELS__`/`__AXIS_HELP__` globals + the `axis_help` param + `_axis_help`
  dict — axis meanings still show on the range-panel slider labels. Verified in-browser (swiftshader WebGL): size key
  stacked above the FBX legend, dots at varied sizes with thick rings, 0 console errors.
- **SAR axis title in 3D only (2026-07-22):** removing `#axis-legend` dropped the only on-plot mention of the SAR
  (x) axis in the default 2D view — which looks straight down that axis, so gl3d can't render a native x-axis title
  there (verified: forcing `xaxis.visible` in 2D shows nothing). Native gl3d also won't place the *3D* x-axis title
  readably in this fitBox-tuned layout (it lands off the bottom/right edge; the plot renders larger than the viewport).
  A **paper annotation** was tried first but *floats* (paper coords don't track the axis as the camera/zoom/window
  change). Fix: **`refreshLabels` appends a scene annotation** `"SAR predictability"` at a bottom x-edge (data coords,
  so it rides the cube) — **3D only** (guarded by the `#disp-toggle .seg2d` active check; 2D stays unlabelled per the
  user's ask). **Tracks rotation (fixed 2026-07-22):** gl3d always draws the x-axis on the MS floor (`z=z_min`) and on
  the association (y) edge facing the viewer at the bottom. That edge is **`y = (eye.y·eye.z > 0) ? y_max : y_min`,
  `z = z_min`** — calibrated by rendering the actual x-ticks (enlarged, red) at default / side / below cameras and
  matching a per-edge marker line to them. (The earlier per-axis `eye`-sign rule *and* a gl-axes3d `lastCubeProps.axis`
  decode both failed: the edge choice is a *coupled* function of the whole camera — e.g. default `axis=[-1,-1,-1]`→y_max
  vs below-view `axis=[-1,-1,1]`→y_min with `axis[1]` unchanged — so neither separable rule holds.) A `plotly_relayout`
  listener (camera-key-guarded to avoid a scene.annotations→relayout loop, rAF-debounced) re-runs `refreshLabels` on
  rotate; `setMode` does `setTimeout(refreshLabelsHook, 0)` so the 2D/3D toggle adds/removes it. Needs `refreshLabels`
  to run — it does whenever in-range dots exist (real data; the synthetic 0-dot default doesn't, which only affects
  testing). Verified with dots at 4 orientations incl. a below-view: the label rides the SAR axis; 0 console errors.
- **MS slider filters per compound, not the gene max (2026-07-22):** the dot's z stays at the gene's max MS
  (`mscore.groupby('genes')['ms_score'].max()`) so genes keep their position under any filter — but the MS range
  slider now filters each **compound experiment** by its OWN per-(gene,compound,plate) MS score. That score is sourced
  from the SAME metric as the position (`FBX_MSSCORE` per uniquecontrast ∪ `df_raw`, FBX wins on shared ucs), attached
  to `compounds_df.ms_score`, and carried into each panel plate-row at **index 8** (`pl[8]`; null on completion rows).
  Client: `msLo/msHi` are set from the z-slider window in `applyRanges` (the gene-max z coordinate test is *removed*
  from the in-range predicate), and `msOk(pl)` gates every real plate-row inside `visPlates` + `geneHasVisibleCompound`.
  So out-of-range experiments prune from the compound panel/hover/export exactly like the Plate/Activity ticks, and a
  gene's dot drops when it has no in-range experiment left — while its plotted position never moves. Invariant (tested):
  a gene's max per-entry MS never exceeds its plotted z. Verified in-browser: MS≥6 → 291→49 dots, 0 positions moved,
  reset → 291.
- **Thick rings via an underlay dot (2026-07-21):** gl3d **hard-caps `marker.line.width`** — bumping it does nothing
  (proven in-browser: width 3.5 vs 8 render identically). So the visible ring is drawn as a **larger dot underneath
  each fill**: a dedicated ring-underlay trace holds one dot per visible gene at `sizeOf(gene) + 2*RING_PX`, in the
  gene's ring colour, and renders BEFORE the fills so the exposed rim = the ring. Thickness `RING_PX` is config
  `GENE_RING_PX` (default 4) → `plot_3d_interface(ring_px=)` → `__RING_PX__`. **Two underlay traces**: the area one
  (`__AREA_RING_TRACE__`, index 1, right after the backdrop) is repainted by `applyRanges` from the same masks
  (per-point ring colour = `valRingOf` in V / `#333` in D); the pin one (`__PIN_RING_TRACE__`, just before the pin
  fill) by `buildPinTrace` (`#333`, per-pin symbol). Fill traces set `marker.line.width=0` + `opacity=1.0` (no
  bleed-through); trace order is backdrop → area-ring → area-fills → pin-ring → pin-fill → legend proxies (all indices
  captured dynamically). **No z-fighting** in 2D or 3D — the fill and its underlay share the exact same coordinate, so
  depth ties break by trace order (fill always on top). Legend swatches keep `marker.line` (SVG honours it). Verified
  in-browser: 291 area dots with thick per-category coloured rings, pins with thick `#333` rings, 0 console errors.
- **Range readouts are edit-in-place (2026-07-14):** each axis's `lo – hi` readout (`#x-val` etc.) is two
  `contenteditable` `.rp-edit` spans (dotted underline = editable hint). Click a number, type, and **Enter/blur
  commits** (Esc reverts): `commitEdit` clamps to `[min,max]`, keeps `lo ≤ hi` (editing lo can't exceed hi and
  vice-versa), writes the slider `.value`, and calls `applyRanges` — same path as dragging, so the filter/labels/
  count all update. Invalid input reverts. `applyRanges` writes the numbers back into the spans but **skips the
  one that is `document.activeElement`** so it never clobbers mid-type. No new fields were added — the existing
  readout became editable (user's explicit ask).
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
  **Superseded 2026-10-01:** real Px interface files can now go on the box (see the 2026-10-01 decision entry).

## Local visual QA — headless chromium screenshots (2026-07-06)
To visually check the rendered interface without a browser, screenshot it with the **cached Playwright
chromium** (no install needed): `~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome`.
- **Serve over HTTP** (the deferred `_data.js` won't load over `file://`): from the interface dir,
  `python -m http.server 8137 --bind 127.0.0.1`.
### How to actually drive chrome here — the working recipe (chrome 148 / WSL2, 2026-07-14)
The old one-liner (`chrome --headless=new … --screenshot=…`) **NO LONGER WORKS on this box** — the one-shot
`--screenshot` mode **hangs forever and never exits**, even on a trivial `data:text/html` page (it stalls on
GCM/background-network registration + swiftshader teardown; a `timeout` kill then SIGTERMs it before the PNG is
flushed → no output). The **Playwright python package is not installed** in any conda env (only the browser
*binary* is cached), and per CLAUDE.md we don't silently `pip install`. So drive chrome over the **DevTools
Protocol (CDP)** with a tiny stdlib-only client. Exact procedure that worked:

1. **Serve the interface** (deferred `_data.js` needs HTTP, not `file://`): `python -m http.server 8765
   --directory tmp/out/interfaces` — launch via the **Bash `run_in_background:true`** flag (a plain `&` gets
   reported "completed" but keeps serving; either way it survives). `curl -s -o /dev/null -w "%{http_code}"` to
   confirm 200.
2. **Launch ONE persistent chrome with the debug port**, via Bash **`run_in_background:true` AND
   `dangerouslyDisableSandbox:true`** — this combo is the crux. The Bash sandbox **blocks the remote-debug port
   from binding** (chrome exits 1); the `dangerously…` flag is what lets the port come up. Flags that matter:
   `--headless=new --no-sandbox --remote-debugging-port=9222 --use-gl=angle --use-angle=swiftshader
   --enable-unsafe-swiftshader --ignore-gpu-blocklist --disable-background-networking --disable-component-update
   --disable-sync --user-data-dir=<scratch> about:blank`. Software WebGL (swiftshader) is mandatory — Scatter3d
   incl. the 2D ortho view needs WebGL, and `--disable-gpu` breaks it. `--disable-background-networking` stops
   the GCM phone-home that hung the one-shot (also our no-telemetry policy). Poll
   `curl -s http://127.0.0.1:9222/json/version` until it returns JSON (~4–5s).
3. **Screenshot via CDP** with a ~60-line stdlib client (no pip deps): open a raw `socket`, do the WebSocket
   upgrade handshake by hand (client→server frames MUST be masked; server→client are not), then JSON-RPC over
   it. Sequence: `Page.enable`, `Runtime.enable`, `Emulation.setDeviceMetricsOverride`
   `{width,height,deviceScaleFactor,mobile:false}` (this sets the exact viewport — no `--window-size` needed and
   DPR is controllable), `Page.navigate`, `time.sleep(~8s)` for gl3d/data.js, optional `Runtime.evaluate` to
   inject JS, then `Page.captureScreenshot {format:"png"}` → base64 in the JSON result → decode to file.
   **Gotcha:** in chrome 148 `/json/new` needs an **HTTP `PUT`** (a GET returns `405 Method Not Allowed`); open
   the tab with `PUT /json/new?about:blank` then close it with `GET /json/close/<id>`.
4. **Cleanup:** the harness may report the background chrome task "failed exit 1" yet leave chrome + its child
   procs (zygote/gpu/renderer) running; `pkill -f` is flaky here (WSL2). Reliable: `ps -eo pid,ppid,cmd | grep
   chrome-linux64`, then `kill -9 <pid>`; likewise the `http.server`.

**Measuring layout from a screenshot:** gl3d draws axis titles/ticks on the **WebGL canvas, not SVG** (the
`svgContainer` overlay is empty; DOM `getBoundingClientRect` on labels returns nothing), and `axesPixels` only
gives data *ranges* not screen px — so **DOM measurement of the plot box is impossible**. Use **PIL on the PNG**:
mask colored (`max−min channel > 40`) pixels, ignore `x<452` (panel) and `y<30` (toolbar), then cluster columns
with a >55px gap → the clusters are `[data]` and `[legend]`; their edges give panel→plot and plot→legend gaps,
and a *merged* cluster means either a <55px (tight, good) gap or an overlap (look at the image to disambiguate).
- **SYNTHETIC RENDERS ONLY.** Never screenshot a real-data render (Desktop/output dirs) — the PNG
  would carry structures/thumbnails and cross the wire. Screenshot `tmp/out*/interfaces/…` only.
- **Render synthetic** in the `ML` conda env (base python lacks numpy): `conda run -n ML python
  tests/make_synthetic.py --out tmp` then `conda run -n ML python python/Px_interface.py --config
  tmp/config.yaml --output_dir tmp/out`. The synthetic `ms_score` is now bottom-heavy 0–~208 (mimics real).
- **The default view shows `0 proteins`** (the default activity/plate filters exclude the random synthetic
  compounds) — the frame/axes still render, but to see **points** for layout QA, inject a tiny script before
  `</body>` that checks every `#filter-panel input[type=checkbox]` and dispatches `change`.
- **TEST AT MULTIPLE VIEWPORT SIZES, especially SHORT ones** (`--window-size=1920,800` as well as
  `1920,1080`, `2560,1080`). The plot is `responsive:true`; a layout bug that fits a tall window can overflow
  a short laptop window — that's exactly how the fixed-aspect regression slipped through (looked fine at
  1900×1000, broke at 1920×800). Also fire a `resize` event ~0.6s after load so the CSS-boxed plot re-fits.

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
- 2026-07-08 — **VPN live & end-to-end verified.** IT finished the FortiGate side; `healthcheck.sh` now
  reads **7 ok / 0 fail, tunnels 1/2 UP** (1/2 is normal: one active + one standby). From a remote laptop
  on FortiClient, `https://172.20.2.125/` loaded the placeholder through the tunnel behind `serac_user` +
  shared password — full chain confirmed (FortiClient → FortiGate → S2S → VPC → private EC2 → nginx). **Only
  remaining functional milestone: Step 6** — upload the real (2D-default) interface HTML to `/var/www/webapp/`
  (transfer via S3-gateway-endpoint or base64-over-SSM; scp/SSH closed by design). Do **not** `terraform apply`
  before Step 6 — the staged boot-retry + `monitoring.tf` changes replace the EC2 and wipe the hand-installed
  nginx + any upload.
- 2026-07-14 — **shareable AWS architecture diagram** added at `docs/aws_architecture_mermaid.shared.md` for
  external (AWS-contact) review: same topology as `docs/aws_architecture_mermaid.md` but with the account ID,
  the globally-unique TF state-bucket name, and all IPs/CIDRs redacted to generic labels (region `eu-north-1`
  kept). The original stays intact for internal use. IT flagged the account ID + bucket as world-wide-unique.
- 2026-07-16 — **moving the repo from personal → org (GitHub transfer).** Origin was
  `git@github.com:gtamo39/Px_interface.git` (personal). **Pre-flight (clean):** no chemistry data tracked
  (`.gitignore` covers `data/`, `tmp/`, `output/`, `*.parquet`, `srb_png/`, `volcanoes_px/`); no passwords/PSKs
  committed (PSKs live only in encrypted TF remote state; htpasswd/tfvars examples use placeholders). **Caveat:**
  the AWS account ID + state-bucket name ARE committed in `aws-vpn/main.tf:24`
  (`vpn-project-tf-state-620423424620`), plus internal CIDR defaults in `aws-vpn/variables.tf` — acceptable for a
  trusted org repo, but they travel with history (parameterize the backend bucket via `-backend-config` if that
  ever needs scrubbing). Confirmed local `main` == `origin/main` (0 ahead/behind) before moving. **Transfer
  procedure** (chose GitHub *transfer* over new-repo-push, to keep history + issues + PRs + stars + URL
  redirects; `gh` CLI is not installed, so via web UI): (1) `github.com/gtamo39/Px_interface` → **Settings** →
  **Danger Zone** → **Transfer** → type repo name to confirm → new owner = the org slug → confirm (needs org
  membership with repo-create rights; authorize SAML SSO if prompted). (2) Old path redirects to
  `github.com/<ORG>/Px_interface`. (3) Re-point the local clone:
  `git remote set-url origin git@github.com:<ORG>/Px_interface.git` then `git remote -v` + `git fetch origin`.
  **Done (checked 2026-09-30, `git remote -v`):** `origin` is `git@github.com:SeracBio/Px_interface.git`, so
  the org slug is `SeracBio`.
- 2026-07-16 — **security review of `aws-vpn/` + `docs/`** (defensive; own infra). Baseline is strong (private
  EC2, no public ingress, SSM-not-SSH, IMDSv2 required, encrypted EBS, IKEv2/AES-256/SHA2-256/DH14, state bucket
  encrypted+versioned+public-blocked+TLS-only+`prevent_destroy`); no internet-facing unauth path. **Fixes
  implemented** (code only — NOT yet `apply`d): **H1 egress lockdown** — `ec2-sg` egress was `0.0.0.0/0` (a
  compromised EC2 could pivot into the office LAN / VPN pool over the propagated VPN routes); replaced with just
  443→`vpc_cidr` (interface endpoints), 443→S3 managed-prefix-list (dnf via gateway endpoint), 53 udp/tcp→
  `vpc_cidr` (VPC resolver), 123 udp→`169.254.169.123/32` (Amazon Time Sync); `vpce-sg` egress tightened to
  `vpc_cidr`. **H2 state-bucket policy** — state holds the webapp TLS private key + VPN PSKs; added
  `DenyOutsideAccount` (via `aws:PrincipalAccount` = caller account) + an OPTIONAL `allowed_state_principals`
  allow-list (default empty = no lock-out; account root auto-appended when set) on top of the existing
  `DenyNonTLS`. **M1** — moved the account-id-bearing state bucket + lock table OUT of `main.tf` into a
  partial backend: `terraform init -backend-config=backend.hcl` (`backend.hcl.example` committed, `backend.hcl`
  gitignored). Both stacks `terraform validate` clean. **Deferred (documented, not done):** rest of H2
  (MFA-delete, access logging, dedicated CMK), M2 (single shared Basic-Auth cred, no rate-limit → per-user
  SSO + `limit_req`), M3 (self-signed cert + HSTS-off → distribute CA then enable HSTS), L1 KMS-decrypt ARN
  pin, L3 pin AMI. **Docs sanitized (2026-07-16):** account ID, EC2 instance ID, VPN ID, private host IP, and
  tunnel public IPs removed from `docs/aws_docs.md` + `docs/aws_architecture_mermaid.md` (replaced with
  placeholders / `terraform output` retrieval; internal RFC1918 CIDRs kept). Current tracked files are clean;
  the identifiers still exist in **git history** (only a history rewrite removes those). **Diagrams updated
  (2026-07-16):** both `docs/aws_architecture_mermaid.md` + `.shared.md` now show the CloudWatch/SNS monitoring
  (previously missing from the map), the EC2-SG egress lockdown, and the same-account state-bucket policy —
  topology unchanged; kept the two files in sync. **Internal diagram now local-only (2026-07-16):**
  `docs/aws_architecture_mermaid.md` (real internal CIDRs) is gitignored + `git rm --cached` (untracked, local
  copy kept) so only the redacted `docs/aws_architecture_mermaid.shared.md` lives in git going forward. Takes
  effect on the remote after commit+push; the file still exists in **git history** (history rewrite to remove).
- 2026-07-16 — **Step 6 prep: interface artifacts S3 bucket** — added `aws-vpn/s3.tf`: a hardened bucket
  `${project}-interface-${account_id}` (name computed at apply → not committed; versioned, SSE-S3/AES256,
  public-access-blocked, TLS-only + same-account policy) + an `s3:GetObject`/`ListBucket` grant on the EC2
  instance role, and an `interface_bucket` output. Upload path: workstation `aws s3 sync tmp/out/interfaces/`
  → bucket → EC2 pulls via the S3 gateway endpoint → `/var/www/webapp`. AES256 (not KMS) so no extra
  kms:Decrypt grant is needed. `terraform validate` clean; not yet applied. Create the bucket alone without the
  staged EC2 replacement via `terraform apply -target=aws_s3_bucket.interface …` (+ the 4 bucket sub-resources
  + `aws_iam_role_policy.s3_interface_read`). And keep the box on the SYNTHETIC render until M365 SSO (hard
  privacy rule; **superseded 2026-10-01**: real Px interface files can now go on the box). **Operational:** next `terraform init` needs `-backend-config=backend.hcl`
  (`-reconfigure` if migrating from the old hard-coded backend); applying H1 changes the SG in place (no EC2
  replace), but a full `apply` still triggers the pending boot/monitoring EC2 replacement — same "not before
  Step 6" caveat as above. The bootstrap bucket-policy apply is independent and safe anytime.
  **`backend.hcl` gotcha (2026-07-16):** `terraform init -backend-config=backend.hcl` FAILS with "file could
  not be read" until you create `backend.hcl` — only `backend.hcl.example` is committed. Create it locally
  (gitignored): `bucket = "vpn-project-tf-state-<ACCOUNT_ID>"` + `dynamodb_table = "vpn-project-tf-lock"`, or
  generate from the bootstrap outputs (`terraform -chdir=bootstrap output -raw state_bucket_name` /
  `lock_table_name`). Same bucket/key as before, so `-reconfigure` re-inits without a state migration prompt.
- 2026-07-16 — **remaining next steps (ordered, for the operator).** (1) **Review + commit** the staged
  hardening + doc-sanitization changes (nothing committed yet). (2) **Git history** still contains the old
  account ID / bucket / instance-VPN IDs — decide whether to `git filter-repo` BEFORE the org transfer or accept
  it (org = trusted). (3) **Transfer repo → org** (web UI Settings → Danger Zone → Transfer), then re-point
  `origin`. (4) **Apply infra fixes in order:** (a) bootstrap bucket policy — `cd aws-vpn/bootstrap &&
  terraform init && terraform apply` (safe anytime; optionally set `allowed_state_principals`); (b) re-init main
  stack — `terraform init -backend-config=backend.hcl -reconfigure`; (c) main `apply` ONLY together with Step 6,
  since it REPLACES the EC2 (staged `monitoring.tf` + boot-retry) — `terraform plan` (expect: egress rules, SG,
  alarms, boot retry), `apply`, re-upload interface, then `bash aws-vpn/healthcheck.sh` to confirm the hardened
  box booted (this is the real test that the H1 egress lockdown didn't break `dnf`/SSM; expect 7 ok, nginx
  active, 4/4 endpoints). (5) **Step 6** — upload the real interface (S3-via-gateway-endpoint recommended, or
  base64-over-SSM for a small file).
- 2026-07-16 — **Step 6 done + boot made hands-off. What went wrong on the manual attempt** (root causes, so
  it never recurs): the running box (`172.20.2.66`) had been replaced by an earlier boot whose `user_data`
  (a) **predated the retry loop** → hit the boot race (cloud-init ran at 8 s uptime, before endpoints/routing;
  `dnf install nginx` had no route, `set -e` aborted) → **nginx never installed**; and (b) was applied
  **without `TF_VAR_webapp_htpasswd_hash`**, so the `variables.tf` **placeholder** hash `$2y$10$REPLACEME…`
  got baked into `.htpasswd` → nginx `crypt_r() failed (22: Invalid argument)` → **HTTP 500** after entering
  the password. Also the boot script writes a 143-byte placeholder `Serac_Px_interface.html`, which showed as
  `Serac Px Interface â€" VPN Access Only` (em-dash mojibake, no `<meta charset>`) until the real HTML was
  synced over it. **Code changes to make future boots self-configuring (no on-box installs):** `ec2.tf`
  `user_data` now (i) **pulls the interface from S3 at boot** — `retry 30 10 aws s3 sync
  s3://<interface-bucket>/interfaces/ /var/www/webapp/` (|| true, placeholder only if the bucket is empty),
  (ii) fixes the placeholder charset (`<meta charset="utf-8">`, ASCII hyphen), (iii) sets `root:nginx` + 644/755
  perms; instance `depends_on` now includes `aws_iam_role_policy.s3_interface_read`. The retry loop (already in
  `ec2.tf`) handles the boot race. NB this `user_data` edit means the next `apply` **replaces the EC2** — which
  is desired (the replacement self-heals).
- 2026-07-16 — **CLEAN DEPLOY RUNBOOK (hands-off; no SSH, no on-box installs).** Prereqs one-time: bootstrap
  applied, `backend.hcl` created, interface bucket exists (`s3.tf` applied). Then, to (re)deploy the box so it
  comes up already serving:
  1. **Render + upload the interface to S3 FIRST** (so the box pulls it at boot):
     `conda run -n ML python tests/make_synthetic.py --out tmp` →
     `conda run -n ML python python/Px_interface.py --config tmp/config.yaml --output_dir tmp/out` →
     `BUCKET=$(terraform -chdir=aws-vpn output -raw interface_bucket)` →
     `aws s3 sync tmp/out/interfaces/ "s3://$BUCKET/interfaces/" --region eu-north-1 --exclude "*_2dtest.html"`.
  2. **Basic-Auth hash** — `ec2.tf` auto-reads `~/.serac_aws` (one line `serac_user:$2y$…`), so nothing to
     export. (If that file is ever missing you'll get the placeholder hash → `crypt_r` 500; recreate it with
     `htpasswd -nbB serac_user > ~/.serac_aws`, or fall back to `export TF_VAR_webapp_htpasswd_hash=…`.)
  3. `cd aws-vpn && terraform init -backend-config=backend.hcl` (if needed) then `terraform plan` / `terraform apply`.
  4. Wait ~2–3 min, then `bash aws-vpn/healthcheck.sh` (expect nginx active, 4/4 endpoints, tunnel UP).
  5. Browse `https://$(terraform -chdir=aws-vpn output -raw ec2_private_ip)/` over the VPN → Basic-Auth → the
     interface. No SSH, no manual `dnf`/`htpasswd`/sync — the box self-configured from `user_data` + S3.
  **Refresh the interface later** (new render, box unchanged): re-render → `aws s3 sync … s3://$BUCKET/interfaces/`
  → then either one SSM command `sudo aws s3 sync s3://$BUCKET/interfaces/ /var/www/webapp/` on the box, or just
  reboot/replace the instance (boot re-pulls). **Order rule:** upload to S3 *before* the apply so the boot pull
  finds it; the two must-be-present-or-it-breaks items are the **S3 upload** and **`~/.serac_aws`** (the hash file).
- 2026-07-16 — **Basic-Auth hash now read from a local file.** `ec2.tf` has a `webapp_htpasswd_hash` local:
  `sensitive(fileexists(pathexpand("~/.serac_aws")) ? trimspace(file(...)) : var.webapp_htpasswd_hash)` — reads
  `~/.serac_aws` (one line `serac_user:$2y$…`) so no `TF_VAR` export needed; falls back to the var if absent.
  `user_data` uses `local.webapp_htpasswd_hash`. (`~/.serac_aws` is `chmod 600`, workstation-only, never committed.)
- 2026-07-16 — **friendly internal DNS: `advantedge.seracbio.com` (Route 53 private zone + inbound resolver).**
  Chosen over a public `seracbio.com/...` path, which is impossible for a VPN-only private box (would require
  public exposure). New `aws-vpn/dns.tf`: a **private hosted zone scoped to `advantedge.seracbio.com`** (NOT all
  of seracbio.com — avoids shadowing the public domain) with an apex **A record → the EC2's fixed private IP**;
  a **Route 53 Resolver INBOUND endpoint** (~$90/mo) with IPs in two AZs (added a 2nd private subnet
  `172.20.3.0/24` in `eu-north-1b`); a resolver SG allowing 53 tcp/udp from `on_premises_cidr`. EC2 pinned to a
  **fixed private IP `172.20.2.10`** (`ec2_private_ip` var) so the record survives replacements. TLS cert
  (`tls.tf`) now has CN + SAN = `advantedge.seracbio.com` (no name-mismatch warning). nginx serves the interface
  under **`/Px_interface/`** (bare `/` 301-redirects there); the boot S3 pull + placeholder now target
  `/var/www/webapp/Px_interface/`. Outputs: `resolver_inbound_ips`, `webapp_url`
  (`https://advantedge.seracbio.com/Px_interface/`). **IT action required:** add a **conditional forwarder** on
  the FortiGate/on-prem DNS for `advantedge.seracbio.com` → the `resolver_inbound_ips` (forward ONLY that name,
  not all seracbio.com). `terraform validate` clean; not yet applied. NB this **replaces the EC2** (user_data +
  private_ip change) — follow the CLEAN DEPLOY RUNBOOK (S3 upload + `~/.serac_aws` first). Cost note: the ~$90/mo
  inbound resolver was the previously-flagged item in the cost table; now incurred. **Docs updated to match:**
  `aws-vpn/instructions.md` (Step 3 hash→`~/.serac_aws`; Step 4 no env var; Step 6 S3 upload + boot auto-pull to
  `/Px_interface/`; new Step 7 = friendly-DNS + conditional forwarder; Step 8 test via `advantedge.seracbio.com`;
  refreshed the ASCII overview to 172.20 CIDRs + resolver) and both mermaid diagrams (`.md` + redacted
  `.shared.md`) now show the Route 53 private zone + inbound resolver, the fixed-IP EC2 serving `/Px_interface/`,
  the interface S3 bucket boot-pull, and a DNS-resolve step in the runtime sequence.
- 2026-07-16 — **Boot race fixed for good: provisioning moved out of cloud-init into a systemd unit.**
  Symptom (recurred even WITH the inline `retry 30 10` loop): a fresh/replaced box came up with nginx not
  listening (browser `ERR_CONNECTION_REFUSED` on `https://172.20.2.10/Px_interface/`), yet
  `sudo bash /var/lib/cloud/instance/scripts/part-001` run manually ~2 min later succeeded — i.e. the box is
  fine, cloud-init just fires `user_data` at ~8 s uptime and the bounded retry (~5–6 min) can exhaust before the
  VPC endpoints / VPN-propagated routes are reachable, then `set -e` aborts and cloud-init never re-runs.
  **Durable fix (no more manual `part-001`):** `user_data` now does **zero network work** — it only writes an
  idempotent `/opt/provision-webapp.sh` + a `provision-webapp.service` systemd oneshot, then
  `systemctl start --no-block` (so cloud-init doesn't block on the network-gated pass). The unit is ordered
  `After=network-online.target` (runs once the NIC is actually up, not at 8 s) and the script retries the whole
  pass internally (60 × 15 s ≈ 15 min) until dnf/SSM/S3 answer; `Restart=on-failure` gives further passes. The
  bash now lives in **`aws-vpn/user_data.sh.tftpl`** (rendered via `templatefile()` in `ec2.tf`) — chosen over a
  triple-nested heredoc-in-heredoc for reliability. Two more changes rolled in: (i) the **Basic-Auth hash moved
  to SSM** — new `aws_ssm_parameter.htpasswd` (`/<project>/webapp/htpasswd`, SecureString), fetched at boot like
  the TLS cert/key, IAM read-grant + `depends_on` updated; keeps the secret out of user_data (IMDS-readable) and
  keeps the `templatefile` vars non-sensitive; (ii) the S3 sync is now `|| return 1` (retry on network error)
  instead of `|| true`, which fixes the old "placeholder shown even though S3 has the interface" bug (empty
  bucket still returns 0 → placeholder; only a *reachable* empty bucket falls through). `terraform validate`
  clean; rendered template passes `bash -n` (outer + inner). NB the `user_data` change **replaces the EC2** on
  the next apply — desired (the replacement self-provisions). Diagnostics on a box: `systemctl status
  provision-webapp`, `journalctl -u provision-webapp`.
- 2026-07-17 — **APPLIED + serving; boot fix confirmed. Only the on-prem DNS forwarder remains (with IT).**
  `terraform apply` replaced the EC2; `bash aws-vpn/healthcheck.sh` = **6 ok / 1 warn (status-checks still
  initialising) / 0 fail** — instance running, SSM Online, 4/4 endpoints, VPN 2/2 tunnels UP, **nginx active with
  no manual `part-001`** → the systemd self-provisioner works as intended. Interface confirmed serving over the
  VPN at `https://172.20.2.10/Px_interface/`. **DNS security review (asked + verified):** the whole DNS stack
  stays inside the VPC/VPN boundary — private hosted zone (never in public DNS; A record → RFC1918 IP), inbound
  resolver endpoint has private IPs only + SG-gated to `on_premises_cidr`, `private_b` subnet on the private RT
  which has **no 0.0.0.0/0 / IGW / NAT**. DNS only maps name→private IP; access still gated by EC2 SG + TLS +
  basic auth. **DNS handoff DONE (pending IT):** `terraform output resolver_inbound_ips` = **`172.20.2.155`,
  `172.20.3.107`** — the conditional-forwarder request (forward ONLY `advantedge.seracbio.com` → those two IPs,
  53 udp/tcp; NOT all of seracbio.com) has been **sent to IT**. Once IT adds it,
  `https://advantedge.seracbio.com/Px_interface/` resolves over the VPN (verify with
  `nslookup advantedge.seracbio.com` → `172.20.2.10`). Until then, use the IP URL. **This is the last open item
  on the AWS deployment.** **Done 2026-10-01:** IT added the forwarder, and the friendly URL loads over the VPN.
- 2026-07-22 — **removed the unused public subnet + Internet Gateway** (AWS contact asked why they were in the
  shared diagram). They carried no traffic: the EC2 lives in the private subnet, a S2S VPN needs no IGW, and all
  outbound goes via VPC endpoints (S3 gateway + SSM) — the public subnet/IGW were reserved-for-future scaffolding
  only. Deleted from `aws-vpn/vpc.tf` (`aws_subnet.public`, `aws_internet_gateway.main`, `aws_route_table.public`
  + its association), plus the now-orphaned `public_subnet_cidr` var (`variables.tf`, `terraform.tfvars`,
  `.tfvars.example`) and `public_subnet_id` output (`outputs.tf`). **Applied** (in the same apply as the AMI pin
  below). Docs synced: both mermaid diagrams (`.md` + `.shared.md`) drop the IGW/PUBSN nodes;
  `aws_architecture_learn.md` Subnets/IGW sections rewritten to "private subnets only, no IGW by design". Makes
  the "zero internet exposure" story self-evident with no reviewer footnote.
- 2026-07-22 — **AMI now PINNED — unpinned `most_recent` AL2023 broke SSM (root cause found & fixed).** After a
  `terraform apply` replaced the EC2, `SSM ping: None` (Session Manager unreachable, `healthcheck.sh` 5 ok/2 fail)
  **even though the webapp kept serving** over the VPN. Full control-plane diagnosis came back 100% green — 4/4
  endpoints available w/ private-DNS + correct SG + same subnet as the box, vpce-sg allows 443 from VPC, ec2-sg
  egress correct, IAM instance profile attached, clock fine (the boot-time Parameter-Store TLS fetch succeeded, so
  SigV4/IMDS/DNS all work). SSM stayed dead across **reboot AND stop/start** → not a boot race. **The one variable
  that changed:** `ec2.tf` `data.aws_ami` uses `most_recent = true` (unpinned), so the replace jumped from the
  07-17 known-good image to **`al2023-ami-2023.12.20260720.0-kernel-6.18`** (`ami-0ac1f955d6e62f3f1`) — a newer
  kernel + `amazon-ssm-agent` that fails to register in this endpoint-only VPC. nginx (separate service) was
  unaffected, which is why the site worked while SSM didn't. **Fix:** pinned `ec2_ami_id =
  "ami-068b5bc67e48209c1"` (`...20260710.0-kernel-6.18`, 07-10 known-good) in `terraform.tfvars` (code already
  honors `var.ec2_ami_id` when non-empty); `apply` → new box `i-086fbee9fb9a1d9c6` on the pinned AMI →
  `healthcheck.sh` **7 ok / 0 fail, SSM Online, nginx active**. Closes the deferred **L3 "pin AMI"** hardening
  item. **Lesson:** never leave AL2023 on `most_recent` for a box you can only manage via SSM — a bad agent build
  silently locks you out on the next replace. Bump the pin deliberately (test SSM comes up) rather than floating.
- 2026-08-18 — **manual source-data fix: force-significant on 2 rows.** Set `significant` 0 → 1 for two
  specific (uniquecontrast, genes) rows directly in the Dropbox `FBX_MEASURE` source CSVs (per user request):
  `STK4` / plate `Pw107` in `20260616/20260616_FBX_MEASURE.csv`, and `ATM` / plate `Pw109` in
  `20260616_02/20260616_FBX_MEASURE_02.csv`. A scan of all 13 `FBX_MEASURE` files confirmed each row exists in
  exactly one file (no dup in the `20260616`↔`20260616_02` re-export pair). Edit was surgical/line-level (only the
  matched line rewritten, every other line byte-for-byte; asserted exactly 1 replacement per file), verified
  `significant==1` post-edit. Pre-edit originals backed up (off-Dropbox) to
  `C:\Users\gtamo\Desktop\GT\interfaces\MS_data_backups\`. Change flows into the interface only on a
  from-source rebuild (`IFACE_OVERWRITE=true`); the `IFACE_DIR` checkpoint still holds the old `meas`.
- 2026-09-15 — **AWS ops cheat-sheet added to `README.md`** (new `## AWS — common commands` section, between
  Layout and Data policy): SSM shell + `send-command`/`get-command-invocation`, file transfer via the interface
  S3 bucket (SSM has **no native file transfer**; `aws s3 sync` laptop→bucket then a `send-command` sync
  bucket→`/var/www/webapp/Px_interface/`), `healthcheck.sh`, the `terraform init -backend-config=backend.hcl` /
  plan / apply / destroy cycle, and browsing via `webapp_url`. Every identifier is fetched live via
  `terraform -chdir=aws-vpn output` — no account/instance IDs or IPs committed (repo sanitization convention).
  Carries the three standing gotchas: apply-replaces-EC2 (upload to S3 first), keep `ec2_ami_id` pinned, and the
  two must-exist local files (`aws-vpn/backend.hcl`, `~/.serac_aws`). Verified live (read-only): `ec2_instance_id`
  → the pinned-AMI box, `interface_bucket`, `webapp_url` → `https://advantedge.seracbio.com/Px_interface/`.
- 2026-09-15 — **RDS phase started locally; `rds/` gitignored.** New untracked `rds/` scripts connect to the
  **production PostgreSQL RDS in `us-east-1`** (psycopg2, `sslmode=verify-full` against the Amazon CA bundle
  `global-bundle.pem`) — the first step of the long-planned RDS source mode for `DATA.load_new_df`. The scripts
  hold a **plaintext DB password**, so `rds/` was added to `.gitignore` (verified via `git check-ignore`;
  `git log --all -- rds/` is empty, so nothing ever reached history and **no rotation is needed**). Before this
  goes anywhere shared, move the password to an env var / AWS Secrets Manager. **Region mismatch to resolve:**
  the RDS is `us-east-1` while the whole Px stack (VPC, EC2, S3, VPN) is `eu-north-1` — the future Fargate/Batch
  rebuild job needs either cross-region networking or to run in `us-east-1` and write to S3 cross-region.
  **Correction (2026-09-23):** a live check found the company instances `seracbio-dev` and `seracbio-prod` in
  **`eu-north-1`**, in the default VPC (`172.31.0.0/16`): PostgreSQL 18.3, Multi-AZ, publicly accessible.
  The `rds/` scripts target two hosts, one in `eu-north-1` and one in `us-east-1`. Thus the region mismatch
  applies only to the `us-east-1` host.
  **Serving model reminder** (re-confirmed today): the EC2 installs only nginx + awscli — it is a pure static
  file server, and **only the rendered artifacts** (HTML, `_data.js`, volcano SVGs, thumbnails) ship via S3.
  The pipeline code and source data never go to the box; the render runs on the workstation.
- 2026-09-21 — **Signals webapp on AWS: handoff pack `aws_signals.zip`.** (The pack now lives at `~/Signals/aws/`; the copy in this repo is deleted.) A second
  internal service (FastAPI/uvicorn, currently on-prem at Ridgeline) is to be hosted with the same access model
  as the Px interface. **Decisions:** a **new EC2 in the EXISTING VPC** (not a second VPC/VPN — the tunnel,
  SSM/S3 endpoints, private zone and resolver are ~$150/mo of shared infra, and a second tunnel means another
  IT round), plus a **NAT gateway** because the app calls the Signals SaaS API. **Three NAT facts established:**
  (1) NAT is **outbound-only** — inbound stays VPN-only, so adding it exposes nothing; (2) a NAT gateway is
  **not a firewall** and cannot filter by destination; (3) **security groups have no deny rules**, so "internet
  except the office LAN" is inexpressible as an SG — it needs a **NACL** with explicit denies for
  `192.168.146.0/24` + `10.0.14.0/24`, which is what preserves the H1 anti-pivot hardening. Recommended egress
  stack = SG 443-only + that NACL + a squid domain allow-list (AWS Network Firewall, ~$395/mo, documented as the
  upgrade for true enforcement). Routing: put the app in its **own subnet/route table/NACL** so the Px box keeps
  zero internet egress; the VPN routes are more specific than `0.0.0.0/0` so office traffic stays on the VGW;
  a NAT gateway **re-introduces the public subnet + IGW deleted on 2026-07-22**. Cost delta ≈ **$47/mo**
  (≈$18 with a `t4g.nano` NAT instance). Pack = `reference/` (reusable .tf incl. bootstrap + healthcheck +
  .example), `rewrite/` (ec2.tf + user_data.sh.tftpl — static-site provisioning, must be rewritten for uvicorn;
  the reusable pattern is the **systemd-oneshot provisioner**, not cloud-init), `docs/`, and a 232-line
  `instructions.md`. Excluded + verified absent by an automated scan: real account ID, tfvars (holds the
  FortiGate public IP), backend.hcl, state (TLS key + VPN PSKs), `~/.serac_aws`, unredacted diagram.
  **Cert correction carried into the pack:** an internal A record does NOT make a public cert possible, but
  **DNS-01 does** (challenge is a public TXT; the host never needs to be internet-reachable) — and an **AD
  Certificate Services** cert is better still on domain-joined machines. My earlier "public cert impossible for
  a private IP" was true only of HTTP-01.
- 2026-09-21 — **Handoff pack finalised (484-line `instructions.md`) and delivered to `~/Signals/aws/`.**
  Added §7b **isolation checklist** — both boxes share the VPC, so the wall is configuration, not a free
  property. Verified starting position: the Px SG accepts 80/443/ICMP **only** from `192.168.146.0/24` +
  `10.0.14.0/24` (`allowed_cidr`), with **no VPC-internal ingress**, so a packet from a Signals subnet is
  dropped today. **The network is not the risk** — ranked: (1) an over-broad instance role (`ssm:*` →
  `SendCommand` onto the Px box, bypassing the network entirely; `AmazonSSMManagedInstanceCore` alone does
  NOT grant it), (2) write access to the Px S3 bucket = supply chain (the Px box syncs that bucket into its
  webroot at boot), (3) wildcard SSM paths leaking the Px **TLS private key + htpasswd** (the Px role reads
  exactly 3 ARNs; both roles share `alias/aws/ssm`, so ARN scoping is the control), (4) password reuse —
  makes isolation moot with no compromise at all. Plus: scope the SSM-endpoint egress by **SG reference**
  not `vpc_cidr` (which contains the Px box), use a **separate Terraform stack**, and re-test after deploy.
  **Also settled this session:** `/32` allow-list **outranks** Network Firewall here (an SG/NACL rule is
  unbypassable from the instance; an NF domain rule matches the **client-supplied, spoofable SNI**) — NF
  only wins when destinations cannot be enumerated. Network Firewall price corrected **~$395 → ~$290/mo**
  per endpoint. Signals resolves to **two stable `/32`s in AWS eu-central-1** (EC2 ranges) → PrivateLink is
  plausible (vendor already on AWS; ask before building) and IP allow-listing is viable — allow the `/32`s,
  **never** the `/14`/`/13` (AWS-wide Frankfurt). Adding the two Signals rules is **not** the whole job: the
  NAT path (public subnet + **re-added IGW** + EIP + NAT + app subnet/RT/NACL) is the structural work, and
  NACLs attach to **subnets, not instances**. All additive — no VPC/VPN destroy.
- 2026-09-21 — **Signals webapp audit + handover to the Signals repo.** (This entry uses STE.)
  **CLAUDE.md now requires ASD-STE100 Simplified Technical English** for replies, documentation, code
  comments, and commit messages. The section sits at line 34. It is byte-identical to the section in
  `/home/gtamo/Signals/CLAUDE.md`, and both start at line 34.
  **Audit of `~/Signals/webapp/app.py` (120 lines).** Three checks passed. The API key stays on the
  server. The app has no general proxy to Signals, so a smaller key scope gives a real benefit. The app
  cannot publish, because that endpoint needs a browser session.
  **One correction settled:** the server holds the compound data. The browser does almost no work.
  `/api/upload` writes the file to a temporary directory, converts it on the server, and puts the frame
  in the `STAGE` dictionary. `/api/create` then reads `STAGE` and writes to Signals. The path is browser,
  then server, then Signals. **The code never removes an entry from `STAGE`**, so compound data stays in
  memory until the process stops. So the data rule applies, and the Px rule of synthetic data until
  single sign-on applies here too. **This decision is still open.** (On 2026-10-01 the Px rule changed
  for the Px box only. That change does not decide the Signals question.)
  **One risk to close:** `/api/create` sends `str(error)` to the browser. An `ElnError` message must
  never contain the API key. Check `signals_eln.py`.
  **No IT work is necessary.** The tunnel already routes all of `172.20.0.0/16`. Put the new subnet in
  that range, for example `172.20.4.0/24`.
  **Handover:** the pack is at `~/Signals/aws/` (21 files + zip). The Claude agent in the Signals repo
  continues the work. The pack does not obey STE yet.
- 2026-09-23 — **Private PostgreSQL RDS in the VPN VPC: deployed and verified end to end.** (STE.)
  **Stack `aws-rds/`** (Signals pattern): a separate stack with the same state bucket and lock table, key
  `rds/terraform.tfstate`. It reads the VPC and VGW through data sources, so its `destroy` cannot touch
  `aws-vpn/`. 10 resources: subnets `172.20.6.0/24` (1a) and `172.20.7.0/24` (1b), because a DB subnet group
  needs two AZs; an own route table with VGW propagation (local + the two on-prem routes, no default route);
  security group `px-rds-sg` (5432 from `192.168.146.0/24` + `10.0.14.0/24`, zero egress rules); DB subnet
  group; parameter group `px-rds-pg18` (`rds.force_ssl=1`); instance `px-rds`. The instance is PostgreSQL 18.3
  (same as `seracbio-prod`), `db.t4g.micro`, 20 GB gp3, encrypted, Single-AZ, not public, deletion protection
  on, 7-day backups. Creation took 7.5 min. Cost: about $14.50/month (AWS Pricing API: $0.016/h, $0.12/GB-month).
  **STALE — `px-rds` was DELETED on 2026-09-29, and `python/px_rds.py` no longer exists. Read this
  entry for the patterns only, not for live facts.**
  **Secret:** RDS kept the `px_admin` password in Secrets Manager (`manage_master_user_password`). Verified:
  Terraform state holds only the secret ARN, no password.
  **CLI `python/px_rds.py`** (`ML` env): `check`, `pull TABLE [--out]`, `set-password`, and the admin commands
  `create-demo` and `create-user NAME`. **Explicit logins (decided 2026-09-23):** every command needs a login,
  given AFTER the command name (`pull px_demo --admin`). `--user NAME` (or `RDS_USER`) asks for that user's
  password; with `--host` (or `RDS_HOST`) too, it needs no AWS access. `--admin` reads the master secret from
  AWS with boto3. Without a login, a command stops with a message before any AWS call: there is NO fallback
  to the master (the user asked for this after the question "does a colleague get admin by default?"). The
  answer was already no, because the admin path needs AWS credentials that can read the secret, but the
  explicit flag also stops an accidental master login. `create-demo` and `create-user` refuse to run
  without `--admin`; `set-password` refuses `--admin`; `--user` with `--admin` is a parser error. In a
  notebook: `connect(cfg, admin=True)`. Every connection uses `sslmode=verify-full` and `global-bundle.pem`.
  Tests: `tests/test_px_rds.py` (7, no AWS); `main(argv)` makes the CLI testable in-process.
  **Removed 2026-10-01:** commit `853b993` deleted `python/px_rds.py`, so the user had its test file deleted too.
  **`pull` prints the rows** (user's edit, 2026-09-23). Thus the assistant must never run `pull` on real data.
  Pins added: `psycopg2-binary==2.9.12`, `boto3==1.43.94`. README has a new `PostgreSQL RDS (aws-rds/)` section.
  **Live results from WSL2 over the VPN** (WSL2 NAT mode, no `.wslconfig` change):
  - Public DNS resolves the RDS name to its private IP (`172.20.7.x`). No Route 53 forwarder is necessary.
  - TCP 5432 passes the FortiGate. No IT work was necessary.
  - TLS 1.3 with a verified certificate. A login with `sslmode=disable` fails (`no encryption`).
  - `create-demo` wrote 1000 synthetic rows (slim `meas` schema, fake `C_`/`G_`/`P_` ids) and read back
    identical values. `pull` wrote Parquet and CSV to `data/rds/` (gitignored) with the same dtypes.
  **Gotchas:**
  - The `.gitignore` pattern `rds/` also ignores `aws-vpn/rds/`. That is why the stack is in `aws-rds/`.
  - `rds.force_ssl` needs `apply_method = "pending-reboot"`. AWS reports that method (source `system`, type
    dynamic), and `immediate` gives a diff on every plan. After the fix, the plan shows no changes.
  - `create-demo` drops and makes again the table named by `RDS_DEMO_TABLE`. Never point it at a real table.
  - Tear down: `terraform apply -var deletion_protection=false`, then `terraform destroy`. The destroy keeps
    the snapshot `px-rds-final`.
  **Data rule:** synthetic data only, until the user decides on real data. **Superseded (2026-09-30):**
  `px-seracbio-prod` holds a copy of the real `seracbio-prod` data. Treat it as real data. The assistant never
  reads its rows.
  **Personal login (decided 2026-09-23):** RDS rotates the master password every 7 days (next: 2026-10-01),
  so a GUI client must not save it. `create-user NAME` makes a login role with read and write rights
  (`pg_read_all_data` + `pg_write_all_data` + `CREATE` on `public`); it cannot drop tables that `px_admin`
  owns. The user types the password at a hidden prompt and keeps it in 1Password (user's choice over a
  Secrets Manager secret). `encrypt_password` makes the SCRAM-SHA-256 hash on the client, so the plain
  password is in no SQL text and no log. A second run sets a new password. Verified live with a temporary
  role: read, insert and create table work; a drop of `px_demo` fails; after a reset the old password fails.
  To remove a role that owns no tables: `REVOKE CREATE ON SCHEMA public FROM <name>; DROP ROLE <name>`.
  **STALE — these facts described `px-rds`, now deleted. `px-seracbio-prod` uses a FIXED 1Password
  password since 2026-10-01, so no RDS-managed secret and no rotation apply to it.**
  **Master secret facts (read 2026-09-23):** Secrets Manager secret `rds!db-<id>` in `eu-north-1`, owned by
  RDS (`OwningService: rds`), encrypted with the AWS-managed key `aws/secretsmanager`. It holds JSON with
  `username` and `password`. RDS rotates it every 7 days; the next rotation happens by 2026-10-01 02:00 CEST.
  Open sessions survive a rotation, and the CLI reads the secret at each connection, so a rotation does not
  affect it. The control is IAM (`secretsmanager:GetSecretValue`), and CloudTrail logs each read. Emergency
  rotation: `aws secretsmanager rotate-secret --secret-id <arn>`.
  **More users:** `max_connections` is 79 on `db.t4g.micro` (3 reserved). The user made a login `seracbio` on
  2026-09-23. For a colleague (user's choice: share by repo clone, not by a single file): the admin runs
  `create-user <name> --admin` with a temporary password, and sends it with the endpoint name through 1Password (the
  endpoint stays out of git). The colleague sets `RDS_HOST` + `RDS_USER` (or passes `--host`/`--user`) and runs
  `set-password`. The colleague needs the VPN and the repo, but no AWS access. Verified live with a temporary
  role and no AWS credentials in the environment: `check`, `pull` and `set-password` work, and the old
  password fails after the change.
  **Not done:** the RDS source mode of `DATA.load_new_df`; a client inside the VPC (that needs an ingress
  rule on `px-rds-sg`). The Signals stack is still not applied, and it reserves
  `172.20.4.0/24` and `172.20.5.0/24`.
- 2026-09-23 — **Transcript audit: what infra information crossed to Anthropic.** (STE.) The user asked the
  realistic risk that someone at Anthropic gets into the VPN VPC. Method: a local scan of the 82 Claude Code
  transcripts on disk (projects Px_interface, Signals, Miscelaneous). The scan paired each command with its
  output and printed only yes/no flags, never values. **Result: no key crossed.** No VPN PSK value, no AWS
  access key (only the last 4 characters, from `aws configure list`), no DB password (the 2026-09-15 read of
  `rds/` masked both password lines), no real htpasswd hash or password (the `htpasswd` hits were `<password>`
  placeholders in docs), no private key. **What crossed is reconnaissance only:** the FortiGate public IP
  (a 2026-07-03 read of `aws-vpn/terraform.tfvars`), the AWS tunnel endpoint IPs, the public TLS certificate,
  the account ID, CIDRs, private IPs, resource IDs, the RDS DNS suffix of this account, user names, and the
  full SG/route design. **Assessment:** low risk. Every way in needs a secret that did not cross: FortiClient
  credentials or the office LAN, the PSK plus traffic from the FortiGate IP, or AWS IAM credentials. **Limits:**
  transcripts older than the local retention period, and the Kiro sessions that generated the stack, cannot be
  audited here. **Bigger risks than the transcripts:** the long-lived admin AWS keys in `~/.aws/credentials`
  (they give SSM, the master secret and the VPC without the VPN); `seracbio-prod`/`-dev` are publicly
  accessible and their DNS names follow from the suffix; the internet-facing FortiGate (patching, MFA).
- 2026-09-23 — **AWS credentials: current state and the migration plan (not done yet).** (STE.) The CLI, Terraform
  and `px_rds.py` use one long-lived access key of the IAM user `gtamo@seracbio.com` (`AdministratorAccess`
  attached directly; key created 2026-06-30). The console login has MFA, but the key does not need MFA. The
  account is standalone (no AWS Organizations) and has no IAM Identity Center instance. The account has 3 IAM
  users. The state bucket policy has only `DenyNonTLS` + `DenyOutsideAccount`, with no principal allow-list,
  so a change of credentials in the same account keeps state access. **Plan:** enable IAM Identity Center
  (organization instance; it makes an Organization with this account as the management account), always-on
  MFA, one user plus the `AdministratorAccess` permission set, then `aws configure sso` (profile
  `serac-admin`, region `eu-north-1`; in WSL `--use-device-code`) and `export AWS_PROFILE=serac-admin`. Then
  remove the key from `~/.aws/credentials`, test, set the key Inactive, and delete it after a week. Keep the
  IAM user without keys, with MFA, as a break-glass login. **Rule:** the assistant never runs
  `iam create-access-key`, because its output holds the new secret. Runbook: `docs/pwd_security.md` (gitignored, local only).
- 2026-09-25 — **Recipe: a plate or volcano does not appear in the interface.** (STE.) A user could not see
  plate `Pw73` for one compound and gene `BNIP3`. The data was correct. A **filter tickbox in the browser**
  hid the row. Check the client filters **before** you doubt the pipeline.
  **Order of checks:**
  1. **Plate date.** `SHOW_PLATE` ticks only the listed dates. Every other plate starts **unticked**.
     `Pw73` has the date `2026-04-29`, but `SHOW_PLATE` held three August dates. This alone hides a plate.
  2. **Target validation.** `FBXO31_INDEPENDENT_TICKED: false` starts the "FBXO31 independent" box
     unticked, so those genes are hidden on load. `VALIDATED_TARGET_FILE` (`data/validated.txt`, 16 genes)
     **replaces** the dependent list. A gene that is in neither list falls in "other", which has no box
     and always shows.
  3. **MS slider**, then the Activity boxes. The MS slider filters each experiment by its own
     per-(gene,compound,plate) score at `pl[8]`, not by the dot's z.
  **Two facts that speed up the next diagnosis:**
  - **Plates older than the first FBX tranche (`20260601`) are df_raw plates.** Their dates come from
    `_DFRAW_DATE_SRC` in `combine_datasets` (`2026-04-29` = `CLEAN_PROTEOMICS_PATH`, plus `2026-05-20`
    and `2026-05-29`), not from a tranche folder name. Do not look for an FBX file for them.
  - **`FREE_UPSTREAM: true` sets `output.report`, `output.measure`, `output.mscore` and `data.MS` to
    `None`.** Diagnose with `output.compounds_df`, `output.meas` and `output.plate2date`, which survive.
    A row that is present in `compounds_df` passed every server-side filter, so the cause is client-side.
  **Latent issue found while reading the code, NOT the cause here and NOT verified:**
  `combine_datasets` builds the activity fallback with
  `data.MS.sort_values('date').drop_duplicates('compound', keep='last')`, then `fillna`s it onto the
  df_raw report rows. So the **newest** run decides the activity for **all older runs of that compound**,
  and a later line drops every `activity == 'Silent'` row. A compound with more than one run can lose an
  old plate this way. Only df_raw rows use this fallback; FBX rows carry their own activity.
- 2026-09-30 — **RDS copy `px-seracbio-prod` is live, and the secret mechanism is documented.** (STE.)
  The stack `aws-rds/` restored a snapshot of `seracbio-prod` into the VPN VPC: `db.m7g.large`,
  600 GB gp3, Multi-AZ, **private**, force_ssl parameter group, user `seracbio`. Verified: PostgreSQL
  18.3, schema `public` with 10 tables, 14 GB of data in the `postgres` database. The placeholder
  `px-rds` is deleted; its final snapshot `px-rds-final` (20 GB) remains. **The copy improves the
  source in two ways:** the source sits in the **default VPC** and is **publicly accessible**; the copy
  is private and reachable over the VPN only. Raise the public source with the team.
  **Password — SUPERSEDED on 2026-10-01, see the entry below.** It was `manage_master_user_password
  = true` (RDS-owned secret `rds!db-<uuid>`, rotating weekly). It is now a fixed 1Password value.
  `rds/connect2.py` now reads the endpoint, the user and the password from AWS at run time and holds
  **no password**. Run it with `conda run -n ML python rds/connect2.py`; the base environment has no
  `boto3`. **The full mechanism (SigV4, the one-Allow-no-Deny rule, the `kms:ViaService` condition on
  the AWS managed key, and the two separate network needs) is in Part 7 of
  [`docs/aws_architecture_learn.md`](../docs/aws_architecture_learn.md).**
  **Finding to act on:** all three IAM users are IAM users, not SSO, and **two hold
  `AdministratorAccess`**. A colleague therefore needs no new policy, only AWS credentials and the VPN.
  The doc lists three ways to narrow this.

- 2026-10-01 — **Two private databases now run in the VPN VPC. Connection details recorded.** (STE.)
  **This supersedes the 2026-09-30 note that the prod password is RDS-managed.** The master password
  of `px-seracbio-prod` is now a **fixed value shared through 1Password**, not an RDS-managed secret.
  `manage_master_user_password` is gone from `restore.tf`; the provider refuses that flag beside
  `password`, so you omit it rather than set it to `false`. Terraform reads the value from
  `~/.px_db_password`, one line `user:password`, with the same `locals` pattern that `aws-vpn` uses for
  `~/.serac_aws`. **CAUTION: the password now sits in the Terraform state file, and it no longer
  rotates.** The user accepted this so a colleague without an AWS account can sign in. RDS deleted its
  own secret; `MasterUserSecret` reads `null`.

  | Field | `px-seracbio-prod` | `px-seracbio-dev` |
  |---|---|---|
  | Host | `px-seracbio-prod.cfyi0icu0fkt.eu-north-1.rds.amazonaws.com` | `px-seracbio-dev.cfyi0icu0fkt.eu-north-1.rds.amazonaws.com` |
  | Port | 5432 | 5432 |
  | Database | `postgres` | `postgres` |
  | User | `seracbio` | `seracbio` |
  | TLS | required, `sslmode=verify-full` + `global-bundle.pem` | the same |
  | Engine | PostgreSQL 18.3 | PostgreSQL 18.3 |
  | Region / zone | `eu-north-1` / `1b` | `eu-north-1` / `1b` |
  | Class | `db.m7g.large` | `db.t4g.micro` |
  | Storage | 600 GB gp3, Multi-AZ | 20 GB gp3, single-AZ |
  | Data | 14 GB, 10 tables in `public` | empty |
  | Password file | `~/.px_db_password` | `~/.px_db_dev_password` |
  | Test script | `rds/connect2.py` | `python/20261001_test_dev_connect.py` |

  **`px-seracbio-dev` is EMPTY and new** (`dev.tf`), not a restore. It reuses the prod subnet group,
  security group and `force_ssl` parameter group, so a later edit to any of those three changes **both**
  databases. `create_dev=false` removes it; `deletion_protection` is off, because dev is disposable.
  **Trap fixed the same day:** `restore_snapshot_identifier` defaulted to `""`, so a bare
  `terraform apply` planned to **destroy `px-seracbio-prod`**. The default is now pinned to
  `seracbio-prod-copy-20260929`, and a bare plan shows `0 to destroy`.
  **RDS password rule:** printable ASCII, 8-128 characters, but **never** `/ @ " or space`. The first
  1Password value held `@` and the apply failed with `InvalidParameterValue`. Nothing changed on AWS.
  **Both test scripts** read their credential file, accept `--prompt` to type the password instead, and
  prompt on their own when the file is absent. They make **no AWS call**, so a colleague needs only the
  VPN, `psycopg2`, `global-bundle.pem` and the password. CAUTION: `--prompt` fails under `conda run`
  (`EOFError`, stdin closed); use `conda activate ML` first. `python/` is tracked by Git, so never write
  a query result there: `uniquecontrast` holds compound identifiers.

- 2026-10-01 — **The friendly name is live. HTTPS needs a trusted certificate.** (STE.)
  IT added the conditional forwarder, so `https://advantedge.seracbio.com/Px_interface/` loads over the VPN.
  Chrome shows "Not secure" and a line through `https`. The cause is the certificate, not the protocol.
  nginx already serves TLS on 443 and sends 80 to 443. But `aws-vpn/tls.tf` makes a **self-signed**
  certificate, and no browser trusts its issuer. **Facts for the fix (checked 2026-10-01):**
  - The public DNS of `seracbio.com` is at **GoDaddy** (`ns43`/`ns44.domaincontrol.com`). This AWS account
    has only the private zone `advantedge.seracbio.com`. The ACM certificate list in `eu-north-1` is empty.
  - `seracbio.com` has **no CAA record**, so Amazon and Let's Encrypt can issue a certificate for it.
  - The box has no internet egress, so it cannot renew a certificate by itself.
  - Internal ALB price in `eu-north-1` (AWS Pricing API): $0.02394/h (about $17.50/month), plus $0.0076
    per LCU-hour. A non-exportable ACM public certificate is free.
  - A public certificate puts the name in the public Certificate Transparency logs. The name still
    resolves only over the VPN.
  - The live certificate (`openssl s_client`): self-signed, `O=My Company`, `CA:FALSE`, valid 2026-07-16 to
    **2028-10-18**. `CA:FALSE` means it cannot sign other certificates, so a laptop that trusts it trusts
    only this one site.

  **Recipe: trust the certificate on a Windows laptop (no IT, $0, no AWS change).** The user did these
  steps on 2026-10-01, and they worked. Each user does them one time, in Chrome or Edge:
  1. In Chrome, open the site, click "Not secure", then click "Certificate is not valid".
  2. On the Details tab, click Export. Save the file as `advantedge.crt`.
  3. Double-click the file, then click "Install Certificate".
  4. Select "Current User".
  5. Select "Place all certificates in the following store", then select "Trusted Root Certification
     Authorities".
  6. Click Finish, then click Yes at the security warning.
  7. Close all Chrome windows, then open Chrome again.

  The file holds no secret, so a user can send it to colleagues with these steps. Each new certificate
  needs a new import. A change to `tls.tf` makes a new certificate, and so does the renewal before the
  expiry on 2028-10-18. To remove the trust: run `certmgr.msc`, open Trusted Root Certification
  Authorities → Certificates, and delete `advantedge.seracbio.com`.

  **Open decision:** an ACM certificate on an internal ALB (one permanent CNAME at GoDaddy, automatic
  renewal), Let's Encrypt with DNS-01 (free, one TXT at GoDaddy for each renewal), or a certificate from an
  IT internal CA. The recipe above works, so this decision is not urgent. Enable HSTS (item M3) only after
  a trusted certificate is live.

- 2026-10-01 — **Decision: real Px interface files can go on the AWS box.** (STE.) The user decided this,
  because the Basic-Auth password is now shared through 1Password. **Controls:** the box is reachable only
  over the VPN (no public IP), nginx asks for the Basic-Auth password, and TLS encrypts the traffic.
  **Accepted limit:** all users share one password, so the logs cannot show which person opened the
  interface. M365 SSO stays the upgrade. S3 versioning is on, so the bucket keeps every old version of each
  file. **Scope:** the Px interface only (the interface S3 bucket and the EC2). **No change to the assistant
  rules:** the assistant never reads real data, and it tests only with synthetic builds. `CLAUDE.md` still
  says that project data never leaves this machine; that text is not changed. This entry supersedes the
  "synthetic only" rule in the 2026-07-01 privacy bullet, in the 2026-07-16 Step 6 entry, and in the README.

- 2026-10-01 — **Fixed: the full test suite crashed with a segmentation fault.** (STE.)
  **Symptom:** `python -m unittest discover -s tests` stopped in
  `TestStemSharedYmax.test_volcano_base_svg_honors_override`, inside `ElementTree.fromstring`, which
  `_volcano_base_svg` calls. The test passed when it ran alone.
  **Cause:** the RDKit 2025.9.3 pip wheel has `rdkit.libs/libRDKitChemDraw`, which holds a private copy of
  expat and exports 66 `XML_*` functions. If RDKit Draw (through its fontconfig) loads the conda `libexpat`
  first, the loader binds some internal calls of `libexpat` to the RDKit copy (`LD_DEBUG=files` shows a
  "relocation dependency"). A later parse by `pyexpat` then runs mixed code from two expat versions and
  crashes. In the suite, `TestRender` draws thumbnails with RDKit before the first ElementTree parse in the
  same process. No real build showed this crash. A probable reason: the volcano renders run in worker
  processes.
  **Fix:** `functions.py` imports `xml.etree.ElementTree` at the top (the local import in
  `_volcano_base_svg` is gone), so `pyexpat` loads expat first, with correct bindings.
  **Rule:** in each process, import `python.functions` (or `xml.etree.ElementTree`) before
  `rdkit.Chem.Draw`. `from rdkit import Chem` alone is safe, because it does not load the Draw libraries.
  The three notebooks obey this rule (cell 1: `rdkit.Chem`, then `python.functions`).
  **Regression test:** `TestExpatImportOrder` runs the crash sequence in a child process (old code: exit 139;
  new code: exit 0). The suite: 57 tests OK.

- 2026-10-01 — **One-command publish to AWS: `--output_dir <PUBLISH_URL>`.** (STE.)
  `python python/Px_interface.py --config config/config.yaml --output_dir "https://advantedge.seracbio.com/Px_interface/"`
  builds the interface and pushes it to the box. **Flow** (`Px_interface.py`, next to `resolve_n_jobs`):
  1. `resolve_output_dir`: a local path builds there, as before. The config `PUBLISH_URL` (last slash
     optional) builds in `PUBLISH_STAGE_DIR`. Another URL stops the CLI.
  2. `aws_publish_targets`, **before the build**: bucket = `<PUBLISH_PROJECT>-interface-<account>` (account
     from STS), then `head_bucket`; EC2 = the one running instance with tag `Name=<project>-instance`; its
     SSM ping must be Online. Thus git holds no bucket name and no instance ID.
  3. `publish_interface`, after the build: `aws s3 sync <stage>/interfaces/ s3://<bucket>/<PUBLISH_S3_PREFIX>
     --only-show-errors`, with `PUBLISH_EXCLUDE` (`*_2dtest.html`, `volcanoes_px/*.json`). Then one SSM
     `AWS-RunShellScript`: `set -e`, a sync to `PUBLISH_WEBROOT` without `*.html`, a second sync (the HTML
     last), then `chown -R root:nginx` and modes 644/755, as the boot script does. It polls
     `get_command_invocation` until a final status.
  The CLI prints counts only, because file names hold compound IDs. It needs AWS credentials, not the VPN.
  **Config:** 7 `PUBLISH_*` keys at the end of `config/config.yaml`. **Tests:** `TestResolveOutputDir`,
  `TestPublish` (mocked boto3 and subprocess, no network). Suite: 62 tests OK.
  **Live test (synthetic only):** a scratch config with prefix `test_publish/interfaces/` and box folder
  `/var/tmp/px_publish_test/`, so the live site did not change (the live HTML in S3 kept 2026-07-16).
  First run: 18 s in total; 402 files (17 MB) in S3 after 6 s; the EC2 copy took 5 s and found 402 files.
  S3 held none of the excluded files. Second run: the S3 step took 1 s, an unchanged SVG kept its S3 date,
  and the HTML got a new one. So the sync sends only changed files.
  **Also on 2026-10-01:** CLAUDE.md has the user's exception for this publish. The README has a "Publish the
  interface" section and a new RDS section (prod + dev, 1Password passwords, the two test scripts).
  **Facts found:** the `RDS_*` config keys had no reader (only the deleted `px_rds.py` read them), so the user
  had them deleted on 2026-10-01. nginx on the box sent no `Cache-Control` and no `Expires` (fixed the same day: `no-cache`), so browsers
  guess how long a saved copy stays valid (about 10% of the file age); after a publish, a browser can show
  old files until a hard refresh. Both
  `python/20261001_test_*_connect.py` scripts have the RDS endpoint names in their code, so these names are
  in git. `20261001_test_prod_connect.py` prints the first 10 rows, so the assistant never runs it.

- 2026-10-01 — **First real publish: the box is correct, but the build had no volcanoes or thumbnails.** (STE.)
  The user emptied S3 `interfaces/` and the box folder, then ran the real publish. Checked by counts and md5:
  S3 and the box hold the same 3 files as the local build (HTML, `_data.js`, `plotly.min.js`; md5 equal).
  The real build has 11,328 genes, 5,348 compounds, 252 plates and 15 dates (2026-04-29 has 65 plates), the
  same counts as the Dropbox build of 2026-08-18 (`DROPBOX_ML/interfaces/`).
  **Symptom 1 — synthetic plates in the browser** (2026-05-20 with only `Pw02`): the browser used its saved
  copy of the July synthetic `_data.js` (nginx sends no `Cache-Control`). A hard refresh (Ctrl+Shift+R) fixes it.
  **Symptom 2 — no volcanoes and no thumbnails:** with `IFACE_OVERWRITE: false`, `build_interface` loads the
  cached compound panels (`IFACE_DIR/panels.json`), and that path skips the volcano render and the PNG copy.
  `PUBLISH_STAGE_DIR` (`output/`) was empty, so `output/interfaces/` got no `volcanoes_px/` and no
  `srb_png/`. **Rule: a build with `IFACE_OVERWRITE: false` needs a stage folder that already holds these
  files.** The page refers to 6,249 volcano SVGs (all present in the Dropbox `volcanoes_px/`, which has
  6,454) and finds thumbnails as `srb_png/<compound>.png` (all 5,348 present in the Dropbox `srb_png/` and
  in `SRB_PNG_DIR`). `__VOLCANO_BASE__` and `__THUMB_DIR__` are relative (`volcanoes_px`, `srb_png`).
  **Next step chosen by the user:** a full rebuild with `IFACE_OVERWRITE: true` (also `CHEMLIB_OVERWRITE`,
  `UPDATE_PNGS`, `DFRAW_OVERWRITE`), with no delete in S3 or on the box.

- 2026-10-01 — **The CLI now uses `UPDATE_PNGS` and `DFRAW_OVERWRITE`.** (STE.) Before, only the notebook
  called `download_cdd_pngs`, and no code in this repo acted on `DFRAW_OVERWRITE` (the notebook only printed
  it). **Changes:** MAIN calls `data.download_cdd_pngs(params)` after `load_chemical_lib_df`, as notebook cell 3
  does. New `DATA.build_old_df`, called by `load_old_df` when `DFRAW_OVERWRITE` is true: the MS_ML notebook
  recipe (`MS_cytotox` cell 6) as code. It reads 3 old proteomics exports with `fn.load_proteomics_data`
  (2026-04-29: RAW + CLEAN; 2026-05-20 and 2026-05-29: `_DB` + `_CDDVAULT` with `mode='cddvault'`; the
  default `drop_plates` and `collections` equal the values in the notebook), concatenates them, builds MS
  `[compound, ndown, origin, activity, date]`, and writes `DFRAW_PATH` + `MS_PATH`. **Why this recipe:** the
  current files (2026-06-30) have no `date` column in `df_raw` (24,604,663 rows) and MS has 2,277 rows =
  1,617 + 288 + 372, so `MS_cytotox` made them, not `MS_TargetML` (the newer cell also merges FBX, keeps the
  latest batch per compound, and uses the deleted `load_fbx_tranche`). The CLI loads FBX in `load_new_df`.
  Left out: the `data/MS/Px_genes.csv` side file (no reader here). **Checks:** on synthetic frames the port
  gives the same MS and df_raw as the notebook code (`DataFrame.equals`, after a parquet round trip).
  Tests `TestBuildOldDf` (2, mocked exports). Suite: 64 tests OK. All 6 source paths, the CDD token and the
  downloader module exist. **Expected on real data,** if the sources did not change: `> rebuilt df_raw
  (24604663, 11)` and `MS (2277, 5)`. The README lists the four flags and has a CAUTION to back up the two
  files first.

- 2026-10-01 — **Full rebuild + publish: 11,552 files (452 MB); S3 took 150 s, the EC2 copy 76 s.** (STE.)
  **Finding 1 — the box kept the old HTML.** By default, `aws s3 sync` from S3 to a disk skips a file of the
  same size unless the disk copy is newer (the AWS CLI documents this under `--exact-timestamps`). Each new
  HTML has the same size, because only plotly's random div ID changes. So the box got the new `_data.js`
  (new size) but kept the old HTML. **Fix:** `publish_interface` adds `--exact-timestamps` to both EC2
  copies (test assertion in `TestPublish`); the manual README command has it too. A second run of the box
  step then gave the same md5 on the box as locally for the HTML, `_data.js` and `plotly.min.js`.
  **Finding 2 — 239 plates on 14 dates, not 252 on 15.** The `20260817` validation tranche (MEASURE +
  REPORT; 13 plates `Pw###VM{WT,KO,BIND}`) was not in `FBX_DIR`. The user had moved it to
  `AdvantEidge Platform/tmp/20260817` "because it was causing issues", and will move it back. Without it,
  `Pw105VMKO`/`Pw105VMWT` take their 2026-06-01 date again, and the compounds go from 5,348 to 5,346. The
  dates before 2026-06-01 have the same plates as before, so the `df_raw` rebuild kept them. **After the
  move back:** rebuild with `IFACE_OVERWRITE: true`, because the `IFACE_DIR` checkpoint has no 20260817
  data now. Expect 252 plates on 15 dates. The cause of the "issues" is not recorded yet.
  **Finding 3 — the browser cache.** F5 kept the July synthetic `_data.js`. The browser saved it about 77
  days after its last change, so its guessed lifetime is about a week. Ctrl+Shift+R fixes it.

- 2026-10-01 — **Volcanoes do not render on the box: `X-Frame-Options: DENY` blocks them.** (STE.) After a hard
  refresh, the genes and compounds show, but the volcanoes stay blank. **Not a sync problem:** the box has all
  6,254 SVGs (the page needs 6,249, and none are missing) and 5,348 PNGs, and nginx serves SVG as
  `image/svg+xml`. **Cause:** the page shows each volcano in `<object class="vobj" type="image/svg+xml">`
  (`functions.py`), and browsers apply `X-Frame-Options` to `<object>`, `<embed>` and `<iframe>`. nginx sends
  `add_header X-Frame-Options DENY always;` on every response (`/etc/nginx/conf.d/webapp.conf`, from
  `aws-vpn/user_data.sh.tftpl` line 62), so the browser refuses each volcano, also from the same site.
  Thumbnails (`<img>`) and `_data.js` (`<script>`) do not use this rule, so they work. Local tests did not show
  it, because a local server sends no such header. **Fix:** `SAMEORIGIN` instead of `DENY` (other sites still cannot
  frame the interface). **Status 2026-10-01:** the user applied it on the live box in an SSM session (`sed`,
  `nginx -t`, reload); the live 401 response shows `X-Frame-Options: SAMEORIGIN`. The template now has
  `SAMEORIGIN` and also `add_header Cache-Control "no-cache" always;` (revalidate each file, 304 if unchanged),
  with `terraform validate` OK. On the live box, the `Cache-Control` line waits for the user: the permission
  system blocks the assistant from remote shell writes, so the user runs that step. The next `terraform apply`
  replaces the EC2, because `user_data` changed; the new box gets both headers and pulls the build from S3.
  **Resolved 2026-10-01:** the user also added the `Cache-Control` line on the box (backup
  `webapp.conf.bak.20261001`), and after a reload the volcanoes show. The live 401 response has all three
  headers. The nginx access log confirms it: volcano requests 27 × 200, 8 × 206 and 18 × 304 (304 = the
  browser revalidates under `no-cache`, and nginx answers "not changed"). The only error is a missing
  `/favicon.ico`, which does no harm.

- 2026-10-02 — **How to change the interface login (Basic-Auth user name and password).** (STE.)
  **Path:** `~/.serac_aws` (one line `user:$2y$…`) → `ec2.tf` local `webapp_htpasswd_hash` (`trimspace(file(...))`)
  → SSM parameter `/<project>/webapp/htpasswd` → the box copies it to `/etc/nginx/.htpasswd` **at boot only**.
  **Steps:** back up the file; `htpasswd -nB <user> > ~/.serac_aws && chmod 600 ~/.serac_aws` (it asks for the
  password, so the password does not go into the shell history; README and `aws-vpn/instructions.md` now use
  `-nB`, not `-nbB '<password>'`); `terraform plan` + `apply`; update 1Password. **State on 2026-10-02:**
  `terraform plan` (read-only) shows only `aws_instance.main must be replaced` (the header change in
  `user_data.sh.tftpl`; `user_data_replace_on_change = true`), so the next apply also gives the new login at
  once. The fixed IP (`172.20.2.10`) means destroy-then-create, so the site is down for a few minutes.
  **CAUTION (applied only to the box before 2026-10-02 05:43 UTC) — do not restart `provision-webapp` on a box
  whose script is older than the template.** The replacement of 2026-10-02 runs the current template, so a
  restart is safe now; with `user_data_replace_on_change = true`, each applied template change gives a new box. To change only the password without a
  replacement, copy the SSM parameter into `/etc/nginx/.htpasswd` by hand. Found: `~/.serac_aws` had mode 644,
  not 600.

- 2026-10-02 — **Real build restored, and the S3 exposure assessed.** (STE.) **Restored:** the user moved
  `20260817` back and published again (build 2026-10-01 23:07): 252 plates on 15 dates (2026-08-17: 15),
  5,348 compounds. S3 `interfaces/` = local build exactly: 11,605 files (3 + 6,254 SVG + 5,348 PNG).
  **S3 facts (`aws-vpn/s3.tf`):** public access blocked, TLS only, `DenyOutsideAccount`, SSE-S3, versioning
  on, **no lifecycle rule and no network condition** (the code comment still says the design was for synthetic
  data). S3 is not in the VPC and not behind the VPN: only the EC2 traffic uses the S3 gateway endpoint, and the
  publish uploads over the internet. So any principal of this account with S3 read rights can download the
  data from any network, with no VPN and no Basic-Auth (2 IAM users have `AdministratorAccess` with long-lived
  keys). Old versions: 1,055 (269 MB), which include the `_data.js` of the first real publish; delete markers:
  1,039. PNGs: 5,348 (89 MB), the only structures. `_data.js` has no SMILES or InChI, but it has compound IDs
  (821,066 `SRB-` strings) and per-compound values. **To delete the PNGs after the copy gives little:** the site
  keeps working, but a new EC2 starts with no thumbnails, each publish sends all PNGs again, versioning keeps
  hidden copies, and `_data.js` + SVGs stay. **Proposed (not applied):** a lifecycle rule for old versions; a
  bucket policy that permits object reads only through the S3 gateway endpoint; the move to SSO + MFA.

- 2026-10-02 — **Volcanoes blank again after the EC2 replacement: the browser cache, not the box.** (STE.) The
  user applied the login change with a full `terraform apply`, so a new EC2 started at 05:43 UTC. That box is
  correct: `provision-webapp` succeeded on attempt 1; 6,254 SVG + 5,348 PNG; nginx has `SAMEORIGIN` + `no-cache`
  from the template; the new login works. Its access log shows volcano requests 1 × 200, 2 × 206, 24 × 304, so
  the browser reused its saved copies. **Cause:** Chromium does not update `X-Frame-Options` on a saved copy
  from a 304 answer (`kNonUpdatedHeaders` in `net/http/http_response_headers.cc` lists `x-frame-options`). So a
  volcano that the browser saved while nginx sent `DENY` (2026-10-01) keeps `DENY`. **Fix (client):** delete
  the browser's "Cached images and files"; an Incognito window confirms it. Only users who opened volcanoes
  while `DENY` was live see this problem.
  **S3 hardening (user approved items 1 + 2; APPLIED by the user, checked 2026-10-05: laptop `head-object` → Forbidden, laptop list works, box `head-object` works, lifecycle `expire-old-versions` Enabled, 7 days; `terraform plan` = no changes):** `s3.tf` bucket policy statement
  `DenyObjectReadOutsideVpc` (deny `s3:GetObject`/`GetObjectVersion` unless `aws:SourceVpce` = the S3 gateway
  endpoint), and `aws_s3_bucket_lifecycle_configuration.interface` (old versions expire after
  `interface_noncurrent_days` = 7, a new variable; expired delete markers removed; incomplete multipart uploads
  aborted after 1 day). `terraform plan`: 1 to add, 1 to change, 0 to destroy (no EC2 change). After the apply,
  laptop reads of bucket files fail with `AccessDenied` (README updated); the publish still works, because it only
  lists and writes. The plan warning "dynamodb_table is deprecated" comes from `backend.hcl`, not from this change.

- 2026-10-05 — **Signals agent handoff: SSM and password hardening for Px, claims checked.** (STE.) Source:
  `../Signals/wiki/wiki.md`, section "HANDOFF — to the Claude agent of `../Px_interface/`" (2026-10-05).
  **Checked in this repo and in AWS (read-only):**
  1. **Confirmed, the main risk:** `vpn-project-ec2-role` has `AmazonSSMManagedInstanceCore`, which allows
     `ssm:GetParameter` on `*`. `iam simulate-principal-policy`: `GetParameter` is **allowed** on
     `/signals-webapp/api-key`, `/signals-webapp/htpasswd` and any other parameter, through that managed policy.
     So the Px box can read every SSM parameter of the account. (The user deleted `/signals-webapp/api-key`
     on 2026-10-05 for this reason.) Fix: an explicit Deny with `NotResource` = the 3 Px parameters +
     `/aws/service/*`, in `aws_iam_role_policy.ssm_tls`; no EC2 replacement.
  2. Confirmed: `ec2.tf` takes `trimspace(file("~/.serac_aws"))` with no format check; the fallback default is
     the `REPLACEME` placeholder. Fix: a `precondition` with a bcrypt-line regex (plan time only).
  3. Confirmed: `htpasswd -B` uses cost 05 by default (dummy test). Traffic on the box: busiest second 5
     requests, busiest 10 s 11 requests (467 log lines), 2 vCPU, so cost 10 is affordable. A long random
     password matters more.
  4. Confirmed: `fetch_param` writes `webapp.key` and `.htpasswd` with the default mode before `chmod`. Fix:
     `install -m 0600 /dev/null "$2"` first. Changes `user_data`, so it replaces the EC2.
  5. Confirmed: no `limit_req`. Optional; size it well above 5 requests per second per user.
  6. Confirmed: `kms:Decrypt` names `alias/aws/ssm`, which KMS never matches. Fix: the key ARN.
  **Batch A (user approved 2026-10-05): steps 1, 2, 6 — code done, apply by the user.** `ec2.tf`: Deny statement
  in `aws_iam_role_policy.ssm_tls` (`NotResource` = the 3 Px parameter ARNs + `arn:aws:ssm:<region>::parameter/aws/
  service/*`; actions GetParameter(s), GetParametersByPath, GetParameterHistory, DescribeParameters);
  `kms:Decrypt` now on `data.aws_kms_alias.ssm.target_key_arn`; a `precondition` on `aws_ssm_parameter.htpasswd`
  (each line must match `^[^:\s]+:\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$`). **Checks:** offline test with a
  scratch `terraform_data` config: 1 line, 2 lines, cost 05/10 and trailing blank lines pass; a plain password,
  the `REPLACEME` placeholder and an empty file are refused, and a refused plan prints no input value.
  `terraform plan`: 0 to add, 1 to change (the role policy), 0 to destroy; the check passes on the real file.
  IAM simulation with the new Deny added (`PolicyInputList`), before the apply: the 3 Px parameters and
  `/aws/service/*` allowed; `/signals-webapp/api-key`, any other parameter and `DescribeParameters` explicitDeny;
  `ssm:UpdateInstanceInformation`, `ssmmessages:CreateControlChannel`, `ec2messages:GetMessages` allowed.
  **After the apply:** run the same simulation without `PolicyInputList`; then tell the Signals agent (user),
  so it can make `/signals-webapp/api-key` again and close its cross-stack note.
  **Batch B — TO DO, not started (user decision 2026-10-05: "at some point"):** steps 3, 4, 5 (+ optional 6), in
  one apply at a quiet time, because steps 4 and 5 change `user_data` and so REPLACE the EC2 (fixed IP, a few
  minutes down; the new box pulls the build from S3). (3) the user makes a new login at cost 10 with a long
  random password before the apply: `(umask 077; htpasswd -nBC 10 <user> | head -1 > ~/.serac_aws)`; then
  switch the README commands from `-nB` to `-nBC 10`. (4) `fetch_param` creates its file at 0600 first
  (`install -m 0600 /dev/null "$2" || return 1`). (5) optional `limit_req` (size it well above the measured peak
  of 5 requests per second, or real page loads get HTTP 503). (6) optional one htpasswd line per person (the
  precondition accepts several lines). Check after: `healthcheck.sh`, the page and the volcanoes.
