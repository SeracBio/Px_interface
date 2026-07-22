import os, sys
# self-locate repo root (parent of this file's dir) so `import python.functions` and
# relative paths (config/, output/) resolve no matter where the script is launched from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)
sys.path.insert(0, os.path.expanduser('~/CDD_Vault_API/python'))  # CDD Vault API (get_df)

import re, gc, ctypes, json, time, importlib, argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from datetime import date
from rdkit import Chem
import yaml
import joblib
from tqdm import tqdm
from types import SimpleNamespace
tqdm.pandas()

# local modules
import python.functions as fn
from get_library import get_df   # CDD Vault collection export


def _fbx_csv(tranche, kind):
    """Path to the one *FBX_<kind>*.csv in a tranche folder (tolerates a _02 re-export suffix)."""
    return os.path.join(tranche, next(f for f in os.listdir(tranche)
                                      if f'FBX_{kind}' in f and f.endswith('.csv')))

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

class PARAMS():
    def __init__(self, config_path):
        self.config_path = config_path
    def load_params(self):
        # read the YAML and expose every key as an attribute (e.g. params.DFRAW_PATH)
        with open(self.config_path) as f:
            self.__dict__.update(yaml.safe_load(f))
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

    def load_old_df(self, params):
        """
        -Extract df_raw which contains the logfc and p-value associated with each gene and compound
        -Extract MS which contain compound level info e.g. activity -> single...
        param class params: the params class
        return None: 
        """
        # load df-raw and process
        self.df_raw = pd.read_parquet(params.DFRAW_PATH)
        self.df_raw = self.df_raw.dropna()
        self.df_raw['-log10(p-value)'] = -np.log10(self.df_raw['pvalue'])
        self.df_raw['ms_score'] = (-self.df_raw['-log10(p-value)'] * self.df_raw['logfc']).clip(lower=0.0, upper=100.0)
        self.df_raw = self.df_raw.sort_values('ms_score',ascending=False)
        self.df_ms = self.df_raw[self.df_raw['significant']==1].groupby(['genes','MSPlate']).first().reset_index()

        # load MS data
        self.MS = pd.read_parquet(params.MS_PATH)
        print(f'> df_raw {self.df_raw.shape} | MS {self.MS.shape}')

    def load_new_df(self, params):
        """
        -Load + concat every FBX tranche under params.FBX_DIR into the unified FBX tables
         (FBX_MEASURE / FBX_MSSCORE / FBX_REPORT). Tranche folders are auto-discovered: any
         date-named subdir holding *FBX_<KIND>*.csv, so a new tranche just needs its folder.
        -Load the per-gene SAR R2 table and build the uniquecontrast -> compound map.
        param class params: the params class
        return None:
        """
        # auto-discover tranche folders (date-named, holding an FBX_REPORT csv)
        self.FBX_TRANCHES = sorted(
            t for t in (os.path.join(params.FBX_DIR, d) for d in os.listdir(params.FBX_DIR))
            if os.path.isdir(t) and os.path.basename(t)[:8].isdigit()
            and any('FBX_REPORT' in f for f in os.listdir(t)))

        def _load_fbx(kind):
            return pd.concat([pd.read_csv(_fbx_csv(t, kind)) for t in self.FBX_TRANCHES],
                             ignore_index=True)

        self.FBX_MEASURE  = _load_fbx('MEASURE')
        self.FBX_MSSCORE  = _load_fbx('MSSCORE')
        self.FBX_REPORT   = _load_fbx('REPORT')
        self.target2R2_df = pd.read_csv(params.GENE_SAR_OUT).rename(columns={'gene': 'genes'})

        # uniquecontrast -> compound (SRB-XXXXXXX, batch stripped); reused by every combine
        _p = self.FBX_REPORT['srbnumber'].astype(str).str.split('-', n=2, expand=True)
        self.uc2compound = (self.FBX_REPORT.assign(compound=_p[0] + '-' + _p[1])
                            .drop_duplicates('uniquecontrast').set_index('uniquecontrast')['compound'])

        print(f'> FBX: {len(self.FBX_TRANCHES)} tranches | MEASURE {len(self.FBX_MEASURE):,} rows | '
              f'MSSCORE {len(self.FBX_MSSCORE):,} | REPORT {len(self.FBX_REPORT):,} '
              f'({self.FBX_REPORT["uniquecontrast"].nunique():,} experiments)')
        
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
        param class data: DATA instance (df_raw, df_ms, MS, FBX_*, uc2compound, FBX_TRANCHES)
        param class params: PARAMS instance (source proteomics paths, PLATE_DATE_OVERRIDES)
        return None:
        """
        ## 1. MEASURE = df_raw UNION FBX_MEASURE (FBX wins on shared uniquecontrasts)
        _dr_uc_set = set(data.df_raw['uniquecontrast'].astype(str))   # reused by the MEASURE + REPORT unions
        _cols = ['compound', 'genes', 'pg', 'plate', 'uniquecontrast',
                 'logfc', 'pvalue', 'adjpval', 'significant']
        fbx_std = (data.FBX_MEASURE.assign(compound=data.FBX_MEASURE['uniquecontrast'].map(data.uc2compound))
                   .reindex(columns=_cols).assign(source='FBX'))
        dr_std = data.df_raw.rename(columns={'MSPlate': 'plate'}).reindex(columns=_cols).assign(source='df_raw')
        _shared_uc = set(data.FBX_MEASURE['uniquecontrast']) & _dr_uc_set
        self.measure = pd.concat([fbx_std, dr_std[~dr_std['uniquecontrast'].astype(str).isin(_shared_uc)]],
                                 ignore_index=True)
        print(f'> combined MEASURE: {len(self.measure):,} rows | '
              f'{self.measure["uniquecontrast"].nunique():,} experiments | '
              f'{self.measure["compound"].nunique():,} compounds | {self.measure["genes"].nunique():,} genes')

        ## 2. MS-SCORE = df_ms UNION FBX_MSSCORE per (gene,plate) (FBX wins on shared keys).
        # FBX_MSSCORE was loaded fresh in load_new_df; drop the noisy plates here.
        _FBX_MS = data.FBX_MSSCORE[~data.FBX_MSSCORE['plate'].isin(['Plate12', 'Plate15', 'Plate23'])]
        _cols = ['genes', 'plate', 'uniquecontrast', 'compound', 'pg', 'ms_score',
                 'association_score', 'genetic_score', 'literature_score', 'activity',
                 'logfc', 'pvalue', 'significant']
        fbx_gp = (_FBX_MS.sort_values('ms_score', ascending=False)
                  .groupby(['genes', 'plate'], as_index=False).first()
                  .assign(compound=lambda d: d['uniquecontrast'].map(data.uc2compound))
                  .reindex(columns=_cols).assign(source='FBX'))
        df_ms_std = data.df_ms.rename(columns={'MSPlate': 'plate'}).reindex(columns=_cols).assign(source='df_raw')
        _fbx_keys = set(fbx_gp[['genes', 'plate']].itertuples(index=False, name=None))
        _keep = ~df_ms_std.set_index(['genes', 'plate']).index.isin(_fbx_keys)
        self.mscore = pd.concat([fbx_gp, df_ms_std[_keep]], ignore_index=True)
        print(f'> combined MS-SCORE: {len(self.mscore):,} (gene,plate) rows '
              f'(FBX {len(fbx_gp):,}, df_raw {int(_keep.sum()):,})')

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
            m = {k: v for k, v in m.items() if k in pd.read_csv(path, nrows=0).columns}
            return pd.read_csv(path, usecols=list(m)).rename(columns=m)
        SRC = pd.concat([_load_src(params.CLEAN_PROTEOMICS_PATH, True),
                         _load_src(params.PX_20260520_CDDVAULT, True),
                         _load_src(params.PX_20260529_CDDVAULT, False)], ignore_index=True)
        SRC = (SRC.dropna(subset=['batch', 'plate']).drop_duplicates(['batch', 'plate'])
               .rename(columns={'batch': 'MoleculeBatchID', 'plate': 'MSPlate'}))
        _cols = ['uniquecontrast', 'compound', 'plate', 'concentration', 'activity',
                 'nr_down', 'cell_line', 'condition']
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

        ## 4. per-plate experiment DATE (tranche-derived) -> date-based plate filtering.
        _DFRAW_DATE_SRC = [
            ('2026-04-29', params.CLEAN_PROTEOMICS_PATH, 'MSData - Proteomics activities: MSPlate'),
            ('2026-05-20', params.PX_20260520_DB, 'MSPlate'),
            ('2026-05-29', params.PX_20260529_DB, 'MSPlate'),
        ]
        self.plate2date = {}
        for _d, _p, _c in _DFRAW_DATE_SRC:
            self.plate2date.update({pl: _d for pl in pd.read_csv(_p, usecols=[_c], dtype=str)[_c].dropna().unique()})
        for _t in data.FBX_TRANCHES:   # FBX last -> wins on shared plates; date from the folder name
            _d = pd.to_datetime(os.path.basename(_t)[:8]).strftime('%Y-%m-%d')
            self.plate2date.update({pl: _d for pl in pd.read_csv(_fbx_csv(_t, 'REPORT'),
                                                                 usecols=['plate'])['plate'].dropna().astype(str).unique()})
        self.plate2date.update(params.PLATE_DATE_OVERRIDES)
        for _df in (self.measure, self.mscore, self.report):
            _df['date'] = pd.to_datetime(_df['plate'].astype(str).map(self.plate2date))
        print(f"> plate dates: {len(self.plate2date)} plates mapped | report spans "
              f"{self.report['date'].min():%Y-%m-%d} .. {self.report['date'].max():%Y-%m-%d}")
        
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
            meas = self.measure[~self.measure['plate'].isin(DROP_PLATES)].copy()
            _pmin = self.measure.loc[self.measure['pvalue'] > 0, 'pvalue'].min()
            meas.loc[meas['pvalue'].eq(0.0), 'pvalue'] = _pmin

            # --- gene-level axes: x=R2 (SAR full-genome), y=OpenTargets association + top area, z=ms_score ---
            R2_df = data.target2R2_df[['genes', 'R2']]
            ot_df = pd.read_parquet(params.OT_CACHE)
            assoc = ot_df.groupby('target_symbol')['overall_score'].max().rename('association_score')
            _rank = {a: i for i, a in enumerate(PRIORITY_DISEASE_AREAS)}
            _areas = (ot_df[['target_symbol', 'overall_score', 'therapeutic_areas']]
                    .assign(area=lambda d: d['therapeutic_areas'].fillna('').str.split('|')).explode('area'))
            _areas = _areas[_areas['area'].isin(_rank)].copy()
            _areas['_rank'] = _areas['area'].map(_rank)
            _top_area = (_areas.sort_values(['target_symbol', '_rank', 'overall_score'], ascending=[True, True, False])
                        .drop_duplicates('target_symbol', keep='first')
                        .rename(columns={'target_symbol': 'gene', 'area': 'disease_area'})[['gene', 'disease_area']])
            ms_gene = self.mscore.groupby('genes')['ms_score'].max().rename('ms_score')

            # --- dots: one per gene over the mscore universe; R2/association left-joined (missing -> 0.0) ---
            iface_df = (ms_gene.reset_index()
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
            rep = self.report[['uniquecontrast', 'compound', 'plate', 'concentration', 'activity']].drop_duplicates('uniquecontrast')
            _hit = meas.loc[(meas['significant'] == 1) & (meas['logfc'] < 0), ['genes', 'uniquecontrast', 'logfc', 'pvalue']]
            hits = _hit.merge(rep, on='uniquecontrast', how='left').rename(columns={'genes': 'gene'})
            hits = hits[hits['compound'].notna() & hits['compound'].str.startswith('SRB-')]
            # Per-(gene,compound,plate) MS score for the slider, taken from the SAME source as the
            # plotted z (mscore): FBX_MSSCORE per experiment, falling back to df_raw (FBX wins on
            # shared uniquecontrasts). Same scale as the gene-max z, so a dot stays put while the
            # slider filters each of its compound experiments by that experiment's own MS score.
            _shared_uc = set(data.FBX_MSSCORE['uniquecontrast'].astype(str))
            ms_per_uc = (pd.concat([data.FBX_MSSCORE[['genes', 'uniquecontrast', 'ms_score']],
                                    data.df_raw.loc[~data.df_raw['uniquecontrast'].astype(str).isin(_shared_uc),
                                                    ['genes', 'uniquecontrast', 'ms_score']]])
                         .dropna(subset=['ms_score'])
                         .groupby(['genes', 'uniquecontrast'])['ms_score'].max())
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
            _measured = set(zip(meas['genes'], meas['uniquecontrast']))          # (gene, contrast) present
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
                _mean_logfc = meas.dropna(subset=['logfc']).groupby(['genes', 'uniquecontrast'])['logfc'].mean()
                add_df = pd.DataFrame(_add)
                add_df['logfc'] = [_mean_logfc.get((g, u)) for g, u in zip(add_df['gene'], add_df['uniquecontrast'])]
                add_df = (add_df.merge(rep[['uniquecontrast', 'activity']].drop_duplicates('uniquecontrast'), on='uniquecontrast', how='left')
                                .merge(n_genes, on='uniquecontrast', how='left')
                                .merge(chemlib, on='compound', how='left'))
                add_df['molecule_batch_id'] = add_df['uniquecontrast'].map(_uc2mbid)
                _cm = add_df['molecule_batch_id'].isna()
                _cp = add_df.loc[_cm, 'uniquecontrast'].str.split('_vs_').str[0].str.replace('.', '-', regex=False)
                _cv = [p.startswith(c) for p, c in zip(_cp, add_df.loc[_cm, 'compound'].astype(str))]
                add_df.loc[_cm, 'molecule_batch_id'] = _cp.where(pd.Series(_cv, index=_cp.index))
                add_df['is_completion'] = True
                add_df['ms_score'] = float('nan')   # completion rows (gene not significant here) bypass MS filtering
                compounds_df = pd.concat([compounds_df, add_df[compounds_df.columns]], ignore_index=True)
            print(f'> validation-stem completion: added {len(_add):,} ride-along condition rows '
                  f'across {len({(a["gene"], a["compound"]) for a in _add}):,} (gene,compound) pairs')

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
        # Plates filter starts with only the latest tranche's plates ticked; untick to widen.
        _latest_date = max(self.plate2date.values())
        PLATE_DEFAULTS = sorted(p for p, d in self.plate2date.items() if d == _latest_date)
        print(f'> Plates default-ticked: {len(PLATE_DEFAULTS)} plate(s) on latest date {_latest_date}')
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
            plate_validation_suffixes=getattr(params, 'VALIDATION_PLATE_SUFFIXES', ('WT', 'MLN', 'KO')),  # …WT/MLN/KO stems, shown side by side
            panels=_panels_in, return_panels=True,  # skip/cache the compound-panel build
            volcano_source=self.meas, volcano_key='uniquecontrast', page_size=5,
            png_dir=params.SRB_PNG_DIR,   # real compound PNGs from config; RDKit-render fallback when absent
            thumb_external=True,   # reference srb_png/<compound>.png next to the HTML (not inline base64)
            range_sliders=True, range_defaults={'x': 0.0, 'y': 0.0, 'z': 0.0}, # {SAR, OT, MS} sliders open fully; alt presets: {'x':0,'y':0,'z':30} or {'x':0.1,'y':0.5,'z':10}
            activity_defaults=['Single', 'Low'],   # open with only Single/Low activity compounds ticked
            control_compounds=data.control_compounds, control_default_on=False,  # hide controls by default
            contaminant_compounds=data.contaminants, contaminant_default_on=False,  # hide contaminants by default
            gene_research=gene_research,
            validated_targets=self.validated_targets, devalidated_targets=self.devalidated_targets,  # Target validation (Y/N) tickboxes
            validated_label='FBXO31 dependent', devalidated_label='FBXO31 independent',
            validated_compounds=self.validated_compounds, devalidated_compounds=self.devalidated_compounds,  # Compound validation tickboxes
            compound_validated_label='FBXO31 dependent', compound_devalidated_label='FBXO31 independent',
            depmap_defaults=['Selective', 'Non-essential'], conf_defaults=['High', 'Med'],
            lof_defaults=['Yes'], validation_defaults=None,  # default ticked boxes on load (validation: all)
            volcano_significant=True, volcano_dir=os.path.join(output_dir, 'interfaces', 'volcanoes_px'),
            volcano_n_jobs=max(1, (os.cpu_count() or 8) - 2),  # use most cores for the volcano render
            volcano_xlim=(-8, 8), volcano_size_px=350,
            disease_area_colors=DISEASE_AREA_COLORS, nb_display=False,
            validation_colors=VALIDATION_COLORS, color_mode_default='V',  # V/D colour toggle; open in validation colouring
            size_buckets=getattr(params, 'GENE_SIZE_BUCKETS', [6, 8, 10, 12, 15, 20]),  # dot px by #significant-compounds (1,2,3,4,5,>5)
            ring_px=getattr(params, 'GENE_RING_PX', 4),   # thickness (px) of the dark ring drawn around each gene dot
            html_path=os.path.join(output_dir, 'interfaces', 'Serac_Px_interface.html'), # 20260612_3d_interface_PX_R2_assoc_ms.html
        )

        if params.IFACE_OVERWRITE or not os.path.exists(_panels_path):
            with open(_panels_path, 'w') as _f:
                json.dump(_panels, _f)
            print(f'> saved compound panels -> {_panels_path}')


# ~~~~~~~~~~~~~~~~~~~~~~
# MAIN
# ~~~~~~~~~~~~~~~~~~~~~~

if __name__ == "__main__":
    
    ap = argparse.ArgumentParser(description="Build/update the Px 3D interface.")
    ap.add_argument('--config', default='config/config.yaml', help="path to the YAML config")
    ap.add_argument('--output_dir', default='output', help="base dir for the HTML + volcanoes (interfaces/ is created under it)")
    args = ap.parse_args()

    ## params:
    params = PARAMS(args.config)
    params.load_params()

    ## data:
    data = DATA()
    data.load_chemical_lib_df(params)
    data.load_old_df(params)
    data.load_new_df(params)
    data.get_contaminants_and_controls(params)
    data.get_gene_research(params)

    ## output:
    output = OUTPUT()
    output.combine_datasets(data, params)
    output.get_de_validated(data, params)
    output.get_iface(data, params)
    output.build_interface(data, params, args.output_dir)