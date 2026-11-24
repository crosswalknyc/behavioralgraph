"""A send-the-file order is carried out each time it is given (Nov 23
2026 ask audit, fix 1).

What went wrong that afternoon, in one thread: a reader asked for a
read, said "Email me this file" and got the file. A second read landed.
"Email me this file" again drew "Working on it. Confirmed your task is
in progress and I will email you when it completes." Twice. Nothing was
sent for an hour. Later the same words were answered with an earlier
read's text as "the same read as earlier in this thread", and "Send this
as an email" was answered with an earlier "Sent ..." line.

The repeat guard reads an identical question as a sign the first reply
failed, and the two replay lanes hand back an earlier identical turn's
answer. Both are right for a question and wrong for an order: an order
acts on the thread as it stands now.

The rule: `send_file.is_order(text)` names a send order; the repeat
guard counts no repeats for one and never hands it to the replays; the
thread replay and the read-registry replay stand aside for it; the send
lane runs it. "Send this as an email" is a send order the lane itself
parses.

Pure tests, no network, no app.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from prometheus import send_file as sf  # noqa: E402
from prometheus import smarter as sm  # noqa: E402
from prometheus import turn_gate as tg  # noqa: E402

_READ_1 = ("Example Film drew 416,287 unique US viewers on YouTube Movies & TV Free with ads across Oct 1 to Nov 9 2026, "
           "against 566,151 plays and 26.3M watch minutes. Unique US viewers: 416,287, Total US views: 716,013.")
_READ_2 = ("Second Film drew 343,185 unique US viewers on YouTube Movies & TV Free with ads across Oct 1 to Oct 31 2026, "
           "against 425,623 plays and 16.3M watch minutes. Unique US viewers: 343,185, Total US views: 536,247.")
_CALM = "Working on it. Confirmed your task is in progress and I will email you when it completes."


def _history():
    return [
        {"role": "user", "ts": "2026-11-24T00:45:37Z", "text": "let's now do the same thing but for Example Film in the window of (10/1/26-11/9/26)"},
        {"role": "agent", "ts": "2026-11-24T00:45:57Z", "text": _READ_1, "meta": {"kind": "read", "read_job_id": "job1aaaaaaaa", "subject": "Example Film"}},
        {"role": "user", "ts": "2026-11-24T00:54:58Z", "text": "Email me this file"},
        {"role": "agent", "ts": "2026-11-24T00:55:10Z", "text": "Sent Example Film Week of availability window.csv to the address on your seat.", "meta": {"kind": "email_file"}},
        {"role": "user", "ts": "2026-11-24T00:57:24Z", "text": "let's now do the same thing but for Second Film in the window of (10/1/26-10/31/26)"},
        {"role": "agent", "ts": "2026-11-24T00:57:42Z", "text": _READ_2, "meta": {"kind": "read", "read_job_id": "job2bbbbbbbb", "subject": "Second Film"}},
    ]


class SendOrderWords(unittest.TestCase):
    def test_send_orders_are_orders(self):
        for t in ("Email me this file", "email me this file.", "Send this as an email", "send this by email",
                  "email this to me", "mail it to me", "email me the Harbor Lights breakdown",
                  "can you send me a copy of this file", "shoot this to my inbox"):
            self.assertTrue(sf.is_order(t), t)

    def test_questions_and_notify_asks_are_not_orders(self):
        for t in ("email me when it is ready", "what platforms is the audience from", "why has engagement decreased",
                  "can you give me an insight we can email to Lego",
                  "send me the data on Nike viewers for October by email with the breakdown by age and gender and the top brands they buy",
                  "what is the email open rate for this audience", ""):
            self.assertFalse(sf.is_order(t), t)

    def test_a_file_of_the_thing_above_is_an_unnamed_order(self):
        # the shape of a file plus a pointer back into the thread names nothing: the last thing goes
        for t in ("can you send me a file with the above full data", "can you send me the full data as a file",
                  "send me a csv of the above", "email us a copy", "send me the data"):
            self.assertTrue(sf.parse(t), t)
            self.assertEqual(sf.named_subject(t), '', t)
        # a file OF something new is a request for data, not a send order on the last thing
        for t in ("can you send me a file of the top 10 brands for Nike", "send me data on Nike"):
            self.assertFalse(sf.parse(t), t)
        self.assertEqual(sf.named_subject("email me the Babylon 5 breakdown"), 'Babylon 5')
        self.assertEqual(sf.named_subject("send me a file of the Nike profile"), 'Nike')

    def test_the_understanding_reads_a_send_order_over_a_product_word(self):
        from prometheus import understanding as und
        u = und.features("can you send me a file with the above full data", {}, [])
        self.assertEqual(u.intent, 'send_file_order', u.compact())
        u2 = und.features("how do I upload a list of names", {}, [])
        self.assertEqual(u2.intent, 'capability', u2.compact())

    def test_send_as_an_email_is_parsed_by_the_lane(self):
        # the lane's own parse reads the "as an email" / "by email" tail, so the modern send lane runs it
        self.assertTrue(sf.parse("Send this as an email"))
        self.assertTrue(sf.parse("send this by email"))
        self.assertTrue(sf.parse("send that via e-mail"))
        self.assertFalse(sf.parse("send this when the read lands"))


class RepeatGuardStandsAside(unittest.TestCase):
    def test_turn_gate_names_a_send_order(self):
        self.assertTrue(tg._is_send_order("Email me this file"))
        self.assertTrue(tg._is_send_order("Send this as an email"))
        self.assertFalse(tg._is_send_order("what platforms is the audience from"))

    def test_repeat_count_still_counts_questions(self):
        # the guard itself is unchanged for a question asked again and answered
        h = [{"role": "user", "ts": "2026-11-24T00:45:37Z", "text": "how many people watched Example Film in October"},
             {"role": "agent", "ts": "2026-11-24T00:45:57Z", "text": _READ_1, "meta": {"kind": "read"}}]
        import datetime as _dt
        now = _dt.datetime(2026, 11, 24, 0, 50, tzinfo=_dt.timezone.utc)
        self.assertEqual(tg.repeat_count("how many people watched Example Film in October", h, now=now), 1)
        self.assertEqual(tg.repeat_count("Email me this file", _history(), now=now), 1,
                         "the counter still sees the earlier identical turn; the gate is what stands aside for an order")


class ReplaysStandAside(unittest.TestCase):
    def test_thread_replay_never_answers_a_send_order(self):
        h = _history()
        self.assertIsNone(sm.thread_replay("Email me this file", h))
        h2 = h[:2] + [{"role": "user", "ts": "2026-11-24T00:54:58Z", "text": "send this as an email"},
                      {"role": "agent", "ts": "2026-11-24T00:55:10Z", "text": "Sent. Example Film Week of availability window.csv is on its way to the address on your seat.", "meta": {"kind": "email_file"}}] + h[4:]
        self.assertIsNone(sm.thread_replay("Send this as an email", h2))

    def test_thread_replay_after_two_holds_and_a_new_read(self):
        # the shape that handed back the newest read's words as "the same read as earlier in this thread"
        h = _history() + [
            {"role": "user", "ts": "2026-11-24T01:02:23Z", "text": "Email me this file"},
            {"role": "agent", "ts": "2026-11-24T01:02:26Z", "text": _CALM, "meta": {"kind": "calm"}},
            {"role": "user", "ts": "2026-11-24T01:06:23Z", "text": "Email me this file"},
            {"role": "agent", "ts": "2026-11-24T01:06:27Z", "text": _CALM, "meta": {"kind": "calm"}},
            {"role": "user", "ts": "2026-11-24T02:03:40Z", "text": "let's now do the same thing but for Third Film in the window of (11/1/26-11/9/26)"},
            {"role": "agent", "ts": "2026-11-24T02:03:52Z", "text": "Third Film drew 41,287 unique US viewers on YouTube Movies & TV Free with ads across Nov 1 to Nov 9 2026.", "meta": {"kind": "read", "read_job_id": "job3cccccccc", "subject": "Third Film"}},
        ]
        self.assertIsNone(sm.thread_replay("Email me this file", h))

    def test_thread_replay_still_answers_a_question_asked_again(self):
        h = [{"role": "user", "ts": "2026-11-24T00:45:37Z", "text": "how many people watched Example Film in October"},
             {"role": "agent", "ts": "2026-11-24T00:45:57Z", "text": _READ_1, "meta": {"kind": "read"}}]
        tr = sm.thread_replay("how many people watched Example Film in October", h)
        self.assertIsNotNone(tr)
        self.assertIn("416,287", tr['reply'])


class FixturesCarryTheCases(unittest.TestCase):
    def test_the_three_live_shapes_are_fixtures(self):
        import json
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        ids = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    d = json.loads(line)
                    ids[d['id']] = d
        for fid in ('fx058', 'fx059', 'fx060', 'fx061'):
            self.assertIn(fid, ids)
            self.assertEqual(ids[fid]['recorded']['route'], 'email_file', fid)
            self.assertTrue(sf.is_order(ids[fid]['question']), fid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
