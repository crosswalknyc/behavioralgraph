#!/usr/bin/env python3
"""One typed Understanding up front (Prometheus rebuild stage 2, Jenna
2026-11-20: "all sound good. let's update and put into action asap").

Pins, hermetically (no model, no S3, no mail, no chat module, no app):

1. the type: to_dict / from_dict / compact / parse_compact round-trip,
   the compact line under 200 characters and free of client text beyond
   a named title;
2. the features reading on the incident classes: where-is on a feature
   is wayfinding and the reader's own order is status (own_order scope);
   an insight to share is insight_ask and never a send; a send order is
   send_file_order; an explicit order is order with action build; a
   cancel is cancel; an armed step is armed; a named title rides as the
   subject; no_build never leaves action build;
3. absorb_reading: a confident reading refines a generic intent, a hard
   rule stands, the contested pairs are settled by the words' scope, the
   subjects and window ride along;
4. claims: off, the lane's own predicate decides; on, the lane runs when
   the Understanding agrees and its predicate agrees, stands down when the
   Understanding read another contested intent, keeps its own predicate
   where the Understanding took no position, and takes no position on
   words a later lane rewrote; the per-intent kill switch and the master
   switch restore the old path;
5. the ask log: stamp_log carries the reading as before and the compact
   Understanding; grade_log_rows reads agreement through the one class
   table;
6. through the door in service mode on a stub host: the own-order ask
   lands on the reader's own work (the known miss), the feature ask on
   wayfinding, the insight ask never on a send, the send order still on
   email_file, and the kill switch brings the old path back.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['PM_MAIL_OFF'] = '1'

from pm_corpus import replay_corpus as RC  # noqa: E402
RC.install_isolation_light()
from prometheus import understanding as U  # noqa: E402
from prometheus import intent_classes as IC  # noqa: E402

OWN_ORDER = 'where is my Example Brand profile, I have not received it'
FEATURE = 'where is the avid fan option'
INSIGHT = "what's a unique insight we can share with Example Brand about their shoppers this month"
SEND = 'email me the Harbor Lights breakdown'
ORDER = 'run a profile on Harbor Lights'
TITLE_HIST = [{'role': 'user', 'text': 'how many people watched Example Show'},
              {'role': 'agent', 'text': 'On Example Show: 2,345,678 US people watched in the trailing year.'}]


def _clear_flags():
    for k in list(os.environ):
        if k.startswith('PM_U_OFF_') or k == 'PM_UNDERSTANDING':
            os.environ.pop(k, None)


class TheType(unittest.TestCase):
    def test_round_trip(self):
        u = U.Understanding(intent='status', subjects=[U.Subject('Example Brand', '', 'brand', 'subject')], named_title='',
                            action='status', scope=U.Scope(own_order=True), confidence=0.85, source='features', evidence=['status_fast'])
        d = u.to_dict()
        self.assertEqual(json.loads(json.dumps(d)), d)
        back = U.Understanding.from_dict(d)
        self.assertEqual(back.intent, 'status')
        self.assertTrue(back.scope.own_order)
        self.assertEqual(back.subjects[0].as_written, 'Example Brand')
        c = u.compact()
        self.assertLessEqual(len(c), 200)
        p = U.parse_compact(c)
        self.assertEqual((p['intent'], p['conf'], p['src'], p['act']), ('status', 0.85, 'features', 'status'))
        self.assertIn('own_order', p['scope'])
        self.assertFalse(p['flip'])
        self.assertNotIn('Example Brand', c)   # a subject never rides the log line; only a named title does

    def test_from_dict_tolerates_junk(self):
        self.assertIsNone(U.Understanding.from_dict('nope'))
        self.assertEqual(U.Understanding.from_dict({}).intent, 'analyze')


class FeaturesReading(unittest.TestCase):
    def setUp(self):
        _clear_flags()

    def test_feature_where_is_is_wayfinding(self):
        u = U.features(FEATURE, {'primary': {'name': 'Example Brand'}}, [], {})
        self.assertEqual(u.intent, 'wayfinding')
        self.assertTrue(u.scope.feature)
        self.assertFalse(u.scope.own_order)
        self.assertEqual(u.action, 'open')

    def test_own_order_where_is_is_status(self):
        u = U.features(OWN_ORDER, None, [], {})
        self.assertEqual(u.intent, 'status')
        self.assertTrue(u.scope.own_order)
        self.assertEqual(u.action, 'status')
        self.assertIn('wayfinding', u.evidence)      # both lanes wanted it; the scope settled it
        self.assertIn('status_fast', u.evidence)

    def test_insight_to_share_is_never_a_send(self):
        u = U.features(INSIGHT, None, [], {})
        self.assertEqual(u.intent, 'insight_ask')
        self.assertEqual(u.action, 'answer')
        u2 = U.features("can you email Example Brand a unique insight about their shoppers", None, [], {})
        self.assertEqual(u2.intent, 'insight_ask')

    def test_send_order_is_send_file(self):
        u = U.features(SEND, None, [], {})
        self.assertEqual((u.intent, u.action), ('send_file_order', 'send'))

    def test_explicit_order(self):
        u = U.features(ORDER, None, [], {}, is_order=True)
        self.assertEqual((u.intent, u.action), ('order', 'build'))
        self.assertTrue(any(e.startswith('product:') for e in u.evidence))
        self.assertEqual(U.features('make this a deck', {'primary': {'name': 'X'}}, [], {}).intent, 'deck')

    def test_no_build_never_builds(self):
        u = U.features(ORDER, None, [], {}, is_order=True, no_build=True)
        self.assertTrue(u.scope.no_build)
        self.assertNotEqual(u.action, 'build')

    def test_armed_step_wins(self):
        u = U.features(OWN_ORDER, None, [], {'extra': {'bpiq_wizard': {'step': 2}}})
        self.assertEqual(u.intent, 'armed')
        self.assertEqual(u.scope.armed_step, 'bpiq_wizard')
        self.assertEqual(IC.class_of_intent('armed'), 'wizard')

    def test_open_wizard_step_on_the_thread_is_armed(self):
        hist = [{'role': 'user', 'text': 'value the Example Brand x Northwind Retail partnership'},
                {'role': 'agent', 'text': 'Step 1 of 4. Which talent or title is the partner?', 'meta': {'bpiq_wizard': {'step': 1}}}]
        self.assertEqual(U.features('Example Talent', None, hist, {}).intent, 'armed')
        # a question typed while the step waits is a question, as the intake guard says
        self.assertNotEqual(U.features('how big is the Example Brand audience?', None, hist, {}).intent, 'armed')

    def test_cancel(self):
        self.assertEqual(U.features('cancel', None, [], {}).intent, 'cancel')

    def test_named_title_is_the_subject(self):
        u = U.features('the same for Harbor Lights', None, TITLE_HIST, {})
        self.assertEqual(u.named_title, 'Harbor Lights')
        self.assertEqual(u.intent, 'analyze')
        self.assertEqual(u.subjects[0].role, 'title')

    def test_small_talk_and_pricing(self):
        self.assertEqual(U.features('thanks!', None, TITLE_HIST, {}).intent, 'chat')
        self.assertEqual(U.features("no, that's not it", None, TITLE_HIST, {}).intent, 'pushback')
        d = U.features('the export button does nothing when I click it', {'primary': {'name': 'X'}}, [], {})
        self.assertEqual(d.intent, 'dashboard_issue')

    def test_empty_and_default(self):
        self.assertEqual(U.features('', None, [], {}).intent, 'analyze')
        u = U.features('who skews older, Example Brand or Northwind Brand?', None, [], {})
        self.assertEqual(u.intent, 'analyze')
        self.assertTrue(u.scope.people)
        self.assertEqual(u.source, 'features')
        self.assertTrue(u.text_key)


class AbsorbReading(unittest.TestCase):
    def test_confident_reading_refines_a_generic_intent(self):
        u = U.features('who skews older, Example Brand or Northwind Brand?', None, [], {})
        r = {'intent': 'compare', 'confidence': 0.9, 'subjects': [{'as_written': 'Example Brand', 'catalog': '', 'kind': 'brand'},
                                                                   {'as_written': 'Northwind Brand', 'catalog': '', 'kind': 'brand'}], 'window': '2025'}
        u2 = U.absorb_reading(u, r)
        self.assertEqual((u2.intent, u2.source), ('compare', 'reader'))
        self.assertEqual(len(u2.subjects), 2)
        self.assertEqual(u2.window, '2025')
        self.assertIn('reader:compare@0.90', u2.evidence)

    def test_low_confidence_reading_does_not_move_the_intent(self):
        u = U.features('who skews older, Example Brand or Northwind Brand?', None, [], {})
        u2 = U.absorb_reading(u, {'intent': 'build_profile', 'confidence': 0.5, 'subjects': []})
        self.assertEqual(u2.intent, 'analyze')

    def test_hard_rules_stand(self):
        u = U.features(ORDER, None, [], {}, is_order=True)
        u2 = U.absorb_reading(u, {'intent': 'capability', 'confidence': 0.95, 'subjects': []})
        self.assertEqual((u2.intent, u2.source), ('order', 'reconciled'))
        a = U.features(OWN_ORDER, None, [], {'extra': {'bpiq_wizard': {'step': 2}}})
        self.assertEqual(U.absorb_reading(a, {'intent': 'status', 'confidence': 0.95}).intent, 'armed')

    def test_contested_pairs_follow_the_words(self):
        w = U.features(FEATURE, {'primary': {'name': 'X'}}, [], {})
        self.assertEqual(U.absorb_reading(w, {'intent': 'status', 'confidence': 0.9}).intent, 'wayfinding')
        s = U.features(OWN_ORDER, None, [], {})
        self.assertEqual(U.absorb_reading(s, {'intent': 'status', 'confidence': 0.9}).intent, 'status')
        i = U.features(INSIGHT, None, [], {})
        self.assertEqual(U.absorb_reading(i, {'intent': 'command', 'confidence': 0.9}).intent, 'insight_ask')
        self.assertEqual(U.absorb_reading(i, {'intent': 'analyze', 'confidence': 0.9}).intent, 'insight_ask')   # a strong feature holds

    def test_no_build_blocks_a_build_reading(self):
        u = U.features('tell me about Harbor Lights viewers, do not build anything', None, [], {}, no_build=True)
        u2 = U.absorb_reading(u, {'intent': 'build_profile', 'confidence': 0.95})
        self.assertNotEqual(u2.intent, 'build_profile')
        self.assertNotEqual(u2.action, 'build')

    def test_junk_reading_is_ignored(self):
        u = U.features(FEATURE, None, [], {})
        self.assertEqual(U.absorb_reading(u, None).intent, u.intent)
        self.assertEqual(U.absorb_reading(u, {'intent': 'nonsense', 'confidence': 0.99}).intent, u.intent)


class Claims(unittest.TestCase):
    def setUp(self):
        _clear_flags()

    def _body(self, text, **kw):
        return U.attach({'text': text}, U.features(text, kw.get('ctx'), kw.get('history') or [], {}))

    def test_flipped_intents_are_the_four(self):
        for n in ('wayfinding', 'status', 'send_file_order', 'insight_ask'):
            self.assertTrue(U.flipped(n), n)
        self.assertTrue(U.flipped('named_title'))      # the last shadow intent moved (2026-11-20, night)
        self.assertEqual(U.SHADOW, ())
        self.assertFalse(U.flipped('analyze'))

    def test_agree_runs_and_marks(self):
        b = self._body(FEATURE, ctx={'primary': {'name': 'X'}})
        self.assertTrue(U.claims(b, 'wayfinding', lambda: True))
        self.assertTrue(U.on_body(b).flipped)

    def test_lane_predicate_still_required(self):
        b = self._body(FEATURE, ctx={'primary': {'name': 'X'}})
        self.assertFalse(U.claims(b, 'wayfinding', lambda: False))

    def test_contested_other_stands_down(self):
        b = self._body(OWN_ORDER)
        self.assertFalse(U.claims(b, 'wayfinding', lambda: True))   # wayfinding wanted it; the Understanding read status
        self.assertTrue(U.on_body(b).flipped)                         # a keyed decision, logged as one
        self.assertTrue(U.claims(b, 'status', lambda: True))

    def test_no_position_keeps_the_lane_predicate(self):
        b = self._body('who skews older, Example Brand or Northwind Brand?')
        self.assertEqual(U.on_body(b).intent, 'analyze')
        self.assertTrue(U.claims(b, 'status', lambda: True))
        self.assertFalse(U.claims(b, 'status', lambda: False))

    def test_insight_stands_the_send_lane_down(self):
        b = self._body(INSIGHT)
        self.assertFalse(U.claims(b, 'send_file_order', lambda: True))
        self.assertTrue(U.claims(b, 'insight_ask', lambda: True))
        s = self._body(SEND)
        self.assertTrue(U.claims(s, 'send_file_order', lambda: True))
        self.assertFalse(U.claims(s, 'insight_ask', lambda: True))

    def test_provenance_is_read_and_keyed(self):
        # where a figure came from, two figures squared, the count in the data, US or global (the four provenance
        # lanes): read as provenance, keyed; a brief that names titles is the read lane's whole (2026-11-20, night)
        hist = [{'role': 'agent', 'text': 'On Harbor Lights: 12,004,203 US viewers in the trailing year.'}]
        for q in ('where does the 12,004,203 come from?',
                  'your two counts for Harbor Lights were 4,287,316 and 7.1M, please reconcile them',
                  'are these numbers US only or global?',
                  'what is the unprojected count behind the 12,004,203?'):
            b = U.attach({'text': q}, U.features(q, None, hist, {}))
            u = U.on_body(b)
            self.assertEqual(u.intent, 'provenance', (q, u.evidence))
            self.assertTrue(any(str(e).startswith('provenance:') for e in u.evidence), q)
            self.assertTrue(U.claims(b, 'provenance', lambda: True), q)
            self.assertFalse(U.claims(b, 'status', lambda: True), q)        # a flipped lane that wanted it stands down
        self.assertTrue(U.flipped('provenance'))
        brief = ('Please size the fan audience for two novels in one run: Harbor Lights by Ann Example and The Quiet Shore by '
                 'Jo Sample, each calendar year from 2019 through 2026, split by source, with the unprojected panelist count '
                 'for each figure, and send it as a CSV.')
        ub = U.on_body(U.attach({'text': brief}, U.features(brief, None, [], {})))
        self.assertNotEqual(ub.intent, 'provenance', ub.evidence)

    def test_exists_is_read_and_keyed(self):
        # is X in the library / do we have a profile for X: the catalog answers from the shelf (2026-11-20, night)
        for q in ('do we have a profile for Harbor Lights?', 'is there a Harbor Lights profile in the library', 'do you have anything on Northwind Sports'):
            b = U.attach({'text': q}, U.features(q, None, [], {}))
            u = U.on_body(b)
            self.assertEqual(u.intent, 'exists', (q, u.evidence))
            self.assertTrue(U.claims(b, 'exists', lambda: True), q)
            self.assertFalse(U.claims(b, 'status', lambda: True), q)
        self.assertTrue(U.flipped('exists'))
        # a world question is not a shelf question
        w = U.on_body(U.attach({'text': 'is there any awareness for the film Harbor Lights'}, U.features('is there any awareness for the film Harbor Lights', None, [], {})))
        self.assertNotEqual(w.intent, 'exists')

    def test_definition_is_read_and_keyed(self):
        # what a term or a method means, the house sample: answered from the glossary and the thread (2026-11-20, night)
        for q in ('how big is your sample', 'what does Avid tier mean on this brief', 'give me the math behind how you would calculate a retention curve',
                  'can we discuss the exact pull first?'):
            b = U.attach({'text': q}, U.features(q, None, [], {}))
            u = U.on_body(b)
            self.assertEqual(u.intent, 'definition', (q, u.evidence))
            self.assertTrue(U.claims(b, 'definition', lambda: True), q)
            self.assertFalse(U.claims(b, 'status', lambda: True), q)
        self.assertTrue(U.flipped('definition'))

    def test_size_is_read_and_keyed(self):
        # how many people an audience holds, a joint demographic segment counted (2026-11-20, night)
        for q in ('how big is the Harbor Lights audience', 'how many people in your database are men aged 30 to 55 with income $50k to $200k in blue-collar jobs'):
            b = U.attach({'text': q}, U.features(q, None, [], {}))
            u = U.on_body(b)
            self.assertEqual(u.intent, 'size', (q, u.evidence))
            self.assertTrue(U.claims(b, 'size', lambda: True), q)
        self.assertTrue(U.flipped('size'))
        # the house sample is a definition, never a sizing
        self.assertEqual(U.on_body(U.attach({'text': 'how big is your sample'}, U.features('how big is your sample', None, [], {}))).intent, 'definition')

    def test_multi_ask_is_read_and_keyed(self):
        # several questions in one message, each answered through the door in turn; a status question inside it
        # does not take the whole message (2026-11-20, night)
        q = '1. is my Harbor Lights profile done? 2. where is the deck for it?'
        b = U.attach({'text': q}, U.features(q, None, [], {}))
        u = U.on_body(b)
        self.assertEqual(u.intent, 'multi_ask', u.evidence)
        self.assertTrue(U.claims(b, 'multi_ask', lambda: True))
        self.assertFalse(U.claims(b, 'status', lambda: True))
        self.assertTrue(U.flipped('multi_ask'))
        one = U.on_body(U.attach({'text': OWN_ORDER}, U.features(OWN_ORDER, None, [], {})))
        self.assertEqual(one.intent, 'status')

    def test_pricing_and_dashboard_issue_are_keyed(self):
        for name in ('pricing', 'dashboard_issue', 'status'):
            self.assertTrue(U.flipped(name), name)
        d = 'the profile dropdown is empty and the page will not load'
        b = U.attach({'text': d}, U.features(d, None, [], {}))
        if U.on_body(b).intent == 'dashboard_issue':
            self.assertTrue(U.claims(b, 'dashboard_issue', lambda: True))
            self.assertFalse(U.claims(b, 'wayfinding', lambda: True))

    def test_bare_replies_and_cancels_are_keyed(self):
        for q, want in (('yes', 'chat'), ('Approve', 'chat'), ('no, not that', 'pushback'), ('cancel', 'cancel'), ('thanks!', 'chat')):
            b = U.attach({'text': q}, U.features(q, None, [{'role': 'agent', 'text': 'On Harbor Lights: 1,284,113 US viewers.'}], {}))
            u = U.on_body(b)
            self.assertEqual(u.intent, want, (q, u.evidence))
            self.assertTrue(U.claims(b, want, lambda: True), q)
        for name in ('chat', 'pushback', 'cancel'):
            self.assertTrue(U.flipped(name), name)
        # a bare cancel with nothing armed is still the bare-reply gate's (cancel is keyed, not contested); a bare yes stands nothing down
        c = U.attach({'text': 'cancel'}, U.features('cancel', None, [], {}))
        self.assertTrue(U.claims(c, 'chat', lambda: True))
        self.assertTrue(U.claims(c, 'cancel', lambda: True))
        y = U.attach({'text': 'yes'}, U.features('yes', None, [], {}))
        self.assertTrue(U.claims(y, 'status', lambda: True))

    def test_page_questions_and_the_wizard_only_on_an_order(self):
        # 2026-11-23: a bare chip answer is a bare reply; a partnership QUESTION is never the valuation intake (the wizard
        # only on an explicit order, Jenna), while naming the product or a slot of it is; what a build carries reads
        # build_facts; "I already pulled the data" is the conversation continuing; a subject statement is logged as set
        self.assertEqual(U.features('Other', None, [], {}).intent, 'chat')
        q = 'Example Brand is partnering with Example Talent, I want to understand why'
        u = U.absorb_reading(U.features(q, None, [], {}), {'intent': 'valuation', 'confidence': 0.9, 'subjects': []})
        self.assertNotEqual(u.intent, 'valuation')
        slot = U.absorb_reading(U.features('the end step is bought a new drink at Example Diner in the past 6 months', None, [], {}),
                                {'intent': 'journey', 'confidence': 0.82, 'subjects': []})
        self.assertEqual(slot.intent, 'journey')
        self.assertEqual(U.features('was the profile run on all 49 of these inputs or did you just add them to the brand field?', None, [], {}).intent, 'build_facts')
        self.assertTrue(U.flipped('build_facts'))
        hist = [{'role': 'agent', 'text': 'Do you want this on Example Brand (open on your screen)?'}]
        self.assertEqual(U.features('what? I already pulled the data', None, hist, {}).intent, 'continuation_correction')
        self.assertEqual(U.features('the subject is Example University', None, [], {}).intent, 'order')
        long_brief = ('Example Show: what is driving the Season 6 lift versus Season 5. GROUND RULES for this query. Panel: the 10M '
                      'census-matched Gen Pop panel. Report US people who streamed a new episode in the first 35 days, by season, '
                      'with the share who never played the show before, the top five services, and the week by week curve for both seasons.')
        self.assertNotEqual(U.features(long_brief, None, [], {}).intent, 'definition')

    def test_page_questions_and_the_wizard_only_on_an_order(self):
        # 2026-11-23: a bare chip answer is a bare reply; a partnership QUESTION is never the valuation intake (the wizard
        # only on an explicit order, Jenna), while naming the product or a slot of it is; what a build carries reads
        # build_facts; "I already pulled the data" is the conversation continuing; a subject statement is logged as set
        self.assertEqual(U.features('Other', None, [], {}).intent, 'chat')
        q = 'Example Brand is partnering with Example Talent, I want to understand why'
        u = U.absorb_reading(U.features(q, None, [], {}), {'intent': 'valuation', 'confidence': 0.9, 'subjects': []})
        self.assertNotEqual(u.intent, 'valuation')
        slot = U.absorb_reading(U.features('the end step is bought a new drink at Example Diner in the past 6 months', None, [], {}),
                                {'intent': 'journey', 'confidence': 0.82, 'subjects': []})
        self.assertEqual(slot.intent, 'journey')
        self.assertEqual(U.features('was the profile run on all 49 of these inputs or did you just add them to the brand field?', None, [], {}).intent, 'build_facts')
        self.assertTrue(U.flipped('build_facts'))
        hist = [{'role': 'agent', 'text': 'Do you want this on Example Brand (open on your screen)?'}]
        self.assertEqual(U.features('what? I already pulled the data', None, hist, {}).intent, 'continuation_correction')
        self.assertEqual(U.features('the subject is Example University', None, [], {}).intent, 'order')
        long_brief = ('Example Show: what is driving the Season 6 lift versus Season 5. GROUND RULES for this query. Panel: the 10M '
                      'census-matched Gen Pop panel. Report US people who streamed a new episode in the first 35 days, by season, '
                      'with the share who never played the show before, the top five services, and the week by week curve for both seasons.')
        self.assertNotEqual(U.features(long_brief, None, [], {}).intent, 'definition')

    def test_a_weak_reading_never_stands_a_lane_down(self):
        b = self._body('no, that is not what I asked for, run the window again', history=[{'role': 'agent', 'text': 'On X: 1.'}])
        u = U.on_body(b)
        if u.intent in U._CONTESTED and u.confidence < U.STAND_DOWN_CONF:
            self.assertTrue(U.claims(b, 'status', lambda: True))
        u.intent, u.confidence = 'provenance', 0.5
        b2 = U.attach({'text': b['text']}, u)
        self.assertTrue(U.claims(b2, 'status', lambda: True))   # below the floor: no position taken

    def test_rewritten_words_take_no_position(self):
        # a lane rewrote the text after the reading (a typed option, the turn gate's re-bind): the lane's own
        # predicate decides again, the stale reading neither claims nor stands anything down
        b = self._body(OWN_ORDER)
        self.assertFalse(U.claims(b, 'wayfinding', lambda: True))
        b2 = dict(b, text='Email me the Harbor Lights file')
        self.assertTrue(U.claims(b2, 'wayfinding', lambda: True))
        self.assertTrue(U.on_body(b).text_key)

    def test_kill_switches_restore_the_lane_walk(self):
        b = self._body(OWN_ORDER)
        os.environ['PM_U_OFF_WAYFINDING'] = '1'
        try:
            self.assertTrue(U.claims(b, 'wayfinding', lambda: True))   # the lane's own predicate decides again
        finally:
            os.environ.pop('PM_U_OFF_WAYFINDING', None)
        os.environ['PM_UNDERSTANDING'] = '0'
        try:
            self.assertFalse(U.enabled())
            self.assertTrue(U.claims(b, 'wayfinding', lambda: True))
        finally:
            os.environ.pop('PM_UNDERSTANDING', None)

    def test_no_understanding_on_body(self):
        self.assertTrue(U.claims({'text': 'x'}, 'wayfinding', lambda: True))
        self.assertFalse(U.claims({'text': 'x'}, 'wayfinding', lambda: False))
        self.assertFalse(U.claims({'text': 'x'}, 'wayfinding', lambda: 1 / 0))   # a predicate that raises is False


class AskLog(unittest.TestCase):
    class _G:
        pass

    def test_stamp_log_carries_reading_and_understanding(self):
        g = self._G()
        g._pm_reading = {'intent': 'size', 'confidence': 0.8, 'subjects': [], 'flags': [], 'restate': 'x', 'ms': 12, 'extra_key': 1}
        g._pm_understanding = U.features(OWN_ORDER, None, [], {}).to_dict()
        extra = U.stamp_log({'result': 'answered'}, g)
        self.assertEqual(extra['result'], 'answered')
        self.assertEqual(set(extra['reading']), {'intent', 'confidence', 'subjects', 'flags', 'restate', 'ms'})
        self.assertTrue(extra['u'].startswith('intent=status;'))
        self.assertEqual(U.stamp_log(None, self._G()), {})

    def test_grade_log_rows(self):
        rows = [{'route': 'status_lookup', 'outcome': 'answered', 'extra': {'u': 'intent=status;conf=0.85;src=features'}},
                {'route': 'wayfinding', 'outcome': 'answered', 'extra': {'u': 'intent=status;conf=0.75;src=features'}},
                {'route': 'reasoned_metrics', 'outcome': 'landed', 'extra': {'reading': 'x'}}]
        g = U.grade_log_rows(rows)
        self.assertEqual((g['n'], g['agree']), (2, 1))
        self.assertEqual(g['by_class']['status'], {'n': 1, 'agree': 1})
        self.assertEqual(g['disagree'][0]['intent'], 'status')

    def test_agrees_reads_the_one_table(self):
        self.assertTrue(U.agrees('intent=wayfinding;conf=0.85', 'wayfinding', 'answered'))
        self.assertFalse(U.agrees('intent=wayfinding;conf=0.85', 'status_lookup', 'answered'))
        self.assertTrue(U.agrees({'intent': 'insight_ask'}, 'page_analysis', 'answered'))


class ThroughTheDoor(unittest.TestCase):
    """service.ask on the stub host, the cores stubbed: the lanes' decisions."""

    @classmethod
    def setUpClass(cls):
        _clear_flags()
        cls.stub = RC._Stub()
        cls.stub.bind()

    def _ask(self, text, reading=None, view='', subject=None, history=None):
        return RC._service_once(self.stub, {'id': 'x', 'question': text, 'view': view, 'surface': 'analyze', 'subject': subject,
                                            'history': list(history or []), 'reading': reading})

    def test_own_order_lands_on_the_status_lane(self):
        live = self._ask(OWN_ORDER, reading={'intent': 'status', 'confidence': 0.9, 'subjects': [{'as_written': 'Example Brand', 'catalog': '', 'kind': 'brand'}]})
        self.assertEqual(live['route'], 'status_lookup', live['reply'][:200])
        self.assertIn('intent=status', live['u'])
        self.assertNotIn('SELECT PRODUCT', live['reply'])

    def test_own_order_without_a_reading_still_lands_on_the_readers_own_work(self):
        # with no reading the Understanding's features alone stand wayfinding down; the ask names a profile, so the
        # deliverable lookup answers about that profile (the name clean, the delivery clause left off)
        live = self._ask(OWN_ORDER)
        self.assertIn(live['route'], ('status_lookup', 'deliverable_lookup'), live['reply'][:200])
        self.assertNotIn('SELECT PRODUCT', live['reply'])
        self.assertNotIn('I have not received', live['reply'])
        self.assertIn('Example Brand', live['reply'])

    def test_feature_where_is_stays_wayfinding(self):
        live = self._ask(FEATURE, view='profileIQ', subject='Example Brand')
        self.assertEqual(live['route'], 'wayfinding')
        self.assertIn('flip=1', live['u'])

    def test_a_named_audience_is_never_the_roster(self):
        # "from the <audience>, which brands under-index" names its audience: a read on it, never the roster offer
        # (a named subject wins; the exists flip, 2026-11-20)
        q = 'from the Example Sports viewers which brands that are outdoor and workwear under-index'
        live = self._ask(q)
        self.assertNotEqual(live['route'], 'roster_offer', live['reply'][:200])
        self.assertNotIn('roster', live['reply'].lower())
        bare = self._ask('which brands do you cover')
        self.assertEqual(bare['route'], 'roster_offer', bare['reply'][:200])     # a roster ask that names nothing is the catalog's
        self.assertIn('intent=exists', bare['u'])

    def test_insight_ask_never_reads_as_a_send(self):
        live = self._ask(INSIGHT, reading={'intent': 'command', 'confidence': 0.9, 'subjects': [{'as_written': 'Example Brand', 'catalog': '', 'kind': 'brand'}]})
        self.assertNotEqual(live['route'], 'email_file')
        self.assertNotIn('Nothing has landed', live['reply'])
        self.assertIn('intent=insight_ask', live['u'])

    def test_send_order_still_sends(self):
        live = self._ask(SEND, reading={'intent': 'command', 'confidence': 0.9, 'subjects': [{'as_written': 'Harbor Lights', 'catalog': '', 'kind': 'title'}]})
        self.assertEqual(live['route'], 'email_file')

    def test_kill_switch_brings_the_old_path_back(self):
        os.environ['PM_U_OFF_WAYFINDING'] = '1'
        try:
            live = self._ask(OWN_ORDER, reading={'intent': 'status', 'confidence': 0.9, 'subjects': []})
            self.assertEqual(live['route'], 'wayfinding', live['reply'][:120])   # the lane order again: wayfinding first
        finally:
            os.environ.pop('PM_U_OFF_WAYFINDING', None)

    def test_master_switch_off_logs_nothing_and_changes_nothing(self):
        os.environ['PM_UNDERSTANDING'] = '0'
        try:
            live = self._ask(OWN_ORDER)
            self.assertEqual(live['route'], 'wayfinding')
            self.assertEqual(live['u'], '')
        finally:
            os.environ.pop('PM_UNDERSTANDING', None)

    def test_every_fixture_carries_an_understanding(self):
        for c in RC.load_fixtures():
            if c['recorded']['route'] == 'data_floor':
                continue   # answered ahead of the Understanding, by the gate
            live = RC._service_once(self.stub, c)
            self.assertTrue(live['u'], c['id'])


class ChatPyUnchanged(unittest.TestCase):
    def test_line_ceiling_and_the_one_stamp(self):
        # the legacy chat text as the suites read it: chat.py with every extracted block spliced back
        # (stage 5); the ratchet counts the legacy code, chat.py plus the cores still beside it (stage 7)
        sys.path.insert(0, HERE)
        import _pm_legacy_view as _lv
        src = _lv.legacy_view()
        self.assertLessEqual(_lv.legacy_lines(), _lv.LEGACY_CEILING)
        # stage 2 touched the legacy chat text in one place, the stamp on the ask log; the read
        # core's subject hint (read_subject_hint, 2026-11-2x) is the second site in the whole text
        self.assertEqual(src.count("prometheus.understanding"), 2)
        self.assertEqual(src.count("__import__('prometheus.understanding', fromlist=['stamp_log'])"), 1)
        self.assertEqual(src.count("__import__('prometheus.understanding', fromlist=['read_subject_hint'])"), 1)


if __name__ == '__main__':
    unittest.main(verbosity=1)
