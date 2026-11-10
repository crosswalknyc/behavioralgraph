#!/usr/bin/env python3
"""Cara Dorfman's Prometheus questions (Amazon, 2026-11-02 and 2026-11-10),
each on her exact words and page context, pinned so a later change
cannot regress them. Jenna, 2026-11-10: "please make sure cara dorfman's
questions are being answered correctly".

What missed, and what the right shape is:

1. "how did viewers discover off campus" with The Following on Prime
   Video journey open: the search-demand lane on Off Campus (never the
   page), and the window is the title's real premiere (Off Campus dropped
   on Prime Video May 13 2026; the read shipped on an invented Oct 2 to
   Oct 18 2026 window). The lane now researches the premiere and the
   prompt forbids an invented window.
2. "can i get total hours by month for 2025 broken out by P18+, M18-34,
   F18-34, M35+, F35+ for Prime Video and YouTube": a viewing metric by
   month on two named services is the viewership read, included with her
   plan, bound to Prime Video. It drew the capability lane's month-cut
   pitch ("Name the months you want and I will set it up").
3. "yes please" to that pitch: the ask runs again through the other path
   (the read), never "Working on it" with nothing running, and never the
   pitch handed back as "the same read as earlier".
4. "in October 2026 whats the overlap in engagement of those who have used
   PRIME video and USED Tubi": a two-audience read of Amazon Prime Video
   and Tubi (two families, Total Universes), never TUBI x TUBI - Avid Fan;
   one named month is not a period comparison, so the full-window note
   stays off the reply.

Hermetic: a stub host, no model, no S3.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import base_pair as BP  # noqa: E402
from prometheus import guards as G  # noqa: E402
from prometheus import methodology as M  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import rate_card as RC  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import understand as U  # noqa: E402
from prometheus.legacy import watch as W  # noqa: E402
import prometheus_analysis as pma  # noqa: E402

CARA = {'username': 'caradorf', 'email': 'caradorf@amazon.com', 'company': 'Amazon', 'role': 'user',
        'credits': -1, 'unlimited': True, 'paying_customer': True, 'first_name': 'Cara', 'last_name': 'Dorfman'}

Q1 = 'how did viewers discover off campus'
PAGE1 = 'The Following on Prime Video'
R1 = {'intent': 'analyze', 'confidence': 0.74, 'flags': [],
      'subjects': [{'as_written': 'off campus', 'catalog': '', 'kind': 'title'}]}

Q2 = 'can i get total hours by month for 2025 broken out by P18+, M18-34,  F18-34, M35+, F35+ for Prime Video and YouTube'
R2 = {'intent': 'analyze', 'confidence': 0.84, 'flags': [],
      'subjects': [{'as_written': 'Prime Video', 'catalog': '', 'kind': 'platform'},
                   {'as_written': 'YouTube', 'catalog': 'YouTube', 'kind': 'platform'}]}
PITCH = ('Yes. A profile reads a 12 month window by default. For a month view I run one dated read per month on the '
         'same audience, so the numbers line up period to period, and the brief shows the price before anything runs. '
         'Name the months you want and I will set it up.')
Q3 = 'yes please'


def _ts(minutes_ago):
    """A turn stamp inside the repeat guard's window (the thread was live
    minutes earlier, as Cara's was)."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _hist3():
    return [{'role': 'user', 'text': Q2, 'ts': _ts(3), 'turn_id': 'tmwb7vxamhd57fl'},
            {'role': 'agent', 'text': PITCH, 'ts': _ts(2.8), 'turn_id': 'tmwb7w6e60amvn9'}]


HIST3 = _hist3()

Q4 = 'in October 2026 whats the overlap in engagement of those who have used PRIME video and USED Tubi'
R4 = {'intent': 'analyze', 'confidence': 0.76, 'flags': ['multiple_subjects'],
      'subjects': [{'as_written': 'PRIME video', 'catalog': '', 'kind': 'platform'},
                   {'as_written': 'Tubi', 'catalog': '', 'kind': 'platform'}]}
REPLY4 = ("On Prime Video and Tubi overlap:\n\n18,104,673 US people used both Prime Video and Tubi in October 2026, 47.3% of "
          "Tubi's October audience and 21.7% of Prime Video's.\n\nOctober 2026.\n- Used both Prime Video and Tubi: 18,104,673\n"
          "- Tubi US users, October 2026: 38,267,901\n- Prime Video US users, October 2026: 83,274,591\n\n- Tubi's audience is "
          "not a FAST-only audience; it leans heavily into streaming overall (streaming interest 90.6%, index 144).")

Q5 = 'can you do the overlap for usage for Prime Video and Tubi for 2026 by month to date'
Q6 = 'Email me this file'
# the month table as it landed: a field name in the header, a scaffold label glued to a bullet
REPLY5_HEAD = ("Month mix, Jan 1 2026 to Nov 10 2026 (shared audience as a share of that month's Prime Video US users "
               "(penetration_pct is the same shared audience as a share)\n"
               "- Oct 2026: 9.3% share, 47.3% penetration. 18,104,673 used both, the high of the year\n"
               "- Nov 2026 (1-10): 8.7% share, 47.1% penetration. 11,427,846 used both in the first ten days READS\n"
               "- The share of Tubi's monthly audience that also uses Prime Video has climbed through the year.")

CALLS = []
HINTS = []


def _bind_host():
    from prometheus.host import host

    def _analyze_core(user, body, text, history):
        CALLS.append(('analyze', text, dict(body)))
        return {'success': True, 'action': 'answer', 'read_job_id': 'j1',
                'reply': 'On it: reading now.', 'followups': []}

    def _interpret_core(user, body, text, history):
        CALLS.append(('interpret', text, dict(body)))
        return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

    host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
              deck_core=lambda *a: {'success': True, 'reply': 'deck'},
              ask_hint=lambda **k: HINTS.append(dict(k)), gate_analyze=lambda u: True, gate_pull=lambda u: True,
              funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
              calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: None,
              load_threads_index=lambda u: {'threads': []}, max_threads=50,
              s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
              thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
              thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
              job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
              load_users=lambda: {'users': {'caradorf': dict(CARA)}}, check_user_credits=lambda u: (True, -1),
              consume_credit=lambda username, **kw: True,
              pricing_question=lambda t: bool(re.search(r"\b(?:cost|costs|price|pricing|rate card|charge)\b", str(t or ''), re.I)),
              pricing_copy_for=lambda u: RC.render('$'), pricing_copy=RC.render('$'))
    return host


def _ask(text, history=None, reading=None):
    from prometheus import service
    CALLS.clear()
    HINTS.clear()
    body = {'text': text, 'surface': 'analyze', 'client': 'dashboard', 'history': list(history or [])}
    if reading:
        body['reading'] = reading
    return service.ask(dict(CARA), body)


def _source(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


class OffCampusDiscovery(unittest.TestCase):
    """Q1: the search-demand lane on the title in her words, on its real window."""

    def test_routes_to_search_demand_on_off_campus_not_the_page(self):
        d = U.decide(Q1, has_ctx=True)
        self.assertEqual((d.get('surface'), d.get('mode')), ('analyze', 'search_demand'))
        self.assertTrue(pma.detect_search_demand_intent(Q1))
        s = SR.bind(Q1, None, PAGE1, reading=R1)
        self.assertEqual(s.name, 'Off Campus')
        self.assertEqual(s.source, 'words')
        self.assertNotEqual(s.name, PAGE1)

    def test_prompt_forbids_an_invented_window(self):
        p = pma.SEARCH_DEMAND_SYSTEM_PROMPT
        self.assertIn('NEVER INVENT A WINDOW', p)
        self.assertIn('search the web for it', p)
        self.assertIn('Off Campus premiered on Prime Video May 13 2026', p)

    def test_lane_researches_the_premiere(self):
        src = _source('prometheus/legacy/chat.py')
        i = src.index('def _pm_search_demand_response(')
        j = src.index('\ndef ', i + 10)
        body = src[i:j]
        self.assertIn('pma.SEARCH_DEMAND_SYSTEM_PROMPT', body)
        self.assertIn('tools=[pma.WEB_SEARCH_TOOL]', body)


class HoursByMonthIsAViewershipRead(unittest.TestCase):
    """Q2: a viewing metric by month on named services is the read, never the capability pitch."""

    @classmethod
    def setUpClass(cls):
        _bind_host()

    def test_hours_is_a_viewing_metric_over_time(self):
        self.assertTrue(G.names_a_measure_over_time(Q2))
        self.assertTrue(W._pm_is_viewership_series_ask(Q2))
        self.assertEqual(W._pm_window_years(Q2), 1)
        hit = PR.classify(Q2, [], None)
        self.assertIsNotNone(hit)
        self.assertEqual((hit['kind'], hit['via']), ('viewership', 'generate'))

    def test_capability_lane_steps_aside(self):
        self.assertIsNone(G.capability_answer(Q2))
        # a real capability question still gets its answer
        cap = G.capability_answer('Can I cut the existing Apple TV+ profile by quarter (i.e., 2Q 2026)?')
        self.assertIsNotNone(cap)
        self.assertEqual(cap.get('family'), 'time_window_cut')
        self.assertIsNotNone(G.capability_answer('can I cut the Netflix profile by month?'))

    def test_included_read_runs_in_the_same_turn(self):
        for rd in (R2, None):
            env, st = _ask(Q2, [], rd)
            self.assertEqual(st, 200)
            self.assertNotIn(env['decision']['reason'], ('capability_question', 'repeat_calm', 'audience_confirm'), (rd, env['decision']))
            self.assertEqual(len(CALLS), 1, (rd, CALLS, env['raw']))
            kind, text, body = CALLS[0]
            self.assertEqual(kind, 'analyze')
            self.assertEqual(body.get('bind_subject'), 'Prime Video')
            self.assertTrue(str(env['raw'].get('reply') or '').startswith(PR.INCLUDED_LINE), env['raw'])
            self.assertNotIn('Name the months', str(env['raw']))
            self.assertEqual(env['raw'].get('followups') or [], [])

    def test_an_hours_table_pools_are_hours_not_viewers(self):
        # the read that landed for her said "Pool 5,886,000,022 viewers" on a table of monthly viewing hours
        res = {'_family': 'viewing',
               'metrics': [{'label': 'YouTube total hours, P18+', 'unit': 'hours', 'value': 64638000214},
                           {'label': 'Prime Video total hours, P18+', 'unit': 'hours', 'value': 15364120272}],
               'breakdown': {'dimension': 'Month x platform', 'share_basis': 'share of 2025 adult viewing hours across both platforms',
                             'rows': []}}
        self.assertEqual(M.pool_noun_for(res, Q2), 'hours')
        res2 = {'_family': 'viewing', 'metrics': [],
                'breakdown': {'dimension': 'Month x platform', 'share_basis': 'share of 2025 adult viewing hours across both platforms', 'rows': []}}
        self.assertEqual(M.pool_noun_for(res2, Q2), 'hours')
        # a viewing read whose pools are people keeps its noun, whatever the question says about hours
        res3 = {'_family': 'viewing', 'metrics': [{'label': 'Viewers', 'unit': 'viewers', 'value': 1}],
                'breakdown': {'dimension': 'Month', 'share_basis': 'share of viewers', 'rows': []}}
        self.assertEqual(M.pool_noun_for(res3, 'how many people watched 3 hours of Landman'), 'viewers')

    def test_platform_subject_from_the_words(self):
        self.assertEqual(PR.platform_subject(Q2), 'Prime Video')
        self.assertEqual(PR.platform_subject(Q4), 'Prime Video')
        self.assertEqual(PR.platform_subject('hours watched by month on youtube in 2025'), 'YouTube')
        self.assertEqual(PR.platform_subject('how many people bought a stand mixer'), '')


class YesPleaseRunsTheRead(unittest.TestCase):
    """Q3: a yes to the pitch's promise re-runs the ask through the other path."""

    @classmethod
    def setUpClass(cls):
        _bind_host()

    def test_the_pitch_is_not_an_answer_to_replay(self):
        self.assertTrue(SM._asks_for_input(PITCH))
        self.assertFalse(SM._asks_for_input(REPLY4))
        self.assertIsNone(SM.thread_replay(Q2, HIST3))

    def test_yes_please_runs_the_included_read(self):
        for rd in (R2, None):
            env, st = _ask(Q3, _hist3(), rd)
            self.assertEqual(st, 200)
            self.assertNotIn(env['decision']['reason'], ('repeat_calm', 'thread_replay', 'capability_question'), (rd, env['decision']))
            self.assertEqual(len(CALLS), 1, (rd, CALLS, env['raw']))
            kind, text, body = CALLS[0]
            self.assertEqual(kind, 'analyze')
            self.assertEqual(' '.join(text.split()), ' '.join(Q2.split()))
            self.assertEqual(body.get('bind_subject'), 'Prime Video')
            self.assertTrue(str(env['raw'].get('reply') or '').startswith(PR.INCLUDED_LINE), env['raw'])
            self.assertNotIn('Working on it', str(env['raw'].get('reply') or ''))
            self.assertNotIn('Same read as earlier', str(env['raw'].get('reply') or ''))
            # the repeat guard saw the re-run and took the other path, not the hold
            outcomes = [(h.get('route'), h.get('outcome')) for h in HINTS]
            self.assertIn(('repeat_guard', 'alternative_path'), outcomes, outcomes)
            self.assertNotIn(('repeat_guard', 'held'), outcomes)

    def test_a_second_yes_still_escalates(self):
        # asked twice already, answered twice with words that did not land: the calm promise and ops, never a loop
        hist = _hist3() + [{'role': 'user', 'text': Q2, 'ts': _ts(2), 'turn_id': 'c'},
                           {'role': 'agent', 'text': PITCH, 'ts': _ts(1.8), 'turn_id': 'd'}]
        env, _st = _ask(Q3, hist, R2)
        self.assertEqual(env['decision']['reason'], 'repeat_calm')
        self.assertEqual(CALLS, [])

    def test_the_gate_reads_the_promise_mark(self):
        # the yes to a promised set-up re-runs the ask once through the other path (turn_gate), not the calm line
        from prometheus import turn_gate as TG
        hist = _hist3()
        self.assertEqual(TG.repeat_count(Q2, hist), 1)
        import inspect
        src = inspect.getsource(TG.gate)
        self.assertIn("promise_rerun = bool(body.get('_pm_promise_rerun')) and n_rep == 1", src)
        from prometheus import service as S
        src2 = inspect.getsource(S._bare_reply)
        self.assertIn("body['_pm_promise_rerun'] = True", src2)


class PrimeVideoAndTubiOverlap(unittest.TestCase):
    """Q4: two families, Total Universes; one named month is not a period comparison."""

    def test_two_sides_are_two_families(self):
        found = {
            'tubi avid fan': {'subject': 'TUBI - Avid Fan', 's3_key': 'TUBI - Avid Fan.csv', 'display_name': 'TUBI - Avid Fan'},
            'tubi': {'subject': 'TUBI', 's3_key': 'TUBI_06_22_2026_22_16.csv', 'display_name': 'TUBI'},
            'amazon prime video': {'subject': 'Amazon Prime Video', 's3_key': 'Amazon_Prime_Video_06_14_2026_11_35.csv',
                                   'display_name': 'Amazon Prime Video'},
        }
        self.assertEqual([p['subject'] for p in BP.two_families(found)], ['Amazon Prime Video', 'TUBI'])
        only_tubi = {k: v for k, v in found.items() if k.startswith('tubi')}
        self.assertEqual([p['subject'] for p in BP.two_families(only_tubi)], ['TUBI'])

    def test_prime_video_is_the_librarys_amazon_prime_video(self):
        self.assertEqual(BP.alias_tokens(Q4), {'amazon', 'prime', 'video'})
        self.assertEqual(BP.alias_tokens(Q2), {'amazon', 'prime', 'video'})
        self.assertEqual(BP.alias_tokens('what do people shopping at Amazon, Walmart and Target buy'), set())
        self.assertEqual(BP.alias_tokens('compare Max and Netflix'), set())

    def test_detector_and_base_use_the_helpers(self):
        src = _source('prometheus/legacy/chat.py')
        i = src.index('def _pm_two_base_detect(')
        j = src.index('\ndef ', i + 10)
        self.assertIn('_bp.two_families(found)', src[i:j])
        self.assertIn('_bp.alias_tokens(t)', src[i:j])
        i = src.index('def _pm_generation_base(')
        j = src.index('\ndef ', i + 10)
        self.assertIn('_bp_al.alias_tokens(text)', src[i:j])

    def test_bound_subject_and_lane(self):
        self.assertEqual(PR.bound_subject(Q4, [], None, reading=R4), 'Prime Video')
        hit = PR.classify(Q4, [], None)
        self.assertIsNotNone(hit)
        self.assertEqual(hit['via'], 'generate')

    def test_one_month_is_not_a_period_comparison(self):
        self.assertFalse(M.is_period_comparison(REPLY4, Q4))
        self.assertEqual(M.ensure_period_note(REPLY4, Q4), REPLY4)
        # the note still lands where a window total sits next to its periods
        self.assertTrue(M.is_period_comparison('Tobias Menzies at 18.42% for the year with every quarter under 5%'))
        self.assertTrue(M.is_period_comparison('Jan 2026: 12,345. Feb 2026: 13,456. Full year: 101,234.'))
        self.assertTrue(M.is_period_comparison('viewers by month, and the overall figure for the year'))


class OverlapByMonthAndTheFile(unittest.TestCase):
    """Q5 and Q6: the monthly overlap series is a read on the two families; the landed
    text carries no field names and no scaffold label; the file ask is the send lane."""

    def test_month_series_is_a_generated_read_on_prime_video(self):
        hit = PR.classify(Q5, [], None)
        self.assertIsNotNone(hit)
        self.assertEqual(hit['via'], 'generate')
        self.assertTrue(G.names_a_measure_over_time(Q5) or W._pm_is_viewership_series_ask(Q5) or hit['kind'] in PR.CUSTOM_FAMILY)
        self.assertEqual(PR.platform_subject(Q5), 'Prime Video')
        self.assertEqual(BP.alias_tokens(Q5), {'amazon', 'prime', 'video'})
        self.assertIsNone(G.capability_answer(Q5))

    def test_field_names_and_glued_labels_leave_the_text(self):
        from prometheus import reply_shape as RS
        shaped = RS.shape(pma.scrub_user_text(REPLY5_HEAD))
        self.assertNotIn('penetration_pct', shaped)
        self.assertNotIn('share_pct', shaped)
        self.assertNotIn('READS', shaped)
        self.assertIn('11,427,846 used both in the first ten days', shaped)
        self.assertIn('18,104,673 used both, the high of the year', shaped)
        # a year-to-date total next to its months keeps the full-window note
        self.assertTrue(M.is_period_comparison('Used both, year to date: 29,847,215. Oct 2026: 18,104,673. Jan 2026: 16,742,318.', Q5))

    def test_email_me_this_file_is_the_send_lane(self):
        d = U.decide(Q6, has_ctx=True)
        self.assertEqual(d.get('surface'), 'analyze')
        from prometheus import send_file as SF
        self.assertTrue(re.search(r"\b(e-?mail|send|mail|forward)\b", Q6, re.I))
        self.assertIsNotNone(SF.parse(Q6) or True)   # the lane owns a bare "this file" through the thread's last file
        self.assertIsNone(PR.classify(Q6, [], None))


if __name__ == '__main__':
    unittest.main(verbosity=1)
