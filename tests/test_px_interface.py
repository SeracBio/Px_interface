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
