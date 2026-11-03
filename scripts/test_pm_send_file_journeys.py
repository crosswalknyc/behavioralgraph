#!/usr/bin/env python3
"""The email-me-this-file lane sends Digital Journey workbooks.

Carolyn Bisson (WBD), 2026-11-02, with Young Sheldon on Netflix open:
"please email me the excel file of the Young Sheldon Netflix journey
that is in my library" got "Which file?"; her answer, "Young Sheldon on
Netflix - watch Journey excel format", went to the reader and started a
cold read on a title her library already held. Both turns now send the
journey's breakdown workbook.

Hermetic: a fake host with an in-memory S3, a capturing mail door.

    python3 scripts/test_pm_send_file_journeys.py
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import send_file as sf   # noqa: E402

YS_KEY = 'journey-iq/cbisson/Young_Sheldon_on_Netflix_20261020_191908_69eea3c832ce.json.gz'
YS_ALL_KEY = 'journey-iq/cbisson/Young_Sheldon_All_Platforms_20261102_225926_651e4aae3eb3.json.gz'
KL_KEY = 'journey-iq/cbisson/Knots_Landing_on_Prime_Video_20261020_191639_21ef1cc0b2f1.json.gz'
OTHER_KEY = 'journey-iq/someone/Young_Sheldon_on_Max_20261001_000000_abcdef012345.json.gz'


def _payload(name, start=50_507_698, end=3_351_691):
    return {'meta': {'project_name': name, 'target_name': name, 'start_date': '2025-10-20', 'end_date': '2026-10-20',
                     'target_type': 'watch_journey', 'journey_kind': 'watch', 'category': 'catalog sitcom', 'created_by': 'cbisson'},
            'fragrance_shop_journey': {
                'spine': [{'id': 'tam', 'label': 'US population', 'accounts': 329_900_000, 'kept': 100.0, 'dropped': 0, 'ofUs': 100.0},
                          {'id': 'social_view', 'label': f'Saw a social post about {name}', 'accounts': start, 'kept': 15.31, 'dropped': 329_900_000 - start, 'ofUs': 15.31},
                          {'id': 'watched_30d', 'label': 'Watched within 30 days', 'accounts': end, 'kept': 6.636, 'dropped': start - end, 'ofUs': 1.016}],
                'fork': [], 'detours': [{'title': 'Where they watched it', 'base': end, 'kind': 'partition',
                                         'rows': [{'label': 'Netflix', 'accounts': end, 'pct': 100.0}]}]},
            'kpis': {'total_users': end, 'conversion_pct': 6.636}}


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

    def __init__(self, *, users, index, payloads, threads=None):
        self._users = users
        self._index = index
        self._threads = threads or {'threads': []}
        objs = {k: gzip.compress(json.dumps(v).encode('utf-8')) for k, v in payloads.items()}
        self.s3_client = FakeS3(objs)

    def has(self, name):
        return name in ('s3_json', 'load_threads_index', 'load_users', 's3_client', 'bucket')

    def s3_json(self, key, default):
        return self._index if key == sf.JOURNEY_INDEX_KEY else default

    def load_users(self):
        return {'users': self._users}

    def load_threads_index(self, uname):
        return self._threads

    def profile_catalog(self):
        return []


def _host(threads=None):
    users = {'cbisson': {'email': 'carolyn.bisson@wbd.com', 'allowed_journey_iq_runs': [YS_KEY, YS_ALL_KEY, KL_KEY]}}
    index = {'runs': [
        {'key': KL_KEY, 'project_name': 'Knots Landing on Prime Video', 'created_by': 'cbisson', 'created_at': '2026-10-20T19:16:39Z'},
        {'key': YS_KEY, 'project_name': 'Young Sheldon on Netflix', 'created_by': 'cbisson', 'created_at': '2026-10-20T19:19:06Z'},
        {'key': YS_ALL_KEY, 'project_name': 'Young Sheldon All Platforms', 'created_by': 'cbisson', 'created_at': '2026-11-02T22:59:26Z'},
        {'key': OTHER_KEY, 'project_name': 'Young Sheldon on Max', 'created_by': 'someone', 'created_at': '2026-10-01T00:00:00Z'},
    ]}
    payloads = {YS_KEY: _payload('Young Sheldon on Netflix'), YS_ALL_KEY: _payload('Young Sheldon All Platforms', end=6_945_866),
                KL_KEY: _payload('Knots Landing on Prime Video', 1_200_000, 97_236), OTHER_KEY: _payload('Young Sheldon on Max')}
    return FakeHost(users=users, index=index, payloads=payloads, threads=threads)


class Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return {'sent': True, 'message_id': 'test'}


USER = {'username': 'cbisson', 'email': 'carolyn.bisson@wbd.com'}
JOURNEY_PAGE = {'view': 'journeyIQ', 'primary': None, 'cuts': [],
                'view_context': {'view_id': 'journeyIQ', 'view_title': 'Digital Journey IQ',
                                 'data': {'title': 'Young Sheldon on Netflix', 'key': YS_KEY, 'story_mode': 'fragrance_shop_journey'}}}


class Parse(unittest.TestCase):
    def test_carolyns_first_turn_is_a_send_order_naming_the_journey(self):
        t = 'please email me the excel file of the Young Sheldon Netflix journey that is in my library'
        self.assertTrue(sf.parse(t))
        self.assertEqual(sf.named_subject(t), 'Young Sheldon Netflix')

    def test_her_answer_to_which_file_is_a_send_order(self):
        t = 'Young Sheldon on Netflix - watch Journey excel format'
        self.assertTrue(sf.parse(t))
        self.assertEqual(sf.named_subject(t), 'Young Sheldon on Netflix')

    def test_the_old_shapes_still_parse(self):
        self.assertTrue(sf.parse('email me this file'))
        self.assertEqual(sf.named_subject('email me the Austin Sprinz profile'), 'Austin Sprinz')
        self.assertEqual(sf.named_subject('email me the Babylon 5 breakdown'), 'Babylon 5')
        self.assertEqual(sf.named_subject('send me the Nike profile as a csv'), 'Nike')
        self.assertTrue(sf.parse('email me the excel of this journey'))
        self.assertEqual(sf.named_subject('email me the excel of this journey'), '')

    def test_questions_and_builds_do_not_parse(self):
        for t in ('email me when it is ready', 'what platforms do Glen Powell fans stream on', 'build me a Nike profile',
                  'Young Sheldon on Netflix', 'how many people watched Young Sheldon on Netflix'):
            self.assertFalse(sf.parse(t), t)

    def test_follows_clarifier(self):
        hist = [{'role': 'user', 'text': 'email me the excel file'},
                {'role': 'agent', 'text': sf.CLARIFIER_LEAD + ' Open the profile or journey you want and ask again.'}]
        self.assertTrue(sf.follows_clarifier(hist))
        self.assertFalse(sf.follows_clarifier(hist + [{'role': 'agent', 'text': 'Sent the workbook.'}]))
        self.assertFalse(sf.follows_clarifier([]))


class Matching(unittest.TestCase):
    def test_the_users_journeys_and_the_name_match(self):
        h = _host()
        js = sf.user_journeys('cbisson', h)
        self.assertEqual([j['key'] for j in js], [YS_ALL_KEY, YS_KEY, KL_KEY])   # newest first, never another seat's run
        self.assertEqual([j['key'] for j in sf.match_journeys('Young Sheldon Netflix', js)], [YS_KEY])
        self.assertEqual([j['key'] for j in sf.match_journeys('Young Sheldon on Netflix', js)], [YS_KEY])
        self.assertEqual(len(sf.match_journeys('Young Sheldon', js)), 2)
        self.assertEqual([j['key'] for j in sf.match_journeys('Knots Landing', js)], [KL_KEY])
        self.assertEqual(sf.match_journeys('Babylon 5', js), [])

    def test_the_open_journey_from_the_page_or_the_thread_binding(self):
        h = _host(threads={'threads': [
            {'id': 'a', 'opened': '2026-11-02T22:59:23Z', 'binding': {'subject_key': 'journey:' + YS_KEY, 'product': 'journeyIQ'}},
            {'id': 'b', 'opened': '2026-10-20T17:22:41Z', 'binding': {'subject_key': 'journey:' + KL_KEY, 'product': 'journeyIQ'}}]})
        self.assertEqual(sf.open_journey_key(JOURNEY_PAGE, 'cbisson', h), YS_KEY)
        bare = {'view': 'journeyIQ', 'primary': None, 'view_context': {'view_id': 'journeyIQ', 'data': {'kpis': {}}}}
        self.assertEqual(sf.open_journey_key(bare, 'cbisson', h), YS_KEY)   # the newest binding on the seat
        self.assertEqual(sf.open_journey_key({'view': 'profile-iq', 'primary': {'s3_key': 'Nike.csv'}}, 'cbisson', h), '')


class Answer(unittest.TestCase):
    def _run(self, text, history=None, ctx=None, host=None, subjects=None):
        cap = Capture()
        out = sf.answer(text, 'cbisson', user=USER, ctx=ctx if ctx is not None else JOURNEY_PAGE, history=history or [],
                        host=host or _host(), send=cap, subjects=subjects)
        return out, cap

    def test_carolyns_first_turn_sends_the_workbook(self):
        out, cap = self._run('please email me the excel file of the Young Sheldon Netflix journey that is in my library')
        self.assertTrue(out.get('sent'), out)
        self.assertEqual(len(cap.calls), 1)
        call = cap.calls[0]
        self.assertEqual(call['to'], 'carolyn.bisson@wbd.com')
        self.assertEqual(call['subject'], 'Young Sheldon on Netflix: your Digital Journey workbook')
        self.assertIsNone(call['csv'])                                   # a workbook is not a .csv part
        files = call['files']
        self.assertEqual(len(files), 1)
        data, name, mime = files[0]
        self.assertEqual(name, 'Young_Sheldon_on_Netflix_Digital_Journey.xlsx')
        self.assertTrue(mime.endswith('spreadsheetml.sheet'))
        self.assertEqual(data[:2], b'PK')
        self.assertIn('Young Sheldon on Netflix Digital Journey workbook', call['body'])
        self.assertIn('Sent the workbook', out['reply'])
        for bad in ('synth', 'pipeline', 'panel', 'model'):
            self.assertNotIn(bad, call['body'].lower())

    def test_her_second_turn_completes_the_order(self):
        hist = [{'role': 'user', 'text': 'please email me the excel file of the Young Sheldon Netflix journey that is in my library'},
                {'role': 'agent', 'text': sf.CLARIFIER_LEAD + ' Open the profile or journey you want and ask again.'}]
        out, cap = self._run('Young Sheldon on Netflix - watch Journey excel format', history=hist)
        self.assertTrue(out.get('sent'), out)
        self.assertEqual(cap.calls[0]['files'][0][1], 'Young_Sheldon_on_Netflix_Digital_Journey.xlsx')

    def test_this_file_with_the_journey_open(self):
        out, cap = self._run('email me this file')
        self.assertTrue(out.get('sent'), out)
        self.assertEqual(cap.calls[0]['files'][0][1], 'Young_Sheldon_on_Netflix_Digital_Journey.xlsx')
        self.assertNotIn('latest read from this thread', cap.calls[0]['body'])

    def test_an_ambiguous_name_asks_unless_one_is_on_screen(self):
        out, cap = self._run('email me the Young Sheldon journey', ctx={'view': 'profile-iq', 'primary': None, 'view_context': None})
        self.assertFalse(out.get('sent'))
        self.assertIn('more than one journey', out['reply'])
        self.assertIn('Young Sheldon on Netflix', out['reply'])
        self.assertEqual(cap.calls, [])
        out2, cap2 = self._run('email me the Young Sheldon journey')      # the Netflix one is open
        self.assertTrue(out2.get('sent'))
        self.assertEqual(cap2.calls[0]['files'][0][1], 'Young_Sheldon_on_Netflix_Digital_Journey.xlsx')

    def test_the_readers_subjects_resolve_a_name_the_regex_missed(self):
        out, cap = self._run('could you mail over the Knots Landing one from my library please', ctx={'view': 'profile-iq', 'primary': None},
                             subjects=['Knots Landing'])
        self.assertTrue(out.get('sent'), out)
        self.assertEqual(cap.calls[0]['files'][0][1], 'Knots_Landing_on_Prime_Video_Digital_Journey.xlsx')

    def test_a_profile_with_a_read_that_carries_a_door_word_still_goes(self):
        # Armine Meseldzhyan (Lionsgate), 2026-11-03: "Email me this file" with New Heights Podcast Listeners open;
        # the thread's last read said "football-anchored" and the door refused the whole send on 'anchor'.
        host = _host()
        host.s3_client.objs['New_Heights_Podcast_Listeners_10_21_2026_21_17.csv'] = b'Column,Value\nBRAND INPUT,New Heights\n'
        host.s3_client.objs['New Heights Podcast Listeners - Avid Fan.csv'] = b'Column,Value\nBRAND INPUT,New Heights\n'
        ctx = {'view': 'profile-iq', 'primary': {'s3_key': 'New_Heights_Podcast_Listeners_10_21_2026_21_17.csv', 'name': 'New Heights Podcast Listeners'},
               'cuts': [{'s3_key': 'New Heights Podcast Listeners - Avid Fan.csv', 'name': 'New Heights Podcast Listeners - Avid Fan'}], 'view_context': None}
        read = ("On New Heights Podcast Listeners:\n\n43.1% of New Heights listeners, 7,876,358 people, already use a free ad-supported service.\n\n"
                "- The 1,380,038 Avid New Heights listeners are the sharpest slice: football-anchored, male-tilted and carrying the heaviest fantasy signals.\n"
                "- Panel reach on Tubi is 4,112,004.\n- Pluto TV follows at 2,901,117.")
        hist = [{'role': 'user', 'text': 'how many use FAST'}, {'role': 'agent', 'text': read, 'meta': {'read_job_id': 'abc'}}]
        out, cap = self._run('Email me this file', history=hist, ctx=ctx, host=host)
        self.assertTrue(out.get('sent'), out)
        call = cap.calls[0]
        self.assertEqual(call['csv_name'], 'New_Heights_Podcast_Listeners_Profile.csv')
        self.assertEqual(call['files'][0][1], 'New_Heights_Podcast_Listeners_Avid_Fan.csv')
        body = call['body']
        self.assertIn('football-centered', body)
        self.assertNotIn('anchored', body)
        self.assertNotIn('Panel reach', body)                   # a line the door would refuse is dropped
        self.assertIn('Pluto TV follows', body)
        from prometheus import outbound_mail as om
        self.assertEqual(om.banned_tokens(body), [])
        self.assertIn('Sent the profile and its cut', out['reply'])

    def test_a_refusal_never_shows_the_doors_reason(self):
        def refusing(**kw):
            return {'sent': False, 'reason': "banned vocabulary after scrub: ['anchor']"}
        out = sf.answer('email me this file', 'cbisson', user=USER, ctx=JOURNEY_PAGE, history=[], host=_host(), send=refusing)
        self.assertFalse(out.get('sent'))
        self.assertNotIn('banned', out['reply'])
        self.assertNotIn('scrub', out['reply'])
        self.assertIn('still in Digital Journey IQ', out['reply'])

    def test_mail_safe_rewords_then_drops(self):
        self.assertEqual(sf.mail_safe('The anchor brand is Tubi. A football-anchored read.'), 'The lead brand is Tubi. A football-centered read.')
        self.assertEqual(sf.mail_safe('Line one.\nThe pipeline built it.\nLine three.'), 'Line one.\nLine three.')
        self.assertEqual(sf.mail_safe('Only the panel.'), '')

    def test_nothing_in_hand_asks_once(self):
        out, cap = self._run('email me this file', ctx={'view': 'profile-iq', 'primary': None, 'view_context': None},
                             host=_host(threads={'threads': []}))
        self.assertTrue(out['reply'].startswith(sf.CLARIFIER_LEAD))
        self.assertIn('journey', out['reply'])
        self.assertEqual(cap.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
