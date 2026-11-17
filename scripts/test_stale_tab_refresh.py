#!/usr/bin/env python3
"""An open dashboard tab comes up to date after an update, never over work.

Jenna, 2026-11-17, looking at the Build a twin audience step (verbatim):
"when one is selected its supposed to have you say what percent of the
audience needs to have that specification. so if you click male you could
say 30%, etc."

The share field beside every pick had shipped on 2026-11-13. Her tab had
been open since before that, and nothing brought it up to date: the block
that refreshed an open tab after an update (2026-07-31) fell out of
templates/index.html on 2026-08-07 and was never missed.

What this holds:
  1. The page carries the refresh block and the version it was rendered at.
  2. The block compares the commit only (boot time differs per instance and
     looped the admin page on 2026-09-17).
  3. It refreshes a tab that has sat in the background, or on screen and
     untouched, and one that is returned to after time away.
  4. It never refreshes over an unsent draft, an ask in flight, a brief or
     a question waiting on an answer in the chat (2026-11-17: "no, build
     fresh" pressed twenty minutes after the brief ran as a question of
     its own, because the page no longer held the brief), a walkthrough
     that was just touched, a playing video or a popped-out room, and
     never twice in a row.
  5. The Build a twin audience step still draws a share field beside each
     pick.

Run:  python3 scripts/test_stale_tab_refresh.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, 'templates', 'index.html')
APP = os.path.join(ROOT, 'app.py')

OPEN = '<script id="cw-stale-tab-refresh">'
JINJA = "{{ (app_build_version or '')|tojson }}"


def _block():
    with open(INDEX, encoding='utf-8') as f:
        src = f.read()
    start = src.index(OPEN) + len(OPEN)
    return src, src[start:src.index('</script>', start)]


HARNESS = r"""
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const MIN = 60000;

function world(opts) {
    opts = opts || {};
    const w = { now: 1700000000000, timers: [], listeners: { doc: {}, win: {} }, store: {}, live: opts.live };
    const src = code.replace('__PAGE__', JSON.stringify(opts.page === undefined ? 'aaaaaaaaaaaa' : opts.page));
    w.doc = {
        hidden: !!opts.hidden, open: '', media: [],
        addEventListener(t, fn) { (w.listeners.doc[t] = w.listeners.doc[t] || []).push(fn); },
        querySelector(sel) { return (w.doc.open && sel.indexOf(w.doc.open) >= 0) ? {} : null; },
        querySelectorAll() { return w.doc.media; }
    };
    w.win = {
        cwHoldRefresh: [],
        location: { reloads: 0, reload() { this.reloads += 1; } },
        addEventListener(t, fn) { (w.listeners.win[t] = w.listeners.win[t] || []).push(fn); }
    };
    if (opts.last) w.store.cw_refresh_at = String(w.now - opts.last);
    const fetchF = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ version: w.live, boot_time: Math.random() }) });
    const dateF = { now: () => w.now };
    const timeoutF = (fn, ms) => { w.timers.push({ at: w.now + ms, fn: fn }); };
    const storeF = { getItem: (k) => (k in w.store ? w.store[k] : null), setItem: (k, v) => { w.store[k] = String(v); } };
    new Function('window', 'document', 'fetch', 'Date', 'setTimeout', 'sessionStorage', 'console', src)(
        w.win, w.doc, fetchF, dateF, timeoutF, storeF, { log() {} });
    w.flush = () => new Promise((r) => setImmediate(r));
    w.advance = async (ms) => {
        const end = w.now + ms;
        for (;;) {
            w.timers.sort((a, b) => a.at - b.at);
            const t = w.timers[0];
            if (!t || t.at > end) break;
            w.timers.shift(); w.now = t.at; t.fn();
            await w.flush(); await w.flush();
        }
        w.now = end;
    };
    w.fire = async (type, ev) => {
        (w.listeners.doc[type] || []).forEach((fn) => fn(ev || {}));
        await w.flush(); await w.flush();
    };
    w.hide = async () => { w.doc.hidden = true; await w.fire('visibilitychange'); };
    w.show = async () => { w.doc.hidden = false; await w.fire('visibilitychange'); };
    w.reloads = () => w.win.location.reloads;
    return w;
}

(async () => {
    const out = {};
    let w;

    // The same version, a new boot time on every poll: nothing to do.
    w = world({ live: 'aaaaaaaaaaaa', hidden: true });
    await w.advance(30 * MIN); out.same_version = w.reloads();

    // An update lands while someone is working on screen.
    w = world({ live: 'bbbbbbbbbbbb' });
    for (let i = 0; i < 30; i++) { await w.advance(MIN); await w.fire('mousemove'); }
    out.working_on_screen = w.reloads();

    // An update lands on a tab in the background.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true });
    await w.advance(3 * MIN); out.background_3m = w.reloads();
    await w.advance(4 * MIN); out.background_7m = w.reloads();

    // On screen, nobody touching it.
    w = world({ live: 'bbbbbbbbbbbb' });
    await w.advance(10 * MIN); out.idle_10m = w.reloads();
    await w.advance(8 * MIN); out.idle_18m = w.reloads();

    // An unsent message is never refreshed away, however long it sits.
    w = world({ live: 'bbbbbbbbbbbb' });
    const draft = { nodeType: 1, tagName: 'TEXTAREA', type: 'textarea', value: 'half a question', isConnected: true };
    await w.fire('keydown'); await w.fire('input', { target: draft, isTrusted: true });
    await w.hide(); await w.advance(600 * MIN); out.draft_10h = w.reloads();
    draft.value = ''; await w.advance(3 * MIN); out.draft_sent = w.reloads();

    // A value a script put in a field is not a draft.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true });
    await w.fire('input', { target: { nodeType: 1, tagName: 'TEXTAREA', value: 'filled by the page', isConnected: true }, isTrusted: false });
    await w.advance(7 * MIN); out.scripted_value = w.reloads();

    // A word left in a search box holds for an hour, not for ever.
    w = world({ live: 'bbbbbbbbbbbb' });
    await w.fire('keydown');
    await w.fire('input', { target: { nodeType: 1, tagName: 'INPUT', type: 'text', value: 'Hazbin', isConnected: true }, isTrusted: true });
    await w.hide(); await w.advance(40 * MIN); out.short_field_40m = w.reloads();
    await w.advance(25 * MIN); out.short_field_65m = w.reloads();

    // An ask in flight.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true });
    globalThis.synthChatInflightController = {};
    await w.advance(30 * MIN); out.ask_in_flight = w.reloads();
    globalThis.synthChatInflightController = null;
    await w.advance(3 * MIN); out.ask_answered = w.reloads();
    delete globalThis.synthChatInflightController;

    // A brief waiting on approve in the chat, touched within the hour.
    w = world({ live: 'bbbbbbbbbbbb' });
    globalThis.synthChatCurrentDraft = { subject: 'Euphoria' };
    await w.fire('pointerdown');
    await w.hide(); await w.advance(30 * MIN); out.brief_30m = w.reloads();
    await w.advance(40 * MIN); out.brief_70m = w.reloads();
    delete globalThis.synthChatCurrentDraft;

    // A question waiting on a yes or a no.
    w = world({ live: 'bbbbbbbbbbbb' });
    globalThis.synthChatClarify = { steps: [{ kind: 'qualifier_match' }] };
    await w.fire('pointerdown');
    await w.hide(); await w.advance(20 * MIN); out.question_20m = w.reloads();
    globalThis.synthChatClarify = null;
    await w.advance(3 * MIN); out.question_answered = w.reloads();
    delete globalThis.synthChatClarify;

    // An empty batch is nothing waiting.
    w = world({ live: 'bbbbbbbbbbbb' });
    globalThis.synthChatBatchDrafts = [];
    await w.fire('pointerdown');
    await w.hide(); await w.advance(7 * MIN); out.empty_batch = w.reloads();
    delete globalThis.synthChatBatchDrafts;

    // A run in progress.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true });
    w.doc.open = '#analysisStatusModal.active';
    await w.advance(120 * MIN); out.run_in_progress = w.reloads();

    // The Build a twin walkthrough, open and just touched, then abandoned.
    w = world({ live: 'bbbbbbbbbbbb' });
    w.doc.open = '#dtwBuildDrawer.open';
    await w.fire('pointerdown');
    await w.hide(); await w.advance(30 * MIN); out.walkthrough_30m = w.reloads();
    await w.advance(40 * MIN); out.walkthrough_70m = w.reloads();

    // A playing video.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true });
    w.doc.media = [{ paused: false, ended: false }];
    await w.advance(60 * MIN); out.video_playing = w.reloads();

    // A room popped out into its own window.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true });
    w.win.cwHoldRefresh.push(() => true);
    await w.advance(120 * MIN); out.popped_room = w.reloads();

    // Back to the tab after time away, with an update that landed meanwhile.
    w = world({ live: 'aaaaaaaaaaaa' });
    await w.advance(MIN); await w.hide(); await w.advance(4 * MIN);
    w.live = 'bbbbbbbbbbbb'; w.now += 6 * MIN;      // the tab was asleep
    await w.show(); await w.advance(1000); out.back_after_away = w.reloads();

    // A glance at another tab is not time away.
    w = world({ live: 'aaaaaaaaaaaa' });
    await w.advance(MIN); await w.fire('pointerdown'); await w.hide(); w.now += 30000;
    w.live = 'bbbbbbbbbbbb';
    await w.show(); await w.advance(1000); out.back_after_glance = w.reloads();

    // Back after time away, and already clicking: wait.
    w = world({ live: 'aaaaaaaaaaaa' });
    await w.advance(MIN); await w.hide(); w.now += 10 * MIN;
    w.doc.hidden = false; (w.listeners.doc.visibilitychange || []).forEach((fn) => fn({}));
    w.now += 500; await w.fire('pointerdown');
    w.live = 'bbbbbbbbbbbb'; await w.advance(2 * MIN); out.back_and_clicking = w.reloads();

    // A machine with no commit to compare never refreshes.
    w = world({ live: 'local-1700000001', page: 'local-1700000000', hidden: true });
    await w.advance(60 * MIN); out.local_version = w.reloads();

    // A page with no version of its own takes the first answer as its own.
    w = world({ live: 'aaaaaaaaaaaa', page: '', hidden: true });
    await w.advance(20 * MIN); out.armed_on_first_poll = w.reloads();
    w.live = 'bbbbbbbbbbbb'; await w.advance(5 * MIN); out.armed_then_updated = w.reloads();

    // Never twice in a row.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true, last: 60000 });
    await w.advance(7 * MIN); out.loop_guard_7m = w.reloads();
    await w.advance(6 * MIN); out.loop_guard_13m = w.reloads();

    // One refresh, then it stops asking.
    w = world({ live: 'bbbbbbbbbbbb', hidden: true });
    await w.advance(120 * MIN); out.only_once = w.reloads();

    process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
"""


class ThePageCarriesIt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src, cls.block = _block()

    def test_the_block_is_on_the_page_once(self):
        self.assertEqual(self.src.count(OPEN), 1)
        self.assertIn('window.cwRefreshState', self.block)

    def test_it_reads_the_version_the_page_was_rendered_at(self):
        self.assertEqual(self.block.count(JINJA), 1)
        with open(APP, encoding='utf-8') as f:
            app = f.read()
        self.assertIn('app_build_version=APP_BUILD_VERSION,', app)
        self.assertEqual(app.count("render_template('index.html'"), 1)

    def test_the_version_lands_as_a_plain_string(self):
        try:
            import jinja2
        except Exception:
            self.skipTest('jinja2 not available')
        line = [x for x in self.block.splitlines() if JINJA in x][0]
        env = jinja2.Environment()
        env.filters.setdefault('tojson', lambda v: json.dumps(v))
        self.assertIn('var PAGE = "9017cca46d81";',
                      env.from_string(line).render(app_build_version='9017cca46d81'))
        self.assertIn('var PAGE = "";', env.from_string(line).render())

    def test_it_compares_the_commit_only(self):
        self.assertNotIn('boot_time', self.block)
        self.assertNotIn('bootTime', self.block)

    def test_it_asks_the_version_route_and_never_caches_the_answer(self):
        self.assertIn("fetch('/api/version', { cache: 'no-store'", self.block)

    def test_a_popped_out_room_registers_its_hold(self):
        self.assertIn('window.cwHoldRefresh || []).push(function() { return piRoomPopped(); })', self.src)

    def test_the_validator_will_not_let_it_go_again(self):
        with open(os.path.join(ROOT, 'scripts', 'validate_index_html.py'), encoding='utf-8') as f:
            v = f.read()
        self.assertIn('id="cw-stale-tab-refresh"', v)
        self.assertIn('then set a share if you want one', v)


class TheShareFieldsAreThere(unittest.TestCase):
    """What she asked for, on the page that is served today."""

    @classmethod
    def setUpClass(cls):
        cls.src, _ = _block()

    def test_every_group_draws_a_share_row_under_its_picks(self):
        self.assertIn('pick any, then set a share if you want one', self.src)
        self.assertIn('bwShareRow(g[0])', self.src)
        self.assertNotIn('<em>pick any</em>', self.src)

    def test_the_field_is_a_percent_beside_the_pick(self):
        m = re.search(r'function bwShareRow\(cat\) \{.*?\n            \}', self.src, re.S)
        self.assertTrue(m)
        row = m.group(0)
        self.assertIn('oninput="dtwBwShare(this)"', row)
        self.assertIn('<i>%</i>', row)
        self.assertIn('in percent', row)


@unittest.skipUnless(shutil.which('node'), 'node not available')
class WhenItRefreshes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, block = _block()
        code = block.replace(JINJA, '__PAGE__')
        cls.tmp = tempfile.mkdtemp(prefix='stale_tab_')
        with open(os.path.join(cls.tmp, 'block.js'), 'w', encoding='utf-8') as f:
            f.write(code)
        with open(os.path.join(cls.tmp, 'harness.js'), 'w', encoding='utf-8') as f:
            f.write(HARNESS)
        p = subprocess.run(['node', os.path.join(cls.tmp, 'harness.js'), os.path.join(cls.tmp, 'block.js')],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise AssertionError(p.stderr[-3000:])
        cls.out = json.loads(p.stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_update_no_refresh(self):
        self.assertEqual(self.out['same_version'], 0)

    def test_never_under_someone_working_on_screen(self):
        self.assertEqual(self.out['working_on_screen'], 0)

    def test_a_background_tab_refreshes_after_five_minutes(self):
        self.assertEqual(self.out['background_3m'], 0)
        self.assertEqual(self.out['background_7m'], 1)

    def test_an_untouched_tab_refreshes_after_fifteen(self):
        self.assertEqual(self.out['idle_10m'], 0)
        self.assertEqual(self.out['idle_18m'], 1)

    def test_an_unsent_message_is_never_lost(self):
        self.assertEqual(self.out['draft_10h'], 0)
        self.assertEqual(self.out['draft_sent'], 1)

    def test_a_value_the_page_filled_in_is_not_a_draft(self):
        self.assertEqual(self.out['scripted_value'], 1)

    def test_a_search_word_holds_for_an_hour_not_for_ever(self):
        self.assertEqual(self.out['short_field_40m'], 0)
        self.assertEqual(self.out['short_field_65m'], 1)

    def test_never_while_an_ask_is_in_flight(self):
        self.assertEqual(self.out['ask_in_flight'], 0)
        self.assertEqual(self.out['ask_answered'], 1)

    def test_a_brief_waiting_in_the_chat_holds_while_it_is_fresh(self):
        self.assertEqual(self.out['brief_30m'], 0)
        self.assertEqual(self.out['brief_70m'], 1)

    def test_a_question_waiting_on_its_answer_holds(self):
        self.assertEqual(self.out['question_20m'], 0)
        self.assertEqual(self.out['question_answered'], 1)

    def test_an_empty_batch_is_nothing_waiting(self):
        self.assertEqual(self.out['empty_batch'], 1)

    def test_never_while_a_run_is_in_progress(self):
        self.assertEqual(self.out['run_in_progress'], 0)

    def test_an_open_walkthrough_holds_while_it_is_fresh(self):
        self.assertEqual(self.out['walkthrough_30m'], 0)
        self.assertEqual(self.out['walkthrough_70m'], 1)

    def test_never_under_a_playing_video_or_a_popped_room(self):
        self.assertEqual(self.out['video_playing'], 0)
        self.assertEqual(self.out['popped_room'], 0)

    def test_coming_back_after_time_away_gets_the_new_page(self):
        self.assertEqual(self.out['back_after_away'], 1)

    def test_a_glance_away_is_not_time_away(self):
        self.assertEqual(self.out['back_after_glance'], 0)

    def test_back_and_already_clicking_waits(self):
        self.assertEqual(self.out['back_and_clicking'], 0)

    def test_no_commit_to_compare_no_refresh(self):
        self.assertEqual(self.out['local_version'], 0)

    def test_a_page_without_a_version_arms_on_the_first_answer(self):
        self.assertEqual(self.out['armed_on_first_poll'], 0)
        self.assertEqual(self.out['armed_then_updated'], 1)

    def test_never_twice_in_a_row(self):
        self.assertEqual(self.out['loop_guard_7m'], 0)
        self.assertEqual(self.out['loop_guard_13m'], 1)

    def test_once_is_enough(self):
        self.assertEqual(self.out['only_once'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=1)
