#!/usr/bin/env python3
"""A repeated ask gets the thing the first ask wanted (Jessie, 2026-11-04,
the New Heights Podcast Listeners FAST share; the same ask Armine
Meseldzhyan made the day before). Hermetic: no S3, no model, no Flask.

What went wrong, each pinned here on Jessie's exact messages and the
shape of her thread (system/synth_chat_threads/jessie/4850033403.json):

wrong file   "from the New Heights Podcast Listeners profile, what
             percentage ... Tubi, Roku, Pluto" carried the hint 'Tubi'
             and the catalog tier bound the TUBI file with the named
             profile open on screen. The page written out in full is
             the base; the platform is what the read measures on it.
confirm      "run a new ask from this base (New Heights Podcast
             Listeners, ...)" names its audience in the same breath as
             the pronoun; it is not a bare referent and never confirms.
replay       after the confirm, the ask replayed BY MEANING the 62.4%
             read she had rejected two turns earlier ("Samsung TV Plus
             isn't even in the data"). An order to run it again never
             replays; a reply the reader pushed back on in this thread
             never replays.
no_base      the fresh read reached the worker, whose history carries
             words only and no screen; the referent gate asked "Which
             audience?" into the job record and the read landed as
             "the data it was started from was not available". A
             resumed read runs on the base it was pinned with.
"""
from __future__ import annotations

import os
import re
import sys
import time
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import insights_ledger as IL  # noqa: E402
from prometheus import audience_referent as AR  # noqa: E402
from prometheus import named_subject as NS  # noqa: E402
from prometheus import read_recovery as RR  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus import user_signal as US  # noqa: E402

SUBJECT = 'New Heights Podcast Listeners'
KEY = 'New_Heights_Podcast_Listeners_10_21_2026_21_17.csv'

# Jessie's messages, verbatim (2026-11-04, thread 4850033403)
Q1 = ("from the New Heights Podcast Listeners profile, what percentage of Listeners engages with at least one FAST "
      "platform, whether that\u2019s Tubi, Roku, Pluto, or Samsung? I want a deduplicated percentage that has a digital "
      "touchpoint to one of these FAST platforms.")
Q2 = "this doesnt make sense with a 6,068,313 total US audience"
Q3 = ("i want the base to be the projected US audience (6,068,313) of New Heights Podcast Listeners. I want the "
      "deduplicated percentage of this base who hit at least one FAST platform")
Q4 = ("your analysis of deduplicated FAST viewers from this New Heights Podcasts listeners base doesn't make sense, "
      "as Samsung TV Plus isn't even in the data for VIRTUAL MVPD FAST CATEGORY")
Q5 = ("You're drawing conclusions based on what already exists in the profile. Instead, run a new ask from this base "
      "(New Heights Podcast Listeners, 2025-10-21 through 2026-10-21, projected US audience 6,068,313). Calculate the "
      "deduplicated percentage that engages with at least one FAST platform.")
Q6 = "New Heights Podcast Listeners, 2025-10-21 through 2026-10-21, projected US audience 6,068,313)"
Q7 = ("No. Go back in an do a fresh ask from a new look/pull from this same audience (New Heights Podcast Listeners, "
      "2025-10-21 through 2026-10-21, projected US audience 6,068,313). Calculate the deduplicated percentage that "
      "engages with at least one FAST platform.")
# Armine's, the day before (thread 2f1510dd03)
A1 = "From the new heights podcast listeners, how much percent are already using FAST"
A2 = "from the pool of 6,068,313. how many of them use FAST in general"

REPLY_62 = ("On the 6,068,313 projected US audience of New Heights Podcast Listeners, 62.4% hit at least one of the "
            "four FAST platforms, which is 3,786,627 people counted once.\n\nProfile base: 6,068,313, On two or more "
            "FAST platforms: 2,233,139, On exactly one FAST platform: 1,553,488.\n\nFAST platform mix, Nov 4 2025 to "
            "Nov 4 2026 (share of the base's FAST touchpoint signals across the four platforms)\n- Samsung TV Plus: "
            "41.7% share, 47.6% penetration. the base's lead FAST path, smart-TV native")
REPLY_48 = ("On New Heights Podcast Listeners:\n\nSamsung TV Plus is not a Virtual MVPD/FAST row for this base, and on "
            "the three FAST platforms that are, 48.6471% of the 6,068,313 projected US audience hit at least one, "
            "which is 2,952,057 people counted once.")
CONFIRM = "I read 'this base' as New Heights Podcast Listeners from earlier in this chat. Go, or name another?"


def jessie_thread():
    """The thread as the widget held it before the 21:36 ask (roles and
    words; the upload note sits between the 62.4% read and her pushback)."""
    return [
        {'role': 'user', 'text': Q1},
        {'role': 'agent', 'text': 'On it: reading Tubi, New Heights Podcast Listeners, THE ROKU Channel and more now.'},
        {'role': 'agent', 'text': 'On New Heights Podcast Listeners:\n\nWindow: Nov 4 2025 to Nov 4 2026.\n- Deduplicated FAST people: 7,876,363',
         'meta': {'read_job_id': '8f25f580f1a5'}},
        {'role': 'user', 'text': Q2},
        {'role': 'agent', 'text': 'Both figures are right; they sit on different bases, which is why one looks bigger than the other.'},
        {'role': 'user', 'text': Q3},
        {'role': 'agent', 'text': 'On it: reading FAST platform (Tubi, Roku, Pluto, Samsung) and New Heights Podcast Listeners now.'},
        {'role': 'agent', 'text': REPLY_62, 'meta': {'read_job_id': '8c2848c43a48'}},
        {'role': 'user', 'text': 'Uploaded New_Heights_Podcast_Listeners_VIRTUAL_MVPD_FAST.csv'},
        {'role': 'agent', 'text': 'Got it: New_Heights_Podcast_Listeners_VIRTUAL_MVPD_FAST.csv (could not be read).'},
        {'role': 'user', 'text': Q4},
        {'role': 'agent', 'text': 'On it: reading FAST viewers and New Heights Podcasts listeners and Samsung Tv now.'},
        {'role': 'agent', 'text': REPLY_48, 'meta': {'read_job_id': 'd3269138a59c'}},
    ]


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", str(s or '').lower())).strip()


def _source(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


STOP = {'the', 'of', 'and', 'a', 'an', 'for', 'in', 'on', 'to', 'with', 'this', 'that', 'from', 'profile',
        'viewers', 'listeners', 'audience', 'total', 'universe'}


def _entry(question, reply, ws='2025-11-04', we='2026-11-04'):
    return IL.make_entry(subject=SUBJECT, metric_family='audience', question=question, route='reasoned_metrics',
                         metrics=[{'name': 'profile_base', 'label': 'Profile base', 'value': 6068313, 'unit': 'people',
                                   'definition': 'projected US audience of the open profile'}],
                         window_start=ws, window_end=we, window_label='Nov 4 2025 to Nov 4 2026', reply=reply,
                         base_profile_key='TUBI_06_22_2026_22_16.csv', universe=SUBJECT,
                         breakdown={'dimension': 'FAST platform', 'share_basis': 'share of the base',
                                    'rows': [{'label': 'Samsung TV Plus', 'share_pct': 41.7, 'penetration_pct': 47.6},
                                             {'label': 'Tubi', 'share_pct': 34.0, 'penetration_pct': 38.7}]})


class SeededLedger:
    """insights_ledger.consult against an in-memory index (no S3)."""

    def __init__(self, entries):
        self.doc = {'subjects': {IL.subject_key(SUBJECT): {'subject': SUBJECT, 'entries': list(entries)}}}

    def __enter__(self):
        self._saved = dict(IL._index_cache)
        IL._index_cache['doc'] = self.doc
        IL._index_cache['ts'] = time.time()
        return self

    def __exit__(self, *a):
        IL._index_cache.update(self._saved)


class WrongFile(unittest.TestCase):
    """The page written out in full is the base, not the platform it names."""

    def test_the_named_page_is_named_outright(self):
        for q in (Q1, Q3, Q5, Q7):
            self.assertTrue(NS.page_named_outright(q, SUBJECT, _norm, STOP), q[:60])
        # a platform list, an audience noun alone, a different subject: not the page
        self.assertFalse(NS.page_named_outright('how many Netflix subscribers are podcast listeners', SUBJECT, _norm, STOP))
        self.assertFalse(NS.page_named_outright('what is the Tubi audience size', SUBJECT, _norm, STOP))
        self.assertFalse(NS.page_named_outright(A2, SUBJECT, _norm, STOP))
        self.assertTrue(NS.page_named_outright('what share of Yellowstone viewers use Tubi', 'Yellowstone', _norm, STOP))
        self.assertTrue(NS.page_named_outright(A1, SUBJECT, _norm, STOP))

    def test_generation_base_keeps_the_named_page_over_the_hint(self):
        """The rule as wired in chat._pm_generation_base, read off the source:
        the subject-conflict guard yields to the page named outright, and
        the page returns before the catalog tiers see the hint."""
        src = _source('prometheus', 'legacy', 'chat.py')
        i = src.index('def _pm_generation_base(')
        body = src[i:src.index('\ndef ', i + 10)]
        self.assertIn("page_named_outright(text, page_base.get('subject')", body)
        guard = body.index("ask names its own subject")
        outright = body.index('_page_outright = False')
        self.assertLess(outright, guard, 'the outright check runs before the conflict guard decides')
        self.assertIn("and not _page_outright):", body)
        ret = body.index("the page is the base")
        tiers = body.index("[pm-base] named:")
        self.assertLess(ret, tiers, 'the page returns before the hint-named catalog tier')


class Confirm(unittest.TestCase):
    """A referent with its name in the same breath is not bare."""

    def test_inline_name_reads_the_parenthetical(self):
        self.assertEqual(AR.inline_name(Q5), SUBJECT)
        self.assertEqual(AR.inline_name(Q7), SUBJECT)
        self.assertEqual(AR.inline_name('run it again on this audience: Wicked Tuna Viewers, same window'), 'Wicked Tuna Viewers')
        self.assertEqual(AR.inline_name(Q3), '')
        self.assertEqual(AR.inline_name('cut this audience (18-34) by gender'), '')
        self.assertEqual(AR.bare_referent(Q5), '')
        self.assertEqual(AR.bare_referent(Q3), 'this base')

    def test_named_inline_never_confirms_on_the_web(self):
        ctx = {'primary': {'name': SUBJECT, 's3_key': KEY}}
        self.assertIsNone(AR.gate(Q5, '', ctx=ctx, thread=None, history=jessie_thread()))
        self.assertIsNone(AR.gate(Q7, '', ctx=ctx, thread=None, history=jessie_thread()))
        self.assertEqual(AR.job_audience(Q5, ''), SUBJECT)

    def test_mark_valdez_still_confirms(self):
        mark = "can you provide a list of 100 channels that are the best fit on youtube for an ad campaign related to this audience?"
        out = AR.gate(mark, 'Ad Campaign Related', ctx={'primary': {'name': 'TikTok', 's3_key': 't.csv'}}, thread=None, history=[])
        self.assertEqual((out or {}).get('outcome'), 'audience_confirm_screen')
        out = AR.gate(mark, 'Ad Campaign Related', ctx=None, thread=None, history=[])
        self.assertEqual((out or {}).get('reply'), AR.NEITHER_LINE)


class NoBase(unittest.TestCase):
    """A resumed read runs on the base it was pinned with."""

    def worker_history(self):
        return RR._trim_history(jessie_thread() + [
            {'role': 'user', 'text': Q5}, {'role': 'agent', 'text': CONFIRM},
            {'role': 'user', 'text': Q6}, {'role': 'agent', 'text': REPLY_62},
            {'role': 'user', 'text': Q7}])

    def test_resume_record_pins_the_base_and_the_gate_honors_it(self):
        rr = RR.resume_record(text=Q7, history=self.worker_history(),
                              base={'subject': SUBJECT, 's3_key': KEY, 'source': 'page'})
        base = RR.resumed_base(rr)
        self.assertEqual(base['subject'], SUBJECT)
        self.assertNotIn('meta', rr['history'][0])
        # the worker: no screen, no thread binding, words-only history
        self.assertIsNone(AR.gate(Q7, rr['bind_subject'], ctx=RR.resumed_ctx(rr), thread=None,
                                  history=rr['history'], pinned=base['subject']))
        # the one Jessie hit: a bare referent, words-only history, nothing pinned used to ask "Which audience?"
        bare = "run a fresh pull for this same audience and calculate the deduplicated FAST percentage"
        self.assertIsNone(AR.gate(bare, SUBJECT, ctx=None, thread=None, history=rr['history'], pinned=SUBJECT))
        out = AR.gate(bare, SUBJECT, ctx=None, thread=None, history=[{'role': 'user', 'text': 'hello'}])
        self.assertEqual((out or {}).get('reply'), AR.NEITHER_LINE, 'nothing pinned, nothing to resolve: still asks')

    def test_words_only_history_still_knows_the_audience(self):
        hist = [{'role': 'user', 'text': Q4}, {'role': 'agent', 'text': REPLY_48}]
        self.assertEqual(AR.thread_audience(None, hist), SUBJECT)
        self.assertEqual(AR.resolve('run a fresh pull for this same audience', ctx=None, thread=None, history=hist),
                         (SUBJECT, 'thread'))

    def test_chat_passes_the_pin(self):
        src = _source('prometheus', 'legacy', 'chat.py')
        i = src.index('def _pm_generate_metrics_response(')
        body = src[i:i + 20000]
        self.assertIn("pinned=_resume_pin", body)
        self.assertLess(body.index('_resume_pin ='), body.index('_audience_referent.gate('))


class Replay(unittest.TestCase):
    """An order to run it again never replays; a rejected reply never replays."""

    def test_fresh_run_orders_are_not_replay_eligible(self):
        for q in (Q5, Q7, 'rerun the FAST read', 'do it again from scratch', 'give me a fresh count on this base'):
            self.assertTrue(IL.fresh_run_requested(q), q)
            self.assertFalse(IL.replay_eligible(q), q)
        for q in (Q1, Q3, A1, 'what share of New Heights listeners use Tubi'):
            self.assertFalse(IL.fresh_run_requested(q), q)
            self.assertTrue(IL.replay_eligible(q), q)

    def test_consult_runs_fresh_on_a_new_ask_and_still_replays_a_plain_repeat(self):
        e62 = _entry(Q3, REPLY_62)
        with SeededLedger([e62]):
            fresh = IL.consult(subject=SUBJECT, question=Q5)
            self.assertIsNone(fresh.get('exact'), 'an order to run a new ask never replays by meaning')
            self.assertTrue(fresh.get('entries'), 'the stored read still rides the constraint block')
            again = IL.consult(subject=SUBJECT, question=Q3)
            self.assertEqual((again.get('exact') or {}).get('reply'), REPLY_62, 'the same question again still replays')

    def test_pushback_reads_jessies_lines(self):
        for q in (Q2, Q4, Q5, Q7, 'no', "that's not what I asked", 'wrong base'):
            self.assertTrue(US.pushes_back(q), q)
        for q in (Q1, Q3, Q6, A1, A2, 'Uploaded New_Heights_Podcast_Listeners_VIRTUAL_MVPD_FAST.csv', 'thanks'):
            self.assertFalse(US.pushes_back(q), q)

    def test_rejected_reply_sees_the_thread_shape(self):
        hist = jessie_thread()
        # the 62.4% read: an upload note, then "isn't even in the data"
        self.assertTrue(US.rejected_reply(REPLY_62, hist))
        # the 48.6% read: the last agent turn, nothing said after it yet
        self.assertFalse(US.rejected_reply(REPLY_48, hist))
        # once she says "run a new ask", the 48.6% read is rejected too
        self.assertTrue(US.rejected_reply(REPLY_48, hist + [{'role': 'user', 'text': Q5}]))
        # a replay lead on the stored words changes nothing
        self.assertTrue(US.rejected_reply('Same read as earlier (Nov 4 at 19:33 UTC):\n\n' + REPLY_62, hist))
        # a reply never shown in this thread is not rejected
        self.assertFalse(US.rejected_reply('On Wicked Tuna Viewers:\n\n' + 'x' * 80, hist))

    def test_thread_replay_skips_a_pushed_back_answer(self):
        hist = [{'role': 'user', 'text': Q3},
                {'role': 'agent', 'text': REPLY_62, 'ts': '2026-11-04T19:33:19Z', 'meta': {'read_job_id': '8c2848c43a48'}},
                {'role': 'user', 'text': Q4},
                {'role': 'agent', 'text': REPLY_48, 'ts': '2026-11-04T19:51:45Z', 'meta': {'read_job_id': 'd3269138a59c'}}]
        self.assertIsNone(SM.thread_replay(Q3, hist), 'the answer to Q3 was pushed back on; never handed back')
        ok = [{'role': 'user', 'text': Q3},
              {'role': 'agent', 'text': REPLY_62, 'ts': '2026-11-04T19:33:19Z', 'meta': {'read_job_id': '8c2848c43a48'}},
              {'role': 'user', 'text': 'thanks, and the avid cut?'}]
        self.assertIsNotNone(SM.thread_replay(Q3, ok), 'an accepted answer still replays for the same question')

    def test_chat_and_service_consult_the_guard(self):
        src = _source('prometheus', 'legacy', 'chat.py')
        self.assertIn('def _pm_replay_rejected_in_thread(entry, history):', src)
        self.assertGreaterEqual(src.count('_pm_replay_rejected_in_thread('), 4, 'defined once, consulted at every ledger replay')
        svc = _source('prometheus', 'service.py')
        self.assertIn("_us_rr.rejected_reply(_rr_hit.get('reply'), history)", svc)


if __name__ == '__main__':
    unittest.main(verbosity=2)
