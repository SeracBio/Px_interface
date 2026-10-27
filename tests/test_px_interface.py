"""Unit tests for python/Px_interface.py, exercised on the synthetic fixture.

The heavy pipeline (DATA load -> combine -> get_iface, and the render) is run ONCE and
cached module-side (see _pipeline); each test only asserts on the cached results, so
assertions can be tweaked without paying the run cost again.

Run from the repo root:
    python -m unittest discover -s tests
or:
    python tests/test_px_interface.py
"""
import os, sys, json, unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # so `import make_synthetic` resolves

import pandas as pd
import make_synthetic                       # the fixture generator (tests/make_synthetic.py)
import python.Px_interface as px            # module under test (chdir's to repo root on import)

_CFG = 'tmp/config.yaml'
_CACHE = {}


def _pipeline():
    """Generate the synthetic fixture and run DATA + OUTPUT through get_iface once; cache the result.

    Returns (params, data, output) with every DATA/OUTPUT attribute populated. IFACE_OVERWRITE is
    True in the synthetic config, so get_iface builds + saves the render inputs (no frames freed).
    """
    if 'built' not in _CACHE:
        make_synthetic.main('tmp')                       # (re)build the fixture deterministically
        params = px.PARAMS(_CFG).load_params()
        data = px.DATA()
        data.load_chemical_lib_df(params)
        data.load_old_df(params)
        data.load_new_df(params)
        data.get_contaminants_and_controls(params)
        data.get_gene_research(params)
        output = px.OUTPUT()
        output.combine_datasets(data, params)
        output.get_de_validated(data, params)
        output.get_iface(data, params)
        _CACHE['built'] = (params, data, output)
    return _CACHE['built']


class TestDataPipeline(unittest.TestCase):
    """Assertions on the loaded/combined frames from the synthetic fixture."""

    @classmethod
    def setUpClass(cls):
        cls.params, cls.data, cls.output = _pipeline()

    def test_params_load(self):
        """load_params exposes every YAML key as an attribute with the right type."""
        # the paths the pipeline needs are present as attributes
        self.assertTrue(all(hasattr(self.params, k) for k in
                            ['DFRAW_PATH', 'MS_PATH', 'FBX_DIR', 'GENE_SAR_OUT', 'CHEMLIB_PATH']))
        # numeric config keys keep their YAML type (not strings)
        self.assertIsInstance(self.params.PHARMA_R2_CUTOFF, float)

    def test_serac_df(self):
        """load_chemical_lib_df returns compound+smiles and maps yes/no columns to {0,1,NaN}."""
        sdf = self.data.serac_df
        # required columns exist
        self.assertTrue({'compound', 'smiles'}.issubset(sdf.columns))
        # each yes/no column holds only 0/1 (plus NaN), never the raw 'yes'/'no' strings
        for c in ['Px_validated_WT(yes/no)', 'Px_Ligase_dependent(yes/no)', 'Px_repetition(yes/no)']:
            self.assertTrue(set(sdf[c].dropna().unique()).issubset({0, 1}))

    def test_df_raw_and_df_ms(self):
        """load_old_df derives ms_score (-log10(p) * -logfc, not clipped since 2026-10-26) and a per-(gene,plate) df_ms."""
        import numpy as np
        df = self.data.df_raw
        # ms_score and the -log10 helper column are created
        self.assertIn('ms_score', df.columns)
        self.assertIn('-log10(p-value)', df.columns)
        # ms_score is the formula, with no clip
        self.assertTrue(np.allclose(df['ms_score'], -np.log10(df['pvalue']) * -df['logfc']))
        # df_ms is unique per (genes, MSPlate)
        self.assertFalse(self.data.df_ms.duplicated(['genes', 'MSPlate']).any())

    def test_fbx_load(self):
        """load_new_df concats both tranches and builds a uniquecontrast->compound map."""
        # both synthetic tranches were discovered
        self.assertEqual(len(self.data.FBX_TRANCHES), 2)
        # the three FBX tables are non-empty
        self.assertTrue(all(len(t) > 0 for t in
                            [self.data.FBX_MEASURE, self.data.FBX_MSSCORE, self.data.FBX_REPORT]))
        # uc2compound is a Series keyed by uniquecontrast
        self.assertIsInstance(self.data.uc2compound, pd.Series)
        # target2R2_df carries the renamed 'genes' + 'R2' columns
        self.assertTrue({'genes', 'R2'}.issubset(self.data.target2R2_df.columns))

    def test_contaminants_and_controls(self):
        """get_contaminants_and_controls copies CONTROLS and reads the contaminants Molecule Name list."""
        # controls come straight from config
        self.assertEqual(self.data.control_compounds, self.params.CONTROLS)
        # contaminants is a non-empty list of compound names
        self.assertIsInstance(self.data.contaminants, list)
        self.assertTrue(len(self.data.contaminants) > 0)

    def test_gene_research(self):
        """get_gene_research loads a list of per-gene records keyed by gene_name."""
        gr = self.data.gene_research
        # it's a list of dicts, each carrying a gene_name
        self.assertIsInstance(gr, list)
        self.assertIn('gene_name', gr[0])

    def test_combine_measure(self):
        """combine_datasets MEASURE tags each row's source and unions FBX + df_raw-only."""
        m = self.output.measure
        # required columns incl. the source tag
        self.assertTrue({'compound', 'genes', 'plate', 'uniquecontrast', 'source'}.issubset(m.columns))
        # source is only ever FBX or df_raw
        self.assertTrue(set(m['source'].unique()).issubset({'FBX', 'df_raw'}))
        # a tranche-derived date was attached
        self.assertIn('date', m.columns)

    def test_combine_mscore(self):
        """MS-SCORE is one row per (genes, uniquecontrast) — per compound EXPERIMENT, not collapsed
        to the best compound per (genes, plate). A gene-plate may carry several compounds now."""
        ms = self.output.mscore
        # no duplicate (genes, uniquecontrast) keys survive the dedup
        self.assertFalse(ms.duplicated(['genes', 'uniquecontrast']).any())
        # per-compound granularity: at least one (genes, plate) carries more than one compound row
        self.assertGreater(int(ms.groupby(['genes', 'plate']).size().max()), 1)

    def test_combine_report(self):
        """REPORT is one row per uniquecontrast, with a plate date."""
        # uniquecontrast is unique
        self.assertFalse(self.output.report.duplicated('uniquecontrast').any())
        # date column present
        self.assertIn('date', self.output.report.columns)

    def test_plate2date(self):
        """plate2date maps every plate seen in report (no NaT dates in report)."""
        # every report row resolved to a date
        self.assertFalse(self.output.report['date'].isna().any())

    def test_de_validated(self):
        """get_de_validated splits serac_df targets/compounds by ligase dependency."""
        out = self.output
        # all four outputs are lists
        self.assertTrue(all(isinstance(x, list) for x in
                            [out.validated_targets, out.devalidated_targets,
                             out.validated_compounds, out.devalidated_compounds]))
        # validated compounds are exactly the ligase-dependent (==1) ones with a target
        sdf = self.data.serac_df
        expected = set(sdf[(sdf['Px_Ligase_dependent(yes/no)'] == 1)
                           & (sdf['Px_Target_interest'].notnull())]['compound'])
        self.assertEqual(set(out.validated_compounds), expected)

    def test_validated_target_file_override(self):
        """VALIDATED_TARGET_FILE replaces the CDD-derived validated_targets with the file's
        comma/whitespace-delimited gene list (upper-cased, deduped); empty/absent keeps CDD."""
        import tempfile
        from types import SimpleNamespace
        out = px.OUTPUT()
        # a comma + newline + whitespace mix, mixed case -> normalised to a sorted upper-case set
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write("brd4, myc\nCDK9  tp53,brd4\n")
            path = f.name
        out.get_de_validated(self.data, SimpleNamespace(VALIDATED_TARGET_FILE=path))
        # the override wins: exactly the file's genes, upper-cased and deduped
        self.assertEqual(out.validated_targets, ['BRD4', 'CDK9', 'MYC', 'TP53'])
        # empty/absent -> CDD-derived list is kept (non-empty from the fixture, not the override set)
        out2 = px.OUTPUT()
        out2.get_de_validated(self.data, SimpleNamespace(VALIDATED_TARGET_FILE=''))
        self.assertNotEqual(set(out2.validated_targets), {'BRD4', 'CDK9', 'MYC', 'TP53'})
        os.remove(path)

    def test_iface_df(self):
        """get_iface builds gene dots with R2/association filled to 0.0 (never NaN)."""
        idf = self.output.iface_df
        # the axis + colour columns exist
        self.assertTrue({'gene', 'ms_score', 'R2', 'association_score', 'disease_area'}.issubset(idf.columns))
        # missing R2 / association were filled with 0.0, so no NaN on the axes
        self.assertFalse(idf[['R2', 'association_score']].isna().any().any())

    def test_compounds_df_membership(self):
        """Every compound in compounds_df is present in serac_df (library filter applied)."""
        # no compound leaks into the viz that isn't in the library
        self.assertTrue(set(self.output.compounds_df['compound'])
                        .issubset(set(self.data.serac_df['compound'])))

    def test_compounds_df_ms_score(self):
        """Each compound experiment carries its OWN per-(gene,compound,plate) MS score, for the MS slider (pl[8]).
        Real (non-completion) hit rows always have a score; completion rows are null (a tested-target row gets its
        validation score). Since 2026-10-26 the dot's z follows gene_ms_score and stays fixed, so an experiment may
        score above its gene's z (an FBX score wins over a higher legacy one)."""
        cdf = self.output.compounds_df
        # the per-compound MS column reached compounds_df
        self.assertIn('ms_score', cdf.columns)
        comp = cdf['is_completion'].fillna(False)
        # every real hit row has a numeric MS score
        self.assertEqual(int(cdf.loc[~comp, 'ms_score'].isna().sum()), 0)
        # completion rows that are not tested-target rows carry no score
        self.assertTrue(cdf.loc[comp & ~cdf['is_target'], 'ms_score'].isna().all())
        # each dot names the source of its z
        self.assertTrue(set(self.output.iface_df['ms_source']) <= {'validation', 'FBX', 'April-May', ''})

    def test_validation_stem_completion(self):
        """Stem completion adds ride-along rows for measured-but-not-significant conditions,
        and omits conditions where the compound was never run.

        Fixture: SRB-0000006 on G_00000 is a significant-down hit on Pw10WT/Pw10MLN/Pw11WT,
        measured-but-not-significant on Pw10KO, and never run on Pw11KO. So compounds_df must
        carry Pw10KO as an is_completion row (gene shown in the insignificant zone) and must
        NOT carry any Pw11KO row.
        """
        cdf = self.output.compounds_df
        # the completion flag column exists
        self.assertIn('is_completion', cdf.columns)
        sel = cdf[(cdf['gene'] == 'G_00000') & (cdf['compound'] == 'SRB-0000006')]
        by_plate = sel.set_index('plate')['is_completion'].to_dict()
        # the significant conditions are present and flagged as real hits (not completion)
        for p in ['Pw10WT', 'Pw10MLN', 'Pw11WT']:
            self.assertIn(p, by_plate, f'{p} hit missing')
            self.assertFalse(bool(by_plate[p]), f'{p} should be a real hit, not completion')
        # Pw10KO is added as a completion row (compound run there, gene measured but not significant)
        self.assertTrue(bool(by_plate.get('Pw10KO')), 'Pw10KO completion row missing')
        # Pw11KO is omitted entirely (compound never run on that condition)
        self.assertNotIn('Pw11KO', by_plate, 'Pw11KO should be omitted (compound not run there)')

    def test_primary_screen_attach(self):
        """For a validation hit, the compound's broad primary-screen volcano is attached to the
        stem (flagged is_primary) so the hover-trace can link primary -> WT -> KO.

        Fixture: SRB-0000006 / G_00000 is a strong significant-down hit on the broad plate Pw00
        (non-validation) as well as on the Pw10/Pw11 validation stems. So compounds_df must carry
        exactly one is_primary row for that pair, on Pw00, and it must NOT be a WT/MLN/KO plate."""
        cdf = self.output.compounds_df
        # the primary flag column exists
        self.assertIn('is_primary', cdf.columns)
        sel = cdf[(cdf['gene'] == 'G_00000') & (cdf['compound'] == 'SRB-0000006')]
        prim = sel[sel['is_primary']]
        # exactly one primary volcano is attached, on the broad (non-validation) plate Pw00
        self.assertEqual(len(prim), 1)
        self.assertEqual(prim['plate'].iloc[0], 'Pw00')
        # it is a primary-screen plate, not a WT/MLN/KO validation condition
        self.assertFalse(prim['plate'].iloc[0].endswith(('WT', 'MLN', 'KO')))
        # the primary hit is a real significant row flagged in place (not a "not significant" ride-along)
        self.assertFalse(bool(prim['is_completion'].iloc[0]))
        # the validation-condition rows for the same pair are NOT flagged primary
        self.assertFalse(sel[sel['plate'].isin(['Pw10WT', 'Pw10KO', 'Pw11WT'])]['is_primary'].any())

    def test_iface_files_saved(self):
        """get_iface saves the four render inputs to IFACE_DIR."""
        d = self.params.IFACE_DIR
        # all four checkpoint files were written
        for f in ['iface_df.parquet', 'compounds_df.parquet', 'meas.parquet', 'plate2date.json']:
            self.assertTrue(os.path.exists(os.path.join(d, f)), f'{f} not saved')


class TestValidationOnlyTranche(unittest.TestCase):
    """A validation-only tranche ships MEASURE + REPORT but no MSSCORE (its MS scores were computed
    in an earlier tranche). _fbx_csv returns None for the absent kind, load_new_df skips it instead
    of raising StopIteration, and the whole pipeline still builds — the tranche's plates flow through
    via MEASURE/REPORT and its folder date lands in plate2date."""

    @classmethod
    def setUpClass(cls):
        import glob
        make_synthetic.main('tmp_valonly')
        cls.params = px.PARAMS('tmp_valonly/config.yaml').load_params()
        tranches = sorted(t for t in glob.glob(os.path.join(cls.params.FBX_DIR, '*')) if os.path.isdir(t))
        cls.latest = tranches[-1]
        cls.removed = glob.glob(os.path.join(cls.latest, '*FBX_MSSCORE*.csv'))
        for f in cls.removed:
            os.remove(f)                                  # drop MSSCORE -> simulate a validation-only drop-in
        data = px.DATA()
        data.load_chemical_lib_df(cls.params); data.load_old_df(cls.params)
        data.load_new_df(cls.params)                      # must NOT raise StopIteration
        data.get_contaminants_and_controls(cls.params); data.get_gene_research(cls.params)
        out = px.OUTPUT()
        out.combine_datasets(data, cls.params)
        out.get_de_validated(data, cls.params)
        out.get_iface(data, cls.params)                   # full build must complete
        cls.data, cls.out = data, out

    def test_precondition_msscore_removed(self):
        # the latest tranche really did have an MSSCORE for us to delete
        self.assertTrue(self.removed)

    def test_fbx_csv_none_for_absent_kind(self):
        files = self.data.TRANCHE_CSVS[self.latest]       # the tranche's csv list, as load_new_df saw it
        # absent kind -> None; the kinds the tranche does have still resolve to a path
        self.assertIsNone(px._fbx_csv(files, 'MSSCORE'))
        self.assertIsNotNone(px._fbx_csv(files, 'MEASURE'))
        self.assertIsNotNone(px._fbx_csv(files, 'REPORT'))

    def test_msscore_loaded_from_other_tranches(self):
        # MSSCORE is still populated from the tranche(s) that have it
        self.assertGreater(len(self.data.FBX_MSSCORE), 0)

    def test_latest_tranche_date_in_plate2date(self):
        # the validation-only tranche's plates still carry its folder date (sourced from REPORT)
        _d = os.path.basename(self.latest)[:8]
        self.assertIn(f'{_d[:4]}-{_d[4:6]}-{_d[6:8]}', set(self.out.plate2date.values()))


class TestDuplicateTrancheDate(unittest.TestCase):
    """Duplicate-tranche gotcha: plate2date is built by dict.update over the SORTED tranches, so when
    two date folders hold the SAME plate names the LATER folder wins the date (and the earlier date
    vanishes). Guards the diagnosed Plate 12/14 issue — identical plates copied under a newer date."""

    def test_later_tranche_wins_shared_plate_date(self):
        import glob, shutil
        make_synthetic.main('tmp_dup')
        params = px.PARAMS('tmp_dup/config.yaml').load_params()
        newdate = '20260701'                              # a LATER date carrying the SAME plates
        dst = os.path.join(params.FBX_DIR, newdate)
        if os.path.isdir(dst): shutil.rmtree(dst)         # clear any leftover from a prior run
        tranches = sorted(t for t in glob.glob(os.path.join(params.FBX_DIR, '*')) if os.path.isdir(t))
        src = tranches[-1]; src_name = os.path.basename(src)
        os.makedirs(dst)
        for f in os.listdir(src):
            shutil.copy(os.path.join(src, f), os.path.join(dst, f.replace(src_name, newdate)))
        data = px.DATA()
        data.load_chemical_lib_df(params); data.load_old_df(params); data.load_new_df(params)
        data.get_contaminants_and_controls(params); data.get_gene_research(params)
        out = px.OUTPUT(); out.combine_datasets(data, params)
        shared = pd.read_csv(px._fbx_csv(data.TRANCHE_CSVS[dst], 'REPORT'), usecols=['plate'])['plate'].dropna().astype(str).unique()
        # precondition: the duplicated tranche has plates to test
        self.assertGreater(len(shared), 0)
        _later = f'{newdate[:4]}-{newdate[4:6]}-{newdate[6:8]}'
        for p in shared:
            # every shared plate resolves to the LATER folder's date (last-tranche-wins)
            self.assertEqual(out.plate2date.get(p), _later)


class TestValidationDir(unittest.TestCase):
    """VALIDATION_DIR (2026-10-09): validation exports shaped like the real JSC_Demo folders load as
    tranches. Their folder date goes only to plates with no date yet (re-runs and primary-screen
    references keep their first date); a contrast in several tranches keeps only the newest copy; a
    per-target MSSCORE (no contrast column) is skipped; the full build completes."""

    @classmethod
    def setUpClass(cls):
        make_synthetic.main('tmp_vdir')
        cls.reexported = make_synthetic.add_validation_dir('tmp_vdir')
        params = px.PARAMS('tmp_vdir/config.yaml').load_params()
        data = px.DATA()
        data.load_chemical_lib_df(params); data.load_old_df(params)
        data.load_new_df(params)
        data.get_contaminants_and_controls(params); data.get_gene_research(params)
        out = px.OUTPUT()
        out.combine_datasets(data, params)
        out.get_de_validated(data, params)
        out.get_iface(data, params)                       # full build must complete
        out.build_interface(data, params, 'tmp_vdir/out_test')   # and the render (a no-plate row crashed it)
        cls.data, cls.out = data, out

    def test_volcano_colours_targets_only(self):
        """Input: validation exports whose report names the targets G_00000;G_00001. Expected: on a contrast with
        targets, volcano_sig = 1 only for a target with adjpval < 0.05 (any logfc); on the other contrasts it equals
        `significant`; `significant` does not change. Rationale: user decision 2026-10-23 (the reference validation
        volcano colours only the tested targets); the panel hits keep the platform flag."""
        import numpy as np
        m, rep = self.out.meas, self.out.report
        tg = rep.dropna(subset=['target']).drop_duplicates('uniquecontrast')
        pairs = {(u, g.strip()) for u, t in zip(tg['uniquecontrast'].astype(str), tg['target'].astype(str)) for g in t.split(';')}
        val = m['uniquecontrast'].isin(set(tg['uniquecontrast']))
        q = self.out.measure.loc[m.index, 'adjpval'].to_numpy(dtype=float)
        want = np.array([(u, g) in pairs for u, g in zip(m['uniquecontrast'].astype(str), m['genes'].astype(str))]) & (q < 0.05)
        # the fixture holds validation contrasts with targets, and significant non-targets that lose the colour
        self.assertTrue(val.any() and (m.loc[val, 'significant'].fillna(0) > m.loc[val, 'volcano_sig']).any())
        # on a contrast with targets: only the targets with adjpval < 0.05
        self.assertTrue((m.loc[val, 'volcano_sig'].to_numpy() == want[val.to_numpy()]).all())
        # on the other contrasts: the significant flag
        self.assertTrue((m.loc[~val, 'volcano_sig'].to_numpy() == m.loc[~val, 'significant'].fillna(0).to_numpy()).all())
        # the flag itself is the measure flag (the panel hits do not change)
        self.assertTrue((m['significant'].to_numpy() == self.out.measure.loc[m.index, 'significant'].to_numpy()).all())

    def test_validation_volcano_svg_colours_targets_only(self):
        """Input: the rendered fixture build. Expected: each validation volcano SVG (found by its data-keyed name)
        has coloured points (<title> tooltips) only on report targets; a primary volcano can colour other genes.
        Rationale: checks that build_interface passes volcano_sig to the renderer (user decision 2026-10-23)."""
        import re
        from python import functions as fn
        m, rep, vdir = self.out.meas, self.out.report, 'tmp_vdir/out_test/interfaces/volcanoes_px'
        tg = rep.dropna(subset=['target']).drop_duplicates('uniquecontrast')
        targets = {u: {g.strip() for g in t.split(';')} for u, t in zip(tg['uniquecontrast'].astype(str), tg['target'].astype(str))}
        vks = sorted(json.load(open(os.path.join(vdir, 'positions.json'))))
        vsrc = m.rename(columns={'uniquecontrast': 'compound'})
        ymax = fn._stem_shared_ymax(self.out.compounds_df, vsrc, ['WT', 'MLN', 'KO', 'BIND'])
        vh = fn._volcano_data_hash(vsrc, vks, sig_col='volcano_sig')
        sig_other = set(m.loc[(m['significant'] > 0) & ~m['genes'].astype(str).isin({'G_00000', 'G_00001'}),
                              'uniquecontrast'].astype(str))
        checked = 0
        for vk in (v for v in vks if v in targets and v in vh):
            path = os.path.join(vdir, fn._volcano_base_cache_fname(vk, (-8, 8), 350, '.svg', ymax=ymax.get(vk), data=vh[vk]))
            titles = set(re.findall(r'<title>([^<]+)</title>', open(path).read()))
            # the coloured points of a validation volcano are targets only
            self.assertTrue(titles <= targets[vk], vk)
            checked += vk in sig_other
        # some checked volcanoes hold significant non-targets (the old colour rule would show them)
        self.assertGreater(checked, 0)

    def test_render_completes(self):
        """The interface HTML is written for the fixture with validation folders."""
        # build_interface reached the end and wrote the page
        self.assertTrue(os.path.exists('tmp_vdir/out_test/interfaces/Serac_Px_interface.html'))

    def test_contrast_without_plate_dropped(self):
        """A contrast with no plate in the file or in its name is dropped at load (it cannot show)."""
        for t in (self.data.FBX_MEASURE, self.data.FBX_REPORT):
            # no row of the vs_DMSO contrast remains, and no row has a missing plate
            self.assertFalse(t['uniquecontrast'].str.contains('_vs_DMSO_DG37_A01').any())
            self.assertFalse(t['plate'].isna().any())

    def test_validation_tranches_found(self):
        """Both validation folders join the 2 FBX tranches; files without the FBX_ prefix resolve."""
        # 2 validation folders, 4 tranches in all
        self.assertEqual(len(self.data.VAL_TRANCHES), 2)
        self.assertEqual(len(self.data.FBX_TRANCHES), 4)
        # REPORT and MEASURE resolve in each validation folder (names like 202606025_REPORT.csv)
        self.assertTrue(all(px._fbx_csv(self.data.TRANCHE_CSVS[t], k) for t in self.data.VAL_TRANCHES for k in ('REPORT', 'MEASURE')))

    def test_new_plates_get_folder_date(self):
        """A plate first seen in a validation folder takes that folder's date."""
        p2d = self.out.plate2date
        # Pw20VD is new in the 0620 folder, Pw21VD in the 0625 folder
        self.assertEqual(p2d['Pw20VDKO'], '2026-06-20')
        self.assertEqual(p2d['Pw21VDWT'], '2026-06-25')

    def test_existing_plates_keep_first_date(self):
        """A re-run keeps its first validation date; a primary reference keeps its FBX date; a contrast
        with no plate in its name adds no plate."""
        p2d = self.out.plate2date
        # Pw20VDWT is in both folders: the earlier (first) date stays
        self.assertEqual(p2d['Pw20VDWT'], '2026-06-20')
        # Pw00 is the primary-screen reference: the 20260601 FBX tranche date stays
        self.assertEqual(p2d['Pw00'], '2026-06-01')
        # the vs_DMSO contrast has no plate in its name -> no 'nan' plate
        self.assertNotIn('nan', p2d)

    def test_newest_copy_wins(self):
        """The contrast re-exported by the later folder keeps only the later copy (logfc 3.0)."""
        rows = self.data.FBX_MEASURE[self.data.FBX_MEASURE['uniquecontrast'] == self.reexported]
        # the later folder's 60 gene rows only, not 60 + 60
        self.assertEqual(len(rows), 60)
        # with the later folder's values
        self.assertTrue((rows['logfc'] == 3.0).all())
        # one REPORT row for the contrast
        self.assertEqual(int((self.data.FBX_REPORT['uniquecontrast'] == self.reexported).sum()), 1)

    def test_validation_ms_score(self):
        """Input: the per-target MSSCORE of the validation folder (G_00000 50, G_00001 20). Expected: VAL_MSSCORE holds
        both, the two genes sit at those scores with the source 'validation', and the hover names it. Rationale: user
        decision 2026-10-26: a validation target sits at its validation score."""
        # the per-target file reached VAL_MSSCORE and VAL_ASSOC
        self.assertEqual(self.data.VAL_MSSCORE.to_dict(), {'G_00000': 50.0, 'G_00001': 20.0})
        self.assertEqual(self.data.VAL_ASSOC.to_dict(), {'G_00000': 0.5, 'G_00001': 0.2})
        z = self.out.iface_df.set_index('gene').loc[['G_00000', 'G_00001']]
        # the two targets sit at their validation score
        self.assertEqual(z['ms_score'].tolist(), [50.0, 20.0])
        self.assertEqual(set(z['ms_source']), {'validation'})
        # the hover names the source
        page = ''.join(open(f'tmp_vdir/out_test/interfaces/{f}').read()
                       for f in ('Serac_Px_interface.html', 'Serac_Px_interface_data.js'))
        self.assertIn('MS score=50.000 (validation)', page)

    def test_validation_ms_score_on_target_experiments(self):
        """Input: validation contrasts whose REPORT names G_00000;G_00001 (validation scores 50 and 20), on validation
        plates and on the primary reference plate Pw00. Expected: every hit or tested-target row of a named gene on
        such a contrast on a validation plate carries the gene's validation score; the stem completions stay null;
        a row on a primary plate keeps its own score. Rationale: the MS slider filters each experiment; a target's
        validation experiments use its validation score, the primary screen plates the primary score (2026-10-26)."""
        cdf, rep = self.out.compounds_df, self.out.report
        tg = rep.dropna(subset=['target']).drop_duplicates('uniquecontrast')
        pairs = {(g.strip(), u) for u, t in zip(tg['uniquecontrast'].astype(str), tg['target'].astype(str)) for g in t.split(';')}
        own = (pd.Series([(g, u) in pairs for g, u in zip(cdf['gene'].astype(str), cdf['uniquecontrast'].astype(str))], index=cdf.index)
               & cdf['plate'].astype(str).str.contains('(?:WT|MLN|KO|BIND)$', regex=True))
        rows = cdf[own & (~cdf['is_completion'] | cdf['is_target'])]
        # the fixture has such rows for both genes
        self.assertEqual(set(rows['gene']), {'G_00000', 'G_00001'})
        # each carries its gene's validation score
        self.assertTrue((rows['ms_score'] == rows['gene'].map({'G_00000': 50.0, 'G_00001': 20.0})).all())
        # stem completions (not tested-target rows) carry no score
        self.assertTrue(cdf.loc[own & cdf['is_completion'] & ~cdf['is_target'], 'ms_score'].isna().all())
        # rows on contrasts with no report target keep their own score (not 50 or 20 by this rule)
        other = cdf[~own & ~cdf['is_completion'] & cdf['gene'].isin(['G_00000', 'G_00001'])]
        self.assertGreater(len(other), 0)
        self.assertTrue(other['ms_score'].notna().all())
        # a tested-target row on the primary reference plate Pw00 does not take the validation score
        ref = cdf[cdf['is_target'] & ~cdf['plate'].astype(str).str.contains('(?:WT|MLN|KO|BIND)$', regex=True)
                  & cdf['gene'].isin(['G_00000', 'G_00001'])]
        self.assertGreater(len(ref), 0)
        self.assertFalse((ref['ms_score'] == ref['gene'].map({'G_00000': 50.0, 'G_00001': 20.0})).any())

    def test_per_target_msscore_skipped(self):
        """The per-target MSSCORE (target/msscore, no contrast) does not reach FBX_MSSCORE."""
        # no column of the per-target file leaks in
        self.assertNotIn('msscore', self.data.FBX_MSSCORE.columns)
        # every MSSCORE row still has a contrast
        self.assertFalse(self.data.FBX_MSSCORE['uniquecontrast'].isna().any())

    def test_every_tested_target_in_panel(self):
        """Every (target gene, contrast) of the validation REPORTs, with the gene measured, has a panel row
        (a hit, a stem completion or a target row). Before 2026-10-12 a gene that was not significant in
        any condition of a stem had none, so its negative result did not show."""
        rep = self.data.FBX_REPORT.dropna(subset=['target'])
        pairs = {(g, u) for u, t in zip(rep['uniquecontrast'], rep['target']) for g in t.split(';')}
        measured = set(zip(self.data.FBX_MEASURE['genes'], self.data.FBX_MEASURE['uniquecontrast']))
        cdf = self.out.compounds_df
        # all tested and measured pairs are in the panel data
        self.assertLessEqual(pairs & measured, set(zip(cdf['gene'], cdf['uniquecontrast'])))

    def test_negative_target_rows_flagged(self):
        """Batch 002 (plate Pw22VD) has both target genes not significant -> 4 target rows (2 genes x WT/KO)
        with is_target and is_completion set (not a hit: no export, no dot size). Since 2026-10-26 they carry the
        gene's validation score (50 / 20), so the MS slider filters them like the target's other validation rows."""
        cdf = self.out.compounds_df
        r = cdf[cdf['uniquecontrast'].astype(str).str.contains('.002_', regex=False)
                & cdf['gene'].isin(make_synthetic.GENES[:2])]
        # both genes of the ';' target, in both conditions
        self.assertEqual(sorted(zip(r['gene'], r['plate'].astype(str))),
                         sorted((g, p) for g in make_synthetic.GENES[:2] for p in ('Pw22VDKO', 'Pw22VDWT')))
        # tested-target context rows, not hits
        self.assertTrue(r['is_target'].all() and r['is_completion'].all())
        # the gene's validation score
        self.assertEqual(r['ms_score'].tolist(), r['gene'].map(dict(zip(make_synthetic.GENES[:2], [50.0, 20.0]))).tolist())

    def test_both_batches_in_panel(self):
        """Compound 8 has batch 001 (plate Pw21VD) and batch 002 (plate Pw22VD). The target gene's panel
        holds both, each with its own molecule_batch_id; the page flags the 002 rows as target rows."""
        import re
        c8, g0 = make_synthetic.COMPOUNDS[8], make_synthetic.GENES[0]
        cdf = self.out.compounds_df
        r = cdf[(cdf['gene'] == g0) & (cdf['compound'] == c8) & cdf['plate'].astype(str).str.startswith(('Pw21VD', 'Pw22VD'))]
        # one batch id per stem, both present
        self.assertEqual(sorted(set(r['molecule_batch_id'])), [f'{c8}-001', f'{c8}-002'])
        with open('tmp_vdir/out_test/interfaces/Serac_Px_interface_data.js') as f:
            v = re.search(r'window\.__GENE_COMPOUNDS__ = (.*?);\n', f.read()).group(1)
        rows = [pl for e in json.loads(json.loads(v[len('JSON.parse('):-1]))[g0][1:] if e[0] == c8 for pl in e[3]]
        # the batch-002 plate rows carry the target flag (pl[11]) to the page
        self.assertLessEqual({'Pw22VDKO', 'Pw22VDWT'}, {pl[0] for pl in rows if pl[11] == 1})

    def test_target_rows_open_stem_js(self):
        """Client side: a target row (pl[11]) on a ticked plate, with its validation score in the MS window, opens
        its stem like a hit, and it keeps its gene in the 3D view; other completion rows still need a real hit."""
        with open('tmp_vdir/out_test/interfaces/Serac_Px_interface.html') as f:
            html = f.read()
        # the stem rule
        self.assertIn('if (pl[11] && ticked[pl[0]] && msOk(pl)) okStems[valStemOf(pl[0])] = 1;', html)
        # the gene-visibility rule
        self.assertIn('if (pl[6] && !pl[11]) continue;', html)


class TestValidationTargetRows(unittest.TestCase):
    """validation_target_rows: the REPORT `target` (';'-separated) gives one panel row per (target gene,
    contrast) that compounds_df does not hold yet and that the contrast measured. The key is the contrast,
    so each batch of a compound gets its own row."""

    def setUp(self):
        self.rep = pd.DataFrame({'uniquecontrast': ['u1', 'u2', 'u3', 'u4'], 'compound': ['C1', 'C1', 'C2', 'C3'],
                                 'plate': ['P1WT', 'P2WT', 'P1WT', 'P0'], 'target': ['G1; G2', 'G1', 'G1', None]})
        self.meas = pd.DataFrame({'genes': ['G1', 'G2', 'G1', 'G1', 'G1'], 'uniquecontrast': ['u1', 'u1', 'u2', 'u3', 'u4'],
                                  'logfc': [-0.5, 0.3, 0.1, -2.0, -1.0]})
        self.cdf = pd.DataFrame({'gene': ['G1'], 'uniquecontrast': ['u3']})   # G1 is already a hit in u3

    def test_rows(self):
        """u1 targets 'G1; G2' (split + strip), u2 is a second batch of C1 (its own contrast), u3 is already
        in the panel, u4 has no target -> rows (G1,u1), (G1,u2), (G2,u1) at their logfc."""
        out = px.validation_target_rows(self.rep, self.cdf, self.meas)
        # both genes of the ';' target, both batches of C1, nothing for u3 / u4
        self.assertEqual(sorted(zip(out['gene'], out['uniquecontrast'], out['logfc'])),
                         [('G1', 'u1', -0.5), ('G1', 'u2', 0.1), ('G2', 'u1', 0.3)])

    def test_unmeasured_gene_skipped(self):
        """A target gene that the contrast did not measure gives no row (it has no volcano point)."""
        out = px.validation_target_rows(self.rep.assign(target=['G1;G9', None, None, None]), self.cdf.iloc[:0], self.meas)
        # only G1 of 'G1;G9'
        self.assertEqual(out['gene'].tolist(), ['G1'])


class TestPlotVolcano(unittest.TestCase):
    """fn.plot_volcano (2026-10-26): `key` selects the rows, `sig_col` colours by a flag column, `gene` can be a list."""

    def test_flag_colour_key_and_gene_list(self):
        """Input: plate P1 with A (flag 1, logfc -0.5), B (flag 0, logfc -2, p 1e-6), C (flag 1, logfc +2) and D, plus
        a row on P2; the genes A, C and X (X not measured). Expected: only the P1 rows; with sig_col A is down and B
        grey; without it the thresholds apply (B down, A grey); A and C carry a label, X is skipped. Rationale: the
        notebook volcano colours by the database flag, as the interface does."""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from python import functions as fn
        df = pd.DataFrame({'plate': ['P1'] * 4 + ['P2'], 'genes': ['A', 'B', 'C', 'D', 'A'],
                           'logfc': [-0.5, -2.0, 2.0, 0.1, -3.0], 'pvalue': [1e-10, 1e-6, 1e-4, 0.5, 1e-9],
                           'significant': [1, 0, 1, 0, 1]})
        fig, (ax, ax2) = plt.subplots(1, 2)
        agg = fn.plot_volcano(df, 'P1', ['A', 'C', 'X'], key='plate', sig_col='significant', ax=ax)
        fn.plot_volcano(df, 'P1', 'A', key='plate', ax=ax2)
        down = lambda a: sorted(a.collections[2].get_offsets()[:, 0].tolist())   # scatter order: ns, up, down
        texts = {t.get_text() for t in ax.texts}
        plt.close(fig)
        # only the P1 rows, with the flag
        self.assertEqual(sorted(agg['genes']), ['A', 'B', 'C', 'D'])
        self.assertIn('significant', agg.columns)
        # the flag colours A as down; the thresholds colour B as down
        self.assertEqual(down(ax), [-0.5])
        self.assertEqual(down(ax2), [-2.0])
        # the measured genes of the list carry a label
        self.assertEqual(texts, {'A', 'C'})


class TestVolcanoColour(unittest.TestCase):
    """px.volcano_colour (user decision 2026-10-23): the reference validation volcano colours only the tested
    targets with adjpval < 0.05, any logfc. The DG25VDA01WT case: MAP2K3 (logfc about -0.9) red, off-targets grey."""

    def test_targets_only_on_validation(self):
        """Input: a validation contrast (targets A;B) and a primary contrast (no target). Expected: on the
        validation contrast A (adjpval 0.01, logfc -0.9, not significant) colours, B (adjpval 0.2) and the
        significant non-target C do not; on the primary contrast the flag is `significant`. Rationale: the
        reference plot; primary volcanoes keep the platform flag."""
        meas = pd.DataFrame({'uniquecontrast': ['v', 'v', 'v', 'p', 'p'], 'genes': ['A', 'B', 'C', 'A', 'C'],
                             'significant': [0, 0, 1, 1, 0]}, index=[10, 11, 12, 13, 14])
        report = pd.DataFrame({'uniquecontrast': ['v', 'p'], 'target': ['A; B', None]})
        measure = pd.DataFrame({'adjpval': [0.01, 0.2, 1e-5, 1e-3, 0.5]}, index=[10, 11, 12, 13, 14])
        flag = px.volcano_colour(meas, report, measure, 0.05)
        # targets only on the validation contrast; the flag on the primary one
        self.assertEqual(flag.tolist(), [1, 0, 0, 1, 0])
        # the index of meas is kept
        self.assertEqual(flag.index.tolist(), [10, 11, 12, 13, 14])


class TestPlateReconstruction(unittest.TestCase):
    """Validation-only tranches omit the `plate` column; it's embedded in the uniquecontrast
    (…_complement_Pw144VM_BIND -> Pw144VMBIND). _plate_from_uc parses it and _ensure_plate fills a
    missing/NaN plate column from it, leaving existing plate values untouched."""

    def test_plate_from_uc(self):
        # stem + condition concatenated, matching the Pw###VM{WT,MLN,KO,BIND} convention
        self.assertEqual(px._plate_from_uc('SRB.0000519.002_vs_SRB.0000519.002_complement_Pw144VM_BIND'), 'Pw144VMBIND')
        self.assertEqual(px._plate_from_uc('x_vs_y_complement_Pw105VM_WT'), 'Pw105VMWT')
        # no _complement_ token -> None (not every contrast is a validation complement)
        self.assertIsNone(px._plate_from_uc('SRB.1_vs_SRB.2'))

    def test_ensure_plate_builds_missing_column(self):
        # no plate column at all -> build it wholly from the contrast
        df = pd.DataFrame({'uniquecontrast': ['a_vs_b_complement_Pw1_WT', 'a_vs_b_complement_Pw1_KO']})
        self.assertEqual(list(px._ensure_plate(df)['plate']), ['Pw1WT', 'Pw1KO'])

    def test_ensure_plate_fills_only_gaps(self):
        # existing plate values kept; only NaN rows are reconstructed
        df = pd.DataFrame({'plate': ['Pw9', None], 'uniquecontrast': ['x', 'a_vs_b_complement_Pw2_BIND']})
        self.assertEqual(list(px._ensure_plate(df)['plate']), ['Pw9', 'Pw2BIND'])


class TestStemSharedYmax(unittest.TestCase):
    """Validation-stem volcanoes (WT/MLN/KO/BIND + the attached primary) render on ONE shared y-max
    = the stem's tallest. _stem_shared_ymax returns {vk: ymax} only for the volcanoes scaled UP; the
    tallest keeps auto-scale (absent from the map -> its on-disk cache stays valid, no re-render)."""

    def test_shares_stem_max_and_skips_tallest(self):
        from python import functions as fn
        import numpy as np
        # one stem: compound C1 on plate-stem PwX with a tall WT (p=1e-8) + short KO (p=1e-2), + primary
        cdf = pd.DataFrame({'compound': ['C1', 'C1', 'C1'],
                            'plate': ['PwXWT', 'PwXKO', 'Pw00'],       # Pw00 = primary (non-validation)
                            'uniquecontrast': ['ucWT', 'ucKO', 'ucP'],
                            'is_primary': [False, False, True]})
        vsrc = pd.DataFrame({'compound': ['ucWT', 'ucKO', 'ucP'],       # vk lives in 'compound' (render slices it)
                             'pvalue': [1e-8, 1e-2, 1e-4],
                             'genes': ['G1', 'G1', 'G1'], 'logfc': [-2, -1, -1.5]})
        m = fn._stem_shared_ymax(cdf, vsrc, ['WT', 'MLN', 'KO', 'BIND'])
        tall = -np.log10(1e-8) * 1.05     # WT is tallest -> the shared max
        # WT is the tallest -> keeps auto (not overridden); KO + primary are scaled UP to WT's height
        self.assertNotIn('ucWT', m)
        self.assertAlmostEqual(m['ucKO'], tall, places=4)
        self.assertAlmostEqual(m['ucP'], tall, places=4)

    def test_noop_without_suffixes_or_df(self):
        from python import functions as fn
        # no compounds_df / no suffixes -> empty map (feature off, every volcano auto-scales)
        self.assertEqual(fn._stem_shared_ymax(None, None, ['WT']), {})
        self.assertEqual(fn._stem_shared_ymax(pd.DataFrame({'x': [1]}), None, ['WT']), {})

    def test_volcano_base_svg_honors_override(self):
        from python import functions as fn
        # a shallow volcano (p=1e-2 -> auto ymax ~2.1); the override must win and drive geom['ymax']
        # (which the client uses for ring fractions), never falling below the data's own max
        df = pd.DataFrame({'compound': ['uc', 'uc'], 'genes': ['G1', 'G2'],
                           'logfc': [-2.0, 2.0], 'pvalue': [1e-2, 1e-2], 'significant': [1, 1]})
        _, geom_auto = fn._volcano_base_svg(df, 'uc', key='compound')
        _, geom_ovr = fn._volcano_base_svg(df, 'uc', key='compound', ymax_override=40.0)
        # auto scales to the data; the override raises it to the shared stem max
        self.assertLess(geom_auto['ymax'], 40.0)
        self.assertEqual(geom_ovr['ymax'], 40.0)


class TestVolcanoDataHash(unittest.TestCase):
    """The base-volcano filename carries a hash of the experiment's data (2026-10-23). Before, the cache
    kept an image after its data changed: the DG25VDA01WT volcano still showed the p = 0 floor of the old parse."""

    def test_hash_follows_the_data(self):
        """Input: two experiments, then the same rows in another order, then one changed p-value. Expected:
        the order gives the same hash; the change gives a new hash for that experiment only, and a new filename.
        Rationale: a data change must render the volcano again; unchanged data must stay a cache hit."""
        from python import functions as fn
        vsrc = pd.DataFrame({'compound': ['uc1', 'uc1', 'uc2', 'uc2'], 'genes': ['G1', 'G2', 'G1', 'G2'],
                             'logfc': [-2.0, 0.1, -1.0, 0.3], 'pvalue': [1e-50, 0.5, 1e-3, 0.2], 'significant': [1, 0, 1, 0]})
        h = fn._volcano_data_hash(vsrc, ['uc1', 'uc2'])
        changed = vsrc.copy()
        changed.loc[0, 'pvalue'] = 1e-30
        h2 = fn._volcano_data_hash(changed, ['uc1', 'uc2'])
        name = lambda d: fn._volcano_base_cache_fname('uc1', (-8, 8), 350, data=d)
        # the row order does not change the hash
        self.assertEqual(fn._volcano_data_hash(vsrc.iloc[::-1], ['uc1', 'uc2']), h)
        # a changed p-value changes the hash of its experiment only
        self.assertNotEqual(h2['uc1'], h['uc1'])
        self.assertEqual(h2['uc2'], h['uc2'])
        # the filename follows the hash
        self.assertNotEqual(name(h['uc1']), name(h2['uc1']))
        self.assertEqual(name(h['uc1']), name(fn._volcano_data_hash(vsrc, ['uc1'])['uc1']))


class TestRender(unittest.TestCase):
    """End-to-end render: build_interface writes the HTML, data.js and volcano SVGs."""

    @classmethod
    def setUpClass(cls):
        cls.params, cls.data, cls.output = _pipeline()
        cls.out_dir = 'tmp/out_test'
        cls.output.build_interface(cls.data, cls.params, cls.out_dir)

    def test_html_written(self):
        """The interface HTML and its deferred data.js are written under output_dir."""
        base = os.path.join(self.out_dir, 'interfaces')
        # the main document exists
        self.assertTrue(os.path.exists(os.path.join(base, 'Serac_Px_interface.html')))
        # the deferred data blob exists
        self.assertTrue(os.path.exists(os.path.join(base, 'Serac_Px_interface_data.js')))

    def test_labels_toggle_and_dependent_on_top_wired(self):
        """The DISPLAY row carries the Labels eye toggle, and the client JS carries both the
        label-hiding rule (hideOtherLabels) and the 2D dependent-on-top depth bump (_depthX).
        Guards against the wiring being dropped from the injected interface HTML."""
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # the eye toggle element is present in the panel markup
        self.assertIn('id="label-toggle"', html)
        # labels-hiding flag + the refreshLabels dependent-only guard are emitted
        self.assertIn('hideOtherLabels', html)
        # the 2D depth-bump helper that brings FBXO31-dependent circles to the front is emitted
        self.assertIn('_depthX', html)
        # the V-mode legend key follows its Target-validation tickbox (untick -> key removed)
        self.assertIn('syncValLegendFromTicks', html)
        # the 2D labels-off leader-line declutter is emitted
        self.assertIn('declutterLabels', html)

    def test_controls_shown_by_default(self):
        """Config CONTROLS_ON (2026-10-26): absent or true -> the control tickbox starts ticked (the CONTROLS compounds
        show); false -> it starts unticked. Before, build_interface always passed control_default_on=False."""
        import tempfile
        js = lambda d: open(os.path.join(d, 'interfaces', 'Serac_Px_interface_data.js')).read()
        had, saved = hasattr(self.params, 'CONTROLS_ON'), getattr(self.params, 'CONTROLS_ON', None)
        tmp = tempfile.TemporaryDirectory()
        try:
            self.params.CONTROLS_ON = False
            self.output.build_interface(self.data, self.params, tmp.name)
            off = js(tmp.name)
        finally:
            if had:
                self.params.CONTROLS_ON = saved
            else:
                del self.params.CONTROLS_ON
            tmp.cleanup()
        # the fixture config has no CONTROLS_ON: the controls show on load
        self.assertIn('window.__CONTROL_DEFAULT_ON__ = true;', js(self.out_dir))
        # CONTROLS_ON: false hides them on load
        self.assertIn('window.__CONTROL_DEFAULT_ON__ = false;', off)

    def test_compound_peek_stays_under_the_pointer(self):
        """The compound row keeps its own width at the right edge of the panel, and the volcano peek closes only when
        the pointer leaves the whole panel (2026-10-23). Before, the panel grew to the left when the volcanoes
        opened, the cells moved under the pointer, and leaving the row closed the peek. A headless-Chrome check
        (scratchpad hover_test.py) showed: cells moved 198 px before, 0 px after."""
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # the row stays at the right, at its own width
        self.assertIn('width: fit-content; max-width: 100%; margin-left: auto;', html)
        # the peek closes on leaving the panel, not the row
        self.assertIn('box.addEventListener("mouseleave"', html)
        self.assertNotIn('row.addEventListener("mouseout"', html)

    def test_loading_screen_first(self):
        """The loading screen comes right after <body>, before plotly.min.js and the figure, so it
        paints first and covers the raw first render; the client JS removes it on window load."""
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # the loading screen is the first thing in <body>
        self.assertTrue(html.split('<body>', 1)[1].lstrip().startswith('<style>\n  #px-loading'))
        # it comes before the plotly.js script and the inline Plotly.newPlot figure
        self.assertLess(html.index('id="px-loading"'), html.index('src="plotly.min.js"'))
        self.assertLess(html.index('id="px-loading"'), html.index('Plotly.newPlot('))
        # the removal hook (window load + Plotly quiet) is emitted once
        self.assertEqual(html.count('gd.on("plotly_afterplot", wait)'), 1)

    def test_validation_blocks_by_date_wired(self):
        """The Plates filter builds one validation block per date (a stem under its earliest plate date)."""
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # the per-date grouping and its block builder are emitted
        self.assertIn('validationBlock(d, valByDate[d])', html)
        # the block label carries the date
        self.assertIn('class="pf-date-all">validation \' + label', html)

    def test_plate_link_wired(self):
        """A link #Plate=20261006 / #Plate=validation20261006 selects plates by the SHOW_PLATE rule
        (2026-10-12). Checks that the page carries: the Plate= alias of p=, the entry rule (date block,
        validation block, plate name), the hashchange listener, and a Link button that does not re-apply."""
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # Plate= / Plates= (any case) map to p=
        self.assertIn(".toLowerCase().replace(/^plates?$/, 'p')", html)
        # the entry rule: an optional validation prefix + a YYYYMMDD or YYYY-MM-DD date (single backslashes in the page)
        self.assertIn(r"match(/^(validation[-_]?)?(\d{4})-?(\d{2})-?(\d{2})$/i)", html)
        # a link pasted into an open tab applies
        self.assertIn('window.addEventListener("hashchange"', html)
        # the Link button writes the hash without firing hashchange
        self.assertIn('history.replaceState(null, "", "#" + buildHash())', html)

    def test_labels_link_wired(self):
        """A link #Labels=false (also off / 0 / no) starts the Labels eye shut; #Labels=true opens it
        (2026-10-12). The link goes through the session apply(), and the Link button writes labels= when
        the eye differs from the config default (LABELS_ON)."""
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # the hash key and its false values
        self.assertIn("if ('labels' in q) sess.labels = !/^(false|0|off|no)$/i.test(q.labels);", html)
        # the session apply sets the eye through the hook
        self.assertIn('if (typeof s.labels === "boolean") setLabelsHook(s.labels);', html)
        # the hook is declared before the Labels block assigns it (a later var would reset it)
        self.assertLess(html.index('var setLabelsHook = function() {};'),
                        html.index('setLabelsHook = function(on) { hideOtherLabels = !on; apply(); };'))
        # the Link button keeps a non-default eye state
        self.assertIn("parts.push('labels=' + !hideOtherLabels)", html)

    def test_other_link_wired(self):
        """A link #Other=false (also off / 0 / no) hides the V-mode "other" genes, as a click on the "other"
        legend key does, so only the FBXO31 genes show; #Other=true shows them again (2026-10-27). The link
        goes through the session apply(), and the Link button writes other=false while they are hidden."""
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # the hash key and its false values
        self.assertIn("if ('other' in q) sess.other = !/^(false|0|off|no)$/i.test(q.other);", html)
        # a hash with only this key is a recognised link
        self.assertIn("'cm', 'labels', 'other'].some(", html)
        # the session apply sets the category through the hook, before it re-runs the mask
        self.assertIn('if (typeof s.other === "boolean") setOtherHook(s.other);', html)
        # the hook is declared before the range block assigns it (a later var would reset it)
        self.assertLess(html.index('var setOtherHook = function() {};'),
                        html.index('setOtherHook = function(on) { valCatShown.rest = on; _syncValLegendDim(); };'))
        # the Link button keeps the hidden state
        self.assertIn("if (valCatShown.rest === false) parts.push('other=false');", html)

    def test_volcanoes_written(self):
        """At least one volcano SVG is rendered into volcanoes_px/."""
        vdir = os.path.join(self.out_dir, 'interfaces', 'volcanoes_px')
        # volcanoes directory has SVG output
        self.assertTrue(os.path.isdir(vdir))
        self.assertTrue(any(f.endswith('.svg') for f in os.listdir(vdir)))

    def test_stem_trace_survives_panels_cache(self):
        """Gene-linking (__STEM_TRACE__) must be built on BOTH the fresh-render and the
        panels-cache path. IFACE_OVERWRITE=false loads cached panels but must still emit the
        cross-plate trace positions (regression: the build used to be skipped on the cache path,
        shipping an empty map and silently disabling the WT/MLN/KO gene-linking)."""
        import re
        out = 'tmp/out_cache_test'
        dj = os.path.join(out, 'interfaces', 'Serac_Px_interface_data.js')

        def stem_total():
            js = open(dj).read()
            m = re.search(r'__STEM_TRACE__ = JSON\.parse\("(.*?)"\);', js, re.S)
            st = json.loads(json.loads('"' + m.group(1) + '"'))
            return sum(len(v) for v in st.values())

        saved = self.params.IFACE_OVERWRITE
        try:
            self.params.IFACE_OVERWRITE = True            # fresh render: writes panels.json + ring_pos.json
            self.output.build_interface(self.data, self.params, out)
            fresh = stem_total()
            self.params.IFACE_OVERWRITE = False           # cache load: must rebuild the trace from cache
            self.output.build_interface(self.data, self.params, out)
            cached = stem_total()
        finally:
            self.params.IFACE_OVERWRITE = saved
        # the fresh render emits trace positions
        self.assertGreater(fresh, 0)
        # the cache path emits the SAME positions (not an empty map)
        self.assertEqual(cached, fresh)

    def test_volcano_rerenders_on_data_change(self):
        """Input: builds into one output folder: the same data two times, then one changed logfc in one
        experiment. Expected: the second build renders no volcano; the third renders only that experiment,
        under a new filename. Rationale: the 2026-10-23 stale image (the cache ignored the data)."""
        import contextlib, io, re, tempfile
        tmp = tempfile.TemporaryDirectory()                # empty: positions.json holds only this build's experiments
        out = tmp.name
        vdir = os.path.join(out, 'interfaces', 'volcanoes_px')
        meas = self.output.meas

        def build():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.output.build_interface(self.data, self.params, out)
            n = int(re.search(r'([\d,]+) base\(s\) rendered', buf.getvalue()).group(1).replace(',', ''))
            return n, {f for f in os.listdir(vdir) if f.endswith('.svg')}

        saved_ow, saved_logfc = self.params.IFACE_OVERWRITE, meas['logfc'].copy()
        try:
            self.params.IFACE_OVERWRITE = True
            build()
            n_same, files_same = build()
            vk = sorted(json.load(open(os.path.join(vdir, 'positions.json'))))[0]
            meas.loc[meas.index[meas['uniquecontrast'].astype(str) == vk][0], 'logfc'] += 0.01
            n_changed, files_changed = build()
        finally:
            self.params.IFACE_OVERWRITE = saved_ow
            meas['logfc'] = saved_logfc
            tmp.cleanup()
        # the same data renders nothing again
        self.assertEqual(n_same, 0)
        # one changed experiment renders one volcano, under one new filename
        self.assertEqual(n_changed, 1)
        self.assertEqual(len(files_changed - files_same), 1)

    def test_volcano_base_dedup(self):
        """Base-dedup: many (gene,plate) cells share ONE per-experiment base SVG (keyed by
        experiment, not focal gene), and each cell carries its focal-gene ring position pl[9]
        for the client to draw the ring. Guards against regressing to one baked file per cell."""
        import re, os
        js = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()
        m = re.search(r'__GENE_COMPOUNDS__ = JSON\.parse\("(.*?)"\);', js, re.S)
        gc = json.loads(json.loads('"' + m.group(1) + '"'))
        cells, bases, with_pos = 0, set(), 0
        for entries in gc.values():
            for t in entries:
                if isinstance(t, list) and len(t) > 3 and isinstance(t[3], list):
                    for pl in t[3]:
                        cells += 1
                        if pl[2]:
                            bases.add(pl[2])
                        # pl[9] = [fx, fy] focal-gene ring position (fraction of the base image)
                        if len(pl) > 9 and pl[9]:
                            with_pos += 1
                            self.assertEqual(len(pl[9]), 2)
                            self.assertTrue(all(0.0 <= c <= 1.0 for c in pl[9]))
        # cells exist and share far fewer base images than there are cells (the dedup)
        self.assertGreater(cells, 0)
        self.assertLess(len(bases), cells)
        # every real cell carries a ring position
        self.assertGreater(with_pos, 0)
        # positions.json persisted for cached-base re-runs
        self.assertTrue(os.path.exists(os.path.join(
            self.out_dir, 'interfaces', 'volcanoes_px', 'positions.json')))

    def test_stem_trace_links_genes_across_conditions(self):
        """The WT/MLN/KO cross-plate gene-linking must survive the render-dedup: every
        __STEM_TRACE__ position must come from the NEW fixed-geometry base+overlay render
        (square image -> aspect 1.0, fractions in [0,1]), and at least one gene must appear
        in >=2 validation contrasts so a hover can draw a line between its conditions.
        Regression guard: analytic ring positions (not the old matplotlib ring-path bbox)
        feed the trace, and no stale non-square ring_pos entries leak in."""
        import re
        js = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()
        m = re.search(r'__STEM_TRACE__ = JSON\.parse\("(.*?)"\);', js, re.S)
        st = json.loads(json.loads('"' + m.group(1) + '"'))
        positions = [p for genes in st.values() for p in genes.values()]
        # the trace is populated (validation stems exist in the fixture)
        self.assertGreater(len(positions), 0)
        # every position is [fx, fy, aspect, isHit] from the fixed-geometry square image
        for fx, fy, aspect, _hit in positions:
            self.assertTrue(0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0)   # in-image fraction
            self.assertAlmostEqual(aspect, 1.0, places=3)            # square (new geometry, not stale tight-crop)
        # at least one gene is present in >=2 contrasts -> its conditions can be linked
        from collections import Counter
        gene_contrasts = Counter(g for genes in st.values() for g in genes)
        self.assertTrue(any(c >= 2 for c in gene_contrasts.values()))

    def test_primary_screen_linked_in_render(self):
        """The attached primary-screen volcano reaches the client as a stem cell: its plate row
        carries pl[10]==1 (primary flag) + pl[7] (contrast id), that contrast is in __STEM_TRACE__
        (so the hover-trace links primary->WT->KO), and the client grouping code is present.
        Fixture: SRB-0000006 / G_00000 is a primary-screen hit on Pw00 alongside the Pw10/Pw11 stems."""
        import re
        base = os.path.join(self.out_dir, 'interfaces')
        js = open(os.path.join(base, 'Serac_Px_interface_data.js')).read()
        code = open(os.path.join(base, 'Serac_Px_interface.html')).read() + js

        def grab(name):
            m = re.search(r'__' + name + r'__ = JSON\.parse\("(.*?)"\);', js, re.S)
            return json.loads(json.loads('"' + m.group(1) + '"'))
        comp = next(e for e in grab('GENE_COMPOUNDS')['G_00000'] if e[0] == 'SRB-0000006')
        prim = [pl for pl in comp[3] if len(pl) > 10 and pl[10] == 1]
        # exactly one plate row is flagged primary, on Pw00, carrying a contrast id for the trace
        self.assertEqual(len(prim), 1)
        self.assertEqual(prim[0][0], 'Pw00')
        self.assertTrue(prim[0][7])
        # that contrast (with G_00000) is in the stem trace -> links to the WT/KO conditions on hover
        st = grab('STEM_TRACE')
        self.assertIn(prim[0][7], st)
        self.assertIn('G_00000', st[prim[0][7]])
        # the client grouping/visibility code for primary cells is present
        for tok in ('isPrimaryPlate', 'vprimary', 'primary screen'):
            self.assertIn(tok, code)

    def test_nonsignificant_primary_shows_location(self):
        """A gene not significant in the primary screen still shows its location: every ride-along
        primary cell (is_primary + is_completion, pl[10]==1 & pl[6]) must carry a ring position pl[9].
        Regression guard for the base-dedup cache — primary genes newly attached to an already-cached
        base must trigger a re-render (focal-set grew) so their positions get recorded, else their
        location would silently go missing on rebuilds over an existing volcanoes_px."""
        import re
        js = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()
        gc = json.loads(json.loads('"' + re.search(r'__GENE_COMPOUNDS__ = JSON\.parse\("(.*?)"\);', js, re.S).group(1) + '"'))
        ride = with_ring = 0
        for entries in gc.values():
            for e in entries:
                if not (isinstance(e, list) and e and e[0] != '__META__' and len(e) > 3 and isinstance(e[3], list)):
                    continue
                for pl in e[3]:
                    if len(pl) > 10 and pl[10] == 1 and pl[6]:   # primary ride-along (gene not significant here)
                        ride += 1
                        with_ring += 1 if pl[9] else 0
        # the fixture produces many non-significant primary ride-alongs...
        self.assertGreater(ride, 0)
        # ...and every one shows its location (ring position recorded)
        self.assertEqual(with_ring, ride)

    def test_gene_size_buckets(self):
        """Dots are sized by #compounds each gene is significant in. The config's
        GENE_SIZE_BUCKETS must reach the client as __SIZE_BUCKETS__, and every plotted
        gene must be assigned one of those bucket sizes in __GENE_SIZE__."""
        import re
        js = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()

        def grab(name):
            m = re.search(r'__' + name + r'__ = JSON\.parse\("(.*?)"\);', js, re.S)
            return json.loads(json.loads('"' + m.group(1) + '"'))
        buckets, gene_size = grab('SIZE_BUCKETS'), grab('GENE_SIZE')
        # the config value (set in make_synthetic) is what reached the client
        self.assertEqual(buckets, [5, 7, 9, 11, 13, 16])
        # every plotted gene got a size, and it's always one of the buckets
        self.assertGreater(len(gene_size), 0)
        self.assertTrue(set(gene_size.values()) <= set(buckets))
        # sizing is not degenerate: more than one bucket is actually used
        self.assertGreater(len(set(gene_size.values())), 1)

    def test_ring_underlay(self):
        """Thick dot rings are drawn as an underlay dot (gl3d caps marker outline width). The
        config GENE_RING_PX must reach the client as __RING_PX__, and the two ring-underlay
        traces must be emitted: __AREA_RING_TRACE__ right after the backdrop (index 1) and
        __PIN_RING_TRACE__ immediately before the pin fill trace."""
        import re
        js = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()

        def num(name):
            m = re.search(r'window\.__' + name + r'__ = ([0-9.]+);', js)
            return float(m.group(1)) if m else None
        # the config ring thickness (set in make_synthetic) reached the client
        self.assertEqual(num('RING_PX'), 5.0)
        # area ring underlay renders right after the backdrop, before every fill trace
        self.assertEqual(num('AREA_RING_TRACE'), 1.0)
        # pin ring underlay is emitted and sits just below (before) the pin fill trace
        self.assertEqual(num('PIN_RING_TRACE'), num('PIN_TRACE') - 1)

    def test_z_fit_pad(self):
        """The MS (z) axis top follows the genes that show: config Z_FIT_PAD reaches the client as
        __RANGES__.zFitPad, and the page sets the z range from the shown genes + pins before each redraw
        that can change them (applyRanges, a pin change, a legend toggle)."""
        import re
        js = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()
        m = re.search(r'window\.__RANGES__ = JSON\.parse\("(.*?)"\);', js, re.S)
        # the config margin (set in make_synthetic) reached the client
        self.assertEqual(json.loads(json.loads('"' + m.group(1) + '"'))['zFitPad'], 0.08)
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        # applyRanges fits z before its redraw
        self.assertIn('fitZ();\n        Plotly.redraw(gd);', html)
        # a pin change fits z before its redraw
        self.assertIn('fitZHook();            // a pin above the z top raises it\n          Plotly.redraw(gd);', html)
        # a legend toggle redraws only when the z range changed
        self.assertIn('if (fitZ()) Plotly.redraw(gd);', html)


    def test_ms_score_platerows(self):
        """The MS slider filters each compound experiment by its own MS score, and the dot's z stays fixed.
        Every real plate-row in the injected panel must carry a numeric MS at index 8, and the client must wire
        that value into the per-entry filter (msOk / msLo / msHi), with no range test on the dot's z."""
        import re
        js = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()
        m = re.search(r'__GENE_COMPOUNDS__ = JSON\.parse\("(.*?)"\);', js, re.S)
        gc = json.loads(json.loads('"' + m.group(1) + '"'))
        vals, real_rows = [], 0
        for entries in gc.values():
            for t in entries:
                if isinstance(t, list) and len(t) > 3 and isinstance(t[3], list):
                    for pl in t[3]:
                        if not pl[6]:              # skip completion rows (null MS by design)
                            real_rows += 1
                            if pl[8] is not None:
                                vals.append(pl[8])
        # real plate-rows exist and carry a numeric MS at index 8
        self.assertGreater(real_rows, 0)
        self.assertEqual(len(vals), real_rows)
        self.assertTrue(all(isinstance(v, (int, float)) for v in vals))
        # the client filter is wired: msOk() gated on the msLo/msHi window
        html = open(os.path.join(self.out_dir, 'interfaces', 'Serac_Px_interface.html')).read()
        self.assertIn('function msOk(pl)', html)
        self.assertIn('msLo = b.z[0]', html)
        # the dot's fixed z is not range-tested
        self.assertNotIn('o.z[k] >= b.z[0]', html)


class TestFbxo31IndependentTicked(unittest.TestCase):
    """FBXO31_INDEPENDENT_TICKED controls the target-validation filter's default tick state.
    false -> the interface opens with only the 'FBXO31 dependent' box ticked (independent genes
    hidden on load, still toggle-able), injected as __VALIDATION_DEFAULTS__; true/absent -> both
    boxes ticked (__VALIDATION_DEFAULTS__ = null)."""

    def _defaults_line(self, out_dir):
        import re
        js = open(os.path.join(out_dir, 'interfaces', 'Serac_Px_interface_data.js')).read()
        return re.search(r'window\.__VALIDATION_DEFAULTS__ = (.*?);', js).group(1)

    def test_independent_unticked_when_false(self):
        params, data, output = _pipeline()
        params.FBXO31_INDEPENDENT_TICKED = False
        output.build_interface(data, params, 'tmp/out_indep_off')
        line = self._defaults_line('tmp/out_indep_off')
        # only the dependent box is ticked on load; independent is excluded
        self.assertIn('FBXO31 dependent', line)
        self.assertNotIn('FBXO31 independent', line)

    def test_both_ticked_when_true(self):
        params, data, output = _pipeline()
        params.FBXO31_INDEPENDENT_TICKED = True
        output.build_interface(data, params, 'tmp/out_indep_on')
        # None -> both boxes ticked (no default filter injected)
        self.assertEqual(self._defaults_line('tmp/out_indep_on'), 'null')


class TestFilterDefaults(unittest.TestCase):
    """The config filter defaults reach the page: SHOW_PLATE 'validation <date>' ticks that validation
    block (the JS no longer forces validation plates off), ACTIVITY_DEFAULTS picks the Activity boxes,
    an empty DEPMAP/CONF/LOF_DEFAULTS ticks every box (injected null, not []), LABELS_ON false
    starts the Labels eye toggle off, and Z_FIT_PAD null keeps the z axis on all genes."""

    @classmethod
    def setUpClass(cls):
        import copy
        params, data, output = _pipeline()
        params = copy.copy(params)                      # the cached params keep the fixture values
        cls.stem = ['Pw10KO', 'Pw10MLN', 'Pw10WT']
        params.SHOW_PLATE = ['validation ' + min(output.plate2date[p] for p in cls.stem).replace('-', '')]
        params.ACTIVITY_DEFAULTS = ['Single', 'Low']
        params.DEPMAP_DEFAULTS, params.CONF_DEFAULTS, params.LOF_DEFAULTS = [], [], []
        params.LABELS_ON = False
        params.Z_FIT_PAD = None
        output.build_interface(data, params, 'tmp/out_filter_defaults')
        with open('tmp/out_filter_defaults/interfaces/Serac_Px_interface_data.js') as f:
            cls.js = f.read()
        with open('tmp/out_filter_defaults/interfaces/Serac_Px_interface.html') as f:
            cls.html = f.read()

    def _inj(self, name):
        """window.__<name>__ in the data file: None for null, else the JSON.parse("...") payload."""
        import re
        v = re.search(rf'window\.__{name}__ = (.*?);\n', self.js).group(1)
        return None if v == 'null' else json.loads(json.loads(v[len('JSON.parse('):-1]))

    def test_validation_block_ticked(self):
        """SHOW_PLATE ['validation <first date of the Pw10 stem>'] -> __PLATE_DEFAULTS__ holds the whole
        Pw10 stem and validation plates only (no date block is named, so no primary plate ticks)."""
        plates = self._inj('PLATE_DEFAULTS')
        # every member of the Pw10 stem starts ticked
        self.assertLessEqual(set(self.stem), set(plates))
        # no primary plate starts ticked
        self.assertTrue(all(p.endswith(('WT', 'MLN', 'KO')) for p in plates), plates)

    def test_js_ticks_listed_validation_plates(self):
        """The Plates init ticks exactly the listed plates, so a listed validation plate starts ticked;
        with no list, every plate except the validation plates starts ticked (as before)."""
        # the init rule that lets plate_defaults tick a validation plate
        self.assertIn('ticked[p] = plateDefaults ? plateDefaults.indexOf(p) !== -1 : !isValidationPlate(p);', self.html)

    def test_activity_defaults_from_config(self):
        """ACTIVITY_DEFAULTS ['Single', 'Low'] -> only the matching Activity levels start ticked."""
        # the bare names resolve to the real level labels
        self.assertEqual(sorted(self._inj('ACTIVITY_DEFAULTS')), ['Low (2-10)', 'Single (1)'])

    def test_empty_target_defaults_tick_all(self):
        """Empty DEPMAP/CONF/LOF_DEFAULTS -> null, so every box starts ticked. An injected [] is truthy
        in JS and would untick every box, which hides all genes."""
        for name in ('DEPMAP_DEFAULTS', 'CONF_DEFAULTS', 'LOF_DEFAULTS'):
            # null = all boxes ticked
            self.assertIsNone(self._inj(name), name)

    def test_labels_toggle_off_from_config(self):
        """LABELS_ON false -> __LABELS_DEFAULT_ON__ = false, and the JS starts hideOtherLabels and the eye
        icon from it: only FBXO31-dependent (+ pinned) genes keep labels on load; a click still flips it."""
        # the config value reaches the page
        self.assertIn('window.__LABELS_DEFAULT_ON__ = false;', self.js)
        # the label rule starts from it
        self.assertIn('var hideOtherLabels = (window.__LABELS_DEFAULT_ON__ === false);', self.html)
        # the eye icon starts from it
        self.assertIn('tg.classList.toggle("off", hideOtherLabels);   // icon follows labels_default_on', self.html)

    def test_z_fit_off_from_config(self):
        """Z_FIT_PAD null -> __RANGES__.zFitPad null, so fitZ does nothing and Plotly fits the z axis to
        all genes (the invisible backdrop), as before the fit."""
        # null reaches the page
        self.assertIsNone(self._inj('RANGES')['zFitPad'])
        # fitZ stops on null
        self.assertIn('if (R.zFitPad == null) return false;', self.html)


class TestMemoryFreeing(unittest.TestCase):
    """FREE_UPSTREAM frees the combined measure/mscore/report inside get_iface (measure right after
    `meas` is built, mscore/report after their derivations) so the ~65M-row frame doesn't linger
    through the render — which still works because it only needs `meas`. EXPORT_COMBINED dumps the
    three tables to parquet first, so the export survives even though the frames are freed."""

    @classmethod
    def setUpClass(cls):
        make_synthetic.main('tmp')
        params = px.PARAMS(_CFG).load_params()
        params.FREE_UPSTREAM = True
        params.EXPORT_COMBINED = True
        params.PX_PARQUET_DIR = 'tmp/px_parquet'
        data = px.DATA()
        for m in ('load_chemical_lib_df', 'load_old_df', 'load_new_df',
                  'get_contaminants_and_controls', 'get_gene_research'):
            getattr(data, m)(params)
        out = px.OUTPUT()
        out.combine_datasets(data, params)
        out.get_de_validated(data, params)
        out.get_iface(data, params)
        out.build_interface(data, params, 'tmp/out_free')
        cls.out = out

    def test_combined_frames_freed(self):
        # measure/mscore/report are released inside get_iface once their derivations finished
        self.assertIsNone(self.out.measure)
        self.assertIsNone(self.out.mscore)
        self.assertIsNone(self.out.report)

    def test_render_inputs_survive(self):
        # the frames the interface actually needs are intact despite the freeing
        self.assertIsNotNone(self.out.meas)
        self.assertGreater(len(self.out.compounds_df), 0)

    def test_export_combined_written(self):
        # EXPORT_COMBINED wrote the three parquet dumps before the frames were freed
        for f in ['Px_MEASURE.parquet', 'Px_MSCORE.parquet', 'Px_REPORT.parquet']:
            self.assertTrue(os.path.exists(os.path.join('tmp/px_parquet', f)), f'{f} not exported')

    def test_html_written_after_free(self):
        # the full render still completes end-to-end with the frames freed
        self.assertTrue(os.path.exists('tmp/out_free/interfaces/Serac_Px_interface.html'))


class TestResolveNJobs(unittest.TestCase):
    """The NJOBS config knob resolves to a concrete worker count for the volcano render:
    <=0/None -> auto (all CPUs but 2); a positive int -> exactly that many."""

    def test_resolve_n_jobs(self):
        import os
        auto = max(1, (os.cpu_count() or 8) - 2)
        # 0 / None / negative -> auto (leave 2 cores free)
        for v in (0, None, -1):
            self.assertEqual(px.resolve_n_jobs(v), auto)
        # a positive int (incl. numeric string from YAML) -> that exact count
        self.assertEqual(px.resolve_n_jobs(4), 4)
        self.assertEqual(px.resolve_n_jobs('8'), 8)
        # never returns < 1
        self.assertGreaterEqual(px.resolve_n_jobs(0), 1)


class TestResolvePlateDefaults(unittest.TestCase):
    """SHOW_PLATE (config/--show_plate) picks which Plates-filter blocks open default-ticked:
    empty/None -> the single latest date; a list of YYYYMMDD dates -> exactly those dates'
    plates, with YYYYMMDD normalised to the YYYY-MM-DD form plate2date stores;
    'validation YYYYMMDD' -> the stems of that validation block (a date never ticks them)."""

    def setUp(self):
        self.p2d = {'Pa': '2026-08-11', 'Pb': '2026-08-12', 'Pc': '2026-08-12', 'Pd': '2026-08-13'}

    def test_default_latest_only(self):
        # no SHOW_PLATE -> only the latest date's plates ticked
        plates, dates = px.resolve_plate_defaults(self.p2d, None)
        self.assertEqual(plates, ['Pd'])
        self.assertEqual(dates, ['2026-08-13'])

    def test_multiple_yyyymmdd_dates(self):
        # a list of YYYYMMDD dates -> every plate on any of them, dates normalised to YYYY-MM-DD
        plates, dates = px.resolve_plate_defaults(self.p2d, ['20260812', '20260813'])
        self.assertEqual(plates, ['Pb', 'Pc', 'Pd'])
        self.assertEqual(dates, ['2026-08-12', '2026-08-13'])

    def test_ints_and_blanks_tolerated(self):
        # YAML ints and stray blank/empty entries are coerced/dropped, not errors
        plates, _ = px.resolve_plate_defaults(self.p2d, [20260811, '', ' '])
        self.assertEqual(plates, ['Pa'])

    def test_validation_block_ticks_whole_stem(self):
        """'validation 20260812' with suffixes (WT, KO) -> both plates of stem Pv (first dated 08-12,
        KO re-run 08-13), and no primary plate or other stem. This is the block the filter shows."""
        p2d = {**self.p2d, 'PvWT': '2026-08-12', 'PvKO': '2026-08-13', 'PwWT': '2026-08-13'}
        plates, blocks = px.resolve_plate_defaults(p2d, ['validation 20260812'], ('WT', 'KO'))
        # the whole Pv stem, nothing else
        self.assertEqual(plates, ['PvKO', 'PvWT'])
        # the label is the Plates-filter block head
        self.assertEqual(blocks, ['validation 2026-08-12'])

    def test_date_skips_validation_plates(self):
        """'20260813' with suffixes (WT, KO) -> only the primary plate Pd. PvKO and PwWT have that date,
        but they sit in validation blocks, so a date entry does not tick them."""
        p2d = {**self.p2d, 'PvWT': '2026-08-12', 'PvKO': '2026-08-13', 'PwWT': '2026-08-13'}
        plates, _ = px.resolve_plate_defaults(p2d, ['20260813'], ('WT', 'KO'))
        # the date block only
        self.assertEqual(plates, ['Pd'])


class TestDownloadCddPngs(unittest.TestCase):
    """data.download_cdd_pngs: opt-in compound-PNG refresh from CDD Vault.
    - UPDATE_PNGS false -> no-op (no import, no network).
    - UPDATE_PNGS true  -> reads the token file and drives the CDD downloader with the
      library's naming convention (prefix 'SRB-', strip_prefix False) into SRB_PNG_DIR.
    The real CDD module is stubbed via sys.modules so no network call is made."""

    def test_noop_when_disabled(self):
        """UPDATE_PNGS false returns immediately and never touches the downloader."""
        import types
        from types import SimpleNamespace
        boom = types.ModuleType('download_cdd_structures')
        def _explode(*a, **k):
            raise AssertionError('CDD downloader called though UPDATE_PNGS is false')
        boom.make_session = boom.list_molecules_in_search = boom.download_all = _explode
        sys.modules['download_cdd_structures'] = boom
        try:
            # disabled -> returns None without importing/calling the downloader
            self.assertIsNone(px.DATA().download_cdd_pngs(SimpleNamespace(UPDATE_PNGS=False)))
        finally:
            del sys.modules['download_cdd_structures']

    def test_calls_downloader_with_library_naming(self):
        """UPDATE_PNGS true: token read+stripped, search id coerced to str, and download_all
        invoked with prefix 'SRB-' / strip_prefix False into the configured SRB_PNG_DIR."""
        import types, tempfile
        from types import SimpleNamespace
        calls = {}
        m = types.ModuleType('download_cdd_structures')
        def _mk(tok): calls['token'] = tok; return 'SESSION'
        def _list(s, v, sr): calls['list'] = (s, v, sr); return [{'id': 7}, {'id': 8}]
        def _dl(s, v, mols, out, **kw): calls['dl'] = {'vault': v, 'mols': mols, 'out': str(out), **kw}; return (2, 0, 0)
        m.make_session, m.list_molecules_in_search, m.download_all = _mk, _list, _dl
        sys.modules['download_cdd_structures'] = m
        with tempfile.TemporaryDirectory() as d:
            tok = os.path.join(d, 'tok')
            with open(tok, 'w') as _f: _f.write('SECRET\n')
            png_dir = os.path.join(d, 'srb_png')
            params = SimpleNamespace(UPDATE_PNGS=True, CDD_VAULT=7108, CDD_SEARCH=23196193,
                                     CDD_TOKEN_FILE=tok, SRB_PNG_DIR=png_dir)
            try:
                px.DATA().download_cdd_pngs(params)
            finally:
                del sys.modules['download_cdd_structures']
        # token read from the file and stripped before make_session
        self.assertEqual(calls['token'], 'SECRET')
        # vault passed through; search id coerced to string
        self.assertEqual(calls['list'][1], 7108)
        self.assertEqual(calls['list'][2], '23196193')
        # library filename convention + configured output dir + the listed molecules
        self.assertEqual(calls['dl']['prefix'], 'SRB-')
        self.assertFalse(calls['dl']['strip_prefix'])
        self.assertEqual(calls['dl']['out'], png_dir)
        self.assertEqual(calls['dl']['vault'], 7108)
        self.assertEqual(len(calls['dl']['mols']), 2)


class TestVolcanoDedup(unittest.TestCase):
    """Volcano render dedup: the base (grey cloud + significant points + axes) is rendered
    ONCE per experiment and the focal-gene ring/label is overlaid cheaply per gene, so many
    (gene, experiment) cells share one matplotlib render. Uses synthetic data (no fixture)."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        import xml.etree.ElementTree as ET
        cls.ET = ET
        from python import functions as F
        cls.F = F
        rng = np.random.RandomState(0)
        n = 300
        lf = rng.normal(0, 2.5, n)
        pv = 10.0 ** (-np.abs(rng.normal(0, 2.2, n)))
        sig = ((np.abs(lf) >= 1.0) & (pv <= 0.05)).astype(int)
        df = pd.DataFrame({'compound': 'UC1', 'genes': [f'G_{i:03d}' for i in range(n)],
                           'logfc': lf, 'pvalue': pv, 'significant': sig})
        df.loc[0, ['logfc', 'pvalue', 'significant']] = [4.0, 1e-6, 1]   # known focal position
        cls.df, cls.n_sig, cls.genes = df, int(sig.sum()), set(df['genes'])
        cls.base, cls.geom = F._volcano_base_svg(df, 'UC1', key='compound',
                                                 sig_col='significant', xmin=-8, xmax=8, size_px=350)

    def test_base_valid_and_titled(self):
        """The base SVG is valid XML, is a fixed 252pt square (no tight-bbox crop), and carries
        exactly one <title> gene-name tooltip per significant point (no ring baked in)."""
        root = self.ET.fromstring(self.base)   # parses as XML
        # fixed geometry -> exactly size_px*0.72 square, aspect 1
        self.assertAlmostEqual(self.geom['W'], 252.0, places=1)
        self.assertAlmostEqual(self.geom['H'], 252.0, places=1)
        # one gene-name <title> per significant point
        titles = [t.text for t in root.iter() if t.tag.endswith('title') and t.text in self.genes]
        self.assertEqual(len(titles), self.n_sig)
        # the base has no focal ring (that is overlaid per gene)
        self.assertNotIn('<g id="tgt-ring">', self.base)

    def test_ring_position_analytic(self):
        """_apply_ring places the focal ring at the analytically-expected image fraction and
        returns matching (fx, fy, aspect) for the cross-plate trace line."""
        g = self.geom
        self.assertEqual(g['xy']['G_000'], (4.0, 6.0))   # -log10(1e-6) = 6
        svg, fx, fy, asp = self.F._apply_ring(self.base, g, 'G_000', return_pos=True)
        exp_fx = g['L'] + (4.0 - g['xmin']) / (g['xmax'] - g['xmin']) * (g['R'] - g['L'])
        exp_fy = 1 - (g['B'] + (6.0 - g['ymin']) / (g['ymax'] - g['ymin']) * (g['T'] - g['B']))
        # returned fraction matches the closed-form axis->image mapping
        self.assertAlmostEqual(fx, exp_fx, places=3)
        self.assertAlmostEqual(fy, exp_fy, places=3)
        # square image -> aspect 1
        self.assertEqual(asp, 1.0)
        # the drawn ring circle sits at (fx*W, fy*H)
        circ = [e for e in self.ET.fromstring(svg).iter() if e.tag.endswith('circle')]
        self.assertTrue(circ)
        self.assertAlmostEqual(float(circ[0].get('cx')), fx * g['W'], places=1)
        self.assertAlmostEqual(float(circ[0].get('cy')), fy * g['H'], places=1)

    def test_base_shared_across_focal_genes(self):
        """Two focal genes reuse the identical base body — only the tgt-ring overlay differs.
        This is the dedup invariant: the expensive render is not repeated per gene."""
        a = self.F._apply_ring(self.base, self.geom, 'G_000')
        b = self.F._apply_ring(self.base, self.geom, 'G_001')

        def strip(s):
            i = s.rfind('<g id="tgt-ring">')
            return s if i == -1 else s[:i] + s[s.rfind('</svg>'):]
        # everything except the ring overlay is byte-identical
        self.assertEqual(strip(a), strip(b))
        # each carries its own focal label
        self.assertIn('>G_000<', a)
        self.assertIn('>G_001<', b)

    def test_absent_focal_gene_no_ring(self):
        """A focal gene with no data in the experiment yields the base unchanged (no ring)."""
        svg = self.F._apply_ring(self.base, self.geom, 'NOT_A_GENE')
        # no ring overlay is injected when the gene is absent
        self.assertNotIn('<g id="tgt-ring">', svg)


class TestBuildOldDf(unittest.TestCase):
    """DATA.build_old_df (DFRAW_OVERWRITE=true) rebuilds df_raw + MS from the three old proteomics exports.
    fn.load_proteomics_data is mocked with synthetic frames, so no real file is read."""

    def test_build_old_df(self):
        """Input: 3 mocked exports (2, 3 and 4 rows); the third has the short Vault column names. Expected:
        df_raw has 9 rows in export order; MS has [compound, ndown, origin, activity, date], one row per export,
        and the third compound comes from its batch ID; exports 2 and 3 use mode='cddvault'. Rationale: this is
        the recipe that made the current DFRAW_PATH and MS_PATH files (MS_ML notebook MS_cytotox)."""
        import tempfile
        from unittest import mock
        from types import SimpleNamespace
        nd, act = 'MSData - Proteomics activities: Nr. Down', 'MSData - Proteomics activities: Cmpd Activity'
        def raw(n, c): return pd.DataFrame({'genes': [f'G_{i:03d}' for i in range(n)], 'logfc': -1.0, 'compound': c})
        rets = [(raw(2, 'SRB-0000001'), pd.DataFrame({'Molecule Name': ['SRB-0000001'], nd: [5], act: ['Single']})),
                (raw(3, 'SRB-0000002'), pd.DataFrame({'Molecule Name': ['SRB-0000002'], nd: [7], act: ['Low']})),
                (raw(4, 'SRB-0000003'), pd.DataFrame({'Molecule-Batch ID': ['SRB-0000003-002'], 'Nr. Down': [9],
                                                      'Cmpd Activity': ['Medium']}))]
        with tempfile.TemporaryDirectory() as d:
            p = SimpleNamespace(RAW_PROTEOMICS_PATH='r', CLEAN_PROTEOMICS_PATH='c', PX_20260520_DB='db20',
                                PX_20260520_CDDVAULT='v20', PX_20260529_DB='db29', PX_20260529_CDDVAULT='v29',
                                DFRAW_PATH=os.path.join(d, 'df_raw.parquet'), MS_PATH=os.path.join(d, 'MS.parquet'),
                                dbx=lambda path: path)    # the 'local' backend: paths pass as they are
            with mock.patch.object(px.fn, 'load_proteomics_data', side_effect=rets) as lpd:
                px.DATA().build_old_df(p)
            df_raw, MS = pd.read_parquet(p.DFRAW_PATH), pd.read_parquet(p.MS_PATH)
        # df_raw = the three exports, in export order
        self.assertEqual(df_raw['compound'].tolist(), ['SRB-0000001'] * 2 + ['SRB-0000002'] * 3 + ['SRB-0000003'] * 4)
        # MS has the schema of the current MS_PATH file
        self.assertEqual(MS.columns.tolist(), ['compound', 'ndown', 'origin', 'activity', 'date'])
        # one MS row per export; the third compound comes from its batch ID
        self.assertEqual(MS['compound'].tolist(), ['SRB-0000001', 'SRB-0000002', 'SRB-0000003'])
        self.assertEqual(MS['ndown'].tolist(), [5, 7, 9])
        self.assertEqual(MS['activity'].tolist(), ['Single', 'Low', 'Medium'])
        # the origin names the export, and the date comes from the origin
        self.assertEqual(MS['origin'].tolist(), ['MS20260429', 'MS20260520', 'MS20260529'])
        self.assertEqual(MS['date'].dt.strftime('%Y-%m-%d').tolist(), ['2026-04-29', '2026-05-20', '2026-05-29'])
        # each export reads its own two files
        self.assertEqual([c.args for c in lpd.call_args_list], [('r', 'c'), ('db20', 'v20'), ('db29', 'v29')])
        # the first export uses the default mode; the two Vault exports use mode='cddvault'
        self.assertEqual([c.kwargs.get('mode') for c in lpd.call_args_list], [None, 'cddvault', 'cddvault'])

    def test_load_old_df_rebuilds_only_when_asked(self):
        """Input: DFRAW_OVERWRITE false, then true, with build_old_df mocked. Expected: no rebuild, then one
        rebuild; both runs load the files. Rationale: a normal build must never rewrite DFRAW_PATH."""
        import tempfile
        from unittest import mock
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            p = SimpleNamespace(DFRAW_PATH=os.path.join(d, 'df_raw.parquet'), MS_PATH=os.path.join(d, 'MS.parquet'))
            pd.DataFrame({'genes': ['G_001', 'G_002'], 'MSPlate': ['P1', 'P1'], 'logfc': [-1.0, 0.5],
                          'pvalue': [0.01, 0.5], 'significant': [1, 0]}).to_parquet(p.DFRAW_PATH)
            pd.DataFrame({'compound': ['SRB-0000001'], 'ndown': [5]}).to_parquet(p.MS_PATH)
            for flag, n_calls in ((False, 0), (True, 1)):
                p.DFRAW_OVERWRITE = flag
                data = px.DATA()
                with mock.patch.object(px.DATA, 'build_old_df') as build:
                    data.load_old_df(p)
                # build_old_df runs only when DFRAW_OVERWRITE is true
                self.assertEqual(build.call_count, n_calls, f'DFRAW_OVERWRITE={flag}')
                # the files are loaded in both cases
                self.assertEqual((len(data.df_raw), len(data.MS)), (2, 1))

    def test_load_old_df_stops_on_p0(self):
        """Input: a df_raw file with p = 0 on a significant-down row (a file written before the exact parse).
        Expected: ValueError that names DFRAW_OVERWRITE. Rationale: the score -log10(p) * -logfc is infinite there,
        and the clip at 100 that hid it is gone (2026-10-26)."""
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            p = SimpleNamespace(DFRAW_PATH=os.path.join(d, 'df_raw.parquet'), MS_PATH=os.path.join(d, 'MS.parquet'),
                                DFRAW_OVERWRITE=False)
            pd.DataFrame({'genes': ['G_001'], 'MSPlate': ['P1'], 'logfc': [-1.0], 'pvalue': [0.0],
                          'significant': [1]}).to_parquet(p.DFRAW_PATH)
            pd.DataFrame({'compound': ['SRB-0000001'], 'ndown': [5]}).to_parquet(p.MS_PATH)
            # the load stops and names the rebuild flag
            with self.assertRaisesRegex(ValueError, 'DFRAW_OVERWRITE'):
                px.DATA().load_old_df(p)


class TestGeneMsScore(unittest.TestCase):
    """gene_ms_score (user decision 2026-10-26): the dot's z is the validation score of a validation target, else
    the highest FBX score, else the highest legacy (April-May) score of a significant-down row, else 0. Raw scale."""

    MS = pd.DataFrame({'genes': ['G1', 'G2', 'G2', 'G3', 'G3', 'G4'],
                       'source': ['FBX', 'FBX', 'df_raw', 'df_raw', 'df_raw', 'df_raw'],
                       'logfc': [None, None, -2.0, -1.0, 1.5, 0.8],
                       'ms_score': [120.0, 30.0, 90.0, 40.0, -7.0, -3.0]}).astype({'genes': 'category', 'source': 'category'})

    def test_rule(self):
        """Input: G1 a validation target (50) with FBX 120; G2 FBX 30 and legacy 90; G3 legacy only (a down row 40
        and an up row); G4 a significant-up legacy row only; G5 a target with no mscore row. Expected: G1 50
        validation, G2 30 FBX, G3 40 April-May, G4 0 with no source, G5 20 validation. Rationale: an FBX score wins
        over a higher legacy score (BCL6), and the legacy score fills only the genes with no FBX score."""
        z = px.gene_ms_score(self.MS, pd.Series({'G1': 50.0, 'G5': 20.0})).set_index('genes')
        # the score of each gene
        self.assertEqual(z['ms_score'].to_dict(), {'G1': 50.0, 'G2': 30.0, 'G3': 40.0, 'G4': 0.0, 'G5': 20.0})
        # the source of each score
        self.assertEqual(z['ms_source'].to_dict(),
                         {'G1': 'validation', 'G2': 'FBX', 'G3': 'April-May', 'G4': '', 'G5': 'validation'})

    def test_row_without_gene_dropped(self):
        """Input: the rows of MS plus an FBX row with no gene (pandas reads the symbol 'NA' as missing). Expected: no
        gene dot without a name. Rationale: a NaN gene went into __AREA_DATA__, and JSON.parse stopped the page script
        (no dots, no target filters; found 2026-10-26)."""
        ms = pd.concat([self.MS, pd.DataFrame({'genes': [None], 'source': ['FBX'], 'logfc': [None], 'ms_score': [50.0]})],
                       ignore_index=True).astype({'genes': 'category', 'source': 'category'})
        z = px.gene_ms_score(ms, None)
        # every dot has a gene name, and the four named genes stay
        self.assertFalse(z['genes'].isna().any())
        self.assertEqual(sorted(z['genes']), ['G1', 'G2', 'G3', 'G4'])

    def test_no_validation_score(self):
        """Input: no validation scores (None). Expected: G1 takes its FBX score. Rationale: a build without
        validation data keeps working."""
        z = px.gene_ms_score(self.MS, None).set_index('genes')
        # the FBX score, from the FBX source
        self.assertEqual((z.loc['G1', 'ms_score'], z.loc['G1', 'ms_source']), (120.0, 'FBX'))


class TestGeneAssociation(unittest.TestCase):
    """gene_association (user decision 2026-10-26): with the RDS opentarget table, a validation target's
    px_ms_score_glues association first, else the opentarget value; without it, the max OT_CACHE overall_score."""

    OT = pd.DataFrame({'target_symbol': ['G1', 'G1', 'G2'], 'overall_score': [0.2, 0.7, 0.4]})

    def test_ot_cache(self):
        """Input: no opentarget table (None). Expected: each gene's highest OT_CACHE overall_score. Rationale: config
        ASSOC_SOURCE 'ot_cache' keeps the y of the builds before 2026-10-26."""
        a = px.gene_association(self.OT, pd.Series({'G1': 0.9}), None)
        # the OT_CACHE max, the validation value is not used
        self.assertEqual(a.to_dict(), {'G1': 0.7, 'G2': 0.4})

    def test_opentarget(self):
        """Input: validation values G1 0.9, G3 empty, G4 0.3 (G4 not in opentarget); opentarget G1 0.5, G2 0.6, G3 0.1.
        Expected: G1 0.9, G2 0.6, G3 0.1, G4 0.3. Rationale: px_ms_score_glues > opentarget; an empty validation
        value falls back to opentarget; a target that opentarget lacks (HSPB11 in the real data) keeps its value."""
        a = px.gene_association(self.OT, pd.Series({'G1': 0.9, 'G3': float('nan'), 'G4': 0.3}),
                                pd.Series({'G1': 0.5, 'G2': 0.6, 'G3': 0.1}))
        # the validation value first, else opentarget
        self.assertEqual(a.to_dict(), {'G1': 0.9, 'G2': 0.6, 'G3': 0.1, 'G4': 0.3})


class TestValidationExperimentScores(unittest.TestCase):
    """validation_experiment_scores (user decision 2026-10-26): the MS slider filters each experiment, and a
    validation target's own validation experiments use its validation score."""

    def test_rule(self):
        """Input: G1 (validation score 50) as a hit on its own validation contrast, a tested-target row, a stem
        completion and a hit on a primary reference plate; G2 a hit on a contrast that names only G1; G3 named but
        with no validation score. Expected: 50 for the G1 hit and the G1 target row, NaN for all others. Rationale:
        the validation sets use the validation score, the primary screen plates their own score."""
        import numpy as np
        cdf = pd.DataFrame({'gene': ['G1', 'G1', 'G1', 'G1', 'G2', 'G3'],
                            'uniquecontrast': ['u1', 'u2', 'u3', 'u4', 'u1', 'u5'],
                            'plate': ['P1WT', 'P1KO', 'P2KO', 'Pw50', 'P1WT', 'P3WT'],
                            'is_completion': [False, True, True, False, False, False],
                            'is_target': [False, True, False, False, False, False]})
        rep = pd.DataFrame({'uniquecontrast': ['u1', 'u2', 'u3', 'u4', 'u5'], 'target': ['G1', 'G1', 'G1;G9', 'G1', 'G3']})
        s = px.validation_experiment_scores(cdf, rep, pd.Series({'G1': 50.0}), ['P1WT', 'P1KO', 'P2KO', 'P3WT'])
        # the validation score only on the G1 hit and the G1 tested-target row of its validation plates
        np.testing.assert_array_equal(s.to_numpy(), [50.0, 50.0, np.nan, np.nan, np.nan, np.nan])


class TestResolveOutputDir(unittest.TestCase):
    """--output_dir: a local path is the build dir; the config PUBLISH_URL builds in PUBLISH_STAGE_DIR
    and then publishes; any other URL stops the CLI before the build."""

    def test_resolve_output_dir(self):
        """Input: a local path, the PUBLISH_URL with and without its last slash, another URL.
        Expected: (path, False), (stage dir, True) twice, then SystemExit. Rationale: a typo in the URL
        must not start a long build."""
        from types import SimpleNamespace
        p = SimpleNamespace(PUBLISH_URL='https://host.example/Px_interface/', PUBLISH_STAGE_DIR='stage')
        # a local path is the build dir, with no publish
        self.assertEqual(px.resolve_output_dir('tmp/out', p), ('tmp/out', False))
        # the PUBLISH_URL, with or without its last slash -> the stage dir, and a publish
        self.assertEqual(px.resolve_output_dir('https://host.example/Px_interface/', p), ('stage', True))
        self.assertEqual(px.resolve_output_dir('https://host.example/Px_interface', p), ('stage', True))
        # another URL stops the CLI
        with self.assertRaises(SystemExit):
            px.resolve_output_dir('https://other.example/', p)


class TestPublish(unittest.TestCase):
    """aws_publish_targets + publish_interface with mocked AWS clients (no network): the bucket name
    comes from the account, the EC2 from its Name tag; the S3 push skips the build-only files and shows
    errors only; the EC2 copies the HTML last."""

    def _params(self):
        from types import SimpleNamespace
        return SimpleNamespace(PUBLISH_PROJECT='proj', PUBLISH_REGION='eu-north-1', PUBLISH_S3_PREFIX='interfaces/',
                               PUBLISH_WEBROOT='/var/www/webapp/Px_interface/',
                               PUBLISH_URL='https://host.example/Px_interface/',
                               PUBLISH_EXCLUDE=['*_2dtest.html', 'volcanoes_px/*.json'])

    def _session(self, n_instances=1, ping='Online', status='Success'):
        from unittest import mock
        c = {name: mock.MagicMock() for name in ('sts', 's3', 'ec2', 'ssm')}
        c['sts'].get_caller_identity.return_value = {'Account': '123456789012'}
        c['ec2'].describe_instances.return_value = {
            'Reservations': [{'Instances': [{'InstanceId': f'i-{k}'} for k in range(n_instances)]}]}
        c['ssm'].describe_instance_information.return_value = {'InstanceInformationList': [{'PingStatus': ping}]}
        c['ssm'].send_command.return_value = {'Command': {'CommandId': 'c-1'}}
        c['ssm'].get_command_invocation.return_value = {
            'Status': status, 'StandardOutputContent': 'files=3\n', 'StandardErrorContent': 'error text'}
        session = mock.MagicMock()
        session.client.side_effect = lambda name: c[name]
        return session, c

    def _publish(self, status):
        """Run publish_interface on a temp interfaces/ folder; return (aws CLI argv, SSM command lines, dir)."""
        import tempfile
        from unittest import mock
        session, c = self._session(status=status)
        with tempfile.TemporaryDirectory() as d:
            for f in ('Serac_Px_interface.html', 'Serac_Px_interface_data.js', 'Serac_Px_interface_2dtest.html',
                      'volcanoes_px/a.svg', 'volcanoes_px/positions.json'):
                os.makedirs(os.path.dirname(os.path.join(d, 'interfaces', f)), exist_ok=True)
                with open(os.path.join(d, 'interfaces', f), 'w') as fh:
                    fh.write('x')
            with mock.patch.object(px.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run, \
                 mock.patch.object(px.time, 'sleep'):
                px.publish_interface(d, self._params(), (session, 'b', 'i-0'))
        return run.call_args.args[0], c['ssm'].send_command.call_args.kwargs['Parameters']['commands'], d

    def test_targets(self):
        """Input: one running EC2, SSM Online. Expected: bucket proj-interface-<account> and that EC2.
        Rationale: git holds neither ID, so the CLI must find both at run time."""
        from unittest import mock
        session, c = self._session()
        with mock.patch.object(px.boto3, 'Session', return_value=session):
            _, bucket, iid = px.aws_publish_targets(self._params())
        # bucket = <project>-interface-<account>, and the one running <project>-instance
        self.assertEqual((bucket, iid), ('proj-interface-123456789012', 'i-0'))
        # the EC2 lookup filters on the Name tag
        self.assertIn({'Name': 'tag:Name', 'Values': ['proj-instance']},
                      c['ec2'].describe_instances.call_args.kwargs['Filters'])

    def test_targets_stop(self):
        """Input: no EC2, two EC2s, or an SSM agent that is not Online. Expected: SystemExit each time.
        Rationale: stop before the long build, not after it."""
        from unittest import mock
        for kw in ({'n_instances': 0}, {'n_instances': 2}, {'ping': 'ConnectionLost'}):
            session, _ = self._session(**kw)
            with mock.patch.object(px.boto3, 'Session', return_value=session):
                # each bad target stops the CLI
                with self.assertRaises(SystemExit, msg=str(kw)):
                    px.aws_publish_targets(self._params())

    def test_publish_interface(self):
        """Input: an interfaces/ folder with 2 build-only files. Expected: the S3 push excludes them and
        shows errors only; the EC2 stops at the first error, copies all but the HTML, then the HTML,
        then sets the nginx owner. Rationale: no page loads before its data, and no file name shows."""
        cmd, lines, d = self._publish('Success')
        # the S3 push: interfaces/ -> s3://b/interfaces/, errors only
        self.assertEqual(cmd[:5], ['aws', 's3', 'sync', os.path.join(d, 'interfaces'), 's3://b/interfaces/'])
        self.assertIn('--only-show-errors', cmd)
        # both build-only patterns are excluded
        self.assertEqual([cmd[i + 1] for i, a in enumerate(cmd) if a == '--exclude'],
                         ['*_2dtest.html', 'volcanoes_px/*.json'])
        # the EC2 stops at the first error
        self.assertEqual(lines[0], 'set -e')
        # the first copy skips the HTML, and the second copy (no exclude) brings it last
        self.assertIn("--exclude '*.html'", lines[1])
        self.assertTrue(lines[2].startswith('aws s3 sync s3://b/interfaces/ /var/www/webapp/Px_interface/'))
        self.assertNotIn('--exclude', lines[2])
        # both EC2 copies compare exact dates: a new HTML of the same size must not be skipped
        self.assertTrue(all('--exact-timestamps' in l for l in lines[1:3]))
        # nginx owns the copied files
        self.assertEqual(lines[3], 'chown -R root:nginx /var/www/webapp/Px_interface/')

    def test_publish_interface_ec2_failure(self):
        """Input: the SSM command ends Failed. Expected: SystemExit. Rationale: a failed EC2 copy must
        not look like a success."""
        # a failed EC2 sync stops the CLI with an error
        with self.assertRaises(SystemExit):
            self._publish('Failed')


class TestDropboxAccess(unittest.TestCase):
    """Dropbox readers (fn.dbx_target / dbx_open / dbx_glob / dbx_push, bound by PARAMS.setup_dropbox). The
    cluster has no Dropbox client: with DROPBOX_BACKEND 'rclone', a path under DROPBOX_LOCAL_ROOT goes to the
    rclone remote, as in MS_ML; every other path, and every path with 'local', stays on the local disk.
    subprocess.run is mocked, so no test calls rclone or the network."""

    ROOT = '/mnt/c/Users/gtamo/Serac Biosciences Dropbox/'

    def _params(self, d, backend=None):
        """PARAMS from a temp config that has the DROPBOX_* keys only when `backend` is given."""
        import yaml
        cfg = {} if backend is None else {'DROPBOX_BACKEND': backend, 'DROPBOX_REMOTE': 'dropbox:',
                                          'DROPBOX_LOCAL_ROOT': self.ROOT}
        with open(os.path.join(d, 'config.yaml'), 'w') as fh:
            yaml.safe_dump(cfg, fh)
        return px.PARAMS(os.path.join(d, 'config.yaml')).load_params()

    def test_target(self):
        """Input: a laptop Dropbox path (with '//'), a path outside the root, and prefix None. Expected:
        'dropbox:Serac_team/x/y.csv', then None two times. Rationale: the config keeps the laptop paths, and
        the cluster maps them to the remote as MS_ML does."""
        # a path under the root maps to the remote; '//' counts as one '/'
        self.assertEqual(px.fn.dbx_target(self.ROOT + 'Serac_team/x//y.csv', 'dropbox:', self.ROOT),
                         'dropbox:Serac_team/x/y.csv')
        # a path outside the root is local
        self.assertIsNone(px.fn.dbx_target('/home/gtamo/MS_ML/data/x.csv', 'dropbox:', self.ROOT))
        # backend 'local' (prefix None): every path is local
        self.assertIsNone(px.fn.dbx_target(self.ROOT + 'Serac_team/x.csv', 'dropbox:', None))

    def test_local_backend(self):
        """Input: a config with no DROPBOX_* keys, and a temp folder. Expected: dbx_push writes a csv (parent
        folders made); dbx returns the path; '*.csv' lists files only and '*/' folders only; no rclone call.
        Rationale: the laptop and the test fixtures read the disk, as before."""
        import tempfile
        from unittest import mock
        df = pd.DataFrame({'a': [1, 2], 'b': ['x', 'y']})
        with tempfile.TemporaryDirectory() as d, mock.patch('subprocess.run') as run:
            p = self._params(d)
            f = os.path.join(d, 'sub', 'f.csv')
            p.dbx_push(df, f)
            opened, back = p.dbx(f), pd.read_csv(p.dbx(f))
            files, dirs = p.dbx_glob(os.path.join(d, 'sub'), '*.csv'), p.dbx_glob(d, '*/')
        # a local path comes back as it is
        self.assertEqual(opened, f)
        # the pushed csv reads back the same (no index column)
        pd.testing.assert_frame_equal(back, df)
        # '*.csv' lists files only; '*/' lists folders only (config.yaml is a file)
        self.assertEqual((files, dirs), ([f], [os.path.join(d, 'sub')]))
        # the local backend never calls rclone
        run.assert_not_called()

    def test_rclone_backend(self):
        """Input: DROPBOX_BACKEND rclone, mocked rclone output. Expected: `rclone cat` on the mapped path gives
        a buffer pandas reads; `rclone lsf` names come back in the config form, and '[0-9]*/' keeps the date
        folders only; dbx_push sends the csv bytes to `rclone rcat`; a local path makes no rclone call.
        Rationale: the cluster reads the laptop's config paths from Dropbox directly."""
        import tempfile
        from unittest import mock
        out = {'cat': b'a,b\n1,x\n', 'lsf': b'20260601/\n20260616_02/\nnotes.txt\nold/\n', 'rcat': b''}
        fake = lambda cmd, input=None, capture_output=True: mock.Mock(returncode=0, stdout=out[cmd[1]], stderr=b'')
        root = self.ROOT + 'Serac_team/Data_Prototype'
        with tempfile.TemporaryDirectory() as d, mock.patch('subprocess.run', side_effect=fake) as run:
            p = self._params(d, 'rclone')
            df = pd.read_csv(p.dbx(root + '/20260601/x_FBX_REPORT.csv'))
            tranches = p.dbx_glob(root, '[0-9]*/')
            p.dbx_push(df, self.ROOT + 'Serac_team/out/df.csv')
            n_rclone, local = run.call_count, p.dbx(os.path.join(d, 'config.yaml'))
        cmds = [c.args[0] for c in run.call_args_list]
        # the Dropbox path streams with rclone cat from the mapped remote path
        self.assertEqual(cmds[0], ['rclone', 'cat', 'dropbox:Serac_team/Data_Prototype/20260601/x_FBX_REPORT.csv'])
        # pandas reads the streamed bytes
        self.assertEqual(df.to_dict('list'), {'a': [1], 'b': ['x']})
        # rclone lsf lists the mapped folder; only the date folders match, in the config path form
        self.assertEqual(cmds[1], ['rclone', 'lsf', 'dropbox:Serac_team/Data_Prototype'])
        self.assertEqual(tranches, [root + '/20260601', root + '/20260616_02'])
        # dbx_push uploads the csv bytes (no index) with rclone rcat
        self.assertEqual((cmds[2], run.call_args_list[2].kwargs['input']),
                         (['rclone', 'rcat', 'dropbox:Serac_team/out/df.csv'], b'a,b\n1,x\n'))
        # a local path comes back as it is, with no rclone call, also with the rclone backend
        self.assertEqual((local, run.call_count), (os.path.join(d, 'config.yaml'), n_rclone))

    def test_rclone_error_raises(self):
        """Input: rclone exits 1 with a message. Expected: OSError with that message. Rationale: a missing
        folder or an expired token must stop the build with rclone's reason, not give an empty table."""
        from unittest import mock
        fail = mock.Mock(returncode=1, stdout=b'', stderr=b'directory not found')
        with mock.patch('subprocess.run', return_value=fail):
            # the rclone message reaches the error
            with self.assertRaisesRegex(OSError, 'directory not found'):
                px.fn.dbx_glob(self.ROOT + 'Serac_team/missing', '*', 'dropbox:', self.ROOT)

    def test_tranche_folders_listed_once(self):
        """Input: the synthetic fixture, with params.dbx_glob wrapped to count its calls. Expected: one call
        for FBX_DIR plus one per date folder; TRANCHE_CSVS holds every tranche. Rationale: one rclone listing
        takes ~1 s, so load_new_df must not list a folder again for each file kind."""
        from unittest import mock
        params, _, _ = _pipeline()
        spy, data = mock.Mock(side_effect=params.dbx_glob), px.DATA()
        with mock.patch.object(params, 'dbx_glob', spy):
            data.load_new_df(params)
        n_dirs = sum(os.path.isdir(os.path.join(params.FBX_DIR, n)) for n in os.listdir(params.FBX_DIR))
        # one listing of FBX_DIR + one per date folder (this fixture has no VALIDATION_DIR)
        self.assertEqual(spy.call_count, 1 + n_dirs)
        # every tranche keeps its csv list
        self.assertEqual(sorted(data.TRANCHE_CSVS), sorted(data.FBX_TRANCHES))

    def test_pipeline_through_rclone(self):
        """Input: the synthetic fixture read through the 'rclone' backend: FBX_DIR and the PX_* exports sit
        under DROPBOX_LOCAL_ROOT, and a fake rclone serves the fixture files. Expected: the same FBX tables,
        REPORT and plate dates as the local build. Rationale: every Dropbox file arrives as a stream that
        pandas can read one time only, and CLEAN_PROTEOMICS_PATH (outside the root) stays local in that run."""
        from pathlib import Path
        from unittest import mock
        root = os.path.abspath('tmp') + '/'

        def fake(cmd, input=None, capture_output=True):   # rclone cat / lsf, served from the fixture
            local = os.path.join(root, cmd[2][len('fake:'):])
            if cmd[1] == 'cat':
                return mock.Mock(returncode=0, stdout=Path(local).read_bytes(), stderr=b'')
            names = [n + ('/' if os.path.isdir(os.path.join(local, n)) else '') for n in os.listdir(local)]
            return mock.Mock(returncode=0, stdout='\n'.join(names).encode(), stderr=b'')

        def build(params):
            data, out = px.DATA(), px.OUTPUT()
            for m in ('load_chemical_lib_df', 'load_old_df', 'load_new_df', 'get_contaminants_and_controls'):
                getattr(data, m)(params)
            out.combine_datasets(data, params)
            return data, out

        _pipeline()                                       # the fixture exists
        data_l, out_l = build(px.PARAMS(_CFG).load_params())
        p = px.PARAMS(_CFG).load_params()
        for k in ('FBX_DIR', 'PX_20260520_DB', 'PX_20260520_CDDVAULT', 'PX_20260529_DB', 'PX_20260529_CDDVAULT'):
            setattr(p, k, os.path.abspath(getattr(p, k)))
        p.DROPBOX_BACKEND, p.DROPBOX_REMOTE, p.DROPBOX_LOCAL_ROOT = 'rclone', 'fake:', root
        p.setup_dropbox()
        with mock.patch('subprocess.run', side_effect=fake) as run:
            data_r, out_r = build(p)
        n_rclone = run.call_count
        # the Dropbox reads really went through rclone
        self.assertGreater(n_rclone, 0)
        # the same tranches, by folder name
        self.assertEqual([os.path.basename(t) for t in data_r.FBX_TRANCHES], [os.path.basename(t) for t in data_l.FBX_TRANCHES])
        # the same FBX tables
        for k in ('FBX_MEASURE', 'FBX_MSSCORE', 'FBX_REPORT'):
            pd.testing.assert_frame_equal(getattr(data_r, k), getattr(data_l, k), obj=k)
        # the same REPORT (the source exports read through rclone) and the same plate dates
        pd.testing.assert_frame_equal(out_r.report, out_l.report)
        self.assertEqual(out_r.plate2date, out_l.plate2date)


class TestRdsTable(unittest.TestCase):
    """fn.rds_table: a read-only session with a verified certificate, COPY of the quoted table, credentials
    split at the first ':'. psycopg2.connect is mocked, so no database is needed."""

    def test_rds_table(self):
        """Input: a credential file 'user:pa:ss' and a fake cursor that writes a 2-row CSV. Expected: the frame of
        that CSV; connect gets user 'user', password 'pa:ss' and sslmode verify-full; the session is read-only;
        the COPY quotes the table name. Rationale: a password may hold ':', and the build never writes to the DB."""
        import tempfile
        from unittest import mock
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.copy_expert.side_effect = lambda q, buf: buf.write(b'id,plate\n1,Pw1\n2,Pw2\n')
        with tempfile.TemporaryDirectory() as d:
            cred = os.path.join(d, 'cred')
            with open(cred, 'w') as fh:
                fh.write('user:pa:ss\n')
            with mock.patch('psycopg2.connect', return_value=conn) as connect:
                df = px.fn.rds_table('px_fbx_report', 'host.example', 'postgres', cred, 'ca.pem')
        kw = connect.call_args.kwargs
        # the frame of the streamed CSV
        self.assertEqual(df.to_dict('list'), {'id': [1, 2], 'plate': ['Pw1', 'Pw2']})
        # the credentials split at the first ':', and the certificate is verified
        self.assertEqual((kw['user'], kw['password'], kw['sslmode']), ('user', 'pa:ss', 'verify-full'))
        # a read-only session, closed at the end
        conn.set_session.assert_called_once_with(readonly=True)
        conn.close.assert_called_once()
        # the COPY names the table as a quoted identifier
        self.assertIn("Identifier('px_fbx_report')", repr(cur.copy_expert.call_args.args[0]))

    def test_rds_query(self):
        """Input: the SQL text 'SELECT target FROM px_ms_score_glues;' and a fake cursor that writes a 2-row CSV.
        Expected: the frame of that CSV; a read-only session, closed at the end; the COPY wraps the query without
        the ';'. Rationale: a notebook cell runs any read-only query, and 'COPY (...;)' is a syntax error."""
        import tempfile
        from unittest import mock
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.copy_expert.side_effect = lambda q, buf: buf.write(b'target\nG1\nG2\n')
        with tempfile.TemporaryDirectory() as d, mock.patch('psycopg2.connect', return_value=conn):
            open(os.path.join(d, 'cred'), 'w').write('u:p\n')
            df = px.fn.rds_query('SELECT target FROM px_ms_score_glues;', 'h', 'postgres', os.path.join(d, 'cred'), 'ca.pem')
        # the frame of the streamed CSV
        self.assertEqual(df.to_dict('list'), {'target': ['G1', 'G2']})
        # a read-only session, closed at the end
        conn.set_session.assert_called_once_with(readonly=True)
        conn.close.assert_called_once()
        # the COPY holds the query text without the trailing ';'
        self.assertIn("SQL('SELECT target FROM px_ms_score_glues')", repr(cur.copy_expert.call_args.args[0]))


class TestExactPvalueParse(unittest.TestCase):
    """The exports and the database store p-values as plain decimals (no exponent). pandas' default float
    parser cuts a decimal after ~17 digits, so every p < ~1e-17 became 0 (found 2026-10-21: 599 RDS contrasts
    got an infinite ms score, and the volcanoes drew those points at the floor). The readers parse exactly."""

    P = '0.' + '0' * 30 + '5856726383653'          # 5.856726383653e-31 as a plain decimal

    def test_rds_table_exact(self):
        """Input: a fake COPY stream with one plain-decimal p of 5.9e-31. Expected: that exact value, not 0.
        Rationale: the MS score is -log10(p) * -logfc, so p = 0 gives an infinite score."""
        import tempfile
        from unittest import mock
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value.copy_expert.side_effect = \
            lambda q, buf: buf.write(f'id,pvalue\n1,{self.P}\n'.encode())
        with tempfile.TemporaryDirectory() as d, mock.patch('psycopg2.connect', return_value=conn):
            open(os.path.join(d, 'cred'), 'w').write('u:p\n')
            p = px.fn.rds_table('px_fbx_measure', 'h', 'postgres', os.path.join(d, 'cred'), 'ca.pem')['pvalue'].iloc[0]
        # the exact value of the decimal
        self.assertEqual(p, float(self.P))

    def test_dropbox_tranche_exact(self):
        """Input: one tranche folder whose MEASURE holds a plain-decimal p of 5.9e-31. Expected: FBX_MEASURE keeps
        the exact value. Rationale: the Dropbox exports use the same format as the database."""
        import functools, tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, '20260101'); os.makedirs(t)
            uc = 'SRB.0000001.001_vs_DMSO'
            pd.DataFrame({'uniquecontrast': [uc], 'srbnumber': ['SRB-0000001-001'], 'plate': ['Pw1']}).to_csv(f'{t}/20260101_FBX_REPORT.csv', index=False)
            pd.DataFrame({'uniquecontrast': [uc], 'genes': ['G1'], 'plate': ['Pw1'], 'ms_score': [1.0]}).to_csv(f'{t}/20260101_FBX_MSSCORE.csv', index=False)
            open(f'{t}/20260101_FBX_MEASURE.csv', 'w').write(f'uniquecontrast,genes,pg,logfc,pvalue,significant,plate\n{uc},G1,P1,-2.0,{self.P},1,Pw1\n')
            data = px.DATA()
            data.load_dropbox_fbx(SimpleNamespace(FBX_DIR=d, dbx=lambda p: p, dbx_glob=functools.partial(px.fn.dbx_glob)))
        # the exact value of the decimal
        self.assertEqual(data.FBX_MEASURE['pvalue'].iloc[0], float(self.P))

    def test_legacy_export_exact(self):
        """Input: an old proteomics export (serac mode) whose raw table holds a plain-decimal p of 5.9e-31.
        Expected: df_raw keeps the exact value. Rationale: build_old_df makes df_raw from these exports, and the
        legacy MS score is -log10(p) * -logfc (found 2026-10-26: 522 legacy rows read p = 0)."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            open(f'{d}/raw.csv', 'w').write('MoleculeBatchID,MSPlate,genes,pg,logfc,pvalue,adjpval,significant,uniquecontrast\n'
                                            f'SRB-0000001-001,Plate1,G1,P1,-2.0,{self.P},{self.P},1,SRB.0000001.001_vs_DMSO\n')
            pd.DataFrame({'CDD Number': ['C1'], 'Molecule Name': ['SRB-0000001'],
                          'MSData - Proteomics activities: Source': ['SERAC'],
                          'MSData - Proteomics activities: Date': ['2026-04-29'],
                          'MSData - Proteomics activities: Molecule-Batch ID': ['SRB-0000001-001']}).to_csv(f'{d}/ms.csv', index=False)
            df_raw, _ = px.fn.load_proteomics_data(f'{d}/raw.csv', f'{d}/ms.csv', verbose=False)
        # the exact value of the decimal
        self.assertEqual(df_raw['pvalue'].iloc[0], float(self.P))


class TestRdsSource(unittest.TestCase):
    """PX_SOURCE 'rds' (2026-10-21): the FBX tables come from the Px database plus a local archive of what it
    lacks, and the plate dates come from PX_DATES. A fake database holds the tmp_vdir exports the way the
    platform loads them: every export appended in date order (ids increase), the FBX folders in the main
    tables, the validation folders in the glues tables (no plate column). It lacks the oldest FBX folder,
    holds one contrast with another adjpval (the database wins), one new contrast with no date, and one FBX
    contrast in the glues tables only (it keeps the score of its FBX export, like Pw108 in the real data)."""

    @classmethod
    def setUpClass(cls):
        import glob, tempfile
        from unittest import mock
        make_synthetic.main('tmp_vdir')
        cls.reexported = make_synthetic.add_validation_dir('tmp_vdir')
        cls.tmp = tempfile.TemporaryDirectory()
        params = px.PARAMS('tmp_vdir/config.yaml').load_params()
        params.IFACE_DIR = os.path.join(cls.tmp.name, 'iface_dropbox')
        params.RDS_HOST = params.RDS_DATABASE = params.RDS_CRED_FILE = params.RDS_CA_BUNDLE = 'fake'
        params.PX_DATES = os.path.join(cls.tmp.name, 'PX_dates.csv')
        params.PX_ARCHIVE_DIR = os.path.join(cls.tmp.name, 'px_archive')
        cls.ref = cls._build(params)

        # the fake database: exports in date order (validation after FBX on a tie), FBX -> main, validation -> glues
        folders = sorted(glob.glob('tmp_vdir/fbx/*') + glob.glob('tmp_vdir/validation/*'),
                         key=lambda d: (os.path.basename(d), 'validation' in d))
        tables, n = {t: [] for t in ('px_fbx_measure', 'px_fbx_report', 'px_fbx_glues', 'px_fbx_glues_report')}, 0
        for d in folders:
            if os.path.basename(d) == '20260601':
                continue                                  # the database lacks the oldest FBX folder
            val = 'validation' in d
            for kind, table in (('MEASURE', 'px_fbx_glues' if val else 'px_fbx_measure'),
                                ('REPORT', 'px_fbx_glues_report' if val else 'px_fbx_report')):
                df = pd.read_csv(glob.glob(os.path.join(d, f'*_{kind}.csv'))[0])
                tables[table].append(df.assign(id=range(n, n + len(df))))
                n += len(df)
        cls.db = {t: pd.concat(f, ignore_index=True) for t, f in tables.items()}
        cls.db['px_ms_score_glues'] = pd.read_csv(glob.glob('tmp_vdir/validation/*/*_MSSCORE.csv')[0])   # per-target scores
        # opentarget: one value per gene for the first 40 genes; G_00000 has another value than its validation one,
        # and G_00001 is absent (as HSPB11 in the real data)
        g = [x for x in make_synthetic.GENES[:40] if x != make_synthetic.GENES[1]]
        cls.db['opentarget'] = pd.DataFrame({'id': range(len(g)), 'approvedsymbol': g,
                                             'associationscore': [0.99] + [round(0.02 * i, 2) for i in range(1, len(g))]})
        m, r = cls.db['px_fbx_measure'], cls.db['px_fbx_report']
        cls.changed, base = r['uniquecontrast'].iloc[0], r['uniquecontrast'].iloc[1]
        m.loc[m['uniquecontrast'] == cls.changed, 'adjpval'] = 0.123
        cls.extra = base + '_NEW'
        cls.db['px_fbx_measure'] = pd.concat([m, m[m['uniquecontrast'] == base].assign(uniquecontrast=cls.extra, plate='Pw99')])
        cls.db['px_fbx_report'] = pd.concat([r, r[r['uniquecontrast'] == base].assign(uniquecontrast=cls.extra, plate='Pw99')])
        ms_ucs = set(cls.ref[0].FBX_MSSCORE['uniquecontrast'])
        cls.moved = next(uc for uc in r['uniquecontrast'] if uc in ms_ucs and uc not in (cls.changed, base))
        for src, dst in (('px_fbx_measure', 'px_fbx_glues'), ('px_fbx_report', 'px_fbx_glues_report')):
            t = cls.db[src]
            cls.db[dst] = pd.concat([cls.db[dst], t[t['uniquecontrast'] == cls.moved]], ignore_index=True)
            cls.db[src] = t[t['uniquecontrast'] != cls.moved]

        # archive + PX_DATES from the Dropbox build, then the same build from the fake database
        with mock.patch.object(px.fn, 'rds_table', side_effect=lambda table, **kw: cls.db[table].copy()):
            px.build_px_archive(params, cls.ref[1].plate2date)
            params.PX_SOURCE, params.IFACE_DIR = 'rds', os.path.join(cls.tmp.name, 'iface_rds')
            params.ASSOC_SOURCE = 'opentarget'
            cls.rds = cls._build(params)
        cls.params = params

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def _build(params):
        """DATA -> combine -> get_iface; returns (data, output) with every frame kept."""
        data, out = px.DATA(), px.OUTPUT()
        for m in ('load_chemical_lib_df', 'load_old_df', 'load_new_df', 'get_contaminants_and_controls', 'get_gene_research'):
            getattr(data, m)(params)
        out.combine_datasets(data, params)
        out.get_de_validated(data, params)
        out.get_iface(data, params)
        return data, out

    def test_px_dates(self):
        """Input: the Dropbox build of the fixture. Expected: PX_DATES has one row per experiment of that build,
        every FBX experiment as FBX, each with its plate's date. Rationale: RDS has no date yet; this keeps them."""
        d = pd.read_csv(self.params.PX_DATES, dtype=str)
        # every FBX experiment of the build, as FBX
        self.assertEqual(set(d.loc[d['source'] == 'FBX', 'uniquecontrast']), set(self.ref[0].FBX_REPORT['uniquecontrast']))
        # one row per experiment
        self.assertFalse(d['uniquecontrast'].dropna().duplicated().any())
        # each experiment carries its plate's date of the build
        self.assertTrue((d['date'].fillna('') == d['plate'].map(self.ref[1].plate2date).fillna('')).all())

    def test_archive_holds_what_rds_lacks(self):
        """Input: a database without the 20260601 folder, and one FBX contrast in its glues tables only. Expected:
        the archive holds exactly that folder's experiments in measure, report and msscore, and the msscore archive
        also holds the glues-only contrast. Rationale: the data that RDS lacks stays available locally, and the
        formula scores the main table only."""
        import glob
        old = set(pd.read_csv(glob.glob('tmp_vdir/fbx/20260601/*_FBX_REPORT.csv')[0])['uniquecontrast'])
        for k in ('measure', 'report', 'msscore'):
            arc = pd.read_parquet(os.path.join(self.params.PX_ARCHIVE_DIR, f'fbx_{k}.parquet'))
            # the archived experiments = the experiments of the folder that the database lacks (+ the glues-only score)
            self.assertEqual(set(arc['uniquecontrast']), old | ({self.moved} if k == 'msscore' else set()), k)

    def test_measure_and_report_reproduced(self):
        """Input: the same experiments from the database + the archive. Expected: FBX_MEASURE and FBX_REPORT equal
        the Dropbox build row for row, except the one contrast whose database copy differs. Rationale: first
        reproduce the last build (user decision 2026-10-21)."""
        cols = {'FBX_MEASURE': ['uniquecontrast', 'genes', 'pg', 'plate', 'logfc', 'pvalue', 'adjpval', 'significant'],
                'FBX_REPORT': ['uniquecontrast', 'srbnumber', 'plate', 'condition', 'activity', 'nr_down', 'target']}
        for k, c in cols.items():
            a, b = (getattr(x[0], k) for x in (self.ref, self.rds))
            a, b = (x.loc[x['uniquecontrast'] != self.changed, c].sort_values(c[:3]).reset_index(drop=True) for x in (a, b))
            # the same rows and values
            pd.testing.assert_frame_equal(a, b, check_dtype=False, obj=k)

    def test_validation_ms_score(self):
        """Input: px_ms_score_glues = the per-target MSSCORE of the validation folder. Expected: the RDS build has the
        same validation scores, and the two targets sit at the same z with the same source, as the Dropbox build.
        Rationale: both sources give one validation score per target (user decision 2026-10-26)."""
        # the same validation scores
        self.assertEqual(self.rds[0].VAL_MSSCORE.to_dict(), self.ref[0].VAL_MSSCORE.to_dict())
        # the targets sit at the same z, from the validation score
        a, b = (x[1].iface_df.set_index('gene').loc[['G_00000', 'G_00001'], ['ms_score', 'ms_source']] for x in (self.ref, self.rds))
        pd.testing.assert_frame_equal(a, b)

    def test_association_from_opentarget(self):
        """Input: ASSOC_SOURCE 'opentarget' (RDS build only); a fake opentarget table where G_00000 has 0.99 and
        G_00001 is absent; validation values 0.5 / 0.2. Expected: G_00000 0.5 and G_00001 0.2 (px_ms_score_glues
        first), every other gene its opentarget value or 0. Rationale: user decision 2026-10-26 (y = px_ms_score_glues
        > opentarget; the FBX MSSCORE exports hold the same opentarget values)."""
        import numpy as np
        y = self.rds[1].iface_df.set_index('gene')['association_score']
        # the two validation targets take their px_ms_score_glues value
        self.assertEqual(y[['G_00000', 'G_00001']].tolist(), [0.5, 0.2])
        # every other gene: the opentarget value, else 0
        rest = y.drop(['G_00000', 'G_00001'])
        ot = self.db['opentarget'].set_index('approvedsymbol')['associationscore']
        self.assertTrue(np.allclose(rest, ot.reindex(rest.index).fillna(0.0)))
        # the table gave values to the build
        self.assertGreater(int((rest > 0).sum()), 0)

    def test_database_wins(self):
        """Input: one contrast whose database adjpval (0.123) differs from its Dropbox export. Expected: the RDS
        build holds the database value. Rationale: user decision 2026-10-21 (RDS wins on a conflict)."""
        m = self.rds[0].FBX_MEASURE
        # every row of that contrast carries the database value
        self.assertTrue((m.loc[m['uniquecontrast'] == self.changed, 'adjpval'] == 0.123).all())

    def test_newest_insert_wins(self):
        """Input: a validation contrast uploaded two times (the later export has logfc 3.0). Expected: only the
        newer insert's 60 rows. Rationale: mirrors "newest tranche wins" (RDS holds 336 re-analysed contrasts)."""
        rows = self.rds[0].FBX_MEASURE[self.rds[0].FBX_MEASURE['uniquecontrast'] == self.reexported]
        # 60 gene rows, all from the newer upload
        self.assertEqual(len(rows), 60)
        self.assertTrue((rows['logfc'] == 3.0).all())

    def test_undated_experiment_skipped(self):
        """Input: a database experiment on a new plate that PX_DATES does not list. Expected: it is not in the FBX
        tables. Rationale: first reproduce the last build; a new experiment needs a date."""
        data = self.rds[0]
        # not in the report and not in the measure table
        self.assertNotIn(self.extra, set(data.FBX_REPORT['uniquecontrast']))
        self.assertNotIn(self.extra, set(data.FBX_MEASURE['uniquecontrast']))

    def test_msscore(self):
        """Input: the RDS build. Expected: the main-table contrasts get ms_score = -log10(p) * -logfc on their
        significant, down-regulated rows; the archived contrasts keep their exported rows; the glues (validation)
        contrasts get no formula score. Rationale: the platform formula, and no score where the last build had none."""
        import numpy as np
        ms, meas = self.rds[0].FBX_MSSCORE, self.rds[0].FBX_MEASURE
        arc = pd.read_parquet(os.path.join(self.params.PX_ARCHIVE_DIR, 'fbx_msscore.parquet'))
        db_ms = ms[~ms['uniquecontrast'].isin(set(arc['uniquecontrast']))].merge(meas, on=['uniquecontrast', 'genes', 'pg'])
        # the formula, to float precision
        self.assertLess(float((db_ms['ms_score'] - -np.log10(db_ms['pvalue']) * -db_ms['logfc']).abs().max()), 1e-9)
        # one row per significant, down-regulated database row of the main table
        sig = meas[(meas['significant'] == 1) & (meas['logfc'] < 0) & meas['uniquecontrast'].isin(set(self.db['px_fbx_report']['uniquecontrast']))]
        self.assertEqual(len(db_ms), len(sig))
        # the archived contrasts keep their exported rows
        self.assertEqual(int(ms['uniquecontrast'].isin(set(arc['uniquecontrast'])).sum()), len(arc))
        # the only glues contrast with a score is the one with an archived export
        glues = set(self.db['px_fbx_glues']['uniquecontrast'])
        self.assertEqual(set(ms.loc[ms['uniquecontrast'].isin(glues), 'uniquecontrast']), {self.moved})

    def test_glues_contrast_keeps_exported_score(self):
        """Input: an FBX contrast that the database holds in the glues tables only (no formula score there), with
        rows in its FBX MSSCORE export. Expected: the RDS build gives it exactly the exported rows of the Dropbox
        build. Rationale: the Pw108 case of 2026-10-21; the archive fills the score that the database lacks."""
        import numpy as np
        key = ['genes', 'pg']
        a, b = (x[0].FBX_MSSCORE for x in (self.rds, self.ref))
        a, b = (x[x['uniquecontrast'] == self.moved].sort_values(key) for x in (a, b))
        # the same rows
        self.assertEqual(list(zip(a['genes'], a['pg'])), list(zip(b['genes'], b['pg'])))
        # the same scores
        np.testing.assert_allclose(a['ms_score'].to_numpy(), b['ms_score'].to_numpy())

    def test_dates_and_panels_reproduced(self):
        """Input: the RDS build. Expected: the same date for every plate of the report, and the same compound-panel
        rows (gene, compound, plate) as the Dropbox build. Rationale: the interface shows the same experiments."""
        ref_out, rds_out = self.ref[1], self.rds[1]
        plates = set(ref_out.report['plate'].astype(str))
        # the plate dates come back from PX_DATES unchanged
        self.assertEqual({p: rds_out.plate2date.get(p) for p in plates}, {p: ref_out.plate2date.get(p) for p in plates})
        # the same panel rows
        key = lambda o: set(zip(o.compounds_df['gene'], o.compounds_df['compound'], o.compounds_df['plate']))
        self.assertEqual(key(rds_out), key(ref_out))


class TestPruneVolcanoes(unittest.TestCase):
    """python/prune_volcanoes.py (2026-10-23), local folder only: it deletes the volcano SVGs that the page does not
    use, and nothing in a dry run or when the page uses a missing file. (The --s3 part uses the publish targets.)"""

    def _run(self, used, files, *flags):
        import subprocess, tempfile
        tmp = tempfile.TemporaryDirectory()
        d = os.path.join(tmp.name, 'interfaces')
        os.makedirs(os.path.join(d, 'volcanoes_px'))
        with open(os.path.join(d, 'Serac_Px_interface.html'), 'w') as f:
            f.write(f'<html>{used[0]}</html>')
        with open(os.path.join(d, 'Serac_Px_interface_data.js'), 'w') as f:
            f.write(json.dumps({'pl': used[1:]}))
        for n in files:
            open(os.path.join(d, 'volcanoes_px', n), 'w').close()
        r = subprocess.run([sys.executable, 'python/prune_volcanoes.py', d, *flags], capture_output=True, text=True)
        left = sorted(os.listdir(os.path.join(d, 'volcanoes_px')))
        tmp.cleanup()
        return r, left

    def test_prune_local(self):
        """Input: a page that uses a and b (one name in the HTML, one in data.js); the folder holds a, b, c and
        positions.json. Expected: a dry run deletes nothing; --apply deletes only c. Rationale: the data-keyed names
        leave the old files after each data change."""
        a, b, c = 'a' * 16 + '.svg', 'b' * 16 + '.svg', 'c' * 16 + '.svg'
        files = [a, b, c, 'positions.json']
        r_dry, left_dry = self._run([a, b], files)
        r_apply, left_apply = self._run([a, b], files, '--apply')
        # the dry run deletes nothing and counts one unused file
        self.assertEqual(left_dry, sorted(files))
        self.assertIn('not used 1 ', r_dry.stdout)
        # --apply deletes only the unused svg
        self.assertEqual(left_apply, sorted([a, b, 'positions.json']))

    def test_stop_when_page_uses_a_missing_file(self):
        """Input: a page that uses a file that the folder lacks. Expected: a non-zero exit and no delete, also with
        --apply. Rationale: a page with missing images means a broken build; a delete then could remove good files."""
        a, d, c = 'a' * 16 + '.svg', 'd' * 16 + '.svg', 'c' * 16 + '.svg'
        r, left = self._run([a, d], [a, c], '--apply')
        # the script stops before any delete
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(left, sorted([a, c]))


class TestExpatImportOrder(unittest.TestCase):
    """RDKit's ChemDraw library holds its own copy of expat and exports its XML_* functions. If RDKit
    Draw loads libexpat before Python's XML parser does, a later ElementTree parse (the volcano SVG)
    crashes with a segfault. functions.py imports ElementTree at the top, so expat loads first."""

    def test_rdkit_draw_then_volcano_svg(self):
        """Input: a child process imports python.functions, draws CCO with RDKit, then renders a
        volcano SVG. Expected: exit code 0. Rationale: before the fix this order crashed in
        ElementTree.fromstring; a child process keeps a crash from stopping the test runner."""
        import subprocess
        code = (
            "import python.functions as fn, pandas as pd\n"
            "from rdkit import Chem\n"
            "from rdkit.Chem import Draw\n"
            "Draw.MolToImage(Chem.MolFromSmiles('CCO'), size=(100, 100))\n"
            "df = pd.DataFrame({'compound': ['uc', 'uc'], 'genes': ['G1', 'G2'], 'logfc': [-2.0, 2.0],\n"
            "                   'pvalue': [1e-2, 1e-2], 'significant': [1, 1]})\n"
            "svg, geom = fn._volcano_base_svg(df, 'uc', key='compound')\n"
            "assert svg and geom\n")
        r = subprocess.run([sys.executable, '-c', code], cwd=_REPO_ROOT, capture_output=True, text=True,
                           timeout=300)
        # exit code 0 = no segfault (-11), and the SVG and its geometry exist
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])


if __name__ == '__main__':
    unittest.main(verbosity=2)
