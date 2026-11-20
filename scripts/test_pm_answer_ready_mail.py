#!/usr/bin/env python3
"""The "Your answer is ready" email fires only for a finished answer
(Jenna 2026-11-19, the Aiyana Lee turn).

Jenna asked "Run a profile for Aiyana Lee"; the ask ran past the long-wait
mark, the build started in the same turn, and the build acknowledgment
("Included with your plan. Profile building: Aiyana Lee ... I will email
you when it is complete.") was mailed to her under "Your answer is ready."
Her words: "I shouldnt have gotten this email ... my answer wasnt ready".

Pins prometheus.answer_ready and the long-wait mail in
prometheus.blueprint:

  1. the Aiyana Lee style build acknowledgment is never mailed as a
     ready answer (the build's completion email is the only mail);
  2. a finished read still mails exactly as before;
  3. a clarifying question is not mailed;
  4. a directions (wayfinding) answer is not mailed;
  5. the other waiting turns (a brief, a quote, a queued read, the calm
     promise, a refusal that says we will get back) are not mailed;
  6. the Raw column line keeps its words through the exit scrub (the
     reader got "the countUS consumers we measure");
  7. the blueprint routes the long-wait mail through the gate.

Hermetic: the outbound door is replaced by a recorder; no SES, no S3,
no Flask app boot.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import answer_ready as AR  # noqa: E402
from prometheus import blueprint as BP  # noqa: E402
from prometheus import envelope as E  # noqa: E402
from prometheus import guards as G  # noqa: E402
from prometheus import orders as O  # noqa: E402
from prometheus import outbound_mail as OM  # noqa: E402

USER = {'username': 'jenna', 'email': 'jenna@crosswalknyc.com', 'role': 'super_admin', 'unlimited': True}
BODY = {'text': 'Run a profile for Aiyana Lee'}
READ = ("On Abbott Elementary viewers on Hulu: 58.1% are women and 46.2% are under 35. They over-index on Netflix "
        "(71.4%, 1.3x the US average) and on TikTok (54.8%). The most engaged tier is 412,317 people, 3.9% of the audience.")


def _build_ack(name='Aiyana Lee', *, auto_started=True):
    """The turn an unlimited seat reads when its order starts in the same
    turn (prometheus.legacy.chat._pm_auto_start_order)."""
    reply = O.build_line(name, '', avid=True, included=True)
    raw = {'success': True, 'run_id': 'a1b2c3d4e5', 'included_with_plan': True, 'message': reply, 'reply': reply,
           'subject': name, 'followups': [], 'run_avid': True}
    if auto_started:
        raw['auto_started'] = True
    return raw


def _wrap(raw, surface='analyze', reason='page_analysis'):
    return E.wrap(dict(raw), surface=surface, decision={'surface': surface, 'mode': None, 'reason': reason, 'client_hint': None})


class _Door:
    """Stands in for prometheus.outbound_mail.send_user_email."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return {'sent': True, 'reason': '', 'message_id': 'm1', 'destinations': [kw.get('to')]}


class AnswerReadyMail(unittest.TestCase):
    def setUp(self):
        self.door = _Door()
        self._orig = OM.send_user_email
        OM.send_user_email = self.door

    def tearDown(self):
        OM.send_user_email = self._orig

    # 1. the Aiyana Lee turn
    def test_a_build_that_started_is_not_mailed_as_a_ready_answer(self):
        env = _wrap(_build_ack(), surface='interpret', reason='build_order')
        self.assertEqual(env['kind'], 'answer')                      # the envelope reads it as an answer: the gate must not
        ok, why = AR.finished_answer(env)
        self.assertFalse(ok)
        self.assertEqual(why, 'order')
        self.assertFalse(BP._email_long_answer(USER, BODY, env))
        self.assertEqual(self.door.calls, [])

    def test_a_build_line_with_a_run_behind_it_is_refused_on_the_run_and_the_words(self):
        # the same words from a path that does not stamp auto_started: the run id and the promise both refuse it
        raw = _build_ack(auto_started=False)
        env = _wrap(raw, surface='interpret', reason='build_order')
        ok, why = AR.finished_answer(env)
        self.assertFalse(ok)
        self.assertEqual(why, 'work still to come')
        bare = {'success': True, 'action': 'answer', 'reply': raw['reply'], 'followups': []}
        ok, why = AR.finished_answer(_wrap(bare))
        self.assertFalse(ok)
        self.assertEqual(why, 'promise')
        self.assertFalse(BP._email_long_answer(USER, BODY, env))
        self.assertEqual(self.door.calls, [])

    # 2. a finished read
    def test_a_finished_read_mails_as_before(self):
        raw = {'success': True, 'action': 'answer', 'reply': READ, 'followups': ['Split it by age'],
               'subject': 'Abbott Elementary viewers on Hulu', 'model': 'read', 'figures': [{'label': 'women', 'value': 58.1}]}
        env = _wrap(raw)
        self.assertEqual(AR.finished_answer(env), (True, 'answer'))
        self.assertTrue(BP._email_long_answer(USER, {'text': 'Who watches Abbott Elementary on Hulu?'}, env))
        self.assertEqual(len(self.door.calls), 1)
        call = self.door.calls[0]
        self.assertEqual(call['to'], 'jenna@crosswalknyc.com')
        self.assertTrue(call['instructed'])
        self.assertEqual(call['caller'], 'long_ask')
        self.assertTrue(call['subject'].startswith('Prometheus: Who watches Abbott Elementary on Hulu?'))
        self.assertIn('58.1% are women', call['body'])
        self.assertIn('This answer is also in your chat thread on the dashboard.', call['body'])
        self.assertIn('Your answer is ready.', call['html'] or '')

    def test_a_count_a_sizing_and_a_table_still_mail(self):
        for reply in ('About 2,776,141 US people watched Gilmore Girls on Hulu in the last 12 months.',
                      'Women 25 to 44 who bought from Sephora online: 6,214,873 US people, 188,366 inside the data.',
                      'Platform / Share\nNetflix / 71.4%\nHulu / 44.2%\nPeacock / 18.9%'):
            env = _wrap({'success': True, 'action': 'answer', 'reply': reply, 'followups': []})
            self.assertEqual(AR.finished_answer(env), (True, 'answer'), reply)

    # 3. a clarifying question
    def test_a_clarifying_question_is_not_mailed(self):
        cases = [
            ({'success': True, 'action': 'clarify', 'question': 'Which Aiyana Lee do you mean: the singer or the actress?'}, 'clarify'),
            ({'success': True, 'action': 'answer', 'reply': 'Which title do you mean?',
              'clarify': {'question': 'Which title do you mean?', 'options': ['Tony (2026)', 'Tony (2013)']}}, 'clarify'),
            ({'success': False, 'guidance': True, 'error': 'Which audience should the profile be on? Name the show, brand or person.'}, 'guidance'),
            ({'success': True, 'action': 'answer', 'reply': 'Did you mean the Hulu audience or the Netflix audience?',
              'followups': ['Hulu', 'Netflix'], 'options': [{'label': 'Hulu'}, {'label': 'Netflix'}]}, 'waiting on the reader'),
        ]
        for raw, why in cases:
            env = _wrap(raw)
            ok, got = AR.finished_answer(env)
            self.assertFalse(ok, raw)
            self.assertEqual(got, why, raw)
            self.assertFalse(BP._email_long_answer(USER, BODY, env))
        self.assertEqual(self.door.calls, [])

    # 4. directions
    def test_a_directions_answer_is_not_mailed(self):
        raw = {'success': True, 'action': 'answer', 'followups': ['Open Profile IQ'], 'offer_deck': False, 'deck_angle': None,
               'reply': 'Profile IQ is under SELECT PRODUCT at the top of the page. Your newest profile there: Aiyana Lee. '
                        'Direct link: https://dashboard.crosswalknyc.com/?view=profileIQ',
               'wayfinding': {'kind': 'product', 'product': 'profileIQ', 'name': '', 'feature': ''},
               'action_hint': {'view': 'profileIQ'}, 'open_view': 'profileIQ'}
        env = _wrap(raw, reason='wayfinding')
        ok, why = AR.finished_answer(env)
        self.assertFalse(ok)
        self.assertEqual(why, 'directions')
        # the decision alone names the lane when a payload carries no block
        bare = _wrap({'success': True, 'action': 'answer', 'reply': 'It is under SELECT PRODUCT.', 'followups': []}, reason='wayfinding')
        self.assertEqual(AR.finished_answer(bare), (False, 'directions'))
        self.assertFalse(BP._email_long_answer(USER, {'text': 'where is profile iq'}, env))
        self.assertEqual(self.door.calls, [])

    # 5. the other waiting turns
    def test_the_other_waiting_turns_are_not_mailed(self):
        cases = [
            ({'success': True, 'spec_draft': {'subject': 'Aiyana Lee', 'decision': 'new_build', 'run_avid': True}}, 'draft'),
            ({'success': True, 'action': 'answer', 'reply': 'Pulling this data will cost $500. Approve or cancel.',
              'panel_offer': {'id': 'o1', 'price_usd': 500}}, 'waiting on the reader'),
            ({'success': True, 'action': 'answer', 'reply': 'On it: reading Abbott Elementary now. The read will land right here.',
              'read_job_id': 'j1'}, 'job'),
            ({'success': True, 'action': 'answer', 'reply': 'Working on it. Confirmed your task is in progress and I will email you when it completes.',
              'held_ref': 'system/ops/held_replies/2026-11-19/x.json'}, 'order'),
            ({'success': True, 'action': 'answer', 'reply': 'Working on it. Confirmed your task is in progress and I will email you when it completes.'}, 'promise'),
            ({'success': True, 'action': 'answer', 'reply': "We can't see Tony (2026) on Amazon at this time. Notifying Crosswalk and will get back to you soon."}, 'promise'),
            ({'success': True, 'action': 'answer', 'reply': 'Aiyana Lee is building.'}, 'promise'),
            ({'success': False, 'guidance': True, 'error': 'A profile is $300 and a cut is $75.'}, 'guidance'),
        ]
        for raw, why in cases:
            env = _wrap(raw, surface=('interpret' if 'spec_draft' in raw else 'analyze'))
            ok, got = AR.finished_answer(env)
            self.assertFalse(ok, raw)
            self.assertEqual(got, why, raw)
            self.assertFalse(BP._email_long_answer(USER, BODY, env))
        self.assertEqual(self.door.calls, [])

    def test_the_gate_never_raises_and_reads_a_doubtful_reply_as_not_finished(self):
        self.assertEqual(AR.finished_answer(None), (False, 'no envelope'))
        self.assertEqual(AR.finished_answer({'kind': 'answer'}), (False, 'no payload'))
        self.assertEqual(AR.finished_answer({'kind': 'answer', 'raw': {'success': True, 'action': 'answer'}}), (False, 'no text'))
        self.assertFalse(BP._email_long_answer(USER, BODY, {'kind': 'answer', 'raw': {'success': True, 'reply': 'x', 'action': 'rewrite'}}))
        self.assertEqual(self.door.calls, [])

    # 6. the Raw column line
    def test_the_raw_column_line_keeps_its_words_through_the_exit_scrub(self):
        line = O.build_line('Aiyana Lee', '', avid=True, included=True)
        self.assertIn(O.RAW_PHRASE, line)
        self.assertIn('the count inside the 10 million US consumers we measure (the Raw column)', G.scrub_sample_vocabulary(line))
        self.assertNotIn('countUS', G.scrub_sample_vocabulary(line))
        env = _wrap(_build_ack(), surface='interpret', reason='build_order')
        self.assertIn(O.RAW_PHRASE, env['raw']['reply'])                 # the whole exit, as the widget and the mail read it
        self.assertIn(O.RAW_PHRASE, env['text'])
        self.assertNotIn('countUS', env['text'])
        # the sample-count sentences the scrub is for still lose the sample count
        self.assertEqual(G.scrub_sample_vocabulary('Estimated audience: 158,305 to 220,662 inside the 10 million sample, '
                                                   'about 5,222,482 to 7,279,639 US people.'),
                         'Estimated audience: about 5,222,482 to 7,279,639 US people.')
        self.assertEqual(G.scrub_sample_vocabulary('Gilmore Girls viewers: 416,277 inside the 10 million sample.'),
                         'Gilmore Girls viewers: 416,277.')
        self.assertEqual(G.scrub_sample_vocabulary('State the count inside the 10 million US consumers we measure beside it.'),
                         'State the count inside the 10 million US consumers we measure beside it.')

    # 7. the wiring
    def test_the_blueprint_routes_the_long_wait_mail_through_the_gate(self):
        src = Path(ROOT, 'prometheus', 'blueprint.py').read_text(encoding='utf-8')
        start = src.index('def _email_long_answer(')
        fn = src[start:src.index('\n@bp.route', start)]
        self.assertIn('from . import answer_ready as _ar', fn)
        self.assertIn('ok, _why = _ar.finished_answer(env)', fn)
        self.assertIn("caller='long_ask'", fn)
        self.assertIn("'Your answer is ready.'", fn)
        self.assertIn('_emailed = _email_long_answer(user, body, env)', src)      # still the one caller, after the long wait
        for word in ('\u2014', '\u2013', 'synth', 'pipeline', 'hostmap', 'queue', 'worker'):
            self.assertNotIn(word, Path(ROOT, 'prometheus', 'answer_ready.py').read_text(encoding='utf-8').lower())


if __name__ == '__main__':
    unittest.main(verbosity=1)
