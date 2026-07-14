"""Generate a small synthetic fixture mirroring the real Px data schema, for fast
testing of python/Px_interface.py without loading the 24.6M-row real df_raw.

Writes to <repo>/tmp by default:
  df_raw.parquet, MS.parquet, gene_sar.csv,
  fbx/<date>/<date>_FBX_{MEASURE,MSSCORE,REPORT}.csv   (2 tranches),
  serac_lib.csv, clean_proteomics.csv, px_2026052*_cddvault.csv, px_2026052*_db.csv,
  config.yaml   (minimal, relative paths -> the files above; no real values).

All IDs are fake (C_/G_/PG_), SMILES are public (CCO); only real *headers* informed
the schema. Run from the repo root:  python tests/make_synthetic.py [--out tmp]
"""
import os, itertools, argparse, json
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# shared dimensions — kept small so the whole DATA + combine_datasets flow runs in ~2s
N_GENE       = 300
GENES        = [f'G_{i:05d}' for i in range(N_GENE)]
PG_OF        = {g: f'PG_{i:05d}' for i, g in enumerate(GENES)}
COMPOUNDS    = [f'SRB-{i:07d}' for i in range(40)]   # real 'SRB-' format so the get_iface filter passes
PLATES       = [f'Pw{i:02d}' for i in range(4)]
ACTS         = ['High (>25)', 'Medium (11-25)', 'Low (2-10)', 'Single (1)', 'Silent']
FBX_TRANCHES = ['20260601', '20260616']
P            = 'MSData - Proteomics activities: '   # prefixed-export column prefix


def make_df_raw_ms(out, rng):
    df = pd.DataFrame(itertools.product(COMPOUNDS, PLATES, GENES),
                      columns=['compound', 'MSPlate', 'genes'])
    df['batch'] = '001'
    df['MoleculeBatchID'] = df['compound'] + '-' + df['batch']
    df['uniquecontrast'] = df['MoleculeBatchID'].str.replace('-', '.', regex=False) + '_vs_DMSO'
    df['pg'] = df['genes'].map(PG_OF)
    df['logfc'] = rng.normal(0, 1.5, len(df))
    df['pvalue'] = rng.uniform(1e-6, 1.0, len(df))
    df['adjpval'] = np.minimum(df['pvalue'] * rng.uniform(1, 5, len(df)), 1.0)
    df['significant'] = ((df['pvalue'] < 0.05) & (df['logfc'].abs() > 1)).astype(float)
    df = df[['MoleculeBatchID', 'MSPlate', 'genes', 'pg', 'logfc', 'pvalue',
             'adjpval', 'significant', 'uniquecontrast', 'compound', 'batch']]
    df.to_parquet(os.path.join(out, 'df_raw.parquet'), index=False)

    MS = pd.DataFrame([(c, float(rng.randint(0, 60)), o, rng.choice(ACTS),
                        pd.to_datetime(o.replace('MS', '')))
                       for o in ['MS20260429', 'MS20260520', 'MS20260529'] for c in COMPOUNDS],
                      columns=['compound', 'ndown', 'origin', 'activity', 'date'])
    MS.to_parquet(os.path.join(out, 'MS.parquet'), index=False)


def make_fbx_gene_sar(out, rng):
    gsub = GENES[:60]
    rid = itertools.count(1)
    for ti, date in enumerate(FBX_TRANCHES):
        d = os.path.join(out, 'fbx', date); os.makedirs(d, exist_ok=True)
        comps  = [f'SRB-{9000000 + ti*1000 + i:07d}' for i in range(12)]   # SRB-, disjoint per tranche
        plates = [f'Pw{ti}{p}' for p in range(2)]
        exps = [(f'{c}.001.{pl}_vs_DMSO', f'{c}-001', c, pl) for c in comps for pl in plates]
        rep = pd.DataFrame([{
            'id': next(rid), 'uniquecontrast': uc, 'srbnumber': srb, 'source': 'synthetic',
            'cell_line': 'HEK293', 'condition': 'WT', 'time': 24, 'concentration': 1.0,
            'controls': np.nan, 'plate': pl, 'nr_down': int(rng.randint(0, 40)),
            'activity': rng.choice(ACTS)} for uc, srb, c, pl in exps])
        meas, msc = [], []
        for uc, srb, c, pl in exps:
            for g in gsub:
                lf, pv = float(rng.normal(0, 1.5)), float(rng.uniform(1e-6, 1))
                meas.append({'id': next(rid), 'pg': PG_OF[g], 'genes': g, 'uniquecontrast': uc,
                             'logfc': lf, 'pvalue': pv, 'adjpval': float(min(pv * rng.uniform(1, 5), 1.0)),
                             'significant': int(pv < 0.05 and abs(lf) > 1), 'plate': pl})
                # realistic MS score: bottom-heavy cluster (most 0-25) with a rare high outlier,
                # so the auto z-axis range mimics real data (~0-210) instead of a flat 0-100.
                _ms = float(min(rng.uniform(150, 210) if rng.uniform() < 0.02 else rng.exponential(12), 212))
                msc.append({'id': next(rid), 'uniquecontrast': uc, 'plate': pl, 'genes': g, 'pg': PG_OF[g],
                            'ms_score': _ms, 'ms_score_percent': float(rng.uniform(0, 1)),
                            'activity': rng.choice(ACTS), 'association_score': float(rng.uniform(0, 1)),
                            'genetic_score': float(rng.uniform(0, 1)), 'literature_score': float(rng.uniform(0, 1))})
        pd.DataFrame(meas).to_csv(os.path.join(d, f'{date}_FBX_MEASURE.csv'), index=False)
        pd.DataFrame(msc).to_csv(os.path.join(d, f'{date}_FBX_MSSCORE.csv'), index=False)
        rep.to_csv(os.path.join(d, f'{date}_FBX_REPORT.csv'), index=False)

    pd.DataFrame({
        'gene': GENES, 'R2': rng.uniform(0, 0.6, N_GENE), 'nullR2': rng.uniform(0, 0.1, N_GENE),
        'n': rng.randint(20, 500, N_GENE), 'pearson_r': rng.uniform(-1, 1, N_GENE),
        'pearson_p': rng.uniform(0, 1, N_GENE), 'spearman_r': rng.uniform(-1, 1, N_GENE),
        'spearman_p': rng.uniform(0, 1, N_GENE),
    }).to_csv(os.path.join(out, 'gene_sar.csv'), index=False)


def make_sources_config(out, out_rel, rng):
    cmp2mbid = {c: f'{c}-001' for c in COMPOUNDS}
    n = len(COMPOUNDS)
    pd.DataFrame({
        'compound': COMPOUNDS, 'smiles': ['CCO'] * n,        # public SMILES (ethanol)
        'Px_repetition(yes/no)':       rng.choice(['yes', 'no', ''], n),
        'Px_validated_WT(yes/no)':     rng.choice(['yes', 'no', ''], n),
        'Px_Ligase_dependent(yes/no)': rng.choice(['yes', 'no', ''], n),
        'Px_NameLigase_dependent':     rng.choice(['CRBN', 'VHL', ''], n),
        'Px_Target_info': '',
        # half the compounds carry a fake target ('<gene> degrader'); '' -> NaN on read
        'Px_Target_interest': [f'{GENES[i]} degrader' if i % 2 == 0 else '' for i in range(n)],
    }).to_csv(os.path.join(out, 'serac_lib.csv'), index=False)

    def src(comps, prefixed):
        pre  = P if prefixed else ''
        bcol = 'Batch Molecule-Batch ID' if prefixed else 'Molecule-Batch ID'
        ccol = (P + 'Concentration (uM)') if prefixed else 'Concentration'
        return pd.DataFrame([{
            bcol: cmp2mbid[c], pre + 'MSPlate': pl, ccol: float(rng.choice([0.1, 1.0, 10.0])),
            pre + 'Cmpd Activity': rng.choice(ACTS), pre + 'Nr. Down': int(rng.randint(0, 40)),
            pre + 'Cell line': 'HEK293', pre + 'Sample Condition': 'WT',
        } for c in comps for pl in PLATES])

    src(COMPOUNDS,      True ).to_csv(os.path.join(out, 'clean_proteomics.csv'), index=False)
    src(COMPOUNDS[:20], True ).to_csv(os.path.join(out, 'px_20260520_cddvault.csv'), index=False)
    src(COMPOUNDS[20:], False).to_csv(os.path.join(out, 'px_20260529_cddvault.csv'), index=False)
    pd.DataFrame({'MSPlate': ['Pw02']}).to_csv(os.path.join(out, 'px_20260520_db.csv'), index=False)
    pd.DataFrame({'MSPlate': ['Pw03']}).to_csv(os.path.join(out, 'px_20260529_db.csv'), index=False)
    # contaminants list (only 'Molecule Name' is read); fake compounds from the synthetic set
    pd.DataFrame({'Molecule Name': COMPOUNDS[2:5]}).to_csv(os.path.join(out, 'contaminants.csv'), index=False)

    # gene degradation research: a list of one record per gene (keyed by gene_name)
    research = [{
        'gene_name': g,
        'target_class': str(rng.choice(['kinase', 'ligase', 'transcription factor', 'other'])),
        'confidence': str(rng.choice(['High', 'Med', 'Low'])),
        'depmap_dependency': str(rng.choice(['Selective', 'Non-essential', 'Essential'])),
        'biology_rationale': 'synthetic rationale',
        'degrader_feasibility': str(rng.choice(['High', 'Medium', 'Low'])),
        'degrader_vs_inhibitor_rationale': 'synthetic',
        'existing_degraders': str(rng.choice(['none', 'preclinical'])),
        'lof_therapeutic_benefit': 'synthetic',
        'opentargets_top_indications': 'synthetic indication',
        'safety_flags': 'none',
        'sources': ['synthetic'],
    } for g in GENES]
    json.dump(research, open(os.path.join(out, 'gene_research.json'), 'w'))

    # OpenTargets cache: only target_symbol / overall_score / therapeutic_areas are read downstream
    AREAS = ['cancer or benign tumor', 'hematologic disease', 'cardiovascular disease',
             'immune system disease', 'nervous system disease', 'nutritional or metabolic disease']
    pd.DataFrame({
        'target_symbol': GENES,
        'overall_score': rng.uniform(0, 1, N_GENE),
        'therapeutic_areas': [rng.choice(AREAS) for _ in GENES],
    }).to_parquet(os.path.join(out, 'ot_cache.parquet'), index=False)
    # pharma patent / BMS pipeline gene lists (subsets of GENES) -> disease_area pharma/BMS tags
    pd.DataFrame({'gene': GENES[:10]}).to_csv(os.path.join(out, 'pharma_patent.csv'), index=False)
    pd.DataFrame({'hgnc_symbol': GENES[10:18]}).to_csv(os.path.join(out, 'bms_genes.csv'), index=False)

    rel = lambda f: os.path.join(out_rel, f)
    # minimal config: only the keys Px_interface reads, all paths relative to repo root
    cfg = {
        'DFRAW_PATH': rel('df_raw.parquet'), 'MS_PATH': rel('MS.parquet'),
        'FBX_DIR': rel('fbx'), 'GENE_SAR_OUT': rel('gene_sar.csv'),
        'CHEMLIB_OVERWRITE': False, 'CHEMLIB_PATH': rel('serac_lib.csv'),
        'CLEAN_PROTEOMICS_PATH': rel('clean_proteomics.csv'),
        'PX_20260520_CDDVAULT': rel('px_20260520_cddvault.csv'),
        'PX_20260529_CDDVAULT': rel('px_20260529_cddvault.csv'),
        'PX_20260520_DB': rel('px_20260520_db.csv'),
        'PX_20260529_DB': rel('px_20260529_db.csv'),
        'PLATE_DATE_OVERRIDES': {},
        'CONTROLS': COMPOUNDS[:2],                     # fake control compounds
        'CONTAMINANTS': rel('contaminants.csv'),
        'GENE_RESEARCH': rel('gene_research.json'),
        'OT_CACHE': rel('ot_cache.parquet'),
        'PHARMA_PATENT_CSV': rel('pharma_patent.csv'),
        'BMS_GENES': rel('bms_genes.csv'),
        'PHARMA_R2_CUTOFF': 0.08,
        'IFACE_DIR': rel('interface'),
        'IFACE_OVERWRITE': True,
        'ACTIVE_C': '#008bfb',                         # 'pharma' dot colour
        'SRB_PNG_DIR': rel('srb_png'),                 # no real PNGs -> RDKit-render from CCO smiles
    }
    yaml.safe_dump(cfg, open(os.path.join(out, 'config.yaml'), 'w'), sort_keys=False)


def main(out_rel='tmp', seed=42):
    out = os.path.join(REPO_ROOT, out_rel)
    os.makedirs(out, exist_ok=True)
    rng = np.random.RandomState(seed)
    make_df_raw_ms(out, rng)
    make_fbx_gene_sar(out, rng)
    make_sources_config(out, out_rel, rng)
    print(f'> synthetic fixture -> {out}/  (config: {out_rel}/config.yaml)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Generate the synthetic Px test fixture.')
    ap.add_argument('--out', default='tmp', help='output dir, relative to repo root (default: tmp)')
    args = ap.parse_args()
    main(args.out)
