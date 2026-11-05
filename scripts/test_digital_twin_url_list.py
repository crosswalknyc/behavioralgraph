#!/usr/bin/env python3
"""A digital twin built from a list of content URLs (Jenna 2026-11-04).
Hermetic: a fake S3, a fake queue, no model, no network.

Covers: URL extraction from CSV / TXT / XLSX / pasted text (bad rows
dropped, hosts summarized), the category by the hosts, the Prometheus
intake detection and the confirm card, the spec body (the engine's
marker, the seed key, BRAND INPUT CSV, the subject, the category, the
window, the requester), the chaining (fake queue, fake profile landing,
the twin built from the landed profile), the failure path (refund, the
partner-safe line) and the price line.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from prometheus import twin_url_list as T  # noqa: E402

WATCH = ['https://www.netflix.com/title/70155618', 'https://www.netflix.com/watch/81511993',
         'https://www.hulu.com/series/gilmore-girls-6ea1c1d0-4bb3-41e2-9cd2-7f6cf3c6e5ea',
         'https://www.paramountplus.com/shows/yellowstone/', 'https://www.paramountplus.com/shows/landman/',
         'https://www.youtube.com/watch?v=dQw4w9WgXcQ']
READ = ['https://www.nytimes.com/2026/10/01/arts/television/ranch-dramas.html',
        'https://www.vulture.com/article/landman-season-2-review.html']
PODS = ['https://podcasts.apple.com/us/podcast/smartless/id1521578868', 'https://open.spotify.com/show/0Yzd0g8NYmn27k2HFNplv7',
        'https://www.iheart.com/podcast/1119-crime-junkie-29319113/']


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')


def _xlsx(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class Extraction(unittest.TestCase):
    def test_csv_with_a_named_link_column_and_bad_rows(self):
        data = ('title,url,notes\nYellowstone,https://www.paramountplus.com/shows/yellowstone/,x\n'
                'Landman,https://www.paramountplus.com/shows/landman/,y\nGilmore,https://www.netflix.com/title/70155618,\n'
                'bad,not a url,\n,https://www.nytimes.com/2026/10/01/arts/television/ranch-dramas.html,\n'
                'dup,HTTPS://www.netflix.com/title/70155618/,\n').encode('utf-8')
        urls, dropped = T.urls_from_bytes('list.csv', data)
        self.assertEqual(len(urls), 4)
        self.assertEqual(dropped, 1)
        self.assertTrue(all(u.startswith('https://') for u in urls))

    def test_csv_with_no_header_and_a_personal_column_keeps_only_the_links(self):
        data = ('https://www.hulu.com/series/x-1,jane@example.com\nhttps://www.hulu.com/series/x-2,john@example.com\n').encode('utf-8')
        urls, dropped = T.urls_from_bytes('rows.csv', data)
        self.assertEqual(urls, ['https://www.hulu.com/series/x-1', 'https://www.hulu.com/series/x-2'])
        items = T.classify(urls)
        self.assertFalse(any('@' in json.dumps(it) for it in items))
        self.assertNotIn(b'example.com', T.seed_csv_bytes(items))

    def test_txt_and_pasted_text(self):
        text = 'Watch these:\nhttps://www.netflix.com/title/70155618\nwww.vulture.com/article/landman.html\nnot a link\nhttps://youtu.be/dQw4w9WgXcQ)\n'
        urls, dropped = T.urls_from_bytes('links.txt', text.encode('utf-8'))
        self.assertEqual(urls, ['https://www.netflix.com/title/70155618', 'https://youtu.be/dQw4w9WgXcQ', 'https://www.vulture.com/article/landman.html'])
        urls2, _d = T.urls_from_text('see https://www.max.com/shows/the-pitt, and https://www.peacocktv.com/watch/asset/tv/poker-face')
        self.assertEqual(len(urls2), 2)
        self.assertFalse(urls2[0].endswith(','))

    def test_xlsx_first_sheet_link_column(self):
        data = _xlsx([['Title', 'Link'], ['Yellowstone', 'https://www.paramountplus.com/shows/yellowstone/'], ['Landman', 'https://www.paramountplus.com/shows/landman/'], ['nothing', '']])
        urls, dropped = T.urls_from_bytes('list.xlsx', data)
        self.assertEqual(len(urls), 2)
        self.assertEqual(dropped, 1)

    def test_private_and_bad_schemes_are_dropped(self):
        urls, dropped = T.urls_from_text('ftp://files.example.com/a http://localhost/x mailto:a@b.com https://192.168.1.1/x https://www.cnn.com/2026/one')
        self.assertEqual(urls, ['https://www.cnn.com/2026/one'])

    def test_hosts_and_kinds_summarized(self):
        items = T.classify(WATCH + READ)
        s = T.summarize(items)
        self.assertEqual(s['count'], 8)
        self.assertEqual(s['hosts'][0], ('netflix.com', 2))
        self.assertEqual(s['kinds']['watch'], 6)
        self.assertEqual(s['kinds']['article'], 2)
        self.assertEqual(s['platforms']['Netflix'], 2)
        line = T.list_words({'items': items, 'summary': s, 'count': 8, 'dropped': 2})
        self.assertIn('8 URLs', line)
        self.assertIn('6 watch pages', line)
        self.assertIn('2 rows with no usable link dropped', line)
        self.assertNotIn('\u2014', line)


class Category(unittest.TestCase):
    def test_watch_lists_read_as_streaming(self):
        cat, why = T.brand_category_for(T.summarize(T.classify(WATCH + READ)))
        self.assertEqual(cat, 'STREAMING/PLATFORM')
        self.assertIn('watch pages', why)

    def test_article_lists_read_as_media(self):
        cat, why = T.brand_category_for(T.summarize(T.classify(READ)))
        self.assertEqual(cat, 'MEDIA')

    def test_podcast_lists_read_as_podcast(self):
        cat, _why = T.brand_category_for(T.summarize(T.classify(PODS)))
        self.assertEqual(cat, 'PODCAST')

    def test_a_tie_reads_as_media(self):
        cat, why = T.brand_category_for(T.summarize(T.classify(WATCH[:2] + READ)))
        self.assertEqual(cat, 'MEDIA')

    def test_seed_platforms_use_the_content_map_spelling(self):
        items = T.classify(WATCH + READ)
        self.assertEqual(T.seed_platforms(items)[:2], ['Netflix', 'Paramount Plus'])
        self.assertEqual(len(T.seed_urls(items)), 6)
        self.assertNotIn(READ[0], T.seed_urls(items))


class Detection(unittest.TestCase):
    def test_list_orders_fire_only_with_url_rows(self):
        self.assertTrue(T.is_list_twin_ask('build a twin from these urls', True))
        self.assertTrue(T.is_list_twin_ask('make a digital twin of the people who watched these', True))
        self.assertTrue(T.is_list_twin_ask('Build a twin from the attached list and call it Fall readers', True))
        self.assertTrue(T.is_list_twin_ask('build a twin of the people who read these https://www.cnn.com/a https://www.cnn.com/b', False))
        self.assertFalse(T.is_list_twin_ask('build a twin from these urls', False))
        self.assertFalse(T.is_list_twin_ask('build a twin of Gilmore Girls Hulu viewers', True))
        self.assertFalse(T.is_list_twin_ask('open a focus group with the Gilmore Girls twin', True))
        self.assertFalse(T.is_list_twin_ask('what are the twin peaks links', True))

    def test_name_and_window_from_the_words(self):
        self.assertEqual(T.name_from_text('build a twin from these urls and call it Yellowstone and Landman viewers.'), 'Yellowstone and Landman viewers')
        self.assertEqual(T.name_from_text('name: Our fall campaign readers'), 'Our fall campaign readers')
        self.assertEqual(T.name_from_text('make a twin of these, "Ranch drama fans"'), 'Ranch drama fans')
        self.assertEqual(T.name_from_text('build a twin from these urls'), '')
        self.assertEqual(T.bare_name('Our fall campaign readers'), 'Our fall campaign readers')
        self.assertEqual(T.bare_name('what is the price?'), '')
        self.assertEqual(T.bare_name('https://www.cnn.com/x'), '')
        w = T.window_from_text('for calendar 2025')
        self.assertEqual((w['start'], w['end']), ('2025-01-01', '2025-12-31'))
        self.assertEqual(T.window_from_text('2025-02-01 to 2025-04-30')['label'], '2025-02-01 to 2025-04-30')
        self.assertEqual(T.window_from_text('build it')['label'], 'Trailing 12 months')
        self.assertIn('Trailing 12 months (', T.window_words(T.default_window()))


class _FakeHost:
    def __init__(self, s3):
        self.s3_client = s3
        self.S3_BUCKET = 'b'


def _thread_with_sheet(s3, uname, tid, name, data, url_count=3):
    from prometheus import documents as _docs
    base = f"{_docs.UPLOAD_PREFIX}{uname}/{tid}/abc_"
    s3.put_object(Bucket='b', Key=base + name, Body=data)
    idx = {'attachments': [{'upload_id': 'abc', 'name': name, 'kind': 'sheet' if name.endswith('.csv') else 'text', 'file_key': base + name,
                            'sheets': [{'name': 'Sheet1', 'url_count': url_count}], 'excerpt': data.decode('utf-8')[:200], 'uploaded_at': '2026-11-04T10:00:00Z'}]}
    s3.put_object(Bucket='b', Key=f"{_docs.UPLOAD_PREFIX}{uname}/{tid}/index.json", Body=json.dumps(idx))


class _Pending:
    def __init__(self):
        self.flows = {}

    def __call__(self, op, user, flow, *args):
        from prometheus import pending_intake as _pi
        if op == 'stash':
            self.flows[flow] = {'payload': args[0], 'label': args[1] if len(args) > 1 else ''}
            return True
        if op == 'peek':
            return (self.flows.get(flow) or {}).get('payload')
        if op == 'clear':
            self.flows.pop(flow, None)
            return True
        hit = self.flows.get(flow)
        if hit and _pi.is_run_order(args[0] if args else ''):
            self.flows.pop(flow, None)
            return hit['payload']
        return None


class PrometheusDoor(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.host = _FakeHost(self.s3)
        self.user = {'username': 'carolyn', 'email': 'carolyn@example.com', 'role': 'user'}
        self.pending = _Pending()
        data = ('url\n' + '\n'.join(WATCH[:3] + READ) + '\nnot a url\n').encode('utf-8')
        _thread_with_sheet(self.s3, 'carolyn', 't1', 'content.csv', data)
        self.started = []
        self.charges = []

        def start_fn(kind, job_id, username, inputs, extras, status_write, runner):
            self.started.append((kind, job_id, username, inputs))
            return {'job_id': job_id, 'status': 'queued'}
        self.start_fn = start_fn

        def consume(uname, description=None, pull_type=None, credits_used=0, job_id=None, **kw):
            self.charges.append((uname, pull_type, credits_used, job_id))
            return True
        self.consume = consume

    def _hook(self, text, history=None, consume=None):
        return T.analyze_hook(text, {'thread_id': 't1'}, history or [], self.user, {}, 'carolyn', host=self.host, pending=self.pending,
                              start_fn=self.start_fn, consume_credit=consume or self.consume, is_free=lambda u, n: False)

    def test_an_ordinary_twin_ask_is_not_this_door(self):
        self.assertIsNone(self._hook('build a twin of Gilmore Girls Hulu viewers'))
        self.assertIsNone(self._hook('how big is the Costco audience?'))

    def test_order_without_a_name_reads_the_list_back_and_asks_the_name(self):
        out = self._hook('make a digital twin of the people who watched these')
        self.assertIsNotNone(out)
        self.assertIn('I read the list: 5 URLs', out['reply'])
        self.assertIn('1 row with no usable link dropped', out['reply'])
        self.assertIn(T.ASK_NAME, out['reply'])
        self.assertTrue(out.get('twin_list_collect'))
        self.assertEqual(self.started, [])
        self.assertEqual(self.charges, [])
        # the name, bare, completes the brief
        hist = [{'role': 'user', 'text': 'make a digital twin of the people who watched these'}, {'role': 'agent', 'text': out['reply']}]
        card = self._hook('Yellowstone and Landman viewers', hist)
        self.assertIn('Here is the twin brief:', card['reply'])
        self.assertIn('- Audience: Yellowstone and Landman viewers', card['reply'])
        self.assertIn('- Defined by: 5 URLs', card['reply'])
        self.assertIn('US people who watched, read or visited this content in the window', card['reply'])
        self.assertIn('- Window: Trailing 12 months (', card['reply'])
        self.assertIn('- Category: STREAMING/PLATFORM', card['reply'])
        self.assertIn('Price: $1,050 charged now ($300 for the audience build and $750 for the twin with its first two runs); each further run $100.', card['reply'])
        self.assertIn("Say 'Build the twin'", card['reply'])
        self.assertEqual(card['followups'], ['Build the twin', 'Cancel'])
        self.assertTrue(card['twin_list_confirm_payload']['list_key'].startswith(T.LIST_PREFIX))
        for banned in ('synth', 'pipeline', 'hostmap', 'Claude', '\u2014', 'queue'):
            self.assertNotIn(banned, card['reply'])
        # the confirm charges both once and queues the twin job with the list on it
        hist2 = hist + [{'role': 'user', 'text': 'Yellowstone and Landman viewers'}, {'role': 'agent', 'text': card['reply']}]
        go = self._hook('Build the twin', hist2)
        self.assertEqual(go.get('job_type'), 'twin_build')
        self.assertIn('Pulling the Yellowstone and Landman viewers audience from your list first', go['reply'])
        self.assertEqual(len(self.started), 1)
        kind, job_id, username, inputs = self.started[0]
        self.assertEqual((kind, username), ('twin_build', 'carolyn'))
        self.assertEqual(inputs['subject'], 'Yellowstone and Landman viewers')
        self.assertEqual(inputs['source'], 'profile')
        self.assertTrue(inputs['url_list_key'].startswith(T.LIST_PREFIX))
        self.assertTrue(inputs['profile_run_id'])
        self.assertEqual(inputs['charged'], {'profile': T.PROFILE_CREDITS, 'twin': T.TWIN_CREDITS})
        self.assertEqual(inputs['requester_email'], 'carolyn@example.com')
        self.assertEqual([c[1] for c in self.charges], ['Chatbot Profile IQ (new_build)', 'Digital Twins IQ'])
        self.assertEqual(self.charges[0][3], inputs['profile_run_id'])   # the audience charge carries the run id the engine refunds by
        self.assertEqual(self.charges[1][3], job_id)

    def test_name_and_window_in_the_order_go_straight_to_the_card(self):
        card = self._hook('Build a twin from the attached list for calendar 2025 and call it Ranch drama fans.')
        self.assertIn('- Audience: Ranch drama fans', card['reply'])
        self.assertIn('- Window: Calendar 2025 (Jan 1, 2025 to Dec 31, 2025)', card['reply'])

    def test_pasted_urls_in_the_message_are_the_list(self):
        s3 = FakeS3()
        host = _FakeHost(s3)
        out = T.analyze_hook('make a twin of the people who read these, call it Two story readers: ' + ' '.join(READ), {'thread_id': 't9'}, [], self.user, {}, 'carolyn',
                             host=host, pending=self.pending, start_fn=self.start_fn, consume_credit=self.consume, is_free=lambda u, n: False)
        self.assertIsNotNone(out)
        self.assertIn('- Audience: Two story readers', out['reply'])
        self.assertIn('- Category: MEDIA', out['reply'])

    def test_funds_short_charges_nothing_and_says_so(self):
        calls = []

        def consume(uname, description=None, pull_type=None, credits_used=0, job_id=None, **kw):
            calls.append(pull_type)
            return False
        card = self._hook('Build a twin from these urls and call it Fall readers.')
        hist = [{'role': 'user', 'text': 'x'}, {'role': 'agent', 'text': card['reply']}]
        go = self._hook('Build the twin', hist, consume=consume)
        self.assertIn('cannot cover it right now', go['reply'])
        self.assertEqual(self.started, [])
        self.assertEqual(calls, ['Chatbot Profile IQ (new_build)'])

    def test_cancel_clears_the_brief(self):
        self._hook('Build a twin from these urls')
        out = self._hook('cancel', [{'role': 'agent', 'text': T.ASK_NAME}])
        self.assertIn('nothing was charged', out['reply'])
        self.assertIsNone(self.pending('peek', self.user, T.FLOW))


def _fake_spec_from_draft(d):
    """The chat converter's shape, hermetically: name, stem, category, the
    messy sample, the rows, the demos, the notes."""
    return {'name': d.get('subject'), 'file_stem': str(d.get('subject') or '').replace(' ', '_'), 'brand_category': d.get('brand_category'),
            'subject_raw_tu': 48_317, 'subject_raw_avid': 10_631, 'subject_rows': [tuple(r) for r in d.get('subject_rows') or []],
            'extra_rows': list(d.get('extra_rows') or []), 'tu_demos': d.get('tu_demos') or {}, 'avid_demos': d.get('avid_demos') or d.get('tu_demos') or {},
            'persona_notes': d.get('persona_notes') or '', 'date_range': d.get('date_range'), 'addon_cuts': d.get('addon_cuts') or []}


def _rec(urls, name='Yellowstone and Landman viewers'):
    items = T.classify(urls)
    for it in items:
        if 'yellowstone' in it['url']:
            it['title'] = 'Yellowstone'
        if 'landman' in it['url']:
            it['title'] = 'Landman'
    s = T.summarize(items)
    cat, why = T.brand_category_for(s)
    return {'key': T.list_key('tok123'), 'token': 'tok123', 'owner': 'carolyn', 'name': name, 'items': items, 'summary': s, 'count': len(items),
            'dropped': 0, 'brand_category': cat, 'category_reason': why, 'window': T.default_window()}


class SpecBody(unittest.TestCase):
    def test_the_body_the_engine_receives(self):
        rec = _rec(WATCH + READ)
        draft = {'subject': 'Something the model renamed', 'brand_category': 'SERIES', 'decision': 'existing_match', 'existing_match_s3_key': 'Yellowstone_01_01_2026_10_00.csv',
                 'addon_cuts': [{'type': 'gender', 'id': 'female'}], 'seed_source': 'content_map', 'content_show': 'Yellowstone',
                 'subject_rows': [['SERIES', 'Yellowstone'], ['SERIES', 'Landman'], ['STREAMING/PLATFORM', 'Netflix']],
                 'tu_demos': {'GENDER': {'FEMALE': 52.1317, 'MALE': 46.7211, 'NON-BINARY': 1.1472}}, 'persona_notes': 'Grown-up drama viewers.',
                 'date_range': {'start': '2020-01-01', 'end': '2020-12-31'}}
        window = {'start': '2025-11-04', 'end': '2026-11-04'}
        body = T.spec_body(draft, name='Yellowstone and Landman viewers', rec=rec, window=window, run_id='run_abc', seed_key='system/universe_seeds/run_abc.csv',
                           requester={'username': 'carolyn', 'email': 'carolyn@example.com'}, spec_from_draft=_fake_spec_from_draft)
        spec = body['spec']
        # the engine's own marker: BRAND INPUT reads the literal CSV from this pair
        self.assertIs(spec['universe_seed_csv'], True)
        self.assertEqual(spec['seed_file'], 'system/universe_seeds/run_abc.csv')
        # the user's name is the subject, whatever the model said; a fresh build, no cuts, no content-map lookup
        self.assertEqual(spec['name'], 'Yellowstone and Landman viewers')
        self.assertEqual(spec['file_stem'], 'Yellowstone_and_Landman_viewers')
        self.assertEqual(body['decision'], 'new_build')
        self.assertTrue(body['run_avid'])
        self.assertEqual(spec['addon_cuts'], [])
        self.assertIsNone(spec['seed_source'])
        self.assertIsNone(spec['content_show'])
        # the category by the hosts, the window as the engine's string
        self.assertEqual(spec['brand_category'], 'STREAMING/PLATFORM')
        self.assertEqual(spec['date_range'], '2025-11-04 TO 2026-11-04')
        # the watch URLs ride the content seed the way a viewers profile's do; the articles do not
        self.assertEqual(len(spec['content_seed_urls']), 6)
        self.assertNotIn(READ[0], spec['content_seed_urls'])
        self.assertEqual(spec['content_seed_platforms'][0], 'Netflix')
        # a multi-title list pins no single title and no single platform; the subject's own row anchors
        self.assertEqual(spec['subject_rows'], [('STREAMING/PLATFORM', 'Yellowstone and Landman viewers')])
        self.assertIn(['SERIES', 'Yellowstone'], spec['extra_rows'])
        self.assertIn(['STREAMING/PLATFORM', 'Netflix'], spec['extra_rows'])
        # the brief leads with the universe definition
        self.assertTrue(spec['persona_notes'].startswith('UNIVERSE DEFINITION: Yellowstone and Landman viewers = US people who watched, read or visited any of 8 listed content pages'))
        self.assertIn('Grown-up drama viewers.', spec['persona_notes'])
        self.assertEqual(spec['url_list']['count'], 8)
        # the requester rides the body so the engine's completion notice and registration happen as for any build
        self.assertEqual(body['user_email'], 'carolyn@example.com')
        self.assertEqual(body['username'], 'carolyn')
        self.assertEqual(body['run_id'], 'run_abc')
        self.assertEqual(body['source'], 'digital_twins_url_list')
        self.assertEqual(body['email_to'], '')
        self.assertNotIn('\u2014', json.dumps(body))

    def test_the_prompt_defines_one_any_of_universe(self):
        rec = _rec(WATCH + READ)
        p = T.audience_prompt('Yellowstone and Landman viewers', rec, T.default_window())
        self.assertTrue(p.startswith('run a profile on Yellowstone and Landman viewers'))
        self.assertIn('any-of union', p)
        self.assertIn('- Yellowstone (Paramount+ watch page: https://www.paramountplus.com/shows/yellowstone/)', p)
        self.assertIn('(nytimes.com article:', p)
        self.assertIn("brand_category 'STREAMING/PLATFORM'", p)

    def test_fallback_draft_is_us_shaped_and_messy(self):
        rec = _rec(READ)
        d = T.fallback_draft('Two story readers', rec, T.default_window(), gen_pop_rows=[{'Column': 'GENDER', 'Value': 'FEMALE', 'Brand Penetration (Row)': '50.7391%'},
                                                                                            {'Column': 'GENDER', 'Value': 'MALE', 'Brand Penetration (Row)': '48.1227%'},
                                                                                            {'Column': 'AGE', 'Value': '18-24', 'Brand Penetration (Row)': '11.4731%'}])
        self.assertEqual(d['tu_demos']['GENDER']['FEMALE'], 50.7391)
        self.assertNotEqual(d['subject_raw_tu'] % 10, 0)
        self.assertEqual(d['brand_category'], 'MEDIA')
        self.assertEqual(d['subject_rows'], [['MEDIA', 'Two story readers']])

    def test_seed_csv_carries_the_url_rows_only(self):
        rec = _rec(WATCH[:2] + READ)
        rows = list(csv.reader(io.StringIO(T.seed_csv_bytes(rec['items']).decode('utf-8'))))
        self.assertEqual(rows[0], ['url', 'host', 'platform', 'kind', 'title'])
        self.assertEqual(len(rows), 5)


class Chaining(unittest.TestCase):
    def _inputs(self, rec, s3):
        s3.put_object(Bucket='b', Key=rec['key'], Body=json.dumps(rec))
        return {'subject': 'Yellowstone and Landman viewers', 'name': 'Yellowstone and Landman viewers', 'source': 'profile', 'from_list': True,
                'url_list_key': rec['key'], 'profile_run_id': 'run_xyz', 'charged': {'profile': 5, 'twin': 15}, 'requester_email': 'carolyn@example.com',
                'window': T.default_window(), 'thread_id': 't1', 'purpose': 'a new season'}

    def test_phase_one_queues_the_audience_with_the_twin_on_the_body_and_stops(self):
        s3 = FakeS3()
        rec = _rec(WATCH + READ)
        inputs = self._inputs(rec, s3)
        posted, stages, status = [], [], {}

        def post(body):
            posted.append(body)
            return 200, {'run_id': body['run_id'], 'status': 'queued'}
        out = T.run_pull_in_job(inputs, 'carolyn', 'job1', {'job_id': 'job1'}, lambda j, r: status.update(r), s3=s3, bucket='b', usage_extras={'user': 'carolyn'},
                                draft_fn=lambda p: None, spec_from_draft=_fake_spec_from_draft, post=post, status_fn=lambda rid: {'status': 'unknown'})
        self.assertIsNone(out)                      # the job stops; the engine hands the twin back when the audience lands
        self.assertEqual(status['status'], 'waiting')
        self.assertEqual(status['stage'], T.WAITING_STAGE)
        self.assertEqual(status['profile_run_id'], 'run_xyz')
        body = posted[0]
        self.assertEqual(body['run_id'], 'run_xyz')
        self.assertEqual(body['spec']['seed_file'], 'system/universe_seeds/run_xyz.csv')
        self.assertTrue(s3.objs['system/universe_seeds/run_xyz.csv'].startswith(b'url,host,platform,kind,title'))
        self.assertEqual(body['spec']['brand_category'], 'STREAMING/PLATFORM')
        self.assertTrue(body['spec']['persona_notes'].startswith('UNIVERSE DEFINITION'))
        then = body['then']
        self.assertEqual((then['kind'], then['job_id'], then['username']), ('twin_build', 'job1', 'carolyn'))
        self.assertEqual(then['inputs']['profile_key'], '')
        self.assertTrue(then['inputs']['from_list'])
        self.assertEqual(then['inputs']['url_list_key'], rec['key'])
        self.assertEqual(then['charged'], {'profile': 5, 'twin': 15})
        self.assertEqual(then['extras'], {'user': 'carolyn'})
        self.assertNotIn('pull_profile', then['inputs'])

    def test_a_restarted_job_never_posts_the_same_audience_twice(self):
        s3 = FakeS3()
        rec = _rec(WATCH + READ)
        inputs = self._inputs(rec, s3)
        posted, status = [], {}
        out = T.run_pull_in_job(inputs, 'carolyn', 'job1', {'job_id': 'job1'}, lambda j, r: status.update(r), s3=s3, bucket='b', draft_fn=lambda p: None,
                                spec_from_draft=_fake_spec_from_draft, post=lambda b: posted.append(b) or (200, {'run_id': 'x'}),
                                status_fn=lambda rid: {'status': 'running', 'run_id': rid})
        self.assertIsNone(out)
        self.assertEqual(posted, [])
        self.assertEqual(status['status'], 'waiting')
        # the engine's duplicate guard reads the same way
        out2 = T.run_pull_in_job(inputs, 'carolyn', 'job1', {'job_id': 'job1'}, lambda j, r: status.update(r), s3=s3, bucket='b', draft_fn=lambda p: None,
                                 spec_from_draft=_fake_spec_from_draft, post=lambda b: (409, {'error': 'a build for this subject is already in progress'}),
                                 status_fn=lambda rid: {'status': 'unknown'})
        self.assertIsNone(out2)
        self.assertEqual(status['status'], 'waiting')

    def test_an_audience_that_already_landed_continues_into_the_build(self):
        s3 = FakeS3()
        rec = _rec(WATCH + READ)
        inputs = self._inputs(rec, s3)
        status = {}
        out = T.run_pull_in_job(inputs, 'carolyn', 'job1', {'job_id': 'job1'}, lambda j, r: status.update(r), s3=s3, bucket='b', draft_fn=lambda p: None,
                                spec_from_draft=_fake_spec_from_draft, post=lambda b: (200, {'run_id': 'x'}),
                                status_fn=lambda rid: {'status': 'tu_done', 'tu_key': 'Yellowstone_and_Landman_viewers_11_04_2026_18_00.csv', 'tu_registered_in_dashboard': True})
        self.assertEqual(out, 'Yellowstone_and_Landman_viewers_11_04_2026_18_00.csv')
        self.assertEqual(status['profile_key'], out)

    def test_the_twin_is_built_from_the_landed_profile(self):
        from migration import digital_twin_synthesis as SYN
        s3 = FakeS3()
        key = 'Yellowstone_and_Landman_viewers_11_04_2026_18_00.csv'
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Column', 'Value', 'Brand Penetration (Row)', 'Original Raw Numbers', 'US Gen Pop Projection'])
        for r in (('BRAND INPUT', 'CSV', '100.0000%', '48317', '1593977'), ('SAMPLE SIZE', 'SAMPLE SIZE (2025-11-04 TO 2026-11-04)', '100.0000%', '48317', '1593977'),
                  ('BRAND CATEGORY', 'STREAMING/PLATFORM', '', '', ''), ('SUBJECT', 'Yellowstone and Landman viewers', '100.0000%', '48317', '1593977'),
                  ('GENDER', 'FEMALE', '52.1317%', '', ''), ('GENDER', 'MALE', '46.7211%', '', ''), ('GENDER', 'NON-BINARY', '1.1472%', '', ''),
                  ('AGE', '18-24', '8.1211%', '', ''), ('AGE', '25-34', '14.2211%', '', ''), ('AGE', '35-44', '17.3311%', '', ''), ('AGE', '45-54', '19.1211%', '', ''),
                  ('AGE', '55-64', '20.1211%', '', ''), ('AGE', '65 OR OLDER', '21.0845%', '', ''),
                  ('STREAMING/PLATFORM', 'Paramount+', '71.2311%', '', ''), ('STREAMING/PLATFORM', 'Netflix', '66.1211%', '', '')):
            w.writerow(r)
        s3.put_object(Bucket='dashboard-inputs', Key=key, Body=buf.getvalue())
        payload = SYN.build_twin({'subject': 'Yellowstone and Landman viewers', 'source': 'profile', 'profile_key': key, 'purpose': 'A new season', 'pull_profile': None},
                                 None, with_research=False, s3=s3)
        m = payload['meta']
        self.assertEqual(m['profile_key'], key)
        self.assertEqual(m['source'], 'profile')
        self.assertEqual(m['name'], 'Yellowstone and Landman viewers')
        self.assertGreater(m['size_us'], 1_000_000)
        gender = {r['label'].lower(): r['pct'] for r in payload['identity']['demographics']['gender']}
        self.assertAlmostEqual(gender['female'], 52.1, delta=0.5)

    def test_phase_two_runs_the_build_on_the_handed_back_key(self):
        """The engine's handoff: the job comes back queued with the landed
        profile key on its inputs; run_job skips the pull, builds from
        that profile, and the requester hears back by email too."""
        from prometheus import digital_twin_job as J
        from prometheus import host as _hostmod
        from migration import digital_twin_synthesis as SYN
        import prometheus.delivered as _delivered
        s3 = FakeS3()
        _hostmod.host.bind(s3_client=s3, bucket='b', ask_hint=lambda **k: None)
        seen = {}
        saved = (SYN.build_twin, SYN.persist, J._grant, J._thread_note, _delivered.on_done)
        try:
            SYN.build_twin = lambda inputs, cj, **kw: seen.setdefault('inputs', dict(inputs)) and {'meta': {'name': inputs['subject'], 'size_us': 2_345_671, 'profile_key': inputs['profile_key']}}
            SYN.persist = lambda s3_, payload, username, job_id: 'digital_twins/runs/yl.json.gz'
            J._grant = lambda username, key: seen.setdefault('grant', (username, key))
            J._thread_note = lambda username, tid, text, meta: seen.setdefault('note', text)
            _delivered.on_done = lambda kind, **kw: seen.setdefault('email', kw.get('email'))
            inputs = {'subject': 'Yellowstone and Landman viewers', 'name': 'Yellowstone and Landman viewers', 'source': 'profile', 'from_list': True,
                      'profile_key': 'Yellowstone_and_Landman_viewers_11_04_2026_18_00.csv', 'url_list_key': 'system/digital_twins/url_lists/tok.json',
                      'profile_run_id': 'run_xyz', 'charged': {'profile': 5, 'twin': 15}, 'thread_id': 't1', 'op': 'build'}
            s3.put_object(Bucket='b', Key=f"{J.JOB_PREFIX}job2.json", Body=json.dumps({'job_id': 'job2', 'user': 'carolyn', 'status': 'queued', 'queued': True, 'inputs': inputs}))
            rec = J.run_job('job2', 'carolyn', inputs, {})
        finally:
            SYN.build_twin, SYN.persist, J._grant, J._thread_note, _delivered.on_done = saved
        self.assertEqual(rec['status'], 'done')
        self.assertEqual(seen['inputs']['profile_key'], 'Yellowstone_and_Landman_viewers_11_04_2026_18_00.csv')
        self.assertEqual(seen['inputs']['source'], 'profile')
        self.assertIsNone(seen['inputs'].get('pull_profile'))
        self.assertIs(seen['email'], True)
        self.assertIn('built from the audience your list defines', rec['ready_message'])
        self.assertIn('The audience itself is in Select Profile', seen['note'])
        self.assertEqual(seen['grant'], ('carolyn', 'digital_twins/runs/yl.json.gz'))

    def test_a_pull_that_never_queues_refunds_both_with_the_partner_safe_line(self):
        s3 = FakeS3()
        rec = _rec(WATCH + READ)
        inputs = self._inputs(rec, s3)
        refunds = []
        import types
        fake_app = types.SimpleNamespace(refund_credit=lambda uname, credits=0, reason='': refunds.append((uname, credits, reason)) or True)
        saved = sys.modules.get('app')
        sys.modules['app'] = fake_app
        try:
            status = {}
            tu = T.run_pull_in_job(inputs, 'carolyn', 'job1', {'job_id': 'job1'}, lambda j, r: status.update(r), s3=s3, bucket='b', draft_fn=lambda p: None,
                                   spec_from_draft=_fake_spec_from_draft, post=lambda b: (400, {'error': 'spec validation: tu_demos must be a non-empty dict'}),
                                   status_fn=lambda rid: {'status': 'unknown'})
        finally:
            if saved is not None:
                sys.modules['app'] = saved
            else:
                sys.modules.pop('app', None)
        self.assertEqual(tu, '')
        self.assertEqual(status['status'], 'error')
        self.assertEqual(status['error'], T.FAIL_LINE)
        self.assertEqual(sorted(c for _u, c, _r in refunds), [5, 15])
        for banned in ('rebuild', 'rerun', 'held', 'queue', 'spec'):
            self.assertNotIn(banned, status['error'].lower())

    def test_an_engine_failure_is_the_engines_refund(self):
        """A prior attempt's run the engine already refused: the twin ends
        with the line here and nothing is refunded twice (the engine
        returned both charges through the run id and the then block)."""
        s3 = FakeS3()
        rec = _rec(WATCH + READ)
        inputs = self._inputs(rec, s3)
        refunds = []
        import types
        fake_app = types.SimpleNamespace(refund_credit=lambda uname, credits=0, reason='': refunds.append(credits) or True)
        saved = sys.modules.get('app')
        sys.modules['app'] = fake_app
        try:
            status = {}
            tu = T.run_pull_in_job(inputs, 'carolyn', 'job2', {'job_id': 'job2'}, lambda j, r: status.update(r), s3=s3, bucket='b', draft_fn=lambda p: None,
                                   spec_from_draft=_fake_spec_from_draft, post=lambda b: (200, {'run_id': b['run_id']}),
                                   status_fn=lambda rid: {'status': 'failed'})
        finally:
            if saved is not None:
                sys.modules['app'] = saved
            else:
                sys.modules.pop('app', None)
        self.assertEqual(tu, '')
        self.assertEqual(refunds, [])
        self.assertEqual(status['error'], T.FAIL_LINE)


class UploadAck(unittest.TestCase):
    def test_a_sheet_of_links_offers_the_twin(self):
        from prometheus import documents as _docs
        reply, chips = _docs.ack({'name': 'content.csv', 'kind': 'sheet', 'summary': '5 rows, 2 columns', 'excerpt': 'url\nhttps://www.netflix.com/title/1',
                                  'sheets': [{'name': 'Sheet1', 'url_count': 5}]})
        self.assertIn('Build a twin from these URLs', chips)
        self.assertIn('digital twin of the people who watched, read or visited them', reply)
        self.assertTrue(T.is_list_twin_ask('Build a twin from these URLs', True))
        reply2, chips2 = _docs.ack({'name': 'sales.csv', 'kind': 'sheet', 'summary': '5 rows', 'excerpt': 'a,b', 'sheets': [{'name': 'Sheet1', 'url_count': 0}]})
        self.assertNotIn('Build a twin from these URLs', chips2)


class JobHook(unittest.TestCase):
    def test_the_job_routes_a_list_build_through_the_pull(self):
        import inspect
        from prometheus import digital_twin_job as J
        src = inspect.getsource(J.run_job)
        self.assertIn("url_list_key", src)
        self.assertIn("_TUL.run_pull_in_job", src)
        self.assertIn("email=from_list", src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
