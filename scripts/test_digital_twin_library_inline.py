#!/usr/bin/env python3
"""The Twins library's Past work opens an item in place (Jenna 2026-11-20: "If I click open run
it should show the run right under this not at the bottom of the screen and it should give
you the option to reopen the room").

Pins: the run is drawn once (runHtml) for the twin page's bottom host and the inline open;
the report's outcome body is shared with the room's closing message; the scorecard has a
brief form for the inline open; every Past work line opens under itself and closes from the
same button; every opened item carries its way back into a room (Reopen the room for a
report or a room, Open a room on this for a run, Open the test for a pilot test); the New
room drawer takes the run's question as the first thing to ask and drops it when the drawer
closes. The page's own functions run in node on a small twin. Hermetic: no model, no network.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

_NODE = r'''const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function fn(name) {
  const a = src.indexOf('            function ' + name + '(');
  if (a < 0) throw new Error('missing function ' + name);
  const ends = ['\n            function ', '\n            window.', '\n            var '].map(function(m) { const i = src.indexOf(m, a + 10); return i < 0 ? src.length : i; });
  return src.slice(a, Math.min.apply(null, ends));
}
function win(name) {
  const a = src.indexOf('            window.' + name + ' = ');
  if (a < 0) throw new Error('missing window.' + name);
  const ends = ['\n            function ', '\n            window.', '\n            var ', '\n            //'].map(function(m) { const i = src.indexOf(m, a + 10); return i < 0 ? src.length : i; });
  return src.slice(a, Math.min.apply(null, ends));
}
function v(name) { const a = src.indexOf('            var ' + name + ' = '); if (a < 0) throw new Error('missing var ' + name); return src.slice(a, src.indexOf('\n', a) + 1); }
const names = ['renderLibrary', 'pad2', 'lcFirst', 'ageWords', 'libPastCount', 'libKindClass', 'libKindsBar', 'libEntry', 'libDoc', 'libLoadDoc', 'libPanel', 'libLearn', 'libStats',
  'libTypeColor', 'libDate', 'libEmptyItem', 'libPast', 'libSession', 'libPilot', 'libRun', 'libSeatsStrip', 'libRoomQs', 'libRoomFacts', 'libDetail', 'libRoomSeedTake',
  'twinKindLabel', 'twinQualifier', 'twinBuiltLine', 'twinNameShared', 'twinPrice', 'twinDelId', 'twinDeleteChrome', 'hueFor', 'titleCase', 'initials', 'fmtDay', 'fmtDate', 'human', 'humanMoney', 'fmtN', 'fmtMoney', 'signed',
  'roomTitle', 'productRuns', 'isRetiredRun', 'kindLabel', 'cap', 'paCallWord', 'paOutcomeBody', 'shortCity', 'kindsSection',
  'runHtml', 'renderRun', 'twinName', 'card', 'figs', 'kv', 'paras', 'readCard', 'runMeta', 'bar', 'cohortTable', 'leverTable', 'exportRow',
  'questionLolli', 'optsOf', 'optObjs', 'rankMap', 'qInFlight', 'qFailed', 'liveCounts', 'seriesOf', 'seatsTotal', 'readHtml', 'answerOptId', 'optionIndex', 'optionLabelOf', 'renderScorecard',
  'piNormChoice', 'piComparedLabel', 'piNormNotes', 'piDenominator', 'piTallies', 'normCell', 'pct1', 'piReaderNotes', 'piNormNoun', 'piNormLabel', 'piNormWord'];
let js = ['HUES', 'MONTHS', 'SEP_TXT', 'LAB_LABEL', 'SEP', 'SERIES', 'IN_FLIGHT', 'PI_NORM_NOUN', 'PI_CAL_RX'].map(v).join('') + names.map(fn).join('\n') + '\n' + win('dtwLibOpenItem');
const stubs = `
var window = {}; var __els = {}; var DOM = {};
function esc(x) { return String(x == null ? '' : x).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function jsq(x) { return String(x || '').replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'"); }
function pct(v, d) { d = d == null ? 1 : d; return (Math.round(Number(v) * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d) + '%'; }
function el(id) { return DOM[id] || (DOM[id] = { innerHTML: '', textContent: '', style: {}, value: '', closest: function() { return { style: {} }; } }); }
function api() { return Promise.resolve({ twin: null }); }
function thumb(u) { return u; }
function normQ(s) { return String(s || '').toLowerCase(); }
var document = { querySelector: function() { return null; }, querySelectorAll: function() { return []; } };
`;
const twin = {
  meta: { name: 'Prime Video', key: 'pv', size_us: 1000000, window_start: '2025-11-18', window_end: '2026-11-18', created_at: '2026-11-18T18:57:54Z' },
  archetypes: { k: 2, noun: 'subscribers', groups: [{ id: 'g1', label: 'Kind one', share_pct: 60, people: 600000, representative: { name: 'Ava', age: 40, city: 'Dallas' } }, { id: 'g2', label: 'Kind two', share_pct: 40, people: 400000, representative: { name: 'Ben', age: 30, city: 'Austin' } }] },
  identity: { demographics: {} }, lifecycle: {}, traits: {},
  panel_sessions: [
    { id: 'pa1', title: 'A report', created_at: '2026-11-18T20:00:00Z', updated_at: '2026-11-19T00:00:00Z', group: { roster: [{ seat: 1, name: 'Ava', age: 40, city: 'Dallas' }], in_room: 1, size_people: 1000000 },
      questions: [{ id: 'q1', q: 'Would you pay?', kind: 'yes_no', stage: 'done', finished_at: '2026-11-19T00:00:00Z', seats_total: 1, options: [{ id: 'yes', label: 'Yes', pct: 61 }, { id: 'no', label: 'No', pct: 39 }], distribution: [{ label: 'Yes', pct: 61 }, { label: 'No', pct: 39 }], read: 'Most would pay. The rest want a lower price. A third sentence that is not shown.', read_short: 'Most would pay.' }],
      guided: { status: 'done', done: 1, total: 1, finished_at: '2026-11-19T00:29:30Z', plan: { goal: 'Will they pay for a year?' }, outcome: { call: 'lean yes', answer: 'Six in ten would pay for a year.', why: 'The football seats carry it.', numbers: ['61% yes'], who: 'Sports first viewers drove it.', would_change: 'A price rise.', next: 'Ask about the ad tier.' } } },
    { id: 'room1', title: 'What stops one more service', created_at: '2026-11-12T19:00:00Z', updated_at: '2026-11-12T19:40:00Z', group: { roster: [{ seat: 1, name: 'Ava', age: 40, city: 'Dallas', portrait_url: 'http://x/1.jpg' }, { seat: 2, name: 'Ben', age: 30, city: 'Austin' }], in_room: 2 },
      questions: [{ id: 'r1', q: 'What stops you from adding another service?', kind: 'multiple_choice', stage: 'done', finished_at: '2026-11-12T19:40:00Z', seats_total: 2, options: [{ id: 'a', label: 'Price', pct: 50 }, { id: 'b', label: 'Nothing I want', pct: 50 }], distribution: [{ label: 'Price', pct: 50 }, { label: 'Nothing I want', pct: 50 }], read: 'Price leads. Nothing I want is close behind.' }] },
    { id: 'empty1', title: 'Empty', created_at: '2026-11-10T19:00:00Z', group: { roster: [] }, questions: [] }
  ],
  pilot_sessions: [{ id: 'pt1', title: 'Pilot A', mode: 'pilot', status: 'stopped', created_at: '2026-11-06T00:00:00Z', genre: '', format: 'pilot', group: { roster: [], size_people: 1000000 },
    scorecard: { overall_like: 57.9, favorite_moment: { clock: '38:57', line: 'A quiet scene.' }, dead_spot: { clock: '17:21', line: 'A slow stretch.' }, notes: [], norm_lines: ['Total positive 49.4% (8 of 16).'], norms: {}, norm_columns: {}, norm_choice: {}, tallies: {},
      battery: { headline: { excellent_pct: 6.5, total_positive_pct: 49.4, definitely_recommend_pct: 29.8, finish_pct: 96.7 }, questions: [], quadrants: [] } } }],
  runs: [{ id: 'run1', kind: 'content', label: 'Content drop', question: 'How does the next episode land?', created_at: '2026-11-18T18:57:54Z', created_by: 'admin',
    outputs: { expected: 381596, low: 288460, high: 504805, completion_pct: 64.4, return_pct: 50.5, subscribe_renew_pct: 41.0, churn_risk_at_end_pct: 15.9, noun: 'buyers', platform: 'Prime Video' },
    drivers: ['Half come back for the next entry.'], recommendation: 'Hold the price.', read: { tldr: ['Expected: 381.6K buyers.'] } }]
};
eval(stubs + js + `
var assert = require('assert');
var dtwLibOpenItem = window.dtwLibOpenItem;
var S = { runs: [{ key: 'pv', name: 'Prime Video', subject_kind: 'brand', size_us: 1000000, kinds: 2, shares: [60, 40], biggest: { label: 'Kind one', share_pct: 60 }, panel_sessions: 3, pilot_sessions: 1, runs: 1 }], key: 'pv', twin: ${JSON.stringify(twin)},
         libKind: 'all', libQ: '', pricing: [], libOpen: { key: 'pv', kind: 'past', item: null }, session: null, fg: { seat: null, open: {} } };
renderLibrary(); var closed = el('dtwLibrary').innerHTML;
assert.strictEqual((closed.match(/dtw-lib-detail/g) || []).length, 0, 'nothing open: no detail');
assert.strictEqual((closed.match(/Open run<i class="c">/g) || []).length, 1, 'the run line opens in place');
assert.strictEqual((closed.match(/Open report<i class="c">/g) || []).length, 1, 'the finished report opens in place');
assert.strictEqual((closed.match(/Open room<i class="c">/g) || []).length, 2, 'both rooms open in place');
assert.strictEqual((closed.match(/Open test<i class="c">/g) || []).length, 1, 'the test opens in place');
assert.ok(closed.indexOf('dtwLibOpenRun(') < 0, 'no line goes to the bottom of the page');
// the run opens right under its line
dtwLibOpenItem('pv', 'run', 'run1'); var run = el('dtwLibrary').innerHTML;
var line = run.indexOf('dtw-lib-item open'), det = run.indexOf('dtw-lib-detail run'), next = run.indexOf('dtw-lib-item', line + 20);
assert.ok(line >= 0 && det > line && det < next, 'the detail sits between the open line and the next line');
assert.ok(run.indexOf('381,596 buyers') >= 0 && run.indexOf('Hold the price.') >= 0, 'the run body is the twin page run');
assert.ok(run.indexOf('export.pptx') < 0, 'the inline run carries no twin export row');
assert.ok(run.indexOf('Open a room on this') >= 0 && run.indexOf("dtwLibRoomOn('pv', 'run1')") >= 0, 'a run offers a room on it');
assert.strictEqual((run.match(/Close<i class="c">/g) || []).length, 1, 'the same button now closes');
// the same button closes it
dtwLibOpenItem('pv', 'run', 'run1'); assert.strictEqual((el('dtwLibrary').innerHTML.match(/dtw-lib-detail/g) || []).length, 0, 'closed again');
// the report: the outcome, the questions with their splits, the ways back in
dtwLibOpenItem('pv', 'pa', 'pa1'); var pa = el('dtwLibrary').innerHTML;
assert.ok(pa.indexOf('dtw-lib-detail pa') >= 0 && pa.indexOf('Leaning yes.') >= 0 && pa.indexOf('Six in ten would pay for a year.') >= 0, 'the outcome');
assert.ok(pa.indexOf('What would change it.') >= 0 && pa.indexOf('Worth asking next.') >= 0, 'the outcome tail');
assert.ok(pa.indexOf('Would you pay?') >= 0 && pa.indexOf('dtw-fg-lolli') >= 0 && pa.indexOf('Most would pay.') >= 0 && pa.indexOf('third sentence') < 0, 'the question, its split and its short read');
assert.ok(pa.indexOf('/guided/pa1/report.pdf') >= 0, 'the report PDF');
assert.ok(pa.indexOf("dtwLibOpenRoom('pv', 'pa1')") >= 0 && pa.indexOf('Reopen the room') >= 0, 'reopen the room');
assert.ok(pa.indexOf("dtwLibOpenReport('pv', 'pa1')") >= 0, 'the full report');
assert.ok(pa.indexOf('finished Nov 19') >= 0, 'the finish date');
// the room: title, facts, seats, questions, reopen
dtwLibOpenItem('pv', 'room', 'room1'); var room = el('dtwLibrary').innerHTML;
assert.ok(room.indexOf('dtw-lib-detail room') >= 0 && room.indexOf('2 seats') >= 0 && room.indexOf('1 question') >= 0, 'the facts');
assert.strictEqual((room.match(/dtw-kind-face/g) || []).length, 2, 'the seats');
assert.ok(room.indexOf('http://x/1.jpg') >= 0 && room.indexOf('>BE<') >= 0, 'a face where one exists, initials otherwise');
assert.ok(room.indexOf('What stops you from adding another service?') >= 0 && room.indexOf('<b>Price leads.</b> Nothing I want is close behind.') >= 0, 'the question and the read');
assert.ok(room.indexOf("dtwLibOpenRoom('pv', 'room1')") >= 0 && room.indexOf('Reopen the room') >= 0 && room.indexOf('/panel/room1/export.csv') >= 0, 'reopen and the raw data');
dtwLibOpenItem('pv', 'room', 'empty1'); assert.ok(el('dtwLibrary').innerHTML.indexOf('No questions asked in this room yet.') >= 0, 'an empty room says so');
// the test: the brief scorecard and the way into the test
dtwLibOpenItem('pv', 'pilot', 'pt1'); var pt = el('dtwLibrary').innerHTML;
assert.ok(pt.indexOf('dtw-lib-detail pilot') >= 0 && pt.indexOf('dtw-pi-money') >= 0 && pt.indexOf('49.4%') >= 0 && pt.indexOf('Dial average') >= 0, 'the headline and the dial');
assert.ok(pt.indexOf('dtw-pi-table') < 0, 'the tables stay on the test');
assert.ok(pt.indexOf("dtwLibOpenTest('pv', 'pt1', 'pilot')") >= 0 && pt.indexOf('Open the test') >= 0, 'open the test');
// the run as the twin page draws it is the same string
var h = el('dtwRunResults'); renderRun(twin.runs[0]); assert.ok(h.innerHTML.indexOf('381,596 buyers') >= 0 && h.innerHTML.indexOf('Hold the price.') >= 0, 'the bottom host still draws the run');
renderRun(null); assert.strictEqual(h.innerHTML, '', 'nothing when there is no run');
// the room seed: taken once for its twin while fresh, never for another twin, never stale
S.roomSeed = { key: 'pv', q: 'How does the next episode land?', title: 'Content drop', at: Date.now() };
assert.strictEqual(libRoomSeedTake('other'), null, 'another twin never takes it'); S.roomSeed = { key: 'pv', q: 'Q', title: 'T', at: Date.now() };
var seed = libRoomSeedTake('pv'); assert.ok(seed && seed.q === 'Q', 'taken'); assert.strictEqual(S.roomSeed, null, 'taken once');
S.roomSeed = { key: 'pv', q: 'Q', title: 'T', at: Date.now() - 16 * 60 * 1000 }; assert.strictEqual(libRoomSeedTake('pv'), null, 'stale seeds are dropped');
var all = [closed, run, pa, room, pt].join(' ').replace(/<[^>]+>/g, ' ');
var bad = all.match(/\\b(synthetic|synth|modell?ed|model|estimated|research|panel|sample|archetype|persona|segment|cohort|confidence|survey|prediction|calibrated|Nielsen)\\b/ig);
assert.ok(!bad, 'banned words: ' + bad);
console.log('node logic checks: OK');
`);
'''


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            cls.html = fh.read()
        a = cls.html.index('            function libPast(r, doc) {')
        b = cls.html.index('            // the ways back in from an entry\n', a)
        cls.block = cls.html[a:b]

    def test_the_run_is_drawn_once(self):
        self.assertEqual(self.html.count('function runHtml(r, opts)'), 1)
        self.assertIn("function renderRun(r) { var h = el('dtwRunResults'); if (!h) return; h.innerHTML = runHtml(r); }", self.html)
        run = self.html[self.html.index('function runHtml(r, opts)'):self.html.index('function renderRun(r)')]
        self.assertIn("var exports = opts.exports === false ? '' : exportRow();", run)
        self.assertNotIn('h.innerHTML', run)
        self.assertIn('runHtml(rn, { exports: false })', self.block)

    def test_the_outcome_body_is_shared(self):
        self.assertEqual(self.html.count('function paOutcomeBody(o)'), 1)
        msg = self.html[self.html.index('function paOutcomeMsg(s)'):self.html.index('function paCallWord(o)')]
        self.assertIn('paOutcomeBody(g.outcome)', msg)
        self.assertIn('paOutcomeBody(g.outcome)', self.block)

    def test_the_scorecard_has_a_brief_form(self):
        self.assertIn('function renderScorecard(s, brief)', self.html)
        self.assertIn('if (brief) return compared + money + lines + dialReads;', self.html)
        self.assertIn('renderScorecard(pt, true)', self.block)

    def test_every_line_opens_in_place_with_a_way_back_into_a_room(self):
        for need in ("inline('run', rn.id, 'Open run')", "inline('room', s.id, 'Open room')", "inline('pilot', s.id, 'Open test')", "inline('pa', s.id, 'Open report')",
                     "if (on) out += libDetail(r, doc, it);", 'function libDetail(r, doc, it)', 'Reopen the room', 'Open a room on this', 'Open the test', 'Download the report (PDF)', 'The full report', 'Raw data (CSV)',
                     'window.dtwLibOpenItem = function(key, type, id)', 'window.dtwLibRoomOn = async function(key, id)', 'function libRoomSeedTake(key)'):
            self.assertIn(need, self.block, need)
        # a running report still goes to the Predictive Analytics tab to be watched; nothing routes to the bottom of the page
        self.assertIn("dtwLibOpenReport(\\'' + kn + '\\', \\'' + sid + '\\')\">Open the run", self.block)
        self.assertNotIn('dtwLibOpenRun(', self.block)
        self.assertIn("block: 'nearest'", self.block)

    def test_the_new_room_drawer_takes_the_runs_question(self):
        opener = self.html[self.html.index('window.dtwPanelOpen = async function()'):self.html.index('window.dtwPanelKind = function(k)')]
        self.assertIn('var seed = libRoomSeedTake(key);', opener)
        self.assertLess(opener.index('var seed = libRoomSeedTake(key);'), opener.index('dtwNewRoomOpen(false);'))
        self.assertIn("if (seed) { var sq = el('dtwPnQ'); if (sq) { sq.value = seed.q;", opener)
        drawer = self.html[self.html.index('window.dtwNewRoomOpen = function(on)'):self.html.index('window.dtwNewRoomOpen = function(on)') + 400]
        self.assertIn('if (on) dtwRoomsOpen(false); else S.roomSeed = null;', drawer)
        self.assertIn("S.roomSeed = { key: key, q: String(rn.question || '')", self.block)
        self.assertIn("The room opens with the run\\'s question ready to ask.", self.block)

    def test_copy_is_clean(self):
        self.assertNotIn('\u2014', self.block)
        self.assertNotIn('\u2013', self.block)
        text = re.sub(r"<[^>]+>", ' ', self.block)
        self.assertFalse(re.search(r"\b(synthetic|synth|modell?ed|hostmap|Claude|persona|archetype|cohort|confidence|calibrat|Nielsen)\b", text, re.I), 'method or source words in the reader copy')
        css = self.html[self.html.index('/* an item opened in place (2026-11-20)'):self.html.index('.dtw-lib-acts .dtw-lb.link.quiet { margin-left: auto; }')]
        for sel in ('.dtw-lib-item.open', '.dtw-lib-detail', '.dtw-lib-q', '.dtw-lib-seats', '.dtw-lib-acts'):
            self.assertIn(sel, css, sel)

    def test_the_page_logic_runs(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write(_NODE)
            path = fh.name
        try:
            r = subprocess.run([node, path, os.path.join(ROOT, 'templates', 'index.html')], capture_output=True, text=True, timeout=120)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2500:])
        self.assertIn('node logic checks: OK', r.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=1)
