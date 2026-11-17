#!/usr/bin/env python3
"""A follow-up to a read is that read again, never a file to build
(Grace Yin, Amazon, 2026-11-17 18:35 UTC).

Under her eleven-book read on all US people for calendar year 2022 the
reply offered a chip: "Run the same eleven-title comparison for 2023,
the year Butcher & Blackbird enters". She pressed it. Three things went
wrong in a row:

  * "Run the same ..." was taken for an order with a pointing word in
    its subject slot (prometheus.orders), though "the same" was the
    article of the comparison she already held;
  * the pointing word was resolved to the read's label with "viewers on
    YouTube" behind it (prometheus.order_referent). Her brief says "all
    US people as the base, not YouTube viewers";
  * the order named no product, so it was written up as "Please queue
    one Profile IQ: ..." and, on a seat with builds included, started
    at once. It failed and she was told "We can't see The Love
    Hypothesis on YouTube at this time."

The rule: a re-run of the thread's last read on another window is that
read's own question on the new window (prometheus.continuation,
prometheus.read_brief.rerun). A platform the reader ruled out is never
the universe. An order that names no product under a read is shown for
the reader's yes, never started on a guess.

Hermetic: a stub host, no model, no S3, no ledger. Her exact words.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import continuation as C  # noqa: E402
from prometheus import order_referent as OR  # noqa: E402
from prometheus import orders as O  # noqa: E402
from prometheus import read_brief as RB  # noqa: E402

CHIP = 'Run the same eleven-title comparison for 2023, the year Butcher & Blackbird enters'
BRIEF_2022 = (
    'Compare these 11 books for US people, calendar year 2022 (Jan 1 \u2013 Dec 31, 2022): The Love Hypothesis (Ali Hazelwood); '
    'The Map That Leads To You (J.P. Monninger); Every Summer After (Carley Fortune); The Deal (Elle Kennedy); We Were Liars '
    '(E. Lockhart); My Lady Jane (Cynthia Hand, Brodi Ashton, Jodi Meadows); The Idea of You (Robinne Lee); Culpa M\xeda '
    "(Mercedes Ron, book 1); Perfect Addiction (Claudia Tan); Butcher & Blackbird (Brynne Weaver); The Bad Boy's Girl "
    '(Blair Holden, book 1).\n\u258e\n\u258e Use the book member only (no adaptation, no author-only activity), and all US people as '
    'the base, not YouTube viewers. For each book, give: total US people engaged and the count in the data; the split into '
    "Prime members who watched Prime Video, Prime members who didn't, and non-Prime members; the split into 1 touch, 2\u20133 "
    'touches and 4+ touches; and people by source (book orders, Goodreads, Wattpad, other fan fiction, TikTok, YouTube, fan '
    'sites, search), with a person counted once inside each source.')
READ_SUBJECT = 'Eleven romance and YA book titles, US people, calendar year 2022'
ELEVEN_SUBJECT = 'The Love Hypothesis, The Map That Leads To You, Every Summer After and 8 more titles'
HAZBIN = {'primary': {'name': 'Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers',
                      's3_key': 'Hazbin_Hotel_Season_1_Amazon_Prime_New_Sign_Ups_Viewers_11_05_2026_19_12.csv'}}
TODAY = dt.date(2026, 11, 17)


def thread(question=BRIEF_2022, subject=READ_SUBJECT, set_aside=True):
    """Her thread as it stood when she pressed the chip: the brief, the
    promise, the landed read with its chips (set aside 73 seconds
    before, which is not what sent it wrong)."""
    meta = {'kind': 'read', 'read_job_id': 'd24e0161f86b', 'subject': subject,
            'options': [{'label': CHIP, 'send': CHIP},
                        {'label': 'Age and gender shape of the 2022 people behind each of the eleven titles',
                         'send': 'Age and gender shape of the 2022 people behind each of the eleven titles'}]}
    if set_aside:
        meta.update(corrected_at='2026-11-17T18:34:35Z', corrected_by='jenna')
    return [
        {'role': 'user', 'text': question},
        {'role': 'agent', 'text': 'Included with your plan. On it: reading the eleven titles now. The read will land right here.',
         'meta': {'read_job_id': 'd24e0161f86b'}},
        {'role': 'agent', 'text': ('On ' + subject + ': The Love Hypothesis is the largest of the eleven, with The Idea of You and '
                                   'We Were Liars behind it. Each person is counted once inside the window.\n\nHow these are '
                                   'counted:\n- Window: Jan 1 2022 to Dec 31 2022.\n- Base: US people.'), 'meta': meta},
    ]


class NotAnOrder(unittest.TestCase):
    def test_her_chip_is_not_an_order(self):
        self.assertFalse(O.is_order(CHIP))
        self.assertFalse(O.is_profile_order(CHIP))
        self.assertEqual(O.referent(CHIP), '')

    def test_the_same_read_on_another_window_is_not_an_order(self):
        for q in ('Run the same for 2023', 'run it again for Q4 2025', 'pull that for January 2026',
                  'Run the same comparison for calendar 2024', 'run the same analysis', 'pull that breakdown again',
                  'run this analysis for Q4 2025', 'Run the same comparison for 2026 year to date'):
            self.assertFalse(O.is_order(q), q)

    def test_an_order_is_still_an_order(self):
        for q in ('Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 - Oct 31 2026.',
                  'I want to add them as a profile', 'run the same profile for 2023', 'ok queue it', 'run it',
                  'build it on Mountain Dew', 'run it on Mountain Dew for Q4 2025', 'build it for 2023',
                  'Can you look into My Life With the Walter Boys viewers? I want to add them as a profile',
                  'build me a Nike profile', 'run a profile on Paw Patrol please'):
            self.assertTrue(O.is_order(q), q)


class TheReadAgain(unittest.TestCase):
    def test_her_chip_is_the_read_on_the_new_window(self):
        c = C.classify(CHIP, thread())
        self.assertEqual(c['kind'], 'rerun_window')
        self.assertEqual(c['window'], ('2023-01-01', '2023-12-31'))
        self.assertEqual((c['deliverable'] or {}).get('kind'), 'read')
        self.assertEqual(' '.join(str((c['deliverable'] or {}).get('question')).split()), ' '.join(BRIEF_2022.split()))

    def test_other_ways_to_say_it(self):
        for q, win in (('Run the same for 2023', ('2023-01-01', '2023-12-31')),
                       ('same comparison for 2024', ('2024-01-01', '2024-12-31')),
                       ('run it again for Q4 2025', ('2025-10-01', '2025-12-31')),
                       ('re-run for 2021', ('2021-01-01', '2021-12-31'))):
            c = C.classify(q, thread())
            self.assertEqual((c['kind'], c['window']), ('rerun_window', win), q)

    def test_a_remark_is_not_a_rerun(self):
        for q in ('the same table shows up twice', 'show me the same numbers by age', 'That comparison is wrong',
                  'Age and gender shape of the 2022 people behind each of the eleven titles'):
            self.assertNotEqual(C.classify(q, thread())['kind'], 'rerun_window', q)

    def test_a_year_on_its_own_is_a_window_only_for_a_rerun(self):
        self.assertEqual(C.year_window('for 2023', today=TODAY), ('2023-01-01', '2023-12-31'))
        self.assertEqual(C.year_window('calendar year 2024', today=TODAY), ('2024-01-01', '2024-12-31'))
        self.assertEqual(C.year_window('for 2026 year to date', today=TODAY), ('2026-01-01', '2026-11-17'))
        self.assertIsNone(C.year_window('for 2027', today=TODAY))                 # a year that has not started
        self.assertIsNone(C.year_window('for 2023 and 2024', today=TODAY))        # two years are not one window
        self.assertIsNone(C.year_window('the eleven titles', today=TODAY))
        self.assertIsNone(C.rerun_window('the film released in 2022'))            # the general reader is unchanged

    def test_the_question_carries_one_window(self):
        q = C.rerun_question(BRIEF_2022, ('2023-01-01', '2023-12-31'))
        self.assertIn('calendar year 2023 (Jan 1 \u2013 Dec 31, 2023)', q)
        self.assertNotIn('2022', q)
        self.assertNotIn('Window:', q)
        self.assertIn('not YouTube viewers', q)                                    # her base, in her words
        self.assertEqual(len(RB.titles(q)), 11)
        ytd = C.rerun_question(BRIEF_2022, ('2026-01-01', '2026-11-17'))
        self.assertIn('calendar year 2026 (Jan 1 \u2013 Nov 17, 2026)', ytd)
        self.assertEqual(C.rewindowed(READ_SUBJECT, ('2023-01-01', '2023-12-31')),
                         'Eleven romance and YA book titles, US people, calendar year 2023')
        # a question that states no year of its own takes the window as a closing line, as before
        self.assertEqual(C.rerun_question('How many people watched The Office?', ('2023-01-01', '2023-12-31')),
                         'How many people watched The Office? Window: January 1 to December 31, 2023.')
        self.assertEqual(C.rerun_question('How many people watched The Office?', ('2026-09-16', '2026-09-30')),
                         'How many people watched The Office? Window: September 16 to September 30, 2026.')

    def test_the_brief_behind_the_read_is_run_on_the_new_window(self):
        out = RB.rerun(CHIP, thread())
        self.assertTrue(RB.is_brief(out))
        self.assertIn('calendar year 2023', out)
        self.assertNotIn('2022', out)
        self.assertEqual(RB.read(out)['subject'], ELEVEN_SUBJECT)
        self.assertEqual(RB.rerun(CHIP, []), '')                                   # no read in the thread
        self.assertEqual(RB.rerun('Run the same comparison', thread()), '')        # no window named
        self.assertEqual(RB.rerun('Age and gender shape of the 2022 people behind each of the eleven titles', thread()), '')


class RuledOutIsNeverTheUniverse(unittest.TestCase):
    def test_a_platform_she_ruled_out_is_not_her_platform(self):
        self.assertEqual(OR.platform_in(BRIEF_2022), '')
        self.assertEqual(OR.platform_in('Audience (base): all US people. Do NOT limit to YouTube viewers or any other platform.'), '')
        self.assertEqual(OR.platform_in('all US people, never Netflix subscribers'), '')
        self.assertEqual(OR.consumer_noun('all US people as the base, not YouTube viewers'), '')

    def test_a_platform_she_named_still_is(self):
        self.assertEqual(OR.platform_in('who watched Fallout on Prime Video'), 'Prime Video')
        self.assertEqual(OR.platform_in('Gilmore Girls Viewers ON HULU'), 'Hulu')
        self.assertEqual(OR.platform_in('not Netflix viewers, Hulu viewers'), 'Hulu')
        self.assertEqual(OR.consumer_noun('Gilmore Girls viewers'), 'viewers')

    def test_a_pointing_order_under_her_read_keeps_her_people(self):
        r = OR.resolve('build it', history=thread())
        self.assertEqual(r['subject'], READ_SUBJECT)                               # no "viewers", no "members" hung on US people
        self.assertEqual(r['platform'], '')
        self.assertTrue(r['landed_read'])
        self.assertNotIn('YouTube', OR.rewrite('build it', history=thread()))
        own = OR.resolve('Can you look into My Life With the Walter Boys viewers? I want to add them as a profile', history=thread())
        self.assertEqual((own['subject'], own['landed_read']), ('My Life With the Walter Boys viewers', False))


class ThroughTheDoor(unittest.TestCase):
    """service.ask with a stub host, on both surfaces, with and without a profile open."""

    @classmethod
    def setUpClass(cls):
        from prometheus.host import host
        cls.calls, cls.mails, cls.hints = [], [], []

        def _analyze_core(user, body, text, history):
            cls.calls.append(('analyze', text, dict(body)))
            return {'success': True, 'action': 'answer', 'reply': 'On it: reading the titles now.', 'followups': [], 'read_job_id': 'job1'}

        def _interpret_core(user, body, text, history):
            cls.calls.append(('interpret', text, dict(body)))
            return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

        host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
                  deck_core=lambda *a: {'success': True, 'reply': 'deck'},
                  ask_hint=lambda **k: cls.hints.append(k), gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: cls.mails.append(a),
                  load_threads_index=lambda u: {'threads': []}, max_threads=50,
                  s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
                  thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
                  thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
                  job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
                  load_users=lambda: {'users': {'canary:rerun17': {'username': 'canary:rerun17', 'unlimited': True, 'company': 'Amazon'}}},
                  check_user_credits=lambda u: (True, 100))
        from prometheus import service
        cls.service = service
        cls.user = {'username': 'canary:rerun17', 'unlimited': True, 'company': 'Amazon'}

    def _ask(self, text, surface='analyze', history=None, page=None, included=True):
        self.calls.clear(), self.mails.clear(), self.hints.clear()
        body = {'text': text, 'surface': surface, 'client': 'probe', 'history': list(history or [])}
        if page:
            body['page_context'] = page
        with mock.patch('prometheus.priced_read.included_with_plan', lambda *a, **k: included):
            return self.service.ask(self.user, body)

    def _routes(self):
        return [h.get('route') for h in self.hints if h.get('route')]

    def test_her_chip_runs_the_brief_for_2023_and_builds_nothing(self):
        for surface in ('interpret', 'analyze'):            # her widget sent it to the build surface
            for page in (None, HAZBIN):
                for included in (True, False):
                    env, st = self._ask(CHIP, surface=surface, history=thread(), page=page, included=included)
                    why = f'{surface} page={bool(page)} included={included}'
                    self.assertEqual(st, 200, why)
                    self.assertEqual([c[0] for c in self.calls], ['analyze'], why)       # one read, never the build flow
                    _kind, text, body = self.calls[0]
                    self.assertIn('calendar year 2023 (Jan 1 \u2013 Dec 31, 2023)', text, why)
                    self.assertNotIn('2022', text, why)
                    self.assertIn('all US people as the base, not YouTube viewers', text, why)
                    self.assertNotIn('Profile IQ', text, why)
                    self.assertNotIn('viewers on YouTube', text, why)
                    self.assertEqual(body.get('bind_subject'), ELEVEN_SUBJECT, why)      # the books, never the page
                    self.assertFalse(body.get('_pm_order'), why)
                    self.assertNotIn('build_order', self._routes(), why)
                    self.assertIn('read_brief', self._routes(), why)
                    self.assertEqual(self.mails, [], why)
                    self.assertNotIn('Hazbin', str(body.get('bind_subject')) + str(env.get('text')), why)

    def test_the_read_being_set_aside_changes_nothing(self):
        env, st = self._ask(CHIP, surface='interpret', history=thread(set_aside=False))
        self.assertEqual([c[0] for c in self.calls], ['analyze'])
        self.assertIn('calendar year 2023', self.calls[0][1])

    def test_a_plain_question_is_run_again_on_the_year_named(self):
        hist = thread(question='How many US people engaged with The Love Hypothesis book in calendar year 2022?',
                      subject='The Love Hypothesis')
        for q in ('Run the same comparison for 2023', 'Run the same for 2023'):
            env, st = self._ask(q, surface='interpret', history=hist)
            self.assertEqual([c[0] for c in self.calls], ['analyze'], q)
            _kind, text, body = self.calls[0]
            self.assertEqual(text, 'How many US people engaged with The Love Hypothesis book in calendar year 2023?', q)
            self.assertEqual(body.get('bind_subject'), 'The Love Hypothesis', q)
            self.assertIn('rerun_window', self._routes(), q)
            self.assertNotIn('build_order', self._routes(), q)
            # a seat that pays is quoted first; nothing starts and nothing is built
            env, st = self._ask(q, surface='interpret', history=hist, included=False)
            self.assertEqual(self.calls, [], q)
            self.assertIn('January 1 to December 31, 2023', env['text'], q)
            self.assertNotIn('build_order', self._routes(), q)

    def test_an_order_that_names_no_product_under_a_read_waits_for_her_yes(self):
        env, st = self._ask('build it', surface='analyze', history=thread())
        self.assertEqual([c[0] for c in self.calls], ['interpret'])
        _kind, text, body = self.calls[0]
        self.assertNotIn('YouTube', text)
        self.assertTrue(body.get('_pm_no_auto_start'))                                   # shown for her yes, never started
        # an order that names its product starts as it always has
        env, st = self._ask('I want to add them as a profile', surface='analyze', history=thread())
        self.assertEqual([c[0] for c in self.calls], ['interpret'])
        self.assertFalse(self.calls[0][2].get('_pm_no_auto_start'))

    def test_an_order_she_typed_still_goes_to_the_build_flow(self):
        q = ('Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 - Oct 31 2026.')
        env, st = self._ask(q, surface='analyze', history=thread())
        self.assertEqual([c[0] for c in self.calls], ['interpret'])
        self.assertTrue(self.calls[0][2].get('_pm_order'))
        self.assertFalse(self.calls[0][2].get('_pm_no_auto_start'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
