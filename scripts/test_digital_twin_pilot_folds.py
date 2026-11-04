#!/usr/bin/env python3
"""Pilot Testing: the folds under a test stay as the reader left them across a repaint (Jenna, 2026-11-04).

"these dont stay open when I click one if something else is happening they should": the test is repainted whole
whenever its session refreshes (a question lands, a forecast attaches, the norm changes, the room pops out or back),
and the folds (Scorecard in full, Diagnostics, Moderator guide, Recut, comps, talent and the brief, the room's Earlier
questions) used to come back closed. They now render through piFold, which applies the open state kept on S by test
and fold, written on toggle; the page and every scroller inside the test go back to where they were.

Hermetic: reads templates/index.html only.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def page():
    with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
        return fh.read()


def block(src, start, end):
    a = src.find(start)
    assert a >= 0, start
    b = src.find(end, a)
    assert b > a, end
    return src[a:b]


class Folds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = page()
        cls.pilot = block(cls.src, 'function piQClock(q)', 'function renderPilotExtras(s)')

    def test_every_fold_under_the_test_renders_through_piFold(self):
        player = block(self.src, 'function renderPilotPlayer(s) {', 'function renderPilotConsole() {')
        for fid, label in (('scorecard', 'Scorecard in full'), ('diagnostics', 'Diagnostics'), ('guide', 'Moderator guide'), ('extras', 'Recut, comps, talent and the brief')):
            self.assertIn("piFold(s, '%s', '%s'," % (fid, label), player)
        self.assertNotIn('ontoggle="drawCompareChart()"', self.src)

    def test_earlier_questions_rides_the_same_store_keyed_by_test(self):
        thread = block(self.src, 'function renderPilotThread(s) {', 'function piRecordCard(doc)')
        self.assertIn('data-fold="earlier"', thread)
        self.assertIn("piFoldOpen(s, 'earlier')", thread)
        self.assertIn('ontoggle="dtwPilotFoldToggle(this)"', thread)
        self.assertIn("data-sid=\"' + esc((s || {}).id || '') + '\"", thread)
        self.assertNotIn('piEarlierOpen', self.src)
        self.assertNotIn('dtwPilotEarlierToggle', self.src)

    def test_no_pilot_fold_is_emitted_without_the_toggle_handler(self):
        # a fold in the test that does not write its state would come back closed on the next repaint
        for m in re.finditer(r"<details class=\"dtw-pi-more\"[^>]*>", self.pilot):
            tag = m.group(0)
            self.assertIn('ontoggle="dtwPilotFoldToggle(this)"', tag, tag)
            self.assertIn('data-fold=', tag, tag)

    def test_piFold_applies_the_kept_state_and_the_toggle_writes_it(self):
        helpers = block(self.src, 'function piFoldKey(sid, id)', 'function renderPilotPlayer(s) {')
        self.assertIn("(piFoldOpen(s, id) ? ' open' : '')", helpers)
        self.assertIn("S.piFolds[piFoldKey(d.getAttribute('data-sid'), d.getAttribute('data-fold'))] = !!d.open;", helpers)
        self.assertIn("if (!body) return '';", helpers)
        self.assertIn('if (d.open) drawCompareChart();', helpers)
        self.assertNotIn('\u2014', helpers)

    def test_the_repaint_keeps_the_page_and_every_scroller_where_they_were(self):
        console = block(self.src, 'function renderPilotConsole() {', 'function pollPilotPrepare() {')
        keep = console.find("var keep = S.piPainted === S.pilot.id ? piScrollKeep(h) : null;")
        paint = console.find('h.innerHTML = renderPilotPlayer(S.pilot);')
        restore = console.find('piScrollRestore(h, keep);')
        reveal = console.find('revealPilot(playheadNow());')
        thread = console.find('piThreadRestore(h, keep);')
        self.assertTrue(0 <= keep < paint < restore < reveal < thread, (keep, paint, restore, reveal, thread))
        self.assertIn('S.piPainted = S.pilot.id;', console)
        self.assertIn("if (!S.pilot) { S.piPainted = null;", console)

    def test_the_popped_out_room_keeps_its_scrollers_too(self):
        pop = block(self.src, 'function piPaintPopout(html) {', 'window.dtwRoomPopOut = function()')
        keep = pop.find('var keep = piScrollKeep(box);')
        paint = pop.find('box.innerHTML = html;')
        restore = pop.find('piScrollRestore(box, keep);')
        self.assertTrue(0 <= keep < paint < restore, (keep, paint, restore))
        self.assertIn('if (keep.thread != null) piThreadRestore(box, keep); else piThreadFollow(seats);', pop)

    def test_the_chat_follows_only_from_its_end(self):
        helpers = block(self.src, 'function piScrollKeep(root)', 'function renderPilotPlayer(s) {')
        self.assertIn("(th.scrollHeight - th.scrollTop - th.clientHeight) >= 28) m.thread = th.scrollTop;", helpers)
        self.assertIn("if (!root || !m || m.thread == null) return;", helpers)

    def test_no_internal_words_in_the_new_copy(self):
        helpers = block(self.src, '// ---- what the reader opened, and where they scrolled, survive a repaint', 'function renderPilotPlayer(s) {')
        low = helpers.lower()
        for tok in ('synth', 'modeled', 'claude', 'hostmap'):
            self.assertNotIn(tok, low)


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=1).result.wasSuccessful() is False)
