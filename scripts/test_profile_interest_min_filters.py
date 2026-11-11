#!/usr/bin/env python3
"""Profile IQ Interests: the Behavioral cards' Min % / Min index filter (2026-11-11, Armine).

Armine: "On the Profiles Interest page, I think it would also be helpful to have filter options for minimum
index and minimum percentage, similar to the filters available on the Behavioral page."

What holds here:
  * one row test for both pages (_piqMinHides): the Behavioral cards call it, the Interests list calls it
  * a row below the minimum % is hidden; a row below the minimum index is hidden; a row with no Gen Pop match
    is never hidden by the index floor; no minimum hides nothing
  * the Interests head carries the same Filter button on the same handlers and state (key '__interests__'),
    and the same panel markup (_piqMinPanelHtml)
  * the list filters its data before it renders, so the count, the empty state and the CSV follow the rows on
    screen; when nothing meets the minimums the list says so in plain words

Run:  python3 scripts/test_profile_interest_min_filters.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class InterestMinFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')

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

    def test_one_row_test_for_both_pages(self):
        beh = self._fn('_piqApplyBehMins')
        self.assertIn("_piqMinHides(m, row.getAttribute('data-pct'), row.getAttribute('data-index'))", beh)
        self.assertIn('panel.innerHTML = _piqMinPanelHtml();', beh)
        self.assertIn('window._piqApplyIntMins()', beh)
        flt = self._fn('_piqIntMinFilter')
        self.assertIn('_piqMinHides(m,', flt)
        panel = self._fn('_piqMinPanelHtml')
        self.assertIn('Min %', panel)
        self.assertIn('Min index', panel)
        self.assertIn('piq-beh-filter-clear', panel)
        self.assertIn('piq-beh-filter-status', panel)

    def test_interests_head_and_list_are_wired(self):
        html = self.html
        i = html.index('window._piqV4DressInterests = function()')
        dress = html[i:i + 2600]
        self.assertIn('class="piq-beh-filter-btn" data-cat="__interests__"', dress)
        self.assertIn('window._piqApplyIntMins()', dress)
        lst = self._fn('renderInterestsList')
        self.assertIn("(window._piqBehMins || {})['__interests__']", lst)
        self.assertIn('_piqIntMinFilter(filtered, _intMin)', lst)
        self.assertLess(lst.index('_piqIntMinFilter(filtered, _intMin)'), lst.index('let sorted = [...filtered];'))
        self.assertIn('No interests meet these minimums.', lst)
        # same state, same handlers: the delegated click / input handlers key on the classes the
        # Interests head and panel reuse, and the state lives in the session only
        self.assertIn("window._piqBehMins[cat] = { pct: read('pct'), idx: read('idx') };", html)
        self.assertIn("const KEY = '__interests__';", html)
        self.assertNotIn("localStorage.setItem('__interests__'", html)
        # the CSV scrapes the rendered rows, so it carries what the filter leaves on screen
        csv = self._fn('_piqInterestsCsvTable')
        self.assertIn("grid.querySelectorAll('.interest-item')", csv)

    def test_filter_behavior_in_node(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        js = '\n'.join([
            self._fn('_piqBehMinActive'), self._fn('_piqMinHides'), self._fn('_piqIntMinFilter'),
            "var items = [",
            "  { name: 'Fishing', pct: 58.8, genPopPct: 16.7, index: 351 },",
            "  { name: 'The weather', pct: 65.4, genPopPct: 54.5, index: 120 },",
            "  { name: 'Reality TV', pct: 41.7, genPopPct: 15.2, index: 274 },",
            "  { name: 'Opera', pct: 2.1, genPopPct: 3.0, index: 70 },",
            "  { name: 'New interest', pct: 12.0, genPopPct: 0, index: 0 },",
            "];",
            "function names(m) { return _piqIntMinFilter(items, m).map(function(i){ return i.name; }); }",
            "console.log(JSON.stringify({",
            "  none: names(undefined), nan: names({ pct: NaN, idx: NaN }),",
            "  pct: names({ pct: 40, idx: NaN }), idx: names({ pct: NaN, idx: 200 }),",
            "  both: names({ pct: 50, idx: 200 }), high: names({ pct: 99, idx: NaN }),",
            "  edge: names({ pct: 58.8, idx: 351 }),",
            "  active: [_piqBehMinActive(undefined), _piqBehMinActive({ pct: NaN, idx: NaN }), _piqBehMinActive({ pct: 5, idx: NaN })],",
            "  row: [_piqMinHides({ pct: 10, idx: NaN }, '9.99', '500'), _piqMinHides({ pct: 10, idx: NaN }, '10', ''),",
            "        _piqMinHides({ pct: NaN, idx: 150 }, '80', ''), _piqMinHides({ pct: NaN, idx: 150 }, '80', '149')] }));",
        ])
        out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        all_names = ['Fishing', 'The weather', 'Reality TV', 'Opera', 'New interest']
        self.assertEqual(got['none'], all_names)
        self.assertEqual(got['nan'], all_names)
        self.assertEqual(got['pct'], ['Fishing', 'The weather', 'Reality TV'])
        # the row with no Gen Pop match is never hidden by the index floor
        self.assertEqual(got['idx'], ['Fishing', 'Reality TV', 'New interest'])
        self.assertEqual(got['both'], ['Fishing'])
        self.assertEqual(got['high'], [])
        self.assertEqual(got['edge'], ['Fishing'])          # a row at the minimum stays
        self.assertEqual(got['active'], [False, False, True])
        self.assertEqual(got['row'], [True, False, False, True])


if __name__ == '__main__':
    unittest.main(verbosity=1)
