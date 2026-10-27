#!/usr/bin/env python3
"""Digital Twin covers: logos for titles and brands, a photo when the
subject is a person. Hermetic: lookups are injected, no network.
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
    def test_talent_is_a_person(self):
        self.assertEqual(C.cover_kind('Reba McEntire', 'talent', '', ''), 'person')
        self.assertEqual(C.cover_kind('iJustine', 'audience', 'creator', ''), 'person')
        self.assertEqual(C.cover_kind('Reba McEntire', 'audience', '', 'MUSICIAN/BAND'), 'person')

    def test_title_and_brand_are_logos(self):
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
            return {'Reba McEntire': '/api/profile-image-file/reba.jpg'}.get(name)

        def wiki(name):
            self.queries.append(('wiki', name))
            return {'Costco': 'https://example.com/costco-logo.png',
                    'Gilmore Girls (TV series)': 'https://example.com/gg-logo.png'}.get(name)

        def search(q):
            self.queries.append(('search', q))
            if 'logo' in q and 'Landman' in q:
                return 'https://example.com/landman-logo.png'
            if q.endswith(' photo'):
                return 'https://example.com/someone.jpg'
            return None

        def resolve(name, master):
            self.queries.append(('resolve', master, name))
            if master == 'TALENT' and name == 'Taylor Sheridan':
                return 'https://example.com/ts.jpg'
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

    def test_person_uses_profile_photo(self):
        url, mode, note = C.resolve_cover_url('Reba McEntire', kind='talent')
        self.assertEqual(mode, 'person')
        self.assertTrue(url.endswith('reba.jpg'))
        self.assertTrue(note.startswith('profile:'))
        self.assertFalse(any(q[0] == 'search' and 'logo' in q[1] for q in self.queries))

    def test_brand_uses_wiki_logo(self):
        url, mode, note = C.resolve_cover_url('Costco', kind='brand', fmt='brand')
        self.assertEqual(mode, 'logo')
        self.assertTrue(url.startswith('/api/profile-image-file/digital-twins/covers/'))
        self.assertTrue(note.startswith('wiki:'))
        self.assertFalse(any(q[0] == 'search' and q[1] == 'Costco' for q in self.queries))

    def test_series_uses_tv_series_wiki_then_logo_search(self):
        url, mode, note = C.resolve_cover_url('Gilmore Girls', kind='ip', fmt='series')
        self.assertEqual(mode, 'logo')
        self.assertTrue(note.startswith('wiki:'))
        self.assertIn(('wiki', 'Gilmore Girls (TV series)'), self.queries)

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

    def test_talent_ladder_before_a_photo_search(self):
        url, mode, note = C.resolve_cover_url('Taylor Sheridan', kind='talent')
        self.assertEqual(mode, 'person')
        self.assertTrue(note.startswith('talent:'))
        self.assertTrue(url)

    def test_attach_writes_meta_and_skips_a_second_pass(self):
        payload = {'meta': {'name': 'Costco', 'subject': 'Costco', 'subject_kind': 'brand', 'format': 'brand'}}
        self.assertTrue(C.attach_cover(None, payload))
        self.assertTrue(payload['meta']['hero_image'])
        self.assertEqual(payload['meta']['cover_kind'], 'logo')
        first = payload['meta']['hero_image']
        self.assertFalse(C.attach_cover(None, payload))
        self.assertEqual(payload['meta']['hero_image'], first)

    def test_index_carries_the_cover(self):
        payload = {'meta': {'name': 'Costco', 'subject': 'Costco', 'subject_kind': 'brand',
                            'hero_image': '/api/profile-image-file/x.png', 'cover_kind': 'logo',
                            'size_us': 12, 'created_at': '2026-10-27T00:00:00Z'},
                   'runs': [], 'labs': {'spend': []}, 'panel_sessions': []}
        row = index_entry('digital-twins/a.json.gz', payload)
        self.assertEqual(row['hero_image'], '/api/profile-image-file/x.png')
        self.assertEqual(row['cover_kind'], 'logo')


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

    def test_skips_rows_that_already_have_a_cover(self):
        n = C.schedule_missing(object(), [{'key': 'k', 'hero_image': '/x.png'}],
                               load_fn=lambda *a: None, save_fn=lambda *a: None)
        self.assertEqual(n, 0)


if __name__ == '__main__':
    unittest.main()
