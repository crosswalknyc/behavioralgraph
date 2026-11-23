#!/usr/bin/env python3
"""Step 1 of Predictive Analytics (Jenna 2026-11-23: 'lets make these a
dropdown and make not here build a twin more prominent'): one dropdown of
every twin the seat can see, defaulting to the twin open on the page, each
row carrying the type, the name, the same-name qualifier and the people;
picking one goes through dtwPaPickTwin; Build a new twin stands beside it
as a card with the price, Start and the Tutorial link."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, 'static', 'digital-twins', 'digital-twins.js')
CSS = os.path.join(ROOT, 'static', 'digital-twins', 'digital-twins.css')


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def _fn(src, name):
    a = src.index('            function ' + name + '(')
    b = min(src.index('\n            function ', a + 10), src.index('\n            window.', a + 10), src.index('\n            var ', a + 10), src.index('\n            document.', a + 10))
    return src[a:b]


RUNS = [
    {'key': 'pp', 'name': 'Paramount+', 'subject_kind': 'brand', 'size_us': 83791763, 'created_at': '2026-11-19T18:57:25Z', 'built_by': 'Casey Pearson'},
    {'key': 'pv', 'name': 'Amazon Prime Video', 'subject_kind': 'brand', 'size_us': 128116302, 'created_at': '2026-11-18T18:57:54Z', 'built_by': 'Jenna Menking'},
    {'key': 'jb', 'name': 'Jennifer Beals', 'subject_kind': 'talent', 'size_us': 2776141},
    {'key': 'ae', 'name': 'Alix Earle', 'subject_kind': 'talent', 'size_us': 30524294},
    {'key': 'tx', 'name': 'texas voters', 'subject_kind': 'audience', 'size_us': 18623931},
    {'key': 'oh', 'name': 'ohio gubernatorial voters', 'subject_kind': 'audience', 'size_us': 7865508},
    {'key': 'pp0', 'name': 'Paramount+', 'subject_kind': 'brand', 'size_us': 83791763, 'created_at': '2026-10-30T20:21:56Z', 'built_by': 'Jenna Menking'},
    {'key': 'mr', 'name': 'Mark Rober', 'subject_kind': 'talent', 'size_us': 82431486},
]


class Static(unittest.TestCase):
    def test_the_step_is_a_dropdown_with_build_beside_it(self):
        js = _read(JS)
        step = js[js.index('function paStepAudience(p)'):js.index('function paStepQuestion(p)')]
        self.assertNotIn('dtw-bw-opts dtw-pa-twins', step, 'the grid of cards is gone')
        for need in ('id="dtwPaDdBtn"', 'aria-haspopup="listbox"', 'role="listbox"', 'role="option"', 'dtwPaDdPick(', 'dtwPaDdKey(event)', 'dtwPaDdFilter(this.value)',
                     'Not here? Build a new twin', "esc(twinPrice())", 'dtwBuildOpen(true)', "dtwTutorialOpen(\\'seating\\', this)", 'Who is the audience?',
                     'The people whose answer you need. Pick a twin, or build one first.', "paNav(!!p.key, 'Next')"):
            self.assertIn(need, step, need)
        self.assertIn("twinQualifier(r) ? '<span class=\"built\">'", step, 'the same-name qualifier rides on every row')
        for need in ('window.dtwPaDdToggle = function(on)', 'window.dtwPaDdPick = function(key) { dtwPaDdToggle(false); dtwPaPickTwin(key); }', 'window.dtwPaDdKey = function(ev)', "k === 'Escape'", "k === 'ArrowDown'", 'type to jump'):
            self.assertIn(need, js, need)
        self.assertNotIn('\u2014', step)
        self.assertNotRegex(step.replace('twinKindLabel', '').replace('subject_kind', ''), r"\bkinds?\b")
        css = _read(CSS)
        for need in ('.dtw-pa-pick { display: grid; grid-template-columns: minmax(0, 1fr) 300px', '.dtw-pa-dd-menu { position: absolute', '@media (max-width: 760px) { #digitalTwinsIQView .dtw-pa-pick { grid-template-columns: 1fr; }', '.dtw-pa-build .dtw-lb.link.tutorial'):
            self.assertIn(need, css, need)


@unittest.skipUnless(shutil.which('node'), 'node not available')
class Behaviour(unittest.TestCase):
    def _run(self, code):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write(code)
            path = fh.name
        try:
            out = subprocess.run(['node', path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr[-900:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_render(self):
        js = _read(JS)
        code = '\n'.join(_fn(js, n) for n in ('paStepAudience', 'paTwinRow', 'twinKindLabel', 'twinQualifier', 'twinNameShared', 'twinBuiltLine', 'twinOptionLabel', 'twinPrice', 'fmtN', 'fmtDay', 'jsq'))
        a = js.index('            var MONTHS = '); code = js[a:js.index('\n', a) + 1] + code
        node = ("global.window = global; function esc(x) { return String(x == null ? '' : x).replace(/[&<>\"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]; }); }\n"
                "function paNav() { return ''; } function paEarlier() { return ''; }\n"
                "var S = { runs: " + json.dumps(RUNS) + ", key: 'jb', pricing: ['Digital Twin: $750 per twin'] };\n" + code + "\n"
                "var html = paStepAudience({ key: 'jb', step: 0 });\n"
                "var opts = html.match(/<div class=\"dtw-pa-dd-opt[^\"]*\"[^>]*data-key=\"([^\"]+)\"/g) || [];\n"
                "console.log(JSON.stringify({ n: opts.length, keys: opts.map(function(o) { return /data-key=\"([^\"]+)\"/.exec(o)[1]; }), on: (html.match(/dtw-pa-dd-opt on\" role=\"option\" id=\"dtwPaDdOpt\\d+\" data-key=\"([^\"]+)\"/) || [])[1],\n"
                "  quals: (html.match(/class=\"built\">([^<]+)</g) || []).map(function(m) { return m.slice(14, -1); }), btn: /dtw-pa-dd-btn[^>]*>.*?<span class=\"nm\">Jennifer Beals<\\/span><span class=\"n\">2,776,141 people<\\/span>/.test(html),\n"
                "  find: /dtwPaDdFind/.test(html), price: /class=\"price\">\\$750</.test(html), build: /Not here\\? Build a new twin/.test(html) && /onclick=\"dtwBuildOpen\\(true\\)\">Start/.test(html) && /dtwTutorialOpen\\('seating', this\\)/.test(html),\n"
                "  none: paStepAudience({ key: '', step: 0 }).indexOf('Pick a twin') >= 0, words: (html.replace(/<[^>]+>/g, ' ').match(/\\b(panel|kinds?|persona|segment|cohort)\\b/ig) || []) }));\n")
        got = self._run(node)
        self.assertEqual(got['n'], len(RUNS), 'every visible twin is a row')
        self.assertEqual(got['keys'], [r['key'] for r in RUNS])
        self.assertEqual(got['on'], 'jb', 'the twin open on the page is the selected row')
        self.assertEqual(got['quals'], ['Built Nov 19 by Casey Pearson', 'Built Oct 30 by Jenna Menking'], 'the same-name qualifier on the two Paramount+ rows only')
        self.assertTrue(got['btn'], 'the closed control shows the chosen twin')
        self.assertTrue(got['find'], 'a search box once the list is long')
        self.assertTrue(got['price'] and got['build'])
        self.assertTrue(got['none'], 'with no twin open the control says Pick a twin')
        self.assertEqual(got['words'], [])

    def test_keyboard_and_pick(self):
        js = _read(JS)
        bs = js.index('            var paDd = { open: false'); be = js.index('            function paStepQuestion(p) {')
        behaviour = js[bs:be]
        node = r'''
var reg = {}, focused = null, picked = [], clicks = [];
function mk(id, cls) { var n = { id: id || '', cls: {}, attrs: {}, hidden: false, children: [], textContent: '', value: '',
  classList: { add: function(c) { n.cls[c] = 1; }, remove: function(c) { delete n.cls[c]; }, contains: function(c) { return !!n.cls[c]; }, toggle: function(c, on) { if (on) n.cls[c] = 1; else delete n.cls[c]; } },
  setAttribute: function(k, v) { n.attrs[k] = String(v); }, getAttribute: function(k) { return n.attrs[k] == null ? null : n.attrs[k]; },
  focus: function() { focused = n; }, scrollIntoView: function() {}, appendChild: function(c) { n.children.push(c); reg[c.id] = c; }, remove: function() {},
  contains: function(x) { return x === n || n.children.indexOf(x) >= 0; }, querySelector: function(sel) { return sel === '.nm' ? { textContent: n.nm || '' } : null; },
  querySelectorAll: function() { return n.children.filter(function(c) { return c.cls['dtw-pa-dd-opt']; }); } };
  (cls || '').split(' ').forEach(function(c) { if (c) n.cls[c] = 1; }); if (id) reg[id] = n; return n; }
var dd = mk('dtwPaDd'), btn = mk('dtwPaDdBtn'), menu = mk('dtwPaDdMenu'), list = mk('dtwPaDdList'), find = mk('dtwPaDdFind'); menu.hidden = true; dd.children = [btn, menu];
var RUNS = __RUNS__;
RUNS.forEach(function(r, i) { var o = mk('dtwPaDdOpt' + i, 'dtw-pa-dd-opt' + (r.key === 'jb' ? ' on' : '')); o.attrs['data-key'] = r.key; o.attrs['data-hay'] = r.name.toLowerCase(); o.nm = r.name; list.appendChild(o); });
global.document = { createElement: function() { return mk('', ''); }, addEventListener: function(t, f) { clicks.push(f); } };
function el(id) { return reg[id] || null; }
global.window = global; window.dtwPaPickTwin = function(k) { picked.push(k); };
''' + behaviour + r'''
var ev = function(key, target) { return { key: key, target: target || list, preventDefault: function() {}, stopPropagation: function() {} }; };
dtwPaDdKey(ev('ArrowDown', btn));
var afterOpen = { open: paDd.open, shown: !menu.hidden, expanded: btn.getAttribute('aria-expanded'), activeIsSelected: el('dtwPaDdOpt2').cls.active === 1, focusOnFind: focused === find };
dtwPaDdKey(ev('ArrowDown')); dtwPaDdKey(ev('ArrowDown')); var afterArrows = paDd.active;
dtwPaDdKey(ev('End')); var atEnd = paDd.active; dtwPaDdKey(ev('Home')); var atHome = paDd.active;
dtwPaDdKey(ev('m')); var typed = paDd.active;
dtwPaDdFilter('para'); var filtered = list.children.filter(function(o) { return !o.hidden; }).map(function(o) { return o.attrs['data-key']; });
dtwPaDdFilter(''); paDdMark(1); dtwPaDdKey(ev('Enter'));
var afterEnter = { picked: picked.slice(), closed: !paDd.open, focusBack: focused === btn };
dtwPaDdToggle(true); dtwPaDdKey(ev('Escape')); var afterEscape = { closed: !paDd.open, focusBack: focused === btn };
dtwPaDdToggle(true); clicks[0]({ target: mk('elsewhere') }); var outside = !paDd.open;
dtwPaDdToggle(true); dtwPaDdFilter('zzz'); var none = !!el('dtwPaDdNone');
console.log(JSON.stringify({ afterOpen: afterOpen, afterArrows: afterArrows, atEnd: atEnd, atHome: atHome, typed: typed, filtered: filtered, afterEnter: afterEnter, afterEscape: afterEscape, outside: outside, none: none }));
'''
        node = node.replace('__RUNS__', json.dumps(RUNS))
        got = self._run(node)
        self.assertEqual(got['afterOpen'], {'open': True, 'shown': True, 'expanded': 'true', 'activeIsSelected': True, 'focusOnFind': True}, 'ArrowDown on the closed control opens it on the chosen twin, focus in the search box')
        self.assertEqual(got['afterArrows'], 4)
        self.assertEqual((got['atEnd'], got['atHome']), (len(RUNS) - 1, 0))
        self.assertEqual(got['typed'], 7, 'typing m jumps to Mark Rober')
        self.assertEqual(got['filtered'], ['pp', 'pp0'], 'the search box narrows the list')
        self.assertEqual(got['afterEnter'], {'picked': ['pv'], 'closed': True, 'focusBack': True}, 'Enter picks through dtwPaPickTwin and closes')
        self.assertEqual(got['afterEscape'], {'closed': True, 'focusBack': True})
        self.assertTrue(got['outside'], 'a click outside closes it')
        self.assertTrue(got['none'], 'no match says so and points at the build card')


if __name__ == '__main__':
    unittest.main(verbosity=2)
