#!/usr/bin/env python3
"""The Focus Group IQ landing (Jenna 2026-11-23: "when you click on focus group it should take
you to a main focus group landing page where you can see your twins and open one, see old
ones, etc... but if you click focus group from the twins page it would open an actual room,
not tak you to the landing. landig would just be a fresh click on it"; her pick: FG10d with
the twins on the left and the groups on the right).

Pins:
- a fresh click on the tab with no room open lands; every path from the Twins page (an
  entry's Open a room, Past work's Reopen the room and Open a room on this, a room deep link,
  a session email link) opens the room or the twin's own console, never the landing; a click
  on the tab while a room is open keeps the room; leaving a room lands, and a click after
  that lands again;
- the landing reads the library index alone: every index entry carries the twin's rooms,
  newest first, capped, with the title, the latest question and the leads through the reader
  scrub, a Predictive Analytics room with its status and verdict, Pilot Testing rooms left out;
- the reader rule: a client seat sees only its own company's rooms, staff see every room, and
  /list cuts the rooms before they leave the server;
- Continue sits on the newest room that can still be picked back up (not a Predictive
  Analytics run, at least one question), Open on every other; the verdict reads on a
  Predictive Analytics entry; the Start's Open a room waits for a pick, Build new twin group
  is the dropdown's first option; the layout is the twins column at 360px on the left and the
  list on the right, stacked twins-first on a phone; no banned word, no em dash, archetype
  vocabulary.
The page's own functions run in node on stubs. Hermetic: no model, no network, no S3.
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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('DTIQ_ROOM_HOST', '0')

JS_PATH = os.path.join(ROOT, 'static', 'digital-twins', 'digital-twins.js')
CSS_PATH = os.path.join(ROOT, 'static', 'digital-twins', 'digital-twins.css')
API_PATH = os.path.join(ROOT, 'digital_twins_api.py')
def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


JS = _read(JS_PATH)
CSS = _read(CSS_PATH)


def _payload():
    sessions = []
    for i in range(15):
        qs = []
        if i % 3:
            qs = [{'q': 'Would you watch this synthetic panel show?', 'stage': 'done', 'finished_at': f'2026-11-{1 + i:02d}T12:30:00Z', 'asked_at': f'2026-11-{1 + i:02d}T12:00:00Z',
                   'distribution': [{'label': 'Yes, the model says so', 'pct': 61.2}, {'label': 'No', 'pct': 38.8}],
                   'read_short': 'Most say yes \u2014 the panel leans in. A second sentence stays out of the index.'}]
        sessions.append({'id': f'r{i}', 'title': f'Room {i} on the synthetic panel \u2013 a test', 'created_by': 'cpearson' if i % 2 else 'admin',
                         'created_at': f'2026-11-{1 + i:02d}T10:00:00Z', 'updated_at': f'2026-11-{1 + i:02d}T12:00:00Z', 'questions': qs})
    sessions.append({'id': 'pa1', 'title': 'Predictive Analytics', 'created_by': 'admin', 'created_at': '2026-11-20T10:00:00Z', 'updated_at': '2026-11-20T10:00:00Z',
                     'guided': {'status': 'done', 'done': 5, 'total': 5, 'finished_at': '2026-11-21T10:00:00Z', 'plan': {'goal': 'Find out whether a boxed set sells at Target', 'questions': [1, 2, 3, 4, 5]},
                                'outcome': {'call': 'lean yes', 'answer': 'A boxed set leans yes: 57.3% would buy. The rest want a lower price.'}},
                     'questions': [{'q': 'Would you buy the set?', 'stage': 'done', 'finished_at': '2026-11-21T09:00:00Z', 'distribution': [{'label': 'Yes', 'pct': 57.3}, {'label': 'No', 'pct': 42.7}]}]})
    sessions.append({'id': 'w1', 'title': 'Still working', 'created_by': 'admin', 'created_at': '2026-11-22T10:00:00Z', 'updated_at': '2026-11-22T10:00:00Z',
                     'questions': [{'q': 'First one?', 'stage': 'researching', 'asked_at': '2026-11-22T10:05:00Z'}]})
    return {'meta': {'name': 'Paramount+', 'created_at': '2026-10-01T00:00:00Z', 'created_by': 'admin', 'size_us': 83791763},
            'archetypes': {'k': 13, 'noun': 'subscribers', 'groups': [{'label': 'A', 'share_pct': 60, 'people': 50000000, 'representative': {'name': 'Ava'}}, {'label': 'B', 'share_pct': 40, 'people': 33791763, 'representative': {'name': 'Ben'}}]},
            'panel_sessions': sessions,
            'pilot_sessions': [{'id': 'pt1', 'title': 'A pilot test', 'mode': 'pilot', 'created_at': '2026-11-23T10:00:00Z', 'updated_at': '2026-11-23T10:00:00Z', 'questions': []}]}


class IndexRooms(unittest.TestCase):
    def test_entry_carries_rooms_capped_newest_first_and_scrubbed(self):
        from migration import digital_twin_synthesis as SYN
        e = SYN.index_entry('digital-twins/admin/P.json.gz', _payload())
        rooms = e['rooms']
        self.assertEqual(len(rooms), SYN.INDEX_ROOMS_CAP, 'capped per twin')
        self.assertEqual([r['id'] for r in rooms[:3]], ['w1', 'pa1', 'r14'], 'newest activity first: the working room, the finished report, then the rooms')
        self.assertNotIn('pt1', [r['id'] for r in rooms], 'Pilot Testing rooms stay out')
        for r in rooms:
            for k in ('id', 'title', 'created_at', 'updated_at', 'questions', 'last_q', 'lead', 'read_short', 'working', 'guided', 'created_by'):
                self.assertIn(k, r)
        blob = json.dumps(rooms).lower()
        for bad in ('synthetic', 'panel', 'model says', '\u2014', '\u2013'):
            self.assertNotIn(bad, blob, f'{bad!r} reached the index')
        r14 = [r for r in rooms if r['id'] == 'r14'][0]
        self.assertEqual(r14['questions'], 1)
        self.assertEqual(r14['lead']['pct'], 61.2)
        self.assertTrue(r14['lead']['label'].startswith('Yes'), r14['lead'])
        self.assertEqual(r14['read_short'], 'Most say yes - the room leans in.', 'the first sentence only, through the reader scrub')
        self.assertFalse(r14['working'])
        pa = [r for r in rooms if r['id'] == 'pa1'][0]
        self.assertEqual(pa['guided']['status'], 'done')
        self.assertEqual(pa['guided']['verdict'], 'Leaning yes')
        self.assertEqual(pa['guided']['goal'], 'Find out whether a boxed set sells at Target')
        self.assertEqual(pa['guided']['answer'], 'A boxed set leans yes: 57.3% would buy.')
        self.assertEqual(pa['updated_at'], '2026-11-21T10:00:00Z', 'the report finish is the last activity')
        w1 = [r for r in rooms if r['id'] == 'w1'][0]
        self.assertTrue(w1['working'])
        self.assertIsNone(w1['lead'])
        self.assertIsNone(w1['guided'])
        # the rest of the entry is as before
        self.assertEqual(e['kinds'], 13)
        self.assertEqual(e['panel_sessions'], 17)

    def test_entry_without_rooms(self):
        from migration import digital_twin_synthesis as SYN
        e = SYN.index_entry('k', {'meta': {'name': 'T', 'created_at': '2026-10-01T00:00:00Z'}})
        self.assertEqual(e['rooms'], [])
        e2 = SYN.index_entry('k', {'meta': {'name': 'T'}, 'panel_sessions': [{'title': 'no id'}, 'junk', {'id': 'ok', 'title': 'Fine'}]})
        self.assertEqual([r['id'] for r in e2['rooms']], ['ok'], 'a session without an id or a non-dict never breaks the index')


class ReaderRule(unittest.TestCase):
    USERS = {'users': {
        'acme1': {'username': 'acme1', 'company': 'Acme', 'role': 'user', 'has_digital_twins_iq_access': True},
        'acme2': {'username': 'acme2', 'company': 'Acme', 'role': 'user', 'has_digital_twins_iq_access': True},
        'zed1': {'username': 'zed1', 'company': 'Zed', 'role': 'user', 'has_digital_twins_iq_access': True},
        'jenna': {'username': 'jenna', 'role': 'super_admin'},
    }}

    def setUp(self):
        from migration import digital_twin_company as CO
        CO.set_users_loader(lambda: self.USERS)
        CO.reset_cache()

    def tearDown(self):
        from migration import digital_twin_company as CO
        CO.set_users_loader(None)
        CO.reset_cache()

    def _runs(self):
        return [{'key': 'digital-twins/acme1/T.json.gz', 'name': 'T', 'created_by': 'acme1', 'company': 'Acme',
                 'rooms': [{'id': 'a', 'title': 'Mine', 'created_by': 'acme1', 'company': ''}, {'id': 'b', 'title': 'A colleague', 'created_by': 'acme2', 'company': ''},
                           {'id': 'c', 'title': 'Staff on our twin', 'created_by': 'jenna', 'company': ''}, {'id': 'd', 'title': 'Another company', 'created_by': 'zed1', 'company': ''},
                           {'id': 'e', 'title': 'No seat, the twin company', 'created_by': '', 'company': ''}, {'id': 'f', 'title': 'No seat, written Zed', 'created_by': '', 'company': 'Zed'}]}]

    def test_client_sees_own_company_rooms_only(self):
        from migration import digital_twins as DT
        runs = DT.scope_index_rooms(self._runs(), self.USERS['users']['acme1'], self.USERS)
        self.assertEqual([r['id'] for r in runs[0]['rooms']], ['a', 'b', 'e'], 'a colleague and the twin-company room, never staff, never another company')
        self.assertTrue(all('company' not in r for r in runs[0]['rooms']), 'the company written on a room never leaves the server')

    def test_staff_see_every_room(self):
        from migration import digital_twins as DT
        runs = DT.scope_index_rooms(self._runs(), self.USERS['users']['jenna'], self.USERS)
        self.assertEqual(len(runs[0]['rooms']), 6)

    def test_fails_closed(self):
        from migration import digital_twins as DT
        self.assertFalse(DT.room_visible({'id': 'x', 'created_by': 'acme1'}, None, None))
        runs = DT.scope_index_rooms([{'key': 'k', 'rooms': [{'id': 'x', 'created_by': 'acme1'}]}], {'username': 'nobody'}, self.USERS)
        self.assertEqual(runs[0]['rooms'], [], 'an unknown seat is its own company and sees nothing of Acme')

    def test_list_route_scopes_rooms(self):
        src = _read(API_PATH)
        a = src.index("@bp.route(f'{PREFIX}/list', methods=['GET'])")
        b = src.index('return _nocache(jsonify(out))', a)
        body = src[a:b]
        self.assertLess(body.index('DT.visible_runs('), body.index('DT.scope_index_rooms(vis, user)'), '/list cuts the rooms after the company filter and before the answer leaves')


_NODE = r'''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function slice(a, extraEnds) {
  const ends = ['\n            function ', '\n            window.', '\n            var ', '\n            document.'].concat(extraEnds || []).map(function(m) { const i = src.indexOf(m, a + 10); return i < 0 ? src.length : i; });
  return src.slice(a, Math.min.apply(null, ends));
}
function fn(name) { const a = src.indexOf('            function ' + name + '('); if (a < 0) throw new Error('missing function ' + name); return slice(a); }
function win(name) { const a = src.indexOf('            window.' + name + ' = '); if (a < 0) throw new Error('missing window.' + name); return slice(a, ['\n            //']); }
function v(name) { const a = src.indexOf('            var ' + name + ' = '); if (a < 0) throw new Error('missing var ' + name); return src.slice(a, src.indexOf(';\n', a) + 2); }
const names = ['fgContinuable', 'fgRoomsAll', 'fgMostRecent', 'fgByLine', 'fgTwinLabel', 'fgRoomCount', 'fgBar', 'fgTwinRow', 'fgLeadLine', 'fgEntry', 'fgLandingHtml', 'ddMake', 'fgDd', 'paTwinRow',
  'twinKindLabel', 'twinQualifier', 'twinNameShared', 'twinBuiltLine', 'twinOptionLabel', 'twinPrice', 'pad2', 'fmtN', 'fmtDay', 'hueFor', 'pct', 'jsq', 'renderPanelConsole', 'syncFgActs', 'libRoomSeedTake'];
let js = ['MONTHS', 'SEP_TXT', 'HUES', 'PA_BUILD_KEY', 'FGL_SHOW', 'DDS', 'TABS', 'GL_OPTIONS', 'DTW_HIDDEN_TABS'].map(v).join('') + names.map(fn).join('\n') + '\n'
  + ['dtwDd', 'dtwFgStart', 'dtwFgShowAll', 'dtwTab', 'dtwOpen', 'dtwTwinRoom', 'dtwLibOpenRoom', 'dtwLibRoomOn', 'dtwPanelClose', 'dtwPanelResume'].map(win).join('\n');
const stubs = `
var window = {}; var DOM = {}; var calls = { newRoom: [], toasts: [], rooms: [] };
function esc(x) { return String(x == null ? '' : x).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function mk(id) { return { id: id, innerHTML: '', textContent: '', hidden: false, value: '', style: { removeProperty: function() {} }, _cls: {}, classList: { toggle: function(c, on) { this._c = this._c || {}; this._c[c] = !!on; }, contains: function(c) { return !!(this._c || {})[c]; }, add: function(c) { this.toggle(c, true); }, remove: function(c) { this.toggle(c, false); } }, setAttribute: function() {}, getAttribute: function() { return null; }, focus: function() {}, querySelector: function() { return null; }, querySelectorAll: function() { return []; }, closest: function() { return null; } }; }
function el(id) { if (id === 'dtwFgHero') return DOM.__room ? mk(id) : null; return DOM[id] || (DOM[id] = mk(id)); }
var document = { querySelector: function() { return null; }, querySelectorAll: function() { return []; }, addEventListener: function() {}, createElement: function() { return mk('x'); } };
var S = { runs: [], twin: null, key: '', tab: 'twins', session: null, pilot: null, plan: null, fg: { sid: null, open: {}, earlier: {}, seat: null, tallyQ: null }, isStaff: true, pricing: ['Digital Twin: $750 per twin'], roomSeed: null, panelKind: '', fgToastT: null };
var PI_HOST = {}; var LA_OPTIONS = ['plan', 'spend'];
// the rest of the page, as stubs: the room's own drawing marks itself ROOM, the twin's no-room console marks itself NOROOM
function noRoomHtml() { return '<div class="dtw-fg-land">NOROOM</div>'; }
function heroHtml() { return 'ROOM'; } function dockHtml() { return ''; } function threadHtml() { return ''; } function composerHtml() { return ''; } function tallyHtml() { return ''; } function popHtml() { return ''; } function theaterHtml() { return ''; }
function paRunning() { return false; } function fgReset(sid) { S.fg.sid = sid; } function clearRoomPoll() {} function clearPortraitTimers() {} function fgLayoutSeats() {} function fgScrollTop() {} function fgDockSize() {}
function syncFgBody() { syncFgActs(); } function rpTick() {} function tallyFocusQ() { return null; } function focusQ() { return null; } function flyQ() { return null; } function setAskEnabled() {} function renderSeatPanel() {} function placePop() {}
function schedulePortraitRefresh() {} function scheduleRoomPoll() {} function scheduleAssetPoll() {} function fgShown() {} function renderPanelSessions() {} function remember() {} function fgToast(t) { calls.toasts.push(t); } function dtwSeatOpen() {} function qInFlight() { return false; }
function dtwRoomsOpen() {} function dtwNewRoomOpen(on) { calls.newRoom.push(!!on); } function dtwBuildOpen() {} function dtwBuildTwin() {} function actsHtml() { return 'ACTS'; } function dtwHowtoOpen() {} function piFocusScroll() {}
function productRuns(t) { return (t && t.runs) || []; } function fillTwinSelects() {} function renderSpine() {} function renderLibrary() {} function renderTwin() {} function syncInputCards() {} function renderEmpty() {} function syncSpendSources() {} function renderPilotConsole() {} function renderSpend() {} function renderLaunch() {} function renderRun() {}
function isPiTab(tab) { return tab === 'greenlight' || !!PI_HOST[tab]; } function piTabFor() { return 'pilot'; } function syncGreenlightOptions() {} function renderNormsTab() {} function renderPredict() {} function syncLaunchOptions() {} function kindLabel(k) { return k; }
function libDoc(key) { return S.key === key ? S.twin : null; } function libRun(doc, id) { return ((doc || {}).runs || []).filter(function(r) { return r.id === id; })[0] || null; }
var TWINS = __TWINS__;
function api(path) { var key = path.replace(/^\\//, ''); var t = TWINS[key]; return Promise.resolve(t ? { twin: JSON.parse(JSON.stringify(t)), summary: {} } : { twin: null }); }
`;
eval(stubs + js + `
var assert = require('assert');
var dtwTab = window.dtwTab, dtwOpen = window.dtwOpen, dtwTwinRoom = window.dtwTwinRoom, dtwLibOpenRoom = window.dtwLibOpenRoom, dtwLibRoomOn = window.dtwLibRoomOn, dtwPanelClose = window.dtwPanelClose, dtwPanelResume = window.dtwPanelResume, dtwFgStart = window.dtwFgStart, dtwDd = window.dtwDd;
S.runs = __RUNS__;
function console_() { return el('dtwPnConsole').innerHTML; }
function landing() { return console_().indexOf('id="dtwFgLanding"') >= 0; }
(async function() {
  // 1. a fresh click on the tab with no room open lands
  dtwTab('panel');
  assert.strictEqual(S.fgLanding, true, 'a fresh click lands');
  assert.ok(landing() && console_().indexOf('NOROOM') < 0, 'the landing, not the twin console');
  assert.strictEqual(el('dtwFgActs').hidden, true, 'the spine actions step aside on the landing');
  var h = console_();
  // the layout: the twins column first (left, above on a phone), the list second
  assert.ok(h.indexOf('class="dtw-fgl-side"') < h.indexOf('class="dtw-fgl-main"'), 'twins first, the list second');
  assert.strictEqual((h.match(/class="dtw-fgl-twin"/g) || []).length, 3, 'every twin the seat can see');
  assert.strictEqual((h.match(/class="dtw-fgl-room/g) || []).length - 1, 5, 'every room across the twins');
  assert.ok(h.indexOf('data-key="pv"') >= 0 && h.indexOf('data-sid="pa1"') >= 0, 'rooms carry their twin and their id');
  // newest first; Continue on the newest room that can still be picked back up (not the finished report, not the empty room)
  var order = (h.match(/data-sid="([^"]+)"/g) || []).map(function(m) { return m.slice(10, -1); });
  assert.deepStrictEqual(order, ['empty1', 'pa1', 'room1', 'old1', 'other1'], 'newest activity first');
  assert.strictEqual((h.match(/>Continue<i>/g) || []).length, 1, 'one Continue');
  var recentArticle = h.slice(h.indexOf('dtw-fgl-room recent'), h.indexOf('</article>', h.indexOf('dtw-fgl-room recent')));
  assert.ok(recentArticle.indexOf('data-sid="room1"') >= 0 && recentArticle.indexOf('Most recent') >= 0 && recentArticle.indexOf('>Continue<i>') >= 0, 'Continue and the marker sit on room1');
  assert.ok(/data-sid="pa1"[\\s\\S]*?<b>Verdict:<\\/b> Leaning yes\\. <span class="rd">Six in ten would pay for a year\\.<\\/span>/.test(h), 'the Predictive Analytics entry reads its verdict');
  // a contest's verdict is the winning option and the answer opens on it: said once
  var saved0 = S.runs; S.runs = JSON.parse(JSON.stringify(saved0)); S.runs[0].rooms[1].guided.verdict = 'A new weeknight live sports package'; S.runs[0].rooms[1].guided.answer = 'A new weeknight live sports package is the clear pick at 55.7%.';
  assert.ok(/<b>Verdict:<\\/b> A new weeknight live sports package is the clear pick at 55\\.7%\\.<\\/span>/.test(fgLandingHtml()), 'the winning option is not said twice'); S.runs = saved0;
  assert.ok(/data-sid="pa1"[\\s\\S]*?tag pa">Predictive Analytics</.test(h) && /data-sid="pa1"[\\s\\S]*?<button type="button" class="t"[^>]*>Will they pay for a year\\?</.test(h), 'the report is marked and titled by its goal');
  assert.ok(/data-sid="room1"[\\s\\S]*?<b>Lead:<\\/b> Price, 50\\.0%/.test(h), 'a room reads its latest lead');
  assert.ok(/data-sid="empty1"[\\s\\S]*?No question asked yet/.test(h), 'an empty room says so and never takes Continue');
  assert.ok(h.indexOf('Prime Video <span class="by">(Jenna Menking)</span>') >= 0 && h.indexOf('<span class="built">Built Oct 30 by Jenna Menking</span>') >= 0, 'same-name twins carry who built them');
  assert.ok(/dtwLibOpenRoom\\('pv', 'room1'\\)/.test(h), 'Open and Continue open the room on its twin');
  assert.ok(/dtwTwinRoom\\('pv'\\)/.test(h), 'a twin row opens a room on that twin');
  assert.ok(h.indexOf('4 focus groups') >= 0 && h.indexOf('1 focus group<') >= 0 && h.indexOf('0 focus groups') >= 0 && h.indexOf('13 archetypes') >= 0 && h.indexOf('83,791,763 people') >= 0, 'the twin facts');
  // the Start: Select twin, Build new twin group first, Open a room waits for a pick
  assert.ok(h.indexOf('<span class="nm ph">Select twin</span>') >= 0 && /id="dtwFgStartBtn" disabled/.test(h), 'the Start waits for a pick');
  var menu = h.slice(h.indexOf('id="dtwFgDdList"'));
  assert.ok(menu.indexOf('Build new twin group') < menu.indexOf('data-key="pv"') && menu.indexOf('class="dtw-pa-dd-opt build"') >= 0 && menu.indexOf('class="dtw-pa-dd-rule"') >= 0, 'Build new twin group is the first option, a hairline under it');
  // 2. the Start: a pick turns Open a room live, and it opens the New room drawer on that twin (the library's Open a room)
  dtwDd('dtwFg').pick('pv'); assert.strictEqual(S.fgPick, 'pv'); assert.ok(!/id="dtwFgStartBtn" disabled/.test(console_()), 'live after a pick');
  await dtwFgStart();
  assert.strictEqual(S.key, 'pv'); assert.strictEqual(S.fgLanding, false, 'starting a room leaves the landing');
  assert.ok(console_().indexOf('NOROOM') >= 0 && !landing(), 'the twin\\'s own console under the drawer');
  assert.deepStrictEqual(calls.newRoom.slice(-1), [true], 'the New room drawer opens');
  // 3. from the Twins page: an entry's Open a room on another twin
  S.fgLanding = true; await dtwTwinRoom('jb');
  assert.strictEqual(S.key, 'jb'); assert.strictEqual(S.fgLanding, false); assert.ok(console_().indexOf('NOROOM') >= 0 && !landing(), 'never the landing');
  // a click on the tab while the twin console shows (no room) is a fresh click: it lands
  dtwTab('panel'); assert.ok(landing(), 'a fresh click lands again');
  // 4. Past work's Reopen the room: the room itself
  await dtwLibOpenRoom('pv', 'room1');
  assert.ok(S.session && S.session.id === 'room1', 'the room is open'); assert.strictEqual(S.fgLanding, false); assert.ok(console_().indexOf('ROOM') >= 0 && !landing(), 'the room, never the landing');
  // a click on the tab while a room is open keeps the room
  dtwTab('panel'); assert.ok(S.session && S.session.id === 'room1' && console_().indexOf('ROOM') >= 0 && !landing(), 'the room stays on a tab click');
  // 5. leaving a room lands; a click after that lands again; a reload remembers no room
  dtwPanelClose();
  assert.strictEqual(S.session, null); assert.strictEqual(S.fgLanding, true); assert.ok(landing(), 'leaving a room lands');
  assert.ok(/in the list/.test(calls.toasts.slice(-1)[0]), 'the toast points at the list');
  dtwTab('twins'); dtwTab('panel'); assert.ok(landing(), 'back on the tab: the landing');
  // 6. a room deep link and a session email link take the same road: dtwOpen(twin, null, room, null, 'panel')
  await dtwOpen('pv', null, 'pa1', null, 'panel');
  assert.ok(S.session && S.session.id === 'pa1' && !landing(), 'a deep link opens the room');
  dtwPanelResume('room1'); assert.ok(S.session && S.session.id === 'room1' && !landing(), 'resume opens the room');
  dtwPanelClose(); assert.ok(landing());
  // 7. Past work's Open a room on this (a run): the New room drawer on the twin, never the landing
  S.key = 'pv'; S.twin = JSON.parse(JSON.stringify(TWINS['pv'])); S.twin.runs = S.twin.runs || [];
  await dtwLibRoomOn('pv', 'run1');
  assert.strictEqual(S.fgLanding, false); assert.ok(console_().indexOf('NOROOM') >= 0 && !landing(), 'a room on a run is the twin console under the drawer');
  assert.ok(S.roomSeed && S.roomSeed.q === 'How does the next episode land?', 'the run\\'s question is ready for the room');
  // 8. the vocabulary on the landing's own copy: no banned word, no em dash, archetype never kind
  S.fgLanding = true; S.session = null; dtwTab('panel');
  var text = console_().replace(/<[^>]+>/g, ' ');
  var bad = text.match(/\\b(synthetic|synth|modell?ed|model|estimated|research|panel|panelists?|sample|persona|segment|cohort|confidence|survey|prediction|simulat\\w*|kinds?)\\b/ig);
  assert.strictEqual(bad, null, 'banned: ' + JSON.stringify(bad));
  assert.ok(!/\\u2014|\\u2013/.test(text), 'no em or en dash');
  assert.ok(/archetypes/.test(text));
  // 9. no twins: the landing says so and builds
  var saved = S.runs; S.runs = []; dtwTab('panel');
  assert.ok(/No twins yet/.test(console_()) && /dtwBuildTwin\\(\\)/.test(console_()) && /Focus groups land here/.test(console_()), 'the empty landing');
  S.runs = saved.map(function(r) { return Object.assign({}, r, { rooms: [] }); }); dtwTab('panel');
  assert.ok(/No focus groups yet/.test(console_()), 'twins and no rooms');
  console.log('OK');
})().catch(function(e) { console.error(e && e.stack || e); process.exit(1); });
`);
'''

RUNS = [
    {'key': 'pv', 'name': 'Prime Video', 'subject_kind': 'brand', 'format': 'brand', 'size_us': 83791763, 'kinds': 13, 'created_at': '2026-10-30T10:00:00Z', 'built_by': 'Jenna Menking', 'shares': [60, 40], 'panel_sessions': 4,
     'rooms': [
         {'id': 'empty1', 'title': 'Empty room', 'created_at': '2026-11-22T10:00:00Z', 'updated_at': '2026-11-22T10:00:00Z', 'questions': 0, 'last_q': '', 'lead': None, 'read_short': '', 'working': False, 'guided': None, 'created_by': 'admin'},
         {'id': 'pa1', 'title': 'Predictive Analytics', 'created_at': '2026-11-18T20:00:00Z', 'updated_at': '2026-11-19T00:29:30Z', 'questions': 1, 'last_q': 'Would you pay?', 'lead': {'label': 'Yes', 'pct': 61.0}, 'read_short': 'Most would pay.', 'working': False,
          'guided': {'status': 'done', 'goal': 'Will they pay for a year?', 'done': 1, 'total': 1, 'verdict': 'Leaning yes', 'answer': 'Six in ten would pay for a year.'}, 'created_by': 'admin'},
         {'id': 'room1', 'title': 'What stops one more service', 'created_at': '2026-11-12T19:00:00Z', 'updated_at': '2026-11-12T19:40:00Z', 'questions': 1, 'last_q': 'What stops you from adding another service?', 'lead': {'label': 'Price', 'pct': 50.0}, 'read_short': 'Price leads.', 'working': False, 'guided': None, 'created_by': 'admin'},
         {'id': 'old1', 'title': 'An older room', 'created_at': '2026-11-02T19:00:00Z', 'updated_at': '2026-11-02T19:40:00Z', 'questions': 3, 'last_q': 'Third?', 'lead': {'label': 'No', 'pct': 55.5}, 'read_short': '', 'working': False, 'guided': None, 'created_by': 'admin'}]},
    {'key': 'pv2', 'name': 'Prime Video', 'subject_kind': 'brand', 'format': 'brand', 'size_us': 1000, 'kinds': 12, 'created_at': '2026-11-19T10:00:00Z', 'built_by': 'Casey Pearson', 'shares': [50, 50], 'panel_sessions': 0, 'rooms': []},
    {'key': 'jb', 'name': 'Jennifer Beals', 'subject_kind': 'talent', 'format': 'creator', 'size_us': 2776141, 'kinds': 16, 'created_at': '2026-11-18T10:00:00Z', 'built_by': 'Jenna Menking', 'shares': [30, 30, 40], 'panel_sessions': 1,
     'rooms': [{'id': 'other1', 'title': 'Brand Partnerships', 'created_at': '2026-11-01T19:00:00Z', 'updated_at': '2026-11-01T19:50:00Z', 'questions': 5, 'last_q': 'Which brand?', 'lead': {'label': 'A brand with a long LGBTQ+ track record', 'pct': 75.8}, 'read_short': 'About three in four land there.', 'working': False, 'guided': None, 'created_by': 'admin'}]},
]
TWINS = {
    'pv': {'meta': {'name': 'Prime Video', 'key': 'pv', 'size_us': 83791763}, 'archetypes': {'k': 13},
           'panel_sessions': [{'id': 'empty1', 'title': 'Empty room', 'questions': [], 'group': {'roster': []}}, {'id': 'pa1', 'title': 'Predictive Analytics', 'questions': [], 'group': {'roster': []}, 'guided': {'status': 'done'}},
                              {'id': 'room1', 'title': 'What stops one more service', 'questions': [{'q': 'x', 'stage': 'done'}], 'group': {'roster': []}}, {'id': 'old1', 'title': 'An older room', 'questions': [], 'group': {'roster': []}}],
           'pilot_sessions': [], 'runs': [{'id': 'run1', 'kind': 'content', 'label': 'Content drop', 'question': 'How does the next episode land?'}]},
    'jb': {'meta': {'name': 'Jennifer Beals', 'key': 'jb', 'size_us': 2776141}, 'archetypes': {'k': 16}, 'panel_sessions': [{'id': 'other1', 'title': 'Brand Partnerships', 'questions': [], 'group': {'roster': []}}], 'pilot_sessions': [], 'runs': []},
}


class PageBehaviour(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node is not installed')
    def test_landing_and_every_way_into_a_room(self):
        script = _NODE.replace('__TWINS__', json.dumps(TWINS)).replace('__RUNS__', json.dumps(RUNS))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'h.js')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(script)
            r = subprocess.run(['node', p, JS_PATH], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:] + r.stdout[-500:])
        self.assertIn('OK', r.stdout)


class PageSource(unittest.TestCase):
    def test_tab_routing_in_the_source(self):
        # the tab's own click lands; dtwOpen and dtwTwinRoom arrive 'open'; the deep-link branch opens the room through dtwOpen
        self.assertIn("if (tab === 'panel') { if (!via) S.fgLanding = !S.session;", JS)
        self.assertIn("dtwTab(tab || (S.pilot ? piTabFor(S.pilot.mode) : S.session ? 'panel' : S.plan ? 'spend' : run ? ({'launch': 'launch', 'spend': 'spend'}[run.kind] || 'twins') : 'twins'), 'open');", JS)
        self.assertIn("else { S.fgLanding = false; dtwTab('panel', 'open'); }", JS)
        self.assertIn("else if (fromUrl && known(fromUrl.twin_key)) { dtwOpen(fromUrl.twin_key, null, fromUrl.room || null, null, fromUrl.tab || (fromUrl.room ? 'panel' : null)); }", JS)
        self.assertIn("h.innerHTML = S.fgLanding ? fgLandingHtml() : noRoomHtml();", JS)
        self.assertIn("S.fgLanding = true;   // leaving a room lands", JS)
        # a reload after leaving a room lands again: the remembered view carries the landing, the restore honours it
        self.assertIn("land: !!(S.tab === 'panel' && !S.session && S.fgLanding)", JS)
        self.assertIn("if (mem.tab === 'panel' && mem.land && !memSid && !S.session) dtwTab('panel');", JS)
        # the tab buttons in the page call dtwTab with the tab alone (a fresh click)
        html = _read(os.path.join(ROOT, 'templates', 'index.html'))
        self.assertIn('''data-tab="panel" role="tab" onclick="dtwTab('panel')">Focus Group IQ''', html)

    def test_layout_classes_and_phone_stack(self):
        self.assertIn('#digitalTwinsIQView .dtw-fgl { display: grid; grid-template-columns: 360px minmax(0, 1fr); gap: 0 52px;', CSS, 'the twins column at 360px on the left, the list on the right')
        phone = CSS[CSS.index('@media (max-width: 860px) {\n            /* the phone stacks the twins above the list */'):]
        phone = phone[:phone.index('\n        }')]
        self.assertIn('#digitalTwinsIQView .dtw-fgl { grid-template-columns: minmax(0, 1fr); gap: 26px 0; }', phone)
        self.assertIn('#digitalTwinsIQView .dtw-fgl-room { grid-template-columns: 30px minmax(0, 1fr);', phone)
        for cls in ('.dtw-fgl-side', '.dtw-fgl-main', '.dtw-fgl-start', '.dtw-fgl-twin', '.dtw-fgl-room', '.dtw-fgl-room.recent', '.dtw-fgl-room .tag.pa', '.dtw-fgl-empty'):
            self.assertIn(cls, CSS, cls)
        self.assertIn('#digitalTwinsIQView .dtw-fgl .dtw-cap.room { color: var(--dtw-lb-room); }', CSS, 'Orchid does the work; the light theme follows the variable')
        self.assertNotIn('portrait', CSS[CSS.index('.dtw-fgl {'):CSS.index('/* the Rooms drawer and the New room drawer')], 'no portraits on the landing')

    def test_landing_copy_has_no_internal_words(self):
        a = JS.index('            // ---- the Focus Group IQ landing (Jenna 2026-11-23')
        b = JS.index('            // The console: the seats, then the question dock')
        block = JS[a:b]
        strings = ' '.join(re.findall(r"'([^'\\]*(?:\\.[^'\\]*)*)'", block))
        strings = re.sub(r'<[^>]+>', ' ', strings)
        self.assertIsNone(re.search(r'\b(synthetic|synth|modell?ed|model|estimated|research|panel|panelists?|sample|persona|segment|cohort|confidence|survey|prediction|kinds?)\b', strings, re.I), strings)
        self.assertNotIn('\u2014', block)
        self.assertNotIn('\u2013', block)


if __name__ == '__main__':
    unittest.main(verbosity=1)
