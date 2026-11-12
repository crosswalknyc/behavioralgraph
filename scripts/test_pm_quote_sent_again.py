#!/usr/bin/env python3
"""A quoted question sent again, and a quote the reader turned down
(Jenna 2026-11-12).

The standing rule: a question that was quoted and not approved, sent
again, gets the same quote at the same price, with no new run, no second
quote stacked on it and no charge. The reader has not rejected anything.

A repeat after the reader said no ("No, run what I asked", "not that",
"cancel") is the rejected case: it is never routed there again in that
thread.

What was wrong:
  * a question in three parts, sent twice, started its free part a second
    time and re-priced its third part between the two sends ($200 in all
    became $100). The second send ran the lanes again to make the quote.
    The quote that stands is now handed back as the thread stored it;
  * "No, run what I asked" straight after a $100 quote drew a second
    quote in other words on prod ("I can pull that read on Landman
    viewers. Pulling this data will cost $100."), and the question sent
    again after a no drew the first quote again;
  * a quote sent again was answered "Same read as earlier in this thread"
    over a price line when the stored turn kept no offer;
  * the reader approving a quote was logged and banked as a repeat: the
    Approve choice sends the question again with the offer. 26 of the 35
    "repeat after a quote" cases banked by 2026-11-12 were approvals
    (Grace, 2026-11-08, her $100 Hazbin Hotel read among them).

Hermetic: pure functions over thread turns and ask-log rows. No S3, no
model, no app.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import declined_quote as DQ  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus import user_signal as US  # noqa: E402

Q = 'What did Landman viewers watch next?'
QUOTE = 'I can pull what Landman viewers did next. Pulling this data will cost $100.'
OFFER = {'question': Q, 'subject': 'Landman viewers', 'kind': 'path', 'price_label': '$100'}
MULTI = ("3 asks here, quoted together and run together; 2 carry a price:\n\n"
         "1. The share of Jason Statham film viewers who did not watch Prime Video. Running now, no charge:\n"
         "On it: reading Jason Statham Films now. This one takes a real look at the data, so give me a moment.\n\n"
         "2. Which segments watched action thrillers on Netflix or YouTube but rarely Prime Video. $100.\n\n"
         "3. I can pull that read on Extraction. $100.\n\n"
         "Approve runs 2 and 3 for $200 in all; Cancel runs none of them.")
MULTI_REPRICED = MULTI.replace('2 carry a price', '1 carries a price').replace(
    '3. I can pull that read on Extraction. $100.', '3. Answered from what we hold, no charge:\nExtraction leads.').replace(
    'Approve runs 2 and 3 for $200 in all', 'Approve runs 2 for $100 in all')


def iso(seconds_ago):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')


def user(text, ago=30):
    return {'role': 'user', 'text': text, 'ts': iso(ago)}


def agent(text, ago=25, **meta):
    turn = {'role': 'agent', 'text': text, 'ts': iso(ago)}
    if meta:
        turn['meta'] = meta
    return turn


def quoted(ago=50):
    return [user(Q, ago), agent(QUOTE, ago - 5, panel_offer=dict(OFFER), options=['Approve', 'Cancel'])]


class AQuotedQuestionSentAgain(unittest.TestCase):
    """Not approved, not turned down: the same quote, handed back as stored."""

    def test_the_stored_quote_stands(self):
        turn = DQ.standing(Q, quoted())
        self.assertIsNotNone(turn)
        raw = DQ.stored_raw(turn)
        self.assertEqual(raw['reply'], QUOTE)                    # the same words, so the same price
        self.assertEqual(raw['panel_offer'], OFFER)              # the same offer behind Approve
        self.assertEqual(raw['followups'], ['Approve', 'Cancel'])
        self.assertNotIn('read_job_id', raw)                     # nothing was started to make it
        self.assertEqual(DQ.quote_stands(QUOTE, raw['reply']), (True, ''))

    def test_the_client_may_send_the_new_turn_in_the_history(self):
        self.assertIsNotNone(DQ.standing(Q, quoted() + [user(Q, 1)]))

    def test_a_quote_in_parts_is_not_made_again(self):
        """The three-part question: the free part is not started twice and no part's price moves."""
        hist = [user('the three questions'), agent(MULTI, panel_offer={'kind': 'multi', 'parts': [{}, {}], 'price_label': '$200'},
                                                   options=['Approve', 'Cancel'])]
        raw = DQ.stored_raw(DQ.standing('the three questions', hist))
        self.assertTrue(raw['reply'].startswith(DQ.STANDS_LEAD))
        self.assertTrue(raw['reply'].endswith(MULTI))
        self.assertEqual(DQ.quote_stands(MULTI, raw['reply'], 'panel_offer', ['job1'], []), (True, ''))
        ok, why = DQ.quote_stands(MULTI, MULTI_REPRICED)
        self.assertFalse(ok)
        self.assertIn('price changed', why)
        ok, why = DQ.quote_stands(MULTI, MULTI, 'panel_offer', ['job1'], ['job2'])
        self.assertFalse(ok)
        self.assertIn('new run', why)

    def test_nothing_stands_when_the_thread_moved_on_or_kept_no_offer(self):
        self.assertIsNone(DQ.standing('What do Reacher viewers buy?', quoted()))                      # another question
        self.assertIsNone(DQ.standing(Q, quoted() + [user('thanks'), agent('Any time.')]))            # something was said since
        self.assertIsNone(DQ.standing(Q, [user(Q), agent(QUOTE)]))                                    # no offer on the turn: drawn fresh
        self.assertIsNone(DQ.standing(Q, [user(Q), agent('Landman viewers went on to 1923.')]))       # an answer, not a quote
        old = [dict(t, ts='2026-01-05T10:00:00Z') for t in quoted()]
        self.assertIsNone(DQ.standing(Q, old))                                                        # a lapsed offer is drawn fresh

    def test_a_client_that_sends_its_own_turns_gets_the_offer_the_server_kept(self):
        """The dashboard sends the quote's words without its offer. The record kept for a typed approval is the offer."""
        from prometheus import pending_offers as PO
        kept = {'id': 'abc123', 'thread_id': 't1', 'status': 'open', 'question': Q, 'subject': 'Landman viewers', 'kind': 'path',
                'price_label': '$100', 'years': 1, 'parts': []}
        bare = [user(Q, 50), agent(QUOTE, 45)]
        real_find = PO.find
        try:
            PO.find = lambda uname, history=None, tid=None, ctx=None, now=None: dict(kept)
            turn = DQ.standing(Q, bare, 'reader', 't1')
            self.assertIsNotNone(turn)
            raw = DQ.stored_raw(turn)
            self.assertEqual(raw['reply'], QUOTE)
            self.assertEqual(raw['panel_offer'], {'question': Q, 'subject': 'Landman viewers', 'kind': 'path', 'price_label': '$100', 'years': 1})
            PO.find = lambda *a, **k: dict(kept, question='What do Reacher viewers buy?')      # the offer on record is for another question
            self.assertIsNone(DQ.standing(Q, bare, 'reader', 't1'))
            PO.find = lambda *a, **k: None                                                     # nothing on record: drawn by the lanes as before
            self.assertIsNone(DQ.standing(Q, bare, 'reader', 't1'))
            PO.find = lambda *a, **k: dict(kept)
            self.assertIsNone(DQ.standing(Q, [user(Q, 50), agent('Landman viewers went on to 1923.', 45)], 'reader', 't1'))   # an answer, not a quote
        finally:
            PO.find = real_find

    def test_what_the_rule_refuses(self):
        for second, word in ((QUOTE + ' I can also pull who they are. Pulling this data will cost $100.', 'stacked'),
                             ('I can pull what Landman viewers did next. Pulling this data will cost $275.', 'price changed'),
                             ('On it: reading Landman viewers now.', 'started a run'),
                             ('Done. You were charged $100 and the read is below.', 'charge'),
                             ('Landman viewers went on to 1923.', 'dropped the quote')):
            ok, why = DQ.quote_stands(QUOTE, second)
            self.assertFalse(ok, second)
            self.assertIn(word, why, second)

    def test_a_quote_is_never_replayed_as_an_earlier_read(self):
        """'Same read as earlier in this thread' is for reads. A price line is not one."""
        self.assertIsNone(SM.thread_replay(Q, [user(Q, 400), agent(QUOTE, 395)]))
        self.assertIsNone(SM.thread_replay(Q, [user(Q, 400), agent(QUOTE, 395, panel_offer=dict(OFFER))]))


class AQuoteTheReaderTurnedDown(unittest.TestCase):
    """The rejected case: never routed there again in that thread."""

    NOS = ('No, run what I asked', 'not that', 'cancel', 'No', 'no thanks', "that's not what I asked for", 'Nope.', 'never mind')

    def test_a_no_is_a_no(self):
        for no in self.NOS:
            self.assertTrue(DQ.says_no(no), no)
            self.assertTrue(DQ.turned_down_now(no, quoted()), no)
        for other in ('No, I want the Hulu audience', 'not that one, the Netflix series', 'no idea, what do you think?', 'run it',
                      'Approve', 'What about 1923?', 'Nobody Wants This viewers', 'Now run Reacher', Q):
            self.assertFalse(DQ.says_no(other), other)

    def test_the_reply_to_a_no_is_never_a_quote(self):
        self.assertFalse(DQ.is_quote_text(DQ.NO_LINE))
        self.assertFalse(DQ.is_quote_text(DQ.DECLINED_LINE))
        for line in (DQ.NO_LINE, DQ.DECLINED_LINE, DQ.STANDS_LEAD):
            self.assertNotIn('\u2014', line)
            self.assertIn('nothing was charged', line.lower())

    def test_the_question_again_after_a_no_is_not_quoted_again(self):
        for no in self.NOS:
            hist = quoted() + [user(no, 20), agent('Understood.', 18)]
            self.assertIsNotNone(DQ.declined(Q, hist), no)
            self.assertIsNone(DQ.standing(Q, hist), no)
        # a third time, after the line that says so: still turned down
        hist = quoted() + [user('cancel', 20), agent('Understood.', 18), user(Q, 15), agent(DQ.DECLINED_LINE, 14)]
        self.assertIsNotNone(DQ.declined(Q, hist))

    def test_nothing_is_turned_down_without_a_no(self):
        self.assertIsNone(DQ.declined(Q, quoted()))                                                    # sent again: the quote stands
        self.assertIsNone(DQ.declined(Q, quoted() + [user('What about 1923?'), agent('1923 holds.'), user('no'), agent('ok')]))
        self.assertIsNone(DQ.declined('What do Reacher viewers buy?', quoted() + [user('no'), agent('ok')]))
        self.assertFalse(DQ.turned_down_now('no', [user('Is Landman on Netflix?'), agent('It is on Paramount+.')]))

    def test_the_reader_can_ask_for_the_quote_again(self):
        hist = quoted() + [user('not that', 20), agent(DQ.NO_LINE, 18), user(Q, 15), agent(DQ.DECLINED_LINE, 14)]
        self.assertEqual(DQ.wants_requote('Quote it again', hist), Q)
        self.assertEqual(DQ.wants_requote('ok, quote it again please', hist + [user('ok, quote it again please', 1)]), Q)
        self.assertEqual(DQ.wants_requote('Quote it again', quoted()), '')                             # nothing was turned down
        # once quoted again at the reader's word, that quote stands like any other
        again = hist + [user('Quote it again', 10), agent(QUOTE, 9, panel_offer=dict(OFFER))]
        self.assertIsNone(DQ.declined(Q, again))
        self.assertIsNotNone(DQ.declined(Q, again + [user('no', 5), agent('Understood.', 4)]))


class AnApprovalIsNotARepeat(unittest.TestCase):
    """The Approve choice sends the question again with the offer. That is a yes."""

    def rows(self, second):
        first = {'ts': '2026-11-08T18:14:35Z', 'user': 'reader', 'question': Q, 'route': 'reasoned_metrics', 'outcome': 'panel_offer', 'ms': 8371}
        return [first, dict({'ts': '2026-11-08T18:17:19Z', 'user': 'reader', 'question': Q, 'route': 'reasoned_metrics', 'ms': 5166}, **second)]

    def test_a_quote_then_the_same_question_as_a_run_is_not_graded(self):
        self.assertEqual(US.grade_sequence(self.rows({'outcome': 'landed', 'job_id': 'a44bda178354'})), [])
        self.assertEqual(US.grade_sequence(self.rows({'outcome': 'answered', 'job_id': 'a44bda178354'})), [])
        self.assertEqual(US.grade_sequence(self.rows({'route': 'priced_offer_acceptance', 'outcome': 'answered'})), [])

    def test_a_quote_then_the_same_question_quoted_again_is_a_repeat(self):
        grades = US.grade_sequence(self.rows({'outcome': 'panel_offer'}))
        self.assertEqual([g['signal'] for g in grades], ['repeat'])

    def test_an_answer_then_the_same_question_is_still_a_repeat(self):
        rows = self.rows({'outcome': 'landed', 'job_id': 'j2'})
        rows[0]['outcome'] = 'answered'
        self.assertEqual([g['signal'] for g in US.grade_sequence(rows)], ['repeat'])

    def test_the_live_signal_skips_an_approval(self):
        hist = quoted()
        self.assertEqual((US.detect(Q, hist) or {}).get('signal'), 'repeat')
        self.assertIsNone(US.detect(Q, hist, {'text': Q, 'panel_confirm': {'subject': 'Landman viewers'}}))
        self.assertIsNone(US.detect(Q, hist, {'text': Q, 'extra': {'panel_confirm': {'subject': 'Landman viewers'}}}))
        self.assertEqual((US.detect(Q, hist, {'text': Q}) or {}).get('signal'), 'repeat')


if __name__ == '__main__':
    unittest.main(verbosity=1)
