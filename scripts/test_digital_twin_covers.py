#!/usr/bin/env python3
"""Digital Twin covers: official logos only. Hermetic: lookups are
injected, no network.
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
os.environ.setdefault('DTIQ_COVERS', '1')

from migration import digital_twin_covers as C  # noqa: E402
from migration.digital_twin_synthesis import index_entry  # noqa: E402


class CoverKind(unittest.TestCase):
    def test_everything_is_a_logo(self):
        self.assertEqual(C.cover_kind('Reba McEntire', 'talent', '', ''), 'logo')
        self.assertEqual(C.cover_kind('iJustine', 'audience', 'creator', ''), 'logo')
        self.assertEqual(C.cover_kind('Reba McEntire', 'audience', '', 'MUSICIAN/BAND'), 'logo')
        self.assertEqual(C.cover_kind('Gilmore Girls', 'ip', 'series', 'SERIES - DRAMA'), 'logo')
        self.assertEqual(C.cover_kind('Costco', 'brand', 'brand', 'RETAILERS'), 'logo')
        self.assertEqual(C.cover_kind('Gilmore Girls Hulu viewers', 'audience', 'series', ''), 'logo')
        self.assertEqual(C.cover_kind('Yellowstone', 'ip', 'series', 'SERIES'), 'logo')
        self.assertEqual(C.cover_kind('a diner after a scandal', 'idea', '', ''), 'logo')


class CleanEntity(unittest.TestCase):
    def test_drops_audience_noun(self):
        self.assertEqual(C.clean_entity('Gilmore Girls Hulu viewers'), 'Gilmore Girls')
        self.assertEqual(C.clean_entity('Reba McEntire fans'), 'Reba McEntire')
        self.assertEqual(C.clean_entity('Costco members'), 'Costco')

    def test_series_names_prefer_the_show(self):
        names = C.search_names('Yellowstone', 'ip', 'series')
        self.assertEqual(names[0], 'Yellowstone (TV series)')
        self.assertIn('Yellowstone', names)


class Resolve(unittest.TestCase):
    def setUp(self):
        C.configure()
        self.queries = []
        self.stored = []

        def profile(name):
            return {'Gilmore Girls': '/api/profile-image-file/lauren.jpg',
                    'Reba McEntire': '/api/profile-image-file/reba.jpg'}.get(name)

        def wiki(name):
            self.queries.append(('wiki', name))
            return {'Costco': 'https://example.com/costco-logo.png',
                    'Gilmore Girls (TV series)': 'https://example.com/gg-cast.png'}.get(name)

        def search(q):
            self.queries.append(('search', q))
            if 'logo' in q and 'Landman' in q:
                return 'https://example.com/landman-logo.png'
            if 'logo' in q and 'Gilmore Girls' in q:
                return 'https://example.com/gg-logo.png'
            if q.endswith(' photo'):
                return 'https://example.com/someone.jpg'
            return None

        def resolve(name, master):
            self.queries.append(('resolve', master, name))
            if master == 'TALENT':
                return 'https://example.com/headshot.jpg'
            if master == 'CONTENT':
                return 'https://example.com/poster.jpg'
            return None

        def download(url):
            return (b'img-' + url.encode('ascii')[:40], 'png')

        def store(data, ext):
            path = f'/api/profile-image-file/digital-twins/covers/{len(self.stored)}.{ext}'
            self.stored.append((data, ext, path))
            return path

        C.configure(profile=profile, wiki=wiki, search=search, resolve=resolve,
                    download=download, store=store, background=False)

    def tearDown(self):
        C.configure()

    def test_series_uses_logo_search_not_the_profile_headshot(self):
        url, mode, note = C.resolve_cover_url('Gilmore Girls', kind='ip', fmt='series')
        self.assertEqual(mode, 'logo')
        self.assertTrue(note.startswith('logo:'))
        self.assertTrue(url.startswith('/api/profile-image-file/digital-twins/covers/'))
        self.assertTrue(any('official logo' in q[1] for q in self.queries if q[0] == 'search'))
        self.assertFalse(any(q[0] == 'wiki' for q in self.queries))
        self.assertFalse(any(q[0] == 'resolve' for q in self.queries))

    def test_brand_uses_logo_search_then_wiki(self):
        url, mode, note = C.resolve_cover_url('Costco', kind='brand', fmt='brand')
        self.assertEqual(mode, 'logo')
        self.assertTrue(url.startswith('/api/profile-image-file/digital-twins/covers/'))
        self.assertTrue(note.startswith('wiki:'))
        self.assertFalse(any(q[0] == 'search' and q[1] == 'Costco' for q in self.queries))

    def test_logo_search_never_uses_the_bare_name(self):
        url, mode, _note = C.resolve_cover_url('Landman', kind='ip', fmt='series')
        self.assertEqual(mode, 'logo')
        self.assertTrue(url)
        searches = [q[1] for q in self.queries if q[0] == 'search']
        self.assertTrue(searches)
        for q in searches:
            self.assertIn('logo', q)
            self.assertNotEqual(q, 'Landman')
            self.assertNotEqual(q, 'Landman (TV series)')

    def test_talent_is_a_logo_not_a_photo(self):
        url, mode, note = C.resolve_cover_url('Taylor Sheridan', kind='talent')
        self.assertEqual(mode, 'logo')
        self.assertFalse(any(q[0] == 'search' and q[1].endswith(' photo') for q in self.queries))
        self.assertFalse(any(q[0] == 'resolve' and q[1] == 'TALENT' for q in self.queries))

    def test_attach_writes_meta_and_skips_a_second_pass(self):
        payload = {'meta': {'name': 'Costco', 'subject': 'Costco', 'subject_kind': 'brand', 'format': 'brand'}}
        self.assertTrue(C.attach_cover(None, payload))
        self.assertTrue(payload['meta']['hero_image'])
        self.assertEqual(payload['meta']['cover_kind'], 'logo')
        self.assertEqual(payload['meta']['cover_rev'], C.COVER_REV)
        first = payload['meta']['hero_image']
        self.assertFalse(C.attach_cover(None, payload))
        self.assertEqual(payload['meta']['hero_image'], first)

    def test_attach_replaces_a_profile_headshot(self):
        payload = {'meta': {
            'name': 'Gilmore Girls', 'subject': 'Gilmore Girls',
            'subject_kind': 'ip', 'format': 'series',
            'hero_image': '/api/profile-image-file/lauren.jpg',
            'cover_kind': 'logo',
        }}
        self.assertTrue(C.needs_cover({'key': 'k', **payload['meta']}))
        self.assertTrue(C.attach_cover(None, payload))
        self.assertNotEqual(payload['meta']['hero_image'], '/api/profile-image-file/lauren.jpg')
        self.assertEqual(payload['meta']['cover_rev'], C.COVER_REV)
        self.assertTrue(payload['meta']['hero_image'].startswith('/api/profile-image-file/digital-twins/covers/'))

    def test_index_carries_the_cover(self):
        payload = {'meta': {'name': 'Costco', 'subject': 'Costco', 'subject_kind': 'brand',
                            'hero_image': '/api/profile-image-file/x.png', 'cover_kind': 'logo',
                            'cover_rev': 2,
                            'size_us': 12, 'created_at': '2026-10-27T00:00:00Z'},
                   'runs': [], 'labs': {'spend': []}, 'panel_sessions': []}
        row = index_entry('digital-twins/a.json.gz', payload)
        self.assertEqual(row['hero_image'], '/api/profile-image-file/x.png')
        self.assertEqual(row['cover_kind'], 'logo')
        self.assertEqual(row['cover_rev'], 2)


class Schedule(unittest.TestCase):
    def tearDown(self):
        C.configure()

    def test_fills_a_missing_cover_and_saves(self):
        saved = []
        payload = {'meta': {'name': 'Costco', 'subject': 'Costco', 'subject_kind': 'brand', 'format': 'brand'}}
        C.configure(
            profile=lambda n: None,
            wiki=lambda n: 'https://example.com/costco-logo.png',
            search=lambda q: None,
            resolve=lambda n, m: None,
            download=lambda u: (b'logo', 'png'),
            store=lambda data, ext: '/api/profile-image-file/digital-twins/covers/1.png',
            background=False,
        )
        runs = [{'key': 'digital-twins/u/costco.json.gz', 'name': 'Costco'}]
        n = C.schedule_missing(
            object(), runs,
            load_fn=lambda s3, key: payload,
            save_fn=lambda s3, key, p: saved.append((key, p['meta'].get('hero_image'))),
        )
        self.assertEqual(n, 1)
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0][1])
        self.assertEqual(runs[0]['cover_kind'], 'logo')
        self.assertEqual(runs[0]['cover_rev'], C.COVER_REV)

    def test_skips_rows_that_already_have_a_current_logo(self):
        n = C.schedule_missing(
            object(),
            [{'key': 'k', 'hero_image': '/x.png', 'cover_rev': C.COVER_REV}],
            load_fn=lambda *a: None, save_fn=lambda *a: None,
        )
        self.assertEqual(n, 0)

    def test_queues_a_stale_profile_tile(self):
        saved = []
        payload = {'meta': {
            'name': 'Gilmore Girls', 'subject': 'Gilmore Girls',
            'subject_kind': 'ip', 'format': 'series',
            'hero_image': '/api/profile-image-file/lauren.jpg',
            'cover_kind': 'logo',
        }}
        C.configure(
            profile=lambda n: '/api/profile-image-file/lauren.jpg',
            wiki=lambda n: None,
            search=lambda q: 'https://example.com/gg-logo.png' if 'logo' in q else None,
            resolve=lambda n, m: None,
            download=lambda u: (b'logo', 'png'),
            store=lambda data, ext: '/api/profile-image-file/digital-twins/covers/gg.png',
            background=False,
        )
        runs = [{'key': 'digital-twins/u/gg.json.gz', 'name': 'Gilmore Girls',
                 'hero_image': '/api/profile-image-file/lauren.jpg', 'cover_kind': 'logo'}]
        n = C.schedule_missing(
            object(), runs,
            load_fn=lambda s3, key: payload,
            save_fn=lambda s3, key, p: saved.append(p['meta']['hero_image']),
        )
        self.assertEqual(n, 1)
        self.assertEqual(saved, ['/api/profile-image-file/digital-twins/covers/gg.png'])


if __name__ == '__main__':
    unittest.main()
