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
        """load_old_df derives ms_score (clipped 0-100) and a per-(gene,plate) df_ms."""
        df = self.data.df_raw
        # ms_score and the -log10 helper column are created
        self.assertIn('ms_score', df.columns)
        self.assertIn('-log10(p-value)', df.columns)
        # ms_score respects the [0, 100] clip
        self.assertGreaterEqual(df['ms_score'].min(), 0.0)
        self.assertLessEqual(df['ms_score'].max(), 100.0)
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
        """Each compound experiment carries its OWN per-(gene,compound,plate) MS score, on the
        same scale as the plotted z (the gene's max). Real (non-completion) hit rows always have
        a score; completion rows are null (they bypass the MS filter). No gene's per-entry MS may
        exceed its plotted z — the dot sits at the gene max, entries are that max or below."""
        cdf = self.output.compounds_df
        # the per-compound MS column reached compounds_df
        self.assertIn('ms_score', cdf.columns)
        comp = cdf['is_completion'].fillna(False)
        # every real hit row has a numeric MS score
        self.assertEqual(int(cdf.loc[~comp, 'ms_score'].isna().sum()), 0)
        # completion rows carry no score (never filtered out by the MS slider)
        self.assertTrue(cdf.loc[comp, 'ms_score'].isna().all())
        # coherence: a gene's largest per-entry MS never exceeds its plotted z (the gene max)
        gmax = cdf.loc[~comp].groupby('gene')['ms_score'].max()
        z = self.output.iface_df.set_index('gene')['ms_score']
        j = pd.concat([gmax.rename('e'), z.rename('z')], axis=1).dropna()
        self.assertEqual(int((j['e'] > j['z'] + 1e-6).sum()), 0)

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
        # absent kind -> None; the kinds the tranche does have still resolve to a path
        self.assertIsNone(px._fbx_csv(self.latest, 'MSSCORE'))
        self.assertIsNotNone(px._fbx_csv(self.latest, 'MEASURE'))
        self.assertIsNotNone(px._fbx_csv(self.latest, 'REPORT'))

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
        shared = pd.read_csv(px._fbx_csv(dst, 'REPORT'), usecols=['plate'])['plate'].dropna().astype(str).unique()
        # precondition: the duplicated tranche has plates to test
        self.assertGreater(len(shared), 0)
        _later = f'{newdate[:4]}-{newdate[4:6]}-{newdate[6:8]}'
        for p in shared:
            # every shared plate resolves to the LATER folder's date (last-tranche-wins)
            self.assertEqual(out.plate2date.get(p), _later)


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


    def test_ms_score_platerows(self):
        """The MS slider filters each compound experiment by its own MS score. Every real
        plate-row in the injected panel must carry a numeric MS at index 8, and the client must
        wire that value into the per-entry filter (msOk / msLo / msHi)."""
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
    """SHOW_PLATE (config/--show_plate) picks which plate dates open default-ticked:
    empty/None -> the single latest date; a list of YYYYMMDD dates -> exactly those dates'
    plates, with YYYYMMDD normalised to the YYYY-MM-DD form plate2date stores."""

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
