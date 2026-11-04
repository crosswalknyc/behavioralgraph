#!/usr/bin/env python3
"""Download / email / convert "this" means the very last thing in the chat.

Jenna 2026-11-04, after Bria Dotson tapped "Download this data as a CSV"
under The Office monthly read and was told there was no recent data read
to export: "when someone says something like this or email me this it
should take the very last thing that happened in chat and that's what
they want emailed, downloaded, converted, etc".

Hermetic: the ledger lookup, the file stash and the mail door are faked.

    python3 scripts/test_pm_last_thing.py
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import last_thing as LT   # noqa: E402
from prometheus import send_file as sf    # noqa: E402
import prometheus_analysis as pma         # noqa: E402

Q = "Show me monthly consumption for The Office US, in the US for the last three calendar years (2023, 2024, and 2025)."
READ = ("On The Office (US) on Peacock:\n\nDecember 2025 is the largest month in the three years, with 12,347,931 US viewers "
        "streaming The Office and 51,977,683 hours watched.\n\nCY2023 unique US viewers: 23,248,617, CY2024 unique US viewers: "
        "26,087,453, CY2025 unique US viewers: 28,974,316.\n\nMonth mix, Jan 1 2023 to Dec 31 2025:\n- Jan 2024: 8.04% of hours, "
        "36.1% penetration\n- Dec 2023: 7.94% of hours")
OFFER = "I can pull month by month viewers and hours watched for The Office US across one year. Pulling this data will cost $100."

DATA_ENTRY = {'subject': 'The Office (US) on Peacock', 'route': 'reasoned_metrics', 'ts': '2026-10-23T17:48:38Z',
              'q': 'Below is a report that was run by you a few weeks ago', 'qn': 'below is a report', 'k': 'the_office|viewership|2023-01-01|2025-12-31',
              'ws': '2023-01-01', 'we': '2025-12-31', 'family': 'viewership', 'reply': READ,
              'breakdown': {'dimension': 'Month', 'rows': [{'label': 'Jan 2024', 'share_pct': 8.0423, 'penetration_pct': 36.1},
                                                           {'label': 'Dec 2023', 'share_pct': 91.9577, 'penetration_pct': 40.1}]},
              'metrics': [{'name': 'Unique US viewers CY2025', 'value': 28_974_316}]}
TEXT_TWIN = dict(DATA_ENTRY, ts='2026-10-25T05:10:20Z', breakdown=None, metrics=None)   # the same words, no table


def consult_office(**kw):
    # the exact entry is the text-only twin (what the live ledger returned for Bria); the table sits on the older entry
    return {'subject': 'The Office (US) on Peacock', 'entries': [DATA_ENTRY, TEXT_TWIN], 'exact': TEXT_TWIN, 'block': ''}


def consult_nothing(**kw):
    return {}


def brias_thread():
    return [
        {'role': 'user', 'text': Q, 'ts': '2026-11-04T15:35:20Z'},
        {'role': 'agent', 'text': OFFER, 'ts': '2026-11-04T15:35:36Z', 'meta': {'options': [{'label': 'Approve'}, {'label': 'Cancel'}]}},
        {'role': 'user', 'text': 'Approve', 'ts': '2026-11-04T15:35:50Z'},
        {'role': 'agent', 'text': READ, 'ts': '2026-11-04T15:35:58Z', 'meta': {'options': [{'label': 'Download this data as a CSV'}]}},
    ]


class Resolve(unittest.TestCase):
    def test_brias_read_is_the_thing_and_its_table_is_found(self):
        r = LT.resolve(brias_thread(), consult=consult_office)
        self.assertEqual(r['kind'], 'read')
        self.assertIs(r['entry'], DATA_ENTRY, 'the entry with the table, not its text-only twin')
        self.assertEqual(r['question'], Q, 'the approval chip is not the question')
        self.assertEqual(r['subject'], 'The Office (US) on Peacock')

    def test_the_offer_and_the_approval_are_never_the_thing(self):
        hist = brias_thread()[:3]
        r = LT.resolve(hist, consult=consult_office)
        self.assertEqual(r['kind'], 'none')

    def test_confirmations_and_commands_are_skipped(self):
        hist = brias_thread() + [
            {'role': 'user', 'text': 'Download this data as a CSV'},
            {'role': 'agent', 'text': 'Saved. The Office (US) on Peacock.csv is in your browser downloads.'},
            {'role': 'user', 'text': 'email me this'},
            {'role': 'agent', 'text': 'Sent. The Office (US) on Peacock.csv is on its way to bria@crosswalknyc.com.'},
        ]
        r = LT.resolve(hist, consult=consult_office)
        self.assertEqual(r['kind'], 'read')
        self.assertEqual(r['question'], Q)

    def test_a_file_the_last_reply_carried_is_the_file(self):
        hist = brias_thread() + [
            {'role': 'user', 'text': 'Build a deck from this'},
            {'role': 'agent', 'text': 'Deck ready: The Office (7 slides). The link is good for 7 days.',
             'meta': {'deck_job_id': 'd1', 'link': {'url': 'https://dashboard-inputs.s3.us-east-2.amazonaws.com/generated_decks/abc/The%20Office.pptx?X-Amz-Signature=1',
                                                      'label': 'Download the deck'}}},
        ]
        r = LT.resolve(hist, consult=consult_office)
        self.assertEqual(r['kind'], 'file')
        self.assertEqual(r['file']['s3_key'], 'generated_decks/abc/The Office.pptx')
        self.assertEqual(r['file']['filename'], 'The Office.pptx')
        self.assertEqual(r['file']['bucket'], 'dashboard-inputs')
        self.assertIn('presentationml', r['file']['mime'])

    def test_a_same_origin_link_keeps_its_key(self):
        f = LT.file_from_url('https://dashboard.crosswalknyc.com/api/download-cached/svod_acq%2FParamount_tracker.csv', bucket='dashboard-inputs')
        self.assertEqual(f['s3_key'], 'svod_acq/Paramount_tracker.csv')
        self.assertTrue(f['same_origin'])
        self.assertEqual(f['mime'], 'text/csv')
        self.assertIsNone(LT.file_from_url('https://example.com/nothing'))

    def test_the_account_file_written_for_this_turn_is_the_file(self):
        stash = {'url': 'https://u', 'filename': 'Office Month.csv', 's3_key': 'generated_data/x/Office Month.csv',
                 'subject': 'The Office (US) on Peacock', 'question': Q, 'ts': 1_793_000_000}
        r = LT.resolve(brias_thread(), consult=consult_nothing, stash=stash)
        self.assertEqual(r['kind'], 'file')
        self.assertEqual(r['file']['s3_key'], 'generated_data/x/Office Month.csv')
        # a stale file for another question never attaches to this turn
        old = dict(stash, question='Below is a report that was run by you a few weeks ago', ts=1_792_777_719.0)
        r2 = LT.resolve(brias_thread(), consult=consult_nothing, stash=old)
        self.assertEqual(r2['kind'], 'text')

    def test_words_with_no_table_are_text_and_become_a_table(self):
        r = LT.resolve(brias_thread(), consult=consult_nothing)
        self.assertEqual(r['kind'], 'text')
        fname, body = LT.text_csv(r['text'], subject=r['subject'], question=r['question'], downloaded='November 4, 2026')
        self.assertEqual(fname, 'The Office (US) on Peacock.csv')
        lines = body.splitlines()
        self.assertEqual(lines[0], 'Item,Figure,Figure 2')
        self.assertIn('Subject,The Office (US) on Peacock,', lines)
        self.assertIn('CY2025 unique US viewers,"28,974,316",', lines)
        self.assertIn('Jan 2024,8.04% of hours,36.1% penetration', lines)
        self.assertFalse(any(l.startswith('"Month mix') for l in lines), 'a heading is not a row')
        self.assertTrue(any('12,347,931 US viewers streaming' in l and '51,977,683 hours watched' in l for l in lines))

    def test_nothing_delivered_is_none(self):
        self.assertEqual(LT.resolve([], consult=consult_nothing)['kind'], 'none')
        self.assertEqual(LT.resolve([{'role': 'user', 'text': 'hi'}, {'role': 'agent', 'text': 'Which audience do you mean?'}], consult=consult_nothing)['kind'], 'none')
        self.assertEqual(LT.resolve([{'role': 'user', 'text': Q}, {'role': 'agent', 'text': 'On it: reading The Office now.'}], consult=consult_nothing)['kind'], 'none')

    def test_commands(self):
        for t in ('Download this data as a CSV', 'email me this', 'send that over to me', 'turn this into a deck', 'export it to excel', 'Approve', 'yes'):
            self.assertTrue(LT.is_command(t), t)
        for t in (Q, 'how many watched it on a TV', 'what is the sample size here'):
            self.assertFalse(LT.is_command(t), t)

    def test_download_asks_without_the_word_csv_route_to_the_lane(self):
        for t in ('download this', 'Export that data', 'save this as a spreadsheet', 'Can you download this for me', 'Download this data'):
            self.assertTrue(pma.detect_csv_download_intent(t), t)
        for t in ('download the Nike profile', 'a csv of all the influencers you have', 'save this profile', 'how many watched this'):
            self.assertFalse(pma.detect_csv_download_intent(t), t)


class _Body:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b


class FakeS3:
    def __init__(self, objs):
        self.objs = objs

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key])}


class FakeHost:
    bucket = 'dashboard-inputs'

    def __init__(self, objs):
        self.s3_client = FakeS3(objs)

    def has(self, name):
        return name in ('s3_client', 'bucket', 'load_users')

    def load_users(self):
        return {'users': {'bria': {'email': 'bria@crosswalknyc.com'}}}

    def load_threads_index(self, uname):
        return {'threads': []}

    def s3_json(self, key, default):
        return default

    def profile_catalog(self):
        return []


class Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return {'sent': True, 'message_id': 'test'}


USER = {'username': 'bria', 'email': 'bria@crosswalknyc.com'}
PAGE = {'view': 'profile-iq', 'primary': {'s3_key': 'Paramount_Plus_10_01_2026_10_00.csv', 'name': 'Paramount+'}, 'cuts': [], 'view_context': None}


class EmailLane(unittest.TestCase):
    def test_email_me_this_sends_the_reads_table_even_with_a_profile_open(self):
        host = FakeHost({'Paramount_Plus_10_01_2026_10_00.csv': b'Column,Value\nBRAND INPUT,Paramount+\n'})
        cap = Capture()
        out = sf.answer('email me this', 'bria', user=USER, ctx=PAGE, history=brias_thread(), host=host, send=cap,
                        consult=consult_office, stash={})
        self.assertTrue(out.get('sent'), out)
        call = cap.calls[0]
        self.assertEqual(call['csv_name'], 'The_Office_US_on_Peacock.csv')
        self.assertIn('Jan 2024', call['csv'].decode('utf-8'))
        self.assertNotIn('files', {k for k, v in call.items() if v})     # the open profile does not ride along
        self.assertIn('12,347,931', call['body'])                          # the read it came from travels in the body
        self.assertEqual(out['followups'], ['Email me the Paramount+ profile'])   # a wrong guess is one tap away
        self.assertIn('The_Office_US_on_Peacock.csv', out['reply'])

    def test_a_deck_the_last_reply_carried_goes_as_the_deck(self):
        host = FakeHost({'generated_decks/abc/The Office.pptx': b'PK\x03\x04deck'})
        hist = brias_thread() + [{'role': 'user', 'text': 'Build a deck from this'},
                                 {'role': 'agent', 'text': 'Deck ready: The Office (7 slides). The link is good for 7 days.',
                                  'meta': {'deck_job_id': 'd1', 'link': {'url': 'https://dashboard-inputs.s3.us-east-2.amazonaws.com/generated_decks/abc/The%20Office.pptx?X-Amz-Signature=1', 'label': 'Download the deck'}}}]
        cap = Capture()
        out = sf.answer('email me this file', 'bria', user=USER, ctx={'view': 'profile-iq', 'primary': None, 'view_context': None},
                        history=hist, host=host, send=cap, consult=consult_office, stash={})
        self.assertTrue(out.get('sent'), out)
        call = cap.calls[0]
        self.assertIsNone(call.get('csv'))
        self.assertEqual(call['files'][0][1], 'The Office.pptx')
        self.assertIn('presentationml', call['files'][0][2])
        self.assertIn('Here is the file you asked for', call['body'])

    def test_a_text_only_read_with_a_profile_open_still_sends_the_profile_with_the_read(self):
        host = FakeHost({'Paramount_Plus_10_01_2026_10_00.csv': b'Column,Value\nBRAND INPUT,Paramount+\n'})
        hist = [{'role': 'user', 'text': 'who watches it'}, {'role': 'agent', 'text': 'On Paramount+:\n\n' + 'The audience leans older and suburban. ' * 6}]
        cap = Capture()
        out = sf.answer('Email me this file', 'bria', user=USER, ctx=PAGE, history=hist, host=host, send=cap, consult=consult_nothing, stash={})
        self.assertTrue(out.get('sent'), out)
        call = cap.calls[0]
        self.assertEqual(call['csv_name'], 'Paramount_Profile.csv')
        self.assertIn('leans older', call['body'])
        self.assertEqual(out['followups'], [])

    def test_nothing_in_the_thread_and_nothing_open_asks_once(self):
        cap = Capture()
        out = sf.answer('email me this file', 'bria', user=USER, ctx={'view': 'profile-iq', 'primary': None, 'view_context': None},
                        history=[], host=FakeHost({}), send=cap, consult=consult_nothing, stash={})
        self.assertTrue(out['reply'].startswith(sf.CLARIFIER_LEAD))
        self.assertEqual(cap.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=1)
