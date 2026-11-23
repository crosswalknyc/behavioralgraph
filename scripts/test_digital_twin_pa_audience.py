#!/usr/bin/env python3
"""The start of Predictive Analytics is the sentence you fill in (Jenna
2026-11-23 picked PA1): Ask [twin dropdown] <join> [the question typed in
place], the five shapes as flat chips that change the connecting words and
keep what was typed, Build a new twin inline and as the card, and the five
steps as a numbered dot-leader index that fills in and goes back. The
inline dropdown is the one from the audience step: every twin the seat can
see, the same-name qualifier, the open twin by default, picking through
dtwPaPickTwin."""
import json
import os
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
    {'key': 'pp', 'name': 'Paramount+', 'subject_kind': 'brand', 'size_us': 83791763, 'created_at': '2026-11-19T18:57:25Z', 'built_by': 'Casey Pearson', 'kinds': 15},
    {'key': 'pv', 'name': 'Amazon Prime Video', 'subject_kind': 'brand', 'size_us': 128116302, 'created_at': '2026-11-18T18:57:54Z', 'built_by': 'Jenna Menking', 'kinds': 16},
    {'key': 'jb', 'name': 'Jennifer Beals', 'subject_kind': 'talent', 'size_us': 2776141, 'kinds': 16},
    {'key': 'ae', 'name': 'Alix Earle', 'subject_kind': 'talent', 'size_us': 30524294, 'kinds': 12},
    {'key': 'tx', 'name': 'texas voters', 'subject_kind': 'audience', 'size_us': 18623931, 'kinds': 16},
    {'key': 'oh', 'name': 'ohio gubernatorial voters', 'subject_kind': 'audience', 'size_us': 7865508, 'kinds': 16},
    {'key': 'pp0', 'name': 'Paramount+', 'subject_kind': 'brand', 'size_us': 83791763, 'created_at': '2026-10-30T20:21:56Z', 'built_by': 'Jenna Menking', 'kinds': 13},
    {'key': 'mr', 'name': 'Mark Rober', 'subject_kind': 'talent', 'size_us': 82431486, 'kinds': 14},
]


class Static(unittest.TestCase):
    def test_the_start_is_the_sentence(self):
        js = _read(JS)
        self.assertNotIn('function paStepQuestion(p)', js, 'the separate question step is gone; the sentence carries it')
        self.assertNotIn('function paStepAudience(p)', js)
        start = js[js.index('function paStart(p)'):js.index('function paQRow(q, i, editable)')]
        for need in ('<span class="w">Ask</span>', 'class="dtw-pa-dd inline" id="dtwPaDd"', 'id="dtwPaDdBtn"', 'role="listbox"', 'dtwPaDdPick(', 'dtwPaDdFilter(this.value)',
                     'id="dtwPaJoin"', 'id="dtwPaGoal" contenteditable="plaintext-only"', 'data-ph=', 'dtwPaGoalInput(this.textContent)', 'class="dtw-pa-shape', "dtwPaKind(\\'",
                     'or build a new twin', 'Not here? Build a new twin', 'esc(twinPrice())', 'dtwBuildOpen(true)', "dtwTutorialOpen(\\'seating\\', this)", 'id="dtwPaPlanBtn"', 'paIndex(p, false)'):
            self.assertIn(need, start, need)
        self.assertIn("twinQualifier(r) ? '<span class=\"built\">'", start, 'the same-name qualifier rides on every row of the dropdown')
        self.assertNotIn('\u2014', start)
        self.assertNotRegex(start.replace('twinKindLabel', '').replace('subject_kind', '').replace('PA_KINDS', '').replace('p.kind', '').replace("k[0]", ''), r"\bkinds?\b")
        for need in ("var PA_JOIN = {", "contest: { join: 'which'", "price: { join: 'what they would pay for'", 'function paIndex(p, compact)', 'function paIndexStatus(p, i)', 'function paGoalText(p)', 'function paGoalValue()',
                     "if (p.step <= 1) out += paStart(p);", "else if (p.step === 2) out += paRail() + paStepPlan(p);", 'window.dtwPaKind = function(k) { var p = pa(); p.goal = paGoalValue();'):
            self.assertIn(need, js, need)
        css = _read(CSS)
        for need in ('.dtw-pa-sent { font-size: 44px; font-weight: 200;', '.dtw-pa-goalin:empty::before { content: attr(data-ph)', '.dtw-pa-ix { display: grid; grid-template-columns: 40px 150px', '.dtw-pa-ix.on .n, #digitalTwinsIQView .dtw-pa-ix.on .t { color: var(--dtw-accent); }', '@media (max-width: 760px) {\n            #digitalTwinsIQView .dtw-pa-sent { font-size: 28px'):
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

    def _code(self):
        js = _read(JS)
        code = '\n'.join(_fn(js, n) for n in ('paHero', 'paStart', 'paTwinRow', 'paGoalValue', 'paGoalText', 'paIndexStatus', 'paIndex', 'paRail', 'paEarlier', 'paCallClass', 'paCallWord', 'paRun', 'paTwinName', 'paPeople', 'pa', 'paFresh',
                                              'twinKindLabel', 'twinQualifier', 'twinNameShared', 'twinBuiltLine', 'twinOptionLabel', 'twinPrice', 'fmtN', 'fmtDay', 'jsq'))
        for v in ('MONTHS', 'PA_STEPS', 'PA_LEVELS', 'PA_JOIN', 'SEP'):
            a = js.index('            var ' + v + ' = '); code = js[a:js.index(';\n', a) + 2] + code
        a = js.index('            var PA_KINDS = ['); code = js[a:js.index('];\n', a) + 3] + code
        return ("global.window = global; function esc(x) { return String(x == null ? '' : x).replace(/[&<>\"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]; }); }\n"
                "function el() { return null; } function groupCard() { return null; } function groupQuote() { return ''; } function groupTierDefault() { return ''; } function twinName() { return 'Jennifer Beals'; }\n"
                "var paDd = { open: false }; var S = { runs: " + json.dumps(RUNS) + ", key: 'jb', pricing: ['Digital Twin: $750 per twin'], twin: { meta: { size_us: 2776141 } }, pa: null };\n" + code + "\n")

    def test_the_sentence_renders_with_the_open_twin(self):
        node = self._code() + r'''
var base = paFresh(); base.key = 'jb'; base.runs = [];
S.pa = Object.assign({}, base, { step: 0, goal: '', kind: '' }); var html = paStart(S.pa);
var keys = (html.match(/data-key="([^"]+)"/g) || []).map(function(m) { return m.slice(10, -1); });
console.log(JSON.stringify({ ask: /<span class="w">Ask<\/span>/.test(html), twin: /dtw-pa-dd-btn[^>]*><span class="nm">Jennifer Beals<\/span>/.test(html), join: (html.match(/id="dtwPaJoin">([^<]+)</) || [])[1], ph: (html.match(/data-ph="([^"]+)"/) || [])[1],
  keys: keys, on: (html.match(/dtw-pa-dd-opt on" role="option" id="dtwPaDdOpt\d+" data-key="([^"]+)"/) || [])[1], quals: (html.match(/class="built">([^<]+)</g) || []).map(function(m) { return m.slice(14, -1); }),
  or: /or build a new twin/.test(html), card: /Not here\? Build a new twin/.test(html) && /class="price">\$750</.test(html) && /onclick="dtwBuildOpen\(true\)">Start/.test(html) && /dtwTutorialOpen\('seating', this\)/.test(html),
  planDisabled: /id="dtwPaPlanBtn" disabled/.test(html), index: (html.match(/class="dtw-pa-ix[^"]*"><span class="n">0(\d)<\/span><span class="t">([^<]+)</g) || []).length, activeRow: (html.match(/class="dtw-pa-ix on[^"]*"><span class="n">0(\d)/) || [])[1], firstStatus: (html.match(/<span class="s">([^<]+)<\/span><\/div><(?:div|button)[^>]*class="dtw-pa-ix on/) || [])[1],
  words: (html.replace(/<[^>]+>/g, ' ').match(/\b(panel|kinds?|persona|segment|cohort|model|survey)\b/ig) || []) }));
'''
        got = self._run(node)
        self.assertTrue(got['ask'] and got['twin'], 'Ask [Jennifer Beals ▾]')
        self.assertEqual(got['join'], 'whether')
        self.assertEqual(got['ph'], 'a two-year activewear deal would pay off')
        self.assertEqual(got['keys'], [r['key'] for r in RUNS], 'every visible twin is a row of the dropdown')
        self.assertEqual(got['on'], 'jb', 'the open twin is chosen')
        self.assertEqual(got['quals'], ['Built Nov 19 by Casey Pearson', 'Built Oct 30 by Jenna Menking'])
        self.assertTrue(got['or'] and got['card'], 'Build a new twin inline and as the card')
        self.assertTrue(got['planDisabled'], 'Plan it waits for a question')
        self.assertEqual(got['index'], 5)
        self.assertEqual(got['activeRow'], '2', 'with the twin chosen the Question step is the active one')
        self.assertEqual(got['firstStatus'], 'Jennifer Beals, 2,776,141 people, 16 archetypes')
        self.assertEqual(got['words'], [])

    def test_shapes_switch_the_words_and_keep_the_text(self):
        node = self._code() + r'''
var base = paFresh(); base.key = 'jb'; base.runs = [];
function join(kind) { S.pa = Object.assign({}, base, { step: 1, goal: 'a two-year activewear deal would pay off', kind: kind }); var h = paStart(S.pa); return { join: (h.match(/id="dtwPaJoin">([^<]+)</) || [])[1], typed: /a two-year activewear deal would pay off<\/span>/.test(h), on: (h.match(/class="dtw-pa-shape on"[^>]*>([^<]+)</) || [])[1] || '', status: (h.match(/<span class="n">02<\/span><span class="t">Question<\/span>.*?<span class="s">([^<]+)</) || [])[1], planOn: !/id="dtwPaPlanBtn" disabled/.test(h) }; }
var out = { none: join(''), effect: join('effect'), contest: join('contest'), go: join('go'), price: join('price'), open: join('open') };
S.pa = Object.assign({}, base, { goal: 'cut wins them over, the Flashdance cut or the Bette Porter cut', kind: 'contest' }); out.goalContest = paGoalText(S.pa);
S.pa = Object.assign({}, base, { goal: 'a hoodie from her collection, at $45', kind: 'price' }); out.goalPrice = paGoalText(S.pa);
S.pa = Object.assign({}, base, { goal: 'Will her followers buy a $45 hoodie?', kind: 'open' }); out.goalAsIs = paGoalText(S.pa);
S.pa = Object.assign({}, base, { goal: 'what it would take for them to see her as a fashion name', kind: 'open' }); out.goalOpen = paGoalText(S.pa);
console.log(JSON.stringify(out));
'''
        got = self._run(node)
        self.assertEqual([got[k]['join'] for k in ('none', 'effect', 'contest', 'go', 'price', 'open')], ['whether', 'whether', 'which', 'whether', 'what they would pay for', 'about'])
        self.assertTrue(all(got[k]['typed'] for k in ('none', 'effect', 'contest', 'go', 'price', 'open')), 'the typed text survives every shape switch')
        self.assertEqual(got['contest']['on'], 'Will X win?')
        self.assertEqual(got['contest']['status'], 'Will X win?')
        self.assertTrue(all(got[k]['planOn'] for k in ('none', 'contest')), 'Plan it is live once there is a question')
        self.assertEqual(got['goalContest'], 'Which cut wins them over, the Flashdance cut or the Bette Porter cut')
        self.assertEqual(got['goalPrice'], 'What they would pay for a hoodie from her collection, at $45')
        self.assertEqual(got['goalAsIs'], 'Will her followers buy a $45 hoodie?', 'a question typed whole goes to the room as written')
        self.assertEqual(got['goalOpen'], 'What it would take for them to see her as a fashion name')

    def test_the_index_fills_in_and_goes_back(self):
        node = self._code() + r'''
var base = paFresh(); base.key = 'jb'; base.runs = []; var plan = { goal: 'g', success: 's', questions: [{ q: 'a', level: 'macro' }, { q: 'b', level: 'mid' }, { q: 'c', level: 'micro' }] };
function ix(p) { S.pa = p; var h = paIndex(p, p.step >= 2); return { on: (h.match(/class="dtw-pa-ix on[^"]*"><span class="n">0(\d)/) || [])[1], backs: (h.match(/<button type="button" onclick="dtwPaGo\((\d)\)"/g) || []).map(function(m) { return m.slice(-3, -2); }), statuses: (h.match(/<span class="s">([^<]*)<\/span>/g) || []).map(function(m) { return m.slice(16, -7); }), compact: /dtw-pa-index compact/.test(h) }; }
var out = {};
out.start = ix(Object.assign({}, base, { step: 0 }));
out.noTwin = ix(Object.assign({}, base, { step: 0, key: '' }));
out.plan = ix(Object.assign({}, base, { step: 2, goal: 'x', kind: 'go', plan: plan }));
out.run = ix(Object.assign({}, base, { step: 3, goal: 'x', kind: 'go', plan: plan, guided: { status: 'running', done: 1, total: 3 } }));
out.stopped = ix(Object.assign({}, base, { step: 3, goal: 'x', plan: plan, guided: { status: 'stopped', done: 1, total: 3 } }));
out.report = ix(Object.assign({}, base, { step: 4, goal: 'x', plan: plan, guided: { status: 'done', done: 3, total: 3 }, report: { outcome: { call: 'lean yes' } } }));
out.busy = ix(Object.assign({}, base, { step: 2, goal: 'x', plan: plan, busy: true }));
console.log(JSON.stringify(out));
'''
        got = self._run(node)
        self.assertEqual((got['start']['on'], got['start']['backs']), ('2', []), 'on the start nothing is a way back')
        self.assertEqual(got['noTwin']['on'], '1')
        self.assertEqual(got['noTwin']['statuses'][0], 'Pick a twin')
        self.assertEqual((got['plan']['on'], got['plan']['backs'], got['plan']['compact']), ('3', ['1', '1'], True), 'Audience and Question lead back to the start from Plan')
        self.assertEqual(got['plan']['statuses'][1:3], ['Should I do X?', '3 questions, from the wide to the narrow'])
        self.assertEqual((got['run']['on'], got['run']['backs']), ('4', ['1', '1']))
        self.assertEqual(got['run']['statuses'][3], '1 of 3 answered')
        self.assertEqual(got['stopped']['statuses'][3], '1 of 3 answered, stopped')
        self.assertEqual((got['report']['on'], got['report']['statuses'][3], got['report']['statuses'][4]), ('5', 'Done', 'Verdict: Leaning yes'))
        self.assertNotIn('2', got['report']['backs'], 'never back into the plan once the room has run')
        self.assertEqual(got['busy']['backs'], [], 'nothing moves while the page is busy')

    def test_dropdown_keyboard_and_pick(self):
        js = _read(JS)
        bs = js.index('            var paDd = { open: false'); be = js.index('            function paQRow(q, i, editable) {')
        behaviour = js[bs:be]
        node = r'''
var reg = {}, focused = null, picked = [], clicks = [];
function mk(id, cls) { var n = { id: id || '', cls: {}, attrs: {}, hidden: false, children: [], listeners: {}, textContent: '', value: '',
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
dtwPaDdKey(ev('ArrowDown', btn)); var afterOpen = { open: paDd.open, shown: !menu.hidden, activeIsSelected: el('dtwPaDdOpt2').cls.active === 1, focusOnFind: focused === find };
dtwPaDdKey(ev('ArrowDown')); dtwPaDdKey(ev('ArrowDown')); var afterArrows = paDd.active;
dtwPaDdKey(ev('m')); var typed = paDd.active;
dtwPaDdFilter('para'); var filtered = list.children.filter(function(o) { return !o.hidden; }).map(function(o) { return o.attrs['data-key']; });
dtwPaDdFilter(''); paDdMark(1); dtwPaDdKey(ev('Enter')); var afterEnter = { picked: picked.slice(), closed: !paDd.open, focusBack: focused === btn };
dtwPaDdToggle(true); dtwPaDdKey(ev('Escape')); var afterEscape = { closed: !paDd.open, focusBack: focused === btn };
dtwPaDdToggle(true); clicks[0]({ target: mk('elsewhere') }); var outside = !paDd.open;
console.log(JSON.stringify({ afterOpen: afterOpen, afterArrows: afterArrows, typed: typed, filtered: filtered, afterEnter: afterEnter, afterEscape: afterEscape, outside: outside }));
'''
        node = node.replace('__RUNS__', json.dumps(RUNS))
        got = self._run(node)
        self.assertEqual(got['afterOpen'], {'open': True, 'shown': True, 'activeIsSelected': True, 'focusOnFind': True})
        self.assertEqual(got['afterArrows'], 4)
        self.assertEqual(got['typed'], 7, 'typing m jumps to Mark Rober')
        self.assertEqual(got['filtered'], ['pp', 'pp0'])
        self.assertEqual(got['afterEnter'], {'picked': ['pv'], 'closed': True, 'focusBack': True}, 'Enter picks through dtwPaPickTwin')
        self.assertEqual(got['afterEscape'], {'closed': True, 'focusBack': True})
        self.assertTrue(got['outside'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
