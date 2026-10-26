import os, sys
# self-locate repo root (parent of this file's dir) so `import python.functions` and
# relative paths (config/, output/) resolve no matter where the script is launched from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)
sys.path.insert(0, os.path.expanduser('~/CDD_Vault_API/python'))  # CDD Vault API (get_df)

import re, gc, ctypes, json, time, importlib, argparse, fnmatch, subprocess, functools
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from datetime import date
from rdkit import Chem
import yaml
import joblib
import boto3
from tqdm import tqdm
from types import SimpleNamespace
tqdm.pandas()

# local modules
import python.functions as fn
from get_library import get_df   # CDD Vault collection export


def _fbx_csv(files, kind):
    """Path to the one *_<kind>*.csv among a tranche folder's `files` (DATA.TRANCHE_CSVS: one listing per
    folder), or None if the tranche has no file of that kind. The FBX_ prefix is optional: the validation
    exports in VALIDATION_DIR omit it; tolerates a _02 re-export suffix. A validation-only tranche ships
    MEASURE + REPORT but no MSSCORE (its MS scores were already computed in an earlier tranche)."""
    return next((f for f in files if fnmatch.fnmatch(os.path.basename(f), f'*_{kind}*.csv')), None)


_PLATE_UC_RE = re.compile(r'_complement_(.+)$')

def _plate_from_uc(uc):
    """Reconstruct a plate name from a validation uniquecontrast whose export omitted the `plate`
    column: 'SRB…_vs_SRB…_complement_Pw144VM_BIND' -> 'Pw144VMBIND' (stem+condition, matching the
    Pw###VM{WT,MLN,KO,BIND} convention). None when the pattern is absent."""
    m = _PLATE_UC_RE.search(str(uc))
    return m.group(1).replace('_', '') if m else None

def _ensure_plate(df):
    """Guarantee a usable `plate` column: keep the existing one and fill any gaps (or build it whole
    when the column is absent) from `uniquecontrast`. Validation-only tranches ship no `plate` column
    in MEASURE/REPORT — the plate is embedded in the contrast instead. Modifies + returns df."""
    if 'uniquecontrast' not in df.columns:
        return df
    if 'plate' not in df.columns:
        df['plate'] = df['uniquecontrast'].map(_plate_from_uc)
    elif df['plate'].isna().any():
        _m = df['plate'].isna()
        df.loc[_m, 'plate'] = df.loc[_m, 'uniquecontrast'].map(_plate_from_uc)
    return df

def _with_plate(df, kind):
    """_ensure_plate, then drop the rows of a contrast with no plate in the file or in its name (e.g.
    ..._vs_DMSO_DG37_A01): such a contrast cannot show in the plate-filtered interface. Notes the drop."""
    df = _ensure_plate(df)
    _no = df['plate'].isna()
    if _no.any():
        print(f'> note: FBX_{kind}: dropped {int(_no.sum()):,} rows of '
              f'{df.loc[_no, "uniquecontrast"].nunique()} contrast(s) with no plate in the file or the name')
    return df[~_no].reset_index(drop=True)

def volcano_colour(meas, report, measure, thr=0.05):
    """
    The flag that colours the volcano points (user decision 2026-10-23). On an experiment whose REPORT names
    targets (`target`, ';'-separated), only a target with adjpval < thr colours (any logfc), as in the reference
    validation volcano; every other experiment keeps `significant`. `significant` (the panel hits) does not change.
    param df meas: uniquecontrast, genes, significant
    param df report: uniquecontrast, target
    param df measure: adjpval, on the index of meas
    param float thr: adjpval limit of a target (config VOLCANO_TARGET_ADJP)
    return Series: int8 flag on the index of meas
    """
    flag = meas['significant'].fillna(0).astype('int8')
    tg = (report[['uniquecontrast', 'target']].dropna().drop_duplicates('uniquecontrast')
          .assign(gene=lambda d: d['target'].astype(str).str.split(';')).explode('gene'))
    vm = meas['uniquecontrast'].isin(set(tg['uniquecontrast'])).to_numpy()
    hit = pd.MultiIndex.from_arrays([meas.loc[vm, 'uniquecontrast'].astype(str), meas.loc[vm, 'genes'].astype(str)]
                                    ).isin(list(zip(tg['uniquecontrast'].astype(str), tg['gene'].str.strip())))
    flag.loc[vm] = (hit & (measure.loc[meas.index[vm], 'adjpval'].to_numpy(dtype=float) < thr)).astype('int8')
    print(f'> volcano colour: targets only on {tg["uniquecontrast"].nunique():,} experiments with report targets')
    return flag

def validation_target_rows(report, compounds_df, meas):
    """
    Panel rows for the genes that validation experiments test. A validation REPORT names them in
    `target` (';'-separated, e.g. 'STAT2;PDCD2L'). Each (target gene, contrast) that compounds_df does
    not hold yet, and that the contrast measured, becomes a row at the gene's mean logfc there. Thus the
    gene's panel shows every tested compound, also a negative result. The key is the contrast, so each
    batch of a compound (e.g. -001 and -002) gives its own rows.
    param df report: per-contrast REPORT (uniquecontrast, compound, plate, target)
    param df compounds_df: panel rows so far (gene, uniquecontrast)
    param df meas: volcano source (genes, uniquecontrast, logfc)
    return df: uniquecontrast, compound, plate, gene, logfc (one row per new (gene, contrast))
    """
    t = (report.loc[report['target'].notna(), ['uniquecontrast', 'compound', 'plate', 'target']].astype(str)
         .assign(gene=lambda d: d['target'].str.split(';')).explode('gene'))
    t['gene'] = t['gene'].str.strip()
    have = set(zip(compounds_df['gene'].astype(str), compounds_df['uniquecontrast'].astype(str)))
    t = t[t['gene'].ne('') & pd.Series([(g, u) not in have for g, u in zip(t['gene'], t['uniquecontrast'])], index=t.index)]
    t = t.drop_duplicates(['gene', 'uniquecontrast'])
    m = meas.loc[meas['uniquecontrast'].isin(set(t['uniquecontrast'])) & meas['genes'].isin(set(t['gene'])),
                 ['genes', 'uniquecontrast', 'logfc']].dropna(subset=['logfc'])
    lf = m.groupby(['genes', 'uniquecontrast'])['logfc'].mean()
    t['logfc'] = [lf.get((g, u)) for g, u in zip(t['gene'], t['uniquecontrast'])]
    return t.dropna(subset=['logfc']).drop(columns='target').reset_index(drop=True)   # unmeasured gene -> no row

def gene_ms_score(mscore, val_ms):
    """
    The MS score of each gene dot (user decision 2026-10-26), on the raw scale: the validation score of a
    validation target (val_ms), else the highest FBX score, else the highest legacy (df_raw, April-May)
    score of a significant-down row, else 0. An FBX score wins also where the legacy score is higher.
    A row with no gene is dropped (pandas reads the symbol 'NA' as missing): a NaN gene breaks the page JSON.
    param df mscore: combined MS-SCORE rows (genes, ms_score, logfc, source 'FBX' | 'df_raw')
    param Series val_ms: target gene -> validation msscore (None or empty: no validation score)
    return df: genes, ms_score, ms_source ('validation' | 'FBX' | 'April-May' | '' when no score)
    """
    val_ms = val_ms if val_ms is not None else pd.Series(dtype=float)
    m = mscore.loc[mscore['genes'].notna(), ['genes', 'source', 'logfc', 'ms_score']].astype({'genes': str, 'source': str})
    fbx = m[m['source'] == 'FBX'].groupby('genes')['ms_score'].max()
    leg = m[(m['source'] == 'df_raw') & (m['logfc'] < 0)].groupby('genes')['ms_score'].max()
    s = (pd.concat([val_ms.rename('validation'), fbx.rename('FBX'), leg.rename('April-May')], axis=1)
         .reindex(pd.Index(m['genes'].unique()).union(val_ms.index.astype(str))))
    return pd.DataFrame({'genes': s.index, 'ms_score': s.bfill(axis=1).iloc[:, 0].fillna(0.0).to_numpy(),
                         'ms_source': s.notna().idxmax(axis=1).where(s.notna().any(axis=1), '').to_numpy()})

def validation_experiment_scores(compounds_df, report, val_ms, val_plates):
    """
    The validation score of a validation target's own validation experiments, for the MS slider (user decision
    2026-10-26): a panel row whose gene the REPORT names as a target of its contrast, on a validation plate,
    that is a hit or a tested-target row (not a stem completion). A primary reference plate keeps its own score.
    param df compounds_df: panel rows (gene, uniquecontrast, plate, is_completion, is_target)
    param df report: per-contrast REPORT (uniquecontrast, target ';'-separated)
    param Series val_ms: target gene -> validation msscore (None: no validation score)
    param list val_plates: the validation plates (VALIDATION_PLATE_SUFFIXES)
    return Series: the validation score on those rows, NaN on the others (index of compounds_df)
    """
    val_ms = val_ms if val_ms is not None else pd.Series(dtype=float)
    tg = report[['uniquecontrast', 'target']].dropna().astype(str)
    pairs = {(g.strip(), u) for u, t in zip(tg['uniquecontrast'], tg['target']) for g in t.split(';')}
    own = (pd.Series([(g, u) in pairs for g, u in zip(compounds_df['gene'].astype(str), compounds_df['uniquecontrast'].astype(str))],
                     index=compounds_df.index)
           & compounds_df['plate'].isin(val_plates) & (~compounds_df['is_completion'] | compounds_df['is_target']))
    return compounds_df['gene'].astype(str).map(val_ms).where(own)

def gene_association(ot_df, val_assoc, ot_assoc):
    """
    The association score (y) of each gene. With the RDS opentarget table (ot_assoc; config ASSOC_SOURCE
    'opentarget', user decision 2026-10-26): a validation target's px_ms_score_glues value (val_assoc) first, else
    the opentarget value. The FBX MSSCORE exports hold the same opentarget values (checked 2026-10-26). Without
    ot_assoc: the highest OT_CACHE overall_score of the gene (the y before 2026-10-26).
    param df ot_df: OT_CACHE (target_symbol, overall_score)
    param Series val_assoc: target gene -> validation association score (None: none)
    param Series ot_assoc: gene -> RDS opentarget association score (None: use OT_CACHE)
    return Series: gene -> association_score
    """
    if ot_assoc is None:
        return ot_df.groupby('target_symbol')['overall_score'].max().rename('association_score')
    val_assoc = val_assoc if val_assoc is not None else pd.Series(dtype=float)
    return val_assoc.combine_first(ot_assoc).rename('association_score')

# OpenTargets therapeutic areas in display/priority order; a gene's disease_area is its
# highest-ranked area here. Single source of truth: get_iface ranks by it, build_interface's
# DISEASE_AREA_COLORS must cover it (asserted there).
PRIORITY_DISEASE_AREAS = [
    'cancer or benign tumor', 'hematologic disease', 'cardiovascular disease',
    'immune system disease', 'musculoskeletal or connective tissue disease',
    'nervous system disease', 'psychiatric disorder',
    'nutritional or metabolic disease', 'endocrine system disease',
]

# ~~~~~~~~~~~~~~~~~~~~~~
# CLASSES
# ~~~~~~~~~~~~~~~~~~~~~~

def resolve_n_jobs(njobs):
    """Config NJOBS -> concrete worker count. <=0/None -> auto (all CPUs but 2); positive -> as-is."""
    n = int(njobs or 0)
    return max(1, (os.cpu_count() or 8) - 2) if n <= 0 else n


def resolve_plate_defaults(plate2date, show_plate=None, validation_suffixes=()):
    """
    Which plates to default-tick in the interface's Plates filter. show_plate (config SHOW_PLATE /
    --show_plate) names blocks of that filter: 'YYYYMMDD' ticks the plates of that date block, and
    'validation YYYYMMDD' ticks the stems of that validation block. A validation plate (its name ends
    in a validation suffix) is in the block 'validation <earliest date of its stem>', as the filter
    shows it, so a date entry never ticks it. Empty/None -> the single latest date block only
    (previous default). YYYYMMDD is normalised to the YYYY-MM-DD form plate2date stores, so it
    matches regardless of the source (FBX folder name or df_raw export).
    param dict plate2date: {plate -> 'YYYY-MM-DD'}
    param list show_plate: 'YYYYMMDD' / 'validation YYYYMMDD' strings or YYYYMMDD ints (None/[] -> latest)
    param list validation_suffixes: validation plate-name suffixes (config VALIDATION_PLATE_SUFFIXES)
    return tuple: (sorted plate names to tick, sorted block labels selected, e.g. 'validation 2026-10-06')
    """
    show = [re.fullmatch(r'(validation\s+)?(.+)', str(e).strip(), re.I).groups()
            for e in (show_plate or []) if str(e).strip()]
    blocks = ({('validation ' if v else '') + pd.to_datetime(d).strftime('%Y-%m-%d') for v, d in show}
              if show else {max(plate2date.values())})
    vre = re.compile('(' + '|'.join(map(str, validation_suffixes)) + ')$', re.I) if validation_suffixes else None
    stem = pd.Series({p: vre.sub('', p) for p in plate2date if vre and vre.search(p)}, dtype=object)
    first = pd.Series(plate2date, dtype=object)[stem.index].groupby(stem).transform('min')   # stem -> earliest date
    block = {**plate2date, **('validation ' + first).to_dict()}
    return sorted(p for p, b in block.items() if b in blocks), sorted(blocks)


def resolve_output_dir(output_dir, params):
    """
    CLI --output_dir -> (local build dir, publish flag). A local path is the build dir, as before.
    The URL in config PUBLISH_URL builds in PUBLISH_STAGE_DIR, and then the CLI publishes to AWS.
    param str output_dir: CLI --output_dir (a local path or a URL)
    param class params: PARAMS instance (PUBLISH_URL, PUBLISH_STAGE_DIR)
    return tuple: (local build dir, True if the CLI must publish)
    """
    if not re.match(r'https?://', output_dir):
        return output_dir, False
    if output_dir.rstrip('/') != str(getattr(params, 'PUBLISH_URL', '')).rstrip('/'):
        sys.exit(f'--output_dir {output_dir} is a URL, but it is not PUBLISH_URL in the config')
    return params.PUBLISH_STAGE_DIR, True


def aws_publish_targets(params):
    """
    Find the AWS targets of a publish before the long build, and stop if one is missing. The bucket
    name holds the account ID and the EC2 ID changes at each replacement, so git holds neither.
    param class params: PARAMS instance (PUBLISH_PROJECT, PUBLISH_REGION)
    return tuple: (boto3 session, bucket name, EC2 instance ID)
    """
    session = boto3.Session(region_name=params.PUBLISH_REGION)
    bucket = f"{params.PUBLISH_PROJECT}-interface-{session.client('sts').get_caller_identity()['Account']}"
    session.client('s3').head_bucket(Bucket=bucket)
    res = session.client('ec2').describe_instances(Filters=[
        {'Name': 'tag:Name', 'Values': [f'{params.PUBLISH_PROJECT}-instance']},
        {'Name': 'instance-state-name', 'Values': ['running']}])
    ids = [i['InstanceId'] for r in res['Reservations'] for i in r['Instances']]
    if len(ids) != 1:
        sys.exit(f'publish: found {len(ids)} running {params.PUBLISH_PROJECT}-instance, expected 1')
    info = session.client('ssm').describe_instance_information(
        Filters=[{'Key': 'InstanceIds', 'Values': ids}])['InstanceInformationList']
    if not info or info[0]['PingStatus'] != 'Online':
        sys.exit(f'publish: the SSM agent of {ids[0]} is not Online')
    print(f'> publish target: s3://{bucket}/{params.PUBLISH_S3_PREFIX} -> EC2 {ids[0]} (SSM Online)')
    return session, bucket, ids[0]


def ssm_run(session, iid, commands):
    """
    Run shell lines on the EC2 with SSM and wait for the final status (Success, Failed, Cancelled or TimedOut).
    param session: boto3 session (from aws_publish_targets)
    param str iid: EC2 instance ID
    param list commands: shell lines
    return dict: the SSM command invocation (Status, StandardOutputContent, StandardErrorContent)
    """
    ssm = session.client('ssm')
    cid = ssm.send_command(InstanceIds=[iid], DocumentName='AWS-RunShellScript',
                           Parameters={'commands': commands})['Command']['CommandId']
    while True:
        time.sleep(5)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv['Status'] not in ('Pending', 'InProgress', 'Delayed'):
            return inv


def publish_interface(local_dir, params, targets):
    """
    Push <local_dir>/interfaces/ to S3, then make the EC2 copy it into the nginx folder. The EC2
    copies the HTML last, so a page never loads before its data. Prints counts only, because the
    file names hold compound IDs.
    param str local_dir: build dir (PUBLISH_STAGE_DIR) that holds interfaces/
    param class params: PARAMS instance (PUBLISH_* keys)
    param tuple targets: (boto3 session, bucket name, EC2 instance ID) from aws_publish_targets
    return None:
    """
    session, bucket, iid = targets
    src, s3_uri, web = os.path.join(local_dir, 'interfaces'), f's3://{bucket}/{params.PUBLISH_S3_PREFIX}', params.PUBLISH_WEBROOT
    excl = list(getattr(params, 'PUBLISH_EXCLUDE', []))
    files = [os.path.relpath(os.path.join(d, f), src) for d, _, fs in os.walk(src) for f in fs]
    files = [f for f in files if not any(fnmatch.fnmatch(f, p) for p in excl)]
    t0 = time.time()
    cmd = ['aws', 's3', 'sync', src, s3_uri, '--region', params.PUBLISH_REGION, '--only-show-errors']
    if subprocess.run(cmd + [a for p in excl for a in ('--exclude', p)]).returncode:
        sys.exit('publish: aws s3 sync to S3 failed (see the errors above)')
    print(f'> publish: {len(files)} files ({sum(os.path.getsize(os.path.join(src, f)) for f in files) / 1e6:.0f} MB) '
          f'in S3 after {time.time() - t0:.0f}s')
    # --exact-timestamps: else an S3->disk sync skips a same-size file (each new HTML) unless the disk copy is newer
    sync = f'aws s3 sync {s3_uri} {web} --region {params.PUBLISH_REGION} --only-show-errors --exact-timestamps'
    t0 = time.time()
    inv = ssm_run(session, iid, [
        'set -e', f"{sync} --exclude '*.html'", sync, f'chown -R root:nginx {web}',
        f'find {web} -type f -exec chmod 644 {{}} +', f'find {web} -type d -exec chmod 755 {{}} +',
        f'echo files=$(find {web} -type f | wc -l)'])
    if inv['Status'] != 'Success':
        sys.exit(f"publish: the EC2 sync ended {inv['Status']}: {inv['StandardErrorContent'][-500:]}")
    print(f"> publish: EC2 synced after {time.time() - t0:.0f}s ({inv['StandardOutputContent'].strip()}) -> {params.PUBLISH_URL}")


class PARAMS():
    def __init__(self, config_path):
        self.config_path = config_path
    def load_params(self):
        # read the YAML and expose every key as an attribute (e.g. params.DFRAW_PATH), then bind the Dropbox readers
        with open(self.config_path) as f:
            self.__dict__.update(yaml.safe_load(f))
        return self.setup_dropbox()

    def setup_dropbox(self):
        """
        -Bind the Dropbox readers that DROPBOX_BACKEND selects (no key -> 'local'). 'rclone' (the cluster has
         no Dropbox client) streams a path under DROPBOX_LOCAL_ROOT from the rclone remote DROPBOX_REMOTE, as
         MS_ML does; a path outside that root stays local. 'local' reads every path from the disk (a laptop
         with the Dropbox client, the test fixture). params.dbx opens a path for pandas, params.dbx_glob lists
         a folder, params.dbx_push writes a DataFrame or bytes (see fn.dbx_open / dbx_glob / dbx_push).
        return self:
        """
        _rclone = getattr(self, 'DROPBOX_BACKEND', 'local') == 'rclone'
        _kw = dict(remote=getattr(self, 'DROPBOX_REMOTE', 'dropbox:'), prefix=self.DROPBOX_LOCAL_ROOT if _rclone else None)
        self.dbx, self.dbx_glob, self.dbx_push = (functools.partial(f, **_kw) for f in (fn.dbx_open, fn.dbx_glob, fn.dbx_push))
        print(f'> Dropbox backend: {"rclone" if _rclone else "local"}')
        return self

class DATA():
    def __init__(self):
        self.df_raw = None
        self.MS = None
        self.df_ms = None
        self.serac_df = None
        self.control_compounds = None
        self.contaminants = None
        self.gene_research = None
        self.VAL_MSSCORE = None
        self.VAL_ASSOC = None
        self.OT_ASSOC = None

    def load_chemical_lib_df(self, params):
        """
        -Load the compound library (name + smiles + Px annotations). With CHEMLIB_OVERWRITE,
         pull the latest straight from CDD Vault (collections AJ/AK) and cache to CHEMLIB_PATH;
         otherwise read the cached csv. Coerce the yes/no annotation columns to 1/0/NaN.
        param class params: the params class
        return None:
        """
        if params.CHEMLIB_OVERWRITE:
            self.serac_df = (get_df(vault=7108, collections=['AK', 'AJ'],
                                    columns=['name', 'smiles', 'Px_repetition(yes/no)',
                                             'Px_validated_WT(yes/no)', 'Px_Ligase_dependent(yes/no)',
                                             'Px_NameLigase_dependent', 'Px_Target_info', 'Px_Target_interest'])
                             .rename(columns={'name': 'compound'}))
            self.serac_df.to_csv(params.CHEMLIB_PATH, sep=',', index=False)
        else:
            self.serac_df = pd.read_csv(params.CHEMLIB_PATH)

        self.serac_df = self.serac_df.drop_duplicates()
        for _c in ['Px_validated_WT(yes/no)', 'Px_Ligase_dependent(yes/no)', 'Px_repetition(yes/no)']:   # 'yes'/'no'/'' -> 1/0/NaN
            self.serac_df[_c] = self.serac_df[_c].astype('string').str.strip().str.lower().map({'yes': 1, 'no': 0})
        print(f'> Chemical lib dim: {self.serac_df.shape}')

    def download_cdd_pngs(self, params):
        """
        Refresh the compound structure PNGs in SRB_PNG_DIR from CDD Vault, reusing the
        CDD Vault API downloader. No-op unless UPDATE_PNGS is true. Resume-safe: it skips
        files already on disk, so a normal run only fetches newly-added compounds; a full
        re-fetch happens only against an empty dir. Filenames are the full compound name
        (SRB-XXXXXXX.png) — the exact key build_interface looks up in SRB_PNG_DIR.
        PNGs stay local; only the API token leaves, to app.collaborativedrug.com.
        param class params: PARAMS instance (UPDATE_PNGS, CDD_VAULT, CDD_SEARCH, CDD_TOKEN_FILE, SRB_PNG_DIR)
        return None:
        """
        if not getattr(params, 'UPDATE_PNGS', False):
            return
        from pathlib import Path
        import download_cdd_structures as cdd   # already on sys.path (see top-of-file insert)
        with open(os.path.expanduser(params.CDD_TOKEN_FILE)) as _tf:
            s = cdd.make_session(_tf.read().strip())
        mols = cdd.list_molecules_in_search(s, params.CDD_VAULT, str(params.CDD_SEARCH))
        print(f'> CDD: {len(mols):,} molecules in search {params.CDD_SEARCH} -> {params.SRB_PNG_DIR}', flush=True)
        n_ok, n_skip, n_err = cdd.download_all(
            s, params.CDD_VAULT, mols, Path(os.path.expanduser(params.SRB_PNG_DIR)),
            size=600, workers=12, delay=0.0, prefix='SRB-', strip_prefix=False)
        print(f'> CDD PNGs: {n_ok:,} new, {n_skip:,} already present, {n_err:,} errors')

    def build_old_df(self, params):
        """
        Rebuild df_raw and MS from the three old proteomics exports (2026-04-29, 2026-05-20, 2026-05-29),
        then write them to DFRAW_PATH and MS_PATH. This is the MS_ML notebook cell (MS_cytotox) as code.
        The FBX tranches are not in these files: load_new_df loads them, and combine_datasets merges them.
        param class params: PARAMS instance (RAW/CLEAN_PROTEOMICS_PATH, PX_20260520/29_DB + _CDDVAULT, DFRAW_PATH,
                            MS_PATH, dbx: the PX_* exports are on Dropbox)
        return None:
        """
        parts = {'20260429': fn.load_proteomics_data(params.dbx(params.RAW_PROTEOMICS_PATH), params.dbx(params.CLEAN_PROTEOMICS_PATH)),
                 '20260520': fn.load_proteomics_data(params.dbx(params.PX_20260520_DB), params.dbx(params.PX_20260520_CDDVAULT), mode='cddvault'),
                 '20260529': fn.load_proteomics_data(params.dbx(params.PX_20260529_DB), params.dbx(params.PX_20260529_CDDVAULT), mode='cddvault')}
        # the 20260529 Vault export has short column names, and only its batch ID holds the molecule name
        ms29 = parts['20260529'][1].rename(columns={'Nr. Down': 'MSData - Proteomics activities: Nr. Down',
                                                    'Cmpd Activity': 'MSData - Proteomics activities: Cmpd Activity'})
        ids = ms29['Molecule-Batch ID'].str.split('-', n=2, expand=True)
        ms29['Molecule Name'] = ids[0] + '-' + ids[1]
        parts['20260529'] = (parts['20260529'][0], ms29)
        cols = {'Molecule Name': 'compound', 'MSData - Proteomics activities: Nr. Down': 'ndown', 'origin': 'origin',
                'MSData - Proteomics activities: Cmpd Activity': 'activity'}
        MS = pd.concat([m.assign(origin=f'MS{k}') for k, (_, m) in parts.items()], ignore_index=True)[list(cols)].rename(columns=cols)
        MS['date'] = pd.to_datetime(MS['origin'].str.replace('MS', ''))
        df_raw = pd.concat([d for d, _ in parts.values()], ignore_index=True)
        MS.to_parquet(params.MS_PATH, index=False)
        df_raw.to_parquet(params.DFRAW_PATH, index=False)
        print(f'> rebuilt df_raw {df_raw.shape} -> DFRAW_PATH | MS {MS.shape} -> MS_PATH')

    def load_old_df(self, params):
        """
        -Extract df_raw which contains the logfc and p-value associated with each gene and compound
        -Extract MS which contain compound level info e.g. activity -> single...
        -ms_score = -log10(p) * -logfc, not clipped (2026-10-26). Stops if a significant-down row has p = 0
         (a file written before the exact parse): rebuild it with DFRAW_OVERWRITE
        -With DFRAW_OVERWRITE, first rebuild both files from the proteomics exports (build_old_df)
        param class params: the params class
        return None:
        """
        if getattr(params, 'DFRAW_OVERWRITE', False):
            self.build_old_df(params)
        # load df-raw and process
        self.df_raw = pd.read_parquet(params.DFRAW_PATH)
        self.df_raw = self.df_raw.dropna()
        n_p0 = int(((self.df_raw['significant'] == 1) & (self.df_raw['logfc'] < 0) & (self.df_raw['pvalue'] == 0)).sum())
        if n_p0:
            raise ValueError(f'df_raw has {n_p0:,} significant-down rows with p = 0 (an infinite MS score): '
                             f'rebuild it from the exports with DFRAW_OVERWRITE: true')
        self.df_raw['-log10(p-value)'] = -np.log10(self.df_raw['pvalue'])
        self.df_raw['ms_score'] = -self.df_raw['-log10(p-value)'] * self.df_raw['logfc']
        self.df_raw = self.df_raw.sort_values('ms_score',ascending=False)
        self.df_ms = self.df_raw[self.df_raw['significant']==1].groupby(['genes','MSPlate']).first().reset_index()

        # load MS data
        self.MS = pd.read_parquet(params.MS_PATH)
        print(f'> df_raw {self.df_raw.shape} | MS {self.MS.shape}')

    def load_new_df(self, params):
        """
        -Load the FBX tables (FBX_MEASURE / FBX_MSSCORE / FBX_REPORT) from the source that config PX_SOURCE
         selects: 'rds' -> load_rds_fbx (the Px database + PX_ARCHIVE_DIR); 'dropbox' (also when the key is
         absent) -> load_dropbox_fbx (the tranche folders).
        -Load the per-gene SAR R2 table and build the uniquecontrast -> compound map.
        -With config ASSOC_SOURCE 'opentarget', read the RDS opentarget table into OT_ASSOC (gene -> association
         score, newest insert) for the y-axis (gene_association); else OT_ASSOC stays None (OT_CACHE).
        param class params: the params class
        return None:
        """
        if getattr(params, 'PX_SOURCE', 'dropbox') == 'rds':
            self.load_rds_fbx(params)
        else:
            self.load_dropbox_fbx(params)
        self.target2R2_df = pd.read_csv(params.GENE_SAR_OUT).rename(columns={'gene': 'genes'})
        if getattr(params, 'ASSOC_SOURCE', 'ot_cache') == 'opentarget':
            ot = fn.rds_table('opentarget', host=params.RDS_HOST, database=params.RDS_DATABASE,
                              cred_file=params.RDS_CRED_FILE, ca_bundle=params.RDS_CA_BUNDLE)
            self.OT_ASSOC = (ot.sort_values('id').drop_duplicates('approvedsymbol', keep='last')
                             .set_index('approvedsymbol')['associationscore'])
            print(f'> association: RDS opentarget, {len(self.OT_ASSOC):,} genes (+ px_ms_score_glues for the validation targets)')

        # uniquecontrast -> compound (SRB-XXXXXXX, batch stripped); reused by every combine
        _p = self.FBX_REPORT['srbnumber'].astype(str).str.split('-', n=2, expand=True)
        self.uc2compound = (self.FBX_REPORT.assign(compound=_p[0] + '-' + _p[1])
                            .drop_duplicates('uniquecontrast').set_index('uniquecontrast')['compound'])

        print(f'> FBX: MEASURE {len(self.FBX_MEASURE):,} rows | MSSCORE {len(self.FBX_MSSCORE):,} | '
              f'REPORT {len(self.FBX_REPORT):,} ({self.FBX_REPORT["uniquecontrast"].nunique():,} experiments) | '
              f'validation MS score: {len(self.VAL_MSSCORE):,} targets')

    def load_dropbox_fbx(self, params):
        """
        -Load + concat every FBX tranche under params.FBX_DIR, plus every validation tranche under the
         optional params.VALIDATION_DIR, into the unified FBX tables (FBX_MEASURE / FBX_MSSCORE /
         FBX_REPORT). Tranche folders are auto-discovered: any date-named subdir holding a *_REPORT csv
         (FBX_ prefix optional), so a new tranche just needs its folder. A contrast that several
         tranches hold keeps only the newest tranche's rows (a later export re-analyses it).
        -VAL_MSSCORE / VAL_ASSOC: the per-target MSSCORE of the validation tranches (target, msscore,
         associationscore), one per target.
        -Each folder is listed one time (params.dbx_glob: rclone on the cluster, ~1 s per folder) into
         TRANCHE_CSVS {tranche: its csv paths}; params.dbx reads the files.
        param class params: the params class
        return None:
        """
        def _tranches(root):   # {date-named subdir holding a REPORT csv: its csv paths}
            csvs = {t: params.dbx_glob(t, '*.csv') for t in params.dbx_glob(root, '[0-9]*/')
                    if os.path.basename(t)[:8].isdigit()}
            return {t: f for t, f in csvs.items() if _fbx_csv(f, 'REPORT')}
        _vdir = getattr(params, 'VALIDATION_DIR', None)
        _val = _tranches(_vdir) if _vdir else {}
        self.TRANCHE_CSVS = {**_tranches(params.FBX_DIR), **_val}
        self.VAL_TRANCHES = list(_val)
        # oldest -> newest by folder date; on a date tie the validation folder counts as the newer one
        self.FBX_TRANCHES = sorted(self.TRANCHE_CSVS, key=lambda t: (os.path.basename(t), t in _val))
        _name = lambda t: os.path.basename(t) + (' (validation)' if t in _val else '')
        val_ms = []   # per-target validation MS scores (target, msscore), newest tranche first

        def _load_fbx(kind):
            # newest tranche first, so a contrast in several tranches keeps only its newest rows. A tranche
            # may lack a kind (validation-only: no MSSCORE) or hold a per-target MSSCORE with no contrast
            # to join on -> skipped (it goes to VAL_MSSCORE), and noted so a genuinely missing file isn't silently swallowed
            frames, seen, skipped, n_old = [], set(), [], 0
            for t in reversed(self.FBX_TRANCHES):
                p = _fbx_csv(self.TRANCHE_CSVS[t], kind)
                # exact parse: the p-values are plain decimals, and the default parser reads p < ~1e-17 as 0
                df = pd.read_csv(params.dbx(p), float_precision='round_trip') if p else None
                if df is not None and {'target', 'msscore'} <= set(df.columns):
                    val_ms.append(df)
                if df is None or 'uniquecontrast' not in df.columns:
                    skipped.append(_name(t)); continue
                old = df['uniquecontrast'].isin(seen)
                n_old += int(old.sum())
                seen.update(df['uniquecontrast'].unique())
                frames.append(df[~old])
            if skipped:
                print(f'> note: no FBX_{kind} with contrasts in {len(skipped)} tranche(s), skipped: {", ".join(skipped[::-1])}')
            if n_old:
                print(f'> FBX_{kind}: dropped {n_old:,} rows whose contrast a newer tranche also holds')
            return pd.concat(frames[::-1], ignore_index=True)

        # validation-only tranches omit `plate`: _with_plate rebuilds it from the uniquecontrast
        self.FBX_MEASURE  = _with_plate(_load_fbx('MEASURE'), 'MEASURE')
        self.FBX_MSSCORE  = _load_fbx('MSSCORE')
        self.FBX_REPORT   = _with_plate(_load_fbx('REPORT'), 'REPORT')
        # the validation MS and association scores: one per target, from the newest validation export that holds it
        v = (pd.concat(val_ms).drop_duplicates('target').set_index('target') if val_ms else pd.DataFrame())
        v = v.reindex(columns=['msscore', 'associationscore']).astype(float)
        self.VAL_MSSCORE, self.VAL_ASSOC = v['msscore'], v['associationscore']
        print(f'> Dropbox: {len(self.FBX_TRANCHES) - len(self.VAL_TRANCHES)} tranches + {len(self.VAL_TRANCHES)} validation')

    def load_rds_fbx(self, params):
        """
        -Read the FBX tables from the Px database (config RDS_*): px_fbx_measure + px_fbx_glues (the validation
         exports: the plate sits in the contrast name) and px_fbx_report + px_fbx_glues_report. A contrast that
         was uploaded two times keeps its newest insert (highest id). On a contrast in both a main and a glues
         table, the glues copy wins: a validation export counts as the newer one, as in the tranche folders.
        -Keep the FBX experiments that PX_DATES dates (the database has no date yet), then add the PX_ARCHIVE_DIR
         rows: the Dropbox rows of the experiments that the database lacks. On a contrast in both, the database wins.
        -FBX_MSSCORE = fn.ms_score_rows on the main measure table (the formula of the platform's MSSCORE exports)
         + the archived MSSCORE rows of the other experiments. The formula does not score a glues contrast; it keeps
         the score of an older FBX export (if any), as in the last build.
        -VAL_MSSCORE / VAL_ASSOC = px_ms_score_glues: the validation MS and association scores, one per target (newest insert).
        param class params: PARAMS instance (RDS_HOST, RDS_DATABASE, RDS_CRED_FILE, RDS_CA_BUNDLE, PX_DATES, PX_ARCHIVE_DIR)
        return None:
        """
        read = functools.partial(fn.rds_table, host=params.RDS_HOST, database=params.RDS_DATABASE,
                                 cred_file=params.RDS_CRED_FILE, ca_bundle=params.RDS_CA_BUNDLE)
        dates = pd.read_csv(params.PX_DATES, dtype=str)
        scope = set(dates.loc[dates['source'] == 'FBX', 'uniquecontrast'])
        arc = {k: pd.read_parquet(os.path.join(params.PX_ARCHIVE_DIR, f'fbx_{k}.parquet')) for k in ('measure', 'report', 'msscore')}
        frames = {}
        for kind, key, main, val in (('measure', ['uniquecontrast', 'genes', 'pg'], 'px_fbx_measure', 'px_fbx_glues'),
                                     ('report', ['uniquecontrast'], 'px_fbx_report', 'px_fbx_glues_report')):
            # newest insert per row, in the PX_DATES scope
            m, v = (read(t).sort_values('id').drop_duplicates(key, keep='last') for t in (main, val))
            n_out = len((set(m['uniquecontrast']) | set(v['uniquecontrast'])) - scope)
            m, v = m[m['uniquecontrast'].isin(scope)], v[v['uniquecontrast'].isin(scope)]

            # the glues copy of a contrast over its main copy; the archive fills what the database lacks
            db = pd.concat([v, m[~m['uniquecontrast'].isin(set(v['uniquecontrast']))]], ignore_index=True)
            a = arc[kind][~arc[kind]['uniquecontrast'].isin(set(db['uniquecontrast']))]
            frames[kind] = pd.concat([db, a], ignore_index=True)
            if kind == 'measure':
                main_measure = m
            print(f'> RDS {kind}: {db["uniquecontrast"].nunique():,} experiments from the database + '
                  f'{a["uniquecontrast"].nunique():,} from the archive | {n_out:,} database experiments without a date skipped')

        self.FBX_MEASURE = _with_plate(frames['measure'], 'MEASURE')
        self.FBX_REPORT  = _with_plate(frames['report'], 'REPORT')
        # the formula on the main table; the archive keeps the exported score of the other experiments
        ms_arc = arc['msscore'][~arc['msscore']['uniquecontrast'].isin(set(main_measure['uniquecontrast']))]
        self.FBX_MSSCORE = pd.concat([fn.ms_score_rows(main_measure), ms_arc], ignore_index=True)
        val = read('px_ms_score_glues').sort_values('id').drop_duplicates('target', keep='last').set_index('target')
        self.VAL_MSSCORE, self.VAL_ASSOC = val['msscore'], val['associationscore']
        self.FBX_TRANCHES, self.VAL_TRANCHES, self.TRANCHE_CSVS = [], [], {}

    def get_contaminants_and_controls(self,params):
        ## local params:
        self.control_compounds = params.CONTROLS

        ## contaminants compounds to remove:
        self.contaminants = list(pd.read_csv(params.CONTAMINANTS)['Molecule Name'])

    def get_gene_research(self,params):

        ## degradation research per target (one record/gene); file path lives in config.GENE_RESEARCH
        with open(params.GENE_RESEARCH) as _f:   # path from config/config.yaml
            self.gene_research = json.load(_f)
        print(f'> loaded degradation research for {len(self.gene_research)} genes')

class OUTPUT():
    def __init__(self):
        self.measure = None
        self.mscore = None
        self.report = None
        self.plate2date = None
        self.validated_targets = None
        self.devalidated_targets = None
        self.validated_compounds = None
        self.devalidated_compounds = None
        self.iface_df = None
        self.compounds_df = None
        self.meas = None

    def combine_datasets(self, data, params):
        """
        Combine the df_raw (broad MS) and FBX (curated WT/KO) sides into the unified
        per-(compound,gene,experiment) MEASURE, per-(gene,plate) MS-SCORE, and per-experiment
        REPORT tables, then attach a tranche-derived plate date. FBX is the source of truth on
        shared experiments / (gene,plate) / uniquecontrasts.
        param class data: DATA instance (df_raw, df_ms, MS, FBX_*, uc2compound, FBX_TRANCHES, VAL_TRANCHES, TRANCHE_CSVS)
        param class params: PARAMS instance (source proteomics paths, PLATE_DATE_OVERRIDES, dbx / dbx_push)
        return None:
        """
        ## 1. MEASURE = df_raw UNION FBX_MEASURE (FBX wins on shared uniquecontrasts)
        _dr_uc_set = set(data.df_raw['uniquecontrast'].astype(str))   # reused by the MEASURE + REPORT unions
        _cols = ['compound', 'genes', 'pg', 'plate', 'uniquecontrast',
                 'logfc', 'pvalue', 'adjpval', 'significant']
        fbx_std = (data.FBX_MEASURE.assign(compound=data.FBX_MEASURE['uniquecontrast'].map(data.uc2compound))
                   .reindex(columns=_cols).assign(source='FBX'))
        _shared_uc = set(data.FBX_MEASURE['uniquecontrast']) & _dr_uc_set
        if getattr(params, 'FREE_UPSTREAM', False):
            data.FBX_MEASURE = None; gc.collect()   # absorbed into fbx_std — free before the big concat
        dr_std = data.df_raw.rename(columns={'MSPlate': 'plate'}).reindex(columns=_cols).assign(source='df_raw')
        self.measure = pd.concat([fbx_std, dr_std[~dr_std['uniquecontrast'].astype(str).isin(_shared_uc)]],
                                 ignore_index=True)
        del fbx_std, dr_std; gc.collect()   # drop the wide intermediates right after the union
        print(f'> combined MEASURE: {len(self.measure):,} rows | '
              f'{self.measure["uniquecontrast"].nunique():,} experiments | '
              f'{self.measure["compound"].nunique():,} compounds | {self.measure["genes"].nunique():,} genes')

        ## 2. MS-SCORE = FBX_MSSCORE UNION df_raw(significant), ONE ROW PER (gene, compound experiment).
        #  Ungrouped (per genes×uniquecontrast) so every compound keeps its OWN ms_score — NOT collapsed
        #  to the best compound per (gene,plate). The 3D dot position comes from these rows by the rule of
        #  gene_ms_score (get_iface); the extra rows are the runner-up compounds the MS slider / reconcile need.
        #  FBX wins on shared uniquecontrasts (as in MEASURE). Noisy plates dropped. Raw scale, not clipped.
        _FBX_MS = data.FBX_MSSCORE[~data.FBX_MSSCORE['plate'].isin(['Plate12', 'Plate15', 'Plate23'])]
        _cols = ['genes', 'plate', 'uniquecontrast', 'compound', 'pg', 'ms_score',
                 'association_score', 'genetic_score', 'literature_score', 'activity',
                 'logfc', 'pvalue', 'significant']
        fbx_ms = (_FBX_MS.assign(compound=lambda d: d['uniquecontrast'].map(data.uc2compound))
                  .reindex(columns=_cols).assign(source='FBX'))
        dr_ms = (data.df_raw[data.df_raw['significant'] == 1].rename(columns={'MSPlate': 'plate'})
                 .reindex(columns=_cols).assign(source='df_raw'))
        _shared_uc = set(_FBX_MS['uniquecontrast'].astype(str))
        self.mscore = pd.concat([fbx_ms, dr_ms[~dr_ms['uniquecontrast'].astype(str).isin(_shared_uc)]],
                                ignore_index=True)
        # one row per (gene, uniquecontrast); keep the highest score on any accidental duplicate
        self.mscore = (self.mscore.sort_values('ms_score', ascending=False)
                       .drop_duplicates(['genes', 'uniquecontrast']).reset_index(drop=True))
        print(f'> combined MS-SCORE: {len(self.mscore):,} (gene,compound) rows '
              f'| {self.mscore.drop_duplicates(["genes", "plate"]).shape[0]:,} (gene,plate) '
              f'| FBX {int((self.mscore["source"] == "FBX").sum()):,}, df_raw {int((self.mscore["source"] == "df_raw").sum()):,}')

        ## 3. REPORT = source-derived per-experiment metadata UNION FBX_REPORT (FBX wins).
        # df_raw side derives real concentration/activity from the raw source exports.
        P = 'MSData - Proteomics activities: '
        def _load_src(path, prefixed):
            pre = P if prefixed else ''
            m = {('Batch Molecule-Batch ID' if prefixed else 'Molecule-Batch ID'): 'batch',
                 pre + 'MSPlate': 'plate',
                 (P + 'Concentration (uM)' if prefixed else 'Concentration'): 'concentration',
                 pre + 'Cmpd Activity': 'activity', pre + 'Nr. Down': 'nr_down',
                 pre + 'Cell line': 'cell_line', pre + 'Sample Condition': 'condition'}
            # one read (a Dropbox file streams one time): keep the columns of m that the file has
            return pd.read_csv(params.dbx(path), usecols=lambda c: c in m).rename(columns=m)
        SRC = pd.concat([_load_src(params.CLEAN_PROTEOMICS_PATH, True),
                         _load_src(params.PX_20260520_CDDVAULT, True),
                         _load_src(params.PX_20260529_CDDVAULT, False)], ignore_index=True)
        SRC = (SRC.dropna(subset=['batch', 'plate']).drop_duplicates(['batch', 'plate'])
               .rename(columns={'batch': 'MoleculeBatchID', 'plate': 'MSPlate'}))
        _cols = ['uniquecontrast', 'compound', 'plate', 'concentration', 'activity',
                 'nr_down', 'cell_line', 'condition', 'target']   # target: validation exports only
        rep_dr = (data.df_raw[['uniquecontrast', 'MoleculeBatchID', 'MSPlate', 'compound']]
                  .drop_duplicates('uniquecontrast')
                  .merge(SRC, on=['MoleculeBatchID', 'MSPlate'], how='left')
                  .rename(columns={'MSPlate': 'plate'}))
        _msact = data.MS.sort_values('date').drop_duplicates('compound', keep='last').set_index('compound')['activity']
        rep_dr['activity'] = rep_dr['activity'].fillna(rep_dr['compound'].map(_msact))
        rep_dr = rep_dr.reindex(columns=_cols).assign(source='df_raw')
        rep_fbx = (data.FBX_REPORT.assign(compound=data.FBX_REPORT['uniquecontrast'].map(data.uc2compound))
                   .reindex(columns=_cols).drop_duplicates('uniquecontrast').assign(source='FBX'))
        _shared_uc = set(data.FBX_REPORT['uniquecontrast']) & _dr_uc_set
        self.report = pd.concat([rep_fbx, rep_dr[~rep_dr['uniquecontrast'].astype(str).isin(_shared_uc)]],
                                ignore_index=True)
        print(f'> combined REPORT: {len(self.report):,} uniquecontrasts | '
              f'compounds {self.report["compound"].nunique():,} | plates {self.report["plate"].nunique():,}')

        ## 4. per-plate experiment DATE -> date-based plate filtering.
        if getattr(params, 'PX_SOURCE', 'dropbox') == 'rds':   # the plate dates of the last Dropbox build
            _dates = pd.read_csv(params.PX_DATES, dtype=str).dropna(subset=['plate', 'date'])
            self.plate2date = _dates.drop_duplicates('plate').set_index('plate')['date'].to_dict()
        else:   # tranche-derived: the three df_raw exports, then each FBX folder date
            _DFRAW_DATE_SRC = [
                ('2026-04-29', params.CLEAN_PROTEOMICS_PATH, 'MSData - Proteomics activities: MSPlate'),
                ('2026-05-20', params.PX_20260520_DB, 'MSPlate'),
                ('2026-05-29', params.PX_20260529_DB, 'MSPlate'),
            ]
            self.plate2date = {}
            for _d, _p, _c in _DFRAW_DATE_SRC:
                self.plate2date.update({pl: _d for pl in pd.read_csv(params.dbx(_p), usecols=[_c], dtype=str)[_c].dropna().unique()})
            for _t in data.FBX_TRANCHES:   # FBX last -> wins on shared plates; date from the folder name
                _d = pd.to_datetime(os.path.basename(_t)[:8]).strftime('%Y-%m-%d')
                # read the whole REPORT (small) so _ensure_plate can rebuild `plate` from the uniquecontrast
                # for validation-only tranches whose export omits the column (else usecols=['plate'] crashes)
                _plates = _ensure_plate(pd.read_csv(params.dbx(_fbx_csv(data.TRANCHE_CSVS[_t], 'REPORT'))))['plate'].dropna().astype(str).unique()
                if _t in data.VAL_TRANCHES:   # a validation export dates only new plates: re-runs and
                    _plates = [pl for pl in _plates if pl not in self.plate2date]   # primary refs keep their date
                self.plate2date.update({pl: _d for pl in _plates})
        self.plate2date.update(params.PLATE_DATE_OVERRIDES)
        for _df in (self.measure, self.mscore, self.report):
            _df['date'] = pd.to_datetime(_df['plate'].astype(str).map(self.plate2date))
        print(f"> plate dates: {len(self.plate2date)} plates mapped | report spans "
              f"{self.report['date'].min():%Y-%m-%d} .. {self.report['date'].max():%Y-%m-%d}")

        # downcast the repeated string columns to `category`: MEASURE is ~47.7M rows and its
        # object-dtype strings (each cell an 8-byte pointer + a Python str) dominate RAM. Category
        # stores int codes + one copy of each unique value — ~10x smaller on MEASURE, and it also
        # shrinks the get_iface `meas` copy and the parquet exports. Round-trips through parquet and
        # is transparent to groupby / isin / .str / merge downstream.
        _cat_cols = ['genes', 'uniquecontrast', 'plate', 'compound', 'pg', 'source']
        for _df in (self.measure, self.mscore, self.report):
            for _c in _cat_cols:
                if _c in _df.columns and _df[_c].dtype == object:
                    _df[_c] = _df[_c].astype('category')
        gc.collect()
        print(f'> categorized string columns | MEASURE now '
              f'{self.measure.memory_usage(deep=True).sum() / 1e9:.1f} GB')

        # optional parquet dump of the combined tables (opt-in) — done here while they're complete, so
        # get_iface can free them afterwards (with FREE_UPSTREAM) instead of holding ~65M rows through
        # the render just to keep the export possible. Category dtype round-trips through parquet.
        if getattr(params, 'EXPORT_COMBINED', False):
            _pq = os.path.expanduser(params.PX_PARQUET_DIR)
            for _k, _df in (('MEASURE', self.measure), ('MSCORE', self.mscore), ('REPORT', self.report)):
                params.dbx_push(_df, os.path.join(_pq, f'Px_{_k}.parquet'))   # PX_PARQUET_DIR on Dropbox -> rclone upload
            print(f'> exported combined tables -> {_pq} (Px_MEASURE/MSCORE/REPORT.parquet)')

        # free the FBX_* sources + MS now — fully absorbed into measure/mscore/report and unused
        # past here (get_iface needs only df_raw/serac_df/target2R2_df + the combined tables). Frees
        # the big FBX_MEASURE BEFORE get_iface allocates its own copy, so the peak doesn't hit swap.
        if getattr(params, 'FREE_UPSTREAM', False):
            for _a in ('FBX_MEASURE', 'FBX_MSSCORE', 'FBX_REPORT', 'MS'):
                setattr(data, _a, None)
            gc.collect()
            try: ctypes.CDLL('libc.so.6').malloc_trim(0)
            except Exception: pass
            print('> freed FBX_* + MS sources (FREE_UPSTREAM)')

    def get_de_validated(self, data, params):
        """
        gets the list of validated and devalidated targets/compounds
        e.g. those for which there was ligase dependent (validated) or not (devalidated) activity
        """
        sdf = data.serac_df
        _has_tgt = sdf['Px_Target_interest'].notnull()
        _dep0 = sdf[(sdf['Px_Ligase_dependent(yes/no)'] == 0) & _has_tgt]   # devalidated (ligase-independent)
        _dep1 = sdf[(sdf['Px_Ligase_dependent(yes/no)'] == 1) & _has_tgt]   # validated (ligase-dependent)
        ## targets: first token of Px_Target_interest (';'-split), upper-cased
        self.devalidated_targets = list({s.split(' ')[0].upper() for x in _dep0['Px_Target_interest'] for s in x.split(';')})
        self.validated_targets   = list({s.split(' ')[0].upper() for x in _dep1['Px_Target_interest'] for s in x.split(';')})
        ## compounds
        self.devalidated_compounds = list(set(_dep0['compound']))
        self.validated_compounds   = list(set(_dep1['compound']))

        # optional override: replace the CDD-derived validated_targets with a user-curated list
        # (comma/whitespace-delimited gene symbols in VALIDATED_TARGET_FILE). Empty/absent -> keep CDD.
        _vtf = getattr(params, 'VALIDATED_TARGET_FILE', None)
        if _vtf and str(_vtf).strip():
            with open(os.path.expanduser(str(_vtf).strip())) as _f:
                self.validated_targets = sorted({g.strip().upper() for g in re.split(r'[,\s]+', _f.read()) if g.strip()})
            print(f'> validated_targets overridden from {_vtf}: {len(self.validated_targets)} genes')

        print(f'> target: {len(self.validated_targets)} validated - {len(self.devalidated_targets)} devalidated targets')
        print(f'> compound: {len(self.validated_compounds)} validated - {len(self.devalidated_compounds)} devalidated compounds')

    def get_iface(self, data, params):
        """
        Build (or load) the four render inputs — iface_df / compounds_df / meas / plate2date — from
        the combined tables + OpenTargets + SAR R2 + pharma/BMS gene lists. IFACE_OVERWRITE builds +
        saves to IFACE_DIR; else loads them back and frees the heavy upstream frames.
        param class data: DATA instance (df_raw, serac_df, target2R2_df)
        param class params: PARAMS instance (OT_CACHE, PHARMA_PATENT_CSV, BMS_GENES, PHARMA_R2_CUTOFF, IFACE_DIR, IFACE_OVERWRITE)
        return None:
        """
        ## save or load interface data
        ## IFACE_OVERWRITE=True  -> build the render inputs from the unified tables (measure / mscore /
        ##   report / plate2date) and SAVE the four to IFACE_DIR. IFACE_OVERWRITE=False -> LOAD them and
        ##   free the heavy upstream frames, so you can skip the combine + build cells (10-15, 21) and run
        ##   only: config + the small param cells (contaminants / targets / research) + this + the render.
        _IFACE_KEYS = ['iface_df', 'compounds_df', 'meas', 'plate2date']

        if params.IFACE_OVERWRITE:
            DROP_PLATES = ['Plate12', 'Plate15', 'Plate23']
            # --- volcano source = unified measure (drop noisy plates) + p-value floor ---
            # a 0.0 p plots at y=300 under -log10; floor zeros to the smallest non-zero p so the
            # renderers' 1e-300 clip is inert (same as the Re-Compute Volcanoes cell). No-op if floored.
            # only the columns get_iface + the volcano render use (drop compound/pg/adjpval/source/date)
            # so this second ~47M-row frame is ~half the width of self.measure it is copied from.
            _MEAS_COLS = ['uniquecontrast', 'genes', 'plate', 'logfc', 'pvalue', 'significant']
            meas = self.measure.loc[~self.measure['plate'].isin(DROP_PLATES), _MEAS_COLS].copy()
            _pmin = self.measure.loc[self.measure['pvalue'] > 0, 'pvalue'].min()
            meas.loc[meas['pvalue'].eq(0.0), 'pvalue'] = _pmin
            # volcano colour: targets only on an experiment with report targets (user decision 2026-10-23)
            meas['volcano_sig'] = volcano_colour(meas, self.report, self.measure, getattr(params, 'VOLCANO_TARGET_ADJP', 0.05))
            # self.measure is fully absorbed into `meas` now (the only frame the render needs); free the
            # ~65M-row original so it doesn't coexist with `meas` through the rest of get_iface + render.
            if getattr(params, 'FREE_UPSTREAM', False):
                self.measure = None
                gc.collect()
                try: ctypes.CDLL('libc.so.6').malloc_trim(0)
                except Exception: pass

            # --- gene-level axes: x=R2 (SAR full-genome), y=association (gene_association) + top area (OT_CACHE), z=ms_score ---
            R2_df = data.target2R2_df[['genes', 'R2']]
            ot_df = pd.read_parquet(params.OT_CACHE)
            assoc = gene_association(ot_df, data.VAL_ASSOC, data.OT_ASSOC)
            _rank = {a: i for i, a in enumerate(PRIORITY_DISEASE_AREAS)}
            _areas = (ot_df[['target_symbol', 'overall_score', 'therapeutic_areas']]
                    .assign(area=lambda d: d['therapeutic_areas'].fillna('').str.split('|')).explode('area'))
            _areas = _areas[_areas['area'].isin(_rank)].copy()
            _areas['_rank'] = _areas['area'].map(_rank)
            _top_area = (_areas.sort_values(['target_symbol', '_rank', 'overall_score'], ascending=[True, True, False])
                        .drop_duplicates('target_symbol', keep='first')
                        .rename(columns={'target_symbol': 'gene', 'area': 'disease_area'})[['gene', 'disease_area']])
            ms_gene = gene_ms_score(self.mscore, data.VAL_MSSCORE)
            print(f'> gene MS score: {ms_gene["ms_source"].replace("", "none").value_counts().to_dict()}')

            # --- dots: one per gene over the mscore universe (+ validation targets); R2/association left-joined (missing -> 0.0) ---
            iface_df = (ms_gene
                        .merge(R2_df, on='genes', how='left')   # left: keep genes lacking a SAR R2 (n_compounds < min_compounds / not yet computed)
                        .merge(assoc, left_on='genes', right_index=True, how='left')
                        .rename(columns={'genes': 'gene'})
                        .merge(_top_area, on='gene', how='left'))
            iface_df[['R2', 'association_score']] = iface_df[['R2', 'association_score']].fillna(0.0)   # no SAR R2 / no OT association -> 0.0 (still plotted)
            _pharma = set(pd.read_csv(params.PHARMA_PATENT_CSV)['gene'].dropna().unique())
            _bms    = set(pd.read_csv(params.BMS_GENES)['hgnc_symbol'].dropna().unique())
            _model  = iface_df['R2'] > params.PHARMA_R2_CUTOFF
            iface_df.loc[iface_df['gene'].isin(_pharma) & _model, 'disease_area'] = 'pharma'
            iface_df.loc[iface_df['gene'].isin(_bms)    & _model, 'disease_area'] = 'BMS'
            print(f'> iface_df: {len(iface_df):,} gene dots (whole Px) | disease_area set for {iface_df["disease_area"].notna().sum():,}')

            # --- compounds_df: significant-down hits from measure + report metadata + smiles ---
            chemlib = data.serac_df[['compound', 'smiles']].drop_duplicates('compound')
            n_genes = meas.dropna(subset=['logfc', 'pvalue']).groupby('uniquecontrast')['genes'].nunique().rename('n_genes')
            rep = self.report[['uniquecontrast', 'compound', 'plate', 'concentration', 'activity', 'target']].drop_duplicates('uniquecontrast')
            _hit = meas.loc[(meas['significant'] == 1) & (meas['logfc'] < 0), ['genes', 'uniquecontrast', 'logfc', 'pvalue']]
            hits = _hit.merge(rep, on='uniquecontrast', how='left').rename(columns={'genes': 'gene'})
            hits = hits[hits['compound'].notna() & hits['compound'].str.startswith('SRB-')]
            # Per-(gene,compound,plate) MS score for the slider (pl[8]), straight from the per-compound mscore
            # table; a validation target's own validation experiments get its validation score below. The
            # dot's z stays fixed (gene_ms_score). Keyed by (genes, uniquecontrast).
            ms_per_uc = self.mscore.dropna(subset=['ms_score']).groupby(['genes', 'uniquecontrast'])['ms_score'].max()
            # last use of the combined mscore/report frames (rep + ms_per_uc are derived above); free them
            # so only the small derived tables + `meas` remain for the completion/primary passes + render.
            if getattr(params, 'FREE_UPSTREAM', False):
                self.mscore = self.report = None
                gc.collect()
                try: ctypes.CDLL('libc.so.6').malloc_trim(0)
                except Exception: pass
            hits['ms_score'] = [ms_per_uc.get((g, u)) for g, u in zip(hits['gene'], hits['uniquecontrast'])]
            hits = hits.sort_values(['gene', 'compound', 'plate', 'logfc'])
            compounds_df = (hits.groupby(['gene', 'compound', 'plate'], as_index=False).first()
                            .merge(chemlib, on='compound', how='left')
                            .merge(n_genes, on='uniquecontrast', how='left'))
            compounds_df = compounds_df[['gene', 'compound', 'plate', 'activity', 'n_genes',
                                        'uniquecontrast', 'logfc', 'ms_score', 'smiles']]
            # keep only compounds present in serac_df (CDD AJ/AK library); exclude the rest from the viz
            _n0c = compounds_df['compound'].nunique()
            compounds_df = compounds_df[compounds_df['compound'].isin(chemlib['compound'])]
            print(f'> {compounds_df["compound"].nunique():,}/{_n0c:,} compounds present in serac_df (rest excluded)')
            # MoleculeBatchID (per experiment) for the volcano label text, sourced from df_raw.
            _uc2mbid = data.df_raw.drop_duplicates('uniquecontrast').set_index('uniquecontrast')['MoleculeBatchID']
            compounds_df['molecule_batch_id'] = compounds_df['uniquecontrast'].map(_uc2mbid)
            # Experiments absent from df_raw (the …WT/KO/Eval plates) have no MoleculeBatchID there,
            # but the batch id is embedded in uniquecontrast (SRB.0005514.001_vs_… -> SRB-0005514-001);
            # reconstruct it for those rows, keeping it only when it matches the row's own compound.
            _miss = compounds_df['molecule_batch_id'].isna()
            _parsed = compounds_df.loc[_miss, 'uniquecontrast'].str.split('_vs_').str[0].str.replace('.', '-', regex=False)
            _valid = [p.startswith(c) for p, c in zip(_parsed, compounds_df.loc[_miss, 'compound'].astype(str))]
            compounds_df.loc[_miss, 'molecule_batch_id'] = _parsed.where(pd.Series(_valid, index=_parsed.index))
            _still = compounds_df['molecule_batch_id'].isna().sum()
            print(f'> molecule_batch_id reconstructed from uniquecontrast for {sum(_valid):,} rows; {_still:,} still missing')
            # drop Silent-activity experiments (no real down-modulation; shrinks the panel)
            _n0 = len(compounds_df)
            compounds_df = compounds_df[compounds_df['activity'] != 'Silent']
            print(f'> dropped {_n0 - len(compounds_df):,} Silent-activity rows -> {len(compounds_df):,} remain')

            def _ride_along(df):   # (gene, compound, plate, uniquecontrast, logfc) -> a not-significant context row
                df = (df.merge(rep[['uniquecontrast', 'activity']], on='uniquecontrast', how='left')
                        .merge(n_genes, on='uniquecontrast', how='left')
                        .merge(chemlib, on='compound', how='left'))
                df['molecule_batch_id'] = df['uniquecontrast'].map(_uc2mbid)
                _m = df['molecule_batch_id'].isna()   # not in df_raw: the batch id sits in the contrast
                _p = df.loc[_m, 'uniquecontrast'].str.split('_vs_').str[0].str.replace('.', '-', regex=False)
                _ok = [p.startswith(c) for p, c in zip(_p, df.loc[_m, 'compound'].astype(str))]
                df.loc[_m, 'molecule_batch_id'] = _p.where(pd.Series(_ok, index=_p.index))
                return df.assign(is_completion=True, ms_score=float('nan'))   # context row: no score of its own (a target row gets its validation score below)

            # --- complete validation stems ---------------------------------------------
            # A (gene, compound) is a hit only where it is significant-down, so a gene
            # significant in the WT condition but not the KO condition of a plate stem
            # (…WT/…MLN/…KO) would have no KO volcano. For each such hit on a validation
            # plate, also add the stem's OTHER conditions where the compound was actually
            # run (contrast exists) and the gene was measured — showing the gene at its
            # true, non-significant coordinates. Conditions where the compound was never
            # tested are correctly omitted. Rows are flagged is_completion so the interface
            # shows them as ride-along context, not as hits.
            compounds_df['is_completion'] = False
            _sufs = [str(s).upper() for s in getattr(params, 'VALIDATION_PLATE_SUFFIXES', ['WT', 'MLN', 'KO'])]
            _valre = re.compile(r'(' + '|'.join(_sufs) + r')$', re.I)
            _stem = lambda p: _valre.sub('', str(p))
            _val_plates = [p for p in rep['plate'].dropna().unique() if _valre.search(str(p))]
            _stem_map = {}
            for _p in _val_plates:
                _stem_map.setdefault(_stem(_p), []).append(_p)
            _uc_of = rep.drop_duplicates(['compound', 'plate']).set_index(['compound', 'plate'])['uniquecontrast']
            # (gene, contrast) present — restricted to validation-plate contrasts, the only ones the
            # completion loop below queries. Building it over all ~47.7M measure rows would materialise
            # ~47.7M Python tuples (~3-4 GB); scoping it to val plates keeps it tiny.
            _val_ucs = set(rep.loc[rep['plate'].isin(_val_plates), 'uniquecontrast'].dropna())
            _mm = meas.loc[meas['uniquecontrast'].isin(_val_ucs), ['genes', 'uniquecontrast']]
            _measured = set(zip(_mm['genes'], _mm['uniquecontrast']))
            _seen = set(zip(compounds_df['gene'], compounds_df['compound'], compounds_df['plate']))
            _add = []
            for _, _r in compounds_df[compounds_df['plate'].isin(_val_plates)].iterrows():
                for _sib in _stem_map.get(_stem(_r['plate']), []):
                    if _sib == _r['plate'] or (_r['gene'], _r['compound'], _sib) in _seen:
                        continue
                    if (_r['compound'], _sib) not in _uc_of.index:
                        continue                                                 # compound never run here -> omit
                    _uc = _uc_of.loc[(_r['compound'], _sib)]
                    _uc = _uc if isinstance(_uc, str) else _uc.iloc[0]
                    if (_r['gene'], _uc) not in _measured:
                        continue                                                 # gene not measured -> omit
                    _seen.add((_r['gene'], _r['compound'], _sib))
                    _add.append({'gene': _r['gene'], 'compound': _r['compound'], 'plate': _sib, 'uniquecontrast': _uc})
            if _add:
                add_df = pd.DataFrame(_add)
                # mean logfc only for the experiments we're adding rows for (a handful), NOT a groupby over
                # all ~65M measured (gene,uc) pairs — that builds a frame-sized Series and pushes into swap.
                _mean_logfc = (meas.loc[meas['uniquecontrast'].isin(set(add_df['uniquecontrast'])),
                                        ['genes', 'uniquecontrast', 'logfc']]
                               .dropna(subset=['logfc']).groupby(['genes', 'uniquecontrast'])['logfc'].mean())
                add_df['logfc'] = [_mean_logfc.get((g, u)) for g, u in zip(add_df['gene'], add_df['uniquecontrast'])]
                compounds_df = pd.concat([compounds_df, _ride_along(add_df)[compounds_df.columns]], ignore_index=True)
            print(f'> validation-stem completion: added {len(_add):,} ride-along condition rows '
                  f'across {len({(a["gene"], a["compound"]) for a in _add}):,} (gene,compound) pairs')

            # --- validation targets ----------------------------------------------------
            # A validation REPORT names the gene(s) each experiment tests (`target`, ';'-separated). The
            # gene's panel shows each such contrast, also where the gene is not significant (a negative
            # result). The key is the contrast, so every batch of a compound shows. is_target lets the row
            # open its validation stem client-side (a plain completion row needs a real hit in its stem).
            compounds_df['is_target'] = False
            _tgt = validation_target_rows(rep, compounds_df, meas)
            _tgt = _tgt[_tgt['compound'].isin(chemlib['compound'])]   # library compounds only, as for the hits
            if len(_tgt):
                compounds_df = pd.concat([compounds_df, _ride_along(_tgt).assign(is_target=True)[compounds_df.columns]],
                                         ignore_index=True)
            _seen.update(zip(_tgt['gene'], _tgt['compound'], _tgt['plate']))
            print(f'> validation targets: added {len(_tgt):,} tested-target rows (gene not a hit there) '
                  f'across {len(set(zip(_tgt["gene"], _tgt["compound"]))):,} (gene,compound) pairs')

            # --- attach the primary-screen volcano to each validation stem -------------
            # For a (gene, compound) that has a validation-plate hit, also surface the
            # compound's broad primary-screen volcano — the NON-validation contrast where the
            # gene has the highest MS score (else its strongest-logfc measurement, as a
            # ride-along). The client renders it as the leading cell of the stem so the
            # hover-trace links primary -> WT -> KO. is_primary flags the row; an existing hit
            # row is flagged in place, otherwise a ride-along row is added.
            compounds_df['is_primary'] = False
            _vp = compounds_df['plate'].isin(_val_plates)
            _val_pairs = set(zip(compounds_df.loc[_vp, 'gene'], compounds_df.loc[_vp, 'compound']))
            # compound -> its primary (non-validation) contrasts, and where each gene was measured
            _prim = (rep.loc[~rep['plate'].isin(_val_plates), ['compound', 'plate', 'uniquecontrast']]
                     .dropna(subset=['uniquecontrast']).drop_duplicates('uniquecontrast'))
            _prim_by_cmp = {c: list(zip(g['plate'], g['uniquecontrast'])) for c, g in _prim.groupby('compound')}
            # measured (gene, contrast) + mean logfc for the primary contrasts — restricted to the
            # validation genes (the only genes the attach loop below queries). Over the full non-validation
            # slice this would be a ~60M-entry Series + a ~60M-tuple set (nearly all of meas) -> swap.
            _val_genes = {g for g, _c in _val_pairs}
            _pm = meas.loc[meas['uniquecontrast'].isin(set(_prim['uniquecontrast']))
                           & meas['genes'].isin(_val_genes),
                           ['genes', 'uniquecontrast', 'logfc']].dropna(subset=['logfc'])
            _prim_logfc = _pm.groupby(['genes', 'uniquecontrast'])['logfc'].mean()
            _prim_measured = set(zip(_pm['genes'], _pm['uniquecontrast']))
            _mark, _padd = set(), []
            for _g, _c in _val_pairs:
                _cands = [(p, u) for (p, u) in _prim_by_cmp.get(_c, []) if (_g, u) in _prim_measured]
                if not _cands:
                    continue                                                 # no primary volcano for this gene -> skip
                # highest MS score for (gene, contrast); tie-break strongest (most negative) logfc
                _pl, _uc = max(_cands, key=lambda pu: (
                    (ms_per_uc.get((_g, pu[1])) if pd.notna(ms_per_uc.get((_g, pu[1]))) else -1e9),
                    -float(_prim_logfc.get((_g, pu[1]), 0.0))))
                if (_g, _c, _pl) in _seen:
                    _mark.add((_g, _c, _pl))                                 # existing hit row -> flag in place
                else:
                    _padd.append({'gene': _g, 'compound': _c, 'plate': _pl, 'uniquecontrast': _uc})
            if _mark:
                compounds_df.loc[[(g, c, p) in _mark for g, c, p in
                                  zip(compounds_df['gene'], compounds_df['compound'], compounds_df['plate'])],
                                 'is_primary'] = True
            if _padd:
                padd_df = pd.DataFrame(_padd)
                padd_df['logfc'] = [_prim_logfc.get((g, u)) for g, u in zip(padd_df['gene'], padd_df['uniquecontrast'])]
                compounds_df = pd.concat([compounds_df, _ride_along(padd_df).assign(is_primary=True, is_target=False)
                                          [compounds_df.columns]], ignore_index=True)
            print(f'> primary-screen attach: {len(_mark):,} existing + {len(_padd):,} ride-along '
                  f'primary volcanoes flagged across {len(_val_pairs):,} validation (gene,compound) pairs')

            # --- a validation target's own validation experiments carry its validation score (the MS slider) ---
            _vs = validation_experiment_scores(compounds_df, rep, data.VAL_MSSCORE, _val_plates)
            compounds_df['ms_score'] = _vs.fillna(compounds_df['ms_score'])
            print(f'> validation MS score on {int(_vs.notna().sum()):,} target experiment rows '
                  f'({compounds_df.loc[_vs.notna(), "gene"].nunique()} genes)')

            print(f'> compounds_df: {len(compounds_df):,} (gene,compound,plate) rows across '
                f'{compounds_df["gene"].nunique():,} genes, {compounds_df["uniquecontrast"].nunique():,} volcanoes to render')

            # --- save the four render inputs ---
            os.makedirs(params.IFACE_DIR, exist_ok=True)
            iface_df.to_parquet(os.path.join(params.IFACE_DIR, 'iface_df.parquet'))
            compounds_df.to_parquet(os.path.join(params.IFACE_DIR, 'compounds_df.parquet'))
            meas.to_parquet(os.path.join(params.IFACE_DIR, 'meas.parquet'))
            with open(os.path.join(params.IFACE_DIR, 'plate2date.json'), 'w') as _fh:
                json.dump(self.plate2date, _fh)
            self.iface_df, self.compounds_df, self.meas = iface_df, compounds_df, meas
            print(f'> saved interface inputs -> {params.IFACE_DIR}/ ({", ".join(_IFACE_KEYS)})')
            # free df_raw now (FBX_* + MS were freed at the end of combine) — absorbed into the render
            # inputs and unused past here. self.measure/mscore/report were already freed above once their
            # derivations finished (optionally exported first via EXPORT_COMBINED). Opt-in via FREE_UPSTREAM
            # so callers that still inspect data.df_raw / FBX_* / self.measure (e.g. tests) can keep them.
            if getattr(params, 'FREE_UPSTREAM', False):
                for _a in ('df_raw', 'MS', 'FBX_MEASURE', 'FBX_MSSCORE', 'FBX_REPORT'):
                    setattr(data, _a, None)
                gc.collect()
                try: ctypes.CDLL('libc.so.6').malloc_trim(0)   # return freed arenas to the OS (Linux)
                except Exception: pass
                print('> freed upstream df_raw frame (FREE_UPSTREAM)')
        else:
            self.iface_df     = pd.read_parquet(os.path.join(params.IFACE_DIR, 'iface_df.parquet'))
            self.compounds_df = pd.read_parquet(os.path.join(params.IFACE_DIR, 'compounds_df.parquet'))
            self.meas         = pd.read_parquet(os.path.join(params.IFACE_DIR, 'meas.parquet'))
            with open(os.path.join(params.IFACE_DIR, 'plate2date.json')) as _fh:
                self.plate2date = json.load(_fh)
            print(f'> loaded interface inputs from {params.IFACE_DIR}/ | iface_df {self.iface_df.shape}, '
                f'compounds_df {self.compounds_df.shape}, meas {self.meas.shape}, {len(self.plate2date)} plate dates')
            # free the heavy upstream frames (absorbed into the loaded inputs); render needs only the four
            for _obj, _attrs in ((data, ['df_raw', 'MS', 'FBX_MEASURE', 'FBX_MSSCORE', 'FBX_REPORT']),
                                 (self, ['measure', 'mscore', 'report'])):
                for _a in _attrs:
                    setattr(_obj, _a, None)
            gc.collect()
            try: ctypes.CDLL('libc.so.6').malloc_trim(0)   # return freed arenas to the OS (Linux)
            except Exception: pass

    def build_interface(self, data, params, output_dir):
        """
        Render the per-gene 3D interface HTML (+ external volcanoes / thumbnails) from the four
        render inputs on self. Dots = one per gene: x=R2 (SAR), y=association (OpenTargets), z=ms_score.
        param class data: DATA instance (control_compounds, contaminants, gene_research)
        param class params: PARAMS instance (ACTIVE_C, SRB_PNG_DIR, IFACE_DIR, IFACE_OVERWRITE)
        param str output_dir: base dir (CLI --output_dir) for interfaces/ (HTML) + volcanoes_px/
        return None:
        """
        DISEASE_AREA_COLORS = {
            'pharma': params.ACTIVE_C, 'BMS': params.BMS_C,
            'cancer or benign tumor': '#DD870E', 'hematologic disease': '#FF0000',
            'cardiovascular disease': "#FB008A", 'immune system disease': '#2A9D8F',
            'musculoskeletal or connective tissue disease': '#264653',
            'nervous system disease': '#963802', 'psychiatric disorder': '#000000',
            'nutritional or metabolic disease': '#17E804', 'endocrine system disease': "#6EE5F5",
        }
        # every priority disease area get_iface can assign must have a colour here
        assert set(PRIORITY_DISEASE_AREAS).issubset(DISEASE_AREA_COLORS), \
            f'DISEASE_AREA_COLORS missing: {set(PRIORITY_DISEASE_AREAS) - set(DISEASE_AREA_COLORS)}'
        # Validation-mode colours (V toggle): each category is a dark ring + light fill
        # (like the reference volcano). Purple = FBXO31 dependent, orange = FBXO31 independent,
        # light blue = every other gene; the grey backdrop turns light blue too.
        VALIDATION_COLORS = {
            'dependent':   {'fill': '#B98BD6', 'ring': '#7B2D8E'},
            'independent': {'fill': '#F2B366', 'ring': '#D07C1A'},
            'rest':        {'fill': '#B3D4E6', 'ring': '#6BA3C7'},
            'background': '#CFE3F0',
        }
        MUST_INCLUDE = sorted(self.iface_df.loc[self.iface_df['disease_area'].isin(['pharma', 'BMS']), 'gene'])
        # Plates filter starts ticked on SHOW_PLATE's blocks (config/--show_plate: dates or 'validation <date>'),
        # else the latest date only; untick to widen. resolve_plate_defaults normalises YYYYMMDD -> YYYY-MM-DD.
        _vsuf = getattr(params, 'VALIDATION_PLATE_SUFFIXES', ('WT', 'MLN', 'KO'))
        PLATE_DEFAULTS, _show_blocks = resolve_plate_defaults(self.plate2date, getattr(params, 'SHOW_PLATE', None), _vsuf)
        print(f'> Plates default-ticked: {len(PLATE_DEFAULTS)} plate(s) in {", ".join(_show_blocks)}')
        # Target-validation filter: untick the FBXO31-independent box on load when configured
        # (FBXO31_INDEPENDENT_TICKED=false -> only the dependent box ticked); default keeps both ticked.
        _val_defaults = None if getattr(params, 'FBXO31_INDEPENDENT_TICKED', True) else ['FBXO31 dependent']
        # gene_research = {gene_name: record}; tolerate a dict, a list of records, or a bad/stale value
        _R = data.gene_research
        if isinstance(_R, dict):
            gene_research = _R
        elif isinstance(_R, (list, tuple)):
            gene_research = {r['gene_name']: r for r in _R if isinstance(r, dict) and 'gene_name' in r}
        else:
            gene_research = {}
        print(f'> gene_research: {len(gene_research)} genes' + ('' if gene_research else '  (empty - re-run the GENE_RESEARCH load cell)'))

        # compound-panel cache (the ~30s 'compound panels' build): load it when not overwriting so
        # the render is near-instant; rebuild + save when IFACE_OVERWRITE (referenced thumbnail/volcano
        # files must already exist on disk). Lives with the render since only plot_3d_interface builds it.
        _panels_path = os.path.join(params.IFACE_DIR, 'panels.json')
        _panels_in = None
        if not params.IFACE_OVERWRITE and os.path.exists(_panels_path):
            with open(_panels_path) as _f:
                _panels_in = json.load(_f)

        fig, highlighted, _panels = fn.plot_3d_interface(
            self.iface_df,
            x_col='R2', y_col='association_score', z_col='ms_score',
            x_label='SAR predictability', y_label='association score', z_label='MS score',
            must_include=MUST_INCLUDE, top_n_highlight=40,
            compounds_df=self.compounds_df, plate_dates=self.plate2date, plate_defaults=PLATE_DEFAULTS,  # nested-by-date Plates filter; default-tick latest date only
            plate_validation_suffixes=_vsuf,  # …WT/MLN/KO stems, shown side by side
            panels=_panels_in, return_panels=True,  # skip/cache the compound-panel build
            volcano_source=self.meas, volcano_key='uniquecontrast', page_size=5,
            png_dir=params.SRB_PNG_DIR,   # real compound PNGs from config; RDKit-render fallback when absent
            thumb_external=True,   # reference srb_png/<compound>.png next to the HTML (not inline base64)
            range_sliders=True, range_defaults={'x': 0.0, 'y': 0.0, 'z': 0.0}, # {SAR, OT, MS} sliders open fully; alt presets: {'x':0,'y':0,'z':30} or {'x':0.1,'y':0.5,'z':10}
            activity_defaults=getattr(params, 'ACTIVITY_DEFAULTS', ['Single', 'Low']),   # Activity boxes ticked on load; empty -> all
            control_compounds=data.control_compounds,
            control_default_on=getattr(params, 'CONTROLS_ON', True),  # control tickbox on load (config CONTROLS_ON)
            contaminant_compounds=data.contaminants, contaminant_default_on=False,  # hide contaminants by default
            gene_research=gene_research,
            validated_targets=self.validated_targets, devalidated_targets=self.devalidated_targets,  # Target validation (Y/N) tickboxes
            validated_label='FBXO31 dependent', devalidated_label='FBXO31 independent',
            validated_compounds=self.validated_compounds, devalidated_compounds=self.devalidated_compounds,  # Compound validation tickboxes
            compound_validated_label='FBXO31 dependent', compound_devalidated_label='FBXO31 independent',
            # target-filter boxes ticked on load (config); empty -> None -> all ticked
            depmap_defaults=getattr(params, 'DEPMAP_DEFAULTS', ['Selective', 'Non-essential']) or None,
            conf_defaults=getattr(params, 'CONF_DEFAULTS', ['High', 'Med']) or None,
            lof_defaults=getattr(params, 'LOF_DEFAULTS', ['Yes']) or None,
            validation_defaults=_val_defaults,  # None -> all ticked; FBXO31_INDEPENDENT_TICKED=false -> only dependent
            volcano_significant=True, volcano_sig_col='volcano_sig',  # targets-only colour on validation volcanoes
            volcano_dir=os.path.join(output_dir, 'interfaces', 'volcanoes_px'),
            volcano_n_jobs=resolve_n_jobs(getattr(params, 'NJOBS', 0)),  # config NJOBS (0 -> CPUs-2) for the volcano render
            volcano_xlim=(-8, 8), volcano_size_px=350,
            disease_area_colors=DISEASE_AREA_COLORS, nb_display=False,
            validation_colors=VALIDATION_COLORS, color_mode_default='V',  # V/D colour toggle; open in validation colouring
            labels_default_on=getattr(params, 'LABELS_ON', True),  # Labels eye toggle on load (config LABELS_ON)
            size_buckets=getattr(params, 'GENE_SIZE_BUCKETS', [6, 8, 10, 12, 15, 20]),  # dot px by #significant-compounds (1,2,3,4,5,>5)
            ring_px=getattr(params, 'GENE_RING_PX', 4),   # thickness (px) of the dark ring drawn around each gene dot
            z_fit_pad=getattr(params, 'Z_FIT_PAD', 0.05),  # MS axis top = the highest shown gene + this part of the span (config Z_FIT_PAD)
            html_path=os.path.join(output_dir, 'interfaces', 'Serac_Px_interface.html'), # 20260612_3d_interface_PX_R2_assoc_ms.html
        )

        if params.IFACE_OVERWRITE or not os.path.exists(_panels_path):
            with open(_panels_path, 'w') as _f:
                json.dump(_panels, _f)
            print(f'> saved compound panels -> {_panels_path}')


def build_px_archive(params, plate2date):
    """
    One-time step before PX_SOURCE: rds. Reads the Dropbox build one last time (load_dropbox_fbx + the df_raw
    experiments) and writes:
      - PX_DATES: one row per experiment of that build: uniquecontrast, molecule_batch_id, plate, date, source
        (FBX | df_raw). The database has no date yet; load_rds_fbx keeps the FBX experiments listed here.
      - PX_ARCHIVE_DIR/fbx_measure.parquet + fbx_report.parquet: the FBX rows of the experiments that the
        database lacks. fbx_msscore.parquet: the MSSCORE rows of the experiments that the main tables lack, also
        the glues ones (load_rds_fbx computes the MSSCORE of the main table only).
    Prints counts only: the compound batches that ran on plates of different dates (so PX_DATES keys on the
    experiment, not the batch) and the deviation of fn.ms_score_rows from the archived MSSCORE export.
    param class params: PARAMS instance (FBX_DIR, VALIDATION_DIR, DFRAW_PATH, RDS_*, PX_DATES, PX_ARCHIVE_DIR)
    param dict plate2date: {plate: 'YYYY-MM-DD'} of the last Dropbox build (IFACE_DIR/plate2date.json)
    return DataFrame: the PX_DATES table
    """
    data = DATA()
    data.load_dropbox_fbx(params)

    # one row per experiment: FBX wins over df_raw on a shared contrast, as in combine_datasets
    fbx = (data.FBX_REPORT[['uniquecontrast', 'srbnumber', 'plate']].drop_duplicates('uniquecontrast')
           .rename(columns={'srbnumber': 'molecule_batch_id'}).assign(source='FBX'))
    dr = (pd.read_parquet(params.DFRAW_PATH, columns=['uniquecontrast', 'MoleculeBatchID', 'MSPlate'])
          .drop_duplicates('uniquecontrast').rename(columns={'MoleculeBatchID': 'molecule_batch_id', 'MSPlate': 'plate'})
          .assign(source='df_raw'))
    dates = pd.concat([fbx, dr[~dr['uniquecontrast'].isin(set(fbx['uniquecontrast']))]], ignore_index=True)
    dates['date'] = dates['plate'].astype(str).map(plate2date)
    os.makedirs(os.path.dirname(params.PX_DATES) or '.', exist_ok=True)
    dates[['uniquecontrast', 'molecule_batch_id', 'plate', 'date', 'source']].to_csv(params.PX_DATES, index=False)

    # the FBX rows of the experiments that the database lacks; the msscore rows of those that the main tables lack
    read = functools.partial(fn.rds_table, host=params.RDS_HOST, database=params.RDS_DATABASE,
                             cred_file=params.RDS_CRED_FILE, ca_bundle=params.RDS_CA_BUNDLE)
    main = set(read('px_fbx_report')['uniquecontrast'])
    in_db = main | set(read('px_fbx_glues_report')['uniquecontrast'])
    os.makedirs(params.PX_ARCHIVE_DIR, exist_ok=True)
    arc = {k: df[~df['uniquecontrast'].isin(main if k == 'msscore' else in_db)].reset_index(drop=True)
           for k, df in (('measure', data.FBX_MEASURE), ('report', data.FBX_REPORT), ('msscore', data.FBX_MSSCORE))}
    for k, df in arc.items():
        df.to_parquet(os.path.join(params.PX_ARCHIVE_DIR, f'fbx_{k}.parquet'), index=False)

    # checks (counts only): batches with several dates; the ms score formula on the archived experiments
    multi = dates.dropna(subset=['date']).groupby('molecule_batch_id')['date'].nunique().gt(1).sum()
    glues = arc['msscore']['uniquecontrast'].isin(in_db - main)
    m = arc['msscore'][~glues].merge(fn.ms_score_rows(arc['measure']), on=['uniquecontrast', 'genes', 'pg'], how='outer',
                                     suffixes=('', '_f'), indicator=True)
    both = m['_merge'] == 'both'
    print(f'> PX_DATES: {len(dates):,} experiments ({int((dates["source"] == "FBX").sum()):,} FBX), '
          f'{int(dates["date"].isna().sum()):,} without a date | {int(multi):,} batches on plates of different dates')
    print(f'> archive: {arc["report"]["uniquecontrast"].nunique():,} FBX experiments that the database lacks, '
          f'{len(arc["measure"]):,} measure rows | ms score formula vs MSSCORE export: {int(both.sum()):,} rows equal to '
          f'{(m.loc[both, "ms_score"] - m.loc[both, "ms_score_f"]).abs().max():.1e}, '
          f'{int((m["_merge"] == "left_only").sum()):,} export-only, {int((m["_merge"] == "right_only").sum()):,} formula-only '
          f'| + {int(glues.sum()):,} msscore rows of {arc["msscore"].loc[glues, "uniquecontrast"].nunique():,} glues experiments')
    return dates


# ~~~~~~~~~~~~~~~~~~~~~~
# MAIN
# ~~~~~~~~~~~~~~~~~~~~~~

if __name__ == "__main__":
    
    ap = argparse.ArgumentParser(description="Build/update the Px 3D interface.")
    ap.add_argument('--config', default='config/config.yaml', help="path to the YAML config")
    ap.add_argument('--output_dir', default='output',
                    help="base dir for the HTML + volcanoes (interfaces/ is created under it), or the config "
                         "PUBLISH_URL: build in PUBLISH_STAGE_DIR, then publish to AWS (S3, then the EC2)")
    ap.add_argument('--show_plate', default=None,
                    help='comma-separated Plates-filter blocks to default-tick: YYYYMMDD dates or '
                         '"validation YYYYMMDD", overriding config SHOW_PLATE; e.g. "20260812, validation 20261006". '
                         'Empty -> latest date only.')
    args = ap.parse_args()

    ## params:
    params = PARAMS(args.config)
    params.load_params()
    if args.show_plate is not None:   # CLI overrides config SHOW_PLATE
        params.SHOW_PLATE = [s.strip() for s in args.show_plate.split(',') if s.strip()]
    local_dir, publish = resolve_output_dir(args.output_dir, params)
    targets = aws_publish_targets(params) if publish else None   # check AWS access before the long build

    ## data:
    data = DATA()
    data.load_chemical_lib_df(params)
    data.download_cdd_pngs(params)   # refresh SRB_PNG_DIR from CDD when UPDATE_PNGS=true (else no-op)
    data.load_old_df(params)
    data.load_new_df(params)
    data.get_contaminants_and_controls(params)
    data.get_gene_research(params)

    ## output:
    output = OUTPUT()
    output.combine_datasets(data, params)
    output.get_de_validated(data, params)
    output.get_iface(data, params)
    output.build_interface(data, params, local_dir)
    if publish:
        publish_interface(local_dir, params, targets)