#!/usr/bin/env python3
"""Pilot Testing: a pasted watch link from any streamer or digital page.
Hermetic. No network.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('DTW_WATCH_URL_FETCH', '0')

from migration import digital_twin_pilot as PI  # noqa: E402
from migration.digital_twin_watch_url import (  # noqa: E402
    KIND_EMBED, KIND_PAGE, KIND_VIDEO, attach_media, parse_url, resolve,
)


def _twin():
    return {
        'meta': {'name': 'Gilmore Girls', 'key': 'gg', 'subject': 'Gilmore Girls'},
        'identity': {'demographics': {
            'gender': [{'label': 'Female', 'pct': 71.2}, {'label': 'Male', 'pct': 28.8}],
            'age': [{'label': '25-34', 'pct': 40.0}, {'label': '35-44', 'pct': 60.0}],
        }},
        'traits': {},
        'lifecycle': {'habit_pct': 40.0, 'discovering_pct': 20.0, 'trying_pct': 15.0,
                      'lapsing_pct': 15.0, 'returning_pct': 10.0},
    }


class Parse(unittest.TestCase):
    def test_paramount_video_id(self):
        rec = resolve('https://www.paramountplus.com/shows/video/ALVE01M3QWCNZYE2VR099PZGTZH04W/')
        self.assertEqual(rec['platform'], 'Paramount Plus')
        self.assertEqual(rec['platform_label'], 'Paramount+')
        self.assertEqual(rec['content_id'], 'ALVE01M3QWCNZYE2VR099PZGTZH04W')
        self.assertEqual(rec['kind'], KIND_PAGE)
        self.assertEqual(rec['source'], 'watch_url')
        self.assertIn('paramountplus.com', rec['url'])
        self.assertGreater(rec['runtime_s'], 2000)
        self.assertIn('Paramount+', rec['open_label'])

    def test_any_streamer(self):
        cases = [
            ('https://www.netflix.com/watch/80192098', 'Netflix', KIND_PAGE),
            ('https://www.hulu.com/watch/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'Hulu', KIND_PAGE),
            ('https://www.max.com/show/eeeeeeee-ffff-0000-1111-222222222222', 'Max', KIND_PAGE),
            ('https://www.disneyplus.com/play/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'Disney Plus', KIND_PAGE),
            ('https://www.peacocktv.com/watch/playback/vod/_/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'Peacock', KIND_PAGE),
            ('https://www.amazon.com/gp/video/detail/B09N8M8K2R', 'Amazon', KIND_PAGE),
            ('https://tv.apple.com/us/show/landman/umc.cmc.abc123xyz789', 'Apple TV', KIND_PAGE),
            ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'YouTube', KIND_EMBED),
            ('https://youtu.be/dQw4w9WgXcQ', 'YouTube', KIND_EMBED),
            ('https://vimeo.com/347119375', 'Vimeo', KIND_EMBED),
            ('https://files.example.com/cuts/pilot.mp4', 'Example', KIND_VIDEO),
        ]
        for url, platform, kind in cases:
            rec = resolve(url)
            self.assertEqual(rec['platform'], platform, url)
            self.assertEqual(rec['kind'], kind, url)
            self.assertTrue(rec['url'].startswith('http'), url)

    def test_youtube_embeds_in_stage(self):
        rec = resolve('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertIn('youtube.com/embed/dQw4w9WgXcQ', rec['embed_url'])

    def test_direct_file_plays_in_stage(self):
        rec = resolve('https://files.example.com/season-2/episode-1.webm')
        self.assertEqual(rec['kind'], KIND_VIDEO)
        self.assertTrue(rec['embed_url'].endswith('.webm'))
        self.assertEqual(rec['title'], 'Episode 1')

    def test_show_slug_becomes_the_title(self):
        rec = resolve('https://www.paramountplus.com/shows/landman/')
        self.assertEqual(rec['title'], 'Landman')

    def test_hint_title_wins(self):
        rec = resolve('https://www.netflix.com/watch/80192098', hint_title='Gilmore Girls')
        self.assertEqual(rec['title'], 'Gilmore Girls')

    def test_user_runtime_wins(self):
        rec = resolve('https://www.hulu.com/watch/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', runtime_s=2617)
        self.assertEqual(rec['runtime_s'], 2617)

    def test_not_a_url(self):
        self.assertEqual(resolve('just some words'), {})
        self.assertEqual(resolve(''), {})
        self.assertEqual(parse_url('ftp://x.com/a'), {})

    def test_bare_host_gets_https(self):
        rec = resolve('netflix.com/title/80192098')
        self.assertTrue(rec['url'].startswith('https://'))
        self.assertEqual(rec['content_id'], '80192098')

    def test_fetch_can_name_the_page(self):
        html = '<html><meta property="og:title" content="Landman | Paramount+"><meta duration="2581"></html>'

        def fetch(_url):
            return 200, html, _url

        rec = resolve('https://www.paramountplus.com/shows/video/ALVE01M3QWCNZYE2VR099PZGTZH04W/', fetch=fetch)
        self.assertEqual(rec['title'], 'Landman')
        self.assertEqual(rec['runtime_s'], 2581)

    def test_no_em_dash_or_internal_words(self):
        rec = resolve('https://www.paramountplus.com/shows/video/ALVE01M3QWCNZYE2VR099PZGTZH04W/')
        blob = ' '.join(str(v) for v in rec.values())
        self.assertNotIn('\u2014', blob)
        for tok in ('synth', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap'):
            self.assertNotIn(tok, blob.lower())


class Attach(unittest.TestCase):
    def test_stamps_the_session(self):
        sess = PI.open_session([_twin()], title='Landman S1', in_room=8, session_id='wurl')
        rec = PI.apply_watch_url(sess, 'https://www.paramountplus.com/shows/video/ALVE01M3QWCNZYE2VR099PZGTZH04W/',
                                 hint_title='Landman')
        self.assertEqual(rec['content_id'], 'ALVE01M3QWCNZYE2VR099PZGTZH04W')
        m = sess['media']
        self.assertEqual(m['source'], 'watch_url')
        self.assertEqual(m['platform_label'], 'Paramount+')
        self.assertEqual(m['title'], 'Landman')
        self.assertTrue(m['url'])
        self.assertTrue(m['hash'])
        pub = PI.public_session(sess)
        self.assertEqual(pub['media']['source'], 'watch_url')
        self.assertEqual(pub['media']['url'], m['url'])
        self.assertNotIn('s3_key', pub['media'])

    def test_leaves_an_uploaded_file(self):
        media = {'s3_key': 'digital-twins/pilot-tests/x/cut.mp4', 'title': 'Cut', 'source': 'upload'}
        rec = resolve('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        out = attach_media(media, rec)
        self.assertEqual(out['s3_key'], media['s3_key'])
        self.assertEqual(out['source'], 'upload')
        self.assertEqual(out['watch_url'], rec['url'])

    def test_prepare_runs_the_full_clock(self):
        sess = PI.open_session([_twin()], title='Landman episode', in_room=6, session_id='wprep', mode='episode')
        PI.apply_watch_url(sess, 'https://www.paramountplus.com/shows/video/ALVE01M3QWCNZYE2VR099PZGTZH04W/',
                           hint_title='Landman', runtime_s=2543, mode='episode')
        PI.prepare_watch(sess, {'gg': _twin()}, duration_s=2543, title='Landman')
        self.assertEqual(sess['status'], 'ready')
        self.assertGreater(len(sess['dial']['value']), 8)
        self.assertTrue(sess.get('events'))
        self.assertTrue(sess.get('scorecard'))
        self.assertTrue((sess.get('guide') or {}).get('questions'))


class Page(unittest.TestCase):
    def test_start_form_has_the_link_field(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('id="dtwPiWatchUrl"', page)
        self.assertIn('watch_url', page)
        self.assertIn('any streamer', page.lower())
        self.assertNotIn('Screen Engine', page[page.find('function renderPilotStart'):page.find('function renderPilotStart') + 8000])


if __name__ == '__main__':
    unittest.main()
