#!/usr/bin/env python3
"""A quote names the subject it will read (Jenna 2026-11-13).

Samantha Kolton (CAA), 2026-11-04, thread ae9b17d7c2:

    "what IP (content, film, toys, characters) are ram's fans very
     interested in?"
    -> "I can pull that read on Ram's Fans. Pulling this data will cost $100."
    -> Approve -> a read on Ram Trucks fans.
    "This is actually for LA rams (the football team) - not ram's trucks ..."

The quote printed her words back; which Ram the read would be on was
decided after she approved.

Held here:

  TheRule        a name that is two things is asked about, each named;
                 a name that places cleanly is quoted under its plain
                 name; a settled subject is left alone
  AtTheGate      her words at the one place a quote is made: the
                 question first, no price, no charge, nothing started,
                 for a paying seat and for a covered one; after the
                 pick the quote names the subject and the price
  ThePick        the which-one confirm resolves a chip, "the football
                 team", "LA rams", "the trucks", "the second one"
  NothingExtra   a clean subject gets no question and no model call; a
                 test never spends the call; the kill switch

Hermetic: the stub host of test_pm_included_with_plan. No model, no S3.
"""
from __future__ import annotations

import importlib.util
import os
import re
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

_spec = importlib.util.spec_from_file_location('pm_iwp_for_quote_subject', os.path.join(HERE, 'test_pm_included_with_plan.py'))
IWP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(IWP)

from prometheus import priced_read as PR  # noqa: E402
from prometheus import quote_subject as QS  # noqa: E402
from prometheus import service as S  # noqa: E402

HER = "what IP (content, film, toys, characters) are ram's fans very interested in?"
TYPED = "Ram's Fans"
TWO = {'ambiguous': True, 'name': '', 'what': '',
       'candidates': [{'name': 'Ram Trucks', 'what': 'the pickup truck brand'},
                      {'name': 'Los Angeles Rams', 'what': 'the NFL team'}]}
TRUCKS = {'ambiguous': False, 'name': 'Ram Trucks', 'what': 'the pickup truck brand', 'candidates': []}
PRICE = re.compile(r"[$\u00a3]\s?\d")
CONFIRM_Q = S._CONFIRM_Q_RX


def _resolver(answer, calls=None):
    def fn(name, text, **kw):
        if calls is not None:
            calls.append((name, text))
        return answer
    return fn


def _which_turn(payload):
    """The stored agent turn a which-one question becomes."""
    return {'role': 'agent', 'text': payload['reply'],
            'meta': {'memory_confirm': payload['memory_confirm'],
                     'options': [{'label': c, 'send': c} for c in payload['followups']]}}


# ============================================================================ 1
class TheRule(unittest.TestCase):
    def test_the_name_and_the_audience_word(self):
        self.assertEqual(QS.split("Ram's Fans"), ("Ram's", 'Fans'))
        self.assertEqual(QS.split('Ram Trucks'), ('Ram Trucks', ''))
        self.assertEqual(QS.split('Gilmore Girls viewers'), ('Gilmore Girls', 'viewers'))

    def test_a_name_that_is_two_things_is_asked_about(self):
        subj, ask = QS.settle(HER, TYPED, [], resolve=_resolver(TWO))
        self.assertEqual(subj, TYPED)
        self.assertTrue(ask)
        self.assertEqual(ask['reply'], 'Do you mean Ram Trucks (the pickup truck brand), or Los Angeles Rams (the NFL team)?')
        self.assertTrue(CONFIRM_Q.match(ask['reply']))                       # the which-one confirm the service resolves
        self.assertEqual(ask['followups'], ['Ram Trucks', 'Los Angeles Rams', 'Something else'])
        self.assertFalse(PRICE.search(ask['reply']))
        self.assertNotIn('panel_offer', ask)
        mc = ask['memory_confirm']
        self.assertEqual(mc['question'], HER)
        self.assertEqual([(o['label'], o['subject']) for o in mc['options']],
                         [('Ram Trucks', 'Ram Trucks Fans'), ('Los Angeles Rams', 'Los Angeles Rams Fans')])

    def test_three_candidates_are_all_named(self):
        three = {'ambiguous': True, 'name': '', 'candidates': [
            {'name': 'Jaguar Cars', 'what': 'the car maker'}, {'name': 'Jacksonville Jaguars', 'what': 'the NFL team'},
            {'name': 'Atari Jaguar', 'what': 'the game console'}]}
        _s, ask = QS.settle('what do jaguar fans buy?', 'Jaguar fans', [], resolve=_resolver(three))
        self.assertEqual(ask['reply'], 'Do you mean Jaguar Cars (the car maker), Jacksonville Jaguars (the NFL team), '
                                       'or Atari Jaguar (the game console)?')
        self.assertTrue(CONFIRM_Q.match(ask['reply']))
        self.assertEqual(len(ask['memory_confirm']['options']), 3)

    def test_two_things_that_go_by_one_name_are_told_apart_on_the_chips(self):
        dm = {'ambiguous': True, 'name': '', 'candidates': [
            {'name': 'Dark Matter (Pearl Jam)', 'what': 'the Pearl Jam album'},
            {'name': 'Dark Matter', 'what': 'the Apple TV+ series'}]}
        _s, ask = QS.settle('how many people listened to Dark Matter', 'Dark Matter', [], resolve=_resolver(dm))
        self.assertEqual(ask['reply'], 'Do you mean Dark Matter (the Pearl Jam album), or Dark Matter (the Apple TV+ series)?')
        self.assertTrue(CONFIRM_Q.match(ask['reply']))
        self.assertEqual(ask['followups'], ['Dark Matter (Pearl Jam album)', 'Dark Matter (Apple TV+ series)', 'Something else'])
        hist = [{'role': 'user', 'text': 'how many people listened to Dark Matter'}, _which_turn(ask)]
        self.assertEqual(S._confirm_answer('the album', hist)['subject'], 'Dark Matter (Pearl Jam album)')
        self.assertEqual(S._confirm_answer('Dark Matter (Apple TV+ series)', hist)['subject'], 'Dark Matter (Apple TV+ series)')
        # and the picked subject is never asked about again
        calls = []
        self.assertEqual(QS.settle('how many people listened to Dark Matter', 'Dark Matter (Pearl Jam album)', hist,
                                   resolve=_resolver(dm, calls)), ('Dark Matter (Pearl Jam album)', None))
        self.assertEqual(calls, [])

    def test_a_name_that_places_cleanly_is_quoted_under_its_plain_name(self):
        subj, ask = QS.settle("what do ram's fans tow with their trucks?", TYPED, [], resolve=_resolver(TRUCKS))
        self.assertIsNone(ask)
        self.assertEqual(subj, 'Ram Trucks Fans')

    def test_one_thing_under_two_spellings_is_one_thing(self):
        same = {'ambiguous': True, 'name': '', 'candidates': [{'name': 'Ram Trucks', 'what': 'the truck brand'},
                                                               {'name': 'RAM trucks', 'what': 'pickup trucks'}]}
        subj, ask = QS.settle(HER, TYPED, [], resolve=_resolver(same))
        self.assertIsNone(ask)
        self.assertEqual(subj, 'Ram Trucks Fans')

    def test_a_placed_name_that_shares_nothing_with_what_was_typed_is_not_used(self):
        off = {'ambiguous': False, 'name': 'Stellantis', 'what': 'the car group', 'candidates': []}
        self.assertEqual(QS.settle(HER, TYPED, [], resolve=_resolver(off)), (TYPED, None))
        off2 = {'ambiguous': True, 'name': '', 'candidates': [{'name': 'Dodge', 'what': 'a'}, {'name': 'Jeep', 'what': 'b'}]}
        self.assertEqual(QS.settle(HER, TYPED, [], resolve=_resolver(off2)), (TYPED, None))

    def test_a_name_that_cannot_be_placed_is_quoted_as_typed(self):
        for got in (None, {}, {'ambiguous': False, 'name': '', 'what': '', 'candidates': []}, 'not a dict'):
            self.assertEqual(QS.settle(HER, TYPED, [], resolve=_resolver(got)), (TYPED, None))
        self.assertEqual(QS.settle(HER, TYPED, [], resolve=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down'))), (TYPED, None))

    def test_the_resolver_reads_strict_json_and_nothing_else(self):
        out = QS.resolve_name("Ram's", HER, reason=lambda s_, u_: 'Here you go: ' + __import__('json').dumps(TWO))
        self.assertTrue(out['ambiguous'])
        self.assertEqual([c['name'] for c in out['candidates']], ['Ram Trucks', 'Los Angeles Rams'])
        self.assertIsNone(QS.resolve_name("Ram's", HER, reason=lambda s_, u_: 'I think it is the truck.'))
        self.assertIsNone(QS.resolve_name("Ram's", HER, reason=lambda s_, u_: ''))


# ============================================================================ 2
class NothingExtra(unittest.TestCase):
    def test_a_settled_subject_gets_no_question_and_no_call(self):
        calls = []
        r = _resolver(TWO, calls)
        which = _which_turn(QS.ask_payload(HER, TWO['candidates'], 'Fans'))
        cases = (
            # (text, subject, history, kwargs)
            ('how do Etsy and Architectural Digest overlap?', 'Etsy and Architectural Digest', [], {}),     # several names
            ('rank brands for Target, Walmart shoppers', 'Target, Walmart shoppers', [], {}),
            ('what do people who watched the walter boys on netflix buy', 'My Life with the Walter Boys Viewers on Netflix', [], {}),
            ('what do they buy', 'everyone in the US', [], {}),
            (HER, TYPED, [], {'panel_confirm': {'kind': 'custom', 'subject': TYPED}}),                     # an accepted quote
            (HER, 'Los Angeles Rams Fans', [{'role': 'user', 'text': HER}, which], {}),                    # the reader just picked it
            (HER, 'Ram Trucks', [{'role': 'user', 'text': HER}, which], {}),
            ('what do they watch', 'Paige Spiranac', [], {'page': 'Paige Spiranac'}),                      # the open profile
        )
        for text, subj, hist, kw in cases:
            self.assertEqual(QS.settle(text, subj, hist, resolve=r, **kw), (subj, None), subj)
        self.assertEqual(calls, [])

    def test_a_subject_the_dashboard_holds_is_left_alone(self):
        calls = []
        with mock.patch.object(QS, '_on_record', lambda name, ctx=None: 'Gilmore Girls' if name == 'Gilmore Girls' else ''):
            self.assertEqual(QS.settle('top QSRs for Gilmore Girls viewers', 'Gilmore Girls viewers', [], resolve=_resolver(TWO, calls)),
                             ('Gilmore Girls viewers', None))
        self.assertEqual(calls, [])

    def test_the_threads_own_subject_is_left_alone(self):
        calls = []
        hist = [{'role': 'user', 'text': 'what do Lakers fans buy'},
                {'role': 'agent', 'text': 'On it: reading Lakers Fans now.', 'meta': {'subject': 'Lakers Fans', 'read_job_id': 'j1'}}]
        self.assertEqual(QS.settle('and what do lakers fans watch?', 'Lakers Fans', hist, resolve=_resolver(TWO, calls)),
                         ('Lakers Fans', None))
        self.assertEqual(calls, [])

    def test_a_test_or_a_replay_never_spends_the_call(self):
        self.assertTrue(os.environ.get('REGRESSION_TEST_MODE'))
        self.assertIsNone(QS.resolve_name("Ram's", HER))
        self.assertEqual(QS.settle(HER, TYPED, []), (TYPED, None))

    def test_kill_switch(self):
        old = os.environ.get('PM_QUOTE_SUBJECT')
        os.environ['PM_QUOTE_SUBJECT'] = '0'
        try:
            self.assertEqual(QS.settle(HER, TYPED, [], resolve=_resolver(TWO)), (TYPED, None))
        finally:
            if old is None:
                os.environ.pop('PM_QUOTE_SUBJECT', None)
            else:
                os.environ['PM_QUOTE_SUBJECT'] = old


# ============================================================================ 3
class AtTheGate(unittest.TestCase):
    """priced_read.offer_or_charge, the one place a quote is made."""

    @classmethod
    def setUpClass(cls):
        cls.calls = []
        IWP._bind_host(cls.calls)

    def setUp(self):
        IWP.CHARGES.clear()

    def _gate(self, name, subject=TYPED, history=None, panel_confirm=None, kind='report'):
        return PR.offer_or_charge(text=HER, subject=subject, kind=kind, panel_confirm=panel_confirm, username=name,
                                  user=IWP._user(name), history=history or [], consume=IWP._consume)

    def test_her_words_are_asked_about_before_any_price(self):
        with mock.patch.object(QS, 'resolve_name', _resolver(TWO)):
            state, payload = self._gate('payer')
        self.assertEqual(state, 'offer')
        self.assertEqual(payload['reply'], 'Do you mean Ram Trucks (the pickup truck brand), or Los Angeles Rams (the NFL team)?')
        self.assertFalse(PRICE.search(payload['reply']))
        self.assertNotIn('panel_offer', payload)                              # nothing to approve yet
        self.assertNotIn(PR.APPROVE_CHIP, payload['followups'])
        self.assertTrue(payload.get('memory_confirm'))
        self.assertEqual(IWP.CHARGES, [])

    def test_a_covered_seat_is_asked_too_and_nothing_starts(self):
        """No offer step on an unlimited seat: before this the read simply started on whichever Ram."""
        for name in ('unlim', 'flagged', 'pooled', 'jenna'):
            with mock.patch.object(QS, 'resolve_name', _resolver(TWO)):
                state, payload = self._gate(name)
            self.assertEqual(state, 'offer', name)
            self.assertTrue(payload.get('subject_ambiguous'), name)
            self.assertNotIn('included', payload)
        self.assertEqual(IWP.CHARGES, [])

    def test_after_the_pick_the_quote_names_the_subject_and_the_price(self):
        with mock.patch.object(QS, 'resolve_name', _resolver(TWO)):
            _state, which = self._gate('payer')
        hist = [{'role': 'user', 'text': HER}, _which_turn(which), {'role': 'user', 'text': 'Los Angeles Rams'}]
        calls = []
        with mock.patch.object(QS, 'resolve_name', _resolver(TWO, calls)):
            state, payload = self._gate('payer', subject='Los Angeles Rams Fans', history=hist)
        self.assertEqual(calls, [])                                           # never asked twice
        self.assertEqual(state, 'offer')
        self.assertIn('Los Angeles Rams Fans', payload['reply'])
        self.assertNotIn("Ram's", payload['reply'])
        self.assertTrue(payload['reply'].endswith(PR.COST_LINE.format(price='$100')))
        self.assertEqual(payload['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP])
        self.assertEqual(payload['panel_offer']['subject'], 'Los Angeles Rams Fans')   # what Approve runs on
        self.assertEqual(payload['subject'], 'Los Angeles Rams Fans')
        self.assertEqual(IWP.CHARGES, [])
        # Approve: the charge lands once, on the subject that was quoted
        state2, paid = self._gate('payer', subject='Los Angeles Rams Fans', history=hist,
                                  panel_confirm={'kind': 'report', 'subject': 'Los Angeles Rams Fans'})
        self.assertEqual(state2, 'charged')
        self.assertEqual(paid['subject'], 'Los Angeles Rams Fans')
        self.assertEqual(len(IWP.CHARGES), 1)

    def test_a_clean_name_is_quoted_under_its_plain_name(self):
        with mock.patch.object(QS, 'resolve_name', _resolver(TRUCKS)):
            state, payload = self._gate('payer')
        self.assertEqual(state, 'offer')
        self.assertIn('Ram Trucks Fans', payload['reply'])
        self.assertNotIn("Ram's", payload['reply'])
        self.assertTrue(PRICE.search(payload['reply']))
        self.assertEqual(payload['panel_offer']['subject'], 'Ram Trucks Fans')
        # and a covered seat's read starts on that same name
        with mock.patch.object(QS, 'resolve_name', _resolver(TRUCKS)):
            state, paid = self._gate('unlim')
        self.assertEqual(state, 'charged')
        self.assertEqual(paid['subject'], 'Ram Trucks Fans')

    def test_with_nothing_placed_the_quote_reads_as_before(self):
        state, payload = self._gate('payer')                                  # no model in a test: as typed
        self.assertEqual(state, 'offer')
        self.assertIn("Ram's Fans", payload['reply'])
        self.assertTrue(payload.get('panel_offer'))

    def test_a_breakdown_of_a_delivered_figure_is_never_asked_about(self):
        calls = []
        with mock.patch.object(QS, 'resolve_name', _resolver(TWO, calls)):
            state, _payload = self._gate('payer', kind='derived')
        self.assertEqual(calls, [])
        self.assertEqual(state, 'offer')

    def test_the_work_gate_binds_the_read_to_the_settled_subject(self):
        with open(os.path.join(ROOT, "prometheus", "priced_read.py")) as fh:
            src = fh.read()
        gate = src[src.index('def work_gate('):]
        self.assertIn("(payload or {}).get('subject') or subject", gate)
        with open(os.path.join(ROOT, "prometheus", "legacy", "chat.py")) as fh:
            chat = fh.read()
        self.assertEqual(chat.count("subj_name = str(_pl.get('subject') or subj_name)"), 2)


# ============================================================================ 4
class ThePick(unittest.TestCase):
    def setUp(self):
        which = QS.ask_payload(HER, TWO['candidates'], 'Fans')
        self.hist = [{'role': 'user', 'text': HER}, _which_turn(which)]

    def _pick(self, words):
        return S._confirm_answer(words, self.hist)

    def test_a_chip(self):
        for words, subject in (('Los Angeles Rams', 'Los Angeles Rams Fans'), ('Ram Trucks', 'Ram Trucks Fans'),
                               ('ram trucks', 'Ram Trucks Fans')):
            ca = self._pick(words)
            self.assertTrue(ca, words)
            self.assertEqual((ca['question'], ca['subject']), (HER, subject))

    def test_her_own_way_of_saying_it(self):
        for words, subject in (('the football team', 'Los Angeles Rams Fans'),
                               ('LA rams', 'Los Angeles Rams Fans'),
                               ('LA rams (the football team)', 'Los Angeles Rams Fans'),
                               ('the NFL team', 'Los Angeles Rams Fans'),
                               ('the trucks', 'Ram Trucks Fans'),
                               ('the truck brand', 'Ram Trucks Fans'),
                               ('the second one', 'Los Angeles Rams Fans'),
                               ('the first', 'Ram Trucks Fans')):
            ca = self._pick(words)
            self.assertTrue(ca, words)
            self.assertEqual(ca['subject'], subject, words)
            self.assertEqual(ca['question'], HER)

    def test_a_reply_that_points_at_neither_picks_nothing(self):
        for words in ('rams', 'hmm', 'what does that cost?', 'Something else'):
            self.assertIsNone(self._pick(words), words)

    def test_words_never_pick_on_a_confirm_that_describes_nothing(self):
        """The older which-one confirms carry no descriptions: their matching is unchanged."""
        hist = [{'role': 'user', 'text': 'how big is will and grace'},
                {'role': 'agent', 'text': 'Do you mean Will And Grace, or Will & Grace on Hulu?',
                 'meta': {'memory_confirm': {'question': 'how big is will and grace',
                                             'options': [{'label': 'Will And Grace', 'subject': 'Will And Grace'},
                                                         {'label': 'Will & Grace on Hulu', 'subject': 'Will & Grace on Hulu'}]}}}]
        self.assertIsNone(S._confirm_answer('hulu please', hist))
        self.assertEqual(S._confirm_answer('the Hulu one', hist)['subject'], 'Will & Grace on Hulu')
        self.assertEqual(S._confirm_answer('both', hist)['subject'], 'Will And Grace')


if __name__ == '__main__':
    unittest.main(verbosity=1)
