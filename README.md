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

## Data policy

Chemistry data (SMILES, compound IDs, structures, screening results) **stays on this
machine** — see [CLAUDE.md](CLAUDE.md). `data/`, `output/`, and rendered `interfaces/`
are gitignored.
