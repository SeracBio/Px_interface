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
        """MS-SCORE is one row per (genes, plate)."""
        # no duplicate (genes, plate) keys survive the source-of-truth dedup
        self.assertFalse(self.output.mscore.duplicated(['genes', 'plate']).any())

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

    def test_iface_files_saved(self):
        """get_iface saves the four render inputs to IFACE_DIR."""
        d = self.params.IFACE_DIR
        # all four checkpoint files were written
        for f in ['iface_df.parquet', 'compounds_df.parquet', 'meas.parquet', 'plate2date.json']:
            self.assertTrue(os.path.exists(os.path.join(d, f)), f'{f} not saved')


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


if __name__ == '__main__':
    unittest.main(verbosity=2)
