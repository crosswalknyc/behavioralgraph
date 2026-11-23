#!/usr/bin/env python3
"""Profile IQ worldwide layout for a '<Subject> - Global' profile (Jenna 2026-11-23).

Jenna (verbatim): "I dont see any locations outside of the US, and the
demographics are all in US terms... you would have US demographics, then list
out demo by country so that you can see them all in each country's
nomenclature". Approved: a worldwide summary at the top, a country switcher
over the demographics card (United States first, then by share, then Rest of
world), LOCATION grouped by country with each country's share, countries =
every country with at least 3% of the audience plus Rest of world, the same
layout for every global profile. Rows: the GLOBAL PROFILE CONTRACT (COUNTRY,
'<DEMO> | <Country>', worldwide AGE / GENDER, 'City, Country' LOCATION).

Pins:
  * wiring: static/global-profile/global-profile.{css,js} are split files the
    validator knows, loaded once with the deploy version, the stylesheet in
    <head> and the script last before </body> (after every function it
    wraps); the page still carries those functions and calls; the page stays
    under its 13.0 MB budget;
  * the model, in node: a MARKET: GLOBAL file only (never US, never one
    country); COUNTRY rows win, a country under 3% folds into Rest of world,
    United States first then by share; with no COUNTRY rows the shares come
    from the cities; per-country blocks (any case) feed the switcher, a
    country without blocks and a country under 3% are never offered; cities
    group under their country, a world city is never a US DMA (Birmingham,
    UK); worldwide age stays as written; bucket order, plain shares, copy;
  * the parse hooks: on a global file the US DMA list keeps the US cities and
    COUNTRY plus the blocks leave the behavioral categories; a US file comes
    back exactly as the page parsed it.
The DOM itself is covered by scripts/smoke_global_profile_view.py (headless
Chromium on the real page). Hermetic: no network, no model, no S3.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

JS = os.path.join(ROOT, 'static', 'global-profile', 'global-profile.js')
CSS = os.path.join(ROOT, 'static', 'global-profile', 'global-profile.css')
INDEX = os.path.join(ROOT, 'templates', 'index.html')
FIXTURE = os.path.join(HERE, 'fixtures', 'global_profile_harbor_stout.json')
VERSION = "?v={{ (app_build_version or 'dev')|urlencode }}"


def _read(p):
    with open(p, encoding='utf-8') as fh:
        return fh.read()


# The module runs in node against a stub window / document. getDMARank knows a
# handful of US DMAs, Birmingham among them (the Alabama one), so a test can
# show that 'Birmingham, UK' still never reads as a US DMA.
_NODE = r'''
const fs = require('fs');
const vm = require('vm');
const [jsPath, casesPath] = process.argv.slice(2);
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const DMAS = {'NEW YORK': 1, 'LOS ANGELES': 2, 'CHICAGO': 3, 'BOSTON': 9, 'PHILADELPHIA': 4, 'BIRMINGHAM': 44,
              'ATLANTA': 7, 'DALLAS': 5, 'SEATTLE': 12, 'DENVER': 16, 'HOUSTON': 8, 'MIAMI': 18, 'PHOENIX': 11};
function makeWindow(withPage) {
  const listeners = {};
  const document = {
    readyState: 'complete',
    addEventListener: function (t, f) { (listeners[t] = listeners[t] || []).push(f); },
    getElementById: function () { return null; },
    querySelector: function () { return null; },
    createElement: function () { return {}; },
    body: { appendChild: function () {} }
  };
  const window = { document: document, console: { error: function () {} } };
  window.window = window;
  window.getDMARank = function (n) { const k = String(n || '').toUpperCase().replace(/,.*$/, '').trim(); return DMAS[k] || null; };
  window.matchCanonicalDMA = function (n) { const k = String(n || '').toUpperCase().replace(/,.*$/, '').replace(/\s+[A-Z]{2}$/, '').trim(); return DMAS[k] ? k : null; };
  if (withPage) {
    window.parseAllData = function (rows) {
      const out = { locations: [], behavioral: {}, categories: {}, demographics: { age: {} } };
      rows.forEach(function (r) {
        const c = String(r.Column || '').toUpperCase();
        const v = r.Value;
        if (c === 'LOCATION' && (window.getDMARank(v) != null || window.getDMARank(window.matchCanonicalDMA(v) || '') != null)) out.locations.push({ name: v, pct: Number(r['Brand Penetration (Row)']) });
        if (['BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT', 'LOCATION', 'AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION'].indexOf(c) < 0) {
          (out.behavioral[c] = out.behavioral[c] || []).push({ name: v });
          (out.categories[c] = out.categories[c] || []).push({ value: v });
        }
      });
      return out;
    };
    window.parseComparisonData = function (rows) {
      const out = { behavioral: {}, behavioralProjection: {} };
      rows.forEach(function (r) { const c = String(r.Column || '').toUpperCase(); if (c !== 'LOCATION') { out.behavioral[c] = []; out.behavioralProjection[c] = []; } });
      return out;
    };
    window._piqV4OnProfile = function () {};
    window._piqV4OnTab = function () {};
    window._piqV4OnClear = function () {};
    window.renderLocationTable = function () {};
  }
  vm.createContext(window);
  vm.runInContext(fs.readFileSync(jsPath, 'utf8'), window, { filename: 'global-profile.js' });
  return window;
}
const w = makeWindow(true);
const G = w.CWGlobal;
const out = {};
function slim(m) {
  if (!m) return null;
  return {
    market: m.market, source: m.shareSource, nCountries: m.nCountries, switcher: m.switcher, sample: m.sample, audience: m.audience,
    countries: m.countries.map(function (c) { return [c.name, Number(c.share.toFixed(4)), c.hasBlocks, !!c.rest]; }),
    blockSums: Object.keys(m.blocks).reduce(function (o, c) { o[c] = Object.keys(m.blocks[c]).reduce(function (p, d) { p[d] = Number(m.blocks[c][d].reduce(function (t, r) { return t + r.pct; }, 0).toFixed(4)); return p; }, {}); return o; }, {}),
    blockLabels: Object.keys(m.blocks).reduce(function (o, c) { o[c] = Object.keys(m.blocks[c]).reduce(function (p, d) { p[d] = m.blocks[c][d].map(function (r) { return r.label; }); return p; }, {}); return o; }, {}),
    worldAge: (m.world.AGE || []).map(function (r) { return [r.label, r.pct]; }),
    groups: m.groups.map(function (g) { return { name: g.name, share: Number(g.share.toFixed(4)), rest: g.rest, nCountries: g.nCountries || 0, cities: g.cities.map(function (x) { return x.name; }) }; }),
    topCities: m.topCities,
    headline: G.headline(m),
    csvLoc: G.csvFor(m, 'location').split('\n').slice(0, 3),
    csvLocLines: G.csvFor(m, 'location').trim().split('\n').length,
    csvDemoUk: G.csvFor(m, 'demo', 'United Kingdom').split('\n').slice(0, 3)
  };
}
out.installed = { parseAllData: !!(w.parseAllData && w.parseAllData.__cwGlobal), parseComparisonData: !!(w.parseComparisonData && w.parseComparisonData.__cwGlobal),
  renderLocationTable: !!(w.renderLocationTable && w.renderLocationTable.__cwGlobal), onProfile: !!(w._piqV4OnProfile && w._piqV4OnProfile.__cwGlobal),
  onTab: !!(w._piqV4OnTab && w._piqV4OnTab.__cwGlobal), onClear: !!(w._piqV4OnClear && w._piqV4OnClear.__cwGlobal), csv: typeof w.cwGlobalCsv };
out.reinstall = G.install();
out.markets = cases.markets.map(function (m) { return [m, G.isGlobalMarket(m)]; });
out.canon = cases.canon.map(function (c) { return [c, G.canonCountry(c)]; });
out.loc = cases.loc.map(function (c) { return [c, G.locationCountry(c)]; });
out.plain = cases.plain.map(function (p) { return [p, G.plainShare(p)]; });
out.geo = cases.geo.map(function (c) { return [c, G.isGeoColumn(c)]; });
out.order = Object.keys(cases.order).reduce(function (o, d) { o[d] = G.orderRows(d, cases.order[d].map(function (l, i) { return { label: l, pct: 10 - i }; })).map(function (r) { return r.label; }); return o; }, {});
out.joins = [G.joinNames(['London']), G.joinNames(['London', 'Lagos']), G.joinNames(['London', 'New York', 'Lagos'])];
out.lead = ['New York Ny', 'Boston Ma Manchester Nh', 'Chicago IL', 'Dallas Ft Worth Tx', 'Washington Dc Hagerstown Md', 'Houston', 'La Crosse Eau Claire Wi'].map(function (n) { return [n, G.leadCity(n)]; });
out.fixture = slim(G.build(cases.fixture));
out.fallback = slim(G.build(cases.fallback));
out.threshold = slim(G.build(cases.threshold));
out.us = G.build(cases.us);
out.single = G.build(cases.single);
const pg = w.parseAllData(cases.fixture);
out.hookGlobal = { hasModel: !!pg._cwGlobal, locations: pg.locations.map(function (l) { return l.name; }), behavioral: Object.keys(pg.behavioral).sort(), categories: Object.keys(pg.categories).sort() };
const pu = w.parseAllData(cases.us);
const raw = w.parseAllData.__cwOrig(cases.us);
out.hookUs = { hasModel: !!pu._cwGlobal, same: JSON.stringify(pu) === JSON.stringify(raw), behavioral: Object.keys(pu.behavioral).sort(), locations: pu.locations.map(function (l) { return l.name; }) };
const cg = w.parseComparisonData(cases.fixture);
const cu = w.parseComparisonData(cases.us);
out.hookCompare = { global: Object.keys(cg.behavioral).sort(), us: Object.keys(cu.behavioral).sort() };
out.copy = G.COPY;
const bare = makeWindow(false);
out.bareInstall = bare.CWGlobal.install();
process.stdout.write(JSON.stringify(out));
'''


def _fixture_rows(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)['rows']


def _row(col, val, bp, raw='', proj=''):
    return {'Column': col, 'Value': val, 'Brand Penetration (Row)': bp, 'Category Share': bp,
            'Original Raw Numbers': raw, 'US Gen Pop Projection': proj, 'Gen Pop Penetration': '', 'Index vs Gen Pop': ''}


def _fallback_rows():
    """Guinness-shaped: MARKET: GLOBAL, worldwide demos, 'City, Country'
    LOCATION, no COUNTRY rows, no per-country blocks (what the live file
    carried before the data side wrote the new rows)."""
    rows = [_row('BRAND INPUT', 'Pint House', '100.0', 291279, 17483525),
            _row('SAMPLE SIZE', 'SAMPLE SIZE (2025-11-23 TO 2026-11-23) | BEHAVIOR STUDY (2025-11-23 TO 2026-11-23) | MARKET: GLOBAL', '100.0', 291279, 17483525)]
    for lbl, v in [('25-34', '24.7903'), ('35-44', '21.0786'), ('45-54', '18.0686'), ('18-24', '13.3726'), ('55-64', '12.8949'), ('65+', '9.7950')]:
        rows.append(_row('AGE', lbl, v))
    for lbl, v in [('Male', '65.5850'), ('Female', '32.5768'), ('Non-Binary', '1.0798'), ('Trans Female', '0.5573'), ('Prefer Not to Say', '0.2011')]:
        rows.append(_row('GENDER', lbl, v))
    locs = [('London, UK', 5.7226), ('New York, USA', 3.6353), ('Lagos, Nigeria', 2.6272), ('Dublin, Ireland', 1.8969),
            ('Los Angeles, USA', 1.7624), ('Chicago, USA', 1.7300), ('Nairobi, Kenya', 1.6157), ('Birmingham, UK', 0.8940),
            ('Toronto, Canada', 1.0946), ('Abuja, Nigeria', 1.0761), ('Sydney, Australia', 0.9615), ('Manchester, UK', 0.8724)]
    # The rest of each country's people sit in cities this test does not list;
    # pad so the city shares by country land where the live file's do.
    pad = {'USA': 32.199 - (3.6353 + 1.7624 + 1.7300), 'UK': 18.942 - (5.7226 + 0.8940 + 0.8724),
           'Nigeria': 6.784 - (2.6272 + 1.0761), 'Ireland': 3.470 - 1.8969, 'Canada': 2.904 - 1.0946,
           'Australia': 2.535 - 0.9615, 'Kenya': 2.292 - 1.6157}
    for name, v in locs:
        rows.append(_row('LOCATION', name, f'{v:.4f}'))
    fillers = {'USA': 'Denver, USA', 'UK': 'Leeds, UK', 'Nigeria': 'Ibadan, Nigeria', 'Ireland': 'Cork, Ireland',
               'Canada': 'Vancouver, Canada', 'Australia': 'Melbourne, Australia', 'Kenya': 'Mombasa, Kenya'}
    for k, v in pad.items():
        rows.append(_row('LOCATION', fillers[k], f'{v:.4f}'))
    rows.append(_row('LOCATION', 'Atlantis', '0.4000'))
    rows.append(_row('SOCIAL MEDIA', 'Instagram', '58.1137'))
    return rows


def _threshold_rows():
    rows = [_row('BRAND INPUT', 'Edge Case', '100.0', 50321, 3020417),
            _row('SAMPLE SIZE', 'SAMPLE SIZE (2025-11-23 TO 2026-11-23) | MARKET: WORLDWIDE', '100.0', 50321, 3020417)]
    for name, v in [('Germany', '48.0000'), ('USA', '3.0000'), ('France', '2.9900'), ('Japan', '11.1000'), ('Rest of World', '34.9100')]:
        rows.append(_row('COUNTRY', name, v))
    rows.append(_row('AGE | GERMANY', '18-24', '100.0000'))
    rows.append(_row('LOCATION', 'Berlin, Germany', '12.1100'))
    rows.append(_row('LOCATION', 'Paris, France', '1.1100'))
    return rows


def _us_rows():
    rows = [_row('BRAND INPUT', 'Lone Star Club', '100.0', 894389, 29505893),
            _row('SAMPLE SIZE', 'SAMPLE SIZE (2025-01-01 TO 2025-12-31) | BEHAVIOR STUDY (2025-01-01 TO 2025-12-31)', '100.0', 894389, 29505893),
            _row('AGE', '25-34', '22.1100'), _row('GENDER', 'Male', '58.4400'),
            _row('LOCATION', 'Dallas Fort Worth, TX', '15.8660'), _row('LOCATION', 'New York, NY', '5.0990'),
            _row('LOCATION', 'Birmingham', '0.4410'), _row('SOCIAL MEDIA', 'Instagram', '61.2200'),
            _row('COUNTRY', 'Country Music Fans', '12.0000')]
    return rows


def _single_country_rows():
    return [_row('BRAND INPUT', 'Pub Quiz', '100.0', 30113, 1606911),
            _row('SAMPLE SIZE', 'SAMPLE SIZE (2025-11-23 TO 2026-11-23) | MARKET: UNITED KINGDOM', '100.0', 30113, 1606911),
            _row('LOCATION', 'London', '25.1000'), _row('LOCATION', 'Birmingham', '4.3900')]


class GlobalProfileView(unittest.TestCase):
    out: dict = {}

    @classmethod
    def setUpClass(cls):
        node = shutil.which('node')
        if not node:
            raise unittest.SkipTest('node is not installed')
        cases = {
            'markets': ['GLOBAL', 'Worldwide', ' world ', 'INTERNATIONAL', 'UNITED KINGDOM', 'US', ''],
            'canon': ['USA', 'U.S.', 'United States of America', 'UK', 'U.K.', 'England', 'UAE', 'NIGERIA', 'Rest of World',
                      "C\u00f4te d\u2019Ivoire", 'Czech Republic', 'trinidad and tobago', 'Ireland', 'T\u00fcrkiye'],
            'loc': ['London, UK', 'New York, USA', 'Portland, OR', 'Chicago IL', 'Toronto, Canada', 'Lagos, Nigeria',
                    'Washington DC, USA', 'Birmingham, UK', 'Atlantis', 'Dubai, UAE', 'Houston'],
            'plain': [32.2, 18.94, 50.6, 6.78, 96.0, 0.4, 61.3, 24.8, 38.6, 65.6, 3.47, 22.4],
            'geo': ['COUNTRY', 'AGE | United Kingdom', 'INCOME | NIGERIA', 'ethnicity | Ireland', 'AGE', 'LOCATION',
                    'MOST PURCHASED BRANDS', 'STREAMING/PLATFORM', 'AGE_OF_CHILDREN | Ireland'],
            'order': {
                'AGE': ['65+', '25-34', 'Prefer not to say', '18-24', 'Under 18', '35-44'],
                'INCOME': ['\u00a3100,000+', '\u00a315,000 - \u00a324,999', 'Prefer Not to Say', '\u00a350,000 - \u00a374,999', 'Under \u00a315,000'],
                'GENDER': ['Prefer Not to Say', 'Female', 'Male'],
            },
            'fixture': _fixture_rows(FIXTURE),
            'fallback': _fallback_rows(),
            'threshold': _threshold_rows(),
            'us': _us_rows(),
            'single': _single_country_rows(),
        }
        tmp = tempfile.mkdtemp(prefix='global_profile_view_')
        try:
            harness = os.path.join(tmp, 'harness.js')
            with open(harness, 'w', encoding='utf-8') as fh:
                fh.write(_NODE)
            case_path = os.path.join(tmp, 'cases.json')
            with open(case_path, 'w', encoding='utf-8') as fh:
                json.dump(cases, fh)
            proc = subprocess.run([node, harness, JS, case_path], capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                raise AssertionError('node harness failed:\n' + proc.stderr[-4000:])
            cls.out = json.loads(proc.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- wiring --------------------------------------------------------------

    def test_files_are_split_files_the_validator_knows(self):
        import validate_index_html as v
        rels = [rel for _ref, rel, _kind, _floor in v.SPLIT_FILES]
        self.assertIn('static/global-profile/global-profile.css', rels)
        self.assertIn('static/global-profile/global-profile.js', rels)
        self.assertTrue(any(a == 'window.CWGlobal' for a, _d in v.REQUIRED_ANCHORS))
        for path in (JS, CSS):
            src = _read(path)
            for tok in ('{{', '{%', '{#'):
                self.assertNotIn(tok, src, f'{os.path.basename(path)} carries Jinja ({tok})')
            self.assertNotIn('\u2014', src, 'no em dashes')
            self.assertNotIn('\u2013', src, 'no en dashes')
        node = shutil.which('node')
        if node:
            proc = subprocess.run([node, '--check', JS], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_page_loads_each_file_once_in_the_right_place(self):
        idx = _read(INDEX)
        css_ref = '<link rel="stylesheet" href="/static/global-profile/global-profile.css' + VERSION + '">'
        js_ref = '<script src="/static/global-profile/global-profile.js' + VERSION + '"></script>'
        self.assertEqual(idx.count('/static/global-profile/global-profile.css'), 1)
        self.assertEqual(idx.count('/static/global-profile/global-profile.js'), 1)
        self.assertIn(css_ref, idx)
        self.assertIn(js_ref, idx)
        head_end = idx.find('\n</head>\n')
        self.assertGreater(head_end, 0)
        self.assertLess(idx.find(css_ref), head_end, 'the stylesheet loads in <head>')
        body_end = idx.rfind('</body>')
        js_at = idx.find(js_ref)
        self.assertGreater(js_at, 0)
        self.assertEqual(idx[js_at + len(js_ref):body_end].strip(), '', 'the script is the last thing before </body>')
        self.assertEqual(idx[js_at:].count('<script'), 1, 'no inline script runs after it')
        for marker in ('function parseAllData(csvData)', 'function renderLocationTable(locations)',
                       'function parseComparisonData(csvData)', 'window._piqV4OnProfile = function(parsed)',
                       'window._piqV4OnTab = function(tabName)', 'window._piqV4OnClear = function()'):
            at = idx.find(marker)
            self.assertGreater(at, 0, f'the page defines {marker}')
            self.assertLess(at, js_at, f'{marker} is defined before the module loads')
        self.assertGreaterEqual(idx.count('window._piqV4OnProfile(parsed)'), 2, 'both profile paths call the v4 profile hook')
        self.assertIn("var fn = btn.getAttribute('data-piq-csv-fn');", idx, 'the house CSV button can call a named function')
        for anchor in ('id="tab-location"', 'id="tab-demographics"', 'id="dashboardContent"'):
            self.assertIn(anchor, idx)
        self.assertIn("const marketMatch = String(value || '').match(/MARKET\\s*:\\s*([^|]+?)(?:\\s*\\||\\s*$)/i);", idx,
                      'the page reads MARKET the same way the module does')
        self.assertLess(len(idx.encode('utf-8')), 13_000_000, 'index.html stays under its 13.0 MB budget')

    def test_the_demographic_charts_never_read_an_undeclared_baseline(self):
        # GENPOP_DEMOGRAPHICS is a const inside parseAllData. renderCharts read it
        # bare in the relationship fallback, which a worldwide profile reaches:
        # the chart pass threw, and on the US % / US POP # toggle the rest of the
        # page was never redrawn.
        idx = _read(INDEX)
        start = idx.find('function parseAllData(csvData)')
        end = idx.find('\n        function ', start + 40)
        lines = (idx[:start] + idx[end:]).split('\n')
        unguarded = []
        for i, line in enumerate(lines):
            if not re.search(r'(?<![\w.])GENPOP_DEMOGRAPHICS(?!\w)', line) or line.strip().startswith('//'):
                continue
            if not any('typeof GENPOP_DEMOGRAPHICS' in lines[j] for j in range(max(0, i - 2), i + 1)):
                unguarded.append(line.strip()[:120])
        self.assertEqual(unguarded, [], 'every read outside parseAllData is guarded')
        self.assertIn("const _gpDemo = (typeof GENPOP_DEMOGRAPHICS !== 'undefined') ? GENPOP_DEMOGRAPHICS : null;", idx)

    def test_hooks_install_once_and_never_on_a_missing_function(self):
        o = self.out
        self.assertEqual(o['installed'], {'parseAllData': True, 'parseComparisonData': True, 'renderLocationTable': True,
                                          'onProfile': True, 'onTab': True, 'onClear': True, 'csv': 'function'})
        self.assertTrue(all(o['reinstall'].values()), o['reinstall'])
        self.assertFalse(any(o['bareInstall'].values()), 'no page functions, nothing wrapped')

    # ---- the model -----------------------------------------------------------

    def test_only_a_global_market_gets_the_layout(self):
        got = dict(self.out['markets'])
        self.assertTrue(got['GLOBAL'] and got['Worldwide'] and got[' world '] and got['INTERNATIONAL'])
        self.assertFalse(got['UNITED KINGDOM'] or got['US'] or got[''])
        self.assertIsNone(self.out['us'], 'a US file never builds the model')
        self.assertIsNone(self.out['single'], 'a single-country file never builds the model')

    def test_country_names_read_the_same_however_they_are_written(self):
        c = dict(self.out['canon'])
        for k in ('USA', 'U.S.', 'United States of America'):
            self.assertEqual(c[k], 'United States')
        for k in ('UK', 'U.K.', 'England'):
            self.assertEqual(c[k], 'United Kingdom')
        self.assertEqual(c['UAE'], 'United Arab Emirates')
        self.assertEqual(c['NIGERIA'], 'Nigeria')
        self.assertEqual(c['Rest of World'], 'Rest of world')
        self.assertEqual(c["C\u00f4te d\u2019Ivoire"], 'Ivory Coast')
        self.assertEqual(c['Czech Republic'], 'Czechia')
        self.assertEqual(c['trinidad and tobago'], 'Trinidad and Tobago')
        self.assertEqual(c['Ireland'], 'Ireland')
        self.assertEqual(c['T\u00fcrkiye'], 'Turkey', 'accents fold before matching')

    def test_a_city_reads_its_country_and_a_world_city_is_never_a_us_dma(self):
        loc = {k: (v['city'], v['country']) for k, v in self.out['loc']}
        self.assertEqual(loc['London, UK'], ('London', 'United Kingdom'))
        self.assertEqual(loc['New York, USA'], ('New York', 'United States'))
        self.assertEqual(loc['Portland, OR'][1], 'United States')
        self.assertEqual(loc['Chicago IL'][1], 'United States')
        self.assertEqual(loc['Houston'][1], 'United States', 'a bare US DMA reads as the US')
        self.assertEqual(loc['Washington DC, USA'], ('Washington DC', 'United States'))
        self.assertEqual(loc['Birmingham, UK'], ('Birmingham', 'United Kingdom'))
        self.assertEqual(loc['Dubai, UAE'][1], 'United Arab Emirates')
        self.assertEqual(loc['Atlantis'], ('Atlantis', ''), 'no country named: Rest of world')

    def test_contract_file_countries_order_threshold_and_switcher(self):
        f = self.out['fixture']
        self.assertEqual(f['source'], 'country')
        names = [c[0] for c in f['countries']]
        self.assertEqual(names, ['United States', 'United Kingdom', 'Nigeria', 'Ireland', 'Rest of world'])
        shares = {c[0]: c[1] for c in f['countries']}
        self.assertAlmostEqual(shares['United States'], 31.4127, places=4)
        self.assertAlmostEqual(shares['Rest of world'], 32.6966 + 2.7184, places=4, msg='Canada (2.7%) folds into Rest of world')
        self.assertAlmostEqual(sum(shares.values()), 100.0, places=3)
        self.assertEqual(f['switcher'], ['United States', 'United Kingdom', 'Nigeria', 'Ireland', 'Rest of world'],
                         'US first, then by share, then Rest of world; Canada has blocks but is under 3%')
        for country, demos in f['blockSums'].items():
            for demo, total in demos.items():
                self.assertAlmostEqual(total, 100.0, places=2, msg=f'{demo} | {country} sums to 100')
        self.assertIn('Yoruba', f['blockLabels']['Nigeria']['ETHNICITY'])
        self.assertIn('25-34', f['blockLabels']['Nigeria']['AGE'], "an upper-case 'AGE | NIGERIA' still reads as Nigeria")
        self.assertIn('White British', f['blockLabels']['United Kingdom']['ETHNICITY'])
        self.assertIn('\u00a335,000 - \u00a349,999', f['blockLabels']['United Kingdom']['INCOME'])
        self.assertEqual(f['sample'], 184267)
        self.assertEqual(f['audience'], 11063279)

    def test_contract_file_cities_group_under_their_country(self):
        f = self.out['fixture']
        groups = {g['name']: g for g in f['groups']}
        self.assertEqual([g['name'] for g in f['groups']], ['United States', 'United Kingdom', 'Nigeria', 'Ireland', 'Rest of world'])
        us = groups['United States']['cities']
        self.assertEqual(us[0], 'New York')
        self.assertIn('Chicago IL', us)
        self.assertNotIn('Birmingham', us)
        self.assertIn('Birmingham', groups['United Kingdom']['cities'])
        rest = groups['Rest of world']
        self.assertIn('Toronto, Canada', rest['cities'], 'a city of a country under 3% lists inside Rest of world, its country named')
        self.assertIn('Nairobi, Kenya', rest['cities'])
        self.assertGreaterEqual(rest['nCountries'], 5)
        self.assertEqual(f['topCities'], ['London', 'New York', 'Lagos'])
        self.assertEqual(f['headline'], 'About 3 in 10 people in this audience live in the United States.')
        self.assertEqual(f['csvLoc'][0], 'Country,Country share of the audience (%),City,City share of the audience (%)')
        self.assertEqual(f['csvLocLines'], 1 + 32, 'one CSV line per city')
        self.assertEqual(f['csvDemoUk'][0], 'Country,Category,Group,Share (%)')
        self.assertTrue(f['csvDemoUk'][1].startswith('United Kingdom,Age,18-24,'))

    def test_without_country_rows_the_cities_set_the_shares(self):
        f = self.out['fallback']
        self.assertEqual(f['source'], 'location')
        self.assertEqual(f['switcher'], [], 'no per-country blocks: the switcher hides')
        shares = {c[0]: c[1] for c in f['countries']}
        self.assertEqual(list(shares), ['United States', 'United Kingdom', 'Nigeria', 'Ireland', 'Rest of world'])
        self.assertAlmostEqual(shares['United States'], 32.199, places=3)
        self.assertAlmostEqual(shares['United Kingdom'], 18.942, places=3)
        self.assertAlmostEqual(shares['Nigeria'], 6.784, places=3)
        self.assertAlmostEqual(shares['Ireland'], 3.470, places=3)
        self.assertAlmostEqual(shares['Rest of world'], 100 - (32.199 + 18.942 + 6.784 + 3.470), places=3)
        rest = [g for g in f['groups'] if g['rest']][0]
        self.assertIn('Atlantis', rest['cities'], 'a city with no country stays in Rest of world as written')
        self.assertIn('Toronto, Canada', rest['cities'])
        self.assertEqual(f['headline'], 'About 1 in 3 people in this audience live in the United States.')
        self.assertEqual([a[0] for a in f['worldAge']][-1], '65+', 'worldwide age as written')
        self.assertIn(['65+', 9.795], f['worldAge'], "65+ is never split into 55-64 and 65 or Older")

    def test_three_percent_is_in_and_just_under_is_out(self):
        t = self.out['threshold']
        names = [c[0] for c in t['countries']]
        self.assertEqual(names, ['United States', 'Germany', 'Japan', 'Rest of world'],
                         'US at exactly 3.0 shows and leads; France at 2.99 folds')
        shares = {c[0]: c[1] for c in t['countries']}
        self.assertAlmostEqual(shares['Rest of world'], 34.91 + 2.99, places=4)
        self.assertEqual(t['switcher'], ['Germany'])
        self.assertEqual(t['market'], 'WORLDWIDE')

    def test_buckets_read_in_order(self):
        o = self.out['order']
        self.assertEqual(o['AGE'], ['Under 18', '18-24', '25-34', '35-44', '65+', 'Prefer not to say'])
        self.assertEqual(o['INCOME'], ['Under \u00a315,000', '\u00a315,000 - \u00a324,999', '\u00a350,000 - \u00a374,999', '\u00a3100,000+', 'Prefer Not to Say'])
        self.assertEqual(o['GENDER'], ['Female', 'Male', 'Prefer Not to Say'])
        self.assertEqual(self.out['joins'], ['London', 'London and Lagos', 'London, New York and Lagos'])
        lead = dict(self.out['lead'])
        self.assertEqual(lead['New York Ny'], 'New York', 'a sentence names the lead city of a US DMA')
        self.assertEqual(lead['Boston Ma Manchester Nh'], 'Boston')
        self.assertEqual(lead['Chicago IL'], 'Chicago')
        self.assertEqual(lead['Dallas Ft Worth Tx'], 'Dallas Ft Worth')
        self.assertEqual(lead['Washington Dc Hagerstown Md'], 'Washington')
        self.assertEqual(lead['Houston'], 'Houston')
        self.assertEqual(lead['La Crosse Eau Claire Wi'], 'La Crosse Eau Claire', 'a leading two-letter word is part of the name')

    def test_every_share_has_a_plain_comparison(self):
        p = {float(k): v for k, v in self.out['plain']}
        self.assertEqual(p[32.2], 'about 1 in 3')
        self.assertEqual(p[18.94], 'about 1 in 5')
        self.assertEqual(p[50.6], 'about half')
        self.assertEqual(p[6.78], 'about 7 in 100')
        self.assertEqual(p[96.0], 'nearly all')
        self.assertEqual(p[0.4], 'fewer than 1 in 100')
        self.assertEqual(p[61.3], 'about 3 in 5')
        self.assertEqual(p[24.8], 'about 1 in 4')
        self.assertEqual(p[65.6], 'about 2 in 3')
        for v in p.values():
            m = re.match(r'^about (\d+) in (\d+)$', v)
            if m:
                self.assertIn(int(m.group(2)), (3, 4, 5, 10, 100), f'{v}: only simple fractions')

    def test_geo_columns_are_never_behavioral(self):
        g = dict(self.out['geo'])
        for k in ('COUNTRY', 'AGE | United Kingdom', 'INCOME | NIGERIA', 'ethnicity | Ireland'):
            self.assertTrue(g[k], k)
        for k in ('AGE', 'LOCATION', 'MOST PURCHASED BRANDS', 'STREAMING/PLATFORM', 'AGE_OF_CHILDREN | Ireland'):
            self.assertFalse(g[k], k)

    # ---- the parse hooks -----------------------------------------------------

    def test_a_global_parse_keeps_us_cities_and_drops_geo_categories(self):
        h = self.out['hookGlobal']
        self.assertTrue(h['hasModel'])
        self.assertNotIn('Birmingham, UK', h['locations'], 'a UK city is never kept as the Birmingham, Alabama DMA')
        self.assertIn('New York, USA', h['locations'])
        self.assertIn('Chicago IL', h['locations'])
        for k in h['behavioral'] + h['categories']:
            self.assertFalse(k == 'COUNTRY' or ' | ' in k, f'{k} left the behavioral categories')
        self.assertIn('SOCIAL MEDIA', h['behavioral'])
        self.assertIn('MOST PURCHASED BRANDS', h['behavioral'])
        c = self.out['hookCompare']
        self.assertFalse(any(k == 'COUNTRY' or ' | ' in k for k in c['global']), c['global'])
        self.assertIn('SOCIAL MEDIA', c['global'])

    def test_a_us_parse_is_untouched(self):
        h = self.out['hookUs']
        self.assertFalse(h['hasModel'])
        self.assertTrue(h['same'], 'the wrapped parse returns exactly what the page parsed')
        self.assertIn('Birmingham', h['locations'], "a US file's own Birmingham DMA stays")
        self.assertIn('COUNTRY', h['behavioral'], 'a US file keeps any column it carries')
        self.assertIn('COUNTRY', self.out['hookCompare']['us'])

    # ---- the words -----------------------------------------------------------

    def test_copy_is_plain_and_carries_no_house_jargon(self):
        copy = self.out['copy']
        strings = [v for v in copy.values() if isinstance(v, str)] + list(copy['titles'].values())
        banned = re.compile(r'\b(DMA|DMAs|household|households|HH|HHs|synth\w*|modeled|panel\w*|index|indexes|'
                            r'penetration|cohort|churn|incremental|leverage|utilize)\b', re.I)
        for s in strings:
            self.assertNotRegex(s, banned, s)
            self.assertNotIn('\u2014', s)
            self.assertNotIn('\u2013', s)
            for sentence in re.split(r'(?<=[.!?])\s+', s):
                self.assertLessEqual(len(sentence.split()), 25, s)
        self.assertEqual(copy['titles']['AGE'], 'Age')
        self.assertEqual(copy['switchLabel'], 'Show the people in')


if __name__ == '__main__':
    unittest.main(verbosity=2)
