#!/usr/bin/env python3
"""The morning Prometheus digest (Jenna 2026-11-23): built from a fixture
day shaped like last week's cases, checked for recipients, wording and
the empty-day rule.

The fixture day (the records paraphrased, no client text):
  Liz       asked for a short positioning line; got "Which subject should
            this be on?", asked again, got "Working on it" and the answer
            came 39 minutes later; two more "Working on it" lines in a
            second chat with nothing after them (held-promise records)
  Samantha  a date correction right after a delivered valuation was
            answered "Which campaign is this about?" (she said she had
            given the wrong dates); two deck asks in the same chat, one
            of which stopped with an error; a charge reversed
  Emma      asked about one audience and the answer read another; she
            said so; the record carries a high-confidence reading that
            disagrees with the class that answered
  Jenna     asked a question and was handed an intake step ("What
            campaign should I value?")
plus: a question answered with a build offer, the same question asked
twice, an email that bounced, an email that never went out, a probe's
rows (never listed) and a day with nothing (no email).

Hermetic: a fake S3 and a fake SES; REGRESSION_TEST_MODE keeps every
send inside the process.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ['PM_PROBE_LOOKUP'] = '0'

from prometheus import daily_digest as dd  # noqa: E402

B = dd.BUCKET
UTC = dt.timezone.utc
# the window: Sun Nov 22 07:00 PT to Mon Nov 23 07:00 PT (15:00Z to 15:00Z)
SINCE = dt.datetime(2026, 11, 22, 15, 0, tzinfo=UTC)
UNTIL = dt.datetime(2026, 11, 23, 15, 0, tzinfo=UTC)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode()
        return {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError('NoSuchKey ' + Key)
        import io
        return {'Body': io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k} for k in sorted(self.objects) if k.startswith(Prefix)], 'IsTruncated': False}


class FakeSES:
    def __init__(self, rows):
        self.rows = rows

    def list_suppressed_destinations(self, **kw):
        return {'SuppressedDestinationSummaries': list(self.rows)}


def _ts(h, m, day=23):
    return f"2026-11-{day:02d}T{h:02d}:{m:02d}:00Z"


def _ep(h, m, day=23):
    return dt.datetime(2026, 11, day, h, m, tzinfo=UTC).timestamp()


def put(s3, key, doc):
    s3.put_object(Bucket=B, Key=key, Body=json.dumps(doc).encode())


def ask(s3, n, user, q, ts, route, outcome, thread, **extra_fields):
    rec = {'ts': ts, 'user': user, 'question': q, 'route': route, 'outcome': outcome, 'surface': 'analyze', 'view': 'profile-iq', 'ms': 900,
           'extra': {'thread': {'id': thread}, 'result': outcome}}
    for k in ('job_id', 'reply', 'settled_at', 'subject'):
        if k in extra_fields:
            rec[k] = extra_fields.pop(k)
    rec['extra'].update(extra_fields)
    day = ts[:10]
    key = f"{dd.ASK_PREFIX}{day}/{ts[11:19].replace(':', '')}_{n:04d}.json"
    put(s3, key, rec)
    if rec.get('job_id'):
        put(s3, f"{dd.ASK_BY_JOB_PREFIX}{rec['job_id']}.json", {'key': key, 'ts': ts, 'user': user})
    return rec


def thread(s3, user, tid, turns):
    put(s3, f"{dd.THREADS_PREFIX}{user}/{tid}.json", [{'role': r, 'text': t, 'ts': e} for r, t, e in turns])


def fixture_day():
    s3 = FakeS3()
    # --- Liz: which subject, then a late answer, then two promises with nothing behind them
    ask(s3, 1, 'liz', 'Give me a three to five word line on how Prometheus differs from a chatbot', _ts(20, 38, 22), 'reasoned_metrics', 'answered', 'liz-chat-1',
        reply='Which subject should this be on? Name the show, brand or person.', u='intent=analyze;conf=0.60;src=features;act=answer;scope=-;flip=0')
    ask(s3, 2, 'liz', 'Give me a three to five word line on how Prometheus differs from a chatbot', _ts(20, 43, 22), 'reasoned_metrics', 'landed', 'liz-chat-1',
        job_id='aaaa11111111', reply='Here are three, each five words or fewer.', settled_at=_ts(21, 22, 22))
    put(s3, f"{dd.READ_PREFIX}aaaa11111111.json", {'status': 'done', 'user': 'liz', 'started_at': str(_ep(20, 43, 22)), 'finished_at': str(_ep(21, 22, 22)),
                                                  'thread_id': 'liz-chat-1', 'question': 'Give me a three to five word line'})
    thread(s3, 'liz', 'liz-chat-1', [('user', 'Give me a three to five word line on how Prometheus differs from a chatbot', _ep(20, 38, 22)),
                                     ('assistant', 'Which subject should this be on? Name the show, brand or person.', _ep(20, 38, 22)),
                                     ('user', 'Give me a three to five word line on how Prometheus differs from a chatbot', _ep(20, 43, 22)),
                                     ('assistant', 'Working on it. Confirmed your task is in progress.', _ep(20, 43, 22)),
                                     ('assistant', 'Here are three, each five words or fewer.', _ep(21, 22, 22))])
    for i, (h, m) in enumerate(((20, 49), (20, 54))):
        put(s3, f"{dd.HELD_PREFIX}2026-11-22/{h}{m}00_liz{i}.json",
            {'id': f'liz-held-{i}', 'user': 'liz', 'kind': 'promise', 'status': 'delivered', 'opened_at': _ts(h, m, 22), 'thread': 'liz-chat-2',
             'question': 'Give me a three to five word line on how Prometheus differs from a chatbot',
             'told': 'Working on it. Confirmed your task is in progress.', 'reason': 'the calm line went out with no job behind it',
             'delivered': {'how': 'a person sent the answer, in both chats', 'at': _ts(21, 30, 22)}})
    # --- Samantha: the which-campaign reply to a date correction, two deck asks (one errored), a reversed charge
    ask(s3, 3, 'samantha', 'I want the period of Aug 27 to Sep 8; I had given the wrong dates for that campaign', _ts(18, 26, 22), 'bpiq_wizard', 'campaign_clarify', 'sam-chat-1',
        reply='Which campaign is this about? Open it in the Attribution IQ tab.', u='intent=continuation_correction;conf=0.86;src=features;act=answer;scope=-;flip=0')
    ask(s3, 4, 'samantha', 'Can you send a refreshed deck for the brand valuation with the corrected dates', _ts(18, 37, 22), 'deck', 'queued', 'sam-chat-1',
        job_id='bbbb22222222', reply='On it: building the deck now.')
    put(s3, f"{dd.DECK_PREFIX}bbbb22222222.json", {'status': 'error', 'user': 'samantha', 'started_at': str(_ep(18, 37, 22)), 'error': 'the builder stopped',
                                                  'thread_id': 'sam-chat-1', 'title': 'Brand valuation deck'})
    ask(s3, 5, 'samantha', 'Can you redo the entire deck so it has all the metrics in the standard brand partnership deck', _ts(19, 14, 22), 'deck', 'queued', 'sam-chat-1',
        job_id='cccc33333333', reply='On it: building the deck now.')
    put(s3, f"{dd.DECK_PREFIX}cccc33333333.json", {'status': 'done', 'user': 'samantha', 'started_at': str(_ep(19, 14, 22)), 'finished_at': str(_ep(19, 19, 22)),
                                                  'thread_id': 'sam-chat-1', 'title': 'Brand valuation deck'})
    thread(s3, 'samantha', 'sam-chat-1', [('user', 'I want the period of Aug 27 to Sep 8; I had given the wrong dates for that campaign', _ep(18, 26, 22)),
                                          ('assistant', 'Which campaign is this about? Open it in the Attribution IQ tab.', _ep(18, 26, 22)),
                                          ('user', 'Can you send a refreshed deck for the brand valuation with the corrected dates', _ep(18, 37, 22)),
                                          ('assistant', 'On it: building the deck now.', _ep(18, 37, 22)),
                                          ('user', 'Can you redo the entire deck so it has all the metrics in the standard brand partnership deck', _ep(19, 14, 22)),
                                          ('assistant', 'On it: building the deck now.', _ep(19, 14, 22)),
                                          ('assistant', 'The deck is ready: Brand valuation deck (14 pages).', _ep(19, 19, 22))])
    put(s3, dd.USERS_KEY, {'users': {
        'samantha': {'email': 's@example.com', 'credit_usage_history': [
            {'used_at': _ts(18, 45, 22), 'description': 'REFUND: the deck did not finish', 'job_id': 'bbbb22222222', 'pull_type': 'refund', 'credits_used': -3, 'amount_usd': -300.0},
            {'used_at': _ts(10, 0, 19), 'description': 'REFUND: outside the window', 'job_id': 'zzzz', 'pull_type': 'refund', 'credits_used': -1},
        ]},
        'liz': {'email': 'liz@crosswalknyc.com', 'credit_usage_history': []},
        'canary:probe': {'credit_usage_history': [{'used_at': _ts(12, 0, 22), 'description': 'REFUND: probe', 'pull_type': 'refund', 'credits_used': -1}]},
    }, 'companies': {}})
    # --- Emma: a high-confidence reading that disagrees with the class that answered, and her pushback in the chat
    ask(s3, 6, 'emma', 'Does the VICE Sports audience show interest in grooming products? I need figures for a sales proposal', _ts(20, 46, 22), 'bpiq_wizard', 'intake', 'emma-chat-1',
        reply='What campaign should I value?', u='intent=analyze;conf=0.91;src=features;act=answer;scope=-;flip=0')
    thread(s3, 'emma', 'emma-chat-1', [('user', 'Does the VICE Sports audience show interest in grooming products? I need figures for a sales proposal', _ep(20, 46, 22)),
                                       ('assistant', 'On the open profile: 73.9% of this audience buys grooming products.', _ep(20, 47, 22)),
                                       ('user', "That's wrong, this is for VICE Sports, not the profile on my screen", _ep(20, 52, 22))])
    # --- Jenna: a question handed to an intake step
    ask(s3, 7, 'jenna', 'What digital creators should the NFL partner with to bring new women 18 to 24 viewers to the Super Bowl', _ts(20, 19, 22), 'bpiq_wizard', 'intake', 'jenna-chat-1',
        reply='What campaign should I value?', u='intent=insight_ask;conf=0.84;src=features;act=answer;scope=-;flip=0')
    # --- a question answered with a build offer; the same question asked twice (a repeat)
    ask(s3, 8, 'graceyin', 'Do you have a profile for Costco shoppers?', _ts(16, 5, 22), 'profile_build', 'panel_offer', 'grace-chat-1',
        reply='I can build Costco shoppers for 8 credits. Approve to start.', u='intent=exists;conf=0.88;src=features;act=answer;scope=-;flip=0')
    ask(s3, 9, 'jared', 'How many people watched Landman last month on Paramount+', _ts(17, 0, 22), 'reasoned_metrics', 'answered', "{'id': 'jared-chat-1'}",
        reply='Here is the read on Landman.')   # the log writer stores the thread dict as its text
    ask(s3, 10, 'jared', 'How many people watched Landman last month on Paramount+', _ts(17, 2, 22), 'reasoned_metrics', 'answered', 'jared-chat-1',
        reply='Here is the read on Landman.')
    # --- a profile build still running is not an unfinished answer; an intake step promises nothing
    ask(s3, 12, 'jessie', 'run a new profile on Starbucks', _ts(20, 7, 22), 'build_order', 'queued', 'jessie-chat-1', job_id='9999build999', reply='Queued: building Starbucks now.')
    # --- a held promise with no question of its own takes the person's last ask before it
    put(s3, f"{dd.HELD_PREFIX}2026-11-22/204800_liz9.json",
        {'id': 'liz-held-9', 'user': 'liz', 'kind': 'promise', 'status': 'delivered', 'opened_at': _ts(20, 48, 22), 'question': '',
         'told': 'Working on it. Confirmed your task is in progress.', 'reason': 'the calm line went out with no job behind it',
         'delivered': {'how': 'a person sent the answer, in both chats'}})
    # --- a probe's rows: never listed
    ask(s3, 11, 'canary:probe', 'How big is the Babylon 5 audience on Prime Video?', _ts(16, 30, 22), 'reasoned_metrics', 'promised', 'probe-chat', job_id='dddd44444444')
    # --- mail: one that never went out, one that bounced (the address on SES's list)
    put(s3, f"{dd.MAIL_PREFIX}2026-11-22/190000_m1.json", {'to': 'buyer@example.com', 'subject': 'Your Costco read', 'caller': 'read-landing', 'sent': False,
                                                          'reason': 'send failed: MessageRejected', 'logged_at': _ts(19, 0, 22)})
    put(s3, f"{dd.MAIL_PREFIX}2026-11-21/150000_m2.json", {'to': 'old.address@example.com', 'subject': 'Your valuation deck', 'caller': 'deck-landing', 'sent': True,
                                                          'logged_at': _ts(15, 0, 21), 'message_id': 'x'})
    ses = FakeSES([{'EmailAddress': 'old.address@example.com', 'Reason': 'BOUNCE', 'LastUpdateTime': dt.datetime(2026, 11, 22, 18, 10, tzinfo=UTC)},
                   {'EmailAddress': 'earlier@example.com', 'Reason': 'BOUNCE', 'LastUpdateTime': dt.datetime(2026, 11, 17, 12, 0, tzinfo=UTC)},
                   {'EmailAddress': 'nobody.we.wrote@example.com', 'Reason': 'BOUNCE', 'LastUpdateTime': dt.datetime(2026, 11, 22, 19, 30, tzinfo=UTC)}])
    return s3, ses


BANNED = re.compile(r"\b(?:synth\w*|pipeline|lanes?|workers?|model(?:s|ed|ing)?|claude|anthropic|openai|gpt\w*|hostmap|clickstream|panelists?|"
                    r"tier\s*[12]|gen\s*pop|queue|hetzner|replay|regex|enforcers?|json|router|routed|routing|vendor)\b", re.I)


class TheFixtureDay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s3, cls.ses = fixture_day()
        cls.doc = dd.collect(s3=cls.s3, ses=cls.ses, since=SINCE, until=UNTIL)
        cls.items = cls.doc['items']
        cls.body = dd.render(cls.doc)
        cls.by_kind = {}
        for it in cls.items:
            cls.by_kind.setdefault(it['kind'], []).append(it)

    def _of(self, kind, user=None):
        return [it for it in self.by_kind.get(kind, []) if user is None or it['user'] == user]

    def test_liz_the_late_answer_and_the_two_empty_promises(self):
        late = [it for it in self._of('unfinished', 'liz') if it['outcome'] == 'late']
        self.assertEqual(len(late), 1)
        self.assertIn('39 minutes later', late[0]['what'])
        held = sorted((it for it in self._of('unfinished', 'liz') if it['outcome'] == 'held'), key=lambda it: it['thread'])
        self.assertEqual(len(held), 2, 'the two promise lines in one chat are one item; the one with no question of its own is another')
        self.assertEqual(held[1]['what'], 'Prometheus said it was working on it and nothing came back on its own. A person sent the answer, in both chats (2 times).')
        self.assertEqual(held[1]['ts'], '2026-11-22T20:54:00Z')
        self.assertEqual(held[1]['link'], 'https://dashboard.crosswalknyc.com/admin/prometheus/chat/liz/liz-chat-2')
        # the promise with no question: the person's last ask before it names it and its chat
        self.assertEqual(held[0]['thread'], 'liz-chat-1')
        self.assertIn('three to five word line', held[0]['question'])

    def test_samantha_the_which_campaign_reply_the_failed_deck_and_the_reversal(self):
        mis = self._of('misrouted', 'samantha')
        self.assertEqual(len(mis), 1, self.items)
        self.assertIn('wrong dates', mis[0]['question'])
        self.assertEqual(mis[0]['what'], 'Handled as an intake step while the message looked like a correction or follow-up on the last answer.')
        self.assertIn('Which campaign', mis[0]['reply'])
        decks = self._of('deck_failure', 'samantha')
        self.assertEqual(len(decks), 1, decks)
        self.assertIn('stopped with an error', decks[0]['what'])
        self.assertEqual(decks[0]['job_id'], 'bbbb22222222')
        rev = self._of('reversal', 'samantha')
        self.assertEqual(len(rev), 1, rev)
        self.assertIn('3 credits, $300.00', rev[0]['what'])
        self.assertIn('the deck did not finish', rev[0]['what'])
        self.assertEqual(rev[0]['link'], 'https://dashboard.crosswalknyc.com/admin/prometheus/chat/samantha/sam-chat-1')
        self.assertEqual(len([it for it in self.items if it['job_id'] == 'bbbb22222222' and it['kind'] != 'reversal']), 1, 'the failed deck is listed once')
        # the deck that finished in five minutes is not a failure
        self.assertFalse(any(it['job_id'] == 'cccc33333333' for it in self.items))

    def test_emma_the_disagreement_and_her_pushback(self):
        wiz = self._of('wizard_captured', 'emma')
        self.assertEqual(len(wiz), 1, self.by_kind.keys())
        self.assertEqual(wiz[0]['what'], 'A question was taken as an intake step; it reads as a data question.')
        push = self._of('correction', 'emma')
        self.assertEqual(len(push), 1, push)
        self.assertIn("pushed back", push[0]['what'])
        self.assertIn('VICE Sports', push[0]['what'])

    def test_jenna_the_question_handed_to_an_intake_step(self):
        wiz = self._of('wizard_captured', 'jenna')
        self.assertEqual(len(wiz), 1)
        self.assertIn('insight question', wiz[0]['what'])
        self.assertEqual(wiz[0]['link'], 'https://dashboard.crosswalknyc.com/admin/prometheus/chat/jenna/jenna-chat-1')

    def test_the_build_offer_on_a_question_and_the_repeat(self):
        offered = self._of('build_offered', 'graceyin')
        self.assertEqual(len(offered), 1)
        self.assertIn('build offer instead of an answer', offered[0]['what'])
        self.assertIn('do-we-have-it', offered[0]['what'])
        rep = self._of('repeat', 'jared')
        self.assertEqual(len(rep), 1, self.by_kind.get('repeat'))
        self.assertEqual(rep[0]['what'], 'Asked the same thing again.')

    def test_the_bounce_and_the_mail_that_never_went_out(self):
        b = self._of('bounce')
        kinds = sorted((it['user'], it['outcome']) for it in b)
        self.assertEqual(kinds, [('buyer@example.com', 'send failed'), ('nobody.we.wrote@example.com', 'BOUNCE'), ('old.address@example.com', 'BOUNCE')])
        unmatched = next(it for it in b if it['user'] == 'nobody.we.wrote@example.com')
        self.assertEqual(unmatched['what'], 'The email bounced; it was not one the chat sent in the last week.')
        self.assertEqual(unmatched['question'], '')
        bounced = next(it for it in b if it['user'] == 'old.address@example.com')
        self.assertIn('bounced', bounced['what'])
        self.assertIn('Your valuation deck', bounced['question'])
        self.assertFalse(any(it['user'] == 'earlier@example.com' for it in b), 'a bounce before the window is not listed')

    def test_probes_never_make_the_digest(self):
        self.assertFalse(any('canary' in it['user'] for it in self.items))
        self.assertEqual(self.doc['asks'], 11)
        self.assertEqual(self.doc['users'], 7)

    def test_counts_worst_ten_and_the_rest(self):
        c = self.doc['counts']
        self.assertEqual(c, {'unfinished': 3, 'misrouted': 4, 'pushback': 3, 'deck_failures': 1, 'bounces': 3, 'reversals': 1}, c)
        self.assertEqual(len(self.items), 15)
        self.assertFalse(any(it['user'] == 'jessie' for it in self.items), 'a running profile build is not an unfinished answer')
        lines = self.body.split('\n')
        self.assertTrue(lines[0].startswith('Prometheus, what went wrong: Sun Nov 22 7:00 AM to Mon Nov 23 7:00 AM PT.'), lines[0])
        self.assertEqual(lines[1], '15 things to look at, out of 11 questions from 7 people.')
        self.assertEqual(lines[3], 'Counts: 3 answers never finished, 4 sent to the wrong place, 3 pushed back on or asked again, 1 decks or files that failed, 3 emails that bounced, 1 charges reversed.')
        self.assertIn('The worst ten:', self.body)
        self.assertIn('The rest, one line each:', self.body)
        numbered = [l for l in lines if re.match(r'^\d+\. ', l)]
        self.assertEqual(len(numbered), 10)
        rest = [l for l in lines if l.startswith('- ')]
        self.assertEqual(len(rest), 5)
        # the worst first: the unfinished answers, the reversal and the failed deck lead; the repeats sit in the rest
        blocks, cur = [], None
        for l in lines:
            if re.match(r'^\d+\. ', l):
                cur = [l]; blocks.append(cur)
            elif cur is not None and l.startswith('    '):
                cur.append(l)
            else:
                cur = None
        self.assertEqual(len(blocks), 10)
        heads = ['\n'.join(b) for b in blocks]
        self.assertTrue(all(any(k in h for k in ('working on it', 'was reversed', 'The deck')) for h in heads[:4]), heads[:4])
        self.assertTrue(any('Asked the same thing again' in l for l in rest))
        self.assertTrue(any('email to buyer@example.com: "Your Costco read"' in h for h in heads), heads)
        self.assertTrue(any('samantha, deck: "Brand valuation deck"' in h for h in heads), heads)
        self.assertTrue(any('What they got: "What campaign should I value?"' in h for h in heads))
        self.assertIn('Open the chat: https://dashboard.crosswalknyc.com/admin/prometheus/chat/', self.body)
        self.assertTrue(self.body.rstrip().endswith('Nothing in this note went to a user.'))

    def test_wording_is_plain_and_carries_no_internal_words_or_dashes(self):
        self.assertIsNone(BANNED.search(self.body), BANNED.search(self.body) and BANNED.search(self.body).group(0))
        self.assertNotIn('\u2014', self.body)
        self.assertNotIn('\u2013', self.body)
        self.assertIsNone(BANNED.search(dd.subject_line(self.doc)))
        self.assertEqual(dd.subject_line(self.doc), 'Prometheus: 15 things went wrong, Sun Nov 22')

    def test_recipients_and_sender_are_the_house_ops_mail(self):
        self.assertEqual(sorted(dd.RECIPIENTS), ['jenna@crosswalknyc.com', 'jessie@crosswalknyc.com'])
        self.assertNotIn('liz@crosswalknyc.com', dd.RECIPIENTS)
        self.assertEqual(dd.SENDER, 'BehavioralGraph <jenna@crosswalknyc.com>')
        with open(os.path.join(ROOT, 'prometheus', 'daily_digest.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("boto3.client('ses', region_name='us-east-2')", src)

    def test_send_writes_the_record_and_the_last_marker_and_sends_nothing_in_a_test(self):
        out = dd.send(self.doc, s3=self.s3)
        self.assertFalse(out['sent'])
        self.assertFalse(out['empty'])
        rec = json.loads(self.s3.objects[f"{dd.OUT_PREFIX}2026-11-23.json"])
        self.assertEqual(rec['subject'], dd.subject_line(self.doc))
        last = json.loads(self.s3.objects[dd.LAST_KEY])
        self.assertEqual(last['until'], '2026-11-23T15:00:00Z')
        self.assertTrue(last['sent'])


class TheEmptyDay(unittest.TestCase):
    def test_nothing_to_report_sends_nothing_but_moves_the_marker(self):
        s3 = FakeS3()
        ask(s3, 1, 'jared', 'How many people watched Landman last month', _ts(17, 0, 22), 'reasoned_metrics', 'landed', 'jared-chat-1',
            job_id='eeee55555555', reply='Here is the read.', settled_at=_ts(17, 3, 22))
        put(s3, f"{dd.READ_PREFIX}eeee55555555.json", {'status': 'done', 'user': 'jared', 'started_at': str(_ep(17, 0, 22)), 'finished_at': str(_ep(17, 3, 22))})
        doc = dd.collect(s3=s3, ses=FakeSES([]), since=SINCE, until=UNTIL)
        self.assertEqual(doc['items'], [])
        self.assertEqual(dd.render(doc), '')
        out = dd.send(doc, s3=s3)
        self.assertTrue(out['empty'])
        self.assertFalse(out['sent'])
        self.assertIn(dd.LAST_KEY, s3.objects)
        self.assertFalse(json.loads(s3.objects[dd.LAST_KEY])['sent'])

    def test_a_fresh_promise_is_not_called_unfinished_yet(self):
        s3 = FakeS3()
        ask(s3, 1, 'jared', 'How many people watched Landman', _ts(14, 50, 23), 'reasoned_metrics', 'promised', 'jared-chat-1',
            job_id='ffff66666666', reply='On it: reading Landman now.')
        doc = dd.collect(s3=s3, ses=FakeSES([]), since=SINCE, until=UNTIL)
        self.assertEqual(doc['items'], [], 'ten minutes old is not fifteen')


class TheWindow(unittest.TestCase):
    def test_from_the_last_digest_or_24_hours(self):
        s3 = FakeS3()
        now = UNTIL
        since, until = dd.window(now=now, s3=s3)
        self.assertEqual((since, until), (now - dt.timedelta(hours=24), now))
        put(s3, dd.LAST_KEY, {'until': '2026-11-22T20:00:00Z'})
        since, until = dd.window(now=now, s3=s3)
        self.assertEqual(since, dt.datetime(2026, 11, 22, 20, 0, tzinfo=UTC))
        put(s3, dd.LAST_KEY, {'until': '2026-11-19T20:00:00Z'})   # too long ago: back to 24 hours
        since, until = dd.window(now=now, s3=s3)
        self.assertEqual(since, now - dt.timedelta(hours=24))
        # a start handed in stands on its own; the end defaults to now
        doc = dd.collect(s3=s3, ses=FakeSES([]), since=dt.datetime(2026, 11, 20, 15, 0, tzinfo=UTC), now=now)
        self.assertEqual((doc['since'], doc['until']), ('2026-11-20T15:00:00Z', '2026-11-23T15:00:00Z'))
        d0, d1 = dd.window(day='2026-10-22')
        self.assertEqual((d0, d1), (dt.datetime(2026, 10, 22, tzinfo=UTC), dt.datetime(2026, 10, 23, tzinfo=UTC)))


class TheOlderShapeStillRenders(unittest.TestCase):
    def test_a_document_with_the_old_keys_renders_its_people(self):
        body = dd.render({'date': '2026-10-20', 'corrections': [{'user': 'mark.valdez', 'question': 'No I am interested in the other one', 'reply': 'Nothing came back'}],
                          'repeats': [], 'held': [{'user': 'cpearson', 'question': 'rerun the Landman read', 'job_id': 'rj1'}], 'faults': []})
        self.assertIn('mark.valdez', body)
        self.assertIn('cpearson', body)
        self.assertIsNone(BANNED.search(body))


class TheTimerAndTheEntryPoint(unittest.TestCase):
    def test_the_timer_runs_each_morning_pacific_and_the_service_logs(self):
        parent = os.path.dirname(ROOT)
        with open(os.path.join(parent, 'migration', 'systemd', 'prometheus-daily-digest.timer'), encoding='utf-8') as fh:
            timer = fh.read()
        with open(os.path.join(parent, 'migration', 'systemd', 'prometheus-daily-digest.service'), encoding='utf-8') as fh:
            service = fh.read()
        self.assertIn('OnCalendar=*-*-* 07:00:00 America/Los_Angeles', timer)
        self.assertIn('Persistent=true', timer)
        self.assertIn('ExecStart=/usr/bin/python3 -u /root/finished_codes/migration/pm_daily_digest.py', service)
        self.assertIn('StandardOutput=append:/var/log/prometheus-daily-digest.log', service)
        with open(os.path.join(parent, 'migration', 'pm_daily_digest.py'), encoding='utf-8') as fh:
            entry = fh.read()
        self.assertIn('from prometheus import daily_digest', entry)

    def test_the_admin_chat_view_is_super_admin_only_and_read_only(self):
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'threads.py'), encoding='utf-8') as fh:
            src = fh.read()
        i = src.index("@_H.app.route('/admin/prometheus/chat/<path:owner>/<tid>'")
        blk = src[i:src.index('def _pm_threads_index_key', i)]
        self.assertIn('@_super_admin_gate', blk)
        gate = src[src.index('def _super_admin_gate(fn):'):i]
        self.assertIn("_H.get('requires_super_admin')", gate)
        self.assertIn("return 'Not found', 404", gate)   # a host without the gate answers nothing, never an open route
        self.assertNotIn('put_json', blk)
        self.assertNotIn('mark_seen', blk)
        self.assertNotIn('delete_object', blk)
        self.assertIn("'private, no-store'", blk)


if __name__ == '__main__':
    unittest.main(verbosity=1)
