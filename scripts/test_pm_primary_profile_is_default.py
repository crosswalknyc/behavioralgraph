#!/usr/bin/env python3
"""The primary profile is the default; a year cut is the subject only
when the words name its year (2026-11-12).

A read on "Reba McEntire fans" ran on "Reba McEntire - 2024 Total
Universe": the library subjects the Reader is shown list the year cuts
beside the primary, the model picked one, and the pick was carried to
the file the read runs on. And "what did Costco shoppers buy in 2025"
ran on the current file: a year in the words was never matched to the
family's year cut.

Holds, by code (prometheus.primary_profile), at the two places a subject
is bound to a library profile: the Reader's library pick and the file a
read runs on. Five real families, each with a year cut beside its
primary: Reba McEntire, Costco, Vin Diesel, Michael B Jordan, Simu Liu.

Hermetic: the library is the list below (the five families' real display
names and file names on 2026-11-12). No S3, no model, no app module.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())


def _norm(s):
    if not s:
        return ''
    s = re.sub(r"[^a-z0-9\s]+", " ", str(s).lower())
    return re.sub(r"\s+", " ", s).strip()


class _FakeHost:
    _normalize_for_match = staticmethod(_norm)
    S3_BUCKET = 'dashboard-inputs'

    def __getattr__(self, name):
        return mock.MagicMock(name=name)


import prometheus.legacy as _legacy  # noqa: E402
_legacy.bind(_FakeHost())
from prometheus.legacy import chat as C  # noqa: E402
from prometheus import primary_profile as PP  # noqa: E402
from prometheus import reader as RD  # noqa: E402

FILES = [
    ('Reba McEntire', 'Reba_McEntire_05_29_2026_01_05.csv'),
    ('Reba McEntire - 2023 Total Universe', 'Reba McEntire - 2023 Total Universe.csv'),
    ('Reba McEntire - 2024 Total Universe', 'Reba McEntire - 2024 Total Universe.csv'),
    ('Reba McEntire - Avid Fan', 'Reba McEntire - Avid Fan.csv'),
    ('Reba McEntire - Spotify Fan', 'Reba McEntire - Spotify Fan.csv'),
    ('Costco', 'Costco_08_23_2026_04_41.csv'),
    ('Costco - 2025 Avid Fan', 'Costco - 2025 Avid Fan.csv'),
    ('Costco - 2025 Total Universe', 'Costco - 2025 Total Universe.csv'),
    ('Costco - Avid Fan', 'Costco - Avid Fan.csv'),
    ('Vin Diesel', 'Vin_Diesel_06_06_2026_19_06.csv'),
    ('Vin Diesel - 2021 Total Universe', 'Vin Diesel - 2021 Total Universe.csv'),
    ('Vin Diesel - 2021 Avid Fan', 'Vin Diesel - 2021 Avid Fan.csv'),
    ('Vin Diesel - 2023 Total Universe', 'Vin Diesel - 2023 Total Universe.csv'),
    ('Vin Diesel - 2023 Avid Fan', 'Vin Diesel - 2023 Avid Fan.csv'),
    ('Vin Diesel - 2023 Avid Male Fan', 'Vin Diesel - 2023 Avid Male Fan.csv'),
    ('Vin Diesel - 2026 YTD Total Universe', 'Vin Diesel - 2026 YTD Total Universe.csv'),
    ('VIN Diesel - Avid Fan', 'VIN DIESEL - Avid Fan.csv'),
    ('Michael B Jordan', 'Michael_B_Jordan_10_21_2026_00_44.csv'),
    ('Michael B Jordan - 2025 Avid Fan', 'Michael B Jordan - 2025 Avid Fan.csv'),
    ('Michael B Jordan - 2025 Total Universe', 'Michael B Jordan - 2025 Total Universe.csv'),
    ('Michael B Jordan - Avid Fan', 'Michael B Jordan - Avid Fan.csv'),
    ('Simu Liu', 'Simu_Liu_10_20_2026_22_54.csv'),
    ('Simu Liu - 2025 Avid Fan', 'Simu Liu - 2025 Avid Fan.csv'),
    ('Simu Liu - 2025 Total Universe', 'Simu Liu - 2025 Total Universe.csv'),
    ('Simu Liu - Avid Fan', 'Simu Liu - Avid Fan.csv'),
    # a family whose only file is a year cut, and one whose own name carries a year
    ('Cruel Summer Viewers - 2021 Total Universe', 'Cruel Summer Viewers - 2021 Total Universe.csv'),
    ('2026 Florida Gubernatorial Voters', '2026_Florida_Gubernatorial_Voters_09_20_2026_10_10.csv'),
    ('Landman', 'Landman_09_01_2026_10_10.csv'),
]
CATALOG = [{'s3_key': k, 'display_name': n, 'subject': n, 'category': 'ACTOR', 'last_modified': '2026-10-20T00:00:00Z', 'days_old': 20}
           for n, k in FILES]
NAMES = [n for n, _k in FILES]
KEY = dict(FILES)
# (family, the noun its people go by, a year the family has a cut for)
FAMILIES = [('Reba McEntire', 'fans', '2024'), ('Costco', 'shoppers', '2025'), ('Vin Diesel', 'fans', '2023'),
            ('Michael B Jordan', 'fans', '2025'), ('Simu Liu', 'fans', '2025')]


def base(hint, text, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        b = C._pm_generation_base(hint, text, **kw)
    return (b or {}).get('s3_key')


class _Base(unittest.TestCase):
    def setUp(self):
        self._cat = C._profile_catalog_for_chat
        C._profile_catalog_for_chat = lambda: CATALOG
        PP._LIB.update(at=0.0, names=None)

    def tearDown(self):
        C._profile_catalog_for_chat = self._cat
        PP._LIB.update(at=0.0, names=None)


class TheRule(unittest.TestCase):
    def test_what_a_year_cut_is(self):
        self.assertEqual(PP.year_cut('Reba McEntire - 2024 Total Universe'), {'family': 'Reba McEntire', 'year': '2024', 'rest': 'Total Universe'})
        self.assertEqual(PP.year_cut('Costco - 2025 Avid Fan')['rest'], 'Avid Fan')
        self.assertEqual(PP.year_cut('Vin Diesel - 2026 YTD Total Universe'), {'family': 'Vin Diesel', 'year': '2026', 'rest': 'YTD Total Universe'})
        self.assertEqual(PP.year_cut('Apple Music - 2024')['year'], '2024')
        for name in ('Reba McEntire', 'Reba McEntire - Avid Fan', 'Reba McEntire - Spotify Fan', '2026 Florida Gubernatorial Voters',
                     'Gen Pop 2026', 'Go-GURT - 18-24', 'Aggregate Perception Box Followers - Sep 2025 To Aug 2026 Total Universe', ''):
            self.assertIsNone(PP.year_cut(name), name)

    def test_when_the_words_name_a_year(self):
        for words, want in (('what did Costco shoppers buy in 2025', {'2025'}), ('use the 2025 file', {'2025'}),
                            ('2025 Costco shoppers', {'2025'}), ('Costco shoppers (2025)', {'2025'}), ('for calendar 2024, please', {'2024'}),
                            ('compare 2023 and 2024', {'2023', '2024'}),
                            ('what do Costco shoppers buy', set()),
                            ('from August 7, 2026 to October 31, 2026', set()), ('since Oct 2025', set()), ('on 10/31/2026', set()),
                            ('as of 2026-10-31', set()), ('the 2025-26 season', set()), ('the 2025/26 season', set()),
                            ('an audience of 2,025 people', set()), ('it cost $2025', set()), ('a 2025x lift', set())):
            self.assertEqual(PP.years_named(words), want, words)
        # a year that is part of the subject's own name names no cut
        self.assertEqual(PP.years_named('what do 2026 Florida Gubernatorial Voters buy', '2026 Florida Gubernatorial Voters'), set())

    def test_the_five_families_both_ways(self):
        for fam, noun, year in FAMILIES:
            cut = f"{fam} - {year} Total Universe"
            plain = f"What do {fam} {noun} buy most often?"
            # no year in the words: the primary, whatever was picked
            self.assertEqual(PP.settle_name(cut, plain, NAMES), fam)
            self.assertEqual(PP.settle_name(fam, plain, NAMES), fam)
            # the year in the words: the year cut, whatever was picked
            for words in (f"What did {fam} {noun} buy most often in {year}?", f"What do {fam} {noun} buy? Use the {year} file."):
                self.assertEqual(PP.settle_name(fam, words, NAMES), cut, words)
                self.assertEqual(PP.settle_name(cut, words, NAMES), cut, words)

    def test_tiers_and_edges(self):
        self.assertEqual(PP.settle_name('Costco - 2025 Avid Fan', 'what do avid Costco shoppers buy', NAMES), 'Costco - Avid Fan')
        self.assertEqual(PP.settle_name('Costco - Avid Fan', 'what did avid Costco shoppers buy in 2025', NAMES), 'Costco - 2025 Avid Fan')
        self.assertEqual(PP.settle_name('Vin Diesel - 2023 Avid Male Fan', 'avid male Vin Diesel fans', NAMES), 'Vin Diesel')   # no such tier outside the years
        self.assertEqual(PP.settle_name('Vin Diesel - 2021 Avid Fan', 'avid Vin Diesel fans', NAMES), 'VIN Diesel - Avid Fan')  # the library's own spelling
        self.assertEqual(PP.settle_name('Vin Diesel', 'Vin Diesel fans in 2026', NAMES), 'Vin Diesel - 2026 YTD Total Universe')
        self.assertEqual(PP.settle_name('Vin Diesel - 2026 YTD Total Universe', 'Vin Diesel fans', NAMES), 'Vin Diesel')
        # a tier with no cut for that year keeps the tier; a year the family has no cut for changes nothing
        self.assertEqual(PP.settle_name('Reba McEntire - Avid Fan', 'avid Reba fans in 2024', NAMES), 'Reba McEntire - Avid Fan')
        self.assertEqual(PP.settle_name('Reba McEntire', 'Reba McEntire fans in 2019', NAMES), 'Reba McEntire')
        self.assertEqual(PP.settle_name('Landman', 'Landman viewers in 2025', NAMES), 'Landman')
        # two years in the words name no single cut; a date is a window, not a file
        self.assertEqual(PP.settle_name('Vin Diesel', 'compare Vin Diesel fans in 2021 and 2023', NAMES), 'Vin Diesel')
        self.assertEqual(PP.settle_name('Costco', 'Costco shoppers from August 7, 2025 to October 31, 2025', NAMES), 'Costco')
        # the only file on record for its family stands
        self.assertEqual(PP.settle_name('Cruel Summer Viewers - 2021 Total Universe', 'what do Cruel Summer viewers watch', NAMES),
                         'Cruel Summer Viewers - 2021 Total Universe')
        # a subject whose own name carries a year is not a cut and is not moved
        self.assertEqual(PP.settle_name('2026 Florida Gubernatorial Voters', 'what do 2026 Florida Gubernatorial Voters buy', NAMES),
                         '2026 Florida Gubernatorial Voters')
        # no library to ask: the year cut still gives way to the plain name
        self.assertEqual(PP.settle_name('Simu Liu - 2025 Total Universe', 'Simu Liu fans', None), 'Simu Liu')
        self.assertEqual(PP.settle_name('Simu Liu - 2025 Avid Fan', 'Simu Liu fans', None), 'Simu Liu - Avid Fan')
        self.assertEqual(PP.settle_name('Simu Liu', 'Simu Liu fans in 2025', None), 'Simu Liu')
        for bad in (None, '', 0):
            self.assertEqual(PP.settle_name(bad, 'x', NAMES), bad)


class TheFileAReadRunsOn(_Base):
    def test_the_five_families_both_ways(self):
        for fam, noun, year in FAMILIES:
            cut = f"{fam} - {year} Total Universe"
            plain = f"What do {fam} {noun} buy most often?"
            # no year in the words: the primary, also when a model handed over the year cut as the subject
            for hint, kw in ((fam, {}), ('', {'prefer_catalog': True}), (cut, {}), (cut, {'prefer_catalog': True}), (f"{fam} {noun}", {})):
                self.assertEqual(base(hint, plain, **kw), KEY[fam], (fam, hint, kw))
            # the year in the words: the year cut
            for words in (f"What did {fam} {noun} buy most often in {year}?", f"What do {fam} {noun} buy most often? Use the {year} file."):
                for hint, kw in ((fam, {}), ('', {'prefer_catalog': True}), (cut, {})):
                    self.assertEqual(base(hint, words, **kw), KEY[cut], (words, hint, kw))

    def test_a_subject_the_reader_bound_themselves_stands(self):
        """A chip or an approved quote that names the year cut is the reader's own choice of it."""
        for fam, _noun, year in FAMILIES:
            cut = f"{fam} - {year} Total Universe"
            self.assertEqual(base(cut, cut, prefer_catalog=True, bound=True), KEY[cut])
            self.assertEqual(base(fam, fam, prefer_catalog=True, bound=True), KEY[fam])

    def test_the_open_page_is_never_taken_away(self):
        page = {'primary': {'name': 'Costco - 2025 Total Universe', 's3_key': KEY['Costco - 2025 Total Universe']}}
        self.assertEqual(base('', 'what do they buy most often?', ctx=page), KEY['Costco - 2025 Total Universe'])
        # the primary open, and the words name the year: the year's file
        page = {'primary': {'name': 'Costco', 's3_key': KEY['Costco']}}
        self.assertEqual(base('', 'what do they buy most often?', ctx=page), KEY['Costco'])
        self.assertEqual(base('', 'what did they buy most often in 2025?', ctx=page), KEY['Costco - 2025 Total Universe'])

    def test_tiers_and_the_rest(self):
        self.assertEqual(base('Costco - 2025 Avid Fan', 'what do avid Costco shoppers buy?'), KEY['Costco - Avid Fan'])
        self.assertEqual(base('Costco shoppers', 'what did avid Costco shoppers buy in 2025?'), KEY['Costco - 2025 Avid Fan'])
        self.assertEqual(base('Cruel Summer Viewers - 2021 Total Universe', 'what do Cruel Summer Viewers watch?'),
                         KEY['Cruel Summer Viewers - 2021 Total Universe'])
        self.assertEqual(base('Landman', 'what did Landman viewers watch in 2025?'), KEY['Landman'])
        self.assertEqual(base('2026 Florida Gubernatorial Voters', 'what do 2026 Florida Gubernatorial Voters buy?'),
                         KEY['2026 Florida Gubernatorial Voters'])

    def test_bases_that_are_not_library_profiles_pass_through(self):
        for b in (None, {'subject': 'X', 's3_key': 'subiq:run1', 'source': 'subscriber_iq'}, {'subject': 'X', 'source': 'view'},
                  {'subject': 'X', 's3_key': 'not-in-the-library.csv', 'source': 'catalog'}):
            self.assertEqual(PP.settle_base(b, 'anything in 2025', CATALOG), b)
        # a catalog that cannot be read changes nothing
        b = {'subject': 'Costco - 2025 Total Universe', 's3_key': KEY['Costco - 2025 Total Universe'], 'source': 'catalog'}

        def boom():
            raise RuntimeError('catalog down')
        self.assertEqual(PP.settle_base(b, 'Costco shoppers', boom), b)

    def test_the_newest_file_under_a_name_is_the_primary(self):
        cat = CATALOG + [{'s3_key': 'Costco_01_02_2025_03_04.csv', 'display_name': 'Costco', 'subject': 'Costco', 'last_modified': '2026-11-01T00:00:00Z'}]
        b = {'subject': 'Costco - 2025 Total Universe', 's3_key': KEY['Costco - 2025 Total Universe'], 'source': 'catalog'}
        self.assertEqual(PP.settle_base(b, 'Costco shoppers', cat)['s3_key'], 'Costco_08_23_2026_04_41.csv')


class TheReadersLibraryPick(_Base):
    def _cands(self, fam, year):
        return [{'subject': fam, 'products': ['profile'], 'for': fam},
                {'subject': f"{fam} - {year} Total Universe", 'products': ['profile'], 'for': fam},
                {'subject': f"{fam} - {year} Avid Fan", 'products': ['profile'], 'for': fam}]

    def _pick(self, text, picked, cands):
        d = {'intent': 'analyze', 'confidence': 0.8, 'subjects': [{'as_written': 'x', 'catalog': picked, 'kind': 'person'}]}
        out = RD._normalize(d, text, cands)
        return out['subjects'][0]['catalog']

    def test_the_five_families_both_ways(self):
        for fam, noun, year in FAMILIES:
            cut = f"{fam} - {year} Total Universe"
            cands = self._cands(fam, year)
            plain = f"What do {fam} {noun} buy most often?"
            self.assertEqual(self._pick(plain, cut, cands), fam)                 # the model picked the year cut: the primary
            self.assertEqual(self._pick(plain, fam, cands), fam)
            named = f"What did {fam} {noun} buy most often in {year}?"
            self.assertEqual(self._pick(named, cut, cands), cut)
            self.assertEqual(self._pick(named, fam, cands), cut)                 # the model picked the primary: the year's file
            self.assertEqual(self._pick(f"Use the {year} file for {fam}", fam, cands), cut)

    def test_with_only_the_list_it_was_shown(self):
        """No dashboard to ask (the library list is what the Reader was shown): the rule still holds."""
        C._profile_catalog_for_chat = lambda: []
        PP._LIB.update(at=0.0, names=None)
        cands = self._cands('Reba McEntire', '2024')
        self.assertEqual(self._pick('What do Reba McEntire fans buy most often?', 'Reba McEntire - 2024 Total Universe', cands), 'Reba McEntire')
        self.assertEqual(self._pick('Reba McEntire fans in 2024', 'Reba McEntire', cands), 'Reba McEntire - 2024 Total Universe')
        # a name it was never shown is still never invented
        self.assertEqual(self._pick('Reba McEntire fans', 'Reba McEntire - 2019 Total Universe', cands), '')


if __name__ == '__main__':
    unittest.main(verbosity=1)
