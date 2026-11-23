#!/usr/bin/env python3
"""The start of Predictive Analytics is the sentence you fill in (Jenna
2026-11-23 picked PA1, then on the live page: 'lets try removing this, and
this and if htey want to build a new one it would be from the drop down.
have the dropdown default to select twin then the first option is build new
twin group. then lists the other. and the drop down currently looks messy
with all the bold and not properly justified the prepopulated quesion is
weird maybe we just dont list one or make it something that makes more
sense? like ask jennifer beals whether a boxed set...just doesnt make
sense.and there is a lot of dead space here').

So: Ask [Select twin] <join> [your question]. The dropdown reads Select twin
until the user picks here, even with a twin open on the page; its first
option is Build new twin group (the only way to a new twin from this page),
a hairline, then every twin the seat can see in regular weight with a fixed
type column, the same-name qualifier under the name and the people
right-aligned. Once picked the control names the audience, not the person
(Jennifer Beals followers, texas voters as is). No example question is
pre-filled. The five shapes still swap the connecting words and keep what
was typed; the index fills in and goes back."""
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
    {'key': 'pp', 'name': 'Paramount+', 'subject_kind': 'brand', 'size_us': 83791763, 'created_at': '2026-11-19T18:57:25Z', 'built_by': 'Casey Pearson', 'kinds': 15, 'noun': 'subscribers'},
    {'key': 'pv', 'name': 'Amazon Prime Video', 'subject_kind': 'brand', 'size_us': 128116302, 'created_at': '2026-11-18T18:57:54Z', 'built_by': 'Jenna Menking', 'kinds': 16, 'noun': 'subscribers'},
    {'key': 'jb', 'name': 'Jennifer Beals', 'subject_kind': 'talent', 'size_us': 2776141, 'kinds': 16, 'noun': 'followers'},
    {'key': 'ae', 'name': 'Alix Earle', 'subject_kind': 'talent', 'size_us': 30524294, 'kinds': 12, 'noun': 'followers'},
    {'key': 'tx', 'name': 'texas voters', 'subject_kind': 'audience', 'size_us': 18623931, 'kinds': 16, 'noun': 'people'},
    {'key': 'oh', 'name': 'ohio gubernatorial voters', 'subject_kind': 'audience', 'size_us': 7865508, 'kinds': 16, 'noun': 'people'},
    {'key': 'pp0', 'name': 'Paramount+', 'subject_kind': 'brand', 'size_us': 83791763, 'created_at': '2026-10-30T20:21:56Z', 'built_by': 'Jenna Menking', 'kinds': 13, 'noun': 'subscribers'},
    {'key': 'mr', 'name': 'Mark Rober', 'subject_kind': 'talent', 'size_us': 82431486, 'kinds': 14},
    {'key': 'gg', 'name': 'Gilmore Girls', 'subject_kind': 'series', 'size_us': 9120447, 'kinds': 12, 'noun': 'people'},
]


class Static(unittest.TestCase):
    def test_the_start_is_the_sentence(self):
        js = _read(JS)
        self.assertNotIn('function paStepQuestion(p)', js, 'the separate question step is gone; the sentence carries it')
        self.assertNotIn('function paStepAudience(p)', js)
        start = js[js.index('function paStart(p)'):js.index('function paQRow(q, i, editable)')]
        for need in ('<span class="w">Ask</span>', 'class="dtw-pa-dd inline" id="dtwPaDd"', 'id="dtwPaDdBtn"', 'role="listbox"', 'dtwPaDdPick(', 'dtwPaDdFilter(this.value)',
                     "'Select twin'", 'class="dtw-pa-dd-opt build" role="option" id="dtwPaDdOptBuild"', 'data-build="1"', 'Build new twin group', 'class="dtw-pa-dd-rule"', 'esc(twinPrice())',
                     'id="dtwPaJoin"', 'id="dtwPaGoal" contenteditable="plaintext-only"', 'data-ph="your question"', 'dtwPaGoalInput(this.textContent)', 'class="dtw-pa-shape', "dtwPaKind(\\'",
                     'class="dtw-pa-startline"', 'id="dtwPaPlanBtn"', 'paIndex(p, false)', 'paAudiencePhrase(p.key)'):
            self.assertIn(need, start, need)
        for gone in ('or build a new twin', 'Not here? Build a new twin', 'dtw-pa-sent-or', 'dtw-pa-dd-foot', 'dtw-pa-build small', 'dtw-pa-sent-build', "dtwTutorialOpen(\\'seating\\', this)", 'dtw-pa-startrow'):
            self.assertNotIn(gone, start, 'building a twin happens from the dropdown only: ' + gone)
        self.assertEqual(start[:start.index('function paGoalValue()')].count('dtwBuildOpen(true)'), 0, 'no build link or card on the start; dtwPaDdPick routes the first option')
        self.assertIn("twinQualifier(r) ? '<span class=\"built\">'", start, 'the same-name qualifier rides on every row of the dropdown')
        self.assertNotIn('\u2014', start)
        self.assertNotRegex(start.replace('twinKindLabel', '').replace('subject_kind', '').replace('PA_KINDS', '').replace('p.kind', '').replace("k[0]", ''), r"\bkinds?\b")
        for need in ("var PA_JOIN = {", "contest: { join: 'which'", "price: { join: 'what they would pay for'", "var PA_BUILD_KEY = '__build__';", 'function paAudiencePhrase(key)', 'function paAudienceNoun(key)',
                     'function paIndex(p, compact)', 'function paIndexStatus(p, i)', 'function paGoalText(p)', 'function paGoalValue()',
                     "if (p.step <= 1) out += paStart(p);", "else if (p.step === 2) out += paRail() + paStepPlan(p);", 'window.dtwPaKind = function(k) { var p = pa(); p.goal = paGoalValue();',
                     "window.dtwPaDdPick = function(key) { dtwPaDdToggle(false); if (key === PA_BUILD_KEY) { dtwBuildOpen(true); return; } pa().picked = true; dtwPaPickTwin(key); };",
                     "return { step: 0, key: S.key || '', picked: false,", "if (p.step === 0) { if (!p.key || !p.picked) return; p.step = 1; }",
                     "h.innerHTML = '<div class=\"dtw-pa-wrap' + (p.step <= 1 ? ' start' : '') + '\">' + out + '</div>';"):
            self.assertIn(need, js, need)
        self.assertNotRegex(js[js.index('var PA_JOIN = {'):js.index('var PA_BUILD_KEY')], r"ph: '", 'no example placeholder on any shape')
        css = _read(CSS)
        for need in ('.dtw-pa-sent { font-size: 44px; font-weight: 200;', '.dtw-pa-goalin:empty::before { content: attr(data-ph)', '.dtw-pa-ix { display: grid; grid-template-columns: 40px 150px', '.dtw-pa-ix.on .n, #digitalTwinsIQView .dtw-pa-ix.on .t { color: var(--dtw-accent); }',
                     '.dtw-pa-dd .nm { min-width: 0; font-size: 14px; font-weight: 400;', '.dtw-pa-dd .k { font-size: 10px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; color: var(--dtw-mute); white-space: nowrap; width: 64px; }',
                     '.dtw-pa-dd .n { font-size: 12.5px; color: var(--dtw-body); white-space: nowrap; font-variant-numeric: tabular-nums; text-align: right; }', '.dtw-pa-dd-opt { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto;',
                     '.dtw-pa-dd-opt.build .plus', '.dtw-pa-dd-rule { height: 1px;', '.dtw-pa-startline { display: flex; align-items: center;', '@media (max-width: 760px) {\n            #digitalTwinsIQView .dtw-pa-sent { font-size: 28px',
                     '.dtw-pa-sent .dtw-pa-dd.inline .dtw-pa-dd-menu { width: auto; left: 0; right: 0;',
                     # the polish (2026-11-23): the start reads to the page's edge, the question slot starts a fresh line when it cannot fit whole,
                     # Plan it is quiet while disabled and lit once it can be pressed, a hairline between twin rows, the phone menu stacks its rows
                     '.dtw-pa-wrap.start { max-width: 1200px; }', '.dtw-pa-goalin { display: inline-block; max-width: 100%;', '.dtw-pa-startline .dtw-lb.go.pa[disabled] { background: var(--dtw-track); color: var(--dtw-mute);',
                     '.dtw-pa-dd-opt + .dtw-pa-dd-opt { border-top: 1px solid var(--dtw-hair); }', '.dtw-pa-dd .dtw-pa-dd-opt .n { grid-column: 2; text-align: left;'):
            self.assertIn(need, css, need)
        for gone in ('.dtw-pa-sent-or', '.dtw-pa-dd-foot', '.dtw-pa-build', '.dtw-pa-startrow', '.dtw-pa-pick'):
            self.assertNotIn(gone, css, 'dead CSS from the removed affordances: ' + gone)
        self.assertNotIn('.dtw-pa-dd .nm { font-weight: 600', css, 'names in the list are regular weight')


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
        code = '\n'.join(_fn(js, n) for n in ('paHero', 'paStart', 'paTwinRow', 'paGoalValue', 'paGoalText', 'paAudienceNoun', 'paAudiencePhrase', 'paIndexStatus', 'paIndex', 'paRail', 'paEarlier', 'paCallClass', 'paCallWord', 'paRun', 'paTwinName', 'paPeople', 'pa', 'paFresh',
                                              'twinKindLabel', 'twinQualifier', 'twinNameShared', 'twinBuiltLine', 'twinOptionLabel', 'twinPrice', 'fmtN', 'fmtDay', 'jsq'))
        for v in ('MONTHS', 'PA_STEPS', 'PA_LEVELS', 'PA_JOIN', 'PA_BUILD_KEY', 'PA_PEOPLE_WORDS', 'SEP'):
            a = js.index('            var ' + v + ' = '); code = js[a:js.index(';\n', a) + 2] + code
        a = js.index('            var PA_KINDS = ['); code = js[a:js.index('];\n', a) + 3] + code
        return ("global.window = global; function esc(x) { return String(x == null ? '' : x).replace(/[&<>\"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]; }); }\n"
                "function el() { return null; } function groupCard() { return null; } function groupQuote() { return ''; } function groupTierDefault() { return ''; } function twinName() { return 'Jennifer Beals'; }\n"
                "var paDd = { open: false }; var S = { runs: " + json.dumps(RUNS) + ", key: 'jb', pricing: ['Digital Twin: $750 per twin'], twin: { meta: { size_us: 2776141 }, archetypes: { noun: 'followers' } }, pa: null };\n" + code + "\n")

    def test_the_start_waits_for_a_pick_even_with_a_twin_open(self):
        node = self._code() + r'''
var base = paFresh(); base.runs = [];
S.pa = Object.assign({}, base, { step: 0, goal: '', kind: '' }); var html = paStart(S.pa);
var opts = (html.match(/<div class="dtw-pa-dd-opt[^"]*" role="option" id="([^"]+)" data-key="([^"]+)"/g) || []).map(function(m) { return m.match(/data-key="([^"]+)"/)[1]; });
var first = html.match(/<div class="dtw-pa-dd-opt build" role="option" id="dtwPaDdOptBuild" data-key="__build__" data-build="1"[^>]*onclick="dtwPaDdPick\('__build__'\)"[^>]*><span class="k"><i class="plus" aria-hidden="true">\+<\/i><\/span><span class="nm">Build new twin group<\/span><span class="n">\$750<\/span><\/div><div class="dtw-pa-dd-rule"/);
console.log(JSON.stringify({ ask: /<span class="w">Ask<\/span>/.test(html), control: (html.match(/dtw-pa-dd-btn[^>]*><span class="nm( ph)?">([^<]+)</) || []).slice(1), keyWasOpen: base.key, picked: !!base.picked,
  join: (html.match(/id="dtwPaJoin">([^<]+)</) || [])[1], ph: (html.match(/data-ph="([^"]+)"/) || [])[1], opts: opts, first: !!first, selected: (html.match(/aria-selected="true"/g) || []).length,
  quals: (html.match(/class="built">([^<]+)</g) || []).map(function(m) { return m.slice(14, -1); }), rowShape: /<span class="k">Talent<\/span><span class="nm">Jennifer Beals<\/span><span class="n">2,776,141 people<\/span>/.test(html),
  build: { or: /or build a new twin/.test(html), card: /Not here\? Build a new twin|dtw-pa-build|dtwTutorialOpen/.test(html), opens: (html.match(/dtwBuildOpen\(true\)/g) || []).length },
  planDisabled: /id="dtwPaPlanBtn" disabled/.test(html), startline: /<div class="dtw-pa-startline"><button type="button" class="dtw-lb go pa" id="dtwPaPlanBtn" disabled onclick="dtwPaNext\(\)">Plan it<i>&rarr;<\/i><\/button><span class="price">Planning is free\./.test(html),
  index: (html.match(/class="dtw-pa-ix[^"]*"><span class="n">0(\d)<\/span><span class="t">([^<]+)</g) || []).length, activeRow: (html.match(/class="dtw-pa-ix on[^"]*"><span class="n">0(\d)/) || [])[1], firstStatus: (html.match(/<span class="n">01<\/span><span class="t">Audience<\/span>.*?<span class="s">([^<]+)</) || [])[1], header: (html.match(/<span>five steps([^<]*)<\/span>/) || [])[1],
  words: (html.replace(/<[^>]+>/g, ' ').match(/\b(panel|kinds?|persona|segment|cohort|model|survey)\b/ig) || []) }));
'''
        got = self._run(node)
        self.assertTrue(got['ask'])
        self.assertEqual(got['control'], [' ph', 'Select twin'], 'the control reads Select twin, muted, until the user picks here')
        self.assertEqual((got['keyWasOpen'], got['picked']), ('jb', False), 'an open twin on the page is not a pick')
        self.assertEqual(got['join'], 'whether')
        self.assertEqual(got['ph'], 'your question', 'no example question is pre-filled')
        self.assertEqual(got['opts'], ['__build__'] + [r['key'] for r in RUNS], 'Build new twin group first, then every visible twin')
        self.assertTrue(got['first'], 'the first option: a plus, Build new twin group, the price muted on the right, then the hairline')
        self.assertEqual(got['selected'], 0)
        self.assertEqual(got['quals'], ['Built Nov 19 by Casey Pearson', 'Built Oct 30 by Jenna Menking'])
        self.assertTrue(got['rowShape'], 'type column, name, people: one row shape for every twin')
        self.assertEqual(got['build'], {'or': False, 'card': False, 'opens': 0}, 'no build link, card or tutorial on the start')
        self.assertTrue(got['planDisabled'] and got['startline'], 'Plan it sits with the price line, disabled until a twin is chosen and a question typed')
        self.assertEqual(got['index'], 5)
        self.assertEqual(got['activeRow'], '1', 'nothing chosen: Audience is the active step')
        self.assertEqual(got['firstStatus'], 'Select twin')
        self.assertEqual(got['header'], '')
        self.assertEqual(got['words'], [])

    def test_a_pick_names_the_audience(self):
        node = self._code() + r'''
var base = paFresh(); base.runs = [];
function ctl(key, goal, twinNoun) { S.key = key; S.twin = twinNoun === undefined ? null : { meta: {}, archetypes: { noun: twinNoun } }; S.pa = Object.assign({}, base, { step: 1, key: key, picked: true, goal: goal || '', kind: '' }); var h = paStart(S.pa);
  return { nm: (h.match(/dtw-pa-dd-btn[^>]*><span class="nm">([^<]+)</) || [])[1], on: (h.match(/dtw-pa-dd-opt on" role="option" id="dtwPaDdOpt\d+" data-key="([^"]+)"/) || [])[1], listName: (h.match(new RegExp('data-key="' + key + '"[^>]*><span class="k">[^<]*<\/span><span class="nm">([^<]+)<')) || [])[1],
    planOn: !/id="dtwPaPlanBtn" disabled/.test(h), activeRow: (h.match(/class="dtw-pa-ix on[^"]*"><span class="n">0(\d)/) || [])[1], status: (h.match(/<span class="n">01<\/span><span class="t">Audience<\/span>.*?<span class="s">([^<]+)</) || [])[1], header: (h.match(/<span>five steps([^<]*)<\/span>/) || [])[1] }; }
var out = { jb: ctl('jb', '', 'followers'), jbTyped: ctl('jb', 'a two-year activewear deal would pay off', 'followers'), pp: ctl('pp', '', 'subscribers'), ppList: ctl('pp0', ''), tx: ctl('tx', '', 'people'), oh: ctl('oh', '', 'voters'), mr: ctl('mr', ''), gg: ctl('gg', '', 'people'), ae: ctl('ae', '', undefined) };
console.log(JSON.stringify(out));
'''
        got = self._run(node)
        self.assertEqual(got['jb']['nm'], 'Jennifer Beals followers', 'Ask Jennifer Beals followers whether ...')
        self.assertEqual(got['pp']['nm'], 'Paramount+ subscribers')
        self.assertEqual(got['ppList']['nm'], 'Paramount+ subscribers', 'the noun comes from the list entry when the twin is not the open one')
        self.assertEqual(got['tx']['nm'], 'texas voters', 'a name that already names people stands as is')
        self.assertEqual(got['oh']['nm'], 'ohio gubernatorial voters')
        self.assertEqual(got['gg']['nm'], 'the Gilmore Girls audience', 'a title whose people are only people reads as its audience')
        self.assertEqual(got['mr']['nm'], 'the Mark Rober audience', 'no noun anywhere: the audience')
        self.assertEqual(got['ae']['nm'], 'Alix Earle followers', 'the list entry carries the noun when the open twin has none')
        self.assertEqual((got['jb']['on'], got['jb']['listName']), ('jb', 'Jennifer Beals'), 'the open list keeps the plain name')
        self.assertEqual((got['jb']['planOn'], got['jbTyped']['planOn']), (False, True), 'Plan it goes live with a pick and a question')
        self.assertEqual((got['jb']['activeRow'], got['jb']['status'], got['jb']['header']), ('2', 'Jennifer Beals, 2,776,141 people, 16 archetypes', ', the first already done'))

    def test_shapes_switch_the_words_and_keep_the_text(self):
        node = self._code() + r'''
var base = paFresh(); base.key = 'jb'; base.picked = true; base.runs = [];
function join(kind) { S.pa = Object.assign({}, base, { step: 1, goal: 'a two-year activewear deal would pay off', kind: kind }); var h = paStart(S.pa); return { join: (h.match(/id="dtwPaJoin">([^<]+)</) || [])[1], typed: /a two-year activewear deal would pay off<\/span>/.test(h), ph: (h.match(/data-ph="([^"]+)"/) || [])[1], on: (h.match(/class="dtw-pa-shape on"[^>]*>([^<]+)</) || [])[1] || '', status: (h.match(/<span class="n">02<\/span><span class="t">Question<\/span>.*?<span class="s">([^<]+)</) || [])[1], planOn: !/id="dtwPaPlanBtn" disabled/.test(h) }; }
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
        self.assertEqual(set(got[k]['ph'] for k in ('none', 'effect', 'contest', 'go', 'price', 'open')), {'your question'}, 'the empty field says only your question, whatever the shape')
        self.assertEqual(got['contest']['on'], 'Will X win?')
        self.assertEqual(got['contest']['status'], 'Will X win?')
        self.assertTrue(all(got[k]['planOn'] for k in ('none', 'contest')), 'Plan it is live once there is a question')
        self.assertEqual(got['goalContest'], 'Which cut wins them over, the Flashdance cut or the Bette Porter cut')
        self.assertEqual(got['goalPrice'], 'What they would pay for a hoodie from her collection, at $45')
        self.assertEqual(got['goalAsIs'], 'Will her followers buy a $45 hoodie?', 'a question typed whole goes to the room as written')
        self.assertEqual(got['goalOpen'], 'What it would take for them to see her as a fashion name')

    def test_the_index_fills_in_and_goes_back(self):
        node = self._code() + r'''
var base = paFresh(); base.key = 'jb'; base.picked = true; base.runs = []; var plan = { goal: 'g', success: 's', questions: [{ q: 'a', level: 'macro' }, { q: 'b', level: 'mid' }, { q: 'c', level: 'micro' }] };
function ix(p) { S.pa = p; var h = paIndex(p, p.step >= 2); return { on: (h.match(/class="dtw-pa-ix on[^"]*"><span class="n">0(\d)/) || [])[1], backs: (h.match(/<button type="button" onclick="dtwPaGo\((\d)\)"/g) || []).map(function(m) { return m.slice(-3, -2); }), statuses: (h.match(/<span class="s">([^<]*)<\/span>/g) || []).map(function(m) { return m.slice(16, -7); }), compact: /dtw-pa-index compact/.test(h) }; }
var out = {};
out.start = ix(Object.assign({}, base, { step: 0 }));
out.unpicked = ix(Object.assign({}, base, { step: 0, picked: false }));
out.noTwin = ix(Object.assign({}, base, { step: 0, key: '', picked: false }));
out.planUnpicked = ix(Object.assign({}, base, { step: 2, picked: false, goal: 'x', kind: 'go', plan: plan }));
out.plan = ix(Object.assign({}, base, { step: 2, goal: 'x', kind: 'go', plan: plan }));
out.run = ix(Object.assign({}, base, { step: 3, goal: 'x', kind: 'go', plan: plan, guided: { status: 'running', done: 1, total: 3 } }));
out.stopped = ix(Object.assign({}, base, { step: 3, goal: 'x', plan: plan, guided: { status: 'stopped', done: 1, total: 3 } }));
out.report = ix(Object.assign({}, base, { step: 4, goal: 'x', plan: plan, guided: { status: 'done', done: 3, total: 3 }, report: { outcome: { call: 'lean yes' } } }));
out.busy = ix(Object.assign({}, base, { step: 2, goal: 'x', plan: plan, busy: true }));
console.log(JSON.stringify(out));
'''
        got = self._run(node)
        self.assertEqual((got['start']['on'], got['start']['backs']), ('2', []), 'on the start nothing is a way back')
        self.assertEqual((got['unpicked']['on'], got['unpicked']['statuses'][0]), ('1', 'Select twin'), 'an open twin that was not picked here leaves Audience open')
        self.assertEqual((got['noTwin']['on'], got['noTwin']['statuses'][0]), ('1', 'Select twin'))
        self.assertEqual(got['planUnpicked']['statuses'][0], 'Jennifer Beals, 2,776,141 people, 16 archetypes', 'past the start the audience is always named, pick flag or not (a restored run)')
        self.assertEqual((got['plan']['on'], got['plan']['backs'], got['plan']['compact']), ('3', ['1', '1'], True), 'Audience and Question lead back to the start from Plan')
        self.assertEqual(got['plan']['statuses'][1:3], ['Should I do X?', '3 questions, from the wide to the narrow'])
        self.assertEqual((got['run']['on'], got['run']['backs']), ('4', ['1', '1']))
        self.assertEqual(got['run']['statuses'][3], '1 of 3 answered')
        self.assertEqual(got['stopped']['statuses'][3], '1 of 3 answered, stopped')
        self.assertEqual((got['report']['on'], got['report']['statuses'][3], got['report']['statuses'][4]), ('5', 'Done', 'Verdict: Leaning yes'))
        self.assertNotIn('2', got['report']['backs'], 'never back into the plan once the room has run')
        self.assertEqual(got['busy']['backs'], [], 'nothing moves while the page is busy')

    def test_dropdown_keyboard_pick_and_build(self):
        js = _read(JS)
        bs = js.index('            var paDd = { open: false'); be = js.index('            function paQRow(q, i, editable) {')
        behaviour = js[bs:be]
        node = r'''
var reg = {}, focused = null, picked = [], built = [], clicks = [];
function mk(id, cls) { var n = { id: id || '', cls: {}, attrs: {}, hidden: false, children: [], listeners: {}, textContent: '', value: '',
  classList: { add: function(c) { n.cls[c] = 1; }, remove: function(c) { delete n.cls[c]; }, contains: function(c) { return !!n.cls[c]; }, toggle: function(c, on) { if (on) n.cls[c] = 1; else delete n.cls[c]; } },
  setAttribute: function(k, v) { n.attrs[k] = String(v); }, getAttribute: function(k) { return n.attrs[k] == null ? null : n.attrs[k]; },
  focus: function() { focused = n; }, scrollIntoView: function() {}, appendChild: function(c) { n.children.push(c); reg[c.id] = c; }, remove: function() {},
  contains: function(x) { return x === n || n.children.indexOf(x) >= 0; }, querySelector: function(sel) { return sel === '.nm' ? { textContent: n.nm || '' } : null; },
  querySelectorAll: function() { return n.children.filter(function(c) { return c.cls['dtw-pa-dd-opt']; }); } };
  (cls || '').split(' ').forEach(function(c) { if (c) n.cls[c] = 1; }); if (id) reg[id] = n; return n; }
var dd = mk('dtwPaDd'), btn = mk('dtwPaDdBtn'), menu = mk('dtwPaDdMenu'), list = mk('dtwPaDdList'), find = mk('dtwPaDdFind'); menu.hidden = true; dd.children = [btn, menu];
var RUNS = __RUNS__, PA_BUILD_KEY = '__build__', S = { pa: { picked: false, key: 'jb' } };
function pa() { return S.pa; } global.window = global; window.dtwBuildOpen = function(on) { built.push(on); };
var b0 = mk('dtwPaDdOptBuild', 'dtw-pa-dd-opt build'); b0.attrs['data-key'] = PA_BUILD_KEY; b0.attrs['data-build'] = '1'; b0.attrs['data-hay'] = 'build new twin group'; b0.nm = 'Build new twin group'; list.appendChild(b0);
RUNS.forEach(function(r, i) { var o = mk('dtwPaDdOpt' + i, 'dtw-pa-dd-opt' + (__ON__ && r.key === 'jb' ? ' on' : '')); o.attrs['data-key'] = r.key; o.attrs['data-hay'] = r.name.toLowerCase(); o.nm = r.name; list.appendChild(o); });
global.document = { createElement: function() { return mk('', ''); }, addEventListener: function(t, f) { clicks.push(f); } };
function el(id) { return reg[id] || null; }
global.window = global; window.dtwPaPickTwin = function(k) { picked.push(k); };
''' + behaviour + r'''
var ev = function(key, target) { return { key: key, target: target || list, preventDefault: function() {}, stopPropagation: function() {} }; };
dtwPaDdKey(ev('ArrowDown', btn)); var afterOpen = { open: paDd.open, shown: !menu.hidden, active: paDd.active, focusOnFind: focused === find };
dtwPaDdKey(ev('ArrowUp')); var atBuild = { active: paDd.active, isBuild: list.children[paDd.active].attrs['data-build'] === '1' };
dtwPaDdKey(ev('Enter')); var afterBuild = { built: built.slice(), picked: picked.slice(), closed: !paDd.open, pickedFlag: S.pa.picked };
dtwPaDdToggle(true); dtwPaDdKey(ev('ArrowDown')); dtwPaDdKey(ev('ArrowDown')); var afterArrows = paDd.active;
dtwPaDdKey(ev('m')); var typed = paDd.active;
paDd.typedAt = 0; dtwPaDdKey(ev('b')); var typedB = paDd.active;   // a moment later: a fresh letter
dtwPaDdFilter('para'); var filtered = list.children.filter(function(o) { return !o.hidden; }).map(function(o) { return o.attrs['data-key']; }); var filteredActive = paDd.active;
dtwPaDdFilter('zzz'); var noMatch = { shown: list.children.filter(function(o) { return !o.hidden; }).map(function(o) { return o.attrs['data-key']; }), none: !!reg['dtwPaDdNone'], active: paDd.active };
dtwPaDdFilter(''); paDdMark(2); dtwPaDdKey(ev('Enter')); var afterEnter = { picked: picked.slice(), closed: !paDd.open, focusBack: focused === btn, pickedFlag: S.pa.picked };
dtwPaDdToggle(true); dtwPaDdKey(ev('Escape')); var afterEscape = { closed: !paDd.open, focusBack: focused === btn };
dtwPaDdToggle(true); clicks[0]({ target: mk('elsewhere') }); var outside = !paDd.open;
console.log(JSON.stringify({ afterOpen: afterOpen, atBuild: atBuild, afterBuild: afterBuild, afterArrows: afterArrows, typed: typed, typedB: typedB, filtered: filtered, filteredActive: filteredActive, noMatch: noMatch, afterEnter: afterEnter, afterEscape: afterEscape, outside: outside }));
'''
        node = node.replace('__RUNS__', json.dumps(RUNS))
        got = self._run(node.replace('__ON__', 'false'))
        self.assertEqual(got['afterOpen'], {'open': True, 'shown': True, 'active': 1, 'focusOnFind': True}, 'nothing chosen: the menu opens on the first twin, Build new twin group one step up')
        self.assertEqual(got['atBuild'], {'active': 0, 'isBuild': True})
        self.assertEqual(got['afterBuild'], {'built': [True], 'picked': [], 'closed': True, 'pickedFlag': False}, 'Enter on the first option opens the Build drawer and picks nothing')
        self.assertEqual(got['afterArrows'], 3)
        self.assertEqual(got['typed'], 8, 'typing m jumps to Mark Rober')
        self.assertEqual(got['typedB'], 0, 'typing b jumps to Build new twin group')
        self.assertEqual((got['filtered'], got['filteredActive']), (['__build__', 'pp', 'pp0'], 1), 'a search keeps the build line and marks the first match')
        self.assertEqual(got['noMatch'], {'shown': ['__build__'], 'none': False, 'active': 0}, 'no match leaves only Build new twin group, active')
        self.assertEqual(got['afterEnter'], {'picked': ['pv'], 'closed': True, 'focusBack': True, 'pickedFlag': True}, 'Enter on a twin picks through dtwPaPickTwin and marks the pick')
        self.assertEqual(got['afterEscape'], {'closed': True, 'focusBack': True})
        self.assertTrue(got['outside'])
        got2 = self._run(node.replace('__ON__', 'true'))
        self.assertEqual(got2['afterOpen']['active'], 3, 'with a twin chosen the menu opens on it')


if __name__ == '__main__':
    unittest.main(verbosity=2)
