#!/usr/bin/env python3
"""A refusal that says "we will get back to you" has a record behind it
(Grace Yin, Amazon, 2026-11-18; Jenna the same day: "grace said she had
something unanswered on Tony").

Her Tony build was refused at 01:26 UTC and her chat read

    We can't see Tony (2026) on Amazon at this time. Notifying Crosswalk
    and will get back to you soon. The charge for this run has been
    refunded.

Two people were mailed once and nothing else stood behind the line: no
record with a due time, nothing the daily digest or the ops watch reads,
and the promise sweep counted the line as an answer. Seventeen hours on
she had to ask.

The rule: where a build's outcome is written into the chat it was
launched from (promise_truth.settle_launch), an outcome that tells the
reader a person will get back to them opens a held record (the reader,
what they asked for, the words they were told, the chat, the run, a due
time) and its key rides the turn. The promise sweep lists such a chat
until a record stands behind it. When the build is run again and its
finished line lands in the same chat, the record closes.

Hermetic: an in-memory store handed in; no S3, no model, no mail.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import build_ready as BR  # noqa: E402
from prometheus import promise_truth as PT  # noqa: E402

USER, TID, RUN = 'graceyin', '9f460c9c19', 'ay1i2xrrxKirZg'
NAME = 'Tony (2026 Film) Viewers on Amazon Prime Video'
ASK = "you don't need the Avid tier: ~14,574 for this pull, i just want the demo by age, gender for people who watched the title on prime video"
REFUSAL = ("We can't see Tony (2026) on Amazon at this time. Notifying Crosswalk and will get back to you soon. "
           "The charge for this run has been refunded.")
NOW = 1794965223.0          # 2026-11-18 01:27 UTC


def thread():
    """Her chat at the moment the build was refused."""
    return [
        {'role': 'user', 'text': ASK, 'ts': '2026-11-18T01:21:55.885Z', 'turn_id': 'u1'},
        {'role': 'agent', 'text': f"New {NAME} profile, ready to run.", 'ts': '2026-11-18T01:25:27.459Z', 'turn_id': 'a1'},
        {'role': 'agent', 'text': f"Profile building: {NAME} (number 1 in line). I will email you when it is complete.",
         'ts': '2026-11-18T01:26:42.563Z', 'turn_id': 'a2', 'meta': {'run_id': RUN, 'subject': NAME}},
        {'role': 'agent', 'text': f"{NAME} is building.", 'ts': '2026-11-18T01:26:52.087Z', 'turn_id': 'a3'},
    ]


RUN_DOC = {'run_id': RUN, 'status': 'failed', 'subject': NAME, 'client_message': REFUSAL}


class Store:
    """The held-record store and the chat, in memory."""

    def __init__(self):
        self.docs, self.threads = {}, {(USER, TID): thread()}

    # held records
    def write(self, key, doc):
        self.docs[key] = dict(doc)

    def list(self, prefix):
        return [k for k in self.docs if k.startswith(prefix)]

    def get(self, key):
        return dict(self.docs[key])

    # the chat
    def exists(self, user, tid):
        return (user, tid) in self.threads

    def load(self, user, tid):
        return [dict(t) for t in self.threads[(user, tid)]]

    def save(self, user, tid, turns):
        self.threads[(user, tid)] = [dict(t) for t in turns]


class TheRefusalHasARecord(unittest.TestCase):
    def settle(self, store, run_doc=RUN_DOC):
        history, how = PT.settle_history(store.load(USER, TID), RUN, dict(run_doc), now=NOW)
        key = PT.back_refusal(USER, TID, RUN, history, subject=NAME, writer=store.write, now=NOW)
        store.save(USER, TID, history)
        return history, how, key

    def test_her_refusal_opens_a_record_with_a_due_time(self):
        store = Store()
        history, how, key = self.settle(store)
        self.assertEqual(how, 'settled')
        self.assertTrue(key.startswith('system/ops/held_replies/2026-11-18/'))
        doc = store.get(key)
        self.assertEqual((doc['user'], doc['kind'], doc['status'], doc['thread_id'], doc['job_id']),
                         (USER, 'get_back', 'open', TID, RUN))
        self.assertEqual(doc['told'], REFUSAL)                           # the words she was shown
        self.assertEqual(doc['subject'], NAME)
        self.assertTrue(doc['question'].startswith(NAME + ': '))
        self.assertIn('i just want the demo by age, gender', doc['question'])   # what she asked for, in her words
        self.assertEqual((doc['opened_at'], doc['due_by']), ('2026-11-18T01:27:03Z', '2026-11-18T03:27:03Z'))
        self.assertIn('will get back', doc['reason'])
        # the key rides the turn that says it
        turn = next(t for t in history if (t.get('meta') or {}).get('build_outcome'))
        self.assertEqual(turn['text'], REFUSAL)
        self.assertEqual(turn['meta']['held_ref'], key)

    def test_once(self):
        store = Store()
        history, _how, key = self.settle(store)
        self.assertEqual(PT.back_refusal(USER, TID, RUN, history, subject=NAME, writer=store.write, now=NOW + 60), '')
        self.assertEqual(len(store.docs), 1)
        again, how = PT.settle_history(history, RUN, dict(RUN_DOC), now=NOW + 60)
        self.assertEqual(how, 'already')
        self.assertEqual(next(t for t in again if (t.get('meta') or {}).get('build_outcome'))['meta']['held_ref'], key)

    def test_the_confirm_the_title_hold_is_a_follow_up_too(self):
        line = ("We need to confirm which The Office you mean before we build this. Notifying Crosswalk and will get back "
                "to you soon. The charge for this run has been refunded.")
        store = Store()
        _history, _how, key = self.settle(store, dict(RUN_DOC, client_message=line))
        self.assertEqual(store.get(key)['told'], line)

    def test_an_outcome_that_promises_nothing_opens_nothing(self):
        for doc in (dict(RUN_DOC, client_message='This build could not be completed. The charge for this run has been refunded.'),
                    {'run_id': RUN, 'status': 'cancelled', 'cancel_note': 'Stopped at your request. The charge for this run was returned.'},
                    dict(RUN_DOC, client_message='Traceback: KeyError in worker')):         # an internal line reads as the house line
            store = Store()
            history, _how, key = self.settle(store, doc)
            self.assertEqual(key, '')
            self.assertEqual(store.docs, {})
            self.assertFalse(PT.owes_follow_up(next(t for t in history if (t.get('meta') or {}).get('build_outcome'))['text']))

    def test_a_thread_that_never_stored_the_build_line_is_backed_too(self):
        store = Store()
        store.threads[(USER, TID)] = thread()[:2]                        # the outcome is appended as its own turn
        history, how, key = self.settle(store)
        self.assertEqual(how, 'appended')
        self.assertEqual(history[-1]['meta']['held_ref'], key)
        self.assertTrue(store.get(key)['question'].startswith(NAME))

    def test_a_test_process_or_a_probe_writes_nothing_on_its_own(self):
        history, _how = PT.settle_history(thread(), RUN, dict(RUN_DOC), now=NOW)
        self.assertEqual(PT.back_refusal(USER, TID, RUN, history, subject=NAME, now=NOW), '')      # no store handed in
        self.assertNotIn('held_ref', next(t for t in history if (t.get('meta') or {}).get('build_outcome'))['meta'])
        self.assertEqual(PT.back_refusal('', TID, RUN, None), '')
        self.assertEqual(PT.back_refusal(USER, TID, 'another-run', history, writer=lambda k, d: None), '')

    def test_the_place_the_outcome_is_written_calls_it(self):
        store = Store()
        launch = {'run_id': RUN, 'user': USER, 'thread_id': TID, 'subject': NAME}
        # the default writer needs the bound web process; a test process gets none, and the outcome still lands
        how = PT.settle_launch(launch, dict(RUN_DOC), exists=store.exists, load=store.load, save=store.save, now=NOW)
        self.assertEqual(how, 'settled')
        self.assertEqual(store.load(USER, TID)[2]['text'], REFUSAL)
        src = open(PT.__file__, encoding='utf-8').read()
        body = src[src.index('def settle_launch('):src.index('def back_refusal(')]
        self.assertLess(body.index("back_refusal(user, tid, rid, history"), body.index('save(user, tid, history)'))


class TheSweepSeesIt(unittest.TestCase):
    def test_her_thread_as_it_stood_reads_as_owed(self):
        history, _how = PT.settle_history(thread(), RUN, dict(RUN_DOC), now=NOW)
        tail = PT.thread_tail(history)
        self.assertEqual(tail['ends_on'], 'answer')                      # it says what happened to the build
        self.assertTrue(tail['follow_up_owed'])                          # and it still owes her
        self.assertEqual(PT.classify_follow_up(tail), ('unbacked', 'the reader was told we will get back to them and nothing on '
                                                                   'record stands behind it'))
        self.assertEqual(PT.classify_follow_up(tail, held={'id': 'x'})[0], 'backed')
        self.assertEqual(PT.classify_tail(tail)[0], 'not_a_promise')     # the progress-line verdict is unchanged

    def test_an_answer_or_a_plain_failure_is_not_owed(self):
        plain, _h = PT.settle_history(thread(), RUN, dict(RUN_DOC, client_message='This build could not be completed. The charge for this run has been refunded.'), now=NOW)
        self.assertFalse(PT.thread_tail(plain)['follow_up_owed'])
        answered = PT.settle_history(thread(), RUN, dict(RUN_DOC), now=NOW)[0] + [BR.ready_turn('MgMCJcRNFXdMQQ', NAME, {}, NOW + 65000)]
        self.assertFalse(PT.thread_tail(answered)['follow_up_owed'])
        self.assertEqual(PT.classify_follow_up(PT.thread_tail(answered)), ('none', ''))
        self.assertFalse(PT.thread_tail([{'role': 'agent', 'text': REFUSAL}])['follow_up_owed'])   # the words alone, with no build behind them

    def test_the_sweep_script_lists_it(self):
        src = open(os.path.join(HERE, 'pm_unbacked_promise_sweep.py'), encoding='utf-8').read()
        self.assertLess(src.index("if tail.get('follow_up_owed'):"), src.index("if tail.get('ends_on') != 'promise':"))
        self.assertIn('PT.classify_follow_up(tail, held=_held_for(held, user, tail', src)


class GettingBackClosesIt(unittest.TestCase):
    def backed(self):
        store = Store()
        history, _how = PT.settle_history(store.load(USER, TID), RUN, dict(RUN_DOC), now=NOW)
        key = PT.back_refusal(USER, TID, RUN, history, subject=NAME, writer=store.write, now=NOW)
        store.save(USER, TID, history)
        return store, key

    def test_the_open_follow_ups_of_a_reader(self):
        store, key = self.backed()
        got = PT.follow_ups_open(USER, now=NOW + 3600, lister=store.list, getter=store.get)
        self.assertEqual([k for k, _d in got], [key])
        self.assertEqual(PT.follow_ups_open(USER, 'another-chat', now=NOW + 3600, lister=store.list, getter=store.get), [])
        self.assertEqual(PT.follow_ups_open('someone_else', now=NOW + 3600, lister=store.list, getter=store.get), [])

    def test_the_build_run_again_closes_it(self):
        store, key = self.backed()
        closed = PT.close_follow_ups(USER, TID, how='the build was run again and finished', subject=NAME, job_id='MgMCJcRNFXdMQQ',
                                     emailed=True, now=NOW + 65000, lister=store.list, getter=store.get, writer=store.write)
        self.assertEqual(closed, [key])
        doc = store.get(key)
        self.assertEqual(doc['status'], 'delivered')
        self.assertEqual(doc['delivered'], {'how': 'the build was run again and finished', 'job_id': 'MgMCJcRNFXdMQQ', 'emailed': True,
                                            'thread_id': TID})
        self.assertEqual(PT.follow_ups_open(USER, now=NOW + 66000, lister=store.list, getter=store.get), [])
        # closed once
        self.assertEqual(PT.close_follow_ups(USER, TID, how='again', subject=NAME, now=NOW + 66000, lister=store.list,
                                             getter=store.get, writer=store.write), [])

    def test_another_build_finishing_in_the_same_chat_does_not_answer_for_it(self):
        store, key = self.backed()
        self.assertEqual(PT.close_follow_ups(USER, TID, how='the build was run again and finished', subject='Zac Efron',
                                             now=NOW + 3600, lister=store.list, getter=store.get, writer=store.write), [])
        self.assertEqual(store.get(key)['status'], 'open')
        # a person closing it by hand names no build
        self.assertEqual(PT.close_follow_ups(USER, TID, how='answered in the chat by a person', by='jenna', now=NOW + 3600,
                                             lister=store.list, getter=store.get, writer=store.write), [key])

    def test_the_finished_line_is_where_it_closes(self):
        src = open(BR.__file__, encoding='utf-8').read()
        body = src[src.index('def post_ready('):src.index('def pending(')]
        self.assertLess(body.index('save(user, tid, history + [ready_turn(rid, name, run_doc, now)])'),
                        body.index("_pt.close_follow_ups(user, tid, how='the build was run again and finished', subject=name, job_id=rid"))
        # and posting still works with no store bound (a test process): the line lands, nothing raises
        store, _key = self.backed()
        out = BR.post_ready({'run_id': 'MgMCJcRNFXdMQQ', 'user': USER, 'thread_id': TID, 'subject': NAME}, {'subject': NAME},
                            exists=store.exists, load=store.load, save=store.save, now=NOW + 65000)
        self.assertEqual(out, 'posted')
        self.assertEqual(store.load(USER, TID)[-1]['turn_id'], 'ready-MgMCJcRNFXdMQQ')


class NothingAReaderSeesChanged(unittest.TestCase):
    def test_the_words_in_the_chat_are_the_same(self):
        history, _how = PT.settle_history(thread(), RUN, dict(RUN_DOC), now=NOW)
        before = [t['text'] for t in history]
        PT.back_refusal(USER, TID, RUN, history, subject=NAME, writer=lambda k, d: None, now=NOW)
        self.assertEqual([t['text'] for t in history], before)
        for word in ('held', 'record', 'digest', 'ops'):
            self.assertNotIn(word, ' '.join(before).lower())


if __name__ == '__main__':
    unittest.main(verbosity=1)
