#!/usr/bin/env python3
"""Profile IQ: the "Cross behaviors" section in the Data Cuts panel
(2026-11-11, Jenna: "Build it on the page, free for anyone with the
profile").

What holds here:
  * the section is mounted inside the Data Cuts panel, and a profile
    with no other cut still shows the Data Cuts button (Gen Pop does not)
  * the page never computes a figure: it reads and posts to
    /api/profile-iq/cross-behaviors/<key>; the route is behind the
    profile file's own access gate, carries no behavior flags and never
    touches a wallet; the read is prometheus.crosstab's (the engine and
    the record the chat uses)
  * the picks the page posts are the rows the options gave it (column,
    value, whole), up to three behaviors and one demographic bucket
  * the helpers that format a figure and build the export behave (node)
  * nothing a reader sees in the section carries a method word, a
    source name, a dash, or a home-level noun

Run:  python3 scripts/test_profile_cross_behaviors_page.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BANNED = re.compile(
    r"\b(?:model(?:ed|led|s)?|estimat\w*|synth\w*|panel\w*|sample|calibrat\w*|deriv\w*|households?|HHs?|confidence|"
    r"approximate\w*|probabilit\w*|independen\w*|pew|nielsen|statista|circana)\b|[\u2014\u2013]", re.I)


class CrossBehaviorsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')
        cls.app = Path(ROOT, 'app.py').read_text(encoding='utf-8')

    def _fn(self, name):
        html = self.html
        i = html.index('function ' + name + '(')
        depth, j = 0, html.index('{', i)
        while True:
            ch = html[j]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return html[i:j + 1]
            j += 1

    def test_mounted_in_the_data_cuts_panel(self):
        html = self.html
        i = html.index('window._openCompareDateRangesPopover = function _openCompareDateRangesPopover() {')
        j = html.index("document.addEventListener('mousedown', _compareDateRangesDocHandler, true);", i)
        opener = html[i:j]
        self.assertIn('_piqCrossMount(pop);', opener)
        self.assertLess(opener.index('_piqCrossMount(pop);'), opener.index('document.body.appendChild(pop);'))
        mount = self._fn('_piqCrossMount')
        self.assertIn('Cross behaviors', mount)
        self.assertIn("/gen[_ ]?pop/i.test(key)", mount)                    # Gen Pop has nothing to cross
        self.assertIn('picks: [null, null, null]', mount)                   # another profile starts clean
        # a profile with no other cut keeps the Data Cuts button
        k = html.index('const _piqCrossHere = !/gen[_ ]?pop/i.test(String(profileId ||')
        branch = html[k:k + 700]
        self.assertIn("deltasBtn.style.display = _piqCrossHere ? '' : 'none';", branch)
        self.assertIn('_openCompareDateRangesPopover()', branch)
        # no new entry in the SELECT PRODUCT dropdown
        self.assertNotIn('value="crossBehaviors"', html)
        self.assertNotIn("value='crossBehaviors'", html)

    def test_page_never_computes_and_posts_the_picks(self):
        run = self._fn('_piqCrossRun')
        self.assertIn("fetch(_piqCrossUrl(key), { method: 'POST'", run)
        self.assertIn('JSON.stringify({ behaviors: picks })', run)
        self.assertIn('{ column: p.column, value: p.value, whole: !!p.whole }', run)
        self.assertIn("picks.push({ column: _piqCross.demo.column, value: _piqCross.demo.value })", run)
        self.assertIn('Pick at least two different behaviors to size a group.', run)
        render = self._fn('_piqCrossRender')
        for needle in ('d.share_text', 'd.us', 'd.index', 'd.times', 'd.table', 'd.read', 'd.rows_read',
                       'Size this group', 'Export CSV', 'Build as a cut', 'Narrow by (optional)'):
            self.assertIn(needle, render)
        # the figures on screen are the payload's; nothing is multiplied, averaged or rounded into a new number here
        for fn in ('_piqCrossRender', '_piqCrossRun', '_piqCrossExport'):
            body = self._fn(fn)
            self.assertIsNone(re.search(r"share_pct\s*[*/+-]|\.us\s*[*/+-]|\bindex\s*[*/]", body), fn)
        search = self._fn('_piqCrossSearch')
        self.assertIn("'?q=' + encodeURIComponent(q || '')", search)
        self.assertIn('o.category', search)
        self.assertIn('o.share_pct', search)
        cut = self._fn('_piqCrossBuildCut')
        self.assertIn('d.cut_order', cut)
        self.assertIn('synthChatSubmit', cut)            # the paid build flow takes the words; nothing is ordered here

    def test_route_is_gated_free_and_shared(self):
        app = self.app
        i = app.index("@app.route('/api/profile-iq/cross-behaviors/<path:s3_key>', methods=['GET', 'POST'])")
        body = app[i:app.index('\n@app.route(', i + 10)]
        self.assertIn('@requires_auth', body)
        self.assertIn('ok, err = _require_profile_run_access(s3_key)', body)
        self.assertLess(body.index('_require_profile_run_access(s3_key)'), body.index('from prometheus import crosstab'))
        self.assertIn('_xt.page_options(s3_key', body)
        self.assertIn('_xt.page_read(s3_key, picks', body)
        route = body.split('def profile_cross_behaviors', 1)[1].split('\n\n\n', 1)[0]
        self.assertIsNone(re.search(r"consume_credit|wallet|has_credits|panel_offer|\bprice|\bforce|override|bypass|\bmode\b", route))
        # the only things read off the request: what was typed in a search box, and the picks
        self.assertEqual(sorted(set(re.findall(r"request\.args\.get\('(\w+)'\)", body))), ['q'])
        self.assertEqual(sorted(set(re.findall(r"body\.get\('(\w+)'\)", body))), ['behaviors'])
        can = app[app.index('def _pm_can_open_profile(user, s3_key):'):]
        can = can[:can.index('\ndef ')]
        self.assertIn('_require_profile_run_access(s3_key)', can)
        self.assertIn("role in ('admin', 'super_admin')", can)

    def test_helpers_in_node(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        js = '\n'.join([
            self._fn('_piqCrossUrl'), self._fn('_piqCrossEsc'), self._fn('_piqCrossNum'), self._fn('_piqCrossPct'),
            "console.log(JSON.stringify({",
            "  url: _piqCrossUrl('Chime Banking Customers_05_12_2026.csv'), nested: _piqCrossUrl('purgatory/A & B.csv'),",
            "  esc: _piqCrossEsc('<b>\"AT&T\"</b>'), num: [_piqCrossNum(6932307), _piqCrossNum(null), _piqCrossNum('')],",
            "  pct: [_piqCrossPct(77.25), _piqCrossPct(9), _piqCrossPct(null)] }));",
        ])
        out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got['url'], '/api/profile-iq/cross-behaviors/Chime%20Banking%20Customers_05_12_2026.csv')
        self.assertEqual(got['nested'], '/api/profile-iq/cross-behaviors/purgatory/A%20%26%20B.csv')
        self.assertEqual(got['esc'], '&lt;b&gt;&quot;AT&amp;T&quot;&lt;/b&gt;')
        self.assertEqual(got['num'], ['6,932,307', '', ''])
        self.assertEqual(got['pct'], ['77.3%', '9.0%', ''])

    def test_copy_is_plain(self):
        html = self.html
        i = html.index('// ─── Cross behaviors (2026-11-11')
        j = html.index('window._openCompareDateRangesPopover = function _openCompareDateRangesPopover() {', i)
        block = html[i:j]
        strings = re.findall(r"'((?:[^'\\\n]|\\.){12,})'", block)
        shown = [s for s in strings if ' ' in s and not re.search(r"[;{}=]|px|rem|var\(", s)]
        self.assertTrue(any('Pick two or three behaviors' in s for s in shown))
        for s in shown:
            self.assertIsNone(BANNED.search(s), s)
        self.assertNotIn('\u2014', block)
        self.assertNotIn('\u2013', block)
        self.assertLess(len(html.encode('utf-8')), 13_500_000)


if __name__ == '__main__':
    unittest.main(verbosity=1)
