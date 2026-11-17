#!/usr/bin/env python3
"""A brief on books it names, on all US people, is never started on a file
that shares one title's name (Grace Yin, Amazon, 2026-11-17).

Grace, 17:35 UTC, moments after the brief lane learned to read her list:
"Compare these 11 books for US people, calendar year 2022 ... We Were
Liars (E. Lockhart) ... Use the book member only (no adaptation, no
author-only activity), and all US people as the base, not YouTube
viewers." The reply promised one read on her titles. The job behind it
was started on the Subscriber IQ file called We Were Liars, the series
she had just excluded, because the base resolver's last tier takes any
Subscriber IQ title the words contain.

The rule (prometheus.read_brief.own_base_brief, asked through
prometheus.page_default.brief_on_own_base): a brief on works it names,
counted on a base it states itself, has no file as its base. The resolver
returns none before the open product, the page, the library and
Subscriber IQ; the read then runs on US people.

Hermetic: no network, no model, no S3.
"""
from __future__ import annotations

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


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())


def _norm(s):
    if not s:
        return ''
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class _FakeHost:
    _normalize_for_match = staticmethod(_norm)
    S3_BUCKET = 'dashboard-inputs'

    def __getattr__(self, name):
        return mock.MagicMock(name=name)


import prometheus.legacy as _legacy  # noqa: E402
_legacy.bind(_FakeHost())
from prometheus.legacy import chat as C  # noqa: E402
import prometheus_analysis as PA  # noqa: E402

BRACKETS = (
    'Compare these 11 books for US people, calendar year 2022 (Jan 1 \u2013 Dec 31, 2022): The Love Hypothesis (Ali Hazelwood); '
    'The Map That Leads To You (J.P. Monninger); Every Summer After (Carley Fortune); The Deal (Elle Kennedy); We Were Liars '
    '(E. Lockhart); My Lady Jane (Cynthia Hand, Brodi Ashton, Jodi Meadows); The Idea of You (Robinne Lee); Culpa M\u00eda '
    "(Mercedes Ron, book 1); Perfect Addiction (Claudia Tan); Butcher & Blackbird (Brynne Weaver); The Bad Boy's Girl (Blair "
    'Holden, book 1).\n\u258e\n\u258e Use the book member only (no adaptation, no author-only activity), and all US people as the base, '
    'not YouTube viewers. For each book, give: total US people engaged and the count in the data; the split into Prime members '
    "who watched Prime Video, Prime members who didn't, and non-Prime members; the split into 1 touch, 2\u20133 touches and 4+ "
    'touches; and people by source (book orders, Goodreads, Wattpad, other fan fiction, TikTok, YouTube, fan sites, search), '
    'with a person counted once inside each source.')
LABEL = 'The Love Hypothesis, The Map That Leads To You, Every Summer After and 8 more titles'
SERIES_KEY = 'We_Were_Liars_04_07_2026_19_50.csv'
HAZBIN = {'view': 'profile-iq', 'primary': {'name': 'Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers',
                                            's3_key': 'Hazbin_Hotel_Season_1_-_Amazon_Prime_New_Sign-Ups_Viewers_11_08_2026_17_09.csv'}}
CATALOG = [
    {'s3_key': HAZBIN['primary']['s3_key'], 'display_name': HAZBIN['primary']['name'], 'subject': HAZBIN['primary']['name'],
     'category': 'SERIES - ANIMATION'},
    {'s3_key': 'The_Idea_of_You_05_10_2026_10_00.csv', 'display_name': 'The Idea of You', 'subject': 'The Idea of You', 'category': 'MOVIE'},
    {'s3_key': 'My_Lady_Jane_07_01_2026_09_00.csv', 'display_name': 'My Lady Jane', 'subject': 'My Lady Jane', 'category': 'SERIES - DRAMA'},
    {'s3_key': 'YouTube_10_01_2026_10_00.csv', 'display_name': 'YouTube', 'subject': 'YouTube', 'category': 'SOCIAL MEDIA'},
]
C._profile_catalog_for_chat = lambda: CATALOG


def _subiq_index(*_a, **_k):
    return {PA._xmod_title_key('We Were Liars'): ('We Were Liars', SERIES_KEY)}


class NoFileIsTheBase(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(PA, '_load_subiq_index', _subiq_index)
        p.start()
        self.addCleanup(p.stop)

    def test_the_series_file_is_found_for_a_question_about_the_series(self):
        # the tier itself still works: a question about the series is read on the series
        got = C._pm_generation_base('', 'What else do We Were Liars viewers watch?', ctx=None, prefer_catalog=True)
        self.assertEqual((got or {}).get('s3_key'), f"subiq:{SERIES_KEY}")

    def test_her_words_have_no_file_as_their_base(self):
        for ctx in (None, HAZBIN):
            self.assertIsNone(C._pm_generation_base('', BRACKETS, ctx=ctx, prefer_catalog=True))
            self.assertIsNone(C._pm_generation_base(LABEL, BRACKETS, ctx=ctx, prefer_catalog=True))
            self.assertIsNone(C._pm_generation_base('We Were Liars', BRACKETS, ctx=ctx))
            self.assertIsNone(C._pm_generation_base('The Idea of You', BRACKETS, ctx=ctx, prefer_catalog=True))

    def test_a_question_on_the_open_profile_keeps_the_profile(self):
        got = C._pm_generation_base('', 'What share of these viewers are 18 to 34?', ctx=HAZBIN)
        self.assertEqual((got or {}).get('source'), 'page')


if __name__ == '__main__':
    unittest.main(verbosity=1)
