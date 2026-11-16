#!/usr/bin/env python3
"""A question about what we can see or tell apart gets the answer, never a
product description (Jenna 2026-11-13: "the answer here would have been
yes, not what it gave").

David Carter (Sony Pictures Television), one thread, 2026-11-13:

    19:12 UTC  "Is there any podcast audience measurement available in Crosswalk?"
    19:14 UTC  "Can you differentiate between audio podcasts and video podcasts?"

Both drew "Yes." bolted onto the Profile IQ description. Neither reply
held the word podcast.

Held here:

  * TheRecord            what went out, and the two causes
  * TheFacts             the answer to each can-you question: yes, no or
                         partly first, then how, then one next step; the
                         description and the brief are never in it
  * TheLanes             what stays where it was (a product question, an
                         order, a subject of its own, the thread's own
                         material, the no-digital-record decline)
  * ThroughTheDoor       both surfaces (the ask box and the place profiles
                         are built): answered, no model call, no brief, no
                         priced offer
  * TheReaderBranch      the Reader calling it a capability question can no
                         longer pick a product off its own note
  * RealWorkStillRuns    a build order still gets its brief, an audience
                         question still reaches the read step
  * TheNextQuestion      a can-you question the sheet holds no line for
                         (2026-11-16): the written answer keeps the same
                         shape, is checked before it goes out, and the
                         one-word yes stands when nothing clean comes back

Hermetic: the stub host of test_pm_new_chat_no_bleed. No model, no S3, no
app module.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('pm_nb_for_capability', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

from prometheus import service as S  # noqa: E402
from prometheus import can_do as CD  # noqa: E402
from prometheus import data_capability as DC  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus import reader as RD  # noqa: E402
from prometheus import own_read as OR  # noqa: E402

APP, World = NB.APP, NB.World

Q1 = 'Is there any podcast audience measurement available in Crosswalk?'
Q2 = 'Can you differentiate between audio podcasts and video podcasts?'
WENT_OUT_1 = ("Yes. A Profile IQ profile builds the audience of any person, title, brand or behavior with demographics and the "
              "brands, platforms and talent they over-index on. Tell me the subject and the window and I will line it up; it "
              "comes back as a brief to approve before anything is charged.")
WENT_OUT_2 = ("Yes. A Profile IQ profile builds the audience of any person, title, brand or behavior (people who attended a "
              "tour, bought a product, watched a title) with demographics and the brands, platforms and talent they over-index "
              "on. Tell me the subject and the window and I will line it up; it comes back as a brief to approve before "
              "anything is charged.")
READING_1 = {'intent': 'capability', 'confidence': 0.82,
             'subjects': [{'as_written': 'podcast audience measurement', 'catalog': '', 'kind': 'unknown'}], 'flags': [],
             'wants': 'They want to know whether Crosswalk has podcast audience measurement.',
             'restate': 'You want to know whether podcast audience measurement is available.'}
READING_2 = {'intent': 'capability', 'confidence': 0.89, 'subjects': [], 'flags': [],
             'wants': 'They want to know whether podcast audiences can be split between audio listening and video viewing.',
             'restate': 'You want to know whether podcast audiences can be split between audio listening and video viewing.'}

# the product description and the brief, in the words that went out
PITCH = ('A Profile IQ profile builds', 'Tell me the subject and the window', 'comes back as a brief',
         'before anything is charged', 'I will line it up', 'Brief drafted')

# question -> (verdict, the fact, words the answer must carry)
CASES = (
    (Q2, 'yes', 'podcast_audio_video', ('audio apps', 'YouTube', 'Spotify', 'listeners from its viewers')),
    (Q1, 'yes', 'podcast_measured', ('podcast', 'Apple Podcasts', 'YouTube')),
    ('Can you separate full episodes from clips?', 'yes', 'episodes_clips', ('full episodes', 'clips', 'Shorts')),
    ("Is it possible to split a show's YouTube clips from its full episodes?", 'yes', 'episodes_clips', ('full episodes', 'clips')),
    ('Can you see app use?', 'yes', 'app_use', ('App activity', 'phones')),
    ('Do you have any data on app usage?', 'yes', 'app_use', ('App activity',)),
    ('How do you tell a listener from a viewer?', 'yes', 'podcast_audio_video', ('audio apps', 'YouTube')),
    # the honest answer is no or partly
    ('Can you tell whether someone watched a podcast with video or just audio inside Spotify?', 'no', 'podcast_one_app',
     ('not inside one app', 'do not split audio from video')),
    ('Can you tell subscribers from free viewers?', 'partly', 'paying_against_free', ('subscribers and viewers', 'do not split')),
    ('Can you measure who heard a podcast ad on the radio?', 'no', 'offline', ('Radio', 'outside what I measure', 'digital side')),
    ('Are you able to see what someone read in a print magazine?', 'no', 'offline', ('outside what I measure',)),
)
LEAD = {'yes': 'Yes.', 'no': 'No', 'partly': 'Partly.'}


def _ask(w, text, tid=None, surface='analyze', history=None):
    w.calls.clear()
    body = {'text': text, 'client': 'dashboard', 'surface': surface, '_client_surface_sent': surface}
    if tid:
        body['thread_id'] = tid
    if history is not None:
        body['history'] = list(history)
    with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
        return S.ask(dict(NB.ARMINE), body)


def _no_pitch(tc, text, why=''):
    for p in PITCH:
        tc.assertNotIn(p, text or '', why or p)


# ============================================================================ 1
class TheRecord(unittest.TestCase):
    def test_what_went_out_said_nothing_about_podcasts(self):
        for reply in (WENT_OUT_1, WENT_OUT_2):
            self.assertTrue(reply.startswith('Yes. A Profile IQ profile builds the audience'))
            self.assertNotIn('podcast', reply.lower())

    def test_cause_one_a_can_you_with_no_product_no_longer_defaults_to_a_description(self):
        """Q2: "Can you ..." with no product word fell to the last row of the product table."""
        self.assertIsNone(SM.capability_ask(Q2))
        self.assertIsNone(SM.capability_ask('can you explan the pinterst more?'))            # Samantha Kolton, 2026-11-06
        self.assertFalse(OR.names_a_product(Q2))

    def test_cause_two_the_readers_note_no_longer_picks_a_product(self):
        """Q1: neither lane read "Is there any ... available"; the Reader's note held the word 'audience'."""
        self.assertIsNotNone(RD.capability_reply(READING_1))                                   # the old call still describes a product
        self.assertIn('Profile IQ', RD.capability_reply(READING_1)['reply'])
        self.assertIsNone(RD.capability_reply(READING_1, Q1))                                  # with the words, it does not
        self.assertIsNone(RD.capability_reply(READING_2, Q2))

    def test_a_question_that_names_a_product_keeps_the_product_answer(self):
        cap = SM.capability_ask('Can you do a brand partnership valuation?')
        self.assertTrue(cap and cap.get('reply'))
        self.assertIsNone(CD.answer('Can you do a brand partnership valuation?'))


# ============================================================================ 2
class TheFacts(unittest.TestCase):
    def test_each_question_gets_its_verdict_first_then_how_then_one_step(self):
        for q, verdict, key, carries in CASES:
            a = CD.answer(q)
            self.assertTrue(a, q)
            self.assertEqual((a['can_do'], a['can_do_verdict']), (key, verdict), q)
            reply = a['reply']
            self.assertTrue(reply.startswith(LEAD[verdict]), (q, reply[:40]))
            for word in carries:
                self.assertIn(word, reply, (q, word))
            _no_pitch(self, reply, q)
            self.assertEqual(a['action'], 'answer')
            self.assertNotIn('spec_draft', a)
            # the last sentence is the one next step, in the reader's hands
            self.assertRegex(reply.rstrip('.').split('. ')[-1], r"^Name (?:the|a) ", q)

    def test_his_answer_states_where_the_line_sits(self):
        reply = CD.answer(Q2)['reply']
        self.assertIn('counted apart from the people who watched its video episodes on YouTube', reply)
        self.assertIn('inside a single app that plays an episode with or without the picture, such as Spotify', reply)
        self.assertIn('do not split audio from video', reply)

    def test_no_answer_carries_house_banned_words(self):
        banned = ('synth', 'modeled', 'estimated', 'pipeline', 'hostmap', 'clickstream', 'panelist', 'content map',
                  'streamscout', 'resolver', '\u2014', '\u2013', 'household')
        for f in CD.FACTS:
            text = ' '.join((f['lead'], f['how'], f['next'])).lower()
            for b in banned:
                self.assertNotIn(b, text, (f['key'], b))

    def test_a_service_named_radio_is_not_radio(self):
        self.assertFalse(CD.is_offline('Can you see iHeartRadio listening?'))
        self.assertTrue(CD.is_offline('Can you measure who heard it on the radio?'))
        self.assertIsNone(DC.answer('Can you measure who heard a podcast ad on the radio?'))   # never the one-word yes

    def test_kill_switch(self):
        old = os.environ.get('PM_CAN_DO')
        os.environ['PM_CAN_DO'] = '0'
        try:
            self.assertIsNone(CD.answer(Q2))
        finally:
            if old is None:
                os.environ.pop('PM_CAN_DO', None)
            else:
                os.environ['PM_CAN_DO'] = old


# ============================================================================ 3
class TheLanes(unittest.TestCase):
    def test_what_is_not_a_can_you_question(self):
        for q in ('Can you build me a profile for Call Her Daddy?',              # an order with manners
                  'Do you have a podcast profile for Call Her Daddy?',           # names its own subject: the library answers
                  'Can you size the Call Her Daddy audience?',                   # work behind the frame
                  'can you explan the pinterst more?',                           # points at the thread
                  'Can you email me the podcast file?',                          # a send
                  'How many people listen to podcasts on Spotify?',              # a question for a read, not about ability
                  'What is the price of a podcast profile?'):
            self.assertIsNone(CD.answer(q), q)

    def test_the_no_digital_record_decline_keeps_its_own_words(self):
        for q in ('Can you see what people buy in store?', 'Can you see what people watch on linear TV?'):
            self.assertIsNone(CD.answer(q), q)                                   # the gate's decline stands
            self.assertIsNone(DC.answer(q), q)


# ============================================================================ 4
class ThroughTheDoor(unittest.TestCase):
    def test_his_two_questions_on_both_surfaces(self):
        """The ask box, and the place profiles are built: answered, no model call, no brief, no priced offer."""
        for surface in ('analyze', 'interpret'):
            for q, must in ((Q1, 'Apple Podcasts'), (Q2, 'do not split audio from video')):
                w = World()
                tid = w.new_thread()
                env, st = _ask(w, q, tid=tid, surface=surface)
                text = env.get('text') or ''
                self.assertEqual(st, 200)
                self.assertTrue(text.startswith('Yes. '), (surface, q, text[:60]))
                self.assertIn(must, text)
                self.assertIn('podcast', text.lower())
                _no_pitch(self, text, (surface, q))
                self.assertEqual(w.calls, [], (surface, q))                      # no read started, no brief drafted
                self.assertFalse(env.get('spec_draft'))
                self.assertFalse(env.get('offer') or env.get('panel_offer') or env.get('quote'))

    def test_every_case_through_the_door(self):
        for q, verdict, _key, carries in CASES:
            w = World()
            tid = w.new_thread()
            env, _st = _ask(w, q, tid=tid)
            text = env.get('text') or ''
            self.assertTrue(text.startswith(LEAD[verdict]), (q, text[:60]))
            self.assertIn(carries[0], text, q)
            _no_pitch(self, text, q)
            self.assertEqual(w.calls, [], q)

    def test_a_question_with_no_line_to_state_is_the_one_word_yes_never_a_description(self):
        w = World()
        tid = w.new_thread()
        env, _st = _ask(w, 'Are you able to distinguish new viewers from returning viewers?', tid=tid)
        self.assertEqual((env.get('text') or '').strip(), 'Yes.')
        _no_pitch(self, env.get('text'))


# ============================================================================ 5
class TheReaderBranch(unittest.TestCase):
    """What ran for him in production: the standing lanes passed, the Reader said 'capability'."""

    def _run(self, reading, text, surface='analyze'):
        w = World()
        tid = w.new_thread()
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            out = S._execute_reading(dict(reading), text, {}, [], dict(NB.ARMINE), NB.USER,
                                     {'text': text, 'client': 'dashboard'}, tid, 'dashboard', False, surface)
        return w, out

    def test_his_questions_with_the_readers_own_readings(self):
        for reading, q, must in ((READING_1, Q1, 'Apple Podcasts'), (READING_2, Q2, 'do not split audio from video')):
            for surface in ('analyze', 'interpret'):
                w, out = self._run(reading, q, surface)
                self.assertIsNotNone(out, q)
                env, st = out
                text = env.get('text') or ''
                self.assertTrue(text.startswith('Yes. '), text[:60])
                self.assertIn(must, text)
                _no_pitch(self, text, (surface, q))
                self.assertEqual(w.calls, [])

    def test_with_the_fact_lane_off_it_is_yes_or_the_answer_lanes_never_a_description(self):
        old = os.environ.get('PM_CAN_DO')
        os.environ['PM_CAN_DO'] = '0'
        try:
            for reading, q in ((READING_1, Q1), (READING_2, Q2)):
                _w, out = self._run(reading, q)
                if out is not None:
                    _no_pitch(self, out[0].get('text'), q)
        finally:
            if old is None:
                os.environ.pop('PM_CAN_DO', None)
            else:
                os.environ['PM_CAN_DO'] = old

    def test_a_capability_reading_on_something_else_falls_to_the_answer_lanes(self):
        """Not about a product, not about seeing or telling apart: no lane here answers; the read step does."""
        reading = {'intent': 'capability', 'confidence': 0.9, 'subjects': [], 'flags': [],
                   'wants': 'They want to know whether the audience can be explained further.'}
        _w, out = self._run(reading, 'can you explan the pinterst more?')
        self.assertIsNone(out)

    def test_an_offline_question_the_reader_calls_capability_is_never_yes(self):
        reading = {'intent': 'capability', 'confidence': 0.9, 'subjects': [], 'flags': [], 'wants': 'They ask about radio.'}
        _w, out = self._run(reading, 'Can you measure who heard a podcast ad on the radio?')
        self.assertIsNotNone(out)
        self.assertTrue((out[0].get('text') or '').startswith('No.'))


# ============================================================================ 6
class RealWorkStillRuns(unittest.TestCase):
    def test_a_build_order_still_gets_its_brief(self):
        w = World()
        tid = w.new_thread()
        env, _st = _ask(w, 'Build a profile for Call Her Daddy', tid=tid, surface='interpret')
        self.assertTrue([c for c in w.calls if c[0] == 'interpret'])
        self.assertNotIn('audio apps', env.get('text') or '')

    def test_an_audience_question_still_reaches_the_read_step(self):
        for q in ('How many Call Her Daddy listeners also watch it on YouTube?',
                  'What share of podcast listeners use Spotify?'):
            w = World()
            tid = w.new_thread()
            env, _st = _ask(w, q, tid=tid)
            self.assertTrue([c for c in w.calls if c[0] == 'analyze'] or env.get('offer') or env.get('panel_offer')
                            or 'credit' in (env.get('text') or '').lower() or '$' in (env.get('text') or ''), (q, env.get('text')))
            self.assertIsNone(CD.answer(q), q)

    def test_samanthas_follow_up_still_reaches_the_read_step(self):
        w = World()
        tid = w.new_thread()
        env, _st = _ask(w, 'can you explan the pinterst more?', tid=tid)
        _no_pitch(self, env.get('text'))
        self.assertTrue([c for c in w.calls if c[0] == 'analyze'])


# ============================================================================ 7
NEXT_Q = 'Are you able to distinguish new viewers from returning viewers?'
GOOD = ("Yes. A viewer is new when the title is the first thing they played on that service inside the window, and "
        "returning when they had played it before. Name the title and I will split the two.")


class TheNextQuestion(unittest.TestCase):
    """A capability question no line of the sheet answers: one written
    answer, grounded in the sheet, held to the shape."""

    def test_the_written_answer_keeps_the_shape(self):
        seen = {}

        def reason(system, user):
            seen['system'], seen['user'] = system, user
            return GOOD
        a = CD.explain(NEXT_Q, reason=reason)
        self.assertTrue(a)
        self.assertEqual((a['can_do'], a['can_do_verdict'], a['action']), ('written', 'yes', 'answer'))
        self.assertEqual(a['reply'], GOOD)
        _no_pitch(self, a['reply'])
        self.assertIn(NEXT_Q, seen['user'])
        # the model stands on the sheet and the boundary, nothing else
        for f in CD.FACTS:
            self.assertIn(f['how'], seen['system'], f['key'])
        self.assertIn('no digital record', seen['system'])
        self.assertIn('Never describe a product', seen['system'])

    def test_no_and_partly_are_answers_too(self):
        for out, verdict in (("No. That happens off any device, so it is outside what I measure. Name the title and I will read "
                              "the streaming side.", 'no'),
                             ("Partly. I can tell which service was used and for how long, and I do not split the plan inside "
                              "it. Name the service and I will show what is there.", 'partly')):
            a = CD.explain(NEXT_Q, reason=lambda s_, u_, _o=out: _o)
            self.assertTrue(a, out)
            self.assertEqual(a['can_do_verdict'], verdict)

    def test_what_does_not_keep_the_shape_is_dropped(self):
        bad = (WENT_OUT_2,                                                        # the description that went out to him
               "Yes. A Profile IQ profile builds the audience. Tell me the subject.",
               "Yes. I can line it up as a brief to approve before anything is charged.",
               "Sure, new and returning viewers can be told apart. Name the title.",   # no yes, no or partly first
               "Yes. About 4,200,000 people are new each month. Name the title.",     # a figure
               "Yes. Around 42% are new. Name the title.",
               "Yes. It is a modeled split. Name the title.",
               "Yes. The panel shows it. Name the title.",
               "Yes. That costs $100. Name the title.",
               "Yes. New viewers \u2014 and returning ones \u2014 are split. Name the title.",
               "Yes.\n\n- one\n- two",
               "Yes. " + ("word " * 130),
               "")
        for out in bad:
            self.assertFalse(CD.shape_ok(out), out[:50])
            self.assertIsNone(CD.explain(NEXT_Q, reason=lambda s_, u_, _o=out: _o), out[:50])
        self.assertIsNone(CD.explain(NEXT_Q, reason=lambda s_, u_: (_ for _ in ()).throw(RuntimeError('down'))))

    def test_it_never_spends_a_call_on_what_another_lane_owns(self):
        calls = []

        def reason(system, user):
            calls.append(user)
            return GOOD
        for q in ('Can you measure who heard a podcast ad on the radio?',          # no digital record: the no stands
                  'Can you see what people buy in store?',                         # the gate's decline
                  'Can you do a brand partnership valuation?',                     # names a product
                  'Can you build me a profile for Call Her Daddy?',                # an order with manners
                  'Can you see which podcasts young men listen to?',               # work behind the frame: the read step's
                  'Can you tell which shows people watch after a podcast?',        # carries its own question: yes and the chip
                  'How many people listen to podcasts on Spotify?',                # a question for a read
                  'What is the price of a podcast profile?'):
            self.assertIsNone(CD.explain(q, reason=reason), q)
        self.assertEqual(calls, [])

    def test_a_test_or_a_replay_never_calls_the_model_on_its_own(self):
        self.assertTrue(os.environ.get('REGRESSION_TEST_MODE'))
        self.assertIsNone(CD.explain(NEXT_Q))

    def test_a_question_that_carries_its_own_question_is_yes_and_the_chip(self):
        for q, chip in (('Can you tell which shows people watch after a podcast?', 'Which shows people watch after a podcast?'),
                        ('Can you track how long people listen to a podcast?', 'How long people listen to a podcast?')):
            self.assertIsNone(CD.answer(q), q)             # the sheet's podcast line is not the answer to a question of its own
            self.assertIsNone(CD.explain(q, reason=lambda s_, u_: GOOD), q)
            a = DC.answer(q)
            self.assertTrue(a and a['reply'] == 'Yes.', q)
            self.assertEqual(a['followups'], [chip])

    def test_through_the_door_on_both_surfaces(self):
        real = CD.explain
        for surface in ('analyze', 'interpret'):
            with mock.patch.object(CD, 'explain', lambda text, **kw: real(text, reason=lambda s_, u_: GOOD)):
                w = World()
                tid = w.new_thread()
                env, st = _ask(w, NEXT_Q, tid=tid, surface=surface)
            text = env.get('text') or ''
            self.assertEqual(st, 200)
            self.assertEqual(text.strip(), GOOD, surface)
            _no_pitch(self, text, surface)
            self.assertEqual(w.calls, [], surface)
            self.assertFalse(env.get('spec_draft'))

    def test_through_the_door_a_dropped_answer_leaves_the_one_word_yes(self):
        real = CD.explain
        with mock.patch.object(CD, 'explain', lambda text, **kw: real(text, reason=lambda s_, u_: WENT_OUT_2)):
            w = World()
            tid = w.new_thread()
            env, _st = _ask(w, NEXT_Q, tid=tid)
        self.assertEqual((env.get('text') or '').strip(), 'Yes.')

    def test_the_reader_branch_writes_the_answer_too(self):
        """A can-you shape the yes lane does not read, which the Reader calls a capability question."""
        q = 'Is cross-device viewing tracked?'
        reading = {'intent': 'capability', 'confidence': 0.9, 'subjects': [], 'flags': [],
                   'wants': 'They want to know whether viewing across devices is tracked.'}
        self.assertIsNone(DC.answer(q))
        self.assertTrue(CD.reads_as_data_ability(q))
        real = CD.explain
        out_text = ("Yes. The same person is followed across their phone, computer and connected TV, so a title started on "
                    "one and finished on another is one viewer. Name the title and I will show it by device.")
        with mock.patch.object(CD, 'explain', lambda text, **kw: real(text, reason=lambda s_, u_: out_text)):
            w = World()
            tid = w.new_thread()
            with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
                out = S._execute_reading(dict(reading), q, {}, [], dict(NB.ARMINE), NB.USER,
                                         {'text': q, 'client': 'dashboard'}, tid, 'dashboard', False, 'analyze')
        self.assertIsNotNone(out)
        self.assertEqual((out[0].get('text') or '').strip(), out_text)
        # and with nothing clean back, the one-word yes, never a description
        with mock.patch.object(CD, 'explain', lambda text, **kw: None):
            with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
                out = S._execute_reading(dict(reading), q, {}, [], dict(NB.ARMINE), NB.USER,
                                         {'text': q, 'client': 'dashboard'}, tid, 'dashboard', False, 'analyze')
        self.assertEqual((out[0].get('text') or '').strip(), 'Yes.')


if __name__ == '__main__':
    unittest.main(verbosity=1)
