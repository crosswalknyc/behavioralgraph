"""The research call inside a read runs under a time budget and a cap on
its web searches (Nov 23 2026 ask audit, speed fix).

That day the knowledge step ran 82 to 308 seconds on eleven of the
seventeen reads, most of it the research call with six web searches,
on subjects the library already held. The read now waits at most
`PM_KNOWLEDGE_RESEARCH_BUDGET_S` (90 s) for its anchors; past that it
goes on without them while the call finishes on its own thread and
saves what it found for the next ask on the subject. The research
step's web search tool is capped at `PM_KNOWLEDGE_RESEARCH_MAX_USES`
(4) searches. Saved anchors still come back at once, and a budget of
0 waits as long as it takes.

Pure tests, no network, no app.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from prometheus import knowledge as kn  # noqa: E402

ASK = {'subject': 'Example Brand', 'text': 'how many people shop at Example Brand', 'family': 'audience', 'product': 'read',
       'window': {'start': '2025-11-23', 'end': '2026-11-23'}}


class SearchCap(unittest.TestCase):
    def test_the_web_search_tool_is_capped_for_research(self):
        tools = [{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 6}, {'type': 'other', 'name': 'x'}]
        out = kn._research_tools(tools)
        self.assertEqual(out[0]['max_uses'], 4)
        self.assertEqual(out[1], {'type': 'other', 'name': 'x'})
        self.assertEqual(tools[0]['max_uses'], 6, 'the caller\'s tool is untouched')
        with mock.patch.dict(os.environ, {kn.ENV_RESEARCH_MAX_USES: '2'}):
            self.assertEqual(kn._research_tools(tools)[0]['max_uses'], 2)
        with mock.patch.dict(os.environ, {kn.ENV_RESEARCH_MAX_USES: 'junk'}):
            self.assertEqual(kn.research_max_uses(), kn.RESEARCH_MAX_USES)

    def test_the_budget_reads_from_env(self):
        self.assertEqual(kn.research_budget_s(), kn.RESEARCH_BUDGET_S)
        with mock.patch.dict(os.environ, {kn.ENV_RESEARCH_BUDGET: '12.5'}):
            self.assertEqual(kn.research_budget_s(), 12.5)
        with mock.patch.dict(os.environ, {kn.ENV_RESEARCH_BUDGET: '-3'}):
            self.assertEqual(kn.research_budget_s(), 0.0)


class Budget(unittest.TestCase):
    def test_a_slow_call_leaves_the_read_to_go_on(self):
        finished = threading.Event()

        def slow(ask, claude_json=None, tools=None, changed_windows=None, store=None, force=False, call=True):
            time.sleep(0.6)
            finished.set()
            return {'anchors': [{'name': 'late'}], 'events': [], 'block': 'late', 'marks': [], 'cached': False, 'key': 'k'}

        stages = {}
        with mock.patch.object(kn, 'research', slow):
            t0 = time.monotonic()
            r = kn.research_bounded(ASK, lambda *a, **k: {}, [], call=True, budget_s=0.15, stages=stages)
            dt = time.monotonic() - t0
        self.assertLess(dt, 0.5)
        self.assertTrue(r.get('timed_out'))
        self.assertEqual(r['anchors'], [])
        self.assertEqual(r['block'], '')
        self.assertEqual(stages.get('research_timeout'), 150)
        self.assertTrue(finished.wait(2.0), 'the call finishes on its own thread')

    def test_a_quick_call_comes_back_whole(self):
        quick = lambda ask, claude_json=None, tools=None, changed_windows=None, store=None, force=False, call=True: (  # noqa: E731
            {'anchors': [{'name': 'a'}], 'events': [], 'block': 'A', 'marks': [], 'cached': True, 'key': 'k'})
        with mock.patch.object(kn, 'research', quick):
            r = kn.research_bounded(ASK, None, [], call=True, budget_s=5, stages={})
        self.assertEqual(r['block'], 'A')
        self.assertFalse(r.get('timed_out'))

    def test_no_budget_and_no_call_run_inline(self):
        calls = []

        def spy(ask, claude_json=None, tools=None, changed_windows=None, store=None, force=False, call=True):
            calls.append((threading.current_thread().name, call))
            return {'anchors': [], 'events': [], 'block': '', 'marks': [], 'cached': False, 'key': 'k', 'skipped': not call}

        with mock.patch.object(kn, 'research', spy):
            kn.research_bounded(ASK, None, [], call=True, budget_s=0)
            kn.research_bounded(ASK, None, [], call=False, budget_s=30)
        self.assertEqual([c[0] for c in calls], [threading.current_thread().name] * 2)
        self.assertEqual([c[1] for c in calls], [True, False])

    def test_read_blocks_calls_the_bounded_step(self):
        src = open(os.path.join(os.path.dirname(HERE), 'prometheus', 'knowledge.py'), encoding='utf-8').read()
        i = src.index('def read_blocks(')
        j = src.index('\ndef ', i + 1)
        body = src[i:j]
        self.assertIn('research_bounded(ask, claude_json, tools, changed_windows=changed or None, call=need_call, stages=stages)', body)
        self.assertNotIn('r = research(ask, claude_json, tools', body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
