#!/usr/bin/env python3
"""The conversation's latest subject outranks the thread's project once
the thread has moved (Jenna 2026-10-30, the mirror case: Reba open, a
read on Gilmore Girls answered, then "how about the men?" stayed on
Reba). The project is still the default for a thread that has not left
it; words still win over both. Hermetic."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import subject_rules as SR  # noqa: E402

PAGE = 'Reba McEntire'
GG_READ = [{'role': 'user', 'text': 'how big is the Gilmore Girls audience?'},
           {'role': 'agent', 'text': 'On Gilmore Girls: 1,109,487 people in the US watched it in the window.'}]


class Bound(unittest.TestCase):
    def setUp(self):
        self._orig = SR.bound_thread_subject
        SR.bound_thread_subject = lambda: PAGE

    def tearDown(self):
        SR.bound_thread_subject = self._orig

    def test_project_is_the_default_until_the_thread_moves(self):
        hist = [{'role': 'user', 'text': 'what are the top QSRs?'},
                {'role': 'agent', 'text': 'On Reba McEntire: Starbucks leads.'}]
        self.assertEqual(SR.thread_subjects(hist, page=PAGE)[0], PAGE)
        self.assertEqual(SR.thread_subjects([], page=PAGE)[0], PAGE)

    def test_latest_read_outranks_the_project(self):
        subs = SR.thread_subjects(GG_READ, page=PAGE)
        self.assertEqual(subs[:2], ['Gilmore Girls', PAGE])
        s = SR.bind('how about the men?', GG_READ, PAGE)
        self.assertEqual(s.name, 'Gilmore Girls')
        self.assertEqual(s.source, 'thread')

    def test_back_on_the_project_after_it_returns(self):
        hist = GG_READ + [{'role': 'user', 'text': 'and the top QSRs?'},
                          {'role': 'agent', 'text': 'On Reba McEntire: Starbucks leads.'}]
        self.assertEqual(SR.thread_subjects(hist, page=PAGE)[0], PAGE)

    def test_landed_read_with_a_cohort_suffix(self):
        hist = [{'role': 'agent', 'text': 'On it: reading The Love Hypothesis now.'},
                {'role': 'agent', 'text': 'On The Love Hypothesis - The Love Hypothesis Prime Video viewers, BookTok exposed: 31.6% came first.'}]
        self.assertEqual(SR.thread_subjects(hist, page=PAGE)[:2], ['The Love Hypothesis', PAGE])

    def test_on_it_is_not_a_subject(self):
        hist = [{'role': 'agent', 'text': 'On it. The read will land right here when it is ready.'}]
        self.assertEqual(SR.thread_subjects(hist, page=PAGE), [PAGE])


class Unbound(unittest.TestCase):
    def test_general_chat_reads_the_landed_read(self):
        self.assertEqual(SR.thread_subjects(GG_READ, page=PAGE), ['Gilmore Girls'])


class NothingCarriedAcrossThreads(unittest.TestCase):
    """2026-11-11 (Jenna, after Armine at Lionsgate): a thread's context is
    its own turns. The 2026-10-30 carry (the widget named the thread an
    automatic switch had left as carried_from, and the service rode its
    last eight turns ahead of the new thread's history) is retired: that
    is how an earlier chat's subject answered a new chat's question. The
    full behavior is held by scripts/test_pm_new_chat_no_bleed.py."""

    def test_service_and_widget_carry_nothing(self):
        svc = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        self.assertNotIn("body.get('carried_from')", svc)
        self.assertNotIn("dict(t, carried=True)", svc)
        # a turn a stale client still marks carried is dropped at the door and never persisted
        self.assertIn("history = [t for t in history if not (isinstance(t, dict) and t.get('carried'))]", svc)
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        self.assertNotIn('carried_from', html)
        self.assertNotIn('_pmCarried', html)

    def test_a_follow_up_binds_inside_its_own_thread(self):
        # the same turns as the thread's own history: the follow-up inherits, as it always did
        s = SR.bind('how about the men?', list(GG_READ), PAGE)
        self.assertEqual(s.name, 'Gilmore Girls')
        # and with no history of its own (a new thread) nothing is inherited
        s0 = SR.bind('how about the men?', [], PAGE)
        self.assertNotEqual(s0.name, 'Gilmore Girls')


if __name__ == '__main__':
    unittest.main(verbosity=1)
